from __future__ import annotations

import math

import pytest

from pcdb.evaluation.high_value import (
    EvaluationInputError,
    HighValueEvaluator,
    LeakageBoundaryError,
    validate_prohibited_consumer_payload,
)


def test_tie_inclusive_definition_summary_does_not_expose_membership() -> None:
    evaluator = HighValueEvaluator(
        [
            ("c6", 7.0),
            ("c2", 9.0),
            ("c4", 8.0),
            ("c1", 10.0),
            ("c5", 8.0),
            ("c3", 8.0),
        ]
    )

    summary = evaluator.definition_summary(fraction=0.5)

    assert summary.nominal_k == 3
    assert summary.actual_high_value_count == 5
    assert summary.boundary_score == 8.0
    assert summary.boundary_tie_count == 3
    assert not hasattr(summary, "candidate_ids")
    assert not hasattr(summary, "membership")


def test_static_metrics_use_exact_coverage_and_average_tie_ranks() -> None:
    evaluator = HighValueEvaluator([("a", 1.0), ("b", 2.0), ("c", 2.0), ("d", 4.0)])

    spearman = evaluator.spearman_rank_correlation([("d", 4.0), ("a", 1.0), ("c", 2.0), ("b", 2.0)])
    recall = evaluator.high_value_recall_at_nominal_k(
        [("a", 0.0), ("b", 0.8), ("c", 0.9), ("d", 0.7)],
        fraction=0.5,
    )

    assert spearman.status == "defined"
    assert spearman.value == pytest.approx(1.0)
    assert recall.status == "defined"
    assert recall.value == pytest.approx(2 / 3)


def test_spearman_undefined_reasons_have_frozen_priority() -> None:
    assert (
        HighValueEvaluator([("a", 1.0)]).spearman_rank_correlation([("a", 1.0)]).undefined_reason
        == "insufficient_observations"
    )
    assert (
        HighValueEvaluator([("a", 1.0), ("b", 1.0)])
        .spearman_rank_correlation([("a", 2.0), ("b", 2.0)])
        .undefined_reason
        == "constant_target_and_prediction"
    )
    assert (
        HighValueEvaluator([("a", 1.0), ("b", 1.0)])
        .spearman_rank_correlation([("a", 1.0), ("b", 2.0)])
        .undefined_reason
        == "constant_target"
    )
    assert (
        HighValueEvaluator([("a", 1.0), ("b", 2.0)])
        .spearman_rank_correlation([("a", 1.0), ("b", 1.0)])
        .undefined_reason
        == "constant_prediction"
    )


def test_sequential_metrics_follow_empty_and_success_rules() -> None:
    evaluator = HighValueEvaluator([("a", 1.0), ("b", 3.0), ("c", 2.0)])

    empty = evaluator.sequential_metrics([], fraction=1 / 3, success_budget=1)
    found = evaluator.sequential_metrics(["b"], fraction=1 / 3, success_budget=1)

    assert empty.best_found_value.status == "undefined"
    assert empty.best_found_value.undefined_reason == "no_revealed_observations"
    assert empty.simple_regret.status == "undefined"
    assert empty.high_value_discovery_recall.value == 0.0
    assert empty.success_at_budget.status == "undefined"
    assert empty.success_at_budget.undefined_reason == "budget_not_reached"
    assert found.best_found_value.value == 3.0
    assert found.simple_regret.value == 0.0
    assert found.high_value_discovery_recall.value == 1.0
    assert found.success_at_budget.value is True


def test_success_at_budget_uses_exact_reveal_prefix() -> None:
    evaluator = HighValueEvaluator([("a", 1.0), ("b", 3.0), ("c", 2.0)])

    result = evaluator.sequential_metrics(
        ["a", "b"],
        fraction=1 / 3,
        success_budget=1,
    )

    assert result.success_at_budget.status == "defined"
    assert result.success_at_budget.value is False


@pytest.mark.parametrize(
    "rows",
    [
        [("a", 1.0), ("a", 2.0)],
        [("a", math.nan)],
        [("", 1.0)],
    ],
)
def test_evaluator_rejects_duplicate_nonfinite_or_empty_candidate(
    rows: list[tuple[str, float]],
) -> None:
    with pytest.raises(EvaluationInputError):
        HighValueEvaluator(rows)


def test_evaluator_rejects_prediction_coverage_drift() -> None:
    evaluator = HighValueEvaluator([("a", 1.0), ("b", 2.0)])

    with pytest.raises(EvaluationInputError, match="exact-cover"):
        evaluator.spearman_rank_correlation([("a", 1.0)])
    with pytest.raises(EvaluationInputError, match="duplicate"):
        evaluator.spearman_rank_correlation([("a", 1.0), ("a", 2.0)])


@pytest.mark.parametrize(
    "payload",
    [
        {"sequence": "AAAA", "target": 1.0},
        {"features": {"boundary_score": 2.0}},
        {"rows": [{"candidate_id": "a", "high_value_membership": True}]},
    ],
)
def test_prohibited_consumers_fail_closed_on_truth_derived_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(LeakageBoundaryError):
        validate_prohibited_consumer_payload(payload, consumer="surrogate-model")


def test_prohibited_consumer_validator_accepts_observable_only_payload() -> None:
    validate_prohibited_consumer_payload(
        {"candidate_id": "a", "features": {"sequence": "AAAA", "length": 4}},
        consumer="candidate-features",
    )
