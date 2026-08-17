from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pcdb.contracts import ContractValidationError, RunSpec, load_high_value_result
from pcdb.contracts.experiment import canonical_json_bytes, load_experiment_config
from pcdb.contracts.high_value import MetricResultEvidence
from pcdb.m4 import materialize_task_seed_aggregate
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet
from pcdb.m4.screening import (
    PilotRunRecall,
    PilotScreeningDecision,
    _evaluate_validated_pilot_recalls,
    evaluate_pilot_screening,
)

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


def _pilot_recall_records(
    differences: dict[tuple[str, str, str, int, str], float] | None = None,
) -> list[PilotRunRecall]:
    packet = json.loads(build_pilot_preflight_packet(ROOT, "a" * 40, "b" * 40))
    differences = {} if differences is None else differences
    records = []
    for item in packet["run_specs"]:
        run_spec = RunSpec(**item["run_spec"])
        recalls = tuple(
            (
                definition_id,
                0.5
                + differences.get(
                    (
                        item["task_id"],
                        run_spec.model_id,
                        run_spec.acquisition_id,
                        run_spec.seed,
                        definition_id,
                    ),
                    0.0,
                ),
            )
            for definition_id in (
                "upper-tail-1pct",
                "upper-tail-0.5pct",
                "upper-tail-2pct",
            )
        )
        records.append(
            PilotRunRecall(
                task_id=item["task_id"],
                run_spec=run_spec,
                trajectory_sha256=hashlib.sha256(run_spec.run_id.encode()).hexdigest(),
                recalls=recalls,
            )
        )
    return records


def _evaluate(records: list[PilotRunRecall]) -> PilotScreeningDecision:
    expected = _pilot_recall_records()
    return _evaluate_validated_pilot_recalls(
        records,
        expected_runs=tuple((record.task_id, record.run_spec) for record in expected),
    )


def test_public_pilot_screening_rejects_directly_forged_recall_records() -> None:
    records = _pilot_recall_records()
    forged_evidence: Any = records

    decision = evaluate_pilot_screening(
        forged_evidence,
        expected_runs=tuple((record.task_id, record.run_spec) for record in records),
        expected_packet_sha256="d" * 64,
        expected_execution_region="ap-chengdu",
    )

    assert decision.status == "INCONCLUSIVE-FAILURE"
    assert decision.selected_explorations == ()


def test_pilot_screening_applies_sensitivity_heterogeneity_and_ascii_tie_break() -> None:
    differences: dict[tuple[str, str, str, int, str], float] = {}

    def improve(
        task_id: str,
        model_id: str,
        acquisition_id: str,
        *,
        primary: float,
        sensitivities: tuple[float, float],
    ) -> None:
        for seed in (1729, 1730, 1731):
            differences[(task_id, model_id, acquisition_id, seed, "upper-tail-1pct")] = primary
            differences[(task_id, model_id, acquisition_id, seed, "upper-tail-0.5pct")] = (
                sensitivities[0]
            )
            differences[(task_id, model_id, acquisition_id, seed, "upper-tail-2pct")] = (
                sensitivities[1]
            )

    for task_id in ("alpha-amylase", "ired"):
        for acquisition_id in (
            "uncertainty-bootstrap-std-v1",
            "ucb-bootstrap-std-beta1-v1",
        ):
            improve(
                task_id,
                "ridge-sklearn-v1",
                acquisition_id,
                primary=0.02,
                sensitivities=(0.0, 0.0),
            )
    improve(
        "alpha-amylase",
        "random-forest-sklearn-v1",
        "uncertainty-bootstrap-std-v1",
        primary=0.02,
        sensitivities=(0.01, 0.01),
    )
    improve(
        "ired",
        "random-forest-sklearn-v1",
        "uncertainty-bootstrap-std-v1",
        primary=-0.01,
        sensitivities=(0.0, 0.0),
    )
    improve(
        "alpha-amylase",
        "xgboost-cpu-v1",
        "uncertainty-bootstrap-std-v1",
        primary=0.02,
        sensitivities=(-0.01, 0.01),
    )

    decision = _evaluate(_pilot_recall_records(differences))

    assert decision.status == "EXPAND"
    assert decision.selected_explorations == (
        ("ridge-sklearn-v1", "ucb-bootstrap-std-beta1-v1"),
        ("random-forest-sklearn-v1", "uncertainty-bootstrap-std-v1"),
    )
    by_model = {item.model_id: item for item in decision.model_decisions}
    assert by_model["ridge-sklearn-v1"].selection_status == "cross-task-directional"
    assert by_model["random-forest-sklearn-v1"].selection_status == ("retain-as-heterogeneous")
    assert by_model["xgboost-cpu-v1"].selection_status == "pilot-eliminated"
    fragile = [
        item
        for item in by_model["xgboost-cpu-v1"].task_decisions
        if item.acquisition_id == "uncertainty-bootstrap-std-v1" and item.task_id == "alpha-amylase"
    ]
    assert len(fragile) == 1 and fragile[0].status == "fragile-inconclusive"


def test_pilot_screening_exact_threshold_and_incomplete_evidence_are_fail_closed() -> None:
    differences = {
        (
            "alpha-amylase",
            "ridge-sklearn-v1",
            "uncertainty-bootstrap-std-v1",
            seed,
            definition_id,
        ): value
        for seed, value in zip((1729, 1730, 1731), (0.015, 0.015, 0.0), strict=True)
        for definition_id in (
            "upper-tail-1pct",
            "upper-tail-0.5pct",
            "upper-tail-2pct",
        )
    }
    decision = _evaluate(_pilot_recall_records(differences))
    ridge = decision.model_decisions[0]

    assert decision.status == "EXPAND"
    assert ridge.selected_acquisition_id == "uncertainty-bootstrap-std-v1"
    threshold = [
        item
        for item in ridge.task_decisions
        if item.task_id == "alpha-amylase" and item.acquisition_id == "uncertainty-bootstrap-std-v1"
    ][0]
    assert threshold.positive_primary_seeds == 2
    assert threshold.primary_mean_difference == 0.01
    assert threshold.status == "task-pass"

    assert _evaluate(_pilot_recall_records()).status == "STOP"
    incomplete = _evaluate(_pilot_recall_records()[:-1])
    assert incomplete.status == "INCONCLUSIVE-FAILURE"
    assert incomplete.selected_explorations == ()
