"""Deterministic processing for the FLIP2 v3 Nuclease B task."""

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

NucbProcessingError = ProcessingError
NucbAudit = ProcessingAudit

_LABEL_LEVELS = (
    "non-functional",
    "activity_greater_than_0",
    "activity_greater_than_WT",
    "activity_greater_than_A73R",
)
_TARGET_VALUES = ("0", "1", "2", "3")
_THRESHOLD_POLICY = "experiment_config_only_not_frozen_by_processing"
_SOURCE_RELATIONSHIP = "same_candidate_universe_and_hidden_truth_with_alternate_split_metadata"
_FIRST_PARTY_COMMIT = "702284847c4558567d8288169a86dc147cfaab32"
_FIRST_PARTY_CONSTANTS_URL = (
    "https://github.com/google-deepmind/nuclease_design/blob/"
    f"{_FIRST_PARTY_COMMIT}/nuclease_design/constants.py"
)


@dataclass(frozen=True)
class NucbProcessingConfig:
    """Frozen rules and evidence-backed NucB facts for one processing revision."""

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
    label_type: str
    label_direction: str
    label_levels: tuple[str, ...]
    allowed_target_values: tuple[str, ...]
    label_semantics: str
    label_source_commit: str
    label_source_url: str
    high_value_threshold_policy: str
    allowed_set_values: tuple[str, ...]
    allowed_validation_values: tuple[str, ...]
    near_duplicate_method: str
    near_duplicate_threshold: int
    expected_near_duplicate_pair_count: int
    reference_sequence: str
    reference_sequence_sha256: str
    reference_resolution: str
    reference_source_commit: str
    reference_source_url: str
    expected_reference_candidate_present: bool
    mutation_count_method: str
    expected_mutation_count_min: int
    expected_mutation_count_max: int
    source_relationship: str
    publisher_reported_candidate_count: int
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
    "label_type",
    "label_direction",
    "label_levels",
    "allowed_target_values",
    "label_semantics",
    "label_source_commit",
    "label_source_url",
    "high_value_threshold_policy",
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
    "reference_sequence_sha256",
    "reference_resolution",
    "reference_source_commit",
    "reference_source_url",
    "expected_reference_candidate_present",
    "mutation_count_method",
    "expected_mutation_count_min",
    "expected_mutation_count_max",
    "source_relationship",
    "publisher_reported_candidate_count",
    "upstream_candidate_count_limitation",
}


def _integer(mapping: dict[str, object], field: str, *, minimum: int) -> int:
    value = mapping.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise NucbProcessingError(f"{field} must be an integer >= {minimum}")
    return value


def _commit(mapping: dict[str, object], field: str) -> str:
    value = string(mapping, field)
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise NucbProcessingError(f"{field} must be a full lowercase Git commit")
    return value


