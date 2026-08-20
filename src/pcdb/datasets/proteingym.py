"""Bounded ProteinGym v1.3 four-assay acquisition and data-readiness runtime."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import zlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, TextIO, cast
from urllib.parse import urlparse
from urllib.request import Request, urlopen

APPROVAL = "APPROVED FOR ISSUE #11 UNIFIED PROTEINGYM DATA READINESS EXECUTION"
CONTINUATION_APPROVAL = "APPROVED FOR ISSUE #11 PROTEINGYM LOCAL BUNDLE CONTINUATION"
CANONICAL_CONFIG_RELATIVE = PurePosixPath(
    "configs/datasets/proteingym/v1.3/four-assay-unified-v1.json"
)
STANDARD_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")
LOCAL_HEADER_SIGNATURE = 0x04034B50


class ProteinGymError(ValueError):
    """Raised when the frozen ProteinGym route cannot proceed unambiguously."""


@dataclass(frozen=True)
class Archive:
    """Frozen identity for one official ProteinGym release archive."""

    archive_id: str
    url: str
    size_bytes: int
    etag: str
    last_modified: str


@dataclass(frozen=True)
class Member:
    """Frozen ZIP central-directory facts for one selected CSV member."""

    archive_id: str
    member_name: str
    local_header_offset: int
    compressed_size: int
    uncompressed_size: int
    crc32: int


@dataclass(frozen=True)
class Assay:
    """One selected ProteinGym assay and its two distinct source members."""

    assay_id: str
    paper_doi: str
    expected_truth_rows: int | None
    truth: Member
    score: Member


@dataclass(frozen=True)
class Caps:
    """Hard limits for the unified runtime."""

    network_bytes: int
    scratch_bytes: int
    cos_growth_bytes: int
    blat_network_bytes: int
    blat_scratch_bytes: int
    blat_cos_growth_bytes: int


@dataclass(frozen=True)
class ProteinGymConfig:
    """Strict versioned contract for the selective four-assay route."""

    dataset_id: str
    dataset_version: str
    task_id: str
    source_url: str
    doi: str
    license: str
    license_boundary: str
    object_prefix: str
    approval: str
    caps: Caps
    archives: tuple[Archive, ...]
    assays: tuple[Assay, ...]


@dataclass(frozen=True)
class AcquiredMember:
    """One range-acquired and decompressed selected member."""

    member: Member
    path: Path
    sha256: str
    size_bytes: int
    row_count: int
    requested_ranges: tuple[str, str, str]


@dataclass(frozen=True)
class PublishedFile:
    """Fresh-readback identity for one immutable object."""

    object_key: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ExpectedFile:
    """One fully materialized object expected under the frozen COS prefix."""

    object_key: str
    path: Path
    sha256: str
    size_bytes: int
    assay_id: str
    phase: str


@dataclass(frozen=True)
class PreparedAssay:
    """One assay's complete deterministic pre-publication inventory."""

    assay: Assay
    truth: AcquiredMember
    score: AcquiredMember
    processing: ProcessingResult
    dataset_manifest_bytes: bytes
    ingest_attempt_bytes: bytes
    files: tuple[ExpectedFile, ...]
    evidence: dict[str, object]


@dataclass(frozen=True)
class LocalBundle:
    """Strictly verified private handoff from local acquisition to the server."""

    members: Mapping[str, tuple[AcquiredMember, AcquiredMember]]
    acquired_at: Mapping[str, datetime]
    manifest_sha256: str
    manifest_size_bytes: int
    source_network_bytes: int
    transfer_bytes: int
    attempt_id: str


@dataclass(frozen=True)
class ProcessingResult:
    """Local processing and split artifacts for one assay."""

    payloads: tuple[tuple[str, Path], ...]
    processing_manifest_key: str
    processing_manifest_bytes: bytes
    split_manifest_key: str
    split_manifest_bytes: bytes
    candidate_count: int
    score_columns: tuple[str, ...]


OpenUrl = Callable[[Request], AbstractContextManager[BinaryIO]]


class FilePublisher(Protocol):
    """Immutable file publication with mandatory fresh readback."""

    def publish(self, path: Path, object_key: str) -> PublishedFile: ...

    def verify_duplicate_rejected(self, path: Path, object_key: str) -> str: ...

    def list_keys(self, object_prefix: str) -> tuple[str, ...]: ...

    def retrieve(self, object_key: str, destination: Path) -> PublishedFile: ...


class CliArguments(Protocol):
    """Arguments accepted by the ProteinGym CLI adapter."""

    config: Path
    repository_root: Path
    scratch_root: Path
    cos_destination: str
    reviewed_git_commit: str
    approval: str
    approval_comment_id: int


class LocalBundleCliArguments(Protocol):
    """Arguments accepted by the local acquisition adapter."""

    config: Path
    repository_root: Path
    scratch_root: Path
    reviewed_git_commit: str
    approval: str
    approval_comment_id: int
    original_approval_comment_id: int


class ContinuationCliArguments(Protocol):
    """Arguments accepted by the server continuation adapter."""

    config: Path
    repository_root: Path
    scratch_root: Path
    bundle_root: Path
    cos_destination: str
    reviewed_git_commit: str
    original_reviewed_git_commit: str
    approval: str
    approval_comment_id: int
    bundle_approval_comment_id: int
    original_approval_comment_id: int
    prior_continuation_approval_comment_id: list[int]


def _default_open_url(request: Request) -> AbstractContextManager[BinaryIO]:
    return cast(AbstractContextManager[BinaryIO], urlopen(request, timeout=60))  # noqa: S310


def _etag_header(value: str) -> str:
    if value.startswith("W/"):
        raise ProteinGymError("weak archive ETags are not accepted")
    return value if value.startswith('"') else f'"{value}"'


def _normalized_etag(value: str) -> str:
    if value.startswith("W/"):
        raise ProteinGymError("weak archive ETags are not accepted")
    return value.strip('"')


def _normalized_http_date(value: str) -> datetime:
    formats = ("%a, %d %b %Y %H:%M:%S GMT", "%Y-%m-%d %H:%M:%S GMT")
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ProteinGymError("Last-Modified does not use a supported UTC HTTP date")


