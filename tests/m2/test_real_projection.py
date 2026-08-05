"""Public synthetic checks for the Issue #58 real-input projection boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

import pytest

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.datasets.processing import candidate_id
from pcdb.m2.projection import (
    ProjectedInputs,
    ProjectionIdentity,
    project_checked_inputs,
    project_development_truth,
)
from pcdb.m2.types import DevelopmentObservation


@dataclass(frozen=True)
class _ProcessingConfig:
    dataset_id: str = "public-fixture"
    dataset_version: str = "v1"
    task_id: str = "mixed-role"
    dataset_manifest_sha256: str = "a" * 64
    source_csv_keys: tuple[str, ...] = ("fixture.csv.gz",)
    candidate_id_namespace: str = "public-fixture:v1:mixed-role"
    observable_feature_fields: tuple[str, ...] = ("sequence",)
    hidden_label_field: str = "target"
    label_direction: str = "higher_is_better"
    near_duplicate_method: str = "none"
    near_duplicate_threshold: int = 0


_CONFIG = _ProcessingConfig()
_CONFIG_BYTES = canonical_json_bytes(
    {
        "candidate_id_namespace": _CONFIG.candidate_id_namespace,
        "dataset_id": _CONFIG.dataset_id,
        "dataset_manifest_sha256": _CONFIG.dataset_manifest_sha256,
        "dataset_version": _CONFIG.dataset_version,
        "task_id": _CONFIG.task_id,
    }
)
_SEQUENCES = {
    "development": "ACDE",
    "validation": "FGHI",
    "candidate-pool": "KLMN",
    "held-out": "PQRS",
}
_CANDIDATES = {role: candidate_id(_CONFIG, sequence) for role, sequence in _SEQUENCES.items()}


def _json_bytes(value: object) -> bytes:
    return canonical_json_bytes(value)


def _membership_bytes(*, duplicate: bool = False) -> bytes:
    rows = [
        "candidate_id,role,group_id",
        *(
            f"{_CANDIDATES[role]},{role},group-{index}"
            for index, role in enumerate(
                ("development", "validation", "candidate-pool", "held-out")
            )
        ),
    ]
    if duplicate:
        rows.append(f"{_CANDIDATES['development']},validation,group-duplicate")
    return ("\n".join(rows) + "\n").encode()


def _split_manifest_bytes(
    membership_bytes: bytes,
    *,
    processing_manifest_sha256: str = "b" * 64,
    include_row_count: bool = True,
) -> bytes:
    membership_file: dict[str, object] = {
        "object_key": "splits/public-fixture/membership.csv",
        "sha256": hashlib.sha256(membership_bytes).hexdigest(),
        "size_bytes": len(membership_bytes),
    }
    if include_row_count:
        membership_file["row_count"] = len(membership_bytes.decode().splitlines()) - 1
    return _json_bytes(
        {
            "held_out_partitions": ["held-out"],
            "membership_file": membership_file,
            "partitions": ["candidate-pool", "development", "validation", "held-out"],
            "processing_manifest_sha256": processing_manifest_sha256,
            "schema_version": "1",
        }
    )


def _identity(
    split_manifest_bytes: bytes,
    *,
    processing_manifest_sha256: str = "b" * 64,
) -> ProjectionIdentity:
    return ProjectionIdentity(
        benchmark_task_id="public-fixture-mixed-role",
        dataset_manifest_sha256="a" * 64,
        processing_manifest_sha256=processing_manifest_sha256,
        split_sha256=hashlib.sha256(split_manifest_bytes).hexdigest(),
        processing_config_sha256=hashlib.sha256(_CONFIG_BYTES).hexdigest(),
        role_counts={"development": 1, "validation": 1, "candidate-pool": 1},
        dataset_id="public-fixture",
        dataset_version="v1",
        dataset_task_id="mixed-role",
        candidate_id_namespace=_CONFIG.candidate_id_namespace,
    )


def _role_bytes(*, extra_field: str | None = None, omit: str | None = None) -> bytes:
    roles: dict[str, list[dict[str, object]]] = {}
    for role in ("development", "validation", "candidate-pool"):
        if role == omit:
            continue
        record: dict[str, object] = {
            "candidate_id": _CANDIDATES[role],
            "sequence": _SEQUENCES[role],
        }
        if extra_field is not None and role == "candidate-pool":
            record[extra_field] = 0.75
        roles[role] = [record]
    return _json_bytes({"roles": roles})


def _hidden_truth_bytes() -> bytes:
    return (
        "candidate_id,target\n"
        + "\n".join(
            f"{_CANDIDATES[role]},{index + 1}.25"
            for index, role in enumerate(
                ("development", "validation", "candidate-pool", "held-out")
            )
        )
        + "\n"
    ).encode()


def _processing_manifest_bytes(hidden_truth_bytes: bytes) -> bytes:
    return _json_bytes(
        {
            "candidate_id_field": "candidate_id",
            "candidate_id_unique": True,
            "dataset_manifest_sha256": _CONFIG.dataset_manifest_sha256,
            "duplicate_policy": {
                "exact_duplicates": "aggregate",
                "near_duplicates": "allowed",
            },
            "git_commit": "c" * 40,
            "hidden_label_field": "target",
            "known_limitations": [],
            "label_direction": "higher_is_better",
            "observable_feature_fields": ["sequence"],
            "output_files": [
                {
                    "object_key": "processed/public-fixture/hidden-truth.csv",
                    "row_count": 4,
                    "sha256": hashlib.sha256(hidden_truth_bytes).hexdigest(),
                    "size_bytes": len(hidden_truth_bytes),
                }
            ],
            "processing_config_sha256": hashlib.sha256(_CONFIG_BYTES).hexdigest(),
            "processing_id": "public-fixture-v1",
            "schema_version": "1",
        }
    )


def _all_memberships() -> dict[str, tuple[str, ...]]:
    return {
        role: (_CANDIDATES[role],)
        for role in ("development", "validation", "candidate-pool", "held-out")
    }


def test_truth_owner_projects_only_development_from_exact_artifact_bytes() -> None:
    hidden_truth = _hidden_truth_bytes()
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    membership = _membership_bytes()
    split_manifest = _split_manifest_bytes(membership)
    identity = replace(
        _identity(split_manifest),
        processing_manifest_sha256=hashlib.sha256(processing_manifest).hexdigest(),
    )

    observations = project_development_truth(
        identity=identity,
        processing_manifest_bytes=processing_manifest,
        hidden_truth_bytes=hidden_truth,
        expected_role_memberships=_all_memberships(),
    )

    assert observations == (DevelopmentObservation(_CANDIDATES["development"], 1.25),)
    assert not {
        _CANDIDATES["validation"],
        _CANDIDATES["candidate-pool"],
        _CANDIDATES["held-out"],
    }.intersection(item.candidate_id for item in observations)


def test_truth_owner_rejects_processing_manifest_identity_drift() -> None:
    hidden_truth = _hidden_truth_bytes()
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    split_manifest = _split_manifest_bytes(_membership_bytes())

    with pytest.raises(ContractValidationError, match="processing manifest identity"):
        project_development_truth(
            identity=_identity(split_manifest),
            processing_manifest_bytes=processing_manifest,
            hidden_truth_bytes=hidden_truth,
            expected_role_memberships=_all_memberships(),
        )


def test_truth_owner_rejects_truth_not_exact_covering_the_closed_split() -> None:
    hidden_truth = _hidden_truth_bytes().replace(
        f"{_CANDIDATES['held-out']},4.25\n".encode(),
        b"",
    )
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    split_manifest = _split_manifest_bytes(_membership_bytes())

    with pytest.raises(ContractValidationError, match="exact-cover split membership"):
        project_development_truth(
            identity=_identity(
                split_manifest,
                processing_manifest_sha256=processing_sha256,
            ),
            processing_manifest_bytes=processing_manifest,
            hidden_truth_bytes=hidden_truth,
            expected_role_memberships=_all_memberships(),
        )


def test_truth_owner_isolates_numeric_parse_failure_from_exception_chain() -> None:
    marker = "private-target-must-not-escape"
    hidden_truth = _hidden_truth_bytes().replace(b"1.25", marker.encode(), 1)
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    split_manifest = _split_manifest_bytes(_membership_bytes())

    with pytest.raises(ContractValidationError, match="target must be numeric") as raised:
        project_development_truth(
            identity=_identity(
                split_manifest,
                processing_manifest_sha256=processing_sha256,
            ),
            processing_manifest_bytes=processing_manifest,
            hidden_truth_bytes=hidden_truth,
            expected_role_memberships=_all_memberships(),
        )

    assert marker not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_truth_owner_isolates_decode_failure_from_exception_chain() -> None:
    marker = "private-decode-marker"
    hidden_truth = _hidden_truth_bytes() + marker.encode() + b"\xff"
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    split_manifest = _split_manifest_bytes(_membership_bytes())

    with pytest.raises(ContractValidationError, match="UTF-8 CSV") as raised:
        project_development_truth(
            identity=_identity(
                split_manifest,
                processing_manifest_sha256=processing_sha256,
            ),
            processing_manifest_bytes=processing_manifest,
            hidden_truth_bytes=hidden_truth,
            expected_role_memberships=_all_memberships(),
        )

    assert marker not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize("failure_stage", ["header", "row"])
def test_truth_owner_isolates_csv_failure_from_exception_chain(failure_stage: str) -> None:
    marker = "private-csv-marker"
    if failure_stage == "header":
        hidden_truth = f'candidate_id,"{marker}\n'.encode()
    else:
        hidden_truth = f'candidate_id,target\n{_CANDIDATES["development"]},"{marker}\n'.encode()
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    split_manifest = _split_manifest_bytes(_membership_bytes())

    with pytest.raises(ContractValidationError, match="valid CSV") as raised:
        project_development_truth(
            identity=_identity(
                split_manifest,
                processing_manifest_sha256=processing_sha256,
            ),
            processing_manifest_bytes=processing_manifest,
            hidden_truth_bytes=hidden_truth,
            expected_role_memberships=_all_memberships(),
        )

    assert marker not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def _project(
    *,
    membership_bytes: bytes | None = None,
    role_bytes: bytes | None = None,
    hidden_truth_bytes: bytes | None = None,
) -> ProjectedInputs:
    membership = membership_bytes or _membership_bytes()
    hidden_truth = hidden_truth_bytes or _hidden_truth_bytes()
    processing_manifest = _processing_manifest_bytes(_hidden_truth_bytes())
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    manifest = _split_manifest_bytes(
        membership,
        processing_manifest_sha256=processing_sha256,
    )
    return project_checked_inputs(
        identity=_identity(manifest, processing_manifest_sha256=processing_sha256),
        processing_config=_CONFIG,
        processing_config_bytes=_CONFIG_BYTES,
        processing_manifest_bytes=processing_manifest,
        split_manifest_bytes=manifest,
        membership_bytes=membership,
        role_input_bytes=role_bytes or _role_bytes(),
        hidden_truth_bytes=hidden_truth,
    )


def test_projection_reconstructs_expected_membership_and_development_exact_cover() -> None:
    projected = _project()

    assert projected.expected_role_memberships == {
        role: (_CANDIDATES[role],) for role in ("development", "validation", "candidate-pool")
    }
    assert tuple(item.candidate_id for item in projected.development_observations) == (
        _CANDIDATES["development"],
    )
    assert not hasattr(projected, "held_out")


def test_projection_accepts_schema_valid_split_without_optional_row_count() -> None:
    membership = _membership_bytes()
    hidden_truth = _hidden_truth_bytes()
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    split_manifest = _split_manifest_bytes(
        membership,
        processing_manifest_sha256=processing_sha256,
        include_row_count=False,
    )

    projected = project_checked_inputs(
        identity=_identity(
            split_manifest,
            processing_manifest_sha256=processing_sha256,
        ),
        processing_config=_CONFIG,
        processing_config_bytes=_CONFIG_BYTES,
        processing_manifest_bytes=processing_manifest,
        split_manifest_bytes=split_manifest,
        membership_bytes=membership,
        role_input_bytes=_role_bytes(),
        hidden_truth_bytes=hidden_truth,
    )

    assert len(projected.development_observations) == 1


def test_projection_rejects_unknown_split_membership_reference_field() -> None:
    membership = _membership_bytes()
    hidden_truth = _hidden_truth_bytes()
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    manifest_value = json.loads(
        _split_manifest_bytes(
            membership,
            processing_manifest_sha256=processing_sha256,
        )
    )
    manifest_value["membership_file"]["etag"] = "uncontracted"
    split_manifest = _json_bytes(manifest_value)

    with pytest.raises(ContractValidationError, match="reference fields drifted"):
        project_checked_inputs(
            identity=_identity(
                split_manifest,
                processing_manifest_sha256=processing_sha256,
            ),
            processing_config=_CONFIG,
            processing_config_bytes=_CONFIG_BYTES,
            processing_manifest_bytes=processing_manifest,
            split_manifest_bytes=split_manifest,
            membership_bytes=membership,
            role_input_bytes=_role_bytes(),
            hidden_truth_bytes=hidden_truth,
        )


@pytest.mark.parametrize("forbidden", ["target", "truth", "high_value", "publisher_label"])
def test_model_role_records_reject_truth_or_truth_derived_fields(forbidden: str) -> None:
    with pytest.raises(ContractValidationError, match="role record fields drifted"):
        _project(role_bytes=_role_bytes(extra_field=forbidden))


def test_projection_rejects_changed_truth_value_without_echoing_it() -> None:
    private_value = "987654321.25"
    changed = _hidden_truth_bytes().replace(b"1.25", private_value.encode(), 1)

    with pytest.raises(ContractValidationError, match="exact bytes") as raised:
        _project(hidden_truth_bytes=changed)

    assert private_value not in str(raised.value)


def test_projection_rejects_missing_role_candidate() -> None:
    with pytest.raises(ContractValidationError, match="role input fields drifted"):
        _project(role_bytes=_role_bytes(omit="validation"))


@pytest.mark.parametrize("failure", ["unknown", "duplicate", "cross-role"])
def test_projection_rejects_unknown_duplicate_or_cross_role_candidate(failure: str) -> None:
    value = json.loads(_role_bytes())
    if failure == "unknown":
        value["roles"]["candidate-pool"][0]["candidate_id"] = "f" * 64
    elif failure == "duplicate":
        value["roles"]["candidate-pool"].append(value["roles"]["candidate-pool"][0])
    else:
        value["roles"]["validation"][0] = value["roles"]["development"][0]

    with pytest.raises(ContractValidationError):
        _project(role_bytes=_json_bytes(value))


def test_projection_rejects_cross_role_duplicate_membership() -> None:
    membership = _membership_bytes(duplicate=True)

    with pytest.raises(ContractValidationError, match="split membership candidate IDs"):
        _project(membership_bytes=membership)


def test_projection_rejects_unknown_candidate_and_processing_identity_mismatch() -> None:
    value = json.loads(_role_bytes())
    value["roles"]["candidate-pool"][0]["sequence"] = "TVWY"

    with pytest.raises(ContractValidationError, match="processing config"):
        _project(role_bytes=_json_bytes(value))


def test_projection_rejects_config_object_not_derived_from_exact_config_bytes() -> None:
    membership = _membership_bytes()
    hidden_truth = _hidden_truth_bytes()
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    manifest = _split_manifest_bytes(
        membership,
        processing_manifest_sha256=processing_sha256,
    )
    drifted_config = replace(_CONFIG, candidate_id_namespace="public-fixture:v1:drifted")

    with pytest.raises(ContractValidationError, match="processing config fields"):
        project_checked_inputs(
            identity=_identity(manifest, processing_manifest_sha256=processing_sha256),
            processing_config=drifted_config,
            processing_config_bytes=_CONFIG_BYTES,
            processing_manifest_bytes=processing_manifest,
            split_manifest_bytes=manifest,
            membership_bytes=membership,
            role_input_bytes=_role_bytes(),
            hidden_truth_bytes=hidden_truth,
        )


def test_projection_rejects_split_or_processing_identity_drift() -> None:
    membership = _membership_bytes()
    hidden_truth = _hidden_truth_bytes()
    processing_manifest = _processing_manifest_bytes(hidden_truth)
    processing_sha256 = hashlib.sha256(processing_manifest).hexdigest()
    manifest = _split_manifest_bytes(
        membership,
        processing_manifest_sha256=processing_sha256,
    )
    identity = _identity(manifest, processing_manifest_sha256=processing_sha256)
    drifted_identity = ProjectionIdentity(
        benchmark_task_id=identity.benchmark_task_id,
        dataset_manifest_sha256=identity.dataset_manifest_sha256,
        processing_manifest_sha256=identity.processing_manifest_sha256,
        split_sha256="f" * 64,
        processing_config_sha256=identity.processing_config_sha256,
        role_counts=identity.role_counts,
        dataset_id=identity.dataset_id,
        dataset_version=identity.dataset_version,
        dataset_task_id=identity.dataset_task_id,
        candidate_id_namespace=identity.candidate_id_namespace,
    )

    with pytest.raises(ContractValidationError, match="split identity"):
        project_checked_inputs(
            identity=drifted_identity,
            processing_config=_CONFIG,
            processing_config_bytes=_CONFIG_BYTES,
            processing_manifest_bytes=processing_manifest,
            split_manifest_bytes=manifest,
            membership_bytes=membership,
            role_input_bytes=_role_bytes(),
            hidden_truth_bytes=hidden_truth,
        )
