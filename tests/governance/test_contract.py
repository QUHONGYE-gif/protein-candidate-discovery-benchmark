from __future__ import annotations

import json
import shutil
from pathlib import Path

from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.governance.checks import sha256_bytes
from pcdb.governance.contract import contract_check_report

REPOSITORY_ROOT = Path(__file__).parents[2]
CONFIG = REPOSITORY_ROOT / "configs/governance/v2.json"
EXAMPLE = REPOSITORY_ROOT / "manifests/governance/examples/m5-example"


def _copy_example(tmp_path: Path) -> Path:
    target = tmp_path / "example"
    shutil.copytree(EXAMPLE, target)
    return target


def _report(root: Path):  # type: ignore[no-untyped-def]
    return contract_check_report(
        manifest_path=root / "milestone-contract-manifest.json",
        gate_a_path=root / "gate-a-decision.json",
        gate_b_path=root / "gate-b-decision.json",
        run_identity_path=root / "run-identity.json",
        config_path=CONFIG,
        source_root=root,
        require_final=False,
    )


def test_complete_example_contract_passes_every_check() -> None:
    report = _report(EXAMPLE)

    assert report.overall_status == "pass"
    assert {item.category for item in report.checks} >= {
        "schema_domain",
        "hash_exact_bytes",
        "source_environment_identity",
        "resource_cost_cap",
        "hidden_label_leakage",
        "inventory_cross_links",
    }


def test_major_boundaries_require_both_authorities(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    gate_a_path = root / "gate-a-decision.json"
    gate_a = json.loads(gate_a_path.read_bytes())
    gate_a["approvals"][1]["explicit_boundaries"] = []
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
