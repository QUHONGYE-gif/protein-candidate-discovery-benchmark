"""开发特征与模型/评价边界的针对性回归。"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pcdb.datasets.skempi_iteration import ARMS, build_features
from pcdb.datasets.skempi_readiness import ObservableCandidate
from pcdb.replay.oracle import Observation


def candidate(cid: str, tokens: tuple[str, ...]) -> ObservableCandidate:
    return ObservableCandidate(cid, "1ABC_A_B", tokens, tokens)


def test_pair_features_restore_lost_substitution_pairing() -> None:
    candidates = [candidate("x", ("AA1G", "CA2V")), candidate("y", ("AA1V", "CA2G"))]
    residues = {("1ABC", "A", "1"): "A", ("1ABC", "A", "2"): "C"}
    features = build_features(candidates, residues)
    assert features.matrices["F0"][0] == features.matrices["F0"][1]
    assert features.matrices["F1"][0] != features.matrices["F1"][1]
    for arm in ARMS:
        assert len(features.matrices[arm][0]) == len(features.columns[arm])


def test_site_features_separate_positions_and_vocabulary_is_order_invariant() -> None:
    candidates = [candidate("x", ("AA1G",)), candidate("y", ("AA9G",))]
    residues = {("1ABC", "A", "1"): "A", ("1ABC", "A", "9"): "A"}
    first = build_features(candidates, residues)
    second = build_features(list(reversed(candidates)), residues)
    assert first.columns == second.columns
    assert first.matrices["F1"][0] == first.matrices["F1"][1]
    assert first.matrices["F2"][0] != first.matrices["F2"][1]
    for arm in ARMS:
        assert first.matrices[arm] == tuple(reversed(second.matrices[arm]))


def test_context_missing_neighbors_explicit_and_center_mismatch_fails() -> None:
    c = [candidate("x", ("AA1G",))]
    f = build_features(c, {("1ABC", "A", "1"): "A"})
    assert (f.center_count, f.neighbor_count, f.neighbor_present) == (1, 4, 0)
    row = f.matrices["F3"][0]
    assert sum(row[f.columns["F3"].index(f"context:{offset}:?")] for offset in (-2, -1, 1, 2)) == 4
    with pytest.raises(ValueError, match="中心"):
        build_features(c, {("1ABC", "A", "1"): "C"})
    with pytest.raises(ValueError, match="跨复合物"):
        build_features([*c, ObservableCandidate("y", "2ABC_A_B", ("AA1G",), ("AA1G",))], {})


def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("run_m5d_feature_iteration")


@pytest.mark.parametrize("model_index", [0, 1])
def test_baseline_exact_reproduction_and_prediction_readback(
    monkeypatch: pytest.MonkeyPatch, model_index: int
) -> None:
    m = module(monkeypatch)
    ids = [f"{i:064x}" for i in range(80)]
    state = {
        "complex": "task",
        "model": m.base.MODELS[model_index],
        "seed": 1729,
        "initial_size": 32,
    }
    features = np.random.default_rng(123).normal(size=(80, 41))
    observations = tuple(
        Observation(cid, float(i)) for i, cid in enumerate(m.base.initial(ids, "task", 1729, 32))
    )
    task = (state, ids, features, observations)
    result = m.select(task)
    assert result["selection"] == m.base.select(task)
    truth = dict.fromkeys(ids, 1.0)
    assert m.diagnostic(result, truth)["prediction_spearman"] is None
    result["prediction"]["acquisition"][0] += 1
    with pytest.raises(ValueError, match="采集分数"):
        m.diagnostic(result, truth)


def test_extra_unrevealed_labels_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    ids = [f"{i:064x}" for i in range(80)]
    state = {"complex": "task", "model": m.base.MODELS[0], "seed": 1729, "initial_size": 32}
    observations = tuple(Observation(cid, 0) for cid in ids)
    with pytest.raises(ValueError, match="prefix"):
        m.select((state, ids, np.zeros((80, 441)), observations))
    assert m.correlation([1, 1, 2], [2, 2, 4]) == pytest.approx(1)
    assert m.correlation([1, 1], [2, 3]) is None


def test_rollup_preserves_group_weight_and_positive_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    m = module(monkeypatch)
    rows = []
    for group in range(4):
        for arm in ARMS:
            for model in m.base.MODELS:
                for size in (32, 64):
                    for seed in range(1729, 1739):
                        gain = 0.1 if arm != "F0" else 0
                        branch = {
                            "recall": dict.fromkeys(("0.005", "0.01", "0.02"), gain),
                            "best_improvement": gain,
                        }
                        rows.append(
                            {
                                "arm": arm,
                                "metrics": {
                                    "state": {
                                        "model": model,
                                        "group": str(group),
                                        "complex": str(group),
                                        "initial_size": size,
                                        "seed": seed,
                                    },
                                    "branches": [branch, branch, *[branch for _ in range(64)]],
                                },
                                "diagnostic": {"mae": 1, "sigma_error_spearman": None},
                                "top1_exhausted": False,
                            }
                        )
    report = m.rollup(rows)
    for result in report["results"]:
        assert result["decision"] == ("inconclusive" if result["arm"] == "F0" else "positive")
        assert result["delta_vs_F0"]["E_recall_0.01"]["estimate"] == pytest.approx(
            0 if result["arm"] == "F0" else 0.1
        )


def test_full_requires_same_source_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = module(monkeypatch)
    with pytest.raises(ValueError, match="smoke"):
        m.require_smoke(tmp_path, "commit", [])
    root = tmp_path / "smoke-001"
    root.mkdir()
    m.base.write(root / "feature-audit.json", [])
    m.base.write(
        root / "terminal.json",
        {
            "mode": "smoke",
            "status": "succeeded",
            "executed_git_commit": "commit",
            "artifacts": {"feature-audit.json": m.base.identity(root / "feature-audit.json")},
        },
    )
    m.require_smoke(tmp_path, "commit", [])
    with pytest.raises(ValueError, match="smoke"):
        m.require_smoke(tmp_path, "other", [])
