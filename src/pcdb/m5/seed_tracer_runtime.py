"""One-shot reviewed runtime for the capped Issue #85 seed-source tracer."""

from __future__ import annotations

import hashlib
import math
import platform
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.feature_identity import LoadedFeatureMatrix, load_feature_matrix
from pcdb.contracts.run import RunSpec
from pcdb.m2.features import sequence_feature_definitions
from pcdb.m2.models import m4_model_config_bytes
from pcdb.m3.gate_a import _verified_runtime_environment_digest, reviewed_input_inventory_metadata
from pcdb.m4.execution_adapter import (
    M4ExecutionPreparation,
    PreparedRun,
    TaskInputInventory,
    _task_inventories,
    _validated_nontruth_inputs,
)
from pcdb.m4.pilot_runtime import (
    PILOT_EXECUTION_REGION,
    ReadOnlyPilotInputClient,
    _CappedLocalArtifactStore,
    _peak_rss_bytes,
    _read_reviewed_inputs,
    _scratch_bytes,
    _utc_timestamp,
    _write_exclusive,
)
from pcdb.m5.seed_tracer import (
    EXPLORATION_ID,
    FIXED_INITIAL_SEED,
    FIXED_INTERNAL_SEED,
    GREEDY_ID,
    INITIAL_SEEDS,
    INTERNAL_SEEDS,
    METRIC_IDS,
    PairedEffect,
    SeedSourceResult,
    classify_seed_sources,
    load_seed_tracer_packet,
    paired_difference,
)
from pcdb.replay.engine import ReplayEngine, ReplayTrajectory
from pcdb.replay.publication import ReplayPublication, publish_replay_attempt
from pcdb.storage.cos import Issue76M4ReadOnlyCosClient

SEED_TRACER_APPROVAL = (
    "AUTHORIZED BY USER FOR ISSUE #85 EXACT 28-RUNSPEC M5 SEED-SOURCE TRACER EXECUTION"
)
ACCEPTED_HOURLY_COST_CNY = Decimal("1.62")

_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_CAPS = {
    "active_execution_wall_seconds": 900,
    "automatic_retry": 0,
    "delete": 0,
    "network_transfer_bytes": 32 * 1024**2,
    "peak_rss_bytes": 1024**3,
    "replacement_attempts": 0,
    "scratch_high_water_bytes": 16 * 1024**2,
    "unique_run_specs_and_attempts": 28,
}


@dataclass(frozen=True, slots=True)
class TracerRun:
    """One exact RunSpec plus its two seed-axis identities and config bytes."""

    task_id: str
    axis: str
    initial_seed: int
    internal_seed: int | None
    run_spec: RunSpec
    experiment_config: bytes


@dataclass(frozen=True, slots=True)
class SeedTracerPreparation:
    """Metadata-only exact preparation for the 28-run tracer."""

    execution: M4ExecutionPreparation
    runs: tuple[TracerRun, ...]


@dataclass(frozen=True, slots=True)
class SeedTracerAuthorization:
    """External user authorization bound to source, packet, environment, and rate."""

    approval_comment_id: int
    approval_text: str
    packet_sha256: str
    reviewed_git_commit: str
    reviewed_git_tree: str
    executed_environment_digest: str
    execution_region: str
    hourly_cost_cny: Decimal


@dataclass(frozen=True, slots=True)
class SeedTracerExecutionResult:
    """Complete local evidence and frozen verdict from one exact 28-run batch."""

    packet_sha256: str
    publications: tuple[ReplayPublication, ...]
    metric_keys: tuple[str, ...]
    unique_run_ids_started: tuple[str, ...]
    verdict: SeedSourceResult
    result_bytes: bytes
    network_transfer_bytes: int
    scratch_high_water_bytes: int
    peak_rss_bytes: int
    active_execution_wall_seconds: float
    estimated_incremental_cost_cny: Decimal


class SeedTracerAttemptFailure(RuntimeError):
    """Sanitized terminal state for an incomplete one-shot tracer."""

    def __init__(
        self,
        *,
        packet_sha256: str,
        attempts_started: int,
        failure_reason: str,
    ) -> None:
        super().__init__("M5 seed-source tracer runtime failed")
        self.packet_sha256 = packet_sha256
        self.attempts_started = attempts_started
        self.failure_reason = failure_reason


