"""位点分层的标签隔离、覆盖守恒、共同支持与权重回归。"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest


def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("run_m5d_site_coverage")


def fixture() -> tuple[Any, Any]:
    r = {
        "state": {"complex": "C", "initial_size": 3},
        "fold_index": 0,
        "train_ids": ["t"],
        "validation_ids": ["a", "b", "c"],
    }
    sites = {
        "C": {
            "t": {"sites": ["A10"], "mutation_count": 1},
            "a": {"sites": ["A10"], "mutation_count": 1},
            "b": {"sites": ["A10", "A11"], "mutation_count": 2},
            "c": {"sites": ["B10"], "mutation_count": 1},
        }
    }
    return r, sites


def test_chain_specific_partial_coverage_and_no_validation_self_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = module(monkeypatch)
    r, sites = fixture()
    masks = m.build_masks([r], sites)
    assert [v["stratum"] for v in masks] == ["all_seen", "any_unseen", "any_unseen"]
    assert [v["coverage"] for v in masks] == ["all_seen", "partial_seen", "none_seen"]
    assert [v["seen_fraction"] for v in masks] == [1, 0.5, 0]


@pytest.mark.parametrize("kind", ["label", "prediction", "overlap"])
def test_masks_reject_outcomes_and_fold_overlap(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    m = module(monkeypatch)
    r, sites = fixture()
    if kind == "overlap":
        r["train_ids"].append("a")
    else:
        r[kind] = [1, 2, 3]
    with pytest.raises(ValueError):
        m.build_masks([r], sites)


def test_small_or_empty_strata_are_not_zero_correlation(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    assert m.subset_metrics([])["mae"] is None
    rr = [{"target": i, "mean": 0, "sigma": i} for i in (1, 2, 3)]
    assert m.subset_metrics(rr)["mae"] == 2
    assert m.subset_metrics(rr)["sigma_error_rho"] is None


def test_unrevealed_truth_cannot_change_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    r, sites = fixture()
    masks = m.build_masks([r], sites)
    record = {**r, "mean": [0, 1, 2], "sigma": [1, 1, 1]}
    a = m.evaluate([r["state"]], [record], masks, {"a": 1, "b": 2, "c": 3, "unrevealed": 4})
    b = m.evaluate([r["state"]], [record], masks, {"a": 1, "b": 2, "c": 3, "unrevealed": 9999})
    assert a == b
    assert a[1][0]["contrast"] is None
    with pytest.raises(ValueError, match="对齐"):
        m.evaluate([r["state"]], [record], list(reversed(masks)), {})


def test_initial_sizes_equal_weight_even_when_some_strata_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = module(monkeypatch)
    rows = [
        {"state": {"initial_size": 32}, "v": 1},
        {"state": {"initial_size": 32}, "v": None},
        {"state": {"initial_size": 64}, "v": 3},
        {"state": {"initial_size": 64}, "v": 3},
    ]
    assert m.state_average(rows, lambda r: r["v"]) == 2


def test_unsupported_targets_remain_inconclusive_and_groups_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = module(monkeypatch)
    rows = []
    empty = m.subset_metrics([])
    for name in m.TARGETS:
        for subset in ("all", "single_mutation"):
            for seed in range(20):
                paired = name == "1A22_A_B" and seed < 2
                contrast = {k: 1.5 for k in m.CONTRASTS} if paired else None
                rows.append(
                    {
                        "state": {
                            "complex": name,
                            "group": name,
                            "initial_size": 32 if seed < 10 else 64,
                        },
                        "subset": subset,
                        "strata": {s: empty for s in m.STRATA},
                        "paired": paired,
                        "contrast": contrast,
                        "coverage_counts": {"all_seen": 0, "partial_seen": 0, "none_seen": 32},
                    }
                )
    r = m.summary(rows)
    assert r["decision"] == "inconclusive"
    assert all(g["contrast"]["mae_delta"] is None for g in r["groups"])
    assert r["supported_groups_only"][0]["eligible_groups"] == 0


def test_pool_singletons_separate_structural_absence_from_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = module(monkeypatch)
    _, sites = fixture()
    r = m.pool_audit(sites)[0]
    assert r["candidates"] == 4
    assert r["distinct_sites"] == 3
    assert r["singleton_sites"] == 2
    assert r["unavoidable_unseen_candidates"] == 2
