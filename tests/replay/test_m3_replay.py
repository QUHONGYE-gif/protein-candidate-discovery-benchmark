from __future__ import annotations

import ast
import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import pcdb.replay.engine as replay_engine_module
from pcdb.contracts import ContractValidationError, RunSpec
from pcdb.contracts.experiment import canonical_json_bytes, load_experiment_config
from pcdb.contracts.feature_identity import (
    DESCRIPTOR_FORMAT_VERSION,
    DESCRIPTOR_SCHEMA_VERSION,
    FEATURE_ARTIFACT_SCHEMA_VERSION,
    FEATURE_CONFIG_SCHEMA_VERSION,
    FEATURE_REGISTRY_VERSION,
    FITTED_STATE_SCHEMA_VERSION,
    MATRIX_FORMAT_VERSION,
    PROTOCOL_VERSION,
    LoadedFeatureMatrix,
    canonical_feature_json_bytes,
    encode_feature_matrix,
    load_feature_matrix,
)
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.evaluation import HighValueEvaluator
from pcdb.m2.features import (
    FEATURE_CONFIG_ID,
    FEATURE_CONFIG_SHA256,
    FEATURE_CONFIG_SIZE_BYTES,
    CandidateSequence,
    build_sequence_feature_rows,
    sequence_feature_definitions,
)
from pcdb.m2.models import (
    MODEL_CONFIG_SHA256,
    DevelopmentObservation,
    fit_m4_regressor,
    fit_regressor,
    m4_model_config_bytes,
    model_config_bytes,
    predict_regressor,
)
from pcdb.replay import (
    HiddenOracle,
    Observation,
    ReplayEngine,
    bootstrap_draws,
    bootstrap_prediction_std,
    is_replay_attempt_eligible,
    load_replay_trajectory,
    publish_replay_attempt,
    ucb_prediction_scores,
)
from pcdb.replay.engine import _resolved_experiment_context
from pcdb.storage import ArtifactMetadata, LocalArtifactStore

ROOT = Path(__file__).parents[2]
WORKED_EXAMPLE = ROOT / "tests/fixtures/m3/issue-66/worked-example.v1.json"
M3_EXPERIMENT_CONFIG = ROOT / "configs/experiments/m3-single-fidelity/alpha-amylase.json"
M4_EXPERIMENT_CONFIG_DIRECTORY = ROOT / "configs/experiments/m4-single-fidelity"
DATASET_SHA256 = "a" * 64
SPLIT_SHA256 = "b" * 64


def _experiment_config_bytes(
    *,
    processing_manifest_sha256: str,
    initial_sample_size: int = 2,
    batch_size: int = 2,
    budget: int = 4,
) -> bytes:
    return canonical_json_bytes(
        {
            "acquisitions": [
                "random-seeded-sha256-v1",
                "greedy-predictive-mean-v1",
                "uncertainty-bootstrap-std-v1",
            ],
            "batch_sizes": [batch_size],
            "budgets": [budget],
            "dataset_manifest_sha256": DATASET_SHA256,
            "evaluation": {"primary_high_value_fraction": 0.01},
            "experiment_id": "m3-synthetic-replay-smoke-v1",
            "held_out_authorized": False,
            "initial_sample_sizes": [initial_sample_size],
            "models": [
                "ridge-sklearn-v1",
                "random-forest-sklearn-v1",
                "xgboost-cpu-v1",
            ],
            "phase": "smoke",
            "processing_manifest_sha256": processing_manifest_sha256,
            "schema_version": "1",
            "seeds": [1729],
            "split_sha256": SPLIT_SHA256,
            "task_id": "synthetic-toy",
        }
    )


def _m4_experiment_config_bytes(*, processing_manifest_sha256: str) -> bytes:
    value = json.loads(
        _experiment_config_bytes(processing_manifest_sha256=processing_manifest_sha256)
    )
    value["acquisitions"].append("ucb-bootstrap-std-beta1-v1")
    value["experiment_id"] = "m4-synthetic-replay-smoke-v1"
    return canonical_json_bytes(value)


def _hidden_truth_bytes(truth: dict[str, float]) -> bytes:
    rows = ["candidate_id,target"]
    rows.extend(
        f"{candidate_id},{json.dumps(float(truth[candidate_id]), allow_nan=False)}"
        for candidate_id in sorted(truth)
    )
    return ("\n".join(rows) + "\n").encode("utf-8")


def _processing_manifest_bytes(truth: dict[str, float]) -> bytes:
    hidden_truth = _hidden_truth_bytes(truth)
    return canonical_json_bytes(
        {
            "candidate_id_field": "candidate_id",
            "candidate_id_unique": True,
            "dataset_manifest_sha256": DATASET_SHA256,
            "duplicate_policy": {
                "exact_duplicates": "error",
                "near_duplicates": "not_assessed",
            },
            "git_commit": "f" * 40,
            "hidden_label_field": "target",
            "known_limitations": ["synthetic fixture only"],
            "label_direction": "higher_is_better",
            "observable_feature_fields": ["sequence"],
            "output_files": [
                {
                    "object_key": "synthetic/hidden-truth.csv",
                    "row_count": len(truth),
                    "sha256": hashlib.sha256(hidden_truth).hexdigest(),
                    "size_bytes": len(hidden_truth),
                }
            ],
            "processing_config_sha256": "c" * 64,
            "processing_id": "m3-synthetic-replay-v1",
            "schema_version": "1",
        }
    )