def _validated_hourly_cost(value: Decimal | str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError):
        raise ContractValidationError("M5 tracer hourly cost is invalid") from None
    if not parsed.is_finite() or parsed != ACCEPTED_HOURLY_COST_CNY:
        raise ContractValidationError(
            "M5 tracer hourly cost differs from the accepted current rate"
        )
    return parsed


def _validate_authorization(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    expected_git_commit: str,
    expected_git_tree: str,
    authorization: SeedTracerAuthorization,
) -> None:
    if (
        isinstance(authorization.approval_comment_id, bool)
        or not isinstance(authorization.approval_comment_id, int)
        or authorization.approval_comment_id <= 0
        or authorization.approval_text != SEED_TRACER_APPROVAL
        or authorization.packet_sha256 != packet_sha256
        or hashlib.sha256(packet_bytes).hexdigest() != packet_sha256
        or authorization.reviewed_git_commit != expected_git_commit
        or authorization.reviewed_git_tree != expected_git_tree
        or authorization.execution_region != PILOT_EXECUTION_REGION
        or not _SHA256.fullmatch(authorization.executed_environment_digest)
        or _validated_hourly_cost(authorization.hourly_cost_cny) != ACCEPTED_HOURLY_COST_CNY
    ):
        raise ContractValidationError("M5 tracer external authorization is absent or drifted")


def prepare_seed_tracer_execution(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    repository_root: Path,
    expected_git_commit: str,
    expected_git_tree: str,
) -> SeedTracerPreparation:
    """Strictly rebuild the packet and bind its runs to reviewed input metadata."""

    packet = load_seed_tracer_packet(
        packet_bytes,
        expected_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
    )
    if packet.get("resource_caps") != _EXPECTED_CAPS:
        raise ContractValidationError("M5 tracer resource caps drifted")
    configs = packet.get("experiment_configs")
    raw_runs = packet.get("run_specs")
    if not isinstance(configs, list) or not isinstance(raw_runs, list):
        raise ContractValidationError("M5 tracer run/config inventory is absent")
    configs_by_sha: dict[str, dict[str, Any]] = {}
    for raw_config in configs:
        if not isinstance(raw_config, dict):
            raise ContractValidationError("M5 tracer config inventory is invalid")
        sha256 = raw_config.get("experiment_config_sha256")
        text = raw_config.get("canonical_json_utf8")
        if not isinstance(sha256, str) or not isinstance(text, str) or sha256 in configs_by_sha:
            raise ContractValidationError("M5 tracer config inventory identity is invalid")
        payload = text.encode("utf-8")
        if hashlib.sha256(payload).hexdigest() != sha256 or raw_config.get("size_bytes") != len(
            payload
        ):
            raise ContractValidationError("M5 tracer config inventory bytes drifted")
        configs_by_sha[sha256] = cast(dict[str, Any], raw_config)

    tracer_runs: list[TracerRun] = []
    prepared_runs: list[PreparedRun] = []
    for raw_run in raw_runs:
        if not isinstance(raw_run, dict) or not isinstance(raw_run.get("run_spec"), dict):
            raise ContractValidationError("M5 tracer RunSpec inventory is invalid")
        try:
            run_spec = RunSpec(**cast(dict[str, Any], raw_run["run_spec"]))
        except (TypeError, ValueError):
            raise ContractValidationError("M5 tracer RunSpec is invalid") from None
        task_id = raw_run.get("task_id")
        axis = raw_run.get("axis")
        config = configs_by_sha.get(run_spec.experiment_config_sha256)
        if (
            not isinstance(task_id, str)
            or axis not in {"initial", "internal"}
            or raw_run.get("run_id") != run_spec.run_id
            or raw_run.get("run_spec_canonical_json_utf8") != run_spec.canonical_json()
            or config is None
            or config.get("task_id") != task_id
            or config.get("acquisition_id") != run_spec.acquisition_id
            or config.get("initial_seed") != run_spec.seed
        ):
            raise ContractValidationError("M5 tracer run/config cross-link drifted")
        internal_seed = config.get("internal_seed")
        if internal_seed is not None and (
            isinstance(internal_seed, bool) or not isinstance(internal_seed, int)
        ):
            raise ContractValidationError("M5 tracer internal seed is invalid")
        if axis == "initial":
            if (
                run_spec.seed not in INITIAL_SEEDS
                or (
                    run_spec.acquisition_id == EXPLORATION_ID
                    and internal_seed != FIXED_INTERNAL_SEED
                )
                or (run_spec.acquisition_id == GREEDY_ID and internal_seed is not None)
            ):
                raise ContractValidationError("M5 tracer initial-axis identity drifted")
        elif (
            run_spec.seed != FIXED_INITIAL_SEED
            or run_spec.acquisition_id != EXPLORATION_ID
            or internal_seed not in INTERNAL_SEEDS[1:]
        ):
            raise ContractValidationError("M5 tracer internal-axis identity drifted")
        experiment_config = cast(str, config["canonical_json_utf8"]).encode("utf-8")
        tracer_runs.append(
            TracerRun(
                task_id=task_id,
                axis=cast(str, axis),
                initial_seed=run_spec.seed,
                internal_seed=internal_seed,
                run_spec=run_spec,
                experiment_config=experiment_config,
            )
        )
        prepared_runs.append(PreparedRun(task_id=task_id, run_spec=run_spec))
    if (
        len(tracer_runs) != 28
        or len({item.run_spec.run_id for item in tracer_runs}) != 28
        or len(configs_by_sha) != 28
    ):
        raise ContractValidationError("M5 tracer preparation is not an exact 28-run cover")
    root = repository_root.resolve()
    tasks = _task_inventories(
        reviewed_input_inventory_metadata(root),
        tuple(prepared_runs),
    )
    inventory_bytes = canonical_json_bytes(
        [asdict(item) for task in tasks for item in task.objects]
    )
    source_artifacts = packet["reviewed_identity_inventory"]["source_artifacts"]
    metadata_paths = tuple(sorted(str(item["path"]) for item in source_artifacts))
    execution = M4ExecutionPreparation(
        packet_sha256=packet_sha256,
        reviewed_git_commit=expected_git_commit,
        reviewed_git_tree=expected_git_tree,
        metadata_paths=metadata_paths,
        inventory_size_bytes=len(inventory_bytes),
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
        tasks=tasks,
        runs=tuple(prepared_runs),
    )
    return SeedTracerPreparation(execution=execution, runs=tuple(tracer_runs))


