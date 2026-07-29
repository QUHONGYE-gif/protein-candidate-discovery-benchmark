from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.hydro import (
    audit_hydro_sources,
    build_hydro_processing_bundle,
    load_hydro_processing_config,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
WILD_TYPES = {
    "P00001": "FFFI",
    "P00002": "CCCCI",
    "P00003": "GGGGGL",
}
SOURCE_KEYS = (
    "hydro/random_split.csv.gz",
    "hydro/three_to_many.csv.gz",
    "hydro/low_to_high.csv.gz",
    "hydro/to_P00001.csv.gz",
    "hydro/to_P00002.csv.gz",
    "hydro/to_P00003.csv.gz",
)


def _source_rows() -> list[dict[str, str]]:
    return [
        {"sequence": "FFFI", "target": "0", "set": "train", "validation": "False"},
        {"sequence": "FFFL", "target": "-1", "set": "test", "validation": "False"},
        {"sequence": "CCCCI", "target": "0.5", "set": "train", "validation": "True"},
        {"sequence": "CCCCL", "target": "-0.5", "set": "test", "validation": "False"},
        {"sequence": "GGGGGL", "target": "1", "set": "train", "validation": "False"},
        {"sequence": "GGGGGM", "target": "-2", "set": "test", "validation": "False"},
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
        if index:
            rows.reverse()
        if source_key.endswith("three_to_many.csv.gz"):
            for row in rows:
                row["set"] = "train"
        elif source_key.endswith("low_to_high.csv.gz"):
            for row in rows:
                is_high = float(row["target"]) >= -0.25
                row["set"] = "test" if is_high else "train"
                if is_high:
                    row["validation"] = "False"
        elif "/to_" in source_key:
            held_out_id = Path(source_key).name.removeprefix("to_").removesuffix(".csv.gz")
            held_out_length = len(WILD_TYPES[held_out_id])
            for row in rows:
                is_held_out = len(row["sequence"]) == held_out_length
                row["set"] = "test" if is_held_out else "train"
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
        "task_id": "hydro",
        "raw_files": raw_files,
    }
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _config_payload(manifest_bytes: bytes) -> dict[str, object]:
    return {
        "config_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "fixture-v1",
        "task_id": "hydro",
        "dataset_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_csv_keys": list(SOURCE_KEYS),
        "required_fields": ["sequence", "target", "set", "validation"],
        "allowed_amino_acids": "ACDEFGHIKLMNPQRSTVWY",
        "variable_amino_acids": "FILMV",
        "expected_candidate_count": 6,
        "candidate_id_algorithm": "sha256",
        "candidate_id_namespace": "flip2:fixture-v1:hydro",
        "candidate_id_preimage": "namespace_null_wild_type_id_null_sequence",
        "observable_feature_fields": ["wild_type_id", "sequence"],
        "hidden_label_field": "target",
        "label_type": "continuous_source_score",
        "label_direction": "higher_is_better",
        "label_semantics": "Continuous FLIP2 hydrophobic-core stability fitness source score.",
        "label_units": "source_score_physical_unit_unasserted",
        "label_source_url": "https://flip.protein.properties/",
        "high_value_threshold_policy": "experiment_config_only_not_frozen_by_processing",
        "missing_value_policy": "error",
        "allowed_set_values": ["test", "train"],
        "allowed_validation_values": ["False", "True"],
        "within_file_duplicate_policy": "error",
        "cross_file_duplicate_policy": "aggregate_if_target_matches",
        "conflicting_target_policy": "error",
        "near_duplicate_method": "within_wild_type_full_length_hamming_distance",
        "near_duplicate_threshold": 1,
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "expected_near_duplicate_pair_count": 3,
        "wild_types": [
            {
                "wild_type_id": wild_type_id,
                "sequence_length": len(reference),
                "reference_sequence": reference,
                "reference_sequence_sha256": hashlib.sha256(reference.encode()).hexdigest(),
                "reference_source_url": f"https://rest.uniprot.org/uniprotkb/{wild_type_id}.fasta",
                "reference_candidate_present": True,
                "expected_candidate_count": 2,
                "variable_positions": [len(reference)],
                "expected_mutation_count_min": 0,
                "expected_mutation_count_max": 1,
                "expected_near_duplicate_pair_count": 1,
            }
            for wild_type_id, reference in WILD_TYPES.items()
        ],
        "source_relationship": (
            "same_candidate_universe_and_hidden_truth_with_alternate_split_metadata"
        ),
        "publisher_reported_candidate_count": 6,
        "observed_target_min": "-2",
        "observed_target_median": "-0.25",
        "observed_target_max": "1",
        "observed_distinct_target_count": 6,
    }


def _write_config(
    tmp_path: Path,
    manifest_bytes: bytes,
    payload: dict[str, object] | None = None,
    *,
    name: str = "hydro-processing.json",
) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(payload or _config_payload(manifest_bytes), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _csv_rows(payload: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))


def test_processing_builds_multi_wild_type_artifacts_with_hidden_truth_separated(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    config_path = _write_config(tmp_path, manifest_bytes)
    config = load_hydro_processing_config(config_path)

    payloads, manifest = build_hydro_processing_bundle(
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

    assert len(candidates) == len(hidden_truth) == 6
    assert len(memberships) == 36
    assert set(candidates[0]) == {"candidate_id", "wild_type_id", "sequence"}
    assert set(hidden_truth[0]) == {"candidate_id", "target"}
    assert all("target" not in row for row in candidates)
    assert {row["wild_type_id"] for row in candidates} == set(WILD_TYPES)
    assert {row["candidate_id"] for row in candidates} == {
        row["candidate_id"] for row in hidden_truth
    }

    expected_id = hashlib.sha256(b"flip2:fixture-v1:hydro\0P00001\0FFFI").hexdigest()
    assert any(row["candidate_id"] == expected_id for row in candidates)
    assert audit["candidate_count"] == 6
    assert audit["wild_type_candidate_counts"] == {
        "P00001": 2,
        "P00002": 2,
        "P00003": 2,
    }
    assert audit["all_source_candidate_sets_identical"] is True
    assert audit["all_source_targets_identical"] is True
    assert audit["near_duplicate_pair_counts"] == {
        "P00001": {"1": 1},
        "P00002": {"1": 1},
        "P00003": {"1": 1},
        "total": {"1": 3},
    }
    source_files = cast(dict[str, dict[str, object]], audit["source_files"])
    random_per_wild_type = cast(
        dict[str, dict[str, object]],
        source_files["hydro/random_split.csv.gz"]["per_wild_type"],
    )
    assert random_per_wild_type["P00001"] == {
        "row_count": 2,
        "set_counts": {"test": 1, "train": 1},
        "validation_counts": {"False": 2},
    }
    assert "candidate_targets" not in audit

    assert manifest["observable_feature_fields"] == ["wild_type_id", "sequence"]
    assert manifest["hidden_label_field"] == "target"
    assert manifest["label_direction"] == "higher_is_better"
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/processing-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)


def test_repository_processing_config_freezes_the_approved_hydro_facts() -> None:
    config_path = (
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/hydro-processing-v1.json"
    )
    manifest_path = (
        REPOSITORY_ROOT / "manifests/datasets/flip2/zenodo-18433203-v3/hydro/dataset-manifest.json"
    )
    config = load_hydro_processing_config(config_path)

    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == (
        config.dataset_manifest_sha256
    )
    assert config.expected_candidate_count == 24_935
    assert config.candidate_id_namespace == "flip2:zenodo-18433203-v3:hydro"
    assert config.observable_feature_fields == ("wild_type_id", "sequence")
    assert config.label_direction == "higher_is_better"
    assert (
        config.label_semantics
        == "Continuous FLIP2 hydrophobic-core stability fitness source score."
    )
    assert config.label_units == "source_score_physical_unit_unasserted"
    assert config.label_source_url == "https://flip.protein.properties/"
    assert config.allowed_amino_acids == "ACDEFGHIKLMNPQRSTVWY"
    assert config.variable_amino_acids == "FILMV"
    assert config.allowed_set_values == ("test", "train")
    assert config.allowed_validation_values == ("False", "True")
    assert config.observed_target_min == "-4.76689312547837"
    assert config.observed_target_median == "-3.20608122700315"
    assert config.observed_target_max == "0.119071964022942"
    assert config.observed_distinct_target_count == 24_745
    assert config.expected_near_duplicate_pair_count == 88_664
    assert {
        item.wild_type_id: (
            item.sequence_length,
            item.expected_candidate_count,
            item.reference_candidate_present,
            item.expected_near_duplicate_pair_count,
            item.reference_sequence_sha256,
            item.variable_positions,
        )
        for item in config.wild_types
    } == {
        "P06241": (
            57,
            9_972,
            False,
            36_830,
            "304f8fd4d48558b27bcba3eb1f9e62500be733da34813a060366ad4da0528999",
            (3, 17, 19, 25, 27, 49, 54),
        ),
        "P01053": (
            63,
            6_463,
            False,
            20_150,
            "dd005937bdc1ad8fef672c3d610baae1fa6d395a7d9e0aa8459537d5af07838d",
            (12, 19, 28, 46, 48, 50, 56),
        ),
        "P0A9X9": (
            65,
            8_500,
            True,
            31_684,
            "b969267b954bad343a488f0af8a68c5a097ee3ae9fdf96d48897b217362bad77",
            (5, 17, 26, 28, 47, 49, 63),
        ),
    }


def test_processing_config_rejects_inconsistent_candidate_counts(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    payload = _config_payload(manifest_bytes)
    payload["publisher_reported_candidate_count"] = 7

    with pytest.raises(ValueError, match="candidate counts are inconsistent"):
        load_hydro_processing_config(_write_config(tmp_path, manifest_bytes, payload))


def test_processing_config_rejects_candidate_namespace_drift(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    payload = _config_payload(manifest_bytes)
    payload["candidate_id_namespace"] = "flip2:fixture-v1:hydro-drift"

    with pytest.raises(ValueError, match="candidate_id_namespace"):
        load_hydro_processing_config(_write_config(tmp_path, manifest_bytes, payload))


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("label_semantics", "Drifted label semantics."),
        ("label_source_url", "https://example.invalid/label"),
    ],
)
def test_processing_config_rejects_label_contract_drift(
    tmp_path: Path,
    field: str,
    drifted_value: str,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    payload = _config_payload(manifest_bytes)
    payload[field] = drifted_value

    with pytest.raises(ValueError, match=field):
        load_hydro_processing_config(_write_config(tmp_path, manifest_bytes, payload))


def test_processing_config_rejects_wild_type_reference_source_drift(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    payload = _config_payload(manifest_bytes)
    wild_types = cast(list[dict[str, object]], payload["wild_types"])
    wild_types[0]["reference_source_url"] = "https://example.invalid/reference"

    with pytest.raises(ValueError, match="reference_source_url"):
        load_hydro_processing_config(_write_config(tmp_path, manifest_bytes, payload))


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("allowed_amino_acids", "ACDEFGHIKLMNPQRSTVWXY"),
        ("variable_amino_acids", "AFILMV"),
        ("allowed_set_values", ["other", "test", "train"]),
        ("allowed_validation_values", ["False", "True", "unknown"]),
    ],
)
def test_processing_config_rejects_policy_domain_drift(
    tmp_path: Path,
    field: str,
    drifted_value: object,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    payload = _config_payload(manifest_bytes)
    payload[field] = drifted_value

    with pytest.raises(ValueError, match=field):
        load_hydro_processing_config(_write_config(tmp_path, manifest_bytes, payload))


def test_processing_fails_closed_when_wild_type_holdout_mapping_drifts(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    source_path = raw_root / "hydro/to_P00001.csv.gz"
    rows = _source_rows()
    for row in rows:
        if len(row["sequence"]) == len(WILD_TYPES["P00002"]):
            row["set"] = "test"
            row["validation"] = "False"
    _write_source(source_path, rows)

    with pytest.raises(ValueError, match="holdout mapping differs"):
        audit_hydro_sources(
            load_hydro_processing_config(_write_config(tmp_path, manifest_bytes)),
            raw_root,
        )


def test_processing_fails_closed_when_fitness_holdout_mapping_drifts(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    source_path = raw_root / "hydro/low_to_high.csv.gz"
    rows = _source_rows()
    for row in rows:
        row["set"] = "test"
        row["validation"] = "False"
    _write_source(source_path, rows)

    with pytest.raises(ValueError, match="fitness holdout mapping differs"):
        audit_hydro_sources(
            load_hydro_processing_config(_write_config(tmp_path, manifest_bytes)),
            raw_root,
        )


def test_processing_fails_closed_when_mutation_count_holdout_mapping_drifts(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    source_path = raw_root / "hydro/three_to_many.csv.gz"
    rows = _source_rows()
    rows[0]["set"] = "test"
    rows[0]["validation"] = "False"
    _write_source(source_path, rows)

    with pytest.raises(ValueError, match="mutation-count holdout mapping differs"):
        audit_hydro_sources(
            load_hydro_processing_config(_write_config(tmp_path, manifest_bytes)),
            raw_root,
        )


def test_processing_fails_closed_when_one_source_target_conflicts(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    rows = _source_rows()
    rows[0]["target"] = "9"
    _write_source(raw_root / SOURCE_KEYS[-1], rows)

    with pytest.raises(ValueError, match="hidden truth conflicts"):
        audit_hydro_sources(
            load_hydro_processing_config(_write_config(tmp_path, manifest_bytes)),
            raw_root,
        )


def test_processing_fails_closed_when_fixed_wild_type_scaffold_drifts(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    rows = _source_rows()
    rows[0]["sequence"] = "CFFI"
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(ValueError, match="fixed WT scaffold differs"):
        audit_hydro_sources(
            load_hydro_processing_config(_write_config(tmp_path, manifest_bytes)),
            raw_root,
        )


def test_processing_fails_closed_when_raw_bytes_drift_from_dataset_manifest(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    config_path = _write_config(tmp_path, manifest_bytes)
    rows = _source_rows()
    rows.reverse()
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(ValueError, match="does not match Dataset Manifest"):
        build_hydro_processing_bundle(
            config=load_hydro_processing_config(config_path),
            config_bytes=config_path.read_bytes(),
            dataset_manifest_bytes=manifest_bytes,
            raw_root=raw_root,
            git_commit="b" * 40,
        )


def test_processing_fails_closed_when_frozen_reference_facts_drift(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    payload = _config_payload(manifest_bytes)
    wild_types = cast(list[dict[str, object]], payload["wild_types"])
    wild_types[0]["expected_near_duplicate_pair_count"] = 2
    payload["expected_near_duplicate_pair_count"] = 4

    with pytest.raises(ValueError, match="near-duplicate count differs"):
        audit_hydro_sources(
            load_hydro_processing_config(_write_config(tmp_path, manifest_bytes, payload)),
            raw_root,
        )


def test_processing_fails_closed_when_frozen_variable_positions_drift(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    payload = _config_payload(manifest_bytes)
    wild_types = cast(list[dict[str, object]], payload["wild_types"])
    wild_types[0]["variable_positions"] = [1, 4]

    with pytest.raises(ValueError, match="variable positions differ"):
        audit_hydro_sources(
            load_hydro_processing_config(_write_config(tmp_path, manifest_bytes, payload)),
            raw_root,
        )


def test_canonical_payloads_are_invariant_to_row_and_source_enumeration(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    first_manifest_bytes = _dataset_manifest_bytes(raw_root)
    first_config_path = _write_config(
        tmp_path,
        first_manifest_bytes,
        name="first.json",
    )
    first_payloads, _ = build_hydro_processing_bundle(
        config=load_hydro_processing_config(first_config_path),
        config_bytes=first_config_path.read_bytes(),
        dataset_manifest_bytes=first_manifest_bytes,
        raw_root=raw_root,
        git_commit="c" * 40,
    )

    for source_key in SOURCE_KEYS:
        path = raw_root / source_key
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        _write_source(path, list(reversed(rows)))
    second_manifest_bytes = _dataset_manifest_bytes(raw_root)
    second_payload = _config_payload(second_manifest_bytes)
    second_payload["source_csv_keys"] = list(reversed(SOURCE_KEYS))
    second_config_path = _write_config(
        tmp_path,
        second_manifest_bytes,
        second_payload,
        name="second.json",
    )
    second_payloads, _ = build_hydro_processing_bundle(
        config=load_hydro_processing_config(second_config_path),
        config_bytes=second_config_path.read_bytes(),
        dataset_manifest_bytes=second_manifest_bytes,
        raw_root=raw_root,
        git_commit="c" * 40,
    )

    assert {Path(key).name: value for key, value in first_payloads.items()} == {
        Path(key).name: value for key, value in second_payloads.items()
    }


def test_cli_stages_a_schema_valid_hydro_bundle_outside_git(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    manifest_path = tmp_path / "dataset-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    config_path = _write_config(tmp_path, manifest_bytes)
    staging_root = tmp_path / "staging"

    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/process_flip2_hydro.py"),
            "--config",
            str(config_path),
            "--dataset-manifest",
            str(manifest_path),
            "--raw-root",
            str(raw_root),
            "--staging-root",
            str(staging_root),
            "--git-commit",
            "d" * 40,
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    staged_names = sorted(path.name for path in staging_root.rglob("*") if path.is_file())
    assert staged_names == [
        "candidates.csv",
        "hidden-truth.csv",
        "processing-audit.json",
        "processing-manifest.json",
        "upstream-split-metadata.csv",
    ]


def test_cli_refuses_to_overwrite_an_existing_hydro_bundle(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    manifest_path = tmp_path / "dataset-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    config_path = _write_config(tmp_path, manifest_bytes)
    staging_root = tmp_path / "staging"
    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "scripts/process_flip2_hydro.py"),
        "--config",
        str(config_path),
        "--dataset-manifest",
        str(manifest_path),
        "--raw-root",
        str(raw_root),
        "--staging-root",
        str(staging_root),
        "--git-commit",
        "d" * 40,
    ]

    first = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert "File exists" in second.stderr
