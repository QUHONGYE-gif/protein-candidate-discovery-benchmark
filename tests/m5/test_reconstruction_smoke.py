"""M5A reconstruction-smoke isolation, encoding, and comparator checks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from pcdb.contracts import ContractValidationError, RunSpec
from pcdb.contracts.feature_identity import encode_feature_matrix
from pcdb.m2.features import (
    CandidateSequence,
    build_sequence_feature_rows,
    sequence_feature_definitions,
)
from pcdb.m2.models import M4_MODEL_CONFIG_SHA256
from pcdb.m5.reconstruction_smoke import (
    ReconstructionStop,
    binary64_be_hex,
    compact_json_bytes,
    compare_reconstruction_bytes,
    load_reconstruction_contract,
    prepare_reconstruction_input,
    ranking_artifact_bytes,
)
from pcdb.replay.engine import ReplayEvent, ReplayTrajectory
from pcdb.replay.oracle import Observation

ROOT = Path(__file__).resolve().parents[2]


def _candidate(index: int) -> str:
    return hashlib.sha256(f"candidate:{index}".encode()).hexdigest()


def _run_spec() -> RunSpec:
    return RunSpec(
        schema_version="1",
        protocol_version="m4-single-fidelity-screening-v1",
        git_commit="a" * 40,
        environment_digest="b" * 64,
        dataset_manifest_sha256="c" * 64,
        processing_manifest_sha256="d" * 64,
        split_sha256="e" * 64,
        feature_sha256="f" * 64,
        experiment_config_sha256="1" * 64,
        model_id="random-forest-sklearn-v1",
        model_config_sha256="2" * 64,
        acquisition_id="uncertainty-bootstrap-std-v1",
        acquisition_config_sha256="3" * 64,
        seed=1729,
        initial_sample_size=2,
        batch_size=2,
        budget=6,
    )


def _trajectory(*, target_ids: tuple[str, str] | None = None) -> ReplayTrajectory:
    selected = (
        (_candidate(0), _candidate(1)),
        target_ids or (_candidate(2), _candidate(3)),
        (_candidate(4), _candidate(5)),
    )
    labels = ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0))
    events: list[ReplayEvent] = []
    for round_index, (candidate_ids, values) in enumerate(zip(selected, labels, strict=True)):
        event = ReplayEvent(
            round=round_index,
            selected_candidate_ids=candidate_ids,
            observations=tuple(
                Observation(candidate_id=candidate_id, label=value)
                for candidate_id, value in zip(candidate_ids, values, strict=True)
            ),
            distinct_revealed=(round_index + 1) * 2,
            checkpoint_sha256="0" * 64,
        )
        prefix = ReplayTrajectory(
            run_spec=_run_spec(),
            task_id="alpha-amylase",
            events=tuple((*events, event)),
        )
        events.append(
            replace(
                event,
                checkpoint_sha256=hashlib.sha256(prefix.checkpoint_bytes(round_index)).hexdigest(),
            )
        )
    return ReplayTrajectory(
        run_spec=_run_spec(),
        task_id="alpha-amylase",
        events=tuple(events),
    )


def test_compact_json_and_binary64_encoding_are_exact() -> None:
    assert compact_json_bytes({"z": 1, "a": [2]}) == b'{"a":[2],"z":1}\n'
    assert binary64_be_hex(1.0) == "3ff0000000000000"
    assert binary64_be_hex(-0.0) == "8000000000000000"
    with pytest.raises(ContractValidationError, match="finite"):
        binary64_be_hex(float("nan"))


def test_reconstruction_input_contains_only_prefix_and_remaining_universe() -> None:
    trajectory = _trajectory()
    all_ids = tuple(sorted(_candidate(index) for index in range(8)))

    prepared = prepare_reconstruction_input(
        trajectory=trajectory,
        round_index=1,
        candidate_ids=all_ids,
        state_identity={"state_id": "S0", "source_git_tree": "4" * 40},
        candidate_feature_sha256="5" * 64,
    )

    value = json.loads(prepared.input_bytes)
    assert value["round"] == 1
    assert [item["candidate_id"] for item in value["observations"]] == [
        _candidate(0),
        _candidate(1),
    ]
    assert tuple(value["remaining_candidate_ids"]) == tuple(
        candidate_id
        for candidate_id in all_ids
        if candidate_id not in {_candidate(0), _candidate(1)}
    )
    assert prepared.actual_selected_ids == (_candidate(2), _candidate(3))
    assert "actual_selected_ids" not in value
    assert "events" not in value

    changed_target = _trajectory(target_ids=(_candidate(6), _candidate(7)))
    changed = prepare_reconstruction_input(
        trajectory=changed_target,
        round_index=1,
        candidate_ids=all_ids,
        state_identity={"state_id": "S0", "source_git_tree": "4" * 40},
        candidate_feature_sha256="5" * 64,
    )
    assert changed.input_bytes == prepared.input_bytes
    assert changed.actual_selected_ids != prepared.actual_selected_ids


def test_ranking_artifact_uses_score_descending_then_candidate_id_ascending() -> None:
    input_value = {
        "schema_version": "m5a-reconstruction-input-v1",
        "batch_size": 2,
        "input_identity": {"state_id": "S0"},
        "remaining_candidate_ids": [_candidate(0), _candidate(1), _candidate(2)],
    }
    artifact = ranking_artifact_bytes(
        input_value=input_value,
        scores=(
            (_candidate(2), 1.0),
            (_candidate(1), 2.0),
            (_candidate(0), 2.0),
        ),
    )
    value = json.loads(artifact)
    tied = sorted((_candidate(0), _candidate(1)))
    assert [item["candidate_id"] for item in value["ranking"]] == [
        *tied,
        _candidate(2),
    ]
    assert value["top_k"] == tied
    assert all(len(item["score_binary64_be_hex"]) == 16 for item in value["ranking"])

    with pytest.raises(ContractValidationError, match="exact-cover"):
        ranking_artifact_bytes(
            input_value=input_value,
            scores=((_candidate(0), 1.0), (_candidate(1), 0.0)),
        )


def test_comparator_stops_on_byte_or_order_mismatch() -> None:
    input_value = {
        "schema_version": "m5a-reconstruction-input-v1",
        "batch_size": 2,
        "input_identity": {"state_id": "S0"},
        "remaining_candidate_ids": [_candidate(0), _candidate(1), _candidate(2)],
    }
    artifact = ranking_artifact_bytes(
        input_value=input_value,
        scores=((_candidate(0), 3.0), (_candidate(1), 2.0), (_candidate(2), 1.0)),
    )
    result = compare_reconstruction_bytes(
        first=artifact,
        second=artifact,
        actual_selected_ids=(_candidate(0), _candidate(1)),
    )
    assert result["byte_identical"] is True
    assert result["ordered_top_k_exact_match"] is True

    with pytest.raises(ReconstructionStop, match="byte-identical"):
        compare_reconstruction_bytes(
            first=artifact,
            second=artifact + b" ",
            actual_selected_ids=(_candidate(0), _candidate(1)),
        )
    with pytest.raises(ReconstructionStop, match="selected-ID/order"):
        compare_reconstruction_bytes(
            first=artifact,
            second=artifact,
            actual_selected_ids=(_candidate(1), _candidate(0)),
        )


def test_frozen_contract_exact_covers_ordinal_strata_and_rejects_drift() -> None:
    path = ROOT / "configs/experiments/m5/reconstruction-smoke-v1.json"
    value = load_reconstruction_contract(path.read_bytes())
    assert [state["state_id"] for state in value["states"]] == [f"S{index}" for index in range(8)]
    assert [state["round"] for state in value["states"]] == [1, 2, 3, 4, 5, 2, 1, 4]

    drifted = json.loads(path.read_bytes())
    drifted["states"][0]["round"] = 2
    with pytest.raises(ContractValidationError, match="ordinal rule"):
        load_reconstruction_contract(json.dumps(drifted).encode())


def test_worker_reconstructs_twice_from_prefix_only_fixture(tmp_path: Path) -> None:
    candidate_ids = tuple(sorted(_candidate(index) for index in range(12)))
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    rows = build_sequence_feature_rows(
        [
            CandidateSequence(
                candidate_id=candidate_id,
                sequence=alphabet[index] + alphabet[index + 1],
            )
            for index, candidate_id in enumerate(candidate_ids)
        ]
    )
    feature_bytes = encode_feature_matrix(
        rows,
        feature_definitions=sequence_feature_definitions(),
    )
    feature_path = tmp_path / "candidate-pool.csv"
    feature_path.write_bytes(feature_bytes)
    commit = subprocess.run(
        ("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ("git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run_spec = replace(
        _run_spec(),
        git_commit=commit,
        model_config_sha256=M4_MODEL_CONFIG_SHA256["random-forest-sklearn-v1"],
    )
    observations = [
        {"candidate_id": candidate_id, "value": float(index)}
        for index, candidate_id in enumerate(candidate_ids[:4])
    ]
    input_value = {
        "batch_size": 2,
        "input_identity": {
            "candidate_feature_sha256": hashlib.sha256(feature_bytes).hexdigest(),
            "run_id": run_spec.run_id,
            "source_git_commit": commit,
            "source_git_tree": tree,
            "state_id": "fixture",
        },
        "observations": observations,
        "remaining_candidate_ids": list(candidate_ids[4:]),
        "round": 1,
        "run_spec": run_spec.__dict__
        if hasattr(run_spec, "__dict__")
        else {field: getattr(run_spec, field) for field in run_spec.__dataclass_fields__},
        "schema_version": "m5a-reconstruction-input-v1",
        "task_id": "synthetic-toy",
        "tie_break": "score-descending-then-candidate-id-ascending-ascii",
    }
    input_path = tmp_path / "input.json"
    input_path.write_bytes(compact_json_bytes(input_value))
    outputs = [tmp_path / "ranking-A.json", tmp_path / "ranking-B.json"]
    environment = os.environ.copy()
    environment.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    for output in outputs:
        subprocess.run(
            (
                sys.executable,
                str(ROOT / "scripts/m5_reconstruction_worker.py"),
                "--historical-source",
                str(ROOT),
                "--input",
                str(input_path),
                "--candidate-features",
                str(feature_path),
                "--output",
                str(output),
            ),
            check=True,
            capture_output=True,
            env=environment,
        )
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
