"""One-shot local runtime for the reviewed Issue #76 conditional expansion."""

from __future__ import annotations

import hashlib
import json
import platform
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.evaluation import SequentialMetricSet
from pcdb.m4.conditional_expansion import (
    PILOT_RESOURCE_USAGE,
    CompletedPilotAttempt,
    prepare_conditional_expansion,
)
from pcdb.m4.execution_adapter import (
    M4ExecutionPreparation,
    _build_replay_engine_from_validated_preparation,
)
from pcdb.m4.pilot_runtime import (
    PILOT_EXECUTION_REGION,
    ReadOnlyPilotInputClient,
    _CappedLocalArtifactStore,
    _definition_metric_record,
    _peak_rss_bytes,
    _read_reviewed_inputs,
    _scratch_bytes,
    _utc_timestamp,
    _write_exclusive,
)
from pcdb.replay.publication import ReplayPublication, publish_replay_attempt

CONDITIONAL_EXPANSION_APPROVAL = "APPROVED FOR ISSUE #76 EXACT 272-RUNSPEC M4 CONDITIONAL EXPANSION"

_CAPS = {
    "active_execution_wall_seconds": 21_600,
    "aggregate_rss_bytes": 16 * 1024**3,
    "network_transfer_bytes": 2 * 1024**3,
    "pilot_plus_expansion_attempts": 344,
    "scratch_high_water_bytes": 20 * 1024**3,
}


@dataclass(frozen=True, slots=True)
class ConditionalExpansionAuthorization:
    """External one-shot approval bound to the reviewed expansion packet."""

    approval_comment_id: int
    approval_text: str
    packet_sha256: str
    reviewed_git_commit: str
    reviewed_git_tree: str
    executed_environment_digest: str
    execution_region: str


@dataclass(frozen=True, slots=True)
class ConditionalExpansionExecutionResult:
    """Local immutable evidence from exactly 272 new RunSpec attempts."""

    packet_sha256: str
    publications: tuple[ReplayPublication, ...]
    definition_metric_keys: tuple[str, ...]
    unique_run_ids_started: tuple[str, ...]
    cumulative_attempts_started: int
    cumulative_network_transfer_bytes: int
    cumulative_scratch_high_water_bytes: int
    peak_rss_bytes: int
    cumulative_active_execution_wall_seconds: float


def _validate_authorization(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    expected_git_commit: str,
    expected_git_tree: str,
    authorization: ConditionalExpansionAuthorization,
) -> None:
    if (
        isinstance(authorization.approval_comment_id, bool)
        or not isinstance(authorization.approval_comment_id, int)
        or authorization.approval_comment_id <= 0
        or authorization.approval_text != CONDITIONAL_EXPANSION_APPROVAL
        or authorization.packet_sha256 != packet_sha256
        or hashlib.sha256(packet_bytes).hexdigest() != packet_sha256
        or authorization.reviewed_git_commit != expected_git_commit
        or authorization.reviewed_git_tree != expected_git_tree
        or authorization.execution_region != PILOT_EXECUTION_REGION
        or len(authorization.executed_environment_digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in authorization.executed_environment_digest
        )
    ):
        raise ContractValidationError("M4 conditional expansion external authorization drifted")


