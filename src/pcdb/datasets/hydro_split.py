"""Deterministic within-wild-type topology stress tracer split for FLIP2 Hydro."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import cast

from pcdb.datasets.ired_split import (
    _ALL_ROLES,
    ArtifactReference,
    Component,
    IredSplitConfig,
    IredSplitError,
    SplitBuild,
    SplitCandidate,
    SplitMembership,
    UpstreamMembership,
    _artifact_reference,
    _build_split_payloads_and_manifest,
    _canonical_config_sha256,
    _components,
    _config_mapping,
    _git_commit,
    _hamming1_edges,
    _integer,
    _lower_hex_digest,
    _parse_upstream_memberships,
    _string,
    _string_list,
    _validate_split_bundle_exact_bytes,
    _verify_processing_chain,
    assign_stratified_singletons,
    role_schedule,
)

HydroSplitError = IredSplitError

_SINGLETON_ROLES = ("candidate-pool", "validation", "held-out")
_PUBLISHER_ROLES = (
    "set=train;validation=False",
    "set=train;validation=True",
    "set=test;validation=False",
)
_PRODUCTION_V1_CANONICAL_CONFIG_SHA256 = (
    "afb0440d803b530aeba90d98656cb68d427766a33261405b28946481fbf431d9"
)
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
    "allowed_amino_acids",
    "expected_candidate_count",
    "candidate_id_namespace",
    "candidate_id_preimage",
    "mutation_count_method",
    "grouping_method",
    "cross_wild_type_distance_policy",
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
    "wild_types",
    "group_id_collision_policy",
    "ordering_key_collision_policy",
    "impossible_allocation_policy",
    "upstream_source_keys",
    "expected_upstream_cross_role_edges",
    "expected_upstream_role_counts",
    "expected_upstream_mixed_component_counts",
    "expected_upstream_assignment_disagreements",
    "publisher_metadata_role",
    "leakage_controls",
    "known_limitations",
}
_WILD_TYPE_KEYS = {
    "wild_type_id",
    "sequence_length",
    "reference_sequence",
    "reference_sequence_sha256",
    "expected_candidate_count",
    "expected_mutation_count_min",
    "expected_mutation_count_max",
    "role_counts",
    "expected_hamming1_pair_count",
    "expected_component_count",
    "expected_singleton_component_count",
    "expected_non_singleton_component_count",
    "expected_largest_component_size",
    "expected_second_largest_component_size",
    "expected_component_size_counts",
    "expected_singleton_mutation_counts",
    "expected_singleton_role_counts_by_mutation",
}


@dataclass(frozen=True, slots=True)
class WildTypeSplitConfig:
    """Frozen topology and allocation contract for one Hydro wild type."""

    wild_type_id: str
    sequence_length: int
    reference_sequence: str
    reference_sequence_sha256: str
    expected_candidate_count: int
    expected_mutation_count_min: int
    expected_mutation_count_max: int
    role_counts: dict[str, int]
    expected_hamming1_pair_count: int
    expected_component_count: int
    expected_singleton_component_count: int
    expected_non_singleton_component_count: int
    expected_largest_component_size: int
    expected_second_largest_component_size: int
    expected_component_size_counts: dict[int, int]
    expected_singleton_mutation_counts: dict[int, int]
    expected_singleton_role_counts_by_mutation: dict[int, dict[str, int]]


@dataclass(frozen=True, slots=True)
class UpstreamDisagreement:
    """Expected assignment disagreement between two publisher protocols."""

    left_source: str
    right_source: str
    count: int


@dataclass(frozen=True, slots=True)
class HydroSplitConfig:
    """Frozen rules for the multi-reference Hydro topology stress tracer."""

    dataset_id: str
    dataset_version: str
    task_id: str
    split_id: str
    protocol_version: str
    processing_manifest_sha256: str
    candidate_input: ArtifactReference
    upstream_metadata_input: ArtifactReference
    allowed_amino_acids: str
    expected_candidate_count: int
    candidate_id_namespace: str
    candidate_id_preimage: str
    mutation_count_method: str
    grouping_method: str
    cross_wild_type_distance_policy: str
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
    wild_types: tuple[WildTypeSplitConfig, ...]
    upstream_source_keys: tuple[str, ...]
    expected_upstream_cross_role_edges: dict[str, int]
    expected_upstream_role_counts: dict[str, dict[str, dict[str, int]]]
    expected_upstream_mixed_component_counts: dict[str, dict[str, int]]
    expected_upstream_assignment_disagreements: tuple[UpstreamDisagreement, ...]
    publisher_metadata_role: str
    leakage_controls: tuple[str, ...]
    known_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HydroCandidate:
    """Observable Hydro candidate fields."""

    candidate_id: str
    wild_type_id: str
    sequence: str


def _counts(
    mapping: dict[str, object],
    field: str,
    keys: tuple[str, ...],
    *,
    positive: bool = False,
) -> dict[str, int]:
    value = mapping.get(field)
    if not isinstance(value, dict) or set(value) != set(keys):
        raise HydroSplitError(f"{field} keys do not match the frozen contract")
    raw = cast(dict[str, object], value)
    return {key: _integer(raw, key, positive=positive) for key in keys}


def _canonical_integer_counts(
    mapping: dict[str, object],
    field: str,
    *,
    positive_keys: bool = False,
) -> dict[int, int]:
    value = mapping.get(field)
    if not isinstance(value, dict) or not value:
        raise HydroSplitError(f"{field} must be a non-empty object")
    result: dict[int, int] = {}
    for raw_key, raw_count in cast(dict[str, object], value).items():
        minimum = 1 if positive_keys else 0
        if not raw_key.isdecimal() or str(int(raw_key)) != raw_key or int(raw_key) < minimum:
            qualifier = "positive" if positive_keys else "non-negative"
            raise HydroSplitError(f"{field} keys must be canonical {qualifier} integers")
        if not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 1:
            raise HydroSplitError(f"{field} values must be positive integers")
        result[int(raw_key)] = raw_count
    return dict(sorted(result.items()))


def _mutation_role_counts(
    mapping: dict[str, object],
    mutation_counts: dict[int, int],
) -> dict[int, dict[str, int]]:
    value = mapping.get("expected_singleton_role_counts_by_mutation")
    if not isinstance(value, dict) or set(value) != {str(key) for key in mutation_counts}:
        raise HydroSplitError("mutation role-count strata do not match mutation counts")
    result: dict[int, dict[str, int]] = {}
    for mutation_count, expected_count in mutation_counts.items():
        raw = cast(dict[str, object], value[str(mutation_count)])
        if not isinstance(raw, dict) or set(raw) != set(_SINGLETON_ROLES):
            raise HydroSplitError("mutation stratum must define the three singleton roles")
        counts = {role: _integer(raw, role) for role in _SINGLETON_ROLES}
        if sum(counts.values()) != expected_count:
            raise HydroSplitError("mutation stratum role counts do not sum to its size")
        result[mutation_count] = counts
    return result


def _parse_wild_type(
    raw: dict[str, object],
    *,
    alphabet: str,
    role_order: tuple[str, ...],
) -> WildTypeSplitConfig:
    if set(raw) != _WILD_TYPE_KEYS:
        raise HydroSplitError("wild-type config fields do not match version 1")
    wild_type_id = _string(raw, "wild_type_id")
    reference = _string(raw, "reference_sequence")
    sequence_length = _integer(raw, "sequence_length", positive=True)
    if (
        reference != reference.upper()
        or len(reference) != sequence_length
        or not set(reference) <= set(alphabet)
    ):
        raise HydroSplitError("wild-type reference_sequence is invalid")
    reference_sha = _lower_hex_digest(
        _string(raw, "reference_sequence_sha256"),
        field="reference_sequence_sha256",
    )
    if hashlib.sha256(reference.encode("ascii")).hexdigest() != reference_sha:
        raise HydroSplitError("reference_sequence_sha256 does not match reference_sequence")

    expected_candidates = _integer(raw, "expected_candidate_count", positive=True)
    role_counts = _counts(raw, "role_counts", _ALL_ROLES)
    if sum(role_counts.values()) != expected_candidates:
        raise HydroSplitError("wild-type role counts do not cover its candidate universe")
    component_sizes = _canonical_integer_counts(
        raw,
        "expected_component_size_counts",
        positive_keys=True,
    )
    expected_components = _integer(raw, "expected_component_count", positive=True)
    expected_singletons = _integer(raw, "expected_singleton_component_count", positive=True)
    expected_non_singletons = _integer(raw, "expected_non_singleton_component_count", positive=True)
    if sum(component_sizes.values()) != expected_components:
        raise HydroSplitError("component-size counts do not sum to expected components")
    if sum(size * count for size, count in component_sizes.items()) != expected_candidates:
        raise HydroSplitError("component-size counts do not cover the wild-type universe")
    if component_sizes.get(1) != expected_singletons:
        raise HydroSplitError("component-size counts do not match singleton count")
    if sum(count for size, count in component_sizes.items() if size > 1) != expected_non_singletons:
        raise HydroSplitError("component-size counts do not match non-singleton count")
    if expected_singletons + expected_non_singletons != expected_components:
        raise HydroSplitError("wild-type component counts do not sum")
    ordered_sizes = sorted(
        (size for size, count in component_sizes.items() for _ in range(count)),
        reverse=True,
    )
    expected_largest = _integer(raw, "expected_largest_component_size", positive=True)
    expected_second = _integer(raw, "expected_second_largest_component_size", positive=True)
    if ordered_sizes[:2] != [expected_largest, expected_second]:
        raise HydroSplitError("wild-type largest component sizes differ from the multiset")
    development_count = sum(size * count for size, count in component_sizes.items() if size > 1)
    if role_counts["development"] != development_count:
        raise HydroSplitError("wild-type development count must contain all non-singletons")
    if sum(role_counts[role] for role in role_order) != expected_singletons:
        raise HydroSplitError("wild-type singleton capacities differ from singleton count")

    mutation_counts = _canonical_integer_counts(raw, "expected_singleton_mutation_counts")
    mutation_role_counts = _mutation_role_counts(raw, mutation_counts)
    if sum(mutation_counts.values()) != expected_singletons:
        raise HydroSplitError("wild-type mutation strata differ from singleton count")
    schedule = role_schedule(
        {role: role_counts[role] for role in role_order},
        role_order,
    )
    offset = 0
    for mutation_count, stratum_size in mutation_counts.items():
        observed = Counter(schedule[offset : offset + stratum_size])
        if {role: observed[role] for role in role_order} != mutation_role_counts[mutation_count]:
            raise HydroSplitError("wild-type mutation quotas differ from its deficit schedule")
        offset += stratum_size

    mutation_min = _integer(raw, "expected_mutation_count_min")
    mutation_max = _integer(raw, "expected_mutation_count_max")
    if mutation_min > mutation_max or mutation_max > sequence_length:
        raise HydroSplitError("wild-type mutation-count range is invalid")
    return WildTypeSplitConfig(
        wild_type_id=wild_type_id,
        sequence_length=sequence_length,
        reference_sequence=reference,
        reference_sequence_sha256=reference_sha,
        expected_candidate_count=expected_candidates,
        expected_mutation_count_min=mutation_min,
        expected_mutation_count_max=mutation_max,
        role_counts=role_counts,
        expected_hamming1_pair_count=_integer(raw, "expected_hamming1_pair_count"),
        expected_component_count=expected_components,
        expected_singleton_component_count=expected_singletons,
        expected_non_singleton_component_count=expected_non_singletons,
        expected_largest_component_size=expected_largest,
        expected_second_largest_component_size=expected_second,
        expected_component_size_counts=component_sizes,
        expected_singleton_mutation_counts=mutation_counts,
        expected_singleton_role_counts_by_mutation=mutation_role_counts,
    )


def _parse_upstream_role_counts(
    config: dict[str, object],
    source_keys: tuple[str, ...],
    wild_type_ids: tuple[str, ...],
) -> dict[str, dict[str, dict[str, int]]]:
    value = config.get("expected_upstream_role_counts")
    if not isinstance(value, dict) or set(value) != set(source_keys):
        raise HydroSplitError("expected_upstream_role_counts sources differ from the contract")
    result: dict[str, dict[str, dict[str, int]]] = {}
    expected_scopes = ("global", *wild_type_ids)
    for source_key in source_keys:
        raw_scopes = cast(dict[str, object], value[source_key])
        if not isinstance(raw_scopes, dict) or set(raw_scopes) != set(expected_scopes):
            raise HydroSplitError("publisher role-count scopes differ from the contract")
        scopes: dict[str, dict[str, int]] = {}
        for scope in expected_scopes:
            raw_counts = raw_scopes[scope]
            if not isinstance(raw_counts, dict) or not raw_counts:
                raise HydroSplitError("publisher role counts must be non-empty objects")
            if not set(raw_counts) <= set(_PUBLISHER_ROLES):
                raise HydroSplitError("publisher role counts contain an unknown role")
            scopes[scope] = {
                role: _integer(cast(dict[str, object], raw_counts), role)
                for role in sorted(raw_counts)
            }
        result[source_key] = scopes
    return result


def _parse_mixed_counts(
    config: dict[str, object],
    source_keys: tuple[str, ...],
) -> dict[str, dict[str, int]]:
    value = config.get("expected_upstream_mixed_component_counts")
    if not isinstance(value, dict) or set(value) != set(source_keys):
        raise HydroSplitError("expected upstream mixed-component sources differ")
    result: dict[str, dict[str, int]] = {}
    for source_key in source_keys:
        raw_value = value[source_key]
        if not isinstance(raw_value, dict):
            raise HydroSplitError("publisher mixed-component counts must be objects")
        raw = cast(dict[str, object], raw_value)
        result[source_key] = _counts(
            {"counts": raw},
            "counts",
            ("component_count", "candidate_count"),
        )
    return result


def _parse_disagreements(
    config: dict[str, object],
    source_keys: tuple[str, ...],
) -> tuple[UpstreamDisagreement, ...]:
    value = config.get("expected_upstream_assignment_disagreements")
    if not isinstance(value, list):
        raise HydroSplitError("publisher disagreements must be an array")
    result: list[UpstreamDisagreement] = []
    seen: set[frozenset[str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"left_source", "right_source", "count"}:
            raise HydroSplitError("publisher disagreement fields do not match version 1")
        raw = cast(dict[str, object], item)
        left = _string(raw, "left_source")
        right = _string(raw, "right_source")
        pair = frozenset((left, right))
        if left == right or not pair <= set(source_keys) or pair in seen:
            raise HydroSplitError("publisher disagreement pair is invalid or duplicated")
        seen.add(pair)
        result.append(
            UpstreamDisagreement(
                left_source=left,
                right_source=right,
                count=_integer(raw, "count"),
            )
        )
    expected_pairs = {frozenset(pair) for pair in combinations(source_keys, 2)}
    if seen != expected_pairs:
        raise HydroSplitError("publisher disagreements do not cover every source pair")
    return tuple(result)


def _require_production_v1_identity(config: dict[str, object]) -> None:
    if _canonical_config_sha256(config) != _PRODUCTION_V1_CANONICAL_CONFIG_SHA256:
        raise HydroSplitError(
            "production v1 config identity does not match the frozen canonical config"
        )


def _parse_hydro_split_config(config: dict[str, object]) -> HydroSplitConfig:
    if set(config) != _CONFIG_KEYS or config.get("config_version") != "1":
        raise HydroSplitError("split config fields do not match version 1")
    exact_values = {
        "task_id": "hydro",
        "candidate_id_preimage": "namespace_null_wild_type_id_null_sequence",
        "mutation_count_method": ("within_wild_type_full_length_hamming_distance_to_reference"),
        "grouping_method": ("within_wild_type_full_length_hamming1_connected_component"),
        "cross_wild_type_distance_policy": "incomparable_without_accepted_alignment",
        "hamming_distance": 1,
        "group_id_algorithm": "sha256",
        "group_preimage_format": (
            "namespace_nul_group-id_nul_uint32be_count_concatenated_raw_candidate_ids"
        ),
        "ordering_key_algorithm": "sha256",
        "ordering_preimage_format": (
            "namespace_nul_order-key_nul_uint64be_seed_uint16be_mutation_count_raw_group_id"
        ),
        "assignment_method": ("per_wild_type_mutation_count_stratified_global_deficit_schedule"),
        "group_id_collision_policy": "error",
        "ordering_key_collision_policy": "error",
        "impossible_allocation_policy": "error",
        "publisher_metadata_role": "audit_only_not_benchmark_membership",
    }
    for field, expected in exact_values.items():
        if config.get(field) != expected:
            raise HydroSplitError(f"{field} does not match the frozen contract")
    role_order = _string_list(config, "assignment_role_order")
    if role_order != _SINGLETON_ROLES:
        raise HydroSplitError("assignment_role_order does not match the frozen contract")
    if _string_list(config, "ordering_tie_break") != (
        "mutation_count",
        "ordering_key",
        "group_id",
    ):
        raise HydroSplitError("ordering_tie_break does not match the frozen contract")
    alphabet = _string(config, "allowed_amino_acids")
    if alphabet != "".join(sorted(set(alphabet))):
        raise HydroSplitError("allowed_amino_acids must be sorted and unique")
    namespace = _string(config, "protocol_namespace")
    candidate_namespace = _string(config, "candidate_id_namespace")
    try:
        namespace.encode("ascii")
        candidate_namespace.encode("ascii")
    except UnicodeEncodeError as error:
        raise HydroSplitError("identity namespaces must contain exact ASCII bytes") from error

    wild_type_value = config.get("wild_types")
    if not isinstance(wild_type_value, list) or len(wild_type_value) < 2:
        raise HydroSplitError("wild_types must contain at least two contracts")
    wild_types = tuple(
        sorted(
            (
                _parse_wild_type(
                    cast(dict[str, object], item),
                    alphabet=alphabet,
                    role_order=role_order,
                )
                for item in wild_type_value
                if isinstance(item, dict)
            ),
            key=lambda item: item.wild_type_id,
        )
    )
    if len(wild_types) != len(wild_type_value):
        raise HydroSplitError("wild_types entries must be objects")
    wild_type_ids = tuple(item.wild_type_id for item in wild_types)
    if len(set(wild_type_ids)) != len(wild_type_ids):
        raise HydroSplitError("wild_type_id values must be unique")

    expected_candidates = _integer(config, "expected_candidate_count", positive=True)
    candidate_input = _artifact_reference(config, "candidate_input")
    upstream_input = _artifact_reference(config, "upstream_metadata_input")
    source_keys = tuple(sorted(_string_list(config, "upstream_source_keys")))
    if len(source_keys) < 2:
        raise HydroSplitError("Hydro requires at least two publisher sources")
    if candidate_input.row_count != expected_candidates:
        raise HydroSplitError("candidate_input row count differs from candidate universe")
    if upstream_input.row_count != expected_candidates * len(source_keys):
        raise HydroSplitError("upstream metadata does not cover every candidate and source")
    if sum(item.expected_candidate_count for item in wild_types) != expected_candidates:
        raise HydroSplitError("wild-type counts do not cover the candidate universe")

    role_counts = _counts(config, "role_counts", _ALL_ROLES, positive=True)
    if sum(role_counts.values()) != expected_candidates:
        raise HydroSplitError("global role counts do not cover the candidate universe")
    for role in _ALL_ROLES:
        if sum(item.role_counts[role] for item in wild_types) != role_counts[role]:
            raise HydroSplitError("wild-type role counts do not sum to global role counts")

    total_fields = (
        ("expected_hamming1_pair_count", "expected_hamming1_pair_count"),
        ("expected_component_count", "expected_component_count"),
        ("expected_singleton_component_count", "expected_singleton_component_count"),
        ("expected_non_singleton_component_count", "expected_non_singleton_component_count"),
    )
    for config_field, wild_type_field in total_fields:
        if _integer(config, config_field) != sum(
            cast(int, getattr(item, wild_type_field)) for item in wild_types
        ):
            raise HydroSplitError(f"{config_field} differs from wild-type totals")
    all_sizes = sorted(
        (
            size
            for item in wild_types
            for size, count in item.expected_component_size_counts.items()
            for _ in range(count)
        ),
        reverse=True,
    )
    if all_sizes[:2] != [
        _integer(config, "expected_largest_component_size", positive=True),
        _integer(config, "expected_second_largest_component_size", positive=True),
    ]:
        raise HydroSplitError("global largest component sizes differ from wild-type contracts")

    cross_edges = _counts(
        config,
        "expected_upstream_cross_role_edges",
        source_keys,
    )
    return HydroSplitConfig(
        dataset_id=_string(config, "dataset_id"),
        dataset_version=_string(config, "dataset_version"),
        task_id="hydro",
        split_id=_string(config, "split_id"),
        protocol_version=_string(config, "protocol_version"),
        processing_manifest_sha256=_lower_hex_digest(
            _string(config, "processing_manifest_sha256"),
            field="processing_manifest_sha256",
        ),
        candidate_input=candidate_input,
        upstream_metadata_input=upstream_input,
        allowed_amino_acids=alphabet,
        expected_candidate_count=expected_candidates,
        candidate_id_namespace=candidate_namespace,
        candidate_id_preimage=cast(str, exact_values["candidate_id_preimage"]),
        mutation_count_method=cast(str, exact_values["mutation_count_method"]),
        grouping_method=cast(str, exact_values["grouping_method"]),
        cross_wild_type_distance_policy=cast(str, exact_values["cross_wild_type_distance_policy"]),
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
        role_counts=role_counts,
        expected_hamming1_pair_count=_integer(config, "expected_hamming1_pair_count"),
        expected_component_count=_integer(config, "expected_component_count"),
        expected_singleton_component_count=_integer(config, "expected_singleton_component_count"),
        expected_non_singleton_component_count=_integer(
            config, "expected_non_singleton_component_count"
        ),
        expected_largest_component_size=_integer(config, "expected_largest_component_size"),
        expected_second_largest_component_size=_integer(
            config, "expected_second_largest_component_size"
        ),
        wild_types=wild_types,
        upstream_source_keys=source_keys,
        expected_upstream_cross_role_edges=cross_edges,
        expected_upstream_role_counts=_parse_upstream_role_counts(
            config, source_keys, wild_type_ids
        ),
        expected_upstream_mixed_component_counts=_parse_mixed_counts(config, source_keys),
        expected_upstream_assignment_disagreements=_parse_disagreements(config, source_keys),
        publisher_metadata_role="audit_only_not_benchmark_membership",
        leakage_controls=_string_list(config, "leakage_controls"),
        known_limitations=_string_list(config, "known_limitations"),
    )


def _load_hydro_split_config_bytes(
    payload: bytes,
    *,
    require_production_identity: bool,
) -> HydroSplitConfig:
    mapping = _config_mapping(payload)
    if require_production_identity:
        _require_production_v1_identity(mapping)
    return _parse_hydro_split_config(mapping)


def load_hydro_split_config(path: Path) -> HydroSplitConfig:
    """Load the exact production-v1 Issue #27 split contract."""
    return _load_hydro_split_config_bytes(
        path.read_bytes(),
        require_production_identity=True,
    )


