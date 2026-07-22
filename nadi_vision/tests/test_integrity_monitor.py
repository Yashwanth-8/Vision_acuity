"""Integrity monitor tests — debounce, pause/resume, and fellow-eye occlusion."""

from backend.integrity.monitor import AttentionState, IntegrityFlag, IntegrityMonitor


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _state(
    face_detected: bool = True,
    face_count: int = 1,
    head_yaw_deg: float = 0.0,
    left_eye_open: bool = False,
    right_eye_open: bool = False,
) -> AttentionState:
    return AttentionState(
        face_detected=face_detected,
        face_count=face_count,
        head_yaw_deg=head_yaw_deg,
        left_eye_open=left_eye_open,
        right_eye_open=right_eye_open,
        face_box_area_px=10_000,
    )


def _monitor_with_clock(
    tested_eye: str = "OD",
    fellow_eye_check_enabled: bool = False,
) -> tuple[IntegrityMonitor, FakeClock, list, list]:
    clock = FakeClock()
    paused_flags: list = []
    resumes: list = []
    monitor = IntegrityMonitor(
        tested_eye=tested_eye,  # type: ignore[arg-type]
        on_pause=lambda flag, _msg: paused_flags.append(flag),
        on_resume=lambda: resumes.append(True),
        time_fn=clock.now,
        fellow_eye_check_enabled=fellow_eye_check_enabled,
    )
    return monitor, clock, paused_flags, resumes


def test_face_loss_triggers_after_debounce() -> None:
    monitor, clock, paused_flags, _ = _monitor_with_clock()

    monitor.update_attention(_state(face_detected=False, face_count=0))
    clock.advance(1.0)
    monitor.update_attention(_state(face_detected=False, face_count=0))
    assert paused_flags == []

    clock.advance(1.1)
    monitor.update_attention(_state(face_detected=False, face_count=0))
    assert IntegrityFlag.FACE_LOSS in paused_flags


