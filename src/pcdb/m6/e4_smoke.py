"""四条件×四策略的内置 synthetic 演示；不是科学运行或已确定的 surrogate。"""

from __future__ import annotations

import hashlib
import json
import math
from functools import partial
from typing import Any

from pcdb.m6.e4 import Pair, ProxySpec, generate_proxy, paired_summary
from pcdb.m6.policies import STRATEGIES, Scores, make_policy
from pcdb.replay._m6_engine import Costs, Evaluation, State, evaluate, replay


def _toy_scores(state: State, *, prior: dict[str, float]) -> Scores:
    """固定二元高斯 toy 信念；跨条件相同，不读取生成器的真实 correlation。

    单候选先验方差 LF=HF=1、协方差=0.5；已购买 LF 更新对应 HF 均值/方差。
    value 是固定三点幻想下最优后验均值的期望提升，两 fidelity 使用同一单位。
    这不是校准过的 surrogate，也不用于真实数据。
    """
    low = {item.candidate_id: item.label for item in state.low}
    high = {item.candidate_id: item.label for item in state.high}
    mean = {
        candidate: high.get(candidate, prior[candidate] + 0.5 * low.get(candidate, 0.0))
        for candidate in state.candidate_ids
    }
    best = max(mean.values())
    high_value: dict[str, float] = {}
    low_value: dict[str, float] = {}
    for candidate in state.candidate_ids:
        competitor = max(
            (value for other, value in mean.items() if other != candidate), default=mean[candidate]
        )

        def gain(scale: float, competitor: float = competitor, candidate: str = candidate) -> float:
            fantasy = sum(
                weight * max(competitor, mean[candidate] + z * scale)
                for z, weight in ((-math.sqrt(2), 0.25), (0.0, 0.5), (math.sqrt(2), 0.25))
            )
            return max(0.0, fantasy - best)

        high_value[candidate] = (
            0.0 if candidate in high else gain(math.sqrt(0.75 if candidate in low else 1.0))
        )
        low_value[candidate] = 0.0 if candidate in high or candidate in low else gain(0.5)
    return Scores(mean, high_value, low_value)


def smoke() -> dict[str, Any]:
    ids = tuple(f"c{i:02d}" for i in range(12))
    # prior 是独立指定的可观察 toy feature，不由本次 HF/LF 结果估计。
    prior = {candidate: 0.05 * i for i, candidate in enumerate(ids)}
    truth = {candidate: math.sin(i * 0.7) + i * 0.08 for i, candidate in enumerate(ids)}
    scorer = partial(_toy_scores, prior=prior)
    conditions: list[dict[str, Any]] = []
    for name, rho, reverse, low_cost in (
        ("reference", 0.8, False, 1),
        ("lower_correlation", 0.2, False, 1),
        ("high_tail_reversal", 0.8, True, 1),
        ("higher_lf_cost", 0.8, False, 2),
    ):
        evaluations: dict[str, list[Evaluation]] = {strategy: [] for strategy in STRATEGIES}
        runs: list[dict[str, Any]] = []
        for seed in (1729, 1730, 1731):
            proxy = generate_proxy(truth, ProxySpec(rho, 0.25, reverse, seed))
            initial = tuple(
                sorted(
                    ids,
                    key=lambda candidate: hashlib.sha256(
                        f"{seed}:{candidate}".encode()
                    ).hexdigest(),
                )[:2]
            )
            for strategy in STRATEGIES:
                policy = make_policy(
                    strategy, scorer, seed=seed, low_per_high=2, screen_fraction=0.5
                )
                trace = replay(
                    truth, proxy.values, Costs(low_cost, 4), 40, policy, initial_high=initial
                )
                result = evaluate(trace, truth, high_value_fraction=0.25, target_recall=0.5)
                evaluations[strategy].append(result)
                endpoint = result.at_cost(40)
                runs.append(
                    {
                        "seed": seed,
                        "strategy": strategy,
                        "spent": trace.events[-1].spent,
                        "recall": endpoint.recall,
                        "regret": endpoint.simple_regret,
                        "cost_to_target": result.cost_to_target,
                        "target_censored": result.target_censored,
                        "realized_pearson": proxy.pearson,
                        "high_tail_spearman": proxy.high_tail_spearman,
                    }
                )
        contrasts = {
            strategy: paired_summary(
                tuple(
                    Pair("synthetic-task", name, seed, baseline, treatment)
                    for seed, baseline, treatment in zip(
                        (1729, 1730, 1731),
                        evaluations["hf_only"],
                        evaluations[strategy],
                        strict=True,
                    )
                ),
                checkpoint=40,
            )
            for strategy in STRATEGIES
            if strategy != "hf_only"
        }
        conditions.append({"condition": name, "runs": runs, "contrasts": contrasts})
    return {
        "evidence_scope": "synthetic-engineering-only",
        "conditions": conditions,
        "scorer": "fixed-independent-candidate-gaussian-toy-not-formal-surrogate",
    }


if __name__ == "__main__":
    print(json.dumps(smoke(), sort_keys=True, allow_nan=False))
