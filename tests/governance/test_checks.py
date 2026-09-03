from __future__ import annotations

from pcdb.governance.checks import (
    CheckEvidence,
    CheckViolation,
    build_report,
    capture_check,
)


def test_check_report_fails_closed_and_never_emits_approval() -> None:
    passed = capture_check(
        name="pass-example",
        category="ci",
        blocks=("ci",),
        input_digests=(),
        operation=lambda: CheckEvidence(reason="passed"),
    )

    def fail() -> CheckEvidence:
        raise CheckViolation("known invariant failure")

    failed = capture_check(
        name="fail-example",
        category="schema_domain",
        blocks=("formal_execution",),
        input_digests=("a" * 64,),
        operation=fail,
    )
    report = build_report((passed, failed), checked_at="2026-09-02T00:00:00Z", git_commit="b" * 40)

    assert report.overall_status == "fail"
    assert report.exit_code == 2
    assert report.as_dict()["approval_events_emitted"] == 0
    assert [item["status"] for item in report.as_dict()["checks"]] == ["pass", "fail"]


def test_unexpected_check_exception_is_error_and_blocks() -> None:
    def error() -> CheckEvidence:
        raise RuntimeError("tool unavailable")

    result = capture_check(
        name="error-example",
        category="archive_readback",
        blocks=("archive",),
        input_digests=(),
        operation=error,
    )

    assert result.status == "error"
    assert build_report((result,), checked_at="2026-09-02T00:00:00Z").exit_code == 2
