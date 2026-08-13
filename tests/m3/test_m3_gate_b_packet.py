"""Review-only M3 Gate B packet checks for Issue #68."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pcdb.cli import main
from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.m3.gate_a import build_gate_a_packet
from pcdb.m3.gate_b import build_gate_b_packet, load_gate_b_packet

ROOT = Path(__file__).parents[2]
COMMIT = "f" * 40
TREE = "e" * 40


def _gate_a_material() -> tuple[bytes, str, bytes, str]:
    packet = build_gate_a_packet(ROOT, COMMIT, TREE)
    packet_sha256 = hashlib.sha256(packet).hexdigest()
    packet_value = json.loads(packet)
    evidence = canonical_json_bytes(
        {
            "authorization": "exact-external-approval-accepted",
            "checks": {
                "budget-monotonic": "PASS",
                "four-decision-metrics": "PASS",
                "interrupted-then-recovered": "PASS",
                "no-repeat-purchase": "PASS",
                "resource-caps": "INCONCLUSIVE",
                "same-runspec-double-run": "PASS",
            },
            "cos_puts": 0,
            "formal_attempts": 0,
            "gate_a_packet_sha256": packet_sha256,
            "reviewed_source": {"git_commit": COMMIT, "git_tree": TREE},
            "run_id": packet_value["sentinel"]["run_id"],
            "schema_version": "m3-gate-a-evidence-v1",
            "task_id": "ired",
            "verdict": "INCONCLUSIVE",
            "verdict_reason": "actual cloud billing requires independent operator review",
        }
    )
    return packet, packet_sha256, evidence, hashlib.sha256(evidence).hexdigest()


def test_packet_freezes_one_gate_b_sequence_without_authorizing_execution() -> None:
    gate_a, gate_a_sha, evidence, evidence_sha = _gate_a_material()

    payload = build_gate_b_packet(
        ROOT,
        reviewed_git_commit=COMMIT,
        reviewed_git_tree=TREE,
        gate_a_packet_bytes=gate_a,
        gate_a_packet_sha256=gate_a_sha,
        gate_a_evidence_bytes=evidence,
        gate_a_evidence_sha256=evidence_sha,
        gate_a_operator_attestation_comment_id=5269603341,
    )
    loaded = load_gate_b_packet(
        payload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        repository_root=ROOT,
        expected_git_commit=COMMIT,
        expected_git_tree=TREE,
        gate_a_packet_bytes=gate_a,
        gate_a_packet_sha256=gate_a_sha,
        gate_a_evidence_bytes=evidence,
        gate_a_evidence_sha256=evidence_sha,
        gate_a_operator_attestation_comment_id=5269603341,
    )

    assert len(loaded["run_specs"]) == 18
    assert len(loaded["expected_inventory"]) == 162
    assert loaded["expected_inventory"][-1]["kind"] == "success-marker"
    sentinel = loaded["sentinel"]
    sentinel_keys = [
        item["object_key"]
        for item in loaded["expected_inventory"]
        if item["run_id"] == sentinel["run_id"]
    ]
    assert len(sentinel_keys) == 9
    assert sentinel_keys[-1].endswith("/_SUCCESS")
    assert loaded["phases"]["gate_b"]["authorized"] is False
    assert loaded["authorization_boundaries"]["gate_b_requires_new_authorization"] is True
    assert loaded["authorization_boundaries"]["gate_b_execution_eligible"] is False
    assert loaded["gate_a_closure"]["governance"]["status"] == "PENDING-USER-ADJUDICATION"
    assert loaded["execution_caps"]["automatic_retry"] == 0
    assert loaded["execution_caps"]["replacement_attempts"] == 0
    assert loaded["execution_caps"]["delete_object"] == 0


def test_packet_rejects_gate_a_evidence_or_attestation_drift() -> None:
    gate_a, gate_a_sha, evidence, evidence_sha = _gate_a_material()
    drifted = json.loads(evidence)
    drifted["checks"]["same-runspec-double-run"] = "INCONCLUSIVE"
    drifted_bytes = canonical_json_bytes(drifted)

    with pytest.raises(ContractValidationError, match="Gate A checks"):
        build_gate_b_packet(
            ROOT,
            reviewed_git_commit=COMMIT,
            reviewed_git_tree=TREE,
            gate_a_packet_bytes=gate_a,
            gate_a_packet_sha256=gate_a_sha,
            gate_a_evidence_bytes=drifted_bytes,
            gate_a_evidence_sha256=hashlib.sha256(drifted_bytes).hexdigest(),
            gate_a_operator_attestation_comment_id=5269603341,
        )


def test_packet_records_explicit_acceptance_without_retroactive_authorization() -> None:
    gate_a, gate_a_sha, evidence, evidence_sha = _gate_a_material()

    payload = build_gate_b_packet(
        ROOT,
        reviewed_git_commit=COMMIT,
        reviewed_git_tree=TREE,
        gate_a_packet_bytes=gate_a,
        gate_a_packet_sha256=gate_a_sha,
        gate_a_evidence_bytes=evidence,
        gate_a_evidence_sha256=evidence_sha,
        gate_a_operator_attestation_comment_id=5269603341,
        gate_a_evidence_acceptance_comment_id=1,
    )
    value = json.loads(payload)

    assert value["gate_a_closure"]["governance"] == {
        "evidence_acceptance_comment_id": 1,
        "status": "ACCEPTED-DISCLOSED-GOVERNANCE-DEVIATION",
    }
    assert value["authorization_boundaries"]["gate_b_execution_eligible"] is True
    assert value["authorization_boundaries"]["gate_b_execution_authorized"] is False


def test_cli_materializes_same_review_packet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate_a, gate_a_sha, evidence, evidence_sha = _gate_a_material()
    gate_a_path = tmp_path / "gate-a-packet.json"
    evidence_path = tmp_path / "gate-a-evidence.json"
    gate_a_path.write_bytes(gate_a)
    evidence_path.write_bytes(evidence)

    assert (
        main(
            [
                "m3-gate-b-packet",
                "--reviewed-git-commit",
                COMMIT,
                "--reviewed-git-tree",
                TREE,
                "--repository-root",
                str(ROOT),
                "--gate-a-repository-root",
                str(ROOT),
                "--gate-a-packet",
                str(gate_a_path),
                "--gate-a-packet-sha256",
                gate_a_sha,
                "--gate-a-evidence",
                str(evidence_path),
                "--gate-a-evidence-sha256",
                evidence_sha,
                "--gate-a-operator-attestation-comment-id",
                "5269603341",
            ]
        )
        == 0
    )
    actual = capsys.readouterr().out.encode()
    assert actual == build_gate_b_packet(
        ROOT,
        reviewed_git_commit=COMMIT,
        reviewed_git_tree=TREE,
        gate_a_packet_bytes=gate_a,
        gate_a_packet_sha256=gate_a_sha,
        gate_a_evidence_bytes=evidence,
        gate_a_evidence_sha256=evidence_sha,
        gate_a_operator_attestation_comment_id=5269603341,
    )

    with pytest.raises(ContractValidationError, match="operator attestation"):
        build_gate_b_packet(
            ROOT,
            reviewed_git_commit=COMMIT,
            reviewed_git_tree=TREE,
            gate_a_packet_bytes=gate_a,
            gate_a_packet_sha256=gate_a_sha,
            gate_a_evidence_bytes=evidence,
            gate_a_evidence_sha256=evidence_sha,
            gate_a_operator_attestation_comment_id=0,
        )