def _feature_descriptor_bytes(
    features: LoadedFeatureMatrix,
    *,
    processing_manifest_sha256: str,
) -> bytes:
    feature_ids = list(features.feature_ids)
    matrices = []
    for role in ("development", "validation", "candidate-pool"):
        matrices.append(
            {
                "artifact": {
                    "object_key": f"synthetic/features/{role}.csv",
                    "sha256": features.sha256,
                    "size_bytes": len(features.canonical_bytes),
                },
                "candidate_id_field": "candidate_id",
                "column_count": len(feature_ids),
                "feature_ids": feature_ids,
                "fit_state_refs": [],
                "role": role,
                "row_count": len(features.candidate_ids),
            }
        )
    return canonical_feature_json_bytes(
        {
            "benchmark_task_id": "synthetic-toy",
            "dataset_manifest_sha256": DATASET_SHA256,
            "dataset_task_id": "synthetic-toy",
            "descriptor_format_version": DESCRIPTOR_FORMAT_VERSION,
            "feature_artifact_contract_status": "production",
            "feature_artifact_schema_version": FEATURE_ARTIFACT_SCHEMA_VERSION,
            "feature_config": {
                "feature_config_id": FEATURE_CONFIG_ID,
                "object_key": "synthetic/features/config.json",
                "schema_version": FEATURE_CONFIG_SCHEMA_VERSION,
                "sha256": FEATURE_CONFIG_SHA256,
                "size_bytes": FEATURE_CONFIG_SIZE_BYTES,
            },
            "feature_definitions": list(sequence_feature_definitions()),
            "feature_registry_version": FEATURE_REGISTRY_VERSION,
            "fitted_state_format_version": FITTED_STATE_SCHEMA_VERSION,
            "fitted_states": [],
            "generator": {
                "environment_digest": "e" * 64,
                "git_commit": "f" * 40,
            },
            "matrices": matrices,
            "matrix_format_version": MATRIX_FORMAT_VERSION,
            "observable_sources": ["sequence"],
            "processing_manifest_sha256": processing_manifest_sha256,
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": DESCRIPTOR_SCHEMA_VERSION,
            "split_sha256": SPLIT_SHA256,
        }
    )