def load_hydro_split_fixture_config(path: Path) -> HydroSplitConfig:
    """Load a structurally valid synthetic fixture through a test-only path."""
    payload = path.read_bytes()
    mapping = _config_mapping(payload)
    if not (
        _string(mapping, "dataset_version").startswith("fixture-")
        and _string(mapping, "split_id").startswith("fixture-")
    ):
        raise HydroSplitError("fixture config must use explicit fixture identities")
    return _load_hydro_split_config_bytes(
        payload,
        require_production_identity=False,
    )


def _parse_candidates(
    config: HydroSplitConfig,
    candidate_bytes: bytes,
) -> tuple[HydroCandidate, ...]:
    try:
        reader = csv.DictReader(io.StringIO(candidate_bytes.decode("utf-8")))
    except UnicodeDecodeError as error:
        raise HydroSplitError("candidate artifact must be UTF-8 CSV") from error
    if tuple(reader.fieldnames or ()) != ("candidate_id", "wild_type_id", "sequence"):
        raise HydroSplitError(
            "candidate artifact fields must be candidate_id,wild_type_id,sequence"
        )
    contracts = {item.wild_type_id: item for item in config.wild_types}
    allowed = set(config.allowed_amino_acids)
    seen_ids: set[str] = set()
    seen_sequences: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    candidates: list[HydroCandidate] = []
    for row_number, row in enumerate(reader, start=2):
        values = [row.get(field) for field in ("candidate_id", "wild_type_id", "sequence")]
        if any(not isinstance(value, str) or not value for value in values):
            raise HydroSplitError(f"candidate artifact row {row_number} is incomplete")
        candidate_id, wild_type_id, sequence = cast(list[str], values)
        _lower_hex_digest(candidate_id, field="candidate_id")
        contract = contracts.get(wild_type_id)
        if contract is None:
            raise HydroSplitError("candidate artifact contains an unknown wild_type_id")
        if (
            sequence != sequence.strip().upper()
            or len(sequence) != contract.sequence_length
            or not set(sequence) <= allowed
        ):
            raise HydroSplitError("candidate artifact contains an invalid sequence")
        expected_id = hashlib.sha256(
            (f"{config.candidate_id_namespace}\0{wild_type_id}\0{sequence}").encode("ascii")
        ).hexdigest()
        if candidate_id != expected_id:
            raise HydroSplitError("candidate_id does not match the frozen identity preimage")
        if candidate_id in seen_ids:
            raise HydroSplitError("candidate artifact contains duplicate candidate_id")
        sequence_key = (wild_type_id, sequence)
        if sequence_key in seen_sequences:
            raise HydroSplitError(
                "candidate artifact contains a duplicate within-wild-type sequence"
            )
        seen_ids.add(candidate_id)
        seen_sequences.add(sequence_key)
        counts[wild_type_id] += 1
        candidates.append(HydroCandidate(candidate_id, wild_type_id, sequence))
    expected_counts = {
        item.wild_type_id: item.expected_candidate_count for item in config.wild_types
    }
    if dict(counts) != expected_counts or len(candidates) != config.expected_candidate_count:
        raise HydroSplitError("wild-type candidate counts differ from the frozen contract")
    return tuple(sorted(candidates, key=lambda item: item.candidate_id))


