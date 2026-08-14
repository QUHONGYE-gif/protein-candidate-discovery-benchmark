from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from pcdb.contracts import ContractValidationError, RunSpec, load_high_value_result
from pcdb.contracts.experiment import canonical_json_bytes, load_experiment_config
from pcdb.contracts.high_value import MetricResultEvidence
from pcdb.m4 import materialize_task_seed_aggregate

ROOT = Path(__file__).parents[2]
CONFIG_DIRECTORY = ROOT / "configs/experiments/m4-single-fidelity"
UCB_CONFIG = ROOT / "configs/acquisitions/m4/ucb-bootstrap-std-beta1-v1.json"
VALID_MANIFESTS = ROOT / "tests/fixtures/contracts/valid-manifests.json"

EXPERIMENT_CONFIG_IDENTITIES = {
    "alpha-amylase": (
        4_894,
        "193f8ee0102a654e63cd7ea31fc77a93bcf7f5fae0c0305570117eac54c097aa",
    ),
    "ired": (
        4_876,
        "7bfdc48ca2f4d41f039b8f119c3dca5910e9eb26c156cdee53f584b4af7c38fa",
    ),
}


@pytest.mark.parametrize("task_id", ["alpha-amylase", "ired"])
def test_m4_experiment_config_is_one_closed_ten_seed_profile(task_id: str) -> None:
    payload = (CONFIG_DIRECTORY / f"{task_id}.json").read_bytes()
    loaded = load_experiment_config(payload)
    expected_size, expected_sha256 = EXPERIMENT_CONFIG_IDENTITIES[task_id]

    assert len(payload) == expected_size
    assert loaded.sha256 == expected_sha256
    assert loaded.value["phase"] == "development"
    assert loaded.value["held_out_authorized"] is False
    assert loaded.value["models"] == (
        "ridge-sklearn-v1",
        "random-forest-sklearn-v1",
        "xgboost-cpu-v1",
    )
    assert loaded.value["acquisitions"] == (
        "random-seeded-sha256-v1",
        "greedy-predictive-mean-v1",
        "uncertainty-bootstrap-std-v1",
        "ucb-bootstrap-std-beta1-v1",
    )
    assert loaded.value["seeds"] == tuple(range(1729, 1739))
    assert loaded.value["initial_sample_sizes"] == (32, 64)
    assert loaded.value["batch_sizes"] == (16,)
    assert loaded.value["budgets"] == (128,)


def test_m4_matrix_arithmetic_stays_within_reviewed_caps() -> None:
    assert 2 * 3 * 4 * 1 * 3 == 72
    for selected_exploration_models in (1, 2, 3):
        assert 240 + 40 * selected_exploration_models <= 360
        assert 276 + 34 * selected_exploration_models <= 378


def test_m4_ucb_config_freezes_formula_rng_and_tie_break() -> None:
    payload = UCB_CONFIG.read_bytes()
    value = json.loads(payload)

    assert len(payload) == 886
    assert canonical_json_bytes(value) == payload
    assert (
        hashlib.sha256(payload).hexdigest()
        == "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d"
    )
    assert value["acquisition_id"] == "ucb-bootstrap-std-beta1-v1"
    assert value["score"]["beta"] == 1.0
    assert value["score"]["bootstrap"] == {
        "ddof": 0,
        "member_count": 5,
        "member_seed_policy": "reuse-uncertainty-bootstrap-std-v1",
        "rng": "numpy-pcg64",
        "sampling": "with-replacement",
    }
    assert value["ranking"]["secondary"] == "candidate_id_ascending"
    assert value["failure_policy"]["fallback"] == "none"