def load_nucb_processing_config(path: Path) -> NucbProcessingConfig:
    """Load one strict, evidence-backed NucB processing config."""
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise NucbProcessingError("processing config must be an object")
    config = cast(dict[str, object], raw)
    if set(config) != _CONFIG_KEYS:
        raise NucbProcessingError("processing config fields do not match NucB version 1")
    if config.get("config_version") != "1":
        raise NucbProcessingError("unsupported processing config version")
    if config.get("dataset_id") != "flip2" or config.get("task_id") != "nucb":
        raise NucbProcessingError("processing config must identify the FLIP2 NucB task")
    if config.get("candidate_id_algorithm") != "sha256":
        raise NucbProcessingError("candidate_id_algorithm must be sha256")
    if config.get("missing_value_policy") != "error":
        raise NucbProcessingError("missing_value_policy must fail closed")
    if config.get("within_file_duplicate_policy") != "error":
        raise NucbProcessingError("within_file_duplicate_policy must fail closed")
    if config.get("cross_file_duplicate_policy") != "aggregate_if_target_matches":
        raise NucbProcessingError("cross_file_duplicate_policy is unsupported")
    if config.get("conflicting_target_policy") != "error":
        raise NucbProcessingError("conflicting_target_policy must fail closed")
    if config.get("near_duplicate_policy") != "allowed_pending_split_manifest":
        raise NucbProcessingError("near_duplicate_policy must defer grouping to Split Manifest")

    source_keys = tuple(safe_source_key(item) for item in string_list(config, "source_csv_keys"))
    if set(source_keys) != {"nucb/random.csv.gz", "nucb/two_to_many.csv.gz"}:
        raise NucbProcessingError("source_csv_keys must preserve the two accepted NucB CSV files")
    required_fields = string_list(config, "required_fields")
    if required_fields != ("sequence", "target", "set", "validation"):
        raise NucbProcessingError("required_fields must preserve the frozen NucB source schema")
    observable_fields = string_list(config, "observable_feature_fields")
    hidden_label = string(config, "hidden_label_field")
    if hidden_label in observable_fields:
        raise NucbProcessingError("hidden label cannot be an observable feature")
    if hidden_label != "target":
        raise NucbProcessingError("NucB hidden label must be target")
    if config.get("label_type") != "ordinal_class":
        raise NucbProcessingError("NucB label_type must be ordinal_class")
    if config.get("label_direction") != "higher_is_better":
        raise NucbProcessingError("NucB label direction must be higher_is_better")
    label_levels = string_list(config, "label_levels")
    if label_levels != _LABEL_LEVELS:
        raise NucbProcessingError("label_levels do not match the frozen NucB activity classes")
    target_values = string_list(config, "allowed_target_values")
    if target_values != _TARGET_VALUES:
        raise NucbProcessingError("allowed_target_values must be the ordered classes 0,1,2,3")
    if config.get("high_value_threshold_policy") != _THRESHOLD_POLICY:
        raise NucbProcessingError("numeric high-value threshold must remain in Experiment Config")
    if config.get("near_duplicate_method") != "full_length_hamming_distance":
        raise NucbProcessingError("near_duplicate_method is unsupported")
    if config.get("mutation_count_method") != "full_length_hamming_distance_to_reference":
        raise NucbProcessingError("mutation_count_method is unsupported")
    if config.get("source_relationship") != _SOURCE_RELATIONSHIP:
        raise NucbProcessingError("source_relationship does not match the accepted NucB audit")
    label_source_commit = _commit(config, "label_source_commit")
    reference_source_commit = _commit(config, "reference_source_commit")
    if label_source_commit != _FIRST_PARTY_COMMIT or reference_source_commit != _FIRST_PARTY_COMMIT:
        raise NucbProcessingError(
            "label and reference provenance must bind the approved first-party commit"
        )
    label_source_url = string(config, "label_source_url")
    reference_source_url = string(config, "reference_source_url")
    if (
        label_source_url != _FIRST_PARTY_CONSTANTS_URL
        or reference_source_url != _FIRST_PARTY_CONSTANTS_URL
    ):
        raise NucbProcessingError(
            "label and reference provenance must use the commit-pinned constants.py URL"
        )

    sequence_length = _integer(config, "sequence_length", minimum=1)
    candidate_count = _integer(config, "expected_candidate_count", minimum=1)
    threshold = _integer(config, "near_duplicate_threshold", minimum=1)
    if threshold != 1:
        raise NucbProcessingError("NucB near_duplicate_threshold must remain 1")
    mutation_min = _integer(config, "expected_mutation_count_min", minimum=0)
    mutation_max = _integer(config, "expected_mutation_count_max", minimum=0)
    if mutation_min > mutation_max:
        raise NucbProcessingError("expected mutation count range is invalid")

    alphabet = string(config, "allowed_amino_acids")
    if alphabet != "".join(sorted(set(alphabet))):
        raise NucbProcessingError("allowed_amino_acids must be sorted and unique")
    reference = string(config, "reference_sequence")
    if reference != reference.strip().upper():
        raise NucbProcessingError("reference_sequence must be canonical uppercase text")
    if len(reference) != sequence_length or not set(reference) <= set(alphabet):
        raise NucbProcessingError("reference_sequence does not match sequence rules")
    reference_sha256 = lower_hex_digest(
        string(config, "reference_sequence_sha256"),
        field="reference_sequence_sha256",
    )
    if hashlib.sha256(reference.encode()).hexdigest() != reference_sha256:
        raise NucbProcessingError("reference_sequence_sha256 does not match reference_sequence")
    expected_reference_present = config.get("expected_reference_candidate_present")
    if not isinstance(expected_reference_present, bool):
        raise NucbProcessingError("expected_reference_candidate_present must be a boolean")

    return NucbProcessingConfig(
        dataset_id="flip2",
        dataset_version=string(config, "dataset_version"),
        task_id="nucb",
        dataset_manifest_sha256=lower_hex_digest(
            string(config, "dataset_manifest_sha256"),
            field="dataset_manifest_sha256",
        ),
        source_csv_keys=source_keys,
        required_fields=required_fields,
        allowed_amino_acids=alphabet,
        sequence_length=sequence_length,
        expected_candidate_count=candidate_count,
        candidate_id_namespace=string(config, "candidate_id_namespace"),
        observable_feature_fields=observable_fields,
        hidden_label_field=hidden_label,
        label_type="ordinal_class",
        label_direction="higher_is_better",
        label_levels=label_levels,
        allowed_target_values=target_values,
        label_semantics=string(config, "label_semantics"),
        label_source_commit=label_source_commit,
        label_source_url=label_source_url,
        high_value_threshold_policy=_THRESHOLD_POLICY,
        allowed_set_values=string_list(config, "allowed_set_values"),
        allowed_validation_values=string_list(config, "allowed_validation_values"),
        near_duplicate_method="full_length_hamming_distance",
        near_duplicate_threshold=threshold,
        expected_near_duplicate_pair_count=_integer(
            config,
            "expected_near_duplicate_pair_count",
            minimum=0,
        ),
        reference_sequence=reference,
        reference_sequence_sha256=reference_sha256,
        reference_resolution=string(config, "reference_resolution"),
        reference_source_commit=reference_source_commit,
        reference_source_url=reference_source_url,
        expected_reference_candidate_present=expected_reference_present,
        mutation_count_method="full_length_hamming_distance_to_reference",
        expected_mutation_count_min=mutation_min,
        expected_mutation_count_max=mutation_max,
        source_relationship=_SOURCE_RELATIONSHIP,
        publisher_reported_candidate_count=_integer(
            config,
            "publisher_reported_candidate_count",
            minimum=1,
        ),
        upstream_candidate_count_limitation=string(
            config,
            "upstream_candidate_count_limitation",
        ),
    )


