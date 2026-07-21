"""Main entry point for Nadi Vision backend process orchestration.

This module is the sole owner of subsystem startup/shutdown:
- Ultrasonic thread
- Camera thread
- Vision inference process
- Integrity monitor task
- WebSocket server
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from queue import Queue
import signal
import threading

from backend.config import (
    ATTENTION_QUEUE_MAXSIZE,
    DISTANCE_QUEUE_MAXSIZE,
    FACE_DETECT_FRAME_SKIP,
    FRAME_QUEUE_MAXSIZE,
    INTEGRITY_QUEUE_MAXSIZE,
)
from backend.sensors.camera import BridgeCameraWorker, CameraWorker
from backend.sensors.ultrasonic import UltrasonicWorker
from backend.server.ws_server import WSServer
from backend.vision.face_detection import FaceInferenceWorker


async def _run_backend() -> None:
    """Start all backend subsystems and block until shutdown signal."""
    stop_event = threading.Event()
    mp_stop_event = multiprocessing.Event()

    distance_queue: Queue = Queue(maxsize=DISTANCE_QUEUE_MAXSIZE)
    integrity_queue: Queue = Queue(maxsize=INTEGRITY_QUEUE_MAXSIZE)

    # Multiprocessing queues are required between camera thread and inference process.
    frame_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=FRAME_QUEUE_MAXSIZE)
    attention_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=ATTENTION_QUEUE_MAXSIZE)

    camera_mode = os.getenv("NADI_CAMERA_MODE", "native").strip().lower()
    if camera_mode == "bridge":
        bridge_path = os.getenv("NADI_BRIDGE_FRAME_PATH", "/tmp/nadi_bridge/latest.jpg")
        bridge_hz = float(os.getenv("NADI_BRIDGE_POLL_HZ", "30"))
        camera_worker = BridgeCameraWorker(
            frame_queue=frame_queue,
            frame_path=bridge_path,
            poll_hz=bridge_hz,
        )
    else:
        camera_worker = CameraWorker(frame_queue=frame_queue)
    ultrasonic_worker = UltrasonicWorker(distance_queue=distance_queue)
    inference_worker = FaceInferenceWorker(
        frame_queue=frame_queue,
        attention_queue=attention_queue,
        stop_event=mp_stop_event,
        frame_skip=FACE_DETECT_FRAME_SKIP,
    )

    ws_server = WSServer(
        distance_queue=distance_queue,
        attention_queue=attention_queue,
        integrity_queue=integrity_queue,
        preview_provider=camera_worker.get_latest_preview_jpeg,
    )

    def _on_signal(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: _on_signal())

    ultrasonic_worker.start()
    camera_worker.start()
    inference_worker.start()
    await ws_server.start()

    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.2)
    finally:
        await ws_server.stop()
        inference_worker.stop()
        camera_worker.stop()
        ultrasonic_worker.stop()


def main() -> None:
    asyncio.run(_run_backend())


if __name__ == "__main__":
    main()
