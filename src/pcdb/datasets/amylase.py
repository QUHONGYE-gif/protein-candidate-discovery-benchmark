"""Deterministic processing for the FLIP2 v3 Alpha Amylase task."""

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
from typing import cast


class AmylaseProcessingError(ValueError):
    """Raised when Alpha Amylase processing cannot proceed without ambiguity."""


@dataclass(frozen=True)
class AmylaseProcessingConfig:
    """Frozen rules for one Alpha Amylase processing revision."""

    dataset_id: str
    dataset_version: str
    task_id: str
    dataset_manifest_sha256: str
    source_csv_keys: tuple[str, ...]
    required_fields: tuple[str, ...]
    allowed_amino_acids: str
    sequence_length: int
    expected_candidate_count: int
    candidate_id_namespace: str
    observable_feature_fields: tuple[str, ...]
    hidden_label_field: str
    label_direction: str
    label_semantics: str
    label_source_url: str
    allowed_set_values: tuple[str, ...]
    allowed_validation_values: tuple[str, ...]
    near_duplicate_method: str
    near_duplicate_threshold: int


@dataclass(frozen=True)
class Candidate:
    """One canonical candidate and its hidden public truth."""

    candidate_id: str
    sequence: str
    target: str


@dataclass(frozen=True)
class SourceMembership:
    """Upstream split metadata retained only for later split audit."""

    candidate_id: str
    source_key: str
    set_value: str
    validation_value: str


@dataclass(frozen=True)
class AmylaseAudit:
    """Canonical candidates, upstream metadata, and compact audit facts."""

    candidates: tuple[Candidate, ...]
    memberships: tuple[SourceMembership, ...]
    report: dict[str, object]


_CONFIG_KEYS = {
    "config_version",
    "dataset_id",
    "dataset_version",
    "task_id",
    "dataset_manifest_sha256",
    "source_csv_keys",
    "required_fields",
    "allowed_amino_acids",
    "sequence_length",
    "expected_candidate_count",
    "candidate_id_algorithm",
    "candidate_id_namespace",
    "observable_feature_fields",
    "hidden_label_field",
    "label_direction",
    "label_semantics",
    "label_source_url",
    "missing_value_policy",
    "allowed_set_values",
    "allowed_validation_values",
    "within_file_duplicate_policy",
    "cross_file_duplicate_policy",
    "conflicting_target_policy",
    "near_duplicate_method",
    "near_duplicate_threshold",
    "near_duplicate_policy",
}