def _task_inventory(
    preparation: SeedTracerPreparation,
    task_id: str,
) -> TaskInputInventory:
    matches = [item for item in preparation.execution.tasks if item.task_id == task_id]
    if len(matches) != 1:
        raise ContractValidationError("M5 tracer task input inventory is unresolved")
    return matches[0]


def _build_tracer_engine(
    *,
    preparation: SeedTracerPreparation,
    prepared: TracerRun,
    inputs: Mapping[str, bytes],
) -> ReplayEngine:
    inventory = _task_inventory(preparation, prepared.task_id)
    payloads = _validated_nontruth_inputs(inventory, inputs)
    model_config = m4_model_config_bytes(prepared.run_spec.model_id)
    if hashlib.sha256(model_config).hexdigest() != prepared.run_spec.model_config_sha256:
        raise ContractValidationError("M5 tracer model config identity is unresolved")
    candidate_features: LoadedFeatureMatrix = load_feature_matrix(
        payloads["candidate-feature-matrix"],
        feature_definitions=sequence_feature_definitions(),
    )
    truth_identity = inventory.objects[-1]
    hidden_truth = inputs.get("hidden-truth")
    if (
        truth_identity.purpose != "hidden-truth"
        or not truth_identity.truth_bearing
        or not isinstance(hidden_truth, bytes)
        or len(hidden_truth) != truth_identity.size_bytes
        or hashlib.sha256(hidden_truth).hexdigest() != truth_identity.sha256
    ):
        raise ContractValidationError("M5 tracer hidden-truth identity differs")
    return ReplayEngine.from_reviewed_inputs(
        run_spec=prepared.run_spec,
        experiment_config=prepared.experiment_config,
        feature_descriptor=payloads["feature-descriptor"],
        processing_manifest=payloads["processing-manifest"],
        hidden_truth=hidden_truth,
        task_id=prepared.task_id,
        candidate_features=candidate_features,
        model_config=model_config,
    )


