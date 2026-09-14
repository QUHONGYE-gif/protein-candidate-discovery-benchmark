"""固定预测重排的身份隔离、端点复现与组级判定回归。"""

from __future__ import annotations

import copy
import importlib
from pathlib import Path
from typing import Any

import pytest


def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("run_m5d_beta_iteration")


def fixture(m: Any) -> tuple[Any, Any, Any]:
    pool = [f"{i:064x}" for i in range(80)]
    state = {
        "complex": "1ABC_A_B",
        "group": "g",
        "pool_sha256": m.base.digest(pool),
        "model": m.base.MODELS[1],
        "initial_size": 32,
        "seed": 1729,
    }
    initial = m.base.initial(pool, state["complex"], 1729, 32)
    ids = [cid for cid in pool if cid not in initial]
    mean = [float(i) for i in range(len(ids))]
    sigma = [float(2 * (len(ids) - i)) for i in range(len(ids))]
    return (
        state,
        pool,
        {
            "ids": ids,
            "mean": mean,
            "sigma": sigma,
            "acquisition": [a + b for a, b in zip(mean, sigma, strict=True)],
        },
    )


def test_endpoints_and_ties_preserve_expected_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    state, pool, p = fixture(m)
    zero = m.select(state, pool, p, 0)
    one = m.select(state, pool, p, 1)
    assert zero["E"] == zero["G"] == list(reversed(p["ids"]))[:16]
    assert one["E"] == p["ids"][:16]
    assert m.select(state, pool, p, 0.5)["E"] == sorted(p["ids"])[:16]
    assert one == m.select(state, pool, copy.deepcopy(p), 1)
    truth = {cid: float(i) for i, cid in enumerate(pool)}
    m.base.evaluate(one, pool, truth)


@pytest.mark.parametrize("change", ["label", "order", "negative_sigma", "acquisition"])
def test_invalid_or_hidden_prediction_inputs_fail_closed(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    m = module(monkeypatch)
    state, pool, p = fixture(m)
    if change == "label":
        p["hidden_truth"] = [1.0] * len(p["ids"])
    elif change == "order":
        p["ids"] = list(reversed(p["ids"]))
    elif change == "negative_sigma":
        p["sigma"][0] = -1
    else:
        p["acquisition"][0] += 1
    with pytest.raises(ValueError):
        m.select(state, pool, p, 1)


def test_saved_selection_tamper_rejected_before_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    state, pool, p = fixture(m)
    original = {"prediction": p, "selection": m.select(state, pool, p, 1)}
    source = {m.base.digest(state): original}
    cohort = [{"complex": state["complex"], "ids": pool}]
    records = m.generate(cohort, [state], source)
    records[2]["selection"]["E"] = list(reversed(records[2]["selection"]["E"]))
    with pytest.raises(ValueError, match="重建"):
        m.evaluate(records, cohort, [state], source, {})


def test_group_equal_weighting_and_conservative_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    rows = []
    for beta in m.BETAS:
        for g, count in (("g1", 1), ("g2", 3), ("g3", 1), ("g4", 1)):
            for i in range(count):
                effect = 0.04 if g == "g1" and beta == 0.5 else 0.0
                values = {
                    "recall_0.005": effect,
                    "recall_0.01": effect,
                    "recall_0.02": effect,
                    "best": 0.0,
                }
                rows.append(
                    {
                        "beta": beta,
                        "metrics": {
                            "state": {"group": g, "complex": f"{g}-{i}", "initial_size": 32}
                        },
                        "values": values,
                    }
                )
    r = m.rollup(rows)["results"][2]
    assert r["delta_vs_beta"]["0"]["recall_0.01"]["estimate"] == pytest.approx(0.01)
    assert r["decision"] == "unstable"
    assert m.classify(0.01, 0.01, [0.01] * 4, 0, 0) == "positive"
    assert m.classify(0.01, 0.01, [0.04, 0.04, 0.04, -0.08], 0, 0) == "unstable"
    assert m.classify(-0.01, 0, [0] * 4, 0, 0) == "negative"
    assert m.ratio(1, 0) is None


def test_full_requires_verified_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = module(monkeypatch)
    with pytest.raises(ValueError, match="smoke"):
        m.require_smoke(tmp_path, "head", "contract")
    root = tmp_path / "smoke-001"
    root.mkdir()
    m.base.write(root / "report.json", {})
    m.base.write(
        root / "terminal.json",
        {
            "mode": "smoke",
            "status": "succeeded",
            "executed_git_commit": "head",
            "config_identity": "contract",
            "source_terminal_sha256": m.PARENT,
            "artifacts": {"report.json": "incorrect"},
        },
    )
    with pytest.raises(ValueError, match="哈希"):
        m.require_smoke(tmp_path, "head", "contract")