def test_no_false_pause_for_short_blink() -> None:
    # fellow_eye_check_enabled=False by default — blink must not trigger hold
    monitor, clock, paused_flags, _ = _monitor_with_clock()

    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    clock.advance(0.3)
    monitor.update_attention(_state(left_eye_open=False, right_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN not in paused_flags


def test_resume_requires_stability_hold() -> None:
    monitor, clock, _, resume_events = _monitor_with_clock()

    # Trigger a face-loss pause
    monitor.update_attention(_state(face_detected=False, face_count=0))
    clock.advance(2.1)
    monitor.update_attention(_state(face_detected=False, face_count=0))
    assert monitor.is_paused()

    # Correct state but less than resume hold
    monitor.update_attention(_state(face_detected=True, face_count=1))
    clock.advance(1.0)
    monitor.update_attention(_state(face_detected=True, face_count=1))
    assert resume_events == []

    # Finish hold and verify resume
    clock.advance(0.6)
    monitor.update_attention(_state(face_detected=True, face_count=1))
    assert resume_events == [True]
    assert not monitor.is_paused()


# ---------------------------------------------------------------------------
# Fellow-eye occlusion tests
# ---------------------------------------------------------------------------

def test_fellow_eye_hold_disabled_before_session_start() -> None:
    """Fellow-eye check must NOT fire before session.start (default disabled)."""
    monitor, clock, paused_flags, _ = _monitor_with_clock(tested_eye="OD",
                                                          fellow_eye_check_enabled=False)
    # Patient's left eye (OS = fellow for OD test) is wide open
    for _ in range(5):
        clock.advance(0.3)
        monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN not in paused_flags


def test_fellow_eye_hold_triggers_for_od_test_when_left_eye_open() -> None:
    """OD test: patient's LEFT eye (OS) open for > 2.0 s must trigger hold."""
    monitor, clock, paused_flags, _ = _monitor_with_clock(tested_eye="OD",
                                                          fellow_eye_check_enabled=True)
    # Patient's left eye is open (OS = fellow eye when testing OD)
    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    clock.advance(1.0)
    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN not in paused_flags  # debounce not yet

    clock.advance(1.1)  # total > 2.0 s
    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN in paused_flags


def test_fellow_eye_hold_triggers_for_os_test_when_right_eye_open() -> None:
    """OS test: patient's RIGHT eye (OD) open for > 2.0 s must trigger hold."""
    monitor, clock, paused_flags, _ = _monitor_with_clock(tested_eye="OS",
                                                          fellow_eye_check_enabled=True)
    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    clock.advance(2.1)
    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN in paused_flags


def test_fellow_eye_hold_clears_when_eye_covered() -> None:
    """Hold must clear (resume path) once the fellow eye is covered."""
    monitor, clock, paused_flags, resume_events = _monitor_with_clock(
        tested_eye="OD", fellow_eye_check_enabled=True
    )
    # Trigger hold
    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    clock.advance(2.1)
    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN in paused_flags
    assert monitor.is_paused()

    # Patient covers left eye (fellow eye for OD)
    monitor.update_attention(_state(left_eye_open=False, right_eye_open=True))
    clock.advance(1.6)  # resume stability hold = 1.5 s
    monitor.update_attention(_state(left_eye_open=False, right_eye_open=True))
    assert resume_events == [True]
    assert not monitor.is_paused()


def test_od_right_eye_open_does_not_trigger_hold() -> None:
    """OD test: testing eye (patient's RIGHT) being open is expected — no hold."""
    monitor, clock, paused_flags, _ = _monitor_with_clock(tested_eye="OD",
                                                          fellow_eye_check_enabled=True)
    # Only patient's right eye (OD = tested eye) is open, left (OS) is covered
    for _ in range(5):
        clock.advance(0.3)
        monitor.update_attention(_state(left_eye_open=False, right_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN not in paused_flags


def test_fellow_eye_check_disabled_after_session_ends() -> None:
    """set_fellow_eye_check_enabled(False) must clear the hold immediately."""
    monitor, clock, paused_flags, resume_events = _monitor_with_clock(
        tested_eye="OD", fellow_eye_check_enabled=True
    )
    # Trigger hold
    monitor.update_attention(_state(left_eye_open=True))
    clock.advance(2.1)
    monitor.update_attention(_state(left_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN in paused_flags

    # Disable (session ended) — flag must be cleared immediately
    monitor.set_fellow_eye_check_enabled(False)
    assert IntegrityFlag.FELLOW_EYE_OPEN not in monitor._active_pause_flags


def test_pause_message_names_correct_eye_od() -> None:
    """OD test message must instruct patient to cover their LEFT eye."""
    monitor, _, _, _ = _monitor_with_clock(tested_eye="OD",
                                           fellow_eye_check_enabled=True)
    from backend.integrity.monitor import IntegrityFlag
    msg = monitor._pause_message(IntegrityFlag.FELLOW_EYE_OPEN)
    assert "left" in msg.lower()


def test_pause_message_names_correct_eye_os() -> None:
    """OS test message must instruct patient to cover their RIGHT eye."""
    monitor, _, _, _ = _monitor_with_clock(tested_eye="OS",
                                           fellow_eye_check_enabled=True)
    from backend.integrity.monitor import IntegrityFlag
    msg = monitor._pause_message(IntegrityFlag.FELLOW_EYE_OPEN)
    assert "right" in msg.lower()


def test_fast_answer_flagged() -> None:
    monitor = IntegrityMonitor(
        tested_eye="OD",
        on_pause=lambda _flag, _msg: None,
        on_resume=lambda: None,
    )
    flags = monitor.record_response(
        response_time_ms=200,
        direction="up",
        trial_start_distance_m=0.5,
        trial_end_distance_m=0.5,
    )
    assert IntegrityFlag.FAST_ANSWER in flags


def test_normal_response_not_fast_flagged() -> None:
    monitor = IntegrityMonitor(
        tested_eye="OD",
        on_pause=lambda _flag, _msg: None,
        on_resume=lambda: None,
    )
    flags = monitor.record_response(
        response_time_ms=600,
        direction="up",
        trial_start_distance_m=0.5,
        trial_end_distance_m=0.5,
    )
    assert IntegrityFlag.FAST_ANSWER not in flags
