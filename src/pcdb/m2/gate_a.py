"""Deterministic zero-real-data Gate A packet for Issue #58."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.feature_identity import PROTOCOL_VERSION
from pcdb.contracts.prediction import (
    M2_STATIC_ACQUISITION_CONFIG_BYTES,
    M2_STATIC_ACQUISITION_CONFIG_SHA256,
    M2_STATIC_ACQUISITION_ID,
)
from pcdb.m2.features import (
    FEATURE_CONFIG_ID,
    FEATURE_CONFIG_SHA256,
    FEATURE_CONFIG_SIZE_BYTES,
)
from pcdb.m2.model_identity import MODEL_CONFIG_SHA256, MODEL_CONFIG_SIZE_BYTES
from pcdb.m2.projection import frozen_projection_identity

GATE_A_SEED = 1729
GATE_A_SENTINEL = 1
GATE_A_SCRATCH_LOGICAL_ROOT = "scratch://pcdb/issue-58/gate-a"
GATE_A_TASKS = ("alpha-amylase", "ired")
GATE_A_MODELS = (
    "ridge-sklearn-v1",
    "random-forest-sklearn-v1",
    "xgboost-cpu-v1",
)

_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_EXPERIMENT_PATHS = {
    "alpha-amylase": "configs/experiments/m2-static/alpha-amylase.json",
    "ired": "configs/experiments/m2-static/ired.json",
}
_ENVIRONMENT_PATH = "configs/environments/m2-gate-a-linux-x86_64.json"


def _artifact(path: str, payload: bytes) -> dict[str, object]:
    return {
        "object_path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _load_environment(repository_root: Path) -> tuple[bytes, str]:
    payload = (repository_root / _ENVIRONMENT_PATH).read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("environment candidate must be strict JSON") from exc
    if canonical_json_bytes(value) != payload:
        raise ContractValidationError("environment candidate bytes are not canonical")
    if not isinstance(value, dict) or value.get("cpu_only") is not True:
        raise ContractValidationError("environment candidate must be CPU-only")
    return payload, hashlib.sha256(payload).hexdigest()


def _experiment_artifacts(repository_root: Path) -> tuple[dict[str, object], dict[str, str]]:
    artifacts: dict[str, object] = {}
    identities: dict[str, str] = {}
    for task_id in GATE_A_TASKS:
        path = _EXPERIMENT_PATHS[task_id]
        payload = (repository_root / path).read_bytes()
        loaded = load_experiment_config(payload)
        expected_profile = {
            "acquisitions": (M2_STATIC_ACQUISITION_ID,),
            "batch_sizes": (GATE_A_SENTINEL,),
            "budgets": (GATE_A_SENTINEL,),
            "held_out_authorized": False,
            "initial_sample_sizes": (GATE_A_SENTINEL,),
            "models": GATE_A_MODELS,
            "phase": "frozen",
            "seeds": (GATE_A_SEED,),
            "task_id": task_id,
        }
        actual_profile = {field: loaded.value[field] for field in expected_profile}
        if actual_profile != expected_profile:
            raise ContractValidationError("Gate A Experiment Config profile drifted")
        artifacts[task_id] = _artifact(path, payload)
        identities[task_id] = loaded.sha256
    return artifacts, identities


def _model_artifacts() -> dict[str, object]:
    artifacts: dict[str, object] = {}
    for model_id in GATE_A_MODELS:
        artifacts[model_id] = {
            "derivation": "pcdb.m2.models.model_config_bytes",
            "sha256": MODEL_CONFIG_SHA256[model_id],
            "size_bytes": MODEL_CONFIG_SIZE_BYTES[model_id],
        }
    return artifacts


def build_gate_a_packet(repository_root: Path, reviewed_git_commit: str) -> bytes:
    """Build the canonical preflight packet without reading any real dataset object."""

    if not _GIT_COMMIT.fullmatch(reviewed_git_commit):
        raise ContractValidationError("reviewed Git commit must be full lowercase 40-hex")
    experiment_artifacts, experiment_identities = _experiment_artifacts(repository_root)
    environment_bytes, environment_digest = _load_environment(repository_root)
    environment_artifact = _artifact(_ENVIRONMENT_PATH, environment_bytes)
    environment_artifact["digest_derivation"] = "sha256(canonical environment candidate bytes)"

    task_identities = {
        task_id: {
            "dataset_manifest_sha256": identity.dataset_manifest_sha256,
            "processing_config_sha256": identity.processing_config_sha256,
            "processing_manifest_sha256": identity.processing_manifest_sha256,
            "role_counts": dict(identity.role_counts),
            "split_sha256": identity.split_sha256,
        }
        for task_id in GATE_A_TASKS
        for identity in (frozen_projection_identity(task_id),)
    }
    templates: list[dict[str, object]] = []
    for task_id in GATE_A_TASKS:
        identity = task_identities[task_id]
        for model_id in GATE_A_MODELS:
            templates.append(
                {
                    "run_spec_fields": {
                        "acquisition_config_sha256": M2_STATIC_ACQUISITION_CONFIG_SHA256,
                        "acquisition_id": M2_STATIC_ACQUISITION_ID,
                        "batch_size": GATE_A_SENTINEL,
                        "budget": GATE_A_SENTINEL,
                        "dataset_manifest_sha256": identity["dataset_manifest_sha256"],
                        "environment_digest": environment_digest,
                        "experiment_config_sha256": experiment_identities[task_id],
                        "git_commit": reviewed_git_commit,
                        "initial_sample_size": GATE_A_SENTINEL,
                        "model_config_sha256": MODEL_CONFIG_SHA256[model_id],
                        "model_id": model_id,
                        "processing_manifest_sha256": identity["processing_manifest_sha256"],
                        "protocol_version": PROTOCOL_VERSION,
                        "schema_version": "1",
                        "seed": GATE_A_SEED,
                        "split_sha256": identity["split_sha256"],
                    },
                    "status": "template-not-a-run-spec",
                    "task_id": task_id,
                    "unresolved_fields": ["feature_sha256", "run_id"],
                }
            )

    packet: dict[str, Any] = {
        "acquisition_config": {
            "derivation": "pcdb.contracts.prediction.M2_STATIC_ACQUISITION_CONFIG_BYTES",
            "sha256": M2_STATIC_ACQUISITION_CONFIG_SHA256,
            "size_bytes": len(M2_STATIC_ACQUISITION_CONFIG_BYTES),
        },
        "environment_candidate": environment_artifact,
        "execution_policy": {
            "automatic_retry": 0,
            "cpu_only": True,
            "manual_starts": 1,
            "max_cost_cny": 20,
            "max_network_bytes": 5 * 1024**3,
            "max_ram_bytes": 16 * 1024**3,
            "max_scratch_bytes": 20 * 1024**3,
            "max_vcpu": 8,
            "max_wall_seconds": 2 * 60 * 60,
            "single_thread": True,
            "writes": {"cos_bytes": 0, "cos_objects": 0},
        },
        "experiment_configs": experiment_artifacts,
        "feature_config": {
            "feature_config_id": FEATURE_CONFIG_ID,
            "sha256": FEATURE_CONFIG_SHA256,
            "size_bytes": FEATURE_CONFIG_SIZE_BYTES,
        },
        "model_configs": _model_artifacts(),
        "reviewed_git_commit": reviewed_git_commit,
        "run_spec_templates": templates,
        "schema_version": "m2-gate-a-packet-v1",
        "scratch_logical_root": GATE_A_SCRATCH_LOGICAL_ROOT,
        "sentinel": {
            "batch_size": GATE_A_SENTINEL,
            "budget": GATE_A_SENTINEL,
            "initial_sample_size": GATE_A_SENTINEL,
        },
        "task_identities": task_identities,
        "truth_boundary": {
            "candidate_truth_reads": 0,
            "cos_accesses": 0,
            "evaluator_calls": 0,
            "held_out_reads": 0,
            "real_input_reads_during_packet_build": 0,
            "server_accesses_during_packet_build": 0,
        },
    }
    return canonical_json_bytes(packet)


def packet_identity(packet_bytes: bytes) -> tuple[int, str]:
    """Return the exact size and SHA-256 for a generated packet."""

    return len(packet_bytes), hashlib.sha256(packet_bytes).hexdigest()


__all__ = [
    "GATE_A_MODELS",
    "GATE_A_SCRATCH_LOGICAL_ROOT",
    "GATE_A_SEED",
    "GATE_A_SENTINEL",
    "GATE_A_TASKS",
    "build_gate_a_packet",
    "packet_identity",
]
