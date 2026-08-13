"""Deterministic review-only Gate B packet for Issue #68."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.m3.gate_a import load_gate_a_packet
from pcdb.m3.gate_b_runtime import GATE_B_APPROVAL

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_PASS_CHECKS = {
    "budget-monotonic",
    "four-decision-metrics",
    "interrupted-then-recovered",
    "no-repeat-purchase",
    "same-runspec-double-run",
}
_EVIDENCE_BUCKET = "pcdb-benchmark-artifacts-1408226911"
_SOURCE_PATHS = (
    Path("src/pcdb/m3/gate_b.py"),
    Path("src/pcdb/m3/gate_b_runtime.py"),
    Path("src/pcdb/replay/engine.py"),
    Path("src/pcdb/replay/publication.py"),
)


def _strict_canonical_object(payload: bytes, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(f"{field} must be canonical JSON") from None
    if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
        raise ContractValidationError(f"{field} must be canonical JSON")
    return cast(dict[str, Any], value)


def _verify_identity(payload: bytes, expected_sha256: str, field: str) -> None:
    if (
        not _SHA256.fullmatch(expected_sha256)
        or hashlib.sha256(payload).hexdigest() != expected_sha256
    ):
        raise ContractValidationError(f"{field} identity drifted")


def _validate_gate_a_evidence(
    payload: bytes,
    *,
    expected_sha256: str,
    packet_sha256: str,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
    sentinel_run_id: str,
) -> dict[str, Any]:
    _verify_identity(payload, expected_sha256, "Gate A evidence")
    value = _strict_canonical_object(payload, "Gate A evidence")
    checks = value.get("checks")
    if not isinstance(checks, dict) or any(checks.get(name) != "PASS" for name in _PASS_CHECKS):
        raise ContractValidationError("Gate A checks are incomplete")
    if checks.get("resource-caps") not in {"PASS", "INCONCLUSIVE"}:
        raise ContractValidationError("Gate A resource-cap check is invalid")
    if (
        value.get("schema_version") != "m3-gate-a-evidence-v1"
        or value.get("authorization") != "exact-external-approval-accepted"
        or value.get("gate_a_packet_sha256") != packet_sha256
        or value.get("reviewed_source")
        != {"git_commit": reviewed_git_commit, "git_tree": reviewed_git_tree}
        or value.get("run_id") != sentinel_run_id
        or value.get("cos_puts") != 0
        or value.get("formal_attempts") != 0
    ):
        raise ContractValidationError("Gate A evidence binding drifted")
    return value


def _expected_inventory(
    run_specs: list[dict[str, Any]], evidence_prefix: str, sentinel_run_id: str
) -> list[dict[str, Any]]:
    ordered = sorted(
        run_specs,
        key=lambda item: (item["run_id"] != sentinel_run_id, item["run_id"]),
    )
    inventory: list[dict[str, Any]] = []
    for item in ordered:
        run_id = str(item["run_id"])
        base = f"{evidence_prefix}runs/{run_id}/attempts/attempt-001"
        for round_index in range(3):
            inventory.append(
                {
                    "generation_rule": "ReplayTrajectory.checkpoint_bytes(round_index)",
                    "kind": "checkpoint",
                    "object_key": f"{base}/checkpoints/round-{round_index:03d}.json",
                    "round": round_index,
                    "run_id": run_id,
                    "validation_rule": "checkpoint SHA exact-matches canonical trajectory event",
                }
            )
        for round_index in range(3):
            inventory.append(
                {
                    "generation_rule": "ReplayTrajectory.metric_bytes(round_index)",
                    "kind": "metric",
                    "object_key": f"{base}/metrics/round-{round_index:03d}.json",
                    "round": round_index,
                    "run_id": run_id,
                    "validation_rule": "metric SHA exact-matches canonical trajectory event",
                }
            )
        inventory.extend(
            [
                {
                    "generation_rule": "ReplayTrajectory.canonical_bytes",
                    "kind": "trajectory",
                    "object_key": f"{base}/trajectory.json",
                    "run_id": run_id,
                    "validation_rule": "strict trajectory loader and RunSpec identity",
                },
                {
                    "generation_rule": "RunAttempt v1 after all payload validation",
                    "kind": "run-attempt",
                    "object_key": f"{base}/run-attempt.json",
                    "run_id": run_id,
                    "validation_rule": "strict RunAttempt loader exact-covers seven payloads",
                },
                {
                    "generation_rule": "constant empty bytes after RunAttempt fresh readback",
                    "kind": "success-marker",
                    "object_key": f"{base}/_SUCCESS",
                    "run_id": run_id,
                    "validation_rule": "exact empty bytes and marker written last",
                },
            ]
        )
    return inventory


def build_gate_b_packet(
    repository_root: Path,
    *,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
    gate_a_repository_root: Path | None = None,
    gate_a_packet_bytes: bytes,
    gate_a_packet_sha256: str,
    gate_a_evidence_bytes: bytes,
    gate_a_evidence_sha256: str,
    gate_a_operator_attestation_comment_id: int,
    gate_a_evidence_acceptance_comment_id: int | None = None,
) -> bytes:
    """Build one zero-execution Gate B review packet from accepted Gate A evidence."""

    if not _GIT_OBJECT.fullmatch(reviewed_git_commit) or not _GIT_OBJECT.fullmatch(
        reviewed_git_tree
    ):
        raise ContractValidationError("reviewed Git identity is invalid")
    if (
        isinstance(gate_a_operator_attestation_comment_id, bool)
        or not isinstance(gate_a_operator_attestation_comment_id, int)
        or gate_a_operator_attestation_comment_id <= 0
    ):
        raise ContractValidationError("Gate A operator attestation is absent")
    if gate_a_evidence_acceptance_comment_id is not None and (
        isinstance(gate_a_evidence_acceptance_comment_id, bool)
        or not isinstance(gate_a_evidence_acceptance_comment_id, int)
        or gate_a_evidence_acceptance_comment_id <= 0
    ):
        raise ContractValidationError("Gate A evidence acceptance is invalid")
    gate_a_untrusted = _strict_canonical_object(gate_a_packet_bytes, "Gate A packet")
    gate_a_source = gate_a_untrusted.get("reviewed_source")
    if not isinstance(gate_a_source, dict):
        raise ContractValidationError("Gate A reviewed source is absent")
    gate_a_commit = str(gate_a_source.get("git_commit"))
    gate_a_tree = str(gate_a_source.get("git_tree"))
    gate_a = load_gate_a_packet(
        gate_a_packet_bytes,
        expected_sha256=gate_a_packet_sha256,
        repository_root=gate_a_repository_root or repository_root,
        expected_git_commit=gate_a_commit,
        expected_git_tree=gate_a_tree,
    )
    sentinel = cast(dict[str, Any], gate_a["sentinel"])
    run_specs = cast(list[dict[str, Any]], gate_a["run_specs"])
    _validate_gate_a_evidence(
        gate_a_evidence_bytes,
        expected_sha256=gate_a_evidence_sha256,
        packet_sha256=gate_a_packet_sha256,
        reviewed_git_commit=gate_a_commit,
        reviewed_git_tree=gate_a_tree,
        sentinel_run_id=str(sentinel["run_id"]),
    )
    evidence_prefix = f"m3/issue-68/single-fidelity-v1/{reviewed_git_commit}/"
    inventory = _expected_inventory(run_specs, evidence_prefix, str(sentinel["run_id"]))
    if len(inventory) != 162 or len({item["object_key"] for item in inventory}) != 162:
        raise ContractValidationError("Gate B expected inventory is not an exact 162-key set")
    environment = cast(dict[str, Any], gate_a["environment_candidate"])
    gate_a_governance_status = (
        "ACCEPTED-DISCLOSED-GOVERNANCE-DEVIATION"
        if gate_a_evidence_acceptance_comment_id is not None
        else "PENDING-USER-ADJUDICATION"
    )
    value = {
        "authorization_boundaries": {
            "gate_b_execution_authorized": False,
            "gate_b_execution_eligible": gate_a_evidence_acceptance_comment_id is not None,
            "gate_b_requires_new_authorization": True,
            "packet_review_authorizes_cos_access": False,
            "packet_review_authorizes_server_access": False,
            "packet_review_authorizes_truth_read": False,
            "required_approval_text": GATE_B_APPROVAL,
        },
        "cam_roles": {
            "independent_reader": {
                "allowed_actions": [
                    "name/cos:GetBucket",
                    "name/cos:GetObject",
                    "name/cos:HeadObject",
                ],
                "evidence_prefix": evidence_prefix,
                "role_name": "PCDBIssue68GateBReader",
            },
            "writer": {
                "allowed_actions": [
                    "name/cos:GetBucket",
                    "name/cos:GetObject",
                    "name/cos:HeadObject",
                    "name/cos:PutObject",
                ],
                "evidence_prefix": evidence_prefix,
                "input_objects": gate_a["input_inventory"],
                "put_condition": {"cos:x-cos-forbid-overwrite": "true"},
                "role_name": "PCDBIssue68GateBWriter",
            },
            "forbidden_actions": ["name/cos:DeleteObject"],
        },
        "evidence_store": {"bucket": _EVIDENCE_BUCKET, "prefix": evidence_prefix},
        "execution_caps": {
            "automatic_retry": 0,
            "cos_net_new_bytes": 2 * 1024**3,
            "cos_object_count": 162,
            "delete_object": 0,
            "formal_attempts": 18,
            "max_cost_cny": 50,
            "max_network_transfer_bytes": 10 * 1024**3,
            "max_ram_bytes": 16 * 1024**3,
            "max_scratch_bytes": 20 * 1024**3,
            "max_vcpu": 8,
            "max_wall_seconds": 6 * 60 * 60,
            "overwrite": 0,
            "per_run_spec_attempts": 1,
            "replacement_attempts": 0,
        },
        "expected_inventory": inventory,
        "gate_a_closure": {
            "evidence_sha256": gate_a_evidence_sha256,
            "evidence_size_bytes": len(gate_a_evidence_bytes),
            "operator_attestation_comment_id": gate_a_operator_attestation_comment_id,
            "packet_sha256": gate_a_packet_sha256,
            "packet_size_bytes": len(gate_a_packet_bytes),
            "reviewed_source": {"git_commit": gate_a_commit, "git_tree": gate_a_tree},
            "governance": {
                "evidence_acceptance_comment_id": gate_a_evidence_acceptance_comment_id,
                "status": gate_a_governance_status,
            },
            "technical_verdict": "PASS-after-operator-resource-attestation",
        },
        "input_inventory": gate_a["input_inventory"],
        "issue_number": 68,
        "phases": {
            "gate_b": {
                "authorized": False,
                "scope": "one sentinel-first 18-RunSpec sequence",
            },
            "independent_reader": {
                "requires_writer_exit": True,
                "scope": "sentinel exact-cover bytes/checksums/cross-links",
            },
            "remaining_matrix": {
                "requires_sentinel_reader_pass": True,
                "run_spec_count": 17,
            },
            "writer": {
                "actual_inventory_freeze": "after local strict validation and before first PUT",
                "scope": "lowest-cost sentinel only",
            },
        },
        "publication_order": [
            "all checkpoints and metrics",
            "trajectory",
            "run-attempt",
            "_SUCCESS",
        ],
        "reviewed_source": {
            "git_commit": reviewed_git_commit,
            "git_tree": reviewed_git_tree,
        },
        "runtime_identity": {
            "executed_environment_digest": environment["sha256"],
            "executed_git_commit": reviewed_git_commit,
        },
        "run_specs": run_specs,
        "schema_version": "m3-gate-b-review-packet-v1",
        "sentinel": sentinel,
        "source_artifacts": [
            {
                "path": path.as_posix(),
                "sha256": hashlib.sha256((repository_root / path).read_bytes()).hexdigest(),
                "size_bytes": len((repository_root / path).read_bytes()),
            }
            for path in _SOURCE_PATHS
        ],
        "stop_conditions": [
            "source/environment/input identity drift",
            "non-empty evidence prefix or target key exists",
            "actual inventory does not exact-cover expected schema",
            "local validation or cap failure before first PUT",
            "forbid-overwrite or fresh-readback failure",
            "sentinel independent-reader missing/extra/duplicate/cross-link failure",
            "retry, replacement, overwrite, delete, or permission expansion required",
        ],
    }
    return canonical_json_bytes(value)


def load_gate_b_packet(
    packet_bytes: bytes,
    *,
    expected_sha256: str,
    repository_root: Path,
    expected_git_commit: str,
    expected_git_tree: str,
    gate_a_repository_root: Path | None = None,
    gate_a_packet_bytes: bytes,
    gate_a_packet_sha256: str,
    gate_a_evidence_bytes: bytes,
    gate_a_evidence_sha256: str,
    gate_a_operator_attestation_comment_id: int,
    gate_a_evidence_acceptance_comment_id: int | None = None,
) -> dict[str, Any]:
    """Strict-load and reproduce one exact Gate B review packet."""

    _verify_identity(packet_bytes, expected_sha256, "Gate B packet")
    value = _strict_canonical_object(packet_bytes, "Gate B packet")
    rebuilt = build_gate_b_packet(
        repository_root,
        reviewed_git_commit=expected_git_commit,
        reviewed_git_tree=expected_git_tree,
        gate_a_repository_root=gate_a_repository_root,
        gate_a_packet_bytes=gate_a_packet_bytes,
        gate_a_packet_sha256=gate_a_packet_sha256,
        gate_a_evidence_bytes=gate_a_evidence_bytes,
        gate_a_evidence_sha256=gate_a_evidence_sha256,
        gate_a_operator_attestation_comment_id=gate_a_operator_attestation_comment_id,
        gate_a_evidence_acceptance_comment_id=gate_a_evidence_acceptance_comment_id,
    )
    if rebuilt != packet_bytes:
        raise ContractValidationError("Gate B packet differs from reviewed inputs")
    return value


__all__ = ["build_gate_b_packet", "load_gate_b_packet"]
