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

from pcdb.datasets.ired_split import (
    Component,
    assign_stratified_singletons,
    group_id_for_members,
    ordering_key_for_singleton,
    role_schedule,
    validate_partition_access,
)
from pcdb.datasets.nucb_split import (
    NucbSplitConfig,
    NucbSplitError,
    build_nucb_split_bundle,
    build_nucb_split_membership,
    load_nucb_split_config,
    load_nucb_split_fixture_config,
    validate_nucb_split_bundle,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
PRODUCTION_CONFIG_PATH = (
    REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/"
    "nucb-split-topology-extrapolation-v1.json"
)
FIXTURE_NAMESPACE = "pcdb:split:fixture-nucb-topology-extrapolation-v1:v1"
SOURCE_KEYS = ("nucb/fixture-one.csv.gz", "nucb/fixture-two.csv.gz")
SEQUENCES = (
    "AAAAAA",
    "AAAAAC",
    "AAAACC",
    "CCCCCC",
    "CCCCCA",
    "DDDDDD",
    "EEEEEE",
    "FFFFFF",
    "GGGGGG",
)


def _candidate_id(sequence: str) -> str:
    return hashlib.sha256(f"fixture\0{sequence}".encode()).hexdigest()


def _csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def _candidate_bytes(*, reverse: bool = False) -> bytes:
    rows = [
        {"candidate_id": _candidate_id(sequence), "sequence": sequence} for sequence in SEQUENCES
    ]
    if reverse:
        rows.reverse()
    return _csv_bytes(("candidate_id", "sequence"), rows)


def _upstream_rows() -> list[dict[str, str]]:
    changed_roles = {
        "AAAAAC": ("train", "True"),
        "AAAACC": ("test", "False"),
        "CCCCCA": ("test", "False"),
        "DDDDDD": ("train", "True"),
        "EEEEEE": ("test", "False"),
    }
    rows: list[dict[str, str]] = []
    for sequence in SEQUENCES:
        candidate_id = _candidate_id(sequence)
        rows.append(
            {
                "candidate_id": candidate_id,
                "source_key": SOURCE_KEYS[0],
                "set": "train",
                "validation": "False",
            }
        )
        set_value, validation = changed_roles.get(sequence, ("train", "False"))
        rows.append(
            {
                "candidate_id": candidate_id,
                "source_key": SOURCE_KEYS[1],
                "set": set_value,
                "validation": validation,
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
        "observable_feature_fields": ["sequence"],
        "hidden_label_field": "target",
        "output_files": [candidate_artifact, upstream_artifact],
    }
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return payload, candidate_artifact, upstream_artifact


def _config_payload(
    processing_manifest_bytes: bytes,
    candidate_artifact: dict[str, object],
    upstream_artifact: dict[str, object],
) -> dict[str, object]:
    reference = "AAAAAA"
    return {
        "config_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "fixture-v1",
        "task_id": "nucb",
        "split_id": "fixture-nucb-topology-extrapolation-v1",
        "protocol_version": "1",
        "processing_manifest_sha256": hashlib.sha256(processing_manifest_bytes).hexdigest(),
        "candidate_input": candidate_artifact,
        "upstream_metadata_input": upstream_artifact,
        "sequence_length": 6,
        "allowed_amino_acids": "ACDEFG",
        "expected_candidate_count": 9,
        "reference_sequence": reference,
        "reference_sequence_sha256": hashlib.sha256(reference.encode()).hexdigest(),
        "mutation_count_method": "full_length_hamming_distance_to_reference",
        "expected_mutation_count_min": 0,
        "expected_mutation_count_max": 6,
        "grouping_method": "full_length_hamming1_connected_component",
        "hamming_distance": 1,
        "protocol_namespace": FIXTURE_NAMESPACE,
        "group_id_algorithm": "sha256",
        "group_preimage_format": (
            "namespace_nul_group-id_nul_uint32be_count_concatenated_raw_candidate_ids"
        ),
        "seed": 20260727,
        "ordering_key_algorithm": "sha256",
        "ordering_preimage_format": (
            "namespace_nul_order-key_nul_uint64be_seed_uint16be_mutation_count_raw_group_id"
        ),
        "ordering_tie_break": ["mutation_count", "ordering_key", "group_id"],
        "assignment_method": "mutation_count_stratified_global_deficit_schedule",
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
        "expected_component_size_counts": {"1": 4, "2": 1, "3": 1},
        "expected_singleton_mutation_counts": {"6": 4},
        "expected_singleton_role_counts_by_mutation": {
            "6": {"candidate-pool": 2, "validation": 1, "held-out": 1}
        },
        "group_id_collision_policy": "error",
        "ordering_key_collision_policy": "error",
        "impossible_allocation_policy": "error",
        "upstream_source_keys": list(SOURCE_KEYS),
        "expected_upstream_cross_role_edges": {
            SOURCE_KEYS[0]: 0,
            SOURCE_KEYS[1]: 3,
        },
        "expected_upstream_assignment_disagreement_count": 5,
        "leakage_controls": ["Hamming-1 components do not cross partitions."],
        "known_limitations": ["Synthetic public fixture only."],
    }


def _fixture(tmp_path: Path) -> tuple[NucbSplitConfig, bytes, bytes, bytes, bytes]:
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
        load_nucb_split_fixture_config(config_path),
        config_bytes,
        processing_bytes,
        candidate_bytes,
        upstream_bytes,
    )


def test_loads_exact_production_contract() -> None:
    config = load_nucb_split_config(PRODUCTION_CONFIG_PATH)

    assert config.split_id == "flip2-nucb-topology-extrapolation-v1"
    assert config.role_counts == {
        "candidate-pool": 11493,
        "development": 32776,
        "validation": 5745,
        "held-out": 5745,
    }
    assert config.expected_component_size_counts[24844] == 1
    assert config.expected_component_size_counts[1] == 22983


def test_reproduces_normative_hash_and_schedule_vectors() -> None:
    namespace = "pcdb:split:flip2-nucb-topology-extrapolation-v1:v1"
    assert group_id_for_members(namespace, ("00" * 32, "11" * 32)) == (
        "0b143553a861ec1dbac870ba82e07ff8ec92b95fd631f6c2148723e60f4139b9"
    )
    singleton_vectors = {
        "22": (
            4,
            "4a169327474fdbb88a74946c6eef9487907403f87903d6e85f14a4a51afc062c",
            "c917df3d044c1300bf01d3b2448a3c1e82264c4ab333444cde9642c96c8e0e37",
        ),
        "33": (
            5,
            "030b7578e52351ef36e6a4fb2c55935a7804b50d553e452b5964313f2eafd453",
            "acbfa0bc62dd851aca9cb78225a16fb36d2b62200e2dd70ff3c1ed69cefcc0ef",
        ),
        "44": (
            5,
            "43c56e3d38202f7c59be63cb80640a6f6f489af42570ee3392d0089b55c5860c",
            "9dedda3789bf165766768b8d302df98e4396e2fca595127ac2eea2250d1dfcd2",
        ),
        "55": (
            5,
            "69ad507ea5f04208b3304c9b1cd39eb99a08e36cbe09fcc617f10e9930e568fe",
            "d8f128c1496dd6cba3ba89a0f950aab55fce36788d19e5e9d9fde97c11423a3a",
        ),
        "66": (
            5,
            "948e829f3e3c9138bdd7a0cda4d03d278c4714da9a383c6037d08f96a3f76cb5",
            "3225a9f4f3952740a7461c7526a354cd13e9f799e0e03ee394ba1ef531ed75ae",
        ),
        "77": (
            6,
            "7b0cd0faa13a663a566588cb0a767fde5725d5a2cea44f22362281cc271c2925",
            "5aa7fc9a63d6f378fac7f5a702ffbb8d3aa35d3f95e9576c999181f1c2b5b166",
        ),
        "88": (
            6,
            "07c418ae822829e7fc0765da97ef22fa88068aa95f1a9dbea9e29011679783f8",
            "34c55421a0162bfaad8f433bc8a6492ddfed917c4b6770851715ba63821738d3",
        ),
    }
    for repeated_byte, (
        mutation_count,
        expected_group,
        expected_order,
    ) in singleton_vectors.items():
        observed_group = group_id_for_members(namespace, (repeated_byte * 32,))
        assert observed_group == expected_group
        assert (
            ordering_key_for_singleton(namespace, 20260727, mutation_count, observed_group)
            == expected_order
        )
    real_candidate = "9d844ee2f8766aa1e0ed75596d5ca32469e61ebad92471248330cdbf65c31ccb"
    real_group = group_id_for_members(namespace, (real_candidate,))
    assert real_group == "99d7344eb968e5b3e1ad1a7de256a1e98aa41930feec1e598bfcc3e6b5e164e9"
    assert ordering_key_for_singleton(namespace, 20260727, 3, real_group) == (
        "00006a0f666cf5f1be2c39b521b5ae18d2601fc592df43b52dc9637570ce1884"
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
    components = tuple(
        Component(
            members=(repeated_byte * 32,),
            group_id=expected_group,
            mutation_count=mutation_count,
            ordering_key=expected_order,
        )
        for repeated_byte, (mutation_count, expected_group, expected_order) in (
            singleton_vectors.items()
        )
    )
    assignments = assign_stratified_singletons(
        components,
        {"candidate-pool": 3, "validation": 2, "held-out": 2},
    )
    assert [assignments[component.group_id] for component in components] == [
        "candidate-pool",
        "candidate-pool",
        "held-out",
        "validation",
        "validation",
        "candidate-pool",
        "held-out",
    ]


def test_builds_target_free_exact_byte_validated_split_bundle(tmp_path: Path) -> None:
    config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)

    payloads, manifest = build_nucb_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit="a" * 40,
    )
    by_name = {PurePosixPath(key).name: payload for key, payload in payloads.items()}
    membership_rows = list(csv.DictReader(io.StringIO(by_name["membership.csv"].decode())))
    audit = cast(dict[str, object], json.loads(by_name["split-audit.json"]))

    assert Counter(row["role"] for row in membership_rows) == config.role_counts
    assert b"target" not in by_name["membership.csv"]
    assert audit["cross_role_hamming1_edge_count"] == 0
    assert audit["hidden_truth_read"] is False
    assert audit["component_size_counts"] == {"1": 4, "2": 1, "3": 1}
    assert audit["upstream_role_counts"] == {
        SOURCE_KEYS[0]: {"set=train;validation=False": 9},
        SOURCE_KEYS[1]: {
            "set=test;validation=False": 3,
            "set=train;validation=False": 4,
            "set=train;validation=True": 2,
        },
    }
    assert audit["upstream_mixed_component_counts"] == {
        SOURCE_KEYS[0]: {"candidate_count": 0, "component_count": 0},
        SOURCE_KEYS[1]: {"candidate_count": 5, "component_count": 2},
    }
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/split-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    validate_nucb_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        membership_bytes=by_name["membership.csv"],
        audit_bytes=by_name["split-audit.json"],
        manifest=manifest,
    )


