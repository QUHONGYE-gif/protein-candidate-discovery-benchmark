"""Deterministic sequence-composition features for the bounded M2 slice."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, cast

from pcdb.contracts.experiment import (
    ContractValidationError,
    LoadedContract,
    canonical_json_bytes,
)
from pcdb.contracts.feature import M2_FEATURE_TASK_BINDINGS, load_feature_artifact
from pcdb.contracts.feature_identity import (
    LoadedFeatureMatrix,
    build_feature_bundle_descriptor,
    encode_feature_config,
    encode_feature_matrix,
    load_feature_bundle_descriptor,
    load_feature_matrix,
)
from pcdb.datasets.processing import ProcessingConfigLike, candidate_id

ALPHABET = "ACDEFGHIKLMNPQRSTVWY"
FEATURE_CONFIG_ID = "sequence-composition-420-v1"
FEATURE_CONFIG_SHA256 = "2b9a9dbfb322f8b34296d208a2b8eb9392cac2b7056508039589fc3a0e34b77d"
FEATURE_CONFIG_SIZE_BYTES = 281_064

_ROLES = ("development", "validation", "candidate-pool")
_CANDIDATE_ID = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = _CANDIDATE_ID


class CandidateIdentityConfig(Protocol):
    """The processing field needed to recompute candidate identity."""

    @property
    def candidate_id_namespace(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CandidateSequence:
    """One explicit observable-only feature-builder input."""

    candidate_id: str
    sequence: str


@dataclass(frozen=True, slots=True)
class SequenceFeatureBundle:
    """Validated logical artifact, canonical members, and descriptor root."""

    logical_artifact: LoadedContract
    descriptor: LoadedContract
    descriptor_bytes: bytes
    member_bytes: Mapping[str, bytes]
    memberships: Mapping[str, tuple[str, ...]]


def _typed_utf8(name: str, value: str) -> dict[str, object]:
    return {
        "dtype": "utf8",
        "name": name,
        "shape": [],
        "values": [value],
    }


def _feature_specifications() -> tuple[tuple[str, str, str], ...]:
    specifications = [
        (f"aa-fraction-{residue.lower()}", "amino-acid-fraction", residue) for residue in ALPHABET
    ]
    specifications.extend(
        (
            f"dipeptide-fraction-{first.lower()}{second.lower()}",
            "ordered-dipeptide-fraction",
            f"{first}{second}",
        )
        for first in ALPHABET
        for second in ALPHABET
    )
    return tuple(sorted(specifications, key=lambda item: item[0].encode("ascii")))


_FEATURE_SPECIFICATIONS = _feature_specifications()


def sequence_feature_definitions() -> tuple[Mapping[str, object], ...]:
    """Return the one frozen 420-column registry in canonical column order."""

    definitions: list[Mapping[str, object]] = []
    for feature_id, derivation_id, _ in _FEATURE_SPECIFICATIONS:
        definitions.append(
            {
                "allowed_values": {
                    "boolean": "forbid",
                    "finite": True,
                    "missing": "forbid",
                },
                "derivation": {
                    "derivation_id": derivation_id,
                    "derivation_version": "v1",
                    "fit_state": None,
                    "source_fields": ["sequence"],
                },
                "dtype": "float64",
                "feature_id": feature_id,
                "shape": [],
            }
        )
    return tuple(definitions)


def _feature_config_value() -> dict[str, object]:
    derivations: list[dict[str, object]] = []
    for feature_id, derivation_id, symbol in _FEATURE_SPECIFICATIONS:
        if derivation_id == "amino-acid-fraction":
            parameters = [
                _typed_utf8("alphabet", ALPHABET),
                _typed_utf8("denominator", "sequence-length"),
                _typed_utf8("residue", symbol),
            ]
        else:
            parameters = [
                _typed_utf8("alphabet", ALPHABET),
                _typed_utf8("denominator", "sequence-length-minus-one"),
                _typed_utf8("pair", symbol),
            ]
        derivations.append(
            {
                "derivation_id": derivation_id,
                "derivation_version": "v1",
                "feature_id": feature_id,
                "parameters": parameters,
            }
        )
    return {
        "derivations": derivations,
        "feature_config_id": FEATURE_CONFIG_ID,
        "schema_version": "m2-feature-config-v1",
    }


_FEATURE_CONFIG_BYTES = encode_feature_config(
    _feature_config_value(),
    feature_definitions=sequence_feature_definitions(),
)
if (
    len(_FEATURE_CONFIG_BYTES) != FEATURE_CONFIG_SIZE_BYTES
    or hashlib.sha256(_FEATURE_CONFIG_BYTES).hexdigest() != FEATURE_CONFIG_SHA256
):
    raise RuntimeError("frozen sequence-composition feature config identity drifted")


def sequence_feature_config_bytes() -> bytes:
    """Return the frozen canonical config bytes."""

    return _FEATURE_CONFIG_BYTES


def _validate_record(
    record: CandidateSequence,
    *,
    processing_config: CandidateIdentityConfig | None,
) -> None:
    if not isinstance(record, CandidateSequence):
        raise ContractValidationError("feature input must be a CandidateSequence")
    if not _CANDIDATE_ID.fullmatch(record.candidate_id):
        raise ContractValidationError("candidate ID must be lower-case 64-hex")
    sequence = record.sequence
    if not isinstance(sequence, str):
        raise ContractValidationError("sequence must be exact uppercase UTF-8")
    try:
        sequence.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractValidationError("sequence must be exact uppercase UTF-8") from exc
    if len(sequence) < 2:
        raise ContractValidationError("sequence length must be at least 2")
    if sequence != sequence.upper():
        raise ContractValidationError("sequence must be exact uppercase UTF-8")
    if any(residue not in ALPHABET for residue in sequence):
        raise ContractValidationError("sequence contains a residue outside the frozen alphabet")
    if processing_config is not None:
        expected = candidate_id(cast(ProcessingConfigLike, processing_config), sequence)
        if record.candidate_id != expected:
            raise ContractValidationError(
                "candidate ID does not exact-match the provided processing config"
            )


def _sequence_feature_row(record: CandidateSequence) -> dict[str, object]:
    residue_counts = Counter(record.sequence)
    pair_counts = Counter(zip(record.sequence, record.sequence[1:], strict=False))
    length = len(record.sequence)
    row: dict[str, object] = {"candidate_id": record.candidate_id}
    for feature_id, derivation_id, symbol in _FEATURE_SPECIFICATIONS:
        if derivation_id == "amino-acid-fraction":
            row[feature_id] = residue_counts[symbol] / length
        else:
            row[feature_id] = pair_counts[(symbol[0], symbol[1])] / (length - 1)
    return row


def build_sequence_feature_rows(
    records: Sequence[CandidateSequence],
    *,
    processing_config: CandidateIdentityConfig | None = None,
) -> tuple[dict[str, object], ...]:
    """Validate observable records and build deterministic feature rows."""

    if isinstance(records, (str, bytes, bytearray)) or not records:
        raise ContractValidationError("feature records must be a non-empty sequence")
    candidate_ids: list[str] = []
    rows: list[dict[str, object]] = []
    for record in records:
        _validate_record(record, processing_config=processing_config)
        candidate_ids.append(record.candidate_id)
        rows.append(_sequence_feature_row(record))
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ContractValidationError("feature records contain duplicate candidate IDs")
    return tuple(rows)


def _require_generator_identity(git_commit: str, environment_digest: str) -> None:
    if not _GIT_COMMIT.fullmatch(git_commit):
        raise ContractValidationError("git_commit must be full lower-case 40-hex")
    if not _SHA256.fullmatch(environment_digest):
        raise ContractValidationError("environment_digest must be lower-case 64-hex")


def build_sequence_feature_bundle(
    *,
    benchmark_task_id: str,
    role_candidates: Mapping[str, Sequence[CandidateSequence]],
    git_commit: str,
    environment_digest: str,
    processing_config: CandidateIdentityConfig | None = None,
    expected_role_memberships: Mapping[str, Collection[str]] | None = None,
) -> SequenceFeatureBundle:
    """Build all three task-bound role members and reconstruct the #47 root."""

    binding = M2_FEATURE_TASK_BINDINGS.get(benchmark_task_id)
    if binding is None:
        raise ContractValidationError("benchmark task is not in the frozen M2 bindings")
    if set(role_candidates) != set(_ROLES):
        raise ContractValidationError(
            "role candidates must contain exactly development, validation, and candidate-pool"
        )
    _require_generator_identity(git_commit, environment_digest)

    memberships: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for role in _ROLES:
        records = role_candidates[role]
        expected_count = binding["role_counts"][role]
        if len(records) != expected_count:
            raise ContractValidationError(f"{role} candidate count does not match task binding")
        role_ids = tuple(record.candidate_id for record in records)
        if len(role_ids) != len(set(role_ids)):
            raise ContractValidationError(f"{role} contains duplicate candidate IDs")
        if seen.intersection(role_ids):
            raise ContractValidationError("candidate IDs overlap across feature roles")
        seen.update(role_ids)
        memberships[role] = role_ids

    descriptor_memberships: Mapping[str, Collection[str]] = memberships
    if expected_role_memberships is not None:
        if set(expected_role_memberships) != set(_ROLES):
            raise ContractValidationError(
                "expected role memberships must contain exactly the three model-visible roles"
            )
        for role in _ROLES:
            expected_ids = tuple(expected_role_memberships[role])
            if len(expected_ids) != len(set(expected_ids)):
                raise ContractValidationError("expected role membership contains duplicate IDs")
            if len(expected_ids) != len(memberships[role]) or set(expected_ids) != set(
                memberships[role]
            ):
                raise ContractValidationError(
                    "feature input does not exact-cover independent role membership"
                )
        descriptor_memberships = expected_role_memberships

    definitions = sequence_feature_definitions()
    feature_ids = [cast(str, definition["feature_id"]) for definition in definitions]
    config_key = f"features/{benchmark_task_id}/{FEATURE_CONFIG_ID}/config.json"
    member_bytes: dict[str, bytes] = {config_key: _FEATURE_CONFIG_BYTES}
    matrices: list[dict[str, object]] = []
    for role in _ROLES:
        rows = build_sequence_feature_rows(
            role_candidates[role],
            processing_config=processing_config,
        )
        matrix_bytes = encode_feature_matrix(rows, feature_definitions=definitions)
        object_key = f"features/{benchmark_task_id}/{FEATURE_CONFIG_ID}/{role}.csv"
        member_bytes[object_key] = matrix_bytes
        matrices.append(
            {
                "artifact": {
                    "object_key": object_key,
                    "sha256": hashlib.sha256(matrix_bytes).hexdigest(),
                    "size_bytes": len(matrix_bytes),
                },
                "candidate_id_field": "candidate_id",
                "column_count": len(feature_ids),
                "feature_ids": feature_ids,
                "fit_state_refs": [],
                "role": role,
                "row_count": len(rows),
            }
        )

    logical_value = {
        "benchmark_task_id": benchmark_task_id,
        "contract_status": "production",
        "dataset_manifest_sha256": binding["dataset_manifest_sha256"],
        "dataset_task_id": binding["dataset_task_id"],
        "feature_config": {
            "feature_config_id": FEATURE_CONFIG_ID,
            "feature_config_sha256": FEATURE_CONFIG_SHA256,
        },
        "feature_definitions": list(definitions),
        "fitted_states": [],
        "generator": {
            "environment_digest": environment_digest,
            "git_commit": git_commit,
        },
        "matrices": matrices,
        "observable_sources": ["sequence"],
        "processing_manifest_sha256": binding["processing_manifest_sha256"],
        "schema_version": "m2-feature-artifact-v1",
        "split_sha256": binding["split_sha256"],
    }
    logical_artifact = load_feature_artifact(canonical_json_bytes(logical_value))
    descriptor_bytes = build_feature_bundle_descriptor(
        logical_artifact,
        feature_config_object_key=config_key,
        member_bytes=member_bytes,
        tensor_definitions={},
        expected_role_memberships=descriptor_memberships,
    )
    descriptor = load_feature_bundle_descriptor(descriptor_bytes)
    return SequenceFeatureBundle(
        logical_artifact=logical_artifact,
        descriptor=descriptor,
        descriptor_bytes=descriptor_bytes,
        member_bytes=MappingProxyType(member_bytes),
        memberships=MappingProxyType(memberships),
    )


def load_bundle_matrix(bundle: SequenceFeatureBundle, role: str) -> LoadedFeatureMatrix:
    """Load one canonical role member from a validated bundle."""

    if role not in _ROLES:
        raise ContractValidationError("feature matrix role is unknown")
    matrix = next(item for item in bundle.descriptor.value["matrices"] if item["role"] == role)
    object_key = cast(str, matrix["artifact"]["object_key"])
    return load_feature_matrix(
        bundle.member_bytes[object_key],
        feature_definitions=cast(
            Sequence[Mapping[str, Any]],
            bundle.descriptor.value["feature_definitions"],
        ),
        expected_sha256=cast(str, matrix["artifact"]["sha256"]),
    )
