from __future__ import annotations

from pathlib import Path

from pcdb.governance.server import server_preflight_report

REPOSITORY_ROOT = Path(__file__).parents[2]
CONFIG = REPOSITORY_ROOT / "configs/governance/v2.json"
WRITERS = (
    "github:QUHONGYE-gif",
    "git:will-zhang",
    "service:pcdb-runner",
    "service:pcdb-archiver",
)


def test_server_preflight_accepts_target_capacity_and_exact_acl() -> None:
    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=50,
        available_bytes=100,
        observed_group="pcdb",
        observed_writers=WRITERS,
    )

    assert report.overall_status == "pass"


def test_server_preflight_fails_above_target_even_below_hard_cap() -> None:
    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=51,
        available_bytes=100,
        observed_group="pcdb",
        observed_writers=WRITERS,
    )

    capacity = next(item for item in report.checks if item.name == "server-capacity")
    assert capacity.status == "fail"
    assert "50% target" in capacity.reason


def test_server_preflight_fails_on_extra_writer() -> None:
    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=1,
        available_bytes=100,
        observed_group="pcdb",
        observed_writers=(*WRITERS, "service:unexpected"),
    )

    acl = next(item for item in report.checks if item.name == "server-namespace-acl")
    assert acl.status == "fail"
    assert "writer set mismatch" in acl.reason
