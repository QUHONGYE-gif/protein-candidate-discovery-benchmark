from __future__ import annotations

from pathlib import Path

from pcdb.governance.server import (
    DirectoryObservation,
    ServerObservation,
    server_preflight_report,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
CONFIG = REPOSITORY_ROOT / "configs/governance/v2.json"
MEMBERS = ("hongye", "pcdb-archiver", "pcdb-runner", "siyuan")


class FakeProbe:
    def __init__(
        self,
        *,
        available_bytes: int,
        members: tuple[str, ...] = MEMBERS,
        mode: int = 0o2770,
    ) -> None:
        self.available_bytes = available_bytes
        self.members = members
        self.mode = mode

    def observe(
        self, *, root: Path, group: str, subdirectories: tuple[str, ...]
    ) -> ServerObservation:
        paths = (root, *(root / item for item in subdirectories))
        return ServerObservation(
            hostname="pcdb-test-host",
            observed_at="2026-09-02T00:00:00Z",
            group=group,
            group_members=self.members,
            available_bytes=self.available_bytes,
            directories=tuple(
                DirectoryObservation(
                    path=str(path),
                    is_directory=True,
                    group=group,
                    mode=self.mode,
                )
                for path in paths
            ),
        )


def test_server_preflight_accepts_observed_target_capacity_and_exact_acl() -> None:
    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=50,
        probe=FakeProbe(available_bytes=100),
    )

    assert report.overall_status == "pass"
    acl = next(item for item in report.checks if item.name == "server-namespace-acl")
    assert acl.evidence is not None
    assert acl.evidence["hostname"] == "pcdb-test-host"


def test_server_preflight_fails_above_observed_target_even_below_hard_cap() -> None:
    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=51,
        probe=FakeProbe(available_bytes=100),
    )

    capacity = next(item for item in report.checks if item.name == "server-capacity")
    assert capacity.status == "fail"
    assert "50% target" in capacity.reason


def test_server_preflight_fails_on_observed_extra_group_member() -> None:
    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=1,
        probe=FakeProbe(available_bytes=100, members=(*MEMBERS, "unexpected")),
    )

    acl = next(item for item in report.checks if item.name == "server-namespace-acl")
    assert acl.status == "fail"
    assert "writer group mismatch" in acl.reason


def test_server_preflight_fails_on_unsafe_observed_directory_mode() -> None:
    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=1,
        probe=FakeProbe(available_bytes=100, mode=0o2777),
    )

    acl = next(item for item in report.checks if item.name == "server-namespace-acl")
    assert acl.status == "fail"
    assert "closed to others" in acl.reason
