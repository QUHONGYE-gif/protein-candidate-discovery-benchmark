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

from pcdb.datasets.amylase_split import (
    AmylaseSplitConfig,
    AmylaseSplitError,
    Component,
    assign_component_roles,
    build_amylase_split_bundle,
    build_amylase_split_membership,
    group_id_for_members,
    load_amylase_split_config,
    ordering_key_for_group,
    validate_amylase_split_bundle,
    validate_partition_access,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
NAMESPACE = "pcdb:split:flip2-amylase-topology-isolation-v1:v1"
SOURCE_KEYS = ("amylase/fixture-one.csv.gz", "amylase/fixture-two.csv.gz")
SEQUENCES = ("AAAA", "AAAC", "AACC", "CCCC", "CCCD", "DDDD", "EEEE")


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
    rows: list[dict[str, str]] = []
    second_roles = {
        "AAAA": ("train", "False"),
        "AAAC": ("train", "True"),
        "AACC": ("test", "False"),
        "CCCC": ("train", "False"),
        "CCCD": ("test", "False"),
        "DDDD": ("train", "False"),
        "EEEE": ("train", "False"),
    }
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
        set_value, validation = second_roles[sequence]
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
    candidate_artifact = _artifact("processed/fixture/candidates.csv", candidate_bytes, 7)
    upstream_artifact = _artifact(
        "processed/fixture/upstream-split-metadata.csv", upstream_bytes, 14
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
    return {
        "config_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "fixture-v1",
        "task_id": "amylase",
        "split_id": "flip2-amylase-topology-isolation-v1",
        "protocol_version": "1",
        "processing_manifest_sha256": hashlib.sha256(processing_manifest_bytes).hexdigest(),
        "candidate_input": candidate_artifact,
        "upstream_metadata_input": upstream_artifact,
        "sequence_length": 4,
        "allowed_amino_acids": "ACDE",
        "expected_candidate_count": 7,
        "grouping_method": "full_length_hamming1_connected_component",
        "hamming_distance": 1,
        "protocol_namespace": NAMESPACE,
        "group_id_algorithm": "sha256",
        "group_preimage_format": (
            "namespace_nul_group-id_nul_uint32be_count_concatenated_raw_candidate_ids"
        ),
        "seed": 20260722,
        "ordering_key_algorithm": "sha256",
        "ordering_preimage_format": "namespace_nul_order-key_nul_uint64be_seed_raw_group_id",
        "ordering_tie_break": ["ordering_key", "group_id"],
        "assignment_role_order": ["development", "validation", "held-out"],
        "role_counts": {
            "candidate-pool": 3,
            "development": 2,
            "validation": 1,
            "held-out": 1,
        },
        "expected_hamming1_pair_count": 3,
        "expected_component_count": 4,
        "expected_singleton_component_count": 2,
        "expected_non_singleton_component_count": 2,
        "expected_largest_component_size": 3,
        "expected_second_largest_component_size": 2,
        "upstream_source_keys": list(SOURCE_KEYS),
        "expected_upstream_cross_role_edges": {
            SOURCE_KEYS[0]: 0,
            SOURCE_KEYS[1]: 3,
        },
        "leakage_controls": ["Hamming-1 components do not cross partitions."],
        "known_limitations": ["Synthetic public fixture only."],
    }


def _write_config(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[Path, bytes]:
    config_bytes = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    path = tmp_path / "split-config.json"
    path.write_bytes(config_bytes)
    return path, config_bytes


def _fixture(tmp_path: Path) -> tuple[AmylaseSplitConfig, bytes, bytes, bytes, bytes]:
    candidate_bytes = _candidate_bytes()
    upstream_bytes = _upstream_bytes()
    processing_bytes, candidate_artifact, upstream_artifact = _processing_manifest_bytes(
        candidate_bytes, upstream_bytes
    )
    config_path, config_bytes = _write_config(
        tmp_path,
        _config_payload(processing_bytes, candidate_artifact, upstream_artifact),
    )
    config = load_amylase_split_config(config_path)
    return config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes


def test_normative_hash_golden_vectors() -> None:
    members = (
        "00" * 32,
        "11" * 32,
    )
    group_id = group_id_for_members(NAMESPACE, members)

    assert group_id == "5f68a413d11c7a270890551556e5c72261503fbd9e3dbe6871860e833f8c786e"
    assert ordering_key_for_group(NAMESPACE, 20260722, group_id) == (
        "89cf1630842a568e3584ce2c3e39acdc3c11fd87ffba0cf5771fd85cb5ff8d3b"
    )
    expected = {
        "22": (
            "d22af1f9229dd2e2841f3311bb7857c45f9280a2b66726d614502d6c833900a9",
            "478d0a565d3a6d766f82721842dcd32c5c50e856a9f5d34767fcd89ce079cdd2",
        ),
        "33": (
            "4a3ea6ada3642c86d21d6f02aff56f25d6173d4a0a39d89d2feb5dfdb65fa43c",
            "e448b6b5d0f797f91b070e5609253f99eb44a6a52105133b71a68de953349cd0",
        ),
    }
    for repeated_byte, (expected_group, expected_order) in expected.items():
        observed_group = group_id_for_members(NAMESPACE, (repeated_byte * 32,))
        assert observed_group == expected_group
        assert ordering_key_for_group(NAMESPACE, 20260722, observed_group) == expected_order


def test_normative_assignment_kernel_vector() -> None:
    def component(members: tuple[str, ...]) -> Component:
        group_id = group_id_for_members(NAMESPACE, members)
        return Component(
            members=members,
            group_id=group_id,
            ordering_key=ordering_key_for_group(NAMESPACE, 20260722, group_id),
        )

    pool = component(("44" * 32, "55" * 32, "66" * 32))
    g1 = component(("00" * 32, "11" * 32))
    g2 = component(("22" * 32,))
    g3 = component(("33" * 32,))
    assignments = assign_component_roles(
        (g3, pool, g1, g2),
        candidate_pool_size=3,
        assignable_role_counts={"development": 2, "validation": 1, "held-out": 1},
        role_order=("development", "validation", "held-out"),
    )

    assert assignments == {
        pool.group_id: "candidate-pool",
        g1.group_id: "development",
        g2.group_id: "validation",
        g3.group_id: "held-out",
    }


def test_split_bundle_is_deterministic_schema_valid_and_contains_no_target(
    tmp_path: Path,
) -> None:
    config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    payloads, manifest = build_amylase_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit="a" * 40,
    )
    by_name = {PurePosixPath(key).name: payload for key, payload in payloads.items()}
    membership_rows = list(csv.DictReader(io.StringIO(by_name["membership.csv"].decode("utf-8"))))
    audit = cast(dict[str, object], json.loads(by_name["split-audit.json"]))

    assert len(membership_rows) == 7
    assert Counter(row["role"] for row in membership_rows) == {
        "candidate-pool": 3,
        "development": 2,
        "validation": 1,
        "held-out": 1,
    }
    assert set(membership_rows[0]) == {"candidate_id", "role", "group_id"}
    assert b"target" not in by_name["membership.csv"]
    assert audit["cross_role_hamming1_edge_count"] == 0
    assert audit["hidden_truth_read"] is False
    assert audit["upstream_cross_role_hamming1_edges"] == {
        SOURCE_KEYS[0]: 0,
        SOURCE_KEYS[1]: 3,
    }

    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/split-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    validate_amylase_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        membership_bytes=by_name["membership.csv"],
        audit_bytes=by_name["split-audit.json"],
        manifest=manifest,
    )
    repeated = build_amylase_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit="a" * 40,
    )
    assert repeated == (payloads, manifest)


