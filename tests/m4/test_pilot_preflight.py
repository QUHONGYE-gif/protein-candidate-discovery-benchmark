"""Zero-truth M4 pilot preflight packet checks for Issue #76."""

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
from pcdb.m4.pilot_preflight import (
    build_pilot_preflight_packet,
    load_pilot_preflight_packet,
    packet_identity,
)

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_COMMIT = "a" * 40
REVIEWED_TREE = "b" * 40


def _packet() -> tuple[bytes, dict[str, Any]]:
    payload = build_pilot_preflight_packet(ROOT, REVIEWED_COMMIT, REVIEWED_TREE)
    return payload, json.loads(payload)


def test_packet_materializes_exact_truth_free_72_run_spec_pilot() -> None:
    payload, packet = _packet()

    assert canonical_json_bytes(packet) == payload
    assert packet_identity(payload) == (len(payload), hashlib.sha256(payload).hexdigest())
    assert packet["reviewed_source"] == {
        "git_commit": REVIEWED_COMMIT,
        "git_tree": REVIEWED_TREE,
    }

    runs = packet["run_specs"]
    assert isinstance(runs, list)
    assert len(runs) == 72
    assert len({item["run_id"] for item in runs}) == 72
    combinations: list[tuple[str, str, str, int]] = []
    for item in runs:
        value = item["run_spec"]
        run_spec = RunSpec(**value)
        assert set(value) == {field.name for field in fields(RunSpec)}
        assert item["run_id"] == run_spec.run_id
        assert run_spec.git_commit == REVIEWED_COMMIT
        assert run_spec.protocol_version == "m4-single-fidelity-screening-v1"
        assert run_spec.environment_digest == packet["environment_candidate"]["sha256"]
        assert (run_spec.initial_sample_size, run_spec.batch_size, run_spec.budget) == (
            32,
            16,
            128,
        )
        combinations.append(
            (item["task_id"], run_spec.model_id, run_spec.acquisition_id, run_spec.seed)
        )

    assert combinations == sorted(
        combinations,
        key=lambda item: (
            ("alpha-amylase", "ired").index(item[0]),
            ("ridge-sklearn-v1", "random-forest-sklearn-v1", "xgboost-cpu-v1").index(item[1]),
            (
                "random-seeded-sha256-v1",
                "greedy-predictive-mean-v1",
                "uncertainty-bootstrap-std-v1",
                "ucb-bootstrap-std-beta1-v1",
            ).index(item[2]),
            item[3],
        ),
    )
    assert {item[3] for item in combinations} == {1729, 1730, 1731}
    assert len(set(combinations)) == 72


def test_packet_binds_closed_configs_and_cumulative_zero_ledger() -> None:
    _, packet = _packet()

    assert packet["matrix"] == {
        "expected_pilot_run_specs": 72,
        "matrix_order": ["task_id", "model_id", "acquisition_id", "seed"],
        "pilot_profile": {
            "batch_size": 16,
            "budget": 128,
            "initial_sample_size": 32,
            "seeds": [1729, 1730, 1731],
        },
        "registered_config_scope": {
            "initial_sample_sizes": [32, 64],
            "seeds": list(range(1729, 1739)),
        },
    }
    assert packet["resource_caps"] == {
        "active_execution_wall_seconds": 21_600,
        "aggregate_rss_bytes": 16 * 1024**3,
        "automatic_retry": 0,
        "delete": 0,
        "max_unique_run_specs_and_attempts": 378,
        "network_transfer_bytes": 2 * 1024**3,
        "overwrite": 0,
        "per_run_spec_attempts": 1,
        "pilot_unique_run_specs_and_attempts": 72,
        "replacement_attempts": 0,
        "scratch_high_water_bytes": 20 * 1024**3,
        "vcpu": 8,
    }
    assert packet["initial_resource_ledger"] == {
        "active_execution_wall_seconds": 0,
        "attempts_started": 0,
        "network_transfer_bytes": 0,
        "paid_compute_cny": 0,
        "scratch_high_water_bytes": 0,
        "unique_run_ids_started": [],
    }
    assert packet["resource_accounting"] == {
        "cap_scope": "pilot-and-conditional-screening-cumulative-no-reset",
        "guard_before_each_remote_operation": True,
        "guard_before_each_run_spec": True,
        "incomplete_attempt_enters_aggregate": False,
        "ledger_persistence_authorized": False,
    }


def test_packet_builder_reads_only_closed_repository_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[Path] = []
    original = Path.read_bytes

    def record(path: Path) -> bytes:
        resolved = path.resolve()
        reads.append(resolved)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", record)
    _, packet = _packet()

    relative_reads = {path.relative_to(ROOT).as_posix() for path in reads}
    assert relative_reads == {
        "configs/acquisitions/m3/greedy-predictive-mean-v1.json",
        "configs/acquisitions/m3/random-seeded-sha256-v1.json",
        "configs/acquisitions/m3/uncertainty-bootstrap-std-v1.json",
        "configs/acquisitions/m4/ucb-bootstrap-std-beta1-v1.json",
        "configs/environments/m2-gate-a-linux-x86_64.json",
        "configs/experiments/m4-single-fidelity/alpha-amylase.json",
        "configs/experiments/m4-single-fidelity/ired.json",
        "manifests/experiments/m3/issue-66/protocol-freeze.v1.json",
        "manifests/experiments/m3/issue-66/run-spec-matrix.v1.json",
    }
    assert packet["truth_boundary"] == {
        "allowed_future_runtime_path": "authorized replay entry -> ReplayEngine -> HiddenOracle",
        "candidate_truth_read_authorized": False,
        "held_out_read_authorized": False,
        "oracle_path_bound": False,
        "packet_build_candidate_truth_reads": 0,
        "packet_build_real_input_reads": 0,
        "runtime_oracle_binding_required": True,
    }
    assert packet["authorization_boundaries"] == {
        "cos_access_authorized": False,
        "packet_review_authorizes_execution": False,
        "paid_compute_authorized": False,
        "pilot_execution_authorized": False,
        "server_access_authorized": False,
    }


def test_loader_rebuilds_exact_packet_and_rejects_tamper() -> None:
    payload, _ = _packet()
    digest = hashlib.sha256(payload).hexdigest()

    loaded = load_pilot_preflight_packet(
        payload,
        expected_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    assert loaded["schema_version"] == "m4-pilot-preflight-packet-v1"

    tampered = payload.replace(
        b'"pilot_execution_authorized": false',
        b'"pilot_execution_authorized": true',
    )
    with pytest.raises(ContractValidationError, match="identity"):
        load_pilot_preflight_packet(
            tampered,
            expected_sha256=digest,
            repository_root=ROOT,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
        )


def test_cli_prints_reproducible_packet_without_writing_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = tuple(tmp_path.iterdir())
    result = main(
        [
            "m4-pilot-preflight",
            "--reviewed-git-commit",
            REVIEWED_COMMIT,
            "--reviewed-git-tree",
            REVIEWED_TREE,
            "--repository-root",
            str(ROOT),
        ]
    )

    assert result == 0
    assert capsys.readouterr().out.encode() == build_pilot_preflight_packet(
        ROOT,
        REVIEWED_COMMIT,
        REVIEWED_TREE,
    )
    assert tuple(tmp_path.iterdir()) == before
