"""Deterministic topology-extrapolation split for FLIP2 IRED."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast


class IredSplitError(ValueError):
    """Raised when the frozen IRED split contract cannot be satisfied exactly."""


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """One checksum-bound processing input artifact."""

    object_key: str
    sha256: str
    size_bytes: int
    row_count: int


@dataclass(frozen=True, slots=True)
class IredSplitConfig:
    """Frozen rules for the IRED topology-extrapolation split."""

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
    reference_sequence: str
    reference_sequence_sha256: str
    mutation_count_method: str
    expected_mutation_count_min: int
    expected_mutation_count_max: int
    grouping_method: str
    hamming_distance: int
    protocol_namespace: str
    group_id_algorithm: str
    group_preimage_format: str
    seed: int
    ordering_key_algorithm: str
    ordering_preimage_format: str
    ordering_tie_break: tuple[str, ...]
    assignment_method: str
    assignment_role_order: tuple[str, ...]
    role_counts: dict[str, int]
    expected_hamming1_pair_count: int
    expected_component_count: int
    expected_singleton_component_count: int
    expected_non_singleton_component_count: int
    expected_largest_component_size: int
    expected_second_largest_component_size: int
    expected_singleton_mutation_counts: dict[int, int]
    expected_singleton_role_counts_by_mutation: dict[int, dict[str, int]]
    upstream_source_keys: tuple[str, ...]
    expected_upstream_cross_role_edges: dict[str, int]
    expected_upstream_assignment_disagreement_count: int
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
    """One Hamming-1 component and its deterministic assignment metadata."""

    members: tuple[str, ...]
    group_id: str
    mutation_count: int | None
    ordering_key: str | None


@dataclass(frozen=True, slots=True)
class SplitBuild:
    """Deterministic membership plus compact audit facts."""

    membership: tuple[SplitMembership, ...]
    audit: dict[str, object]


_SINGLETON_ROLES = ("candidate-pool", "validation", "held-out")
_ALL_ROLES = ("candidate-pool", "development", "validation", "held-out")
_SHA256_HEX_LENGTH = 64
_ARTIFACT_KEYS = {"object_key", "sha256", "size_bytes", "row_count"}
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
    "reference_sequence",
    "reference_sequence_sha256",
    "mutation_count_method",
    "expected_mutation_count_min",
    "expected_mutation_count_max",
    "grouping_method",
    "hamming_distance",
    "protocol_namespace",
    "group_id_algorithm",
    "group_preimage_format",
    "seed",
    "ordering_key_algorithm",
    "ordering_preimage_format",
    "ordering_tie_break",
    "assignment_method",
    "assignment_role_order",
    "role_counts",
    "expected_hamming1_pair_count",
    "expected_component_count",
    "expected_singleton_component_count",
    "expected_non_singleton_component_count",
    "expected_largest_component_size",
    "expected_second_largest_component_size",
    "expected_singleton_mutation_counts",
    "expected_singleton_role_counts_by_mutation",
    "group_id_collision_policy",
    "ordering_key_collision_policy",
    "impossible_allocation_policy",
    "upstream_source_keys",
    "expected_upstream_cross_role_edges",
    "expected_upstream_assignment_disagreement_count",
    "leakage_controls",
    "known_limitations",
}


def _string(mapping: dict[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise IredSplitError(f"{field} must be a non-empty string")
    return value


def _string_list(mapping: dict[str, object], field: str) -> tuple[str, ...]:
    value = mapping.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise IredSplitError(f"{field} must contain unique non-empty strings")
    return tuple(cast(list[str], value))


def _integer(mapping: dict[str, object], field: str, *, positive: bool = False) -> int:
    value = mapping.get(field)
    minimum = 1 if positive else 0
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise IredSplitError(f"{field} must be a {qualifier} integer")
    return value


def _lower_hex_digest(value: str, *, field: str) -> str:
    if len(value) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise IredSplitError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _git_commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise IredSplitError("git_commit must be a full lowercase 40-character commit")
    return value


def _safe_object_key(value: str, *, field: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise IredSplitError(f"{field} must be a safe relative POSIX object key")
    return value


def _artifact_reference(mapping: dict[str, object], field: str) -> ArtifactReference:
    value = mapping.get(field)
    if not isinstance(value, dict):
        raise IredSplitError(f"{field} must be an object")
    artifact = cast(dict[str, object], value)
    if set(artifact) != _ARTIFACT_KEYS:
        raise IredSplitError(f"{field} fields do not match version 1")
    return ArtifactReference(
        object_key=_safe_object_key(_string(artifact, "object_key"), field=field),
        sha256=_lower_hex_digest(_string(artifact, "sha256"), field=f"{field}.sha256"),
        size_bytes=_integer(artifact, "size_bytes"),
        row_count=_integer(artifact, "row_count", positive=True),
    )


def _integer_map(
    mapping: dict[str, object], field: str, expected_keys: tuple[str, ...]
) -> dict[str, int]:
    value = mapping.get(field)
    if not isinstance(value, dict) or set(value) != set(expected_keys):
        raise IredSplitError(f"{field} keys do not match the frozen contract")
    raw = cast(dict[str, object], value)
    return {key: _integer(raw, key) for key in expected_keys}


def _role_counts(mapping: dict[str, object]) -> dict[str, int]:
    value = mapping.get("role_counts")
    if not isinstance(value, dict) or set(value) != set(_ALL_ROLES):
        raise IredSplitError("role_counts must define exactly the four frozen roles")
    raw = cast(dict[str, object], value)
    return {role: _integer(raw, role, positive=True) for role in _ALL_ROLES}


def _mutation_counts(mapping: dict[str, object]) -> dict[int, int]:
    value = mapping.get("expected_singleton_mutation_counts")
    if not isinstance(value, dict) or not value:
        raise IredSplitError("expected_singleton_mutation_counts must be a non-empty object")
    result: dict[int, int] = {}
    for raw_key, raw_count in cast(dict[str, object], value).items():
        if not raw_key.isdecimal() or str(int(raw_key)) != raw_key:
            raise IredSplitError("mutation-count keys must be canonical decimal integers")
        if not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 1:
            raise IredSplitError("singleton mutation counts must be positive integers")
        result[int(raw_key)] = raw_count
    return dict(sorted(result.items()))


def _mutation_role_counts(
    mapping: dict[str, object], mutation_counts: dict[int, int]
) -> dict[int, dict[str, int]]:
    value = mapping.get("expected_singleton_role_counts_by_mutation")
    expected_keys = {str(key) for key in mutation_counts}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise IredSplitError("mutation role-count strata do not match mutation counts")
    result: dict[int, dict[str, int]] = {}
    for mutation_count in mutation_counts:
        raw_counts = cast(dict[str, object], value[str(mutation_count)])
        if not isinstance(raw_counts, dict) or set(raw_counts) != set(_SINGLETON_ROLES):
            raise IredSplitError("each mutation stratum must define the three singleton roles")
        counts = {role: _integer(raw_counts, role) for role in _SINGLETON_ROLES}
        if sum(counts.values()) != mutation_counts[mutation_count]:
            raise IredSplitError("mutation stratum role counts do not sum to its size")
        result[mutation_count] = counts
    return result


def role_schedule(
    role_counts: dict[str, int],
    role_order: tuple[str, ...] = _SINGLETON_ROLES,
) -> tuple[str, ...]:
    """Return the frozen global integer-deficit schedule."""
    if set(role_counts) != set(role_order) or len(set(role_order)) != len(role_order):
        raise IredSplitError("schedule roles do not match the frozen role order")
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in role_counts.values()
    ):
        raise IredSplitError("schedule capacities must be non-negative integers")
    total = sum(role_counts.values())
    if total < 1:
        raise IredSplitError("schedule must contain at least one assignment")
    assigned = {role: 0 for role in role_order}
    schedule: list[str] = []
    for index in range(1, total + 1):
        eligible = [role for role in role_order if assigned[role] < role_counts[role]]
        if not eligible:
            raise IredSplitError("global deficit schedule exhausted all eligible roles")
        best = max(
            eligible,
            key=lambda role: (
                index * role_counts[role] - total * assigned[role],
                -role_order.index(role),
            ),
        )
        schedule.append(best)
        assigned[best] += 1
    if assigned != role_counts:
        raise IredSplitError("global deficit schedule did not fill exact capacities")
    return tuple(schedule)


def _config_mapping(payload: bytes) -> dict[str, object]:
    try:
        raw = cast(object, json.loads(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IredSplitError("split config must be valid UTF-8 JSON") from error
    if not isinstance(raw, dict):
        raise IredSplitError("split config must be an object")
    return cast(dict[str, object], raw)


def _parse_ired_split_config(config: dict[str, object]) -> IredSplitConfig:
    if set(config) != _CONFIG_KEYS or config.get("config_version") != "1":
        raise IredSplitError("split config fields do not match version 1")
    exact_values = {
        "task_id": "ired",
        "grouping_method": "full_length_hamming1_connected_component",
        "hamming_distance": 1,
        "mutation_count_method": "full_length_hamming_distance_to_reference",
        "group_id_algorithm": "sha256",
        "group_preimage_format": (
            "namespace_nul_group-id_nul_uint32be_count_concatenated_raw_candidate_ids"
        ),
        "ordering_key_algorithm": "sha256",
        "ordering_preimage_format": (
            "namespace_nul_order-key_nul_uint64be_seed_uint16be_mutation_count_raw_group_id"
        ),
        "assignment_method": "mutation_count_stratified_global_deficit_schedule",
        "group_id_collision_policy": "error",
        "ordering_key_collision_policy": "error",
        "impossible_allocation_policy": "error",
    }
    for field, expected in exact_values.items():
        if config.get(field) != expected:
            raise IredSplitError(f"{field} does not match the frozen contract")
    if _string_list(config, "ordering_tie_break") != (
        "mutation_count",
        "ordering_key",
        "group_id",
    ):
        raise IredSplitError("ordering_tie_break does not match the frozen contract")
    role_order = _string_list(config, "assignment_role_order")
    if role_order != _SINGLETON_ROLES:
        raise IredSplitError("assignment_role_order does not match the frozen contract")

    alphabet = _string(config, "allowed_amino_acids")
    if alphabet != "".join(sorted(set(alphabet))):
        raise IredSplitError("allowed_amino_acids must be sorted and unique")
    reference = _string(config, "reference_sequence")
    sequence_length = _integer(config, "sequence_length", positive=True)
    if (
        reference != reference.upper()
        or len(reference) != sequence_length
        or not set(reference) <= set(alphabet)
    ):
        raise IredSplitError("reference_sequence is invalid")
    reference_sha = _lower_hex_digest(
        _string(config, "reference_sequence_sha256"),
        field="reference_sequence_sha256",
    )
    if hashlib.sha256(reference.encode("ascii")).hexdigest() != reference_sha:
        raise IredSplitError("reference_sequence_sha256 does not match reference_sequence")

    namespace = _string(config, "protocol_namespace")
    try:
        namespace.encode("ascii")
    except UnicodeEncodeError as error:
        raise IredSplitError("protocol_namespace must contain exact ASCII bytes") from error
    candidate_input = _artifact_reference(config, "candidate_input")
    upstream_input = _artifact_reference(config, "upstream_metadata_input")
    upstream_sources = _string_list(config, "upstream_source_keys")
    if len(upstream_sources) != 2:
        raise IredSplitError("IRED v1 requires exactly two publisher split sources")
    counts = _role_counts(config)
    expected_candidates = _integer(config, "expected_candidate_count", positive=True)
    if sum(counts.values()) != expected_candidates:
        raise IredSplitError("role_counts must sum to expected_candidate_count")
    if candidate_input.row_count != expected_candidates:
        raise IredSplitError("candidate_input row_count must equal expected_candidate_count")
    if upstream_input.row_count != expected_candidates * len(upstream_sources):
        raise IredSplitError("upstream metadata must cover every candidate and source")

    expected_components = _integer(config, "expected_component_count", positive=True)
    expected_singletons = _integer(config, "expected_singleton_component_count")
    expected_non_singletons = _integer(
        config, "expected_non_singleton_component_count", positive=True
    )
    expected_largest = _integer(config, "expected_largest_component_size", positive=True)
    expected_second = _integer(config, "expected_second_largest_component_size", positive=True)
    if expected_singletons + expected_non_singletons != expected_components:
        raise IredSplitError("component counts do not sum to expected_component_count")
    if expected_non_singletons != 1 or expected_second != 1:
        raise IredSplitError("IRED v1 requires one giant component and only singleton remainders")
    if counts["development"] != expected_largest:
        raise IredSplitError("development count must equal the giant component size")
    if expected_largest + expected_singletons != expected_candidates:
        raise IredSplitError("giant and singleton counts must cover the candidate universe")
    if sum(counts[role] for role in role_order) != expected_singletons:
        raise IredSplitError("singleton role capacities must equal singleton count")

    mutation_counts = _mutation_counts(config)
    mutation_role_counts = _mutation_role_counts(config, mutation_counts)
    if sum(mutation_counts.values()) != expected_singletons:
        raise IredSplitError("mutation strata must sum to expected singleton count")
    schedule = role_schedule({role: counts[role] for role in role_order}, role_order)
    offset = 0
    for mutation_count, stratum_size in mutation_counts.items():
        observed = Counter(schedule[offset : offset + stratum_size])
        if dict(observed) != {
            role: count for role, count in mutation_role_counts[mutation_count].items() if count
        }:
            raise IredSplitError("frozen mutation quotas do not match the global schedule")
        offset += stratum_size

    mutation_min = _integer(config, "expected_mutation_count_min")
    mutation_max = _integer(config, "expected_mutation_count_max")
    if mutation_min > mutation_max or mutation_max > sequence_length:
        raise IredSplitError("expected mutation-count range is invalid")

    return IredSplitConfig(
        dataset_id=_string(config, "dataset_id"),
        dataset_version=_string(config, "dataset_version"),
        task_id="ired",
        split_id=_string(config, "split_id"),
        protocol_version=_string(config, "protocol_version"),
        processing_manifest_sha256=_lower_hex_digest(
            _string(config, "processing_manifest_sha256"),
            field="processing_manifest_sha256",
        ),
        candidate_input=candidate_input,
        upstream_metadata_input=upstream_input,
        sequence_length=sequence_length,
        allowed_amino_acids=alphabet,
        expected_candidate_count=expected_candidates,
        reference_sequence=reference,
        reference_sequence_sha256=reference_sha,
        mutation_count_method=cast(str, exact_values["mutation_count_method"]),
        expected_mutation_count_min=mutation_min,
        expected_mutation_count_max=mutation_max,
        grouping_method=cast(str, exact_values["grouping_method"]),
        hamming_distance=1,
        protocol_namespace=namespace,
        group_id_algorithm="sha256",
        group_preimage_format=cast(str, exact_values["group_preimage_format"]),
        seed=_integer(config, "seed"),
        ordering_key_algorithm="sha256",
        ordering_preimage_format=cast(str, exact_values["ordering_preimage_format"]),
        ordering_tie_break=("mutation_count", "ordering_key", "group_id"),
        assignment_method=cast(str, exact_values["assignment_method"]),
        assignment_role_order=role_order,
        role_counts=counts,
        expected_hamming1_pair_count=_integer(config, "expected_hamming1_pair_count"),
        expected_component_count=expected_components,
        expected_singleton_component_count=expected_singletons,
        expected_non_singleton_component_count=expected_non_singletons,
        expected_largest_component_size=expected_largest,
        expected_second_largest_component_size=expected_second,
        expected_singleton_mutation_counts=mutation_counts,
        expected_singleton_role_counts_by_mutation=mutation_role_counts,
        upstream_source_keys=upstream_sources,
        expected_upstream_cross_role_edges=_integer_map(
            config, "expected_upstream_cross_role_edges", upstream_sources
        ),
        expected_upstream_assignment_disagreement_count=_integer(
            config, "expected_upstream_assignment_disagreement_count"
        ),
        leakage_controls=_string_list(config, "leakage_controls"),
        known_limitations=_string_list(config, "known_limitations"),
    )


def _load_ired_split_config_bytes(payload: bytes) -> IredSplitConfig:
    return _parse_ired_split_config(_config_mapping(payload))


def load_ired_split_config(path: Path) -> IredSplitConfig:
    """Load the strict machine-readable Issue #22 split contract."""
    return _load_ired_split_config_bytes(path.read_bytes())