class _TracerResources:
    """One non-resettable batch ledger enforcing every frozen #85 cap."""

    def __init__(
        self,
        *,
        scratch_root: Path,
        input_client: ReadOnlyPilotInputClient,
        packet_sha256: str,
        approval_comment_id: int,
        hourly_cost_cny: Decimal,
        clock: Callable[[], float],
    ) -> None:
        self._scratch_root = scratch_root
        self._input_client = input_client
        self._packet_sha256 = packet_sha256
        self._approval_comment_id = approval_comment_id
        self._hourly_cost_cny = hourly_cost_cny
        self._clock = clock
        self._started_at = clock()
        self._run_ids: list[str] = []
        self._scratch_high_water_bytes = 0
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

    def estimated_cost_cny(self) -> Decimal:
        return self._hourly_cost_cny * Decimal(str(self.elapsed_seconds)) / Decimal(3600)

    def guard(
        self,
        *,
        expected_network_bytes: int = 0,
        expected_scratch_bytes: int = 0,
        before_attempt: bool = False,
    ) -> None:
        if (
            isinstance(expected_network_bytes, bool)
            or not isinstance(expected_network_bytes, int)
            or expected_network_bytes < 0
            or isinstance(expected_scratch_bytes, bool)
            or not isinstance(expected_scratch_bytes, int)
            or expected_scratch_bytes < 0
        ):
            raise ContractValidationError("M5 tracer projected resource use is invalid")
        transferred = self._input_client.transferred_bytes
        scratch = _scratch_bytes(self._scratch_root)
        rss = _peak_rss_bytes()
        elapsed = self.elapsed_seconds
        self._scratch_high_water_bytes = max(self._scratch_high_water_bytes, scratch)
        self._peak_rss_bytes = max(self._peak_rss_bytes, rss)
        if (
            isinstance(transferred, bool)
            or not isinstance(transferred, int)
            or transferred < 0
            or transferred + expected_network_bytes > _EXPECTED_CAPS["network_transfer_bytes"]
            or scratch + expected_scratch_bytes > _EXPECTED_CAPS["scratch_high_water_bytes"]
            or rss > _EXPECTED_CAPS["peak_rss_bytes"]
            or elapsed > _EXPECTED_CAPS["active_execution_wall_seconds"]
            or self.estimated_cost_cny() > ACCEPTED_HOURLY_COST_CNY / Decimal(4)
            or (
                before_attempt
                and (
                    transferred >= _EXPECTED_CAPS["network_transfer_bytes"]
                    or scratch >= _EXPECTED_CAPS["scratch_high_water_bytes"]
                    or rss >= _EXPECTED_CAPS["peak_rss_bytes"]
                    or elapsed >= _EXPECTED_CAPS["active_execution_wall_seconds"]
                    or self.estimated_cost_cny() >= ACCEPTED_HOURLY_COST_CNY / Decimal(4)
                )
            )
        ):
            raise ContractValidationError("M5 tracer cumulative resource cap reached")

    def start_attempt(self, run_id: str) -> None:
        if run_id in self._run_ids:
            raise ContractValidationError("M5 tracer RunSpec attempt was already started")
        if len(self._run_ids) >= _EXPECTED_CAPS["unique_run_specs_and_attempts"]:
            raise ContractValidationError("M5 tracer attempt cap reached")
        payload = canonical_json_bytes(
            {
                "approval_comment_id": self._approval_comment_id,
                "hourly_cost_cny": str(self._hourly_cost_cny),
                "packet_sha256": self._packet_sha256,
                "record_type": "m5-seed-tracer-attempt-start-v1",
                "run_id": run_id,
            }
        )
        self.guard(expected_scratch_bytes=len(payload), before_attempt=True)
        _write_exclusive(
            self._scratch_root / "ledger" / "attempts" / f"{run_id}.started.json",
            payload,
        )
        self._run_ids.append(run_id)
        self.guard()


def _metric_decimal(value: object, metric_id: str) -> Decimal:
    status = getattr(value, "status", None)
    raw = getattr(value, "value", None)
    reason = getattr(value, "undefined_reason", None)
    if (
        status != "defined"
        or reason is not None
        or isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(float(raw))
    ):
        raise ContractValidationError(f"M5 tracer {metric_id} is undefined or non-finite")
    return Decimal(str(raw))


