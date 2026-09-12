"""两保真成本回放的内部原型，仅用于 synthetic 工程验证。

成本为调用方明确给出的整数单位；没有默认科学成本模型。LF 和 HF 分别收费，
HF 揭示才计为发现。尚不接入 RunSpec、真实数据入口或正式 M6 协议。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pcdb.replay.oracle import HiddenOracle, Observation

Fidelity = Literal["low", "high"]


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class Costs:
    low: int
    high: int

    def __post_init__(self) -> None:
        _positive_integer(self.low, "low cost")
        _positive_integer(self.high, "high cost")

    def price(self, fidelity: Fidelity) -> int:
        if fidelity not in ("low", "high"):
            raise ValueError("unknown fidelity")
        return self.low if fidelity == "low" else self.high


@dataclass(frozen=True, slots=True)
class Action:
    candidate_id: str
    fidelity: Fidelity

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError("candidate ID must be a nonempty string")
        if self.fidelity not in ("low", "high"):
            raise ValueError("unknown fidelity")


@dataclass(frozen=True, slots=True)
class State:
    """策略仅接收候选身份和已购买观测，不接收 oracle 或 evaluator。"""

    candidate_ids: tuple[str, ...]
    low: tuple[Observation, ...]
    high: tuple[Observation, ...]
    costs: Costs
    spent: int
    budget: int

    def available(self, fidelity: Fidelity) -> tuple[str, ...]:
        price = self.costs.price(fidelity)
        if price > self.budget - self.spent:
            return ()
        observations = self.low if fidelity == "low" else self.high
        bought = {observation.candidate_id for observation in observations}
        return tuple(candidate for candidate in self.candidate_ids if candidate not in bought)


@dataclass(frozen=True, slots=True)
class Event:
    action: Action
    label: float
    spent: int


@dataclass(frozen=True, slots=True)
class Trace:
    """内部回放记录，不是可发布的科学 artifact。"""

    candidate_ids: tuple[str, ...]
    high_truth_sha256: str
    costs: Costs
    budget: int
    events: tuple[Event, ...]


Policy = Callable[[State], tuple[Action, ...]]


def replay(
    high_truth: Mapping[str, float],
    low_truth: Mapping[str, float],
    costs: Costs,
    budget: int,
    policy: Policy,
    *,
    initial_high: tuple[str, ...] = (),
) -> Trace:
    """每次调用建立独立 oracle；整批校验后才揭示和记账。"""
    _positive_integer(budget, "budget")
    high_oracle, low_oracle = HiddenOracle(high_truth), HiddenOracle(low_truth)
    if high_oracle.candidate_ids != low_oracle.candidate_ids:
        raise ValueError("low/high candidate universe mismatch")
    candidate_ids = tuple(sorted(high_oracle.candidate_ids))
    events: list[Event] = []
    purchased: set[Action] = set()
    spent = 0

    def buy(batch: tuple[Action, ...]) -> None:
        nonlocal spent
        if not isinstance(batch, tuple) or any(not isinstance(action, Action) for action in batch):
            raise ValueError("policy must return a tuple of Actions")
        if len(set(batch)) != len(batch):
            raise ValueError("duplicate action in batch")
        if any(action in purchased for action in batch):
            raise ValueError("action already purchased")
        if any(action.candidate_id not in high_oracle.candidate_ids for action in batch):
            raise ValueError("unknown candidate")
        if spent + sum(costs.price(action.fidelity) for action in batch) > budget:
            raise ValueError("purchase exceeds budget")
        # 所有可预见的输入错误已验证；回调无法接触任一 oracle。
        for action in batch:
            oracle = low_oracle if action.fidelity == "low" else high_oracle
            observation = oracle.reveal((action.candidate_id,))[0]
            spent += costs.price(action.fidelity)
            purchased.add(action)
            events.append(Event(action, observation.label, spent))

    buy(tuple(Action(candidate, "high") for candidate in initial_high))
    while True:
        state = State(
            candidate_ids, low_oracle.observations, high_oracle.observations, costs, spent, budget
        )
        if not state.available("low") and not state.available("high"):
            break
        batch = policy(state)
        if batch == ():
            break
        buy(batch)
    return Trace(candidate_ids, high_oracle.truth_sha256, costs, budget, tuple(events))


@dataclass(frozen=True, slots=True)
class Point:
    spent: int
    best_found: float | None
    simple_regret: float | None
    recall: float


@dataclass(frozen=True, slots=True)
class Evaluation:
    points: tuple[Point, ...]
    budget: int
    cost_to_target: int | None
    target_censored: bool

    def at_cost(self, cost: int) -> Point:
        """同一成本阈值采用此前最后状态；不会利用阈值后的购买。"""
        if type(cost) is not int or not 0 <= cost <= self.budget:
            raise ValueError("checkpoint outside declared budget")
        return next(point for point in reversed(self.points) if point.spent <= cost)


def evaluate(
    trace: Trace,
    high_truth: Mapping[str, float],
    *,
    high_value_fraction: float,
    target_recall: float,
) -> Evaluation:
    """回放结束后由 evaluator 读取 HF truth；策略看不到指标或高值 membership。

    仅实现 larger-is-better 标签。top-fraction 包含边界并列值；未取得任何 HF 时
    best/regret 未定义。未达到目标的 cost-to-target 为 None，不能补成总预算。
    """
    for value in (high_value_fraction, target_recall):
        if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError("fractions must be finite and in (0, 1]")
    identity = HiddenOracle(high_truth)
    if identity.truth_sha256 != trace.high_truth_sha256:
        raise ValueError("high truth identity mismatch")
    if tuple(sorted(identity.candidate_ids)) != trace.candidate_ids:
        raise ValueError("candidate universe mismatch")
    _positive_integer(trace.budget, "budget")
    ranked = sorted(high_truth.values(), reverse=True)
    threshold = ranked[max(1, math.ceil(len(ranked) * high_value_fraction)) - 1]
    high_value = {candidate for candidate, label in high_truth.items() if label >= threshold}
    best: float | None = None
    hits: set[str] = set()
    purchased: set[Action] = set()
    points = [Point(0, None, None, 0.0)]
    cost_to_target: int | None = None
    spent = 0
    for event in trace.events:
        if event.action in purchased or event.action.candidate_id not in high_truth:
            raise ValueError("invalid purchase trace")
        if not math.isfinite(event.label):
            raise ValueError("nonfinite observation")
        spent += trace.costs.price(event.action.fidelity)
        if event.spent != spent or spent > trace.budget:
            raise ValueError("invalid cost trace")
        purchased.add(event.action)
        if event.action.fidelity == "high":
            if event.label != high_truth[event.action.candidate_id]:
                raise ValueError("revealed label mismatch")
            best = event.label if best is None else max(best, event.label)
            if event.action.candidate_id in high_value:
                hits.add(event.action.candidate_id)
        recall = len(hits) / len(high_value)
        points.append(Point(spent, best, None if best is None else max(ranked) - best, recall))
        if recall >= target_recall and cost_to_target is None:
            cost_to_target = spent
    return Evaluation(tuple(points), trace.budget, cost_to_target, cost_to_target is None)