def test_membership_is_invariant_to_candidate_and_upstream_row_order(tmp_path: Path) -> None:
    config, _, _, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    first = build_amylase_split_membership(
        config=config,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
    )
    second = build_amylase_split_membership(
        config=config,
        candidate_bytes=_candidate_bytes(reverse=True),
        upstream_bytes=_upstream_bytes(reverse=True),
    )

    assert second == first


def test_split_fails_closed_on_processing_or_membership_checksum_mismatch(
    tmp_path: Path,
) -> None:
    config, config_bytes, processing_bytes, candidate_bytes, upstream_bytes = _fixture(tmp_path)
    with pytest.raises(AmylaseSplitError, match="candidate_input .* frozen reference"):
        build_amylase_split_bundle(
            config=config,
            config_bytes=config_bytes,
            processing_manifest_bytes=processing_bytes,
            candidate_bytes=candidate_bytes + b"\n",
            upstream_bytes=upstream_bytes,
            git_commit="a" * 40,
        )

    payloads, manifest = build_amylase_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit="a" * 40,
    )
    by_name = {PurePosixPath(key).name: payload for key, payload in payloads.items()}
    with pytest.raises(AmylaseSplitError, match="membership artifact"):
        validate_amylase_split_bundle(
            config=config,
            config_bytes=config_bytes,
            processing_manifest_bytes=processing_bytes,
            candidate_bytes=candidate_bytes,
            upstream_bytes=upstream_bytes,
            membership_bytes=by_name["membership.csv"] + b"\n",
            audit_bytes=by_name["split-audit.json"],
            manifest=manifest,
        )