def _metric_record(
    *,
    packet_sha256: str,
    prepared: TracerRun,
    trajectory: ReplayTrajectory,
    definitions: tuple[tuple[str, Any], ...],
) -> bytes:
    final = trajectory.events[-1].metrics
    if final is None:
        raise ContractValidationError("M5 tracer final metric set is absent")
    for metric_id in METRIC_IDS:
        _metric_decimal(getattr(final, metric_id), metric_id)
    return canonical_json_bytes(
        {
            "attempt_id": "attempt-001",
            "axis": prepared.axis,
            "definition_sensitivity": [
                {"definition_id": definition_id, "metrics": asdict(metrics)}
                for definition_id, metrics in definitions
            ],
            "initial_seed": prepared.initial_seed,
            "internal_seed": prepared.internal_seed,
            "packet_sha256": packet_sha256,
            "primary_metrics": asdict(final),
            "record_type": "m5-seed-tracer-final-metrics-v1",
            "run_id": prepared.run_spec.run_id,
            "task_id": prepared.task_id,
            "trajectory_sha256": trajectory.sha256,
        }
    )


def _post_initial_selection(trajectory: ReplayTrajectory) -> frozenset[str]:
    return frozenset(
        candidate_id
        for event in trajectory.events[1:]
        for candidate_id in event.selected_candidate_ids
    )


def _comparison_value(result: SeedSourceResult) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for item in result.comparisons:
        values.append(
            {
                "initial": {
                    "ci_lower": str(item.initial.ci_lower),
                    "ci_upper": str(item.initial.ci_upper),
                    "mean": str(item.initial.mean),
                    "n": item.initial.n,
                    "sample_sd": str(item.initial.sample_sd),
                },
                "initial_dominant": item.initial_dominant,
                "internal": {
                    "ci_lower": str(item.internal.ci_lower),
                    "ci_upper": str(item.internal.ci_upper),
                    "mean": str(item.internal.mean),
                    "n": item.internal.n,
                    "sample_sd": str(item.internal.sample_sd),
                },
                "internal_dominant": item.internal_dominant,
                "metric_id": item.metric_id,
                "task_id": item.task_id,
            }
        )
    return values


def _classify_trajectories(
    preparation: SeedTracerPreparation,
    trajectories: Mapping[str, ReplayTrajectory],
) -> tuple[SeedSourceResult, list[dict[str, object]]]:
    records = {item.run_spec.run_id: item for item in preparation.runs}
    if set(trajectories) != set(records):
        raise ContractValidationError("M5 tracer trajectories do not exact-cover 28 RunSpecs")
    index = {
        (
            item.task_id,
            item.run_spec.acquisition_id,
            item.initial_seed,
            item.internal_seed,
        ): trajectories[item.run_spec.run_id]
        for item in preparation.runs
    }
    effects: list[PairedEffect] = []
    overlap: list[dict[str, object]] = []
    for task_id in ("alpha-amylase", "ired"):
        for initial_seed in INITIAL_SEEDS:
            exploration = index[(task_id, EXPLORATION_ID, initial_seed, FIXED_INTERNAL_SEED)]
            greedy = index[(task_id, GREEDY_ID, initial_seed, None)]
            if (
                exploration.events[0].selected_candidate_ids
                != greedy.events[0].selected_candidate_ids
            ):
                raise ContractValidationError("M5 tracer paired initial selections differ")
            for metric_id in METRIC_IDS:
                exploration_metric = _metric_decimal(
                    getattr(exploration.events[-1].metrics, metric_id), metric_id
                )
                greedy_metric = _metric_decimal(
                    getattr(greedy.events[-1].metrics, metric_id), metric_id
                )
                effects.append(
                    PairedEffect(
                        task_id=task_id,
                        metric_id=metric_id,
                        axis="initial",
                        seed=initial_seed,
                        difference=paired_difference(
                            metric_id=metric_id,
                            exploration=exploration_metric,
                            greedy=greedy_metric,
                        ),
                    )
                )
            left = _post_initial_selection(exploration)
            right = _post_initial_selection(greedy)
            union = left | right
            overlap.append(
                {
                    "axis": "initial",
                    "jaccard": str(Decimal(len(left & right)) / Decimal(len(union))),
                    "seed": initial_seed,
                    "task_id": task_id,
                }
            )
        greedy = index[(task_id, GREEDY_ID, FIXED_INITIAL_SEED, None)]
        for internal_seed in INTERNAL_SEEDS:
            exploration = index[(task_id, EXPLORATION_ID, FIXED_INITIAL_SEED, internal_seed)]
            if (
                exploration.events[0].selected_candidate_ids
                != greedy.events[0].selected_candidate_ids
            ):
                raise ContractValidationError("M5 tracer internal-axis initial selection drifted")
            for metric_id in METRIC_IDS:
                exploration_metric = _metric_decimal(
                    getattr(exploration.events[-1].metrics, metric_id), metric_id
                )
                greedy_metric = _metric_decimal(
                    getattr(greedy.events[-1].metrics, metric_id), metric_id
                )
                effects.append(
                    PairedEffect(
                        task_id=task_id,
                        metric_id=metric_id,
                        axis="internal",
                        seed=internal_seed,
                        difference=paired_difference(
                            metric_id=metric_id,
                            exploration=exploration_metric,
                            greedy=greedy_metric,
                        ),
                    )
                )
            left = _post_initial_selection(exploration)
            right = _post_initial_selection(greedy)
            union = left | right
            overlap.append(
                {
                    "axis": "internal",
                    "jaccard": str(Decimal(len(left & right)) / Decimal(len(union))),
                    "seed": internal_seed,
                    "task_id": task_id,
                }
            )
    result = classify_seed_sources(effects)
    if result.verdict == "DATA-INCOMPLETE":
        raise ContractValidationError("M5 tracer complete trajectories classified incomplete")
    return result, overlap


