"""Fail-closed hidden-label oracle for offline replay."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass


class UnknownCandidateError(KeyError):
    """Raised when a selection references an unknown candidate."""


class AlreadyRevealedError(ValueError):
    """Raised when a candidate is selected more than once."""


@dataclass(frozen=True, slots=True)
class Observation:
    """One label made observable by a replay selection."""

    candidate_id: str
    label: float


class HiddenOracle:
    """Own hidden truth and reveal labels only for selected candidate IDs."""

    def __init__(self, truth: Mapping[str, float]) -> None:
        if not truth:
            raise ValueError("truth must contain at least one candidate")
        if any(not candidate_id for candidate_id in truth):
            raise ValueError("candidate IDs must not be empty")
        validated: dict[str, float] = {}
        for candidate_id, raw_value in truth.items():
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError("truth values must be finite numeric values")
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError("truth values must be finite numeric values")
            validated[candidate_id] = value
        self.__truth = validated
        self.__observations: list[Observation] = []
        self.__revealed: set[str] = set()

    @property
    def candidate_ids(self) -> frozenset[str]:
        """Return selectable IDs without exposing labels."""
        return frozenset(self.__truth)

    @property
    def revealed_ids(self) -> frozenset[str]:
        """Return candidate IDs already revealed by the oracle."""
        return frozenset(self.__revealed)

    @property
    def observations(self) -> tuple[Observation, ...]:
        """Return observations in reveal order."""
        return tuple(self.__observations)

    def reveal(self, selected_ids: list[str] | tuple[str, ...]) -> tuple[Observation, ...]:
        """Reveal a batch atomically after validating every selected ID."""
        if len(set(selected_ids)) != len(selected_ids):
            raise AlreadyRevealedError("a selection batch contains duplicate candidate IDs")

        unknown = [
            candidate_id for candidate_id in selected_ids if candidate_id not in self.__truth
        ]
        if unknown:
            raise UnknownCandidateError(unknown[0])

        repeated = [
            candidate_id for candidate_id in selected_ids if candidate_id in self.__revealed
        ]
        if repeated:
            raise AlreadyRevealedError(f"candidate already revealed: {repeated[0]}")

        revealed = tuple(
            Observation(candidate_id=candidate_id, label=self.__truth[candidate_id])
            for candidate_id in selected_ids
        )
        self.__observations.extend(revealed)
        self.__revealed.update(selected_ids)
        return revealed
