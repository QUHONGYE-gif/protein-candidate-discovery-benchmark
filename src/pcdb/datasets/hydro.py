"""Deterministic processing for the FLIP2 v3 Hydrophobic Core task."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import cast

from pcdb.datasets.processing import (
    Candidate,
    ProcessingAudit,
    ProcessingError,
    SourceMembership,
    canonical_decimal,
    lower_hex_digest,
    near_duplicate_counts,
    prepare_processing_identity,
    render_processing_bundle,
    safe_source_key,
    string,
    string_list,
    verify_raw_file,
)

HydroProcessingError = ProcessingError
HydroAudit = ProcessingAudit

_SOURCE_RELATIONSHIP = "same_candidate_universe_and_hidden_truth_with_alternate_split_metadata"
_THRESHOLD_POLICY = "experiment_config_only_not_frozen_by_processing"
_CANDIDATE_PREIMAGE = "namespace_null_wild_type_id_null_sequence"
_NEAR_DUPLICATE_METHOD = "within_wild_type_full_length_hamming_distance"
_LABEL_SEMANTICS = "Continuous FLIP2 hydrophobic-core stability fitness source score."
_LABEL_SOURCE_URL = "https://flip.protein.properties/"
_ALLOWED_AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
_VARIABLE_AMINO_ACIDS = "FILMV"
_ALLOWED_SET_VALUES = ("test", "train")
_ALLOWED_VALIDATION_VALUES = ("False", "True")


@dataclass(frozen=True)
class HydroWildType:
    """One frozen Hydro wild-type/reference identity."""

    wild_type_id: str
    sequence_length: int
    reference_sequence: str
    reference_sequence_sha256: str
    reference_source_url: str
    reference_candidate_present: bool
    expected_candidate_count: int
    variable_positions: tuple[int, ...]
    expected_mutation_count_min: int
    expected_mutation_count_max: int
    expected_near_duplicate_pair_count: int


@dataclass(frozen=True)
class HydroProcessingConfig:
    """Frozen rules and evidence-backed Hydro facts for one processing revision."""

    dataset_id: str
    dataset_version: str
    task_id: str
    dataset_manifest_sha256: str
    source_csv_keys: tuple[str, ...]
    required_fields: tuple[str, ...]
    allowed_amino_acids: str
    variable_amino_acids: str
    expected_candidate_count: int
    candidate_id_namespace: str
    observable_feature_fields: tuple[str, ...]
    hidden_label_field: str
    label_type: str
    label_direction: str
    label_semantics: str
    label_units: str
    label_source_url: str
    high_value_threshold_policy: str
    allowed_set_values: tuple[str, ...]
    allowed_validation_values: tuple[str, ...]
    near_duplicate_method: str
    near_duplicate_threshold: int
    expected_near_duplicate_pair_count: int
    wild_types: tuple[HydroWildType, ...]
    source_relationship: str
    publisher_reported_candidate_count: int
    observed_target_min: str
    observed_target_median: str
    observed_target_max: str
    observed_distinct_target_count: int


_CONFIG_KEYS = {
    "config_version",
    "dataset_id",
    "dataset_version",
    "task_id",
    "dataset_manifest_sha256",
    "source_csv_keys",
    "required_fields",
    "allowed_amino_acids",
    "variable_amino_acids",
    "expected_candidate_count",
    "candidate_id_algorithm",
    "candidate_id_namespace",
    "candidate_id_preimage",
    "observable_feature_fields",
    "hidden_label_field",
    "label_type",
    "label_direction",
    "label_semantics",
    "label_units",
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
    "wild_types",
    "source_relationship",
    "publisher_reported_candidate_count",
    "observed_target_min",
    "observed_target_median",
    "observed_target_max",
    "observed_distinct_target_count",
}
_WILD_TYPE_KEYS = {
    "wild_type_id",
    "sequence_length",
    "reference_sequence",
    "reference_sequence_sha256",
    "reference_source_url",
    "reference_candidate_present",
    "expected_candidate_count",
    "variable_positions",
    "expected_mutation_count_min",
    "expected_mutation_count_max",
    "expected_near_duplicate_pair_count",
}


def _integer(mapping: dict[str, object], field: str, *, minimum: int) -> int:
    value = mapping.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise HydroProcessingError(f"{field} must be an integer >= {minimum}")
    return value


def _canonical_config_decimal(mapping: dict[str, object], field: str) -> str:
    value = string(mapping, field)
    canonical = canonical_decimal(value, source_key="processing config", row_number=1)
    if canonical != value:
        raise HydroProcessingError(f"{field} must be a canonical decimal")
    return canonical


def _wild_types(config: dict[str, object], alphabet: str) -> tuple[HydroWildType, ...]:
    raw = config.get("wild_types")
    if not isinstance(raw, list) or len(raw) != 3:
        raise HydroProcessingError("wild_types must contain exactly three entries")
    result: list[HydroWildType] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != _WILD_TYPE_KEYS:
            raise HydroProcessingError("wild_type fields do not match Hydro version 1")
        mapping = cast(dict[str, object], item)
        wild_type_id = string(mapping, "wild_type_id")
        if not wild_type_id.startswith("P") or not wild_type_id[1:].isalnum():
            raise HydroProcessingError("wild_type_id must be a UniProt-style accession")
        reference_source_url = string(mapping, "reference_source_url")
        expected_reference_source_url = f"https://rest.uniprot.org/uniprotkb/{wild_type_id}.fasta"
        if reference_source_url != expected_reference_source_url:
            raise HydroProcessingError(
                "reference_source_url must match the wild_type_id UniProt entry"
            )
        sequence_length = _integer(mapping, "sequence_length", minimum=1)
        reference = string(mapping, "reference_sequence")
        if (
            reference != reference.strip().upper()
            or len(reference) != sequence_length
            or not set(reference) <= set(alphabet)
        ):
            raise HydroProcessingError("reference_sequence does not match sequence rules")
        reference_sha256 = lower_hex_digest(
            string(mapping, "reference_sequence_sha256"),
            field="reference_sequence_sha256",
        )
        if hashlib.sha256(reference.encode()).hexdigest() != reference_sha256:
            raise HydroProcessingError(
                "reference_sequence_sha256 does not match reference_sequence"
            )
        positions = mapping.get("variable_positions")
        if (
            not isinstance(positions, list)
            or not positions
            or any(
                not isinstance(position, int)
                or isinstance(position, bool)
                or position < 1
                or position > sequence_length
                for position in positions
            )
            or positions != sorted(set(cast(list[int], positions)))
        ):
            raise HydroProcessingError(
                "variable_positions must be sorted unique one-based positions"
            )
        reference_present = mapping.get("reference_candidate_present")
        if not isinstance(reference_present, bool):
            raise HydroProcessingError("reference_candidate_present must be a boolean")
        mutation_min = _integer(mapping, "expected_mutation_count_min", minimum=0)
        mutation_max = _integer(mapping, "expected_mutation_count_max", minimum=0)
        if mutation_min > mutation_max:
            raise HydroProcessingError("expected mutation count range is invalid")
        result.append(
            HydroWildType(
                wild_type_id=wild_type_id,
                sequence_length=sequence_length,
                reference_sequence=reference,
                reference_sequence_sha256=reference_sha256,
                reference_source_url=reference_source_url,
                reference_candidate_present=reference_present,
                expected_candidate_count=_integer(mapping, "expected_candidate_count", minimum=1),
                variable_positions=tuple(cast(list[int], positions)),
                expected_mutation_count_min=mutation_min,
                expected_mutation_count_max=mutation_max,
                expected_near_duplicate_pair_count=_integer(
                    mapping,
                    "expected_near_duplicate_pair_count",
                    minimum=0,
                ),
            )
        )
    if len({item.wild_type_id for item in result}) != len(result):
        raise HydroProcessingError("wild_type_id values must be unique")
    if len({item.sequence_length for item in result}) != len(result):
        raise HydroProcessingError("wild-type sequence lengths must be unique")
    return tuple(result)


def load_hydro_processing_config(path: Path) -> HydroProcessingConfig:
    """Load one strict, evidence-backed Hydro processing config."""
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise HydroProcessingError("processing config must be an object")
    config = cast(dict[str, object], raw)
    if set(config) != _CONFIG_KEYS:
        raise HydroProcessingError("processing config fields do not match Hydro version 1")
    if config.get("config_version") != "1":
        raise HydroProcessingError("unsupported processing config version")
    if config.get("dataset_id") != "flip2" or config.get("task_id") != "hydro":
        raise HydroProcessingError("processing config must identify the FLIP2 Hydro task")
    dataset_version = string(config, "dataset_version")
    candidate_id_namespace = string(config, "candidate_id_namespace")
    expected_candidate_id_namespace = f"flip2:{dataset_version}:hydro"
    if candidate_id_namespace != expected_candidate_id_namespace:
        raise HydroProcessingError(
            "candidate_id_namespace must match dataset_id, dataset_version, and task_id"
        )
    required_literals = {
        "candidate_id_algorithm": "sha256",
        "candidate_id_preimage": _CANDIDATE_PREIMAGE,
        "label_type": "continuous_source_score",
        "label_direction": "higher_is_better",
        "label_semantics": _LABEL_SEMANTICS,
        "label_units": "source_score_physical_unit_unasserted",
        "label_source_url": _LABEL_SOURCE_URL,
        "high_value_threshold_policy": _THRESHOLD_POLICY,
        "missing_value_policy": "error",
        "within_file_duplicate_policy": "error",
        "cross_file_duplicate_policy": "aggregate_if_target_matches",
        "conflicting_target_policy": "error",
        "near_duplicate_method": _NEAR_DUPLICATE_METHOD,
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "source_relationship": _SOURCE_RELATIONSHIP,
    }
    for field, expected in required_literals.items():
        if config.get(field) != expected:
            raise HydroProcessingError(f"{field} does not match the frozen Hydro contract")

    source_keys = tuple(safe_source_key(item) for item in string_list(config, "source_csv_keys"))
    wild_type_ids_from_sources = {
        PurePosixPath(key).name.removeprefix("to_").removesuffix(".csv.gz")
        for key in source_keys
        if PurePosixPath(key).name.startswith("to_")
    }
    required_source_names = {
        "random_split.csv.gz",
        "three_to_many.csv.gz",
        "low_to_high.csv.gz",
    }
    if (
        len(source_keys) != 6
        or not all(PurePosixPath(key).parent == PurePosixPath("hydro") for key in source_keys)
        or not required_source_names <= {PurePosixPath(key).name for key in source_keys}
        or len(wild_type_ids_from_sources) != 3
    ):
        raise HydroProcessingError("source_csv_keys must preserve the six Hydro CSV files")
    required_fields = string_list(config, "required_fields")
    if required_fields != ("sequence", "target", "set", "validation"):
        raise HydroProcessingError("required_fields must preserve the frozen Hydro source schema")
    observable_fields = string_list(config, "observable_feature_fields")
    if observable_fields != ("wild_type_id", "sequence"):
        raise HydroProcessingError("Hydro observable fields must be wild_type_id and sequence")
    hidden_label = string(config, "hidden_label_field")
    if hidden_label != "target" or hidden_label in observable_fields:
        raise HydroProcessingError("Hydro target must remain hidden")
    alphabet = string(config, "allowed_amino_acids")
    variable_alphabet = string(config, "variable_amino_acids")
    if alphabet != _ALLOWED_AMINO_ACIDS:
        raise HydroProcessingError("allowed_amino_acids does not match the frozen Hydro contract")
    if variable_alphabet != _VARIABLE_AMINO_ACIDS:
        raise HydroProcessingError("variable_amino_acids does not match the frozen Hydro contract")
    allowed_set_values = string_list(config, "allowed_set_values")
    if allowed_set_values != _ALLOWED_SET_VALUES:
        raise HydroProcessingError("allowed_set_values does not match the frozen Hydro contract")
    allowed_validation_values = string_list(config, "allowed_validation_values")
    if allowed_validation_values != _ALLOWED_VALIDATION_VALUES:
        raise HydroProcessingError(
            "allowed_validation_values does not match the frozen Hydro contract"
        )
    wild_types = _wild_types(config, alphabet)
    if {item.wild_type_id for item in wild_types} != wild_type_ids_from_sources:
        raise HydroProcessingError("wild_types must match the three holdout source identities")
    threshold = _integer(config, "near_duplicate_threshold", minimum=1)
    if threshold != 1:
        raise HydroProcessingError("Hydro near_duplicate_threshold must remain 1")
    expected_candidate_count = _integer(config, "expected_candidate_count", minimum=1)
    publisher_reported_candidate_count = _integer(
        config,
        "publisher_reported_candidate_count",
        minimum=1,
    )
    if (
        publisher_reported_candidate_count != expected_candidate_count
        or sum(item.expected_candidate_count for item in wild_types) != expected_candidate_count
    ):
        raise HydroProcessingError("Hydro candidate counts are inconsistent")

    return HydroProcessingConfig(
        dataset_id="flip2",
        dataset_version=dataset_version,
        task_id="hydro",
        dataset_manifest_sha256=lower_hex_digest(
            string(config, "dataset_manifest_sha256"),
            field="dataset_manifest_sha256",
        ),
        source_csv_keys=source_keys,
        required_fields=required_fields,
        allowed_amino_acids=alphabet,
        variable_amino_acids=variable_alphabet,
        expected_candidate_count=expected_candidate_count,
        candidate_id_namespace=candidate_id_namespace,
        observable_feature_fields=observable_fields,
        hidden_label_field=hidden_label,
        label_type="continuous_source_score",
        label_direction="higher_is_better",
        label_semantics=_LABEL_SEMANTICS,
        label_units="source_score_physical_unit_unasserted",
        label_source_url=_LABEL_SOURCE_URL,
        high_value_threshold_policy=_THRESHOLD_POLICY,
        allowed_set_values=allowed_set_values,
        allowed_validation_values=allowed_validation_values,
        near_duplicate_method=_NEAR_DUPLICATE_METHOD,
        near_duplicate_threshold=threshold,
        expected_near_duplicate_pair_count=_integer(
            config,
            "expected_near_duplicate_pair_count",
            minimum=0,
        ),
        wild_types=wild_types,
        source_relationship=_SOURCE_RELATIONSHIP,
        publisher_reported_candidate_count=publisher_reported_candidate_count,
        observed_target_min=_canonical_config_decimal(config, "observed_target_min"),
        observed_target_median=_canonical_config_decimal(config, "observed_target_median"),
        observed_target_max=_canonical_config_decimal(config, "observed_target_max"),
        observed_distinct_target_count=_integer(
            config, "observed_distinct_target_count", minimum=1
        ),
    )


@dataclass(frozen=True)
class _HydroSource:
    targets_by_sequence: dict[str, str]
    wild_type_by_sequence: dict[str, str]
    metadata: tuple[tuple[str, str, str], ...]
    report: dict[str, object]


def _candidate_id(config: HydroProcessingConfig, wild_type_id: str, sequence: str) -> str:
    payload = (f"{config.candidate_id_namespace}\0{wild_type_id}\0{sequence}").encode()
    return hashlib.sha256(payload).hexdigest()


def _read_source(
    *,
    config: HydroProcessingConfig,
    source_key: str,
    path: Path,
) -> _HydroSource:
    by_length = {item.sequence_length: item for item in config.wild_types}
    targets: dict[str, str] = {}
    wild_types: dict[str, str] = {}
    metadata: list[tuple[str, str, str]] = []
    set_counts: Counter[str] = Counter()
    validation_counts: Counter[str] = Counter()
    wild_type_counts: Counter[str] = Counter()
    per_wild_type_set_counts: dict[str, Counter[str]] = {
        item.wild_type_id: Counter() for item in config.wild_types
    }
    per_wild_type_validation_counts: dict[str, Counter[str]] = {
        item.wild_type_id: Counter() for item in config.wild_types
    }
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != config.required_fields:
            raise HydroProcessingError(f"{source_key}: fields do not match the frozen schema")
        for row_number, row in enumerate(reader, start=2):
            if any(
                row.get(field) is None or not cast(str, row[field]).strip()
                for field in config.required_fields
            ):
                raise HydroProcessingError(f"{source_key}:{row_number}: missing value")
            sequence = cast(str, row["sequence"])
            if sequence != sequence.strip().upper():
                raise HydroProcessingError(
                    f"{source_key}:{row_number}: sequence is not canonical uppercase text"
                )
            spec = by_length.get(len(sequence))
            if spec is None or not set(sequence) <= set(config.allowed_amino_acids):
                raise HydroProcessingError(f"{source_key}:{row_number}: unknown WT mapping")
            variable_indexes = {position - 1 for position in spec.variable_positions}
            for index, (observed, reference) in enumerate(
                zip(sequence, spec.reference_sequence, strict=True)
            ):
                if index not in variable_indexes and observed != reference:
                    raise HydroProcessingError(
                        f"{source_key}:{row_number}: fixed WT scaffold differs"
                    )
                if index in variable_indexes and observed not in config.variable_amino_acids:
                    raise HydroProcessingError(
                        f"{source_key}:{row_number}: variable residue is not approved"
                    )
            if sequence in targets:
                raise HydroProcessingError(
                    f"{source_key}:{row_number}: duplicate sequence within one source file"
                )
            target = canonical_decimal(
                cast(str, row["target"]).strip(),
                source_key=source_key,
                row_number=row_number,
            )
            set_value = cast(str, row["set"]).strip()
            validation_value = cast(str, row["validation"]).strip()
            if set_value not in config.allowed_set_values:
                raise HydroProcessingError(f"{source_key}:{row_number}: unexpected set value")
            if validation_value not in config.allowed_validation_values:
                raise HydroProcessingError(
                    f"{source_key}:{row_number}: unexpected validation value"
                )
            targets[sequence] = target
            wild_types[sequence] = spec.wild_type_id
            metadata.append((sequence, set_value, validation_value))
            set_counts[set_value] += 1
            validation_counts[validation_value] += 1
            wild_type_counts[spec.wild_type_id] += 1
            per_wild_type_set_counts[spec.wild_type_id][set_value] += 1
            per_wild_type_validation_counts[spec.wild_type_id][validation_value] += 1
    return _HydroSource(
        targets_by_sequence=targets,
        wild_type_by_sequence=wild_types,
        metadata=tuple(metadata),
        report={
            "row_count": len(targets),
            "unique_sequence_count": len(targets),
            "missing_value_count": 0,
            "invalid_sequence_count": 0,
            "non_finite_target_count": 0,
            "within_file_duplicate_count": 0,
            "set_counts": dict(sorted(set_counts.items())),
            "validation_counts": dict(sorted(validation_counts.items())),
            "wild_type_counts": dict(sorted(wild_type_counts.items())),
            "per_wild_type": {
                wild_type_id: {
                    "row_count": wild_type_counts[wild_type_id],
                    "set_counts": dict(sorted(per_wild_type_set_counts[wild_type_id].items())),
                    "validation_counts": dict(
                        sorted(per_wild_type_validation_counts[wild_type_id].items())
                    ),
                }
                for wild_type_id in sorted(wild_type_counts)
            },
        },
    )


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def audit_hydro_sources(
    config: HydroProcessingConfig,
    raw_root: Path,
    *,
    raw_csv_artifacts: dict[str, tuple[str, int]] | None = None,
) -> HydroAudit:
    """Audit six Hydro source views and build one explicit multi-WT universe."""
    sources: dict[str, _HydroSource] = {}
    source_reports: dict[str, object] = {}
    for source_key in config.source_csv_keys:
        path = raw_root.joinpath(*PurePosixPath(source_key).parts)
        if raw_csv_artifacts is not None:
            expected = raw_csv_artifacts.get(source_key)
            if expected is None:
                raise HydroProcessingError(
                    f"missing Dataset Manifest artifact for source file: {source_key}"
                )
            if not path.is_file():
                raise HydroProcessingError(f"missing source file: {source_key}")
            verify_raw_file(path, source_key, expected)
        source = _read_source(config=config, source_key=source_key, path=path)
        sources[source_key] = source
        source_reports[source_key] = source.report

    first_key = config.source_csv_keys[0]
    canonical = sources[first_key]
    canonical_sequences = set(canonical.targets_by_sequence)
    for source_key, source in sources.items():
        if set(source.targets_by_sequence) != canonical_sequences:
            raise HydroProcessingError(f"{source_key}: candidate universe differs from {first_key}")
        if source.targets_by_sequence != canonical.targets_by_sequence:
            raise HydroProcessingError(f"{source_key}: hidden truth conflicts with {first_key}")
        if source.wild_type_by_sequence != canonical.wild_type_by_sequence:
            raise HydroProcessingError(f"{source_key}: WT mapping conflicts with {first_key}")
    if len(canonical_sequences) != config.expected_candidate_count:
        raise HydroProcessingError("candidate count differs from processing config")

    wild_type_by_id = {item.wild_type_id: item for item in config.wild_types}
    for source_key, source in sources.items():
        source_name = PurePosixPath(source_key).name
        if not source_name.startswith("to_"):
            continue
        held_out_id = source_name.removeprefix("to_").removesuffix(".csv.gz")
        for sequence, set_value, validation_value in source.metadata:
            is_held_out = canonical.wild_type_by_sequence[sequence] == held_out_id
            expected_set = "test" if is_held_out else "train"
            if set_value != expected_set or (is_held_out and validation_value != "False"):
                raise HydroProcessingError(f"{source_key}: wild-type holdout mapping differs")

    mutation_source_key = next(
        key for key in config.source_csv_keys if key.endswith("three_to_many.csv.gz")
    )
    mutation_source = sources[mutation_source_key]
    for sequence, set_value, validation_value in mutation_source.metadata:
        spec = wild_type_by_id[canonical.wild_type_by_sequence[sequence]]
        mutation_count = sum(
            left != right
            for left, right in zip(
                sequence,
                spec.reference_sequence,
                strict=True,
            )
        )
        is_held_out = mutation_count > 3
        expected_set = "test" if is_held_out else "train"
        if set_value != expected_set or (is_held_out and validation_value != "False"):
            raise HydroProcessingError(
                f"{mutation_source_key}: mutation-count holdout mapping differs"
            )

    sequences_by_wild_type: dict[str, list[str]] = {
        item.wild_type_id: [] for item in config.wild_types
    }
    for sequence, wild_type_id in canonical.wild_type_by_sequence.items():
        sequences_by_wild_type[wild_type_id].append(sequence)

    mutation_reports: dict[str, object] = {}
    near_duplicate_reports: dict[str, object] = {}
    total_near_duplicates = 0
    for wild_type_id in sorted(wild_type_by_id):
        spec = wild_type_by_id[wild_type_id]
        sequences = tuple(sorted(sequences_by_wild_type[wild_type_id]))
        if len(sequences) != spec.expected_candidate_count:
            raise HydroProcessingError(
                f"{wild_type_id}: candidate count differs from processing config"
            )
        reference_present = spec.reference_sequence in sequences
        if reference_present is not spec.reference_candidate_present:
            raise HydroProcessingError(f"{wild_type_id}: reference candidate presence differs")
        observed_variable_positions = tuple(
            index + 1
            for index in range(spec.sequence_length)
            if any(sequence[index] != spec.reference_sequence[index] for sequence in sequences)
        )
        if observed_variable_positions != spec.variable_positions:
            raise HydroProcessingError(f"{wild_type_id}: observed variable positions differ")
        mutation_counts = Counter(
            sum(
                left != right for left, right in zip(sequence, spec.reference_sequence, strict=True)
            )
            for sequence in sequences
        )
        if (
            min(mutation_counts) != spec.expected_mutation_count_min
            or max(mutation_counts) != spec.expected_mutation_count_max
        ):
            raise HydroProcessingError(f"{wild_type_id}: mutation count range differs")
        near_duplicates = near_duplicate_counts(
            sequences,
            spec.reference_sequence,
            config.near_duplicate_threshold,
        )
        if near_duplicates != {"1": spec.expected_near_duplicate_pair_count}:
            raise HydroProcessingError(f"{wild_type_id}: near-duplicate count differs")
        total_near_duplicates += spec.expected_near_duplicate_pair_count
        mutation_reports[wild_type_id] = {
            str(count): occurrences for count, occurrences in sorted(mutation_counts.items())
        }
        near_duplicate_reports[wild_type_id] = near_duplicates
    if total_near_duplicates != config.expected_near_duplicate_pair_count:
        raise HydroProcessingError("total near-duplicate count differs")
    near_duplicate_reports["total"] = {"1": total_near_duplicates}

    targets = [Decimal(value) for value in canonical.targets_by_sequence.values()]
    observed_target_facts = (
        format(min(targets).normalize(), "f"),
        format(_median(targets).normalize(), "f"),
        format(max(targets).normalize(), "f"),
        len(set(targets)),
    )
    expected_target_facts = (
        config.observed_target_min,
        config.observed_target_median,
        config.observed_target_max,
        config.observed_distinct_target_count,
    )
    if observed_target_facts != expected_target_facts:
        raise HydroProcessingError("observed target facts differ from processing config")

    fitness_source_key = next(
        key for key in config.source_csv_keys if key.endswith("low_to_high.csv.gz")
    )
    fitness_source = sources[fitness_source_key]
    median = Decimal(config.observed_target_median)
    for sequence, set_value, validation_value in fitness_source.metadata:
        is_high = Decimal(canonical.targets_by_sequence[sequence]) >= median
        expected_set = "test" if is_high else "train"
        if set_value != expected_set or (is_high and validation_value != "False"):
            raise HydroProcessingError(f"{fitness_source_key}: fitness holdout mapping differs")

    candidates = tuple(
        sorted(
            (
                Candidate(
                    candidate_id=_candidate_id(
                        config,
                        canonical.wild_type_by_sequence[sequence],
                        sequence,
                    ),
                    sequence=sequence,
                    target=canonical.targets_by_sequence[sequence],
                    observable_metadata=(
                        (
                            "wild_type_id",
                            canonical.wild_type_by_sequence[sequence],
                        ),
                    ),
                )
                for sequence in canonical_sequences
            ),
            key=lambda item: item.candidate_id,
        )
    )
    if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise HydroProcessingError("candidate ID collision")
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
                for source_key, source in sources.items()
                for sequence, set_value, validation_value in source.metadata
            ),
            key=lambda item: (item.candidate_id, item.source_key),
        )
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
        "all_source_targets_identical": True,
        "cross_source_target_conflict_count": 0,
        "source_relationship": config.source_relationship,
        "wild_type_candidate_counts": {
            wild_type_id: len(sequences_by_wild_type[wild_type_id])
            for wild_type_id in sorted(sequences_by_wild_type)
        },
        "wild_type_reference_facts": {
            item.wild_type_id: {
                "sequence_length": item.sequence_length,
                "reference_sequence_sha256": item.reference_sequence_sha256,
                "reference_source_url": item.reference_source_url,
                "reference_candidate_present": item.reference_candidate_present,
                "variable_positions": list(item.variable_positions),
            }
            for item in sorted(config.wild_types, key=lambda item: item.wild_type_id)
        },
        "mutation_count_method": "within_wild_type_full_length_hamming_distance_to_reference",
        "mutation_count_from_reference": mutation_reports,
        "target_type": config.label_type,
        "target_semantics": config.label_semantics,
        "label_units": config.label_units,
        "label_source_url": config.label_source_url,
        "label_direction": config.label_direction,
        "target_min": config.observed_target_min,
        "target_median": config.observed_target_median,
        "target_max": config.observed_target_max,
        "distinct_target_count": config.observed_distinct_target_count,
        "high_value_threshold_policy": config.high_value_threshold_policy,
        "near_duplicate_method": config.near_duplicate_method,
        "near_duplicate_threshold": config.near_duplicate_threshold,
        "near_duplicate_pair_counts": near_duplicate_reports,
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "publisher_roles_are_audit_only": True,
        "target_derived_source_keys": ["hydro/low_to_high.csv.gz"],
        "upstream_split_metadata_is_not_frozen": True,
    }
    return HydroAudit(
        candidates=candidates,
        memberships=memberships,
        report=report,
    )


def build_hydro_processing_bundle(
    *,
    config: HydroProcessingConfig,
    config_bytes: bytes,
    dataset_manifest_bytes: bytes,
    raw_root: Path,
    git_commit: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Build deterministic Hydro artifacts and a Processing Manifest without publication."""
    identity = prepare_processing_identity(
        config=config,
        config_bytes=config_bytes,
        dataset_manifest_bytes=dataset_manifest_bytes,
        git_commit_value=git_commit,
    )
    audit = audit_hydro_sources(
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
                "The accepted FLIP2 artifact establishes a continuous stability fitness source "
                "score and higher-is-better direction, but no physical unit is asserted."
            ),
            (
                "Publisher random, mutation-count, fitness-range, and wild-type holdout roles "
                "are retained only as audit metadata and are not benchmark membership."
            ),
            (
                "Near-duplicate comparison is defined only within each wild-type group; "
                "cross-WT sequence distances are not treated as comparable."
            ),
            (
                "Split grouping, leakage controls, held-out identity, replay, models, benchmark "
                "evidence, and scientific gates remain outside this processing revision."
            ),
        ],
    )