def execute_reviewed_seed_tracer_locally(
    *,
    preparation: SeedTracerPreparation,
    packet_bytes: bytes,
    packet_sha256: str,
    expected_git_commit: str,
    expected_git_tree: str,
    repository_root: Path,
    input_client: ReadOnlyPilotInputClient,
    scratch_root: Path,
    authorization: SeedTracerAuthorization,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    clock: Callable[[], float] = time.perf_counter,
) -> SeedTracerExecutionResult:
    """Execute exactly 28 attempts once, publish locally, and classify once."""

    _validate_authorization(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
        authorization=authorization,
    )
    reviewed = prepare_seed_tracer_execution(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
    )
    if preparation != reviewed:
        raise ContractValidationError("M5 tracer preparation differs from reviewed packet")
    if {item.run_spec.environment_digest for item in reviewed.runs} != {
        authorization.executed_environment_digest
    }:
        raise ContractValidationError("M5 tracer environment binding drifted")
    root = scratch_root.resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ContractValidationError("M5 tracer scratch root must start empty")
    if input_client.transferred_bytes != 0:
        raise ContractValidationError("M5 tracer transfer ledger must start at zero")
    root.mkdir(parents=True, exist_ok=True)
    resources = _TracerResources(
        scratch_root=root,
        input_client=input_client,
        packet_sha256=packet_sha256,
        approval_comment_id=authorization.approval_comment_id,
        hourly_cost_cny=authorization.hourly_cost_cny,
        clock=clock,
    )
    resources.guard()
    inputs = _read_reviewed_inputs(
        reviewed.execution,
        input_client,
        cast(Any, resources),
    )
    store = _CappedLocalArtifactStore(root / "objects", cast(Any, resources))
    publications: list[ReplayPublication] = []
    metric_keys: list[str] = []
    trajectories: dict[str, ReplayTrajectory] = {}
    for prepared in reviewed.runs:
        resources.start_attempt(prepared.run_spec.run_id)
        started_at = _utc_timestamp(now)
        engine = _build_tracer_engine(
            preparation=reviewed,
            prepared=prepared,
            inputs=inputs[prepared.task_id],
        )
        trajectory = engine.run()
        resources.guard()
        if trajectory.run_id != prepared.run_spec.run_id or not trajectory.complete:
            raise ContractValidationError("M5 tracer trajectory is incomplete or drifted")
        definitions = engine.final_m4_definition_metrics()
        metric_key = (
            f"m5/issue-85/seed-tracer/{packet_sha256}/runs/{trajectory.run_id}/"
            "attempts/attempt-001/seed-tracer-final-metrics.json"
        )
        metric_record = _metric_record(
            packet_sha256=packet_sha256,
            prepared=prepared,
            trajectory=trajectory,
            definitions=definitions,
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
            key_prefix=f"m5/issue-85/seed-tracer/{packet_sha256}",
            executor_type="compute_server",
            region=authorization.execution_region,
            additional_outputs=((metric_key, metric_record),),
        )
        if metric_key not in publication.output_keys:
            raise ContractValidationError("M5 tracer metric is outside RunAttempt outputs")
        publications.append(publication)
        metric_keys.append(metric_key)
        trajectories[trajectory.run_id] = trajectory
        resources.guard()
    if len(publications) != 28 or len(resources.run_ids) != 28:
        raise ContractValidationError("M5 tracer did not exact-cover 28 attempts")
    verdict, selection_overlap = _classify_trajectories(reviewed, trajectories)
    result_bytes = canonical_json_bytes(
        {
            "active_execution_wall_seconds": resources.elapsed_seconds,
            "attempts_started": len(resources.run_ids),
            "comparisons": _comparison_value(verdict),
            "estimated_incremental_cost_cny": str(resources.estimated_cost_cny()),
            "hourly_cost_cny": str(authorization.hourly_cost_cny),
            "network_transfer_bytes": input_client.transferred_bytes,
            "packet_sha256": packet_sha256,
            "peak_rss_bytes": resources.peak_rss_bytes,
            "record_type": "m5-seed-tracer-result-v1",
            "scratch_high_water_bytes": resources.scratch_high_water_bytes,
            "selection_overlap": selection_overlap,
            "verdict": verdict.verdict,
        }
    )
    resources.guard(expected_scratch_bytes=len(result_bytes))
    _write_exclusive(root / "seed-tracer-result.json", result_bytes)
    resources.guard()
    return SeedTracerExecutionResult(
        packet_sha256=packet_sha256,
        publications=tuple(publications),
        metric_keys=tuple(metric_keys),
        unique_run_ids_started=resources.run_ids,
        verdict=verdict,
        result_bytes=result_bytes,
        network_transfer_bytes=input_client.transferred_bytes,
        scratch_high_water_bytes=resources.scratch_high_water_bytes,
        peak_rss_bytes=resources.peak_rss_bytes,
        active_execution_wall_seconds=resources.elapsed_seconds,
        estimated_incremental_cost_cny=resources.estimated_cost_cny(),
    )


