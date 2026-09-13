"""E4 内部工程切片：可控输入、四策略和不泄漏的配对评价。"""

from __future__ import annotations

import math

import pytest

from pcdb.m6.e4 import Pair, ProxySpec, generate_proxy, paired_summary
from pcdb.m6.policies import Scores, make_policy
from pcdb.replay._m6_engine import Action, Costs, State, evaluate, replay
from pcdb.replay.oracle import Observation


def test_proxy_has_requested_pearson_before_bias_and_is_order_invariant() -> None:
    truth = {f"c{i}": float(i) for i in range(20)}
    spec = ProxySpec(0.6, 0.2, False, 1729)
    a = generate_proxy(truth, spec)
    b = generate_proxy(dict(reversed(list(truth.items()))), spec)
    assert a == b
    assert a.pearson == pytest.approx(0.6)
    assert a.values != truth


def test_high_tail_reversal_retains_non_tail_values() -> None:
    truth = {f"c{i}": float(i) for i in range(20)}
    baseline = generate_proxy(truth, ProxySpec(1.0, 0.2, False, 1729))
    reversed_tail = generate_proxy(truth, ProxySpec(1.0, 0.2, True, 1729))
    for i in range(16):
        assert baseline.values[f"c{i}"] == reversed_tail.values[f"c{i}"]
    assert reversed_tail.high_tail_spearman == pytest.approx(-1.0)
    assert baseline.high_tail_spearman == pytest.approx(1.0)


@pytest.mark.parametrize("rho", [-1.1, 1.1, math.nan, True])
def test_invalid_proxy_parameter_fails(rho: float) -> None:
    with pytest.raises(ValueError):
        ProxySpec(rho, 0.2, False, 1)


def test_constant_truth_has_no_fake_correlation() -> None:
    with pytest.raises(ValueError, match="constant"):
        generate_proxy({"a": 1.0, "b": 1.0, "c": 1.0}, ProxySpec(0.5, 0.2, False, 1))


def _scores(state: State) -> Scores:
    return Scores(
        mean={candidate: float(i) for i, candidate in enumerate(state.candidate_ids)},
        high_value={candidate: 4.0 for candidate in state.candidate_ids},
        low_value={candidate: 2.0 for candidate in state.candidate_ids},
    )


def test_joint_selection_compares_information_value_per_cost() -> None:
    policy = make_policy("cost_aware", _scores, seed=1, low_per_high=2, screen_fraction=0.5)
    state = State(("a", "b"), (), (), Costs(1, 4), 0, 8)
    assert policy(state)[0].fidelity == "low"
    expensive = State(("a", "b"), (), (), Costs(3, 4), 0, 8)
    assert policy(expensive)[0].fidelity == "high"


def test_hf_only_never_buys_lf() -> None:
    policy = make_policy("hf_only", _scores, seed=1, low_per_high=2, screen_fraction=0.5)
    result = replay({"a": 1.0, "b": 2.0}, {"a": 3.0, "b": 4.0}, Costs(1, 4), 8, policy)
    assert all(event.action.fidelity == "high" for event in result.events)


def test_fixed_ratio_is_a_purchase_cadence_not_an_unstated_cost_ratio() -> None:
    policy = make_policy("fixed_ratio", _scores, seed=1, low_per_high=2, screen_fraction=0.5)
    truth = {str(i): float(i) for i in range(5)}
    trace = replay(truth, truth, Costs(1, 4), 6, policy)
    assert [event.action.fidelity for event in trace.events] == ["low", "low", "high"]


def test_prescreen_requires_complete_paid_screen_and_keeps_frozen_subset() -> None:
    policy = make_policy("hard_prescreen", _scores, seed=1, low_per_high=2, screen_fraction=0.5)
    ids = ("a", "b", "c", "d")
    state = State(ids, (), (), Costs(1, 4), 0, 5)
    with pytest.raises(ValueError, match="screen budget"):
        policy(state)
    low = tuple(Observation(k, v) for k, v in zip(ids, [4.0, 3.0, 2.0, 1.0], strict=True))
    state = State(ids, low, (Observation("a", 9.0),), Costs(1, 4), 8, 20)
    assert policy(state) == (Action("b", "high"),)
    state = State(ids, low, (Observation("a", 9.0), Observation("b", 9.0)), Costs(1, 4), 12, 20)
    assert policy(state) == ()


