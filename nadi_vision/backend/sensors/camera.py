"""Camera frame consumer for the NadiVision application service.

Connects to the shared-memory ring and Unix-domain socket created by
nadivision-camera.service (scripts/camera_service.py, system Python + Picamera2).

Design (per Updates.md §2.2 and §2.3):
- Three named POSIX shared-memory slots hold 320×240 RGB frames.
- The camera service writes each sampled frame into the next slot and sends a
  compact JSON metadata packet over a Unix-domain SOCK_SEQPACKET socket.
- This consumer attaches to the slots once, then reads slot index from each
  metadata packet.  One NumPy array copy is made per delivered frame — the
  shared-memory buffer itself is never modified by the consumer.
- OpenCV is NOT used anywhere in this module.
- If the camera service is not running, the consumer retries silently; the
  inference worker will produce "no face" state until frames resume.

Preview JPEG is served by the camera service over HTTP/MJPEG at port 8766.
The frontend connects to that endpoint directly — no base64 relay through the
application WebSocket.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from multiprocessing import Queue as MPQueue
from multiprocessing.shared_memory import SharedMemory
from typing import Optional

import numpy as np

from backend.scoring.constants import INFER_HEIGHT, INFER_WIDTH

_SLOT_COUNT = 3
_INFER_W = INFER_WIDTH
_INFER_H = INFER_HEIGHT
_INFER_BYTES = _INFER_W * _INFER_H * 3          # RGB888
_UDS_PATH = "/run/nadivision/frames.sock"
_RECONNECT_DELAY_S = 2.0
_RECV_TIMEOUT_S = 1.0


class SharedMemoryCameraConsumer:
    """Reads RGB frames from the camera service via shared memory + UDS.

    Usage::

        consumer = SharedMemoryCameraConsumer(frame_queue)
        consumer.start()
        # … run inference loop …
        consumer.stop()
    """

    def __init__(self, frame_queue: MPQueue) -> None:
        self._frame_queue = frame_queue
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._slots: list[SharedMemory] = []

    # ------------------------------------------------------------------
    # Shared-memory management
    # ------------------------------------------------------------------

    def _attach_slots(self) -> bool:
        """Attach to all named shared-memory slots created by the camera service."""
        attached: list[SharedMemory] = []
        try:
            for i in range(_SLOT_COUNT):
                shm = SharedMemory(name=f"nadi_frame_{i}", create=False)
                attached.append(shm)
            self._slots = attached
            return True
        except Exception:
            for s in attached:
                try:
                    s.close()
                except Exception:
                    pass
            return False

    def _detach_slots(self) -> None:
        for shm in self._slots:
            try:
                shm.close()
            except Exception:
                pass
        self._slots = []

    # ------------------------------------------------------------------
    # Frame queue helpers
    # ------------------------------------------------------------------

    def _push_frame(self, frame: np.ndarray) -> None:
        """Push a frame, discarding the oldest if the queue is full (drop-oldest)."""
        try:
            self._frame_queue.put_nowait(frame)
        except Exception:
            try:
                self._frame_queue.get_nowait()
            except Exception:
                pass
            try:
                self._frame_queue.put_nowait(frame)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Main worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            # Wait for the camera service to create its shared-memory slots.
            if not self._attach_slots():
                self._stop_event.wait(_RECONNECT_DELAY_S)
                continue

            # Connect to the Unix-domain SEQPACKET socket.
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            try:
                sock.connect(_UDS_PATH)
                sock.settimeout(_RECV_TIMEOUT_S)
            except Exception:
                sock.close()
                self._detach_slots()
                self._stop_event.wait(_RECONNECT_DELAY_S)
                continue

            try:
                while not self._stop_event.is_set():
                    try:
                        raw = sock.recv(512)
                    except socket.timeout:
                        continue
                    except Exception:
                        break

                    if not raw:
                        break

                    try:
                        meta = json.loads(raw)
                    except Exception:
                        continue

                    slot = int(meta.get("slot", -1))
                    if slot < 0 or slot >= len(self._slots):
                        continue

                    # Zero-copy read: wrap shm buffer as a NumPy view, then copy
                    # once into the queue.  The camera service may overwrite this
                    # slot at any time, so we must copy before yielding the array.
                    frame = np.frombuffer(
                        self._slots[slot].buf,
                        dtype=np.uint8,
                        count=_INFER_BYTES,
                    ).reshape((_INFER_H, _INFER_W, 3)).copy()

                    self._push_frame(frame)

            finally:
                try:
                    sock.close()
                except Exception:
                    pass
                self._detach_slots()

            if not self._stop_event.is_set():
                self._stop_event.wait(_RECONNECT_DELAY_S)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_latest_preview_jpeg(self) -> Optional[bytes]:
        """Preview JPEG is served by the camera service over HTTP/MJPEG (port 8766).

        The frontend connects to http://localhost:8766/preview directly.
        This method is kept for interface compatibility but always returns None.
        """
        return None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="nadi-camera-consumer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._detach_slots()