def _verify_runtime_source(
    repository_root: Path,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
) -> None:
    from pcdb.m5 import seed_tracer as packet_module
    from pcdb.replay import engine as replay_module
    from pcdb.replay import oracle as oracle_module
    from pcdb.storage import cos as cos_module

    root = repository_root.resolve()
    expected = {
        "cos": root / "src/pcdb/storage/cos.py",
        "oracle": root / "src/pcdb/replay/oracle.py",
        "packet": root / "src/pcdb/m5/seed_tracer.py",
        "replay": root / "src/pcdb/replay/engine.py",
        "runtime": root / "src/pcdb/m5/seed_tracer_runtime.py",
    }
    actual = {
        "cos": Path(str(cos_module.__file__)),
        "oracle": Path(str(oracle_module.__file__)),
        "packet": Path(str(packet_module.__file__)),
        "replay": Path(str(replay_module.__file__)),
        "runtime": Path(__file__),
    }
    if any(actual[name].resolve() != path.resolve() for name, path in expected.items()):
        raise ContractValidationError("M5 tracer executing source differs from repository root")
    if not _GIT_OBJECT.fullmatch(reviewed_git_commit) or not _GIT_OBJECT.fullmatch(
        reviewed_git_tree
    ):
        raise ContractValidationError("M5 tracer reviewed Git identity is invalid")
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractValidationError("M5 tracer runtime source could not be verified") from exc
    if commit != reviewed_git_commit or tree != reviewed_git_tree or status:
        raise ContractValidationError("M5 tracer checkout is not the exact clean reviewed source")


def _started_attempt_count(scratch_root: Path) -> int:
    ledger = scratch_root.resolve() / "ledger" / "attempts"
    if not ledger.is_dir():
        return 0
    return sum(1 for path in ledger.glob("*.started.json") if path.is_file())