def group_id_for_members(namespace: str, candidate_ids: tuple[str, ...]) -> str:
    """Return the exact group digest for one sorted component membership."""
    if not candidate_ids or len(candidate_ids) >= 2**32:
        raise IredSplitError("component member count must fit non-zero uint32")
    sorted_ids = tuple(sorted(candidate_ids))
    if len(set(sorted_ids)) != len(sorted_ids):
        raise IredSplitError("component candidate IDs must be unique")
    raw_ids: list[bytes] = []
    for candidate_id in sorted_ids:
        _lower_hex_digest(candidate_id, field="candidate_id")
        raw_ids.append(bytes.fromhex(candidate_id))
    preimage = (
        namespace.encode("ascii")
        + b"\x00group-id\x00"
        + len(sorted_ids).to_bytes(4, "big")
        + b"".join(raw_ids)
    )
    return hashlib.sha256(preimage).hexdigest()


def ordering_key_for_singleton(
    namespace: str, seed: int, mutation_count: int, group_id: str
) -> str:
    """Return the exact seed- and mutation-salted singleton ordering digest."""
    if seed < 0 or seed >= 2**64:
        raise IredSplitError("seed must fit unsigned uint64")
    if mutation_count < 0 or mutation_count >= 2**16:
        raise IredSplitError("mutation_count must fit unsigned uint16")
    _lower_hex_digest(group_id, field="group_id")
    preimage = (
        namespace.encode("ascii")
        + b"\x00order-key\x00"
        + seed.to_bytes(8, "big")
        + mutation_count.to_bytes(2, "big")
        + bytes.fromhex(group_id)
    )
    return hashlib.sha256(preimage).hexdigest()


