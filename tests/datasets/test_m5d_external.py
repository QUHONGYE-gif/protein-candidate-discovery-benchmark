"""Scientific boundary regression tests for the issue-scoped M5D entry point."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pcdb.replay.oracle import Observation


def module() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts/run_m5d_skempi.py"
    spec = importlib.util.spec_from_file_location("m5d_test", path)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_nested_initial_and_overlap_controls() -> None:
    m = module()
    ids = [f"{i:064x}" for i in range(100)]
    assert m.initial(ids, "task", 1729, 32) == m.initial(ids, "task", 1729, 64)[:32]
    e, g = ids[:16], ids[8:24]
    batches = m.controls("state", ids, e, g)
    assert len(batches) == 64
    assert batches == m.controls("state", list(reversed(ids)), e, g)
    for batch in batches:
        assert len(set(batch)) == 16
        assert set(batch) & set(g) == set(e) & set(g)


def test_worker_rejects_unrevealed_label_and_nonfinite_features() -> None:
    m = module()
    ids = [f"{i:064x}" for i in range(80)]
    state = {"complex": "task", "seed": 1729, "initial_size": 32, "model": m.MODELS[0]}
    observations = tuple(Observation(cid, 0) for cid in m.initial(ids, "task", 1729, 32))
    extra = next(cid for cid in ids if cid not in {o.candidate_id for o in observations})
    with pytest.raises(ValueError, match="prefix"):
        m.select((state, ids, np.zeros((80, 41)), (*observations, Observation(extra, 99))))
    matrix = np.zeros((80, 41))
    matrix[40, 0] = np.nan
    with pytest.raises(ValueError, match="feature"):
        m.select((state, ids, matrix, observations))


def test_evaluation_ties_disjointness_and_corrupt_control() -> None:
    m = module()
    ids = [f"{i:064x}" for i in range(80)]
    state = {"complex": "task", "seed": 1729, "initial_size": 32, "model": m.MODELS[0]}
    initial = m.initial(ids, "task", 1729, 32)
    remaining = [cid for cid in ids if cid not in initial]
    e, g = remaining[:16], remaining[8:24]
    selection = {
        "state": state,
        "initial": initial,
        "E": e,
        "G": g,
        "M": m.controls(m.digest(state), remaining, e, g),
    }
    truth = dict.fromkeys(ids, 0.0)
    truth[e[0]] = truth[e[1]] = 1.0
    result = m.evaluate(selection, ids, truth)
    assert result["high_value_counts"]["0.01"] == 2
    assert result["branches"][0]["recall"]["0.01"] == 1
    assert result["branches"][1]["recall"]["0.01"] == 0
    assert result["branches"][0]["best_improvement"] == 1
    selection["M"][0][0] = initial[0]
    with pytest.raises(ValueError, match="controls"):
        m.evaluate(selection, ids, truth)


def test_group_weighting_does_not_count_seeds_as_external_units() -> None:
    m = module()
    rows = []
    # Nine complexes split 6/1/1/1; unweighted-complex estimate would be 2/3.
    for group, count, effect in (("a", 6, 1), ("b", 1, 0), ("c", 1, 0), ("d", 1, 0)):
        for complex_index in range(count):
            for model in m.MODELS:
                for size in (32, 64):
                    for seed in range(1729, 1739):

                        def branch(value: int) -> dict[str, Any]:
                            return {
                                "recall": dict.fromkeys(("0.005", "0.01", "0.02"), value),
                                "best_improvement": value,
                            }

                        rows.append(
                            {
                                "state": {
                                    "group": group,
                                    "complex": f"{group}{complex_index}",
                                    "model": model,
                                    "initial_size": size,
                                    "seed": seed,
                                },
                                "branches": [branch(effect), *[branch(0) for _ in range(65)]],
                            }
                        )
    result = m.summarize(rows)
    for model in result["models"]:
        for key in ("recall_0.01_E-G", "recall_0.01_E-M"):
            assert model["effects"][key]["estimate"] == 0.25
            assert model["effects"][key]["interval"][0] == 0
        assert not model["both_primary_lower_bounds_positive"]


@pytest.mark.parametrize("model_index", [0, 1])
def test_fixed_model_selection_is_deterministic_and_label_scoped(model_index: int) -> None:
    m = module()
    ids = [f"{i:064x}" for i in range(80)]
    state = {"complex": "task", "seed": 1729, "initial_size": 32, "model": m.MODELS[model_index]}
    matrix = np.random.default_rng(123).normal(size=(80, 41))
    observations = tuple(
        Observation(cid, float(i)) for i, cid in enumerate(m.initial(ids, "task", 1729, 32))
    )
    first = m.select((state, ids, matrix, observations))
    second = m.select((state, ids, matrix, observations))
    assert first == second
    for batch in [first["E"], first["G"], *first["M"]]:
        assert len(set(batch)) == 16
        assert not set(batch) & set(first["initial"])
