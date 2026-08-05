"""Dependency-free value types shared across M2 boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DevelopmentObservation:
    """One explicit development-role training observation."""

    candidate_id: str
    observation: float


__all__ = ["DevelopmentObservation"]
