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

from pcdb.datasets.amylase import (
    AmylaseProcessingError,
    audit_amylase_sources,
    build_amylase_processing_bundle,
    load_amylase_processing_config,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
SOURCE_KEYS = (
    "amylase/by_mutation.csv.gz",
    "amylase/close_to_far.csv.gz",
    "amylase/far_to_close.csv.gz",
    "amylase/one_to_many.csv.gz",
    "amylase/random_split.csv.gz",
)


def _placeholder_manifest_bytes() -> bytes:
    return b'{"fixture":"dataset-manifest"}\n'


def _config_payload() -> dict[str, object]:
    return {
        "config_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "fixture-v1",
        "task_id": "amylase",
        "dataset_manifest_sha256": hashlib.sha256(_placeholder_manifest_bytes()).hexdigest(),
        "source_csv_keys": list(SOURCE_KEYS),
        "required_fields": ["sequence", "target", "set", "validation"],
        "allowed_amino_acids": "ACDE",
        "sequence_length": 4,
        "expected_candidate_count": 3,
        "candidate_id_algorithm": "sha256",
        "candidate_id_namespace": "flip2:fixture-v1:amylase",
        "observable_feature_fields": ["sequence"],
        "hidden_label_field": "target",
        "label_direction": "higher_is_better",
        "label_semantics": "Public fixture activity.",
        "label_source_url": "https://doi.org/10.1016/j.csbj.2024.09.007",
        "missing_value_policy": "error",
        "allowed_set_values": ["test", "train"],
        "allowed_validation_values": ["False", "True"],
        "within_file_duplicate_policy": "error",
        "cross_file_duplicate_policy": "aggregate_if_target_matches",
        "conflicting_target_policy": "error",
        "near_duplicate_method": "full_length_hamming_distance",
        "near_duplicate_threshold": 1,
        "near_duplicate_policy": "allowed_pending_split_manifest",
    }


def _write_config(
    tmp_path: Path,
    payload: dict[str, object] | None = None,
    *,
    manifest_bytes: bytes | None = None,
) -> Path:
    path = tmp_path / "processing-config.json"
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
        {"sequence": "AAAA", "target": "0.10", "set": "train", "validation": "False"},
        {"sequence": "AAAC", "target": "0.20", "set": "train", "validation": "True"},
        {"sequence": "AACC", "target": "0.30", "set": "test", "validation": "False"},
    ]


