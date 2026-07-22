from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.ired import (
    IredProcessingError,
    audit_ired_sources,
    build_ired_processing_bundle,
    load_ired_processing_config,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
SOURCE_KEYS = ("ired/random.csv.gz", "ired/two_to_many.csv.gz")


def _placeholder_manifest_bytes() -> bytes:
    return b'{"fixture":"dataset-manifest"}\n'


def _config_payload() -> dict[str, object]:
    return {
        "config_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "fixture-v1",
        "task_id": "ired",
        "dataset_manifest_sha256": hashlib.sha256(_placeholder_manifest_bytes()).hexdigest(),
        "source_csv_keys": list(SOURCE_KEYS),
        "required_fields": ["sequence", "set", "validation", "target"],
        "allowed_amino_acids": "ACDE",
        "sequence_length": 4,
        "expected_candidate_count": 3,
        "candidate_id_algorithm": "sha256",
        "candidate_id_namespace": "flip2:fixture-v1:ired",
        "observable_feature_fields": ["sequence"],
        "hidden_label_field": "target",
        "label_direction": "higher_is_better",
        "label_semantics": "Fixture log-enrichment over wild type.",
        "label_units": "Dimensionless fixture log-enrichment.",
        "label_source_url": "https://zenodo.org/records/11357236",
        "high_value_meaning": "Higher is better; no numeric threshold is frozen.",
        "missing_value_policy": "error",
        "allowed_set_values": ["test", "train"],
        "allowed_validation_values": ["False", "True"],
        "within_file_duplicate_policy": "error",
        "cross_file_duplicate_policy": "aggregate_if_target_matches",
        "conflicting_target_policy": "error",
        "near_duplicate_method": "full_length_hamming_distance",
        "near_duplicate_threshold": 1,
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "expected_near_duplicate_pair_count": 2,
        "reference_sequence": "AAAC",
        "reference_resolution": "Fixture WT cross-checked with a unique modal consensus.",
        "reference_source_url": "https://zenodo.org/records/11357236",
        "mutation_count_method": "full_length_hamming_distance_to_reference",
        "expected_mutation_count_min": 0,
        "expected_mutation_count_max": 1,
        "source_relationship": (
            "same_candidate_universe_and_hidden_truth_with_alternate_split_metadata"
        ),
        "publisher_reported_candidate_count": 10,
        "upstream_reconciliation_record_id": 11357236,
        "upstream_reconciliation_record_version": (
            "Version-specific Zenodo record 11357236; metadata.version is not declared."
        ),
        "upstream_reconciliation_record_url": "https://zenodo.org/records/11357236",
        "upstream_reconciliation_role": ("provenance_reconciliation_only_not_processing_input"),
        "upstream_active_filename": "srired_active_data.csv",
        "upstream_active_publisher_md5": "0" * 32,
        "upstream_active_size_bytes": 10,
        "upstream_active_row_count": 5,
        "upstream_inactive_filename": "srired_inactive_data.csv",
        "upstream_inactive_publisher_md5": "1" * 32,
        "upstream_inactive_size_bytes": 10,
        "upstream_inactive_row_count": 4,
        "upstream_candidate_count_limitation": (
            "Fixture publisher count differs from deposited and processed counts."
        ),
    }


def _write_config(
    tmp_path: Path,
    payload: dict[str, object] | None = None,
    *,
    manifest_bytes: bytes | None = None,
    name: str = "processing-config.json",
) -> Path:
    path = tmp_path / name
    config_payload = payload or _config_payload()
    if manifest_bytes is not None:
        config_payload = {
            **config_payload,
            "dataset_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        }
    path.write_text(json.dumps(config_payload, indent=2) + "\n", encoding="utf-8")
    return path


def _source_rows() -> list[dict[str, str]]:
    return [
        {"sequence": "AAAA", "set": "train", "validation": "False", "target": "0.10"},
        {"sequence": "AAAC", "set": "train", "validation": "True", "target": "0.20"},
        {"sequence": "AACC", "set": "test", "validation": "False", "target": "0.30"},
    ]


def _write_source(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("sequence", "set", "validation", "target"),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_raw_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "raw"
    for index, source_key in enumerate(SOURCE_KEYS):
        rows = _source_rows()
        if index:
            rows.reverse()
            for row in rows:
                row["set"] = "test" if row["set"] == "train" else "train"
                row["validation"] = "False"
        _write_source(root / source_key, rows)
    return root


def _dataset_manifest_bytes(raw_root: Path) -> bytes:
    raw_files = []
    for source_key in SOURCE_KEYS:
        payload = (raw_root / source_key).read_bytes()
        raw_files.append(
            {
                "object_key": f"raw/flip2/fixture-v1/{source_key}",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
    manifest = {
        "schema_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "fixture-v1",
        "task_id": "ired",
        "raw_files": raw_files,
    }
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _csv_rows(payload: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))


def test_processing_builds_one_candidate_universe_and_separates_hidden_truth(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)
    config = load_ired_processing_config(config_path)
    payloads, manifest = build_ired_processing_bundle(
        config=config,
        config_bytes=config_path.read_bytes(),
        dataset_manifest_bytes=manifest_bytes,
        raw_root=raw_root,
        git_commit="a" * 40,
    )

    by_name = {Path(key).name: value for key, value in payloads.items()}
    candidates = _csv_rows(by_name["candidates.csv"])
    hidden_truth = _csv_rows(by_name["hidden-truth.csv"])
    memberships = _csv_rows(by_name["upstream-split-metadata.csv"])
    audit = cast(dict[str, object], json.loads(by_name["processing-audit.json"]))

    assert len(candidates) == len(hidden_truth) == 3
    assert len(memberships) == 6
    assert set(candidates[0]) == {"candidate_id", "sequence"}
    assert set(hidden_truth[0]) == {"candidate_id", "target"}
    assert {row["candidate_id"] for row in candidates} == {
        row["candidate_id"] for row in hidden_truth
    }
    assert audit["candidate_count"] == 3
    assert audit["all_source_candidate_sets_identical"] is True
    assert audit["all_source_targets_identical"] is True
    assert audit["near_duplicate_pair_counts"] == {"1": 2}
    assert audit["reference_candidate_present"] is True
    assert audit["publisher_claim_minus_upstream_deposit_rows"] == 1
    assert audit["upstream_reconciliation"] == {
        "record_id": 11357236,
        "record_version": (
            "Version-specific Zenodo record 11357236; metadata.version is not declared."
        ),
        "record_url": "https://zenodo.org/records/11357236",
        "role": "provenance_reconciliation_only_not_processing_input",
        "processing_payload_input": False,
        "files": [
            {
                "role": "active",
                "filename": "srired_active_data.csv",
                "publisher_md5": "0" * 32,
                "size_bytes": 10,
                "observed_row_count": 5,
            },
            {
                "role": "inactive",
                "filename": "srired_inactive_data.csv",
                "publisher_md5": "1" * 32,
                "size_bytes": 10,
                "observed_row_count": 4,
            },
        ],
    }
    assert "target_min" not in audit
    assert "target_max" not in audit
    assert manifest["observable_feature_fields"] == ["sequence"]
    assert manifest["hidden_label_field"] == "target"
    assert manifest["label_direction"] == "higher_is_better"

    output_files = cast(list[dict[str, object]], manifest["output_files"])
    assert all("row_count" in item for item in output_files)
    assert {
        Path(cast(str, item["object_key"])).name: item["row_count"] for item in output_files
    } == {
        "candidates.csv": 3,
        "hidden-truth.csv": 3,
        "processing-audit.json": 1,
        "upstream-split-metadata.csv": 6,
    }

    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/processing-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)

    repeated_payloads, repeated_manifest = build_ired_processing_bundle(
        config=config,
        config_bytes=config_path.read_bytes(),
        dataset_manifest_bytes=manifest_bytes,
        raw_root=raw_root,
        git_commit="a" * 40,
    )
    assert repeated_payloads == payloads
    assert repeated_manifest == manifest


def test_candidate_ids_are_invariant_to_row_and_source_enumeration(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    first_config = load_ired_processing_config(_write_config(tmp_path, name="first.json"))
    first_audit = audit_ired_sources(first_config, raw_root)

    for source_key in SOURCE_KEYS:
        path = raw_root / source_key
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        _write_source(path, list(reversed(rows)))
    reversed_payload = {**_config_payload(), "source_csv_keys": list(reversed(SOURCE_KEYS))}
    second_config = load_ired_processing_config(
        _write_config(tmp_path, reversed_payload, name="second.json")
    )
    second_audit = audit_ired_sources(second_config, raw_root)

    assert first_audit.candidates == second_audit.candidates
    assert first_audit.memberships == second_audit.memberships


def test_processing_bundle_fails_closed_when_raw_checksum_differs(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)
    _write_source(raw_root / SOURCE_KEYS[0], list(reversed(_source_rows())))

    with pytest.raises(IredProcessingError, match="does not match Dataset Manifest"):
        build_ired_processing_bundle(
            config=load_ired_processing_config(config_path),
            config_bytes=config_path.read_bytes(),
            dataset_manifest_bytes=manifest_bytes,
            raw_root=raw_root,
            git_commit="b" * 40,
        )


def test_processing_fails_closed_on_cross_source_target_conflict(tmp_path: Path) -> None:
    config = load_ired_processing_config(_write_config(tmp_path))
    raw_root = _write_raw_bundle(tmp_path)
    conflicting = _source_rows()
    conflicting[0]["target"] = "9.9"
    _write_source(raw_root / SOURCE_KEYS[-1], conflicting)

    with pytest.raises(IredProcessingError, match="hidden truth conflicts"):
        audit_ired_sources(config, raw_root)


def test_processing_fails_closed_when_candidate_universe_differs(tmp_path: Path) -> None:
    config = load_ired_processing_config(_write_config(tmp_path))
    raw_root = _write_raw_bundle(tmp_path)
    _write_source(raw_root / SOURCE_KEYS[-1], _source_rows()[:-1])

    with pytest.raises(IredProcessingError, match="candidate universe differs"):
        audit_ired_sources(config, raw_root)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"sequence": ""}, "missing value"),
        ({"sequence": "AAAX"}, "invalid sequence"),
        ({"target": "NaN"}, "target must be finite"),
        ({"set": "held-out"}, "unexpected set value"),
    ],
)
def test_processing_fails_closed_on_invalid_source_values(
    tmp_path: Path, mutation: dict[str, str], message: str
) -> None:
    config = load_ired_processing_config(_write_config(tmp_path))
    raw_root = _write_raw_bundle(tmp_path)
    rows = _source_rows()
    rows[0].update(mutation)
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(IredProcessingError, match=message):
        audit_ired_sources(config, raw_root)


