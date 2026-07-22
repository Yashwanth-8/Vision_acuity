"""Face detection and attention inference worker.

Two MediaPipe models run in a dedicated subprocess during active OD/OS tests:

    FaceDetection  (model_selection=0)       6 Hz  fast face/count/position check
    Hands          (max_num_hands=1)         3 Hz  hand/eye overlap for occlusion

Eye-open mapping (unmirrored patient coordinates):
  Camera image-LEFT  (lower x)  = Patient RIGHT eye (OD)
  Camera image-RIGHT (higher x) = Patient LEFT  eye (OS)

Fellow-eye visibility is inferred from a fused score:
    0.65 * hand-eye IoU + 0.35 * sclera suppression, with hysteresis.

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

import multiprocessing
from typing import Any, Dict, Optional

import numpy as np

from backend.scoring.constants import (
    FELLOW_EYE_COVERED_THRESH,
    FELLOW_EYE_UNCOVERED_THRESH,
    HAND_EYE_INFER_SKIP,
    INFER_HEIGHT,
    INFER_WIDTH,
)

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


def _eye_open_score_from_roi(frame_rgb: np.ndarray, roi: tuple[int, int, int, int]) -> float:
    """Return an eye-openness score in [0, 1] from sclera brightness/contrast."""
    x0, y0, x1, y1 = roi
    if x1 <= x0 or y1 <= y0:
        return 0.0

    patch = frame_rgb[y0:y1, x0:x1]
    if patch.size == 0:
        return 0.0

    gray = patch.mean(axis=2)
    p90 = float(np.percentile(gray, 90))
    p50 = float(np.percentile(gray, 50))
    bright_ratio = float(np.mean(gray >= 190.0))
    contrast = p90 - p50

    bright_score = float(np.clip((bright_ratio - 0.02) / 0.10, 0.0, 1.0))
    contrast_score = float(np.clip((contrast - 10.0) / 25.0, 0.0, 1.0))
    return 0.5 * (bright_score + contrast_score)


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = float((ix1 - ix0) * (iy1 - iy0))
    area_a = float(max(0, ax1 - ax0) * max(0, ay1 - ay0))
    area_b = float(max(0, bx1 - bx0) * max(0, by1 - by0))
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def _hand_bbox_iou(hand_result: Any, target_roi: tuple[int, int, int, int], frame_w: int, frame_h: int) -> float:
    """Return max IoU between any detected hand bbox and the target ROI."""
    lm_sets = getattr(hand_result, "multi_hand_landmarks", None)
    if not lm_sets:
        return 0.0

    best = 0.0
    for hand in lm_sets:
        xs = [int(lm.x * frame_w) for lm in hand.landmark]
        ys = [int(lm.y * frame_h) for lm in hand.landmark]
        if not xs or not ys:
            continue
        hand_roi = _clip_box(min(xs), min(ys), max(xs), max(ys), frame_w, frame_h)
        best = max(best, _iou(hand_roi, target_roi))
    return best


def _eye_rois_from_bbox(
    bbox: Dict[str, float],
    frame_w: int,
    frame_h: int,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Return (image-left eye roi, image-right eye roi) inside the face box."""
    x = int(bbox["x"])
    y = int(bbox["y"])
    w = int(bbox["w"])
    h = int(bbox["h"])

    eye_band_top = y + int(0.22 * h)
    eye_band_bottom = y + int(0.48 * h)

    left_eye_x0 = x + int(0.10 * w)
    left_eye_x1 = x + int(0.43 * w)
    right_eye_x0 = x + int(0.57 * w)
    right_eye_x1 = x + int(0.90 * w)

    image_left_roi = _clip_box(left_eye_x0, eye_band_top, left_eye_x1, eye_band_bottom, frame_w, frame_h)
    image_right_roi = _clip_box(right_eye_x0, eye_band_top, right_eye_x1, eye_band_bottom, frame_w, frame_h)
    return image_left_roi, image_right_roi