def _random_run_spec(
    candidate_ids: tuple[str, ...] | None = None,
    *,
    truth: dict[str, float] | None = None,
    initial_sample_size: int = 2,
    batch_size: int = 2,
    budget: int = 4,
) -> RunSpec:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    if candidate_ids is None:
        candidate_ids = tuple(fixture["candidate_ids"])
    if truth is None:
        truth = fixture["synthetic_oracle"]["labels"]
    features = _loaded_features(candidate_ids)
    processing_manifest = _processing_manifest_bytes(truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    feature_descriptor = _feature_descriptor_bytes(
        features,
        processing_manifest_sha256=processing_sha256,
    )
    experiment_config = load_experiment_config(
        _experiment_config_bytes(
            processing_manifest_sha256=processing_sha256,
            initial_sample_size=initial_sample_size,
            batch_size=batch_size,
            budget=budget,
        )
    )
    return RunSpec(
        schema_version="1",
        protocol_version="m3-single-fidelity-replay-v1",
        git_commit="f" * 40,
        environment_digest="e" * 64,
        dataset_manifest_sha256=experiment_config.value["dataset_manifest_sha256"],
        processing_manifest_sha256=experiment_config.value["processing_manifest_sha256"],
        split_sha256=experiment_config.value["split_sha256"],
        feature_sha256=hashlib.sha256(feature_descriptor).hexdigest(),
        experiment_config_sha256=experiment_config.sha256,
        model_id="ridge-sklearn-v1",
        model_config_sha256=MODEL_CONFIG_SHA256["ridge-sklearn-v1"],
        acquisition_id="random-seeded-sha256-v1",
        acquisition_config_sha256=(
            "24f80499cb48ad6716f86a9c47bf098d47acdb78841bcb7b8926504619983e0f"
        ),
        seed=1729,
        initial_sample_size=initial_sample_size,
        batch_size=batch_size,
        budget=budget,
    )


def _m4_ucb_run_spec(
    candidate_ids: tuple[str, ...] | None = None,
    *,
    truth: dict[str, float] | None = None,
) -> RunSpec:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    if candidate_ids is None:
        candidate_ids = tuple(fixture["candidate_ids"])
    if truth is None:
        truth = fixture["synthetic_oracle"]["labels"]
    processing_manifest = _processing_manifest_bytes(truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    experiment_config = _m4_experiment_config_bytes(processing_manifest_sha256=processing_sha256)
    return replace(
        _random_run_spec(candidate_ids, truth=truth),
        protocol_version="m4-single-fidelity-screening-v1",
        experiment_config_sha256=hashlib.sha256(experiment_config).hexdigest(),
        acquisition_id="ucb-bootstrap-std-beta1-v1",
        acquisition_config_sha256=(
            "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d"
        ),
    )


def _loaded_features(candidate_ids: tuple[str, ...]) -> LoadedFeatureMatrix:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    sequences = tuple(
        alphabet[index % len(alphabet)] + alphabet[(index // len(alphabet)) % len(alphabet)]
        for index in range(len(candidate_ids))
    )
    rows = build_sequence_feature_rows(
        [
            CandidateSequence(candidate_id=candidate_id, sequence=sequence)
            for candidate_id, sequence in zip(candidate_ids, sequences, strict=True)
        ]
    )
    definitions = sequence_feature_definitions()
    return load_feature_matrix(
        encode_feature_matrix(rows, feature_definitions=definitions),
        feature_definitions=definitions,
    )


def _engine(
    *,
    run_spec: RunSpec,
    candidate_ids: tuple[str, ...],
    truth: dict[str, float],
    oracle: HiddenOracle | None = None,
    evaluator: HighValueEvaluator | None = None,
    task_id: str = "synthetic-toy",
    high_value_fraction: float | None = None,
    candidate_features: LoadedFeatureMatrix | None = None,
    model_config: bytes | None = None,
    experiment_config: bytes | None = None,
    feature_descriptor: bytes | None = None,
    processing_manifest: bytes | None = None,
    hidden_truth: bytes | None = None,
) -> ReplayEngine:
    resolved_evaluator = evaluator or HighValueEvaluator(tuple(truth.items()))
    resolved_features = candidate_features or _loaded_features(candidate_ids)
    resolved_processing_manifest = processing_manifest or _processing_manifest_bytes(truth)
    processing_sha256 = hashlib.sha256(resolved_processing_manifest).hexdigest()
    return ReplayEngine(
        run_spec=run_spec,
        experiment_config=experiment_config
        or _experiment_config_bytes(
            processing_manifest_sha256=processing_sha256,
            initial_sample_size=run_spec.initial_sample_size,
            batch_size=run_spec.batch_size,
            budget=run_spec.budget,
        ),
        feature_descriptor=feature_descriptor
        or _feature_descriptor_bytes(
            resolved_features,
            processing_manifest_sha256=processing_sha256,
        ),
        processing_manifest=resolved_processing_manifest,
        hidden_truth=hidden_truth or _hidden_truth_bytes(truth),
        task_id=task_id,
        candidate_ids=candidate_ids,
        oracle=oracle or HiddenOracle(truth),
        candidate_features=resolved_features,
        model_config=model_config or model_config_bytes(run_spec.model_id),
        evaluator=resolved_evaluator,
        high_value_fraction=high_value_fraction,
    )


def _initial_selection(
    candidate_ids: tuple[str, ...], run_spec: RunSpec, *, task_id: str = "synthetic-toy"
) -> tuple[str, ...]:
    ranked = sorted(
        (
            hashlib.sha256(
                json.dumps(
                    [run_spec.protocol_version, task_id, run_spec.seed, candidate_id],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            candidate_id,
        )
        for candidate_id in candidate_ids
    )
    return tuple(candidate_id for _, candidate_id in ranked[: run_spec.initial_sample_size])


def _subset_features(
    features: LoadedFeatureMatrix,
    candidate_ids: tuple[str, ...],
) -> LoadedFeatureMatrix:
    definitions = sequence_feature_definitions()
    rows = [row for row in features.rows if row["candidate_id"] in set(candidate_ids)]
    return load_feature_matrix(
        encode_feature_matrix(rows, feature_definitions=definitions),
        feature_definitions=definitions,
    )


def test_random_replay_reaches_budget_with_byte_identical_trajectory() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _random_run_spec()

    first = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
    ).run()
    second = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
    ).run()

    assert first.events[0].selected_candidate_ids == _initial_selection(candidate_ids, run_spec)
    assert first.distinct_revealed == run_spec.budget
    assert first.run_id == run_spec.run_id
    assert first.canonical_bytes == second.canonical_bytes
    assert first.sha256 == second.sha256
    assert [
        observation.candidate_id for event in first.events for observation in event.observations
    ] == [candidate_id for event in first.events for candidate_id in event.selected_candidate_ids]


def test_reviewed_inputs_project_full_truth_superset_to_candidate_pool() -> None:
    candidate_ids = tuple(f"{index:064x}" for index in range(1, 7))
    truth = {
        **{candidate_id: float(index) for index, candidate_id in enumerate(candidate_ids)},
        f"{999:064x}": 999.0,
    }
    features = _loaded_features(candidate_ids)
    processing_manifest = _processing_manifest_bytes(truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    run_spec = _random_run_spec(candidate_ids, truth=truth)

    trajectory = ReplayEngine.from_reviewed_inputs(
        run_spec=run_spec,
        experiment_config=_experiment_config_bytes(processing_manifest_sha256=processing_sha256),
        feature_descriptor=_feature_descriptor_bytes(
            features,
            processing_manifest_sha256=processing_sha256,
        ),
        processing_manifest=processing_manifest,
        hidden_truth=_hidden_truth_bytes(truth),
        task_id="synthetic-toy",
        candidate_features=features,
        model_config=model_config_bytes(run_spec.model_id),
    ).run()

    assert trajectory.complete is True
    assert {
        observation.candidate_id
        for event in trajectory.events
        for observation in event.observations
    } <= set(candidate_ids)


def test_reviewed_inputs_reject_truth_missing_candidate_before_replay() -> None:
    candidate_ids = tuple(f"{index:064x}" for index in range(1, 7))
    truth = {candidate_id: float(index) for index, candidate_id in enumerate(candidate_ids[:-1])}
    features = _loaded_features(candidate_ids)
    processing_manifest = _processing_manifest_bytes(truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    run_spec = _random_run_spec(candidate_ids, truth=truth)

    with pytest.raises(
        ContractValidationError,
        match="hidden-truth does not cover the candidate pool",
    ):
        ReplayEngine.from_reviewed_inputs(
            run_spec=run_spec,
            experiment_config=_experiment_config_bytes(
                processing_manifest_sha256=processing_sha256
            ),
            feature_descriptor=_feature_descriptor_bytes(
                features,
                processing_manifest_sha256=processing_sha256,
            ),
            processing_manifest=processing_manifest,
            hidden_truth=_hidden_truth_bytes(truth),
            task_id="synthetic-toy",
            candidate_features=features,
            model_config=model_config_bytes(run_spec.model_id),
        )


def test_formal_experiment_config_v2_resolution_remains_run_spec_bound() -> None:
    config_bytes = M3_EXPERIMENT_CONFIG.read_bytes()
    config = load_experiment_config(config_bytes)
    run_spec = RunSpec(
        schema_version="1",
        protocol_version="m3-single-fidelity-replay-v1",
        git_commit="f" * 40,
        environment_digest="e" * 64,
        dataset_manifest_sha256=config.value["dataset_manifest_sha256"],
        processing_manifest_sha256=config.value["processing_manifest_sha256"],
        split_sha256=config.value["split_sha256"],
        feature_sha256="d" * 64,
        experiment_config_sha256=config.sha256,
        model_id="ridge-sklearn-v1",
        model_config_sha256=MODEL_CONFIG_SHA256["ridge-sklearn-v1"],
        acquisition_id="random-seeded-sha256-v1",
        acquisition_config_sha256=(
            "24f80499cb48ad6716f86a9c47bf098d47acdb78841bcb7b8926504619983e0f"
        ),
        seed=1729,
        initial_sample_size=16,
        batch_size=8,
        budget=32,
    )

    assert _resolved_experiment_context(run_spec, config_bytes) == ("alpha-amylase", 0.01)


@pytest.mark.parametrize("task_id", ["alpha-amylase", "ired"])
def test_m4_frozen_experiment_config_v2_dispatch_is_direct_and_fail_closed(
    task_id: str,
) -> None:
    config_bytes = (M4_EXPERIMENT_CONFIG_DIRECTORY / f"{task_id}.json").read_bytes()
    config = load_experiment_config(config_bytes)
    run_spec = RunSpec(
        schema_version="1",
        protocol_version="m4-single-fidelity-screening-v1",
        git_commit="f" * 40,
        environment_digest="e" * 64,
        dataset_manifest_sha256=config.value["dataset_manifest_sha256"],
        processing_manifest_sha256=config.value["processing_manifest_sha256"],
        split_sha256=config.value["split_sha256"],
        feature_sha256="d" * 64,
        experiment_config_sha256=config.sha256,
        model_id="ridge-sklearn-v1",
        model_config_sha256=MODEL_CONFIG_SHA256["ridge-sklearn-v1"],
        acquisition_id="ucb-bootstrap-std-beta1-v1",
        acquisition_config_sha256=(
            "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d"
        ),
        seed=1729,
        initial_sample_size=32,
        batch_size=16,
        budget=128,
    )

    assert config.schema_version == "2"
    assert _resolved_experiment_context(run_spec, config_bytes) == (task_id, 0.01)
    with pytest.raises(ContractValidationError, match="frozen profile"):
        _resolved_experiment_context(replace(run_spec, seed=1739), config_bytes)


def test_recovery_from_complete_boundary_matches_uninterrupted_bytes() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _random_run_spec()

    interrupted = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
    ).run(stop_after_round=0)
    recovered = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
    ).run(checkpoint=interrupted.latest_checkpoint_bytes)
    uninterrupted = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
    ).run()

    assert interrupted.complete is False
    assert recovered.complete is True
    assert recovered.canonical_bytes == uninterrupted.canonical_bytes
    assert recovered.sha256 == uninterrupted.sha256


def test_greedy_replay_ranks_exact_cover_predictions_without_truth_access() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    features = _loaded_features(candidate_ids)
    run_spec = replace(
        _random_run_spec(),
        acquisition_id="greedy-predictive-mean-v1",
        acquisition_config_sha256=(
            "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"
        ),
        model_config_sha256=MODEL_CONFIG_SHA256["ridge-sklearn-v1"],
    )
    initial = _initial_selection(candidate_ids, run_spec)
    remaining = tuple(candidate_id for candidate_id in candidate_ids if candidate_id not in initial)
    fitted = fit_regressor(
        model_config_bytes(run_spec.model_id),
        _subset_features(features, tuple(sorted(initial))),
        [
            DevelopmentObservation(candidate_id, truth[candidate_id])
            for candidate_id in sorted(initial)
        ],
        seed=run_spec.seed,
    )
    predictions = predict_regressor(fitted, _subset_features(features, tuple(sorted(remaining))))
    expected = tuple(
        candidate_id
        for candidate_id, _ in sorted(predictions, key=lambda item: (-item[1], item[0]))[
            : run_spec.batch_size
        ]
    )

    trajectory = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
        candidate_features=features,
        model_config=model_config_bytes(run_spec.model_id),
    ).run()

    assert trajectory.events[1].selected_candidate_ids == expected


def test_bootstrap_draws_exact_match_frozen_golden_vector() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    golden = fixture["uncertainty_bootstrap_golden_vector"]

    draws = bootstrap_draws(
        protocol_id=fixture["protocol_id"],
        run_seed=golden["run_seed"],
        task_id=fixture["task_id"],
        model_id=golden["model_id"],
        round_index=golden["round"],
        observation_count=golden["observation_row_count"],
    )

    assert [
        {
            "indices": list(draw.indices),
            "member_index": draw.member_index,
            "member_seed_uint64": draw.member_seed_uint64,
        }
        for draw in draws
    ] == golden["members"]


@pytest.mark.parametrize(
    "model_id",
    ["ridge-sklearn-v1", "random-forest-sklearn-v1", "xgboost-cpu-v1"],
)
def test_uncertainty_replay_is_deterministic_for_all_frozen_models(model_id: str) -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    features = _loaded_features(candidate_ids)
    run_spec = replace(
        _random_run_spec(),
        model_id=model_id,
        model_config_sha256=MODEL_CONFIG_SHA256[model_id],
        acquisition_id="uncertainty-bootstrap-std-v1",
        acquisition_config_sha256=(
            "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6"
        ),
    )
    initial = _initial_selection(candidate_ids, run_spec)
    observations = tuple(
        Observation(candidate_id=candidate_id, label=truth[candidate_id])
        for candidate_id in initial
    )
    remaining = tuple(candidate_id for candidate_id in candidate_ids if candidate_id not in initial)
    first_scores = bootstrap_prediction_std(
        run_spec=run_spec,
        task_id="alpha-amylase",
        candidate_features=features,
        model_config=model_config_bytes(model_id),
        observations=observations,
        candidate_ids=remaining,
        round_index=1,
    )
    second_scores = bootstrap_prediction_std(
        run_spec=run_spec,
        task_id="alpha-amylase",
        candidate_features=features,
        model_config=model_config_bytes(model_id),
        observations=observations,
        candidate_ids=remaining,
        round_index=1,
    )
    expected = tuple(
        candidate_id
        for candidate_id, _ in sorted(first_scores, key=lambda item: (-item[1], item[0]))[
            : run_spec.batch_size
        ]
    )
    trajectory = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
        candidate_features=features,
        model_config=model_config_bytes(model_id),
    ).run()

    assert first_scores == second_scores
    assert {candidate_id for candidate_id, _ in first_scores} == set(remaining)
    assert all(math.isfinite(score) and score >= 0 for _, score in first_scores)
    assert trajectory.events[1].selected_candidate_ids == expected


def test_m4_ucb_replay_is_deterministic_recoverable_and_strictly_versioned() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    processing_sha256 = hashlib.sha256(_processing_manifest_bytes(truth)).hexdigest()
    experiment_config = _m4_experiment_config_bytes(processing_manifest_sha256=processing_sha256)
    run_spec = _m4_ucb_run_spec(candidate_ids, truth=truth)
    features = _loaded_features(candidate_ids)

    def make_engine() -> ReplayEngine:
        return _engine(
            run_spec=run_spec,
            candidate_ids=candidate_ids,
            truth=truth,
            candidate_features=features,
            model_config=m4_model_config_bytes(run_spec.model_id),
            experiment_config=experiment_config,
        )

    first = make_engine().run()
    second = make_engine().run()
    interrupted = make_engine().run(stop_after_round=0)
    recovered = make_engine().run(checkpoint=interrupted.latest_checkpoint_bytes)

    assert first.canonical_bytes == second.canonical_bytes == recovered.canonical_bytes
    assert first.canonical_bytes.startswith(b'{\n  "events"')
    assert b'"schema_version": "m4-replay-trajectory-v1"' in first.canonical_bytes
    assert b'"schema_version": "m4-replay-checkpoint-v1"' in first.checkpoint_bytes(0)
    assert load_replay_trajectory(first.canonical_bytes) == first
    drifted = json.loads(first.canonical_bytes)
    drifted["schema_version"] = "m3-replay-trajectory-v1"
    with pytest.raises(ContractValidationError, match="schema version"):
        load_replay_trajectory(canonical_json_bytes(drifted))


def test_m4_final_sensitivity_metrics_are_frozen_aggregate_only() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    processing_sha256 = hashlib.sha256(_processing_manifest_bytes(truth)).hexdigest()
    run_spec = _m4_ucb_run_spec(candidate_ids, truth=truth)

    engine = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
        candidate_features=_loaded_features(candidate_ids),
        model_config=m4_model_config_bytes(run_spec.model_id),
        experiment_config=_m4_experiment_config_bytes(processing_manifest_sha256=processing_sha256),
    )
    with pytest.raises(ContractValidationError, match="complete run"):
        engine.final_m4_definition_metrics()
    trajectory = engine.run()
    metrics = engine.final_m4_definition_metrics()
    revealed = [
        candidate_id for event in trajectory.events for candidate_id in event.selected_candidate_ids
    ]
    evaluator = HighValueEvaluator(tuple(truth.items()))

    assert tuple(definition_id for definition_id, _ in metrics) == (
        "upper-tail-1pct",
        "upper-tail-0.5pct",
        "upper-tail-2pct",
    )
    for (definition_id, observed), fraction in zip(
        metrics,
        (0.01, 0.005, 0.02),
        strict=True,
    ):
        assert definition_id.startswith("upper-tail-")
        assert observed == evaluator.sequential_metrics(
            revealed,
            fraction=fraction,
            success_budget=run_spec.budget,
        )
    assert "high_value_membership" not in repr(metrics)


