from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import pcdb.datasets.ired_split as ired_split
from pcdb.datasets.ired_split import (
    Component,
    IredSplitConfig,
    IredSplitError,
    assign_stratified_singletons,
    build_ired_split_bundle,
    build_ired_split_membership,
    group_id_for_members,
    load_ired_split_config,
    ordering_key_for_singleton,
    role_schedule,
    validate_ired_split_bundle,
    validate_partition_access,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
NAMESPACE = "pcdb:split:flip2-ired-topology-extrapolation-v1:v1"
SOURCE_KEYS = ("ired/fixture-one.csv.gz", "ired/fixture-two.csv.gz")
SEQUENCES = (
    "AAAAAA",
    "AAAAAC",
    "AAAACC",
    "CCCCCC",
    "DDDDDD",
    "EEEEEE",
    "FFFFFF",
    "GGGGGG",
    "HHHHHH",
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
        "CCCCCC": ("test", "False"),
        "DDDDDD": ("train", "True"),
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
        "task_id": "ired",
        "split_id": "flip2-ired-topology-extrapolation-v1",
        "protocol_version": "1",
        "processing_manifest_sha256": hashlib.sha256(processing_manifest_bytes).hexdigest(),
        "candidate_input": candidate_artifact,
        "upstream_metadata_input": upstream_artifact,
        "sequence_length": 6,
        "allowed_amino_acids": "ACDEFGH",
        "expected_candidate_count": 9,
        "reference_sequence": reference,
        "reference_sequence_sha256": hashlib.sha256(reference.encode()).hexdigest(),
        "mutation_count_method": "full_length_hamming_distance_to_reference",
        "expected_mutation_count_min": 0,
        "expected_mutation_count_max": 6,
        "grouping_method": "full_length_hamming1_connected_component",
        "hamming_distance": 1,
        "protocol_namespace": NAMESPACE,
        "group_id_algorithm": "sha256",
        "group_preimage_format": (
            "namespace_nul_group-id_nul_uint32be_count_concatenated_raw_candidate_ids"
        ),
        "seed": 20260724,
        "ordering_key_algorithm": "sha256",
        "ordering_preimage_format": (
            "namespace_nul_order-key_nul_uint64be_seed_uint16be_mutation_count_raw_group_id"
        ),
        "ordering_tie_break": ["mutation_count", "ordering_key", "group_id"],
        "assignment_method": "mutation_count_stratified_global_deficit_schedule",
        "assignment_role_order": ["candidate-pool", "validation", "held-out"],
        "role_counts": {
            "candidate-pool": 2,
            "development": 3,
            "validation": 2,
            "held-out": 2,
        },
        "expected_hamming1_pair_count": 2,
        "expected_component_count": 7,
        "expected_singleton_component_count": 6,
        "expected_non_singleton_component_count": 1,
        "expected_largest_component_size": 3,
        "expected_second_largest_component_size": 1,
        "expected_singleton_mutation_counts": {"6": 6},
        "expected_singleton_role_counts_by_mutation": {
            "6": {"candidate-pool": 2, "validation": 2, "held-out": 2}
        },
        "group_id_collision_policy": "error",
        "ordering_key_collision_policy": "error",
        "impossible_allocation_policy": "error",
        "upstream_source_keys": list(SOURCE_KEYS),
        "expected_upstream_cross_role_edges": {
            SOURCE_KEYS[0]: 0,
            SOURCE_KEYS[1]: 2,
        },
        "expected_upstream_assignment_disagreement_count": 4,
        "leakage_controls": ["Hamming-1 components do not cross partitions."],
        "known_limitations": ["Synthetic public fixture only."],
    }


def _write_config(tmp_path: Path, payload: dict[str, object]) -> tuple[Path, bytes]:
    config_bytes = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    path = tmp_path / "split-config.json"
    path.write_bytes(config_bytes)
    return path, config_bytes


def _fixture(tmp_path: Path) -> tuple[IredSplitConfig, bytes, bytes, bytes, bytes]:
    candidate_bytes = _candidate_bytes()
    upstream_bytes = _upstream_bytes()
    processing_bytes, candidate_artifact, upstream_artifact = _processing_manifest_bytes(
        candidate_bytes, upstream_bytes
    )
    config_path, config_bytes = _write_config(
        tmp_path,
        _config_payload(processing_bytes, candidate_artifact, upstream_artifact),
    )
    config = load_ired_split_config(config_path)
    return config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes


def test_normative_hash_golden_vectors() -> None:
    assert group_id_for_members(NAMESPACE, ("00" * 32, "11" * 32)) == (
        "19bf4d32013d4c6da6f35a9505f4ee0659a9475b81b8cf73f55fdbb3d975ee6a"
    )
    vectors = {
        "22": (
            4,
            "cd65e66d09df3ba4f96ebdbcc7bcaf4ca327243619bc254ff2672ab532cefc46",
            "10b4a42d8a0464f55e1b7322d0a8bf269d6c54007bbf165eba2018356c663a57",
        ),
        "33": (
            5,
            "55b24db200e4ee76b17ec43838f9844f90e8d850dabb61dbfe6142c23181bf39",
            "e4f75d3a5991627bdc442faedecec35c78f5db42c057ea939ce0cec2bb603621",
        ),
        "44": (
            5,
            "c8c4b74dd7c13d12ddb8d8a09253289be80d2571122143e4ceb206dd9d3e30bb",
            "7c41c71258ebfe6dee6f7f761089700b87e8a830ec9c591cf7aebdbf26b18778",
        ),
        "55": (
            5,
            "ef7a8981b992253fb2047f44a18fc0602271690fb0fbb43a19170b81ecabf068",
            "2541e8d0b4a1508572949aa0dc3fddcc9385c2ac83f0457912251f083a987120",
        ),
        "66": (
            6,
            "d0411d46e578b5f1dd2ab2ea12391f71b87b972eaa1e0e88fc1adb9a61b4ca71",
            "43b149060ac4ce54a6ff3c6944021b90b5bd760e6ace8a0a75c5e8d6efdd5c08",
        ),
        "77": (
            6,
            "2027fc73a20d42fe14d3eff420f50e26e6d4906555fb7533bee48c63a1c9e066",
            "5b95967209419e7b90b5b693d1f10ecd329ab034d72988f962a4384c8f706557",
        ),
    }
    for repeated_byte, (mutation_count, expected_group, expected_order) in vectors.items():
        observed_group = group_id_for_members(NAMESPACE, (repeated_byte * 32,))
        assert observed_group == expected_group
        assert (
            ordering_key_for_singleton(NAMESPACE, 20260724, mutation_count, observed_group)
            == expected_order
        )


def test_normative_global_schedule_and_stratified_assignment() -> None:
    assert role_schedule({"candidate-pool": 2, "validation": 2, "held-out": 2}) == (
        "candidate-pool",
        "validation",
        "held-out",
        "candidate-pool",
        "validation",
        "held-out",
    )
    vector_inputs = (
        ("22", 4),
        ("33", 5),
        ("44", 5),
        ("55", 5),
        ("66", 6),
        ("77", 6),
    )
    components = tuple(
        Component(
            members=(repeated_byte * 32,),
            group_id=group_id_for_members(NAMESPACE, (repeated_byte * 32,)),
            mutation_count=mutation_count,
            ordering_key=ordering_key_for_singleton(
                NAMESPACE,
                20260724,
                mutation_count,
                group_id_for_members(NAMESPACE, (repeated_byte * 32,)),
            ),
        )
        for repeated_byte, mutation_count in vector_inputs
    )
    assignments = assign_stratified_singletons(
        components,
        {"candidate-pool": 2, "validation": 2, "held-out": 2},
    )

    assert [assignments[component.group_id] for component in components] == [
        "candidate-pool",
        "candidate-pool",
        "held-out",
        "validation",
        "validation",
        "held-out",
    ]


def test_production_config_reproduces_frozen_mutation_quotas() -> None:
    config = load_ired_split_config(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/"
        "ired-split-topology-extrapolation-v1.json"
    )

    assert config.role_counts == {
        "candidate-pool": 2070,
        "development": 4448,
        "validation": 1034,
        "held-out": 1034,
    }
    assert config.expected_singleton_role_counts_by_mutation[8] == {
        "candidate-pool": 20,
        "validation": 11,
        "held-out": 10,
    }
    assert config.expected_singleton_role_counts_by_mutation[12] == {
        "candidate-pool": 0,
        "validation": 0,
        "held-out": 1,
    }


def test_bundle_is_deterministic_schema_valid_and_contains_no_target(tmp_path: Path) -> None:
    config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    payloads, manifest = build_ired_split_bundle(
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
    assert set(membership_rows[0]) == {"candidate_id", "role", "group_id"}
    assert b"target" not in by_name["membership.csv"]
    assert audit["cross_role_hamming1_edge_count"] == 0
    assert audit["hidden_truth_read"] is False
    assert audit["upstream_assignment_disagreement_count"] == 4
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/split-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    validate_ired_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        membership_bytes=by_name["membership.csv"],
        audit_bytes=by_name["split-audit.json"],
        manifest=manifest,
    )
    assert build_ired_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit="a" * 40,
    ) == (payloads, manifest)


