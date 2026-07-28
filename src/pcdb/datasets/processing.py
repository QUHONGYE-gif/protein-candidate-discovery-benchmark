"""Shared deterministic processing primitives for public sequence datasets."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Protocol, cast


class ProcessingError(ValueError):
    """Raised when frozen processing cannot proceed without ambiguity."""


class ProcessingConfigLike(Protocol):
    """Fields required by shared processing identity and rendering behavior."""

    @property
    def dataset_id(self) -> str: ...

    @property
    def dataset_version(self) -> str: ...

    @property
    def task_id(self) -> str: ...

    @property
    def dataset_manifest_sha256(self) -> str: ...

    @property
    def source_csv_keys(self) -> tuple[str, ...]: ...

    @property
    def candidate_id_namespace(self) -> str: ...

    @property
    def observable_feature_fields(self) -> tuple[str, ...]: ...

    @property
    def hidden_label_field(self) -> str: ...

    @property
    def label_direction(self) -> str: ...

    @property
    def near_duplicate_method(self) -> str: ...

    @property
    def near_duplicate_threshold(self) -> int: ...


@dataclass(frozen=True)
class Candidate:
    """One canonical candidate and its hidden public truth."""

    candidate_id: str
    sequence: str
    target: str
    observable_metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SourceMembership:
    """Upstream split metadata retained only for later split audit."""

    candidate_id: str
    source_key: str
    set_value: str
    validation_value: str


@dataclass(frozen=True)
class ProcessingAudit:
    """Canonical candidates, upstream metadata, and compact audit facts."""

    candidates: tuple[Candidate, ...]
    memberships: tuple[SourceMembership, ...]
    report: dict[str, object]


@dataclass(frozen=True)
class ProcessingIdentity:
    """Checksum-bound identity and raw artifacts for one processing revision."""

    dataset_manifest_sha256: str
    processing_config_sha256: str
    git_commit: str
    processing_id: str
    output_prefix: str
    raw_csv_artifacts: dict[str, tuple[str, int]]


@dataclass(frozen=True)
class SourceRead:
    """Validated rows and compact audit facts from one upstream split reference."""

    targets_by_sequence: dict[str, str]
    metadata: tuple[tuple[str, str, str], ...]
    report: dict[str, object]


@dataclass(frozen=True)
class ReconciledSources:
    """One canonical universe reconciled across alternate upstream split files."""

    candidates: tuple[Candidate, ...]
    memberships: tuple[SourceMembership, ...]
    sequences: tuple[str, ...]


def string(mapping: dict[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ProcessingError(f"{field} must be a non-empty string")
    return value


def string_list(mapping: dict[str, object], field: str) -> tuple[str, ...]:
    value = mapping.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ProcessingError(f"{field} must contain unique non-empty strings")
    return tuple(cast(list[str], value))


def lower_hex_digest(value: str, *, field: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ProcessingError(f"{field} must be a lowercase SHA-256 digest")
    return value


def git_commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ProcessingError("git_commit must be a full lowercase 40-character commit")
    return value


def safe_source_key(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ProcessingError("source_csv_keys must be safe relative POSIX paths")
    if path.suffixes[-2:] != [".csv", ".gz"]:
        raise ProcessingError("source_csv_keys must identify gzip CSV files")
    return value


def canonical_decimal(value: str, *, source_key: str, row_number: int) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ProcessingError(
            f"{source_key}:{row_number}: target must be a finite decimal"
        ) from error
    if not number.is_finite():
        raise ProcessingError(f"{source_key}:{row_number}: target must be finite")
    canonical = format(number.normalize(), "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return "0" if canonical == "-0" else canonical


def candidate_id(config: ProcessingConfigLike, sequence: str) -> str:
    payload = f"{config.candidate_id_namespace}\0{sequence}".encode()
    return hashlib.sha256(payload).hexdigest()


def raw_csv_artifacts(
    config: ProcessingConfigLike,
    dataset_manifest_bytes: bytes,
) -> dict[str, tuple[str, int]]:
    try:
        raw_manifest = cast(object, json.loads(dataset_manifest_bytes))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProcessingError("Dataset Manifest must be valid UTF-8 JSON") from error
    if not isinstance(raw_manifest, dict):
        raise ProcessingError("Dataset Manifest must be an object")
    manifest = cast(dict[str, object], raw_manifest)
    expected_identity = {
        "dataset_id": config.dataset_id,
        "dataset_version": config.dataset_version,
        "task_id": config.task_id,
    }
    for field, expected in expected_identity.items():
        if manifest.get(field) != expected:
            raise ProcessingError(f"Dataset Manifest {field} does not match processing config")

    raw_files = manifest.get("raw_files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ProcessingError("Dataset Manifest raw_files must be a non-empty array")
    by_object_key: dict[str, tuple[str, int]] = {}
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise ProcessingError("Dataset Manifest raw_files entries must be objects")
        item = cast(dict[str, object], raw_file)
        object_key = item.get("object_key")
        sha256 = item.get("sha256")
        size_bytes = item.get("size_bytes")
        if not isinstance(object_key, str) or not object_key:
            raise ProcessingError("Dataset Manifest object_key must be a non-empty string")
        if object_key in by_object_key:
            raise ProcessingError("Dataset Manifest raw object keys must be unique")
        if not isinstance(sha256, str):
            raise ProcessingError("Dataset Manifest raw SHA-256 must be a string")
        lower_hex_digest(sha256, field="raw file sha256")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise ProcessingError("Dataset Manifest raw size must be a non-negative integer")
        by_object_key[object_key] = (sha256, size_bytes)

    object_prefix = f"raw/{config.dataset_id}/{config.dataset_version}"
    artifacts: dict[str, tuple[str, int]] = {}
    for source_key in config.source_csv_keys:
        object_key = f"{object_prefix}/{source_key}"
        artifact = by_object_key.get(object_key)
        if artifact is None:
            raise ProcessingError(
                f"Dataset Manifest does not contain configured source file: {source_key}"
            )
        artifacts[source_key] = artifact
    return artifacts


def verify_raw_file(path: Path, source_key: str, expected: tuple[str, int]) -> None:
    expected_sha256, expected_size = expected
    sha256 = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
            size_bytes += len(chunk)
    if size_bytes != expected_size:
        raise ProcessingError(f"{source_key}: size does not match Dataset Manifest")
    if sha256.hexdigest() != expected_sha256:
        raise ProcessingError(f"{source_key}: SHA-256 does not match Dataset Manifest")


def read_split_reference_source(
    *,
    path: Path,
    source_key: str,
    required_fields: tuple[str, ...],
    allowed_amino_acids: str,
    sequence_length: int,
    allowed_set_values: tuple[str, ...],
    allowed_validation_values: tuple[str, ...],
) -> SourceRead:
    if not path.is_file():
        raise ProcessingError(f"missing source file: {source_key}")
    by_sequence: dict[str, str] = {}
    metadata: list[tuple[str, str, str]] = []
    set_counts: Counter[str] = Counter()
    validation_counts: Counter[str] = Counter()
    allowed_alphabet = set(allowed_amino_acids)

    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != required_fields:
            raise ProcessingError(f"{source_key}: fields do not match the frozen schema")
        for row_number, row in enumerate(reader, start=2):
            if any(
                row.get(field) is None or not cast(str, row[field]).strip()
                for field in required_fields
            ):
                raise ProcessingError(f"{source_key}:{row_number}: missing value")
            sequence = cast(str, row["sequence"])
            if sequence != sequence.strip().upper():
                raise ProcessingError(
                    f"{source_key}:{row_number}: sequence is not canonical uppercase text"
                )
            if len(sequence) != sequence_length or not set(sequence) <= allowed_alphabet:
                raise ProcessingError(f"{source_key}:{row_number}: invalid sequence")
            target = canonical_decimal(
                cast(str, row["target"]).strip(),
                source_key=source_key,
                row_number=row_number,
            )
            if sequence in by_sequence:
                raise ProcessingError(
                    f"{source_key}:{row_number}: duplicate sequence within one source file"
                )
            set_value = cast(str, row["set"]).strip()
            validation_value = cast(str, row["validation"]).strip()
            if set_value not in allowed_set_values:
                raise ProcessingError(f"{source_key}:{row_number}: unexpected set value")
            if validation_value not in allowed_validation_values:
                raise ProcessingError(f"{source_key}:{row_number}: unexpected validation value")
            by_sequence[sequence] = target
            metadata.append((sequence, set_value, validation_value))
            set_counts[set_value] += 1
            validation_counts[validation_value] += 1

    return SourceRead(
        targets_by_sequence=by_sequence,
        metadata=tuple(metadata),
        report={
            "row_count": len(by_sequence),
            "unique_sequence_count": len(by_sequence),
            "missing_value_count": 0,
            "invalid_sequence_count": 0,
            "non_finite_target_count": 0,
            "within_file_duplicate_count": 0,
            "set_counts": dict(sorted(set_counts.items())),
            "validation_counts": dict(sorted(validation_counts.items())),
        },
    )


def reconcile_split_reference_sources(
    *,
    config: ProcessingConfigLike,
    sources: dict[str, SourceRead],
    expected_candidate_count: int,
) -> ReconciledSources:
    if tuple(sources) != config.source_csv_keys:
        raise ProcessingError("source reads must preserve the configured source enumeration")
    first_key = config.source_csv_keys[0]
    canonical_mapping = sources[first_key].targets_by_sequence
    canonical_sequences = set(canonical_mapping)
    for source_key, source in sources.items():
        mapping = source.targets_by_sequence
        if set(mapping) != canonical_sequences:
            raise ProcessingError(f"{source_key}: candidate universe differs from {first_key}")
        if any(
            mapping[sequence] != canonical_mapping[sequence] for sequence in canonical_sequences
        ):
            raise ProcessingError(f"{source_key}: hidden truth conflicts with {first_key}")
    if len(canonical_sequences) != expected_candidate_count:
        raise ProcessingError("candidate count differs from the frozen processing config")

    candidates = tuple(
        sorted(
            (
                Candidate(
                    candidate_id=candidate_id(config, sequence),
                    sequence=sequence,
                    target=canonical_mapping[sequence],
                )
                for sequence in canonical_sequences
            ),
            key=lambda item: item.candidate_id,
        )
    )
    if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise ProcessingError("candidate ID collision")
    id_by_sequence = {candidate.sequence: candidate.candidate_id for candidate in candidates}
    memberships = tuple(
        sorted(
            (
                SourceMembership(
                    candidate_id=id_by_sequence[sequence],
                    source_key=source_key,
                    set_value=set_value,
                    validation_value=validation_value,
                )
                for source_key, source in sources.items()
                for sequence, set_value, validation_value in source.metadata
            ),
            key=lambda item: (item.candidate_id, item.source_key),
        )
    )
    return ReconciledSources(
        candidates=candidates,
        memberships=memberships,
        sequences=tuple(sorted(canonical_sequences)),
    )


def modal_reference(sequences: tuple[str, ...], *, require_unique: bool = False) -> str:
    if not sequences:
        raise ProcessingError("cannot resolve a reference from an empty candidate universe")
    reference: list[str] = []
    for index in range(len(sequences[0])):
        counts = Counter(sequence[index] for sequence in sequences)
        most_common = counts.most_common()
        if require_unique and len(most_common) > 1 and most_common[0][1] == most_common[1][1]:
            raise ProcessingError(f"reference residue is ambiguous at position {index + 1}")
        reference.append(most_common[0][0])
    return "".join(reference)


def near_duplicate_counts(
    sequences: tuple[str, ...], reference: str, threshold: int
) -> dict[str, int]:
    signatures = [
        tuple(
            (index, amino_acid)
            for index, amino_acid in enumerate(sequence)
            if amino_acid != reference[index]
        )
        for sequence in sequences
    ]
    if len(set(signatures)) != len(signatures):
        raise ProcessingError("near-duplicate audit requires unique sequences")

    if threshold == 1:
        signature_lookup = set(signatures)
        addition_or_removal_pairs = 0
        replacement_groups: Counter[tuple[int, tuple[tuple[int, str], ...]]] = Counter()
        for signature in signatures:
            for index, (position, _) in enumerate(signature):
                without_mutation = signature[:index] + signature[index + 1 :]
                if without_mutation in signature_lookup:
                    addition_or_removal_pairs += 1
                replacement_groups[(position, without_mutation)] += 1
        replacement_pairs = sum(count * (count - 1) // 2 for count in replacement_groups.values())
        return {"1": addition_or_removal_pairs + replacement_pairs}

    signature_maps = [dict(signature) for signature in signatures]
    counts: Counter[int] = Counter()
    for index, left in enumerate(signature_maps):
        left_positions = set(left)
        for right in signature_maps[index + 1 :]:
            distance = 0
            for position in left_positions | right.keys():
                if left.get(position, reference[position]) != right.get(
                    position, reference[position]
                ):
                    distance += 1
                    if distance > threshold:
                        break
            if 0 < distance <= threshold:
                counts[distance] += 1
    return {str(distance): counts[distance] for distance in range(1, threshold + 1)}


def prepare_processing_identity(
    *,
    config: ProcessingConfigLike,
    config_bytes: bytes,
    dataset_manifest_bytes: bytes,
    git_commit_value: str,
) -> ProcessingIdentity:
    git_commit(git_commit_value)
    dataset_manifest_sha256 = hashlib.sha256(dataset_manifest_bytes).hexdigest()
    if dataset_manifest_sha256 != config.dataset_manifest_sha256:
        raise ProcessingError("Dataset Manifest checksum does not match processing config")
    artifacts = raw_csv_artifacts(config, dataset_manifest_bytes)
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    identity = {
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "processing_config_sha256": config_sha256,
        "git_commit": git_commit_value,
    }
    processing_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    processing_id = f"{config.dataset_id}-{config.task_id}-{processing_hash}"
    output_prefix = (
        f"processed/{config.dataset_id}/{config.dataset_version}/{config.task_id}/{processing_id}"
    )
    return ProcessingIdentity(
        dataset_manifest_sha256=dataset_manifest_sha256,
        processing_config_sha256=config_sha256,
        git_commit=git_commit_value,
        processing_id=processing_id,
        output_prefix=output_prefix,
        raw_csv_artifacts=artifacts,
    )


def csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def json_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def artifact(object_key: str, payload: bytes, row_count: int) -> dict[str, object]:
    return {
        "object_key": object_key,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "row_count": row_count,
    }


def render_processing_bundle(
    *,
    config: ProcessingConfigLike,
    identity: ProcessingIdentity,
    audit: ProcessingAudit,
    known_limitations: list[str],
) -> tuple[dict[str, bytes], dict[str, object]]:
    candidate_rows: list[dict[str, str]] = []
    for candidate in audit.candidates:
        available = {"sequence": candidate.sequence, **dict(candidate.observable_metadata)}
        expected = set(config.observable_feature_fields)
        if set(available) != expected:
            raise ProcessingError("candidate observable fields do not match processing config")
        candidate_rows.append(
            {
                "candidate_id": candidate.candidate_id,
                **{field: available[field] for field in config.observable_feature_fields},
            }
        )
    hidden_truth_rows = [
        {"candidate_id": candidate.candidate_id, "target": candidate.target}
        for candidate in audit.candidates
    ]
    membership_rows = [
        {
            "candidate_id": item.candidate_id,
            "source_key": item.source_key,
            "set": item.set_value,
            "validation": item.validation_value,
        }
        for item in audit.memberships
    ]
    candidate_key = f"{identity.output_prefix}/candidates.csv"
    hidden_truth_key = f"{identity.output_prefix}/hidden-truth.csv"
    membership_key = f"{identity.output_prefix}/upstream-split-metadata.csv"
    audit_key = f"{identity.output_prefix}/processing-audit.json"
    payloads = {
        candidate_key: csv_bytes(
            ("candidate_id", *config.observable_feature_fields),
            candidate_rows,
        ),
        hidden_truth_key: csv_bytes(("candidate_id", "target"), hidden_truth_rows),
        membership_key: csv_bytes(
            ("candidate_id", "source_key", "set", "validation"), membership_rows
        ),
        audit_key: json_bytes(audit.report),
    }
    row_counts = {
        candidate_key: len(candidate_rows),
        hidden_truth_key: len(hidden_truth_rows),
        membership_key: len(membership_rows),
        audit_key: 1,
    }
    manifest: dict[str, object] = {
        "schema_version": "1",
        "processing_id": identity.processing_id,
        "dataset_manifest_sha256": identity.dataset_manifest_sha256,
        "processing_config_sha256": identity.processing_config_sha256,
        "git_commit": identity.git_commit,
        "candidate_id_field": "candidate_id",
        "candidate_id_unique": True,
        "observable_feature_fields": list(config.observable_feature_fields),
        "hidden_label_field": config.hidden_label_field,
        "label_direction": config.label_direction,
        "duplicate_policy": {
            "exact_duplicates": "aggregate",
            "near_duplicates": "allowed",
            "near_duplicate_method": (
                f"{config.near_duplicate_method} <= {config.near_duplicate_threshold}; "
                "grouping deferred to Split Manifest"
            ),
        },
        "output_files": [
            artifact(object_key, payloads[object_key], row_counts[object_key])
            for object_key in sorted(payloads)
        ],
        "known_limitations": known_limitations,
    }
    return payloads, manifest


def validate_processing_manifest_domain(manifest: dict[str, object]) -> None:
    """Apply fail-closed invariants required for newly generated processing revisions."""
    hidden_label = manifest.get("hidden_label_field")
    observable_fields = manifest.get("observable_feature_fields")
    if not isinstance(hidden_label, str) or not isinstance(observable_fields, list):
        raise ProcessingError("Processing Manifest field visibility is invalid")
    if hidden_label in observable_fields:
        raise ProcessingError("hidden label cannot be observable")
    if manifest.get("candidate_id_unique") is not True:
        raise ProcessingError("Processing Manifest must assert unique candidate IDs")

    output_files = manifest.get("output_files")
    if not isinstance(output_files, list) or not output_files:
        raise ProcessingError("Processing Manifest output_files must be a non-empty array")
    object_keys: list[str] = []
    for output in output_files:
        if not isinstance(output, dict):
            raise ProcessingError("Processing Manifest outputs must be objects")
        object_key = output.get("object_key")
        row_count = output.get("row_count")
        if not isinstance(object_key, str) or not object_key:
            raise ProcessingError("Processing Manifest output object keys must be non-empty")
        if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
            raise ProcessingError("new Processing Manifest outputs must include row_count")
        object_keys.append(object_key)
    if len(set(object_keys)) != len(object_keys):
        raise ProcessingError("Processing Manifest output object keys must be unique")
