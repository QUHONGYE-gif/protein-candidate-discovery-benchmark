"""M6 内部 synthetic 切片：预算、揭示边界与手算指标。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from pcdb.m6._replay import Action, Costs, State, evaluate, replay

HF = {"a": 1.0, "b": 9.0, "c": 5.0}
LF = {"a": 99.0, "b": -4.0, "c": 0.0}


def test_upgrade_charges_both_fidelities_and_only_hf_counts() -> None:
    batches = iter([(Action("a", "low"),), (Action("a", "high"),)])
    trace = replay(HF, LF, Costs(1, 4), 5, lambda _: next(batches))
    assert [event.spent for event in trace.events] == [1, 5]
    result = evaluate(trace, HF, high_value_fraction=1 / 3, target_recall=1.0)
    assert result.points[1].best_found is None
    assert result.points[1].simple_regret is None
    assert result.points[1].recall == 0.0
    assert result.points[-1].best_found == 1.0
    assert result.points[-1].simple_regret == 8.0
    assert result.cost_to_target is None
    assert result.target_censored


def test_initial_labels_are_paid_and_target_cost_includes_lf() -> None:
    batches = iter([(Action("c", "low"),), (Action("b", "high"),)])
    trace = replay(HF, LF, Costs(1, 4), 9, lambda _: next(batches), initial_high=("a",))
    result = evaluate(trace, HF, high_value_fraction=1 / 3, target_recall=1.0)
    assert [event.spent for event in trace.events] == [4, 5, 9]
    assert result.cost_to_target == 9
    assert not result.target_censored
    assert result.points[-1].simple_regret == 0.0


def test_unaffordable_batch_is_rejected_before_any_reveal(monkeypatch: pytest.MonkeyPatch) -> None:
    from pcdb.replay.oracle import HiddenOracle

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid purchase must not reveal any label")

    monkeypatch.setattr(HiddenOracle, "reveal", forbidden)
    with pytest.raises(ValueError, match="budget"):
        replay(HF, LF, Costs(1, 4), 4, lambda _: (Action("a", "low"), Action("b", "high")))


@pytest.mark.parametrize(
    "batch",
    [
        (Action("absent", "high"),),
        (Action("a", "low"), Action("a", "low")),
    ],
)
def test_invalid_batch_is_atomic(
    batch: tuple[Action, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pcdb.replay.oracle import HiddenOracle

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid purchase must not reveal any label")

    monkeypatch.setattr(HiddenOracle, "reveal", forbidden)
    with pytest.raises(ValueError):
        replay(HF, LF, Costs(1, 4), 8, lambda _: batch)


def test_duplicate_purchase_across_rounds_fails() -> None:
    with pytest.raises(ValueError, match="already purchased"):
        replay(HF, LF, Costs(1, 4), 8, lambda _: (Action("a", "low"),))


def test_unseen_truth_cannot_affect_strategy_state() -> None:
    def run(high: dict[str, float], low: dict[str, float]) -> list[State]:
        seen: list[State] = []

        def policy(state: State) -> tuple[Action, ...]:
            seen.append(state)
            return (Action("a", "low"),) if not state.low else ()

        replay(high, low, Costs(1, 4), 6, policy)
        return seen

    expected = run(HF, LF)
    changed = run({"a": -100.0, "b": 30.0, "c": 40.0}, {"a": 99.0, "b": 999.0, "c": 888.0})
    assert expected == changed
    assert expected[0].high == expected[0].low == ()
    assert expected[1].low[0].label == 99.0
    assert expected[1].high == ()
    assert not hasattr(expected[0], "oracle")
    assert not hasattr(expected[0], "truth")
    with pytest.raises(FrozenInstanceError):
        expected[0].spent = 100  # type: ignore[misc]


def test_ties_and_fixed_budget_carry_forward() -> None:
    high = {"a": 9.0, "b": 9.0, "c": 1.0}
    trace = replay(high, LF, Costs(1, 4), 10, lambda _: (), initial_high=("a",))
    result = evaluate(trace, high, high_value_fraction=1 / 3, target_recall=1.0)
    assert result.points[-1].recall == 0.5
    assert result.at_cost(10).recall == 0.5
    assert result.at_cost(3).recall == 0.0
    assert result.at_cost(4).recall == 0.5
    assert result.target_censored
    with pytest.raises(ValueError):
        result.at_cost(11)


def test_evaluator_rejects_different_truth_even_if_selected_labels_match() -> None:
    trace = replay(HF, LF, Costs(1, 4), 4, lambda _: (), initial_high=("a",))
    with pytest.raises(ValueError, match="identity"):
        evaluate(
            trace, {"a": 1.0, "b": 100.0, "c": 5.0}, high_value_fraction=0.5, target_recall=1.0
        )


@pytest.mark.parametrize("value", [0, -1, True, 1.5, float("nan")])
def test_costs_reject_invalid_units(value: int) -> None:
    with pytest.raises(ValueError):
        Costs(value, 4)


def test_oracle_coverage_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="universe"):
        replay(HF, {"a": 1.0}, Costs(1, 4), 4, lambda _: ())


def test_deterministic_smoke_and_branch_isolation() -> None:
    from pcdb.m6.synthetic_smoke import smoke

    first = smoke(1729)
    assert first == smoke(1729)
    assert first["evidence_scope"] == "synthetic-engineering-only"
    branches = first["branches"]
    assert isinstance(branches, dict)
    assert len(branches) == 2
    assert all(branch["spent"] == 12 for branch in branches.values())


def test_same_candidate_can_be_purchased_at_both_fidelities_in_one_batch() -> None:
    trace = replay(HF, LF, Costs(1, 4), 5, lambda _: (Action("a", "low"), Action("a", "high")))
    assert len(trace.events) == 2
    assert trace.events[-1].spent == 5


def test_branch_replay_does_not_share_reveals() -> None:
    def policy(state: State) -> tuple[Action, ...]:
        assert state.high == ()
        return ()

    first = replay(HF, LF, Costs(1, 4), 4, lambda _: (), initial_high=("a",))
    second = replay(HF, LF, Costs(1, 4), 4, policy)
    assert len(first.events) == 1
    assert second.events == ()


def test_forged_trace_cost_and_label_fail_closed() -> None:
    trace = replay(HF, LF, Costs(1, 4), 4, lambda _: (), initial_high=("a",))
    for event in (replace(trace.events[0], spent=3), replace(trace.events[0], label=99.0)):
        with pytest.raises(ValueError):
            evaluate(
                replace(trace, events=(event,)), HF, high_value_fraction=0.5, target_recall=1.0
            )


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.1, float("nan"), float("inf"), True])
def test_invalid_evaluation_fraction_fails(fraction: float) -> None:
    trace = replay(HF, LF, Costs(1, 4), 4, lambda _: ())
    with pytest.raises(ValueError):
        evaluate(trace, HF, high_value_fraction=fraction, target_recall=1.0)


def test_initial_budget_rejection_precedes_policy() -> None:
    def forbidden(state: State) -> tuple[Action, ...]:
        pytest.fail("invalid initial budget must not invoke policy")

    with pytest.raises(ValueError, match="budget"):
        replay(HF, LF, Costs(1, 4), 3, forbidden, initial_high=("a",))
