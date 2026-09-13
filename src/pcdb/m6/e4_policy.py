"""E4 v1 计费 warmup、独立节奏与 value/成本选择；保留旧 synthetic 策略。"""

from __future__ import annotations

import hashlib
import math

from pcdb.m6.policies import STRATEGIES, Scorer, Strategy
from pcdb.replay._m6_engine import Action, Policy, State


def make_e4_policy(
    strategy: Strategy, scorer: Scorer, *, seed: int, initial_ids: tuple[str, ...]
) -> Policy:
    if strategy not in STRATEGIES or not initial_ids or len(set(initial_ids)) != len(initial_ids):
        raise ValueError("invalid strategy/initial IDs")

    def tie(c: str) -> str:
        return hashlib.sha256(f"{seed}:{c}".encode()).hexdigest()

    def policy(state: State) -> tuple[Action, ...]:
        highs = {o.candidate_id for o in state.high}
        lows = {o.candidate_id for o in state.low}
        if not set(initial_ids).issubset(highs):
            raise ValueError("initial HF incomplete")
        high = state.available("high")
        if strategy != "hf_only":
            warm = tuple(c for c in initial_ids if c not in lows)
            if warm:
                if len(warm) * state.costs.low + state.costs.high > state.budget - state.spent:
                    raise ValueError("insufficient warmup budget")
                return (Action(warm[0], "low"),)
        if not high:
            return ()
        low = tuple(c for c in state.available("low") if c not in highs)
        if strategy == "hard_prescreen":
            remaining = tuple(c for c in state.candidate_ids if c not in lows)
            if remaining:
                if len(remaining) * state.costs.low + state.costs.high > state.budget - state.spent:
                    raise ValueError("insufficient full screen budget")
                return (Action(min(remaining, key=tie), "low"),)
            ranked = sorted(state.low, key=lambda o: (-o.label, tie(o.candidate_id)))
            keep = {o.candidate_id for o in ranked[: math.ceil(len(ranked) * 0.25)]}
            high = tuple(c for c in high if c in keep)
            if not high:
                return ()
        scores = scorer(state)
        scores.validate(state.candidate_ids)
        greedy = min(high, key=lambda c: (-scores.mean[c], tie(c)))
        if strategy in ("hf_only", "hard_prescreen"):
            return (Action(greedy, "high"),)
        if state.budget - state.spent < state.costs.low + state.costs.high:
            low = ()
        if strategy == "fixed_ratio":
            new_low = len(lows) - len(initial_ids)
            new_high = len(highs) - len(initial_ids)
            if low and new_low < 2 * (new_high + 1):
                return (Action(min(low, key=lambda c: (-scores.low_value[c], tie(c))), "low"),)
            return (Action(greedy, "high"),)
        actions = [(scores.high_value[c] / state.costs.high, "high", c) for c in high]
        actions.extend((scores.low_value[c] / state.costs.low, "low", c) for c in low)
        if max(a[0] for a in actions) == 0:
            return (Action(greedy, "high"),)
        _, fidelity, c = min(
            actions, key=lambda a: (-a[0], a[1] != "high", -scores.mean[a[2]], tie(a[2]))
        )
        return (Action(c, "high" if fidelity == "high" else "low"),)

    return policy