def test_m4_ucb_score_is_exact_mean_plus_population_std() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    features = _loaded_features(candidate_ids)
    run_spec = _m4_ucb_run_spec(candidate_ids, truth=truth)
    initial = _initial_selection(candidate_ids, run_spec)
    remaining = tuple(sorted(set(candidate_ids) - set(initial)))
    observations = tuple(
        Observation(candidate_id=candidate_id, label=truth[candidate_id])
        for candidate_id in initial
    )
    model_config = m4_model_config_bytes(run_spec.model_id)
    fitted = fit_m4_regressor(
        model_config,
        _subset_features(features, tuple(sorted(initial))),
        [
            DevelopmentObservation(candidate_id=candidate_id, observation=truth[candidate_id])
            for candidate_id in sorted(initial)
        ],
        seed=run_spec.seed,
    )
    means = dict(predict_regressor(fitted, _subset_features(features, remaining)))
    standard_deviations = dict(
        bootstrap_prediction_std(
            run_spec=run_spec,
            task_id="synthetic-toy",
            candidate_features=features,
            model_config=model_config,
            observations=observations,
            candidate_ids=remaining,
            round_index=1,
        )
    )

    scores = ucb_prediction_scores(
        run_spec=run_spec,
        task_id="synthetic-toy",
        candidate_features=features,
        model_config=model_config,
        observations=observations,
        candidate_ids=remaining,
        round_index=1,
    )

    assert tuple(candidate_id for candidate_id, _ in scores) == remaining
    assert dict(scores) == {
        candidate_id: means[candidate_id] + standard_deviations[candidate_id]
        for candidate_id in remaining
    }


