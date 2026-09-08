"""Three small validity checks on synthetic GFP identities; no real data."""

from __future__ import annotations

import csv
import gzip
import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from pcdb.m5.proteingym_confirmation import initial_selection
from pcdb.m5.proteingym_runtime import (
    M5CTruthEvaluator,
    build_feature_artifact,
    load_candidate_table,
    load_truth_evaluator,
    select_state,
)

ASSAY = "GFP_AEQVI_Sarkisyan_2016"


def write_csv(
    path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]
) -> dict[str, Any]:
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    return {
        "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "expected_size": path.stat().st_size,
    }


def test_truth_joins_by_id_after_row_shuffle_and_rejects_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from run_m5c_gfp_iteration import validate_membership  # type: ignore[import-not-found]

    mutants = [f"A{i}V" for i in range(1, 5)]
    ids = [hashlib.sha256(f"proteingym:v1.3:{ASSAY}\0{m}".encode()).hexdigest() for m in mutants]
    candidate = tmp_path / "candidates.gz"
    identity = write_csv(
        candidate,
        ["candidate_id", "mutant", "mutated_sequence"],
        zip(ids, mutants, ["ACDE"] * 4, strict=True),
    )
    table = load_candidate_table(candidate, assay_id=ASSAY, **identity)
    truth = tmp_path / "truth.gz"
    identity = write_csv(
        truth, ["candidate_id", "DMS_score"], [(ids[i], 10 * i) for i in [3, 1, 0, 2]]
    )
    evaluator = load_truth_evaluator(truth, candidate_table=table, **identity)
    assert [o.label for o in evaluator.reveal(ids)] == [0, 10, 20, 30]
    identity = write_csv(truth, ["candidate_id", "DMS_score"], [(ids[0], 0)] * 4)
    with pytest.raises(ValueError, match="membership"):
        load_truth_evaluator(truth, candidate_table=table, **identity)
    membership = tmp_path / "membership.gz"
    identity = write_csv(
        membership,
        ["candidate_id", "partition"],
        [(cid, "candidate-pool") for cid in reversed(ids)],
    )
    reference = {
        "sha256": identity["expected_sha256"],
        "size_bytes": identity["expected_size"],
        "row_count": 4,
    }
    validate_membership(membership, reference, tuple(ids))
    identity = write_csv(
        membership, ["candidate_id", "partition"], [(cid, "held-out") for cid in ids]
    )
    reference.update(sha256=identity["expected_sha256"], size_bytes=identity["expected_size"])
    with pytest.raises(ValueError, match="partition"):
        validate_membership(membership, reference, tuple(ids))


@pytest.mark.parametrize("model", ["random-forest-sklearn-v1", "xgboost-cpu-v1"])
def test_unrevealed_truth_cannot_change_selection(tmp_path: Path, model: str) -> None:
    ids = tuple(hashlib.sha256(f"synthetic-gfp-{i}".encode()).hexdigest() for i in range(96))
    sequences = tuple(
        "A" * (i % 7 + 1) + "C" * (i % 5 + 1) + "D" * (i % 3 + 1) + "EFGHIK" for i in range(96)
    )
    feature = build_feature_artifact(sequences, tmp_path / "features.npy")
    initial = initial_selection(ids, assay_id=ASSAY, run_seed=1729, size=32)
    first_truth = np.arange(96, dtype=np.float64)
    second_truth = first_truth.copy()
    for i, cid in enumerate(ids):
        if cid not in initial:
            second_truth[i] = -1000 - i
    outputs = []
    for truth in [first_truth, second_truth]:
        evaluator = M5CTruthEvaluator(ids, truth)
        outputs.append(
            select_state(
                assay_id=ASSAY,
                candidate_ids=ids,
                candidate_pool_sha256="a" * 64,
                features=np.load(feature.path, mmap_mode="r"),
                feature_sha256=feature.sha256,
                initial_sample_size=32,
                model_id=model,
                observations=evaluator.reveal(initial),
                run_seed=1729,
            )
        )
    assert outputs[0] == outputs[1]
    assert len(outputs[0].greedy_ids) == len(set(outputs[0].greedy_ids)) == 16
    assert not set(initial) & set(outputs[0].greedy_ids)
    common = set(outputs[0].greedy_ids) & set(outputs[0].exploration_ids)
    for matched in outputs[0].matched_ids:
        assert len(matched) == len(set(matched)) == 16
        assert set(matched) & set(outputs[0].greedy_ids) == common
        assert not set(initial) & set(matched)


def test_hand_calculated_ties_recall_and_paired_improvement() -> None:
    ids = tuple(hashlib.sha256(f"metric-{i}".encode()).hexdigest() for i in range(100))
    values = np.arange(100, dtype=np.float64)
    values[-2:] = 100.0
    evaluator = M5CTruthEvaluator(ids, values)
    prefix = ids[:32]
    selected = ids[32:47] + (ids[-1],)
    result = evaluator.evaluate_batch(prefix, selected)
    assert cast(dict[str, dict[str, float]], result["high_value"])["0.01"] == {
        "count": 1,
        "recall": 0.5,
    }
    assert result["best_found_improvement"] == result["simple_regret_reduction"] == 69.0
    assert evaluator.definition_summary["0.01"]["actual_count"] == 2
    assert evaluator.evaluate_batch(prefix, selected) == result
