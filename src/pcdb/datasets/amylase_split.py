"""Deterministic topology-isolated split for FLIP2 Alpha Amylase."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast


class AmylaseSplitError(ValueError):
    """Raised when the frozen split contract cannot be satisfied exactly."""


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """One checksum-bound processing input artifact."""

    object_key: str
    sha256: str
    size_bytes: int
    row_count: int


@dataclass(frozen=True, slots=True)
class AmylaseSplitConfig:
    """Frozen rules for the Alpha Amylase topology-isolation split."""

    dataset_id: str
    dataset_version: str
    task_id: str
    split_id: str
    protocol_version: str
    processing_manifest_sha256: str
    candidate_input: ArtifactReference
    upstream_metadata_input: ArtifactReference
    sequence_length: int
    allowed_amino_acids: str
    expected_candidate_count: int
    grouping_method: str
    hamming_distance: int
    protocol_namespace: str
    group_id_algorithm: str
    group_preimage_format: str
    seed: int
    ordering_key_algorithm: str
    ordering_preimage_format: str
    ordering_tie_break: tuple[str, ...]
    assignment_role_order: tuple[str, ...]
    role_counts: dict[str, int]
    expected_hamming1_pair_count: int
    expected_component_count: int
    expected_singleton_component_count: int
    expected_non_singleton_component_count: int
    expected_largest_component_size: int
    expected_second_largest_component_size: int
    upstream_source_keys: tuple[str, ...]
    expected_upstream_cross_role_edges: dict[str, int]
    leakage_controls: tuple[str, ...]
    known_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SplitCandidate:
    """Observable candidate fields used to construct the split."""

    candidate_id: str
    sequence: str


@dataclass(frozen=True, slots=True)
class UpstreamMembership:
    """Publisher split metadata retained only for comparison."""

    candidate_id: str
    source_key: str
    set_value: str
    validation_value: str


@dataclass(frozen=True, slots=True)
class SplitMembership:
    """One candidate's immutable benchmark role and sequence group."""

    candidate_id: str
    role: str
    group_id: str


@dataclass(frozen=True, slots=True)
class Component:
    """One Hamming-1 connected component."""

    members: tuple[str, ...]
    group_id: str
    ordering_key: str


@dataclass(frozen=True, slots=True)
class SplitBuild:
    """Deterministic membership plus compact audit facts."""

    membership: tuple[SplitMembership, ...]
    audit: dict[str, object]


_ROLE_CANDIDATE_POOL = "candidate-pool"
_ASSIGNABLE_ROLES = ("development", "validation", "held-out")
_ALL_ROLES = (_ROLE_CANDIDATE_POOL, *_ASSIGNABLE_ROLES)
_SHA256_HEX_LENGTH = 64

_CONFIG_KEYS = {
    "config_version",
    "dataset_id",
    "dataset_version",
    "task_id",
    "split_id",
    "protocol_version",
    "processing_manifest_sha256",
    "candidate_input",
    "upstream_metadata_input",
    "sequence_length",
    "allowed_amino_acids",
    "expected_candidate_count",
    "grouping_method",
    "hamming_distance",
    "protocol_namespace",
    "group_id_algorithm",
    "group_preimage_format",
    "seed",
    "ordering_key_algorithm",
    "ordering_preimage_format",
    "ordering_tie_break",
    "assignment_role_order",
    "role_counts",
    "expected_hamming1_pair_count",
    "expected_component_count",
    "expected_singleton_component_count",
    "expected_non_singleton_component_count",
    "expected_largest_component_size",
    "expected_second_largest_component_size",
    "upstream_source_keys",
    "expected_upstream_cross_role_edges",
    "leakage_controls",
    "known_limitations",
}

_ARTIFACT_KEYS = {"object_key", "sha256", "size_bytes", "row_count"}