def _verify_hydro_processing_chain(
    config: HydroSplitConfig,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
) -> None:
    _verify_processing_chain(
        cast(IredSplitConfig, config),
        processing_manifest_bytes,
        candidate_bytes,
        upstream_bytes,
    )
    manifest = cast(dict[str, object], json.loads(processing_manifest_bytes))
    if manifest.get("observable_feature_fields") != ["wild_type_id", "sequence"]:
        raise HydroSplitError(
            "Processing Manifest observable fields differ from the Hydro contract"
        )


def _publisher_audit(
    config: HydroSplitConfig,
    candidates: tuple[HydroCandidate, ...],
    upstream: tuple[UpstreamMembership, ...],
    components: tuple[Component, ...],
    edges: tuple[tuple[str, str], ...],
) -> tuple[
    dict[str, int],
    dict[str, dict[str, dict[str, int]]],
    dict[str, dict[str, int]],
    tuple[UpstreamDisagreement, ...],
]:
    roles = {
        (item.source_key, item.candidate_id): (
            f"set={item.set_value};validation={item.validation_value}"
        )
        for item in upstream
    }
    candidate_wild_types = {
        candidate.candidate_id: candidate.wild_type_id for candidate in candidates
    }
    crossings = {
        source: sum(roles[(source, left)] != roles[(source, right)] for left, right in edges)
        for source in config.upstream_source_keys
    }
    role_counts: dict[str, dict[str, dict[str, int]]] = {}
    mixed_counts: dict[str, dict[str, int]] = {}
    for source in config.upstream_source_keys:
        scopes: dict[str, dict[str, int]] = {}
        for scope in ("global", *(item.wild_type_id for item in config.wild_types)):
            observed = Counter(
                role
                for (observed_source, candidate_id), role in roles.items()
                if observed_source == source
                and (scope == "global" or candidate_wild_types[candidate_id] == scope)
            )
            scopes[scope] = dict(sorted(observed.items()))
        role_counts[source] = scopes
        mixed = [
            component
            for component in components
            if len({roles[(source, candidate_id)] for candidate_id in component.members}) > 1
        ]
        mixed_counts[source] = {
            "component_count": len(mixed),
            "candidate_count": sum(len(component.members) for component in mixed),
        }
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    disagreements = tuple(
        UpstreamDisagreement(
            left_source=item.left_source,
            right_source=item.right_source,
            count=sum(
                roles[(item.left_source, candidate_id)] != roles[(item.right_source, candidate_id)]
                for candidate_id in candidate_ids
            ),
        )
        for item in config.expected_upstream_assignment_disagreements
    )
    return crossings, role_counts, mixed_counts, disagreements


