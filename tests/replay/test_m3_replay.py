from __future__ import annotations

import ast
import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from pcdb.contracts import ContractValidationError, RunSpec
from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.contracts.feature_identity import (
    LoadedFeatureMatrix,
    encode_feature_matrix,
    load_feature_matrix,
)
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.evaluation import HighValueEvaluator
from pcdb.m2.features import (
    CandidateSequence,
    build_sequence_feature_rows,
    sequence_feature_definitions,
)
from pcdb.m2.models import (
    MODEL_CONFIG_SHA256,
    DevelopmentObservation,
    fit_regressor,
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
)
from pcdb.storage import ArtifactMetadata, LocalArtifactStore

ROOT = Path(__file__).parents[2]
WORKED_EXAMPLE = ROOT / "tests/fixtures/m3/issue-66/worked-example.v1.json"


def _random_run_spec() -> RunSpec:
    return RunSpec(
        schema_version="1",
        protocol_version="m3-single-fidelity-replay-v1",
        git_commit="f" * 40,
        environment_digest="e" * 64,
        dataset_manifest_sha256="1" * 64,
        processing_manifest_sha256="2" * 64,
        split_sha256="3" * 64,
        feature_sha256="4" * 64,
        experiment_config_sha256="5" * 64,
        model_id="ridge-sklearn-v1",
        model_config_sha256="6" * 64,
        acquisition_id="random-seeded-sha256-v1",
        acquisition_config_sha256=(
            "24f80499cb48ad6716f86a9c47bf098d47acdb78841bcb7b8926504619983e0f"
        ),
        seed=1729,
        initial_sample_size=2,
        batch_size=2,
        budget=4,
    )


def _loaded_features(candidate_ids: tuple[str, ...]) -> LoadedFeatureMatrix:
    sequences = ("AA", "AC", "AD", "CA", "CC", "CD")
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

    first = ReplayEngine(
        run_spec=run_spec,
        task_id=fixture["task_id"],
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
    ).run()
    second = ReplayEngine(
        run_spec=run_spec,
        task_id=fixture["task_id"],
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
    ).run()

    assert [event.selected_candidate_ids for event in first.events] == [
        tuple(fixture["initial_selection"]["selected_candidate_ids"]),
        tuple(fixture["random_round"]["selected_candidate_ids"]),
    ]
    assert first.distinct_revealed == run_spec.budget
    assert first.run_id == run_spec.run_id
    assert first.canonical_bytes == second.canonical_bytes
    assert first.sha256 == second.sha256
    assert [
        observation.candidate_id for event in first.events for observation in event.observations
    ] == [candidate_id for event in first.events for candidate_id in event.selected_candidate_ids]