def test_m4_ucb_ties_use_candidate_id_ascending_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _m4_ucb_run_spec(candidate_ids, truth=truth)
    processing_sha256 = hashlib.sha256(_processing_manifest_bytes(truth)).hexdigest()

    def tied_scores(**kwargs: object) -> tuple[tuple[str, float], ...]:
        ids = cast(tuple[str, ...], kwargs["candidate_ids"])
        return tuple((candidate_id, 1.0) for candidate_id in reversed(ids))

    monkeypatch.setattr(replay_engine_module, "ucb_prediction_scores", tied_scores)
    trajectory = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
        model_config=m4_model_config_bytes(run_spec.model_id),
        experiment_config=_m4_experiment_config_bytes(processing_manifest_sha256=processing_sha256),
    ).run()
    remaining = sorted(set(candidate_ids) - set(trajectory.events[0].selected_candidate_ids))

    assert trajectory.events[1].selected_candidate_ids == tuple(remaining[: run_spec.batch_size])


def test_replay_reuses_sequential_evaluator_without_persisting_private_membership() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]

    evaluator = HighValueEvaluator(tuple(truth.items()))
    trajectory = _engine(
        run_spec=_random_run_spec(),
        candidate_ids=candidate_ids,
        truth=truth,
        evaluator=evaluator,
    ).run()

    revealed: list[str] = []
    for event in trajectory.events:
        revealed.extend(event.selected_candidate_ids)
        expected = evaluator.sequential_metrics(revealed, fraction=0.01, success_budget=4)
        assert event.metrics is not None
        assert event.metrics == expected

    persisted = trajectory.canonical_bytes.decode("utf-8")
    assert "high_value_candidate_ids" not in persisted
    assert "high_value_membership" not in persisted
    unrevealed = set(candidate_ids) - {
        observation.candidate_id
        for event in trajectory.events
        for observation in event.observations
    }
    assert all(candidate_id not in persisted for candidate_id in unrevealed)


