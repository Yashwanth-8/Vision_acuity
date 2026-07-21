"""Picamera2 frame producer for split-interpreter Raspberry Pi deployments.

Run this script with system Python (3.13) so it can import libcamera/picamera2.
It writes the latest frame atomically to a shared JPEG path read by
`BridgeCameraWorker` in the backend (Python 3.11 + MediaPipe).

Usage:
    /usr/bin/python3 scripts/camera_bridge_producer.py \
      --out /tmp/nadi_bridge/latest.jpg --width 1280 --height 720 --fps 30
"""

from __future__ import annotations

import argparse
import os
import time

import cv2
from picamera2 import Picamera2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/nadi_bridge/latest.jpg")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--jpeg-quality", type=int, default=80)
    args = parser.parse_args()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_path = out_path + ".tmp"

    cam = Picamera2()
    cfg = cam.create_preview_configuration(
        main={"size": (args.width, args.height), "format": "RGB888"},
        controls={"FrameRate": args.fps},
    )
    cam.configure(cfg)
    cam.start()
    time.sleep(0.8)

    frame_period = 1.0 / max(args.fps, 1.0)
    try:
        while True:
            loop_start = time.monotonic()
            frame = cam.capture_array()
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            ok = cv2.imwrite(
                tmp_path,
                bgr,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(args.jpeg_quality)],
            )
            if ok:
                os.replace(tmp_path, out_path)

            elapsed = time.monotonic() - loop_start
            remaining = frame_period - elapsed
            if remaining > 0:
                time.sleep(remaining)
    finally:
        cam.stop()
        cam.close()


if __name__ == "__main__":
    main()