def _string(mapping: dict[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise AmylaseProcessingError(f"{field} must be a non-empty string")
    return value


def _string_list(mapping: dict[str, object], field: str) -> tuple[str, ...]:
    value = mapping.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise AmylaseProcessingError(f"{field} must contain unique non-empty strings")
    return tuple(cast(list[str], value))


def _lower_hex_digest(value: str, *, field: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise AmylaseProcessingError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _git_commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise AmylaseProcessingError("git_commit must be a full lowercase 40-character commit")
    return value


def _safe_source_key(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise AmylaseProcessingError("source_csv_keys must be safe relative POSIX paths")
    if path.suffixes[-2:] != [".csv", ".gz"]:
        raise AmylaseProcessingError("source_csv_keys must identify gzip CSV files")
    return value


def load_amylase_processing_config(path: Path) -> AmylaseProcessingConfig:
    """Load the strict, evidence-backed Alpha Amylase processing config."""
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise AmylaseProcessingError("processing config must be an object")
    config = cast(dict[str, object], raw)
    if set(config) != _CONFIG_KEYS:
        raise AmylaseProcessingError("processing config fields do not match version 1")
    if config.get("config_version") != "1":
        raise AmylaseProcessingError("unsupported processing config version")
    if config.get("candidate_id_algorithm") != "sha256":
        raise AmylaseProcessingError("candidate_id_algorithm must be sha256")
    if config.get("missing_value_policy") != "error":
        raise AmylaseProcessingError("missing_value_policy must fail closed")
    if config.get("within_file_duplicate_policy") != "error":
        raise AmylaseProcessingError("within_file_duplicate_policy must fail closed")
    if config.get("cross_file_duplicate_policy") != "aggregate_if_target_matches":
        raise AmylaseProcessingError("cross_file_duplicate_policy is unsupported")
    if config.get("conflicting_target_policy") != "error":
        raise AmylaseProcessingError("conflicting_target_policy must fail closed")
    if config.get("near_duplicate_policy") != "allowed_pending_split_manifest":
        raise AmylaseProcessingError("near_duplicate_policy must defer grouping to Split Manifest")

    source_keys = tuple(_safe_source_key(item) for item in _string_list(config, "source_csv_keys"))
    required_fields = _string_list(config, "required_fields")
    if required_fields != ("sequence", "target", "set", "validation"):
        raise AmylaseProcessingError("required_fields must preserve the frozen source schema")
    observable_fields = _string_list(config, "observable_feature_fields")
    hidden_label = _string(config, "hidden_label_field")
    if hidden_label in observable_fields:
        raise AmylaseProcessingError("hidden label cannot be an observable feature")
    if hidden_label != "target":
        raise AmylaseProcessingError("Alpha Amylase hidden label must be target")
    if config.get("label_direction") != "higher_is_better":
        raise AmylaseProcessingError("Alpha Amylase label direction must be higher_is_better")
    if config.get("near_duplicate_method") != "full_length_hamming_distance":
        raise AmylaseProcessingError("near_duplicate_method is unsupported")

    sequence_length = config.get("sequence_length")
    candidate_count = config.get("expected_candidate_count")
    threshold = config.get("near_duplicate_threshold")
    if (
        not isinstance(sequence_length, int)
        or isinstance(sequence_length, bool)
        or sequence_length < 1
    ):
        raise AmylaseProcessingError("sequence_length must be a positive integer")
    if (
        not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count < 1
    ):
        raise AmylaseProcessingError("expected_candidate_count must be a positive integer")
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise AmylaseProcessingError("near_duplicate_threshold must be a positive integer")

    alphabet = _string(config, "allowed_amino_acids")
    if alphabet != "".join(sorted(set(alphabet))):
        raise AmylaseProcessingError("allowed_amino_acids must be sorted and unique")

    return AmylaseProcessingConfig(
        dataset_id=_string(config, "dataset_id"),
        dataset_version=_string(config, "dataset_version"),
        task_id=_string(config, "task_id"),
        dataset_manifest_sha256=_lower_hex_digest(
            _string(config, "dataset_manifest_sha256"), field="dataset_manifest_sha256"
        ),
        source_csv_keys=source_keys,
        required_fields=required_fields,
        allowed_amino_acids=alphabet,
        sequence_length=sequence_length,
        expected_candidate_count=candidate_count,
        candidate_id_namespace=_string(config, "candidate_id_namespace"),
        observable_feature_fields=observable_fields,
        hidden_label_field=hidden_label,
        label_direction="higher_is_better",
        label_semantics=_string(config, "label_semantics"),
        label_source_url=_string(config, "label_source_url"),
        allowed_set_values=_string_list(config, "allowed_set_values"),
        allowed_validation_values=_string_list(config, "allowed_validation_values"),
        near_duplicate_method="full_length_hamming_distance",
        near_duplicate_threshold=threshold,
    )


def _canonical_decimal(value: str, *, source_key: str, row_number: int) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise AmylaseProcessingError(
            f"{source_key}:{row_number}: target must be a finite decimal"
        ) from error
    if not number.is_finite():
        raise AmylaseProcessingError(f"{source_key}:{row_number}: target must be finite")
    canonical = format(number.normalize(), "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return "0" if canonical == "-0" else canonical


def _candidate_id(config: AmylaseProcessingConfig, sequence: str) -> str:
    payload = f"{config.candidate_id_namespace}\0{sequence}".encode()
    return hashlib.sha256(payload).hexdigest()


def _raw_csv_artifacts(
    config: AmylaseProcessingConfig,
    dataset_manifest_bytes: bytes,
) -> dict[str, tuple[str, int]]:
    try:
        raw_manifest = cast(object, json.loads(dataset_manifest_bytes))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AmylaseProcessingError("Dataset Manifest must be valid UTF-8 JSON") from error
    if not isinstance(raw_manifest, dict):
        raise AmylaseProcessingError("Dataset Manifest must be an object")
    manifest = cast(dict[str, object], raw_manifest)
    expected_identity = {
        "dataset_id": config.dataset_id,
        "dataset_version": config.dataset_version,
        "task_id": config.task_id,
    }
    for field, expected in expected_identity.items():
        if manifest.get(field) != expected:
            raise AmylaseProcessingError(
                f"Dataset Manifest {field} does not match processing config"
            )

    raw_files = manifest.get("raw_files")
    if not isinstance(raw_files, list) or not raw_files:
        raise AmylaseProcessingError("Dataset Manifest raw_files must be a non-empty array")
    by_object_key: dict[str, tuple[str, int]] = {}
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise AmylaseProcessingError("Dataset Manifest raw_files entries must be objects")
        item = cast(dict[str, object], raw_file)
        object_key = item.get("object_key")
        sha256 = item.get("sha256")
        size_bytes = item.get("size_bytes")
        if not isinstance(object_key, str) or not object_key:
            raise AmylaseProcessingError("Dataset Manifest object_key must be a non-empty string")
        if object_key in by_object_key:
            raise AmylaseProcessingError("Dataset Manifest raw object keys must be unique")
        if not isinstance(sha256, str):
            raise AmylaseProcessingError("Dataset Manifest raw SHA-256 must be a string")
        _lower_hex_digest(sha256, field="raw file sha256")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise AmylaseProcessingError("Dataset Manifest raw size must be a non-negative integer")
        by_object_key[object_key] = (sha256, size_bytes)

    object_prefix = f"raw/{config.dataset_id}/{config.dataset_version}"
    artifacts: dict[str, tuple[str, int]] = {}
    for source_key in config.source_csv_keys:
        object_key = f"{object_prefix}/{source_key}"
        artifact = by_object_key.get(object_key)
        if artifact is None:
            raise AmylaseProcessingError(
                f"Dataset Manifest does not contain configured source file: {source_key}"
            )
        artifacts[source_key] = artifact
    return artifacts


def _verify_raw_file(path: Path, source_key: str, expected: tuple[str, int]) -> None:
    expected_sha256, expected_size = expected
    sha256 = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
            size_bytes += len(chunk)
    if size_bytes != expected_size:
        raise AmylaseProcessingError(f"{source_key}: size does not match Dataset Manifest")
    if sha256.hexdigest() != expected_sha256:
        raise AmylaseProcessingError(f"{source_key}: SHA-256 does not match Dataset Manifest")


def _read_source(
    path: Path,
    source_key: str,
    config: AmylaseProcessingConfig,
) -> tuple[dict[str, str], tuple[tuple[str, str, str], ...], dict[str, object]]:
    if not path.is_file():
        raise AmylaseProcessingError(f"missing source file: {source_key}")
    by_sequence: dict[str, str] = {}
    metadata: list[tuple[str, str, str]] = []
    set_counts: Counter[str] = Counter()
    validation_counts: Counter[str] = Counter()
    allowed_amino_acids = set(config.allowed_amino_acids)

    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != config.required_fields:
            raise AmylaseProcessingError(f"{source_key}: fields do not match the frozen schema")
        for row_number, row in enumerate(reader, start=2):
            if any(
                row.get(field) is None or not cast(str, row[field]).strip()
                for field in config.required_fields
            ):
                raise AmylaseProcessingError(f"{source_key}:{row_number}: missing value")
            sequence = cast(str, row["sequence"])
            if sequence != sequence.strip().upper():
                raise AmylaseProcessingError(
                    f"{source_key}:{row_number}: sequence is not canonical uppercase text"
                )
            if len(sequence) != config.sequence_length or not set(sequence) <= allowed_amino_acids:
                raise AmylaseProcessingError(f"{source_key}:{row_number}: invalid sequence")
            target = _canonical_decimal(
                cast(str, row["target"]).strip(), source_key=source_key, row_number=row_number
            )
            if sequence in by_sequence:
                raise AmylaseProcessingError(
                    f"{source_key}:{row_number}: duplicate sequence within one source file"
                )
            set_value = cast(str, row["set"]).strip()
            validation_value = cast(str, row["validation"]).strip()
            if set_value not in config.allowed_set_values:
                raise AmylaseProcessingError(f"{source_key}:{row_number}: unexpected set value")
            if validation_value not in config.allowed_validation_values:
                raise AmylaseProcessingError(
                    f"{source_key}:{row_number}: unexpected validation value"
                )
            by_sequence[sequence] = target
            metadata.append((sequence, set_value, validation_value))
            set_counts[set_value] += 1
            validation_counts[validation_value] += 1

    return (
        by_sequence,
        tuple(metadata),
        {
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


def _modal_reference(sequences: tuple[str, ...]) -> str:
    length = len(sequences[0])
    return "".join(
        Counter(sequence[index] for sequence in sequences).most_common(1)[0][0]
        for index in range(length)
    )


def _near_duplicate_counts(
    sequences: tuple[str, ...], reference: str, threshold: int
) -> dict[str, int]:
    signatures = [
        {
            index: amino_acid
            for index, amino_acid in enumerate(sequence)
            if amino_acid != reference[index]
        }
        for sequence in sequences
    ]
    counts: Counter[int] = Counter()
    for index, left in enumerate(signatures):
        left_positions = set(left)
        for right in signatures[index + 1 :]:
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


def audit_amylase_sources(
    config: AmylaseProcessingConfig,
    raw_root: Path,
    *,
    raw_csv_artifacts: dict[str, tuple[str, int]] | None = None,
) -> AmylaseAudit:
    """Audit all alternate split files and construct one canonical candidate universe."""
    source_maps: dict[str, dict[str, str]] = {}
    source_metadata: dict[str, tuple[tuple[str, str, str], ...]] = {}
    source_reports: dict[str, object] = {}
    for source_key in config.source_csv_keys:
        path = raw_root.joinpath(*PurePosixPath(source_key).parts)
        if raw_csv_artifacts is not None:
            expected = raw_csv_artifacts.get(source_key)
            if expected is None:
                raise AmylaseProcessingError(
                    f"missing Dataset Manifest artifact for source file: {source_key}"
                )
            if not path.is_file():
                raise AmylaseProcessingError(f"missing source file: {source_key}")
            _verify_raw_file(path, source_key, expected)
        mapping, metadata, source_report = _read_source(path, source_key, config)
        source_maps[source_key] = mapping
        source_metadata[source_key] = metadata
        source_reports[source_key] = source_report

    first_key = config.source_csv_keys[0]
    canonical_mapping = source_maps[first_key]
    canonical_sequences = set(canonical_mapping)
    for source_key, mapping in source_maps.items():
        if set(mapping) != canonical_sequences:
            raise AmylaseProcessingError(
                f"{source_key}: candidate universe differs from {first_key}"
            )
        conflicts = [
            sequence
            for sequence in canonical_sequences
            if mapping[sequence] != canonical_mapping[sequence]
        ]
        if conflicts:
            raise AmylaseProcessingError(f"{source_key}: hidden truth conflicts with {first_key}")
    if len(canonical_sequences) != config.expected_candidate_count:
        raise AmylaseProcessingError("candidate count differs from the frozen processing config")

    candidates = tuple(
        sorted(
            (
                Candidate(
                    candidate_id=_candidate_id(config, sequence),
                    sequence=sequence,
                    target=canonical_mapping[sequence],
                )
                for sequence in canonical_sequences
            ),
            key=lambda item: item.candidate_id,
        )
    )
    if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise AmylaseProcessingError("candidate ID collision")
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
                for source_key, rows in source_metadata.items()
                for sequence, set_value, validation_value in rows
            ),
            key=lambda item: (item.candidate_id, item.source_key),
        )
    )

    sequences = tuple(sorted(canonical_sequences))
    reference = _modal_reference(sequences)
    mutation_counts = Counter(
        sum(left != right for left, right in zip(sequence, reference, strict=True))
        for sequence in sequences
    )
    report: dict[str, object] = {
        "schema_version": "1",
        "dataset_id": config.dataset_id,
        "dataset_version": config.dataset_version,
        "task_id": config.task_id,
        "source_file_count": len(config.source_csv_keys),
        "source_files": source_reports,
        "candidate_count": len(candidates),
        "union_sequence_count": len(canonical_sequences),
        "intersection_sequence_count": len(canonical_sequences),
        "all_source_candidate_sets_identical": True,
        "cross_source_target_conflict_count": 0,
        "sequence_length": config.sequence_length,
        "sequence_alphabet": config.allowed_amino_acids,
        "modal_reference_candidate_present": reference in canonical_sequences,
        "mutation_count_from_modal_reference": {
            str(count): occurrences for count, occurrences in sorted(mutation_counts.items())
        },
        "target_semantics": config.label_semantics,
        "label_direction": config.label_direction,
        "label_source_url": config.label_source_url,
        "near_duplicate_method": config.near_duplicate_method,
        "near_duplicate_threshold": config.near_duplicate_threshold,
        "near_duplicate_pair_counts": _near_duplicate_counts(
            sequences, reference, config.near_duplicate_threshold
        ),
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "upstream_split_metadata_is_not_frozen": True,
    }
    return AmylaseAudit(candidates=candidates, memberships=memberships, report=report)


def _csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _json_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _artifact(object_key: str, payload: bytes, row_count: int) -> dict[str, object]:
    return {
        "object_key": object_key,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "row_count": row_count,
    }


def build_amylase_processing_bundle(
    *,
    config: AmylaseProcessingConfig,
    config_bytes: bytes,
    dataset_manifest_bytes: bytes,
    raw_root: Path,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic processed artifacts and a Processing Manifest without publishing them."""
    _git_commit(git_commit)
    dataset_manifest_sha256 = hashlib.sha256(dataset_manifest_bytes).hexdigest()
    if dataset_manifest_sha256 != config.dataset_manifest_sha256:
        raise AmylaseProcessingError("Dataset Manifest checksum does not match processing config")
    raw_csv_artifacts = _raw_csv_artifacts(config, dataset_manifest_bytes)
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    processing_identity = {
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "processing_config_sha256": config_sha256,
        "git_commit": git_commit,
    }
    processing_hash = hashlib.sha256(
        json.dumps(processing_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    processing_id = f"flip2-amylase-{processing_hash}"
    prefix = (
        f"processed/{config.dataset_id}/{config.dataset_version}/{config.task_id}/{processing_id}"
    )

    audit = audit_amylase_sources(
        config,
        raw_root,
        raw_csv_artifacts=raw_csv_artifacts,
    )
    candidate_rows = [
        {"candidate_id": candidate.candidate_id, "sequence": candidate.sequence}
        for candidate in audit.candidates
    ]
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
    candidate_key = f"{prefix}/candidates.csv"
    hidden_truth_key = f"{prefix}/hidden-truth.csv"
    membership_key = f"{prefix}/upstream-split-metadata.csv"
    audit_key = f"{prefix}/processing-audit.json"
    payloads = {
        candidate_key: _csv_bytes(
            ("candidate_id", "sequence"),
            candidate_rows,
        ),
        hidden_truth_key: _csv_bytes(
            ("candidate_id", "target"),
            hidden_truth_rows,
        ),
        membership_key: _csv_bytes(
            ("candidate_id", "source_key", "set", "validation"),
            membership_rows,
        ),
        audit_key: _json_bytes(audit.report),
    }
    row_counts = {
        candidate_key: len(candidate_rows),
        hidden_truth_key: len(hidden_truth_rows),
        membership_key: len(membership_rows),
        audit_key: 1,
    }
    manifest: dict[str, object] = {
        "schema_version": "1",
        "processing_id": processing_id,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "processing_config_sha256": config_sha256,
        "git_commit": git_commit,
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
            _artifact(object_key, payloads[object_key], row_counts[object_key])
            for object_key in sorted(payloads)
        ],
        "known_limitations": [
            (
                "Upstream set and validation fields are retained only as audit metadata and are "
                "not a frozen benchmark split."
            ),
            (
                "Near-duplicate grouping, leakage controls, and held-out authorization are "
                "deferred to a Split Manifest."
            ),
            (
                "This processing revision provides no feature, model, replay, benchmark, or "
                "scientific-gate evidence."
            ),
        ],
    }
    return payloads, manifest