def test_recovery_from_complete_boundary_matches_uninterrupted_bytes() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    run_spec = _random_run_spec()

    interrupted = ReplayEngine(
        run_spec=run_spec,
        task_id=fixture["task_id"],
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
        evaluator=HighValueEvaluator(tuple(truth.items())),
        high_value_fraction=1 / 3,
    ).run(stop_after_round=0)
    recovered = ReplayEngine(
        run_spec=run_spec,
        task_id=fixture["task_id"],
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
        evaluator=HighValueEvaluator(tuple(truth.items())),
        high_value_fraction=1 / 3,
    ).run(checkpoint=interrupted.latest_checkpoint_bytes)
    uninterrupted = ReplayEngine(
        run_spec=run_spec,
        task_id=fixture["task_id"],
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
        evaluator=HighValueEvaluator(tuple(truth.items())),
        high_value_fraction=1 / 3,
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
    initial = tuple(fixture["initial_selection"]["selected_candidate_ids"])
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

    trajectory = ReplayEngine(
        run_spec=run_spec,
        task_id=fixture["task_id"],
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
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
    initial = tuple(fixture["initial_selection"]["selected_candidate_ids"])
    observations = tuple(
        Observation(candidate_id=candidate_id, label=truth[candidate_id])
        for candidate_id in initial
    )
    remaining = tuple(candidate_id for candidate_id in candidate_ids if candidate_id not in initial)
    kwargs = {
        "run_spec": run_spec,
        "task_id": fixture["task_id"],
        "candidate_features": features,
        "model_config": model_config_bytes(model_id),
        "observations": observations,
        "candidate_ids": remaining,
        "round_index": 1,
    }

    first_scores = bootstrap_prediction_std(**kwargs)
    second_scores = bootstrap_prediction_std(**kwargs)
    expected = tuple(
        candidate_id
        for candidate_id, _ in sorted(first_scores, key=lambda item: (-item[1], item[0]))[
            : run_spec.batch_size
        ]
    )
    trajectory = ReplayEngine(
        run_spec=run_spec,
        task_id=fixture["task_id"],
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
        candidate_features=features,
        model_config=model_config_bytes(model_id),
    ).run()

    assert first_scores == second_scores
    assert {candidate_id for candidate_id, _ in first_scores} == set(remaining)
    assert all(math.isfinite(score) and score >= 0 for _, score in first_scores)
    assert trajectory.events[1].selected_candidate_ids == expected


def test_replay_reuses_sequential_evaluator_without_persisting_private_membership() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]

    trajectory = ReplayEngine(
        run_spec=_random_run_spec(),
        task_id=fixture["task_id"],
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
        evaluator=HighValueEvaluator(tuple(truth.items())),
        high_value_fraction=1 / 3,
    ).run()

    for event, expected in zip(trajectory.events, fixture["expected_metrics"], strict=True):
        assert event.metrics is not None
        assert event.metrics.best_found_value.value == expected["best_found_value"]
        assert event.metrics.simple_regret.value == pytest.approx(expected["simple_regret"])
        assert (
            event.metrics.high_value_discovery_recall.value
            == expected["high_value_discovery_recall"]
        )
        assert event.metrics.success_at_budget.value == expected["success_at_budget"]["value"]

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
    trajectory = ReplayEngine(
        run_spec=_random_run_spec(),
        task_id=fixture["task_id"],
        candidate_ids=tuple(fixture["candidate_ids"]),
        oracle=HiddenOracle(truth),
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
    trajectory = ReplayEngine(
        run_spec=_random_run_spec(),
        task_id=fixture["task_id"],
        candidate_ids=tuple(fixture["candidate_ids"]),
        oracle=HiddenOracle(truth),
        evaluator=HighValueEvaluator(tuple(truth.items())),
        high_value_fraction=1 / 3,
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
    trajectory = ReplayEngine(
        run_spec=_random_run_spec(),
        task_id=fixture["task_id"],
        candidate_ids=tuple(fixture["candidate_ids"]),
        oracle=HiddenOracle(truth),
        evaluator=HighValueEvaluator(tuple(truth.items())),
        high_value_fraction=1 / 3,
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
    with pytest.raises(ContractValidationError, match="exact-match"):
        publish_replay_attempt(
            trajectory,
            store=store,
            attempt_id="attempt-invalid",
            started_at="2026-08-11T00:00:00Z",
            finished_at="2026-08-11T00:00:01Z",
            executed_git_commit="a" * 40,
            executed_environment_digest="b" * 64,
            runtime_version="python-test",
        )
    assert store.writes == []

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


def test_protocol_sized_random_replay_has_exact_16_24_32_boundaries() -> None:
    candidate_ids = tuple(f"{index:064x}" for index in range(1, 41))
    truth = {candidate_id: float(index) for index, candidate_id in enumerate(candidate_ids)}
    run_spec = replace(
        _random_run_spec(),
        initial_sample_size=16,
        batch_size=8,
        budget=32,
    )

    trajectory = ReplayEngine(
        run_spec=run_spec,
        task_id="synthetic-protocol-sized",
        candidate_ids=candidate_ids,
        oracle=HiddenOracle(truth),
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

    assert call_sites == [Path("src/pcdb/replay/engine.py")]


def test_uncertainty_member_failure_has_no_fallback() -> None:
    fixture = json.loads(WORKED_EXAMPLE.read_bytes())
    candidate_ids = tuple(fixture["candidate_ids"])
    truth = fixture["synthetic_oracle"]["labels"]
    features = _loaded_features(candidate_ids)
    initial = tuple(fixture["initial_selection"]["selected_candidate_ids"])
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
    run_spec = replace(
        _random_run_spec(),
        model_config_sha256=MODEL_CONFIG_SHA256["ridge-sklearn-v1"],
        acquisition_id="uncertainty-bootstrap-std-v1",
        acquisition_config_sha256=(
            "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6"
        ),
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

    with pytest.raises(ContractValidationError, match="distinct"):
        ReplayEngine(
            run_spec=_random_run_spec(),
            task_id=fixture["task_id"],
            candidate_ids=(*candidate_ids[:-1], candidate_ids[0]),
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
    with pytest.raises(ContractValidationError, match="exact-cover"):
        ReplayEngine(
            run_spec=greedy_spec,
            task_id=fixture["task_id"],
            candidate_ids=candidate_ids,
            oracle=oracle,
            candidate_features=_subset_features(
                _loaded_features(candidate_ids),
                candidate_ids[:-1],
            ),
            model_config=model_config_bytes(greedy_spec.model_id),
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
