"""Camera capture worker using Picamera2 on top of libcamera.

Captures XRGB8888 frames, drops the X channel, and publishes:
- full preview frames
- 320x240 inference frames
"""

from __future__ import annotations

from queue import Empty, Full, Queue
import threading
import time
from typing import Optional

import cv2
import numpy as np

from backend.config import (
    CAMERA_FRAMERATE,
    CAMERA_HEIGHT,
    CAMERA_WIDTH,
    DETECT_HEIGHT,
    DETECT_WIDTH,
    PREVIEW_QUALITY,
    PREVIEW_SKIP,
)

try:
    from picamera2 import Picamera2
except Exception:  # pragma: no cover
    Picamera2 = None


class CameraWorker:
    """Background camera capture worker."""

    def __init__(self, frame_queue: Queue, *, camera_index: int = 0) -> None:
        self._frame_queue = frame_queue
        self._camera_index = camera_index

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._picam = None
        self._cv_cap = None

        self._preview_lock = threading.Lock()
        self._latest_preview_jpeg: Optional[bytes] = None

    def _init_camera(self) -> None:
        if Picamera2 is not None:
            self._picam = Picamera2()
            cfg = self._picam.create_preview_configuration(
                main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"},
                controls={"FrameRate": CAMERA_FRAMERATE},
            )
            self._picam.configure(cfg)
            self._picam.start()
            time.sleep(0.5)
            return

        self._cv_cap = cv2.VideoCapture(self._camera_index)
        self._cv_cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        self._cv_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        self._cv_cap.set(cv2.CAP_PROP_FPS, CAMERA_FRAMERATE)

    def _capture_frame(self) -> Optional[np.ndarray]:
        if self._picam is not None:
            frame = self._picam.capture_array()
            if frame is None:
                return None
            # Picamera2 RGB888 -> convert to BGR for OpenCV and detectors.
            return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        if self._cv_cap is None:
            return None
        ok, frame = self._cv_cap.read()
        if not ok:
            return None
        return frame

    def _push_detect_frame(self, frame: np.ndarray) -> None:
        detect_frame = cv2.resize(frame, (DETECT_WIDTH, DETECT_HEIGHT), interpolation=cv2.INTER_AREA)
        try:
            self._frame_queue.put_nowait(detect_frame)
        except Full:
            try:
                self._frame_queue.get_nowait()
            except Empty:
                pass
            try:
                self._frame_queue.put_nowait(detect_frame)
            except Full:
                pass

    def _update_preview(self, frame: np.ndarray, frame_idx: int) -> None:
        if frame_idx % PREVIEW_SKIP != 0:
            return
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(PREVIEW_QUALITY)],
        )
        if not ok:
            return
        with self._preview_lock:
            self._latest_preview_jpeg = encoded.tobytes()

    def get_latest_preview_jpeg(self) -> Optional[bytes]:
        with self._preview_lock:
            return self._latest_preview_jpeg

    def _run(self) -> None:
        self._init_camera()
        frame_idx = 0
        frame_period = 1.0 / max(1, CAMERA_FRAMERATE)

        while not self._stop_event.is_set():
            loop_start = time.monotonic()
            frame = self._capture_frame()
            if frame is None:
                self._stop_event.wait(0.05)
                continue

            frame_idx += 1
            self._push_detect_frame(frame)
            self._update_preview(frame, frame_idx)

            elapsed = time.monotonic() - loop_start
            remaining = frame_period - elapsed
            if remaining > 0:
                self._stop_event.wait(remaining)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="nadi-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._picam is not None:
            try:
                self._picam.stop()
            except Exception:
                pass
            try:
                self._picam.close()
            except Exception:
                pass
        if self._cv_cap is not None:
            self._cv_cap.release()