def assign_stratified_singletons(
    singletons: tuple[Component, ...],
    role_counts: dict[str, int],
    role_order: tuple[str, ...] = _SINGLETON_ROLES,
) -> dict[str, str]:
    """Assign ordered mutation strata against one global deficit schedule."""
    if any(len(component.members) != 1 for component in singletons):
        raise IredSplitError("only singleton components may use stratified assignment")
    seen_group_ids: dict[str, tuple[str, ...]] = {}
    seen_ordering_keys: dict[str, str] = {}
    for component in singletons:
        if component.mutation_count is None or component.ordering_key is None:
            raise IredSplitError("singleton assignment metadata is incomplete")
        previous_members = seen_group_ids.get(component.group_id)
        if previous_members is not None and previous_members != component.members:
            raise IredSplitError("distinct memberships produced the same group_id")
        seen_group_ids[component.group_id] = component.members
        previous_group = seen_ordering_keys.get(component.ordering_key)
        if previous_group is not None and previous_group != component.group_id:
            raise IredSplitError("distinct singleton group IDs produced the same ordering_key")
        seen_ordering_keys[component.ordering_key] = component.group_id
    schedule = role_schedule(role_counts, role_order)
    if len(schedule) != len(singletons):
        raise IredSplitError("singleton capacity differs from observed singleton count")
    ordered = sorted(
        singletons,
        key=lambda item: (
            cast(int, item.mutation_count),
            bytes.fromhex(cast(str, item.ordering_key)),
            bytes.fromhex(item.group_id),
        ),
    )
    return {component.group_id: schedule[index] for index, component in enumerate(ordered)}


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


