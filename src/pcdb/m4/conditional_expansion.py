"""Bounded conditional expansion and Gate 3 reconciliation for Issue #76."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.contracts.run import RunSpec
from pcdb.m4.execution_adapter import (
    M4ExecutionPreparation,
    PreparedRun,
    prepare_execution_adapter,
)
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet
from pcdb.m4.screening import (
    PilotRunEvidence,
    _defined_metric_value,
    evaluate_pilot_screening,
    load_pilot_run_recall,
)
from pcdb.replay.engine import load_replay_trajectory
from pcdb.replay.publication import is_replay_attempt_eligible
from pcdb.storage.base import ArtifactStore
from pcdb.storage.local import LocalArtifactStore

_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TASKS = ("alpha-amylase", "ired")
_MODELS = (
    "ridge-sklearn-v1",
    "random-forest-sklearn-v1",
    "xgboost-cpu-v1",
)
_CONTROLS = (
    "random-seeded-sha256-v1",
    "greedy-predictive-mean-v1",
)
_INITIAL_SIZES = (32, 64)
_REGISTERED_SEEDS = tuple(range(1729, 1739))
_PILOT_SEEDS = (1729, 1730, 1731)
_DEFINITION_IDS = (
    "upper-tail-1pct",
    "upper-tail-0.5pct",
    "upper-tail-2pct",
)

SELECTED_EXPLORATIONS = (
    ("random-forest-sklearn-v1", "uncertainty-bootstrap-std-v1"),
    ("xgboost-cpu-v1", "ucb-bootstrap-std-beta1-v1"),
)


@dataclass(frozen=True, slots=True)
class PilotResourceUsage:
    """Already consumed resource ledger that the expansion cannot reset."""

    active_execution_wall_seconds: float
    attempts_started: int
    network_transfer_bytes: int
    scratch_high_water_bytes: int


PILOT_RESOURCE_USAGE = PilotResourceUsage(
    active_execution_wall_seconds=1581.3715364050004,
    attempts_started=72,
    network_transfer_bytes=32_716_485,
    scratch_high_water_bytes=15_083_519,
)


@dataclass(frozen=True, slots=True)
class ConditionalRunMatrix:
    """The exact Gate 3 matrix split into reusable pilot rows and missing runs."""

    candidate_runs: tuple[PreparedRun, ...]
    completed_overlap: tuple[PreparedRun, ...]
    missing_runs: tuple[PreparedRun, ...]
    excluded_pilot_run_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompletedPilotAttempt:
    """Immutable identity of one fresh-validated successful pilot attempt chain."""

    run_id: str
    attempt_key: str
    attempt_sha256: str
    definition_metric_key: str


@dataclass(frozen=True, slots=True)
class ConditionalRunMetrics:
    """Fresh-validated final-budget decision metrics for one candidate RunSpec."""

    task_id: str
    run_spec: RunSpec
    recalls: tuple[tuple[str, float], ...]
    successes: tuple[tuple[str, bool], ...]
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ConditionalRunEvidence:
    """Fresh-readable references for one completion-marked expansion attempt."""

    task_id: str
    run_spec: RunSpec
    store: ArtifactStore
    attempt_key: str
    attempt_sha256: str
    definition_metric_key: str


@dataclass(frozen=True, slots=True)
class Gate3CellDecision:
    """Frozen ten-seed decision for one task/model/exploration/initial cell."""

    task_id: str
    model_id: str
    acquisition_id: str
    initial_sample_size: int
    positive_primary_seeds: int
    primary_recall_mean_difference: float
    success_probability_difference: float
    sensitivity_mean_differences: tuple[tuple[str, float], ...]
    recall_pass: bool
    success_pass: bool
    task_heterogeneous: bool
    status: str


@dataclass(frozen=True, slots=True)
class Gate3SeedEvidence:
    """Auditable seed-level final metrics behind one Gate 3 matrix row."""

    task_id: str
    model_id: str
    acquisition_id: str
    initial_sample_size: int
    seed: int
    run_id: str
    recalls: tuple[tuple[str, float], ...]
    successes: tuple[tuple[str, bool], ...]
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class Gate3Decision:
    """The only terminal outcomes admitted by the M4 frozen protocol."""

    status: str
    cell_decisions: tuple[Gate3CellDecision, ...]
    seed_evidence: tuple[Gate3SeedEvidence, ...]
    frozen_run_ids: tuple[str, ...]
    failure_reason: str | None


def _load_pilot_runs(packet_bytes: bytes) -> tuple[PreparedRun, ...]:
    try:
        packet = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M4 pilot packet must be canonical JSON") from None
    raw_runs = packet.get("run_specs") if isinstance(packet, dict) else None
    if (
        not isinstance(packet, dict)
        or canonical_json_bytes(packet) != packet_bytes
        or packet.get("schema_version") != "m4-pilot-preflight-packet-v1"
        or not isinstance(raw_runs, list)
        or len(raw_runs) != 72
    ):
        raise ContractValidationError("M4 conditional expansion requires the exact pilot packet")
    runs: list[PreparedRun] = []
    for item in raw_runs:
        if not isinstance(item, dict) or set(item) != {"run_id", "run_spec", "task_id"}:
            raise ContractValidationError("M4 pilot RunSpec inventory drifted")
        raw_spec = item.get("run_spec")
        if not isinstance(raw_spec, dict):
            raise ContractValidationError("M4 pilot RunSpec inventory drifted")
        try:
            run_spec = RunSpec(**raw_spec)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("M4 pilot RunSpec inventory drifted") from exc
        if item.get("task_id") not in _TASKS or item.get("run_id") != run_spec.run_id:
            raise ContractValidationError("M4 pilot RunSpec inventory drifted")
        runs.append(PreparedRun(task_id=str(item["task_id"]), run_spec=run_spec))
    keys = {
        (
            item.task_id,
            item.run_spec.model_id,
            item.run_spec.acquisition_id,
            item.run_spec.initial_sample_size,
            item.run_spec.seed,
        )
        for item in runs
    }
    expected = {
        (task_id, model_id, acquisition_id, 32, seed)
        for task_id in _TASKS
        for model_id in _MODELS
        for acquisition_id in (*_CONTROLS, *(item[1] for item in SELECTED_EXPLORATIONS))
        for seed in _PILOT_SEEDS
    }
    if len(keys) != 72 or keys != expected:
        raise ContractValidationError("M4 pilot RunSpec inventory drifted")
    return tuple(runs)


def materialize_conditional_run_specs(
    pilot_packet_bytes: bytes,
    *,
    reviewed_git_commit: str,
    selected_explorations: Sequence[tuple[str, str]],
) -> ConditionalRunMatrix:
    """Mechanically produce 320 candidates, 48 reusable rows, and 272 missing rows."""

    if not _GIT_OBJECT.fullmatch(reviewed_git_commit):
        raise ContractValidationError("reviewed expansion Git commit must be lowercase 40-hex")
    selected = tuple(selected_explorations)
    if selected != SELECTED_EXPLORATIONS or len(set(selected)) != len(selected):
        raise ContractValidationError("M4 selected exploration set drifted")
    pilot_runs = _load_pilot_runs(pilot_packet_bytes)
    pilot_by_key = {
        (
            item.task_id,
            item.run_spec.model_id,
            item.run_spec.acquisition_id,
            item.run_spec.initial_sample_size,
            item.run_spec.seed,
        ): item
        for item in pilot_runs
    }
    candidate_runs: list[PreparedRun] = []
    overlap: list[PreparedRun] = []
    missing: list[PreparedRun] = []
    for task_id in _TASKS:
        for model_id in _MODELS:
            acquisitions = list(_CONTROLS)
            acquisitions.extend(
                acquisition_id
                for selected_model, acquisition_id in selected
                if selected_model == model_id
            )
            for acquisition_id in acquisitions:
                prototype = pilot_by_key[(task_id, model_id, acquisition_id, 32, 1729)]
                for initial_size in _INITIAL_SIZES:
                    for seed in _REGISTERED_SEEDS:
                        key = (task_id, model_id, acquisition_id, initial_size, seed)
                        if key in pilot_by_key:
                            prepared = pilot_by_key[key]
                            overlap.append(prepared)
                        else:
                            prepared = PreparedRun(
                                task_id=task_id,
                                run_spec=replace(
                                    prototype.run_spec,
                                    git_commit=reviewed_git_commit,
                                    initial_sample_size=initial_size,
                                    seed=seed,
                                ),
                            )
                            missing.append(prepared)
                        candidate_runs.append(prepared)
    candidate_ids = {item.run_spec.run_id for item in candidate_runs}
    excluded = tuple(
        item.run_spec.run_id for item in pilot_runs if item.run_spec.run_id not in candidate_ids
    )
    if (
        len(candidate_runs) != 320
        or len(candidate_ids) != 320
        or len(overlap) != 48
        or len(missing) != 272
        or len(excluded) != 24
        or candidate_ids
        != {item.run_spec.run_id for item in overlap} | {item.run_spec.run_id for item in missing}
    ):
        raise ContractValidationError("M4 conditional expansion matrix arithmetic drifted")
    return ConditionalRunMatrix(
        candidate_runs=tuple(candidate_runs),
        completed_overlap=tuple(overlap),
        missing_runs=tuple(missing),
        excluded_pilot_run_ids=excluded,
    )


def cumulative_resource_ledger(usage: PilotResourceUsage) -> dict[str, int | float]:
    """Validate and expose the non-resettable pilot resource starting point."""

    if usage != PILOT_RESOURCE_USAGE:
        raise ContractValidationError("M4 pilot resource usage differs from reviewed evidence")
    return {
        "active_execution_wall_seconds": usage.active_execution_wall_seconds,
        "attempts_started": usage.attempts_started,
        "network_transfer_bytes": usage.network_transfer_bytes,
        "scratch_high_water_bytes": usage.scratch_high_water_bytes,
    }


def _validated_completed_attempts(
    pilot_packet_bytes: bytes,
    values: Sequence[CompletedPilotAttempt],
) -> tuple[CompletedPilotAttempt, ...]:
    pilot_runs = _load_pilot_runs(pilot_packet_bytes)
    packet_sha256 = hashlib.sha256(pilot_packet_bytes).hexdigest()
    by_run_id = {item.run_id: item for item in values}
    if (
        len(values) != 72
        or len(by_run_id) != 72
        or set(by_run_id) != {item.run_spec.run_id for item in pilot_runs}
    ):
        raise ContractValidationError("M4 completed pilot attempt inventory must exact-cover 72")
    ordered: list[CompletedPilotAttempt] = []
    for prepared in pilot_runs:
        item = by_run_id[prepared.run_spec.run_id]
        base = f"m4/issue-76/pilot/{packet_sha256}/runs/{item.run_id}/attempts/attempt-001"
        if (
            item.attempt_key != f"{base}/run-attempt.json"
            or item.definition_metric_key != f"{base}/pilot-final-definition-metrics.json"
            or not _SHA256.fullmatch(item.attempt_sha256)
        ):
            raise ContractValidationError("M4 completed pilot attempt identity drifted")
        ordered.append(item)
    return tuple(ordered)


def build_conditional_expansion_packet(
    repository_root: Path,
    *,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
    pilot_packet_bytes: bytes,
    completed_pilot_attempts: Sequence[CompletedPilotAttempt],
) -> bytes:
    """Build a review-only 272-run packet after fresh pilot-chain reconciliation."""

    if not _GIT_OBJECT.fullmatch(reviewed_git_tree):
        raise ContractValidationError("reviewed expansion Git tree must be lowercase 40-hex")
    try:
        pilot_packet = json.loads(pilot_packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M4 pilot packet must be canonical JSON") from None
    pilot_source = pilot_packet.get("reviewed_source") if isinstance(pilot_packet, dict) else None
    if (
        not isinstance(pilot_source, dict)
        or not isinstance(pilot_source.get("git_commit"), str)
        or not isinstance(pilot_source.get("git_tree"), str)
        or build_pilot_preflight_packet(
            repository_root.resolve(),
            str(pilot_source["git_commit"]),
            str(pilot_source["git_tree"]),
        )
        != pilot_packet_bytes
    ):
        raise ContractValidationError("M4 prior pilot packet differs from reviewed repository")
    completed = _validated_completed_attempts(pilot_packet_bytes, completed_pilot_attempts)
    matrix = materialize_conditional_run_specs(
        pilot_packet_bytes,
        reviewed_git_commit=reviewed_git_commit,
        selected_explorations=SELECTED_EXPLORATIONS,
    )
    packet = {
        "authorization_boundaries": {
            "candidate_truth_read_authorized": False,
            "conditional_execution_authorized": False,
            "cos_write_list_delete_authorized": False,
            "held_out_read_authorized": False,
            "packet_review_authorizes_execution": False,
        },
        "candidate_run_specs": [
            {
                "run_id": item.run_spec.run_id,
                "run_spec": asdict(item.run_spec),
                "task_id": item.task_id,
            }
            for item in matrix.candidate_runs
        ],
        "completed_overlap_run_ids": [item.run_spec.run_id for item in matrix.completed_overlap],
        "completed_pilot_attempts": [asdict(item) for item in completed],
        "excluded_pilot_run_ids": list(matrix.excluded_pilot_run_ids),
        "initial_resource_ledger": cumulative_resource_ledger(PILOT_RESOURCE_USAGE),
        "issue_number": 76,
        "matrix": {
            "candidate_run_specs": 320,
            "completed_overlap": 48,
            "excluded_pilot_run_specs": 24,
            "missing_run_specs": 272,
            "pilot_completed_run_specs": 72,
            "pilot_plus_expansion_attempts": 344,
        },
        "missing_run_specs": [
            {
                "run_id": item.run_spec.run_id,
                "run_spec": asdict(item.run_spec),
                "task_id": item.task_id,
            }
            for item in matrix.missing_runs
        ],
        "prior_pilot": {
            "packet_sha256": hashlib.sha256(pilot_packet_bytes).hexdigest(),
            "packet_size_bytes": len(pilot_packet_bytes),
            "reviewed_source": pilot_source,
            "selected_explorations": [list(item) for item in SELECTED_EXPLORATIONS],
        },
        "resource_caps": {
            "active_execution_wall_seconds": 21_600,
            "aggregate_rss_bytes": 16 * 1024**3,
            "automatic_retry": 0,
            "delete": 0,
            "network_transfer_bytes": 2 * 1024**3,
            "overwrite": 0,
            "pilot_plus_expansion_attempts": 344,
            "replacement_attempts": 0,
            "scratch_high_water_bytes": 20 * 1024**3,
            "vcpu": 8,
        },
        "reviewed_source": {
            "git_commit": reviewed_git_commit,
            "git_tree": reviewed_git_tree,
        },
        "schema_version": "m4-conditional-expansion-packet-v1",
    }
    return canonical_json_bytes(packet)


def load_conditional_expansion_packet(
    packet_bytes: bytes,
    *,
    expected_sha256: str,
    repository_root: Path,
    expected_git_commit: str,
    expected_git_tree: str,
    pilot_packet_bytes: bytes,
    completed_pilot_attempts: Sequence[CompletedPilotAttempt],
) -> dict[str, Any]:
    """Strict-load and reproduce one review-only conditional expansion packet."""

    digest = hashlib.sha256(packet_bytes).hexdigest()
    if not _SHA256.fullmatch(expected_sha256) or digest != expected_sha256:
        raise ContractValidationError("M4 conditional expansion packet identity differs")
    try:
        value = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "M4 conditional expansion packet must be canonical JSON"
        ) from None
    if not isinstance(value, dict) or canonical_json_bytes(value) != packet_bytes:
        raise ContractValidationError("M4 conditional expansion packet must be canonical JSON")
    expected = build_conditional_expansion_packet(
        repository_root,
        reviewed_git_commit=expected_git_commit,
        reviewed_git_tree=expected_git_tree,
        pilot_packet_bytes=pilot_packet_bytes,
        completed_pilot_attempts=completed_pilot_attempts,
    )
    if expected != packet_bytes:
        raise ContractValidationError(
            "M4 conditional expansion packet differs from reviewed inputs"
        )
    return cast(dict[str, Any], value)


def reconcile_completed_pilot_attempts(
    *,
    repository_root: Path,
    pilot_packet_bytes: bytes,
    pilot_packet_sha256: str,
    pilot_scratch_root: Path,
) -> tuple[CompletedPilotAttempt, ...]:
    """Fresh-load all pilot chains and bind only the accepted EXPAND selection."""

    if (
        not _SHA256.fullmatch(pilot_packet_sha256)
        or hashlib.sha256(pilot_packet_bytes).hexdigest() != pilot_packet_sha256
    ):
        raise ContractValidationError("M4 prior pilot packet identity differs")
    try:
        packet = json.loads(pilot_packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M4 prior pilot packet must be canonical JSON") from None
    source = packet.get("reviewed_source") if isinstance(packet, dict) else None
    if not isinstance(source, dict):
        raise ContractValidationError("M4 prior pilot source binding is absent")
    commit = source.get("git_commit")
    tree = source.get("git_tree")
    if not isinstance(commit, str) or not isinstance(tree, str):
        raise ContractValidationError("M4 prior pilot source binding is absent")
    preparation = prepare_execution_adapter(
        packet_bytes=pilot_packet_bytes,
        packet_sha256=pilot_packet_sha256,
        repository_root=repository_root,
        expected_git_commit=commit,
        expected_git_tree=tree,
    )
    store = LocalArtifactStore(pilot_scratch_root.resolve() / "objects")
    completed: list[CompletedPilotAttempt] = []
    evidence: list[PilotRunEvidence] = []
    for prepared in preparation.runs:
        base = (
            f"m4/issue-76/pilot/{pilot_packet_sha256}/runs/"
            f"{prepared.run_spec.run_id}/attempts/attempt-001"
        )
        attempt_key = f"{base}/run-attempt.json"
        definition_key = f"{base}/pilot-final-definition-metrics.json"
        try:
            attempt_sha256 = hashlib.sha256(store.get_bytes(attempt_key)).hexdigest()
        except (KeyError, OSError, TypeError, ValueError):
            raise ContractValidationError(
                "M4 completed pilot attempt chain is unavailable"
            ) from None
        completed_item = CompletedPilotAttempt(
            run_id=prepared.run_spec.run_id,
            attempt_key=attempt_key,
            attempt_sha256=attempt_sha256,
            definition_metric_key=definition_key,
        )
        completed.append(completed_item)
        evidence.append(
            PilotRunEvidence(
                task_id=prepared.task_id,
                run_spec=prepared.run_spec,
                store=store,
                attempt_key=attempt_key,
                attempt_sha256=attempt_sha256,
                definition_metric_key=definition_key,
            )
        )
    decision = evaluate_pilot_screening(
        evidence,
        expected_runs=tuple((item.task_id, item.run_spec) for item in preparation.runs),
        expected_packet_sha256=pilot_packet_sha256,
        expected_execution_region="ap-chengdu",
    )
    if (
        decision.status != "EXPAND"
        or decision.selected_explorations != SELECTED_EXPLORATIONS
        or decision.failure_reason is not None
    ):
        raise ContractValidationError("M4 completed pilot chains do not reproduce selection")
    return _validated_completed_attempts(pilot_packet_bytes, completed)


def prepare_conditional_expansion(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    repository_root: Path,
    expected_git_commit: str,
    expected_git_tree: str,
    pilot_packet_bytes: bytes,
    completed_pilot_attempts: Sequence[CompletedPilotAttempt],
) -> M4ExecutionPreparation:
    """Bind the exact missing inventory without granting reads, attempts, or writes."""

    packet = load_conditional_expansion_packet(
        packet_bytes,
        expected_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=expected_git_commit,
        expected_git_tree=expected_git_tree,
        pilot_packet_bytes=pilot_packet_bytes,
        completed_pilot_attempts=completed_pilot_attempts,
    )
    pilot_source = json.loads(pilot_packet_bytes)["reviewed_source"]
    pilot_preparation = prepare_execution_adapter(
        packet_bytes=pilot_packet_bytes,
        packet_sha256=hashlib.sha256(pilot_packet_bytes).hexdigest(),
        repository_root=repository_root,
        expected_git_commit=str(pilot_source["git_commit"]),
        expected_git_tree=str(pilot_source["git_tree"]),
    )
    raw_runs = packet.get("missing_run_specs")
    if not isinstance(raw_runs, list) or len(raw_runs) != 272:
        raise ContractValidationError("M4 expansion preparation must exact-cover 272 missing runs")
    runs: list[PreparedRun] = []
    for item in raw_runs:
        if not isinstance(item, dict) or set(item) != {"run_id", "run_spec", "task_id"}:
            raise ContractValidationError("M4 expansion missing RunSpec member drifted")
        raw_spec = item.get("run_spec")
        if not isinstance(raw_spec, dict) or item.get("task_id") not in _TASKS:
            raise ContractValidationError("M4 expansion missing RunSpec member drifted")
        try:
            run_spec = RunSpec(**raw_spec)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("M4 expansion missing RunSpec member drifted") from exc
        if item.get("run_id") != run_spec.run_id or run_spec.git_commit != expected_git_commit:
            raise ContractValidationError("M4 expansion missing RunSpec identity drifted")
        runs.append(PreparedRun(task_id=str(item["task_id"]), run_spec=run_spec))
    if len({item.run_spec.run_id for item in runs}) != 272:
        raise ContractValidationError("M4 expansion missing RunSpecs are not unique")
    return M4ExecutionPreparation(
        packet_sha256=packet_sha256,
        reviewed_git_commit=expected_git_commit,
        reviewed_git_tree=expected_git_tree,
        metadata_paths=pilot_preparation.metadata_paths,
        inventory_size_bytes=pilot_preparation.inventory_size_bytes,
        inventory_sha256=pilot_preparation.inventory_sha256,
        tasks=pilot_preparation.tasks,
        runs=tuple(runs),
    )


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, start=Decimal(0)) / Decimal(len(values))


def _invalid_gate_3(reason: str) -> Gate3Decision:
    return Gate3Decision(
        status="INCONCLUSIVE",
        cell_decisions=(),
        seed_evidence=(),
        frozen_run_ids=(),
        failure_reason=reason,
    )


def evaluate_gate_3(metrics: Sequence[ConditionalRunMetrics]) -> Gate3Decision:
    """Apply the frozen ten-seed task-first rule after an exact 320-chain cover."""

    if len(metrics) != 320 or any(not isinstance(item, ConditionalRunMetrics) for item in metrics):
        return _invalid_gate_3("Gate 3 evidence does not exact-cover 320 RunSpecs")
    by_key: dict[tuple[str, str, str, int, int], ConditionalRunMetrics] = {}
    for item in metrics:
        key = (
            item.task_id,
            item.run_spec.model_id,
            item.run_spec.acquisition_id,
            item.run_spec.initial_sample_size,
            item.run_spec.seed,
        )
        if (
            item.task_id not in _TASKS
            or item.run_spec.initial_sample_size not in _INITIAL_SIZES
            or item.run_spec.seed not in _REGISTERED_SEEDS
            or tuple(name for name, _ in item.recalls) != _DEFINITION_IDS
            or tuple(name for name, _ in item.successes) != ("upper-tail-1pct",)
            or not _SHA256.fullmatch(item.evidence_sha256)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 <= float(value) <= 1
                for _, value in item.recalls
            )
            or key in by_key
        ):
            return _invalid_gate_3("Gate 3 evidence identity or metric value drifted")
        by_key[key] = item
    expected = {
        (task_id, model_id, acquisition_id, initial_size, seed)
        for task_id in _TASKS
        for model_id in _MODELS
        for acquisition_id in (
            *_CONTROLS,
            *(acquisition for model, acquisition in SELECTED_EXPLORATIONS if model == model_id),
        )
        for initial_size in _INITIAL_SIZES
        for seed in _REGISTERED_SEEDS
    }
    if set(by_key) != expected or len({item.run_spec.run_id for item in metrics}) != 320:
        return _invalid_gate_3("Gate 3 evidence does not exact-cover 320 RunSpecs")

    provisional: list[Gate3CellDecision] = []
    for task_id in _TASKS:
        for model_id, acquisition_id in SELECTED_EXPLORATIONS:
            for initial_size in _INITIAL_SIZES:
                recall_differences: dict[str, list[Decimal]] = {
                    definition_id: [] for definition_id in _DEFINITION_IDS
                }
                exploration_successes: list[Decimal] = []
                greedy_successes: list[Decimal] = []
                for seed in _REGISTERED_SEEDS:
                    exploration = by_key[(task_id, model_id, acquisition_id, initial_size, seed)]
                    greedy = by_key[
                        (
                            task_id,
                            model_id,
                            "greedy-predictive-mean-v1",
                            initial_size,
                            seed,
                        )
                    ]
                    exploration_recalls = dict(exploration.recalls)
                    greedy_recalls = dict(greedy.recalls)
                    for definition_id in _DEFINITION_IDS:
                        recall_differences[definition_id].append(
                            Decimal(str(exploration_recalls[definition_id]))
                            - Decimal(str(greedy_recalls[definition_id]))
                        )
                    exploration_successes.append(
                        Decimal(int(dict(exploration.successes)["upper-tail-1pct"]))
                    )
                    greedy_successes.append(Decimal(int(dict(greedy.successes)["upper-tail-1pct"])))
                primary = recall_differences["upper-tail-1pct"]
                primary_mean = _mean(primary)
                success_difference = _mean(exploration_successes) - _mean(greedy_successes)
                sensitivity = tuple(
                    (definition_id, _mean(recall_differences[definition_id]))
                    for definition_id in _DEFINITION_IDS[1:]
                )
                recall_pass = sum(value > 0 for value in primary) >= 7 and primary_mean >= Decimal(
                    "0.01"
                )
                success_pass = success_difference >= Decimal("0.10")
                sensitivity_reversal = any(value < 0 for _, value in sensitivity)
                other_primary_nonnegative = (not recall_pass or success_difference >= 0) and (
                    not success_pass or primary_mean >= 0
                )
                if sensitivity_reversal:
                    status = "fragile-inconclusive"
                elif (recall_pass or success_pass) and other_primary_nonnegative:
                    status = "retained"
                else:
                    status = "eliminated"
                provisional.append(
                    Gate3CellDecision(
                        task_id=task_id,
                        model_id=model_id,
                        acquisition_id=acquisition_id,
                        initial_sample_size=initial_size,
                        positive_primary_seeds=sum(value > 0 for value in primary),
                        primary_recall_mean_difference=float(primary_mean),
                        success_probability_difference=float(success_difference),
                        sensitivity_mean_differences=tuple(
                            (definition_id, float(value)) for definition_id, value in sensitivity
                        ),
                        recall_pass=recall_pass,
                        success_pass=success_pass,
                        task_heterogeneous=False,
                        status=status,
                    )
                )
    decisions = tuple(
        replace(
            item,
            task_heterogeneous=len(
                {
                    other.status
                    for other in provisional
                    if other.model_id == item.model_id
                    and other.acquisition_id == item.acquisition_id
                    and other.initial_sample_size == item.initial_sample_size
                }
            )
            > 1,
        )
        for item in provisional
    )
    retained = tuple(item for item in decisions if item.status == "retained")
    frozen_keys = {
        (item.task_id, item.model_id, acquisition_id, item.initial_sample_size, seed)
        for item in retained
        for acquisition_id in (*_CONTROLS, item.acquisition_id)
        for seed in _REGISTERED_SEEDS
    }
    frozen_run_ids = tuple(
        by_key[key].run_spec.run_id for key in sorted(frozen_keys) if key in by_key
    )
    seed_evidence = tuple(
        Gate3SeedEvidence(
            task_id=item.task_id,
            model_id=item.run_spec.model_id,
            acquisition_id=item.run_spec.acquisition_id,
            initial_sample_size=item.run_spec.initial_sample_size,
            seed=item.run_spec.seed,
            run_id=item.run_spec.run_id,
            recalls=item.recalls,
            successes=item.successes,
            evidence_sha256=item.evidence_sha256,
        )
        for item in sorted(
            metrics,
            key=lambda value: (
                value.task_id,
                value.run_spec.model_id,
                value.run_spec.acquisition_id,
                value.run_spec.initial_sample_size,
                value.run_spec.seed,
            ),
        )
    )
    return Gate3Decision(
        status="FROZEN" if retained else "STOP",
        cell_decisions=decisions,
        seed_evidence=seed_evidence,
        frozen_run_ids=frozen_run_ids,
        failure_reason=None,
    )


def _decision_metrics_from_payload(
    payload: bytes,
    *,
    expected_record_type: str,
    expected_packet_sha256: str,
    expected_task_id: str,
    run_spec: RunSpec,
    expected_trajectory_sha256: str,
    evidence_sha256: str,
) -> ConditionalRunMetrics:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M4 final metric record must be canonical JSON") from None
    if (
        not isinstance(value, dict)
        or canonical_json_bytes(value) != payload
        or set(value)
        != {
            "attempt_id",
            "budget",
            "definitions",
            "packet_sha256",
            "record_type",
            "run_id",
            "task_id",
            "trajectory_sha256",
        }
        or value.get("record_type") != expected_record_type
        or value.get("attempt_id") != "attempt-001"
        or value.get("packet_sha256") != expected_packet_sha256
        or value.get("run_id") != run_spec.run_id
        or value.get("task_id") != expected_task_id
        or value.get("budget") != run_spec.budget
        or value.get("trajectory_sha256") != expected_trajectory_sha256
    ):
        raise ContractValidationError("M4 final metric record identity drifted")
    definitions = value.get("definitions")
    if (
        not isinstance(definitions, list)
        or len(definitions) != 3
        or tuple(item.get("definition_id") for item in definitions if isinstance(item, dict))
        != _DEFINITION_IDS
    ):
        raise ContractValidationError("M4 final definition metric set drifted")
    recalls: list[tuple[str, float]] = []
    primary_success: bool | None = None
    for item in definitions:
        if not isinstance(item, dict) or set(item) != {"definition_id", "metrics"}:
            raise ContractValidationError("M4 final definition metric member drifted")
        metrics = item.get("metrics")
        if not isinstance(metrics, dict) or set(metrics) != {
            "best_found_value",
            "high_value_discovery_recall",
            "simple_regret",
            "success_at_budget",
        }:
            raise ContractValidationError("M4 final sequential metric set drifted")
        for metric_id, metric_value in metrics.items():
            _defined_metric_value(metric_value, metric_id=metric_id)
        recall = _defined_metric_value(
            metrics["high_value_discovery_recall"],
            metric_id="high_value_discovery_recall",
        )
        success = _defined_metric_value(
            metrics["success_at_budget"],
            metric_id="success_at_budget",
        )
        if not isinstance(recall, float) or not isinstance(success, bool):
            raise ContractValidationError("M4 final decision metric type drifted")
        recalls.append((str(item["definition_id"]), recall))
        if item["definition_id"] == "upper-tail-1pct":
            primary_success = success
    if primary_success is None:
        raise ContractValidationError("M4 primary success metric is absent")
    return ConditionalRunMetrics(
        task_id=expected_task_id,
        run_spec=run_spec,
        recalls=tuple(recalls),
        successes=(("upper-tail-1pct", primary_success),),
        evidence_sha256=evidence_sha256,
    )


def load_pilot_conditional_metrics(
    evidence: PilotRunEvidence,
    *,
    expected_packet_sha256: str,
    expected_execution_region: str,
) -> ConditionalRunMetrics:
    """Fresh-validate one reusable pilot chain and retain success_at_budget."""

    recall = load_pilot_run_recall(
        evidence,
        expected_packet_sha256=expected_packet_sha256,
        expected_execution_region=expected_execution_region,
    )
    return _decision_metrics_from_payload(
        evidence.store.get_bytes(evidence.definition_metric_key),
        expected_record_type="m4-pilot-final-definition-metrics-v1",
        expected_packet_sha256=expected_packet_sha256,
        expected_task_id=evidence.task_id,
        run_spec=evidence.run_spec,
        expected_trajectory_sha256=recall.trajectory_sha256,
        evidence_sha256=evidence.attempt_sha256,
    )


def load_conditional_run_metrics(
    evidence: ConditionalRunEvidence,
    *,
    expected_packet_sha256: str,
    expected_execution_region: str,
) -> ConditionalRunMetrics:
    """Fresh-validate one expansion attempt/output/trajectory/marker chain."""

    base = (
        f"m4/issue-76/conditional-expansion/{expected_packet_sha256}/runs/"
        f"{evidence.run_spec.run_id}/attempts/attempt-001"
    )
    if (
        expected_execution_region != "ap-chengdu"
        or not _SHA256.fullmatch(expected_packet_sha256)
        or evidence.attempt_key != f"{base}/run-attempt.json"
        or evidence.definition_metric_key != f"{base}/conditional-final-definition-metrics.json"
        or not _SHA256.fullmatch(evidence.attempt_sha256)
    ):
        raise ContractValidationError("M4 conditional evidence identity drifted")
    try:
        attempt = load_run_attempt_v1(
            evidence.store.get_bytes(evidence.attempt_key),
            expected_sha256=evidence.attempt_sha256,
        )
    except (ContractValidationError, KeyError, OSError, TypeError, ValueError):
        raise ContractValidationError("M4 conditional RunAttempt is unavailable") from None
    compute = attempt.value["compute"]
    if (
        attempt.value["status"] != "succeeded"
        or attempt.value["attempt_id"] != "attempt-001"
        or attempt.value["run_id"] != evidence.run_spec.run_id
        or attempt.value["executed_git_commit"] != evidence.run_spec.git_commit
        or attempt.value["executed_environment_digest"] != evidence.run_spec.environment_digest
        or compute.get("executor_type") != "compute_server"
        or compute.get("region") != expected_execution_region
        or attempt.value["completion_marker"]["object_key"] != f"{base}/_SUCCESS"
        or not is_replay_attempt_eligible(
            evidence.store,
            evidence.attempt_key,
            additional_output_keys=(evidence.definition_metric_key,),
        )
    ):
        raise ContractValidationError("M4 conditional attempt is not completion-marked eligible")
    trajectory_outputs = [
        item
        for item in attempt.value["outputs"]
        if str(item["object_key"]).endswith("/trajectory.json")
    ]
    definition_outputs = [
        item
        for item in attempt.value["outputs"]
        if item["object_key"] == evidence.definition_metric_key
    ]
    if len(trajectory_outputs) != 1 or len(definition_outputs) != 1:
        raise ContractValidationError("M4 conditional attempt output cross-link drifted")
    trajectory = load_replay_trajectory(
        evidence.store.get_bytes(str(trajectory_outputs[0]["object_key"]))
    )
    if (
        trajectory.run_spec != evidence.run_spec
        or trajectory.task_id != evidence.task_id
        or not trajectory.complete
    ):
        raise ContractValidationError("M4 conditional trajectory identity drifted")
    return _decision_metrics_from_payload(
        evidence.store.get_bytes(evidence.definition_metric_key),
        expected_record_type="m4-conditional-final-definition-metrics-v1",
        expected_packet_sha256=expected_packet_sha256,
        expected_task_id=evidence.task_id,
        run_spec=evidence.run_spec,
        expected_trajectory_sha256=trajectory.sha256,
        evidence_sha256=evidence.attempt_sha256,
    )


def reconcile_gate_3(
    pilot_evidence: Sequence[PilotRunEvidence],
    expansion_evidence: Sequence[ConditionalRunEvidence],
    *,
    expected_pilot_packet_sha256: str,
    expected_expansion_packet_sha256: str,
    expected_execution_region: str,
) -> Gate3Decision:
    """Fresh-load 48 reused and 272 new chains before any Gate 3 decision."""

    if len(pilot_evidence) != 48 or len(expansion_evidence) != 272:
        return _invalid_gate_3("Gate 3 fresh evidence requires 48 pilot and 272 expansion chains")
    try:
        metrics = tuple(
            load_pilot_conditional_metrics(
                item,
                expected_packet_sha256=expected_pilot_packet_sha256,
                expected_execution_region=expected_execution_region,
            )
            for item in pilot_evidence
        ) + tuple(
            load_conditional_run_metrics(
                item,
                expected_packet_sha256=expected_expansion_packet_sha256,
                expected_execution_region=expected_execution_region,
            )
            for item in expansion_evidence
        )
    except (ContractValidationError, KeyError, OSError, TypeError, ValueError):
        return _invalid_gate_3("Gate 3 attempt chain is incomplete, failed, or drifted")
    return evaluate_gate_3(metrics)


__all__ = [
    "PILOT_RESOURCE_USAGE",
    "SELECTED_EXPLORATIONS",
    "CompletedPilotAttempt",
    "ConditionalRunEvidence",
    "ConditionalRunMatrix",
    "ConditionalRunMetrics",
    "Gate3CellDecision",
    "Gate3Decision",
    "Gate3SeedEvidence",
    "PilotResourceUsage",
    "build_conditional_expansion_packet",
    "cumulative_resource_ledger",
    "evaluate_gate_3",
    "load_conditional_expansion_packet",
    "load_conditional_run_metrics",
    "load_pilot_conditional_metrics",
    "materialize_conditional_run_specs",
    "prepare_conditional_expansion",
    "reconcile_completed_pilot_attempts",
    "reconcile_gate_3",
]
