"""Scoring engine tests for Phase 2 implementation."""

from backend.scoring.constants import LOGMAR_CEILING, VAS_OFFSET
from backend.scoring.engine import AcuitySession, LowVisionCategory


def test_ceiling_constant_is_1_0() -> None:
    assert LOGMAR_CEILING == 1.0


def test_result_logmar_never_exceeds_ceiling() -> None:
    session = AcuitySession(eye="OD", correction="UCVA")
    for _ in range(5):
        session.record_response("up", "down", 0.5, 1000)
    result = session.get_result()
    assert result.logmar is not None
    assert result.logmar <= LOGMAR_CEILING


def test_vas_offset() -> None:
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


def test_terminates_on_majority_wrong() -> None:
    session = AcuitySession(eye="OD", correction="UCVA")
    # 3 wrong out of 5 means 60% error rate, threshold is exceeded only if > 0.6.
    session.record_response("up", "up", 0.5, 500)
    session.record_response("down", "up", 0.5, 500)
    session.record_response("left", "up", 0.5, 500)
    session.record_response("right", "up", 0.5, 500)
    session.record_response("up", "up", 0.5, 500)
    assert session.should_terminate() is False


def test_terminates_when_error_rate_exceeds_threshold() -> None:
    session = AcuitySession(eye="OD", correction="UCVA")
    session.record_response("up", "down", 0.5, 500)
    session.record_response("down", "up", 0.5, 500)
    session.record_response("left", "up", 0.5, 500)
    session.record_response("right", "up", 0.5, 500)
    session.record_response("up", "up", 0.5, 500)
    assert session.should_terminate() is True
