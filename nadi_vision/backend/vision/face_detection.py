"""Face detection and attention inference worker.

Two MediaPipe models run in one dedicated subprocess:

  FaceDetection  (model_selection=0)       6 Hz  fast face/count/position check
  FaceMesh       (refine_landmarks=False)  3 Hz  Eye Aspect Ratio per eye

EAR-based eye-open mapping (unmirrored patient coordinates):
  Camera image-LEFT  (lower x)  = Patient RIGHT eye (OD)
  Camera image-RIGHT (higher x) = Patient LEFT  eye (OS)

Attention dict fields:
  right_eye_open  patient OD eye visible (EAR >= threshold)
  left_eye_open   patient OS eye visible (EAR >= threshold)

IntegrityMonitor usage:
  tested_eye=OD  check left_eye_open   (patient must cover OS)
  tested_eye=OS  check right_eye_open  (patient must cover OD)

Frames are RGB NumPy arrays from SharedMemoryCameraConsumer.
No OpenCV dependency anywhere in this module.
"""

from __future__ import annotations

import math
import multiprocessing
from typing import Any, Dict, Optional

import numpy as np

from backend.scoring.constants import EAR_OPEN_THRESHOLD, FACE_MESH_SKIP, INFER_HEIGHT, INFER_WIDTH

try:
    import mediapipe as mp
    _MEDIAPIPE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MEDIAPIPE_AVAILABLE = False

# ---------------------------------------------------------------------------
# EAR landmark indices (MediaPipe Face Mesh, 468 landmarks)
#
# Six-point EAR: [outer, top-outer, top-inner, inner, bottom-inner, bottom-outer]
# EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
# ---------------------------------------------------------------------------
_IMG_LEFT_EAR_IDX  = [33,  160, 158, 133, 153, 144]  # image-left  = patient OD
_IMG_RIGHT_EAR_IDX = [362, 385, 387, 263, 373, 380]  # image-right = patient OS


def _ear(landmarks: Any, indices: list[int], w: int, h: int) -> float:
    """Compute Eye Aspect Ratio from 6 Face Mesh landmark indices."""
    pts = [(landmarks[i].x * w, landmarks[i].y * h) for i in indices]
    a = math.hypot(pts[1][0] - pts[5][0], pts[1][1] - pts[5][1])
    b = math.hypot(pts[2][0] - pts[4][0], pts[2][1] - pts[4][1])
    c = math.hypot(pts[0][0] - pts[3][0], pts[0][1] - pts[3][1])
    return (a + b) / (2.0 * c) if c > 0 else 0.0


def _eye_states_from_mesh(
    mesh_result: Any,
    frame_w: int,
    frame_h: int,
) -> Dict[str, bool]:
    """Return patient-perspective eye-open flags from a FaceMesh result.

    Defaults to False (eyes covered = safe state) when no face is found.
    """
    if not mesh_result.multi_face_landmarks:
        return {"right_eye_open": False, "left_eye_open": False}

    lm = mesh_result.multi_face_landmarks[0].landmark
    ear_od = _ear(lm, _IMG_LEFT_EAR_IDX,  frame_w, frame_h)  # patient OD
    ear_os = _ear(lm, _IMG_RIGHT_EAR_IDX, frame_w, frame_h)  # patient OS

    return {
        "right_eye_open": ear_od >= EAR_OPEN_THRESHOLD,
        "left_eye_open":  ear_os >= EAR_OPEN_THRESHOLD,
    }