def _string(mapping: Mapping[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ProteinGymError(f"{field} must be a non-empty string")
    return value


def _positive_integer(mapping: Mapping[str, object], field: str) -> int:
    value = mapping.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProteinGymError(f"{field} must be a positive integer")
    return value


def _safe_path(value: str, *, field: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ProteinGymError(f"{field} must be a safe relative POSIX path")
    return value


def _archive(raw: object) -> Archive:
    if not isinstance(raw, dict):
        raise ProteinGymError("archives entries must be objects")
    item = cast(dict[str, object], raw)
    if set(item) != {"archive_id", "url", "size_bytes", "etag", "last_modified"}:
        raise ProteinGymError("archive fields do not match contract version 1")
    url = _string(item, "url")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "marks.hms.harvard.edu"
        or not parsed.path.startswith("/proteingym/ProteinGym_v1.3/")
    ):
        raise ProteinGymError("archive URL must use the official ProteinGym v1.3 HTTPS base")
    return Archive(
        archive_id=_string(item, "archive_id"),
        url=url,
        size_bytes=_positive_integer(item, "size_bytes"),
        etag=_string(item, "etag"),
        last_modified=_string(item, "last_modified"),
    )


def _member(raw: object) -> Member:
    if not isinstance(raw, dict):
        raise ProteinGymError("member must be an object")
    item = cast(dict[str, object], raw)
    expected = {
        "archive_id",
        "member_name",
        "local_header_offset",
        "compressed_size",
        "uncompressed_size",
        "crc32",
    }
    if set(item) != expected:
        raise ProteinGymError("member fields do not match contract version 1")
    member_name = _safe_path(_string(item, "member_name"), field="member_name")
    if PurePosixPath(member_name).suffix != ".csv":
        raise ProteinGymError("selected member must be a CSV")
    crc32_text = _string(item, "crc32")
    if not re.fullmatch(r"[0-9a-f]{8}", crc32_text):
        raise ProteinGymError("crc32 must contain eight lowercase hexadecimal characters")
    offset = item.get("local_header_offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ProteinGymError("local_header_offset must be a non-negative integer")
    return Member(
        archive_id=_string(item, "archive_id"),
        member_name=member_name,
        local_header_offset=offset,
        compressed_size=_positive_integer(item, "compressed_size"),
        uncompressed_size=_positive_integer(item, "uncompressed_size"),
        crc32=int(crc32_text, 16),
    )


def _assay(raw: object) -> Assay:
    if not isinstance(raw, dict):
        raise ProteinGymError("assays entries must be objects")
    item = cast(dict[str, object], raw)
    if set(item) != {"assay_id", "paper_doi", "expected_truth_rows", "truth", "score"}:
        raise ProteinGymError("assay fields do not match contract version 1")
    assay_id = _string(item, "assay_id")
    if not re.fullmatch(r"[A-Za-z0-9_]+", assay_id):
        raise ProteinGymError("assay_id contains unsupported characters")
    expected_rows = item.get("expected_truth_rows")
    if expected_rows is not None and (
        not isinstance(expected_rows, int) or isinstance(expected_rows, bool) or expected_rows < 1
    ):
        raise ProteinGymError("expected_truth_rows must be null or a positive integer")
    truth = _member(item.get("truth"))
    score = _member(item.get("score"))
    expected_member = f"{assay_id}.csv"
    if truth.member_name != expected_member or score.member_name != expected_member:
        raise ProteinGymError("member_name must exactly equal the assay CSV basename")
    if truth.archive_id == score.archive_id:
        raise ProteinGymError("truth and score must use distinct source archives")
    return Assay(
        assay_id=assay_id,
        paper_doi=_string(item, "paper_doi"),
        expected_truth_rows=expected_rows,
        truth=truth,
        score=score,
    )


def load_config(path: Path) -> ProteinGymConfig:
    """Load and validate the frozen unified route."""
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProteinGymError("ProteinGym config must be valid UTF-8 JSON") from error
    if not isinstance(raw, dict):
        raise ProteinGymError("ProteinGym config must be an object")
    item = cast(dict[str, object], raw)
    expected = {
        "contract_version",
        "dataset_id",
        "dataset_version",
        "task_id",
        "source_url",
        "doi",
        "license",
        "license_boundary",
        "object_prefix",
        "approval",
        "caps",
        "archives",
        "assays",
    }
    if set(item) != expected or item.get("contract_version") != "1":
        raise ProteinGymError("config fields do not match contract version 1")
    raw_caps = item.get("caps")
    if not isinstance(raw_caps, dict):
        raise ProteinGymError("caps must be an object")
    caps_item = cast(dict[str, object], raw_caps)
    cap_fields = {
        "network_bytes",
        "scratch_bytes",
        "cos_growth_bytes",
        "blat_network_bytes",
        "blat_scratch_bytes",
        "blat_cos_growth_bytes",
    }
    if set(caps_item) != cap_fields:
        raise ProteinGymError("cap fields do not match contract version 1")
    caps = Caps(**{field: _positive_integer(caps_item, field) for field in cap_fields})
    raw_archives = item.get("archives")
    raw_assays = item.get("assays")
    if not isinstance(raw_archives, list) or not isinstance(raw_assays, list):
        raise ProteinGymError("archives and assays must be arrays")
    archives = tuple(_archive(entry) for entry in raw_archives)
    assays = tuple(_assay(entry) for entry in raw_assays)
    if len(archives) != 2 or len({entry.archive_id for entry in archives}) != 2:
        raise ProteinGymError("exactly two distinct archives are required")
    expected_assays = (
        "BLAT_ECOLX_Firnberg_2014",
        "CAPSD_AAV2S_Sinai_2021",
        "GFP_AEQVI_Sarkisyan_2016",
        "SPG1_STRSG_Olson_2014",
    )
    if tuple(assay.assay_id for assay in assays) != expected_assays:
        raise ProteinGymError("assays must preserve the frozen BLAT-first four-assay order")
    archive_ids = {entry.archive_id for entry in archives}
    members = tuple(member for assay in assays for member in (assay.truth, assay.score))
    if any(member.archive_id not in archive_ids for member in members):
        raise ProteinGymError("member references an unknown archive")
    if sum(member.compressed_size for member in members) > caps.network_bytes:
        raise ProteinGymError("selected compressed payload exceeds the network cap")
    if sum(member.uncompressed_size for member in members) > caps.scratch_bytes:
        raise ProteinGymError("selected uncompressed payload exceeds the scratch cap")
    if sum(member.compressed_size for member in members[:2]) > caps.blat_network_bytes:
        raise ProteinGymError("BLAT selected payload exceeds its network cap")
    object_prefix = _safe_path(_string(item, "object_prefix"), field="object_prefix")
    approval = _string(item, "approval")
    if approval != APPROVAL:
        raise ProteinGymError("approval string does not match the runtime contract")
    return ProteinGymConfig(
        dataset_id=_string(item, "dataset_id"),
        dataset_version=_string(item, "dataset_version"),
        task_id=_string(item, "task_id"),
        source_url=_string(item, "source_url"),
        doi=_string(item, "doi"),
        license=_string(item, "license"),
        license_boundary=_string(item, "license_boundary"),
        object_prefix=object_prefix,
        approval=approval,
        caps=caps,
        archives=archives,
        assays=assays,
    )


class RangeReader:
    """No-retry HTTP Range reader with a cumulative byte cap."""

    def __init__(
        self,
        *,
        byte_cap: int,
        open_url: OpenUrl = _default_open_url,
    ) -> None:
        self.byte_cap = byte_cap
        self.bytes_read = 0
        self._open_url = open_url

    @contextmanager
    def open(self, archive: Archive, start: int, length: int) -> Iterator[_CountingReader]:
        if length < 1 or start < 0 or start + length > archive.size_bytes:
            raise ProteinGymError("requested range falls outside the frozen archive")
        if self.bytes_read + length > self.byte_cap:
            raise ProteinGymError("network byte cap would be exceeded")
        end = start + length - 1
        request = Request(
            archive.url,
            headers={"Range": f"bytes={start}-{end}", "If-Range": _etag_header(archive.etag)},
        )
        with self._open_url(request) as response:
            status = int(getattr(response, "status", 0))
            headers = getattr(response, "headers", None)
            get_header = headers.get if headers is not None else lambda _name: None
            expected_content_range = f"bytes {start}-{end}/{archive.size_bytes}"
            if status != 206:
                raise ProteinGymError("selected-member request must return HTTP 206")
            if get_header("Content-Range") != expected_content_range:
                raise ProteinGymError("Content-Range does not match the frozen request")
            if get_header("Content-Length") != str(length):
                raise ProteinGymError("Content-Length does not match the frozen request")
            observed_etag = get_header("ETag")
            if not isinstance(observed_etag, str) or _normalized_etag(
                observed_etag
            ) != _normalized_etag(archive.etag):
                raise ProteinGymError("archive ETag drifted")
            observed_last_modified = get_header("Last-Modified")
            if not isinstance(observed_last_modified, str) or _normalized_http_date(
                observed_last_modified
            ) != _normalized_http_date(archive.last_modified):
                raise ProteinGymError("archive Last-Modified drifted")
            counted = _CountingReader(response, length)
            yield counted
            counted.verify_complete()
            self.bytes_read += length


class _CountingReader:
    def __init__(self, response: BinaryIO, expected: int) -> None:
        self._response = response
        self._expected = expected
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        data = self._response.read(size)
        self._read += len(data)
        if self._read > self._expected:
            raise ProteinGymError("Range response exceeded its exact expected length")
        return data

    def verify_complete(self) -> None:
        if self._read != self._expected:
            raise ProteinGymError("Range response ended before its exact expected length")


def _read_exact(handle: _CountingReader, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = handle.read(remaining)
        if not chunk:
            raise ProteinGymError("Range response ended early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def acquire_member(
    *,
    archive: Archive,
    member: Member,
    destination: Path,
    reader: RangeReader,
) -> AcquiredMember:
    """Acquire one exact deflated ZIP member without retaining the full archive."""
    offset = member.local_header_offset
    with reader.open(archive, offset, LOCAL_HEADER.size) as response:
        fixed = _read_exact(response, LOCAL_HEADER.size)
    (
        signature,
        _version,
        flags,
        method,
        _mtime,
        _mdate,
        local_crc,
        local_compressed,
        local_uncompressed,
        filename_length,
        extra_length,
    ) = LOCAL_HEADER.unpack(fixed)
    if signature != LOCAL_HEADER_SIGNATURE:
        raise ProteinGymError("local ZIP header signature drifted")
    if flags & 0x1:
        raise ProteinGymError("encrypted ZIP members are not allowed")
    if method != 8:
        raise ProteinGymError("selected member must use raw DEFLATE compression")
    if flags & 0x8:
        if any(
            value not in {0, expected}
            for value, expected in (
                (local_crc, member.crc32),
                (local_compressed, member.compressed_size),
                (local_uncompressed, member.uncompressed_size),
            )
        ):
            raise ProteinGymError("data-descriptor local header conflicts with frozen inventory")
    elif (
        local_crc != member.crc32
        or local_compressed != member.compressed_size
        or local_uncompressed != member.uncompressed_size
    ):
        raise ProteinGymError("local ZIP header sizes or CRC drifted")
    variable_length = filename_length + extra_length
    with reader.open(archive, offset + LOCAL_HEADER.size, variable_length) as response:
        variable = _read_exact(response, variable_length)
    try:
        filename = variable[:filename_length].decode("utf-8" if flags & 0x800 else "cp437")
    except UnicodeDecodeError as error:
        raise ProteinGymError("selected member filename cannot be decoded") from error
    filename_path = PurePosixPath(filename)
    if (
        filename_path.is_absolute()
        or "." in filename_path.parts
        or ".." in filename_path.parts
        or filename_path.name != member.member_name
    ):
        raise ProteinGymError("selected ZIP member path drifted or is unsafe")

    payload_start = offset + LOCAL_HEADER.size + variable_length
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    if destination.exists() or temporary.exists():
        raise ProteinGymError("acquisition destination already exists")
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    sha256 = hashlib.sha256()
    crc32 = 0
    size_bytes = 0
    try:
        with (
            reader.open(archive, payload_start, member.compressed_size) as response,
            temporary.open("xb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                decoded = decompressor.decompress(chunk)
                if decoded:
                    output.write(decoded)
                    sha256.update(decoded)
                    crc32 = zlib.crc32(decoded, crc32)
                    size_bytes += len(decoded)
            tail = decompressor.flush()
            if tail:
                output.write(tail)
                sha256.update(tail)
                crc32 = zlib.crc32(tail, crc32)
                size_bytes += len(tail)
            output.flush()
            os.fsync(output.fileno())
        if not decompressor.eof or decompressor.unused_data:
            raise ProteinGymError("selected DEFLATE stream did not end exactly")
        if size_bytes != member.uncompressed_size:
            raise ProteinGymError("extracted member size does not match frozen inventory")
        if crc32 & 0xFFFFFFFF != member.crc32:
            raise ProteinGymError("extracted member CRC-32 does not match frozen inventory")
        row_count = _csv_row_count(temporary)
        os.link(temporary, destination)
        temporary.unlink()
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return AcquiredMember(
        member=member,
        path=destination,
        sha256=sha256.hexdigest(),
        size_bytes=size_bytes,
        row_count=row_count,
        requested_ranges=(
            f"bytes={offset}-{offset + LOCAL_HEADER.size - 1}",
            (
                f"bytes={offset + LOCAL_HEADER.size}-"
                f"{offset + LOCAL_HEADER.size + variable_length - 1}"
            ),
            f"bytes={payload_start}-{payload_start + member.compressed_size - 1}",
        ),
    )


def _csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration as error:
            raise ProteinGymError("selected CSV is empty") from error
        return sum(1 for _ in reader)


def _canonical_decimal(value: str, *, context: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ProteinGymError(f"{context}: DMS_score must be a finite decimal") from error
    if not number.is_finite():
        raise ProteinGymError(f"{context}: DMS_score must be finite")
    canonical = format(number.normalize(), "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return "0" if canonical == "-0" else canonical


@contextmanager
def _deterministic_gzip_text(path: Path) -> Iterator[TextIO]:
    raw = path.open("xb")
    compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
    text = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
    try:
        yield text
    finally:
        text.close()


def _candidate_id(assay_id: str, mutant: str) -> str:
    return hashlib.sha256(f"proteingym:v1.3:{assay_id}\0{mutant}".encode()).hexdigest()


def _artifact(path: Path, object_key: str, row_count: int | None = None) -> dict[str, object]:
    sha256, size_bytes = _file_identity(path)
    result: dict[str, object] = {
        "object_key": object_key,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }
    if row_count is not None:
        result["row_count"] = row_count
    return result


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _write_bytes_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def build_dataset_manifest(
    config: ProteinGymConfig,
    assay: Assay,
    truth: AcquiredMember,
    score: AcquiredMember,
    *,
    downloaded_at: datetime,
) -> bytes:
    """Render a task-level Dataset Manifest with distinct truth and score objects."""
    if downloaded_at.tzinfo is None:
        raise ProteinGymError("downloaded_at must be timezone-aware")
    raw_prefix = f"{config.object_prefix}/raw/{assay.assay_id}"
    return _json_bytes(
        {
            "schema_version": "1",
            "dataset_id": config.dataset_id,
            "dataset_version": config.dataset_version,
            "task_id": assay.assay_id,
            "source_url": config.source_url,
            "doi": config.doi,
            "license": config.license,
            "downloaded_at": downloaded_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "raw_files": [
                {
                    "object_key": f"{raw_prefix}/truth.csv",
                    "sha256": truth.sha256,
                    "size_bytes": truth.size_bytes,
                },
                {
                    "object_key": f"{raw_prefix}/zero-shot-scores.csv",
                    "sha256": score.sha256,
                    "size_bytes": score.size_bytes,
                },
            ],
            "known_limitations": [
                config.license_boundary,
                (
                    "Zero-shot score columns are inputs under audit, not approved "
                    "low-fidelity signals."
                ),
            ],
        }
    )


def process_assay(
    *,
    config: ProteinGymConfig,
    assay: Assay,
    truth_path: Path,
    score_path: Path,
    dataset_manifest_bytes: bytes,
    config_bytes: bytes,
    git_commit: str,
    output_root: Path,
) -> ProcessingResult:
    """Separate candidate observables from truth and build a no-held-out split."""
    if not re.fullmatch(r"[0-9a-f]{40}", git_commit):
        raise ProteinGymError("git_commit must be a full lowercase SHA-1")
    manifest_sha256 = hashlib.sha256(dataset_manifest_bytes).hexdigest()
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    identity_payload = _json_bytes(
        {
            "assay_id": assay.assay_id,
            "dataset_manifest_sha256": manifest_sha256,
            "processing_config_sha256": config_sha256,
            "git_commit": git_commit,
        }
    )
    processing_hash = hashlib.sha256(identity_payload).hexdigest()
    processing_id = f"proteingym-{assay.assay_id.lower()}-{processing_hash}"
    prefix = f"{config.object_prefix}/processed/{assay.assay_id}/{processing_id}"
    local_prefix = output_root / assay.assay_id
    local_prefix.mkdir(parents=True, exist_ok=False)
    candidate_path = local_prefix / "candidates.csv.gz"
    truth_output_path = local_prefix / "hidden-truth.csv.gz"
    membership_path = local_prefix / "membership.csv.gz"
    audit_path = local_prefix / "processing-audit.json"
    coverage_path = local_prefix / "score-coverage.json"

    sequences_by_mutant: dict[str, str] = {}
    candidate_count = 0
    with (
        truth_path.open("r", encoding="utf-8-sig", newline="") as source,
        _deterministic_gzip_text(candidate_path) as candidate_output,
        _deterministic_gzip_text(truth_output_path) as truth_output,
        _deterministic_gzip_text(membership_path) as membership_output,
    ):
        reader = csv.DictReader(source)
        fields = tuple(reader.fieldnames or ())
        if len(set(fields)) != len(fields):
            raise ProteinGymError(f"{assay.assay_id}: truth CSV fields must be unique")
        required = {"mutant", "mutated_sequence", "DMS_score"}
        if not required <= set(fields):
            raise ProteinGymError(f"{assay.assay_id}: truth CSV lacks required fields")
        candidate_writer = csv.DictWriter(
            candidate_output,
            fieldnames=("candidate_id", "mutant", "mutated_sequence"),
            lineterminator="\n",
        )
        truth_writer = csv.DictWriter(
            truth_output, fieldnames=("candidate_id", "DMS_score"), lineterminator="\n"
        )
        membership_writer = csv.DictWriter(
            membership_output,
            fieldnames=("candidate_id", "partition"),
            lineterminator="\n",
        )
        candidate_writer.writeheader()
        truth_writer.writeheader()
        membership_writer.writeheader()
        for row_number, row in enumerate(reader, start=2):
            mutant = (row.get("mutant") or "").strip()
            sequence = (row.get("mutated_sequence") or "").strip()
            score = (row.get("DMS_score") or "").strip()
            if not mutant or mutant in sequences_by_mutant:
                raise ProteinGymError(f"{assay.assay_id}:{row_number}: duplicate/missing mutant")
            if (
                not sequence
                or sequence != sequence.upper()
                or not set(sequence) <= STANDARD_AMINO_ACIDS
            ):
                raise ProteinGymError(f"{assay.assay_id}:{row_number}: invalid mutated_sequence")
            canonical_score = _canonical_decimal(score, context=f"{assay.assay_id}:{row_number}")
            candidate = _candidate_id(assay.assay_id, mutant)
            sequences_by_mutant[mutant] = sequence
            candidate_writer.writerow(
                {"candidate_id": candidate, "mutant": mutant, "mutated_sequence": sequence}
            )
            truth_writer.writerow({"candidate_id": candidate, "DMS_score": canonical_score})
            membership_writer.writerow({"candidate_id": candidate, "partition": "candidate-pool"})
            candidate_count += 1
    if assay.expected_truth_rows is not None and candidate_count != assay.expected_truth_rows:
        raise ProteinGymError(f"{assay.assay_id}: truth row count differs from frozen expectation")

    reserved = {"mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"}
    score_seen: set[str] = set()
    score_columns: tuple[str, ...]
    coverage: dict[str, dict[str, int]]
    with score_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = tuple(reader.fieldnames or ())
        if len(set(fields)) != len(fields):
            raise ProteinGymError(f"{assay.assay_id}: score CSV fields must be unique")
        if "mutant" not in fields:
            raise ProteinGymError(f"{assay.assay_id}: score CSV lacks mutant")
        score_columns = tuple(field for field in fields if field not in reserved)
        if not score_columns:
            raise ProteinGymError(f"{assay.assay_id}: score CSV has no model score columns")
        coverage = {
            column: {"finite": 0, "missing": 0, "non_finite": 0} for column in score_columns
        }
        for row_number, row in enumerate(reader, start=2):
            mutant = (row.get("mutant") or "").strip()
            if not mutant or mutant in score_seen:
                raise ProteinGymError(
                    f"{assay.assay_id}:{row_number}: duplicate/missing score mutant"
                )
            if mutant not in sequences_by_mutant:
                raise ProteinGymError(f"{assay.assay_id}:{row_number}: score mutant lacks truth")
            score_sequence = (row.get("mutated_sequence") or "").strip()
            if "mutated_sequence" in fields and score_sequence != sequences_by_mutant[mutant]:
                raise ProteinGymError(
                    f"{assay.assay_id}:{row_number}: score mutated_sequence conflicts with truth"
                )
            score_seen.add(mutant)
            for column in score_columns:
                value = (row.get(column) or "").strip()
                if not value:
                    coverage[column]["missing"] += 1
                    continue
                try:
                    number = Decimal(value)
                except InvalidOperation as error:
                    raise ProteinGymError(
                        f"{assay.assay_id}:{row_number}: non-numeric {column}"
                    ) from error
                bucket = "finite" if number.is_finite() else "non_finite"
                coverage[column][bucket] += 1
    missing_score_rows = candidate_count - len(score_seen)
    coverage_bytes = _json_bytes(
        {
            "assay_id": assay.assay_id,
            "truth_candidate_count": candidate_count,
            "score_row_count": len(score_seen),
            "truth_candidates_without_score_row": missing_score_rows,
            "score_columns": list(score_columns),
            "coverage": coverage,
            "imputation": "none",
            "model_approval": "none",
        }
    )
    audit_bytes = _json_bytes(
        {
            "assay_id": assay.assay_id,
            "candidate_count": candidate_count,
            "candidate_id_unique": True,
            "mutant_unique": True,
            "invalid_sequence_count": 0,
            "non_finite_truth_count": 0,
            "partition": "candidate-pool",
            "held_out_partitions": [],
        }
    )
    _write_bytes_once(coverage_path, coverage_bytes)
    _write_bytes_once(audit_path, audit_bytes)

    candidate_key = f"{prefix}/candidates.csv.gz"
    hidden_truth_key = f"{prefix}/hidden-truth.csv.gz"
    audit_key = f"{prefix}/processing-audit.json"
    coverage_key = f"{prefix}/score-coverage.json"
    membership_key = f"{prefix}/membership.csv.gz"
    processing_manifest: dict[str, object] = {
        "schema_version": "1",
        "processing_id": processing_id,
        "dataset_manifest_sha256": manifest_sha256,
        "processing_config_sha256": config_sha256,
        "git_commit": git_commit,
        "candidate_id_field": "candidate_id",
        "candidate_id_unique": True,
        "observable_feature_fields": ["mutant", "mutated_sequence"],
        "hidden_label_field": "DMS_score",
        "label_direction": "higher_is_better",
        "duplicate_policy": {
            "exact_duplicates": "error",
            "near_duplicates": "allowed",
            "near_duplicate_method": "mutation-string identity only; topology audit deferred",
        },
        "output_files": [
            _artifact(candidate_path, candidate_key, candidate_count),
            _artifact(truth_output_path, hidden_truth_key, candidate_count),
            _artifact(audit_path, audit_key, 1),
            _artifact(coverage_path, coverage_key, 1),
        ],
        "known_limitations": [
            "All candidates remain in one candidate-pool; experiment sampling is not frozen here.",
            "Zero-shot coverage is audited without imputation or model approval.",
        ],
    }
    processing_manifest_bytes = _json_bytes(processing_manifest)
    processing_manifest_sha256 = hashlib.sha256(processing_manifest_bytes).hexdigest()
    split_id = f"proteingym-{assay.assay_id.lower()}-candidate-pool-{processing_hash}"
    split_manifest = {
        "schema_version": "1",
        "split_id": split_id,
        "protocol_version": "proteingym-four-assay-data-readiness-v1",
        "processing_manifest_sha256": processing_manifest_sha256,
        "split_config_sha256": config_sha256,
        "git_commit": git_commit,
        "membership_file": _artifact(membership_path, membership_key, candidate_count),
        "partitions": ["candidate-pool"],
        "grouping_unit": "candidate_id",
        "leakage_controls": [
            "No cross-partition flow exists because this revision creates no evaluation partition.",
            "Experiment-level initial observations and evaluation access require a later protocol.",
        ],
        "held_out_partitions": [],
        "partition_summary": [
            {
                "partition": "candidate-pool",
                "candidate_count": candidate_count,
                "group_count": candidate_count,
                "largest_group_size": 1,
            }
        ],
        "known_limitations": [
            "This is a data-readiness membership, not a train/validation/held-out split."
        ],
    }
    split_manifest_bytes = _json_bytes(split_manifest)
    return ProcessingResult(
        payloads=(
            (candidate_key, candidate_path),
            (hidden_truth_key, truth_output_path),
            (audit_key, audit_path),
            (coverage_key, coverage_path),
            (membership_key, membership_path),
        ),
        processing_manifest_key=f"{prefix}/processing-manifest.json",
        processing_manifest_bytes=processing_manifest_bytes,
        split_manifest_key=f"{prefix}/split-manifest.json",
        split_manifest_bytes=split_manifest_bytes,
        candidate_count=candidate_count,
        score_columns=score_columns,
    )


class CoscliFilePublisher:
    """File-streaming COSCLI publisher with overwrite denial and fresh readback."""

    def __init__(self, destination: str, readback_root: Path) -> None:
        if not re.fullmatch(r"cos://[A-Za-z0-9._-]+(?:/[A-Za-z0-9._/-]+)?", destination):
            raise ProteinGymError("COS destination must be a safe cos:// bucket/prefix")
        self.destination = destination.rstrip("/")
        self.readback_root = readback_root

    def publish(self, path: Path, object_key: str) -> PublishedFile:
        _safe_path(object_key, field="object_key")
        remote = f"{self.destination}/{object_key}"
        subprocess.run(
            [
                "coscli",
                "cp",
                str(path),
                remote,
                "--forbid-overwrite",
                "--err-retry-num",
                "0",
            ],
            check=True,
        )
        readback = self.readback_root / "published" / Path(*PurePosixPath(object_key).parts)
        published = self.retrieve(object_key, readback)
        source_identity = _file_identity(path)
        if source_identity != (published.sha256, published.size_bytes):
            raise ProteinGymError("COS fresh readback differs from the published file")
        readback.unlink()
        return PublishedFile(object_key, *source_identity)

    def list_keys(self, object_prefix: str) -> tuple[str, ...]:
        """List one exact prefix and fail if COSCLI output is incomplete or ambiguous."""
        prefix = _safe_path(object_prefix, field="object_prefix").rstrip("/")
        remote = f"{self.destination}/{prefix}/"
        result = subprocess.run(
            ["coscli", "ls", remote, "-r", "--limit", "1000"],
            check=True,
            capture_output=True,
            text=True,
        )
        keys: list[str] = []
        for line in result.stdout.splitlines():
            fields = [field.strip() for field in line.split("|")]
            if len(fields) >= 3 and fields[1] == "STANDARD" and fields[0]:
                keys.append(_safe_path(fields[0], field="listed object key"))
        matches = re.findall(r"TOTAL OBJECTS:\s*\|\s*(\d+)", result.stdout)
        if len(matches) != 1 or int(matches[0]) != len(keys):
            raise ProteinGymError("COSCLI prefix inventory output is incomplete or ambiguous")
        if len(keys) != len(set(keys)) or any(not key.startswith(f"{prefix}/") for key in keys):
            raise ProteinGymError(
                "COSCLI prefix inventory contains duplicate or out-of-prefix keys"
            )
        return tuple(sorted(keys))

    def retrieve(self, object_key: str, destination: Path) -> PublishedFile:
        """Fresh-read one exact object into a new local path and return its identity."""
        _safe_path(object_key, field="object_key")
        remote = f"{self.destination}/{object_key}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ProteinGymError("fresh readback destination already exists")
        subprocess.run(
            ["coscli", "cp", remote, str(destination), "--err-retry-num", "0"], check=True
        )
        sha256, size_bytes = _file_identity(destination)
        return PublishedFile(object_key, sha256, size_bytes)

    def verify_duplicate_rejected(self, path: Path, object_key: str) -> str:
        """Verify the immutable destination rejects a second write to the same key."""
        _safe_path(object_key, field="object_key")
        remote = f"{self.destination}/{object_key}"
        result = subprocess.run(
            [
                "coscli",
                "cp",
                str(path),
                remote,
                "--forbid-overwrite",
                "--err-retry-num",
                "0",
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            raise ProteinGymError("duplicate COS write unexpectedly succeeded")
        return f"coscli duplicate write rejected with exit {result.returncode}"


def build_ingest_attempt(
    *,
    config: ProteinGymConfig,
    assay: Assay,
    truth: AcquiredMember,
    score: AcquiredMember,
    dataset_manifest_key: str,
    dataset_manifest_bytes: bytes,
    config_repository_path: str,
    config_sha256: str,
    git_commit: str,
    cos_destination: str,
    duplicate_write_status: str,
    verified_at: datetime,
    acquisition_command: str = "HTTP Range selected-member acquisition; zero automatic retries",
) -> bytes:
    """Render a schema-valid per-assay DatasetIngestAttempt."""
    manifest_sha256 = hashlib.sha256(dataset_manifest_bytes).hexdigest()
    destination = urlparse(cos_destination)
    bucket_alias = destination.netloc
    return _json_bytes(
        {
            "schema_version": "1",
            "attempt_id": f"proteingym-{assay.assay_id.lower()}-{config_sha256[:16]}",
            "status": "succeeded",
            "dataset_id": config.dataset_id,
            "dataset_version": config.dataset_version,
            "task_id": assay.assay_id,
            "raw_ingest_git_commit": git_commit,
            "manifest_revision_git_commit": git_commit,
            "source_inventory": {
                "repository_path": config_repository_path,
                "sha256": config_sha256,
            },
            "dataset_manifest": {
                "revision": 1,
                "object_key": dataset_manifest_key,
                "sha256": manifest_sha256,
                "size_bytes": len(dataset_manifest_bytes),
            },
            "environment": {
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "coscli_version": "runtime coscli",
                "cos_region": "configured by COSCLI profile",
                "cos_bucket_alias": bucket_alias,
            },
            "commands": [
                acquisition_command,
                "coscli cp --forbid-overwrite --err-retry-num 0; fresh exact readback",
            ],
            "raw_objects": [
                {
                    "object_key": f"{config.object_prefix}/raw/{assay.assay_id}/truth.csv",
                    "sha256": truth.sha256,
                    "size_bytes": truth.size_bytes,
                },
                {
                    "object_key": (
                        f"{config.object_prefix}/raw/{assay.assay_id}/zero-shot-scores.csv"
                    ),
                    "sha256": score.sha256,
                    "size_bytes": score.size_bytes,
                },
            ],
            "verification": {
                "publisher_md5_and_size_verified": False,
                "cos_readback_sha256_and_size_verified": True,
                "manifest_schema_verified": True,
                "manifest_exact_bytes_verified": True,
                "duplicate_write_rejected": True,
                "duplicate_write_status": duplicate_write_status,
                "raw_object_count": 2,
            },
            "verified_at": verified_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
    )


def _publish_bytes(
    *,
    payload: bytes,
    object_key: str,
    scratch_root: Path,
    publisher: FilePublisher,
) -> PublishedFile:
    local = scratch_root / "manifest-staging" / Path(*PurePosixPath(object_key).parts)
    _write_bytes_once(local, payload)
    return publisher.publish(local, object_key)


def _directory_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _verify_repository(repository_root: Path, reviewed_git_commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", reviewed_git_commit):
        raise ProteinGymError("reviewed_git_commit must be a full lowercase SHA-1")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if head != reviewed_git_commit or status:
        raise ProteinGymError("runtime requires the exact reviewed commit in a clean worktree")


def _require_positive_comment_id(value: int, *, field: str) -> None:
    if isinstance(value, bool) or value < 1:
        raise ProteinGymError(f"{field} must be a positive integer")


def _require_outside_repository(path: Path, repository_root: Path, *, field: str) -> None:
    try:
        path.resolve().relative_to(repository_root.resolve())
    except ValueError:
        return
    raise ProteinGymError(f"{field} must be outside the reviewed repository")


def _require_canonical_config_path(config_path: Path, repository_root: Path) -> Path:
    canonical = repository_root.resolve().joinpath(*CANONICAL_CONFIG_RELATIVE.parts)
    if config_path.resolve() != canonical:
        raise ProteinGymError("config must be the canonical file in the reviewed repository")
    if config_path.is_symlink() or not config_path.is_file():
        raise ProteinGymError("canonical config must be a regular non-symlink file")
    return canonical


def _prepare_new_scratch(
    scratch_root: Path, repository_root: Path, *, required_free_bytes: int
) -> None:
    _require_outside_repository(scratch_root, repository_root, field="scratch_root")
    if not scratch_root.parent.is_dir():
        raise ProteinGymError("scratch parent must already exist")
    if shutil.disk_usage(scratch_root.parent).free < required_free_bytes:
        raise ProteinGymError("free local space is below the frozen scratch cap")
    scratch_root.mkdir(parents=False, exist_ok=False)


def _crc32_file(path: Path) -> int:
    checksum = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum = zlib.crc32(chunk, checksum)
    return checksum & 0xFFFFFFFF


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ProteinGymError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc_text(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ProteinGymError(f"{field} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProteinGymError(f"{field} must be a UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ProteinGymError(f"{field} must be a UTC timestamp")
    return parsed.astimezone(UTC)


def _mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProteinGymError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _expected_bundle_paths(config: ProteinGymConfig) -> set[str]:
    expected = {"local-acquisition-bundle.json"}
    for assay in config.assays:
        expected.add(f"raw/{assay.assay_id}/truth.csv")
        expected.add(f"raw/{assay.assay_id}/zero-shot-scores.csv")
    return expected


def _validate_bundle_inventory(bundle_root: Path, config: ProteinGymConfig) -> None:
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise ProteinGymError("bundle root must be a regular non-symlink directory")
    observed: set[str] = set()
    for path in bundle_root.rglob("*"):
        relative = path.relative_to(bundle_root).as_posix()
        if path.is_symlink():
            raise ProteinGymError(f"bundle contains a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ProteinGymError(f"bundle contains a special file: {relative}")
        observed.add(relative)
    if observed != _expected_bundle_paths(config):
        raise ProteinGymError("bundle file inventory differs from the frozen nine-file inventory")


def _bundle_member_record(
    *, assay: Assay, role: str, acquired: AcquiredMember, root: Path, acquired_at: datetime
) -> dict[str, object]:
    return {
        "assay_id": assay.assay_id,
        "role": role,
        "archive_id": acquired.member.archive_id,
        "member_name": acquired.member.member_name,
        "relative_path": acquired.path.relative_to(root).as_posix(),
        "sha256": acquired.sha256,
        "size_bytes": acquired.size_bytes,
        "row_count": acquired.row_count,
        "crc32": f"{acquired.member.crc32:08x}",
        "requested_ranges": list(acquired.requested_ranges),
        "acquired_at": _utc_text(acquired_at),
    }


def run_local_bundle(
    *,
    config_path: Path,
    repository_root: Path,
    scratch_root: Path,
    reviewed_git_commit: str,
    approval: str,
    approval_comment_id: int,
    original_approval_comment_id: int,
    open_url: OpenUrl = _default_open_url,
) -> dict[str, object]:
    """Acquire the exact eight frozen members locally and seal a private handoff bundle."""
    if approval != CONTINUATION_APPROVAL:
        raise ProteinGymError("exact continuation approval string is required")
    _require_positive_comment_id(approval_comment_id, field="approval_comment_id")
    _require_positive_comment_id(original_approval_comment_id, field="original_approval_comment_id")
    if approval_comment_id == original_approval_comment_id:
        raise ProteinGymError("continuation and original approval IDs must differ")
    _verify_repository(repository_root, reviewed_git_commit)
    config_path = _require_canonical_config_path(config_path, repository_root)
    config_bytes = config_path.read_bytes()
    config = load_config(config_path)
    _prepare_new_scratch(
        scratch_root, repository_root, required_free_bytes=config.caps.scratch_bytes
    )
    archives = {archive.archive_id: archive for archive in config.archives}
    reader = RangeReader(byte_cap=config.caps.network_bytes, open_url=open_url)
    member_records: list[dict[str, object]] = []
    for assay in config.assays:
        raw_root = scratch_root / "raw" / assay.assay_id
        truth = acquire_member(
            archive=archives[assay.truth.archive_id],
            member=assay.truth,
            destination=raw_root / "truth.csv",
            reader=reader,
        )
        score = acquire_member(
            archive=archives[assay.score.archive_id],
            member=assay.score,
            destination=raw_root / "zero-shot-scores.csv",
            reader=reader,
        )
        if assay.expected_truth_rows is not None and truth.row_count != assay.expected_truth_rows:
            raise ProteinGymError("BLAT truth row count failed the internal checkpoint")
        acquired_at = datetime.now(UTC)
        member_records.extend(
            (
                _bundle_member_record(
                    assay=assay,
                    role="truth",
                    acquired=truth,
                    root=scratch_root,
                    acquired_at=acquired_at,
                ),
                _bundle_member_record(
                    assay=assay,
                    role="score",
                    acquired=score,
                    root=scratch_root,
                    acquired_at=acquired_at,
                ),
            )
        )
        if _directory_size(scratch_root) > config.caps.scratch_bytes:
            raise ProteinGymError("local bundle scratch cap was exceeded")
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    attempt_id = f"proteingym-local-continuation-{approval_comment_id}"
    manifest: dict[str, object] = {
        "contract_version": "1",
        "status": "succeeded",
        "issue": 11,
        "attempt_id": attempt_id,
        "approval": approval,
        "approval_comment_id": approval_comment_id,
        "original_approval_comment_id": original_approval_comment_id,
        "reviewed_git_commit": reviewed_git_commit,
        "config_sha256": config_sha256,
        "source_network_bytes": reader.bytes_read,
        "source_network_cap_bytes": config.caps.network_bytes,
        "source_network_cap_scope": "v3_local_acquisition_only",
        "historical_consumed_attempt_accounting": "separate_not_counted_in_v3_cap",
        "automatic_retries": 0,
        "full_archive_downloaded": False,
        "members": member_records,
        "authorization_consumption": {
            "local_bundle_and_first_server_start_share_comment_id": True,
            "local_bundle_completion_consumes_server_start": False,
            "first_server_start_requires_new_id_for_any_replacement": True,
        },
    }
    manifest_path = scratch_root / "local-acquisition-bundle.json"
    _write_bytes_once(manifest_path, _json_bytes(manifest))
    if _directory_size(scratch_root) > config.caps.scratch_bytes:
        raise ProteinGymError("local bundle scratch cap was exceeded")
    return manifest


def _load_local_bundle(
    *,
    bundle_root: Path,
    repository_root: Path,
    config: ProteinGymConfig,
    config_sha256: str,
    reviewed_git_commit: str,
    approval_comment_id: int,
    original_approval_comment_id: int,
) -> LocalBundle:
    _require_outside_repository(bundle_root, repository_root, field="bundle_root")
    manifest_path = bundle_root / "local-acquisition-bundle.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ProteinGymError("local bundle manifest must be a regular non-symlink file")
    _validate_bundle_inventory(bundle_root, config)
    try:
        manifest_bytes = manifest_path.read_bytes()
        raw = cast(object, json.loads(manifest_bytes))
    except (OSError, json.JSONDecodeError) as error:
        raise ProteinGymError("local bundle manifest must be readable JSON") from error
    manifest = _mapping(raw, field="local bundle manifest")
    expected_fields = {
        "contract_version",
        "status",
        "issue",
        "attempt_id",
        "approval",
        "approval_comment_id",
        "original_approval_comment_id",
        "reviewed_git_commit",
        "config_sha256",
        "source_network_bytes",
        "source_network_cap_bytes",
        "source_network_cap_scope",
        "historical_consumed_attempt_accounting",
        "automatic_retries",
        "full_archive_downloaded",
        "members",
        "authorization_consumption",
    }
    if set(manifest) != expected_fields:
        raise ProteinGymError("local bundle manifest fields do not match contract version 1")
    if (
        manifest.get("contract_version") != "1"
        or manifest.get("status") != "succeeded"
        or manifest.get("issue") != 11
        or manifest.get("approval") != CONTINUATION_APPROVAL
        or manifest.get("approval_comment_id") != approval_comment_id
        or manifest.get("original_approval_comment_id") != original_approval_comment_id
        or manifest.get("reviewed_git_commit") != reviewed_git_commit
        or manifest.get("config_sha256") != config_sha256
        or manifest.get("source_network_cap_bytes") != config.caps.network_bytes
        or manifest.get("source_network_cap_scope") != "v3_local_acquisition_only"
        or manifest.get("historical_consumed_attempt_accounting")
        != "separate_not_counted_in_v3_cap"
        or manifest.get("automatic_retries") != 0
        or manifest.get("full_archive_downloaded") is not False
    ):
        raise ProteinGymError(
            "local bundle identity or safety boundary does not match continuation"
        )
    source_network_bytes = manifest.get("source_network_bytes")
    if (
        not isinstance(source_network_bytes, int)
        or isinstance(source_network_bytes, bool)
        or source_network_bytes < 1
        or source_network_bytes > config.caps.network_bytes
    ):
        raise ProteinGymError("local bundle source network accounting is invalid")
    attempt_id = manifest.get("attempt_id")
    if attempt_id != f"proteingym-local-continuation-{approval_comment_id}":
        raise ProteinGymError("local bundle attempt identity does not match authorization")
    consumption = _mapping(
        manifest.get("authorization_consumption"), field="authorization_consumption"
    )
    if consumption != {
        "local_bundle_and_first_server_start_share_comment_id": True,
        "local_bundle_completion_consumes_server_start": False,
        "first_server_start_requires_new_id_for_any_replacement": True,
    }:
        raise ProteinGymError("local bundle authorization consumption semantics drifted")
    raw_members = manifest.get("members")
    if not isinstance(raw_members, list) or len(raw_members) != 2 * len(config.assays):
        raise ProteinGymError("local bundle must contain exactly two members per frozen assay")
    records: dict[tuple[str, str], dict[str, object]] = {}
    member_fields = {
        "assay_id",
        "role",
        "archive_id",
        "member_name",
        "relative_path",
        "sha256",
        "size_bytes",
        "row_count",
        "crc32",
        "requested_ranges",
        "acquired_at",
    }
    for raw_member in raw_members:
        record = _mapping(raw_member, field="bundle member")
        if set(record) != member_fields:
            raise ProteinGymError("bundle member fields do not match contract version 1")
        assay_id = _string(record, "assay_id")
        role = _string(record, "role")
        if role not in {"truth", "score"} or (assay_id, role) in records:
            raise ProteinGymError("bundle member assay/role identity is invalid")
        records[(assay_id, role)] = record
    acquired_by_assay: dict[str, tuple[AcquiredMember, AcquiredMember]] = {}
    acquired_at_by_assay: dict[str, datetime] = {}
    transfer_bytes = len(manifest_bytes)
    for assay in config.assays:
        loaded: list[AcquiredMember] = []
        timestamps: list[datetime] = []
        for role, member, filename in (
            ("truth", assay.truth, "truth.csv"),
            ("score", assay.score, "zero-shot-scores.csv"),
        ):
            try:
                record = records.pop((assay.assay_id, role))
            except KeyError as error:
                raise ProteinGymError("bundle member set differs from frozen assays") from error
            relative = _safe_path(_string(record, "relative_path"), field="relative_path")
            expected_relative = f"raw/{assay.assay_id}/{filename}"
            if (
                relative != expected_relative
                or record.get("archive_id") != member.archive_id
                or record.get("member_name") != member.member_name
                or record.get("crc32") != f"{member.crc32:08x}"
            ):
                raise ProteinGymError("bundle member identity differs from frozen inventory")
            path = bundle_root.joinpath(*PurePosixPath(relative).parts)
            if path.is_symlink() or not path.is_file():
                raise ProteinGymError("bundle member must be a regular non-symlink file")
            sha256, size_bytes = _file_identity(path)
            row_count = _csv_row_count(path)
            ranges = record.get("requested_ranges")
            if (
                record.get("sha256") != sha256
                or record.get("size_bytes") != size_bytes
                or size_bytes != member.uncompressed_size
                or record.get("row_count") != row_count
                or _crc32_file(path) != member.crc32
                or not isinstance(ranges, list)
                or len(ranges) != 3
                or any(not isinstance(item, str) for item in ranges)
            ):
                raise ProteinGymError("bundle member bytes or acquisition evidence drifted")
            loaded.append(
                AcquiredMember(
                    member=member,
                    path=path,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    row_count=row_count,
                    requested_ranges=cast(tuple[str, str, str], tuple(ranges)),
                )
            )
            timestamps.append(_parse_utc_text(record.get("acquired_at"), field="acquired_at"))
            transfer_bytes += size_bytes
        if timestamps[0] != timestamps[1]:
            raise ProteinGymError("truth and score acquisition timestamps must match per assay")
        if (
            assay.expected_truth_rows is not None
            and loaded[0].row_count != assay.expected_truth_rows
        ):
            raise ProteinGymError("BLAT truth row count failed the bundle checkpoint")
        acquired_by_assay[assay.assay_id] = (loaded[0], loaded[1])
        acquired_at_by_assay[assay.assay_id] = timestamps[0]
    if records:
        raise ProteinGymError("bundle contains an unexpected assay member")
    if transfer_bytes > config.caps.scratch_bytes:
        raise ProteinGymError("bundle transfer bytes exceed the frozen scratch cap")
    return LocalBundle(
        members=acquired_by_assay,
        acquired_at=acquired_at_by_assay,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        manifest_size_bytes=len(manifest_bytes),
        source_network_bytes=source_network_bytes,
        transfer_bytes=transfer_bytes,
        attempt_id=attempt_id,
    )


def _expected_file(*, object_key: str, path: Path, assay_id: str, phase: str) -> ExpectedFile:
    sha256, size_bytes = _file_identity(path)
    return ExpectedFile(object_key, path, sha256, size_bytes, assay_id, phase)


def _stage_expected_bytes(
    *,
    payload: bytes,
    object_key: str,
    scratch_root: Path,
    assay_id: str,
    phase: str,
) -> ExpectedFile:
    path = scratch_root / "manifest-staging" / Path(*PurePosixPath(object_key).parts)
    _write_bytes_once(path, payload)
    return _expected_file(object_key=object_key, path=path, assay_id=assay_id, phase=phase)


def _assay_evidence(
    *,
    assay: Assay,
    truth: AcquiredMember,
    score: AcquiredMember,
    processing: ProcessingResult,
    dataset_manifest_bytes: bytes,
    ingest_attempt_bytes: bytes,
) -> dict[str, object]:
    return {
        "assay_id": assay.assay_id,
        "paper_doi": assay.paper_doi,
        "truth": {
            "sha256": truth.sha256,
            "size_bytes": truth.size_bytes,
            "row_count": truth.row_count,
            "ranges": list(truth.requested_ranges),
        },
        "score": {
            "sha256": score.sha256,
            "size_bytes": score.size_bytes,
            "row_count": score.row_count,
            "ranges": list(score.requested_ranges),
            "columns": list(processing.score_columns),
        },
        "candidate_count": processing.candidate_count,
        "dataset_manifest_sha256": hashlib.sha256(dataset_manifest_bytes).hexdigest(),
        "processing_manifest_sha256": hashlib.sha256(
            processing.processing_manifest_bytes
        ).hexdigest(),
        "split_manifest_sha256": hashlib.sha256(processing.split_manifest_bytes).hexdigest(),
        "dataset_ingest_attempt_sha256": hashlib.sha256(ingest_attempt_bytes).hexdigest(),
    }


def _prepare_continuation_assay(
    *,
    config: ProteinGymConfig,
    assay: Assay,
    truth: AcquiredMember,
    score: AcquiredMember,
    acquired_at: datetime,
    config_bytes: bytes,
    config_repository_path: str,
    reviewed_git_commit: str,
    cos_destination: str,
    scratch_root: Path,
) -> PreparedAssay:
    dataset_manifest_bytes = build_dataset_manifest(
        config, assay, truth, score, downloaded_at=acquired_at
    )
    processing = process_assay(
        config=config,
        assay=assay,
        truth_path=truth.path,
        score_path=score.path,
        dataset_manifest_bytes=dataset_manifest_bytes,
        config_bytes=config_bytes,
        git_commit=reviewed_git_commit,
        output_root=scratch_root / "processed",
    )
    raw_prefix = f"{config.object_prefix}/raw/{assay.assay_id}"
    files: list[ExpectedFile] = [
        _expected_file(
            object_key=f"{raw_prefix}/truth.csv",
            path=truth.path,
            assay_id=assay.assay_id,
            phase="payload",
        ),
        _expected_file(
            object_key=f"{raw_prefix}/zero-shot-scores.csv",
            path=score.path,
            assay_id=assay.assay_id,
            phase="payload",
        ),
    ]
    files.extend(
        _expected_file(
            object_key=object_key,
            path=path,
            assay_id=assay.assay_id,
            phase="payload",
        )
        for object_key, path in processing.payloads
    )
    dataset_manifest_key = (
        f"{config.object_prefix}/manifests/{assay.assay_id}/dataset-manifest-v1.json"
    )
    files.extend(
        (
            _stage_expected_bytes(
                payload=dataset_manifest_bytes,
                object_key=dataset_manifest_key,
                scratch_root=scratch_root,
                assay_id=assay.assay_id,
                phase="manifest",
            ),
            _stage_expected_bytes(
                payload=processing.processing_manifest_bytes,
                object_key=processing.processing_manifest_key,
                scratch_root=scratch_root,
                assay_id=assay.assay_id,
                phase="manifest",
            ),
            _stage_expected_bytes(
                payload=processing.split_manifest_bytes,
                object_key=processing.split_manifest_key,
                scratch_root=scratch_root,
                assay_id=assay.assay_id,
                phase="manifest",
            ),
        )
    )
    ingest_attempt_bytes = build_ingest_attempt(
        config=config,
        assay=assay,
        truth=truth,
        score=score,
        dataset_manifest_key=dataset_manifest_key,
        dataset_manifest_bytes=dataset_manifest_bytes,
        config_repository_path=config_repository_path,
        config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        git_commit=reviewed_git_commit,
        cos_destination=cos_destination,
        duplicate_write_status="coscli forbid-overwrite duplicate probe rejected",
        verified_at=acquired_at,
        acquisition_command=(
            "validated local Range-acquisition bundle; server source network bytes zero"
        ),
    )
    ingest_attempt_key = (
        f"{config.object_prefix}/evidence/{assay.assay_id}/dataset-ingest-attempt.json"
    )
    files.append(
        _stage_expected_bytes(
            payload=ingest_attempt_bytes,
            object_key=ingest_attempt_key,
            scratch_root=scratch_root,
            assay_id=assay.assay_id,
            phase="per-assay-evidence",
        )
    )
    evidence = _assay_evidence(
        assay=assay,
        truth=truth,
        score=score,
        processing=processing,
        dataset_manifest_bytes=dataset_manifest_bytes,
        ingest_attempt_bytes=ingest_attempt_bytes,
    )
    return PreparedAssay(
        assay=assay,
        truth=truth,
        score=score,
        processing=processing,
        dataset_manifest_bytes=dataset_manifest_bytes,
        ingest_attempt_bytes=ingest_attempt_bytes,
        files=tuple(files),
        evidence=evidence,
    )


def _read_json_mapping(path: Path, *, field: str) -> dict[str, object]:
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProteinGymError(f"{field} must be readable UTF-8 JSON") from error
    return _mapping(raw, field=field)


def _identity_record(value: object, *, field: str) -> tuple[str, str, int]:
    record = _mapping(value, field=field)
    key = _safe_path(_string(record, "object_key"), field=f"{field}.object_key")
    sha256 = _string(record, "sha256")
    size_bytes = record.get("size_bytes")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ProteinGymError(f"{field}.sha256 is invalid")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        raise ProteinGymError(f"{field}.size_bytes is invalid")
    return key, sha256, size_bytes


def _load_existing_blat(
    *,
    config: ProteinGymConfig,
    config_sha256: str,
    original_reviewed_git_commit: str,
    observed_keys: tuple[str, ...],
    publisher: FilePublisher,
    scratch_root: Path,
    truth: AcquiredMember,
    score: AcquiredMember,
) -> tuple[tuple[ExpectedFile, ...], dict[str, object], Path, dict[str, PublishedFile]]:
    assay = config.assays[0]
    assay_id = assay.assay_id
    prefix = config.object_prefix
    fixed = {
        f"{prefix}/raw/{assay_id}/truth.csv",
        f"{prefix}/raw/{assay_id}/zero-shot-scores.csv",
        f"{prefix}/manifests/{assay_id}/dataset-manifest-v1.json",
        f"{prefix}/evidence/{assay_id}/dataset-ingest-attempt.json",
    }
    processed_root = f"{prefix}/processed/{assay_id}/"
    processed = {key for key in observed_keys if key.startswith(processed_root)}
    expected_basenames = {
        "candidates.csv.gz",
        "hidden-truth.csv.gz",
        "membership.csv.gz",
        "processing-audit.json",
        "score-coverage.json",
        "processing-manifest.json",
        "split-manifest.json",
    }
    processing_parents = {str(PurePosixPath(key).parent) for key in processed}
    if (
        not fixed <= set(observed_keys)
        or len(processed) != 7
        or len(processing_parents) != 1
        or {PurePosixPath(key).name for key in processed} != expected_basenames
    ):
        raise ProteinGymError("existing BLAT inventory is incomplete or ambiguous")
    blat_keys = fixed | processed
    if len(blat_keys) != 11:
        raise ProteinGymError("existing BLAT inventory must contain exactly 11 objects")
    files: list[ExpectedFile] = []
    readbacks: dict[str, PublishedFile] = {}
    paths: dict[str, Path] = {}
    for key in sorted(blat_keys):
        path = scratch_root / "existing-readback" / Path(*PurePosixPath(key).parts)
        published = publisher.retrieve(key, path)
        files.append(
            ExpectedFile(key, path, published.sha256, published.size_bytes, assay_id, "reused")
        )
        readbacks[key] = published
        paths[key] = path
    raw_expected = {
        f"{prefix}/raw/{assay_id}/truth.csv": (truth.sha256, truth.size_bytes),
        f"{prefix}/raw/{assay_id}/zero-shot-scores.csv": (score.sha256, score.size_bytes),
    }
    if any(
        (readbacks[key].sha256, readbacks[key].size_bytes) != identity
        for key, identity in raw_expected.items()
    ):
        raise ProteinGymError("existing BLAT raw objects differ from the local source bundle")
    dataset_key = f"{prefix}/manifests/{assay_id}/dataset-manifest-v1.json"
    ingest_key = f"{prefix}/evidence/{assay_id}/dataset-ingest-attempt.json"
    processing_key = next(key for key in processed if key.endswith("/processing-manifest.json"))
    split_key = next(key for key in processed if key.endswith("/split-manifest.json"))
    coverage_key = next(key for key in processed if key.endswith("/score-coverage.json"))
    dataset = _read_json_mapping(paths[dataset_key], field="existing BLAT dataset manifest")
    ingest = _read_json_mapping(paths[ingest_key], field="existing BLAT ingest attempt")
    processing = _read_json_mapping(
        paths[processing_key], field="existing BLAT processing manifest"
    )
    split = _read_json_mapping(paths[split_key], field="existing BLAT split manifest")
    coverage = _read_json_mapping(paths[coverage_key], field="existing BLAT score coverage")
    raw_files = dataset.get("raw_files")
    if not isinstance(raw_files, list) or len(raw_files) != 2:
        raise ProteinGymError("existing BLAT dataset manifest raw inventory is invalid")
    dataset_raw = {
        key: (sha256, size_bytes)
        for key, sha256, size_bytes in (
            _identity_record(item, field="BLAT dataset raw file") for item in raw_files
        )
    }
    if dataset.get("task_id") != assay_id or dataset_raw != raw_expected:
        raise ProteinGymError("existing BLAT dataset manifest differs from source bundle")
    ingest_raw = ingest.get("raw_objects")
    dataset_reference = ingest.get("dataset_manifest")
    if not isinstance(ingest_raw, list) or len(ingest_raw) != 2:
        raise ProteinGymError("existing BLAT ingest raw inventory is invalid")
    if {
        key: (sha256, size_bytes)
        for key, sha256, size_bytes in (
            _identity_record(item, field="BLAT ingest raw object") for item in ingest_raw
        )
    } != raw_expected:
        raise ProteinGymError("existing BLAT ingest raw identity differs from source bundle")
    dataset_reference_key, dataset_reference_sha, dataset_reference_size = _identity_record(
        dataset_reference, field="BLAT ingest dataset manifest"
    )
    if (
        dataset_reference_key != dataset_key
        or (dataset_reference_sha, dataset_reference_size)
        != (readbacks[dataset_key].sha256, readbacks[dataset_key].size_bytes)
        or ingest.get("task_id") != assay_id
        or ingest.get("status") != "succeeded"
        or ingest.get("raw_ingest_git_commit") != original_reviewed_git_commit
        or ingest.get("manifest_revision_git_commit") != original_reviewed_git_commit
        or _mapping(ingest.get("source_inventory"), field="BLAT source inventory").get("sha256")
        != config_sha256
    ):
        raise ProteinGymError("existing BLAT ingest evidence chain is invalid")
    output_files = processing.get("output_files")
    if not isinstance(output_files, list) or len(output_files) != 4:
        raise ProteinGymError("existing BLAT processing inventory is invalid")
    processing_outputs = {
        key: (sha256, size_bytes, _mapping(item, field="BLAT processing output").get("row_count"))
        for item in output_files
        for key, sha256, size_bytes in (_identity_record(item, field="BLAT processing output"),)
    }
    for key, (sha256, size_bytes, _row_count) in processing_outputs.items():
        if key not in readbacks or (sha256, size_bytes) != (
            readbacks[key].sha256,
            readbacks[key].size_bytes,
        ):
            raise ProteinGymError("existing BLAT processing payload chain is invalid")
    membership_key, membership_sha, membership_size = _identity_record(
        split.get("membership_file"), field="BLAT membership file"
    )
    if membership_key not in readbacks or (membership_sha, membership_size) != (
        readbacks[membership_key].sha256,
        readbacks[membership_key].size_bytes,
    ):
        raise ProteinGymError("existing BLAT membership chain is invalid")
    if (
        processing.get("dataset_manifest_sha256") != readbacks[dataset_key].sha256
        or processing.get("processing_config_sha256") != config_sha256
        or processing.get("git_commit") != original_reviewed_git_commit
        or split.get("processing_manifest_sha256") != readbacks[processing_key].sha256
        or split.get("split_config_sha256") != config_sha256
        or split.get("git_commit") != original_reviewed_git_commit
    ):
        raise ProteinGymError("existing BLAT manifest chain is invalid")
    candidate_key = next(key for key in processing_outputs if key.endswith("/candidates.csv.gz"))
    candidate_count = processing_outputs[candidate_key][2]
    score_columns = coverage.get("score_columns")
    if (
        not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count < 1
        or not isinstance(score_columns, list)
        or not score_columns
        or any(not isinstance(item, str) or not item for item in score_columns)
    ):
        raise ProteinGymError("existing BLAT compact processing evidence is invalid")
    evidence: dict[str, object] = {
        "assay_id": assay_id,
        "paper_doi": assay.paper_doi,
        "truth": {
            "sha256": truth.sha256,
            "size_bytes": truth.size_bytes,
            "row_count": truth.row_count,
            "ranges": list(truth.requested_ranges),
        },
        "score": {
            "sha256": score.sha256,
            "size_bytes": score.size_bytes,
            "row_count": score.row_count,
            "ranges": list(score.requested_ranges),
            "columns": score_columns,
        },
        "candidate_count": candidate_count,
        "dataset_manifest_sha256": readbacks[dataset_key].sha256,
        "processing_manifest_sha256": readbacks[processing_key].sha256,
        "split_manifest_sha256": readbacks[split_key].sha256,
        "dataset_ingest_attempt_sha256": readbacks[ingest_key].sha256,
        "continuation_action": "reused_after_fresh_validation",
    }
    return tuple(files), evidence, paths[dataset_key], readbacks


def _reconcile_inventory(
    *,
    expected_files: tuple[ExpectedFile, ...],
    observed_keys: tuple[str, ...],
    publisher: FilePublisher,
    scratch_root: Path,
    preverified: Mapping[str, PublishedFile],
) -> dict[str, str]:
    expected_by_key: dict[str, ExpectedFile] = {}
    for expected in expected_files:
        if expected.object_key in expected_by_key:
            raise ProteinGymError("prepared inventory contains a duplicate object key")
        expected_by_key[expected.object_key] = expected
    observed = set(observed_keys)
    unexpected = observed - set(expected_by_key)
    if unexpected:
        raise ProteinGymError("exact prefix contains an object outside the prepared inventory")
    actions: dict[str, str] = {}
    for key, expected in expected_by_key.items():
        if key not in observed:
            actions[key] = "publish"
            continue
        actual = preverified.get(key)
        if actual is None:
            destination = scratch_root / "existing-readback" / Path(*PurePosixPath(key).parts)
            actual = publisher.retrieve(key, destination)
        if (actual.sha256, actual.size_bytes) != (expected.sha256, expected.size_bytes):
            raise ProteinGymError("existing object identity differs from the prepared inventory")
        actions[key] = "reuse"
    missing_seen = False
    for expected in expected_files:
        if actions[expected.object_key] == "publish":
            missing_seen = True
        elif missing_seen:
            raise ProteinGymError("existing objects do not form a valid publication-order prefix")
    return actions


def _publish_expected(
    *, expected: ExpectedFile, action: str, publisher: FilePublisher
) -> PublishedFile:
    if action == "reuse":
        return PublishedFile(expected.object_key, expected.sha256, expected.size_bytes)
    published = publisher.publish(expected.path, expected.object_key)
    if (published.sha256, published.size_bytes) != (expected.sha256, expected.size_bytes):
        raise ProteinGymError("published object identity differs from the prepared inventory")
    return published


def _final_evidence_bytes(
    *, evidence: dict[str, object], growth_before_final: int, prefix_before_final: int
) -> bytes:
    evidence["final_evidence_size_bytes"] = 0
    evidence["continuation_cos_growth_bytes"] = growth_before_final
    evidence["expected_prefix_bytes_after_final"] = prefix_before_final
    for _ in range(20):
        payload = _json_bytes(evidence)
        size_bytes = len(payload)
        values = (
            size_bytes,
            growth_before_final + size_bytes,
            prefix_before_final + size_bytes,
        )
        current = (
            evidence["final_evidence_size_bytes"],
            evidence["continuation_cos_growth_bytes"],
            evidence["expected_prefix_bytes_after_final"],
        )
        if current == values:
            return payload
        evidence["final_evidence_size_bytes"] = values[0]
        evidence["continuation_cos_growth_bytes"] = values[1]
        evidence["expected_prefix_bytes_after_final"] = values[2]
    raise ProteinGymError("final evidence size accounting did not converge")


def _validate_continuation_approvals(
    *,
    approval_comment_id: int,
    bundle_approval_comment_id: int,
    original_approval_comment_id: int,
    prior_continuation_approval_comment_ids: tuple[int, ...],
) -> None:
    for field, value in (
        ("approval_comment_id", approval_comment_id),
        ("bundle_approval_comment_id", bundle_approval_comment_id),
        ("original_approval_comment_id", original_approval_comment_id),
    ):
        _require_positive_comment_id(value, field=field)
    if approval_comment_id == original_approval_comment_id:
        raise ProteinGymError("original and continuation approval IDs must differ")
    seen_ids = {approval_comment_id, original_approval_comment_id}
    for prior_id in prior_continuation_approval_comment_ids:
        _require_positive_comment_id(prior_id, field="prior continuation approval ID")
        if prior_id in seen_ids:
            raise ProteinGymError("authorization history contains a duplicate comment ID")
        seen_ids.add(prior_id)
    if bundle_approval_comment_id != approval_comment_id and bundle_approval_comment_id not in set(
        prior_continuation_approval_comment_ids
    ):
        raise ProteinGymError(
            "replacement must retain the bundle authorization in its prior server-start history"
        )


def run_continuation(
    *,
    config_path: Path,
    repository_root: Path,
    scratch_root: Path,
    bundle_root: Path,
    cos_destination: str,
    reviewed_git_commit: str,
    original_reviewed_git_commit: str,
    approval: str,
    approval_comment_id: int,
    bundle_approval_comment_id: int,
    original_approval_comment_id: int,
    prior_continuation_approval_comment_ids: tuple[int, ...] = (),
    publisher_factory: Callable[[str, Path], FilePublisher] = CoscliFilePublisher,
) -> dict[str, object]:
    """Continue Issue #11 from a local bundle with prefix-wide exact-match reuse."""
    if approval != CONTINUATION_APPROVAL:
        raise ProteinGymError("exact continuation approval string is required")
    _validate_continuation_approvals(
        approval_comment_id=approval_comment_id,
        bundle_approval_comment_id=bundle_approval_comment_id,
        original_approval_comment_id=original_approval_comment_id,
        prior_continuation_approval_comment_ids=prior_continuation_approval_comment_ids,
    )
    if not re.fullmatch(r"[0-9a-f]{40}", original_reviewed_git_commit):
        raise ProteinGymError("original_reviewed_git_commit must be a full lowercase SHA-1")
    _verify_repository(repository_root, reviewed_git_commit)
    config_bytes = config_path.read_bytes()
    config = load_config(config_path)
    try:
        config_repository_path = (
            config_path.resolve().relative_to(repository_root.resolve()).as_posix()
        )
    except ValueError as error:
        raise ProteinGymError("config must be inside the reviewed repository") from error
    _prepare_new_scratch(
        scratch_root, repository_root, required_free_bytes=config.caps.scratch_bytes
    )
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    bundle = _load_local_bundle(
        bundle_root=bundle_root,
        repository_root=repository_root,
        config=config,
        config_sha256=config_sha256,
        reviewed_git_commit=reviewed_git_commit,
        approval_comment_id=bundle_approval_comment_id,
        original_approval_comment_id=original_approval_comment_id,
    )
    publisher = publisher_factory(cos_destination, scratch_root / "fresh-readback")
    observed_keys = publisher.list_keys(config.object_prefix)
    final_key = f"{config.object_prefix}/evidence/unified-attempt.json"
    if final_key in observed_keys:
        raise ProteinGymError("unified final evidence already exists; continuation is complete")
    blat_truth, blat_score = bundle.members[config.assays[0].assay_id]
    blat_files, blat_evidence, blat_dataset_manifest_path, preverified = _load_existing_blat(
        config=config,
        config_sha256=config_sha256,
        original_reviewed_git_commit=original_reviewed_git_commit,
        observed_keys=observed_keys,
        publisher=publisher,
        scratch_root=scratch_root,
        truth=blat_truth,
        score=blat_score,
    )
    prepared: list[PreparedAssay] = []
    for assay in config.assays[1:]:
        truth, score = bundle.members[assay.assay_id]
        prepared.append(
            _prepare_continuation_assay(
                config=config,
                assay=assay,
                truth=truth,
                score=score,
                acquired_at=bundle.acquired_at[assay.assay_id],
                config_bytes=config_bytes,
                config_repository_path=config_repository_path,
                reviewed_git_commit=reviewed_git_commit,
                cos_destination=cos_destination,
                scratch_root=scratch_root,
            )
        )
    expected_files = blat_files + tuple(expected for assay in prepared for expected in assay.files)
    expected_prefix_bytes_before_final = sum(item.size_bytes for item in expected_files)
    if expected_prefix_bytes_before_final >= config.caps.cos_growth_bytes:
        raise ProteinGymError("prepared exact-prefix inventory exceeds the COS cap")
    if _directory_size(scratch_root) > config.caps.scratch_bytes:
        raise ProteinGymError("continuation scratch cap was exceeded before publication")
    actions = _reconcile_inventory(
        expected_files=expected_files,
        observed_keys=observed_keys,
        publisher=publisher,
        scratch_root=scratch_root,
        preverified=preverified,
    )
    publisher.verify_duplicate_rejected(
        blat_dataset_manifest_path,
        next(item.object_key for item in blat_files if item.path == blat_dataset_manifest_path),
    )
    for prepared_assay in prepared:
        ingest_file = prepared_assay.files[-1]
        for expected in prepared_assay.files[:-1]:
            _publish_expected(
                expected=expected, action=actions[expected.object_key], publisher=publisher
            )
        dataset_manifest_file = next(
            item
            for item in prepared_assay.files
            if item.object_key.endswith("/dataset-manifest-v1.json")
        )
        publisher.verify_duplicate_rejected(
            dataset_manifest_file.path, dataset_manifest_file.object_key
        )
        _publish_expected(
            expected=ingest_file,
            action=actions[ingest_file.object_key],
            publisher=publisher,
        )
    observed_before_final = publisher.list_keys(config.object_prefix)
    if set(observed_before_final) != {item.object_key for item in expected_files}:
        raise ProteinGymError(
            "exact prefix differs from the prepared inventory before final evidence"
        )
    growth_before_final = sum(
        item.size_bytes for item in expected_files if actions[item.object_key] == "publish"
    )
    assay_evidence = [blat_evidence, *(item.evidence for item in prepared)]
    inventory = [
        {
            "object_key": item.object_key,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "assay_id": item.assay_id,
            "phase": item.phase,
            "action": actions[item.object_key],
        }
        for item in expected_files
    ]
    evidence: dict[str, object] = {
        "contract_version": "2",
        "status": "succeeded",
        "issue": 11,
        "attempt_id": f"proteingym-server-continuation-{approval_comment_id}",
        "approval": approval,
        "approval_comment_id": approval_comment_id,
        "original_consumed_approval_comment_id": original_approval_comment_id,
        "prior_continuation_approval_comment_ids": list(prior_continuation_approval_comment_ids),
        "reviewed_git_commit": reviewed_git_commit,
        "original_reviewed_git_commit": original_reviewed_git_commit,
        "config_sha256": config_sha256,
        "local_bundle": {
            "attempt_id": bundle.attempt_id,
            "approval_comment_id": bundle_approval_comment_id,
            "sha256": bundle.manifest_sha256,
            "size_bytes": bundle.manifest_size_bytes,
            "source_network_bytes": bundle.source_network_bytes,
            "source_network_cap_bytes": config.caps.network_bytes,
            "source_network_cap_scope": "v3_local_acquisition_only",
            "historical_consumed_attempt_accounting": "separate_not_counted_in_v3_cap",
            "local_to_server_transfer_bytes": bundle.transfer_bytes,
        },
        "server_source_network_bytes": 0,
        "existing_prefix_bytes_before_continuation": sum(
            item.size_bytes for item in expected_files if actions[item.object_key] == "reuse"
        ),
        "scratch_bytes_before_final": _directory_size(scratch_root),
        "automatic_retries": 0,
        "full_archive_downloaded": False,
        "held_out_accessed": False,
        "overwrite_succeeded": False,
        "delete_attempted": False,
        "assays": assay_evidence,
        "object_inventory_excluding_final_evidence": inventory,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "coscli": "invoked with --err-retry-num 0 for transfers",
        },
    }
    evidence_bytes = _final_evidence_bytes(
        evidence=evidence,
        growth_before_final=growth_before_final,
        prefix_before_final=expected_prefix_bytes_before_final,
    )
    if int(cast(int, evidence["expected_prefix_bytes_after_final"])) > config.caps.cos_growth_bytes:
        raise ProteinGymError("final exact-prefix bytes exceed the COS cap")
    final_path = scratch_root / "manifest-staging" / Path(*PurePosixPath(final_key).parts)
    _write_bytes_once(final_path, evidence_bytes)
    if _directory_size(scratch_root) > config.caps.scratch_bytes:
        raise ProteinGymError("continuation scratch cap was exceeded by final evidence")
    publisher.publish(final_path, final_key)
    observed_after_final = publisher.list_keys(config.object_prefix)
    if set(observed_after_final) != {
        *(item.object_key for item in expected_files),
        final_key,
    }:
        raise ProteinGymError("terminal exact prefix differs from the complete inventory")
    return evidence


def run_unified(
    *,
    config_path: Path,
    repository_root: Path,
    scratch_root: Path,
    cos_destination: str,
    reviewed_git_commit: str,
    approval: str,
    approval_comment_id: int,
    open_url: OpenUrl = _default_open_url,
    publisher_factory: Callable[[str, Path], FilePublisher] = CoscliFilePublisher,
) -> dict[str, object]:
    """Run one authorized BLAT-first acquisition and data-readiness publication."""
    if approval != APPROVAL or approval_comment_id < 1:
        raise ProteinGymError("exact approval and a positive approval comment ID are required")
    _verify_repository(repository_root, reviewed_git_commit)
    config_bytes = config_path.read_bytes()
    config = load_config(config_path)
    try:
        config_repository_path = (
            config_path.resolve().relative_to(repository_root.resolve()).as_posix()
        )
    except ValueError as error:
        raise ProteinGymError("config must be inside the reviewed repository") from error
    if shutil.disk_usage(scratch_root.parent).free < config.caps.scratch_bytes:
        raise ProteinGymError("free local space is below the frozen scratch cap")
    scratch_root.mkdir(parents=True, exist_ok=False)
    publisher = publisher_factory(cos_destination, scratch_root / "fresh-readback")
    archives = {archive.archive_id: archive for archive in config.archives}
    reader = RangeReader(byte_cap=config.caps.network_bytes, open_url=open_url)
    cos_growth = 0
    assay_evidence: list[dict[str, object]] = []

    for assay_index, assay in enumerate(config.assays):
        raw_root = scratch_root / "raw" / assay.assay_id
        truth = acquire_member(
            archive=archives[assay.truth.archive_id],
            member=assay.truth,
            destination=raw_root / "truth.csv",
            reader=reader,
        )
        score = acquire_member(
            archive=archives[assay.score.archive_id],
            member=assay.score,
            destination=raw_root / "zero-shot-scores.csv",
            reader=reader,
        )
        if assay.expected_truth_rows is not None and truth.row_count != assay.expected_truth_rows:
            raise ProteinGymError("BLAT truth row count failed the internal checkpoint")
        downloaded_at = datetime.now(UTC)
        dataset_manifest_bytes = build_dataset_manifest(
            config, assay, truth, score, downloaded_at=downloaded_at
        )
        processing = process_assay(
            config=config,
            assay=assay,
            truth_path=truth.path,
            score_path=score.path,
            dataset_manifest_bytes=dataset_manifest_bytes,
            config_bytes=config_bytes,
            git_commit=reviewed_git_commit,
            output_root=scratch_root / "processed",
        )
        raw_prefix = f"{config.object_prefix}/raw/{assay.assay_id}"
        publication: list[tuple[str, Path]] = [
            (f"{raw_prefix}/truth.csv", truth.path),
            (f"{raw_prefix}/zero-shot-scores.csv", score.path),
            *processing.payloads,
        ]
        for object_key, path in publication:
            size = path.stat().st_size
            assay_cap = (
                config.caps.blat_cos_growth_bytes
                if assay_index == 0
                else config.caps.cos_growth_bytes
            )
            if cos_growth + size > assay_cap:
                raise ProteinGymError("COS growth cap would be exceeded")
            published = publisher.publish(path, object_key)
            cos_growth += published.size_bytes

        dataset_manifest_key = (
            f"{config.object_prefix}/manifests/{assay.assay_id}/dataset-manifest-v1.json"
        )
        manifests = (
            (dataset_manifest_key, dataset_manifest_bytes),
            (processing.processing_manifest_key, processing.processing_manifest_bytes),
            (processing.split_manifest_key, processing.split_manifest_bytes),
        )
        for object_key, payload in manifests:
            manifest_cap = (
                config.caps.blat_cos_growth_bytes
                if assay_index == 0
                else config.caps.cos_growth_bytes
            )
            if cos_growth + len(payload) > manifest_cap:
                raise ProteinGymError("COS growth cap would be exceeded")
            published = _publish_bytes(
                payload=payload,
                object_key=object_key,
                scratch_root=scratch_root,
                publisher=publisher,
            )
            cos_growth += published.size_bytes
        dataset_manifest_path = (
            scratch_root / "manifest-staging" / Path(*PurePosixPath(dataset_manifest_key).parts)
        )
        duplicate_write_status = publisher.verify_duplicate_rejected(
            dataset_manifest_path, dataset_manifest_key
        )
        ingest_attempt_bytes = build_ingest_attempt(
            config=config,
            assay=assay,
            truth=truth,
            score=score,
            dataset_manifest_key=dataset_manifest_key,
            dataset_manifest_bytes=dataset_manifest_bytes,
            config_repository_path=config_repository_path,
            config_sha256=hashlib.sha256(config_bytes).hexdigest(),
            git_commit=reviewed_git_commit,
            cos_destination=cos_destination,
            duplicate_write_status=duplicate_write_status,
            verified_at=datetime.now(UTC),
        )
        ingest_attempt_key = (
            f"{config.object_prefix}/evidence/{assay.assay_id}/dataset-ingest-attempt.json"
        )
        if cos_growth + len(ingest_attempt_bytes) > manifest_cap:
            raise ProteinGymError("COS growth cap would be exceeded")
        published_attempt = _publish_bytes(
            payload=ingest_attempt_bytes,
            object_key=ingest_attempt_key,
            scratch_root=scratch_root,
            publisher=publisher,
        )
        cos_growth += published_attempt.size_bytes
        assay_evidence.append(
            {
                "assay_id": assay.assay_id,
                "paper_doi": assay.paper_doi,
                "truth": {
                    "sha256": truth.sha256,
                    "size_bytes": truth.size_bytes,
                    "row_count": truth.row_count,
                    "ranges": list(truth.requested_ranges),
                },
                "score": {
                    "sha256": score.sha256,
                    "size_bytes": score.size_bytes,
                    "row_count": score.row_count,
                    "ranges": list(score.requested_ranges),
                    "columns": list(processing.score_columns),
                },
                "candidate_count": processing.candidate_count,
                "dataset_manifest_sha256": hashlib.sha256(dataset_manifest_bytes).hexdigest(),
                "processing_manifest_sha256": hashlib.sha256(
                    processing.processing_manifest_bytes
                ).hexdigest(),
                "split_manifest_sha256": hashlib.sha256(
                    processing.split_manifest_bytes
                ).hexdigest(),
                "dataset_ingest_attempt_sha256": hashlib.sha256(ingest_attempt_bytes).hexdigest(),
            }
        )
        scratch_size = _directory_size(scratch_root)
        scratch_cap = (
            config.caps.blat_scratch_bytes if assay_index == 0 else config.caps.scratch_bytes
        )
        if scratch_size > scratch_cap:
            raise ProteinGymError("scratch cap was exceeded")

    evidence: dict[str, object] = {
        "contract_version": "1",
        "status": "succeeded",
        "issue": 11,
        "approval": approval,
        "approval_comment_id": approval_comment_id,
        "reviewed_git_commit": reviewed_git_commit,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "network_bytes": reader.bytes_read,
        "cos_growth_bytes": cos_growth,
        "scratch_bytes": _directory_size(scratch_root),
        "automatic_retries": 0,
        "full_archive_downloaded": False,
        "held_out_accessed": False,
        "assays": assay_evidence,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "coscli": "invoked with --err-retry-num 0",
        },
    }
    evidence_bytes = _json_bytes(evidence)
    evidence_key = f"{config.object_prefix}/evidence/unified-attempt.json"
    if cos_growth + len(evidence_bytes) > config.caps.cos_growth_bytes:
        raise ProteinGymError("COS growth cap would be exceeded")
    _publish_bytes(
        payload=evidence_bytes,
        object_key=evidence_key,
        scratch_root=scratch_root,
        publisher=publisher,
    )
    return evidence


def run_cli(arguments: object) -> int:
    """Execute the unified runtime from argparse arguments."""
    args = cast(CliArguments, arguments)
    run_unified(
        config_path=args.config,
        repository_root=args.repository_root,
        scratch_root=args.scratch_root,
        cos_destination=args.cos_destination,
        reviewed_git_commit=args.reviewed_git_commit,
        approval=args.approval,
        approval_comment_id=args.approval_comment_id,
    )
    return 0


def run_local_bundle_cli(arguments: object) -> int:
    """Execute the authorized local acquisition stage from argparse arguments."""
    args = cast(LocalBundleCliArguments, arguments)
    run_local_bundle(
        config_path=args.config,
        repository_root=args.repository_root,
        scratch_root=args.scratch_root,
        reviewed_git_commit=args.reviewed_git_commit,
        approval=args.approval,
        approval_comment_id=args.approval_comment_id,
        original_approval_comment_id=args.original_approval_comment_id,
    )
    return 0


def run_continuation_cli(arguments: object) -> int:
    """Execute the authorized server continuation from argparse arguments."""
    args = cast(ContinuationCliArguments, arguments)
    run_continuation(
        config_path=args.config,
        repository_root=args.repository_root,
        scratch_root=args.scratch_root,
        bundle_root=args.bundle_root,
        cos_destination=args.cos_destination,
        reviewed_git_commit=args.reviewed_git_commit,
        original_reviewed_git_commit=args.original_reviewed_git_commit,
        approval=args.approval,
        approval_comment_id=args.approval_comment_id,
        bundle_approval_comment_id=args.bundle_approval_comment_id,
        original_approval_comment_id=args.original_approval_comment_id,
        prior_continuation_approval_comment_ids=tuple(args.prior_continuation_approval_comment_id),
    )
    return 0
