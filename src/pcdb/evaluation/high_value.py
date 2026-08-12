"""Synthetic-safe, truth-owning high-value evaluation primitives."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


class EvaluationInputError(ValueError):
    """Raised when evaluator inputs violate the frozen candidate universe."""


class LeakageBoundaryError(ValueError):
    """Raised when a prohibited consumer receives truth-derived fields."""


@dataclass(frozen=True, slots=True)
class DefinitionSummary:
    """Aggregate high-value definition evidence without candidate membership."""

    nominal_k: int
    actual_high_value_count: int
    boundary_score: float
    boundary_tie_count: int


@dataclass(frozen=True, slots=True)
class MetricValue:
    """A finite defined value or one frozen structured undefined state."""

    status: Literal["defined", "undefined"]
    value: float | bool | None
    undefined_reason: str | None

    @classmethod
    def defined(cls, value: float | bool) -> MetricValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise EvaluationInputError("defined metric value must be finite")
        return cls(status="defined", value=value, undefined_reason=None)

    @classmethod
    def undefined(cls, reason: str) -> MetricValue:
        return cls(status="undefined", value=None, undefined_reason=reason)


@dataclass(frozen=True, slots=True)
class SequentialMetricSet:
    """The frozen M3-M5 metric set for one reveal boundary."""

    best_found_value: MetricValue
    simple_regret: MetricValue
    high_value_discovery_recall: MetricValue
    success_at_budget: MetricValue


def _validated_rows(rows: Sequence[tuple[str, float]], *, field: str) -> dict[str, float]:
    if not rows:
        raise EvaluationInputError(f"{field} must not be empty")
    result: dict[str, float] = {}
    for candidate_id, raw_value in rows:
        if not isinstance(candidate_id, str) or not candidate_id:
            raise EvaluationInputError(f"{field} candidate IDs must not be empty")
        if candidate_id in result:
            raise EvaluationInputError(f"{field} contains duplicate candidate ID: {candidate_id}")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise EvaluationInputError(f"{field} values must be numeric")
        value = float(raw_value)
        if not math.isfinite(value):
            raise EvaluationInputError(f"{field} values must be finite")
        result[candidate_id] = value
    return result


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average = (start + 1 + end) / 2
        for position in order[start:end]:
            ranks[position] = average
        start = end
    return ranks


def _variance(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values)


class HighValueEvaluator:
    """Own hidden truth and expose only aggregate summaries and metrics."""

    def __init__(self, truth_rows: Sequence[tuple[str, float]]) -> None:
        self.__truth = _validated_rows(truth_rows, field="truth")

    @property
    def candidate_ids(self) -> frozenset[str]:
        """Expose the selectable universe without labels or high-value membership."""

        return frozenset(self.__truth)

    @property
    def truth_sha256(self) -> str:
        """Return an identity-only digest without exposing labels or membership."""

        payload = json.dumps(
            sorted(self.__truth.items()),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _definition(self, fraction: float) -> tuple[DefinitionSummary, frozenset[str]]:
        if (
            isinstance(fraction, bool)
            or not isinstance(fraction, (int, float))
            or not math.isfinite(float(fraction))
            or not 0 < float(fraction) <= 1
        ):
            raise EvaluationInputError("fraction must be finite and within (0, 1]")
        nominal_k = math.ceil(float(fraction) * len(self.__truth))
        ranked = sorted(self.__truth.items(), key=lambda item: (-item[1], item[0]))
        boundary_score = ranked[nominal_k - 1][1]
        membership = frozenset(
            candidate_id for candidate_id, target in ranked if target >= boundary_score
        )
        summary = DefinitionSummary(
            nominal_k=nominal_k,
            actual_high_value_count=len(membership),
            boundary_score=boundary_score,
            boundary_tie_count=sum(target == boundary_score for _, target in ranked),
        )
        return summary, membership

    def definition_summary(self, *, fraction: float) -> DefinitionSummary:
        """Return tie-inclusive counts without returning membership IDs."""

        summary, _ = self._definition(fraction)
        return summary

    def _predictions(self, rows: Sequence[tuple[str, float]]) -> dict[str, float]:
        predictions = _validated_rows(rows, field="predictions")
        if set(predictions) != set(self.__truth):
            raise EvaluationInputError("predictions must exact-cover the candidate universe")
        return predictions

    def spearman_rank_correlation(
        self, prediction_rows: Sequence[tuple[str, float]]
    ) -> MetricValue:
        """Compute frozen average-tie-rank Spearman correlation."""

        predictions = self._predictions(prediction_rows)
        candidate_ids = sorted(self.__truth)
        if len(candidate_ids) < 2:
            return MetricValue.undefined("insufficient_observations")
        prediction_ranks = _average_ranks([predictions[item] for item in candidate_ids])
        target_ranks = _average_ranks([self.__truth[item] for item in candidate_ids])
        prediction_variance = _variance(prediction_ranks)
        target_variance = _variance(target_ranks)
        if target_variance == 0 and prediction_variance == 0:
            return MetricValue.undefined("constant_target_and_prediction")
        if target_variance == 0:
            return MetricValue.undefined("constant_target")
        if prediction_variance == 0:
            return MetricValue.undefined("constant_prediction")
        prediction_mean = sum(prediction_ranks) / len(prediction_ranks)
        target_mean = sum(target_ranks) / len(target_ranks)
        covariance = sum(
            (prediction - prediction_mean) * (target - target_mean)
            for prediction, target in zip(prediction_ranks, target_ranks, strict=True)
        )
        return MetricValue.defined(covariance / math.sqrt(prediction_variance * target_variance))

    def high_value_recall_at_nominal_k(
        self,
        prediction_rows: Sequence[tuple[str, float]],
        *,
        fraction: float,
    ) -> MetricValue:
        """Compute prediction Top-K recall against private tie-inclusive truth."""

        predictions = self._predictions(prediction_rows)
        summary, membership = self._definition(fraction)
        prediction_ranking = sorted(predictions.items(), key=lambda item: (-item[1], item[0]))
        predicted = {candidate_id for candidate_id, _ in prediction_ranking[: summary.nominal_k]}
        return MetricValue.defined(len(predicted & membership) / len(membership))

    def sequential_metrics(
        self,
        revealed_candidate_ids: Sequence[str],
        *,
        fraction: float,
        success_budget: int,
    ) -> SequentialMetricSet:
        """Compute frozen sequential metrics for one distinct reveal prefix."""

        if (
            isinstance(success_budget, bool)
            or not isinstance(success_budget, int)
            or success_budget < 1
        ):
            raise EvaluationInputError("success budget must be a positive integer")
        if len(set(revealed_candidate_ids)) != len(revealed_candidate_ids):
            raise EvaluationInputError("revealed candidate IDs must be distinct")
        unknown = [
            candidate_id
            for candidate_id in revealed_candidate_ids
            if candidate_id not in self.__truth
        ]
        if unknown:
            raise EvaluationInputError(f"unknown revealed candidate ID: {unknown[0]}")
        _, membership = self._definition(fraction)
        revealed = set(revealed_candidate_ids)
        discovered = len(revealed & membership)
        recall = MetricValue.defined(discovered / len(membership))
        if len(revealed_candidate_ids) < success_budget:
            success = MetricValue.undefined("budget_not_reached")
        else:
            budget_prefix = set(revealed_candidate_ids[:success_budget])
            success = MetricValue.defined(bool(budget_prefix & membership))
        if not revealed:
            return SequentialMetricSet(
                best_found_value=MetricValue.undefined("no_revealed_observations"),
                simple_regret=MetricValue.undefined("no_revealed_observations"),
                high_value_discovery_recall=recall,
                success_at_budget=success,
            )
        best = max(self.__truth[item] for item in revealed)
        regret = max(self.__truth.values()) - best
        if regret < 0:
            raise EvaluationInputError("simple regret must not be negative")
        return SequentialMetricSet(
            best_found_value=MetricValue.defined(best),
            simple_regret=MetricValue.defined(regret),
            high_value_discovery_recall=recall,
            success_at_budget=success,
        )


_PROHIBITED_CONSUMERS = {
    "candidate-features",
    "surrogate-model",
    "calibration",
    "acquisition-strategy",
}
_TRUTH_DERIVED_KEYS = {
    "actual_high_value_count",
    "boundary_score",
    "boundary_tie_count",
    "high_value",
    "high_value_flag",
    "high_value_membership",
    "hidden_label",
    "label",
    "target",
    "unrevealed_target",
}


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | {
            nested_key for nested in value.values() for nested_key in _walk_keys(nested)
        }
    if isinstance(value, (list, tuple)):
        return {nested_key for nested in value for nested_key in _walk_keys(nested)}
    return set()


def validate_prohibited_consumer_payload(
    payload: Mapping[str, Any],
    *,
    consumer: str,
) -> None:
    """Fail closed if model-side consumers receive truth-derived fields."""

    if consumer not in _PROHIBITED_CONSUMERS:
        raise LeakageBoundaryError(f"unknown prohibited consumer: {consumer}")
    leaked = sorted(_walk_keys(payload) & _TRUTH_DERIVED_KEYS)
    if leaked:
        raise LeakageBoundaryError(f"{consumer} payload contains truth-derived fields: {leaked}")
