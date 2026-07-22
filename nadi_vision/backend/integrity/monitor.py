"""Integrity monitor state machine for test pause/resume decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import statistics
import time
from typing import Callable, List, Literal, Optional

from backend.scoring.constants import (
    DISTANCE_DRIFT_TOLERANCE_M,
    DISTANCE_STABILITY_HOLD_S,
    DISTANCE_STABILITY_WINDOW_M,
    FACE_LOSS_DEBOUNCE_S,
    FAST_ANSWER_THRESHOLD_MS,
    FELLOW_EYE_DEBOUNCE_S,
    GAZE_OFF_DEBOUNCE_S,
    GAZE_YAW_THRESHOLD_DEG,
    RESUME_STABILITY_HOLD_S,
)


class IntegrityFlag(Enum):
    FACE_LOSS = "face_loss"
    MULTIPLE_FACES = "multiple_faces"
    GAZE_OFF_SCREEN = "gaze_off_screen"
    FELLOW_EYE_OPEN = "fellow_eye_open"
    FAST_ANSWER = "fast_answer"
    FULLSCREEN_EXIT = "fullscreen_exit"
    ANSWER_PATTERN_SUSPICIOUS = "answer_pattern_suspicious"
    DISTANCE_FACE_MISMATCH = "distance_face_mismatch"
    SCRIPTED_TIMING_SUSPECTED = "scripted_timing_suspected"
    DISTANCE_DRIFT_MID_TRIAL = "distance_drift_mid_trial"
    DISTANCE_UNSTABLE = "distance_unstable"
    DISTANCE_MOVED = "distance_moved"


@dataclass
class PauseEvent:
    flag: IntegrityFlag
    start_time: float
    end_time: Optional[float] = None
    duration_s: Optional[float] = None


@dataclass
class AttentionState:
    face_detected: bool
    face_count: int
    head_yaw_deg: float
    left_eye_open: bool
    right_eye_open: bool
    face_box_area_px: int


class DebounceTimer:
    def __init__(self, threshold_s: float) -> None:
        self.threshold_s = threshold_s
        self._start: Optional[float] = None

    def update(self, in_violation: bool, now: float) -> bool:
        if in_violation:
            if self._start is None:
                self._start = now
            return (now - self._start) >= self.threshold_s
        self._start = None
        return False

    def reset(self) -> None:
        self._start = None


class IntegrityMonitor:
    """Owns pause/resume transitions and integrity event logging."""

    def __init__(
        self,
        tested_eye: Literal["OD", "OS"],
        on_pause: Callable[[IntegrityFlag, str], None],
        on_resume: Callable[[], None],
        time_fn: Callable[[], float] | None = None,
        fellow_eye_check_enabled: bool = False,
    ) -> None:
        self.tested_eye = tested_eye
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._time_fn = time_fn or time.monotonic
        # Fellow-eye check is disabled until a session starts.
        # This prevents false holds on the pre-session camera-setup screen.
        self._fellow_eye_check_enabled = fellow_eye_check_enabled

        self._paused = False
        self._active_pause_flags: set[IntegrityFlag] = set()
        self._pause_events: List[PauseEvent] = []
        self._current_pause_event: Optional[PauseEvent] = None

        self._face_loss_timer = DebounceTimer(FACE_LOSS_DEBOUNCE_S)
        self._gaze_timer = DebounceTimer(GAZE_OFF_DEBOUNCE_S)
        self._fellow_eye_timer = DebounceTimer(FELLOW_EYE_DEBOUNCE_S)
        self._resume_timer = DebounceTimer(RESUME_STABILITY_HOLD_S)
        self._distance_stability_timer = DebounceTimer(DISTANCE_STABILITY_HOLD_S)

        self._fullscreen_ok = True

        # Distance tracking
        self._distance_anchor: Optional[float] = None
        self._trial_start_distance: Optional[float] = None

        self._response_directions: List[str] = []
        self._response_times_ms: List[float] = []
        self._post_hoc_flags: set[IntegrityFlag] = set()

    def _pause_message(self, flag: IntegrityFlag) -> str:
        if flag == IntegrityFlag.FACE_LOSS:
            return "Face the screen to continue"
        if flag == IntegrityFlag.MULTIPLE_FACES:
            return "Only one person should be in frame"
        if flag == IntegrityFlag.GAZE_OFF_SCREEN:
            return "Please look at the screen"
        if flag == IntegrityFlag.FELLOW_EYE_OPEN:
            # Name the specific eye the patient must cover
            fellow_side = "left" if self.tested_eye == "OD" else "right"
            return f"Cover your {fellow_side} eye with your hand to continue"
        if flag == IntegrityFlag.FULLSCREEN_EXIT:
            return "Please return to fullscreen"
        if flag == IntegrityFlag.DISTANCE_UNSTABLE:
            return "Hold still while we set your test distance"
        if flag == IntegrityFlag.DISTANCE_MOVED:
            return "Please return to position and hold still"
        return "Integrity condition triggered"

    def _trigger_pause(self, flag: IntegrityFlag, now: float) -> None:
        self._active_pause_flags.add(flag)
        if self._paused:
            return

        self._paused = True
        self._current_pause_event = PauseEvent(flag=flag, start_time=now)
        self._pause_events.append(self._current_pause_event)
        self._resume_timer.reset()
        self._on_pause(flag, self._pause_message(flag))

    def _clear_pause_flag(self, flag: IntegrityFlag) -> None:
        if flag in self._active_pause_flags:
            self._active_pause_flags.remove(flag)

    def update_attention(self, state: AttentionState) -> None:
        now = self._time_fn()

        if state.face_count > 1:
            self._trigger_pause(IntegrityFlag.MULTIPLE_FACES, now)
        else:
            self._clear_pause_flag(IntegrityFlag.MULTIPLE_FACES)

        face_loss = self._face_loss_timer.update(not state.face_detected, now)
        if face_loss:
            self._trigger_pause(IntegrityFlag.FACE_LOSS, now)
        else:
            self._clear_pause_flag(IntegrityFlag.FACE_LOSS)

        gaze_off = self._gaze_timer.update(abs(state.head_yaw_deg) > GAZE_YAW_THRESHOLD_DEG, now)
        if gaze_off:
            self._trigger_pause(IntegrityFlag.GAZE_OFF_SCREEN, now)
        else:
            self._clear_pause_flag(IntegrityFlag.GAZE_OFF_SCREEN)

        fellow_eye_open = state.left_eye_open if self.tested_eye == "OD" else state.right_eye_open
        # Only enforce fellow-eye check once session.start has been received
        if self._fellow_eye_check_enabled:
            fellow_flag = self._fellow_eye_timer.update(fellow_eye_open, now)
            if fellow_flag:
                self._trigger_pause(IntegrityFlag.FELLOW_EYE_OPEN, now)
            else:
                self._clear_pause_flag(IntegrityFlag.FELLOW_EYE_OPEN)
        else:
            self._clear_pause_flag(IntegrityFlag.FELLOW_EYE_OPEN)
            self._fellow_eye_timer.reset()

        self._update_resume_state(now)

    def update_distance(self, distance_m: float) -> None:
        """Track distance and trial-level drift without sticky stability holds."""
        now = self._time_fn()

        # Keep a rolling anchor for informational stability, but do not pause the
        # session on micro-variation. This avoids long/sticky DISTANCE_UNSTABLE holds.
        if self._distance_anchor is None:
            self._distance_anchor = distance_m

        within_window = abs(distance_m - self._distance_anchor) <= DISTANCE_STABILITY_WINDOW_M
        if not within_window:
            # Patient moved; reset anchor
            self._distance_anchor = distance_m
        self._distance_stability_timer.update(within_window, now)
        self._clear_pause_flag(IntegrityFlag.DISTANCE_UNSTABLE)

        # Trial-level drift: hold if moved beyond tolerance since trial started
        if self._trial_start_distance is not None:
            drifted = abs(distance_m - self._trial_start_distance) > DISTANCE_DRIFT_TOLERANCE_M
            if drifted:
                self._trigger_pause(IntegrityFlag.DISTANCE_MOVED, now)
                # Re-anchor immediately so the hold can clear once patient is still.
                self._trial_start_distance = distance_m
            else:
                self._clear_pause_flag(IntegrityFlag.DISTANCE_MOVED)

        self._update_resume_state(now)

    def mark_trial_start_distance(self, distance_m: float) -> None:
        """Record the distance at the start of a trial for drift detection."""
        self._trial_start_distance = distance_m

    def prime_distance(self, distance_m: float) -> None:
        """Pre-seed the distance anchor so stability is satisfied immediately.

        Call at session.start with the current sensor reading so the patient
        does not see 'Hold still while we set your test distance' at the
        beginning of every session when they are already seated and still.
        The stability timer is fast-forwarded to its threshold so the very
        next update_distance call clears any DISTANCE_UNSTABLE hold.
        """
        self._distance_anchor = distance_m
        now = self._time_fn()
        # Fast-forward the timer to appear already satisfied
        self._distance_stability_timer._start = (
            now - self._distance_stability_timer.threshold_s
        )

    def set_fellow_eye_check_enabled(self, enabled: bool) -> None:
        """Enable or disable the fellow-eye occlusion hold.

        Must be called with enabled=True when session.start is received, and
        enabled=False when the session ends.  This prevents false holds on the
        camera-setup screen before any eye side is committed.
        """
        self._fellow_eye_check_enabled = enabled
        if not enabled:
            self._fellow_eye_timer.reset()
            self._clear_pause_flag(IntegrityFlag.FELLOW_EYE_OPEN)

    def check_fullscreen(self, is_fullscreen: bool) -> None:
        self._fullscreen_ok = is_fullscreen
        now = self._time_fn()
        if not is_fullscreen:
            self._trigger_pause(IntegrityFlag.FULLSCREEN_EXIT, now)
        else:
            self._clear_pause_flag(IntegrityFlag.FULLSCREEN_EXIT)
            self._update_resume_state(now)

    def _update_resume_state(self, now: float) -> None:
        if not self._paused:
            return

        all_clear = len(self._active_pause_flags) == 0 and self._fullscreen_ok
        should_resume = self._resume_timer.update(all_clear, now)
        if should_resume:
            self._paused = False
            if self._current_pause_event is not None:
                self._current_pause_event.end_time = now
                self._current_pause_event.duration_s = now - self._current_pause_event.start_time
                self._current_pause_event = None
            self._on_resume()

    def record_response(
        self,
        response_time_ms: float,
        direction: str,
        trial_start_distance_m: float,
        trial_end_distance_m: float,
    ) -> List[IntegrityFlag]:
        flags: List[IntegrityFlag] = []
        self._response_directions.append(direction)
        self._response_times_ms.append(response_time_ms)

        if response_time_ms < FAST_ANSWER_THRESHOLD_MS:
            flags.append(IntegrityFlag.FAST_ANSWER)
            self._post_hoc_flags.add(IntegrityFlag.FAST_ANSWER)

        if math.fabs(trial_end_distance_m - trial_start_distance_m) > DISTANCE_DRIFT_TOLERANCE_M:
            flags.append(IntegrityFlag.DISTANCE_DRIFT_MID_TRIAL)
            self._post_hoc_flags.add(IntegrityFlag.DISTANCE_DRIFT_MID_TRIAL)

        return flags

    def check_distance_face_mismatch(self, mismatch: bool) -> None:
        if mismatch:
            self._post_hoc_flags.add(IntegrityFlag.DISTANCE_FACE_MISMATCH)

    def is_paused(self) -> bool:
        return self._paused

    def get_pause_events(self) -> List[PauseEvent]:
        return self._pause_events

    def get_post_hoc_flags(self) -> List[IntegrityFlag]:
        # Suspicious answer pattern
        if self._is_answer_pattern_suspicious(self._response_directions):
            self._post_hoc_flags.add(IntegrityFlag.ANSWER_PATTERN_SUSPICIOUS)

        # Scripted timing suspicion
        if self._is_scripted_timing_suspected(self._response_times_ms):
            self._post_hoc_flags.add(IntegrityFlag.SCRIPTED_TIMING_SUSPECTED)

        return sorted(self._post_hoc_flags, key=lambda x: x.value)

    @staticmethod
    def _is_answer_pattern_suspicious(responses: List[str]) -> bool:
        if len(responses) < 10:
            return False

        max_streak = 1
        streak = 1
        for i in range(1, len(responses)):
            if responses[i] == responses[i - 1]:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 1

        if max_streak >= 5:
            return True

        counts: dict[str, int] = {}
        for direction in responses:
            counts[direction] = counts.get(direction, 0) + 1
        total = len(responses)
        entropy = -sum((count / total) * math.log2(count / total) for count in counts.values())
        return entropy < 1.0

    @staticmethod
    def _is_scripted_timing_suspected(response_times_ms: List[float]) -> bool:
        if len(response_times_ms) < 10:
            return False

        valid_times = [t for t in response_times_ms if 300 < t < 3000]
        if len(valid_times) < 10:
            return False

        mean_t = statistics.mean(valid_times)
        std_t = statistics.stdev(valid_times)
        cv = std_t / mean_t if mean_t > 0 else 0.0
        return cv < 0.05
