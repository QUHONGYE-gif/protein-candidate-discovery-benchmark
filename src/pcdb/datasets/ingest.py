"""Fail-closed preparation of immutable public raw-dataset objects."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ChecksumMismatchError(ValueError):
    """Raised when downloaded bytes do not match publisher metadata."""


@dataclass(frozen=True)
class SourceFile:
    """One immutable file from a canonical public release."""

    key: str
    url: str
    md5: str
    size_bytes: int


@dataclass(frozen=True)
class SourceInventory:
    """Versioned download plan for one dataset task."""

    dataset_id: str
    dataset_version: str
    task_id: str
    source_url: str
    doi: str
    license: str
    object_prefix: str
    known_limitations: tuple[str, ...]
    files: tuple[SourceFile, ...]


@dataclass(frozen=True)
class VerifiedFile:
    """Publisher-verified local file and its content digest."""

    source: SourceFile
    path: Path
    sha256: str
    size_bytes: int


OpenUrl = Callable[[Request], AbstractContextManager[BinaryIO]]


def _default_open_url(request: Request) -> AbstractContextManager[BinaryIO]:
    return cast(AbstractContextManager[BinaryIO], urlopen(request, timeout=60))  # noqa: S310


def _require_string(mapping: Mapping[str, object], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _relative_posix_path(value: str, *, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{field} must be a safe relative POSIX path")
    return path


def _canonical_zenodo_url(value: str, *, field: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "zenodo.org":
        raise ValueError(f"{field} must use canonical HTTPS zenodo.org")
    return value


def _source_file(raw: object) -> SourceFile:
    if not isinstance(raw, dict):
        raise ValueError("each source file must be an object")
    item = cast(dict[str, object], raw)
    expected_keys = {"key", "url", "md5", "size_bytes"}
    if set(item) != expected_keys:
        raise ValueError("source file fields do not match the inventory contract")

    key = _require_string(item, "key")
    _relative_posix_path(key, field="file key")
    url = _canonical_zenodo_url(_require_string(item, "url"), field="file URL")
    md5 = _require_string(item, "md5")
    if len(md5) != 32 or any(character not in "0123456789abcdef" for character in md5):
        raise ValueError("md5 must contain 32 lowercase hexadecimal characters")
    size_bytes = item.get("size_bytes")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        raise ValueError("size_bytes must be a non-negative integer")
    return SourceFile(key=key, url=url, md5=md5, size_bytes=size_bytes)


def load_source_inventory(path: Path) -> SourceInventory:
    """Load and validate a strict source inventory from JSON."""
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise ValueError("source inventory must be an object")
    inventory = cast(dict[str, object], raw)
    expected_keys = {
        "inventory_version",
        "dataset_id",
        "dataset_version",
        "task_id",
        "source_url",
        "doi",
        "license",
        "object_prefix",
        "known_limitations",
        "files",
    }
    if set(inventory) != expected_keys:
        raise ValueError("source inventory fields do not match version 1")
    if inventory.get("inventory_version") != "1":
        raise ValueError("unsupported source inventory version")

    source_url = _canonical_zenodo_url(_require_string(inventory, "source_url"), field="source URL")
    object_prefix = _require_string(inventory, "object_prefix")
    _relative_posix_path(object_prefix, field="object prefix")

    limitations = inventory.get("known_limitations")
    if (
        not isinstance(limitations, list)
        or any(not isinstance(item, str) or not item for item in limitations)
        or len(set(limitations)) != len(limitations)
    ):
        raise ValueError("known_limitations must contain unique non-empty strings")

    raw_files = inventory.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("files must be a non-empty array")
    files = tuple(_source_file(item) for item in raw_files)
    if len({item.key for item in files}) != len(files):
        raise ValueError("source file keys must be unique")

    return SourceInventory(
        dataset_id=_require_string(inventory, "dataset_id"),
        dataset_version=_require_string(inventory, "dataset_version"),
        task_id=_require_string(inventory, "task_id"),
        source_url=source_url,
        doi=_require_string(inventory, "doi"),
        license=_require_string(inventory, "license"),
        object_prefix=object_prefix,
        known_limitations=tuple(cast(list[str], limitations)),
        files=files,
    )


def _local_path(root: Path, key: str, *, suffix: str = "") -> Path:
    relative = _relative_posix_path(key, field="file key")
    resolved_root = root.resolve()
    path = resolved_root.joinpath(*relative.parts).with_name(relative.name + suffix)
    if not path.resolve().is_relative_to(resolved_root):
        raise ValueError("file key escapes the staging root")
    return path


def _digests(path: Path) -> tuple[str, str, int]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
            size_bytes += len(chunk)
    return md5.hexdigest(), sha256.hexdigest(), size_bytes


def _verified_metadata(source: SourceFile, path: Path) -> VerifiedFile:
    md5, sha256, size_bytes = _digests(path)
    if size_bytes != source.size_bytes:
        raise ChecksumMismatchError(
            f"{source.key}: expected {source.size_bytes} bytes, received {size_bytes}"
        )
    if md5 != source.md5:
        raise ChecksumMismatchError(f"{source.key}: publisher MD5 mismatch")
    return VerifiedFile(source=source, path=path, sha256=sha256, size_bytes=size_bytes)


def download_source_file(
    source: SourceFile,
    staging_root: Path,
    *,
    open_url: OpenUrl = _default_open_url,
) -> VerifiedFile:
    """Resume one download, verify publisher metadata, and publish it locally once."""
    verified_path = _local_path(staging_root / "verified", source.key)
    if verified_path.exists():
        return _verified_metadata(source, verified_path)

    part_path = _local_path(staging_root / "incoming", source.key, suffix=".part")
    part_path.parent.mkdir(parents=True, exist_ok=True)
    verified_path.parent.mkdir(parents=True, exist_ok=True)
    offset = part_path.stat().st_size if part_path.exists() else 0
    if part_path.exists() and offset >= source.size_bytes:
        metadata = _verified_metadata(source, part_path)
        os.link(part_path, verified_path)
        part_path.unlink()
        return VerifiedFile(
            source=source,
            path=verified_path,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
        )
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    request = Request(source.url, headers=headers)

    with open_url(request) as response:
        status = int(getattr(response, "status", 200))
        if status not in {200, 206}:
            raise OSError(f"unexpected HTTP status {status} for {source.url}")
        mode = "ab" if offset and status == 206 else "wb"
        with part_path.open(mode) as handle:
            shutil.copyfileobj(response, handle)
            handle.flush()
            os.fsync(handle.fileno())

    metadata = _verified_metadata(source, part_path)
    os.link(part_path, verified_path)
    part_path.unlink()
    return VerifiedFile(
        source=source,
        path=verified_path,
        sha256=metadata.sha256,
        size_bytes=metadata.size_bytes,
    )


def build_dataset_manifest(
    inventory: SourceInventory,
    verified_files: Sequence[VerifiedFile],
    *,
    downloaded_at: datetime,
) -> dict[str, object]:
    """Build Dataset Manifest v1 from exactly the inventory's verified files."""
    if downloaded_at.utcoffset() is None:
        raise ValueError("downloaded_at must be timezone-aware")
    by_key = {item.source.key: item for item in verified_files}
    if set(by_key) != {item.key for item in inventory.files}:
        raise ValueError("verified files must exactly match the source inventory")

    raw_files: list[dict[str, object]] = []
    prefix = PurePosixPath(inventory.object_prefix)
    for source in inventory.files:
        verified = by_key[source.key]
        raw_files.append(
            {
                "object_key": str(prefix / PurePosixPath(source.key)),
                "sha256": verified.sha256,
                "size_bytes": verified.size_bytes,
            }
        )

    timestamp = downloaded_at.isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "1",
        "dataset_id": inventory.dataset_id,
        "dataset_version": inventory.dataset_version,
        "task_id": inventory.task_id,
        "source_url": inventory.source_url,
        "license": inventory.license,
        "downloaded_at": timestamp,
        "raw_files": raw_files,
        "known_limitations": list(inventory.known_limitations),
    }
