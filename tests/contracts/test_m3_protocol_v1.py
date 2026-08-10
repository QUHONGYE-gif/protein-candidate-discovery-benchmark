from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from pcdb.contracts.experiment import canonical_json_bytes, load_experiment_config
from pcdb.contracts.run import RunSpec

ROOT = Path(__file__).parents[2]
MATRIX_PATH = Path("manifests/experiments/m3/issue-66/run-spec-matrix.v1.json")
FREEZE_PATH = Path("manifests/experiments/m3/issue-66/protocol-freeze.v1.json")
WORKED_EXAMPLE_PATH = Path("tests/fixtures/m3/issue-66/worked-example.v1.json")


def _load_canonical(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = (ROOT / path).read_bytes()
    value = json.loads(payload)
    assert canonical_json_bytes(value) == payload
    return value, payload


def _tuple_sha256(values: list[object]) -> str:
    payload = json.dumps(
        values,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_protocol_freeze_inventory_exact_covers_reviewed_artifacts() -> None:
    freeze, _ = _load_canonical(FREEZE_PATH)

    assert freeze["schema_version"] == "m3-protocol-freeze-v1"
    assert freeze["protocol_version"] == "m3-single-fidelity-replay-v1"
    assert freeze["predecessor_main_commit"] == ("e2321596958e37491d25152ecd68a36aa059f745")
    assert len(freeze["artifact_inventory"]) == 8

    paths: set[str] = set()
    for artifact in freeze["artifact_inventory"]:
        path = artifact["path"]
        assert path not in paths
        paths.add(path)
        payload = (ROOT / path).read_bytes()
        assert len(payload) == artifact["size_bytes"]
        assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]

    assert freeze["authorization_boundaries"] == {
        "protocol_bytes_authorize_gate_2": False,
        "protocol_bytes_authorize_m3_formal_execution": False,
        "protocol_bytes_authorize_real_input_preflight": False,
        "protocol_bytes_authorize_synthetic_tdd": False,
        "synthetic_tdd_requires_independent_exact_bytes_review": True,
    }
    assert freeze["materialization_side_effects"] == {
        "candidate_truth_reads": 0,
        "cos_writes": 0,
        "held_out_reads": 0,
        "server_executions": 0,
    }


def test_acquisition_configs_are_canonical_and_fail_closed() -> None:
    matrix, _ = _load_canonical(MATRIX_PATH)
    loaded = {}
    for reference in matrix["acquisition_configs"]:
        value, payload = _load_canonical(Path(reference["path"]))
        assert len(payload) == reference["size_bytes"]
        assert hashlib.sha256(payload).hexdigest() == reference["sha256"]
        assert value["acquisition_id"] == reference["acquisition_id"]
        assert value["schema_version"] == "m3-acquisition-config-v1"
        assert value["failure_policy"]["fallback"] == "none"
        assert value["ranking"]["secondary"] == "candidate_id_ascending"
        loaded[value["acquisition_id"]] = value

    assert loaded["random-seeded-sha256-v1"]["score"] == {
        "canonical_tuple": [
            "protocol_id",
            "task_id",
            "seed",
            "round",
            "candidate_id",
        ],
        "encoding": "utf8-json-compact-v1",
        "hash": "sha256",
        "kind": "seeded-hash",
    }
    assert loaded["greedy-predictive-mean-v1"]["score"]["uses_unrevealed_labels"] is False

    uncertainty = loaded["uncertainty-bootstrap-std-v1"]["score"]
    assert uncertainty["kind"] == "bootstrap-prediction-standard-deviation"
    assert uncertainty["ddof"] == 0
    assert uncertainty["model_config_policy"] == "reuse-frozen-model-config"
    assert uncertainty["uses_unrevealed_labels"] is False
    assert uncertainty["bootstrap"] == {
        "index_generation": {
            "apply_to": "candidate-id-ascending-observation-table",
            "bit_generator": "numpy.random.PCG64",
            "constructor": "numpy.random.Generator(numpy.random.PCG64(member_seed_uint64))",
            "draw_api": "numpy.random.Generator.integers",
            "draw_calls_per_member": 1,
            "dtype": "numpy.int64",
            "endpoint": False,
            "high": "revealed_observation_count",
            "low": 0,
            "order": "preserve-draw-order",
            "size": "revealed_observation_count",
        },
        "member_count": 5,
        "member_indices": [0, 1, 2, 3, 4],
        "member_seed": {
            "canonical_tuple": [
                "protocol_id",
                "run_seed",
                "task_id",
                "model_id",
                "round",
                "member_index",
            ],
            "derivation": "sha256_first_8_bytes_unsigned_big_endian",
            "encoding": "utf8-json-compact-v1",
        },
        "rng": "numpy-pcg64",
        "sample_size": "revealed_observation_count",
        "sampling": "with-replacement",
    }


def test_uncertainty_bootstrap_golden_vector_is_exactly_reconstructable() -> None:
    fixture, _ = _load_canonical(WORKED_EXAMPLE_PATH)
    golden = fixture["uncertainty_bootstrap_golden_vector"]

    assert golden["observation_row_count"] == 16
    assert golden["member_indices"] == [0, 1, 2, 3, 4]

    rebuilt = []
    for member_index in golden["member_indices"]:
        tuple_values = [
            fixture["protocol_id"],
            golden["run_seed"],
            fixture["task_id"],
            golden["model_id"],
            golden["round"],
            member_index,
        ]
        seed = int.from_bytes(bytes.fromhex(_tuple_sha256(tuple_values))[:8], "big")
        generator = np.random.Generator(np.random.PCG64(seed))
        indices = generator.integers(
            0,
            golden["observation_row_count"],
            size=golden["observation_row_count"],
            dtype=np.int64,
            endpoint=False,
        )
        rebuilt.append(
            {
                "indices": indices.tolist(),
                "member_index": member_index,
                "member_seed_uint64": seed,
            }
        )

    assert rebuilt == golden["members"]


@pytest.mark.parametrize("task_id", ["alpha-amylase", "ired"])
def test_m3_experiment_configs_reuse_v2_and_freeze_one_profile(task_id: str) -> None:
    matrix, _ = _load_canonical(MATRIX_PATH)
    reference = next(item for item in matrix["experiment_configs"] if item["task_id"] == task_id)
    payload = (ROOT / reference["path"]).read_bytes()

    loaded = load_experiment_config(payload)

    assert len(payload) == reference["size_bytes"]
    assert loaded.sha256 == reference["sha256"]
    assert loaded.value["task_id"] == task_id
    assert loaded.value["phase"] == "frozen"
    assert loaded.value["held_out_authorized"] is False
    assert loaded.value["models"] == tuple(model["model_id"] for model in matrix["models"])
    assert loaded.value["acquisitions"] == tuple(
        config["acquisition_id"] for config in matrix["acquisition_configs"]
    )
    assert loaded.value["seeds"] == (1729,)
    assert loaded.value["initial_sample_sizes"] == (16,)
    assert loaded.value["batch_sizes"] == (8,)
    assert loaded.value["budgets"] == (32,)


def test_matrix_expands_to_exactly_18_valid_unique_run_specs() -> None:
    matrix, _ = _load_canonical(MATRIX_PATH)
    run_spec_schema = json.loads((ROOT / "schemas/run-spec.schema.json").read_text())

    assert matrix["matrix_order"] == [
        "task_id",
        "model_id",
        "acquisition_id",
        "seed",
    ]
    assert matrix["late_bound_run_spec_fields"] == ["environment_digest", "git_commit"]
    assert matrix["expected_run_spec_count"] == 18
    assert matrix["run_spec_profile"] == {
        "batch_size": 8,
        "budget": 32,
        "initial_sample_size": 16,
        "schema_version": "1",
    }

    manifests = {item["task_id"]: item for item in matrix["manifest_chains"]}
    experiments = {item["task_id"]: item for item in matrix["experiment_configs"]}
    models = {item["model_id"]: item for item in matrix["models"]}
    acquisitions = {item["acquisition_id"]: item for item in matrix["acquisition_configs"]}
    combinations = list(
        itertools.product(
            matrix["tasks"],
            models,
            acquisitions,
            matrix["seeds"],
        )
    )
    assert len(combinations) == matrix["expected_run_spec_count"]

    run_ids = set()
    for task_id, model_id, acquisition_id, seed in combinations:
        manifest = manifests[task_id]
        run_spec = RunSpec(
            schema_version=matrix["run_spec_profile"]["schema_version"],
            protocol_version=matrix["protocol_version"],
            git_commit="f" * 40,
            environment_digest="e" * 64,
            dataset_manifest_sha256=manifest["dataset_manifest_sha256"],
            processing_manifest_sha256=manifest["processing_manifest_sha256"],
            split_sha256=manifest["split_sha256"],
            feature_sha256=manifest["feature_sha256"],
            experiment_config_sha256=experiments[task_id]["sha256"],
            model_id=model_id,
            model_config_sha256=models[model_id]["model_config_sha256"],
            acquisition_id=acquisition_id,
            acquisition_config_sha256=acquisitions[acquisition_id]["sha256"],
            seed=seed,
            initial_sample_size=matrix["run_spec_profile"]["initial_sample_size"],
            batch_size=matrix["run_spec_profile"]["batch_size"],
            budget=matrix["run_spec_profile"]["budget"],
        )
        Draft202012Validator(run_spec_schema).validate(asdict(run_spec))
        run_ids.add(run_spec.run_id)

    assert len(run_ids) == 18
    assert matrix["authorization_boundaries"] == {
        "protocol_bytes_authorize_gate_2": False,
        "protocol_bytes_authorize_m3_formal_execution": False,
        "protocol_bytes_authorize_real_input_preflight": False,
        "protocol_bytes_authorize_synthetic_tdd": False,
        "synthetic_tdd_requires_independent_exact_bytes_review": True,
    }
    assert matrix["materialization_side_effects"] == {
        "candidate_truth_reads": 0,
        "cos_writes": 0,
        "held_out_reads": 0,
        "server_executions": 0,
    }


def test_worked_example_rebuilds_selection_metrics_and_recovery_identity() -> None:
    fixture, _ = _load_canonical(WORKED_EXAMPLE_PATH)
    candidates = fixture["candidate_ids"]
    protocol_id = fixture["protocol_id"]
    task_id = fixture["task_id"]
    seed = fixture["seed"]

    initial_ranked = sorted(
        (
            _tuple_sha256([protocol_id, task_id, seed, candidate_id]),
            candidate_id,
        )
        for candidate_id in candidates
    )
    assert fixture["initial_selection"]["ranked"] == [
        {"candidate_id": candidate_id, "sha256": digest} for digest, candidate_id in initial_ranked
    ]
    initial = fixture["initial_selection"]["selected_candidate_ids"]
    assert initial == [candidate_id for _, candidate_id in initial_ranked[:2]]

    random_ranked = sorted(
        (
            _tuple_sha256([protocol_id, task_id, seed, 1, candidate_id]),
            candidate_id,
        )
        for candidate_id in candidates
        if candidate_id not in set(initial)
    )
    assert fixture["random_round"]["ranked"] == [
        {"candidate_id": candidate_id, "sha256": digest} for digest, candidate_id in random_ranked
    ]
    assert fixture["random_round"]["selected_candidate_ids"] == [
        candidate_id for _, candidate_id in random_ranked[:2]
    ]

    trajectory = fixture["trajectory"]
    assert (
        hashlib.sha256(canonical_json_bytes(trajectory)).hexdigest()
        == fixture["expected_trajectory_sha256"]
    )
    assert fixture["recovery"]["resumed_trajectory_sha256"] == fixture["expected_trajectory_sha256"]
    assert fixture["recovery"]["successful_attempt_overwrite_allowed"] is False

    labels = fixture["synthetic_oracle"]["labels"]
    high_value = set(fixture["synthetic_oracle"]["high_value_candidate_ids"])
    universe_max = max(labels.values())
    revealed: list[str] = []
    for event, expected in zip(trajectory["events"], fixture["expected_metrics"], strict=True):
        revealed.extend(event["selected_candidate_ids"])
        assert len(revealed) == len(set(revealed)) == expected["distinct_revealed"]
        best = max(labels[candidate_id] for candidate_id in revealed)
        assert best == pytest.approx(expected["best_found_value"])
        assert universe_max - best == pytest.approx(expected["simple_regret"])
        recall = len(set(revealed) & high_value) / len(high_value)
        assert recall == pytest.approx(expected["high_value_discovery_recall"])

    assert fixture["expected_metrics"][0]["success_at_budget"] == {
        "status": "undefined",
        "undefined_reason": "budget_not_reached",
        "value": None,
    }
    assert fixture["expected_metrics"][1]["success_at_budget"] == {
        "status": "defined",
        "undefined_reason": None,
        "value": True,
    }
    assert "labels" not in fixture["acquisition_visible_fields"]
    assert "high_value_candidate_ids" not in fixture["acquisition_visible_fields"]
