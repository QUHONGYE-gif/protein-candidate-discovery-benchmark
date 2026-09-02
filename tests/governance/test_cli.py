from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcdb.cli import main

REPOSITORY_ROOT = Path(__file__).parents[2]
CONFIG = REPOSITORY_ROOT / "configs/governance/v2.json"


def test_repository_check_cli_emits_common_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "governance-check",
            "repository",
            "--repository-root",
            str(REPOSITORY_ROOT),
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["overall_status"] == "pass"
    assert report["approval_events_emitted"] == 0


def test_server_check_cli_returns_nonzero_for_capacity_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "governance-check",
            "server",
            "--config",
            str(CONFIG),
            "--expected-peak-bytes",
            "71",
            "--available-bytes",
            "100",
            "--observed-group",
            "pcdb",
            "--observed-writer",
            "github:QUHONGYE-gif",
            "--observed-writer",
            "git:will-zhang",
            "--observed-writer",
            "service:pcdb-runner",
            "--observed-writer",
            "service:pcdb-archiver",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert report["overall_status"] == "fail"
    assert report["approval_events_emitted"] == 0
