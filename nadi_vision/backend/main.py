"""Main entry point for Nadi Vision backend process orchestration.

This module is the sole owner of subsystem startup/shutdown:
- Shared-memory camera consumer (attaches to nadivision-camera.service)
- Ultrasonic thread
- Vision inference process
- Integrity monitor task (owned by WSServer)
- WebSocket server
"""

from __future__ import annotations

import asyncio
import multiprocessing
import signal
import threading

from backend.config import (
    ATTENTION_QUEUE_MAXSIZE,
    DISTANCE_QUEUE_MAXSIZE,
    FACE_DETECT_FRAME_SKIP,
    FRAME_QUEUE_MAXSIZE,
)
from backend.sensors.camera import SharedMemoryCameraConsumer
from backend.sensors.ultrasonic import UltrasonicWorker
from backend.server.ws_server import WSServer
from backend.vision.face_detection import FaceInferenceWorker
from queue import Queue


async def _run_backend() -> None:
    """Start all backend subsystems and block until shutdown signal."""
    stop_event = threading.Event()
    mp_stop_event = multiprocessing.Event()
    mp_single_eye = multiprocessing.Value("b", 0)

    distance_queue: Queue = Queue(maxsize=DISTANCE_QUEUE_MAXSIZE)

    # Multiprocessing queues are required between camera consumer thread and
    # the inference subprocess.
    frame_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=FRAME_QUEUE_MAXSIZE)
    attention_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=ATTENTION_QUEUE_MAXSIZE)

    camera_consumer = SharedMemoryCameraConsumer(frame_queue=frame_queue)
    ultrasonic_worker = UltrasonicWorker(distance_queue=distance_queue)
    inference_worker = FaceInferenceWorker(
        frame_queue=frame_queue,
        attention_queue=attention_queue,
        stop_event=mp_stop_event,
        frame_skip=FACE_DETECT_FRAME_SKIP,
        single_eye_flag=mp_single_eye,
    )

    ws_server = WSServer(
        distance_queue=distance_queue,
        attention_queue=attention_queue,
        single_eye_flag=mp_single_eye,
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
    camera_consumer.start()
    inference_worker.start()
    await ws_server.start()

    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.2)
    finally:
        await ws_server.stop()
        inference_worker.stop()
        camera_consumer.stop()
        ultrasonic_worker.stop()


def main() -> None:
    asyncio.run(_run_backend())


if __name__ == "__main__":
    main()
