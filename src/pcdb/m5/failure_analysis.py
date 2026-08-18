"""Deterministic, read-only M4 Gate 3 failure attribution for Issue #83.

This module consumes only the accepted M2 review packet, the accepted M4
terminal, and strict-loaded M4 trajectories.  It never runs a model, calls an
oracle, writes an artifact store, or exposes candidate identifiers in output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Literal, cast

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.m4.conditional_expansion import (
    SELECTED_EXPLORATIONS,
    ConditionalRunMetrics,
    Gate3Decision,
    evaluate_gate_3,
)
from pcdb.replay.engine import ReplayTrajectory, load_replay_trajectory
from pcdb.replay.publication import is_replay_attempt_eligible
from pcdb.storage.local import LocalArtifactStore

Verdict = Literal["SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE"]

TASKS = ("alpha-amylase", "ired")
MODELS = (
    "ridge-sklearn-v1",
    "random-forest-sklearn-v1",
    "xgboost-cpu-v1",
)
INITIAL_SIZES = (32, 64)
SEEDS = tuple(range(1729, 1739))
PILOT_SEEDS = (1729, 1730, 1731)
REMAINING_SEEDS = tuple(range(1732, 1739))
CHECKPOINTS = (64, 80, 96, 112, 128)
DEFINITION_IDS = (
    "upper-tail-1pct",
    "upper-tail-0.5pct",
    "upper-tail-2pct",
)
GREEDY = "greedy-predictive-mean-v1"
RANDOM = "random-seeded-sha256-v1"

TERMINAL_SIZE = 236_250
TERMINAL_SHA256 = "06688b7ae505e1900cec2b04bc69c5a132ef93191c927ee42d70de36e3d7227f"
EXPANSION_PACKET_SHA256 = "5885bc25fd4beb2d73284a4271c3ae28e7684984fe968c457cd523ab1781e5ee"
PILOT_PACKET_SHA256 = "2c6bcdbe3ff73eb0ac241fd3b25738af467a355be9c8d12e8bcf8c28aab74b12"
M2_PATH = "docs/M2_GATE_B_REVIEW_PACKET.md"
M2_COMMIT = "fff8ae7047b88c9f562ed5e1dfbc6aa81bcab8a1"
M2_BLOB = "9271b3efbbd92220e5d699536c660493d05fbfee"
M2_SIZE = 16_906
M2_SHA256 = "e4d977c261e23d39cd79108491e7dc2c3469a79c2e76b8c0cd44a05c86663514"

# The accepted primary definitions contain no boundary ties in these two tasks.
# These values are the accepted actual counts, not inferred from this analysis.
PRIMARY_HIGH_VALUE_COUNTS = {"alpha-amylase": 23, "ired": 21}
T_CRITICAL = {
    3: Decimal("4.302652729911275"),
    7: Decimal("2.4469118487916806"),
    10: Decimal("2.2621571627409915"),
}


class AnalysisIncomplete(Exception):
    """A hypothesis cannot be evaluated without shrinking its frozen universe."""


@dataclass(frozen=True, slots=True)
class PairStats:
    n: int
    mean: Decimal
    median: Decimal
    sample_sd: Decimal
    minimum: Decimal
    maximum: Decimal
    positive: int
    zero: int
    negative: int
    ci_lower: Decimal
    ci_upper: Decimal
    loo_sign_change: bool


@dataclass(frozen=True, slots=True)
class StaticRow:
    task_id: str
    model_id: str
    spearman: Decimal
    recall_05: Decimal
    recall_1: Decimal
    recall_2: Decimal


@dataclass(frozen=True, slots=True)
class TerminalSeed:
    task_id: str
    model_id: str
    acquisition_id: str
    initial_sample_size: int
    seed: int
    run_id: str
    recalls: tuple[tuple[str, Decimal], ...]
    successes: tuple[tuple[str, bool], ...]
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    terminal: TerminalSeed
    trajectory: ReplayTrajectory
    checkpoints: tuple[tuple[int, tuple[tuple[str, Decimal], ...]], ...]
    post_initial_by_checkpoint: tuple[tuple[int, frozenset[str]], ...]

    def checkpoint_metric(self, checkpoint: int, metric_id: str) -> Decimal:
        by_checkpoint = dict(self.checkpoints)
        if checkpoint not in by_checkpoint or metric_id not in dict(by_checkpoint[checkpoint]):
            raise AnalysisIncomplete(
                f"required checkpoint metric is absent: {checkpoint}/{metric_id}"
            )
        return dict(by_checkpoint[checkpoint])[metric_id]

    def final_recall(self, definition_id: str) -> Decimal:
        recalls = dict(self.terminal.recalls)
        if definition_id not in recalls:
            raise AnalysisIncomplete(f"required final recall is absent: {definition_id}")
        return recalls[definition_id]


@dataclass(frozen=True, slots=True)
class HypothesisResult:
    hypothesis_id: str
    verdict: Verdict
    reason: str
    details: object


@dataclass(frozen=True, slots=True)
class FailureAnalysisReport:
    input_identity: Mapping[str, object]
    cause_matrix: tuple[HypothesisResult, ...]
    gate_blocker_map: tuple[Mapping[str, object], ...]
    unidentifiable: tuple[str, ...]
    limitations: tuple[str, ...]

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(_jsonable(asdict(self)))


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def _strict_json(payload: bytes, field: str) -> tuple[dict[str, Any], dict[str, Any]]:
    def reject_constant(_value: str) -> None:
        raise ContractValidationError(f"{field} contains a non-finite constant")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ContractValidationError(f"{field} contains a duplicate key")
            value[key] = item
        return value

    try:
        normal = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
        decimal_value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_float=Decimal,
            parse_constant=reject_constant,
        )
    except ContractValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError(f"{field} must be strict UTF-8 JSON") from exc
    if (
        not isinstance(normal, dict)
        or not isinstance(decimal_value, dict)
        or canonical_json_bytes(normal) != payload
    ):
        raise ContractValidationError(f"{field} must be one canonical JSON object")
    return cast(dict[str, Any], normal), cast(dict[str, Any], decimal_value)


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ContractValidationError(f"{field} must be a finite decimal number")
    result = Decimal(value)
    if not result.is_finite():
        raise ContractValidationError(f"{field} must be a finite decimal number")
    return result


def _sign(value: Decimal) -> int:
    return -1 if value < 0 else 1 if value > 0 else 0


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise AnalysisIncomplete("empty values are outside the frozen denominator")
    return sum(values, start=Decimal(0)) / Decimal(len(values))


def _pair_stats(values: Sequence[Decimal]) -> PairStats:
    if len(values) not in T_CRITICAL:
        raise AnalysisIncomplete("paired statistics require exactly n=3, n=7, or n=10")
    with localcontext() as context:
        context.prec = 50
        mean_value = _mean(values)
        if len(set(values)) == 1:
            sample_sd = Decimal(0)
        else:
            variance = sum(
                ((value - mean_value) ** 2 for value in values), start=Decimal(0)
            ) / Decimal(len(values) - 1)
            sample_sd = variance.sqrt()
        radius = T_CRITICAL[len(values)] * sample_sd / Decimal(len(values)).sqrt()
        positive = sum(value > 0 for value in values)
        negative = sum(value < 0 for value in values)
        zero = len(values) - positive - negative
        loo_change = False
        if len(values) == 10:
            full_sign = _sign(mean_value)
            loo_change = any(
                _sign(_mean((*values[:index], *values[index + 1 :]))) != full_sign
                for index in range(10)
            )
        ordered = sorted(values)
        middle = len(values) // 2
        median_value = (
            ordered[middle]
            if len(values) % 2
            else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
        )
        return PairStats(
            n=len(values),
            mean=mean_value,
            median=median_value,
            sample_sd=sample_sd,
            minimum=min(values),
            maximum=max(values),
            positive=positive,
            zero=zero,
            negative=negative,
            ci_lower=mean_value - radius,
            ci_upper=mean_value + radius,
            loo_sign_change=loo_change,
        )


def _git_blob_sha1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


_M2_ROW = re.compile(
    rb"^\| (alpha-amylase|ired) \| "
    rb"(ridge-sklearn-v1|random-forest-sklearn-v1|xgboost-cpu-v1) \| "
    rb"([0-9]+\.[0-9]+) \| ([0-9]+\.[0-9]+) \| ([0-9]+\.[0-9]+) \| "
    rb"([0-9]+\.[0-9]+) \|$"
)


def load_m2_static_rows(
    payload: bytes,
    *,
    repository_path: str = M2_PATH,
    git_commit: str = M2_COMMIT,
) -> tuple[StaticRow, ...]:
    """Load only the exact six-row accepted M2 table, with no fallback."""

    if (
        repository_path != M2_PATH
        or git_commit != M2_COMMIT
        or len(payload) != M2_SIZE
        or hashlib.sha256(payload).hexdigest() != M2_SHA256
        or _git_blob_sha1(payload) != M2_BLOB
    ):
        raise ContractValidationError("M2 static input identity drifted")
    rows: list[StaticRow] = []
    for line in payload.splitlines():
        match = _M2_ROW.fullmatch(line)
        if match is None:
            continue
        task, model, spearman, recall_05, recall_1, recall_2 = match.groups()
        rows.append(
            StaticRow(
                task_id=task.decode(),
                model_id=model.decode(),
                spearman=Decimal(spearman.decode()),
                recall_05=Decimal(recall_05.decode()),
                recall_1=Decimal(recall_1.decode()),
                recall_2=Decimal(recall_2.decode()),
            )
        )
    expected = {(task, model) for task in TASKS for model in MODELS}
    if len(rows) != 6 or {(row.task_id, row.model_id) for row in rows} != expected:
        raise ContractValidationError("M2 static table does not exact-cover 2 tasks x 3 models")
    return tuple(rows)


def load_terminal_seed_evidence(
    payload: bytes,
    *,
    expected_size: int = TERMINAL_SIZE,
    expected_sha256: str = TERMINAL_SHA256,
) -> tuple[dict[str, Any], tuple[TerminalSeed, ...]]:
    """Strict-load the accepted terminal and retain Decimal metric text."""

    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ContractValidationError("M4 terminal identity drifted")
    normal, decimal_value = _strict_json(payload, "M4 terminal")
    required = {
        "cumulative_active_execution_wall_seconds",
        "cumulative_attempts_started",
        "cumulative_network_transfer_bytes",
        "cumulative_scratch_high_water_bytes",
        "failure_reason",
        "frozen_run_ids",
        "gate_3_cell_decisions",
        "gate_3_seed_evidence",
        "gate_3_status",
        "new_attempts_started",
        "packet_sha256",
        "peak_rss_bytes",
        "record_type",
    }
    raw_seeds = decimal_value.get("gate_3_seed_evidence")
    if (
        set(normal) != required
        or normal.get("record_type") != "m4-conditional-launch-summary-v1"
        or normal.get("packet_sha256") != EXPANSION_PACKET_SHA256
        or normal.get("gate_3_status") != "STOP"
        or normal.get("failure_reason") is not None
        or normal.get("frozen_run_ids") != []
        or normal.get("cumulative_attempts_started") != 344
        or normal.get("new_attempts_started") != 272
        or not isinstance(raw_seeds, list)
        or len(raw_seeds) != 320
    ):
        raise ContractValidationError("M4 terminal domain identity drifted")
    seeds: list[TerminalSeed] = []
    for index, item in enumerate(raw_seeds):
        fields = {
            "acquisition_id",
            "evidence_sha256",
            "initial_sample_size",
            "model_id",
            "recalls",
            "run_id",
            "seed",
            "successes",
            "task_id",
        }
        if not isinstance(item, dict) or set(item) != fields:
            raise ContractValidationError("M4 terminal seed evidence fields drifted")
        raw_recalls = item["recalls"]
        raw_successes = item["successes"]
        if (
            not isinstance(raw_recalls, list)
            or [pair[0] for pair in raw_recalls if isinstance(pair, list) and len(pair) == 2]
            != list(DEFINITION_IDS)
            or not isinstance(raw_successes, list)
            or len(raw_successes) != 1
            or not isinstance(raw_successes[0], list)
            or len(raw_successes[0]) != 2
            or raw_successes[0][0] != "upper-tail-1pct"
            or not isinstance(raw_successes[0][1], bool)
        ):
            raise ContractValidationError("M4 terminal seed metrics drifted")
        recalls = tuple(
            (str(pair[0]), _decimal(pair[1], f"terminal recall {index}")) for pair in raw_recalls
        )
        if any(not Decimal(0) <= value <= Decimal(1) for _, value in recalls):
            raise ContractValidationError("M4 terminal recall is outside [0,1]")
        task_id = item["task_id"]
        model_id = item["model_id"]
        acquisition_id = item["acquisition_id"]
        initial_size = item["initial_sample_size"]
        seed = item["seed"]
        run_id = item["run_id"]
        evidence_sha256 = item["evidence_sha256"]
        if (
            task_id not in TASKS
            or model_id not in MODELS
            or not isinstance(acquisition_id, str)
            or isinstance(initial_size, bool)
            or initial_size not in INITIAL_SIZES
            or isinstance(seed, bool)
            or seed not in SEEDS
            or not isinstance(run_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", run_id)
            or not isinstance(evidence_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256)
        ):
            raise ContractValidationError("M4 terminal seed identity drifted")
        seed_record = TerminalSeed(
            task_id=task_id,
            model_id=model_id,
            acquisition_id=acquisition_id,
            initial_sample_size=initial_size,
            seed=seed,
            run_id=run_id,
            recalls=recalls,
            successes=(("upper-tail-1pct", bool(raw_successes[0][1])),),
            evidence_sha256=evidence_sha256,
        )
        seeds.append(seed_record)
    return normal, tuple(seeds)


def load_analysis_runs(
    terminal_seeds: Sequence[TerminalSeed],
    trajectory_payloads: Sequence[bytes],
) -> tuple[AnalysisRun, ...]:
    """Strict-load and exact-match all 320 trajectories without retaining labels."""

    if len(trajectory_payloads) != 320:
        raise ContractValidationError("Issue #83 requires exactly 320 trajectory payloads")
    terminals = {item.run_id: item for item in terminal_seeds}
    if len(terminals) != 320:
        raise ContractValidationError("terminal run IDs are not an exact unique cover")
    runs: list[AnalysisRun] = []
    seen: set[str] = set()
    for payload in trajectory_payloads:
        trajectory = load_replay_trajectory(payload)
        if trajectory.run_id in seen or trajectory.run_id not in terminals:
            raise ContractValidationError("trajectory run IDs differ from terminal exact cover")
        seen.add(trajectory.run_id)
        terminal = terminals[trajectory.run_id]
        spec = trajectory.run_spec
        if (
            trajectory.task_id != terminal.task_id
            or spec.model_id != terminal.model_id
            or spec.acquisition_id != terminal.acquisition_id
            or spec.initial_sample_size != terminal.initial_sample_size
            or spec.seed != terminal.seed
            or not trajectory.complete
        ):
            raise ContractValidationError("trajectory identity differs from terminal seed evidence")
        _, raw = _strict_json(payload, "M4 trajectory")
        raw_events = raw["events"]
        checkpoints: list[tuple[int, tuple[tuple[str, Decimal], ...]]] = []
        selected: set[str] = set()
        post_initial: list[tuple[int, frozenset[str]]] = []
        for event_index, (event, raw_event) in enumerate(
            zip(trajectory.events, raw_events, strict=True)
        ):
            if event_index > 0:
                selected.update(event.selected_candidate_ids)
            metrics = raw_event.get("metrics")
            if not isinstance(metrics, dict):
                raise ContractValidationError("M4 trajectory omitted sequential metrics")
            values: list[tuple[str, Decimal]] = []
            for metric_id in (
                "high_value_discovery_recall",
                "simple_regret",
                "best_found_value",
            ):
                metric = metrics.get(metric_id)
                if not isinstance(metric, dict) or set(metric) != {
                    "status",
                    "undefined_reason",
                    "value",
                }:
                    raise ContractValidationError("M4 trajectory metric fields drifted")
                if metric["status"] != "defined" or metric["undefined_reason"] is not None:
                    raise ContractValidationError("M4 trajectory required metric is undefined")
                values.append((metric_id, _decimal(metric["value"], metric_id)))
            checkpoints.append((event.distinct_revealed, tuple(values)))
            post_initial.append((event.distinct_revealed, frozenset(selected)))
        final_primary = dict(checkpoints[-1][1])["high_value_discovery_recall"]
        if final_primary != dict(terminal.recalls)["upper-tail-1pct"]:
            raise ContractValidationError("trajectory final primary recall differs from terminal")
        final_success = trajectory.events[-1].metrics
        if (
            final_success is None
            or final_success.success_at_budget.status != "defined"
            or final_success.success_at_budget.value != dict(terminal.successes)["upper-tail-1pct"]
        ):
            raise ContractValidationError("trajectory final success differs from terminal")
        runs.append(
            AnalysisRun(
                terminal=terminal,
                trajectory=trajectory,
                checkpoints=tuple(checkpoints),
                post_initial_by_checkpoint=tuple(post_initial),
            )
        )
    if seen != set(terminals):
        raise ContractValidationError("trajectory payloads do not exact-cover terminal runs")
    return tuple(runs)


def collect_chain_bound_trajectories(
    terminal_bytes: bytes,
    *,
    pilot_objects_root: Path,
    expansion_objects_root: Path,
) -> tuple[tuple[bytes, ...], Mapping[str, object]]:
    """Read only the terminal-selected 320 complete attempt chains from local stores."""

    _, terminal_seeds = load_terminal_seed_evidence(terminal_bytes)
    terminal_by_run = {item.run_id: item for item in terminal_seeds}
    stores = {
        "pilot": LocalArtifactStore(pilot_objects_root.resolve()),
        "expansion": LocalArtifactStore(expansion_objects_root.resolve()),
    }
    roots = {
        "pilot": pilot_objects_root.resolve(),
        "expansion": expansion_objects_root.resolve(),
    }
    attempt_keys: dict[str, list[tuple[str, str]]] = {run_id: [] for run_id in terminal_by_run}
    for source, root in roots.items():
        if not root.is_dir():
            raise ContractValidationError(f"{source} object root is unavailable")
        for path in root.rglob("run-attempt.json"):
            parts = path.relative_to(root).parts
            try:
                runs_index = parts.index("runs")
            except ValueError:
                continue
            if runs_index + 1 >= len(parts):
                continue
            run_id = parts[runs_index + 1]
            if run_id in attempt_keys:
                attempt_keys[run_id].append((source, path.relative_to(root).as_posix()))
    if any(len(values) != 1 for values in attempt_keys.values()):
        raise ContractValidationError(
            "terminal-selected RunAttempts are missing or duplicated across stores"
        )

    payloads: list[bytes] = []
    source_counts = {"pilot": 0, "expansion": 0}
    attempt_key_digest = hashlib.sha256()
    trajectory_digest = hashlib.sha256()
    for terminal in terminal_seeds:
        source, attempt_key = attempt_keys[terminal.run_id][0]
        store = stores[source]
        if source == "pilot":
            base = (
                f"m4/issue-76/pilot/{PILOT_PACKET_SHA256}/runs/{terminal.run_id}/"
                "attempts/attempt-001"
            )
            definition_key = f"{base}/pilot-final-definition-metrics.json"
        else:
            base = (
                f"m4/issue-76/conditional-expansion/{EXPANSION_PACKET_SHA256}/runs/"
                f"{terminal.run_id}/attempts/attempt-001"
            )
            definition_key = f"{base}/conditional-final-definition-metrics.json"
        if attempt_key != f"{base}/run-attempt.json":
            raise ContractValidationError("terminal-selected RunAttempt key drifted")
        attempt = load_run_attempt_v1(
            store.get_bytes(attempt_key), expected_sha256=terminal.evidence_sha256
        )
        trajectory_outputs = [
            item
            for item in attempt.value["outputs"]
            if str(item["object_key"]).endswith("/trajectory.json")
        ]
        definition_outputs = [
            item for item in attempt.value["outputs"] if item["object_key"] == definition_key
        ]
        if (
            attempt.value["status"] != "succeeded"
            or attempt.value["attempt_id"] != "attempt-001"
            or attempt.value["run_id"] != terminal.run_id
            or attempt.value["completion_marker"]["object_key"] != f"{base}/_SUCCESS"
            or attempt.value["compute"].get("executor_type") != "compute_server"
            or attempt.value["compute"].get("region") != "ap-chengdu"
            or len(trajectory_outputs) != 1
            or len(definition_outputs) != 1
            or not is_replay_attempt_eligible(
                store,
                attempt_key,
                additional_output_keys=(definition_key,),
            )
        ):
            raise ContractValidationError("terminal-selected attempt chain is not eligible")
        trajectory_key = str(trajectory_outputs[0]["object_key"])
        trajectory_bytes = store.get_bytes(trajectory_key)
        trajectory = load_replay_trajectory(trajectory_bytes)
        if (
            trajectory.run_id != terminal.run_id
            or trajectory.run_spec.git_commit != attempt.value["executed_git_commit"]
            or trajectory.run_spec.environment_digest
            != attempt.value["executed_environment_digest"]
        ):
            raise ContractValidationError("terminal-selected trajectory provenance drifted")
        source_counts[source] += 1
        attempt_key_digest.update(f"{source}\0{attempt_key}\0".encode())
        attempt_key_digest.update(terminal.evidence_sha256.encode())
        trajectory_digest.update(f"{terminal.run_id}\0{trajectory.sha256}\0".encode())
        payloads.append(trajectory_bytes)
    if source_counts != {"pilot": 48, "expansion": 272}:
        raise ContractValidationError(
            "terminal-selected source split is not 48 pilot + 272 expansion"
        )
    return tuple(payloads), {
        "status": "PASS",
        "pilot_chains": source_counts["pilot"],
        "expansion_chains": source_counts["expansion"],
        "total_chains": len(payloads),
        "attempt_keyset_sha256": attempt_key_digest.hexdigest(),
        "trajectory_keyset_sha256": trajectory_digest.hexdigest(),
        "excluded_pilot_payloads_read": 0,
    }


def _run_index(runs: Sequence[AnalysisRun]) -> dict[tuple[str, str, str, int, int], AnalysisRun]:
    result = {
        (
            run.terminal.task_id,
            run.terminal.model_id,
            run.terminal.acquisition_id,
            run.terminal.initial_sample_size,
            run.terminal.seed,
        ): run
        for run in runs
    }
    expected = {
        (task, model, acquisition, initial, seed)
        for task in TASKS
        for model in MODELS
        for acquisition in (
            RANDOM,
            GREEDY,
            *(item[1] for item in SELECTED_EXPLORATIONS if item[0] == model),
        )
        for initial in INITIAL_SIZES
        for seed in SEEDS
    }
    if len(runs) != 320 or len(result) != 320 or set(result) != expected:
        raise ContractValidationError("analysis runs do not exact-cover the frozen 320 universe")
    return result


def _cell_keys() -> tuple[tuple[str, str, str, int], ...]:
    return tuple(
        (task, model, acquisition, initial)
        for task in TASKS
        for model, acquisition in SELECTED_EXPLORATIONS
        for initial in INITIAL_SIZES
    )


def _paired(
    index: Mapping[tuple[str, str, str, int, int], AnalysisRun],
    cell: tuple[str, str, str, int],
    seeds: Sequence[int],
    *,
    checkpoint: int = 128,
    metric_id: str = "high_value_discovery_recall",
    definition_id: str | None = None,
) -> tuple[Decimal, ...]:
    task, model, acquisition, initial = cell
    values: list[Decimal] = []
    for seed in seeds:
        exploration = index[(task, model, acquisition, initial, seed)]
        greedy = index[(task, model, GREEDY, initial, seed)]
        if definition_id is not None:
            left = exploration.final_recall(definition_id)
            right = greedy.final_recall(definition_id)
        else:
            left = exploration.checkpoint_metric(checkpoint, metric_id)
            right = greedy.checkpoint_metric(checkpoint, metric_id)
        values.append(right - left if metric_id == "simple_regret" else left - right)
    return tuple(values)


def _majority_by_task(flags: Mapping[tuple[str, str, str, int], bool]) -> bool:
    return all(sum(flags[cell] for cell in _cell_keys() if cell[0] == task) >= 3 for task in TASKS)


def _h1_cell_predicate(
    start: PairStats,
    end: PairStats,
    q_task: Decimal,
    definition_means: Mapping[str, Decimal],
) -> Mapping[str, bool]:
    definition_sensitive = len({_sign(value) for value in definition_means.values()}) > 1
    sign_decline = min(end.positive, end.negative) < min(start.positive, start.negative)
    sd_decline = end.sample_sd < start.sample_sd
    quantized = abs(end.mean) <= q_task
    support = sd_decline and (sign_decline or quantized or definition_sensitive)
    against = end.sample_sd >= start.sample_sd and not quantized and not definition_sensitive
    return {
        "sd_decline": sd_decline,
        "sign_decline": sign_decline,
        "quantized": quantized,
        "definition_sensitive": definition_sensitive,
        "cell_support": support,
        "cell_against": against,
    }


def _hypothesis_1(index: Mapping[tuple[str, str, str, int, int], AnalysisRun]) -> HypothesisResult:
    supports: dict[tuple[str, str, str, int], bool] = {}
    against: dict[tuple[str, str, str, int], bool] = {}
    details: list[dict[str, object]] = []
    for cell in _cell_keys():
        checkpoint_stats = {
            checkpoint: _pair_stats(_paired(index, cell, SEEDS, checkpoint=checkpoint))
            for checkpoint in CHECKPOINTS
        }
        simple_regret_stats = {
            checkpoint: _pair_stats(
                _paired(
                    index,
                    cell,
                    SEEDS,
                    checkpoint=checkpoint,
                    metric_id="simple_regret",
                )
            )
            for checkpoint in CHECKPOINTS
        }
        final_definition_means = {
            definition: _mean(_paired(index, cell, SEEDS, definition_id=definition))
            for definition in DEFINITION_IDS
        }
        start = checkpoint_stats[64]
        end = checkpoint_stats[128]
        q_task = Decimal(1) / Decimal(PRIMARY_HIGH_VALUE_COUNTS[cell[0]])
        predicate = _h1_cell_predicate(start, end, q_task, final_definition_means)
        support = predicate["cell_support"]
        cell_against = predicate["cell_against"]
        supports[cell] = support
        against[cell] = cell_against
        details.append(
            {
                "cell": cell,
                "checkpoint_stats": checkpoint_stats,
                "task_local_simple_regret_stats": simple_regret_stats,
                "final_definition_means": final_definition_means,
                "q_task": q_task,
                **predicate,
            }
        )
    if _majority_by_task(supports):
        verdict: Verdict = "SUPPORTED"
    elif _majority_by_task(against):
        verdict = "NOT_SUPPORTED"
    else:
        verdict = "INCONCLUSIVE"
    return HypothesisResult("H1", verdict, "effective-label/event scarcity", tuple(details))


def _h2_cell_predicate(stats: PairStats) -> Mapping[str, bool]:
    zero_touch = stats.ci_lower <= 0 <= stats.ci_upper
    mixed_sign = stats.positive > 0 and stats.negative > 0
    same_order = stats.sample_sd >= abs(stats.mean)
    small = stats.sample_sd < Decimal("0.5") * abs(stats.mean)
    support = same_order and (zero_touch or mixed_sign or stats.loo_sign_change)
    against = small and not zero_touch and not mixed_sign and not stats.loo_sign_change
    return {
        "same_order_or_larger": same_order,
        "small_dispersion": small,
        "mixed_sign": mixed_sign,
        "ci_zero_touch": zero_touch,
        "cell_support": support,
        "cell_against": against,
    }


def _hypothesis_2(index: Mapping[tuple[str, str, str, int, int], AnalysisRun]) -> HypothesisResult:
    supports: dict[tuple[str, str, str, int], bool] = {}
    against: dict[tuple[str, str, str, int], bool] = {}
    details: list[dict[str, object]] = []
    for cell in _cell_keys():
        stats = _pair_stats(_paired(index, cell, SEEDS))
        simple_regret_stats = _pair_stats(_paired(index, cell, SEEDS, metric_id="simple_regret"))
        predicate = _h2_cell_predicate(stats)
        support = predicate["cell_support"]
        cell_against = predicate["cell_against"]
        supports[cell] = support
        against[cell] = cell_against
        details.append(
            {
                "cell": cell,
                "stats": stats,
                "task_local_simple_regret_stats": simple_regret_stats,
                **predicate,
            }
        )
    if _majority_by_task(supports):
        verdict: Verdict = "SUPPORTED"
    elif _majority_by_task(against):
        verdict = "NOT_SUPPORTED"
    else:
        verdict = "INCONCLUSIVE"
    return HypothesisResult("H2", verdict, "RunSpec-seed variability", tuple(details))


def _rank_concordance(left: Mapping[str, Decimal], right: Mapping[str, Decimal]) -> Decimal:
    concordant = 0
    discordant = 0
    models = MODELS
    for first_index, first in enumerate(models):
        for second in models[first_index + 1 :]:
            left_sign = _sign(left[first] - left[second])
            right_sign = _sign(right[first] - right[second])
            if left_sign == 0 or right_sign == 0:
                continue
            if left_sign == right_sign:
                concordant += 1
            else:
                discordant += 1
    return Decimal(concordant - discordant) / Decimal(3)


def _h3_static_signal(
    spearman: Mapping[str, Decimal],
    primary_recall: Mapping[str, Decimal],
    q_task: Decimal,
) -> str:
    weak = max(spearman.values()) < Decimal("0.20") and max(primary_recall.values()) <= q_task
    strong = (
        max(spearman.values()) >= Decimal("0.30")
        and max(primary_recall.values()) >= Decimal(2) * q_task
    )
    return "weak" if weak else "strong" if strong else "intermediate"


def _hypothesis_3(
    index: Mapping[tuple[str, str, str, int, int], AnalysisRun],
    rows: Sequence[StaticRow],
) -> HypothesisResult:
    static = {(row.task_id, row.model_id): row for row in rows}
    task_details: list[dict[str, object]] = []
    task_mismatches: dict[str, int] = {}
    weak_by_task: dict[str, bool] = {}
    for task in TASKS:
        spearman = {model: static[(task, model)].spearman for model in MODELS}
        static_primary = {model: static[(task, model)].recall_1 for model in MODELS}
        q_task = Decimal(1) / Decimal(PRIMARY_HIGH_VALUE_COUNTS[task])
        static_signal = _h3_static_signal(spearman, static_primary, q_task)
        weak = static_signal == "weak"
        comparisons: list[dict[str, object]] = []
        sensitivity_comparisons: list[dict[str, object]] = []
        mismatch_count = 0
        for initial in INITIAL_SIZES:
            greedy = {
                model: _mean(
                    [
                        index[(task, model, GREEDY, initial, seed)].final_recall("upper-tail-1pct")
                        for seed in SEEDS
                    ]
                )
                for model in MODELS
            }
            sources = (("spearman", spearman), ("primary_hv_recall", static_primary))
            for source_name, source in sources:
                concordance = _rank_concordance(source, greedy)
                mismatch = concordance <= 0
                mismatch_count += int(mismatch)
                comparisons.append(
                    {
                        "source": source_name,
                        "initial_sample_size": initial,
                        "rank_concordance": concordance,
                        "rank_mismatch": mismatch,
                    }
                )
            for definition, attribute in (
                ("upper-tail-0.5pct", "recall_05"),
                ("upper-tail-2pct", "recall_2"),
            ):
                static_sensitivity = {
                    model: cast(Decimal, getattr(static[(task, model)], attribute))
                    for model in MODELS
                }
                greedy_sensitivity = {
                    model: _mean(
                        [
                            index[(task, model, GREEDY, initial, seed)].final_recall(definition)
                            for seed in SEEDS
                        ]
                    )
                    for model in MODELS
                }
                sensitivity_comparisons.append(
                    {
                        "definition_id": definition,
                        "initial_sample_size": initial,
                        "rank_concordance": _rank_concordance(
                            static_sensitivity, greedy_sensitivity
                        ),
                    }
                )
        task_mismatches[task] = mismatch_count
        weak_by_task[task] = weak
        task_details.append(
            {
                "task_id": task,
                "static_signal": static_signal,
                "h3a_weak_static": weak,
                "h3b_rank_mismatch_count": mismatch_count,
                "comparisons": tuple(comparisons),
                "definition_sensitivity_only": tuple(sensitivity_comparisons),
            }
        )
    if all(task_mismatches[task] >= 3 for task in TASKS) or all(weak_by_task.values()):
        verdict: Verdict = "SUPPORTED"
    elif all(task_mismatches[task] <= 1 and not weak_by_task[task] for task in TASKS):
        verdict = "NOT_SUPPORTED"
    else:
        verdict = "INCONCLUSIVE"
    return HypothesisResult(
        "H3",
        verdict,
        "weak static signal or prediction-to-decision rank mismatch",
        tuple(task_details),
    )


def _h4_cell_predicate(pilot: PairStats, remaining: PairStats) -> Mapping[str, bool]:
    shrinkage = abs(remaining.mean) < abs(pilot.mean)
    cross_zero = (pilot.mean > 0 and remaining.mean <= 0) or (
        pilot.mean < 0 and remaining.mean >= 0
    )
    ci_loses = not (pilot.ci_lower <= 0 <= pilot.ci_upper) and (
        remaining.ci_lower <= 0 <= remaining.ci_upper
    )
    return {
        "shrinkage": shrinkage,
        "cross_zero": cross_zero,
        "ci_loses_exclusion": ci_loses,
    }


def _hypothesis_4(index: Mapping[tuple[str, str, str, int, int], AnalysisRun]) -> HypothesisResult:
    details: list[dict[str, object]] = []
    shrinkage_count = 0
    warning_count = 0
    for task in TASKS:
        for model, acquisition in SELECTED_EXPLORATIONS:
            cell = (task, model, acquisition, 32)
            pilot = _pair_stats(_paired(index, cell, PILOT_SEEDS))
            remaining = _pair_stats(_paired(index, cell, REMAINING_SEEDS))
            predicate = _h4_cell_predicate(pilot, remaining)
            shrinkage_count += int(predicate["shrinkage"])
            warning_count += int(predicate["cross_zero"] or predicate["ci_loses_exclusion"])
            details.append(
                {
                    "cell": cell,
                    "pilot_n3": pilot,
                    "remaining_n7": remaining,
                    **predicate,
                }
            )
    if shrinkage_count >= 3 and warning_count >= 1:
        verdict: Verdict = "SUPPORTED"
    elif shrinkage_count <= 1 and warning_count == 0:
        verdict = "NOT_SUPPORTED"
    else:
        verdict = "INCONCLUSIVE"
    return HypothesisResult("H4", verdict, "pilot selection optimism", tuple(details))


def _pair_relation(a: Decimal, b: Decimal, q: Decimal) -> dict[str, object]:
    reversal = a * b < 0
    gap = abs(a - b) > q
    if a == b == 0:
        similar = True
    else:
        denominator = max(abs(a), abs(b))
        similar = denominator != 0 and min(abs(a), abs(b)) / denominator >= Decimal("0.5")
    return {
        "a": a,
        "b": b,
        "sign_reversal": reversal,
        "large_gap": gap,
        "same_sign": _sign(a) == _sign(b),
        "similar_magnitude": similar,
    }


def _hypothesis_5(index: Mapping[tuple[str, str, str, int, int], AnalysisRun]) -> HypothesisResult:
    means = {cell: _mean(_paired(index, cell, SEEDS)) for cell in _cell_keys()}
    task_pairs: list[dict[str, object]] = []
    initial_pairs: list[dict[str, object]] = []
    for model, acquisition in SELECTED_EXPLORATIONS:
        for initial in INITIAL_SIZES:
            relation = _pair_relation(
                means[("alpha-amylase", model, acquisition, initial)],
                means[("ired", model, acquisition, initial)],
                max(
                    Decimal(1) / Decimal(PRIMARY_HIGH_VALUE_COUNTS["alpha-amylase"]),
                    Decimal(1) / Decimal(PRIMARY_HIGH_VALUE_COUNTS["ired"]),
                ),
            )
            task_pairs.append(
                {
                    "model_id": model,
                    "acquisition_id": acquisition,
                    "initial": initial,
                    **relation,
                }
            )
    for task in TASKS:
        for model, acquisition in SELECTED_EXPLORATIONS:
            relation = _pair_relation(
                means[(task, model, acquisition, 32)],
                means[(task, model, acquisition, 64)],
                Decimal(1) / Decimal(PRIMARY_HIGH_VALUE_COUNTS[task]),
            )
            initial_pairs.append(
                {
                    "task_id": task,
                    "model_id": model,
                    "acquisition_id": acquisition,
                    **relation,
                }
            )

    def heterogeneous(item: Mapping[str, object]) -> bool:
        return bool(item["sign_reversal"] or item["large_gap"])

    def similar(item: Mapping[str, object]) -> bool:
        return bool(item["same_sign"] and item["similar_magnitude"])

    if (
        sum(heterogeneous(item) for item in task_pairs) >= 2
        or sum(heterogeneous(item) for item in initial_pairs) >= 2
    ):
        verdict: Verdict = "SUPPORTED"
    elif (
        sum(similar(item) for item in task_pairs) >= 3
        and sum(similar(item) for item in initial_pairs) >= 3
    ):
        verdict = "NOT_SUPPORTED"
    else:
        verdict = "INCONCLUSIVE"
    return HypothesisResult(
        "H5",
        verdict,
        "task/initial regime heterogeneity (initial comparison is round-confounded)",
        {"task_pairs": tuple(task_pairs), "initial_pairs": tuple(initial_pairs)},
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> Decimal:
    union = left | right
    if not union:
        return Decimal(1)
    return Decimal(len(left & right)) / Decimal(len(union))


def _h6_cell_predicate(
    overlap_exploration_greedy: Decimal,
    overlap_random_greedy: Decimal,
    paired_effect: Decimal,
    q_task: Decimal,
) -> Mapping[str, bool]:
    redundant = overlap_exploration_greedy >= overlap_random_greedy
    separated = overlap_exploration_greedy + Decimal("0.10") <= overlap_random_greedy
    small = abs(paired_effect) <= q_task
    return {
        "redundant": redundant,
        "clearly_separated": separated,
        "small_effect": small,
        "cell_support": redundant and small,
        "cell_against": separated,
    }


def _hypothesis_6(index: Mapping[tuple[str, str, str, int, int], AnalysisRun]) -> HypothesisResult:
    supports: dict[tuple[str, str, str, int], bool] = {}
    against: dict[tuple[str, str, str, int], bool] = {}
    details: list[dict[str, object]] = []
    for cell in _cell_keys():
        task, model, acquisition, initial = cell
        eg_final: list[Decimal] = []
        rg_final: list[Decimal] = []
        checkpoint_rows: list[dict[str, object]] = []
        for checkpoint in CHECKPOINTS:
            eg: list[Decimal] = []
            rg: list[Decimal] = []
            for seed in SEEDS:
                exploration = dict(
                    index[(task, model, acquisition, initial, seed)].post_initial_by_checkpoint
                )[checkpoint]
                greedy = dict(
                    index[(task, model, GREEDY, initial, seed)].post_initial_by_checkpoint
                )[checkpoint]
                random = dict(
                    index[(task, model, RANDOM, initial, seed)].post_initial_by_checkpoint
                )[checkpoint]
                eg.append(_jaccard(exploration, greedy))
                rg.append(_jaccard(random, greedy))
            checkpoint_rows.append(
                {
                    "checkpoint": checkpoint,
                    "exploration_greedy": _mean(eg),
                    "random_greedy": _mean(rg),
                }
            )
            if checkpoint == 128:
                eg_final = eg
                rg_final = rg
        overlap_eg = _mean(eg_final)
        overlap_rg = _mean(rg_final)
        effect = _mean(_paired(index, cell, SEEDS))
        q_task = Decimal(1) / Decimal(PRIMARY_HIGH_VALUE_COUNTS[task])
        predicate = _h6_cell_predicate(overlap_eg, overlap_rg, effect, q_task)
        supports[cell] = predicate["cell_support"]
        against[cell] = predicate["cell_against"]
        details.append(
            {
                "cell": cell,
                "checkpoint_overlap": tuple(checkpoint_rows),
                "paired_primary_effect": effect,
                "q_task": q_task,
                **predicate,
            }
        )
    if _majority_by_task(supports):
        verdict: Verdict = "SUPPORTED"
    elif _majority_by_task(against):
        verdict = "NOT_SUPPORTED"
    else:
        verdict = "INCONCLUSIVE"
    return HypothesisResult(
        "H6", verdict, "exploration/Greedy behavioral redundancy", tuple(details)
    )


def build_gate_blocker_map(decision: Gate3Decision) -> tuple[Mapping[str, object], ...]:
    """Map only the authoritative Gate 3 booleans to the frozen display taxonomy."""

    if decision.status == "INCONCLUSIVE" or len(decision.cell_decisions) != 8:
        return ()
    result: list[Mapping[str, object]] = []
    for cell in decision.cell_decisions:
        sensitivities = dict(cell.sensitivity_mean_differences)
        sensitivity_reversal = any(Decimal(str(value)) < 0 for value in sensitivities.values())
        primary = Decimal(str(cell.primary_recall_mean_difference))
        success = Decimal(str(cell.success_probability_difference))
        other_nonnegative = (not cell.recall_pass or success >= 0) and (
            not cell.success_pass or primary >= 0
        )
        if sensitivity_reversal:
            category = "SENSITIVITY_REVERSAL"
        elif not cell.recall_pass and not cell.success_pass:
            category = "NO_PRIMARY_PASS"
        elif not other_nonnegative:
            category = "OTHER_PRIMARY_NEGATIVE"
        else:
            category = "RETAINED"
        expected_status = {
            "SENSITIVITY_REVERSAL": "fragile-inconclusive",
            "NO_PRIMARY_PASS": "eliminated",
            "OTHER_PRIMARY_NEGATIVE": "eliminated",
            "RETAINED": "retained",
        }[category]
        if cell.status != expected_status:
            raise ContractValidationError("Gate blocker category differs from authoritative status")
        margins: dict[str, object] = {
            "positive_count_margin": cell.positive_primary_seeds - 7,
            "primary_mean_margin": primary - Decimal("0.01"),
            "success_margin": success - Decimal("0.10"),
            "sensitivity_zero_margins": tuple(
                (definition, Decimal(str(value))) for definition, value in sensitivities.items()
            ),
        }
        if cell.recall_pass and not cell.success_pass:
            margins["other_success_nonnegative_margin"] = success
        if cell.success_pass and not cell.recall_pass:
            margins["other_recall_nonnegative_margin"] = primary
        result.append(
            {
                "task_id": cell.task_id,
                "model_id": cell.model_id,
                "acquisition_id": cell.acquisition_id,
                "initial_sample_size": cell.initial_sample_size,
                "category": category,
                "authoritative_status": cell.status,
                "recall_pass": cell.recall_pass,
                "success_pass": cell.success_pass,
                "task_heterogeneous_annotation": cell.task_heterogeneous,
                **margins,
            }
        )
    return tuple(result)


def _build_exact_gate_blocker_map(
    decision: Gate3Decision,
    index: Mapping[tuple[str, str, str, int, int], AnalysisRun],
) -> tuple[Mapping[str, object], ...]:
    """Recompute display margins from canonical terminal decimal text."""

    approximate = build_gate_blocker_map(decision)
    exact_rows: list[Mapping[str, object]] = []
    for row in approximate:
        cell = (
            cast(str, row["task_id"]),
            cast(str, row["model_id"]),
            cast(str, row["acquisition_id"]),
            cast(int, row["initial_sample_size"]),
        )
        primary_values = _paired(index, cell, SEEDS)
        primary = _mean(primary_values)
        success_values: list[Decimal] = []
        sensitivity: list[tuple[str, Decimal]] = []
        task, model, acquisition, initial = cell
        for seed in SEEDS:
            exploration = index[(task, model, acquisition, initial, seed)]
            greedy = index[(task, model, GREEDY, initial, seed)]
            success_values.append(
                Decimal(int(dict(exploration.terminal.successes)["upper-tail-1pct"]))
                - Decimal(int(dict(greedy.terminal.successes)["upper-tail-1pct"]))
            )
        success = _mean(success_values)
        for definition in DEFINITION_IDS[1:]:
            sensitivity.append(
                (definition, _mean(_paired(index, cell, SEEDS, definition_id=definition)))
            )
        positive_count = sum(value > 0 for value in primary_values)
        exact = dict(row)
        exact.update(
            {
                "positive_count_margin": positive_count - 7,
                "primary_mean_margin": primary - Decimal("0.01"),
                "success_margin": success - Decimal("0.10"),
                "sensitivity_zero_margins": tuple(sensitivity),
            }
        )
        if row["recall_pass"] and not row["success_pass"]:
            exact["other_success_nonnegative_margin"] = success
        if row["success_pass"] and not row["recall_pass"]:
            exact["other_recall_nonnegative_margin"] = primary
        exact_rows.append(exact)
    return tuple(exact_rows)


def _reproduce_gate(
    terminal_normal: Mapping[str, Any], runs: Sequence[AnalysisRun]
) -> Gate3Decision:
    metrics = tuple(
        ConditionalRunMetrics(
            task_id=run.terminal.task_id,
            run_spec=run.trajectory.run_spec,
            recalls=tuple((name, float(value)) for name, value in run.terminal.recalls),
            successes=run.terminal.successes,
            evidence_sha256=run.terminal.evidence_sha256,
        )
        for run in runs
    )
    decision = evaluate_gate_3(metrics)
    if (
        decision.status != terminal_normal["gate_3_status"]
        or decision.failure_reason != terminal_normal["failure_reason"]
        or canonical_json_bytes([asdict(item) for item in decision.cell_decisions])
        != canonical_json_bytes(terminal_normal["gate_3_cell_decisions"])
        or canonical_json_bytes([asdict(item) for item in decision.seed_evidence])
        != canonical_json_bytes(terminal_normal["gate_3_seed_evidence"])
        or list(decision.frozen_run_ids) != terminal_normal["frozen_run_ids"]
    ):
        raise ContractValidationError("M4 terminal does not reproduce from strict-loaded evidence")
    return decision


def _inconclusive(hypothesis_id: str, exc: Exception) -> HypothesisResult:
    return HypothesisResult(
        hypothesis_id=hypothesis_id,
        verdict="INCONCLUSIVE",
        reason="DATA-INCOMPLETE",
        details={"error": str(exc)},
    )


def analyze_issue83_failure(
    *,
    terminal_bytes: bytes,
    trajectory_payloads: Sequence[bytes],
    m2_review_packet_bytes: bytes,
    m2_repository_path: str = M2_PATH,
    m2_git_commit: str = M2_COMMIT,
) -> FailureAnalysisReport:
    """Produce the frozen H1-H7 cause matrix from accepted evidence only."""

    terminal_normal, terminal_seeds = load_terminal_seed_evidence(terminal_bytes)
    runs = load_analysis_runs(terminal_seeds, trajectory_payloads)
    index = _run_index(runs)
    gate = _reproduce_gate(terminal_normal, runs)
    try:
        static_rows = load_m2_static_rows(
            m2_review_packet_bytes,
            repository_path=m2_repository_path,
            git_commit=m2_git_commit,
        )
    except ContractValidationError as exc:
        static_rows = ()
        h3 = HypothesisResult("H3", "INCONCLUSIVE", "IDENTITY-DRIFT", {"error": str(exc)})
    else:
        h3 = _hypothesis_3(index, static_rows)
    evaluators: tuple[tuple[str, Callable[[], HypothesisResult]], ...] = (
        ("H1", lambda: _hypothesis_1(index)),
        ("H2", lambda: _hypothesis_2(index)),
        ("H4", lambda: _hypothesis_4(index)),
        ("H5", lambda: _hypothesis_5(index)),
        ("H6", lambda: _hypothesis_6(index)),
    )
    results: dict[str, HypothesisResult] = {"H3": h3}
    for hypothesis_id, evaluator in evaluators:
        try:
            results[hypothesis_id] = evaluator()
        except (AnalysisIncomplete, ContractValidationError, KeyError, ValueError) as exc:
            results[hypothesis_id] = _inconclusive(hypothesis_id, exc)
    blocker_map = _build_exact_gate_blocker_map(gate, index)
    results["H7"] = HypothesisResult(
        "H7",
        "SUPPORTED",
        "authoritative Gate 3 blocker path reproduced exactly",
        {"gate_status": gate.status, "blocker_count": len(blocker_map)},
    )
    return FailureAnalysisReport(
        input_identity={
            "m2": {
                "repository_path": m2_repository_path,
                "git_commit": m2_git_commit,
                "git_blob": M2_BLOB,
                "size_bytes": len(m2_review_packet_bytes),
                "sha256": hashlib.sha256(m2_review_packet_bytes).hexdigest(),
            },
            "m4_terminal": {
                "size_bytes": len(terminal_bytes),
                "sha256": hashlib.sha256(terminal_bytes).hexdigest(),
                "packet_sha256": EXPANSION_PACKET_SHA256,
                "trajectory_count": len(runs),
                "attempts_started": 344,
            },
        },
        cause_matrix=tuple(results[f"H{index}"] for index in range(1, 8)),
        gate_blocker_map=blocker_map,
        unidentifiable=(
            "label noise",
            "activity cliff / local landscape roughness",
            "feature versus model-class contribution",
            "UQ/acquisition calibration or parameter mismatch",
            "candidate-pool scale effect",
            "individual initial/bootstrap/acquisition randomness contribution",
            "full per-round prediction-to-decision ranking",
        ),
        limitations=(
            "initial=32 versus 64 is confounded by acquisition-round count",
            "RunSpec seed couples initial selection, bootstrap, and acquisition randomness",
            "M2/M4 rank alignment has only three models and two tasks",
            "H5 applies the frozen no-rounding rule, so opposite-sign Decimal residues such as "
            "1E-18 and -6E-19 count as sign reversal; interpret that component as "
            "numerical-boundary sensitive",
            "multi-seed evidence does not replace independent task/assay replication",
            "SUPPORTED is descriptive consistency, not causal proof",
        ),
    )


def analyze_issue83_from_stores(
    *,
    terminal_bytes: bytes,
    m2_review_packet_bytes: bytes,
    pilot_objects_root: Path,
    expansion_objects_root: Path,
) -> FailureAnalysisReport:
    """Validate the accepted local chains and run the bounded analysis."""

    trajectory_payloads, chain_identity = collect_chain_bound_trajectories(
        terminal_bytes,
        pilot_objects_root=pilot_objects_root,
        expansion_objects_root=expansion_objects_root,
    )
    report = analyze_issue83_failure(
        terminal_bytes=terminal_bytes,
        trajectory_payloads=trajectory_payloads,
        m2_review_packet_bytes=m2_review_packet_bytes,
    )
    identity = dict(report.input_identity)
    identity["m4_chain_validation"] = chain_identity
    return replace(report, input_identity=identity)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bounded local-store analysis without exposing candidate-level data."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", type=Path, required=True)
    parser.add_argument("--m2-review-packet", type=Path, required=True)
    parser.add_argument("--pilot-objects-root", type=Path, required=True)
    parser.add_argument("--expansion-objects-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = cast(Path, args.output)
    if output.exists():
        raise ContractValidationError("Issue #83 output path already exists")
    report = analyze_issue83_from_stores(
        terminal_bytes=cast(Path, args.terminal).read_bytes(),
        m2_review_packet_bytes=cast(Path, args.m2_review_packet).read_bytes(),
        pilot_objects_root=cast(Path, args.pilot_objects_root),
        expansion_objects_root=cast(Path, args.expansion_objects_root),
    )
    payload = report.canonical_bytes
    with output.open("xb") as handle:
        handle.write(payload)
    print(
        json.dumps(
            {
                "cause_matrix": [
                    {
                        "hypothesis_id": item.hypothesis_id,
                        "verdict": item.verdict,
                    }
                    for item in report.cause_matrix
                ],
                "gate_blocker_count": len(report.gate_blocker_map),
                "output_sha256": hashlib.sha256(payload).hexdigest(),
                "output_size_bytes": len(payload),
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


__all__ = [
    "FailureAnalysisReport",
    "HypothesisResult",
    "PairStats",
    "StaticRow",
    "analyze_issue83_failure",
    "analyze_issue83_from_stores",
    "build_gate_blocker_map",
    "load_analysis_runs",
    "load_m2_static_rows",
    "load_terminal_seed_evidence",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
