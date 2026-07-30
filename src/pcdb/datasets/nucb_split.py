"""Deterministic topology-extrapolation split for FLIP2 NucB."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pcdb.datasets.ired_split import (
    _ALL_ROLES,
    Component,
    IredSplitConfig,
    IredSplitError,
    SplitBuild,
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
    _integer_map,
    _lower_hex_digest,
    _mutation_counts,
    _mutation_role_counts,
    _parse_candidates,
    _parse_upstream_memberships,
    _role_counts,
    _string,
    _string_list,
    _upstream_audit,
    _validate_split_bundle_exact_bytes,
    _verify_processing_chain,
    assign_stratified_singletons,
    role_schedule,
)
from pcdb.datasets.ired_split import (
    _CONFIG_KEYS as _IRED_CONFIG_KEYS,
)

NucbSplitError = IredSplitError


@dataclass(frozen=True, slots=True)
class NucbSplitConfig(IredSplitConfig):
    """Frozen rules for the NucB topology-extrapolation split."""

    expected_component_size_counts: dict[int, int]


_SINGLETON_ROLES = ("candidate-pool", "validation", "held-out")
_CONFIG_KEYS = _IRED_CONFIG_KEYS | {"expected_component_size_counts"}
_PRODUCTION_V1_CANONICAL_CONFIG_SHA256 = (
    "068a40609bcc73e1e5f92b645f6f822298ce80e30c93eb40ef9310a8c42dcbd2"
)


def _component_size_counts(mapping: dict[str, object]) -> dict[int, int]:
    value = mapping.get("expected_component_size_counts")
    if not isinstance(value, dict) or not value:
        raise NucbSplitError("expected_component_size_counts must be a non-empty object")
    result: dict[int, int] = {}
    for raw_key, raw_count in cast(dict[str, object], value).items():
        if not raw_key.isdecimal() or str(int(raw_key)) != raw_key or int(raw_key) < 1:
            raise NucbSplitError("component-size keys must be canonical positive integers")
        if not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 1:
            raise NucbSplitError("component-size counts must be positive integers")
        result[int(raw_key)] = raw_count
    return dict(sorted(result.items()))


def _require_production_v1_identity(config: dict[str, object]) -> None:
    if _canonical_config_sha256(config) != _PRODUCTION_V1_CANONICAL_CONFIG_SHA256:
        raise NucbSplitError(
            "production v1 config identity does not match the frozen canonical config"
        )


def _parse_nucb_split_config(config: dict[str, object]) -> NucbSplitConfig:
    if set(config) != _CONFIG_KEYS or config.get("config_version") != "1":
        raise NucbSplitError("split config fields do not match version 1")
    exact_values = {
        "task_id": "nucb",
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
            raise NucbSplitError(f"{field} does not match the frozen contract")
    if _string_list(config, "ordering_tie_break") != (
        "mutation_count",
        "ordering_key",
        "group_id",
    ):
        raise NucbSplitError("ordering_tie_break does not match the frozen contract")
    role_order = _string_list(config, "assignment_role_order")
    if role_order != _SINGLETON_ROLES:
        raise NucbSplitError("assignment_role_order does not match the frozen contract")

    alphabet = _string(config, "allowed_amino_acids")
    if alphabet != "".join(sorted(set(alphabet))):
        raise NucbSplitError("allowed_amino_acids must be sorted and unique")
    reference = _string(config, "reference_sequence")
    sequence_length = _integer(config, "sequence_length", positive=True)
    if (
        reference != reference.upper()
        or len(reference) != sequence_length
        or not set(reference) <= set(alphabet)
    ):
        raise NucbSplitError("reference_sequence is invalid")
    reference_sha = _lower_hex_digest(
        _string(config, "reference_sequence_sha256"),
        field="reference_sequence_sha256",
    )
    if hashlib.sha256(reference.encode("ascii")).hexdigest() != reference_sha:
        raise NucbSplitError("reference_sequence_sha256 does not match reference_sequence")

    namespace = _string(config, "protocol_namespace")
    try:
        namespace.encode("ascii")
    except UnicodeEncodeError as error:
        raise NucbSplitError("protocol_namespace must contain exact ASCII bytes") from error
    candidate_input = _artifact_reference(config, "candidate_input")
    upstream_input = _artifact_reference(config, "upstream_metadata_input")
    upstream_sources = _string_list(config, "upstream_source_keys")
    if len(upstream_sources) != 2:
        raise NucbSplitError("NucB v1 requires exactly two publisher split sources")

    counts = _role_counts(config)
    expected_candidates = _integer(config, "expected_candidate_count", positive=True)
    if sum(counts.values()) != expected_candidates:
        raise NucbSplitError("role_counts must sum to expected_candidate_count")
    if candidate_input.row_count != expected_candidates:
        raise NucbSplitError("candidate_input row_count must equal expected_candidate_count")
    if upstream_input.row_count != expected_candidates * len(upstream_sources):
        raise NucbSplitError("upstream metadata must cover every candidate and source")

    expected_components = _integer(config, "expected_component_count", positive=True)
    expected_singletons = _integer(config, "expected_singleton_component_count", positive=True)
    expected_non_singletons = _integer(
        config, "expected_non_singleton_component_count", positive=True
    )
    expected_largest = _integer(config, "expected_largest_component_size", positive=True)
    expected_second = _integer(config, "expected_second_largest_component_size", positive=True)
    component_sizes = _component_size_counts(config)
    if sum(component_sizes.values()) != expected_components:
        raise NucbSplitError("component-size counts do not sum to expected components")
    if sum(size * count for size, count in component_sizes.items()) != expected_candidates:
        raise NucbSplitError("component-size counts do not cover the candidate universe")
    if component_sizes.get(1) != expected_singletons:
        raise NucbSplitError("component-size counts do not match the singleton count")
    if sum(count for size, count in component_sizes.items() if size > 1) != expected_non_singletons:
        raise NucbSplitError("component-size counts do not match the non-singleton count")
    ordered_sizes = sorted(
        (size for size, count in component_sizes.items() for _ in range(count)),
        reverse=True,
    )
    if ordered_sizes[:2] != [expected_largest, expected_second]:
        raise NucbSplitError("largest component sizes do not match the frozen multiset")
    development_count = sum(size * count for size, count in component_sizes.items() if size > 1)
    if counts["development"] != development_count:
        raise NucbSplitError("development count must contain every non-singleton candidate")
    if sum(counts[role] for role in role_order) != expected_singletons:
        raise NucbSplitError("singleton role capacities must equal singleton count")

    mutation_counts = _mutation_counts(config)
    mutation_role_counts = _mutation_role_counts(config, mutation_counts)
    if sum(mutation_counts.values()) != expected_singletons:
        raise NucbSplitError("mutation strata must sum to expected singleton count")
    schedule = role_schedule({role: counts[role] for role in role_order}, role_order)
    offset = 0
    for mutation_count, stratum_size in mutation_counts.items():
        observed = Counter(schedule[offset : offset + stratum_size])
        if {role: observed[role] for role in role_order} != mutation_role_counts[mutation_count]:
            raise NucbSplitError("frozen mutation quotas do not match the global schedule")
        offset += stratum_size

    mutation_min = _integer(config, "expected_mutation_count_min")
    mutation_max = _integer(config, "expected_mutation_count_max")
    if mutation_min > mutation_max or mutation_max > sequence_length:
        raise NucbSplitError("expected mutation-count range is invalid")

    return NucbSplitConfig(
        dataset_id=_string(config, "dataset_id"),
        dataset_version=_string(config, "dataset_version"),
        task_id="nucb",
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
        expected_component_size_counts=component_sizes,
    )


def _load_nucb_split_config_bytes(
    payload: bytes,
    *,
    require_production_identity: bool,
) -> NucbSplitConfig:
    mapping = _config_mapping(payload)
    if require_production_identity:
        _require_production_v1_identity(mapping)
    return _parse_nucb_split_config(mapping)


def load_nucb_split_config(path: Path) -> NucbSplitConfig:
    """Load the exact production-v1 Issue #25 split contract."""
    return _load_nucb_split_config_bytes(
        path.read_bytes(),
        require_production_identity=True,
    )