def test_membership_is_invariant_to_input_row_order(tmp_path: Path) -> None:
    config, _, _, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    first = build_ired_split_membership(
        config=config,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
    )
    second = build_ired_split_membership(
        config=config,
        candidate_bytes=_candidate_bytes(reverse=True),
        upstream_bytes=_upstream_bytes(reverse=True),
    )

    assert second == first


def test_fails_closed_on_processing_or_exact_byte_mismatch(tmp_path: Path) -> None:
    config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    with pytest.raises(IredSplitError, match="candidate_input .* frozen reference"):
        build_ired_split_bundle(
            config=config,
            config_bytes=config_bytes,
            processing_manifest_bytes=processing_bytes,
            candidate_bytes=candidate_bytes + b"\n",
            upstream_bytes=upstream_bytes,
            git_commit="a" * 40,
        )
    payloads, manifest = build_ired_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit="a" * 40,
    )
    by_name = {PurePosixPath(key).name: payload for key, payload in payloads.items()}
    with pytest.raises(IredSplitError, match="membership artifact"):
        validate_ired_split_bundle(
            config=config,
            config_bytes=config_bytes,
            processing_manifest_bytes=processing_bytes,
            candidate_bytes=candidate_bytes,
            upstream_bytes=upstream_bytes,
            membership_bytes=by_name["membership.csv"] + b"\n",
            audit_bytes=by_name["split-audit.json"],
            manifest=manifest,
        )


