"""Deterministic, zero-truth M4 pilot preparation for Issue #76."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.run import RunSpec
from pcdb.m2.models import m4_model_config_bytes

_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MATRIX_PATH = Path("manifests/experiments/m3/issue-66/run-spec-matrix.v1.json")
_FREEZE_PATH = Path("manifests/experiments/m3/issue-66/protocol-freeze.v1.json")
_ENVIRONMENT_PATH = Path("configs/environments/m2-gate-a-linux-x86_64.json")
_MATRIX_IDENTITY = (
    4_313,
    "1eaf9657d618e88bca19df7e219b46b3182b74aca3d8f8b4080e8794583e6148",
)
_FREEZE_IDENTITY = (
    2_894,
    "5d8cf415654a3fd5ac19fcc17f50788b3be4aaee982a4477601ab035d078c170",
)
_ENVIRONMENT_IDENTITY = (
    1_656,
    "0994a73f124c2baa6f330bf8739db1673217bdec6ad7d9046ded908fc8dba4fc",
)
_TASKS = ("alpha-amylase", "ired")
_MODELS = (
    (
        "ridge-sklearn-v1",
        1_243,
        "806e9bb5c16a511b233213ca6f1f08ac848eeb77c7e9702915171ce4a22293d7",
    ),
    (
        "random-forest-sklearn-v1",
        2_615,
        "418b0fd6189fd9b3987620b05021e5ec5ff3018c074024cd13874cd3b9845312",
    ),
    (
        "xgboost-cpu-v1",
        5_733,
        "31eb5bc146f4e810027de8870fa5632e09a5427bd8dddfee9261572c1e3473f2",
    ),
)
_ACQUISITIONS = (
    (
        "random-seeded-sha256-v1",
        Path("configs/acquisitions/m3/random-seeded-sha256-v1.json"),
        538,
        "24f80499cb48ad6716f86a9c47bf098d47acdb78841bcb7b8926504619983e0f",
    ),
    (
        "greedy-predictive-mean-v1",
        Path("configs/acquisitions/m3/greedy-predictive-mean-v1.json"),
        499,
        "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15",
    ),
    (
        "uncertainty-bootstrap-std-v1",
        Path("configs/acquisitions/m3/uncertainty-bootstrap-std-v1.json"),
        1_657,
        "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6",
    ),
    (
        "ucb-bootstrap-std-beta1-v1",
        Path("configs/acquisitions/m4/ucb-bootstrap-std-beta1-v1.json"),
        886,
        "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d",
    ),
)
_EXPERIMENTS = (
    (
        "alpha-amylase",
        Path("configs/experiments/m4-single-fidelity/alpha-amylase.json"),
        4_894,
        "193f8ee0102a654e63cd7ea31fc77a93bcf7f5fae0c0305570117eac54c097aa",
    ),
    (
        "ired",
        Path("configs/experiments/m4-single-fidelity/ired.json"),
        4_876,
        "7bfdc48ca2f4d41f039b8f119c3dca5910e9eb26c156cdee53f584b4af7c38fa",
    ),
)
_PILOT_SEEDS = (1729, 1730, 1731)
_REGISTERED_SEEDS = tuple(range(1729, 1739))


def _load_canonical_object(path: Path, field: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{field} must be readable canonical JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
        raise ContractValidationError(f"{field} must be canonical JSON")
    return cast(dict[str, Any], value), payload


def _artifact(path: Path, payload: bytes) -> dict[str, object]:
    return {
        "path": path.as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _require_identity(payload: bytes, identity: tuple[int, str], field: str) -> None:
    expected_size, expected_sha256 = identity
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ContractValidationError(f"{field} identity drifted")


def _source_state(
    repository_root: Path,
) -> tuple[
    dict[str, dict[str, object]],
    str,
    dict[str, str],
    dict[str, str],
    dict[str, str],
    list[dict[str, object]],
    dict[str, list[dict[str, object]]],
]:
    matrix, matrix_bytes = _load_canonical_object(repository_root / _MATRIX_PATH, "M3 matrix")
    freeze, freeze_bytes = _load_canonical_object(repository_root / _FREEZE_PATH, "M3 freeze")
    environment, environment_bytes = _load_canonical_object(
        repository_root / _ENVIRONMENT_PATH,
        "environment candidate",
    )
    _require_identity(matrix_bytes, _MATRIX_IDENTITY, "accepted M3 matrix")
    _require_identity(freeze_bytes, _FREEZE_IDENTITY, "accepted M3 freeze")
    _require_identity(environment_bytes, _ENVIRONMENT_IDENTITY, "environment candidate")
    if (
        matrix.get("schema_version") != "m3-run-spec-matrix-v1"
        or tuple(matrix.get("tasks", ())) != _TASKS
        or matrix.get("late_bound_run_spec_fields") != ["environment_digest", "git_commit"]
        or environment.get("cpu_only") is not True
    ):
        raise ContractValidationError("accepted M3 metadata boundary drifted")
    frozen_matrix = [
        item
        for item in cast(list[dict[str, object]], freeze.get("artifact_inventory"))
        if item.get("path") == _MATRIX_PATH.as_posix()
    ]
    if len(frozen_matrix) != 1 or any(
        frozen_matrix[0].get(field) != _artifact(_MATRIX_PATH, matrix_bytes)[field]
        for field in ("path", "sha256", "size_bytes")
    ):
        raise ContractValidationError("accepted M3 matrix differs from its protocol freeze")
    raw_chains = cast(list[dict[str, object]], matrix.get("manifest_chains"))
    if len(raw_chains) != 2 or tuple(str(item.get("task_id")) for item in raw_chains) != _TASKS:
        raise ContractValidationError("accepted manifest-chain inventory drifted")
    chains = {str(item["task_id"]): item for item in raw_chains}
    source_artifacts = [
        _artifact(_MATRIX_PATH, matrix_bytes),
        _artifact(_FREEZE_PATH, freeze_bytes),
        _artifact(_ENVIRONMENT_PATH, environment_bytes),
    ]

    experiments: dict[str, str] = {}
    experiment_inventory: list[dict[str, object]] = []
    for task_id, path, size_bytes, sha256 in _EXPERIMENTS:
        payload = (repository_root / path).read_bytes()
        loaded = load_experiment_config(payload)
        chain = chains[task_id]
        if (
            len(payload) != size_bytes
            or loaded.sha256 != sha256
            or loaded.value["task_id"] != task_id
            or loaded.value["phase"] != "development"
            or loaded.value["held_out_authorized"] is not False
            or loaded.value["models"] != tuple(item[0] for item in _MODELS)
            or loaded.value["acquisitions"] != tuple(item[0] for item in _ACQUISITIONS)
            or loaded.value["seeds"] != _REGISTERED_SEEDS
            or loaded.value["initial_sample_sizes"] != (32, 64)
            or loaded.value["batch_sizes"] != (16,)
            or loaded.value["budgets"] != (128,)
            or any(
                loaded.value[field] != chain[field]
                for field in (
                    "dataset_manifest_sha256",
                    "processing_manifest_sha256",
                    "split_sha256",
                )
            )
        ):
            raise ContractValidationError("M4 Experiment Config identity or binding drifted")
        experiments[task_id] = loaded.sha256
        artifact = _artifact(path, payload)
        source_artifacts.append(artifact)
        experiment_inventory.append({"task_id": task_id, **artifact})

    acquisitions: dict[str, str] = {}
    acquisition_inventory: list[dict[str, object]] = []
    for acquisition_id, path, size_bytes, sha256 in _ACQUISITIONS:
        value, payload = _load_canonical_object(
            repository_root / path,
            "acquisition config",
        )
        if (
            value.get("acquisition_id") != acquisition_id
            or len(payload) != size_bytes
            or hashlib.sha256(payload).hexdigest() != sha256
        ):
            raise ContractValidationError("M4 acquisition config identity drifted")
        acquisitions[acquisition_id] = sha256
        artifact = _artifact(path, payload)
        source_artifacts.append(artifact)
        acquisition_inventory.append({"acquisition_id": acquisition_id, **artifact})

    models: dict[str, str] = {}
    model_inventory: list[dict[str, object]] = []
    for model_id, size_bytes, sha256 in _MODELS:
        payload = m4_model_config_bytes(model_id)
        if len(payload) != size_bytes or hashlib.sha256(payload).hexdigest() != sha256:
            raise ContractValidationError("M4 model config identity drifted")
        models[model_id] = sha256
        model_inventory.append(
            {
                "model_config_sha256": sha256,
                "model_config_size_bytes": size_bytes,
                "model_id": model_id,
            }
        )
    config_inventory = {
        "acquisitions": acquisition_inventory,
        "experiments": experiment_inventory,
        "models": model_inventory,
    }
    return (
        chains,
        hashlib.sha256(environment_bytes).hexdigest(),
        experiments,
        models,
        acquisitions,
        source_artifacts,
        config_inventory,
    )


def build_pilot_preflight_packet(
    repository_root: Path,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
) -> bytes:
    """Build canonical pilot inventory bytes without real inputs, truth, server, or COS access."""

    if not _GIT_OBJECT.fullmatch(reviewed_git_commit) or not _GIT_OBJECT.fullmatch(
        reviewed_git_tree
    ):
        raise ContractValidationError("reviewed Git commit and tree must be lowercase 40-hex")
    root = repository_root.resolve()
    (
        chains,
        environment_digest,
        experiments,
        models,
        acquisitions,
        source_artifacts,
        config_inventory,
    ) = _source_state(root)
    run_specs: list[dict[str, object]] = []
    for task_id in _TASKS:
        chain = chains[task_id]
        for model_id, _, _ in _MODELS:
            for acquisition_id, _, _, _ in _ACQUISITIONS:
                for seed in _PILOT_SEEDS:
                    run_spec = RunSpec(
                        schema_version="1",
                        protocol_version="m4-single-fidelity-screening-v1",
                        git_commit=reviewed_git_commit,
                        environment_digest=environment_digest,
                        dataset_manifest_sha256=str(chain["dataset_manifest_sha256"]),
                        processing_manifest_sha256=str(chain["processing_manifest_sha256"]),
                        split_sha256=str(chain["split_sha256"]),
                        feature_sha256=str(chain["feature_sha256"]),
                        experiment_config_sha256=experiments[task_id],
                        model_id=model_id,
                        model_config_sha256=models[model_id],
                        acquisition_id=acquisition_id,
                        acquisition_config_sha256=acquisitions[acquisition_id],
                        seed=seed,
                        initial_sample_size=32,
                        batch_size=16,
                        budget=128,
                    )
                    run_specs.append(
                        {
                            "run_id": run_spec.run_id,
                            "run_spec": asdict(run_spec),
                            "task_id": task_id,
                        }
                    )
    if len(run_specs) != 72 or len({str(item["run_id"]) for item in run_specs}) != 72:
        raise ContractValidationError("M4 pilot RunSpec inventory is not an exact 72-run set")
    packet = {
        "authorization_boundaries": {
            "cos_access_authorized": False,
            "packet_review_authorizes_execution": False,
            "paid_compute_authorized": False,
            "pilot_execution_authorized": False,
            "server_access_authorized": False,
        },
        "closed_config_inventory": config_inventory,
        "environment_candidate": {
            **next(
                item for item in source_artifacts if item["path"] == _ENVIRONMENT_PATH.as_posix()
            ),
            "runtime_verification_required": True,
        },
        "initial_resource_ledger": {
            "active_execution_wall_seconds": 0,
            "attempts_started": 0,
            "network_transfer_bytes": 0,
            "paid_compute_cny": 0,
            "scratch_high_water_bytes": 0,
            "unique_run_ids_started": [],
        },
        "issue_number": 76,
        "matrix": {
            "expected_pilot_run_specs": 72,
            "matrix_order": ["task_id", "model_id", "acquisition_id", "seed"],
            "pilot_profile": {
                "batch_size": 16,
                "budget": 128,
                "initial_sample_size": 32,
                "seeds": list(_PILOT_SEEDS),
            },
            "registered_config_scope": {
                "initial_sample_sizes": [32, 64],
                "seeds": list(_REGISTERED_SEEDS),
            },
        },
        "resource_accounting": {
            "cap_scope": "pilot-and-conditional-screening-cumulative-no-reset",
            "guard_before_each_remote_operation": True,
            "guard_before_each_run_spec": True,
            "incomplete_attempt_enters_aggregate": False,
            "ledger_persistence_authorized": False,
        },
        "resource_caps": {
            "active_execution_wall_seconds": 21_600,
            "aggregate_rss_bytes": 16 * 1024**3,
            "automatic_retry": 0,
            "delete": 0,
            "max_unique_run_specs_and_attempts": 378,
            "network_transfer_bytes": 2 * 1024**3,
            "overwrite": 0,
            "per_run_spec_attempts": 1,
            "pilot_unique_run_specs_and_attempts": 72,
            "replacement_attempts": 0,
            "scratch_high_water_bytes": 20 * 1024**3,
            "vcpu": 8,
        },
        "reviewed_source": {
            "git_commit": reviewed_git_commit,
            "git_tree": reviewed_git_tree,
        },
        "run_specs": run_specs,
        "schema_version": "m4-pilot-preflight-packet-v1",
        "source_artifacts": source_artifacts,
        "truth_boundary": {
            "allowed_future_runtime_path": (
                "authorized replay entry -> ReplayEngine -> HiddenOracle"
            ),
            "candidate_truth_read_authorized": False,
            "held_out_read_authorized": False,
            "oracle_path_bound": False,
            "packet_build_candidate_truth_reads": 0,
            "packet_build_real_input_reads": 0,
            "runtime_oracle_binding_required": True,
        },
    }
    return canonical_json_bytes(packet)


def packet_identity(packet_bytes: bytes) -> tuple[int, str]:
    """Return exact packet size and SHA-256."""

    return len(packet_bytes), hashlib.sha256(packet_bytes).hexdigest()


def load_pilot_preflight_packet(
    packet_bytes: bytes,
    *,
    expected_sha256: str,
    repository_root: Path,
    expected_git_commit: str,
    expected_git_tree: str,
) -> dict[str, Any]:
    """Strict-load and reproduce one zero-truth M4 pilot preflight packet."""

    if (
        not _SHA256.fullmatch(expected_sha256)
        or hashlib.sha256(packet_bytes).hexdigest() != expected_sha256
    ):
        raise ContractValidationError("M4 pilot preflight packet identity differs")
    try:
        value = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M4 pilot preflight packet must be canonical JSON") from None
    if not isinstance(value, dict) or canonical_json_bytes(value) != packet_bytes:
        raise ContractValidationError("M4 pilot preflight packet must be canonical JSON")
    expected = build_pilot_preflight_packet(
        repository_root,
        expected_git_commit,
        expected_git_tree,
    )
    if expected != packet_bytes:
        raise ContractValidationError("M4 pilot preflight packet differs from repository bytes")
    return cast(dict[str, Any], value)


__all__ = [
    "build_pilot_preflight_packet",
    "load_pilot_preflight_packet",
    "packet_identity",
]