def test_canonical_trajectory_loader_validates_identity_and_budget_invariants() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    truth = fixture["synthetic_oracle"]["labels"]
    trajectory = _engine(
        run_spec=_random_run_spec(),
        candidate_ids=tuple(fixture["candidate_ids"]),
        truth=truth,
    ).run()

    loaded = load_replay_trajectory(trajectory.canonical_bytes)

    assert loaded.canonical_bytes == trajectory.canonical_bytes
    assert loaded.sha256 == trajectory.sha256
    drifted = json.loads(trajectory.canonical_bytes)
    drifted["events"][1]["selected_candidate_ids"][0] = drifted["events"][0][
        "selected_candidate_ids"
    ][0]
    with pytest.raises(ContractValidationError, match="repeat|observation"):
        load_replay_trajectory(canonical_json_bytes(drifted))
    with pytest.raises(ContractValidationError, match="canonical"):
        load_replay_trajectory(trajectory.canonical_bytes + b"\n")


def test_trajectory_binds_each_checkpoint_and_metric_artifact_hash() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    truth = fixture["synthetic_oracle"]["labels"]
    trajectory = _engine(
        run_spec=_random_run_spec(),
        candidate_ids=tuple(fixture["candidate_ids"]),
        truth=truth,
    ).run()

    for round_index, event in enumerate(trajectory.events):
        checkpoint = trajectory.checkpoint_bytes(round_index)
        metrics = trajectory.metric_bytes(round_index)
        assert hashlib.sha256(checkpoint).hexdigest() == event.checkpoint_sha256
        assert metrics is not None
        assert hashlib.sha256(metrics).hexdigest() == event.metric_sha256


def test_publication_is_immutable_read_back_and_success_marker_last(tmp_path: Path) -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    truth = fixture["synthetic_oracle"]["labels"]
    trajectory = _engine(
        run_spec=_random_run_spec(),
        candidate_ids=tuple(fixture["candidate_ids"]),
        truth=truth,
    ).run()

    class RecordingStore:
        def __init__(self, root: Path) -> None:
            self.delegate = LocalArtifactStore(root)
            self.writes: list[str] = []

        def put_bytes(
            self,
            key: str,
            data: bytes,
            *,
            overwrite: bool = False,
        ) -> ArtifactMetadata:
            metadata = self.delegate.put_bytes(key, data, overwrite=overwrite)
            self.writes.append(key)
            return metadata

        def get_bytes(self, key: str) -> bytes:
            return self.delegate.get_bytes(key)

        def exists(self, key: str) -> bool:
            return self.delegate.exists(key)

    store = RecordingStore(tmp_path / "complete")
    publication = publish_replay_attempt(
        trajectory,
        store=store,
        attempt_id="attempt-001",
        started_at="2026-08-11T00:00:00Z",
        finished_at="2026-08-11T00:00:01Z",
        executed_git_commit=trajectory.run_spec.git_commit,
        executed_environment_digest=trajectory.run_spec.environment_digest,
        runtime_version="python-test",
    )

    assert store.writes[-1] == publication.marker_key
    assert publication.marker_key.endswith("/_SUCCESS")
    assert is_replay_attempt_eligible(store, publication.attempt_key) is True
    attempt = load_run_attempt_v1(store.get_bytes(publication.attempt_key))
    assert attempt.value["status"] == "succeeded"
    assert attempt.value["run_id"] == trajectory.run_id
    assert attempt.value["executed_git_commit"] == trajectory.run_spec.git_commit
    assert trajectory.run_spec.git_commit == "f" * 40
    assert load_replay_trajectory(trajectory.canonical_bytes).run_id == trajectory.run_id
    assert {item["sha256"] for item in attempt.value["outputs"]} >= {
        event.checkpoint_sha256 for event in trajectory.events
    }
    with pytest.raises(FileExistsError):
        publish_replay_attempt(
            trajectory,
            store=store,
            attempt_id="attempt-001",
            started_at="2026-08-11T00:00:00Z",
            finished_at="2026-08-11T00:00:01Z",
            executed_git_commit=trajectory.run_spec.git_commit,
            executed_environment_digest=trajectory.run_spec.environment_digest,
            runtime_version="python-test",
        )

    incomplete = LocalArtifactStore(tmp_path / "incomplete")
    for output in attempt.value["outputs"]:
        incomplete.put_bytes(output["object_key"], store.get_bytes(output["object_key"]))
    incomplete.put_bytes(publication.attempt_key, store.get_bytes(publication.attempt_key))
    assert is_replay_attempt_eligible(incomplete, publication.attempt_key) is False

    extra_output = LocalArtifactStore(tmp_path / "extra-output")
    for output in attempt.value["outputs"]:
        extra_output.put_bytes(output["object_key"], store.get_bytes(output["object_key"]))
    extra_key = publication.attempt_key.removesuffix("run-attempt.json") + "extra.json"
    extra_payload = b"{}\n"
    extra_output.put_bytes(extra_key, extra_payload)
    drifted_attempt = json.loads(store.get_bytes(publication.attempt_key))
    drifted_attempt["outputs"].append(
        {
            "object_key": extra_key,
            "sha256": hashlib.sha256(extra_payload).hexdigest(),
            "size_bytes": len(extra_payload),
        }
    )
    extra_output.put_bytes(publication.attempt_key, canonical_json_bytes(drifted_attempt))
    extra_output.put_bytes(publication.marker_key, b"")
    assert is_replay_attempt_eligible(extra_output, publication.attempt_key) is False


def test_run_attempt_records_actual_runtime_provenance_beside_frozen_runspec(
    tmp_path: Path,
) -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    trajectory = _engine(
        run_spec=_random_run_spec(),
        candidate_ids=tuple(fixture["candidate_ids"]),
        truth=fixture["synthetic_oracle"]["labels"],
    ).run()
    store = LocalArtifactStore(tmp_path / "runtime-provenance")

    publication = publish_replay_attempt(
        trajectory,
        store=store,
        attempt_id="attempt-001",
        started_at="2026-08-13T00:00:00Z",
        finished_at="2026-08-13T00:00:01Z",
        executed_git_commit="a" * 40,
        executed_environment_digest="b" * 64,
        runtime_version="python-test",
    )

    attempt = load_run_attempt_v1(store.get_bytes(publication.attempt_key))
    assert trajectory.run_spec.git_commit == "f" * 40
    assert attempt.value["executed_git_commit"] == "a" * 40
    assert attempt.value["executed_environment_digest"] == "b" * 64
    assert is_replay_attempt_eligible(store, publication.attempt_key) is True


