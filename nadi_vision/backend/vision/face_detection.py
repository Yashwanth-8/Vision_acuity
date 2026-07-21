"""Face detection and attention inference worker.

Uses MediaPipe FaceDetection (model_selection=0, short-range <2m) as the
primary detector.  Selected over YuNet based on Pi 4 real-inference benchmark
(2026-07-21):

  Model       avg_ms   p95_ms   max_ms   rss_mb
  ---------  -------  -------  -------  -------
  YuNet        33.5     52.0     72.3     77.4
  MediaPipe    13.8     18.9     29.5    134.0   ← SELECTED

MediaPipe is 2.4× faster on Cortex-A72 due to TFLite NEON SIMD optimisation.
The +57 MB RAM cost is acceptable on Pi 4 (4 GB).  YuNet path removed.
"""

from __future__ import annotations

import multiprocessing
from typing import Any, Dict, Optional

import cv2
import numpy as np

try:
    import mediapipe as mp
    _MEDIAPIPE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MEDIAPIPE_AVAILABLE = False


def _attention_from_detection(
    result: Any,
    frame_w: int,
    frame_h: int,
) -> Dict[str, Any]:
    """Build a compact attention-state dict from a MediaPipe detection result."""
    if not result.detections:
        return {"face_detected": False, "num_faces": 0, "bbox": None, "score": 0.0,
                "multiple_faces": False}

    best = max(result.detections, key=lambda d: d.score[0])
    box = best.location_data.relative_bounding_box
    num = len(result.detections)

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
    }


def _inference_loop(
    frame_queue: "multiprocessing.Queue",
    attention_queue: "multiprocessing.Queue",
    stop_event: "multiprocessing.Event",
    frame_skip: int,
    frame_w: int,
    frame_h: int,
) -> None:
    """Entry point for the vision inference subprocess."""
    if not _MEDIAPIPE_AVAILABLE:  # pragma: no cover
        raise RuntimeError("mediapipe is not installed — run: pip install mediapipe>=0.10")

    face_detection = mp.solutions.face_detection.FaceDetection(
        model_selection=0,               # short-range model (≤2 m)
        min_detection_confidence=0.5,
    )
    skip_counter = 0

    while not stop_event.is_set():
        try:
            frame: Optional[np.ndarray] = frame_queue.get(timeout=0.1)
        except Exception:
            continue

        if frame is None:  # sentinel — clean shutdown
            break

        skip_counter += 1
        if skip_counter % frame_skip != 0:
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = face_detection.process(rgb)
        state = _attention_from_detection(result, frame_w, frame_h)

        # Drop-oldest policy: never block the inference loop
        try:
            attention_queue.put_nowait(state)
        except Exception:
            try:
                attention_queue.get_nowait()
                attention_queue.put_nowait(state)
            except Exception:
                pass

    face_detection.close()


class FaceInferenceWorker:
    """MediaPipe face-detection worker running in a dedicated subprocess.

    Usage::

        worker = FaceInferenceWorker(frame_queue, attention_queue, stop_event)
        worker.start()
        # ... run session ...
        worker.stop()
    """

    def __init__(
        self,
        frame_queue: "multiprocessing.Queue",
        attention_queue: "multiprocessing.Queue",
        stop_event: "multiprocessing.Event",
        frame_skip: int = 4,
        frame_w: int = 320,
        frame_h: int = 240,
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
                self._frame_queue.put_nowait(None)  # unblock blocking get
            except Exception:
                pass
            self._process.join(timeout=5.0)
            if self._process.is_alive():
                self._process.terminate()
