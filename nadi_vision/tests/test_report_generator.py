"""Report generator tests."""

import re

from backend.report.generator import DISCLAIMER, ReportGenerator
from backend.scoring.engine import AcuitySession


def _make_session(eye: str, correction: str) -> object:
    session = AcuitySession(eye=eye, correction=correction)
    session.record_response("up", "up", 0.5, 500)
    session.record_response("up", "up", 0.5, 500)
    session.record_response("up", "up", 0.5, 500)
    session.record_response("up", "up", 0.5, 500)
    session.record_response("up", "up", 0.5, 500)
    return session.get_result()


def test_report_id_format() -> None:
    report_id = ReportGenerator.make_report_id()
    assert re.match(r"^NV-\d{8}-\d{4}$", report_id)


def test_disclaimer_verbatim() -> None:
    generator = ReportGenerator()
    report = generator.build({"sessions": [], "consent": True})
    assert report["disclaimer"] == DISCLAIMER


def test_od_os_ucva_bcva_rows_render_distinct() -> None:
    generator = ReportGenerator()
    sessions = [
        _make_session("OD", "UCVA"),
        _make_session("OD", "BCVA"),
        _make_session("OS", "UCVA"),
        _make_session("OS", "BCVA"),
    ]

    report = generator.build({"sessions": sessions, "consent": True, "distance_statement_m": 0.55})
    rows = report["rows"]

    assert len(rows) == 4
    assert (rows[0]["eye"], rows[0]["correction"]) == ("OD", "UCVA")
    assert (rows[1]["eye"], rows[1]["correction"]) == ("OD", "BCVA")
    assert (rows[2]["eye"], rows[2]["correction"]) == ("OS", "UCVA")
    assert (rows[3]["eye"], rows[3]["correction"]) == ("OS", "BCVA")
    assert all(row["ci_label"] == "screening-tier/provisional" for row in rows)
