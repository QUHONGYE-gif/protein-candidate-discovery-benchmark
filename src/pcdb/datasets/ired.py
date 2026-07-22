"""Deterministic processing for the FLIP2 v3 Imine Reductase task."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from pcdb.datasets.processing import (
    ProcessingAudit,
    ProcessingError,
    SourceRead,
    lower_hex_digest,
    modal_reference,
    near_duplicate_counts,
    prepare_processing_identity,
    read_split_reference_source,
    reconcile_split_reference_sources,
    render_processing_bundle,
    safe_source_key,
    string,
    string_list,
    verify_raw_file,
)

IredProcessingError = ProcessingError
IredAudit = ProcessingAudit


@dataclass(frozen=True)
class IredProcessingConfig:
    """Frozen rules and evidence-backed IRED-specific facts for one revision."""

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
    label_units: str
    label_source_url: str
    high_value_meaning: str
    allowed_set_values: tuple[str, ...]
    allowed_validation_values: tuple[str, ...]
    near_duplicate_method: str
    near_duplicate_threshold: int
    expected_near_duplicate_pair_count: int
    reference_sequence: str
    reference_resolution: str
    reference_source_url: str
    mutation_count_method: str
    expected_mutation_count_min: int
    expected_mutation_count_max: int
    source_relationship: str
    publisher_reported_candidate_count: int
    upstream_active_row_count: int
    upstream_inactive_row_count: int
    upstream_candidate_count_limitation: str


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
    "label_units",
    "label_source_url",
    "high_value_meaning",
    "missing_value_policy",
    "allowed_set_values",
    "allowed_validation_values",
    "within_file_duplicate_policy",
    "cross_file_duplicate_policy",
    "conflicting_target_policy",
    "near_duplicate_method",
    "near_duplicate_threshold",
    "near_duplicate_policy",
    "expected_near_duplicate_pair_count",
    "reference_sequence",
    "reference_resolution",
    "reference_source_url",
    "mutation_count_method",
    "expected_mutation_count_min",
    "expected_mutation_count_max",
    "source_relationship",
    "publisher_reported_candidate_count",
    "upstream_active_row_count",
    "upstream_inactive_row_count",
    "upstream_candidate_count_limitation",
}


def _integer(
    mapping: dict[str, object],
    field: str,
    *,
    minimum: int,
) -> int:
    value = mapping.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise IredProcessingError(f"{field} must be an integer >= {minimum}")
    return value


def load_ired_processing_config(path: Path) -> IredProcessingConfig:
    """Load the strict, evidence-backed IRED processing config."""
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise IredProcessingError("processing config must be an object")
    config = cast(dict[str, object], raw)
    if set(config) != _CONFIG_KEYS:
        raise IredProcessingError("processing config fields do not match IRED version 1")
    if config.get("config_version") != "1":
        raise IredProcessingError("unsupported processing config version")
    if config.get("dataset_id") != "flip2" or config.get("task_id") != "ired":
        raise IredProcessingError("processing config must identify the FLIP2 IRED task")
    if config.get("candidate_id_algorithm") != "sha256":
        raise IredProcessingError("candidate_id_algorithm must be sha256")
    if config.get("missing_value_policy") != "error":
        raise IredProcessingError("missing_value_policy must fail closed")
    if config.get("within_file_duplicate_policy") != "error":
        raise IredProcessingError("within_file_duplicate_policy must fail closed")
    if config.get("cross_file_duplicate_policy") != "aggregate_if_target_matches":
        raise IredProcessingError("cross_file_duplicate_policy is unsupported")
    if config.get("conflicting_target_policy") != "error":
        raise IredProcessingError("conflicting_target_policy must fail closed")
    if config.get("near_duplicate_policy") != "allowed_pending_split_manifest":
        raise IredProcessingError("near_duplicate_policy must defer grouping to Split Manifest")

    source_keys = tuple(safe_source_key(item) for item in string_list(config, "source_csv_keys"))
    if set(source_keys) != {"ired/random.csv.gz", "ired/two_to_many.csv.gz"}:
        raise IredProcessingError("source_csv_keys must preserve the two accepted IRED CSV files")
    required_fields = string_list(config, "required_fields")
    if required_fields != ("sequence", "set", "validation", "target"):
        raise IredProcessingError("required_fields must preserve the frozen IRED source schema")
    observable_fields = string_list(config, "observable_feature_fields")
    hidden_label = string(config, "hidden_label_field")
    if hidden_label in observable_fields:
        raise IredProcessingError("hidden label cannot be an observable feature")
    if hidden_label != "target":
        raise IredProcessingError("IRED hidden label must be target")
    if config.get("label_direction") != "higher_is_better":
        raise IredProcessingError("IRED label direction must be higher_is_better")
    if config.get("near_duplicate_method") != "full_length_hamming_distance":
        raise IredProcessingError("near_duplicate_method is unsupported")
    if config.get("mutation_count_method") != "full_length_hamming_distance_to_reference":
        raise IredProcessingError("mutation_count_method is unsupported")
    if (
        config.get("source_relationship")
        != "same_candidate_universe_and_hidden_truth_with_alternate_split_metadata"
    ):
        raise IredProcessingError("source_relationship does not match the accepted IRED audit")

    sequence_length = _integer(config, "sequence_length", minimum=1)
    candidate_count = _integer(config, "expected_candidate_count", minimum=1)
    threshold = _integer(config, "near_duplicate_threshold", minimum=1)
    if threshold != 1:
        raise IredProcessingError("IRED near_duplicate_threshold must remain 1")
    mutation_min = _integer(config, "expected_mutation_count_min", minimum=0)
    mutation_max = _integer(config, "expected_mutation_count_max", minimum=0)
    if mutation_min > mutation_max:
        raise IredProcessingError("expected mutation count range is invalid")

    alphabet = string(config, "allowed_amino_acids")
    if alphabet != "".join(sorted(set(alphabet))):
        raise IredProcessingError("allowed_amino_acids must be sorted and unique")
    reference = string(config, "reference_sequence")
    if reference != reference.strip().upper():
        raise IredProcessingError("reference_sequence must be canonical uppercase text")
    if len(reference) != sequence_length or not set(reference) <= set(alphabet):
        raise IredProcessingError("reference_sequence does not match sequence rules")

    return IredProcessingConfig(
        dataset_id="flip2",
        dataset_version=string(config, "dataset_version"),
        task_id="ired",
        dataset_manifest_sha256=lower_hex_digest(
            string(config, "dataset_manifest_sha256"), field="dataset_manifest_sha256"
        ),
        source_csv_keys=source_keys,
        required_fields=required_fields,
        allowed_amino_acids=alphabet,
        sequence_length=sequence_length,
        expected_candidate_count=candidate_count,
        candidate_id_namespace=string(config, "candidate_id_namespace"),
        observable_feature_fields=observable_fields,
        hidden_label_field=hidden_label,
        label_direction="higher_is_better",
        label_semantics=string(config, "label_semantics"),
        label_units=string(config, "label_units"),
        label_source_url=string(config, "label_source_url"),
        high_value_meaning=string(config, "high_value_meaning"),
        allowed_set_values=string_list(config, "allowed_set_values"),
        allowed_validation_values=string_list(config, "allowed_validation_values"),
        near_duplicate_method="full_length_hamming_distance",
        near_duplicate_threshold=threshold,
        expected_near_duplicate_pair_count=_integer(
            config, "expected_near_duplicate_pair_count", minimum=0
        ),
        reference_sequence=reference,
        reference_resolution=string(config, "reference_resolution"),
        reference_source_url=string(config, "reference_source_url"),
        mutation_count_method="full_length_hamming_distance_to_reference",
        expected_mutation_count_min=mutation_min,
        expected_mutation_count_max=mutation_max,
        source_relationship=string(config, "source_relationship"),
        publisher_reported_candidate_count=_integer(
            config, "publisher_reported_candidate_count", minimum=1
        ),
        upstream_active_row_count=_integer(config, "upstream_active_row_count", minimum=1),
        upstream_inactive_row_count=_integer(config, "upstream_inactive_row_count", minimum=1),
        upstream_candidate_count_limitation=string(config, "upstream_candidate_count_limitation"),
    )


def audit_ired_sources(
    config: IredProcessingConfig,
    raw_root: Path,
    *,
    raw_csv_artifacts: dict[str, tuple[str, int]] | None = None,
) -> IredAudit:
    """Audit alternate IRED split files and construct one canonical candidate universe."""
    source_reads: dict[str, SourceRead] = {}
    source_reports: dict[str, object] = {}
    for source_key in config.source_csv_keys:
        path = raw_root.joinpath(*PurePosixPath(source_key).parts)
        if raw_csv_artifacts is not None:
            expected = raw_csv_artifacts.get(source_key)
            if expected is None:
                raise IredProcessingError(
                    f"missing Dataset Manifest artifact for source file: {source_key}"
                )
            if not path.is_file():
                raise IredProcessingError(f"missing source file: {source_key}")
            verify_raw_file(path, source_key, expected)
        source = read_split_reference_source(
            path=path,
            source_key=source_key,
            required_fields=config.required_fields,
            allowed_amino_acids=config.allowed_amino_acids,
            sequence_length=config.sequence_length,
            allowed_set_values=config.allowed_set_values,
            allowed_validation_values=config.allowed_validation_values,
        )
        source_reads[source_key] = source
        source_reports[source_key] = source.report

    reconciled = reconcile_split_reference_sources(
        config=config,
        sources=source_reads,
        expected_candidate_count=config.expected_candidate_count,
    )
    reference = modal_reference(reconciled.sequences, require_unique=True)
    if reference != config.reference_sequence:
        raise IredProcessingError("observed reference differs from the frozen IRED reference")
    if reference not in reconciled.sequences:
        raise IredProcessingError("frozen IRED reference is not present as a candidate")

    mutation_counts = Counter(
        sum(left != right for left, right in zip(sequence, reference, strict=True))
        for sequence in reconciled.sequences
    )
    observed_mutation_min = min(mutation_counts)
    observed_mutation_max = max(mutation_counts)
    if (
        observed_mutation_min != config.expected_mutation_count_min
        or observed_mutation_max != config.expected_mutation_count_max
    ):
        raise IredProcessingError("observed mutation count range differs from processing config")

    near_duplicates = near_duplicate_counts(
        reconciled.sequences,
        reference,
        config.near_duplicate_threshold,
    )
    if near_duplicates != {"1": config.expected_near_duplicate_pair_count}:
        raise IredProcessingError("near-duplicate count differs from processing config")

    upstream_deposit_count = config.upstream_active_row_count + config.upstream_inactive_row_count
    report: dict[str, object] = {
        "schema_version": "1",
        "dataset_id": config.dataset_id,
        "dataset_version": config.dataset_version,
        "task_id": config.task_id,
        "source_file_count": len(config.source_csv_keys),
        "source_files": source_reports,
        "candidate_count": len(reconciled.candidates),
        "union_sequence_count": len(reconciled.sequences),
        "intersection_sequence_count": len(reconciled.sequences),
        "all_source_candidate_sets_identical": True,
        "all_source_targets_identical": True,
        "cross_source_target_conflict_count": 0,
        "source_relationship": config.source_relationship,
        "sequence_length": config.sequence_length,
        "sequence_alphabet": config.allowed_amino_acids,
        "reference_resolution": config.reference_resolution,
        "reference_source_url": config.reference_source_url,
        "reference_sequence_sha256": hashlib.sha256(reference.encode()).hexdigest(),
        "reference_candidate_present": True,
        "mutation_count_method": config.mutation_count_method,
        "mutation_count_from_reference": {
            str(count): occurrences for count, occurrences in sorted(mutation_counts.items())
        },
        "observed_mutation_count_min": observed_mutation_min,
        "observed_mutation_count_max": observed_mutation_max,
        "publisher_reported_candidate_count": config.publisher_reported_candidate_count,
        "upstream_deposit_active_row_count": config.upstream_active_row_count,
        "upstream_deposit_inactive_row_count": config.upstream_inactive_row_count,
        "upstream_deposit_total_row_count": upstream_deposit_count,
        "publisher_claim_minus_upstream_deposit_rows": (
            config.publisher_reported_candidate_count - upstream_deposit_count
        ),
        "publisher_claim_minus_processed_candidates": (
            config.publisher_reported_candidate_count - len(reconciled.candidates)
        ),
        "upstream_candidate_count_limitation": config.upstream_candidate_count_limitation,
        "target_semantics": config.label_semantics,
        "target_units": config.label_units,
        "label_direction": config.label_direction,
        "label_source_url": config.label_source_url,
        "high_value_meaning": config.high_value_meaning,
        "near_duplicate_method": config.near_duplicate_method,
        "near_duplicate_threshold": config.near_duplicate_threshold,
        "near_duplicate_pair_counts": near_duplicates,
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "upstream_split_metadata_is_not_frozen": True,
    }
    return IredAudit(
        candidates=reconciled.candidates,
        memberships=reconciled.memberships,
        report=report,
    )


def build_ired_processing_bundle(
    *,
    config: IredProcessingConfig,
    config_bytes: bytes,
    dataset_manifest_bytes: bytes,
    raw_root: Path,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic IRED artifacts and a Processing Manifest without publishing them."""
    identity = prepare_processing_identity(
        config=config,
        config_bytes=config_bytes,
        dataset_manifest_bytes=dataset_manifest_bytes,
        git_commit_value=git_commit,
    )
    audit = audit_ired_sources(
        config,
        raw_root,
        raw_csv_artifacts=identity.raw_csv_artifacts,
    )
    return render_processing_bundle(
        config=config,
        identity=identity,
        audit=audit,
        known_limitations=[
            config.upstream_candidate_count_limitation,
            (
                "Upstream random, two-to-many, set, and validation values are retained only as "
                "audit metadata and are not a frozen benchmark split."
            ),
            (
                "Higher log-enrichment means higher measured activity, but no benchmark "
                "high-value threshold is authorized by this processing revision."
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
    )
