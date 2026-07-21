"""Async WebSocket server for frontend-backend data exchange."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from queue import Empty
from typing import Any, Callable, Optional

import websockets

from backend.config import WS_HOST, WS_PORT, WS_UPDATE_HZ
from backend.integrity.monitor import AttentionState, IntegrityMonitor


class WSServer:
    """WebSocket transport layer."""

    def __init__(
        self,
        *,
        distance_queue: Any,
        attention_queue: Any,
        integrity_queue: Any,
        preview_provider: Optional[Callable[[], Optional[bytes]]] = None,
        tested_eye: str = "OD",
    ) -> None:
        self._distance_queue = distance_queue
        self._attention_queue = attention_queue
        self._integrity_queue = integrity_queue
        self._preview_provider = preview_provider

        self._stop_event = asyncio.Event()
        self._server: Optional[websockets.server.Serve] = None
        self._broadcast_task: Optional[asyncio.Task[Any]] = None
        self._clients: set[Any] = set()

        self._last_distance: Optional[float] = None
        self._last_attention: dict[str, Any] = {}
        self._last_integrity: dict[str, Any] = {}

        self._integrity_monitor = IntegrityMonitor(
            tested_eye=tested_eye,  # type: ignore[arg-type]
            on_pause=self._on_pause,
            on_resume=self._on_resume,
        )

    def _push_integrity_snapshot(self) -> None:
        payload = {
            "paused": self._integrity_monitor.is_paused(),
            "pause_events": [
                {
                    "flag": event.flag.value,
                    "start_time": event.start_time,
                    "end_time": event.end_time,
                    "duration_s": event.duration_s,
                }
                for event in self._integrity_monitor.get_pause_events()
            ],
            "post_hoc_flags": [flag.value for flag in self._integrity_monitor.get_post_hoc_flags()],
        }
        self._last_integrity = payload
        try:
            self._integrity_queue.put_nowait(payload)
        except Exception:
            pass

    def _on_pause(self, flag: Any, message: str) -> None:
        self._last_integrity = {
            "paused": True,
            "active_flag": flag.value,
            "message": message,
        }
        self._push_integrity_snapshot()

    def _on_resume(self) -> None:
        self._last_integrity = {
            "paused": False,
            "active_flag": None,
            "message": "resumed",
        }
        self._push_integrity_snapshot()

    async def _client_handler(self, websocket: Any) -> None:
        self._clients.add(websocket)
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                event_type = msg.get("type")
                if event_type == "fullscreen":
                    self._integrity_monitor.check_fullscreen(bool(msg.get("is_fullscreen", True)))
                    self._push_integrity_snapshot()

                if event_type == "response":
                    start_d = float(msg.get("trial_start_distance_m", self._last_distance or 0.0))
                    end_d = float(msg.get("trial_end_distance_m", self._last_distance or 0.0))
                    response_time_ms = float(msg.get("response_time_ms", 0.0))
                    direction = str(msg.get("direction", "unknown"))
                    self._integrity_monitor.record_response(response_time_ms, direction, start_d, end_d)
                    self._push_integrity_snapshot()
        finally:
            self._clients.discard(websocket)

    @staticmethod
    def _drain_queue_nowait(q: Any, last_value: Any) -> Any:
        value = last_value
        while True:
            try:
                value = q.get_nowait()
            except Empty:
                return value
            except Exception:
                return value

    async def _broadcast_loop(self) -> None:
        tick = 1.0 / WS_UPDATE_HZ
        while not self._stop_event.is_set():
            self._last_distance = self._drain_queue_nowait(self._distance_queue, self._last_distance)
            self._last_attention = self._drain_queue_nowait(self._attention_queue, self._last_attention) or {}
            self._last_integrity = self._drain_queue_nowait(self._integrity_queue, self._last_integrity) or {}

            if self._last_attention:
                bbox = self._last_attention.get("bbox") or {}
                area = int(max(0.0, float(bbox.get("w", 0.0)) * float(bbox.get("h", 0.0))))
                state = AttentionState(
                    face_detected=bool(self._last_attention.get("face_detected", False)),
                    face_count=int(self._last_attention.get("num_faces", 0)),
                    head_yaw_deg=float(self._last_attention.get("head_yaw_deg", 0.0)),
                    left_eye_open=bool(self._last_attention.get("left_eye_open", False)),
                    right_eye_open=bool(self._last_attention.get("right_eye_open", False)),
                    face_box_area_px=area,
                )
                self._integrity_monitor.update_attention(state)
                self._push_integrity_snapshot()

            preview_b64 = None
            if self._preview_provider:
                preview = self._preview_provider()
                if preview:
                    preview_b64 = base64.b64encode(preview).decode("ascii")

            payload = {
                "distance_m": self._last_distance,
                "attention": self._last_attention,
                "integrity": self._last_integrity,
                "preview_jpeg_base64": preview_b64,
            }
            message = json.dumps(payload)

            if self._clients:
                await asyncio.gather(
                    *(client.send(message) for client in tuple(self._clients)),
                    return_exceptions=True,
                )

            await asyncio.sleep(tick)

    async def start(self) -> None:
        self._stop_event.clear()
        self._server = await websockets.serve(self._client_handler, WS_HOST, WS_PORT)
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        self._stop_event.set()

        if self._broadcast_task:
            self._broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._broadcast_task

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

        if self._clients:
            await asyncio.gather(
                *(client.close() for client in tuple(self._clients)),
                return_exceptions=True,
            )
