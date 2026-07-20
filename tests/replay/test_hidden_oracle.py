from __future__ import annotations

import pytest

from pcdb.replay import AlreadyRevealedError, HiddenOracle, Observation, UnknownCandidateError


def test_oracle_exposes_ids_without_hidden_labels() -> None:
    oracle = HiddenOracle({"candidate-a": 0.1, "candidate-b": 0.9})

    assert oracle.candidate_ids == frozenset({"candidate-a", "candidate-b"})
    assert oracle.observations == ()


def test_oracle_reveals_only_selected_candidates_in_order() -> None:
    oracle = HiddenOracle({"candidate-a": 0.1, "candidate-b": 0.9})

    revealed = oracle.reveal(["candidate-b"])

    assert revealed == (Observation(candidate_id="candidate-b", label=0.9),)
    assert oracle.observations == revealed
    assert oracle.revealed_ids == frozenset({"candidate-b"})


def test_oracle_validates_a_batch_before_revealing_any_label() -> None:
    oracle = HiddenOracle({"candidate-a": 0.1})

    with pytest.raises(UnknownCandidateError):
        oracle.reveal(["candidate-a", "missing"])

    assert oracle.observations == ()


def test_oracle_rejects_repeated_selection() -> None:
    oracle = HiddenOracle({"candidate-a": 0.1})
    oracle.reveal(["candidate-a"])

    with pytest.raises(AlreadyRevealedError, match="already revealed"):
        oracle.reveal(["candidate-a"])
