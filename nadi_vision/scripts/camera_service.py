"""NadiVision camera service — run with system Python (Picamera2/libcamera).

nadivision-camera.service executes this script under the Raspberry Pi OS
system Python so it can import libcamera / picamera2.

Architecture (Updates.md §2.2):
- Two independent Picamera2 streams are configured once at startup:
    inference : 320×240 RGB888  — sampled at 30 fps, every 5th frame published
    preview   : 1280×720 XRGB8888 — encoded to JPEG by Picamera2 at 5–10 fps
- Three named POSIX shared-memory slots (320×240 × 3 bytes each) hold the
  latest inference frames.  The application service attaches to them by name.
- A Unix-domain SOCK_SEQPACKET socket carries compact frame-metadata packets
  to the application service.  No pixel data crosses the socket.
- The preview stream is served as MJPEG over HTTP on port 8766 so the kiosk
  frontend can display it via a plain <img> src without any WebSocket relay.
- OpenCV is NOT imported or used anywhere in this module.

Usage (system Python):
    /usr/bin/python3 scripts/camera_service.py

Optional environment variables:
    NADI_INFER_W      inference frame width  (default: 320)
    NADI_INFER_H      inference frame height (default: 240)
    NADI_PREVIEW_W    preview width          (default: 1280)
    NADI_PREVIEW_H    preview height         (default: 720)
    NADI_CAM_FPS      capture frame rate     (default: 30)
    NADI_INFER_SKIP   publish every N frames (default: 5  → 6 Hz)
    NADI_PREVIEW_SKIP preview JPEG every N frames (default: 4 → ~7.5 fps)
    NADI_UDS_PATH     Unix-domain socket path (default: /run/nadivision/frames.sock)
    NADI_MJPEG_PORT   MJPEG HTTP port        (default: 8766)
    NADI_JPEG_QUALITY preview JPEG quality   (default: 65)
"""

from __future__ import annotations

import http.server
import io
import json
import os
import signal
import socket
import threading
import time
from multiprocessing.shared_memory import SharedMemory
from typing import Optional

from picamera2 import Picamera2

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_INFER_W = int(os.getenv("NADI_INFER_W", "320"))
_INFER_H = int(os.getenv("NADI_INFER_H", "240"))
_PREVIEW_W = int(os.getenv("NADI_PREVIEW_W", "1280"))
_PREVIEW_H = int(os.getenv("NADI_PREVIEW_H", "720"))
_CAM_FPS = int(os.getenv("NADI_CAM_FPS", "30"))
_INFER_SKIP = int(os.getenv("NADI_INFER_SKIP", "5"))       # 30 / 5 = 6 Hz inference
_PREVIEW_SKIP = int(os.getenv("NADI_PREVIEW_SKIP", "4"))   # ~7.5 fps preview
_UDS_PATH = os.getenv("NADI_UDS_PATH", "/run/nadivision/frames.sock")
_MJPEG_PORT = int(os.getenv("NADI_MJPEG_PORT", "8766"))
_JPEG_QUALITY = int(os.getenv("NADI_JPEG_QUALITY", "65"))
_SLOT_COUNT = 3
_INFER_BYTES = _INFER_W * _INFER_H * 3


# ---------------------------------------------------------------------------
# Shared-memory ring
# ---------------------------------------------------------------------------

class _SharedMemoryRing:
    """Three fixed POSIX shared-memory slots for RGB inference frames."""

    def __init__(self) -> None:
        self._slots: list[SharedMemory] = []
        for i in range(_SLOT_COUNT):
            shm = SharedMemory(
                name=f"nadi_frame_{i}",
                create=True,
                size=_INFER_BYTES,
            )
            self._slots.append(shm)

    def write(self, slot: int, rgb_bytes: bytes) -> None:
        self._slots[slot].buf[:_INFER_BYTES] = rgb_bytes

    def cleanup(self) -> None:
        for shm in self._slots:
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# MJPEG preview server
# ---------------------------------------------------------------------------

class _MJPEGOutput:
    """Thread-safe buffer for the latest preview JPEG frame."""

    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._frame: Optional[bytes] = None

    def write(self, buf: bytes) -> None:
        with self._lock:
            self._frame = bytes(buf)
            self._lock.notify_all()

    def get_frame(self, timeout: float = 1.0) -> Optional[bytes]:
        with self._lock:
            self._lock.wait(timeout)
            return self._frame


_mjpeg_output = _MJPEGOutput()


