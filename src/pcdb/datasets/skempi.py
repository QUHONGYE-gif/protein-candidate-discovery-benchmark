"""Fail-closed preparation of the official SKEMPI 2.0 source bundle."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tarfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class SourceSizeMismatchError(ValueError):
    """Raised when SKEMPI bytes do not match the publisher Content-Length."""


@dataclass(frozen=True)
class SkempiSourceFile:
    """One publisher object without a publisher-supplied cryptographic checksum."""

    key: str
    url: str
    published_date: str
    size_bytes: int
    content_type: str
    http_status: int = 200
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class SkempiSourceInventory:
    """Frozen acquisition contract for the official SKEMPI 2.0 source bundle."""

    dataset_id: str
    dataset_version: str
    task_id: str
    source_url: str
    doi: str
    license: str
    license_url: str
    attribution: str
    publisher_checksum_policy: str
    http_metadata_observed_at: str
    object_prefix: str
    known_limitations: tuple[str, ...]
    files: tuple[SkempiSourceFile, ...]


@dataclass(frozen=True)
class VerifiedSkempiFile:
    """Size-verified local source object and its locally computed digest."""

    source: SkempiSourceFile
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


def _canonical_skempi_url(value: str, *, field: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "life.bsc.es"
        or not parsed.path.startswith("/pid/skempi2/")
    ):
        raise ValueError(f"{field} must use canonical HTTPS life.bsc.es/pid/skempi2")
    return value


def _source_file(raw: object) -> SkempiSourceFile:
    if not isinstance(raw, dict):
        raise ValueError("each source file must be an object")
    item = cast(dict[str, object], raw)
    expected_keys = {
        "key",
        "url",
        "published_date",
        "size_bytes",
        "content_type",
        "http_status",
        "etag",
        "last_modified",
    }
    if set(item) != expected_keys:
        raise ValueError("source file fields do not match the SKEMPI inventory contract")

    key = _require_string(item, "key")
    _relative_posix_path(key, field="file key")
    source_url = _canonical_skempi_url(_require_string(item, "url"), field="file URL")
    published_date = _require_string(item, "published_date")
    try:
        date.fromisoformat(published_date)
    except ValueError as error:
        raise ValueError("published_date must use ISO 8601 YYYY-MM-DD") from error
    size_bytes = item.get("size_bytes")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 1:
        raise ValueError("size_bytes must be a positive integer")
    http_status = item.get("http_status")
    if http_status != 200:
        raise ValueError("frozen source HTTP status must be 200")
    etag = item.get("etag")
    last_modified = item.get("last_modified")
    if etag is not None and (not isinstance(etag, str) or not etag):
        raise ValueError("etag must be null or a non-empty string")
    if last_modified is not None and (not isinstance(last_modified, str) or not last_modified):
        raise ValueError("last_modified must be null or a non-empty string")
    return SkempiSourceFile(
        key=key,
        url=source_url,
        published_date=published_date,
        size_bytes=size_bytes,
        content_type=_require_string(item, "content_type"),
        http_status=http_status,
        etag=etag,
        last_modified=last_modified,
    )


def load_skempi_source_inventory(path: Path) -> SkempiSourceInventory:
    """Load and validate the strict SKEMPI source inventory."""
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
        "license_url",
        "attribution",
        "publisher_checksum_policy",
        "http_metadata_observed_at",
        "object_prefix",
        "known_limitations",
        "files",
    }
    if set(inventory) != expected_keys:
        raise ValueError("source inventory fields do not match version 1")
    if inventory.get("inventory_version") != "1":
        raise ValueError("unsupported source inventory version")
    if inventory.get("publisher_checksum_policy") != "not-provided":
        raise ValueError("publisher_checksum_policy must explicitly be not-provided")

    source_url = _canonical_skempi_url(_require_string(inventory, "source_url"), field="source URL")
    license_url = _canonical_skempi_url(
        _require_string(inventory, "license_url"), field="license URL"
    )
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

    return SkempiSourceInventory(
        dataset_id=_require_string(inventory, "dataset_id"),
        dataset_version=_require_string(inventory, "dataset_version"),
        task_id=_require_string(inventory, "task_id"),
        source_url=source_url,
        doi=_require_string(inventory, "doi"),
        license=_require_string(inventory, "license"),
        license_url=license_url,
        attribution=_require_string(inventory, "attribution"),
        publisher_checksum_policy="not-provided",
        http_metadata_observed_at=_require_string(inventory, "http_metadata_observed_at"),
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


def _verified_metadata(source: SkempiSourceFile, path: Path) -> VerifiedSkempiFile:
    sha256 = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
            size_bytes += len(chunk)
    if size_bytes != source.size_bytes:
        raise SourceSizeMismatchError(
            f"{source.key}: expected {source.size_bytes} bytes, received {size_bytes}"
        )
    return VerifiedSkempiFile(
        source=source,
        path=path,
        sha256=sha256.hexdigest(),
        size_bytes=size_bytes,
    )


def download_skempi_source_file(
    source: SkempiSourceFile,
    staging_root: Path,
    *,
    open_url: OpenUrl = _default_open_url,
) -> VerifiedSkempiFile:
    """Resume, size-check, hash, and exclusively publish one SKEMPI source object."""
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
        return VerifiedSkempiFile(
            source=source,
            path=verified_path,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
        )

    request = Request(source.url, headers={"Range": f"bytes={offset}-"} if offset else {})
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
    return VerifiedSkempiFile(
        source=source,
        path=verified_path,
        sha256=metadata.sha256,
        size_bytes=metadata.size_bytes,
    )


def build_skempi_dataset_manifest(
    inventory: SkempiSourceInventory,
    verified_files: Sequence[VerifiedSkempiFile],
    *,
    downloaded_at: datetime,
    observed_limitations: Sequence[str] = (),
) -> dict[str, object]:
    """Build Dataset Manifest v1 for exactly the verified SKEMPI source bundle."""
    if downloaded_at.utcoffset() is None:
        raise ValueError("downloaded_at must be timezone-aware")
    by_key = {item.source.key: item for item in verified_files}
    if set(by_key) != {item.key for item in inventory.files}:
        raise ValueError("verified files must exactly match the source inventory")

    prefix = PurePosixPath(inventory.object_prefix)
    raw_files = [
        {
            "object_key": str(prefix / source.key),
            "sha256": by_key[source.key].sha256,
            "size_bytes": by_key[source.key].size_bytes,
        }
        for source in inventory.files
    ]
    return {
        "schema_version": "1",
        "dataset_id": inventory.dataset_id,
        "dataset_version": inventory.dataset_version,
        "task_id": inventory.task_id,
        "source_url": inventory.source_url,
        "doi": inventory.doi,
        "license": inventory.license,
        "downloaded_at": downloaded_at.isoformat().replace("+00:00", "Z"),
        "raw_files": raw_files,
        "known_limitations": [*inventory.known_limitations, *observed_limitations],
    }


def build_skempi_source_inspection(
    inventory: SkempiSourceInventory,
    verified_files: Sequence[VerifiedSkempiFile],
) -> dict[str, object]:
    """Inspect source structure without normalizing labels, mutations, or mappings."""
    by_key = {item.source.key: item for item in verified_files}
    expected_keys = {"skempi_v2.csv", "SKEMPI2_PDBs.tgz"}
    if set(by_key) != expected_keys or set(by_key) != {item.key for item in inventory.files}:
        raise ValueError("verified files must be the exact SKEMPI source bundle")

    table = by_key["skempi_v2.csv"]
    with table.path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.reader(handle, delimiter=";")
        try:
            header = next(rows)
        except StopIteration as error:
            raise ValueError("SKEMPI table must contain a header") from error
        row_count = sum(1 for _ in rows)

    structure_archive = by_key["SKEMPI2_PDBs.tgz"]
    members: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    with tarfile.open(structure_archive.path, "r:gz") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "." in path.parts:
                raise ValueError("structure archive contains an unsafe member path")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("structure archive contains a non-regular member")
            if member.name in seen_paths:
                raise ValueError("structure archive member paths must be unique")
            seen_paths.add(member.name)
            members.append({"path": member.name, "size_bytes": member.size})
    members.sort(key=lambda item: cast(str, item["path"]))
    apple_double_member_count = sum(
        1 for item in members if PurePosixPath(cast(str, item["path"])).name.startswith("._")
    )

    def is_payload_with_suffix(item: dict[str, object], suffix: str) -> bool:
        path = PurePosixPath(cast(str, item["path"]))
        return not path.name.startswith("._") and path.name.lower().endswith(suffix)

    observed_limitations = [
        f"The publisher archive contains {apple_double_member_count} AppleDouble metadata "
        "member(s); they are preserved in the raw object and deterministic member inventory "
        "but excluded from PDB and mapping payload counts."
    ]

    prefix = PurePosixPath(inventory.object_prefix)
    return {
        "schema_version": "1",
        "dataset_id": inventory.dataset_id,
        "dataset_version": inventory.dataset_version,
        "task_id": inventory.task_id,
        "observed_limitations": observed_limitations,
        "table": {
            "object_key": str(prefix / table.source.key),
            "sha256": table.sha256,
            "delimiter": ";",
            "header": header,
            "row_count": row_count,
        },
        "structure_archive": {
            "object_key": str(prefix / structure_archive.source.key),
            "sha256": structure_archive.sha256,
            "regular_file_count": len(members),
            "apple_double_member_count": apple_double_member_count,
            "pdb_payload_member_count": sum(
                1 for item in members if is_payload_with_suffix(item, ".pdb")
            ),
            "mapping_payload_member_count": sum(
                1 for item in members if is_payload_with_suffix(item, ".mapping")
            ),
            "members": members,
        },
    }