def audit_nucb_sources(
    config: NucbProcessingConfig,
    raw_root: Path,
    *,
    raw_csv_artifacts: dict[str, tuple[str, int]] | None = None,
) -> NucbAudit:
    """Audit the two NucB source views and construct one canonical candidate universe."""
    source_reads: dict[str, SourceRead] = {}
    source_reports: dict[str, object] = {}
    for source_key in config.source_csv_keys:
        path = raw_root.joinpath(*PurePosixPath(source_key).parts)
        if raw_csv_artifacts is not None:
            expected = raw_csv_artifacts.get(source_key)
            if expected is None:
                raise NucbProcessingError(
                    f"missing Dataset Manifest artifact for source file: {source_key}"
                )
            if not path.is_file():
                raise NucbProcessingError(f"missing source file: {source_key}")
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
        unexpected_targets = set(source.targets_by_sequence.values()) - set(
            config.allowed_target_values
        )
        if unexpected_targets:
            raise NucbProcessingError(
                f"{source_key}: target must be one of {','.join(config.allowed_target_values)}"
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
        raise NucbProcessingError("observed reference differs from the frozen NucB reference")
    reference_present = reference in reconciled.sequences
    if reference_present is not config.expected_reference_candidate_present:
        raise NucbProcessingError("reference candidate presence differs from processing config")

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
        raise NucbProcessingError("observed mutation count range differs from processing config")

    near_duplicates = near_duplicate_counts(
        reconciled.sequences,
        reference,
        config.near_duplicate_threshold,
    )
    if near_duplicates != {"1": config.expected_near_duplicate_pair_count}:
        raise NucbProcessingError("near-duplicate count differs from processing config")

    target_class_counts = Counter(candidate.target for candidate in reconciled.candidates)
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
        "reference_source_commit": config.reference_source_commit,
        "reference_source_url": config.reference_source_url,
        "reference_sequence_sha256": config.reference_sequence_sha256,
        "reference_candidate_present": reference_present,
        "mutation_count_method": config.mutation_count_method,
        "mutation_count_from_reference": {
            str(count): occurrences for count, occurrences in sorted(mutation_counts.items())
        },
        "observed_mutation_count_min": observed_mutation_min,
        "observed_mutation_count_max": observed_mutation_max,
        "publisher_reported_candidate_count": config.publisher_reported_candidate_count,
        "publisher_claim_minus_processed_candidates": (
            config.publisher_reported_candidate_count - len(reconciled.candidates)
        ),
        "upstream_candidate_count_limitation": config.upstream_candidate_count_limitation,
        "target_type": config.label_type,
        "target_semantics": config.label_semantics,
        "target_level_meanings": dict(
            zip(config.allowed_target_values, config.label_levels, strict=True)
        ),
        "target_class_counts": {
            target: target_class_counts[target] for target in config.allowed_target_values
        },
        "label_direction": config.label_direction,
        "label_source_commit": config.label_source_commit,
        "label_source_url": config.label_source_url,
        "high_value_threshold_policy": config.high_value_threshold_policy,
        "near_duplicate_method": config.near_duplicate_method,
        "near_duplicate_threshold": config.near_duplicate_threshold,
        "near_duplicate_pair_counts": near_duplicates,
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "upstream_split_metadata_is_not_frozen": True,
    }
    return NucbAudit(
        candidates=reconciled.candidates,
        memberships=reconciled.memberships,
        report=report,
    )


def build_nucb_processing_bundle(
    *,
    config: NucbProcessingConfig,
    config_bytes: bytes,
    dataset_manifest_bytes: bytes,
    raw_root: Path,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic NucB artifacts and a Processing Manifest without publishing them."""
    identity = prepare_processing_identity(
        config=config,
        config_bytes=config_bytes,
        dataset_manifest_bytes=dataset_manifest_bytes,
        git_commit_value=git_commit,
    )
    audit = audit_nucb_sources(
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
                "The ordered activity classes preserve source labels, but no numeric high-value "
                "threshold is authorized by this processing revision."
            ),
            (
                "Near-duplicate grouping, leakage controls, and held-out partition identity are "
                "deferred to a Split Manifest; per-run held-out authorization belongs to an "
                "Experiment Config."
            ),
            (
                "This processing revision provides no feature, model, replay, benchmark, or "
                "scientific-gate evidence."
            ),
        ],
    )
