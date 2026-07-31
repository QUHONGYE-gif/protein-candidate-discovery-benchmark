from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.hydro_split import (
    HydroSplitConfig,
    HydroSplitError,
    build_hydro_split_bundle,
    build_hydro_split_membership,
    load_hydro_split_config,
    load_hydro_split_fixture_config,
    validate_hydro_split_bundle,
)
from pcdb.datasets.ired_split import (
    group_id_for_members,
    ordering_key_for_singleton,
    role_schedule,
    validate_partition_access,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
PRODUCTION_CONFIG_PATH = (
    REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/"
    "hydro-split-within-wt-topology-stress-tracer-v1.json"
)
FIXTURE_NAMESPACE = "pcdb:split:fixture-hydro-within-wt-topology-stress-tracer-v1:v1"
SOURCE_KEYS = ("hydro/source-a.csv.gz", "hydro/source-b.csv.gz")
SEQUENCES = {
    "WT-A": ("AAAA", "AAAC", "AACC", "CCCC", "DDDD"),
    "WT-B": ("FFFFF", "FFFFG", "GGGGG", "HHHHH"),
}
REFERENCES = {"WT-A": "AAAA", "WT-B": "FFFFF"}


def _candidate_id(wild_type_id: str, sequence: str) -> str:
    return hashlib.sha256(f"fixture\0{wild_type_id}\0{sequence}".encode()).hexdigest()


def _csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def _candidate_rows() -> list[dict[str, str]]:
    return [
        {
            "candidate_id": _candidate_id(wild_type_id, sequence),
            "wild_type_id": wild_type_id,
            "sequence": sequence,
        }
        for wild_type_id, sequences in SEQUENCES.items()
        for sequence in sequences
    ]


def _candidate_bytes(*, reverse: bool = False) -> bytes:
    rows = _candidate_rows()
    if reverse:
        rows.reverse()
    return _csv_bytes(("candidate_id", "wild_type_id", "sequence"), rows)


def _upstream_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    changed = {
        ("WT-A", "AAAC"): ("train", "True"),
        ("WT-A", "AACC"): ("test", "False"),
        ("WT-B", "FFFFG"): ("test", "False"),
        ("WT-B", "HHHHH"): ("train", "True"),
    }
    for row in _candidate_rows():
        for source in SOURCE_KEYS:
            role = (
                changed.get((row["wild_type_id"], row["sequence"]), ("train", "False"))
                if source == SOURCE_KEYS[1]
                else ("train", "False")
            )
            rows.append(
                {
                    "candidate_id": row["candidate_id"],
                    "source_key": source,
                    "set": role[0],
                    "validation": role[1],
                }
            )
    return rows


def _upstream_bytes(*, reverse: bool = False) -> bytes:
    rows = _upstream_rows()
    if reverse:
        rows.reverse()
    return _csv_bytes(("candidate_id", "source_key", "set", "validation"), rows)


def _artifact(object_key: str, payload: bytes, row_count: int) -> dict[str, object]:
    return {
        "object_key": object_key,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "row_count": row_count,
    }


def _processing_manifest_bytes(
    candidate_bytes: bytes, upstream_bytes: bytes
) -> tuple[bytes, dict[str, object], dict[str, object]]:
    candidate_artifact = _artifact("processed/fixture/candidates.csv", candidate_bytes, 9)
    upstream_artifact = _artifact(
        "processed/fixture/upstream-split-metadata.csv", upstream_bytes, 18
    )
    manifest = {
        "schema_version": "1",
        "observable_feature_fields": ["wild_type_id", "sequence"],
        "hidden_label_field": "target",
        "output_files": [candidate_artifact, upstream_artifact],
    }
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return payload, candidate_artifact, upstream_artifact


def _wt_contract(
    wild_type_id: str,
    *,
    component_sizes: dict[str, int],
    role_counts: dict[str, int],
    mutation_counts: dict[str, int],
    mutation_role_counts: dict[str, dict[str, int]],
) -> dict[str, object]:
    reference = REFERENCES[wild_type_id]
    sizes = sorted(
        (int(size) for size, count in component_sizes.items() for _ in range(count)),
        reverse=True,
    )
    sequences = SEQUENCES[wild_type_id]
    return {
        "wild_type_id": wild_type_id,
        "sequence_length": len(reference),
        "reference_sequence": reference,
        "reference_sequence_sha256": hashlib.sha256(reference.encode()).hexdigest(),
        "expected_candidate_count": len(sequences),
        "expected_mutation_count_min": min(
            sum(left != right for left, right in zip(sequence, reference, strict=True))
            for sequence in sequences
        ),
        "expected_mutation_count_max": max(
            sum(left != right for left, right in zip(sequence, reference, strict=True))
            for sequence in sequences
        ),
        "role_counts": role_counts,
        "expected_hamming1_pair_count": 2 if wild_type_id == "WT-A" else 1,
        "expected_component_count": len(sizes),
        "expected_singleton_component_count": component_sizes["1"],
        "expected_non_singleton_component_count": len(sizes) - component_sizes["1"],
        "expected_largest_component_size": sizes[0],
        "expected_second_largest_component_size": sizes[1],
        "expected_component_size_counts": component_sizes,
        "expected_singleton_mutation_counts": mutation_counts,
        "expected_singleton_role_counts_by_mutation": mutation_role_counts,
    }


def _config_payload(
    processing_bytes: bytes,
    candidate_artifact: dict[str, object],
    upstream_artifact: dict[str, object],
) -> dict[str, object]:
    return {
        "config_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "fixture-v1",
        "task_id": "hydro",
        "split_id": "fixture-hydro-within-wt-topology-stress-tracer-v1",
        "protocol_version": "1",
        "processing_manifest_sha256": hashlib.sha256(processing_bytes).hexdigest(),
        "candidate_input": candidate_artifact,
        "upstream_metadata_input": upstream_artifact,
        "allowed_amino_acids": "ACDFGH",
        "expected_candidate_count": 9,
        "candidate_id_namespace": "fixture",
        "candidate_id_preimage": "namespace_null_wild_type_id_null_sequence",
        "mutation_count_method": "within_wild_type_full_length_hamming_distance_to_reference",
        "grouping_method": "within_wild_type_full_length_hamming1_connected_component",
        "cross_wild_type_distance_policy": "incomparable_without_accepted_alignment",
        "hamming_distance": 1,
        "protocol_namespace": FIXTURE_NAMESPACE,
        "group_id_algorithm": "sha256",
        "group_preimage_format": (
            "namespace_nul_group-id_nul_uint32be_count_concatenated_raw_candidate_ids"
        ),
        "seed": 20260731,
        "ordering_key_algorithm": "sha256",
        "ordering_preimage_format": (
            "namespace_nul_order-key_nul_uint64be_seed_uint16be_mutation_count_raw_group_id"
        ),
        "ordering_tie_break": ["mutation_count", "ordering_key", "group_id"],
        "assignment_method": ("per_wild_type_mutation_count_stratified_global_deficit_schedule"),
        "assignment_role_order": ["candidate-pool", "validation", "held-out"],
        "role_counts": {
            "candidate-pool": 2,
            "development": 5,
            "validation": 1,
            "held-out": 1,
        },
        "expected_hamming1_pair_count": 3,
        "expected_component_count": 6,
        "expected_singleton_component_count": 4,
        "expected_non_singleton_component_count": 2,
        "expected_largest_component_size": 3,
        "expected_second_largest_component_size": 2,
        "wild_types": [
            _wt_contract(
                "WT-A",
                component_sizes={"1": 2, "3": 1},
                role_counts={
                    "candidate-pool": 1,
                    "development": 3,
                    "validation": 1,
                    "held-out": 0,
                },
                mutation_counts={"4": 2},
                mutation_role_counts={"4": {"candidate-pool": 1, "validation": 1, "held-out": 0}},
            ),
            _wt_contract(
                "WT-B",
                component_sizes={"1": 2, "2": 1},
                role_counts={
                    "candidate-pool": 1,
                    "development": 2,
                    "validation": 0,
                    "held-out": 1,
                },
                mutation_counts={"5": 2},
                mutation_role_counts={"5": {"candidate-pool": 1, "validation": 0, "held-out": 1}},
            ),
        ],
        "group_id_collision_policy": "error",
        "ordering_key_collision_policy": "error",
        "impossible_allocation_policy": "error",
        "upstream_source_keys": list(SOURCE_KEYS),
        "expected_upstream_cross_role_edges": {SOURCE_KEYS[0]: 0, SOURCE_KEYS[1]: 3},
        "expected_upstream_role_counts": {
            SOURCE_KEYS[0]: {
                "global": {
                    "set=test;validation=False": 0,
                    "set=train;validation=False": 9,
                    "set=train;validation=True": 0,
                },
                "WT-A": {
                    "set=test;validation=False": 0,
                    "set=train;validation=False": 5,
                    "set=train;validation=True": 0,
                },
                "WT-B": {
                    "set=test;validation=False": 0,
                    "set=train;validation=False": 4,
                    "set=train;validation=True": 0,
                },
            },
            SOURCE_KEYS[1]: {
                "global": {
                    "set=test;validation=False": 2,
                    "set=train;validation=False": 5,
                    "set=train;validation=True": 2,
                },
                "WT-A": {
                    "set=test;validation=False": 1,
                    "set=train;validation=False": 3,
                    "set=train;validation=True": 1,
                },
                "WT-B": {
                    "set=test;validation=False": 1,
                    "set=train;validation=False": 2,
                    "set=train;validation=True": 1,
                },
            },
        },
        "expected_upstream_mixed_component_counts": {
            SOURCE_KEYS[0]: {"component_count": 0, "candidate_count": 0},
            SOURCE_KEYS[1]: {"component_count": 2, "candidate_count": 5},
        },
        "expected_upstream_assignment_disagreements": [
            {"left_source": SOURCE_KEYS[0], "right_source": SOURCE_KEYS[1], "count": 4}
        ],
        "publisher_metadata_role": "audit_only_not_benchmark_membership",
        "leakage_controls": ["Fixture components never cross partitions."],
        "known_limitations": ["Synthetic public fixture only."],
    }


def _fixture(tmp_path: Path) -> tuple[HydroSplitConfig, bytes, bytes, bytes, bytes]:
    candidate_bytes = _candidate_bytes()
    upstream_bytes = _upstream_bytes()
    processing_bytes, candidate_artifact, upstream_artifact = _processing_manifest_bytes(
        candidate_bytes, upstream_bytes
    )
    config_bytes = (
        json.dumps(
            _config_payload(processing_bytes, candidate_artifact, upstream_artifact),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    config_path = tmp_path / "split-config.json"
    config_path.write_bytes(config_bytes)
    return (
        load_hydro_split_fixture_config(config_path),
        config_bytes,
        processing_bytes,
        candidate_bytes,
        upstream_bytes,
    )


def test_loads_exact_production_contract() -> None:
    config = load_hydro_split_config(PRODUCTION_CONFIG_PATH)

    assert config.split_id == "flip2-hydro-within-wt-topology-stress-tracer-v1"
    assert config.role_counts == {
        "candidate-pool": 580,
        "development": 23775,
        "validation": 290,
        "held-out": 290,
    }
    assert {item.wild_type_id for item in config.wild_types} == {
        "P06241",
        "P01053",
        "P0A9X9",
    }
    assert config.cross_wild_type_distance_policy == "incomparable_without_accepted_alignment"


def test_reproduces_normative_hash_and_schedule_vectors() -> None:
    namespace = "pcdb:split:flip2-hydro-within-wt-topology-stress-tracer-v1:v1"
    assert group_id_for_members(namespace, ("00" * 32, "11" * 32)) == (
        "334549d78d36a210873cb213de6b04204425e64cc43bd5f1b97bdcf0ddc61ac4"
    )
    group_id = group_id_for_members(namespace, ("22" * 32,))
    assert group_id == "dba53df5dd2b67d48cd0b6beef12f6a14b769ebe8d564c7525a923e88118db73"
    assert ordering_key_for_singleton(namespace, 20260731, 3, group_id) == (
        "15992bc72b6b64077ff6154b4b4d45e500a6c3021001c6f0b5506160d766873c"
    )
    production_vectors = (
        (
            "d688f4966d528b937f0b3bd33d9eeb12cf650ac7b7d029cbcfd47d99fb3cdadc",
            3,
            "c759680b86758a5c45ce845b716195433081dd6bd6bd63692858302d267b3754",
            "1248b59c99e474d79fe2f2b31a8dfd98680e10598f7976acbea29ea08770efe2",
        ),
        (
            "fe7c371296c8d39400dbeed509f7f9ab54d4e10610b1ec25e4e6c80b42c353e5",
            1,
            "8119a71c54c8bb7d9b80cf2a56ff19be463a1bafd7983d4c87e1d5fe239bc2f9",
            "c7b8de68dd323cb07dabc4ed1fc612824e2f491b2284ca7c715f8b4625ccee09",
        ),
        (
            "07cf244903616679f43acb465c40b33c678c0d582d0cdd9d5264aebbb85fffbd",
            2,
            "d8812c4f23623a6efd9e89fb540e028987544273eac36ca2a52471c447ea122b",
            "04e7d7cc587b0b62885ddbcf3478a9443e84b66a733749827c41c8fce7f64cf1",
        ),
    )
    for candidate_id, mutation_count, expected_group, expected_order in production_vectors:
        observed_group = group_id_for_members(namespace, (candidate_id,))
        assert observed_group == expected_group
        assert (
            ordering_key_for_singleton(
                namespace,
                20260731,
                mutation_count,
                observed_group,
            )
            == expected_order
        )
    assert role_schedule({"candidate-pool": 3, "validation": 2, "held-out": 2}) == (
        "candidate-pool",
        "validation",
        "held-out",
        "candidate-pool",
        "validation",
        "held-out",
        "candidate-pool",
    )


def test_builds_within_wt_target_free_exact_byte_bundle(tmp_path: Path) -> None:
    config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)

    payloads, manifest = build_hydro_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit="a" * 40,
    )
    by_name = {PurePosixPath(key).name: payload for key, payload in payloads.items()}
    membership = list(csv.DictReader(io.StringIO(by_name["membership.csv"].decode())))
    audit = cast(dict[str, object], json.loads(by_name["split-audit.json"]))

    assert Counter(row["role"] for row in membership) == config.role_counts
    assert b"target" not in by_name["membership.csv"]
    assert audit["cross_role_hamming1_edge_count"] == 0
    assert audit["cross_wild_type_hamming_computed"] is False
    assert audit["exclusion_count"] == 0
    assert audit["hidden_truth_read"] is False
    assert audit["publisher_metadata_role"] == "audit_only_not_benchmark_membership"
    upstream_role_counts = cast(
        dict[str, dict[str, dict[str, int]]],
        audit["upstream_role_counts"],
    )
    assert upstream_role_counts[SOURCE_KEYS[0]]["WT-A"] == {
        "set=test;validation=False": 0,
        "set=train;validation=False": 5,
        "set=train;validation=True": 0,
    }
    assert audit["upstream_assignment_disagreements"] == [
        {"count": 4, "left_source": SOURCE_KEYS[0], "right_source": SOURCE_KEYS[1]}
    ]
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/split-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    validate_hydro_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        membership_bytes=by_name["membership.csv"],
        audit_bytes=by_name["split-audit.json"],
        manifest=manifest,
    )