class _MJPEGHandler(http.server.BaseHTTPRequestHandler):
    """Serves a multipart/x-mixed-replace MJPEG stream at /preview."""

    def log_message(self, *_: object) -> None:  # suppress access logs
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/preview":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "multipart/x-mixed-replace; boundary=nadivision_frame",
        )
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            while True:
                frame = _mjpeg_output.get_frame(timeout=2.0)
                if frame is None:
                    continue
                boundary = b"--nadivision_frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                header = boundary + str(len(frame)).encode() + b"\r\n\r\n"
                self.wfile.write(header + frame + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def _start_mjpeg_server() -> None:
    server = http.server.HTTPServer(("0.0.0.0", _MJPEG_PORT), _MJPEGHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


# ---------------------------------------------------------------------------
# Unix-domain socket (SEQPACKET) server
# ---------------------------------------------------------------------------

class _UDSServer:
    """Accepts one consumer connection and forwards frame-metadata packets."""

    def __init__(self) -> None:
        os.makedirs(os.path.dirname(_UDS_PATH), exist_ok=True)
        try:
            os.unlink(_UDS_PATH)
        except FileNotFoundError:
            pass
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self._server.bind(_UDS_PATH)
        self._server.listen(1)
        self._server.setblocking(False)
        self._conn: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def accept_if_pending(self) -> None:
        try:
            conn, _ = self._server.accept()
            conn.setblocking(False)
            with self._lock:
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                self._conn = conn
        except BlockingIOError:
            pass

    def send_meta(self, meta: dict) -> None:
        with self._lock:
            conn = self._conn
        if conn is None:
            return
        payload = json.dumps(meta).encode()
        try:
            conn.send(payload)
        except (BrokenPipeError, BlockingIOError, OSError):
            with self._lock:
                try:
                    conn.close()
                except Exception:
                    pass
                self._conn = None

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
        try:
            self._server.close()
        except Exception:
            pass
        try:
            os.unlink(_UDS_PATH)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Picamera2 capture loop
# ---------------------------------------------------------------------------

def _build_camera_config(cam: Picamera2) -> object:
    """Configure two independent streams — inference (RGB888) + preview (XRGB8888)."""
    return cam.create_video_configuration(
        main={"size": (_INFER_W, _INFER_H), "format": "RGB888"},
        lores={"size": (_PREVIEW_W, _PREVIEW_H), "format": "XRGB8888"},
        controls={"FrameRate": _CAM_FPS},
        buffer_count=4,
    )


def _run(stop_event: threading.Event) -> None:
    ring = _SharedMemoryRing()
    uds = _UDSServer()
    _start_mjpeg_server()

    cam = Picamera2()
    cfg = _build_camera_config(cam)
    cam.configure(cfg)
    cam.start()
    time.sleep(0.8)  # allow AEC/AWB to settle

    seq = 0
    infer_frame_idx = 0
    preview_frame_idx = 0

    try:
        while not stop_event.is_set():
            ts_start = time.monotonic()

            # Accept a new consumer connection if one is pending
            uds.accept_if_pending()

            # Capture inference frame (320×240 RGB888) from main stream
            infer_frame = cam.capture_array("main")   # shape (H, W, 3) RGB
            infer_frame_idx += 1

            # Capture preview frame (1280×720 XRGB8888) from lores stream
            preview_frame_idx += 1

            # Publish inference frame every INFER_SKIP captures (→ 6 Hz)
            if infer_frame_idx % _INFER_SKIP == 0:
                seq += 1
                slot = (seq - 1) % _SLOT_COUNT
                ring.write(slot, bytes(infer_frame.tobytes()))
                uds.send_meta({
                    "seq": seq,
                    "slot": slot,
                    "ts": ts_start,
                    "w": _INFER_W,
                    "h": _INFER_H,
                    "stride": _INFER_W * 3,
                    "colour_order": "RGB",
                })

            # Encode and publish preview JPEG every PREVIEW_SKIP captures
            if preview_frame_idx % _PREVIEW_SKIP == 0:
                # lores XRGB8888: drop the X channel to get RGB
                lores_frame = cam.capture_array("lores")
                if lores_frame is not None and lores_frame.ndim == 3:
                    # XRGB8888 has 4 channels; channels 1-3 are RGB
                    rgb_preview = lores_frame[:, :, 1:4]
                    _encode_preview_jpeg(rgb_preview)

            # Pace the loop to the camera frame rate
            elapsed = time.monotonic() - ts_start
            remaining = (1.0 / _CAM_FPS) - elapsed
            if remaining > 0:
                time.sleep(remaining)

    finally:
        cam.stop()
        cam.close()
        uds.close()
        ring.cleanup()


def _encode_preview_jpeg(rgb: "np.ndarray") -> None:
    """Encode an RGB NumPy array to JPEG using Pillow (python3-pil on Pi OS)."""
    try:
        buf = io.BytesIO()
        # Import PIL only if available (it is on Pi OS Bookworm via python3-pil)
        try:
            from PIL import Image
            img = Image.fromarray(rgb, mode="RGB")
            img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=False)
            _mjpeg_output.write(buf.getvalue())
        except ImportError:
            pass  # PIL not available — preview disabled, inference continues
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    stop_event = threading.Event()

    def _on_signal(*_: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    _run(stop_event)


if __name__ == "__main__":
    main()
