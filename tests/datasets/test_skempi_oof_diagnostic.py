"""交叉拟合的标签隔离、折覆盖与保存预测重建。"""

from __future__ import annotations

import copy
import importlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pcdb.replay.oracle import Observation


def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("run_m5d_oof_diagnostic")


def fixture(m: Any) -> tuple[Any, Any, Any, Any]:
    pool = [f"{i:064x}" for i in range(80)]
    state = {
        "complex": "1ABC_A_B",
        "group": "g",
        "pool_sha256": m.base.digest(pool),
        "model": m.base.MODELS[1],
        "initial_size": 32,
        "seed": 1729,
    }
    matrix = [[i, i % 7, i % 3] for i in range(80)]
    truth = {cid: float(i) / 80 for i, cid in enumerate(pool)}
    return state, pool, matrix, truth


def task(m: Any, state: Any, pool: Any, matrix: Any, truth: Any, fold: int) -> Any:
    partition = m.folds(state, pool)
    train = sorted(cid for i, ids in enumerate(partition) if i != fold for cid in ids)
    return {
        "state": state,
        "pool": pool,
        "matrix": matrix,
        "fold_index": fold,
        "observations": tuple(Observation(cid, truth[cid]) for cid in train),
    }


def test_folds_cover_initial_once_and_ignore_pool_order(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    state, pool, _, _ = fixture(m)
    f = m.folds(state, pool)
    assert list(map(len, f)) == [8] * 4
    assert set.union(*(set(v) for v in f)) == set(m.base.initial(pool, state["complex"], 1729, 32))
    assert sum(map(len, f)) == len(set(cid for v in f for cid in v))
    assert f == m.folds(state, list(reversed(pool)))


@pytest.mark.parametrize("kind", ["validation", "remaining", "field"])
def test_forbidden_labels_fail_closed(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    m = module(monkeypatch)
    state, pool, matrix, truth = fixture(m)
    t = task(m, state, pool, matrix, truth, 0)
    if kind == "field":
        t["hidden_truth"] = truth
    else:
        initial = set(cid for f in m.folds(state, pool) for cid in f)
        cid = (
            m.folds(state, pool)[0][0]
            if kind == "validation"
            else next(cid for cid in pool if cid not in initial)
        )
        t["observations"] += (Observation(cid, truth[cid]),)
    with pytest.raises(ValueError):
        m.train_predict(t)


def test_actual_fit_receives_only_training_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    state, pool, matrix, truth = fixture(m)
    t = task(m, state, pool, matrix, truth, 0)
    allowed = {o.label for o in t["observations"]}
    seen = []

    class Spy:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def fit(self, x: Any, y: Any) -> None:
            assert set(y) <= allowed
            assert len(y) == 24
            seen.append(list(y))
            self.mean = float(np.mean(y))

        def predict(self, x: Any) -> Any:
            return np.full(len(x), self.mean)

    monkeypatch.setattr(m, "XGBRegressor", Spy)
    r = m.train_predict(t)
    assert len(seen) == 6
    assert len(r["members"]) == 5
    assert not set(r["train_ids"]) & set(r["validation_ids"])


def test_real_predictions_rebuild_and_ignore_unrevealed_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = module(monkeypatch)
    state, pool, matrix, truth = fixture(m)
    records = [m.train_predict(task(m, state, pool, matrix, truth, f)) for f in range(4)]
    assert records[0] == m.train_predict(task(m, state, pool, matrix, truth, 0))
    cohort, source = [{"complex": state["complex"], "ids": pool}], {state["complex"]: matrix}
    rows = m.evaluate(records, cohort, [state], source, truth)
    initial = set(cid for r in records for cid in r["validation_ids"])
    changed = {cid: value if cid in initial else 99999 for cid, value in truth.items()}
    assert rows == m.evaluate(records, cohort, [state], source, changed)
    bad = copy.deepcopy(records)
    bad[0]["members"][0][0] += 1
    with pytest.raises(ValueError, match="方差"):
        m.evaluate(bad, cohort, [state], source, truth)
    with pytest.raises(ValueError, match="覆盖"):
        m.evaluate(records[:-1], cohort, [state], source, truth)


def test_constant_sigma_is_undefined_not_false_enrichment(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    d = m.metrics([0, 0, 0, 0], [1, 1, 1, 1], [0, 1, 2, 3], ["a", "b", "c", "d"])
    assert d["sigma_error_spearman"] is None
    assert d["high_sigma_lift"] is None
    d = m.metrics([0, 0, 0, 0], [0, 1, 2, 3], [0, 1, 2, 3], ["a", "b", "c", "d"])
    assert d["sigma_error_spearman"] == pytest.approx(1)
    assert d["high_sigma_lift"] == pytest.approx(2)


def test_group_weighting_and_defined_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    rows = []
    for g, count in (("g1", 1), ("g2", 3), ("g3", 1), ("g4", 1)):
        for i in range(count):
            values = {
                "sigma_error_spearman": 0.4 if g == "g1" else 0.2,
                "within_fold_spearman": 0.1,
                "high_sigma_lift": 1.3,
                "mae": 1.0,
            }
            rows.append(
                {"state": {"group": g, "complex": f"{g}-{i}", "initial_size": 32}, "values": values}
            )
    r = m.rollup(rows)
    assert r["values"]["sigma_error_spearman"] == pytest.approx(0.25)
    assert r["decision"] == "positive"
    assert m.classify(r["values"], r["groups"], [0.89]) == "inconclusive"
    r["values"]["sigma_error_spearman"] = -0.1
    r["values"]["high_sigma_lift"] = 0.9
    assert m.classify(r["values"], r["groups"], [1]) == "negative"