def test_membership_is_invariant_to_input_row_order(tmp_path: Path) -> None:
    config, _, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    first = build_hydro_split_membership(
        config=config,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
    )
    second = build_hydro_split_membership(
        config=config,
        candidate_bytes=_candidate_bytes(reverse=True),
        upstream_bytes=_upstream_bytes(reverse=True),
    )
    assert second == first

    _, candidate_artifact, upstream_artifact = _processing_manifest_bytes(
        candidate_bytes,
        upstream_bytes,
    )
    payload = _config_payload(processing_bytes, candidate_artifact, upstream_artifact)
    cast(list[object], payload["wild_types"]).reverse()
    cast(list[object], payload["upstream_source_keys"]).reverse()
    config_path = tmp_path / "reordered-config.json"
    config_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reordered = build_hydro_split_membership(
        config=load_hydro_split_fixture_config(config_path),
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
    )
    assert reordered == first


def test_rejects_cross_wt_or_processing_drift(tmp_path: Path) -> None:
    config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    rows = _candidate_rows()
    rows[0]["wild_type_id"] = "WT-B"
    with pytest.raises(HydroSplitError, match="invalid sequence|candidate counts"):
        build_hydro_split_membership(
            config=config,
            candidate_bytes=_csv_bytes(
                ("candidate_id", "wild_type_id", "sequence"),
                rows,
            ),
            upstream_bytes=upstream_bytes,
        )
    with pytest.raises(HydroSplitError, match="candidate_input .* frozen reference"):
        build_hydro_split_bundle(
            config=config,
            config_bytes=config_bytes,
            processing_manifest_bytes=processing_bytes,
            candidate_bytes=candidate_bytes + b"\n",
            upstream_bytes=upstream_bytes,
            git_commit="a" * 40,
        )