MutationSignature = tuple[tuple[int, str], ...]


def _hamming1_edges(
    candidates: tuple[SplitCandidate, ...],
    reference: str,
) -> tuple[dict[str, int], tuple[tuple[str, str], ...]]:
    signature_to_candidate: dict[MutationSignature, str] = {}
    signatures: dict[str, MutationSignature] = {}
    for candidate in candidates:
        signature = tuple(
            (index, amino_acid)
            for index, amino_acid in enumerate(candidate.sequence)
            if amino_acid != reference[index]
        )
        if signature in signature_to_candidate:
            raise IredSplitError("distinct candidates have duplicate sequences")
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
    return (
        {candidate_id: len(signature) for candidate_id, signature in signatures.items()},
        tuple(sorted(edges)),
    )


def _components(
    config: IredSplitConfig,
    candidates: tuple[SplitCandidate, ...],
    mutation_counts: dict[str, int],
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
    seen_ordering_keys: dict[str, str] = {}
    for raw_members in by_root.values():
        members = tuple(sorted(raw_members))
        group_id = group_id_for_members(config.protocol_namespace, members)
        previous = seen_group_ids.get(group_id)
        if previous is not None and previous != members:
            raise IredSplitError("distinct memberships produced the same group_id")
        seen_group_ids[group_id] = members
        mutation_count = mutation_counts[members[0]] if len(members) == 1 else None
        ordering_key = (
            ordering_key_for_singleton(
                config.protocol_namespace,
                config.seed,
                cast(int, mutation_count),
                group_id,
            )
            if len(members) == 1
            else None
        )
        if ordering_key is not None:
            previous_group = seen_ordering_keys.get(ordering_key)
            if previous_group is not None and previous_group != group_id:
                raise IredSplitError("distinct singleton group IDs produced the same ordering_key")
            seen_ordering_keys[ordering_key] = group_id
        components.append(
            Component(
                members=members,
                group_id=group_id,
                mutation_count=mutation_count,
                ordering_key=ordering_key,
            )
        )
    return tuple(sorted(components, key=lambda item: item.group_id))


def _parse_candidates(
    config: IredSplitConfig, candidate_bytes: bytes
) -> tuple[SplitCandidate, ...]:
    try:
        reader = csv.DictReader(io.StringIO(candidate_bytes.decode("utf-8")))
    except UnicodeDecodeError as error:
        raise IredSplitError("candidate artifact must be UTF-8 CSV") from error
    if tuple(reader.fieldnames or ()) != ("candidate_id", "sequence"):
        raise IredSplitError("candidate artifact fields must be candidate_id,sequence")
    allowed = set(config.allowed_amino_acids)
    candidates: list[SplitCandidate] = []
    seen_ids: set[str] = set()
    seen_sequences: set[str] = set()
    for row_number, row in enumerate(reader, start=2):
        candidate_id = cast(str | None, row.get("candidate_id"))
        sequence = cast(str | None, row.get("sequence"))
        if candidate_id is None or sequence is None:
            raise IredSplitError(f"candidate artifact row {row_number} is incomplete")
        _lower_hex_digest(candidate_id, field="candidate_id")
        if candidate_id in seen_ids:
            raise IredSplitError("candidate artifact contains duplicate candidate_id")
        if sequence != sequence.strip().upper():
            raise IredSplitError("candidate sequence must be canonical uppercase text")
        if len(sequence) != config.sequence_length or not set(sequence) <= allowed:
            raise IredSplitError("candidate artifact contains an invalid sequence")
        if sequence in seen_sequences:
            raise IredSplitError("candidate artifact contains duplicate sequence")
        seen_ids.add(candidate_id)
        seen_sequences.add(sequence)
        candidates.append(SplitCandidate(candidate_id=candidate_id, sequence=sequence))
    if len(candidates) != config.expected_candidate_count:
        raise IredSplitError("candidate count differs from the frozen split config")
    return tuple(sorted(candidates, key=lambda item: item.candidate_id))


def _parse_upstream_memberships(
    config: IredSplitConfig,
    upstream_bytes: bytes,
    candidate_ids: frozenset[str],
) -> tuple[UpstreamMembership, ...]:
    try:
        reader = csv.DictReader(io.StringIO(upstream_bytes.decode("utf-8")))
    except UnicodeDecodeError as error:
        raise IredSplitError("upstream metadata artifact must be UTF-8 CSV") from error
    if tuple(reader.fieldnames or ()) != ("candidate_id", "source_key", "set", "validation"):
        raise IredSplitError(
            "upstream metadata fields must be candidate_id,source_key,set,validation"
        )
    memberships: list[UpstreamMembership] = []
    seen: set[tuple[str, str]] = set()
    expected_sources = set(config.upstream_source_keys)
    for row_number, row in enumerate(reader, start=2):
        values = [row.get(field) for field in ("candidate_id", "source_key", "set", "validation")]
        if any(not isinstance(value, str) or not value for value in values):
            raise IredSplitError(f"upstream metadata row {row_number} is incomplete")
        candidate_id, source_key, set_value, validation_value = cast(list[str], values)
        if candidate_id not in candidate_ids:
            raise IredSplitError("upstream metadata contains an unknown candidate_id")
        if source_key not in expected_sources:
            raise IredSplitError("upstream metadata contains an unknown source_key")
        key = (candidate_id, source_key)
        if key in seen:
            raise IredSplitError("upstream metadata contains duplicate candidate/source rows")
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
        raise IredSplitError("upstream metadata is incomplete for the candidate universe")
    return tuple(sorted(memberships, key=lambda item: (item.candidate_id, item.source_key)))


def _verify_artifact(payload: bytes, reference: ArtifactReference, *, field: str) -> None:
    if len(payload) != reference.size_bytes:
        raise IredSplitError(f"{field} size does not match the frozen reference")
    if hashlib.sha256(payload).hexdigest() != reference.sha256:
        raise IredSplitError(f"{field} SHA-256 does not match the frozen reference")


def _processing_output_artifacts(
    processing_manifest_bytes: bytes,
) -> dict[str, ArtifactReference]:
    try:
        raw = cast(object, json.loads(processing_manifest_bytes))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IredSplitError("Processing Manifest must be valid UTF-8 JSON") from error
    if not isinstance(raw, dict):
        raise IredSplitError("Processing Manifest must be an object")
    manifest = cast(dict[str, object], raw)
    observable = manifest.get("observable_feature_fields")
    hidden = manifest.get("hidden_label_field")
    if not isinstance(observable, list) or hidden != "target" or "target" in observable:
        raise IredSplitError("Processing Manifest field visibility is incompatible")
    output_files = manifest.get("output_files")
    if not isinstance(output_files, list) or not output_files:
        raise IredSplitError("Processing Manifest output_files must be a non-empty array")
    artifacts: dict[str, ArtifactReference] = {}
    for raw_artifact in output_files:
        if not isinstance(raw_artifact, dict):
            raise IredSplitError("Processing Manifest output entries must be objects")
        artifact = cast(dict[str, object], raw_artifact)
        object_key = _safe_object_key(_string(artifact, "object_key"), field="output_files")
        if object_key in artifacts:
            raise IredSplitError("Processing Manifest output object keys must be unique")
        artifacts[object_key] = ArtifactReference(
            object_key=object_key,
            sha256=_lower_hex_digest(_string(artifact, "sha256"), field="output sha256"),
            size_bytes=_integer(artifact, "size_bytes"),
            row_count=_integer(artifact, "row_count", positive=True),
        )
    return artifacts


def _verify_processing_chain(
    config: IredSplitConfig,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
) -> None:
    if hashlib.sha256(processing_manifest_bytes).hexdigest() != config.processing_manifest_sha256:
        raise IredSplitError("Processing Manifest checksum does not match split config")
    outputs = _processing_output_artifacts(processing_manifest_bytes)
    for reference, payload, field in (
        (config.candidate_input, candidate_bytes, "candidate_input"),
        (config.upstream_metadata_input, upstream_bytes, "upstream_metadata_input"),
    ):
        if outputs.get(reference.object_key) != reference:
            raise IredSplitError(f"{field} does not match the Processing Manifest")
        _verify_artifact(payload, reference, field=field)


def _upstream_role(item: UpstreamMembership) -> tuple[str, str]:
    return item.set_value, item.validation_value


def _upstream_audit(
    config: IredSplitConfig,
    upstream: tuple[UpstreamMembership, ...],
    edges: tuple[tuple[str, str], ...],
) -> tuple[dict[str, int], int]:
    roles = {(item.source_key, item.candidate_id): _upstream_role(item) for item in upstream}
    crossing_counts = {
        source_key: sum(
            roles[(source_key, left)] != roles[(source_key, right)] for left, right in edges
        )
        for source_key in config.upstream_source_keys
    }
    first, second = config.upstream_source_keys
    disagreement_count = sum(
        roles[(first, candidate_id)] != roles[(second, candidate_id)]
        for candidate_id in {item.candidate_id for item in upstream if item.source_key == first}
    )
    return crossing_counts, disagreement_count


def build_ired_split_membership(
    *,
    config: IredSplitConfig,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
) -> SplitBuild:
    """Build IRED membership and audit facts without reading hidden truth."""
    candidates = _parse_candidates(config, candidate_bytes)
    candidate_ids = frozenset(candidate.candidate_id for candidate in candidates)
    upstream = _parse_upstream_memberships(config, upstream_bytes, candidate_ids)
    mutation_counts, edges = _hamming1_edges(candidates, config.reference_sequence)
    observed_mutation_range = (min(mutation_counts.values()), max(mutation_counts.values()))
    if observed_mutation_range != (
        config.expected_mutation_count_min,
        config.expected_mutation_count_max,
    ):
        raise IredSplitError("observed mutation-count range differs from the frozen config")
    components = _components(config, candidates, mutation_counts, edges)

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
        raise IredSplitError("observed Hamming-1 topology differs from the frozen config")
    giant_components = [
        component
        for component in components
        if len(component.members) == config.expected_largest_component_size
    ]
    singletons = tuple(component for component in components if len(component.members) == 1)
    if len(giant_components) != 1 or len(singletons) != singleton_count:
        raise IredSplitError("IRED v1 requires one unique giant and singleton remainders")

    observed_strata = dict(
        sorted(Counter(cast(int, component.mutation_count) for component in singletons).items())
    )
    if observed_strata != config.expected_singleton_mutation_counts:
        raise IredSplitError("singleton mutation strata differ from the frozen config")
    assignments = assign_stratified_singletons(
        singletons,
        {role: config.role_counts[role] for role in config.assignment_role_order},
        config.assignment_role_order,
    )
    assignments[giant_components[0].group_id] = "development"

    observed_stratum_roles: dict[int, dict[str, int]] = {}
    for mutation_count in observed_strata:
        counts = Counter(
            assignments[component.group_id]
            for component in singletons
            if component.mutation_count == mutation_count
        )
        observed_stratum_roles[mutation_count] = {
            role: counts[role] for role in config.assignment_role_order
        }
    if observed_stratum_roles != config.expected_singleton_role_counts_by_mutation:
        raise IredSplitError("assigned mutation-stratum quotas differ from the frozen config")

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
        raise IredSplitError("a Hamming-1 edge crosses benchmark roles")

    upstream_crossings, upstream_disagreements = _upstream_audit(config, upstream, edges)
    if upstream_crossings != config.expected_upstream_cross_role_edges:
        raise IredSplitError("upstream edge comparison differs from the frozen config")
    if upstream_disagreements != config.expected_upstream_assignment_disagreement_count:
        raise IredSplitError("upstream assignment comparison differs from the frozen config")

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
    assigned_counts = {
        cast(str, item["partition"]): cast(int, item["candidate_count"])
        for item in partition_summary
    }
    if assigned_counts != config.role_counts:
        raise IredSplitError("assigned candidate counts differ from the frozen role counts")

    strata_summary = [
        {
            "mutation_count": mutation_count,
            "singleton_count": observed_strata[mutation_count],
            "role_counts": observed_stratum_roles[mutation_count],
        }
        for mutation_count in observed_strata
    ]
    audit: dict[str, object] = {
        "schema_version": "1",
        "split_id": config.split_id,
        "processing_manifest_sha256": config.processing_manifest_sha256,
        "candidate_count": len(candidates),
        "sequence_length": config.sequence_length,
        "reference_sequence_sha256": config.reference_sequence_sha256,
        "mutation_count_method": config.mutation_count_method,
        "mutation_count_min": observed_mutation_range[0],
        "mutation_count_max": observed_mutation_range[1],
        "grouping_method": config.grouping_method,
        "hamming_distance": config.hamming_distance,
        "hamming1_pair_count": len(edges),
        "component_count": len(components),
        "singleton_component_count": singleton_count,
        "non_singleton_component_count": non_singleton_count,
        "largest_component_size": sizes[0],
        "second_largest_component_size": sizes[1],
        "cross_role_hamming1_edge_count": cross_role_edges,
        "assignment_method": config.assignment_method,
        "assignment_role_order": list(config.assignment_role_order),
        "singleton_mutation_strata": strata_summary,
        "partition_summary": partition_summary,
        "upstream_cross_role_hamming1_edges": upstream_crossings,
        "upstream_assignment_disagreement_count": upstream_disagreements,
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


def build_ired_split_bundle(
    *,
    config: IredSplitConfig,
    config_bytes: bytes,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic split artifacts and a manifest without publishing them."""
    _git_commit(git_commit)
    if _load_ired_split_config_bytes(config_bytes) != config:
        raise IredSplitError("split config object does not match split config bytes")
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
    build = build_ired_split_membership(
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


def validate_ired_split_bundle(
    *,
    config: IredSplitConfig,
    config_bytes: bytes,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
    membership_bytes: bytes,
    audit_bytes: bytes,
    manifest: dict[str, object],
) -> None:
    """Rebuild the bundle and reject any semantic or exact-byte mismatch."""
    git_commit = manifest.get("git_commit")
    if not isinstance(git_commit, str):
        raise IredSplitError("Split Manifest git_commit must be a string")
    expected_payloads, expected_manifest = build_ired_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_manifest_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit=git_commit,
    )
    if manifest != expected_manifest:
        raise IredSplitError("Split Manifest does not match the rebuilt expected manifest")
    by_name = {PurePosixPath(key).name: payload for key, payload in expected_payloads.items()}
    if membership_bytes != by_name["membership.csv"]:
        raise IredSplitError("membership artifact does not match rebuilt exact bytes")
    if audit_bytes != by_name["split-audit.json"]:
        raise IredSplitError("split audit does not match rebuilt exact bytes")


def validate_partition_access(
    split_manifest_bytes: bytes,
    experiment_config: dict[str, object],
    requested_partitions: tuple[str, ...],
) -> None:
    """Fail closed when a consumer requests unauthorized held-out membership."""
    try:
        raw_manifest = cast(object, json.loads(split_manifest_bytes.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IredSplitError("Split Manifest must be valid UTF-8 JSON") from error
    if not isinstance(raw_manifest, dict):
        raise IredSplitError("Split Manifest must be an object")
    split_manifest = cast(dict[str, object], raw_manifest)
    partitions = split_manifest.get("partitions")
    held_out = split_manifest.get("held_out_partitions")
    if not isinstance(partitions, list) or not all(isinstance(item, str) for item in partitions):
        raise IredSplitError("Split Manifest partitions are invalid")
    if not isinstance(held_out, list) or not all(isinstance(item, str) for item in held_out):
        raise IredSplitError("Split Manifest held_out_partitions are invalid")
    unknown = set(requested_partitions) - set(cast(list[str], partitions))
    if unknown:
        raise IredSplitError("consumer requested an unknown split partition")
    if not set(requested_partitions) & set(cast(list[str], held_out)):
        return
    if experiment_config.get("held_out_authorized") is not True:
        raise IredSplitError("held-out access is not authorized by Experiment Config")
    configured_sha = experiment_config.get("split_sha256")
    if not isinstance(configured_sha, str):
        raise IredSplitError("Experiment Config split_sha256 is missing or invalid")
    configured_sha = _lower_hex_digest(
        configured_sha,
        field="Experiment Config split_sha256",
    )
    if configured_sha != hashlib.sha256(split_manifest_bytes).hexdigest():
        raise IredSplitError(
            "Experiment Config split_sha256 does not match the accessed Split Manifest"
        )
    reference = experiment_config.get("held_out_authorization_reference")
    if not isinstance(reference, str) or not reference:
        raise IredSplitError("held-out authorization reference is missing")
