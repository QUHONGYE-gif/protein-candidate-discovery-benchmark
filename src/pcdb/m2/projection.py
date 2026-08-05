"""Fail-closed real-input projection for the bounded M2 experiment."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError
from pcdb.contracts.feature import M2_FEATURE_TASK_BINDINGS
from pcdb.datasets.processing import ProcessingConfigLike, candidate_id
from pcdb.m2.features import CandidateSequence
from pcdb.m2.types import DevelopmentObservation

_ROLES = ("development", "validation", "candidate-pool")
_ALL_SPLIT_ROLES = (*_ROLES, "held-out")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_ID = _SHA256

_PROCESSING_CONFIG_SHA256 = MappingProxyType(
    {
        "alpha-amylase": "d34c3ae855f2406cee27e1e31e1b4ecb4c86e75f85a762bf2ebbd0c44ff7a0ae",
        "ired": "af2f3782654a84f529f4bc67376b735f67835daa7b2b3293411f950c94d52bb5",
    }
)
_TASK_CONFIG_IDENTITY = MappingProxyType(
    {
        "alpha-amylase": (
            "flip2",
            "zenodo-18433203-v3",
            "amylase",
            "flip2:zenodo-18433203-v3:amylase",
        ),
        "ired": (
            "flip2",
            "zenodo-18433203-v3",
            "ired",
            "flip2:zenodo-18433203-v3:ired",
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ProjectionIdentity:
    """Trusted identities used to validate one role projection."""

    benchmark_task_id: str
    dataset_manifest_sha256: str
    processing_manifest_sha256: str
    split_sha256: str
    processing_config_sha256: str
    role_counts: Mapping[str, int]
    dataset_id: str
    dataset_version: str
    dataset_task_id: str
    candidate_id_namespace: str


@dataclass(frozen=True, slots=True)
class ProjectedInputs:
    """The complete model-visible projection and its independent memberships."""

    role_candidates: Mapping[str, tuple[CandidateSequence, ...]]
    development_observations: tuple[DevelopmentObservation, ...]
    expected_role_memberships: Mapping[str, tuple[str, ...]]


def frozen_projection_identity(benchmark_task_id: str) -> ProjectionIdentity:
    """Return the reviewed, code-frozen production identity for one M2 task."""

    binding = M2_FEATURE_TASK_BINDINGS.get(benchmark_task_id)
    config_identity = _TASK_CONFIG_IDENTITY.get(benchmark_task_id)
    processing_config_sha256 = _PROCESSING_CONFIG_SHA256.get(benchmark_task_id)
    if binding is None or config_identity is None or processing_config_sha256 is None:
        raise ContractValidationError("benchmark task is not in the frozen projection bindings")
    dataset_id, dataset_version, dataset_task_id, candidate_id_namespace = config_identity
    return ProjectionIdentity(
        benchmark_task_id=benchmark_task_id,
        dataset_manifest_sha256=cast(str, binding["dataset_manifest_sha256"]),
        processing_manifest_sha256=cast(str, binding["processing_manifest_sha256"]),
        split_sha256=cast(str, binding["split_sha256"]),
        processing_config_sha256=processing_config_sha256,
        role_counts=cast(Mapping[str, int], binding["role_counts"]),
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_task_id=dataset_task_id,
        candidate_id_namespace=candidate_id_namespace,
    )


def _reject_constant(_value: str) -> None:
    raise ContractValidationError("non-finite JSON constant is forbidden")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError("duplicate JSON key is forbidden")
        result[key] = value
    return result


def _strict_json_object(payload: bytes, field: str) -> dict[str, Any]:
    if not payload or payload.startswith(b"\xef\xbb\xbf"):
        raise ContractValidationError(f"{field} must be strict UTF-8 JSON")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ContractValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError(f"{field} must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be a JSON object")
    return cast(dict[str, Any], value)


def _validate_identity(identity: ProjectionIdentity) -> None:
    for field in (
        identity.dataset_manifest_sha256,
        identity.processing_manifest_sha256,
        identity.split_sha256,
        identity.processing_config_sha256,
    ):
        if not _SHA256.fullmatch(field):
            raise ContractValidationError("projection identity contains an invalid digest")
    if set(identity.role_counts) != set(_ROLES) or any(
        isinstance(count, bool) or not isinstance(count, int) or count < 1
        for count in identity.role_counts.values()
    ):
        raise ContractValidationError("projection identity role counts are invalid")


def _validate_processing_config(
    identity: ProjectionIdentity,
    processing_config: ProcessingConfigLike,
    processing_config_bytes: bytes,
) -> None:
    if hashlib.sha256(processing_config_bytes).hexdigest() != identity.processing_config_sha256:
        raise ContractValidationError(
            "processing config identity does not match projection binding"
        )
    expected = (
        identity.dataset_id,
        identity.dataset_version,
        identity.dataset_task_id,
        identity.dataset_manifest_sha256,
        ("sequence",),
        identity.candidate_id_namespace,
        "target",
    )
    actual = (
        processing_config.dataset_id,
        processing_config.dataset_version,
        processing_config.task_id,
        processing_config.dataset_manifest_sha256,
        processing_config.observable_feature_fields,
        processing_config.candidate_id_namespace,
        processing_config.hidden_label_field,
    )
    if actual != expected:
        raise ContractValidationError("processing config fields do not match projection binding")


def _membership_reference(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    reference = manifest.get("membership_file")
    expected_fields = {"object_key", "row_count", "sha256", "size_bytes"}
    if not isinstance(reference, dict) or set(reference) != expected_fields:
        raise ContractValidationError("split membership reference fields drifted")
    return cast(Mapping[str, Any], reference)


def _split_memberships(
    identity: ProjectionIdentity,
    split_manifest_bytes: bytes,
    membership_bytes: bytes,
) -> Mapping[str, tuple[str, ...]]:
    if hashlib.sha256(split_manifest_bytes).hexdigest() != identity.split_sha256:
        raise ContractValidationError("split identity does not match projection binding")
    manifest = _strict_json_object(split_manifest_bytes, "split manifest")
    if manifest.get("processing_manifest_sha256") != identity.processing_manifest_sha256:
        raise ContractValidationError("split processing identity does not match projection binding")
    partitions = manifest.get("partitions")
    if (
        not isinstance(partitions, list)
        or any(not isinstance(role, str) for role in partitions)
        or len(partitions) != len(set(partitions))
        or set(partitions) != set(_ALL_SPLIT_ROLES)
    ):
        raise ContractValidationError("split partitions do not match the closed projection roles")
    if manifest.get("held_out_partitions") != ["held-out"]:
        raise ContractValidationError("split held-out boundary is invalid")

    reference = _membership_reference(manifest)
    if reference.get("sha256") != hashlib.sha256(membership_bytes).hexdigest() or reference.get(
        "size_bytes"
    ) != len(membership_bytes):
        raise ContractValidationError("split membership exact bytes do not match manifest")
    try:
        text = membership_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractValidationError("split membership must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    if reader.fieldnames != ["candidate_id", "role", "group_id"]:
        raise ContractValidationError("split membership fields drifted")
    by_role: dict[str, list[str]] = {role: [] for role in _ALL_SPLIT_ROLES}
    seen: set[str] = set()
    try:
        for row in reader:
            if set(row) != {"candidate_id", "role", "group_id"} or None in row:
                raise ContractValidationError("split membership row fields drifted")
            candidate = row["candidate_id"]
            role = row["role"]
            group_id = row["group_id"]
            if (
                not isinstance(candidate, str)
                or not _CANDIDATE_ID.fullmatch(candidate)
                or role not in by_role
                or not isinstance(group_id, str)
                or not group_id
            ):
                raise ContractValidationError("split membership row is invalid")
            if candidate in seen:
                raise ContractValidationError("split membership candidate IDs are not unique")
            seen.add(candidate)
            by_role[role].append(candidate)
    except csv.Error as exc:
        raise ContractValidationError("split membership must be valid CSV") from exc
    if reference.get("row_count") != len(seen):
        raise ContractValidationError("split membership row count does not match manifest")
    for role in _ROLES:
        if len(by_role[role]) != identity.role_counts[role]:
            raise ContractValidationError("split membership role count does not match binding")
    if not by_role["held-out"]:
        raise ContractValidationError("split membership must retain a closed held-out partition")
    return MappingProxyType({role: tuple(sorted(by_role[role])) for role in _ROLES})


def _load_role_candidates(payload: bytes) -> Mapping[str, tuple[CandidateSequence, ...]]:
    value = _strict_json_object(payload, "role input")
    if set(value) != {"roles"}:
        raise ContractValidationError("role input fields drifted")
    roles = value["roles"]
    if not isinstance(roles, dict) or set(roles) != set(_ROLES):
        raise ContractValidationError("role input fields drifted")
    loaded: dict[str, tuple[CandidateSequence, ...]] = {}
    for role in _ROLES:
        raw_records = roles[role]
        if not isinstance(raw_records, list) or not raw_records:
            raise ContractValidationError("role records must be non-empty arrays")
        records: list[CandidateSequence] = []
        for raw_record in raw_records:
            if not isinstance(raw_record, dict) or set(raw_record) != {
                "candidate_id",
                "sequence",
            }:
                raise ContractValidationError("role record fields drifted")
            record_id = raw_record["candidate_id"]
            sequence = raw_record["sequence"]
            if not isinstance(record_id, str) or not isinstance(sequence, str):
                raise ContractValidationError("role record values are invalid")
            records.append(CandidateSequence(record_id, sequence))
        loaded[role] = tuple(records)
    return MappingProxyType(loaded)


def _load_development_observations(payload: bytes) -> tuple[DevelopmentObservation, ...]:
    value = _strict_json_object(payload, "development observation input")
    if set(value) != {"development"}:
        raise ContractValidationError("observation input must contain only development")
    raw_observations = value["development"]
    if not isinstance(raw_observations, list) or not raw_observations:
        raise ContractValidationError("development observations must be a non-empty array")
    observations: list[DevelopmentObservation] = []
    for item in raw_observations:
        if not isinstance(item, dict) or set(item) != {"candidate_id", "observation"}:
            raise ContractValidationError("development observation fields drifted")
        record_id = item["candidate_id"]
        observation = item["observation"]
        if not isinstance(record_id, str) or not _CANDIDATE_ID.fullmatch(record_id):
            raise ContractValidationError("development observation candidate ID is invalid")
        if isinstance(observation, bool) or not isinstance(observation, (int, float)):
            raise ContractValidationError("development observation must be numeric")
        numeric = float(observation)
        if not math.isfinite(numeric):
            raise ContractValidationError("development observation must be finite")
        observations.append(DevelopmentObservation(record_id, numeric))
    return tuple(observations)


def project_checked_inputs(
    *,
    identity: ProjectionIdentity,
    processing_config: ProcessingConfigLike,
    processing_config_bytes: bytes,
    split_manifest_bytes: bytes,
    membership_bytes: bytes,
    role_input_bytes: bytes,
    development_observation_bytes: bytes,
) -> ProjectedInputs:
    """Project strict model inputs against an independently supplied identity chain."""

    _validate_identity(identity)
    _validate_processing_config(identity, processing_config, processing_config_bytes)
    expected = _split_memberships(identity, split_manifest_bytes, membership_bytes)
    roles = _load_role_candidates(role_input_bytes)
    observations = _load_development_observations(development_observation_bytes)

    seen: set[str] = set()
    for role in _ROLES:
        records = roles[role]
        actual_ids = tuple(record.candidate_id for record in records)
        if len(actual_ids) != len(set(actual_ids)) or seen.intersection(actual_ids):
            raise ContractValidationError("role candidate IDs are duplicate or cross-role")
        seen.update(actual_ids)
        if set(actual_ids) != set(expected[role]) or len(actual_ids) != len(expected[role]):
            raise ContractValidationError("role candidates do not exact-cover split membership")
        for record in records:
            if candidate_id(processing_config, record.sequence) != record.candidate_id:
                raise ContractValidationError("candidate ID does not match processing config")

    development_ids = tuple(item.candidate_id for item in observations)
    if (
        len(development_ids) != len(set(development_ids))
        or set(development_ids) != set(expected["development"])
        or len(development_ids) != len(expected["development"])
    ):
        raise ContractValidationError("observations do not exact-cover development membership")
    return ProjectedInputs(
        role_candidates=roles,
        development_observations=observations,
        expected_role_memberships=expected,
    )


def project_frozen_m2_inputs(
    *,
    benchmark_task_id: str,
    processing_config: ProcessingConfigLike,
    processing_config_bytes: bytes,
    split_manifest_bytes: bytes,
    membership_bytes: bytes,
    role_input_bytes: bytes,
    development_observation_bytes: bytes,
) -> ProjectedInputs:
    """Project one Alpha/IRED input using only code-frozen production identities."""

    return project_checked_inputs(
        identity=frozen_projection_identity(benchmark_task_id),
        processing_config=processing_config,
        processing_config_bytes=processing_config_bytes,
        split_manifest_bytes=split_manifest_bytes,
        membership_bytes=membership_bytes,
        role_input_bytes=role_input_bytes,
        development_observation_bytes=development_observation_bytes,
    )


__all__ = [
    "ProjectedInputs",
    "ProjectionIdentity",
    "frozen_projection_identity",
    "project_checked_inputs",
    "project_frozen_m2_inputs",
]
