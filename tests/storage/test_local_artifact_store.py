from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

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


def test_store_can_replace_when_overwrite_is_explicit(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    store.put_bytes("object.bin", b"first")

    metadata = store.put_bytes("object.bin", b"second", overwrite=True)

    assert store.get_bytes("object.bin") == b"second"
    assert metadata.sha256 == hashlib.sha256(b"second").hexdigest()


def test_store_allows_exactly_one_concurrent_create(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    start = Barrier(2)

    def write(payload: bytes) -> tuple[bytes, bool]:
        start.wait()
        try:
            store.put_bytes("object.bin", payload)
        except FileExistsError:
            return payload, False
        return payload, True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, (b"first", b"second")))

    winners = [payload for payload, succeeded in outcomes if succeeded]
    assert len(winners) == 1
    assert store.get_bytes("object.bin") == winners[0]


@pytest.mark.parametrize("key", ["", "/absolute", "../escape", "safe/../../escape"])
def test_store_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    store = LocalArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="artifact key"):
        store.put_bytes(key, b"data")
