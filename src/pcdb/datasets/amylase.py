"""Deterministic processing for the FLIP2 v3 Alpha Amylase task."""

from __future__ import annotations

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

AmylaseProcessingError = ProcessingError


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


AmylaseAudit = ProcessingAudit


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

    source_keys = tuple(safe_source_key(item) for item in string_list(config, "source_csv_keys"))
    required_fields = string_list(config, "required_fields")
    if required_fields != ("sequence", "target", "set", "validation"):
        raise AmylaseProcessingError("required_fields must preserve the frozen source schema")
    observable_fields = string_list(config, "observable_feature_fields")
    hidden_label = string(config, "hidden_label_field")
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

    alphabet = string(config, "allowed_amino_acids")
    if alphabet != "".join(sorted(set(alphabet))):
        raise AmylaseProcessingError("allowed_amino_acids must be sorted and unique")

    return AmylaseProcessingConfig(
        dataset_id=string(config, "dataset_id"),
        dataset_version=string(config, "dataset_version"),
        task_id=string(config, "task_id"),
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
        label_source_url=string(config, "label_source_url"),
        allowed_set_values=string_list(config, "allowed_set_values"),
        allowed_validation_values=string_list(config, "allowed_validation_values"),
        near_duplicate_method="full_length_hamming_distance",
        near_duplicate_threshold=threshold,
    )


def audit_amylase_sources(
    config: AmylaseProcessingConfig,
    raw_root: Path,
    *,
    raw_csv_artifacts: dict[str, tuple[str, int]] | None = None,
) -> AmylaseAudit:
    """Audit all alternate split files and construct one canonical candidate universe."""
    source_reads: dict[str, SourceRead] = {}
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

    sequences = reconciled.sequences
    reference = modal_reference(sequences)
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
        "candidate_count": len(reconciled.candidates),
        "union_sequence_count": len(sequences),
        "intersection_sequence_count": len(sequences),
        "all_source_candidate_sets_identical": True,
        "cross_source_target_conflict_count": 0,
        "sequence_length": config.sequence_length,
        "sequence_alphabet": config.allowed_amino_acids,
        "modal_reference_candidate_present": reference in sequences,
        "mutation_count_from_modal_reference": {
            str(count): occurrences for count, occurrences in sorted(mutation_counts.items())
        },
        "target_semantics": config.label_semantics,
        "label_direction": config.label_direction,
        "label_source_url": config.label_source_url,
        "near_duplicate_method": config.near_duplicate_method,
        "near_duplicate_threshold": config.near_duplicate_threshold,
        "near_duplicate_pair_counts": near_duplicate_counts(
            sequences, reference, config.near_duplicate_threshold
        ),
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "upstream_split_metadata_is_not_frozen": True,
    }
    return AmylaseAudit(
        candidates=reconciled.candidates,
        memberships=reconciled.memberships,
        report=report,
    )


def build_amylase_processing_bundle(
    *,
    config: AmylaseProcessingConfig,
    config_bytes: bytes,
    dataset_manifest_bytes: bytes,
    raw_root: Path,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic processed artifacts and a Processing Manifest without publishing them."""
    identity = prepare_processing_identity(
        config=config,
        config_bytes=config_bytes,
        dataset_manifest_bytes=dataset_manifest_bytes,
        git_commit_value=git_commit,
    )
    audit = audit_amylase_sources(
        config,
        raw_root,
        raw_csv_artifacts=identity.raw_csv_artifacts,
    )
    return render_processing_bundle(
        config=config,
        identity=identity,
        audit=audit,
        known_limitations=[
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
    )
