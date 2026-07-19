from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pcdb.storage import LocalArtifactStore


def test_store_round_trip_and_metadata(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    payload = b"public benchmark artifact"

    metadata = store.put_bytes("runs/run-1/metrics.bin", payload)

    assert store.exists(metadata.key)
    assert store.get_bytes(metadata.key) == payload
    assert metadata.size_bytes == len(payload)
    assert metadata.sha256 == hashlib.sha256(payload).hexdigest()


def test_store_is_immutable_by_default(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    store.put_bytes("object.bin", b"first")

    with pytest.raises(FileExistsError):
        store.put_bytes("object.bin", b"second")

    assert store.get_bytes("object.bin") == b"first"


@pytest.mark.parametrize("key", ["", "/absolute", "../escape", "safe/../../escape"])
def test_store_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    store = LocalArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="artifact key"):
        store.put_bytes(key, b"data")
