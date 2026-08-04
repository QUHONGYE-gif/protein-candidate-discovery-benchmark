"""Canonical static M2 prediction artifact and evidence contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from types import MappingProxyType
from typing import Any, cast

from pcdb.contracts.experiment import (
    ContractValidationError,
    LoadedContract,
    load_experiment_config,
    load_high_value_result,
)
from pcdb.contracts.feature import M2_FEATURE_TASK_BINDINGS
from pcdb.contracts.feature_identity import (
    PROTOCOL_VERSION,
    canonical_feature_json_bytes,
    validate_feature_bundle_identity,
)
from pcdb.contracts.high_value import MetricResultEvidence, validate_metric_result_evidence
from pcdb.contracts.run import RunSpec

PREDICTION_SCHEMA_VERSION = "m2-prediction-artifact-v1"
M2_STATIC_ACQUISITION_ID = "m2-static-full-pool-v1"
M2_STATIC_ACQUISITION_CONFIG_SHA256 = (
    "d50c876c6d114eb74a878bbce43a2f760e21507dad80c0d0f87ce02f2ff7c9d5"
)
M2_STATIC_SENTINEL = 1

_STATIC_ACQUISITION_CONFIG = {
    "acquisition_id": M2_STATIC_ACQUISITION_ID,
    "prediction_scope": "candidate-pool-exact-cover",
    "schema_version": "m2-static-acquisition-config-v1",
    "selection_policy": "none",
}
M2_STATIC_ACQUISITION_CONFIG_BYTES = canonical_feature_json_bytes(_STATIC_ACQUISITION_CONFIG)
if (
    len(M2_STATIC_ACQUISITION_CONFIG_BYTES) != 186
    or hashlib.sha256(M2_STATIC_ACQUISITION_CONFIG_BYTES).hexdigest()
    != M2_STATIC_ACQUISITION_CONFIG_SHA256
):
    raise RuntimeError("frozen M2-static acquisition config identity drifted")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FLOAT64_BITS = re.compile(r"^[0-9a-f]{16}$")
_RFC3339 = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_PREDICTION_FIELDS = {
    "acquisition_config_sha256",
    "acquisition_id",
    "attempt_id",
    "candidate_count",
    "candidate_role",
    "dataset_manifest_sha256",
    "experiment_config_sha256",
    "feature_sha256",
    "model_config_sha256",
    "model_id",
    "predictions",
    "processing_manifest_sha256",
    "protocol_version",
    "run_id",
    "schema_version",
    "seed",
    "split_sha256",
    "task_id",
}
_PREDICTION_ROW_FIELDS = {"candidate_id", "prediction"}
_RUN_SPEC_FIELDS = {field.name for field in fields(RunSpec)}
_RUN_ATTEMPT_REQUIRED_FIELDS = {
    "attempt_id",
    "compute",
    "executed_environment_digest",
    "executed_git_commit",
    "outputs",
    "run_id",
    "schema_version",
    "started_at",
    "status",
}
_RUN_ATTEMPT_OPTIONAL_FIELDS = {
    "completion_marker",
    "failure",
    "finished_at",
}
_ARTIFACT_FIELDS = {"object_key", "sha256", "size_bytes"}
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_M2_METRIC_IDENTITIES = frozenset(
    {
        ("spearman_rank_correlation", None),
        ("high_value_recall_at_nominal_k", "upper-tail-1pct"),
        ("high_value_recall_at_nominal_k", "upper-tail-0.5pct"),
        ("high_value_recall_at_nominal_k", "upper-tail-2pct"),
    }
)


@dataclass(frozen=True, slots=True)
class LoadedPredictionArtifact:
    """One strict-decoded canonical prediction artifact."""

    value: Mapping[str, Any]
    candidate_ids: tuple[str, ...]
    predictions: tuple[float, ...]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class LoadedRunAttempt:
    """One fully schema/domain-validated RunAttempt v1."""

    value: Mapping[str, Any]
    exact_bytes: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class M2StaticPredictionEvidence:
    """Validated fresh-read evidence for one complete M2-static attempt."""

    run_spec: RunSpec
    experiment_config: LoadedContract
    feature_descriptor: LoadedContract
    run_attempt: LoadedRunAttempt
    prediction: LoadedPredictionArtifact
    metric_results: tuple[LoadedContract, ...]


@dataclass(frozen=True, slots=True)
class M2StaticProducerInputs:
    """Truth-free capabilities made available to a point-prediction producer."""

    run_spec: RunSpec
    experiment_config: LoadedContract
    feature_descriptor: LoadedContract
    feature_member_bytes: Mapping[str, bytes]
    candidate_ids: tuple[str, ...]
    attempt_id: str
    model_config_bytes: bytes


@dataclass(frozen=True, slots=True)
class M2StaticEvaluatorInputs:
    """Fresh sealed prediction and public identities made available to an evaluator."""

    run_spec: RunSpec
    experiment_config: LoadedContract
    prediction_bytes: bytes
    prediction_sha256: str
    candidate_ids: tuple[str, ...]
    attempt_id: str


@dataclass(frozen=True, slots=True)
class M2StaticOrchestrationOutputs:
    """Sealed prediction plus aggregate-only evaluator result byte streams."""

    prediction: LoadedPredictionArtifact
    metric_result_bytes: Mapping[str, bytes]


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _reject_constant(value: str) -> None:
    raise ContractValidationError(f"non-finite JSON constant is forbidden: {value}")


def _reject_float(value: str) -> None:
    raise ContractValidationError(f"JSON floating-point numbers are forbidden: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractValidationError(f"duplicate JSON key is forbidden: {key}")
        value[key] = item
    return value


def _parse_json_object(payload: bytes, field: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{field} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be a JSON object")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractValidationError(f"{field} fields drifted; missing={missing}, extra={extra}")


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{field} must be a non-empty string")
    return value


def _require_identifier(value: Any, field: str) -> str:
    text = _require_string(value, field)
    if not _IDENTIFIER.fullmatch(text):
        raise ContractValidationError(f"{field} must be a stable lowercase identifier")
    return text


def _require_sha256(value: Any, field: str) -> str:
    text = _require_string(value, field)
    if not _SHA256.fullmatch(text):
        raise ContractValidationError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"{field} must be a non-negative integer")
    return value


def _decode_float64_bits(value: Any, field: str) -> float:
    if not isinstance(value, str) or not _FLOAT64_BITS.fullmatch(value):
        raise ContractValidationError(f"{field} must be 16 lower-case float64 hex characters")
    bits = int(value, 16)
    if bits & 0x7FF0000000000000 == 0x7FF0000000000000:
        raise ContractValidationError(f"{field} must encode a finite float64")
    decoded = cast(float, struct.unpack(">d", bytes.fromhex(value))[0])
    if not math.isfinite(decoded):
        raise ContractValidationError(f"{field} must encode a finite float64")
    return decoded


def load_prediction_artifact(
    payload: bytes,
    *,
    expected_sha256: str | None = None,
) -> LoadedPredictionArtifact:
    """Strict-decode one canonical full-pool M2 point-prediction artifact."""

    value = _parse_json_object(payload, "prediction artifact")
    if canonical_feature_json_bytes(value) != payload:
        raise ContractValidationError("prediction artifact bytes are not pcdb-canonical-json-v1")
    _require_exact_keys(value, _PREDICTION_FIELDS, "prediction artifact")
    if value["schema_version"] != PREDICTION_SCHEMA_VERSION:
        raise ContractValidationError("prediction artifact schema_version is invalid")
    if value["protocol_version"] != PROTOCOL_VERSION:
        raise ContractValidationError("prediction artifact protocol_version is invalid")
    task_id = _require_string(value["task_id"], "task_id")
    binding = M2_FEATURE_TASK_BINDINGS.get(task_id)
    if binding is None:
        raise ContractValidationError("prediction task_id is unknown")
    for field in (
        "experiment_config_sha256",
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
        "feature_sha256",
        "run_id",
        "model_config_sha256",
        "acquisition_config_sha256",
    ):
        _require_sha256(value[field], field)
    for field in (
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ):
        if value[field] != binding[field]:
            raise ContractValidationError(f"prediction {field} does not match task binding")
    _require_identifier(value["attempt_id"], "attempt_id")
    _require_string(value["model_id"], "model_id")
    if value["candidate_role"] != "candidate-pool":
        raise ContractValidationError("prediction candidate_role must be candidate-pool")
    expected_count = binding["role_counts"]["candidate-pool"]
    if value["candidate_count"] != expected_count:
        raise ContractValidationError("prediction candidate_count does not match task binding")
    if value["acquisition_id"] != M2_STATIC_ACQUISITION_ID:
        raise ContractValidationError("prediction acquisition_id is not the static singleton")
    if value["acquisition_config_sha256"] != M2_STATIC_ACQUISITION_CONFIG_SHA256:
        raise ContractValidationError("prediction acquisition config identity drifted")
    _require_nonnegative_int(value["seed"], "seed")
    rows = value["predictions"]
    if not isinstance(rows, list) or not rows:
        raise ContractValidationError("predictions must be a non-empty array")
    if len(rows) != expected_count:
        raise ContractValidationError("prediction rows do not match candidate_count")
    candidate_ids: list[str] = []
    predictions: list[float] = []
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, Mapping):
            raise ContractValidationError(f"predictions[{index}] must be an object")
        _require_exact_keys(raw_row, _PREDICTION_ROW_FIELDS, f"predictions[{index}]")
        candidate_id = _require_sha256(
            raw_row["candidate_id"],
            f"predictions[{index}].candidate_id",
        )
        candidate_ids.append(candidate_id)
        predictions.append(
            _decode_float64_bits(
                raw_row["prediction"],
                f"predictions[{index}].prediction",
            )
        )
    if candidate_ids != sorted(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise ContractValidationError(
            "prediction candidate IDs must be unique and strictly ASCII ascending"
        )
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractValidationError("prediction artifact SHA-256 does not match")
    return LoadedPredictionArtifact(
        value=_deep_freeze(value),
        candidate_ids=tuple(candidate_ids),
        predictions=tuple(predictions),
        canonical_bytes=payload,
        sha256=digest,
    )


def validate_m2_static_profile(
    run_spec: RunSpec,
    experiment_config: LoadedContract,
) -> None:
    """Validate the one approved M2-static RunSpec/Experiment Config profile."""

    if run_spec.schema_version != "1" or run_spec.protocol_version != PROTOCOL_VERSION:
        raise ContractValidationError("RunSpec does not use the approved M2-static profile")
    if experiment_config.schema_version != "2":
        raise ContractValidationError("M2-static profile requires Experiment Config v2")
    config = experiment_config.value
    if run_spec.experiment_config_sha256 != experiment_config.sha256:
        raise ContractValidationError("RunSpec experiment config SHA-256 does not match")
    expected_run_fields = {
        "acquisition_id": M2_STATIC_ACQUISITION_ID,
        "acquisition_config_sha256": M2_STATIC_ACQUISITION_CONFIG_SHA256,
        "initial_sample_size": M2_STATIC_SENTINEL,
        "batch_size": M2_STATIC_SENTINEL,
        "budget": M2_STATIC_SENTINEL,
    }
    for field, expected in expected_run_fields.items():
        if getattr(run_spec, field) != expected:
            raise ContractValidationError(f"RunSpec {field} is outside the M2-static profile")
    expected_config_fields = {
        "acquisitions": (M2_STATIC_ACQUISITION_ID,),
        "initial_sample_sizes": (M2_STATIC_SENTINEL,),
        "batch_sizes": (M2_STATIC_SENTINEL,),
        "budgets": (M2_STATIC_SENTINEL,),
    }
    for field, expected in expected_config_fields.items():
        if tuple(config[field]) != expected:
            raise ContractValidationError(
                f"Experiment Config {field} is outside the M2-static profile"
            )
    if run_spec.model_id not in config["models"] or run_spec.seed not in config["seeds"]:
        raise ContractValidationError("RunSpec model/seed is not registered by Experiment Config")
    if config["held_out_authorized"] is not False:
        raise ContractValidationError("M2-static contract slice forbids held-out authorization")
    for field in (
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ):
        if getattr(run_spec, field) != config[field]:
            raise ContractValidationError(f"RunSpec {field} does not match Experiment Config")


def load_run_spec_v1(payload: bytes) -> RunSpec:
    """Strict-decode exact canonical RunSpec v1 bytes."""

    value = _parse_json_object(payload, "RunSpec")
    _require_exact_keys(value, _RUN_SPEC_FIELDS, "RunSpec")
    for field in ("seed", "initial_sample_size", "batch_size", "budget"):
        if isinstance(value[field], bool) or not isinstance(value[field], int):
            raise ContractValidationError(f"RunSpec {field} must be an integer")
    try:
        run_spec = RunSpec(**value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("RunSpec v1 domain validation failed") from exc
    if run_spec.canonical_json().encode("utf-8") != payload:
        raise ContractValidationError("RunSpec bytes are not canonical")
    return run_spec


def _require_datetime(value: Any, field: str) -> None:
    text = _require_string(value, field)
    if not _RFC3339.fullmatch(text):
        raise ContractValidationError(f"{field} must be an RFC 3339 date-time")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(f"{field} must be an RFC 3339 date-time") from exc
    if parsed.tzinfo is None:
        raise ContractValidationError(f"{field} must include a timezone")


def _validate_artifact_reference(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be an artifact object")
    _require_exact_keys(value, _ARTIFACT_FIELDS, field)
    _require_string(value["object_key"], f"{field}.object_key")
    _require_sha256(value["sha256"], f"{field}.sha256")
    _require_nonnegative_int(value["size_bytes"], f"{field}.size_bytes")
    return value


def load_run_attempt_v1(
    payload: bytes,
    *,
    expected_sha256: str | None = None,
) -> LoadedRunAttempt:
    """Strict-parse and fully validate one RunAttempt v1 before evidence checks."""

    value = _parse_json_object(payload, "RunAttempt")
    actual_fields = set(value)
    if not actual_fields >= _RUN_ATTEMPT_REQUIRED_FIELDS or not actual_fields <= (
        _RUN_ATTEMPT_REQUIRED_FIELDS | _RUN_ATTEMPT_OPTIONAL_FIELDS
    ):
        raise ContractValidationError("RunAttempt required/allowed fields drifted")
    if value["schema_version"] != "1":
        raise ContractValidationError("RunAttempt schema_version must be '1'")
    _require_sha256(value["run_id"], "RunAttempt.run_id")
    _require_identifier(value["attempt_id"], "RunAttempt.attempt_id")
    if value["status"] not in {"running", "succeeded", "failed"}:
        raise ContractValidationError("RunAttempt status is invalid")
    _require_datetime(value["started_at"], "RunAttempt.started_at")
    if "finished_at" in value:
        _require_datetime(value["finished_at"], "RunAttempt.finished_at")
    if not isinstance(value["executed_git_commit"], str) or not _GIT_COMMIT.fullmatch(
        value["executed_git_commit"]
    ):
        raise ContractValidationError("RunAttempt executed_git_commit is invalid")
    _require_sha256(
        value["executed_environment_digest"],
        "RunAttempt.executed_environment_digest",
    )
    compute = value["compute"]
    if not isinstance(compute, dict):
        raise ContractValidationError("RunAttempt compute must be an object")
    required_compute = {"executor_type", "runtime_version"}
    allowed_compute = required_compute | {"accelerator", "region"}
    if not required_compute <= set(compute) or not set(compute) <= allowed_compute:
        raise ContractValidationError("RunAttempt compute fields drifted")
    if compute["executor_type"] not in {"local", "compute_server"}:
        raise ContractValidationError("RunAttempt executor_type is invalid")
    _require_string(compute["runtime_version"], "RunAttempt runtime_version")
    for field in ("accelerator", "region"):
        if field in compute:
            _require_string(compute[field], f"RunAttempt {field}")
    outputs = value["outputs"]
    if not isinstance(outputs, list):
        raise ContractValidationError("RunAttempt outputs must be an array")
    for index, output in enumerate(outputs):
        _validate_artifact_reference(output, f"RunAttempt.outputs[{index}]")
    output_keys = [output["object_key"] for output in outputs]
    if len(output_keys) != len(set(output_keys)):
        raise ContractValidationError("RunAttempt output object keys must be unique")
    if "completion_marker" in value:
        marker = _validate_artifact_reference(
            value["completion_marker"],
            "RunAttempt.completion_marker",
        )
        if (
            not marker["object_key"].endswith("/_SUCCESS")
            or marker["sha256"] != _EMPTY_SHA256
            or marker["size_bytes"] != 0
        ):
            raise ContractValidationError("RunAttempt completion marker is invalid")
    if "failure" in value:
        failure = value["failure"]
        if not isinstance(failure, dict):
            raise ContractValidationError("RunAttempt failure must be an object")
        _require_exact_keys(failure, {"code", "message", "retryable"}, "RunAttempt.failure")
        _require_string(failure["code"], "RunAttempt.failure.code")
        _require_string(failure["message"], "RunAttempt.failure.message")
        if not isinstance(failure["retryable"], bool):
            raise ContractValidationError("RunAttempt failure.retryable must be boolean")
    status = value["status"]
    if status == "running":
        if {"finished_at", "completion_marker", "failure"} & set(value):
            raise ContractValidationError("running RunAttempt contains terminal fields")
    elif status == "succeeded":
        if (
            "finished_at" not in value
            or "completion_marker" not in value
            or "failure" in value
            or not outputs
        ):
            raise ContractValidationError("succeeded RunAttempt terminal state is invalid")
    elif "finished_at" not in value or "failure" not in value or "completion_marker" in value:
        raise ContractValidationError("failed RunAttempt terminal state is invalid")
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractValidationError("RunAttempt SHA-256 does not match")
    return LoadedRunAttempt(
        value=_deep_freeze(value),
        exact_bytes=payload,
        sha256=digest,
    )


def _expect_exact(actual: Any, expected: Any, field: str) -> None:
    if actual != expected:
        raise ContractValidationError(f"{field} does not exact-match")


def _output_reference(
    attempt: LoadedRunAttempt,
    object_key: str,
    *,
    field: str,
) -> Mapping[str, Any]:
    _require_string(object_key, field)
    matches = [output for output in attempt.value["outputs"] if output["object_key"] == object_key]
    if len(matches) != 1:
        raise ContractValidationError(
            f"RunAttempt outputs must contain exactly one {field} reference"
        )
    return cast(Mapping[str, Any], matches[0])


def _validate_prediction_identity(
    prediction: LoadedPredictionArtifact,
    *,
    run_spec: RunSpec,
    experiment_config: LoadedContract,
    attempt_id: str,
    expected_role_memberships: Mapping[str, Collection[str]],
) -> None:
    value = prediction.value
    config = experiment_config.value
    exact_identities = {
        "experiment_config_sha256": experiment_config.sha256,
        "dataset_manifest_sha256": run_spec.dataset_manifest_sha256,
        "processing_manifest_sha256": run_spec.processing_manifest_sha256,
        "split_sha256": run_spec.split_sha256,
        "feature_sha256": run_spec.feature_sha256,
        "run_id": run_spec.run_id,
        "attempt_id": attempt_id,
        "task_id": config["task_id"],
        "candidate_role": "candidate-pool",
        "model_id": run_spec.model_id,
        "model_config_sha256": run_spec.model_config_sha256,
        "acquisition_id": run_spec.acquisition_id,
        "acquisition_config_sha256": run_spec.acquisition_config_sha256,
        "seed": run_spec.seed,
    }
    for field, expected in exact_identities.items():
        _expect_exact(value[field], expected, f"prediction {field}")

    raw_candidates = expected_role_memberships.get("candidate-pool")
    if raw_candidates is None:
        raise ContractValidationError("candidate-pool feature membership is required")
    candidates = tuple(raw_candidates)
    if len(candidates) != len(set(candidates)):
        raise ContractValidationError("candidate-pool feature membership contains duplicates")
    expected_candidates = tuple(sorted(candidates))
    _expect_exact(
        prediction.candidate_ids,
        expected_candidates,
        "prediction candidate-pool exact cover",
    )
    _expect_exact(
        value["candidate_count"],
        len(expected_candidates),
        "prediction candidate_count",
    )


def _validate_metric_identity(
    result: LoadedContract,
    *,
    prediction: LoadedPredictionArtifact,
    run_spec: RunSpec,
    experiment_config: LoadedContract,
    attempt_id: str,
) -> tuple[str, str | None]:
    value = result.value
    config = experiment_config.value
    prediction_value = prediction.value
    exact_identities = {
        "experiment_config_sha256": experiment_config.sha256,
        "dataset_manifest_sha256": run_spec.dataset_manifest_sha256,
        "processing_manifest_sha256": run_spec.processing_manifest_sha256,
        "split_sha256": run_spec.split_sha256,
        "run_id": run_spec.run_id,
        "attempt_id": attempt_id,
        "input_artifact_sha256": prediction.sha256,
        "task_id": config["task_id"],
        "candidate_role": prediction_value["candidate_role"],
        "model_id": run_spec.model_id,
        "model_config_sha256": run_spec.model_config_sha256,
        "acquisition_id": run_spec.acquisition_id,
        "acquisition_config_sha256": run_spec.acquisition_config_sha256,
        "seed": run_spec.seed,
        "evaluation_scope": "m2-static",
        "round": None,
        "budget": None,
    }
    for field, expected in exact_identities.items():
        _expect_exact(value[field], expected, f"metric result {field}")
    _expect_exact(
        value["support"]["evaluated_candidate_count"],
        prediction_value["candidate_count"],
        "metric evaluated_candidate_count",
    )
    if value["definition_id"] is not None:
        definitions = {
            definition["definition_id"]: definition
            for definition in config["evaluation"]["high_value_definitions"]
        }
        definition = definitions[value["definition_id"]]
        _expect_exact(
            value["support"]["frozen_candidate_count"],
            definition["frozen_candidate_count"],
            "metric frozen_candidate_count",
        )
        _expect_exact(
            value["support"]["nominal_k"],
            definition["derivation"]["nominal_k"],
            "metric nominal_k",
        )
    return value["metric_id"], value["definition_id"]


def _validate_complete_metric_identities(
    identities: Collection[tuple[str, str | None]],
) -> None:
    if len(identities) != len(set(identities)):
        raise ContractValidationError("M2-static metric result identities must be unique")
    if set(identities) != _M2_METRIC_IDENTITIES:
        raise ContractValidationError("M2-static metric result identity set is incomplete")


def orchestrate_m2_static_prediction(
    *,
    run_spec_bytes: bytes,
    experiment_config_bytes: bytes,
    attempt_id: str,
    model_config_bytes: bytes,
    feature_artifact: LoadedContract | Mapping[str, Any],
    feature_descriptor_bytes: bytes,
    feature_member_bytes: Mapping[str, bytes],
    expected_role_memberships: Mapping[str, Collection[str]],
    producer: Callable[[M2StaticProducerInputs], bytes],
    evaluator: Callable[[M2StaticEvaluatorInputs], Mapping[str, bytes]],
) -> M2StaticOrchestrationOutputs:
    """Run the truth-free producer before a fresh-byte, aggregate-only evaluator."""

    run_spec = load_run_spec_v1(run_spec_bytes)
    attempt_id = _require_identifier(attempt_id, "attempt_id")
    experiment_config = load_experiment_config(experiment_config_bytes)
    validate_m2_static_profile(run_spec, experiment_config)
    feature_descriptor = validate_feature_bundle_identity(
        feature_artifact,
        descriptor_bytes=feature_descriptor_bytes,
        member_bytes=feature_member_bytes,
        expected_role_memberships=expected_role_memberships,
        run_spec=run_spec,
    )
    if hashlib.sha256(model_config_bytes).hexdigest() != run_spec.model_config_sha256:
        raise ContractValidationError("model config bytes do not match RunSpec")
    raw_candidates = expected_role_memberships.get("candidate-pool")
    if raw_candidates is None:
        raise ContractValidationError("candidate-pool feature membership is required")
    candidate_ids = tuple(sorted(raw_candidates))
    producer_inputs = M2StaticProducerInputs(
        run_spec=run_spec,
        experiment_config=experiment_config,
        feature_descriptor=feature_descriptor,
        feature_member_bytes=MappingProxyType(dict(feature_member_bytes)),
        candidate_ids=candidate_ids,
        attempt_id=attempt_id,
        model_config_bytes=bytes(memoryview(model_config_bytes)),
    )
    try:
        prediction_bytes = producer(producer_inputs)
    except Exception:
        raise ContractValidationError("target-free prediction producer failed") from None
    if not isinstance(prediction_bytes, bytes):
        raise ContractValidationError("prediction producer must return exact bytes")
    prediction = load_prediction_artifact(prediction_bytes)
    _validate_prediction_identity(
        prediction,
        run_spec=run_spec,
        experiment_config=experiment_config,
        attempt_id=attempt_id,
        expected_role_memberships=expected_role_memberships,
    )

    fresh_prediction_bytes = memoryview(prediction.canonical_bytes).tobytes()
    evaluator_inputs = M2StaticEvaluatorInputs(
        run_spec=run_spec,
        experiment_config=experiment_config,
        prediction_bytes=fresh_prediction_bytes,
        prediction_sha256=hashlib.sha256(fresh_prediction_bytes).hexdigest(),
        candidate_ids=prediction.candidate_ids,
        attempt_id=attempt_id,
    )
    try:
        raw_metric_results = evaluator(evaluator_inputs)
    except Exception:
        raise ContractValidationError("M2 aggregate evaluator failed") from None
    if not isinstance(raw_metric_results, Mapping) or len(raw_metric_results) != 4:
        raise ContractValidationError(
            "M2 aggregate evaluator must return exactly four result byte streams"
        )
    metric_results: dict[str, bytes] = {}
    metric_identities: list[tuple[str, str | None]] = []
    for object_key, payload in raw_metric_results.items():
        _require_string(object_key, "metric result object key")
        if not isinstance(payload, bytes):
            raise ContractValidationError("M2 aggregate evaluator may return only result bytes")
        result = load_high_value_result(
            payload,
            expected_schema_version="high-value-metric-result-v1",
        )
        metric_identities.append(
            _validate_metric_identity(
                result,
                prediction=prediction,
                run_spec=run_spec,
                experiment_config=experiment_config,
                attempt_id=attempt_id,
            )
        )
        metric_results[object_key] = payload
    _validate_complete_metric_identities(metric_identities)
    return M2StaticOrchestrationOutputs(
        prediction=prediction,
        metric_result_bytes=MappingProxyType(metric_results),
    )


def validate_m2_static_prediction_evidence(
    *,
    run_spec_bytes: bytes,
    experiment_config_bytes: bytes,
    run_attempt_bytes: bytes,
    prediction_object_key: str,
    prediction_bytes: bytes,
    metric_result_bytes: Mapping[str, bytes],
    feature_artifact: LoadedContract | Mapping[str, Any],
    feature_descriptor_bytes: bytes,
    feature_member_bytes: Mapping[str, bytes],
    expected_role_memberships: Mapping[str, Collection[str]],
) -> M2StaticPredictionEvidence:
    """Validate one complete, fresh-read M2-static prediction evidence closure."""

    run_spec = load_run_spec_v1(run_spec_bytes)
    run_attempt = load_run_attempt_v1(run_attempt_bytes)
    _expect_exact(run_attempt.value["run_id"], run_spec.run_id, "RunAttempt run_id")
    _expect_exact(run_attempt.value["status"], "succeeded", "RunAttempt status")
    _expect_exact(
        run_attempt.value["executed_git_commit"],
        run_spec.git_commit,
        "RunAttempt executed_git_commit",
    )
    _expect_exact(
        run_attempt.value["executed_environment_digest"],
        run_spec.environment_digest,
        "RunAttempt executed_environment_digest",
    )
    experiment_config = load_experiment_config(experiment_config_bytes)
    validate_m2_static_profile(run_spec, experiment_config)
    feature_descriptor = validate_feature_bundle_identity(
        feature_artifact,
        descriptor_bytes=feature_descriptor_bytes,
        member_bytes=feature_member_bytes,
        expected_role_memberships=expected_role_memberships,
        run_spec=run_spec,
    )

    prediction_reference = _output_reference(
        run_attempt,
        prediction_object_key,
        field="prediction",
    )
    prediction = load_prediction_artifact(
        prediction_bytes,
        expected_sha256=prediction_reference["sha256"],
    )
    _expect_exact(
        len(prediction_bytes),
        prediction_reference["size_bytes"],
        "prediction size_bytes",
    )
    _validate_prediction_identity(
        prediction,
        run_spec=run_spec,
        experiment_config=experiment_config,
        attempt_id=run_attempt.value["attempt_id"],
        expected_role_memberships=expected_role_memberships,
    )

    if len(metric_result_bytes) != 4:
        raise ContractValidationError("M2-static evidence requires exactly four metric results")
    metric_identities: list[tuple[str, str | None]] = []
    metric_results: list[LoadedContract] = []
    for object_key, payload in metric_result_bytes.items():
        if object_key == prediction_object_key:
            raise ContractValidationError("prediction and metric object keys must differ")
        reference = _output_reference(run_attempt, object_key, field="metric result")
        result = load_high_value_result(
            payload,
            expected_schema_version="high-value-metric-result-v1",
            expected_sha256=reference["sha256"],
        )
        _expect_exact(len(payload), reference["size_bytes"], "metric result size_bytes")
        validate_metric_result_evidence(
            MetricResultEvidence(
                result=result,
                run_spec=run_spec,
                run_attempt=run_attempt.value,
            ),
            experiment_config,
        )
        metric_identities.append(
            _validate_metric_identity(
                result,
                prediction=prediction,
                run_spec=run_spec,
                experiment_config=experiment_config,
                attempt_id=run_attempt.value["attempt_id"],
            )
        )
        metric_results.append(result)
    _validate_complete_metric_identities(metric_identities)

    return M2StaticPredictionEvidence(
        run_spec=run_spec,
        experiment_config=experiment_config,
        feature_descriptor=feature_descriptor,
        run_attempt=run_attempt,
        prediction=prediction,
        metric_results=tuple(metric_results),
    )