def test_scorer_only_receives_state_and_invalid_scores_fail_closed() -> None:
    states: list[State] = []

    def score(state: State) -> Scores:
        states.append(state)
        return Scores({"a": 0.0}, {"a": 1.0}, {"a": 1.0})

    policy = make_policy("cost_aware", score, seed=1, low_per_high=2, screen_fraction=0.5)
    with pytest.raises(ValueError, match="coverage"):
        policy(State(("a", "b"), (), (), Costs(1, 4), 0, 8))
    assert states[0].low == states[0].high == ()


def test_paired_summary_does_not_turn_censoring_into_zero_savings() -> None:
    truth = {"a": 0.0, "b": 1.0}
    baseline = evaluate(
        replay(truth, truth, Costs(1, 4), 8, lambda _: (), initial_high=("b",)),
        truth,
        high_value_fraction=0.5,
        target_recall=1.0,
    )
    treatment = evaluate(
        replay(truth, truth, Costs(1, 4), 8, lambda _: (), initial_high=("a",)),
        truth,
        high_value_fraction=0.5,
        target_recall=1.0,
    )
    summary = paired_summary((Pair("task", "condition", 1, baseline, treatment),), checkpoint=8)
    assert summary["negative_recall_fraction"] == 1.0
    assert summary["mean_savings_both_reached"] is None
    assert summary["both_reached"] == 0
    assert summary["treatment_censored"] == 1
    assert summary["mean_delta_recall"] == -1.0


def test_four_strategy_smoke_is_deterministic_and_complete() -> None:
    from pcdb.m6.e4_smoke import smoke

    a = smoke()
    assert a == smoke()
    assert a["evidence_scope"] == "synthetic-engineering-only"
    assert len(a["conditions"]) == 4


def test_target_reached_after_checkpoint_is_censored_at_checkpoint() -> None:
    truth = {"a": 0.0, "b": 1.0}
    batches = iter([(Action("a", "high"),), (Action("b", "high"),)])
    result = evaluate(
        replay(truth, truth, Costs(1, 4), 8, lambda _: next(batches)),
        truth,
        high_value_fraction=0.5,
        target_recall=1.0,
    )
    summary = paired_summary((Pair("task", "condition", 1, result, result),), checkpoint=4)
    assert summary["both_reached"] == 0
    assert summary["baseline_censored"] == 1
    assert summary["mean_savings_both_reached"] is None


def test_summary_rejects_cross_task_and_duplicate_seeds() -> None:
    truth = {"a": 0.0, "b": 1.0}
    result = evaluate(
        replay(truth, truth, Costs(1, 4), 8, lambda _: ()),
        truth,
        high_value_fraction=0.5,
        target_recall=1.0,
    )
    a = Pair("task", "condition", 1, result, result)
    for b in (
        a,
        Pair("other", "condition", 2, result, result),
        Pair("task", "other", 2, result, result),
    ):
        with pytest.raises(ValueError):
            paired_summary((a, b), checkpoint=8)


@pytest.mark.parametrize("strategy", ["hf_only", "fixed_ratio", "hard_prescreen", "cost_aware"])
def test_policy_choices_do_not_depend_on_unpurchased_truth(strategy: str) -> None:
    from typing import cast

    from pcdb.m6.policies import Strategy

    def run(truth: dict[str, float]) -> Action:
        policy = make_policy(
            cast(Strategy, strategy), _scores, seed=1, low_per_high=2, screen_fraction=0.5
        )
        trace = replay(truth, truth, Costs(1, 4), 12, policy)
        return trace.events[0].action

    assert run({"a": 1.0, "b": 2.0, "c": 3.0}) == run({"a": 99.0, "b": -3.0, "c": -1.0})


def test_fixed_ratio_is_deterministic_from_paid_state() -> None:
    policy = make_policy("fixed_ratio", _scores, seed=1, low_per_high=2, screen_fraction=0.5)
    ids = tuple(str(i) for i in range(8))
    truth = {candidate: float(i) for i, candidate in enumerate(ids)}
    trace = replay(truth, truth, Costs(1, 4), 16, policy, initial_high=("7",))
    assert [event.action.fidelity for event in trace.events] == [
        "high",
        "low",
        "low",
        "high",
        "low",
        "low",
        "high",
    ]


def test_scorer_nonfinite_and_negative_information_fail() -> None:
    for value in (math.nan, -1.0):
        with pytest.raises(ValueError):
            Scores({"a": 0.0}, {"a": value}, {"a": 1.0}).validate(("a",))