def _string(mapping: dict[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise AmylaseSplitError(f"{field} must be a non-empty string")
    return value


def _string_list(mapping: dict[str, object], field: str) -> tuple[str, ...]:
    value = mapping.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise AmylaseSplitError(f"{field} must contain unique non-empty strings")
    return tuple(cast(list[str], value))


def _positive_int(mapping: dict[str, object], field: str) -> int:
    value = mapping.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise AmylaseSplitError(f"{field} must be a positive integer")
    return value


def _non_negative_int(mapping: dict[str, object], field: str) -> int:
    value = mapping.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AmylaseSplitError(f"{field} must be a non-negative integer")
    return value


def _lower_hex_digest(value: str, *, field: str) -> str:
    if len(value) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise AmylaseSplitError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _git_commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise AmylaseSplitError("git_commit must be a full lowercase 40-character commit")
    return value


def _safe_object_key(value: str, *, field: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise AmylaseSplitError(f"{field} must be a safe relative POSIX object key")
    return value


def _artifact_reference(mapping: dict[str, object], field: str) -> ArtifactReference:
    value = mapping.get(field)
    if not isinstance(value, dict):
        raise AmylaseSplitError(f"{field} must be an object")
    artifact = cast(dict[str, object], value)
    if set(artifact) != _ARTIFACT_KEYS:
        raise AmylaseSplitError(f"{field} fields do not match version 1")
    return ArtifactReference(
        object_key=_safe_object_key(_string(artifact, "object_key"), field=field),
        sha256=_lower_hex_digest(_string(artifact, "sha256"), field=f"{field}.sha256"),
        size_bytes=_non_negative_int(artifact, "size_bytes"),
        row_count=_positive_int(artifact, "row_count"),
    )


def _role_counts(mapping: dict[str, object]) -> dict[str, int]:
    value = mapping.get("role_counts")
    if not isinstance(value, dict) or set(value) != set(_ALL_ROLES):
        raise AmylaseSplitError("role_counts must define exactly the four frozen roles")
    raw_counts = cast(dict[str, object], value)
    return {role: _positive_int(raw_counts, role) for role in _ALL_ROLES}


def _integer_map(
    mapping: dict[str, object], field: str, expected_keys: tuple[str, ...]
) -> dict[str, int]:
    value = mapping.get(field)
    if not isinstance(value, dict) or set(value) != set(expected_keys):
        raise AmylaseSplitError(f"{field} keys must match upstream_source_keys")
    raw_values = cast(dict[str, object], value)
    return {key: _non_negative_int(raw_values, key) for key in expected_keys}


def _config_mapping(payload: bytes) -> dict[str, object]:
    try:
        raw = cast(object, json.loads(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AmylaseSplitError("split config must be valid UTF-8 JSON") from error
    if not isinstance(raw, dict):
        raise AmylaseSplitError("split config must be an object")
    return cast(dict[str, object], raw)


def _parse_amylase_split_config(config: dict[str, object]) -> AmylaseSplitConfig:
    if set(config) != _CONFIG_KEYS:
        raise AmylaseSplitError("split config fields do not match version 1")
    if config.get("config_version") != "1":
        raise AmylaseSplitError("unsupported split config version")
    if config.get("grouping_method") != "full_length_hamming1_connected_component":
        raise AmylaseSplitError("grouping_method must use full-length Hamming-1 components")
    if config.get("hamming_distance") != 1:
        raise AmylaseSplitError("hamming_distance must be exactly 1")
    if config.get("group_id_algorithm") != "sha256":
        raise AmylaseSplitError("group_id_algorithm must be sha256")
    if config.get("ordering_key_algorithm") != "sha256":
        raise AmylaseSplitError("ordering_key_algorithm must be sha256")
    if config.get("group_preimage_format") != (
        "namespace_nul_group-id_nul_uint32be_count_concatenated_raw_candidate_ids"
    ):
        raise AmylaseSplitError("group_preimage_format does not match the frozen byte contract")
    if config.get("ordering_preimage_format") != (
        "namespace_nul_order-key_nul_uint64be_seed_raw_group_id"
    ):
        raise AmylaseSplitError("ordering_preimage_format does not match the frozen byte contract")
    if _string_list(config, "ordering_tie_break") != ("ordering_key", "group_id"):
        raise AmylaseSplitError("ordering_tie_break does not match the frozen contract")
    role_order = _string_list(config, "assignment_role_order")
    if role_order != _ASSIGNABLE_ROLES:
        raise AmylaseSplitError("assignment_role_order does not match the frozen contract")

    alphabet = _string(config, "allowed_amino_acids")
    if alphabet != "".join(sorted(set(alphabet))):
        raise AmylaseSplitError("allowed_amino_acids must be sorted and unique")
    namespace = _string(config, "protocol_namespace")
    try:
        namespace.encode("ascii")
    except UnicodeEncodeError as error:
        raise AmylaseSplitError("protocol_namespace must contain exact ASCII bytes") from error

    upstream_source_keys = _string_list(config, "upstream_source_keys")
    candidate_input = _artifact_reference(config, "candidate_input")
    upstream_metadata_input = _artifact_reference(config, "upstream_metadata_input")
    counts = _role_counts(config)
    expected_candidate_count = _positive_int(config, "expected_candidate_count")
    if sum(counts.values()) != expected_candidate_count:
        raise AmylaseSplitError("role_counts must sum to expected_candidate_count")
    if candidate_input.row_count != expected_candidate_count:
        raise AmylaseSplitError("candidate_input row_count must equal expected_candidate_count")
    if upstream_metadata_input.row_count != expected_candidate_count * len(upstream_source_keys):
        raise AmylaseSplitError(
            "upstream_metadata_input row_count must cover every candidate and source"
        )

    expected_components = _positive_int(config, "expected_component_count")
    expected_singletons = _non_negative_int(config, "expected_singleton_component_count")
    expected_non_singletons = _positive_int(config, "expected_non_singleton_component_count")
    if expected_singletons + expected_non_singletons != expected_components:
        raise AmylaseSplitError("singleton and non-singleton counts must sum to component count")
    expected_largest = _positive_int(config, "expected_largest_component_size")
    expected_second_largest = _positive_int(config, "expected_second_largest_component_size")
    if counts[_ROLE_CANDIDATE_POOL] != expected_largest:
        raise AmylaseSplitError("candidate-pool count must equal largest component size")
    if expected_second_largest > expected_largest:
        raise AmylaseSplitError("second-largest component cannot exceed the largest")

    return AmylaseSplitConfig(
        dataset_id=_string(config, "dataset_id"),
        dataset_version=_string(config, "dataset_version"),
        task_id=_string(config, "task_id"),
        split_id=_string(config, "split_id"),
        protocol_version=_string(config, "protocol_version"),
        processing_manifest_sha256=_lower_hex_digest(
            _string(config, "processing_manifest_sha256"),
            field="processing_manifest_sha256",
        ),
        candidate_input=candidate_input,
        upstream_metadata_input=upstream_metadata_input,
        sequence_length=_positive_int(config, "sequence_length"),
        allowed_amino_acids=alphabet,
        expected_candidate_count=expected_candidate_count,
        grouping_method="full_length_hamming1_connected_component",
        hamming_distance=1,
        protocol_namespace=namespace,
        group_id_algorithm="sha256",
        group_preimage_format=cast(str, config["group_preimage_format"]),
        seed=_non_negative_int(config, "seed"),
        ordering_key_algorithm="sha256",
        ordering_preimage_format=cast(str, config["ordering_preimage_format"]),
        ordering_tie_break=("ordering_key", "group_id"),
        assignment_role_order=role_order,
        role_counts=counts,
        expected_hamming1_pair_count=_non_negative_int(config, "expected_hamming1_pair_count"),
        expected_component_count=expected_components,
        expected_singleton_component_count=expected_singletons,
        expected_non_singleton_component_count=expected_non_singletons,
        expected_largest_component_size=expected_largest,
        expected_second_largest_component_size=expected_second_largest,
        upstream_source_keys=upstream_source_keys,
        expected_upstream_cross_role_edges=_integer_map(
            config, "expected_upstream_cross_role_edges", upstream_source_keys
        ),
        leakage_controls=_string_list(config, "leakage_controls"),
        known_limitations=_string_list(config, "known_limitations"),
    )


def _load_amylase_split_config_bytes(payload: bytes) -> AmylaseSplitConfig:
    return _parse_amylase_split_config(_config_mapping(payload))


def load_amylase_split_config(path: Path) -> AmylaseSplitConfig:
    """Load the strict machine-readable Issue #19 split contract."""
    return _load_amylase_split_config_bytes(path.read_bytes())


def group_id_for_members(namespace: str, candidate_ids: tuple[str, ...]) -> str:
    """Return the exact frozen group digest for one sorted component membership."""
    if not candidate_ids:
        raise AmylaseSplitError("a component must contain at least one candidate")
    if len(candidate_ids) >= 2**32:
        raise AmylaseSplitError("component member count exceeds uint32")
    sorted_ids = tuple(sorted(candidate_ids))
    if len(set(sorted_ids)) != len(sorted_ids):
        raise AmylaseSplitError("component candidate IDs must be unique")
    raw_ids: list[bytes] = []
    for candidate_id in sorted_ids:
        _lower_hex_digest(candidate_id, field="candidate_id")
        raw_ids.append(bytes.fromhex(candidate_id))
    preimage = (
        namespace.encode("ascii")
        + b"\x00group-id\x00"
        + len(sorted_ids).to_bytes(4, "big", signed=False)
        + b"".join(raw_ids)
    )
    return hashlib.sha256(preimage).hexdigest()


def ordering_key_for_group(namespace: str, seed: int, group_id: str) -> str:
    """Return the exact frozen seed-salted ordering digest for one group."""
    if seed < 0 or seed >= 2**64:
        raise AmylaseSplitError("seed must fit unsigned uint64")
    _lower_hex_digest(group_id, field="group_id")
    preimage = (
        namespace.encode("ascii")
        + b"\x00order-key\x00"
        + seed.to_bytes(8, "big", signed=False)
        + bytes.fromhex(group_id)
    )
    return hashlib.sha256(preimage).hexdigest()


class _DisjointSet:
    def __init__(self, candidate_ids: tuple[str, ...]) -> None:
        self._parent = {candidate_id: candidate_id for candidate_id in candidate_ids}

    def find(self, candidate_id: str) -> str:
        parent = self._parent[candidate_id]
        while parent != self._parent[parent]:
            parent = self._parent[parent]
        while candidate_id != parent:
            next_candidate = self._parent[candidate_id]
            self._parent[candidate_id] = parent
            candidate_id = next_candidate
        return parent

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self._parent[right_root] = left_root


def _modal_reference(candidates: tuple[SplitCandidate, ...]) -> str:
    return "".join(
        min(
            amino_acid
            for amino_acid, count in Counter(
                candidate.sequence[index] for candidate in candidates
            ).items()
            if count == max(Counter(candidate.sequence[index] for candidate in candidates).values())
        )
        for index in range(len(candidates[0].sequence))
    )


MutationSignature = tuple[tuple[int, str], ...]


def _hamming1_edges(
    candidates: tuple[SplitCandidate, ...],
) -> tuple[str, tuple[tuple[str, str], ...]]:
    reference = _modal_reference(candidates)
    signature_to_candidate: dict[MutationSignature, str] = {}
    signatures: dict[str, MutationSignature] = {}
    for candidate in candidates:
        signature = tuple(
            (index, amino_acid)
            for index, amino_acid in enumerate(candidate.sequence)
            if amino_acid != reference[index]
        )
        if signature in signature_to_candidate:
            raise AmylaseSplitError("distinct candidates have duplicate sequences")
        signature_to_candidate[signature] = candidate.candidate_id
        signatures[candidate.candidate_id] = signature

    edges: set[tuple[str, str]] = set()
    substitution_buckets: dict[tuple[MutationSignature, int], list[str]] = defaultdict(list)
    for candidate_id, signature in signatures.items():
        for mutation_index, (position, _) in enumerate(signature):
            base = signature[:mutation_index] + signature[mutation_index + 1 :]
            base_candidate = signature_to_candidate.get(base)
            if base_candidate is not None:
                left, right = sorted((candidate_id, base_candidate))
                edges.add((left, right))
            substitution_buckets[(base, position)].append(candidate_id)

    for bucket in substitution_buckets.values():
        ordered = sorted(bucket)
        for left_index, left in enumerate(ordered):
            for right in ordered[left_index + 1 :]:
                edges.add((left, right))
    return reference, tuple(sorted(edges))


def _components(
    config: AmylaseSplitConfig,
    candidates: tuple[SplitCandidate, ...],
    edges: tuple[tuple[str, str], ...],
) -> tuple[Component, ...]:
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    disjoint_set = _DisjointSet(candidate_ids)
    for left, right in edges:
        disjoint_set.union(left, right)
    by_root: dict[str, list[str]] = defaultdict(list)
    for candidate_id in candidate_ids:
        by_root[disjoint_set.find(candidate_id)].append(candidate_id)

    components: list[Component] = []
    seen_group_ids: dict[str, tuple[str, ...]] = {}
    for raw_members in by_root.values():
        members = tuple(sorted(raw_members))
        group_id = group_id_for_members(config.protocol_namespace, members)
        previous = seen_group_ids.get(group_id)
        if previous is not None and previous != members:
            raise AmylaseSplitError("distinct memberships produced the same group_id")
        seen_group_ids[group_id] = members
        components.append(
            Component(
                members=members,
                group_id=group_id,
                ordering_key=ordering_key_for_group(
                    config.protocol_namespace, config.seed, group_id
                ),
            )
        )
    return tuple(sorted(components, key=lambda item: item.group_id))


def assign_component_roles(
    components: tuple[Component, ...],
    *,
    candidate_pool_size: int,
    assignable_role_counts: dict[str, int],
    role_order: tuple[str, ...],
) -> dict[str, str]:
    """Apply the frozen largest-component reservation and greedy role assignment."""
    if role_order != _ASSIGNABLE_ROLES or set(assignable_role_counts) != set(role_order):
        raise AmylaseSplitError("assignable roles do not match the frozen role order")
    largest = [
        component for component in components if len(component.members) == candidate_pool_size
    ]
    if len(largest) != 1:
        raise AmylaseSplitError("candidate-pool component must be unique at the frozen size")
    candidate_pool = largest[0]
    remaining = [component for component in components if component != candidate_pool]
    non_singletons = sorted(
        (component for component in remaining if len(component.members) > 1),
        key=lambda item: (-len(item.members), item.ordering_key, item.group_id),
    )
    singletons = sorted(
        (component for component in remaining if len(component.members) == 1),
        key=lambda item: (item.ordering_key, item.group_id),
    )

    capacities = dict(assignable_role_counts)
    assignments = {candidate_pool.group_id: _ROLE_CANDIDATE_POOL}
    role_rank = {role: index for index, role in enumerate(role_order)}
    for component in (*non_singletons, *singletons):
        size = len(component.members)
        eligible = [role for role in role_order if capacities[role] >= size]
        if not eligible:
            raise AmylaseSplitError("no role has sufficient remaining capacity for a group")
        role = min(eligible, key=lambda item: (-capacities[item], role_rank[item]))
        assignments[component.group_id] = role
        capacities[role] -= size
    if any(capacities.values()):
        raise AmylaseSplitError("final role deficits are nonzero")
    return assignments


def _parse_candidates(
    config: AmylaseSplitConfig, candidate_bytes: bytes
) -> tuple[SplitCandidate, ...]:
    try:
        reader = csv.DictReader(io.StringIO(candidate_bytes.decode("utf-8")))
    except UnicodeDecodeError as error:
        raise AmylaseSplitError("candidate artifact must be UTF-8 CSV") from error
    if tuple(reader.fieldnames or ()) != ("candidate_id", "sequence"):
        raise AmylaseSplitError("candidate artifact fields must be candidate_id,sequence")
    allowed = set(config.allowed_amino_acids)
    candidates: list[SplitCandidate] = []
    seen_ids: set[str] = set()
    seen_sequences: set[str] = set()
    for row_number, row in enumerate(reader, start=2):
        candidate_id = cast(str | None, row.get("candidate_id"))
        sequence = cast(str | None, row.get("sequence"))
        if candidate_id is None or sequence is None:
            raise AmylaseSplitError(f"candidate artifact row {row_number} is incomplete")
        _lower_hex_digest(candidate_id, field="candidate_id")
        if candidate_id in seen_ids:
            raise AmylaseSplitError("candidate artifact contains duplicate candidate_id")
        if sequence != sequence.strip().upper():
            raise AmylaseSplitError("candidate sequence must be canonical uppercase text")
        if len(sequence) != config.sequence_length or not set(sequence) <= allowed:
            raise AmylaseSplitError("candidate artifact contains an invalid sequence")
        if sequence in seen_sequences:
            raise AmylaseSplitError("candidate artifact contains duplicate sequence")
        seen_ids.add(candidate_id)
        seen_sequences.add(sequence)
        candidates.append(SplitCandidate(candidate_id=candidate_id, sequence=sequence))
    if len(candidates) != config.expected_candidate_count:
        raise AmylaseSplitError("candidate count differs from the frozen split config")
    return tuple(sorted(candidates, key=lambda item: item.candidate_id))


def _parse_upstream_memberships(
    config: AmylaseSplitConfig,
    upstream_bytes: bytes,
    candidate_ids: frozenset[str],
) -> tuple[UpstreamMembership, ...]:
    try:
        reader = csv.DictReader(io.StringIO(upstream_bytes.decode("utf-8")))
    except UnicodeDecodeError as error:
        raise AmylaseSplitError("upstream metadata artifact must be UTF-8 CSV") from error
    if tuple(reader.fieldnames or ()) != ("candidate_id", "source_key", "set", "validation"):
        raise AmylaseSplitError(
            "upstream metadata fields must be candidate_id,source_key,set,validation"
        )
    memberships: list[UpstreamMembership] = []
    seen: set[tuple[str, str]] = set()
    expected_sources = set(config.upstream_source_keys)
    for row_number, row in enumerate(reader, start=2):
        values = [row.get(field) for field in ("candidate_id", "source_key", "set", "validation")]
        if any(not isinstance(value, str) or not value for value in values):
            raise AmylaseSplitError(f"upstream metadata row {row_number} is incomplete")
        candidate_id, source_key, set_value, validation_value = cast(list[str], values)
        if candidate_id not in candidate_ids:
            raise AmylaseSplitError("upstream metadata contains an unknown candidate_id")
        if source_key not in expected_sources:
            raise AmylaseSplitError("upstream metadata contains an unknown source_key")
        key = (candidate_id, source_key)
        if key in seen:
            raise AmylaseSplitError("upstream metadata contains duplicate candidate/source rows")
        seen.add(key)
        memberships.append(
            UpstreamMembership(
                candidate_id=candidate_id,
                source_key=source_key,
                set_value=set_value,
                validation_value=validation_value,
            )
        )
    expected = {
        (candidate_id, source_key)
        for candidate_id in candidate_ids
        for source_key in config.upstream_source_keys
    }
    if seen != expected:
        raise AmylaseSplitError("upstream metadata is incomplete for the candidate universe")
    return tuple(sorted(memberships, key=lambda item: (item.candidate_id, item.source_key)))


def _verify_artifact(payload: bytes, reference: ArtifactReference, *, field: str) -> None:
    if len(payload) != reference.size_bytes:
        raise AmylaseSplitError(f"{field} size does not match the frozen reference")
    if hashlib.sha256(payload).hexdigest() != reference.sha256:
        raise AmylaseSplitError(f"{field} SHA-256 does not match the frozen reference")


def _processing_output_artifacts(processing_manifest_bytes: bytes) -> dict[str, ArtifactReference]:
    try:
        raw = cast(object, json.loads(processing_manifest_bytes))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AmylaseSplitError("Processing Manifest must be valid UTF-8 JSON") from error
    if not isinstance(raw, dict):
        raise AmylaseSplitError("Processing Manifest must be an object")
    manifest = cast(dict[str, object], raw)
    observable = manifest.get("observable_feature_fields")
    hidden = manifest.get("hidden_label_field")
    if not isinstance(observable, list) or hidden != "target" or "target" in observable:
        raise AmylaseSplitError("Processing Manifest field visibility is incompatible")
    output_files = manifest.get("output_files")
    if not isinstance(output_files, list) or not output_files:
        raise AmylaseSplitError("Processing Manifest output_files must be a non-empty array")
    artifacts: dict[str, ArtifactReference] = {}
    for raw_artifact in output_files:
        if not isinstance(raw_artifact, dict):
            raise AmylaseSplitError("Processing Manifest output entries must be objects")
        artifact = cast(dict[str, object], raw_artifact)
        object_key = _safe_object_key(_string(artifact, "object_key"), field="output_files")
        if object_key in artifacts:
            raise AmylaseSplitError("Processing Manifest output object keys must be unique")
        artifacts[object_key] = ArtifactReference(
            object_key=object_key,
            sha256=_lower_hex_digest(_string(artifact, "sha256"), field="output sha256"),
            size_bytes=_non_negative_int(artifact, "size_bytes"),
            row_count=_positive_int(artifact, "row_count"),
        )
    return artifacts


def _verify_processing_chain(
    config: AmylaseSplitConfig,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
) -> None:
    if hashlib.sha256(processing_manifest_bytes).hexdigest() != config.processing_manifest_sha256:
        raise AmylaseSplitError("Processing Manifest checksum does not match split config")
    outputs = _processing_output_artifacts(processing_manifest_bytes)
    for reference, payload, field in (
        (config.candidate_input, candidate_bytes, "candidate_input"),
        (config.upstream_metadata_input, upstream_bytes, "upstream_metadata_input"),
    ):
        manifest_reference = outputs.get(reference.object_key)
        if manifest_reference != reference:
            raise AmylaseSplitError(f"{field} does not match the Processing Manifest")
        _verify_artifact(payload, reference, field=field)


def _upstream_crossing_counts(
    config: AmylaseSplitConfig,
    upstream: tuple[UpstreamMembership, ...],
    edges: tuple[tuple[str, str], ...],
) -> dict[str, int]:
    roles = {
        (item.source_key, item.candidate_id): (item.set_value, item.validation_value)
        for item in upstream
    }
    return {
        source_key: sum(
            roles[(source_key, left)] != roles[(source_key, right)] for left, right in edges
        )
        for source_key in config.upstream_source_keys
    }


def build_amylase_split_membership(
    *,
    config: AmylaseSplitConfig,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
) -> SplitBuild:
    """Build membership and audit facts without reading hidden truth."""
    candidates = _parse_candidates(config, candidate_bytes)
    candidate_ids = frozenset(candidate.candidate_id for candidate in candidates)
    upstream = _parse_upstream_memberships(config, upstream_bytes, candidate_ids)
    reference, edges = _hamming1_edges(candidates)
    components = _components(config, candidates, edges)

    sizes = sorted((len(component.members) for component in components), reverse=True)
    singleton_count = sum(size == 1 for size in sizes)
    non_singleton_count = len(sizes) - singleton_count
    observed_topology = (
        len(edges),
        len(components),
        singleton_count,
        non_singleton_count,
        sizes[0],
        sizes[1],
    )
    expected_topology = (
        config.expected_hamming1_pair_count,
        config.expected_component_count,
        config.expected_singleton_component_count,
        config.expected_non_singleton_component_count,
        config.expected_largest_component_size,
        config.expected_second_largest_component_size,
    )
    if observed_topology != expected_topology:
        raise AmylaseSplitError("observed Hamming-1 topology differs from the frozen config")

    assignments = assign_component_roles(
        components,
        candidate_pool_size=config.role_counts[_ROLE_CANDIDATE_POOL],
        assignable_role_counts={role: config.role_counts[role] for role in _ASSIGNABLE_ROLES},
        role_order=config.assignment_role_order,
    )
    membership = tuple(
        sorted(
            (
                SplitMembership(candidate_id, assignments[component.group_id], component.group_id)
                for component in components
                for candidate_id in component.members
            ),
            key=lambda item: item.candidate_id,
        )
    )
    role_by_candidate = {item.candidate_id: item.role for item in membership}
    cross_role_edges = sum(
        role_by_candidate[left] != role_by_candidate[right] for left, right in edges
    )
    if cross_role_edges:
        raise AmylaseSplitError("a Hamming-1 edge crosses benchmark roles")
    upstream_crossings = _upstream_crossing_counts(config, upstream, edges)
    if upstream_crossings != config.expected_upstream_cross_role_edges:
        raise AmylaseSplitError("upstream comparison differs from the frozen config")

    partition_summary: list[dict[str, object]] = []
    for role in _ALL_ROLES:
        role_components = [
            component for component in components if assignments[component.group_id] == role
        ]
        partition_summary.append(
            {
                "partition": role,
                "candidate_count": sum(len(component.members) for component in role_components),
                "group_count": len(role_components),
                "largest_group_size": max(len(component.members) for component in role_components),
            }
        )
    if {cast(str, item["partition"]): item["candidate_count"] for item in partition_summary} != (
        config.role_counts
    ):
        raise AmylaseSplitError("assigned candidate counts differ from the frozen role counts")

    audit: dict[str, object] = {
        "schema_version": "1",
        "split_id": config.split_id,
        "processing_manifest_sha256": config.processing_manifest_sha256,
        "candidate_count": len(candidates),
        "sequence_length": config.sequence_length,
        "modal_reference_sha256": hashlib.sha256(reference.encode("ascii")).hexdigest(),
        "grouping_method": config.grouping_method,
        "hamming_distance": config.hamming_distance,
        "hamming1_pair_count": len(edges),
        "component_count": len(components),
        "singleton_component_count": singleton_count,
        "non_singleton_component_count": non_singleton_count,
        "largest_component_size": sizes[0],
        "second_largest_component_size": sizes[1],
        "cross_role_hamming1_edge_count": cross_role_edges,
        "partition_summary": partition_summary,
        "upstream_cross_role_hamming1_edges": upstream_crossings,
        "upstream_metadata_role": "audit_only_not_benchmark_membership",
        "hidden_truth_read": False,
        "known_limitations": list(config.known_limitations),
    }
    return SplitBuild(membership=membership, audit=audit)


def _csv_bytes(rows: tuple[SplitMembership, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=("candidate_id", "role", "group_id"),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(
        {
            "candidate_id": item.candidate_id,
            "role": item.role,
            "group_id": item.group_id,
        }
        for item in rows
    )
    return output.getvalue().encode("utf-8")


def _json_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _artifact(
    object_key: str, payload: bytes, *, row_count: int | None = None
) -> dict[str, object]:
    result: dict[str, object] = {
        "object_key": object_key,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    if row_count is not None:
        result["row_count"] = row_count
    return result


def build_amylase_split_bundle(
    *,
    config: AmylaseSplitConfig,
    config_bytes: bytes,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic split artifacts and a Split Manifest without publishing them."""
    _git_commit(git_commit)
    if _load_amylase_split_config_bytes(config_bytes) != config:
        raise AmylaseSplitError("split config object does not match split config bytes")
    _verify_processing_chain(config, processing_manifest_bytes, candidate_bytes, upstream_bytes)
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    identity = {
        "processing_manifest_sha256": config.processing_manifest_sha256,
        "split_config_sha256": config_sha256,
        "git_commit": git_commit,
    }
    protocol_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prefix = (
        f"splits/{config.dataset_id}/{config.dataset_version}/{config.task_id}/"
        f"{config.split_id}/{protocol_hash}"
    )
    build = build_amylase_split_membership(
        config=config,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
    )
    membership_key = f"{prefix}/membership.csv"
    audit_key = f"{prefix}/split-audit.json"
    payloads = {
        membership_key: _csv_bytes(build.membership),
        audit_key: _json_bytes(build.audit),
    }
    partition_summary = cast(list[dict[str, object]], build.audit["partition_summary"])
    manifest: dict[str, object] = {
        "schema_version": "1",
        "split_id": config.split_id,
        "protocol_version": config.protocol_version,
        "processing_manifest_sha256": config.processing_manifest_sha256,
        "split_config_sha256": config_sha256,
        "git_commit": git_commit,
        "membership_file": _artifact(
            membership_key,
            payloads[membership_key],
            row_count=len(build.membership),
        ),
        "audit_file": _artifact(audit_key, payloads[audit_key]),
        "partitions": list(_ALL_ROLES),
        "grouping_unit": config.grouping_method,
        "leakage_controls": list(config.leakage_controls),
        "held_out_partitions": ["held-out"],
        "partition_summary": partition_summary,
        "group_summary": {
            "total_group_count": build.audit["component_count"],
            "singleton_group_count": build.audit["singleton_component_count"],
            "non_singleton_group_count": build.audit["non_singleton_component_count"],
            "largest_group_size": build.audit["largest_component_size"],
            "cross_partition_hamming1_edges": build.audit["cross_role_hamming1_edge_count"],
        },
        "known_limitations": list(config.known_limitations),
    }
    return payloads, manifest


def validate_amylase_split_bundle(
    *,
    config: AmylaseSplitConfig,
    config_bytes: bytes,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
    membership_bytes: bytes,
    audit_bytes: bytes,
    manifest: dict[str, object],
) -> None:
    """Rebuild the full expected bundle and reject any semantic or byte mismatch."""
    git_commit = manifest.get("git_commit")
    if not isinstance(git_commit, str):
        raise AmylaseSplitError("Split Manifest git_commit must be a string")
    expected_payloads, expected_manifest = build_amylase_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_manifest_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit=git_commit,
    )
    if manifest != expected_manifest:
        raise AmylaseSplitError("Split Manifest does not match the rebuilt expected manifest")
    by_name = {PurePosixPath(key).name: payload for key, payload in expected_payloads.items()}
    if membership_bytes != by_name["membership.csv"]:
        raise AmylaseSplitError("membership artifact does not match rebuilt exact bytes")
    if audit_bytes != by_name["split-audit.json"]:
        raise AmylaseSplitError("split audit does not match rebuilt exact bytes")


def validate_partition_access(
    split_manifest: dict[str, object],
    experiment_config: dict[str, object],
    requested_partitions: tuple[str, ...],
) -> None:
    """Fail closed when a consumer requests unauthorized held-out membership."""
    partitions = split_manifest.get("partitions")
    held_out = split_manifest.get("held_out_partitions")
    if not isinstance(partitions, list) or not all(isinstance(item, str) for item in partitions):
        raise AmylaseSplitError("Split Manifest partitions are invalid")
    if not isinstance(held_out, list) or not all(isinstance(item, str) for item in held_out):
        raise AmylaseSplitError("Split Manifest held_out_partitions are invalid")
    unknown = set(requested_partitions) - set(cast(list[str], partitions))
    if unknown:
        raise AmylaseSplitError("consumer requested an unknown split partition")
    requests_held_out = bool(set(requested_partitions) & set(cast(list[str], held_out)))
    if not requests_held_out:
        return
    if experiment_config.get("held_out_authorized") is not True:
        raise AmylaseSplitError("held-out access is not authorized by Experiment Config")
    reference = experiment_config.get("held_out_authorization_reference")
    if not isinstance(reference, str) or not reference:
        raise AmylaseSplitError("held-out authorization reference is missing")