def test_processing_fails_closed_on_duplicate_within_source(tmp_path: Path) -> None:
    config = load_ired_processing_config(_write_config(tmp_path))
    raw_root = _write_raw_bundle(tmp_path)
    rows = _source_rows()
    rows.append(rows[0].copy())
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(IredProcessingError, match="duplicate sequence"):
        audit_ired_sources(config, raw_root)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"reference_sequence": "AAAA"}, "observed reference differs"),
        ({"expected_mutation_count_max": 0}, "mutation count range differs"),
        ({"expected_near_duplicate_pair_count": 1}, "near-duplicate count differs"),
    ],
)
def test_processing_fails_closed_when_frozen_audit_facts_drift(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    payload = {**_config_payload(), **mutation}
    config = load_ired_processing_config(_write_config(tmp_path, payload))

    with pytest.raises(IredProcessingError, match=message):
        audit_ired_sources(config, _write_raw_bundle(tmp_path))


def test_repository_processing_config_is_bound_to_accepted_manifest() -> None:
    config_path = (
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/ired-processing-v1.json"
    )
    manifest_path = (
        REPOSITORY_ROOT / "manifests/datasets/flip2/zenodo-18433203-v3/ired/dataset-manifest.json"
    )
    config = load_ired_processing_config(config_path)

    assert config.dataset_id == "flip2"
    assert config.task_id == "ired"
    assert config.expected_candidate_count == 8_586
    assert config.sequence_length == 290
    assert config.expected_near_duplicate_pair_count == 9_279
    assert config.publisher_reported_candidate_count == 17_143
    assert config.upstream_reconciliation_record_id == 11357236
    assert config.upstream_reconciliation_role == (
        "provenance_reconciliation_only_not_processing_input"
    )
    assert config.upstream_active_filename == "srired_active_data.csv"
    assert config.upstream_active_publisher_md5 == "0e58f694bc1eb3b4375378e5c04b0ad3"
    assert config.upstream_active_size_bytes == 4_093_522
    assert config.upstream_inactive_filename == "srired_inactive_data.csv"
    assert config.upstream_inactive_publisher_md5 == "8a1f51dd84d6ddf8f5b9d428e318fa24"
    assert config.upstream_inactive_size_bytes == 1_612_008
    assert config.upstream_active_row_count + config.upstream_inactive_row_count == 16_278
    assert config.label_direction == "higher_is_better"
    assert hashlib.sha256(config.reference_sequence.encode()).hexdigest() == (
        "27fbaa950b2c68b73f7595a2a53e86a8ab627118640b8e181a08b57a324e2fdb"
    )
    assert config.dataset_manifest_sha256 == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
