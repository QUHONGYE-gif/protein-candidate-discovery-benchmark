from __future__ import annotations

from pathlib import Path

import pytest

from pcdb.contracts import load_experiment_config
from pcdb.metrics import (
    HiddenTruthLeakageError,
    HighValueEvaluationError,
    evaluate_sequential_observations,
    evaluate_static_predictions,
    validate_observable_rows,
)

CONFORMANCE_CONFIG = (
    Path(__file__).parents[2] / "configs/experiments/conformance/alpha-amylase-high-value-v2.json"
)


@pytest.mark.parametrize(
    "consumer",
    [
        "candidate-features",
        "surrogate-model",
        "calibration",
        "acquisition-strategy",
    ],
)
def test_prohibited_consumers_reject_hidden_target(consumer: str) -> None:
    rows = [{"candidate_id": "candidate-1", "sequence": "ACD", "target": 1.5}]

    with pytest.raises(HiddenTruthLeakageError, match="target"):
        validate_observable_rows(consumer, rows)


def test_static_evaluator_returns_aggregate_metrics_without_membership() -> None:
    config = load_experiment_config(CONFORMANCE_CONFIG.read_bytes())
    targets = [(f"candidate-{index:04d}", float(index)) for index in range(2262)]
    predictions = list(targets)

    result = evaluate_static_predictions(config, predictions, targets)

    assert result.metrics[0].metric_id == "spearman_rank_correlation"
    assert result.metrics[0].definition_id is None
    assert result.metrics[0].value == pytest.approx(1.0)
    assert [metric.value for metric in result.metrics[1:]] == [1.0, 1.0, 1.0]
    assert all(not hasattr(report, "member_ids") for report in result.high_value_reports)


def test_sequential_evaluator_reports_revealed_success_without_membership() -> None:
    config = load_experiment_config(CONFORMANCE_CONFIG.read_bytes())
    targets = [(f"candidate-{index:04d}", float(index)) for index in range(2262)]
    revealed = [("candidate-2261", 2261.0)]

    result = evaluate_sequential_observations(
        config,
        revealed,
        targets,
        round_index=0,
        budget=1,
    )

    assert [metric.metric_id for metric in result.metrics[:2]] == [
        "best_found_value",
        "simple_regret",
    ]
    assert [metric.value for metric in result.metrics[:2]] == [2261.0, 0.0]
    assert result.metrics[2].metric_id == "high_value_discovery_recall"
    assert result.metrics[2].value == pytest.approx(1 / 23)
    assert result.metrics[5].metric_id == "success_at_budget"
    assert result.metrics[5].value is True
    assert all(not hasattr(report, "member_ids") for report in result.high_value_reports)


def test_sequential_evaluator_marks_unreached_budget_undefined() -> None:
    config = load_experiment_config(CONFORMANCE_CONFIG.read_bytes())
    targets = [(f"candidate-{index:04d}", float(index)) for index in range(2262)]

    result = evaluate_sequential_observations(
        config,
        [],
        targets,
        round_index=0,
        budget=1,
    )

    assert result.metrics[0].undefined_reason == "no_revealed_observations"
    assert result.metrics[1].undefined_reason == "no_revealed_observations"
    assert [metric.value for metric in result.metrics[2:5]] == [0.0, 0.0, 0.0]
    assert all(metric.status == "undefined" for metric in result.metrics[5:])
    assert all(metric.value is None for metric in result.metrics[5:])
    assert all(metric.undefined_reason == "budget_not_reached" for metric in result.metrics[5:])


def test_static_evaluator_rejects_missing_prediction_candidate() -> None:
    config = load_experiment_config(CONFORMANCE_CONFIG.read_bytes())
    targets = [(f"candidate-{index:04d}", float(index)) for index in range(2262)]

    with pytest.raises(HighValueEvaluationError, match="exact-cover"):
        evaluate_static_predictions(config, targets[:-1], targets)


def test_prohibited_consumer_rejects_nested_hidden_membership() -> None:
    rows = [
        {
            "candidate_id": "candidate-1",
            "metadata": {"high_value_membership": True},
        }
    ]

    with pytest.raises(HiddenTruthLeakageError, match="metadata.high_value_membership"):
        validate_observable_rows("candidate-features", rows)
