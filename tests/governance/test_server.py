from __future__ import annotations

from pathlib import Path

import pytest

from pcdb.governance.server import (
    DirectoryObservation,
    ServerObservation,
    SystemServerStateProbe,
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


def test_server_preflight_fails_above_observed_hard_cap() -> None:
    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=71,
        probe=FakeProbe(available_bytes=100),
    )

    capacity = next(item for item in report.checks if item.name == "server-capacity")
    assert capacity.status == "fail"
    assert "70% hard capacity cap" in capacity.reason


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


def test_system_probe_preserves_capacity_when_namespace_is_unprovisioned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_root = tmp_path / "not-provisioned"

    def missing_group(_: str) -> None:
        raise KeyError

    monkeypatch.setattr("pcdb.governance.server.grp.getgrnam", missing_group)

    observation = SystemServerStateProbe().observe(
        root=missing_root,
        group="pcdb-does-not-exist",
        subdirectories=("runs", "releases"),
    )

    assert observation.group_error == "target host has no Unix group 'pcdb-does-not-exist'"
    assert observation.available_bytes > 0
    assert observation.capacity_path == str(tmp_path)
    assert all(item.error is not None for item in observation.directories)


def test_server_preflight_reports_capacity_independently_from_namespace_failure() -> None:
    class UnprovisionedProbe(FakeProbe):
        def observe(
            self, *, root: Path, group: str, subdirectories: tuple[str, ...]
        ) -> ServerObservation:
            observation = super().observe(
                root=root,
                group=group,
                subdirectories=subdirectories,
            )
            return ServerObservation(
                hostname=observation.hostname,
                observed_at=observation.observed_at,
                group=observation.group,
                group_members=(),
                available_bytes=observation.available_bytes,
                directories=observation.directories,
                capacity_path="/",
                group_error="target host has no Unix group 'pcdb'",
            )

    report = server_preflight_report(
        config_path=CONFIG,
        expected_peak_bytes=50,
        probe=UnprovisionedProbe(available_bytes=100),
    )

    acl = next(item for item in report.checks if item.name == "server-namespace-acl")
    capacity = next(item for item in report.checks if item.name == "server-capacity")
    assert report.overall_status == "fail"
    assert acl.status == "fail"
    assert capacity.status == "pass"
    assert capacity.evidence is not None
    assert capacity.evidence["available_bytes"] == 100
    assert capacity.evidence["observed_filesystem_path"] == "/"
