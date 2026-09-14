"""四种实验内部选择器；预测与信息价值必须来自显式的 observation-only scorer。"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pcdb.replay._m6_engine import Action, Policy, State

Strategy = Literal["hf_only", "fixed_ratio", "hard_prescreen", "cost_aware"]
STRATEGIES: tuple[Strategy, ...] = ("hf_only", "fixed_ratio", "hard_prescreen", "cost_aware")


@dataclass(frozen=True, slots=True)
class Scores:
    """两 fidelity 的 value 必须使用同一效用单位，不能直接填入 LF/HF 原始分数。"""

    mean: Mapping[str, float]
    high_value: Mapping[str, float]
    low_value: Mapping[str, float]

    def validate(self, ids: tuple[str, ...]) -> None:
        for mapping in (self.mean, self.high_value, self.low_value):
            if set(mapping) != set(ids):
                raise ValueError("score coverage mismatch")
            if any(
                isinstance(value, bool) or not math.isfinite(value) for value in mapping.values()
            ):
                raise ValueError("scores must be finite")
        if any(
            value < 0 for mapping in (self.high_value, self.low_value) for value in mapping.values()
        ):
            raise ValueError("information value must be nonnegative")


Scorer = Callable[[State], Scores]


def make_policy(
    strategy: Strategy,
    scorer: Scorer,
    *,
    seed: int,
    low_per_high: int,
    screen_fraction: float,
) -> Policy:
    """所有选择按状态重建，不依赖共享可变计数器；没有默认科学参数。"""
    if strategy not in STRATEGIES:
        raise ValueError("unknown strategy")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if type(low_per_high) is not int or low_per_high < 1:
        raise ValueError("low_per_high must be a positive integer")
    if (
        isinstance(screen_fraction, bool)
        or not math.isfinite(screen_fraction)
        or not 0 < screen_fraction <= 1
    ):
        raise ValueError("invalid screen fraction")

    def tie(candidate: str) -> str:
        return hashlib.sha256(f"{seed}:{candidate}".encode()).hexdigest()

    def policy(state: State) -> tuple[Action, ...]:
        high = state.available("high")
        low = tuple(
            candidate
            for candidate in state.available("low")
            if candidate not in {item.candidate_id for item in state.high}
        )
        if strategy == "hard_prescreen":
            # 筛选全集固定为全部候选，含初始 HF；不随 HF 新购买扩大/移动阈值。
            unscreened = tuple(
                candidate
                for candidate in state.candidate_ids
                if candidate not in {item.candidate_id for item in state.low}
            )
            if unscreened:
                required = len(unscreened) * state.costs.low + state.costs.high
                if required > state.budget - state.spent:
                    raise ValueError("insufficient screen budget for full LF plus one HF")
                return (Action(min(unscreened, key=tie), "low"),)
            keep = math.ceil(len(state.candidate_ids) * screen_fraction)
            ranked = sorted(state.low, key=lambda item: (-item.label, tie(item.candidate_id)))
            retained = {item.candidate_id for item in ranked[:keep]}
            high = tuple(candidate for candidate in high if candidate in retained)
        if not high and (strategy in ("hf_only", "hard_prescreen") or not low):
            return ()
        scores = scorer(state)
        scores.validate(state.candidate_ids)
        if strategy in ("hf_only", "hard_prescreen"):
            return (
                Action(
                    min(high, key=lambda candidate: (-scores.mean[candidate], tie(candidate))),
                    "high",
                ),
            )
        if strategy == "fixed_ratio":
            if not high:
                return ()
            if state.budget - state.spent < state.costs.low + state.costs.high:
                low = ()
            # HF 数包含初始购买；从首个 LF 开始，每 low_per_high 次 LF 后执行一次 HF。
            bought = len(state.low)
            last_cycle_lows = bought % low_per_high
            confirmed_screened = sum(
                item.candidate_id in {x.candidate_id for x in state.low} for item in state.high
            )
            want_low = (
                bought == 0 or last_cycle_lows != 0 or confirmed_screened >= bought // low_per_high
            )
            if low and (want_low or not high):
                return (
                    Action(
                        min(
                            low,
                            key=lambda candidate: (-scores.low_value[candidate], tie(candidate)),
                        ),
                        "low",
                    ),
                )
            if high:
                # 优先确认已筛候选，避免用 HF 计数掩盖未完成的一轮筛选。
                screened = {item.candidate_id for item in state.low}
                eligible = tuple(candidate for candidate in high if candidate in screened) or high
                return (
                    Action(
                        min(
                            eligible,
                            key=lambda candidate: (-scores.mean[candidate], tie(candidate)),
                        ),
                        "high",
                    ),
                )
            return ()
        candidates = [
            (scores.high_value[candidate] / state.costs.high, "high", candidate)
            for candidate in high
        ]
        # LF 之后必须还能支付至少一次 HF；不把最终只能买 LF 的尾款当发现机会。
        if state.budget - state.spent >= state.costs.low + state.costs.high and high:
            candidates.extend(
                (scores.low_value[candidate] / state.costs.low, "low", candidate)
                for candidate in low
            )
        if not candidates:
            return ()
        _, fidelity, candidate = min(
            candidates, key=lambda row: (-row[0], row[1] != "high", tie(row[2]))
        )
        return (Action(candidate, "high" if fidelity == "high" else "low"),)

    return policy
