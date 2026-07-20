"""Storage contract shared by local and future COS adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """Verified metadata returned after writing an artifact."""

    key: str
    size_bytes: int
    sha256: str


class ArtifactStore(Protocol):
    """Minimal object-style artifact store contract."""

    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> ArtifactMetadata:
        """Write one complete object."""
        ...

    def get_bytes(self, key: str) -> bytes:
        """Read one complete object."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether an object exists."""
        ...
