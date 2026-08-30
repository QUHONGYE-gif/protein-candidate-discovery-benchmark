"""Exact-cohort and aggregate contracts for formal read-only M5A reconstruction."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import struct
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.contracts.run import RunSpec
from pcdb.m4.conditional_expansion import (
    SELECTED_EXPLORATIONS,
    materialize_conditional_run_specs,
)
from pcdb.m4.execution_adapter import PreparedRun
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet
from pcdb.m5.reconstruction_smoke import compact_json_bytes
from pcdb.replay.engine import ReplayTrajectory, load_replay_trajectory

FORMAL_REPORT_SCHEMA = "m5a-formal-mechanism-report-v1"
TRAJECTORY_SUMMARY_SCHEMA = "m5a-formal-trajectory-summary-v1"
_GREEDY = "greedy-predictive-mean-v1"
_TARGET_PAIRS = {
    ("random-forest-sklearn-v1", _GREEDY),
    ("random-forest-sklearn-v1", "uncertainty-bootstrap-std-v1"),
    ("xgboost-cpu-v1", _GREEDY),
    ("xgboost-cpu-v1", "ucb-bootstrap-std-beta1-v1"),
}


@dataclass(frozen=True, slots=True)
class FormalTrajectory:
    """One selected trajectory with its exact immutable source chain."""

    prepared: PreparedRun
    source: str
    attempt_path: Path
    attempt_sha256: str
    trajectory_path: Path
    trajectory_sha256: str
    trajectory: ReplayTrajectory


@dataclass(frozen=True, slots=True)
class FormalInventory:
    """Exact 160 selected plus 184 metadata-only exclusions."""

    selected: tuple[FormalTrajectory, ...]
    excluded: tuple[Mapping[str, object], ...]
    canonical_bytes: bytes
    sha256: str


def binary64_hex(value: float) -> str:
    converted = float(value)
    if not math.isfinite(converted):
        raise ContractValidationError("formal aggregate value must be finite")
    return struct.pack(">d", converted).hex()


def binary64_from_hex(value: object) -> float:
    if not isinstance(value, str) or len(value) != 16:
        raise ContractValidationError("formal binary64 hex is invalid")
    try:
        decoded = cast(float, struct.unpack(">d", bytes.fromhex(value))[0])
    except (ValueError, struct.error):
        raise ContractValidationError("formal binary64 hex is invalid") from None
    if not math.isfinite(decoded):
        raise ContractValidationError("formal binary64 hex must be finite")
    return decoded


def load_formal_contract(payload: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("formal M5A contract must be UTF-8 JSON") from None
    expected = {
        "artifact_contract",
        "cohort",
        "features",
        "resource_caps",
        "resource_probe",
        "retry_policy",
        "schema_version",
        "sources",
        "stop_conditions",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ContractValidationError("formal M5A contract fields drifted")
    if value["schema_version"] != "m5a-formal-reconstruction-contract-v1":
        raise ContractValidationError("formal M5A contract schema drifted")
    cohort = value["cohort"]
    if (
        cohort.get("successful_attempt_inventory") != 344
        or cohort.get("selected_trajectories") != 160
        or cohort.get("excluded_successful_attempts") != 184
        or cohort.get("tasks") != ["alpha-amylase", "ired"]
        or cohort.get("initial_sample_sizes") != [32, 64]
        or cohort.get("seeds") != list(range(1729, 1739))
        or {tuple(item) for item in cohort.get("models_and_policies", [])} != _TARGET_PAIRS
    ):
        raise ContractValidationError("formal M5A cohort contract drifted")
    caps = value["resource_caps"]
    if caps != {
        "active_execution_network_bytes": 0,
        "attempt_wall_seconds": 18000,
        "estimated_hourly_cost_cny": "1.62",
        "estimated_maximum_cost_cny": "8.10",
        "process_tree_peak_rss_bytes": 2 * 1024**3,
        "scratch_high_water_bytes": 1024**3,
        "trajectory_two_run_cumulative_wall_seconds": 360,
        "vcpu": 2,
    }:
        raise ContractValidationError("formal M5A resource caps drifted")
    if value["retry_policy"] != {
        "automatic_retries": 0,
        "maximum_attempts": 2,
        "maximum_cumulative_wall_seconds": 36000,
        "retryable_failure_class": "transient-infrastructure",
    }:
        raise ContractValidationError("formal M5A retry policy drifted")
    return MappingProxyType(cast(dict[str, Any], value))


def _attempt_root(
    prepared: PreparedRun,
    *,
    source: str,
    contract: Mapping[str, Any],
    store_roots: Mapping[str, Path],
) -> Path:
    phase = "pilot" if source == "pilot" else "conditional-expansion"
    packet_sha256 = cast(str, contract["sources"][source]["packet_sha256"])
    return (
        store_roots[source]
        / "objects"
        / "m4"
        / "issue-76"
        / phase
        / packet_sha256
        / "runs"
        / prepared.run_spec.run_id
        / "attempts"
        / "attempt-001"
    )


def _exclusion_reason(prepared: PreparedRun, pilot_specific: bool) -> str:
    if pilot_specific:
        return "pilot-only-ridge-exploration"
    if prepared.run_spec.acquisition_id == "random-seeded-sha256-v1":
        return "random-control"
    if prepared.run_spec.model_id == "ridge-sklearn-v1":
        return "ridge-greedy"
    raise ContractValidationError("formal M5A exclusion reason is unresolved")


def build_formal_inventory(
    repository_root: Path,
    *,
    contract: Mapping[str, Any],
    store_roots: Mapping[str, Path],
) -> FormalInventory:
    """Outcome-free exact-select 160 and metadata-only exact-exclude 184."""

    pilot = build_pilot_preflight_packet(
        repository_root,
        contract["sources"]["pilot"]["git_commit"],
        contract["sources"]["pilot"]["git_tree"],
    )
    if hashlib.sha256(pilot).hexdigest() != contract["sources"]["pilot"]["packet_sha256"]:
        raise ContractValidationError("pilot packet identity drifted")
    matrix = materialize_conditional_run_specs(
        pilot,
        reviewed_git_commit=contract["sources"]["conditional-expansion"]["git_commit"],
        selected_explorations=SELECTED_EXPLORATIONS,
    )
    pilot_value = json.loads(pilot)
    pilot_runs = {
        item["run_id"]: PreparedRun(
            task_id=item["task_id"],
            run_spec=RunSpec(**item["run_spec"]),
        )
        for item in pilot_value["run_specs"]
    }
    expected_runs = {item.run_spec.run_id: item for item in matrix.candidate_runs}
    expected_runs.update(pilot_runs)
    if len(expected_runs) != 344:
        raise ContractValidationError("formal M5A successful inventory arithmetic drifted")

    actual_paths: dict[str, Path] = {}
    for source, root in store_roots.items():
        packet = contract["sources"][source]["packet_sha256"]
        phase = "pilot" if source == "pilot" else "conditional-expansion"
        run_root = root / "objects" / "m4" / "issue-76" / phase / packet / "runs"
        paths = tuple(run_root.glob("*/attempts/attempt-001/run-attempt.json"))
        if len(paths) != contract["sources"][source]["successful_attempts"]:
            raise ContractValidationError("formal M5A source attempt count drifted")
        for path in paths:
            run_id = path.parents[2].name
            if run_id in actual_paths:
                raise ContractValidationError("formal M5A duplicate successful Run ID")
            actual_paths[run_id] = path
    if set(actual_paths) != set(expected_runs):
        raise ContractValidationError("formal M5A 344 successful attempts do not exact-cover")

    selected: list[FormalTrajectory] = []
    excluded: list[Mapping[str, object]] = []
    pilot_ids = set(pilot_runs)
    candidate_ids = {item.run_spec.run_id for item in matrix.candidate_runs}
    ordered = sorted(
        expected_runs.values(),
        key=lambda item: (
            item.task_id,
            item.run_spec.model_id,
            item.run_spec.acquisition_id,
            item.run_spec.initial_sample_size,
            item.run_spec.seed,
            item.run_spec.run_id,
        ),
    )
    for prepared in ordered:
        run_id = prepared.run_spec.run_id
        source = "pilot" if run_id in pilot_ids else "conditional-expansion"
        attempt_path = actual_paths[run_id]
        attempt_bytes = attempt_path.read_bytes()
        attempt = load_run_attempt_v1(attempt_bytes)
        marker_path = attempt_path.with_name("_SUCCESS")
        if (
            attempt.value["status"] != "succeeded"
            or attempt.value["run_id"] != run_id
            or marker_path.read_bytes() != b""
        ):
            raise ContractValidationError("formal M5A attempt is not completion-marked successful")
        refs = [
            output
            for output in attempt.value["outputs"]
            if str(output["object_key"]).endswith("/trajectory.json")
        ]
        if len(refs) != 1:
            raise ContractValidationError("formal M5A RunAttempt trajectory reference drifted")
        is_selected = (
            run_id in candidate_ids
            and (prepared.run_spec.model_id, prepared.run_spec.acquisition_id) in _TARGET_PAIRS
        )
        if not is_selected:
            excluded.append(
                MappingProxyType(
                    {
                        "attempt_sha256": hashlib.sha256(attempt_bytes).hexdigest(),
                        "reason": _exclusion_reason(prepared, run_id not in candidate_ids),
                        "run_id": run_id,
                        "source": source,
                        "trajectory_reference_sha256": refs[0]["sha256"],
                    }
                )
            )
            continue
        trajectory_path = attempt_path.with_name("trajectory.json")
        trajectory_bytes = trajectory_path.read_bytes()
        if (
            hashlib.sha256(trajectory_bytes).hexdigest() != refs[0]["sha256"]
            or len(trajectory_bytes) != refs[0]["size_bytes"]
        ):
            raise ContractValidationError("formal M5A selected trajectory bytes drifted")
        trajectory = load_replay_trajectory(trajectory_bytes)
        if trajectory.run_spec != prepared.run_spec or trajectory.task_id != prepared.task_id:
            raise ContractValidationError("formal M5A trajectory differs from selected RunSpec")
        selected.append(
            FormalTrajectory(
                prepared=prepared,
                source=source,
                attempt_path=attempt_path,
                attempt_sha256=hashlib.sha256(attempt_bytes).hexdigest(),
                trajectory_path=trajectory_path,
                trajectory_sha256=hashlib.sha256(trajectory_bytes).hexdigest(),
                trajectory=trajectory,
            )
        )
    if len(selected) != 160 or len(excluded) != 184:
        raise ContractValidationError("formal M5A selected/excluded exact-cover drifted")
    pair_counts: dict[tuple[object, ...], set[str]] = defaultdict(set)
    for item in selected:
        spec = item.prepared.run_spec
        key = (item.prepared.task_id, spec.model_id, spec.initial_sample_size, spec.seed)
        pair_counts[key].add("greedy" if spec.acquisition_id == _GREEDY else "exploration")
    if len(pair_counts) != 80 or any(
        value != {"greedy", "exploration"} for value in pair_counts.values()
    ):
        raise ContractValidationError("formal M5A 80 trajectory pairs drifted")

    inventory_value = {
        "excluded": [dict(item) for item in excluded],
        "schema_version": "m5a-formal-inventory-v1",
        "selected": [
            {
                "attempt_sha256": item.attempt_sha256,
                "run_id": item.prepared.run_spec.run_id,
                "run_spec": asdict(item.prepared.run_spec),
                "source": item.source,
                "task_id": item.prepared.task_id,
                "trajectory_sha256": item.trajectory_sha256,
            }
            for item in selected
        ],
    }
    inventory_bytes = compact_json_bytes(inventory_value)
    return FormalInventory(
        selected=tuple(selected),
        excluded=tuple(excluded),
        canonical_bytes=inventory_bytes,
        sha256=hashlib.sha256(inventory_bytes).hexdigest(),
    )


def ordinal_spearman(first: Sequence[str], second: Sequence[str]) -> float:
    if len(first) != len(second) or set(first) != set(second) or len(first) < 2:
        raise ContractValidationError("rankings must be equal non-trivial permutations")
    positions = {candidate_id: index + 1 for index, candidate_id in enumerate(second)}
    squared = sum(
        (index + 1 - positions[candidate_id]) ** 2 for index, candidate_id in enumerate(first)
    )
    count = len(first)
    return 1.0 - (6.0 * squared) / (count * (count * count - 1))


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _numeric_summary(values: Iterable[float]) -> dict[str, object]:
    materialized = [float(value) for value in values]
    if not materialized or any(not math.isfinite(value) for value in materialized):
        raise ContractValidationError("formal aggregate numeric group is empty or non-finite")
    return {
        "count": len(materialized),
        "maximum_binary64": binary64_hex(max(materialized)),
        "mean_binary64": binary64_hex(statistics.fmean(materialized)),
        "median_binary64": binary64_hex(_quantile(materialized, 0.5)),
        "minimum_binary64": binary64_hex(min(materialized)),
        "p25_binary64": binary64_hex(_quantile(materialized, 0.25)),
        "p75_binary64": binary64_hex(_quantile(materialized, 0.75)),
    }


def _row_group(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in fields)].append(row)
    output: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda value: tuple(str(item) for item in value)):
        items = groups[key]
        output.append(
            {
                "group": dict(zip(fields, key, strict=True)),
                "actual_exact_matches": sum(
                    bool(item["actual_policy_exact_match"]) for item in items
                ),
                "actual_exact_total": len(items),
                "divergence": _numeric_summary(
                    binary64_from_hex(item["divergence_binary64"]) for item in items
                ),
                "mean_acquisition_spearman": _numeric_summary(
                    binary64_from_hex(item["mean_acquisition_spearman_binary64"]) for item in items
                ),
                "selected_mean_ranks": _numeric_summary(
                    rank for item in items for rank in item["selected_mean_ranks"]
                ),
                "selected_uncertainty_ranks": _numeric_summary(
                    rank for item in items for rank in item["selected_uncertainty_ranks"]
                ),
                "top_k_jaccard": _numeric_summary(
                    binary64_from_hex(item["top_k_jaccard_binary64"]) for item in items
                ),
            }
        )
    return output


def _pearson(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second) or len(first) < 2:
        raise ContractValidationError("paired Pearson inputs are invalid")
    mean_first = statistics.fmean(first)
    mean_second = statistics.fmean(second)
    numerator = sum(
        (x - mean_first) * (y - mean_second) for x, y in zip(first, second, strict=True)
    )
    denominator = math.sqrt(
        sum((x - mean_first) ** 2 for x in first) * sum((y - mean_second) ** 2 for y in second)
    )
    return 0.0 if denominator == 0.0 else numerator / denominator


def build_formal_report(
    summary_payloads: Sequence[bytes],
    *,
    inventory_sha256: str,
    analysis_commit: str,
    analysis_tree: str,
    environment_digest: str,
) -> bytes:
    """Aggregate 160 trajectory summaries into one candidate-free mechanism report."""

    summaries: list[dict[str, Any]] = []
    for payload in summary_payloads:
        value = json.loads(payload)
        if not isinstance(value, dict) or compact_json_bytes(value) != payload:
            raise ContractValidationError("trajectory summary is not canonical")
        if value.get("schema_version") != TRAJECTORY_SUMMARY_SCHEMA:
            raise ContractValidationError("trajectory summary schema drifted")
        summaries.append(cast(dict[str, Any], value))
    if len(summaries) != 160:
        raise ContractValidationError("formal report requires exactly 160 trajectory summaries")
    identities = [
        (
            item["task_id"],
            item["model_id"],
            item["initial_sample_size"],
            item["seed"],
            item["policy"],
        )
        for item in summaries
    ]
    if len(set(identities)) != 160:
        raise ContractValidationError("formal trajectory summary identities are duplicated")
    rows = [
        {
            **round_value,
            "initial_sample_size": item["initial_sample_size"],
            "model_id": item["model_id"],
            "policy": item["policy"],
            "seed": item["seed"],
            "task_id": item["task_id"],
        }
        for item in summaries
        for round_value in item["rounds"]
    ]
    if len(rows) != 800:
        raise ContractValidationError(
            "formal report must exact-cover 800 historical decision states"
        )
    if not all(row["actual_policy_exact_match"] is True for row in rows):
        raise ContractValidationError("formal report contains an actual selection mismatch")

    paired: dict[tuple[object, ...], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        key = (
            row["task_id"],
            row["model_id"],
            row["initial_sample_size"],
            row["seed"],
            row["round"],
        )
        paired[key][row["policy"]] = row
    if len(paired) != 400 or any(
        set(value) != {"greedy", "exploration"} for value in paired.values()
    ):
        raise ContractValidationError("formal report trajectory pairing drifted")
    metric_ids = (
        "best_found_value",
        "high_value_discovery_recall",
        "simple_regret",
        "success_at_budget",
    )
    relations: list[dict[str, object]] = []
    for metric_id in metric_ids:
        divergences: list[float] = []
        differences: list[float] = []
        for value in paired.values():
            exploration = value["exploration"]
            greedy = value["greedy"]
            left = exploration["checkpoint_metrics"][metric_id]
            right = greedy["checkpoint_metrics"][metric_id]
            if left["status"] != "defined" or right["status"] != "defined":
                continue
            divergences.append(binary64_from_hex(exploration["divergence_binary64"]))
            differences.append(
                binary64_from_hex(left["value_binary64"])
                - binary64_from_hex(right["value_binary64"])
            )
        relations.append(
            {
                "divergence_metric_difference_pearson_binary64": binary64_hex(
                    _pearson(divergences, differences)
                ),
                "metric_difference": _numeric_summary(differences),
                "metric_id": metric_id,
                "paired_defined_count": len(differences),
            }
        )
    report = {
        "analysis_identity": {
            "environment_digest": environment_digest,
            "git_commit": analysis_commit,
            "git_tree": analysis_tree,
            "inventory_sha256": inventory_sha256,
        },
        "cohort": {
            "decision_states": 800,
            "excluded_successful_attempts": 184,
            "selected_trajectories": 160,
            "successful_attempt_inventory": 344,
            "trajectory_pairs": 80,
        },
        "paired_checkpoint_relation": relations,
        "schema_version": FORMAL_REPORT_SCHEMA,
        "summaries": {
            "by_cell": _row_group(
                rows,
                ("task_id", "model_id", "initial_sample_size", "policy", "round"),
            ),
            "by_initial_sample_size": _row_group(rows, ("initial_sample_size",)),
            "by_model": _row_group(rows, ("model_id",)),
            "by_policy": _row_group(rows, ("policy",)),
            "by_revealed_before": _row_group(rows, ("revealed_before",)),
            "by_round": _row_group(rows, ("round",)),
            "by_seed": _row_group(rows, ("seed",)),
            "by_task": _row_group(rows, ("task_id",)),
            "overall": _row_group(rows, ()),
        },
    }
    encoded = compact_json_bytes(report)
    if b"candidate_id" in encoded or b'"value":' in encoded:
        raise ContractValidationError("formal report leaks candidate IDs or raw labels")
    return encoded


__all__ = [
    "FORMAL_REPORT_SCHEMA",
    "TRAJECTORY_SUMMARY_SCHEMA",
    "FormalInventory",
    "FormalTrajectory",
    "binary64_from_hex",
    "binary64_hex",
    "build_formal_inventory",
    "build_formal_report",
    "load_formal_contract",
    "ordinal_spearman",
]
