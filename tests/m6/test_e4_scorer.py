"""真实 E4 scorer 的同单位信息价值、状态重建与付费边界。"""

from dataclasses import replace

import numpy as np
import pytest

from pcdb.m6.e4_scorer import RFScorer, information_value
from pcdb.replay._m6_engine import Costs, State
from pcdb.replay.oracle import Observation


def test_three_point_value_hand_calculation() -> None:
    h = np.array([[0.0, 2.0], [2.0, 0.0]])
    # mean=1, covariance=+/-1，epsilon=sqrt(3)，beta=+/-0.5。
    value = information_value(h, h, np.array([True, True]), 0.0, 3.0**0.5)
    np.testing.assert_allclose(value, [2.0**0.5 / 4, 2.0**0.5 / 4])
    np.testing.assert_array_equal(information_value(h, h * 0, np.ones(2, bool), 0.0, 1.0), 0)


def _state() -> State:
    return State(
        tuple(f"c{i}" for i in range(8)),
        (Observation("c0", 3.0), Observation("c1", 1.0)),
        (Observation("c0", 0.0), Observation("c1", 2.0)),
        Costs(1, 100),
        202,
        6400,
    )


def test_rf_paid_override_and_rebuild_are_exact() -> None:
    state = _state()
    x = np.random.default_rng(1).normal(size=(8, 420))
    scorer = RFScorer(
        state.candidate_ids,
        x,
        task="test",
        seed=1729,
        initial_ids=("c0", "c1"),
        warmup_ids=("c0", "c1"),
    )
    h, lf_samples = scorer.samples(state)
    np.testing.assert_array_equal(h[:, :2], np.tile([0.0, 2.0], (5, 1)))
    np.testing.assert_array_equal(lf_samples[:, :2], np.tile([3.0, 1.0], (5, 1)))
    rebuilt = replace(state, high=tuple(reversed(state.high)), low=tuple(reversed(state.low)))
    assert scorer(state) == scorer(rebuilt)
    # 调用者更改原 X 不得改变 scorer 的输入。
    before = scorer(state)
    x[:] = 999
    assert scorer(state) == before


def test_nonfinite_features_and_unknown_observations_fail_closed() -> None:
    s = _state()
    with pytest.raises(ValueError):
        RFScorer(
            s.candidate_ids,
            np.full((8, 420), np.nan),
            task="t",
            seed=1,
            initial_ids=("c0", "c1"),
            warmup_ids=(),
        )
    scorer = RFScorer(
        s.candidate_ids,
        np.zeros((8, 420)),
        task="t",
        seed=1,
        initial_ids=("c0", "c1"),
        warmup_ids=(),
    )
    with pytest.raises(ValueError):
        scorer(replace(s, high=s.high + (Observation("outside", 1.0),)))


def test_hf_observation_changes_do_not_train_lf_model() -> None:
    state = _state()
    scorer = RFScorer(
        state.candidate_ids,
        np.random.default_rng(7).normal(size=(8, 420)),
        task="test",
        seed=1729,
        initial_ids=("c0", "c1"),
        warmup_ids=("c0", "c1"),
    )
    _, before = scorer.samples(state)
    _, after = scorer.samples(
        replace(state, high=(Observation("c0", -100.0), Observation("c1", 100.0)))
    )
    np.testing.assert_array_equal(before, after)


def test_threaded_members_preserve_exact_predictions_scores_and_action() -> None:
    from pcdb.m6.e4_policy import make_e4_policy

    state = _state()
    features = np.random.default_rng(27).normal(size=(8, 420))
    serial = RFScorer(
        state.candidate_ids,
        features,
        task="t",
        seed=1729,
        initial_ids=("c0", "c1"),
        warmup_ids=("c0", "c1"),
    )
    parallel = RFScorer(
        state.candidate_ids,
        features,
        task="t",
        seed=1729,
        initial_ids=("c0", "c1"),
        warmup_ids=("c0", "c1"),
        member_jobs=2,
    )
    for first, second in zip(serial.samples(state), parallel.samples(state), strict=True):
        np.testing.assert_array_equal(first, second)
    assert serial(state) == parallel(state)
    a = make_e4_policy("cost_aware", serial, seed=1729, initial_ids=("c0", "c1"))
    b = make_e4_policy("cost_aware", parallel, seed=1729, initial_ids=("c0", "c1"))
    assert a(state) == b(state)


@pytest.mark.parametrize("jobs", [0, -1, 6, True])
def test_invalid_thread_count_fails_closed(jobs: int) -> None:
    with pytest.raises(ValueError, match="member_jobs"):
        RFScorer(
            ("c0", "c1"),
            np.zeros((2, 420)),
            task="t",
            seed=1,
            initial_ids=("c0",),
            warmup_ids=(),
            member_jobs=jobs,
        )
