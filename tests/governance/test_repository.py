from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pcdb.governance.checks import CheckReport, CheckResult, CheckViolation
from pcdb.governance.repository import (
    repository_check_report,
    validate_tracking_declaration,
)

REPOSITORY_ROOT = Path(__file__).parents[2]


def _result(report: CheckReport, name: str) -> CheckResult:
    return next(item for item in report.checks if item.name == name)


def test_repository_workflow_and_check_report_schema_agree() -> None:
    report = repository_check_report(REPOSITORY_ROOT)

    workflow = _result(report, "repository-workflow-v2")
    assert workflow.status == "pass"
    assert workflow.evidence == {
        "active_document_count": 8,
        "allowed_tracking_authorities": ["github_issue", "multica"],
        "cross_link_mapping": "many_to_one",
        "issue_form_count": 4,
        "maintenance_logical_change_limit": 10,
        "status_update_events": [
            "gate_a_scientific_transition",
            "gate_b_terminal",
            "milestone_closeout",
        ],
        "tracking_authority_trigger_count": 5,
    }

    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/check-report.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report.as_dict())
    assert report.as_dict()["approval_events_emitted"] == 0


def test_tracking_declarations_require_only_the_selected_authority_fields() -> None:
    workflow = json.loads(
        (REPOSITORY_ROOT / "configs/governance/v2.json").read_text(encoding="utf-8")
    )["repository_workflow"]
    policy = workflow["tracking_authority"]

    validate_tracking_declaration(workflow["tracking_examples"]["github_issue"], policy)
    validate_tracking_declaration(workflow["tracking_examples"]["multica"], policy)

    missing_multica_identity = json.loads(json.dumps(workflow["tracking_examples"]["multica"]))
    del missing_multica_identity["objects"][0]["object_id"]
    with pytest.raises(CheckViolation, match="Multica tracking object fields"):
        validate_tracking_declaration(missing_multica_identity, policy)

    multica_with_github_only_field = json.loads(
        json.dumps(workflow["tracking_examples"]["multica"])
    )
    multica_with_github_only_field["issue_number"] = 123
    with pytest.raises(CheckViolation, match="tracking declaration fields differ for multica"):
        validate_tracking_declaration(multica_with_github_only_field, policy)


def test_repository_workflow_fails_closed_when_batch_trigger_drifts(tmp_path: Path) -> None:
    copied_root = tmp_path / "repository"
    shutil.copytree(
        REPOSITORY_ROOT,
        copied_root,
        ignore=shutil.ignore_patterns(".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"),
    )
    template = copied_root / ".github/pull_request_template.md"
    template.write_text(
        template.read_text(encoding="utf-8").replace(
            "累计 10 个逻辑小变化",
            "累计 11 个逻辑小变化",
        ),
        encoding="utf-8",
    )

    report = repository_check_report(copied_root)

    workflow = _result(report, "repository-workflow-v2")
    assert workflow.status == "fail"
    assert "maintenance batch triggers differ" in workflow.reason


def test_repository_workflow_rejects_a_third_tracking_ledger(tmp_path: Path) -> None:
    copied_root = tmp_path / "repository"
    shutil.copytree(
        REPOSITORY_ROOT,
        copied_root,
        ignore=shutil.ignore_patterns(".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"),
    )
    (copied_root / "maintenance-batch-ledger.yaml").write_text("changes: []\n", encoding="utf-8")

    report = repository_check_report(copied_root)

    workflow = _result(report, "repository-workflow-v2")
    assert workflow.status == "fail"
    assert "forbidden workflow ledger or registry" in workflow.reason