def test_formal_publication_records_compute_server_provenance(tmp_path: Path) -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    trajectory = _engine(
        run_spec=_random_run_spec(),
        candidate_ids=tuple(fixture["candidate_ids"]),
        truth=fixture["synthetic_oracle"]["labels"],
    ).run()
    store = LocalArtifactStore(tmp_path / "compute-server-provenance")

    publication = publish_replay_attempt(
        trajectory,
        store=store,
        attempt_id="attempt-001",
        started_at="2026-08-13T00:00:00Z",
        finished_at="2026-08-13T00:00:01Z",
        executed_git_commit="a" * 40,
        executed_environment_digest="b" * 64,
        runtime_version="python-test",
        executor_type="compute_server",
        region="ap-beijing",
    )

    attempt = load_run_attempt_v1(store.get_bytes(publication.attempt_key))
    assert attempt.value["compute"] == {
        "executor_type": "compute_server",
        "region": "ap-beijing",
        "runtime_version": "python-test",
    }


def test_publication_can_be_bound_to_one_reviewed_object_prefix(tmp_path: Path) -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    trajectory = _engine(
        run_spec=_random_run_spec(),
        candidate_ids=tuple(fixture["candidate_ids"]),
        truth=fixture["synthetic_oracle"]["labels"],
    ).run()
    store = LocalArtifactStore(tmp_path / "prefixed")

    publication = publish_replay_attempt(
        trajectory,
        store=store,
        attempt_id="attempt-001",
        started_at="2026-08-13T00:00:00Z",
        finished_at="2026-08-13T00:00:01Z",
        executed_git_commit=trajectory.run_spec.git_commit,
        executed_environment_digest=trajectory.run_spec.environment_digest,
        runtime_version="python-test",
        key_prefix="m3/issue-68/reviewed/",
    )

    assert publication.attempt_key.startswith("m3/issue-68/reviewed/runs/")
    assert publication.marker_key.startswith("m3/issue-68/reviewed/runs/")
    assert is_replay_attempt_eligible(store, publication.attempt_key) is True


def test_protocol_sized_random_replay_has_exact_16_24_32_boundaries() -> None:
    candidate_ids = tuple(f"{index:064x}" for index in range(1, 41))
    truth = {candidate_id: float(index) for index, candidate_id in enumerate(candidate_ids)}
    run_spec = _random_run_spec(
        candidate_ids,
        truth=truth,
        initial_sample_size=16,
        batch_size=8,
        budget=32,
    )

    trajectory = _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
    ).run()

    assert [event.round for event in trajectory.events] == [0, 1, 2]
    assert [event.distinct_revealed for event in trajectory.events] == [16, 24, 32]
    selected = [
        candidate_id for event in trajectory.events for candidate_id in event.selected_candidate_ids
    ]
    assert len(selected) == len(set(selected)) == run_spec.budget


def test_only_replay_engine_calls_hidden_oracle_reveal() -> None:
    call_sites: list[Path] = []
    for path in (ROOT / "src/pcdb").rglob("*.py"):
        tree = ast.parse(path.read_text())
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "reveal"
            for node in ast.walk(tree)
        ):
            call_sites.append(path.relative_to(ROOT))

    # 两个回放引擎拥有揭示权限；策略、模型和其他模块仍禁止调用。
    assert sorted(call_sites) == sorted(
        [Path("src/pcdb/replay/engine.py"), Path("src/pcdb/replay/_m6_engine.py")]
    )


def test_uncertainty_member_failure_has_no_fallback() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    features = _loaded_features(candidate_ids)
    run_spec = replace(
        _random_run_spec(),
        model_config_sha256=MODEL_CONFIG_SHA256["ridge-sklearn-v1"],
        acquisition_id="uncertainty-bootstrap-std-v1",
        acquisition_config_sha256=(
            "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6"
        ),
    )
    initial = _initial_selection(candidate_ids, run_spec)
    remaining = tuple(candidate_id for candidate_id in candidate_ids if candidate_id not in initial)
    feature_id = features.feature_ids[0]
    drifted_rows = []
    for row in features.rows:
        drifted = dict(row)
        if row["candidate_id"] in set(initial):
            drifted[feature_id] = math.nan
        drifted_rows.append(drifted)
    drifted_features = LoadedFeatureMatrix(
        feature_ids=features.feature_ids,
        candidate_ids=features.candidate_ids,
        rows=tuple(drifted_rows),
        canonical_bytes=b"",
        sha256="",
    )

    with pytest.raises(ContractValidationError, match="finite"):
        bootstrap_prediction_std(
            run_spec=run_spec,
            task_id=fixture["task_id"],
            candidate_features=drifted_features,
            model_config=model_config_bytes(run_spec.model_id),
            observations=tuple(
                Observation(candidate_id=candidate_id, label=truth[candidate_id])
                for candidate_id in initial
            ),
            candidate_ids=remaining,
            round_index=1,
        )


def test_invalid_pool_or_feature_exact_cover_fails_before_oracle_reveal() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    oracle = HiddenOracle(truth)

    with pytest.raises(ContractValidationError, match="duplicate|distinct"):
        _engine(
            run_spec=_random_run_spec(),
            candidate_ids=(*candidate_ids[:-1], candidate_ids[0]),
            truth=truth,
            oracle=oracle,
        )
    assert oracle.observations == ()

    greedy_spec = replace(
        _random_run_spec(),
        model_config_sha256=MODEL_CONFIG_SHA256["ridge-sklearn-v1"],
        acquisition_id="greedy-predictive-mean-v1",
        acquisition_config_sha256=(
            "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"
        ),
    )
    with pytest.raises(
        ContractValidationError,
        match="feature bundle descriptor SHA-256|feature identity|exact-cover",
    ):
        _engine(
            run_spec=greedy_spec,
            candidate_ids=candidate_ids,
            truth=truth,
            oracle=oracle,
            candidate_features=_subset_features(
                _loaded_features(candidate_ids),
                candidate_ids[:-1],
            ),
        )
    assert oracle.observations == ()