def _write_source(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("sequence", "target", "set", "validation"),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_raw_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "raw"
    for index, source_key in enumerate(SOURCE_KEYS):
        rows = _source_rows()
        if index % 2:
            rows.reverse()
        for row in rows:
            row["set"] = "test" if index % 2 else row["set"]
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
        "task_id": "amylase",
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
    config_bytes = config_path.read_bytes()
    config = load_amylase_processing_config(config_path)
    payloads, manifest = build_amylase_processing_bundle(
        config=config,
        config_bytes=config_bytes,
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
    assert len(memberships) == 15
    assert set(candidates[0]) == {"candidate_id", "sequence"}
    assert set(hidden_truth[0]) == {"candidate_id", "target"}
    assert {row["candidate_id"] for row in candidates} == {
        row["candidate_id"] for row in hidden_truth
    }
    assert audit["candidate_count"] == 3
    assert audit["all_source_candidate_sets_identical"] is True
    assert audit["near_duplicate_pair_counts"] == {"1": 2}
    assert "target_min" not in audit
    assert "target_max" not in audit
    assert manifest["observable_feature_fields"] == ["sequence"]
    assert manifest["hidden_label_field"] == "target"
    assert manifest["label_direction"] == "higher_is_better"
    output_files = cast(list[dict[str, object]], manifest["output_files"])
    assert all("row_count" in item for item in output_files)
    row_counts = {
        Path(cast(str, item["object_key"])).name: item["row_count"] for item in output_files
    }
    assert row_counts == {
        "candidates.csv": 3,
        "hidden-truth.csv": 3,
        "processing-audit.json": 1,
        "upstream-split-metadata.csv": 15,
    }

    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/processing-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)

    repeated_payloads, repeated_manifest = build_amylase_processing_bundle(
        config=config,
        config_bytes=config_bytes,
        dataset_manifest_bytes=manifest_bytes,
        raw_root=raw_root,
        git_commit="a" * 40,
    )
    assert repeated_payloads == payloads
    assert repeated_manifest == manifest


def test_candidate_ids_and_outputs_are_invariant_to_row_order(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_amylase_processing_config(config_path)
    raw_root = _write_raw_bundle(tmp_path)
    first_audit = audit_amylase_sources(config, raw_root)
    for source_key in SOURCE_KEYS:
        source_path = raw_root / source_key
        with gzip.open(source_path, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        _write_source(source_path, list(reversed(rows)))
    second_audit = audit_amylase_sources(config, raw_root)

    assert first_audit == second_audit


def test_processing_bundle_fails_closed_when_raw_checksum_differs(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)
    _write_source(raw_root / SOURCE_KEYS[0], list(reversed(_source_rows())))

    with pytest.raises(AmylaseProcessingError, match="does not match Dataset Manifest"):
        build_amylase_processing_bundle(
            config=load_amylase_processing_config(config_path),
            config_bytes=config_path.read_bytes(),
            dataset_manifest_bytes=manifest_bytes,
            raw_root=raw_root,
            git_commit="b" * 40,
        )


def test_processing_bundle_requires_full_git_commit(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)

    with pytest.raises(AmylaseProcessingError, match="full lowercase 40-character commit"):
        build_amylase_processing_bundle(
            config=load_amylase_processing_config(config_path),
            config_bytes=config_path.read_bytes(),
            dataset_manifest_bytes=manifest_bytes,
            raw_root=raw_root,
            git_commit="b" * 39,
        )


def test_processing_bundle_rejects_dataset_manifest_identity_mismatch(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest = json.loads(_dataset_manifest_bytes(raw_root))
    manifest["task_id"] = "not-amylase"
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)

    with pytest.raises(AmylaseProcessingError, match="task_id does not match"):
        build_amylase_processing_bundle(
            config=load_amylase_processing_config(config_path),
            config_bytes=config_path.read_bytes(),
            dataset_manifest_bytes=manifest_bytes,
            raw_root=raw_root,
            git_commit="b" * 40,
        )


def test_processing_fails_closed_on_cross_source_target_conflict(tmp_path: Path) -> None:
    config = load_amylase_processing_config(_write_config(tmp_path))
    raw_root = _write_raw_bundle(tmp_path)
    conflicting = _source_rows()
    conflicting[0]["target"] = "9.9"
    _write_source(raw_root / SOURCE_KEYS[-1], conflicting)

    with pytest.raises(AmylaseProcessingError, match="hidden truth conflicts"):
        audit_amylase_sources(config, raw_root)


def test_processing_fails_closed_when_candidate_universe_differs(tmp_path: Path) -> None:
    config = load_amylase_processing_config(_write_config(tmp_path))
    raw_root = _write_raw_bundle(tmp_path)
    _write_source(raw_root / SOURCE_KEYS[-1], _source_rows()[:-1])

    with pytest.raises(AmylaseProcessingError, match="candidate universe differs"):
        audit_amylase_sources(config, raw_root)


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
    config = load_amylase_processing_config(_write_config(tmp_path))
    raw_root = _write_raw_bundle(tmp_path)
    rows = _source_rows()
    rows[0].update(mutation)
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(AmylaseProcessingError, match=message):
        audit_amylase_sources(config, raw_root)


def test_processing_fails_closed_on_duplicate_within_source(tmp_path: Path) -> None:
    config = load_amylase_processing_config(_write_config(tmp_path))
    raw_root = _write_raw_bundle(tmp_path)
    rows = _source_rows()
    rows.append(rows[0].copy())
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(AmylaseProcessingError, match="duplicate sequence"):
        audit_amylase_sources(config, raw_root)


def test_repository_processing_config_is_bound_to_accepted_manifest() -> None:
    config_path = (
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/amylase-processing-v1.json"
    )
    manifest_path = (
        REPOSITORY_ROOT
        / "manifests/datasets/flip2/zenodo-18433203-v3/amylase/dataset-manifest.json"
    )
    config = load_amylase_processing_config(config_path)

    assert config.dataset_id == "flip2"
    assert config.task_id == "amylase"
    assert config.expected_candidate_count == 3_706
    assert config.sequence_length == 425
    assert config.label_direction == "higher_is_better"
    assert config.dataset_manifest_sha256 == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