def build_hydro_split_membership(
    *,
    config: HydroSplitConfig,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
) -> SplitBuild:
    """Build the Hydro tracer membership and audits without reading hidden truth."""
    candidates = _parse_candidates(config, candidate_bytes)
    candidate_ids = frozenset(candidate.candidate_id for candidate in candidates)
    upstream = _parse_upstream_memberships(
        cast(IredSplitConfig, config),
        upstream_bytes,
        candidate_ids,
    )
    by_wild_type: dict[str, list[HydroCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_wild_type[candidate.wild_type_id].append(candidate)

    all_components: list[Component] = []
    all_edges: list[tuple[str, str]] = []
    assignments: dict[str, str] = {}
    per_wild_type_audit: list[dict[str, object]] = []
    for contract in config.wild_types:
        split_candidates = tuple(
            SplitCandidate(item.candidate_id, item.sequence)
            for item in by_wild_type[contract.wild_type_id]
        )
        mutation_counts, edges = _hamming1_edges(
            split_candidates,
            contract.reference_sequence,
        )
        components = _components(
            cast(IredSplitConfig, config),
            split_candidates,
            mutation_counts,
            edges,
        )
        sizes = sorted((len(component.members) for component in components), reverse=True)
        component_size_counts = dict(sorted(Counter(sizes).items()))
        singleton_count = component_size_counts.get(1, 0)
        non_singleton_count = len(components) - singleton_count
        observed_range = (min(mutation_counts.values()), max(mutation_counts.values()))
        observed_topology = (
            len(edges),
            len(components),
            singleton_count,
            non_singleton_count,
            sizes[0],
            sizes[1],
            component_size_counts,
            observed_range,
        )
        expected_topology = (
            contract.expected_hamming1_pair_count,
            contract.expected_component_count,
            contract.expected_singleton_component_count,
            contract.expected_non_singleton_component_count,
            contract.expected_largest_component_size,
            contract.expected_second_largest_component_size,
            contract.expected_component_size_counts,
            (
                contract.expected_mutation_count_min,
                contract.expected_mutation_count_max,
            ),
        )
        if observed_topology != expected_topology:
            raise HydroSplitError(
                f"{contract.wild_type_id} topology differs from the frozen contract"
            )
        singletons = tuple(component for component in components if len(component.members) == 1)
        non_singletons = tuple(component for component in components if len(component.members) > 1)
        observed_strata = dict(
            sorted(Counter(cast(int, component.mutation_count) for component in singletons).items())
        )
        if observed_strata != contract.expected_singleton_mutation_counts:
            raise HydroSplitError(f"{contract.wild_type_id} singleton mutation strata differ")
        wt_assignments = assign_stratified_singletons(
            singletons,
            {role: contract.role_counts[role] for role in config.assignment_role_order},
            config.assignment_role_order,
        )
        wt_assignments.update({component.group_id: "development" for component in non_singletons})
        observed_stratum_roles: dict[int, dict[str, int]] = {}
        for mutation_count in observed_strata:
            counts = Counter(
                wt_assignments[component.group_id]
                for component in singletons
                if component.mutation_count == mutation_count
            )
            observed_stratum_roles[mutation_count] = {
                role: counts[role] for role in config.assignment_role_order
            }
        if observed_stratum_roles != contract.expected_singleton_role_counts_by_mutation:
            raise HydroSplitError(f"{contract.wild_type_id} assigned mutation quotas differ")
        if set(assignments) & set(wt_assignments):
            raise HydroSplitError("group_id collision occurred across wild types")
        assignments.update(wt_assignments)
        all_components.extend(components)
        all_edges.extend(edges)
        per_wild_type_audit.append(
            {
                "wild_type_id": contract.wild_type_id,
                "candidate_count": len(split_candidates),
                "sequence_length": contract.sequence_length,
                "reference_sequence_sha256": contract.reference_sequence_sha256,
                "mutation_count_min": observed_range[0],
                "mutation_count_max": observed_range[1],
                "hamming1_pair_count": len(edges),
                "component_count": len(components),
                "component_size_counts": {
                    str(size): count for size, count in component_size_counts.items()
                },
                "singleton_component_count": singleton_count,
                "non_singleton_component_count": non_singleton_count,
                "largest_component_size": sizes[0],
                "second_largest_component_size": sizes[1],
                "singleton_mutation_strata": [
                    {
                        "mutation_count": mutation_count,
                        "singleton_count": observed_strata[mutation_count],
                        "role_counts": observed_stratum_roles[mutation_count],
                    }
                    for mutation_count in observed_strata
                ],
                "role_counts": contract.role_counts,
            }
        )

    components_tuple = tuple(sorted(all_components, key=lambda item: item.group_id))
    edges_tuple = tuple(sorted(all_edges))
    global_sizes = sorted(
        (len(component.members) for component in components_tuple),
        reverse=True,
    )
    observed_global = (
        len(edges_tuple),
        len(components_tuple),
        sum(size == 1 for size in global_sizes),
        sum(size > 1 for size in global_sizes),
        global_sizes[0],
        global_sizes[1],
    )
    expected_global = (
        config.expected_hamming1_pair_count,
        config.expected_component_count,
        config.expected_singleton_component_count,
        config.expected_non_singleton_component_count,
        config.expected_largest_component_size,
        config.expected_second_largest_component_size,
    )
    if observed_global != expected_global:
        raise HydroSplitError("global topology differs from the frozen contract")

    membership = tuple(
        sorted(
            (
                SplitMembership(
                    candidate_id,
                    assignments[component.group_id],
                    component.group_id,
                )
                for component in components_tuple
                for candidate_id in component.members
            ),
            key=lambda item: bytes.fromhex(item.candidate_id),
        )
    )
    role_by_candidate = {item.candidate_id: item.role for item in membership}
    cross_role_edges = sum(
        role_by_candidate[left] != role_by_candidate[right] for left, right in edges_tuple
    )
    if cross_role_edges:
        raise HydroSplitError("a within-wild-type Hamming-1 edge crosses benchmark roles")

    partition_summary: list[dict[str, object]] = []
    for role in _ALL_ROLES:
        role_components = [
            component for component in components_tuple if assignments[component.group_id] == role
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
        raise HydroSplitError("assigned candidate counts differ from frozen role counts")

    (
        publisher_crossings,
        publisher_role_counts,
        publisher_mixed_counts,
        publisher_disagreements,
    ) = _publisher_audit(
        config,
        candidates,
        upstream,
        components_tuple,
        edges_tuple,
    )
    if publisher_crossings != config.expected_upstream_cross_role_edges:
        raise HydroSplitError("publisher edge crossings differ from the frozen audit")
    if publisher_role_counts != config.expected_upstream_role_counts:
        raise HydroSplitError("publisher role counts differ from the frozen audit")
    if publisher_mixed_counts != config.expected_upstream_mixed_component_counts:
        raise HydroSplitError("publisher mixed-component counts differ from the frozen audit")
    if publisher_disagreements != config.expected_upstream_assignment_disagreements:
        raise HydroSplitError("publisher assignment disagreements differ from the frozen audit")

    audit: dict[str, object] = {
        "schema_version": "1",
        "split_id": config.split_id,
        "processing_manifest_sha256": config.processing_manifest_sha256,
        "candidate_count": len(candidates),
        "wild_type_count": len(config.wild_types),
        "mutation_count_method": config.mutation_count_method,
        "grouping_method": config.grouping_method,
        "cross_wild_type_distance_policy": config.cross_wild_type_distance_policy,
        "cross_wild_type_hamming_computed": False,
        "hamming_distance": config.hamming_distance,
        "hamming1_pair_count": len(edges_tuple),
        "component_count": len(components_tuple),
        "singleton_component_count": observed_global[2],
        "non_singleton_component_count": observed_global[3],
        "largest_component_size": global_sizes[0],
        "second_largest_component_size": global_sizes[1],
        "cross_role_hamming1_edge_count": cross_role_edges,
        "exclusion_count": 0,
        "assignment_method": config.assignment_method,
        "assignment_role_order": list(config.assignment_role_order),
        "partition_summary": partition_summary,
        "per_wild_type": per_wild_type_audit,
        "upstream_cross_role_hamming1_edges": publisher_crossings,
        "upstream_role_counts": publisher_role_counts,
        "upstream_mixed_component_counts": publisher_mixed_counts,
        "upstream_assignment_disagreements": [
            {
                "left_source": item.left_source,
                "right_source": item.right_source,
                "count": item.count,
            }
            for item in publisher_disagreements
        ],
        "publisher_metadata_role": config.publisher_metadata_role,
        "duplicate_candidate_id_count": 0,
        "duplicate_within_wild_type_sequence_count": 0,
        "distribution_shift": {
            "all_non_singleton_components_in_development": True,
            "non_development_groups_are_singletons": True,
        },
        "hidden_truth_read": False,
        "known_limitations": list(config.known_limitations),
    }
    return SplitBuild(membership=membership, audit=audit)


def build_hydro_split_bundle(
    *,
    config: HydroSplitConfig,
    config_bytes: bytes,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic Hydro tracer artifacts without publishing them."""
    _git_commit(git_commit)
    is_fixture = config.dataset_version.startswith("fixture-") and config.split_id.startswith(
        "fixture-"
    )
    if (
        _load_hydro_split_config_bytes(
            config_bytes,
            require_production_identity=not is_fixture,
        )
        != config
    ):
        raise HydroSplitError("split config object does not match split config bytes")
    _verify_hydro_processing_chain(
        config,
        processing_manifest_bytes,
        candidate_bytes,
        upstream_bytes,
    )
    build = build_hydro_split_membership(
        config=config,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
    )
    return _build_split_payloads_and_manifest(
        config=cast(IredSplitConfig, config),
        config_bytes=config_bytes,
        git_commit=git_commit,
        build=build,
    )


def validate_hydro_split_bundle(
    *,
    config: HydroSplitConfig,
    config_bytes: bytes,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
    membership_bytes: bytes,
    audit_bytes: bytes,
    manifest: dict[str, object],
) -> None:
    """Rebuild and reject any Hydro tracer semantic or exact-byte mismatch."""
    git_commit = manifest.get("git_commit")
    if not isinstance(git_commit, str):
        raise HydroSplitError("Split Manifest git_commit must be a string")
    expected_payloads, expected_manifest = build_hydro_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_manifest_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit=git_commit,
    )
    _validate_split_bundle_exact_bytes(
        expected_payloads=expected_payloads,
        expected_manifest=expected_manifest,
        membership_bytes=membership_bytes,
        audit_bytes=audit_bytes,
        manifest=manifest,
    )
