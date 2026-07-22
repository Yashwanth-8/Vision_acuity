"""Async WebSocket server — full session protocol (Updates.md §5.1).

Versioned message contract:
  UI  → app   session.start      eye, correction, consent, patient metadata
  UI  → app   trial.answer       opaque trial token, direction, response_time_ms
  UI  → app   ui.fullscreen      is_fullscreen (bool)
  UI  → app   ui.visibility      visible (bool)
  app → UI    session.state      continuous broadcast: trial params, hold, distance
  app → UI    report.ready       final structured report when session terminates

The backend generates every trial direction and opaque token.
The frontend NEVER receives a correct-answer key, calculates an acuity result,
or decides a hold state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import random
import secrets
import time
from queue import Empty
from typing import Any, Dict, Literal, Optional

import websockets

from backend.config import WS_HOST, WS_PORT, WS_UPDATE_HZ
from backend.integrity.monitor import AttentionState, IntegrityMonitor
from backend.report.generator import ReportGenerator
from backend.scoring.engine import AcuitySession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DIRECTIONS: list[str] = ["up", "down", "left", "right"]

# logMAR → arcMinutes per stroke (ISO 8596, 14 lines)
_ARCMIN: Dict[float, float] = {
    1.0: 10.000, 0.9: 7.943, 0.8: 6.310, 0.7: 5.012,
    0.6: 3.981,  0.5: 3.162, 0.4: 2.512, 0.3: 1.995,
    0.2: 1.585,  0.1: 1.259, 0.0: 1.000, -0.1: 0.794,
   -0.2: 0.631, -0.3: 0.501,
}
_E_STROKES = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_token() -> str:
    return secrets.token_urlsafe(12)


def _random_direction(last: Optional[str] = None) -> str:
    available = [d for d in _DIRECTIONS if d != last]
    return random.choice(available)


def _e_height_mm(distance_m: float, logmar: float) -> float:
    """Physical Tumbling E height in mm at the given distance and logMAR."""
    arc_min = _ARCMIN.get(round(logmar, 1), 1.0)
    rad = (arc_min * _E_STROKES * math.pi) / (180.0 * 60.0)
    return distance_m * math.tan(rad) * 1000.0


# ---------------------------------------------------------------------------
# Per-session state container
# ---------------------------------------------------------------------------

class _ActiveSession:
    """Holds the mutable state of one OD/OS acuity session."""

    def __init__(
        self,
        eye: Literal["OD", "OS"],
        correction: Literal["UCVA", "BCVA"],
    ) -> None:
        self.eye = eye
        self.correction = correction
        self.acuity = AcuitySession(eye, correction)
        self.current_token: str = _new_token()
        self.current_direction: str = _random_direction()
        self.trial_start_time: float = time.monotonic()
        self.trial_start_distance: Optional[float] = None
        self.answered_tokens: set[str] = set()
        self.terminated: bool = False


# ---------------------------------------------------------------------------
# WebSocket server
# ---------------------------------------------------------------------------

class WSServer:
    """WebSocket transport and session protocol."""

    def __init__(
        self,
        *,
        distance_queue: Any,
        attention_queue: Any,
        single_eye_flag: Any,
    ) -> None:
        self._distance_queue = distance_queue
        self._attention_queue = attention_queue
        self._single_eye_flag = single_eye_flag

        self._stop_event = asyncio.Event()
        self._server: Optional[Any] = None
        self._broadcast_task: Optional[asyncio.Task[Any]] = None
        self._clients: set[Any] = set()

        self._last_distance: Optional[float] = None
        self._last_attention: Dict[str, Any] = {}

        self._session: Optional[_ActiveSession] = None
        self._integrity_monitor = IntegrityMonitor(
            tested_eye="OD",
            on_pause=self._on_pause,
            on_resume=self._on_resume,
        )
        self._hold_message: Optional[str] = None
        self._report_gen = ReportGenerator()

    # ------------------------------------------------------------------
    # Integrity callbacks
    # ------------------------------------------------------------------

    def _on_pause(self, flag: Any, message: str) -> None:
        self._hold_message = message

    def _on_resume(self) -> None:
        self._hold_message = None

    # ------------------------------------------------------------------
    # Queue helper
    # ------------------------------------------------------------------

    @staticmethod
    def _drain_queue(q: Any, last: Any) -> Any:
        value = last
        while True:
            try:
                value = q.get_nowait()
            except (Empty, Exception):
                return value

    # ------------------------------------------------------------------
    # Session message handlers
    # ------------------------------------------------------------------

    async def _handle_session_start(self, msg: Dict[str, Any]) -> None:
        eye = str(msg.get("eye", "OD"))
        correction = str(msg.get("correction", "UCVA"))

        # Rebuild integrity monitor for the new eye side.
        # Enable the fellow-eye occlusion check now that a side is committed.
        self._integrity_monitor = IntegrityMonitor(
            tested_eye=eye,  # type: ignore[arg-type]
            on_pause=self._on_pause,
            on_resume=self._on_resume,
            fellow_eye_check_enabled=True,
        )
        self._hold_message = None

        sess = _ActiveSession(
            eye=eye,  # type: ignore[arg-type]
            correction=correction,  # type: ignore[arg-type]
        )
        self._single_eye_flag.value = 1
        if self._last_distance:
            sess.trial_start_distance = self._last_distance
            self._integrity_monitor.mark_trial_start_distance(self._last_distance)
        self._session = sess

    async def _handle_trial_answer(self, msg: Dict[str, Any]) -> None:
        sess = self._session
        if sess is None or sess.terminated:
            return

        token = str(msg.get("token", ""))
        if token != sess.current_token:
            return  # stale or replayed token — ignore

        if token in sess.answered_tokens:
            return  # duplicate submission
        sess.answered_tokens.add(token)

        # Integrity hold blocks answer acceptance
        if self._integrity_monitor.is_paused():
            return

        direction = str(msg.get("direction", ""))
        response_time_ms = float(msg.get("response_time_ms", 0.0))
        distance_m = float(self._last_distance or 0.0)
        start_d = float(sess.trial_start_distance or distance_m)

        # Post-hoc integrity flags for this response
        self._integrity_monitor.record_response(
            response_time_ms, direction, start_d, distance_m
        )

        # Score the answer on the backend AcuitySession
        sess.acuity.record_response(
            presented=sess.current_direction,
            answered=direction,
            distance_m=distance_m,
            response_time_ms=response_time_ms,
        )

        if sess.acuity.should_terminate():
            sess.terminated = True
            # Disable fellow-eye check — session is over
            self._integrity_monitor.set_fellow_eye_check_enabled(False)
            await self._emit_report(sess)
            return

        # Advance to next trial
        sess.current_token = _new_token()
        sess.current_direction = _random_direction(sess.current_direction)
        sess.trial_start_time = time.monotonic()
        sess.trial_start_distance = distance_m
        self._integrity_monitor.mark_trial_start_distance(distance_m)

    async def _emit_report(self, sess: _ActiveSession) -> None:
        self._single_eye_flag.value = 0
        result = sess.acuity.get_result()
        payload = self._report_gen.build({
            "sessions": [result],
            "distance_statement_m": result.avg_distance_m,
            "integrity_flags": [
                f.value for f in self._integrity_monitor.get_post_hoc_flags()
            ],
        })
        message = json.dumps({"type": "report.ready", "report": payload})
        if self._clients:
            await asyncio.gather(
                *(c.send(message) for c in tuple(self._clients)),
                return_exceptions=True,
            )

    # ------------------------------------------------------------------
    # Client handler
    # ------------------------------------------------------------------

    async def _client_handler(self, websocket: Any) -> None:
        self._clients.add(websocket)
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                t = msg.get("type")
                if t == "session.start":
                    await self._handle_session_start(msg)
                elif t == "trial.answer":
                    await self._handle_trial_answer(msg)
                elif t == "ui.fullscreen":
                    self._integrity_monitor.check_fullscreen(
                        bool(msg.get("is_fullscreen", True))
                    )
                elif t == "ui.visibility":
                    # Treat tab-hidden as a fullscreen-exit equivalent
                    visible = bool(msg.get("visible", True))
                    self._integrity_monitor.check_fullscreen(visible)
        finally:
            self._clients.discard(websocket)

    # ------------------------------------------------------------------
    # session.state payload builder
    # ------------------------------------------------------------------

    def _build_session_state(self) -> Dict[str, Any]:
        dist = self._last_distance
        paused = self._integrity_monitor.is_paused()

        state: Dict[str, Any] = {
            "type": "session.state",
            "distance_m": dist,
            "hold": {
                "paused": paused,
                "warning": self._integrity_monitor.is_warned(),
                "message": self._hold_message if paused else None,
            },
            "attention": {
                "face_detected": bool(self._last_attention.get("face_detected", False)),
                "num_faces": int(self._last_attention.get("num_faces", 0)),
            },
        }

        sess = self._session
        if sess is None:
            state["session_status"] = "idle"
            return state

        if sess.terminated:
            state["session_status"] = "complete"
            return state

        logmar = sess.acuity.get_current_logmar()
        size_distance_m = sess.trial_start_distance or dist or 1.0
        e_mm = _e_height_mm(size_distance_m, logmar)

        state.update({
            "session_status": "active",
            "eye": sess.eye,
            "trial_token": sess.current_token,
            "direction": sess.current_direction,
            "logmar": logmar,
            "e_height_mm": round(e_mm, 3),
            "total_correct": sess.acuity.get_total_correct(),
            "logmar_estimate": round(sess.acuity.get_logmar_estimate(), 3),
        })
        return state

    # ------------------------------------------------------------------
    # Broadcast loop
    # ------------------------------------------------------------------

    async def _broadcast_loop(self) -> None:
        tick = 1.0 / WS_UPDATE_HZ
        while not self._stop_event.is_set():
            # Drain queues — latest-value-wins policy
            self._last_distance = self._drain_queue(
                self._distance_queue, self._last_distance
            )
            new_attn = self._drain_queue(self._attention_queue, None)
            if new_attn:
                self._last_attention = new_attn
                attn = AttentionState(
                    face_detected=bool(self._last_attention.get("face_detected", False)),
                    face_count=int(self._last_attention.get("num_faces", 0)),
                    head_yaw_deg=float(self._last_attention.get("head_yaw_deg", 0.0)),
                    left_eye_open=bool(self._last_attention.get("left_eye_open", False)),
                    right_eye_open=bool(self._last_attention.get("right_eye_open", False)),
                )
                self._integrity_monitor.update_attention(attn)

            if self._last_distance is not None:
                self._integrity_monitor.update_distance(self._last_distance)

            payload = self._build_session_state()
            message = json.dumps(payload)

            if self._clients:
                await asyncio.gather(
                    *(c.send(message) for c in tuple(self._clients)),
                    return_exceptions=True,
                )

            await asyncio.sleep(tick)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._stop_event.clear()
        self._server = await websockets.serve(
            self._client_handler, WS_HOST, WS_PORT
        )
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        self._single_eye_flag.value = 0

        if self._broadcast_task:
            self._broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._broadcast_task

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

        if self._clients:
            await asyncio.gather(
                *(c.close() for c in tuple(self._clients)),
                return_exceptions=True,
            )
