"""Zero-truth preparation and one reviewed M4 ReplayEngine construction path."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.feature_identity import LoadedFeatureMatrix, load_feature_matrix
from pcdb.contracts.run import RunSpec
from pcdb.m2.features import sequence_feature_definitions
from pcdb.m2.models import m4_model_config_bytes
from pcdb.m3.gate_a import reviewed_input_inventory_metadata
from pcdb.m4.pilot_preflight import load_pilot_preflight_packet
from pcdb.replay.engine import ReplayEngine

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TASKS = ("alpha-amylase", "ired")
_PURPOSES = (
    "candidate-feature-matrix",
    "dataset-manifest",
    "feature-descriptor",
    "processing-manifest",
    "split-manifest",
    "hidden-truth",
)
_M2_PACKET_PATH = "manifests/experiments/m2/issue-58/gate-b-packet.v1.json"
_EXPERIMENT_PATHS = {
    task_id: Path(f"configs/experiments/m4-single-fidelity/{task_id}.json") for task_id in _TASKS
}
_ORACLE_CODE_PATH = (
    "pcdb.m4.execution_adapter.build_reviewed_replay_engine"
    " -> pcdb.replay.engine.ReplayEngine.from_reviewed_inputs"
    " -> pcdb.replay.oracle.HiddenOracle"
)
_PACKET_AUTHORIZATION_BOUNDARIES = {
    "cos_access_authorized": False,
    "packet_review_authorizes_execution": False,
    "paid_compute_authorized": False,
    "pilot_execution_authorized": False,
    "server_access_authorized": False,
}
_ZERO_LEDGER = {
    "active_execution_wall_seconds": 0,
    "attempts_started": 0,
    "network_transfer_bytes": 0,
    "paid_compute_cny": 0,
    "scratch_high_water_bytes": 0,
    "unique_run_ids_started": [],
}


@dataclass(frozen=True, slots=True)
class InputObjectIdentity:
    """One exact remote object identity; never an object body."""

    bucket: str
    object_key: str
    purpose: str
    sha256: str
    size_bytes: int
    task_id: str
    truth_bearing: bool


@dataclass(frozen=True, slots=True)
class TaskInputInventory:
    """The exact six-object input set for one M4 task."""

    task_id: str
    objects: tuple[InputObjectIdentity, ...]


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """One reviewed pilot RunSpec and its resolved task."""

    task_id: str
    run_spec: RunSpec


@dataclass(frozen=True, slots=True)
class M4ExecutionPreparation:
    """Metadata-only execution preparation; it grants no read, write, or attempt."""

    packet_sha256: str
    reviewed_git_commit: str
    reviewed_git_tree: str
    metadata_paths: tuple[str, ...]
    inventory_size_bytes: int
    inventory_sha256: str
    tasks: tuple[TaskInputInventory, ...]
    runs: tuple[PreparedRun, ...]
    oracle_code_path: str = _ORACLE_CODE_PATH
    candidate_body_reads_authorized: bool = False
    oracle_calls_authorized: int = 0
    attempts_authorized: int = 0
    writes_authorized: int = 0


def _input_identity(value: Mapping[str, object]) -> InputObjectIdentity:
    if set(value) != {
        "bucket",
        "object_key",
        "purpose",
        "sha256",
        "size_bytes",
        "task_id",
        "truth_bearing",
    }:
        raise ContractValidationError("M4 input metadata fields drifted")
    try:
        item = InputObjectIdentity(
            bucket=cast(str, value["bucket"]),
            object_key=cast(str, value["object_key"]),
            purpose=cast(str, value["purpose"]),
            sha256=cast(str, value["sha256"]),
            size_bytes=cast(int, value["size_bytes"]),
            task_id=cast(str, value["task_id"]),
            truth_bearing=cast(bool, value["truth_bearing"]),
        )
    except (KeyError, TypeError) as exc:
        raise ContractValidationError("M4 input metadata member is invalid") from exc
    if (
        not isinstance(item.bucket, str)
        or not item.bucket
        or not isinstance(item.object_key, str)
        or not item.object_key
        or item.purpose not in _PURPOSES
        or item.task_id not in _TASKS
        or isinstance(item.size_bytes, bool)
        or not isinstance(item.size_bytes, int)
        or item.size_bytes < 1
        or not isinstance(item.sha256, str)
        or not _SHA256.fullmatch(item.sha256)
        or not isinstance(item.truth_bearing, bool)
        or item.truth_bearing != (item.purpose == "hidden-truth")
    ):
        raise ContractValidationError("M4 input metadata member is invalid")
    return item


def _prepared_runs(packet: Mapping[str, object]) -> tuple[PreparedRun, ...]:
    raw_runs = packet.get("run_specs")
    if not isinstance(raw_runs, list) or len(raw_runs) != 72:
        raise ContractValidationError("M4 prepared RunSpecs must exact-cover the 72-run pilot")
    runs: list[PreparedRun] = []
    for raw in raw_runs:
        if not isinstance(raw, dict) or set(raw) != {"run_id", "run_spec", "task_id"}:
            raise ContractValidationError("M4 prepared RunSpec member is invalid")
        raw_spec = raw.get("run_spec")
        task_id = raw.get("task_id")
        if not isinstance(raw_spec, dict) or not isinstance(task_id, str):
            raise ContractValidationError("M4 prepared RunSpec member is invalid")
        try:
            run_spec = RunSpec(**raw_spec)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("M4 prepared RunSpec member is invalid") from exc
        if (
            task_id not in _TASKS
            or raw.get("run_id") != run_spec.run_id
            or run_spec.protocol_version != "m4-single-fidelity-screening-v1"
        ):
            raise ContractValidationError("M4 prepared RunSpec identity drifted")
        runs.append(PreparedRun(task_id=task_id, run_spec=run_spec))
    if len({item.run_spec.run_id for item in runs}) != 72 or [
        sum(item.task_id == task_id for item in runs) for task_id in _TASKS
    ] != [36, 36]:
        raise ContractValidationError("M4 prepared RunSpecs have incomplete task coverage")
    return tuple(runs)


def _task_inventories(
    values: tuple[Mapping[str, object], ...],
    runs: tuple[PreparedRun, ...],
) -> tuple[TaskInputInventory, ...]:
    objects = tuple(_input_identity(value) for value in values)
    if len(objects) != 12 or len({(item.bucket, item.object_key) for item in objects}) != 12:
        raise ContractValidationError("M4 input inventory must remain an exact 12-object set")
    tasks: list[TaskInputInventory] = []
    for task_id in _TASKS:
        task_objects = tuple(item for item in objects if item.task_id == task_id)
        if tuple(item.purpose for item in task_objects) != _PURPOSES:
            raise ContractValidationError("M4 task input purpose order or exact cover drifted")
        task_runs = tuple(item.run_spec for item in runs if item.task_id == task_id)
        identities = {item.purpose: item.sha256 for item in task_objects}
        if (
            {item.dataset_manifest_sha256 for item in task_runs} != {identities["dataset-manifest"]}
            or {item.processing_manifest_sha256 for item in task_runs}
            != {identities["processing-manifest"]}
            or {item.split_sha256 for item in task_runs} != {identities["split-manifest"]}
            or {item.feature_sha256 for item in task_runs} != {identities["feature-descriptor"]}
        ):
            raise ContractValidationError("M4 RunSpecs differ from exact input metadata")
        tasks.append(TaskInputInventory(task_id=task_id, objects=task_objects))
    return tuple(tasks)


def prepare_execution_adapter(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    repository_root: Path,
    expected_git_commit: str,
    expected_git_tree: str,
) -> M4ExecutionPreparation:
    """Bind exact input metadata and code path without reading bodies or starting attempts."""

    packet = load_pilot_preflight_packet(
        packet_bytes,
        expected_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
    )
    if (
        packet.get("authorization_boundaries") != _PACKET_AUTHORIZATION_BOUNDARIES
        or packet.get("initial_resource_ledger") != _ZERO_LEDGER
    ):
        raise ContractValidationError("M4 preparation authorization or zero ledger drifted")
    truth_boundary = packet.get("truth_boundary")
    if not isinstance(truth_boundary, dict) or (
        truth_boundary.get("candidate_truth_read_authorized") is not False
        or truth_boundary.get("held_out_read_authorized") is not False
        or truth_boundary.get("oracle_path_bound") is not False
        or truth_boundary.get("packet_build_candidate_truth_reads") != 0
        or truth_boundary.get("packet_build_real_input_reads") != 0
        or truth_boundary.get("runtime_oracle_binding_required") is not True
    ):
        raise ContractValidationError("M4 preparation truth boundary drifted")
    runs = _prepared_runs(packet)
    tasks = _task_inventories(
        reviewed_input_inventory_metadata(repository_root),
        runs,
    )
    inventory_bytes = canonical_json_bytes(
        [asdict(item) for task in tasks for item in task.objects]
    )
    source_artifacts = packet.get("source_artifacts")
    if not isinstance(source_artifacts, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("path"), str)
        for item in source_artifacts
    ):
        raise ContractValidationError("M4 preparation source artifact inventory drifted")
    metadata_paths = tuple(
        sorted({cast(str, item["path"]) for item in source_artifacts} | {_M2_PACKET_PATH})
    )
    return M4ExecutionPreparation(
        packet_sha256=packet_sha256,
        reviewed_git_commit=expected_git_commit,
        reviewed_git_tree=expected_git_tree,
        metadata_paths=metadata_paths,
        inventory_size_bytes=len(inventory_bytes),
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
        tasks=tasks,
        runs=runs,
    )


def _resolved_run(
    preparation: M4ExecutionPreparation,
    run_spec: RunSpec,
) -> tuple[PreparedRun, TaskInputInventory]:
    matches = [item for item in preparation.runs if item.run_spec.run_id == run_spec.run_id]
    if len(matches) != 1 or matches[0].run_spec != run_spec:
        raise ContractValidationError("RunSpec is outside the reviewed M4 preparation")
    prepared = matches[0]
    inventories = [item for item in preparation.tasks if item.task_id == prepared.task_id]
    if len(inventories) != 1:
        raise ContractValidationError("M4 task input inventory is unresolved")
    return prepared, inventories[0]


def _validated_nontruth_inputs(
    inventory: TaskInputInventory,
    inputs: Mapping[str, bytes],
) -> dict[str, bytes]:
    if set(inputs) != set(_PURPOSES):
        raise ContractValidationError("M4 engine inputs do not exact-cover reviewed purposes")
    payloads: dict[str, bytes] = {}
    for item in inventory.objects[:-1]:
        payload = inputs[item.purpose]
        if len(payload) != item.size_bytes or hashlib.sha256(payload).hexdigest() != item.sha256:
            raise ContractValidationError("M4 input identity differs from reviewed metadata")
        payloads[item.purpose] = payload
    return payloads


def _build_replay_engine_from_validated_preparation(
    *,
    preparation: M4ExecutionPreparation,
    run_spec: RunSpec,
    inputs: Mapping[str, bytes],
    repository_root: Path,
) -> ReplayEngine:
    """Build from an already revalidated preparation; never fetch an input object."""

    prepared, inventory = _resolved_run(preparation, run_spec)
    experiment_path = _EXPERIMENT_PATHS[prepared.task_id]
    experiment_config = (repository_root.resolve() / experiment_path).read_bytes()
    loaded_experiment = load_experiment_config(experiment_config)
    if (
        loaded_experiment.sha256 != run_spec.experiment_config_sha256
        or loaded_experiment.value["task_id"] != prepared.task_id
    ):
        raise ContractValidationError("M4 Experiment Config identity is unresolved")
    model_config = m4_model_config_bytes(run_spec.model_id)
    if hashlib.sha256(model_config).hexdigest() != run_spec.model_config_sha256:
        raise ContractValidationError("M4 model config identity is unresolved")

    payloads = _validated_nontruth_inputs(inventory, inputs)
    candidate_features: LoadedFeatureMatrix = load_feature_matrix(
        payloads["candidate-feature-matrix"],
        feature_definitions=sequence_feature_definitions(),
    )
    truth_identity = inventory.objects[-1]
    if truth_identity.purpose != "hidden-truth" or not truth_identity.truth_bearing:
        raise ContractValidationError("M4 hidden-truth identity is not last")
    hidden_truth = inputs["hidden-truth"]
    if (
        len(hidden_truth) != truth_identity.size_bytes
        or hashlib.sha256(hidden_truth).hexdigest() != truth_identity.sha256
    ):
        raise ContractValidationError("M4 hidden-truth identity differs from reviewed metadata")
    return ReplayEngine.from_reviewed_inputs(
        run_spec=run_spec,
        experiment_config=experiment_config,
        feature_descriptor=payloads["feature-descriptor"],
        processing_manifest=payloads["processing-manifest"],
        hidden_truth=hidden_truth,
        task_id=prepared.task_id,
        candidate_features=candidate_features,
        model_config=model_config,
    )


def build_reviewed_replay_engine(
    *,
    preparation: M4ExecutionPreparation,
    packet_bytes: bytes,
    packet_sha256: str,
    expected_git_commit: str,
    expected_git_tree: str,
    run_spec: RunSpec,
    inputs: Mapping[str, bytes],
    repository_root: Path,
) -> ReplayEngine:
    """Rebind reviewed provenance before any input body access, then build once."""

    reviewed = prepare_execution_adapter(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
    )
    if preparation != reviewed:
        raise ContractValidationError("M4 preparation differs from the reviewed packet")
    return _build_replay_engine_from_validated_preparation(
        preparation=reviewed,
        run_spec=run_spec,
        inputs=inputs,
        repository_root=repository_root,
    )


__all__ = [
    "InputObjectIdentity",
    "M4ExecutionPreparation",
    "PreparedRun",
    "TaskInputInventory",
    "build_reviewed_replay_engine",
    "prepare_execution_adapter",
]
