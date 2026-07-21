"""Integrity monitor tests for debounce and pause/resume behavior."""

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
        face_box_area_px=10000,
    )


def test_face_loss_triggers_after_debounce() -> None:
    clock = FakeClock()
    paused_flags = []
    monitor = IntegrityMonitor(
        tested_eye="OD",
        on_pause=lambda flag, _msg: paused_flags.append(flag),
        on_resume=lambda: None,
        time_fn=clock.now,
    )

    monitor.update_attention(_state(face_detected=False, face_count=0))
    clock.advance(1.0)
    monitor.update_attention(_state(face_detected=False, face_count=0))
    assert paused_flags == []

    clock.advance(1.1)
    monitor.update_attention(_state(face_detected=False, face_count=0))
    assert IntegrityFlag.FACE_LOSS in paused_flags


def test_no_false_pause_for_short_blink() -> None:
    clock = FakeClock()
    paused_flags = []
    monitor = IntegrityMonitor(
        tested_eye="OD",
        on_pause=lambda flag, _msg: paused_flags.append(flag),
        on_resume=lambda: None,
        time_fn=clock.now,
    )

    # In OD mode, left eye is fellow eye and should remain closed.
    monitor.update_attention(_state(left_eye_open=False, right_eye_open=True))
    monitor.update_attention(_state(left_eye_open=True, right_eye_open=True))
    clock.advance(0.3)
    monitor.update_attention(_state(left_eye_open=False, right_eye_open=True))
    assert IntegrityFlag.FELLOW_EYE_OPEN not in paused_flags


def test_resume_requires_stability_hold() -> None:
    clock = FakeClock()
    resume_events = []
    monitor = IntegrityMonitor(
        tested_eye="OD",
        on_pause=lambda _flag, _msg: None,
        on_resume=lambda: resume_events.append(True),
        time_fn=clock.now,
    )

    # Trigger a face-loss pause.
    monitor.update_attention(_state(face_detected=False, face_count=0))
    clock.advance(2.1)
    monitor.update_attention(_state(face_detected=False, face_count=0))
    assert monitor.is_paused()

    # Correct state but less than resume hold.
    monitor.update_attention(_state(face_detected=True, face_count=1))
    clock.advance(1.0)
    monitor.update_attention(_state(face_detected=True, face_count=1))
    assert resume_events == []

    # Finish hold and verify resume.
    clock.advance(0.6)
    monitor.update_attention(_state(face_detected=True, face_count=1))
    assert resume_events == [True]
    assert not monitor.is_paused()


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