def run_reviewed_seed_tracer(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
    executed_environment_digest: str,
    approval_comment_id: int,
    approval_text: str,
    hourly_cost_cny: Decimal | str,
    input_role_arn: str,
    repository_root: Path,
    scratch_root: Path,
) -> SeedTracerExecutionResult:
    """Validate exact bindings, execute once, and return one terminal result."""

    hourly_cost = _validated_hourly_cost(hourly_cost_cny)
    preparation = prepare_seed_tracer_execution(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=reviewed_git_commit,
        expected_git_tree=reviewed_git_tree,
    )
    _verify_runtime_source(repository_root, reviewed_git_commit, reviewed_git_tree)
    if _verified_runtime_environment_digest(repository_root) != executed_environment_digest:
        raise ContractValidationError("M5 tracer runtime environment differs from reviewed bytes")
    authorization = SeedTracerAuthorization(
        approval_comment_id=approval_comment_id,
        approval_text=approval_text,
        packet_sha256=packet_sha256,
        reviewed_git_commit=reviewed_git_commit,
        reviewed_git_tree=reviewed_git_tree,
        executed_environment_digest=executed_environment_digest,
        execution_region=PILOT_EXECUTION_REGION,
        hourly_cost_cny=hourly_cost,
    )
    input_client = Issue76M4ReadOnlyCosClient(input_role_arn)
    try:
        return execute_reviewed_seed_tracer_locally(
            preparation=preparation,
            packet_bytes=packet_bytes,
            packet_sha256=packet_sha256,
            expected_git_commit=reviewed_git_commit,
            expected_git_tree=reviewed_git_tree,
            repository_root=repository_root,
            input_client=input_client,
            scratch_root=scratch_root,
            authorization=authorization,
        )
    except Exception as exc:
        reason = (
            "resource-cap-reached"
            if isinstance(exc, ContractValidationError) and "resource cap reached" in str(exc)
            else "runtime-failure"
        )
        raise SeedTracerAttemptFailure(
            packet_sha256=packet_sha256,
            attempts_started=_started_attempt_count(scratch_root),
            failure_reason=reason,
        ) from exc


def seed_tracer_summary_bytes(result: SeedTracerExecutionResult) -> bytes:
    """Return a compact aggregate-only terminal record for stdout."""

    return canonical_json_bytes(
        {
            "active_execution_wall_seconds": result.active_execution_wall_seconds,
            "attempts_started": len(result.unique_run_ids_started),
            "comparisons": _comparison_value(result.verdict),
            "estimated_incremental_cost_cny": str(result.estimated_incremental_cost_cny),
            "network_transfer_bytes": result.network_transfer_bytes,
            "packet_sha256": result.packet_sha256,
            "peak_rss_bytes": result.peak_rss_bytes,
            "record_type": "m5-seed-tracer-launch-summary-v1",
            "scratch_high_water_bytes": result.scratch_high_water_bytes,
            "verdict": result.verdict.verdict,
        }
    )


def seed_tracer_failure_summary_bytes(failure: SeedTracerAttemptFailure) -> bytes:
    """Return the canonical non-secret terminal state for an incomplete batch."""

    return canonical_json_bytes(
        {
            "attempts_started": failure.attempts_started,
            "failure_reason": failure.failure_reason,
            "packet_sha256": failure.packet_sha256,
            "record_type": "m5-seed-tracer-launch-summary-v1",
            "verdict": "DATA-INCOMPLETE",
        }
    )


def run_seed_tracer_cli(arguments: Any) -> int:
    """Execute the exact SHA-bound tracer and print one aggregate-only terminal state."""

    import sys

    try:
        result = run_reviewed_seed_tracer(
            packet_bytes=arguments.packet.read_bytes(),
            packet_sha256=arguments.packet_sha256,
            reviewed_git_commit=arguments.reviewed_git_commit,
            reviewed_git_tree=arguments.reviewed_git_tree,
            executed_environment_digest=arguments.environment_digest,
            approval_comment_id=arguments.approval_comment_id,
            approval_text=arguments.approval,
            hourly_cost_cny=arguments.hourly_cost_cny,
            input_role_arn=arguments.input_role_arn,
            repository_root=arguments.repository_root,
            scratch_root=arguments.scratch_root,
        )
    except SeedTracerAttemptFailure as failure:
        sys.stdout.buffer.write(seed_tracer_failure_summary_bytes(failure))
        return 2
    sys.stdout.buffer.write(seed_tracer_summary_bytes(result))
    return 0


__all__ = [
    "ACCEPTED_HOURLY_COST_CNY",
    "SEED_TRACER_APPROVAL",
    "SeedTracerAttemptFailure",
    "SeedTracerAuthorization",
    "SeedTracerExecutionResult",
    "SeedTracerPreparation",
    "TracerRun",
    "execute_reviewed_seed_tracer_locally",
    "prepare_seed_tracer_execution",
    "run_reviewed_seed_tracer",
    "run_seed_tracer_cli",
    "seed_tracer_failure_summary_bytes",
    "seed_tracer_summary_bytes",
]