def load_nucb_split_fixture_config(path: Path) -> NucbSplitConfig:
    """Load a structurally valid synthetic fixture through an explicit test-only path."""
    payload = path.read_bytes()
    mapping = _config_mapping(payload)
    if not (
        _string(mapping, "dataset_version").startswith("fixture-")
        and _string(mapping, "split_id").startswith("fixture-")
    ):
        raise NucbSplitError("fixture config must use explicit fixture identities")
    return _load_nucb_split_config_bytes(
        payload,
        require_production_identity=False,
    )


def _publisher_audit_details(
    config: NucbSplitConfig,
    upstream: tuple[UpstreamMembership, ...],
    components: tuple[Component, ...],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    roles = {
        (item.source_key, item.candidate_id): (
            f"set={item.set_value};validation={item.validation_value}"
        )
        for item in upstream
    }
    role_counts: dict[str, dict[str, int]] = {}
    mixed_counts: dict[str, dict[str, int]] = {}
    for source_key in config.upstream_source_keys:
        source_role_counts = Counter(
            role for (observed_source, _), role in roles.items() if observed_source == source_key
        )
        mixed_components = [
            component
            for component in components
            if len({roles[(source_key, candidate_id)] for candidate_id in component.members}) > 1
        ]
        role_counts[source_key] = dict(sorted(source_role_counts.items()))
        mixed_counts[source_key] = {
            "component_count": len(mixed_components),
            "candidate_count": sum(len(component.members) for component in mixed_components),
        }
    return role_counts, mixed_counts


def build_nucb_split_membership(
    *,
    config: NucbSplitConfig,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
) -> SplitBuild:
    """Build NucB membership and audit facts without reading hidden truth."""
    candidates = _parse_candidates(config, candidate_bytes)
    candidate_ids = frozenset(candidate.candidate_id for candidate in candidates)
    upstream = _parse_upstream_memberships(config, upstream_bytes, candidate_ids)
    mutation_counts, edges = _hamming1_edges(candidates, config.reference_sequence)
    observed_mutation_range = (min(mutation_counts.values()), max(mutation_counts.values()))
    if observed_mutation_range != (
        config.expected_mutation_count_min,
        config.expected_mutation_count_max,
    ):
        raise NucbSplitError("observed mutation-count range differs from the frozen config")
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
        raise NucbSplitError("observed Hamming-1 topology differs from the frozen config")
    observed_component_sizes = dict(sorted(Counter(sizes).items()))
    if observed_component_sizes != config.expected_component_size_counts:
        raise NucbSplitError("component-size multiset differs from the frozen config")

    singletons = tuple(component for component in components if len(component.members) == 1)
    non_singletons = tuple(component for component in components if len(component.members) > 1)
    observed_strata = dict(
        sorted(Counter(cast(int, component.mutation_count) for component in singletons).items())
    )
    if observed_strata != config.expected_singleton_mutation_counts:
        raise NucbSplitError("singleton mutation strata differ from the frozen config")
    assignments = assign_stratified_singletons(
        singletons,
        {role: config.role_counts[role] for role in config.assignment_role_order},
        config.assignment_role_order,
    )
    assignments.update({component.group_id: "development" for component in non_singletons})

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
        raise NucbSplitError("assigned mutation-stratum quotas differ from the frozen config")

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
        raise NucbSplitError("a Hamming-1 edge crosses benchmark roles")

    upstream_crossings, upstream_disagreements = _upstream_audit(config, upstream, edges)
    if upstream_crossings != config.expected_upstream_cross_role_edges:
        raise NucbSplitError("upstream edge comparison differs from the frozen config")
    if upstream_disagreements != config.expected_upstream_assignment_disagreement_count:
        raise NucbSplitError("upstream assignment comparison differs from the frozen config")
    upstream_role_counts, upstream_mixed_counts = _publisher_audit_details(
        config,
        upstream,
        components,
    )

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
        raise NucbSplitError("assigned candidate counts differ from the frozen role counts")

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
        "component_size_counts": {
            str(size): count for size, count in observed_component_sizes.items()
        },
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
        "upstream_role_counts": upstream_role_counts,
        "upstream_mixed_component_counts": upstream_mixed_counts,
        "upstream_assignment_disagreement_count": upstream_disagreements,
        "upstream_metadata_role": "audit_only_not_benchmark_membership",
        "hidden_truth_read": False,
        "known_limitations": list(config.known_limitations),
    }
    return SplitBuild(membership=membership, audit=audit)