def _metric_evidence(
    *,
    experiment_config_sha256: str,
    seed: int,
    value: bool,
) -> MetricResultEvidence:
    config = load_experiment_config((CONFIG_DIRECTORY / "alpha-amylase.json").read_bytes())
    run_spec = RunSpec(
        schema_version="1",
        protocol_version="m4-single-fidelity-screening-v1",
        git_commit="a" * 40,
        environment_digest="b" * 64,
        dataset_manifest_sha256=config.value["dataset_manifest_sha256"],
        processing_manifest_sha256=config.value["processing_manifest_sha256"],
        split_sha256=config.value["split_sha256"],
        feature_sha256="c" * 64,
        experiment_config_sha256=experiment_config_sha256,
        model_id="ridge-sklearn-v1",
        model_config_sha256=("806e9bb5c16a511b233213ca6f1f08ac848eeb77c7e9702915171ce4a22293d7"),
        acquisition_id="greedy-predictive-mean-v1",
        acquisition_config_sha256=(
            "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"
        ),
        seed=seed,
        initial_sample_size=32,
        batch_size=16,
        budget=128,
    )
    raw = json.loads(VALID_MANIFESTS.read_text())
    result = copy.deepcopy(raw["high-value-metric-result.schema.json"])
    result.update(
        {
            "acquisition_config_sha256": run_spec.acquisition_config_sha256,
            "acquisition_id": run_spec.acquisition_id,
            "attempt_id": "attempt-001",
            "budget": 128,
            "dataset_manifest_sha256": run_spec.dataset_manifest_sha256,
            "definition_id": "upper-tail-1pct",
            "evaluation_scope": "sequential",
            "experiment_config_sha256": experiment_config_sha256,
            "input_artifact_sha256": "d" * 64,
            "metric_id": "success_at_budget",
            "model_config_sha256": run_spec.model_config_sha256,
            "model_id": run_spec.model_id,
            "processing_manifest_sha256": run_spec.processing_manifest_sha256,
            "round": 6,
            "run_id": run_spec.run_id,
            "seed": seed,
            "split_sha256": run_spec.split_sha256,
            "status": "defined",
            "support": {
                "actual_high_value_count": 23,
                "boundary_score": 1.0,
                "boundary_tie_count": 1,
                "evaluated_candidate_count": 128,
                "frozen_candidate_count": 2262,
                "nominal_k": 23,
            },
            "task_id": "alpha-amylase",
            "undefined_reason": None,
            "value": value,
        }
    )
    loaded = load_high_value_result(
        canonical_json_bytes(result),
        expected_schema_version="high-value-metric-result-v1",
    )
    attempt = {
        "attempt_id": "attempt-001",
        "completion_marker": {
            "object_key": f"runs/{run_spec.run_id}/attempt-001/_SUCCESS",
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "size_bytes": 0,
        },
        "executed_environment_digest": run_spec.environment_digest,
        "executed_git_commit": run_spec.git_commit,
        "outputs": [
            {
                "object_key": f"runs/{run_spec.run_id}/attempt-001/metric.json",
                "sha256": loaded.sha256,
                "size_bytes": len(loaded.canonical_bytes),
            }
        ],
        "run_id": run_spec.run_id,
        "status": "succeeded",
    }
    return MetricResultEvidence(
        result=loaded,
        run_spec=run_spec,
        run_attempt=attempt,
        expected_executed_git_commit=run_spec.git_commit,
        expected_executed_environment_digest=run_spec.environment_digest,
    )


def test_m4_three_of_ten_remains_per_run_only_and_ten_of_ten_aggregates() -> None:
    config = load_experiment_config((CONFIG_DIRECTORY / "alpha-amylase.json").read_bytes())
    evidences = [
        _metric_evidence(
            experiment_config_sha256=config.sha256,
            seed=seed,
            value=seed % 2 == 0,
        )
        for seed in range(1729, 1739)
    ]

    with pytest.raises(ContractValidationError, match="per registered seed"):
        materialize_task_seed_aggregate(
            config,
            evidences[:3],
            input_metric_id="success_at_budget",
        )
    aggregate = materialize_task_seed_aggregate(
        config,
        evidences,
        input_metric_id="success_at_budget",
    )

    assert aggregate.value["metric_id"] == "success_probability"
    assert aggregate.value["value"] == 0.5
    assert tuple(item["seed"] for item in aggregate.value["ordered_inputs"]) == tuple(
        range(1729, 1739)
    )
