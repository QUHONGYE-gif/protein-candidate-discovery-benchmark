"""Prediction-only composition for the bounded synthetic M2 implementation."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError, LoadedContract
from pcdb.contracts.feature_identity import (
    LoadedFeatureMatrix,
    canonical_feature_json_bytes,
    load_feature_matrix,
)
from pcdb.contracts.prediction import (
    M2_STATIC_ACQUISITION_CONFIG_SHA256,
    M2_STATIC_ACQUISITION_ID,
    M2StaticProducerInputs,
    load_prediction_artifact,
    validate_m2_static_profile,
)
from pcdb.contracts.run import RunSpec
from pcdb.m2.features import SequenceFeatureBundle
from pcdb.m2.models import (
    DevelopmentObservation,
    fit_regressor,
    load_model_config,
    model_config_bytes,
    predict_regressor,
)

SYNTHETIC_ATTEMPT_ID = "synthetic-attempt-001"
SYNTHETIC_SEED = 1729


def _descriptor_matrix(
    inputs: M2StaticProducerInputs,
    role: str,
) -> LoadedFeatureMatrix:
    matrices = inputs.feature_descriptor.value["matrices"]
    matrix = next((item for item in matrices if item["role"] == role), None)
    if matrix is None:
        raise ContractValidationError(f"feature descriptor is missing the {role} matrix")
    artifact = matrix["artifact"]
    object_key = cast(str, artifact["object_key"])
    payload = inputs.feature_member_bytes.get(object_key)
    if payload is None:
        raise ContractValidationError(f"feature members are missing the {role} matrix")
    return load_feature_matrix(
        payload,
        feature_definitions=cast(
            Sequence[Mapping[str, Any]],
            inputs.feature_descriptor.value["feature_definitions"],
        ),
        expected_sha256=cast(str, artifact["sha256"]),
    )


def produce_static_prediction(
    inputs: M2StaticProducerInputs,
    development_observations: Sequence[DevelopmentObservation],
) -> bytes:
    """Fit from explicit development observations and emit canonical candidate predictions."""

    validate_m2_static_profile(inputs.run_spec, inputs.experiment_config)
    if inputs.feature_descriptor.sha256 != inputs.run_spec.feature_sha256:
        raise ContractValidationError("feature descriptor root does not match RunSpec")
    if hashlib.sha256(inputs.model_config_bytes).hexdigest() != (
        inputs.run_spec.model_config_sha256
    ):
        raise ContractValidationError("model config does not match RunSpec")
    model_config = load_model_config(inputs.model_config_bytes)
    if model_config.model_id != inputs.run_spec.model_id:
        raise ContractValidationError("model config model_id does not match RunSpec")

    development = _descriptor_matrix(inputs, "development")
    candidates = _descriptor_matrix(inputs, "candidate-pool")
    if candidates.candidate_ids != inputs.candidate_ids:
        raise ContractValidationError("candidate matrix does not exact-cover producer membership")
    fitted = fit_regressor(
        inputs.model_config_bytes,
        development,
        development_observations,
        seed=inputs.run_spec.seed,
    )
    prediction_rows = predict_regressor(fitted, candidates)
    config = inputs.experiment_config.value
    payload = canonical_feature_json_bytes(
        {
            "acquisition_config_sha256": inputs.run_spec.acquisition_config_sha256,
            "acquisition_id": inputs.run_spec.acquisition_id,
            "attempt_id": inputs.attempt_id,
            "candidate_count": len(prediction_rows),
            "candidate_role": "candidate-pool",
            "dataset_manifest_sha256": inputs.run_spec.dataset_manifest_sha256,
            "experiment_config_sha256": inputs.experiment_config.sha256,
            "feature_sha256": inputs.run_spec.feature_sha256,
            "model_config_sha256": inputs.run_spec.model_config_sha256,
            "model_id": inputs.run_spec.model_id,
            "predictions": [
                {
                    "candidate_id": candidate_id,
                    "prediction": struct.pack(">d", prediction).hex(),
                }
                for candidate_id, prediction in prediction_rows
            ],
            "processing_manifest_sha256": inputs.run_spec.processing_manifest_sha256,
            "protocol_version": inputs.run_spec.protocol_version,
            "run_id": inputs.run_spec.run_id,
            "schema_version": "m2-prediction-artifact-v1",
            "seed": inputs.run_spec.seed,
            "split_sha256": inputs.run_spec.split_sha256,
            "task_id": config["task_id"],
        }
    )
    loaded = load_prediction_artifact(payload)
    if loaded.candidate_ids != inputs.candidate_ids:
        raise ContractValidationError("prediction does not exact-cover candidate membership")
    return payload


def make_static_prediction_producer(
    development_observations: Sequence[DevelopmentObservation],
) -> Callable[[M2StaticProducerInputs], bytes]:
    """Bind an immutable synthetic development-observation capability."""

    bound_observations = tuple(development_observations)

    def producer(inputs: M2StaticProducerInputs) -> bytes:
        return produce_static_prediction(inputs, bound_observations)

    return producer


def build_preflight_run_spec(
    *,
    bundle: SequenceFeatureBundle,
    experiment_config: LoadedContract,
    model_id: str,
    git_commit: str,
    environment_digest: str,
    seed: int = SYNTHETIC_SEED,
) -> RunSpec:
    """Build a pre-formal RunSpec bound to one validated feature root."""

    config_bytes = model_config_bytes(model_id)
    run_spec = RunSpec(
        schema_version="1",
        protocol_version="m2-alpha-ired-static-v1",
        git_commit=git_commit,
        environment_digest=environment_digest,
        dataset_manifest_sha256=cast(
            str,
            bundle.logical_artifact.value["dataset_manifest_sha256"],
        ),
        processing_manifest_sha256=cast(
            str,
            bundle.logical_artifact.value["processing_manifest_sha256"],
        ),
        split_sha256=cast(str, bundle.logical_artifact.value["split_sha256"]),
        feature_sha256=bundle.descriptor.sha256,
        experiment_config_sha256=experiment_config.sha256,
        model_id=model_id,
        model_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        acquisition_id=M2_STATIC_ACQUISITION_ID,
        acquisition_config_sha256=M2_STATIC_ACQUISITION_CONFIG_SHA256,
        seed=seed,
        initial_sample_size=1,
        batch_size=1,
        budget=1,
    )
    validate_m2_static_profile(run_spec, experiment_config)
    return run_spec


def build_synthetic_run_spec(
    *,
    bundle: SequenceFeatureBundle,
    experiment_config: LoadedContract,
    model_id: str,
    git_commit: str,
    environment_digest: str,
    seed: int = SYNTHETIC_SEED,
) -> RunSpec:
    """Backward-compatible synthetic wrapper around the preflight RunSpec builder."""

    return build_preflight_run_spec(
        bundle=bundle,
        experiment_config=experiment_config,
        model_id=model_id,
        git_commit=git_commit,
        environment_digest=environment_digest,
        seed=seed,
    )
