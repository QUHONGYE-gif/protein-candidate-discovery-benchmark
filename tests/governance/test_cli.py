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


def test_decision_digest_cli_emits_exact_github_attestation_marker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    decision = REPOSITORY_ROOT / "manifests/governance/examples/m5-example/gate-a-decision.json"

    exit_code = main(["governance-decision-digest", "--decision", str(decision)])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert len(result["decision_digest"]) == 64
    assert result["github_comment_marker"].endswith(result["decision_digest"])


def test_server_check_cli_fails_closed_when_current_host_is_not_provisioned(
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
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert report["overall_status"] == "fail"
    assert report["approval_events_emitted"] == 0
