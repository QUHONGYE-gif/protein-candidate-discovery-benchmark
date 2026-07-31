"""Synthetic-safe high-value evaluator boundaries."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pcdb.contracts.experiment import LoadedExperimentConfig


class HiddenTruthLeakageError(ValueError):
    """Raised when evaluator-owned hidden state reaches a prohibited consumer."""


_PROHIBITED_CONSUMERS = {
    "candidate-features",
    "surrogate-model",
    "calibration",
    "acquisition-strategy",
}
_HIDDEN_FIELDS = {
    "boundary_score",
    "high_value",
    "high_value_membership",
    "high_value_rank",
    "is_high_value",
    "oracle_max",
    "target",
    "target_rank",
    "unrevealed_target",
}


def _hidden_field_paths(value: object, *, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            item_path = f"{path}.{key}" if path else key
            if key in _HIDDEN_FIELDS:
                found.append(item_path)
            found.extend(_hidden_field_paths(item, path=item_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            found.extend(_hidden_field_paths(item, path=f"{path}[{index}]"))
    return found


class HighValueEvaluationError(ValueError):
    """Raised when evaluator inputs do not satisfy the frozen universe contract."""


@dataclass(frozen=True, slots=True)
class HighValueReport:
    """Truth-derived definition summary without candidate-level membership."""

    definition_id: str
    nominal_k: int
    actual_high_value_count: int
    boundary_score: float
    boundary_tie_count: int


@dataclass(frozen=True, slots=True)
class MetricValue:
    """One aggregate metric value safe to expose outside the evaluator boundary."""

    metric_id: str
    definition_id: str | None
    direction: str
    status: str
    value: float | bool | None
    undefined_reason: str | None


@dataclass(frozen=True, slots=True)
class StaticEvaluation:
    """Synthetic-safe M2 static evaluation output."""

    task_id: str
    high_value_reports: tuple[HighValueReport, ...]
    metrics: tuple[MetricValue, ...]


@dataclass(frozen=True, slots=True)
class SequentialEvaluation:
    """Synthetic-safe M3-M5 evaluation output at one checkpoint."""

    task_id: str
    round_index: int
    budget: int
    high_value_reports: tuple[HighValueReport, ...]
    metrics: tuple[MetricValue, ...]


def validate_observable_rows(consumer: str, rows: Sequence[Mapping[str, object]]) -> None:
    """Reject evaluator-owned fields before data reaches a prohibited consumer."""

    if consumer not in _PROHIBITED_CONSUMERS:
        raise ValueError(f"unknown observable-data consumer: {consumer}")
    for row_number, row in enumerate(rows, start=1):
        leaked = sorted(_hidden_field_paths(row))
        if leaked:
            fields = ", ".join(leaked)
            raise HiddenTruthLeakageError(
                f"{consumer} row {row_number} contains hidden field(s): {fields}"
            )


def _normalize_score_rows(rows: Sequence[tuple[str, float]], *, field: str) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, tuple) or len(row) != 2:
            raise HighValueEvaluationError(f"{field} row {row_number} must be a pair")
        candidate_id, raw_value = row
        if not isinstance(candidate_id, str) or not candidate_id:
            raise HighValueEvaluationError(f"{field} row {row_number} has invalid candidate ID")
        if candidate_id in normalized:
            raise HighValueEvaluationError(f"{field} contains duplicate candidate ID")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise HighValueEvaluationError(f"{field} contains a missing or non-numeric value")
        value = float(raw_value)
        if not math.isfinite(value):
            raise HighValueEvaluationError(f"{field} contains a non-finite value")
        normalized[candidate_id] = value
    return normalized


def _average_ranks(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    ranks: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = ((start + 1) + end) / 2.0
        for candidate_id, _ in ordered[start:end]:
            ranks[candidate_id] = average_rank
        start = end
    return ranks


def _spearman(predictions: Mapping[str, float], targets: Mapping[str, float]) -> MetricValue:
    if len(targets) < 2:
        return MetricValue(
            "spearman_rank_correlation",
            None,
            "higher_is_better",
            "undefined",
            None,
            "insufficient_observations",
        )
    prediction_ranks = _average_ranks(predictions)
    target_ranks = _average_ranks(targets)
    candidate_ids = sorted(targets)
    prediction_values = [prediction_ranks[candidate_id] for candidate_id in candidate_ids]
    target_values = [target_ranks[candidate_id] for candidate_id in candidate_ids]
    prediction_mean = sum(prediction_values) / len(prediction_values)
    target_mean = sum(target_values) / len(target_values)
    prediction_variance = sum((value - prediction_mean) ** 2 for value in prediction_values)
    target_variance = sum((value - target_mean) ** 2 for value in target_values)
    if prediction_variance == 0.0 or target_variance == 0.0:
        if prediction_variance == 0.0 and target_variance == 0.0:
            reason = "constant_target_and_prediction"
        elif target_variance == 0.0:
            reason = "constant_target"
        else:
            reason = "constant_prediction"
        return MetricValue(
            "spearman_rank_correlation",
            None,
            "higher_is_better",
            "undefined",
            None,
            reason,
        )
    covariance = sum(
        (prediction - prediction_mean) * (target - target_mean)
        for prediction, target in zip(prediction_values, target_values, strict=True)
    )
    value = covariance / math.sqrt(prediction_variance * target_variance)
    return MetricValue(
        "spearman_rank_correlation",
        None,
        "higher_is_better",
        "defined",
        value,
        None,
    )


def _high_value_reports_and_memberships(
    targets: Mapping[str, float], definitions: Sequence[Mapping[str, Any]]
) -> tuple[list[HighValueReport], list[tuple[str, set[str]]]]:
    reports: list[HighValueReport] = []
    memberships: list[tuple[str, set[str]]] = []
    target_ranking = sorted(
        targets, key=lambda candidate_id: (-targets[candidate_id], candidate_id)
    )
    for definition in definitions:
        nominal_k = int(definition["derivation"]["nominal_k"])
        boundary_score = targets[target_ranking[nominal_k - 1]]
        members = {
            candidate_id for candidate_id, target in targets.items() if target >= boundary_score
        }
        definition_id = str(definition["definition_id"])
        memberships.append((definition_id, members))
        reports.append(
            HighValueReport(
                definition_id=definition_id,
                nominal_k=nominal_k,
                actual_high_value_count=len(members),
                boundary_score=boundary_score,
                boundary_tie_count=sum(target == boundary_score for target in targets.values()),
            )
        )
    return reports, memberships


def evaluate_static_predictions(
    config: LoadedExperimentConfig,
    prediction_rows: Sequence[tuple[str, float]],
    hidden_target_rows: Sequence[tuple[str, float]],
) -> StaticEvaluation:
    """Evaluate synthetic M2 predictions without exposing hidden membership."""

    if config.schema_version != "2":
        raise HighValueEvaluationError("static high-value evaluation requires Experiment Config v2")
    predictions = _normalize_score_rows(prediction_rows, field="predictions")
    targets = _normalize_score_rows(hidden_target_rows, field="hidden targets")
    if set(predictions) != set(targets):
        raise HighValueEvaluationError("predictions must exact-cover the hidden target universe")

    evaluation = config.value["evaluation"]
    definitions = evaluation["high_value_definitions"]
    frozen_candidate_count = definitions[0]["frozen_candidate_count"]
    if len(targets) != frozen_candidate_count:
        raise HighValueEvaluationError("evaluator universe does not match frozen candidate count")

    reports, memberships = _high_value_reports_and_memberships(targets, definitions)
    recalls: list[MetricValue] = []
    prediction_ranking = sorted(
        predictions,
        key=lambda candidate_id: (-predictions[candidate_id], candidate_id),
    )
    for report, (definition_id, members) in zip(reports, memberships, strict=True):
        predicted = set(prediction_ranking[: report.nominal_k])
        recalls.append(
            MetricValue(
                metric_id="high_value_recall_at_nominal_k",
                definition_id=definition_id,
                direction="higher_is_better",
                status="defined",
                value=len(predicted & members) / len(members),
                undefined_reason=None,
            )
        )
    return StaticEvaluation(
        task_id=config.value["task_id"],
        high_value_reports=tuple(reports),
        metrics=(_spearman(predictions, targets), *recalls),
    )


def evaluate_sequential_observations(
    config: LoadedExperimentConfig,
    revealed_rows: Sequence[tuple[str, float]],
    hidden_target_rows: Sequence[tuple[str, float]],
    *,
    round_index: int,
    budget: int,
) -> SequentialEvaluation:
    """Evaluate one synthetic sequential checkpoint without exposing hidden membership."""

    if config.schema_version != "2":
        raise HighValueEvaluationError("sequential evaluation requires Experiment Config v2")
    if type(round_index) is not int or round_index < 0:
        raise HighValueEvaluationError("round_index must be a non-negative integer")
    if type(budget) is not int or budget not in config.value["budgets"]:
        raise HighValueEvaluationError("budget must be preregistered by Experiment Config")

    revealed = _normalize_score_rows(revealed_rows, field="revealed observations")
    targets = _normalize_score_rows(hidden_target_rows, field="hidden targets")
    evaluation = config.value["evaluation"]
    definitions = evaluation["high_value_definitions"]
    frozen_candidate_count = definitions[0]["frozen_candidate_count"]
    if len(targets) != frozen_candidate_count:
        raise HighValueEvaluationError("evaluator universe does not match frozen candidate count")
    if len(revealed) > budget:
        raise HighValueEvaluationError("revealed observations exceed the evaluation budget")
    if not set(revealed) <= set(targets):
        raise HighValueEvaluationError("revealed observations contain an unknown candidate ID")
    if any(revealed[candidate_id] != targets[candidate_id] for candidate_id in revealed):
        raise HighValueEvaluationError("revealed observation does not match evaluator-owned truth")

    reports, memberships = _high_value_reports_and_memberships(targets, definitions)

    if revealed:
        best_found = max(revealed.values())
        simple_regret = max(targets.values()) - best_found
        if simple_regret < 0:
            raise HighValueEvaluationError("simple regret must not be negative")
        leading_metrics = (
            MetricValue(
                "best_found_value",
                None,
                "higher_is_better",
                "defined",
                best_found,
                None,
            ),
            MetricValue(
                "simple_regret",
                None,
                "lower_is_better",
                "defined",
                simple_regret,
                None,
            ),
        )
    else:
        leading_metrics = (
            MetricValue(
                "best_found_value",
                None,
                "higher_is_better",
                "undefined",
                None,
                "no_revealed_observations",
            ),
            MetricValue(
                "simple_regret",
                None,
                "lower_is_better",
                "undefined",
                None,
                "no_revealed_observations",
            ),
        )

    revealed_ids = set(revealed)
    recalls = tuple(
        MetricValue(
            "high_value_discovery_recall",
            definition_id,
            "higher_is_better",
            "defined",
            len(revealed_ids & members) / len(members),
            None,
        )
        for definition_id, members in memberships
    )
    if len(revealed) < budget:
        successes = tuple(
            MetricValue(
                "success_at_budget",
                definition_id,
                "higher_is_better",
                "undefined",
                None,
                "budget_not_reached",
            )
            for definition_id, _ in memberships
        )
    else:
        successes = tuple(
            MetricValue(
                "success_at_budget",
                definition_id,
                "higher_is_better",
                "defined",
                bool(revealed_ids & members),
                None,
            )
            for definition_id, members in memberships
        )
    return SequentialEvaluation(
        task_id=config.value["task_id"],
        round_index=round_index,
        budget=budget,
        high_value_reports=tuple(reports),
        metrics=(*leading_metrics, *recalls, *successes),
    )