def test_cli_requires_outside_worktree_and_never_overwrites(tmp_path: Path) -> None:
    script_path = REPOSITORY_ROOT / "scripts/split_flip2_hydro.py"
    spec = importlib.util.spec_from_file_location("split_flip2_hydro", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(ValueError, match="outside the Git repository"):
        module._ensure_outside_repository(REPOSITORY_ROOT / "staging")
    destination = module._exclusive_write(tmp_path, "splits/fixture/membership.csv", b"first")
    assert destination.read_bytes() == b"first"
    with pytest.raises(FileExistsError):
        module._exclusive_write(tmp_path, "splits/fixture/membership.csv", b"second")
    assert destination.read_bytes() == b"first"


def test_shared_consumer_guard_rejects_unauthorized_or_non_partition_access() -> None:
    split_manifest = {
        "partitions": list(("candidate-pool", "development", "validation", "held-out")),
        "held_out_partitions": ["held-out"],
    }
    split_bytes = (
        json.dumps(split_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    with pytest.raises(HydroSplitError, match="not authorized"):
        validate_partition_access(
            split_bytes,
            {"held_out_authorized": False},
            ("held-out",),
        )
    with pytest.raises(HydroSplitError, match="unknown split partition"):
        validate_partition_access(
            split_bytes,
            {"held_out_authorized": True},
            ("WT-holdout",),
        )
