from __future__ import annotations

import hashlib
import json

import pytest

from pcdb.contracts.experiment import ContractValidationError
from pcdb.contracts.feature_identity import canonical_feature_json_bytes
from pcdb.contracts.prediction import (
    M2_STATIC_ACQUISITION_CONFIG_BYTES,
    M2_STATIC_ACQUISITION_CONFIG_SHA256,
    load_prediction_artifact,
)


def _candidate_ids(count: int) -> list[str]:
    return sorted(
        hashlib.sha256(f"candidate:{index}".encode()).hexdigest() for index in range(count)
    )


def _prediction_bytes() -> bytes:
    return canonical_feature_json_bytes(
        {
            "schema_version": "m2-prediction-artifact-v1",
            "protocol_version": "m2-alpha-ired-static-v1",
            "experiment_config_sha256": "1" * 64,
            "dataset_manifest_sha256": (
                "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac"
            ),
            "processing_manifest_sha256": (
                "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec"
            ),
            "split_sha256": ("c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a"),
            "feature_sha256": "2" * 64,
            "run_id": "3" * 64,
            "attempt_id": "attempt-001",
            "task_id": "alpha-amylase",
            "candidate_role": "candidate-pool",
            "candidate_count": 2262,
            "model_id": "synthetic-model",
            "model_config_sha256": "4" * 64,
            "acquisition_id": "m2-static-full-pool-v1",
            "acquisition_config_sha256": (
                "d50c876c6d114eb74a878bbce43a2f760e21507dad80c0d0f87ce02f2ff7c9d5"
            ),
            "seed": 0,
            "predictions": [
                {"candidate_id": candidate_id, "prediction": "0000000000000000"}
                for candidate_id in _candidate_ids(2262)
            ],
        }
    )


def test_static_acquisition_config_has_approved_exact_identity() -> None:
    assert len(M2_STATIC_ACQUISITION_CONFIG_BYTES) == 186
    assert (
        hashlib.sha256(M2_STATIC_ACQUISITION_CONFIG_BYTES).hexdigest()
        == M2_STATIC_ACQUISITION_CONFIG_SHA256
        == "d50c876c6d114eb74a878bbce43a2f760e21507dad80c0d0f87ce02f2ff7c9d5"
    )


def test_prediction_loader_accepts_canonical_exact_cover() -> None:
    payload = _prediction_bytes()

    loaded = load_prediction_artifact(payload)

    assert loaded.canonical_bytes == payload
    assert loaded.sha256 == hashlib.sha256(payload).hexdigest()
    assert len(loaded.candidate_ids) == 2262
    assert loaded.predictions[0] == 0.0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value.update({"unknown": "forbidden"}),
            "fields drifted",
        ),
        (
            lambda value: value["predictions"].reverse(),
            "strictly ASCII ascending",
        ),
        (
            lambda value: value["predictions"][0].update(
                {"candidate_id": value["predictions"][1]["candidate_id"]}
            ),
            "unique",
        ),
        (
            lambda value: value["predictions"][0].update({"prediction": "7ff0000000000000"}),
            "finite",
        ),
    ],
)
def test_prediction_loader_rejects_closed_domain_drift(
    mutation: object,
    message: str,
) -> None:
    value = json.loads(_prediction_bytes())
    mutation(value)  # type: ignore[operator]

    with pytest.raises(ContractValidationError, match=message):
        load_prediction_artifact(canonical_feature_json_bytes(value))


def test_prediction_loader_rejects_noncanonical_bytes_and_hash_drift() -> None:
    payload = _prediction_bytes()

    with pytest.raises(ContractValidationError, match="canonical"):
        load_prediction_artifact(payload[:-1])
    with pytest.raises(ContractValidationError, match="SHA-256"):
        load_prediction_artifact(payload, expected_sha256="0" * 64)


@pytest.mark.parametrize(
    "payload",
    [
        b"\xef\xbb\xbf" + _prediction_bytes(),
        b"\xff" + _prediction_bytes(),
    ],
)
def test_prediction_loader_rejects_bom_and_invalid_utf8(payload: bytes) -> None:
    with pytest.raises(ContractValidationError, match="UTF-8"):
        load_prediction_artifact(payload)


def test_prediction_loader_rejects_duplicate_json_keys() -> None:
    duplicate = _prediction_bytes().replace(
        b'  "attempt_id":',
        b'  "attempt_id": "duplicate",\n  "attempt_id":',
        1,
    )

    with pytest.raises(ContractValidationError, match="duplicate"):
        load_prediction_artifact(duplicate)
