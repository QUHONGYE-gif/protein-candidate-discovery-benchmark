"""E4 warmup 与四策略成本语义。"""

from pcdb.m6.e4_policy import make_e4_policy
from pcdb.m6.policies import Scores
from pcdb.replay._m6_engine import Action, Costs, State, replay


def scores(state: State) -> Scores:
    means = {c: float(i) for i, c in enumerate(state.candidate_ids)}
    zeros = dict.fromkeys(state.candidate_ids, 0.0)
    return Scores(means, zeros, zeros)


def test_warmup_is_paid_once_and_excluded_from_cadence() -> None:
    truth = {f"c{i:02}": float(i) for i in range(16)}
    policy = make_e4_policy("fixed_ratio", scores, seed=1, initial_ids=("c00", "c01"))
    trace = replay(truth, truth, Costs(1, 100), 507, policy, initial_high=("c00", "c01"))
    assert [e.action.fidelity for e in trace.events[:7]] == [
        "high",
        "high",
        "low",
        "low",
        "low",
        "low",
        "high",
    ]
    assert trace.events[3].spent == 202
    assert len({e.action for e in trace.events}) == len(trace.events)
    assert trace.events[-1].spent <= 507


def test_zero_value_falls_back_to_hf_greedy_and_hidden_truth_cannot_steer_it() -> None:
    t = {f"c{i}": float(i) for i in range(8)}
    p = make_e4_policy("cost_aware", scores, seed=1, initial_ids=("c0", "c1"))
    a = replay(t, t, Costs(1, 100), 302, p, initial_high=("c0", "c1"))
    other = dict(t, c7=-1000.0)
    b = replay(other, other, Costs(1, 100), 302, p, initial_high=("c0", "c1"))
    assert [e.action for e in a.events] == [e.action for e in b.events]
    assert a.events[-1].action == Action("c7", "high")


def test_prescreen_includes_warmup_without_double_charge() -> None:
    t = {f"c{i:02}": float(i) for i in range(16)}
    p = make_e4_policy("hard_prescreen", scores, seed=1, initial_ids=("c00", "c01"))
    a = replay(t, t, Costs(4, 100), 500, p, initial_high=("c00", "c01"))
    low = [e for e in a.events if e.action.fidelity == "low"]
    assert len(low) == 16
    assert a.events[17].spent == 264
    assert a.events[18].action == Action("c15", "high")
