"""运行内置小型 synthetic smoke；没有真实数据或文件读取参数。"""

from __future__ import annotations

import json
import random

from pcdb.replay._m6_engine import Action, Costs, State, evaluate, replay


def smoke(seed: int = 1729) -> dict[str, object]:
    """两条 toy 路径验证同预算与隔离，不代表正式 E4 的四策略。"""
    high = {f"c{i:02d}": float(i) for i in range(8)}
    low = {candidate: -value for candidate, value in high.items()}
    order = list(sorted(high))
    random.Random(seed).shuffle(order)

    def hf_only(state: State) -> tuple[Action, ...]:
        available = set(state.available("high"))
        return tuple(Action(candidate, "high") for candidate in order if candidate in available)[:1]

    def screen_then_confirm(state: State) -> tuple[Action, ...]:
        if len(state.low) < 4:
            available = set(state.available("low"))
            return tuple(Action(candidate, "low") for candidate in order if candidate in available)[
                :1
            ]
        available = set(state.available("high"))
        ranked = sorted(state.low, key=lambda item: (-item.label, item.candidate_id))
        return tuple(
            Action(item.candidate_id, "high") for item in ranked if item.candidate_id in available
        )[:1]

    branches: dict[str, object] = {}
    for name, policy in (
        ("hf_only_toy", hf_only),
        ("screen_then_confirm_toy", screen_then_confirm),
    ):
        trace = replay(high, low, Costs(1, 4), 12, policy)
        result = evaluate(trace, high, high_value_fraction=0.25, target_recall=0.5)
        endpoint = result.at_cost(12)
        branches[name] = {
            "spent": trace.events[-1].spent,
            "budget": trace.budget,
            "simple_regret": endpoint.simple_regret,
            "recall": endpoint.recall,
            "cost_to_target": result.cost_to_target,
            "target_censored": result.target_censored,
        }
    return {"evidence_scope": "synthetic-engineering-only", "seed": seed, "branches": branches}


if __name__ == "__main__":
    print(json.dumps(smoke(), sort_keys=True, allow_nan=False))