class _ConditionalResources:
    """Non-resettable pilot-plus-expansion resource accounting."""

    def __init__(
        self,
        *,
        scratch_root: Path,
        input_client: ReadOnlyPilotInputClient,
        packet_sha256: str,
        approval_comment_id: int,
        clock: Callable[[], float],
    ) -> None:
        self._scratch_root = scratch_root
        self._input_client = input_client
        self._packet_sha256 = packet_sha256
        self._approval_comment_id = approval_comment_id
        self._clock = clock
        self._started_at = clock()
        self._run_ids: list[str] = []
        self._scratch_high_water_bytes = PILOT_RESOURCE_USAGE.scratch_high_water_bytes
        self._peak_rss_bytes = 0

    @property
    def elapsed_seconds(self) -> float:
        return self._clock() - self._started_at

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(self._run_ids)

    @property
    def scratch_high_water_bytes(self) -> int:
        return self._scratch_high_water_bytes

    @property
    def peak_rss_bytes(self) -> int:
        return self._peak_rss_bytes

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
            raise ContractValidationError("M4 conditional projected resource use is invalid")
        transferred = self._input_client.transferred_bytes
        if isinstance(transferred, bool) or not isinstance(transferred, int) or transferred < 0:
            raise ContractValidationError("M4 conditional transfer ledger is invalid")
        scratch = _scratch_bytes(self._scratch_root)
        rss = _peak_rss_bytes()
        self._scratch_high_water_bytes = max(self._scratch_high_water_bytes, scratch)
        self._peak_rss_bytes = max(self._peak_rss_bytes, rss)
        if (
            PILOT_RESOURCE_USAGE.network_transfer_bytes + transferred + expected_network_bytes
            > _CAPS["network_transfer_bytes"]
            or max(
                self._scratch_high_water_bytes,
                scratch + expected_scratch_bytes,
            )
            > _CAPS["scratch_high_water_bytes"]
            or rss > _CAPS["aggregate_rss_bytes"]
            or PILOT_RESOURCE_USAGE.active_execution_wall_seconds + self.elapsed_seconds
            > _CAPS["active_execution_wall_seconds"]
        ):
            raise ContractValidationError("M4 conditional cumulative resource cap reached")

    def start_attempt(self, run_id: str) -> None:
        if run_id in self._run_ids:
            raise ContractValidationError("M4 conditional RunSpec attempt was already started")
        if (
            PILOT_RESOURCE_USAGE.attempts_started + len(self._run_ids)
            >= _CAPS["pilot_plus_expansion_attempts"]
        ):
            raise ContractValidationError("M4 conditional cumulative attempt cap reached")
        payload = canonical_json_bytes(
            {
                "approval_comment_id": self._approval_comment_id,
                "packet_sha256": self._packet_sha256,
                "record_type": "m4-conditional-attempt-start-v1",
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


def _conditional_definition_metric_record(
    *,
    packet_sha256: str,
    task_id: str,
    trajectory_sha256: str,
    run_id: str,
    budget: int,
    definitions: tuple[tuple[str, SequentialMetricSet], ...],
) -> bytes:
    pilot_record = json.loads(
        _definition_metric_record(
            packet_sha256=packet_sha256,
            task_id=task_id,
            trajectory_sha256=trajectory_sha256,
            run_id=run_id,
            budget=budget,
            definitions=definitions,
        )
    )
    pilot_record["record_type"] = "m4-conditional-final-definition-metrics-v1"
    return canonical_json_bytes(pilot_record)


def execute_reviewed_conditional_expansion_locally(
    *,
    preparation: M4ExecutionPreparation,
    packet_bytes: bytes,
    packet_sha256: str,
    pilot_packet_bytes: bytes,
    completed_pilot_attempts: Sequence[CompletedPilotAttempt],
    expected_git_commit: str,
    expected_git_tree: str,
    repository_root: Path,
    input_client: ReadOnlyPilotInputClient,
    scratch_root: Path,
    authorization: ConditionalExpansionAuthorization,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    clock: Callable[[], float] = time.perf_counter,
) -> ConditionalExpansionExecutionResult:
    """Execute exactly the reviewed 272 missing rows once into new local scratch."""

    _validate_authorization(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
        authorization=authorization,
    )
    reviewed = prepare_conditional_expansion(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
        pilot_packet_bytes=pilot_packet_bytes,
        completed_pilot_attempts=completed_pilot_attempts,
    )
    if preparation != reviewed:
        raise ContractValidationError("M4 conditional preparation differs from reviewed packet")
    completed_ids = {item.run_id for item in completed_pilot_attempts}
    if (
        len(reviewed.runs) != 272
        or len({item.run_spec.run_id for item in reviewed.runs}) != 272
        or completed_ids & {item.run_spec.run_id for item in reviewed.runs}
        or {item.run_spec.environment_digest for item in reviewed.runs}
        != {authorization.executed_environment_digest}
    ):
        raise ContractValidationError("M4 conditional RunSpec cover or identity drifted")
    root = scratch_root.resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ContractValidationError("M4 conditional scratch root must start empty")
    if input_client.transferred_bytes != 0:
        raise ContractValidationError("M4 conditional transfer ledger must start at zero")
    root.mkdir(parents=True, exist_ok=True)
    resources = _ConditionalResources(
        scratch_root=root,
        input_client=input_client,
        packet_sha256=packet_sha256,
        approval_comment_id=authorization.approval_comment_id,
        clock=clock,
    )
    resources.guard()
    inputs = _read_reviewed_inputs(reviewed, input_client, cast(Any, resources))
    store = _CappedLocalArtifactStore(root / "objects", cast(Any, resources))
    publications: list[ReplayPublication] = []
    definition_metric_keys: list[str] = []
    for prepared in reviewed.runs:
        resources.start_attempt(prepared.run_spec.run_id)
        started_at = _utc_timestamp(now)
        engine = _build_replay_engine_from_validated_preparation(
            preparation=reviewed,
            run_spec=prepared.run_spec,
            inputs=inputs[prepared.task_id],
            repository_root=repository_root,
        )
        trajectory = engine.run()
        resources.guard()
        if trajectory.run_id != prepared.run_spec.run_id or not trajectory.complete:
            raise ContractValidationError("M4 conditional trajectory is incomplete or drifted")
        definition_key = (
            f"m4/issue-76/conditional-expansion/{packet_sha256}/runs/{trajectory.run_id}/"
            "attempts/attempt-001/conditional-final-definition-metrics.json"
        )
        definition_record = _conditional_definition_metric_record(
            packet_sha256=packet_sha256,
            task_id=prepared.task_id,
            trajectory_sha256=trajectory.sha256,
            run_id=trajectory.run_id,
            budget=prepared.run_spec.budget,
            definitions=engine.final_m4_definition_metrics(),
        )
        publication = publish_replay_attempt(
            trajectory,
            store=store,
            attempt_id="attempt-001",
            started_at=started_at,
            finished_at=_utc_timestamp(now),
            executed_git_commit=expected_git_commit,
            executed_environment_digest=authorization.executed_environment_digest,
            runtime_version=f"python-{platform.python_version()}",
            key_prefix=f"m4/issue-76/conditional-expansion/{packet_sha256}",
            executor_type="compute_server",
            region=authorization.execution_region,
            additional_outputs=((definition_key, definition_record),),
        )
        if definition_key not in publication.output_keys:
            raise ContractValidationError("M4 conditional metric is outside RunAttempt outputs")
        publications.append(publication)
        definition_metric_keys.append(definition_key)
        resources.guard()
    if len(publications) != 272 or len(resources.run_ids) != 272:
        raise ContractValidationError("M4 conditional execution did not exact-cover 272 attempts")
    resources.guard()
    return ConditionalExpansionExecutionResult(
        packet_sha256=packet_sha256,
        publications=tuple(publications),
        definition_metric_keys=tuple(definition_metric_keys),
        unique_run_ids_started=resources.run_ids,
        cumulative_attempts_started=PILOT_RESOURCE_USAGE.attempts_started + len(resources.run_ids),
        cumulative_network_transfer_bytes=(
            PILOT_RESOURCE_USAGE.network_transfer_bytes + input_client.transferred_bytes
        ),
        cumulative_scratch_high_water_bytes=resources.scratch_high_water_bytes,
        peak_rss_bytes=resources.peak_rss_bytes,
        cumulative_active_execution_wall_seconds=(
            PILOT_RESOURCE_USAGE.active_execution_wall_seconds + resources.elapsed_seconds
        ),
    )


__all__ = [
    "CONDITIONAL_EXPANSION_APPROVAL",
    "ConditionalExpansionAuthorization",
    "ConditionalExpansionExecutionResult",
    "execute_reviewed_conditional_expansion_locally",
]
