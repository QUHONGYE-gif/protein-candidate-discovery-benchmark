from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.governance.checks import CheckReport, CheckViolation, sha256_bytes
from pcdb.governance.contract import contract_check_report, validate_governance_config
from tests.governance.helpers import (
    FakeDecisionEvidenceVerifier,
    attach_example_attestations,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
CONFIG = REPOSITORY_ROOT / "configs/governance/v2.json"
EXAMPLE = REPOSITORY_ROOT / "manifests/governance/examples/m5-example"


def _copy_example(tmp_path: Path) -> Path:
    target = tmp_path / "example"
    shutil.copytree(EXAMPLE, target)
    return target


def _report(
    root: Path,
    *,
    require_final: bool = False,
    verifier: FakeDecisionEvidenceVerifier | None = None,
) -> CheckReport:
    return contract_check_report(
        manifest_path=root / "milestone-contract-manifest.json",
        gate_a_path=root / "gate-a-decision.json",
        gate_b_path=root / "gate-b-decision.json",
        run_identity_path=root / "run-identity.json",
        config_path=CONFIG,
        source_root=root,
        evidence_verifier=verifier,
        require_final=require_final,
    )


def test_complete_example_contract_passes_every_check() -> None:
    report = _report(EXAMPLE)

    assert report.overall_status == "pass"
    assert {item.category for item in report.checks} >= {
        "authenticated_human_evidence",
        "schema_domain",
        "hash_exact_bytes",
        "source_environment_identity",
        "resource_cost_cap",
        "hidden_label_leakage",
        "inventory_cross_links",
    }


def test_governance_config_rejects_repository_workflow_drift() -> None:
    config = json.loads(CONFIG.read_bytes())
    config["repository_workflow"]["maintenance_batch"]["logical_change_limit"] = 11

    with pytest.raises(CheckViolation, match="repository workflow classification"):
        validate_governance_config(config)


def test_final_gate_requires_and_uses_authenticated_human_evidence(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    gate_a_path = root / "gate-a-decision.json"
    gate_b_path = root / "gate-b-decision.json"
    gate_a = json.loads(gate_a_path.read_bytes())
    gate_b = json.loads(gate_b_path.read_bytes())
    gate_a["record_status"] = "final"
    gate_b["record_status"] = "final"
    attach_example_attestations(gate_a, first_comment_id=301)
    attach_example_attestations(gate_b, first_comment_id=401)
    gate_a_path.write_bytes(canonical_json_bytes(gate_a))
    gate_b_path.write_bytes(canonical_json_bytes(gate_b))
    manifest_path = root / "milestone-contract-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["manifest_status"] = "terminal"
    manifest["gate_a"]["sha256"] = sha256_bytes(gate_a_path.read_bytes())
    manifest["gate_b"]["sha256"] = sha256_bytes(gate_b_path.read_bytes())
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    missing = _report(root, require_final=True)
    missing_gate = next(item for item in missing.checks if item.name == "scientific-gate-bindings")
    assert missing_gate.status == "fail"
    assert "authenticated evidence verifier" in missing_gate.reason

    verifier = FakeDecisionEvidenceVerifier()
    verified = _report(root, require_final=True, verifier=verifier)
    assert verified.overall_status == "pass"
    assert verifier.verified_comment_ids == [301, 302, 401, 402]


def test_major_boundaries_require_both_authorities(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    gate_a_path = root / "gate-a-decision.json"
    gate_a = json.loads(gate_a_path.read_bytes())
    gate_a["approvals"][1]["explicit_boundaries"] = []
    attach_example_attestations(gate_a, first_comment_id=501)
    gate_a_path.write_bytes(canonical_json_bytes(gate_a))
    manifest_path = root / "milestone-contract-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["gate_a"]["sha256"] = sha256_bytes(gate_a_path.read_bytes())
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    report = _report(root)

    gate_check = next(item for item in report.checks if item.name == "scientific-gate-bindings")
    assert gate_check.status == "fail"
    assert "both authorities" in gate_check.reason


def test_hidden_truth_cannot_reach_model_or_acquisition(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    manifest_path = root / "milestone-contract-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["input_artifacts"][0]["visibility"] = "hidden_truth"
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    report = _report(root)

    leakage = next(item for item in report.checks if item.category == "hidden_label_leakage")
    assert leakage.status == "fail"
    assert "prohibited consumer" in leakage.reason


def test_resource_overage_fails_only_the_cap_check(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    identity_path = root / "run-identity.json"
    identity = json.loads(identity_path.read_bytes())
    identity["observed_usage"]["cost"] = 2
    identity_path.write_bytes(canonical_json_bytes(identity))

    report = _report(root)

    source = next(item for item in report.checks if item.category == "source_environment_identity")
    resource = next(item for item in report.checks if item.category == "resource_cost_cap")
    assert source.status == "pass"
    assert resource.status == "fail"
    assert "cost" in resource.reason
