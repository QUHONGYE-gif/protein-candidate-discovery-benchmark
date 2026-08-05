"""Public synthetic checks for the Issue #58 real-input projection boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pytest

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.datasets.processing import candidate_id
from pcdb.m2.projection import ProjectedInputs, ProjectionIdentity, project_checked_inputs


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


def _split_manifest_bytes(membership_bytes: bytes) -> bytes:
    return _json_bytes(
        {
            "held_out_partitions": ["held-out"],
            "membership_file": {
                "object_key": "splits/public-fixture/membership.csv",
                "row_count": len(membership_bytes.decode().splitlines()) - 1,
                "sha256": hashlib.sha256(membership_bytes).hexdigest(),
                "size_bytes": len(membership_bytes),
            },
            "partitions": ["candidate-pool", "development", "validation", "held-out"],
            "processing_manifest_sha256": "b" * 64,
            "schema_version": "1",
        }
    )


def _identity(split_manifest_bytes: bytes) -> ProjectionIdentity:
    return ProjectionIdentity(
        benchmark_task_id="public-fixture-mixed-role",
        dataset_manifest_sha256="a" * 64,
        processing_manifest_sha256="b" * 64,
        split_sha256=hashlib.sha256(split_manifest_bytes).hexdigest(),
        processing_config_sha256=hashlib.sha256(_CONFIG_BYTES).hexdigest(),
        role_counts={"development": 1, "validation": 1, "candidate-pool": 1},
        dataset_id="public-fixture",
        dataset_version="v1",
        dataset_task_id="mixed-role",
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


def _observation_bytes(*, role: str = "development") -> bytes:
    return _json_bytes(
        {
            role: [
                {
                    "candidate_id": _CANDIDATES[role],
                    "observation": 1.25,
                }
            ]
        }
    )


def _project(
    *,
    membership_bytes: bytes | None = None,
    role_bytes: bytes | None = None,
    observation_bytes: bytes | None = None,
) -> ProjectedInputs:
    membership = membership_bytes or _membership_bytes()
    manifest = _split_manifest_bytes(membership)
    return project_checked_inputs(
        identity=_identity(manifest),
        processing_config=_CONFIG,
        processing_config_bytes=_CONFIG_BYTES,
        split_manifest_bytes=manifest,
        membership_bytes=membership,
        role_input_bytes=role_bytes or _role_bytes(),
        development_observation_bytes=observation_bytes or _observation_bytes(),
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


@pytest.mark.parametrize("forbidden", ["target", "truth", "high_value", "publisher_label"])
def test_model_role_records_reject_truth_or_truth_derived_fields(forbidden: str) -> None:
    with pytest.raises(ContractValidationError, match="role record fields drifted"):
        _project(role_bytes=_role_bytes(extra_field=forbidden))


def test_projection_rejects_non_development_observations_without_echoing_values() -> None:
    private_value = "private-candidate-value-must-not-escape"
    payload = json.loads(_observation_bytes(role="candidate-pool"))
    payload["candidate-pool"][0]["observation"] = private_value

    with pytest.raises(ContractValidationError) as raised:
        _project(observation_bytes=_json_bytes(payload))

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


def test_projection_rejects_split_or_processing_identity_drift() -> None:
    membership = _membership_bytes()
    manifest = _split_manifest_bytes(membership)
    identity = _identity(manifest)
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
    )

    with pytest.raises(ContractValidationError, match="split identity"):
        project_checked_inputs(
            identity=drifted_identity,
            processing_config=_CONFIG,
            processing_config_bytes=_CONFIG_BYTES,
            split_manifest_bytes=manifest,
            membership_bytes=membership,
            role_input_bytes=_role_bytes(),
            development_observation_bytes=_observation_bytes(),
        )