def _attention_from_detection(
    result: Any,
    frame_w: int,
    frame_h: int,
) -> Dict[str, Any]:
    """Build attention-state dict from a MediaPipe FaceDetection result.

    head_yaw_deg is a coarse proxy from face-centre horizontal offset;
    it is not precise gaze tracking, only head-pose attention.
    Eye-open states are set to False here and overwritten by FaceMesh.
    """
    if not result.detections:
        return {
            "face_detected": False,
            "num_faces": 0,
            "bbox": None,
            "score": 0.0,
            "multiple_faces": False,
            "head_yaw_deg": 0.0,
            "right_eye_open": False,
            "left_eye_open": False,
        }

    best = max(result.detections, key=lambda d: d.score[0])
    box  = best.location_data.relative_bounding_box
    num  = len(result.detections)

    # Coarse yaw: face centre at ±25 % of frame width ≈ ±45° empirical
    face_cx = (box.xmin + box.width * 0.5) * frame_w
    yaw_deg = ((face_cx - frame_w * 0.5) / (frame_w * 0.25)) * 45.0

    return {
        "face_detected": True,
        "num_faces": num,
        "multiple_faces": num > 1,
        "bbox": {
            "x": box.xmin * frame_w,
            "y": box.ymin * frame_h,
            "w": box.width * frame_w,
            "h": box.height * frame_h,
        },
        "score": float(best.score[0]),
        "head_yaw_deg": float(yaw_deg),
        "right_eye_open": False,
        "left_eye_open":  False,
    }


def _inference_loop(
    frame_queue: "multiprocessing.Queue",
    attention_queue: "multiprocessing.Queue",
    stop_event: "multiprocessing.Event",
    frame_skip: int,
    frame_w: int,
    frame_h: int,
) -> None:
    """Entry point for the vision inference subprocess.

    Frames arrive as RGB NumPy arrays (no colour conversion needed).
    """
    if not _MEDIAPIPE_AVAILABLE:  # pragma: no cover
        raise RuntimeError("mediapipe is not installed — run: pip install mediapipe>=0.10")

    face_detection = mp.solutions.face_detection.FaceDetection(
        model_selection=0,
        min_detection_confidence=0.5,
    )
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    skip_counter = 0
    mesh_counter = 0
    last_eye_states: Dict[str, bool] = {"right_eye_open": False, "left_eye_open": False}

    while not stop_event.is_set():
        try:
            frame: Optional[np.ndarray] = frame_queue.get(timeout=0.1)
        except Exception:
            continue

        if frame is None:
            break

        skip_counter += 1
        if skip_counter % frame_skip != 0:
            continue

        # FaceDetection — 6 Hz
        detection_result = face_detection.process(frame)
        attention = _attention_from_detection(detection_result, frame_w, frame_h)

        # FaceMesh — 3 Hz, only when face already detected
        mesh_counter += 1
        if mesh_counter % FACE_MESH_SKIP == 0 and attention["face_detected"]:
            mesh_result = face_mesh.process(frame)
            last_eye_states = _eye_states_from_mesh(mesh_result, frame_w, frame_h)

        attention["right_eye_open"] = last_eye_states["right_eye_open"]
        attention["left_eye_open"]  = last_eye_states["left_eye_open"]

        try:
            attention_queue.put_nowait(attention)
        except Exception:
            try:
                attention_queue.get_nowait()
                attention_queue.put_nowait(attention)
            except Exception:
                pass

    face_detection.close()
    face_mesh.close()


class FaceInferenceWorker:
    """MediaPipe face-detection worker running in a dedicated subprocess."""

    def __init__(
        self,
        frame_queue: "multiprocessing.Queue",
        attention_queue: "multiprocessing.Queue",
        stop_event: "multiprocessing.Event",
        frame_skip: int = 1,
        frame_w: int = INFER_WIDTH,
        frame_h: int = INFER_HEIGHT,
    ) -> None:
        self._frame_queue = frame_queue
        self._attention_queue = attention_queue
        self._stop_event = stop_event
        self._frame_skip = frame_skip
        self._frame_w = frame_w
        self._frame_h = frame_h
        self._process: Optional[multiprocessing.Process] = None

    def start(self) -> None:
        self._process = multiprocessing.Process(
            target=_inference_loop,
            args=(
                self._frame_queue,
                self._attention_queue,
                self._stop_event,
                self._frame_skip,
                self._frame_w,
                self._frame_h,
            ),
            daemon=True,
            name="nadi-vision-inference",
        )
        self._process.start()

    def stop(self) -> None:
        if self._process and self._process.is_alive():
            self._stop_event.set()
            try:
                self._frame_queue.put_nowait(None)
            except Exception:
                pass
            self._process.join(timeout=5.0)
            if self._process.is_alive():
                self._process.terminate()
