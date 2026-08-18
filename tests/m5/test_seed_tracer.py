"""Issue #85 zero-truth matrix, identity, and frozen verdict checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

import pcdb.replay.engine as replay_engine_module
from pcdb.contracts import ContractValidationError, RunSpec, load_experiment_config
from pcdb.contracts.feature_identity import encode_feature_matrix, load_feature_matrix
from pcdb.m2.features import (
    CandidateSequence,
    build_sequence_feature_rows,
    sequence_feature_definitions,
)
from pcdb.m2.models import m4_model_config_bytes
from pcdb.m5.seed_tracer import (
    EXPLORATION_ID,
    GREEDY_ID,
    INITIAL_SEEDS,
    INTERNAL_SEEDS,
    METRIC_IDS,
    TASKS,
    PairedEffect,
    build_seed_tracer_packet,
    classify_seed_sources,
    load_seed_tracer_packet,
    paired_difference,
)
from pcdb.replay.engine import (
    ReplayEngine,
    _resolved_seed_axes,
    bootstrap_draws,
    bootstrap_prediction_std,
)
from pcdb.replay.oracle import Observation

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_COMMIT = "a" * 40
REVIEWED_TREE = "b" * 40


def _packet() -> tuple[bytes, dict[str, Any]]:
    payload = build_seed_tracer_packet(ROOT, REVIEWED_COMMIT, REVIEWED_TREE)
    return payload, cast(dict[str, Any], json.loads(payload))


def _config_bytes(item: dict[str, Any]) -> bytes:
    return str(item["canonical_json_utf8"]).encode("utf-8")


def _effects(
    *, initial_values: tuple[Decimal, ...], internal_values: tuple[Decimal, ...]
) -> list[PairedEffect]:
    rows: list[PairedEffect] = []
    for task_id in TASKS:
        for metric_id in METRIC_IDS:
            rows.extend(
                PairedEffect(task_id, metric_id, "initial", seed, value)
                for seed, value in zip(INITIAL_SEEDS, initial_values, strict=True)
            )
            rows.extend(
                PairedEffect(task_id, metric_id, "internal", seed, value)
                for seed, value in zip(INTERNAL_SEEDS, internal_values, strict=True)
            )
    return rows


def test_packet_materializes_exact_hash_bound_20_plus_8_universe() -> None:
    payload, packet = _packet()

    assert packet["schema_version"] == "m5-seed-source-tracer-packet-v1"
    assert packet["matrix"]["axis_a_runs"] == 20
    assert packet["matrix"]["axis_b_additional_runs"] == 8
    assert packet["matrix"]["expected_unique_run_specs"] == 28
    assert packet["initial_resource_ledger"]["attempts_started"] == 0
    assert packet["authorization_boundaries"] == {
        "cos_access_authorized": False,
        "packet_review_authorizes_execution": False,
        "paid_compute_authorized": False,
        "run_spec_attempts_authorized": False,
        "server_execution_authorized": False,
        "truth_read_authorized": False,
    }
    assert packet["resource_caps"] == {
        "active_execution_wall_seconds": 900,
        "automatic_retry": 0,
        "delete": 0,
        "network_transfer_bytes": 32 * 1024**2,
        "peak_rss_bytes": 1024**3,
        "replacement_attempts": 0,
        "scratch_high_water_bytes": 16 * 1024**2,
        "unique_run_specs_and_attempts": 28,
    }
    assert packet["resource_accounting"] == {
        "active_execution_wall_scope": "one-28-run-batch-cumulative",
        "guard_after_each_attempt": True,
        "guard_before_each_attempt": True,
        "network_transfer_scope": "one-28-run-batch-cumulative",
        "peak_rss_scope": "maximum-over-all-batch-attempt-processes",
        "scratch_scope": "one-batch-root-high-water",
    }
    runs = cast(list[dict[str, Any]], packet["run_specs"])
    configs = cast(list[dict[str, Any]], packet["experiment_configs"])
    assert len(runs) == len(configs) == 28
    assert len({item["run_id"] for item in runs}) == 28
    assert len({item["experiment_config_sha256"] for item in configs}) == 28

    axis_counts = {"initial": 0, "internal": 0}
    for run_item, config_item in zip(runs, configs, strict=True):
        run_spec = RunSpec(**run_item["run_spec"])
        config_bytes = _config_bytes(config_item)
        loaded = load_experiment_config(config_bytes)
        assert run_item["run_id"] == run_spec.run_id
        assert run_item["run_spec_canonical_json_utf8"] == run_spec.canonical_json()
        assert hashlib.sha256(run_spec.canonical_json().encode()).hexdigest() == run_spec.run_id
        assert run_spec.protocol_version == "m5-seed-source-tracer-v1"
        assert run_spec.experiment_config_sha256 == hashlib.sha256(config_bytes).hexdigest()
        assert loaded.sha256 == run_spec.experiment_config_sha256
        assert loaded.value["task_id"] == run_item["task_id"]
        assert loaded.value["seed_axes"]["initial_seed"] == run_spec.seed
        if run_spec.acquisition_id == GREEDY_ID:
            assert loaded.value["seed_axes"]["internal_seed"] is None
        else:
            assert run_spec.acquisition_id == EXPLORATION_ID
            assert loaded.value["seed_axes"]["internal_seed"] in INTERNAL_SEEDS
        axis_counts[cast(str, run_item["axis"])] += 1
    assert axis_counts == {"initial": 20, "internal": 8}


def test_seed_roles_drive_actual_initial_and_bootstrap_identities_independently() -> None:
    _, packet = _packet()
    runs = cast(list[dict[str, Any]], packet["run_specs"])
    configs = cast(list[dict[str, Any]], packet["experiment_configs"])
    indexed = {
        (
            item["task_id"],
            item["acquisition_id"],
            item["initial_seed"],
            item["internal_seed"],
        ): (RunSpec(**run["run_spec"]), _config_bytes(item))
        for run, item in zip(runs, configs, strict=True)
    }
    candidate_ids = tuple(
        hashlib.sha256(f"candidate:{index}".encode()).hexdigest() for index in range(96)
    )

    def initial_ids(run_spec: RunSpec, config_bytes: bytes) -> tuple[str, ...]:
        engine = object.__new__(ReplayEngine)
        engine._run_spec = run_spec
        engine._task_id = "alpha-amylase"
        engine._candidate_ids = candidate_ids
        engine._seed_axes = _resolved_seed_axes(run_spec, config_bytes)
        return engine._initial_selection()

    fixed_internal = [
        indexed[("alpha-amylase", EXPLORATION_ID, seed, 2719)] for seed in INITIAL_SEEDS
    ]
    initial_sets = [initial_ids(run_spec, config) for run_spec, config in fixed_internal]
    assert len(set(initial_sets)) == 5
    assert {
        _resolved_seed_axes(run_spec, config).internal_seed for run_spec, config in fixed_internal
    } == {2719}

    fixed_initial = [
        indexed[("alpha-amylase", EXPLORATION_ID, 1729, seed)] for seed in INTERNAL_SEEDS
    ]
    assert len({initial_ids(run_spec, config) for run_spec, config in fixed_initial}) == 1
    assert len({run_spec.run_id for run_spec, _ in fixed_initial}) == 5
    draw_identities = {
        tuple(
            draw.member_seed_uint64
            for draw in bootstrap_draws(
                protocol_id=run_spec.protocol_version,
                run_seed=cast(int, _resolved_seed_axes(run_spec, config).internal_seed),
                task_id="alpha-amylase",
                model_id=run_spec.model_id,
                round_index=1,
                observation_count=32,
            )
        )
        for run_spec, config in fixed_initial
    }
    assert len(draw_identities) == 5

    exploration_spec, exploration_config = fixed_initial[0]
    greedy_spec, greedy_config = indexed[("alpha-amylase", GREEDY_ID, 1729, None)]
    assert initial_ids(exploration_spec, exploration_config) == initial_ids(
        greedy_spec, greedy_config
    )


def test_seed_identity_drift_fails_closed_before_selection() -> None:
    _, packet = _packet()
    run_item = cast(list[dict[str, Any]], packet["run_specs"])[0]
    config_item = cast(list[dict[str, Any]], packet["experiment_configs"])[0]
    run_spec = RunSpec(**run_item["run_spec"])
    config = _config_bytes(config_item)

    with pytest.raises(ContractValidationError, match="identity"):
        _resolved_seed_axes(replace(run_spec, experiment_config_sha256="f" * 64), config)
    with pytest.raises(ContractValidationError, match="RunSpec"):
        _resolved_seed_axes(replace(run_spec, seed=run_spec.seed + 1), config)
    with pytest.raises(ContractValidationError, match="protocol"):
        _resolved_seed_axes(
            replace(run_spec, protocol_version="m4-single-fidelity-screening-v1"),
            config,
        )

    value = json.loads(config)
    value["seed_axes"]["internal_seed"] = None
    with pytest.raises(ContractValidationError, match="internal seed"):
        load_experiment_config(json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def test_uncertainty_runtime_consumes_internal_draw_seed_and_frozen_estimator_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, packet = _packet()
    runs = cast(list[dict[str, Any]], packet["run_specs"])
    configs = cast(list[dict[str, Any]], packet["experiment_configs"])
    run_item, config_item = next(
        (run, config)
        for run, config in zip(runs, configs, strict=True)
        if config["task_id"] == "alpha-amylase"
        and config["acquisition_id"] == EXPLORATION_ID
        and config["initial_seed"] == 1729
        and config["internal_seed"] == 2720
    )
    run_spec = RunSpec(**run_item["run_spec"])
    config_bytes = _config_bytes(config_item)
    candidate_ids = tuple(
        hashlib.sha256(f"runtime-candidate:{index}".encode()).hexdigest() for index in range(40)
    )
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    rows = build_sequence_feature_rows(
        [
            CandidateSequence(
                candidate_id=candidate_id,
                sequence=alphabet[index % len(alphabet)]
                + alphabet[(index // len(alphabet)) % len(alphabet)],
            )
            for index, candidate_id in enumerate(candidate_ids)
        ]
    )
    definitions = sequence_feature_definitions()
    features = load_feature_matrix(
        encode_feature_matrix(rows, feature_definitions=definitions),
        feature_definitions=definitions,
    )
    observed_ids = tuple(sorted(features.candidate_ids[:32]))
    remaining_ids = tuple(sorted(set(features.candidate_ids) - set(observed_ids)))
    draw_seeds: list[int] = []
    fit_seeds: list[int] = []
    original_draws = replay_engine_module.bootstrap_draws

    def capture_draws(**kwargs: Any) -> tuple[replay_engine_module.BootstrapDraw, ...]:
        draw_seeds.append(cast(int, kwargs["run_seed"]))
        return original_draws(**kwargs)

    def fake_fit(
        _model_config: bytes,
        _features: Any,
        _observations: Any,
        *,
        sampled_indices: Any,
        seed: int,
    ) -> object:
        assert len(sampled_indices) == 32
        fit_seeds.append(seed)
        return object()

    def fake_predict(_model: object, matrix: Any) -> tuple[tuple[str, float], ...]:
        return tuple((candidate_id, 0.0) for candidate_id in matrix.candidate_ids)

    monkeypatch.setattr(replay_engine_module, "bootstrap_draws", capture_draws)
    monkeypatch.setattr(replay_engine_module, "fit_m4_bootstrap_regressor", fake_fit)
    monkeypatch.setattr(replay_engine_module, "predict_regressor", fake_predict)

    scores = bootstrap_prediction_std(
        run_spec=run_spec,
        task_id="alpha-amylase",
        candidate_features=features,
        model_config=m4_model_config_bytes(run_spec.model_id),
        observations=tuple(
            Observation(candidate_id=candidate_id, label=float(index))
            for index, candidate_id in enumerate(observed_ids)
        ),
        candidate_ids=remaining_ids,
        round_index=1,
        experiment_config=config_bytes,
    )

    assert draw_seeds == [2720]
    assert fit_seeds == [1729] * 5
    assert tuple(candidate_id for candidate_id, _ in scores) == tuple(
        candidate_id
        for candidate_id in features.candidate_ids
        if candidate_id in set(remaining_ids)
    )


def test_packet_loader_rebuilds_exact_bytes_and_rejects_tamper() -> None:
    payload, _ = _packet()
    digest = hashlib.sha256(payload).hexdigest()

    loaded = load_seed_tracer_packet(
        payload,
        expected_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    assert loaded["matrix"]["expected_unique_run_specs"] == 28

    tampered = payload.replace(
        b'"server_execution_authorized": false', b'"server_execution_authorized": true'
    )
    with pytest.raises(ContractValidationError, match="identity"):
        load_seed_tracer_packet(
            tampered,
            expected_sha256=digest,
            repository_root=ROOT,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
        )


def test_metric_direction_and_two_x_inclusive_boundary() -> None:
    assert paired_difference(
        metric_id="high_value_discovery_recall",
        exploration=Decimal("0.6"),
        greedy=Decimal("0.4"),
    ) == Decimal("0.2")
    assert paired_difference(
        metric_id="simple_regret",
        exploration=Decimal("0.2"),
        greedy=Decimal("0.5"),
    ) == Decimal("0.3")

    result = classify_seed_sources(
        _effects(
            initial_values=(
                Decimal("-2"),
                Decimal("-2"),
                Decimal("0"),
                Decimal("2"),
                Decimal("2"),
            ),
            internal_values=(
                Decimal("-1"),
                Decimal("-1"),
                Decimal("0"),
                Decimal("1"),
                Decimal("1"),
            ),
        )
    )

    assert result.verdict == "INITIAL-DOMINATED"
    assert all(item.initial.sample_sd == 2 * item.internal.sample_sd for item in result.comparisons)
    assert all(item.initial_dominant for item in result.comparisons)


def test_three_of_four_boundary_zero_variance_and_incomplete_fail_closed() -> None:
    rows = _effects(
        initial_values=tuple(Decimal(value) for value in (0, 1, 2, 3, 4)),
        internal_values=tuple(Decimal(value) for value in (0, 0, 0, 0, 0)),
    )
    fourth = (TASKS[-1], METRIC_IDS[-1])
    rows = [
        replace(row, difference=Decimal(0)) if (row.task_id, row.metric_id) == fourth else row
        for row in rows
    ]
    result = classify_seed_sources(rows)

    assert result.verdict == "INITIAL-DOMINATED"
    assert sum(item.initial_dominant for item in result.comparisons) == 3
    zero = next(item for item in result.comparisons if (item.task_id, item.metric_id) == fourth)
    assert zero.initial.sample_sd == zero.internal.sample_sd == 0
    assert not zero.initial_dominant and not zero.internal_dominant

    assert classify_seed_sources(rows[:-1]).verdict == "DATA-INCOMPLETE"
    assert classify_seed_sources([*rows, rows[0]]).verdict == "DATA-INCOMPLETE"
    assert (
        classify_seed_sources([replace(rows[0], difference=Decimal("NaN")), *rows[1:]]).verdict
        == "DATA-INCOMPLETE"
    )
