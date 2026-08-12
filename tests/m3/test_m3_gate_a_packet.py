"""Zero-side-effect M3 review packet checks for Issue #68 Gate A."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from pcdb.cli import main
from pcdb.contracts import ContractValidationError, RunSpec
from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.m3.gate_a import (
    build_gate_a_packet,
    load_gate_a_packet,
    packet_identity,
    validate_gate_a_entry,
)

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_COMMIT = "a" * 40
REVIEWED_TREE = "b" * 40


def _packet() -> tuple[bytes, dict[str, Any]]:
    payload = build_gate_a_packet(ROOT, REVIEWED_COMMIT, REVIEWED_TREE)
    return payload, json.loads(payload)


def test_packet_materializes_exact_18_run_specs_and_one_lowest_cost_sentinel() -> None:
    payload, packet = _packet()

    assert canonical_json_bytes(packet) == payload
    assert packet_identity(payload) == (len(payload), hashlib.sha256(payload).hexdigest())
    assert packet["reviewed_source"] == {
        "git_commit": REVIEWED_COMMIT,
        "git_tree": REVIEWED_TREE,
    }

    runs = packet["run_specs"]
    assert isinstance(runs, list)
    assert len(runs) == 18
    combinations = set()
    for item in runs:
        assert isinstance(item, dict)
        value = item["run_spec"]
        assert isinstance(value, dict)
        run_spec = RunSpec(**value)
        assert set(value) == {field.name for field in fields(RunSpec)}
        assert item["run_id"] == run_spec.run_id
        assert run_spec.git_commit == REVIEWED_COMMIT
        assert run_spec.protocol_version == "m3-single-fidelity-replay-v1"
        assert (run_spec.initial_sample_size, run_spec.batch_size, run_spec.budget) == (
            16,
            8,
            32,
        )
        combinations.add(
            (item["task_id"], run_spec.model_id, run_spec.acquisition_id, run_spec.seed)
        )
    assert len(combinations) == 18

    sentinel = packet["sentinel"]
    assert sentinel["task_id"] == "ired"
    assert sentinel["model_id"] == "ridge-sklearn-v1"
    assert sentinel["acquisition_id"] == "random-seeded-sha256-v1"
    assert sentinel["run_id"] in {item["run_id"] for item in runs}


def test_packet_freezes_exact_read_only_inputs_and_zero_write_gate_a_policy() -> None:
    _, packet = _packet()

    inventory = packet["input_inventory"]
    assert isinstance(inventory, list)
    assert len(inventory) == 12
    exact_keys = {(item["bucket"], item["object_key"]) for item in inventory}
    assert len(exact_keys) == len(inventory)
    assert all(item["size_bytes"] > 0 for item in inventory)
    assert all(len(item["sha256"]) == 64 for item in inventory)
    assert [item["truth_bearing"] for item in inventory[-2:]] == [True, True]
    assert not any(item["truth_bearing"] for item in inventory[:-2])

    assert packet["cam_policy"] == {
        "allowed_actions": [
            "name/cos:GetBucket",
            "name/cos:GetObject",
            "name/cos:HeadObject",
        ],
        "delete_object": 0,
        "exact_object_only": True,
        "put_object": 0,
    }
    policy = packet["execution_policy"]
    assert policy["cos_puts"] == 0
    assert policy["formal_attempts"] == 0
    assert policy["automatic_retry"] == 0
    assert policy["replacement_attempts"] == 0
    assert packet["authorization_boundaries"] == {
        "gate_a_execution_authorized": False,
        "gate_b_execution_authorized": False,
        "packet_review_authorizes_candidate_truth_read": False,
        "packet_review_authorizes_cos_access": False,
        "packet_review_authorizes_server_access": False,
    }


def test_loader_rebuilds_from_reviewed_repository_bytes_and_fails_closed_on_tamper() -> None:
    payload, _ = _packet()
    digest = hashlib.sha256(payload).hexdigest()

    loaded = load_gate_a_packet(
        payload,
        expected_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    assert loaded["schema_version"] == "m3-gate-a-review-packet-v1"

    tampered = payload.replace(b'"cos_puts": 0', b'"cos_puts": 1')
    with pytest.raises(ContractValidationError, match="identity"):
        load_gate_a_packet(
            tampered,
            expected_sha256=digest,
            repository_root=ROOT,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
        )


def test_entry_validation_precedes_real_input_reads_and_requires_empty_scratch(
    tmp_path: Path,
) -> None:
    payload, packet = _packet()
    digest = hashlib.sha256(payload).hexdigest()
    environment = packet["environment_candidate"]
    scratch = tmp_path / "scratch"

    validated = validate_gate_a_entry(
        packet_bytes=payload,
        packet_sha256=digest,
        repository_root=ROOT,
        runtime_git_commit=REVIEWED_COMMIT,
        runtime_git_tree=REVIEWED_TREE,
        runtime_environment_digest=environment["sha256"],
        scratch_root=scratch,
    )
    assert validated == scratch.resolve()
    assert not scratch.exists()

    with pytest.raises(ContractValidationError, match="ignored .scratch"):
        validate_gate_a_entry(
            packet_bytes=payload,
            packet_sha256=digest,
            repository_root=ROOT,
            runtime_git_commit=REVIEWED_COMMIT,
            runtime_git_tree=REVIEWED_TREE,
            runtime_environment_digest=environment["sha256"],
            scratch_root=ROOT / "gate-a-output",
        )

    scratch.mkdir()
    (scratch / "unexpected").write_text("occupied", encoding="utf-8")
    with pytest.raises(ContractValidationError, match="empty"):
        validate_gate_a_entry(
            packet_bytes=payload,
            packet_sha256=digest,
            repository_root=ROOT,
            runtime_git_commit=REVIEWED_COMMIT,
            runtime_git_tree=REVIEWED_TREE,
            runtime_environment_digest=environment["sha256"],
            scratch_root=scratch,
        )


def test_packet_cli_prints_reproducible_review_bytes_without_writing_files(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "m3-gate-a-packet",
            "--reviewed-git-commit",
            REVIEWED_COMMIT,
            "--reviewed-git-tree",
            REVIEWED_TREE,
            "--repository-root",
            str(ROOT),
        ]
    )

    assert result == 0
    assert capsys.readouterr().out.encode() == build_gate_a_packet(
        ROOT,
        REVIEWED_COMMIT,
        REVIEWED_TREE,
    )
