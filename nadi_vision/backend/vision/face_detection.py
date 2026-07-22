"""Face detection and attention inference worker.

One MediaPipe model runs in a dedicated subprocess:

  FaceDetection  (model_selection=0)       6 Hz  fast face/count/position check

Sclera-based eye-open mapping (unmirrored patient coordinates):
  Camera image-LEFT  (lower x)  = Patient RIGHT eye (OD)
  Camera image-RIGHT (higher x) = Patient LEFT  eye (OS)

Attention dict fields:
    right_eye_open  patient OD eye appears open
    left_eye_open   patient OS eye appears open

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

from backend.scoring.constants import INFER_HEIGHT, INFER_WIDTH

try:
    import mediapipe as mp
    _MEDIAPIPE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MEDIAPIPE_AVAILABLE = False

def _clip_box(x0: int, y0: int, x1: int, y1: int, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    x0 = max(0, min(frame_w, x0))
    y0 = max(0, min(frame_h, y0))
    x1 = max(0, min(frame_w, x1))
    y1 = max(0, min(frame_h, y1))
    return x0, y0, x1, y1


def _estimate_eye_open_from_roi(frame_rgb: np.ndarray, roi: tuple[int, int, int, int]) -> bool:
    """Infer if an eye appears open via sclera brightness in a compact ROI.

    The method intentionally prefers false-closed over false-open. If uncertain,
    it returns False so occluded fellow-eye does not cause unnecessary holds.
    """
    x0, y0, x1, y1 = roi
    if x1 <= x0 or y1 <= y0:
        return False

    patch = frame_rgb[y0:y1, x0:x1]
    if patch.size == 0:
        return False

    gray = patch.mean(axis=2)
    p90 = float(np.percentile(gray, 90))
    p50 = float(np.percentile(gray, 50))
    bright_ratio = float(np.mean(gray >= 190.0))
    contrast = p90 - p50

    # Open eye usually contains enough bright sclera pixels with local contrast.
    return bright_ratio >= 0.05 and contrast >= 22.0


def _eye_states_from_bbox(
    frame_rgb: np.ndarray,
    bbox: Dict[str, float],
    frame_w: int,
    frame_h: int,
) -> Dict[str, bool]:
    """Estimate patient eye-open flags from face box geometry and sclera signal.

    Mapping:
    - Image-left ROI  -> patient RIGHT eye (OD)
    - Image-right ROI -> patient LEFT eye (OS)
    """
    x = int(bbox["x"])
    y = int(bbox["y"])
    w = int(bbox["w"])
    h = int(bbox["h"])

    if w < 30 or h < 30:
        return {"right_eye_open": False, "left_eye_open": False}

    eye_band_top = y + int(0.22 * h)
    eye_band_bottom = y + int(0.48 * h)

    left_eye_x0 = x + int(0.10 * w)
    left_eye_x1 = x + int(0.43 * w)

    right_eye_x0 = x + int(0.57 * w)
    right_eye_x1 = x + int(0.90 * w)

    left_roi = _clip_box(left_eye_x0, eye_band_top, left_eye_x1, eye_band_bottom, frame_w, frame_h)
    right_roi = _clip_box(right_eye_x0, eye_band_top, right_eye_x1, eye_band_bottom, frame_w, frame_h)

    image_left_open = _estimate_eye_open_from_roi(frame_rgb, left_roi)
    image_right_open = _estimate_eye_open_from_roi(frame_rgb, right_roi)

    return {
        "right_eye_open": image_left_open,
        "left_eye_open": image_right_open,
    }


def _attention_from_detection(
    result: Any,
    frame_w: int,
    frame_h: int,
) -> Dict[str, Any]:
    """Build attention-state dict from a MediaPipe FaceDetection result.

    head_yaw_deg is a coarse proxy from face-centre horizontal offset;
    it is not precise gaze tracking, only head-pose attention.
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
    skip_counter = 0

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

        if attention["face_detected"] and attention["bbox"] is not None:
            eye_states = _eye_states_from_bbox(frame, attention["bbox"], frame_w, frame_h)
            attention["right_eye_open"] = eye_states["right_eye_open"]
            attention["left_eye_open"] = eye_states["left_eye_open"]

        try:
            attention_queue.put_nowait(attention)
        except Exception:
            try:
                attention_queue.get_nowait()
                attention_queue.put_nowait(attention)
            except Exception:
                pass

    face_detection.close()


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
