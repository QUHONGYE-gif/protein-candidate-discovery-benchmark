"""Minimal fail-closed local execution layer for the bounded Issue #76 pilot."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.evaluation import SequentialMetricSet
from pcdb.m4.execution_adapter import (
    M4ExecutionPreparation,
    build_reviewed_replay_engine,
    prepare_execution_adapter,
)
from pcdb.replay.publication import ReplayPublication, publish_replay_attempt
from pcdb.storage.base import ArtifactMetadata
from pcdb.storage.local import LocalArtifactStore

PILOT_APPROVAL = "APPROVED FOR ISSUE #76 EXACT 72-RUNSPEC M4 PILOT EXECUTION"

_RESOURCE_CAPS = {
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
}
_DEFINITION_IDS = (
    "upper-tail-1pct",
    "upper-tail-0.5pct",
    "upper-tail-2pct",
)


class ReadOnlyPilotInputClient(Protocol):
    """Only remote capabilities exposed to the M4 pilot."""

    @property
    def transferred_bytes(self) -> int: ...

    def head(self, bucket: str, key: str) -> object: ...

    def get(self, bucket: str, key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class PilotAuthorization:
    """External pilot approval bound to exact source, packet, and environment identities."""

    approval_comment_id: int
    approval_text: str
    packet_sha256: str
    reviewed_git_commit: str
    reviewed_git_tree: str
    executed_environment_digest: str


@dataclass(frozen=True, slots=True)
class PilotExecutionResult:
    """Local engineering evidence from one complete 72-RunSpec pilot process."""

    packet_sha256: str
    publications: tuple[ReplayPublication, ...]
    definition_metric_keys: tuple[str, ...]
    unique_run_ids_started: tuple[str, ...]
    network_transfer_bytes: int
    scratch_high_water_bytes: int
    peak_rss_bytes: int
    active_execution_wall_seconds: float


def _validate_authorization(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    expected_git_commit: str,
    expected_git_tree: str,
    authorization: PilotAuthorization,
) -> None:
    if (
        isinstance(authorization.approval_comment_id, bool)
        or not isinstance(authorization.approval_comment_id, int)
        or authorization.approval_comment_id <= 0
        or authorization.approval_text != PILOT_APPROVAL
        or authorization.packet_sha256 != packet_sha256
        or hashlib.sha256(packet_bytes).hexdigest() != packet_sha256
        or authorization.reviewed_git_commit != expected_git_commit
        or authorization.reviewed_git_tree != expected_git_tree
        or len(authorization.executed_environment_digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in authorization.executed_environment_digest
        )
    ):
        raise ContractValidationError("M4 pilot external authorization is absent or drifted")


def _packet_caps(packet_bytes: bytes) -> Mapping[str, int]:
    try:
        value = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M4 pilot packet must be canonical JSON") from None
    if (
        not isinstance(value, dict)
        or canonical_json_bytes(value) != packet_bytes
        or value.get("resource_caps") != _RESOURCE_CAPS
    ):
        raise ContractValidationError("M4 pilot resource caps drifted")
    return _RESOURCE_CAPS


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)


def _scratch_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _write_exclusive(path: Path, payload: bytes) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            target.unlink()
        raise


class _PilotResources:
    """One-process cumulative cap enforcement with immutable attempt-start markers."""

    def __init__(
        self,
        *,
        scratch_root: Path,
        input_client: ReadOnlyPilotInputClient,
        packet_sha256: str,
        approval_comment_id: int,
        caps: Mapping[str, int],
        clock: Callable[[], float],
    ) -> None:
        self._scratch_root = scratch_root
        self._input_client = input_client
        self._packet_sha256 = packet_sha256
        self._approval_comment_id = approval_comment_id
        self._caps = caps
        self._clock = clock
        self._started_at = clock()
        self._run_ids: list[str] = []
        self._scratch_high_water_bytes = 0
        self._peak_rss_bytes = 0

    @property
    def elapsed_seconds(self) -> float:
        return self._clock() - self._started_at

    @property
    def scratch_high_water_bytes(self) -> int:
        return self._scratch_high_water_bytes

    @property
    def peak_rss_bytes(self) -> int:
        return self._peak_rss_bytes

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(self._run_ids)

    def guard(
        self,
        *,
        expected_network_bytes: int = 0,
        expected_scratch_bytes: int = 0,
    ) -> None:
        if (
            isinstance(expected_network_bytes, bool)
            or not isinstance(expected_network_bytes, int)
            or expected_network_bytes < 0
            or isinstance(expected_scratch_bytes, bool)
            or not isinstance(expected_scratch_bytes, int)
            or expected_scratch_bytes < 0
        ):
            raise ContractValidationError("M4 pilot projected resource use is invalid")
        transferred = self._input_client.transferred_bytes
        if isinstance(transferred, bool) or not isinstance(transferred, int) or transferred < 0:
            raise ContractValidationError("M4 pilot transfer ledger is invalid")
        scratch = _scratch_bytes(self._scratch_root)
        rss = _peak_rss_bytes()
        elapsed = self.elapsed_seconds
        self._scratch_high_water_bytes = max(self._scratch_high_water_bytes, scratch)
        self._peak_rss_bytes = max(self._peak_rss_bytes, rss)
        if (
            transferred + expected_network_bytes > self._caps["network_transfer_bytes"]
            or scratch + expected_scratch_bytes > self._caps["scratch_high_water_bytes"]
            or rss > self._caps["aggregate_rss_bytes"]
            or elapsed > self._caps["active_execution_wall_seconds"]
        ):
            raise ContractValidationError("M4 pilot cumulative resource cap reached")

    def start_attempt(self, run_id: str) -> None:
        if run_id in self._run_ids:
            raise ContractValidationError("M4 pilot RunSpec attempt was already started")
        if len(self._run_ids) >= self._caps["pilot_unique_run_specs_and_attempts"]:
            raise ContractValidationError("M4 pilot attempt cap reached")
        payload = canonical_json_bytes(
            {
                "approval_comment_id": self._approval_comment_id,
                "packet_sha256": self._packet_sha256,
                "record_type": "m4-pilot-attempt-start-v1",
                "run_id": run_id,
            }
        )
        self.guard(expected_scratch_bytes=len(payload))
        _write_exclusive(
            self._scratch_root / "ledger" / "attempts" / f"{run_id}.started.json",
            payload,
        )
        self._run_ids.append(run_id)
        self.guard()


class _CappedLocalArtifactStore:
    """Local immutable store that checks the cumulative scratch cap before every write."""

    def __init__(self, root: Path, resources: _PilotResources) -> None:
        self._store = LocalArtifactStore(root)
        self._resources = resources

    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> ArtifactMetadata:
        if overwrite:
            raise ContractValidationError("M4 pilot artifact overwrite is forbidden")
        self._resources.guard(expected_scratch_bytes=len(data))
        metadata = self._store.put_bytes(key, data, overwrite=False)
        self._resources.guard()
        return metadata

    def get_bytes(self, key: str) -> bytes:
        return self._store.get_bytes(key)

    def exists(self, key: str) -> bool:
        return self._store.exists(key)


def _head_identity(metadata: object) -> tuple[int, str | None]:
    if isinstance(metadata, tuple) and len(metadata) == 2:
        size, digest = metadata
    else:
        size = getattr(metadata, "size_bytes", None)
        digest = getattr(metadata, "sha256", None)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ContractValidationError("M4 pilot HEAD returned invalid object size")
    if digest is not None and not isinstance(digest, str):
        raise ContractValidationError("M4 pilot HEAD returned invalid object digest")
    return size, digest


def _read_reviewed_inputs(
    preparation: M4ExecutionPreparation,
    input_client: ReadOnlyPilotInputClient,
    resources: _PilotResources,
) -> dict[str, dict[str, bytes]]:
    objects = tuple(item for task in preparation.tasks for item in task.objects)
    if len(objects) != 12:
        raise ContractValidationError("M4 pilot input inventory is not the exact 12-object set")
    for item in objects:
        resources.guard()
        size, digest = _head_identity(input_client.head(item.bucket, item.object_key))
        resources.guard()
        if size != item.size_bytes or (digest is not None and digest != item.sha256):
            raise ContractValidationError("M4 pilot HEAD identity differs from reviewed input")

    ordered = tuple(item for item in objects if not item.truth_bearing) + tuple(
        item for item in objects if item.truth_bearing
    )
    if [item.truth_bearing for item in ordered] != [*([False] * 10), True, True]:
        raise ContractValidationError("M4 pilot truth-bearing input order drifted")
    inputs: dict[str, dict[str, bytes]] = {task.task_id: {} for task in preparation.tasks}
    for item in ordered:
        resources.guard(expected_network_bytes=item.size_bytes)
        payload = input_client.get(item.bucket, item.object_key)
        resources.guard()
        if len(payload) != item.size_bytes or hashlib.sha256(payload).hexdigest() != item.sha256:
            raise ContractValidationError("M4 pilot GET identity differs from reviewed input")
        inputs[item.task_id][item.purpose] = payload
    return inputs


def _utc_timestamp(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractValidationError("M4 pilot clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _definition_metric_record(
    *,
    packet_sha256: str,
    task_id: str,
    trajectory_sha256: str,
    run_id: str,
    budget: int,
    definitions: tuple[tuple[str, SequentialMetricSet], ...],
) -> bytes:
    if tuple(definition_id for definition_id, _ in definitions) != _DEFINITION_IDS:
        raise ContractValidationError("M4 pilot definition metric set drifted")
    values: list[dict[str, object]] = []
    for definition_id, metrics in definitions:
        recall = metrics.high_value_discovery_recall
        success = metrics.success_at_budget
        if (
            recall.status != "defined"
            or isinstance(recall.value, bool)
            or not isinstance(recall.value, (int, float))
            or not 0 <= recall.value <= 1
            or success.status != "defined"
            or not isinstance(success.value, bool)
        ):
            raise ContractValidationError("M4 pilot final definition metric is undefined")
        values.append({"definition_id": definition_id, "metrics": asdict(metrics)})
    return canonical_json_bytes(
        {
            "attempt_id": "attempt-001",
            "budget": budget,
            "definitions": values,
            "packet_sha256": packet_sha256,
            "record_type": "m4-pilot-final-definition-metrics-v1",
            "run_id": run_id,
            "task_id": task_id,
            "trajectory_sha256": trajectory_sha256,
        }
    )


def execute_reviewed_pilot_locally(
    *,
    preparation: M4ExecutionPreparation,
    packet_bytes: bytes,
    packet_sha256: str,
    expected_git_commit: str,
    expected_git_tree: str,
    repository_root: Path,
    input_client: ReadOnlyPilotInputClient,
    scratch_root: Path,
    authorization: PilotAuthorization,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    clock: Callable[[], float] = time.perf_counter,
) -> PilotExecutionResult:
    """Execute exactly one reviewed 72-run pilot into empty local scratch; never write COS."""

    _validate_authorization(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
        authorization=authorization,
    )
    caps = _packet_caps(packet_bytes)
    reviewed = prepare_execution_adapter(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
    )
    if preparation != reviewed:
        raise ContractValidationError("M4 pilot preparation differs from the reviewed packet")
    if (
        len(reviewed.runs) != caps["pilot_unique_run_specs_and_attempts"]
        or len({item.run_spec.run_id for item in reviewed.runs}) != len(reviewed.runs)
        or {item.run_spec.environment_digest for item in reviewed.runs}
        != {authorization.executed_environment_digest}
    ):
        raise ContractValidationError("M4 pilot runtime identity or RunSpec cover drifted")
    root = scratch_root.resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ContractValidationError("M4 pilot scratch root must start empty")
    root.mkdir(parents=True, exist_ok=True)
    if input_client.transferred_bytes != 0:
        raise ContractValidationError("M4 pilot transfer ledger must start at zero")
    resources = _PilotResources(
        scratch_root=root,
        input_client=input_client,
        packet_sha256=packet_sha256,
        approval_comment_id=authorization.approval_comment_id,
        caps=caps,
        clock=clock,
    )
    resources.guard()
    inputs = _read_reviewed_inputs(reviewed, input_client, resources)
    store = _CappedLocalArtifactStore(root / "objects", resources)
    publications: list[ReplayPublication] = []
    definition_metric_keys: list[str] = []
    for prepared in reviewed.runs:
        resources.start_attempt(prepared.run_spec.run_id)
        started_at = _utc_timestamp(now)
        engine = build_reviewed_replay_engine(
            preparation=reviewed,
            packet_bytes=packet_bytes,
            packet_sha256=packet_sha256,
            expected_git_commit=expected_git_commit,
            expected_git_tree=expected_git_tree,
            run_spec=prepared.run_spec,
            inputs=inputs[prepared.task_id],
            repository_root=repository_root,
        )
        trajectory = engine.run()
        resources.guard()
        if trajectory.run_id != prepared.run_spec.run_id or not trajectory.complete:
            raise ContractValidationError("M4 pilot trajectory is incomplete or drifted")
        definition_metric_key = (
            f"m4/issue-76/pilot/{packet_sha256}/runs/{trajectory.run_id}/"
            "attempts/attempt-001/pilot-final-definition-metrics.json"
        )
        definition_metric_record = _definition_metric_record(
            packet_sha256=packet_sha256,
            task_id=prepared.task_id,
            trajectory_sha256=trajectory.sha256,
            run_id=trajectory.run_id,
            budget=prepared.run_spec.budget,
            definitions=engine.final_m4_definition_metrics(),
        )
        store.put_bytes(definition_metric_key, definition_metric_record, overwrite=False)
        definition_metric_keys.append(definition_metric_key)
        publications.append(
            publish_replay_attempt(
                trajectory,
                store=store,
                attempt_id="attempt-001",
                started_at=started_at,
                finished_at=_utc_timestamp(now),
                executed_git_commit=expected_git_commit,
                executed_environment_digest=authorization.executed_environment_digest,
                runtime_version=f"python-{platform.python_version()}",
                key_prefix=f"m4/issue-76/pilot/{packet_sha256}",
                executor_type="compute_server",
                region="ap-beijing",
            )
        )
        resources.guard()
    if len(publications) != 72 or len(resources.run_ids) != 72:
        raise ContractValidationError("M4 pilot did not exact-cover all 72 attempts")
    resources.guard()
    return PilotExecutionResult(
        packet_sha256=packet_sha256,
        publications=tuple(publications),
        definition_metric_keys=tuple(definition_metric_keys),
        unique_run_ids_started=resources.run_ids,
        network_transfer_bytes=input_client.transferred_bytes,
        scratch_high_water_bytes=resources.scratch_high_water_bytes,
        peak_rss_bytes=resources.peak_rss_bytes,
        active_execution_wall_seconds=resources.elapsed_seconds,
    )


__all__ = [
    "PILOT_APPROVAL",
    "PilotAuthorization",
    "PilotExecutionResult",
    "ReadOnlyPilotInputClient",
    "execute_reviewed_pilot_locally",
]
