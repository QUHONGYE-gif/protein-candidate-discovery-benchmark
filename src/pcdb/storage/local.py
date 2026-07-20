"""Filesystem implementation of the object-style artifact contract."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

from pcdb.storage.base import ArtifactMetadata


class LocalArtifactStore:
    """Store artifacts under one local root using atomic publication."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        object_key = PurePosixPath(key)
        if not key or object_key.is_absolute() or ".." in object_key.parts:
            raise ValueError("artifact key must be a non-empty relative POSIX path")
        path = self._root.joinpath(*object_key.parts)
        if not path.resolve().is_relative_to(self._root):
            raise ValueError("artifact key escapes the store root")
        return path

    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> ArtifactMetadata:
        """Write an object atomically and return its verified metadata."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary_path = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(temporary_path, path)
            else:
                os.link(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return ArtifactMetadata(
            key=key,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def get_bytes(self, key: str) -> bytes:
        """Read a complete object."""
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        """Return whether an object exists."""
        return self._path(key).is_file()
