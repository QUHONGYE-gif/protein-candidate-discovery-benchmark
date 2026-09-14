"""The explicit iteration cohort preserves scientific and formal boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from pcdb.m5.proteingym_confirmation import initial_selection
from pcdb.m5.proteingym_runtime import M5CTruthEvaluator, build_feature_artifact, select_state

COHORT = (
    "PABP_YEAST_Melamed_2013",
    "HSP82_YEAST_Flynn_2019",
    "HXK4_HUMAN_Gersing_2022_activity",
)


@pytest.mark.parametrize("assay", COHORT)
def test_explicit_iteration_keeps_formal_default_closed_and_nested(assay: str) -> None:
    ids = tuple(hashlib.sha256(f"cohort-fixture-{i}".encode()).hexdigest() for i in range(96))
    with pytest.raises(ValueError, match="identity"):
        initial_selection(ids, assay_id=assay, run_seed=1729, size=32)
    small = initial_selection(ids, assay_id=assay, run_seed=1729, size=32, assay_ids=COHORT)
    large = initial_selection(ids[::-1], assay_id=assay, run_seed=1729, size=64, assay_ids=COHORT)
    assert small == large[:32]


@pytest.mark.parametrize("model", ["random-forest-sklearn-v1", "xgboost-cpu-v1"])
def test_iteration_selection_uses_only_revealed_labels(tmp_path: Path, model: str) -> None:
    assay = COHORT[0]
    ids = tuple(hashlib.sha256(f"benign-fixture-{i}".encode()).hexdigest() for i in range(96))
    sequences = tuple("A" * (i % 7 + 1) + "C" * (i % 5 + 1) + "DEFGHIK" for i in range(96))
    artifact = build_feature_artifact(sequences, tmp_path / "features.npy")
    initial = initial_selection(ids, assay_id=assay, run_seed=1729, size=32, assay_ids=COHORT)
    first = np.arange(96, dtype=np.float64)
    second = np.asarray([first[i] if cid in initial else -1000 - i for i, cid in enumerate(ids)])
    selections = []
    for labels in (first, second):
        oracle = M5CTruthEvaluator(ids, labels)
        selections.append(
            select_state(
                assay_id=assay,
                candidate_ids=ids,
                candidate_pool_sha256="a" * 64,
                features=np.load(artifact.path, mmap_mode="r"),
                feature_sha256=artifact.sha256,
                initial_sample_size=32,
                model_id=model,
                observations=oracle.reveal(initial),
                run_seed=1729,
                assay_ids=COHORT,
            )
        )
    assert selections[0] == selections[1]
    selection = selections[0]
    overlap = set(selection.greedy_ids) & set(selection.exploration_ids)
    for branch in (selection.greedy_ids, selection.exploration_ids, *selection.matched_ids):
        assert len(branch) == len(set(branch)) == 16
        assert not set(branch) & set(initial)
    for matched in selection.matched_ids:
        assert set(matched) & set(selection.greedy_ids) == overlap


def test_source_sequence_identity_rejects_wrong_numbering_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from prepare_m5c_benign_iteration import validate_variant  # type: ignore[import-not-found]

    validate_variant("A1V:D3E", "VCEF", "ACDF")
    for mutant, sequence in (("A2V", "AVDF"), ("A1V", "ACDF"), ("A1V:A1G", "GCDF")):
        with pytest.raises(ValueError):
            validate_variant(mutant, sequence, "ACDF")
