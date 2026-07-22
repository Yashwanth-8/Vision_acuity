"""Scoring engine tests — updated for mid-line 3rd-wrong termination."""

from backend.scoring.constants import LOGMAR_CEILING, VAS_OFFSET
from backend.scoring.engine import AcuitySession, LowVisionCategory


def test_ceiling_constant_is_1_0() -> None:
    assert LOGMAR_CEILING == 1.0


def test_result_logmar_never_exceeds_ceiling() -> None:
    # 3 wrong answers terminate mid-line; result is still a valid capped logmar
    session = AcuitySession(eye="OD", correction="UCVA")
    session.record_response("up", "down", 0.5, 1000)
    session.record_response("up", "down", 0.5, 1000)
    session.record_response("up", "down", 0.5, 1000)
    assert session.should_terminate() is True
    result = session.get_result()
    assert result.logmar is not None
    assert result.logmar <= LOGMAR_CEILING


def test_vas_offset() -> None:
    # 5 correct answers on first line (no termination)
    session = AcuitySession(eye="OD", correction="UCVA")
    for _ in range(5):
        session.record_response("up", "up", 0.5, 500)
    result = session.get_result()
    assert result.vas == result.etdrs_letter_score + VAS_OFFSET


def test_decimal_va_for_20_20_like_score() -> None:
    session = AcuitySession(eye="OD", correction="UCVA", start_logmar=0.0)
    result = session.get_result()
    assert result.decimal_va is not None
    assert abs(result.decimal_va - 1.0) < 1e-6


def test_low_vision_fallback_category_cf() -> None:
    session = AcuitySession(eye="OD", correction="UCVA")
    session.enter_reduced_distance_mode()
    session.record_low_vision_category(LowVisionCategory.CF)
    result = session.get_result()
    assert result.low_vision_category == LowVisionCategory.CF
    assert result.logmar is None


def test_all_low_vision_categories_supported() -> None:
    for category in LowVisionCategory:
        session = AcuitySession(eye="OD", correction="UCVA")
        session.record_low_vision_category(category)
        result = session.get_result()
        assert result.low_vision_category == category


def test_od_os_sessions_are_independent() -> None:
    od_session = AcuitySession(eye="OD", correction="UCVA")
    os_session = AcuitySession(eye="OS", correction="UCVA")

    for _ in range(10):
        od_session.record_response("up", "up", 0.5, 500)
    for _ in range(5):
        os_session.record_response("up", "up", 0.5, 500)

    od_result = od_session.get_result()
    os_result = os_session.get_result()
    assert od_result.eye == "OD"
    assert os_result.eye == "OS"
    assert od_result.etdrs_letter_score == 10
    assert os_result.etdrs_letter_score == 5


def test_distance_recorded_per_trial() -> None:
    session = AcuitySession(eye="OD", correction="UCVA")
    session.record_response("up", "up", 0.45, 500)
    session.record_response("up", "up", 0.52, 500)
    result = session.get_result()
    assert result.trials[0].distance_m == 0.45
    assert result.trials[1].distance_m == 0.52


def test_terminates_on_third_wrong_answer() -> None:
    """Session must terminate immediately at the 3rd wrong answer (mid-line)."""
    session = AcuitySession(eye="OD", correction="UCVA")
    session.record_response("up", "up", 0.5, 500)    # correct
    session.record_response("down", "up", 0.5, 500)  # wrong 1
    session.record_response("left", "up", 0.5, 500)  # wrong 2
    assert session.should_terminate() is False        # 2 wrongs — still going
    session.record_response("right", "up", 0.5, 500) # wrong 3 — terminates HERE
    assert session.should_terminate() is True         # terminated after 3rd wrong


def test_does_not_terminate_on_two_wrongs() -> None:
    """Two wrong answers on a line must NOT terminate the session."""
    session = AcuitySession(eye="OD", correction="UCVA")
    session.record_response("up", "down", 0.5, 500)  # wrong 1
    session.record_response("up", "down", 0.5, 500)  # wrong 2
    assert session.should_terminate() is False


def test_terminates_at_exactly_three_wrongs() -> None:
    """Exactly three wrong answers must terminate — not two, not four."""
    session = AcuitySession(eye="OD", correction="UCVA")
    session.record_response("up", "down", 0.5, 500)  # wrong 1
    session.record_response("up", "down", 0.5, 500)  # wrong 2
    assert session.should_terminate() is False
    session.record_response("up", "down", 0.5, 500)  # wrong 3
    assert session.should_terminate() is True


def test_14_lines_in_session() -> None:
    """Session must have exactly 14 logMAR lines (1.0 through -0.3)."""
    session = AcuitySession(eye="OD", correction="UCVA")
    assert len(session._line_logmars) == 14
    assert session._line_logmars[0] == 1.0
    assert session._line_logmars[-1] == -0.3


def test_final_line_terminates_after_completion() -> None:
    """Successfully completing the last line must finalise the session."""
    session = AcuitySession(eye="OD", correction="UCVA",
                            line_logmars=[-0.3])  # single-line session
    for _ in range(5):
        session.record_response("up", "up", 0.5, 500)  # all correct
    assert session.should_terminate() is True  # must not loop
