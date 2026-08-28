"""Formal M5A exact-cohort and candidate-free aggregation checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.m5.formal_reconstruction import (
    binary64_hex,
    build_formal_report,
    load_formal_contract,
    ordinal_spearman,
)
from pcdb.m5.reconstruction_smoke import compact_json_bytes

ROOT = Path(__file__).resolve().parents[2]


def test_formal_contract_freezes_exact_160_and_caps() -> None:
    path = ROOT / "configs/experiments/m5/reconstruction-formal-v1.json"
    value = load_formal_contract(path.read_bytes())
    assert value["cohort"]["successful_attempt_inventory"] == 344
    assert value["cohort"]["selected_trajectories"] == 160
    assert value["cohort"]["excluded_successful_attempts"] == 184
    assert value["resource_caps"]["attempt_wall_seconds"] == 18_000

    drifted = json.loads(path.read_bytes())
    drifted["cohort"]["selected_trajectories"] = 159
    with pytest.raises(ContractValidationError, match="cohort contract drifted"):
        load_formal_contract(json.dumps(drifted).encode())


def test_ordinal_spearman_is_exact_for_permutations() -> None:
    assert ordinal_spearman(("a", "b", "c"), ("a", "b", "c")) == 1.0
    assert ordinal_spearman(("a", "b", "c"), ("c", "b", "a")) == -1.0
    with pytest.raises(ContractValidationError, match="permutations"):
        ordinal_spearman(("a", "b"), ("a", "c"))


def _metric(value: float) -> dict[str, object]:
    return {"status": "defined", "value_binary64": binary64_hex(value)}


def _summaries(*, mismatch: bool = False) -> list[bytes]:
    payloads: list[bytes] = []
    for task_index, task_id in enumerate(("alpha-amylase", "ired")):
        for model_index, model_id in enumerate(
            ("random-forest-sklearn-v1", "xgboost-cpu-v1")
        ):
            for initial_sample_size in (32, 64):
                for seed in range(1729, 1739):
                    for policy in ("exploration", "greedy"):
                        rounds = []
                        for round_index in range(1, 7 if initial_sample_size == 32 else 5):
                            value = float(
                                task_index
                                + model_index
                                + round_index
                                + seed
                                - 1729
                            )
                            rounds.append(
                                {
                                    "actual_policy_exact_match": not (
                                        mismatch
                                        and task_index == 0
                                        and model_index == 0
                                        and initial_sample_size == 32
                                        and seed == 1729
                                        and round_index == 1
                                        and policy == "exploration"
                                    ),
                                    "checkpoint_metrics": {
                                        "best_found_value": _metric(
                                            value + (0.25 if policy == "exploration" else 0.0)
                                        ),
                                        "high_value_discovery_recall": _metric(value / 100),
                                        "simple_regret": _metric(-value),
                                        "success_at_budget": _metric(
                                            float(
                                                round_index
                                                == (6 if initial_sample_size == 32 else 4)
                                            )
                                        ),
                                    },
                                    "divergence_binary64": binary64_hex(round_index / 10),
                                    "mean_acquisition_spearman_binary64": binary64_hex(
                                        1.0 if policy == "greedy" else 0.5
                                    ),
                                    "remaining_count": 2000 - initial_sample_size,
                                    "revealed_before": initial_sample_size
                                    + (round_index - 1) * 16,
                                    "round": round_index,
                                    "selected_mean_ranks": list(range(1, 17)),
                                    "selected_uncertainty_ranks": list(range(16, 0, -1)),
                                    "top_k_jaccard_binary64": binary64_hex(
                                        1.0 if policy == "greedy" else 0.5
                                    ),
                                    "top_k_overlap": 16 if policy == "greedy" else 8,
                                }
                            )
                        payloads.append(
                            compact_json_bytes(
                                {
                                    "initial_sample_size": initial_sample_size,
                                    "model_id": model_id,
                                    "policy": policy,
                                    "rounds": rounds,
                                    "schema_version": "m5a-formal-trajectory-summary-v1",
                                    "seed": seed,
                                    "task_id": task_id,
                                }
                            )
                        )
    return payloads


def test_formal_report_exact_covers_800_states_and_leaks_no_candidates_or_labels() -> None:
    report = build_formal_report(
        _summaries(),
        inventory_sha256="a" * 64,
        analysis_commit="b" * 40,
        analysis_tree="c" * 40,
        environment_digest="d" * 64,
    )
    value = json.loads(report)
    assert value["cohort"] == {
        "decision_states": 800,
        "excluded_successful_attempts": 184,
        "selected_trajectories": 160,
        "successful_attempt_inventory": 344,
        "trajectory_pairs": 80,
    }
    assert value["summaries"]["overall"][0]["actual_exact_matches"] == 800
    assert b"candidate_id" not in report
    assert b'"value":' not in report
    assert report == build_formal_report(
        _summaries(),
        inventory_sha256="a" * 64,
        analysis_commit="b" * 40,
        analysis_tree="c" * 40,
        environment_digest="d" * 64,
    )


def test_formal_report_stops_on_any_actual_selection_mismatch() -> None:
    with pytest.raises(ContractValidationError, match="actual selection mismatch"):
        build_formal_report(
            _summaries(mismatch=True),
            inventory_sha256="a" * 64,
            analysis_commit="b" * 40,
            analysis_tree="c" * 40,
            environment_digest="d" * 64,
        )