def test_replay_context_rejects_task_or_primary_definition_drift_before_reveal() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _random_run_spec(candidate_ids)
    features = _loaded_features(candidate_ids)

    for task_id, fraction, message in (
        ("different-task", 0.01, "task"),
        ("synthetic-toy", 1 / 3, "primary high-value"),
    ):
        oracle = HiddenOracle(truth)
        with pytest.raises(ContractValidationError, match=message):
            _engine(
                run_spec=run_spec,
                task_id=task_id,
                candidate_ids=candidate_ids,
                truth=truth,
                oracle=oracle,
                candidate_features=features,
                high_value_fraction=fraction,
            )
        assert oracle.observations == ()


def test_replay_context_rejects_candidate_feature_identity_drift_before_reveal() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _random_run_spec(candidate_ids)
    processing_manifest = _processing_manifest_bytes(truth)
    descriptor = _feature_descriptor_bytes(
        _loaded_features(candidate_ids),
        processing_manifest_sha256=hashlib.sha256(processing_manifest).hexdigest(),
    )
    assert hashlib.sha256(descriptor).hexdigest() == run_spec.feature_sha256
    _engine(
        run_spec=run_spec,
        candidate_ids=candidate_ids,
        truth=truth,
        feature_descriptor=descriptor,
    )
    drifted_features = _loaded_features(tuple(reversed(candidate_ids)))
    oracle = HiddenOracle(truth)

    with pytest.raises(
        ContractValidationError, match="candidate feature member differs from descriptor"
    ):
        _engine(
            run_spec=run_spec,
            candidate_ids=candidate_ids,
            truth=truth,
            oracle=oracle,
            candidate_features=drifted_features,
            feature_descriptor=descriptor,
        )

    assert oracle.observations == ()

    substituted_descriptor = bytearray(descriptor)
    substituted_descriptor[-2] = ord(" ")
    descriptor_oracle = HiddenOracle(truth)
    with pytest.raises(ContractValidationError, match="canonical|SHA-256|strict JSON"):
        _engine(
            run_spec=run_spec,
            candidate_ids=candidate_ids,
            truth=truth,
            oracle=descriptor_oracle,
            feature_descriptor=bytes(substituted_descriptor),
        )
    assert descriptor_oracle.observations == ()

    valid_features = _loaded_features(candidate_ids)
    forged_rows = list(valid_features.rows)
    forged_row = dict(forged_rows[0])
    forged_row[valid_features.feature_ids[0]] = float(forged_row[valid_features.feature_ids[0]]) + 1
    forged_rows[0] = forged_row
    forged_features = LoadedFeatureMatrix(
        feature_ids=valid_features.feature_ids,
        candidate_ids=valid_features.candidate_ids,
        rows=tuple(forged_rows),
        canonical_bytes=valid_features.canonical_bytes,
        sha256=valid_features.sha256,
    )
    forged_oracle = HiddenOracle(truth)
    with pytest.raises(ContractValidationError, match="decoded candidate features"):
        _engine(
            run_spec=run_spec,
            candidate_ids=candidate_ids,
            truth=truth,
            oracle=forged_oracle,
            candidate_features=forged_features,
        )
    assert forged_oracle.observations == ()


def test_random_replay_rejects_model_config_drift_before_reveal() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _random_run_spec(candidate_ids)
    oracle = HiddenOracle(truth)

    with pytest.raises(ContractValidationError, match="model config identity"):
        _engine(
            run_spec=run_spec,
            candidate_ids=candidate_ids,
            truth=truth,
            oracle=oracle,
            model_config=model_config_bytes("random-forest-sklearn-v1"),
        )

    assert oracle.observations == ()


def test_replay_context_rejects_config_or_truth_drift_before_reveal() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _random_run_spec(candidate_ids)
    features = _loaded_features(candidate_ids)
    processing_manifest = _processing_manifest_bytes(truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()

    drifted_config_oracle = HiddenOracle(truth)
    with pytest.raises(ContractValidationError, match="Experiment Config identity"):
        _engine(
            run_spec=run_spec,
            candidate_ids=candidate_ids,
            truth=truth,
            oracle=drifted_config_oracle,
            candidate_features=features,
            experiment_config=_experiment_config_bytes(
                processing_manifest_sha256=processing_sha256,
                budget=6,
            ),
        )
    assert drifted_config_oracle.observations == ()

    drifted_truth = dict(truth)
    drifted_truth[candidate_ids[0]] += 1.0
    drifted_truth_oracle = HiddenOracle(truth)
    with pytest.raises(ContractValidationError, match="evaluator truth identity"):
        _engine(
            run_spec=run_spec,
            candidate_ids=candidate_ids,
            truth=truth,
            oracle=drifted_truth_oracle,
            candidate_features=features,
            evaluator=HighValueEvaluator(tuple(drifted_truth.items())),
        )
    assert drifted_truth_oracle.observations == ()


def test_replay_context_rejects_synchronized_truth_drift_before_reveal() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _random_run_spec(candidate_ids)
    drifted_truth = dict(truth)
    drifted_truth[candidate_ids[0]] += 1.0
    oracle = HiddenOracle(drifted_truth)
    processing_manifest = _processing_manifest_bytes(truth)
    hidden_truth = _hidden_truth_bytes(truth)

    with pytest.raises(
        ContractValidationError,
        match="processing manifest identity|hidden oracle truth identity",
    ):
        _engine(
            run_spec=run_spec,
            candidate_ids=candidate_ids,
            truth=drifted_truth,
            oracle=oracle,
            evaluator=HighValueEvaluator(tuple(drifted_truth.items())),
            processing_manifest=processing_manifest,
            hidden_truth=hidden_truth,
        )

    assert oracle.observations == ()


def test_run_identity_changes_with_seed_or_acquisition_config() -> None:
    original = _random_run_spec()

    assert replace(original, seed=1730).run_id != original.run_id
    assert replace(original, acquisition_config_sha256="7" * 64).run_id != original.run_id
    assert (
        replace(
            original,
            acquisition_id="greedy-predictive-mean-v1",
            acquisition_config_sha256=(
                "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"
            ),
        ).run_id
        != original.run_id
    )
