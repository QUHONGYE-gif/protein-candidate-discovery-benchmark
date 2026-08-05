"""Commit-bound, local-scratch-only entry for the frozen M2 Gate A preflight."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.prediction import M2StaticProducerInputs, load_prediction_artifact
from pcdb.datasets.processing import ProcessingConfigLike
from pcdb.m2.features import SequenceFeatureBundle, build_sequence_feature_bundle
from pcdb.m2.gate_a import build_gate_a_packet
from pcdb.m2.projection import project_frozen_m2_inputs
from pcdb.storage import LocalArtifactStore

_MODEL_IDS = (
    "ridge-sklearn-v1",
    "random-forest-sklearn-v1",
    "xgboost-cpu-v1",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _find_repository_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists() and (candidate / ".gitignore").is_file():
            return candidate
    return None


def validate_local_scratch_root(path: Path) -> Path:
    """Reject repository-local output unless it stays in the ignored `.scratch` namespace."""

    working_directory = Path.cwd().resolve()
    resolved = path.resolve()
    if not path.is_absolute():
        ignored_root = (working_directory / ".scratch").resolve()
        if not resolved.is_relative_to(ignored_root):
            raise ContractValidationError(
                "relative scratch root must stay inside the ignored .scratch namespace"
            )
        return resolved

    repository_root = _find_repository_root(resolved)
    if repository_root is not None and resolved.is_relative_to(repository_root):
        relative_parts = resolved.relative_to(repository_root).parts
        if ".scratch" not in relative_parts:
            raise ContractValidationError(
                "scratch root inside the repository must stay inside an ignored .scratch namespace"
            )
    return resolved


def _same_bundle(first: SequenceFeatureBundle, second: SequenceFeatureBundle) -> bool:
    return (
        first.logical_artifact.canonical_bytes == second.logical_artifact.canonical_bytes
        and first.descriptor_bytes == second.descriptor_bytes
        and dict(first.member_bytes) == dict(second.member_bytes)
        and dict(first.memberships) == dict(second.memberships)
    )


def _validate_gate_a_identity(
    *,
    benchmark_task_id: str,
    experiment_config_bytes: bytes,
    git_commit: str,
    environment_digest: str,
    gate_a_packet_bytes: bytes,
    gate_a_packet_sha256: str,
    repository_root: Path,
) -> None:
    if (
        not _SHA256.fullmatch(gate_a_packet_sha256)
        or hashlib.sha256(gate_a_packet_bytes).hexdigest() != gate_a_packet_sha256
    ):
        raise ContractValidationError("Gate A packet identity does not match reviewed bytes")
    try:
        packet = json.loads(gate_a_packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("Gate A packet must be canonical JSON") from None
    if not isinstance(packet, dict) or canonical_json_bytes(packet) != gate_a_packet_bytes:
        raise ContractValidationError("Gate A packet must be canonical JSON")
    reviewed_git_commit = packet.get("reviewed_git_commit")
    if not isinstance(reviewed_git_commit, str):
        raise ContractValidationError("Gate A packet reviewed commit is invalid")
    if gate_a_packet_bytes != build_gate_a_packet(repository_root, reviewed_git_commit):
        raise ContractValidationError("Gate A packet does not match exact repository bytes")
    if git_commit != reviewed_git_commit:
        raise ContractValidationError("Git commit does not match the reviewed Gate A packet")

    environment = packet["environment_candidate"]
    if environment_digest != environment["sha256"]:
        raise ContractValidationError(
            "environment digest does not match the reviewed Gate A packet"
        )
    experiment_reference = packet["experiment_configs"].get(benchmark_task_id)
    if (
        not isinstance(experiment_reference, dict)
        or experiment_reference.get("size_bytes") != len(experiment_config_bytes)
        or experiment_reference.get("sha256") != hashlib.sha256(experiment_config_bytes).hexdigest()
    ):
        raise ContractValidationError(
            "Experiment Config identity does not match the reviewed Gate A packet"
        )


def validate_frozen_preflight_entry(
    *,
    benchmark_task_id: str,
    experiment_config_bytes: bytes,
    git_commit: str,
    environment_digest: str,
    scratch_root: Path,
    gate_a_packet_bytes: bytes,
    gate_a_packet_sha256: str,
    repository_root: Path,
) -> Path:
    """Validate reviewed provenance and local scratch before any truth-bearing read."""

    validated_scratch_root = validate_local_scratch_root(scratch_root)
    resolved_repository_root = repository_root.resolve()
    if validated_scratch_root.is_relative_to(resolved_repository_root):
        relative_parts = validated_scratch_root.relative_to(resolved_repository_root).parts
        if ".scratch" not in relative_parts:
            raise ContractValidationError(
                "scratch root inside the repository must stay inside an ignored .scratch namespace"
            )
    _validate_gate_a_identity(
        benchmark_task_id=benchmark_task_id,
        experiment_config_bytes=experiment_config_bytes,
        git_commit=git_commit,
        environment_digest=environment_digest,
        gate_a_packet_bytes=gate_a_packet_bytes,
        gate_a_packet_sha256=gate_a_packet_sha256,
        repository_root=repository_root,
    )
    return validated_scratch_root


def run_frozen_preflight(
    *,
    benchmark_task_id: str,
    processing_config: ProcessingConfigLike,
    processing_config_bytes: bytes,
    processing_manifest_bytes: bytes,
    split_manifest_bytes: bytes,
    membership_bytes: bytes,
    role_input_bytes: bytes,
    hidden_truth_bytes: bytes,
    experiment_config_bytes: bytes,
    git_commit: str,
    environment_digest: str,
    scratch_root: Path,
    gate_a_packet_bytes: bytes,
    gate_a_packet_sha256: str,
    repository_root: Path,
) -> dict[str, Any]:
    """Run the reviewed projection → bundle → three-prediction chain in local scratch."""

    started = time.perf_counter()
    validated_scratch_root = validate_frozen_preflight_entry(
        benchmark_task_id=benchmark_task_id,
        experiment_config_bytes=experiment_config_bytes,
        git_commit=git_commit,
        environment_digest=environment_digest,
        scratch_root=scratch_root,
        gate_a_packet_bytes=gate_a_packet_bytes,
        gate_a_packet_sha256=gate_a_packet_sha256,
        repository_root=repository_root,
    )
    projected = project_frozen_m2_inputs(
        benchmark_task_id=benchmark_task_id,
        processing_config=processing_config,
        processing_config_bytes=processing_config_bytes,
        processing_manifest_bytes=processing_manifest_bytes,
        split_manifest_bytes=split_manifest_bytes,
        membership_bytes=membership_bytes,
        role_input_bytes=role_input_bytes,
        hidden_truth_bytes=hidden_truth_bytes,
    )
    first_bundle = build_sequence_feature_bundle(
        benchmark_task_id=benchmark_task_id,
        role_candidates=projected.role_candidates,
        git_commit=git_commit,
        environment_digest=environment_digest,
        processing_config=processing_config,
        expected_role_memberships=projected.expected_role_memberships,
    )
    second_bundle = build_sequence_feature_bundle(
        benchmark_task_id=benchmark_task_id,
        role_candidates=projected.role_candidates,
        git_commit=git_commit,
        environment_digest=environment_digest,
        processing_config=processing_config,
        expected_role_memberships=projected.expected_role_memberships,
    )
    if not _same_bundle(first_bundle, second_bundle):
        raise ContractValidationError("repeated preflight feature bundles are not deterministic")

    experiment_config = load_experiment_config(experiment_config_bytes)
    configured_models = tuple(experiment_config.value.get("models", ()))
    if (
        experiment_config.value.get("task_id") != benchmark_task_id
        or len(configured_models) != len(_MODEL_IDS)
        or set(configured_models) != set(_MODEL_IDS)
        or experiment_config.value.get("seeds") != (1729,)
    ):
        raise ContractValidationError(
            "experiment config does not match the frozen preflight matrix"
        )

    # Imports stay local so identity/projection checks remain dependency-light.
    from pcdb.m2.models import model_config_bytes
    from pcdb.m2.producer import build_preflight_run_spec, produce_static_prediction

    predictions: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {}
    prefix = f"m2-preflight/{benchmark_task_id}"
    payloads[f"{prefix}/feature-logical.json"] = first_bundle.logical_artifact.canonical_bytes
    payloads[f"{prefix}/feature-descriptor.json"] = first_bundle.descriptor_bytes
    payloads.update(first_bundle.member_bytes)
    for model_id in _MODEL_IDS:
        config_bytes = model_config_bytes(model_id)
        run_spec = build_preflight_run_spec(
            bundle=first_bundle,
            experiment_config=experiment_config,
            model_id=model_id,
            git_commit=git_commit,
            environment_digest=environment_digest,
        )
        attempt_id = f"gate-a-preflight-{benchmark_task_id}-{model_id}"
        producer_inputs = M2StaticProducerInputs(
            run_spec=run_spec,
            experiment_config=experiment_config,
            feature_descriptor=first_bundle.descriptor,
            feature_member_bytes=first_bundle.member_bytes,
            candidate_ids=tuple(sorted(first_bundle.memberships["candidate-pool"])),
            attempt_id=attempt_id,
            model_config_bytes=config_bytes,
        )
        first_prediction = produce_static_prediction(
            producer_inputs,
            projected.development_observations,
        )
        second_prediction = produce_static_prediction(
            producer_inputs,
            projected.development_observations,
        )
        if first_prediction != second_prediction:
            raise ContractValidationError("repeated preflight predictions are not deterministic")
        loaded_prediction = load_prediction_artifact(first_prediction)
        if loaded_prediction.candidate_ids != producer_inputs.candidate_ids:
            raise ContractValidationError(
                "preflight prediction does not exact-cover candidate membership"
            )
        model_prefix = f"{prefix}/{model_id}"
        payloads[f"{model_prefix}/model-config.json"] = config_bytes
        payloads[f"{model_prefix}/run-spec.json"] = run_spec.canonical_json().encode("utf-8")
        payloads[f"{model_prefix}/prediction.json"] = first_prediction
        predictions.append(
            {
                "model_id": model_id,
                "prediction_sha256": hashlib.sha256(first_prediction).hexdigest(),
                "run_id": run_spec.run_id,
            }
        )

    summary: dict[str, Any] = {
        "artifact_payload_size_bytes": sum(len(payload) for payload in payloads.values()),
        "benchmark_task_id": benchmark_task_id,
        "environment_digest": environment_digest,
        "feature_sha256": first_bundle.descriptor.sha256,
        "git_commit": git_commit,
        "predictions": predictions,
        "role_counts": {
            role: len(projected.role_candidates[role])
            for role in ("development", "validation", "candidate-pool")
        },
        "runtime_seconds": round(time.perf_counter() - started, 6),
        "schema_version": "m2-gate-a-preflight-summary-v1",
    }
    payloads[f"{prefix}/summary.json"] = canonical_json_bytes(summary)

    store = LocalArtifactStore(validated_scratch_root)
    for object_key in sorted(payloads):
        store.put_bytes(object_key, payloads[object_key])
    return summary


__all__ = [
    "run_frozen_preflight",
    "validate_frozen_preflight_entry",
    "validate_local_scratch_root",
]
