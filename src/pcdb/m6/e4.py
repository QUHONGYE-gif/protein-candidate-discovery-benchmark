"""Evaluator-owned 可控代理与单任务配对汇总；不提供正式数据运行入口。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from pcdb.replay._m6_engine import Evaluation


@dataclass(frozen=True, slots=True)
class ProxySpec:
    correlation: float
    high_fraction: float
    reverse_high_tail: bool
    seed: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.correlation, bool)
            or not math.isfinite(self.correlation)
            or not -1 <= self.correlation <= 1
        ):
            raise ValueError("invalid correlation")
        if (
            isinstance(self.high_fraction, bool)
            or not math.isfinite(self.high_fraction)
            or not 0 < self.high_fraction <= 1
        ):
            raise ValueError("invalid high fraction")
        if type(self.reverse_high_tail) is not bool or type(self.seed) is not int or self.seed < 0:
            raise ValueError("invalid proxy seed or bias flag")


@dataclass(frozen=True, slots=True)
class Proxy:
    values: dict[str, float]
    pearson: float | None
    high_tail_spearman: float | None


def _correlation(a: NDArray[np.float64], b: NDArray[np.float64]) -> float | None:
    if len(a) < 2:
        return None
    x, y = a - a.mean(), b - b.mean()
    denominator = float(np.linalg.norm(x) * np.linalg.norm(y))
    return None if denominator == 0 else float(np.clip(x @ y / denominator, -1, 1))


def _ranks(values: NDArray[np.float64]) -> NDArray[np.float64]:
    ranks = np.empty(len(values), dtype=np.float64)
    for value in np.unique(values):
        ranks[values == value] = (
            float(np.count_nonzero(values < value)) + (np.count_nonzero(values == value) - 1) / 2
        )
    return ranks


def generate_proxy(truth: Mapping[str, float], spec: ProxySpec) -> Proxy:
    """偏差前 Pearson 精确匹配；偏差后报告实际相关性，绝不重新调回目标。"""
    ids = tuple(sorted(truth))
    if len(ids) < 3 or any(not isinstance(candidate, str) or not candidate for candidate in ids):
        raise ValueError("proxy requires at least three named candidates")
    if any(isinstance(value, bool) or not math.isfinite(value) for value in truth.values()):
        raise ValueError("truth must be finite")
    y = np.asarray([truth[candidate] for candidate in ids], dtype=np.float64)
    centered = y - y.mean()
    if not np.isfinite(centered).all() or not 0 < np.linalg.norm(centered) < math.inf:
        raise ValueError("constant or invalid truth has no correlation")
    z = centered / np.linalg.norm(centered)
    noise = np.random.Generator(np.random.PCG64(spec.seed)).normal(size=len(ids))
    noise -= noise.mean()
    noise -= float(noise @ z) * z
    norm = float(np.linalg.norm(noise))
    if norm <= 1e-12:
        raise ValueError("degenerate orthogonal noise")
    low = spec.correlation * z + math.sqrt(1 - spec.correlation**2) * noise / norm
    threshold = np.sort(y)[-math.ceil(len(y) * spec.high_fraction)]
    tail = y >= threshold
    if spec.reverse_high_tail:
        # 对高值区代理做镜像，其余候选不变；真实局部排序未必与 HF 完全负相关。
        low[tail] = 2 * low[tail].mean() - low[tail]
    if not np.isfinite(low).all():
        raise ValueError("nonfinite generated proxy")
    return Proxy(
        dict(zip(ids, map(float, low), strict=True)),
        _correlation(y, low),
        _correlation(_ranks(y[tail]), _ranks(low[tail])),
    )


@dataclass(frozen=True, slots=True)
class Pair:
    task_id: str
    condition_id: str
    seed: int
    baseline: Evaluation
    treatment: Evaluation


def paired_summary(pairs: Sequence[Pair], *, checkpoint: int) -> dict[str, float | int | None]:
    """同一 task/condition 内 seed 配对；不能把返回的 seed 比例当跨 assay 概率。"""
    if not pairs:
        raise ValueError("empty paired cohort")
    if (
        len({(pair.task_id, pair.condition_id) for pair in pairs}) != 1
        or not pairs[0].task_id
        or not pairs[0].condition_id
    ):
        raise ValueError("pair summary must contain exactly one task and condition")
    if any(type(pair.seed) is not int or pair.seed < 0 for pair in pairs):
        raise ValueError("invalid paired seed")
    if len({pair.seed for pair in pairs}) != len(pairs):
        raise ValueError("duplicate paired seed")
    recalls: list[float] = []
    regrets: list[float] = []
    savings: list[float] = []
    baseline_censored = treatment_censored = 0
    for pair in pairs:
        baseline, treatment = pair.baseline, pair.treatment
        if baseline.budget != treatment.budget:
            raise ValueError("paired budget mismatch")
        base, test = baseline.at_cost(checkpoint), treatment.at_cost(checkpoint)
        recalls.append(test.recall - base.recall)
        if base.simple_regret is not None and test.simple_regret is not None:
            regrets.append(test.simple_regret - base.simple_regret)
        base_cost = baseline.cost_to_target
        test_cost = treatment.cost_to_target
        base_reached = base_cost is not None and base_cost <= checkpoint
        test_reached = test_cost is not None and test_cost <= checkpoint
        baseline_censored += not base_reached
        treatment_censored += not test_reached
        if base_reached and test_reached and base_cost is not None and test_cost is not None:
            if base_cost <= 0:
                raise ValueError("invalid baseline target cost")
            savings.append(1 - test_cost / base_cost)
    return {
        "paired_seeds": len(pairs),
        "mean_delta_recall": float(np.mean(recalls)),
        "mean_delta_regret_defined": float(np.mean(regrets)) if regrets else None,
        "regret_defined_pairs": len(regrets),
        "negative_recall_fraction": sum(value < 0 for value in recalls) / len(pairs),
        "mean_savings_both_reached": float(np.mean(savings)) if savings else None,
        "both_reached": len(savings),
        "baseline_censored": baseline_censored,
        "treatment_censored": treatment_censored,
    }