def test_fails_closed_on_stratum_or_metadata_drift(tmp_path: Path) -> None:
    config, _, _, _, upstream_bytes = _fixture(tmp_path)
    changed_rows = [
        {"candidate_id": _candidate_id(sequence), "sequence": sequence} for sequence in SEQUENCES
    ]
    changed_rows[-1]["sequence"] = "HHHHHA"
    with pytest.raises(IredSplitError, match="mutation strata"):
        build_ired_split_membership(
            config=config,
            candidate_bytes=_csv_bytes(("candidate_id", "sequence"), changed_rows),
            upstream_bytes=upstream_bytes,
        )
    with pytest.raises(IredSplitError, match="incomplete"):
        build_ired_split_membership(
            config=config,
            candidate_bytes=_candidate_bytes(),
            upstream_bytes=_csv_bytes(
                ("candidate_id", "source_key", "set", "validation"),
                _upstream_rows()[:-1],
            ),
        )


def test_hash_collisions_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, _, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    monkeypatch.setattr(ired_split, "group_id_for_members", lambda *_: "f" * 64)
    with pytest.raises(IredSplitError, match="same group_id"):
        build_ired_split_membership(
            config=config,
            candidate_bytes=candidate_bytes,
            upstream_bytes=upstream_bytes,
        )

    monkeypatch.undo()
    monkeypatch.setattr(ired_split, "ordering_key_for_singleton", lambda *_: "e" * 64)
    with pytest.raises(IredSplitError, match="same ordering_key"):
        build_ired_split_membership(
            config=config,
            candidate_bytes=candidate_bytes,
            upstream_bytes=upstream_bytes,
        )


def test_config_rejects_changed_byte_or_role_contract(tmp_path: Path) -> None:
    candidate_bytes = _candidate_bytes()
    upstream_bytes = _upstream_bytes()
    processing_bytes, candidate_artifact, upstream_artifact = _processing_manifest_bytes(
        candidate_bytes, upstream_bytes
    )
    payload = _config_payload(processing_bytes, candidate_artifact, upstream_artifact)
    payload["assignment_role_order"] = ["held-out", "validation", "candidate-pool"]
    path, _ = _write_config(tmp_path, payload)

    with pytest.raises(IredSplitError, match="assignment_role_order"):
        load_ired_split_config(path)


def test_held_out_consumer_access_requires_exact_authorization() -> None:
    split_manifest: dict[str, object] = {
        "partitions": list(("candidate-pool", "development", "validation", "held-out")),
        "held_out_partitions": ["held-out"],
    }
    split_bytes = (
        json.dumps(split_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    split_sha = hashlib.sha256(split_bytes).hexdigest()
    validate_partition_access(
        split_bytes,
        {"held_out_authorized": False},
        ("candidate-pool", "development", "validation"),
    )
    with pytest.raises(IredSplitError, match="not authorized"):
        validate_partition_access(
            split_bytes,
            {"held_out_authorized": False},
            ("held-out",),
        )
    with pytest.raises(IredSplitError, match="does not match"):
        validate_partition_access(
            split_bytes,
            {
                "split_sha256": "f" * 64,
                "held_out_authorized": True,
                "held_out_authorization_reference": "approval",
            },
            ("held-out",),
        )
    validate_partition_access(
        split_bytes,
        {
            "split_sha256": split_sha,
            "held_out_authorized": True,
            "held_out_authorization_reference": "approval",
        },
        ("held-out",),
    )
