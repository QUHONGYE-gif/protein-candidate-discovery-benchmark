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

from pcdb.datasets import processing
from pcdb.datasets.nucb import (
    audit_nucb_sources,
    build_nucb_processing_bundle,
    load_nucb_processing_config,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
SOURCE_KEYS = ("nucb/random.csv.gz", "nucb/two_to_many.csv.gz")
SOURCE_COMMIT = "702284847c4558567d8288169a86dc147cfaab32"


def _placeholder_manifest_bytes() -> bytes:
    return b'{"fixture":"dataset-manifest"}\n'


def _config_payload() -> dict[str, object]:
    reference = "AAAA"
    return {
        "config_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "fixture-v1",
        "task_id": "nucb",
        "dataset_manifest_sha256": hashlib.sha256(_placeholder_manifest_bytes()).hexdigest(),
        "source_csv_keys": list(SOURCE_KEYS),
        "required_fields": ["sequence", "target", "set", "validation"],
        "allowed_amino_acids": "AC",
        "sequence_length": 4,
        "expected_candidate_count": 4,
        "candidate_id_algorithm": "sha256",
        "candidate_id_namespace": "flip2:fixture-v1:nucb",
        "observable_feature_fields": ["sequence"],
        "hidden_label_field": "target",
        "label_type": "ordinal_class",
        "label_direction": "higher_is_better",
        "label_levels": [
            "non-functional",
            "activity_greater_than_0",
            "activity_greater_than_WT",
            "activity_greater_than_A73R",
        ],
        "allowed_target_values": ["0", "1", "2", "3"],
        "label_semantics": "Fixture ordered NucB activity classes.",
        "label_source_commit": SOURCE_COMMIT,
        "label_source_url": (
            "https://github.com/google-deepmind/nuclease_design/blob/"
            f"{SOURCE_COMMIT}/nuclease_design/constants.py"
        ),
        "high_value_threshold_policy": "experiment_config_only_not_frozen_by_processing",
        "missing_value_policy": "error",
        "allowed_set_values": ["test", "train"],
        "allowed_validation_values": ["False", "True"],
        "within_file_duplicate_policy": "error",
        "cross_file_duplicate_policy": "aggregate_if_target_matches",
        "conflicting_target_policy": "error",
        "near_duplicate_method": "full_length_hamming_distance",
        "near_duplicate_threshold": 1,
        "near_duplicate_policy": "allowed_pending_split_manifest",
        "expected_near_duplicate_pair_count": 0,
        "reference_sequence": reference,
        "reference_sequence_sha256": hashlib.sha256(reference.encode()).hexdigest(),
        "reference_resolution": (
            "First-party WT cross-checked with the unique per-position modal consensus."
        ),
        "reference_source_commit": SOURCE_COMMIT,
        "reference_source_url": (
            "https://github.com/google-deepmind/nuclease_design/blob/"
            f"{SOURCE_COMMIT}/nuclease_design/constants.py"
        ),
        "expected_reference_candidate_present": False,
        "mutation_count_method": "full_length_hamming_distance_to_reference",
        "expected_mutation_count_min": 1,
        "expected_mutation_count_max": 1,
        "source_relationship": (
            "same_candidate_universe_and_hidden_truth_with_alternate_split_metadata"
        ),
        "publisher_reported_candidate_count": 5,
        "upstream_candidate_count_limitation": (
            "Fixture first-party landscape includes one WT row absent from accepted inputs."
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
        {"sequence": "CAAA", "target": "0", "set": "train", "validation": "False"},
        {"sequence": "ACAA", "target": "1", "set": "train", "validation": "True"},
        {"sequence": "AACA", "target": "2", "set": "test", "validation": "False"},
        {"sequence": "AAAC", "target": "3", "set": "test", "validation": "False"},
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
        "task_id": "nucb",
        "raw_files": raw_files,
    }
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _csv_rows(payload: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))


def test_processing_builds_deterministic_artifacts_with_hidden_truth_separated(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)
    config = load_nucb_processing_config(config_path)

    payloads, manifest = build_nucb_processing_bundle(
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

    assert len(candidates) == len(hidden_truth) == 4
    assert len(memberships) == 8
    assert set(candidates[0]) == {"candidate_id", "sequence"}
    assert set(hidden_truth[0]) == {"candidate_id", "target"}
    assert {row["target"] for row in hidden_truth} == {"0", "1", "2", "3"}
    assert {row["candidate_id"] for row in candidates} == {
        row["candidate_id"] for row in hidden_truth
    }
    assert all("target" not in row for row in candidates)

    assert audit["candidate_count"] == 4
    assert audit["all_source_candidate_sets_identical"] is True
    assert audit["all_source_targets_identical"] is True
    assert audit["reference_candidate_present"] is False
    assert audit["near_duplicate_pair_counts"] == {"1": 0}
    assert audit["target_class_counts"] == {"0": 1, "1": 1, "2": 1, "3": 1}
    assert audit["high_value_threshold_policy"] == (
        "experiment_config_only_not_frozen_by_processing"
    )
    assert "candidate_targets" not in audit

    assert manifest["observable_feature_fields"] == ["sequence"]
    assert manifest["hidden_label_field"] == "target"
    assert manifest["label_direction"] == "higher_is_better"
    output_files = cast(list[dict[str, object]], manifest["output_files"])
    assert {
        Path(cast(str, item["object_key"])).name: item["row_count"] for item in output_files
    } == {
        "candidates.csv": 4,
        "hidden-truth.csv": 4,
        "processing-audit.json": 1,
        "upstream-split-metadata.csv": 8,
    }

    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/processing-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)

    repeated_payloads, repeated_manifest = build_nucb_processing_bundle(
        config=config,
        config_bytes=config_path.read_bytes(),
        dataset_manifest_bytes=manifest_bytes,
        raw_root=raw_root,
        git_commit="a" * 40,
    )
    assert repeated_payloads == payloads
    assert repeated_manifest == manifest


def test_repository_processing_config_freezes_the_approved_nucb_facts() -> None:
    config_path = (
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/nucb-processing-v1.json"
    )
    manifest_path = (
        REPOSITORY_ROOT / "manifests/datasets/flip2/zenodo-18433203-v3/nucb/dataset-manifest.json"
    )
    config = load_nucb_processing_config(config_path)

    assert config.dataset_id == "flip2"
    assert config.dataset_version == "zenodo-18433203-v3"
    assert config.task_id == "nucb"
    assert config.expected_candidate_count == 55_759
    assert config.sequence_length == 142
    assert config.allowed_target_values == ("0", "1", "2", "3")
    assert config.label_levels == (
        "non-functional",
        "activity_greater_than_0",
        "activity_greater_than_WT",
        "activity_greater_than_A73R",
    )
    assert config.expected_reference_candidate_present is False
    assert config.expected_mutation_count_min == 1
    assert config.expected_mutation_count_max == 23
    assert config.expected_near_duplicate_pair_count == 76_890
    assert config.publisher_reported_candidate_count == 55_760
    assert config.high_value_threshold_policy == ("experiment_config_only_not_frozen_by_processing")
    assert hashlib.sha256(config.reference_sequence.encode()).hexdigest() == (
        "5ce46bb493dbedbebac11e8f358ab1405919032bade046c66360b8b6c7ca89a5"
    )
    assert config.dataset_manifest_sha256 == hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def test_processing_bundle_fails_closed_when_raw_checksum_differs(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)
    _write_source(raw_root / SOURCE_KEYS[0], list(reversed(_source_rows())))

    config = load_nucb_processing_config(config_path)
    with pytest.raises(ValueError, match="does not match Dataset Manifest"):
        build_nucb_processing_bundle(
            config=config,
            config_bytes=config_path.read_bytes(),
            dataset_manifest_bytes=manifest_bytes,
            raw_root=raw_root,
            git_commit="b" * 40,
        )


def test_processing_bundle_fails_closed_when_manifest_identity_differs(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest = json.loads(_dataset_manifest_bytes(raw_root))
    manifest["task_id"] = "not-nucb"
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)

    with pytest.raises(ValueError, match="task_id does not match"):
        build_nucb_processing_bundle(
            config=load_nucb_processing_config(config_path),
            config_bytes=config_path.read_bytes(),
            dataset_manifest_bytes=manifest_bytes,
            raw_root=raw_root,
            git_commit="b" * 40,
        )


def test_processing_config_fails_closed_when_ordinal_contract_drifts(tmp_path: Path) -> None:
    payload = {**_config_payload(), "allowed_target_values": ["0", "1", "2", "4"]}

    with pytest.raises(ValueError, match="ordered classes 0,1,2,3"):
        load_nucb_processing_config(_write_config(tmp_path, payload))


@pytest.mark.parametrize("field", ["label_source_commit", "reference_source_commit"])
def test_processing_config_fails_closed_when_first_party_commit_drifts(
    tmp_path: Path,
    field: str,
) -> None:
    payload = {**_config_payload(), field: "0" * 40}

    with pytest.raises(ValueError, match="approved first-party commit"):
        load_nucb_processing_config(_write_config(tmp_path, payload))


@pytest.mark.parametrize("field", ["label_source_url", "reference_source_url"])
def test_processing_config_fails_closed_when_first_party_url_drifts(
    tmp_path: Path,
    field: str,
) -> None:
    payload = {**_config_payload(), field: "https://example.org/mutable"}

    with pytest.raises(ValueError, match="commit-pinned constants.py"):
        load_nucb_processing_config(_write_config(tmp_path, payload))


def test_processing_fails_closed_on_non_class_target(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    rows = _source_rows()
    rows[0]["target"] = "1.5"
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(ValueError, match="target must be one of 0,1,2,3"):
        audit_nucb_sources(
            load_nucb_processing_config(_write_config(tmp_path)),
            raw_root,
        )


def test_processing_fails_closed_on_cross_source_target_conflict(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    rows = _source_rows()
    rows[0]["target"] = "3"
    _write_source(raw_root / SOURCE_KEYS[-1], rows)

    with pytest.raises(ValueError, match="hidden truth conflicts"):
        audit_nucb_sources(
            load_nucb_processing_config(_write_config(tmp_path)),
            raw_root,
        )


def test_processing_fails_closed_on_missing_target(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    rows = _source_rows()
    rows[0]["target"] = ""
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(ValueError, match="missing value"):
        audit_nucb_sources(
            load_nucb_processing_config(_write_config(tmp_path)),
            raw_root,
        )


def test_processing_fails_closed_on_candidate_id_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(processing, "candidate_id", lambda config, sequence: "0" * 64)

    with pytest.raises(ValueError, match="candidate ID collision"):
        audit_nucb_sources(
            load_nucb_processing_config(_write_config(tmp_path)),
            _write_raw_bundle(tmp_path),
        )


def test_processing_fails_closed_on_duplicate_within_source(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    rows = _source_rows()
    rows.append(rows[0].copy())
    _write_source(raw_root / SOURCE_KEYS[0], rows)

    with pytest.raises(ValueError, match="duplicate sequence"):
        audit_nucb_sources(
            load_nucb_processing_config(_write_config(tmp_path)),
            raw_root,
        )


def test_processing_fails_closed_when_candidate_universe_differs(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    _write_source(raw_root / SOURCE_KEYS[-1], _source_rows()[:-1])

    with pytest.raises(ValueError, match="candidate universe differs"):
        audit_nucb_sources(
            load_nucb_processing_config(_write_config(tmp_path)),
            raw_root,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            {
                "reference_sequence": "CCCC",
                "reference_sequence_sha256": hashlib.sha256(b"CCCC").hexdigest(),
            },
            "observed reference differs",
        ),
        ({"expected_mutation_count_max": 2}, "mutation count range differs"),
        ({"expected_near_duplicate_pair_count": 1}, "near-duplicate count differs"),
    ],
)
def test_processing_fails_closed_when_frozen_audit_facts_drift(
    tmp_path: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    payload = {**_config_payload(), **mutation}

    with pytest.raises(ValueError, match=message):
        audit_nucb_sources(
            load_nucb_processing_config(_write_config(tmp_path, payload)),
            _write_raw_bundle(tmp_path),
        )


def test_canonical_payloads_are_invariant_to_row_and_source_enumeration(
    tmp_path: Path,
) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    first_manifest_bytes = _dataset_manifest_bytes(raw_root)
    first_config_path = _write_config(
        tmp_path,
        manifest_bytes=first_manifest_bytes,
        name="first.json",
    )
    first_payloads, _ = build_nucb_processing_bundle(
        config=load_nucb_processing_config(first_config_path),
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
    second_payload = {
        **_config_payload(),
        "source_csv_keys": list(reversed(SOURCE_KEYS)),
    }
    second_config_path = _write_config(
        tmp_path,
        second_payload,
        manifest_bytes=second_manifest_bytes,
        name="second.json",
    )
    second_payloads, _ = build_nucb_processing_bundle(
        config=load_nucb_processing_config(second_config_path),
        config_bytes=second_config_path.read_bytes(),
        dataset_manifest_bytes=second_manifest_bytes,
        raw_root=raw_root,
        git_commit="c" * 40,
    )

    assert {Path(key).name: value for key, value in first_payloads.items()} == {
        Path(key).name: value for key, value in second_payloads.items()
    }


def test_cli_stages_a_schema_valid_fixture_bundle_outside_git(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    manifest_path = tmp_path / "dataset-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)
    staging_root = tmp_path / "staging"

    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/process_flip2_nucb.py"),
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


def test_cli_refuses_to_overwrite_an_existing_staging_bundle(tmp_path: Path) -> None:
    raw_root = _write_raw_bundle(tmp_path)
    manifest_bytes = _dataset_manifest_bytes(raw_root)
    manifest_path = tmp_path / "dataset-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    config_path = _write_config(tmp_path, manifest_bytes=manifest_bytes)
    staging_root = tmp_path / "staging"
    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "scripts/process_flip2_nucb.py"),
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