def test_membership_is_invariant_to_candidate_upstream_and_source_order(
    tmp_path: Path,
) -> None:
    config, _, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    first = build_nucb_split_membership(
        config=config,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
    )
    processing_payload, candidate_artifact, upstream_artifact = _processing_manifest_bytes(
        candidate_bytes,
        upstream_bytes,
    )
    assert processing_payload == processing_bytes
    reversed_config_payload = _config_payload(
        processing_payload,
        candidate_artifact,
        upstream_artifact,
    )
    reversed_config_payload["upstream_source_keys"] = list(reversed(SOURCE_KEYS))
    config_path = tmp_path / "reversed-source-config.json"
    config_path.write_text(
        json.dumps(reversed_config_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reversed_source_config = load_nucb_split_fixture_config(config_path)

    second = build_nucb_split_membership(
        config=reversed_source_config,
        candidate_bytes=_candidate_bytes(reverse=True),
        upstream_bytes=_upstream_bytes(reverse=True),
    )

    assert second == first


def test_fails_closed_on_processing_or_exact_byte_mismatch(tmp_path: Path) -> None:
    config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    with pytest.raises(NucbSplitError, match="candidate_input .* frozen reference"):
        build_nucb_split_bundle(
            config=config,
            config_bytes=config_bytes,
            processing_manifest_bytes=processing_bytes,
            candidate_bytes=candidate_bytes + b"\n",
            upstream_bytes=upstream_bytes,
            git_commit="a" * 40,
        )
    payloads, manifest = build_nucb_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit="a" * 40,
    )
    by_name = {PurePosixPath(key).name: payload for key, payload in payloads.items()}
    with pytest.raises(NucbSplitError, match="membership artifact"):
        validate_nucb_split_bundle(
            config=config,
            config_bytes=config_bytes,
            processing_manifest_bytes=processing_bytes,
            candidate_bytes=candidate_bytes,
            upstream_bytes=upstream_bytes,
            membership_bytes=by_name["membership.csv"] + b"\n",
            audit_bytes=by_name["split-audit.json"],
            manifest=manifest,
        )


def test_fails_closed_on_candidate_or_metadata_drift(tmp_path: Path) -> None:
    config, _, _, _, upstream_bytes = _fixture(tmp_path)
    duplicate_rows = [
        {"candidate_id": _candidate_id(sequence), "sequence": sequence}
        for sequence in SEQUENCES[:-1]
    ]
    duplicate_rows.append(duplicate_rows[-1])
    with pytest.raises(NucbSplitError, match="duplicate candidate_id"):
        build_nucb_split_membership(
            config=config,
            candidate_bytes=_csv_bytes(("candidate_id", "sequence"), duplicate_rows),
            upstream_bytes=upstream_bytes,
        )
    with pytest.raises(NucbSplitError, match="incomplete"):
        build_nucb_split_membership(
            config=config,
            candidate_bytes=_candidate_bytes(),
            upstream_bytes=_csv_bytes(
                ("candidate_id", "source_key", "set", "validation"),
                _upstream_rows()[:-1],
            ),
        )


def test_production_v1_rejects_identity_or_component_multiset_drift(tmp_path: Path) -> None:
    production = cast(
        dict[str, object],
        json.loads(PRODUCTION_CONFIG_PATH.read_text(encoding="utf-8")),
    )
    changed = cast(dict[str, object], json.loads(json.dumps(production)))
    component_sizes = cast(dict[str, int], changed["expected_component_size_counts"])
    component_sizes["2"] += 1
    path = tmp_path / "changed-production-config.json"
    path.write_text(json.dumps(changed, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(NucbSplitError, match="production v1 config identity"):
        load_nucb_split_config(path)


def test_held_out_consumer_access_requires_exact_authorization() -> None:
    split_manifest: dict[str, object] = {
        "partitions": list(("candidate-pool", "development", "validation", "held-out")),
        "held_out_partitions": ["held-out"],
    }
    split_bytes = (
        json.dumps(split_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    validate_partition_access(
        split_bytes,
        {"held_out_authorized": False},
        ("candidate-pool", "development", "validation"),
    )
    with pytest.raises(NucbSplitError, match="not authorized"):
        validate_partition_access(
            split_bytes,
            {"held_out_authorized": False},
            ("held-out",),
        )


def test_cli_requires_outside_worktree_and_never_overwrites(tmp_path: Path) -> None:
    script_path = REPOSITORY_ROOT / "scripts/split_flip2_nucb.py"
    spec = importlib.util.spec_from_file_location("split_flip2_nucb", script_path)
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