def build_nucb_split_bundle(
    *,
    config: NucbSplitConfig,
    config_bytes: bytes,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic NucB split artifacts and a manifest without publishing them."""
    _git_commit(git_commit)
    is_fixture = config.dataset_version.startswith("fixture-") and config.split_id.startswith(
        "fixture-"
    )
    if (
        _load_nucb_split_config_bytes(
            config_bytes,
            require_production_identity=not is_fixture,
        )
        != config
    ):
        raise NucbSplitError("split config object does not match split config bytes")
    _verify_processing_chain(config, processing_manifest_bytes, candidate_bytes, upstream_bytes)
    build = build_nucb_split_membership(
        config=config,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
    )
    return _build_split_payloads_and_manifest(
        config=config,
        config_bytes=config_bytes,
        git_commit=git_commit,
        build=build,
    )


def validate_nucb_split_bundle(
    *,
    config: NucbSplitConfig,
    config_bytes: bytes,
    processing_manifest_bytes: bytes,
    candidate_bytes: bytes,
    upstream_bytes: bytes,
    membership_bytes: bytes,
    audit_bytes: bytes,
    manifest: dict[str, object],
) -> None:
    """Rebuild the NucB bundle and reject any semantic or exact-byte mismatch."""
    git_commit = manifest.get("git_commit")
    if not isinstance(git_commit, str):
        raise NucbSplitError("Split Manifest git_commit must be a string")
    expected_payloads, expected_manifest = build_nucb_split_bundle(
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