def test_split_fails_closed_on_missing_or_unknown_candidate_metadata(tmp_path: Path) -> None:
    config, _, _, candidate_bytes, _ = _fixture(tmp_path)
    incomplete = _upstream_rows()[:-1]
    with pytest.raises(AmylaseSplitError, match="incomplete"):
        build_amylase_split_membership(
            config=config,
            candidate_bytes=candidate_bytes,
            upstream_bytes=_csv_bytes(
                ("candidate_id", "source_key", "set", "validation"), incomplete
            ),
        )

    unknown = _upstream_rows()
    unknown[0]["candidate_id"] = "ff" * 32
    with pytest.raises(AmylaseSplitError, match="unknown candidate_id"):
        build_amylase_split_membership(
            config=config,
            candidate_bytes=candidate_bytes,
            upstream_bytes=_csv_bytes(("candidate_id", "source_key", "set", "validation"), unknown),
        )


def test_split_fails_closed_when_topology_or_exact_role_counts_change(tmp_path: Path) -> None:
    config, _, _, _, upstream_bytes = _fixture(tmp_path)
    changed_rows = [
        {"candidate_id": _candidate_id(sequence), "sequence": sequence} for sequence in SEQUENCES
    ]
    changed_rows[-1]["sequence"] = "EDDD"
    with pytest.raises(AmylaseSplitError, match="topology differs"):
        build_amylase_split_membership(
            config=config,
            candidate_bytes=_csv_bytes(("candidate_id", "sequence"), changed_rows),
            upstream_bytes=upstream_bytes,
        )


def test_held_out_consumer_access_fails_closed_without_experiment_authorization() -> None:
    split_manifest: dict[str, object] = {
        "partitions": ["candidate-pool", "development", "validation", "held-out"],
        "held_out_partitions": ["held-out"],
    }
    validate_partition_access(
        split_manifest,
        {"held_out_authorized": False},
        ("candidate-pool", "development", "validation"),
    )
    with pytest.raises(AmylaseSplitError, match="not authorized"):
        validate_partition_access(
            split_manifest,
            {"held_out_authorized": False},
            ("held-out",),
        )
    with pytest.raises(AmylaseSplitError, match="reference is missing"):
        validate_partition_access(
            split_manifest,
            {"held_out_authorized": True},
            ("held-out",),
        )
    validate_partition_access(
        split_manifest,
        {
            "held_out_authorized": True,
            "held_out_authorization_reference": "approved-gate-reference",
        },
        ("held-out",),
    )


def test_config_rejects_changed_byte_or_role_contract(tmp_path: Path) -> None:
    candidate_bytes = _candidate_bytes()
    upstream_bytes = _upstream_bytes()
    processing_bytes, candidate_artifact, upstream_artifact = _processing_manifest_bytes(
        candidate_bytes, upstream_bytes
    )
    payload = _config_payload(processing_bytes, candidate_artifact, upstream_artifact)
    payload["assignment_role_order"] = ["held-out", "validation", "development"]
    path, _ = _write_config(tmp_path, payload)

    with pytest.raises(AmylaseSplitError, match="assignment_role_order"):
        load_amylase_split_config(path)