def _eye_states_from_bbox(
    frame_rgb: np.ndarray,
    bbox: Dict[str, float],
    hand_result: Any,
    image_left_covered_state: bool,
    image_right_covered_state: bool,
    frame_w: int,
    frame_h: int,
) -> tuple[Dict[str, bool], bool, bool]:
    """Estimate patient eye-open flags from face box geometry and sclera signal.

    Mapping:
    - Image-left ROI  -> patient RIGHT eye (OD)
    - Image-right ROI -> patient LEFT eye (OS)
    """
    w = int(bbox["w"])
    h = int(bbox["h"])

    if w < 30 or h < 30:
        return {"right_eye_open": False, "left_eye_open": False}, True, True

    image_left_roi, image_right_roi = _eye_rois_from_bbox(bbox, frame_w, frame_h)

    left_iou = _hand_bbox_iou(hand_result, image_left_roi, frame_w, frame_h)
    right_iou = _hand_bbox_iou(hand_result, image_right_roi, frame_w, frame_h)

    left_open_score = _eye_open_score_from_roi(frame_rgb, image_left_roi)
    right_open_score = _eye_open_score_from_roi(frame_rgb, image_right_roi)

    # Coverage confidence: hand overlap (primary) + sclera suppression (secondary)
    left_covered_score = (0.65 * left_iou) + (0.35 * (1.0 - left_open_score))
    right_covered_score = (0.65 * right_iou) + (0.35 * (1.0 - right_open_score))

    if left_covered_score >= FELLOW_EYE_COVERED_THRESH:
        image_left_covered_state = True
    elif left_covered_score <= FELLOW_EYE_UNCOVERED_THRESH:
        image_left_covered_state = False

    if right_covered_score >= FELLOW_EYE_COVERED_THRESH:
        image_right_covered_state = True
    elif right_covered_score <= FELLOW_EYE_UNCOVERED_THRESH:
        image_right_covered_state = False

    return (
        {
            # image-left = patient OD; image-right = patient OS
            "right_eye_open": not image_left_covered_state,
            "left_eye_open": not image_right_covered_state,
        },
        image_left_covered_state,
        image_right_covered_state,
    )


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
    single_eye_flag: Any,
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
    hands = mp.solutions.hands.Hands(
        max_num_hands=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    skip_counter = 0
    hand_skip_counter = 0
    last_hand_result: Any = None
    image_left_covered_state = True
    image_right_covered_state = True

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

        single_eye_phase = bool(getattr(single_eye_flag, "value", 0))
        if single_eye_phase and attention["face_detected"] and attention["bbox"] is not None:
            hand_skip_counter += 1
            if hand_skip_counter % HAND_EYE_INFER_SKIP == 0:
                last_hand_result = hands.process(frame)

            eye_states, image_left_covered_state, image_right_covered_state = _eye_states_from_bbox(
                frame,
                attention["bbox"],
                last_hand_result,
                image_left_covered_state,
                image_right_covered_state,
                frame_w,
                frame_h,
            )
            attention["right_eye_open"] = eye_states["right_eye_open"]
            attention["left_eye_open"] = eye_states["left_eye_open"]
        else:
            # Safe default outside OD/OS active test phases.
            image_left_covered_state = True
            image_right_covered_state = True
            attention["right_eye_open"] = False
            attention["left_eye_open"] = False

        try:
            attention_queue.put_nowait(attention)
        except Exception:
            try:
                attention_queue.get_nowait()
                attention_queue.put_nowait(attention)
            except Exception:
                pass

    face_detection.close()
    hands.close()


class FaceInferenceWorker:
    """MediaPipe face-detection worker running in a dedicated subprocess."""

    def __init__(
        self,
        frame_queue: "multiprocessing.Queue",
        attention_queue: "multiprocessing.Queue",
        stop_event: "multiprocessing.Event",
        single_eye_flag: Any,
        frame_skip: int = 1,
        frame_w: int = INFER_WIDTH,
        frame_h: int = INFER_HEIGHT,
    ) -> None:
        self._frame_queue = frame_queue
        self._attention_queue = attention_queue
        self._stop_event = stop_event
        self._single_eye_flag = single_eye_flag
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
                self._single_eye_flag,
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
