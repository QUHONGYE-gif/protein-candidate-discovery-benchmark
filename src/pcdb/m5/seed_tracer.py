"""Zero-truth preparation and frozen statistics for the Issue #85 seed tracer."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Literal, cast

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.run import RunSpec
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet

PROTOCOL_ID = "m5-seed-source-tracer-v1"
TASKS = ("alpha-amylase", "ired")
MODEL_ID = "random-forest-sklearn-v1"
EXPLORATION_ID = "uncertainty-bootstrap-std-v1"
GREEDY_ID = "greedy-predictive-mean-v1"
INITIAL_SEEDS = tuple(range(1729, 1734))
INTERNAL_SEEDS = tuple(range(2719, 2724))
FIXED_INITIAL_SEED = 1729
FIXED_INTERNAL_SEED = 2719
ESTIMATOR_SEED = 1729
METRIC_IDS = ("high_value_discovery_recall", "simple_regret")
T_CRITICAL_N5 = Decimal("2.7764451051977987")

_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASE_EXPERIMENTS = {
    "alpha-amylase": Path("configs/experiments/m4-single-fidelity/alpha-amylase.json"),
    "ired": Path("configs/experiments/m4-single-fidelity/ired.json"),
}

Axis = Literal["initial", "internal"]
TracerVerdict = Literal[
    "INITIAL-DOMINATED",
    "INTERNAL-DOMINATED",
    "MIXED-INCONCLUSIVE",
    "DATA-INCOMPLETE",
]


@dataclass(frozen=True, slots=True)
class PairedEffect:
    """One direction-normalized Exploration-minus-Greedy paired effect."""

    task_id: str
    metric_id: str
    axis: Axis
    seed: int
    difference: Decimal


@dataclass(frozen=True, slots=True)
class EffectStats:
    """Frozen n=5 descriptive statistics for one task/metric/axis cell."""

    n: int
    mean: Decimal
    sample_sd: Decimal
    ci_lower: Decimal
    ci_upper: Decimal


@dataclass(frozen=True, slots=True)
class AxisComparison:
    """Two-axis dispersion comparison for one task and metric."""

    task_id: str
    metric_id: str
    initial: EffectStats
    internal: EffectStats
    initial_dominant: bool
    internal_dominant: bool


@dataclass(frozen=True, slots=True)
class SeedSourceResult:
    """One frozen four-way tracer result."""

    verdict: TracerVerdict
    comparisons: tuple[AxisComparison, ...]
    incomplete_reason: str | None = None


def paired_difference(*, metric_id: str, exploration: Decimal, greedy: Decimal) -> Decimal:
    """Apply the frozen direction convention to one metric pair."""

    if not exploration.is_finite() or not greedy.is_finite():
        raise ContractValidationError("paired tracer metrics must be finite Decimals")
    if metric_id == "high_value_discovery_recall":
        return exploration - greedy
    if metric_id == "simple_regret":
        return greedy - exploration
    raise ContractValidationError("paired tracer metric is outside the frozen primary set")


def _effect_stats(values: tuple[Decimal, ...]) -> EffectStats:
    if len(values) != 5 or any(not value.is_finite() for value in values):
        raise ContractValidationError("tracer statistics require five finite paired effects")
    with localcontext() as context:
        context.prec = 50
        count = Decimal(len(values))
        mean = sum(values, Decimal(0)) / count
        variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
        sample_sd = variance.sqrt()
        margin = T_CRITICAL_N5 * sample_sd / count.sqrt()
        return EffectStats(
            n=len(values),
            mean=+mean,
            sample_sd=+sample_sd,
            ci_lower=+(mean - margin),
            ci_upper=+(mean + margin),
        )


def classify_seed_sources(effects: list[PairedEffect]) -> SeedSourceResult:
    """Fail closed or classify the exact 2-task x 2-metric x 2-axis evidence."""

    expected = {
        (task_id, metric_id, axis, seed)
        for task_id in TASKS
        for metric_id in METRIC_IDS
        for axis, seeds in (("initial", INITIAL_SEEDS), ("internal", INTERNAL_SEEDS))
        for seed in seeds
    }
    observed: dict[tuple[str, str, str, int], Decimal] = {}
    for effect in effects:
        key = (effect.task_id, effect.metric_id, effect.axis, effect.seed)
        if key in observed:
            return SeedSourceResult("DATA-INCOMPLETE", (), "duplicate paired-effect cell")
        if key not in expected:
            return SeedSourceResult("DATA-INCOMPLETE", (), "unexpected paired-effect cell")
        if not isinstance(effect.difference, Decimal) or not effect.difference.is_finite():
            return SeedSourceResult("DATA-INCOMPLETE", (), "undefined or non-finite effect")
        observed[key] = effect.difference
    if set(observed) != expected:
        return SeedSourceResult("DATA-INCOMPLETE", (), "paired-effect exact cover is missing")

    comparisons: list[AxisComparison] = []
    for task_id in TASKS:
        for metric_id in METRIC_IDS:
            initial = _effect_stats(
                tuple(observed[(task_id, metric_id, "initial", seed)] for seed in INITIAL_SEEDS)
            )
            internal = _effect_stats(
                tuple(observed[(task_id, metric_id, "internal", seed)] for seed in INTERNAL_SEEDS)
            )
            comparisons.append(
                AxisComparison(
                    task_id=task_id,
                    metric_id=metric_id,
                    initial=initial,
                    internal=internal,
                    initial_dominant=(
                        initial.sample_sd > 0 and initial.sample_sd >= 2 * internal.sample_sd
                    ),
                    internal_dominant=(
                        internal.sample_sd > 0 and internal.sample_sd >= 2 * initial.sample_sd
                    ),
                )
            )
    initial_count = sum(item.initial_dominant for item in comparisons)
    internal_count = sum(item.internal_dominant for item in comparisons)
    verdict: TracerVerdict
    if initial_count >= 3:
        verdict = "INITIAL-DOMINATED"
    elif internal_count >= 3:
        verdict = "INTERNAL-DOMINATED"
    else:
        verdict = "MIXED-INCONCLUSIVE"
    return SeedSourceResult(verdict, tuple(comparisons))


def _v3_config_bytes(
    base: dict[str, Any],
    *,
    task_id: str,
    acquisition_id: str,
    initial_seed: int,
    internal_seed: int | None,
) -> bytes:
    policy = "uncertainty" if acquisition_id == EXPLORATION_ID else "greedy"
    value = {
        **base,
        "acquisitions": [acquisition_id],
        "batch_sizes": [16],
        "budgets": [128],
        "experiment_id": (
            f"m5-seed-tracer.{task_id}.{policy}.i{initial_seed}."
            f"r{internal_seed if internal_seed is not None else 'none'}"
        ),
        "held_out_authorized": False,
        "initial_sample_sizes": [32],
        "models": [MODEL_ID],
        "protocol_version": PROTOCOL_ID,
        "schema_version": "3",
        "seed_axes": {
            "estimator_seed": ESTIMATOR_SEED,
            "estimator_seed_binding": "frozen-model-config",
            "initial_seed": initial_seed,
            "initial_seed_binding": "run-spec.seed",
            "internal_seed": internal_seed,
            "internal_seed_binding": "experiment-config-sha256",
        },
        "seeds": [initial_seed],
    }
    payload = canonical_json_bytes(value)
    loaded = load_experiment_config(payload)
    if loaded.schema_version != "3" or loaded.value["task_id"] != task_id:
        raise ContractValidationError("derived tracer Experiment Config identity drifted")
    return payload


def build_seed_tracer_packet(
    repository_root: Path,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
) -> bytes:
    """Build the exact zero-truth 28-RunSpec inventory without starting an attempt."""

    if not _GIT_OBJECT.fullmatch(reviewed_git_commit) or not _GIT_OBJECT.fullmatch(
        reviewed_git_tree
    ):
        raise ContractValidationError("reviewed Git commit and tree must be lowercase 40-hex")
    root = repository_root.resolve()
    m4_packet_bytes = build_pilot_preflight_packet(
        root,
        reviewed_git_commit,
        reviewed_git_tree,
    )
    m4_packet = cast(dict[str, Any], json.loads(m4_packet_bytes))
    templates: dict[tuple[str, str], RunSpec] = {}
    for item in cast(list[dict[str, Any]], m4_packet["run_specs"]):
        raw_spec = cast(dict[str, Any], item["run_spec"])
        if (
            raw_spec["model_id"] == MODEL_ID
            and raw_spec["acquisition_id"] in {EXPLORATION_ID, GREEDY_ID}
            and raw_spec["seed"] == FIXED_INITIAL_SEED
        ):
            templates[(str(item["task_id"]), str(raw_spec["acquisition_id"]))] = RunSpec(**raw_spec)
    if set(templates) != {
        (task_id, acquisition_id)
        for task_id in TASKS
        for acquisition_id in (EXPLORATION_ID, GREEDY_ID)
    }:
        raise ContractValidationError("reviewed M4 tracer templates are incomplete")

    base_configs: dict[str, dict[str, Any]] = {}
    for task_id, path in _BASE_EXPERIMENTS.items():
        payload = (root / path).read_bytes()
        loaded = load_experiment_config(payload)
        if loaded.schema_version != "2" or loaded.value["task_id"] != task_id:
            raise ContractValidationError("reviewed M4 Experiment Config binding drifted")
        base_configs[task_id] = cast(dict[str, Any], json.loads(payload))

    run_specs: list[dict[str, object]] = []
    config_inventory: list[dict[str, object]] = []

    def append_run(
        *,
        task_id: str,
        axis: Axis,
        acquisition_id: str,
        initial_seed: int,
        internal_seed: int | None,
    ) -> None:
        config = _v3_config_bytes(
            base_configs[task_id],
            task_id=task_id,
            acquisition_id=acquisition_id,
            initial_seed=initial_seed,
            internal_seed=internal_seed,
        )
        config_sha256 = hashlib.sha256(config).hexdigest()
        template = templates[(task_id, acquisition_id)]
        run_spec = replace(
            template,
            protocol_version=PROTOCOL_ID,
            experiment_config_sha256=config_sha256,
            seed=initial_seed,
        )
        run_specs.append(
            {
                "axis": axis,
                "run_id": run_spec.run_id,
                "run_spec": asdict(run_spec),
                "run_spec_canonical_json_utf8": run_spec.canonical_json(),
                "task_id": task_id,
            }
        )
        config_inventory.append(
            {
                "acquisition_id": acquisition_id,
                "canonical_json_utf8": config.decode("utf-8"),
                "experiment_config_sha256": config_sha256,
                "initial_seed": initial_seed,
                "internal_seed": internal_seed,
                "size_bytes": len(config),
                "task_id": task_id,
            }
        )

    for task_id in TASKS:
        for initial_seed in INITIAL_SEEDS:
            append_run(
                task_id=task_id,
                axis="initial",
                acquisition_id=EXPLORATION_ID,
                initial_seed=initial_seed,
                internal_seed=FIXED_INTERNAL_SEED,
            )
            append_run(
                task_id=task_id,
                axis="initial",
                acquisition_id=GREEDY_ID,
                initial_seed=initial_seed,
                internal_seed=None,
            )
        for internal_seed in INTERNAL_SEEDS[1:]:
            append_run(
                task_id=task_id,
                axis="internal",
                acquisition_id=EXPLORATION_ID,
                initial_seed=FIXED_INITIAL_SEED,
                internal_seed=internal_seed,
            )
    if (
        len(run_specs) != 28
        or len({str(item["run_id"]) for item in run_specs}) != 28
        or len({str(item["experiment_config_sha256"]) for item in config_inventory}) != 28
    ):
        raise ContractValidationError("seed tracer inventory is not an exact 28-run set")

    m4_config_inventory = cast(
        dict[str, list[dict[str, object]]], m4_packet["closed_config_inventory"]
    )
    reviewed_identity_inventory = {
        "acquisitions": [
            item
            for item in m4_config_inventory["acquisitions"]
            if item["acquisition_id"] in {EXPLORATION_ID, GREEDY_ID}
        ],
        "environment_candidate": m4_packet["environment_candidate"],
        "models": [item for item in m4_config_inventory["models"] if item["model_id"] == MODEL_ID],
        "source_artifacts": m4_packet["source_artifacts"],
        "source_experiments": m4_config_inventory["experiments"],
    }

    packet = {
        "authorization_boundaries": {
            "cos_access_authorized": False,
            "packet_review_authorizes_execution": False,
            "paid_compute_authorized": False,
            "run_spec_attempts_authorized": False,
            "server_execution_authorized": False,
            "truth_read_authorized": False,
        },
        "experiment_configs": config_inventory,
        "initial_resource_ledger": {
            "active_execution_wall_seconds": 0,
            "attempts_started": 0,
            "network_transfer_bytes": 0,
            "peak_rss_bytes": 0,
            "scratch_high_water_bytes": 0,
            "unique_run_ids_started": [],
        },
        "issue_number": 85,
        "matrix": {
            "axis_a_runs": 20,
            "axis_b_additional_runs": 8,
            "expected_unique_run_specs": 28,
            "fixed_estimator_seed": ESTIMATOR_SEED,
            "fixed_initial_seed": FIXED_INITIAL_SEED,
            "fixed_internal_seed": FIXED_INTERNAL_SEED,
            "initial_seeds": list(INITIAL_SEEDS),
            "internal_seeds": list(INTERNAL_SEEDS),
            "matrix_order": ["task_id", "axis", "seed", "policy"],
        },
        "resource_accounting": {
            "active_execution_wall_scope": "one-28-run-batch-cumulative",
            "guard_after_each_attempt": True,
            "guard_before_each_attempt": True,
            "network_transfer_scope": "one-28-run-batch-cumulative",
            "peak_rss_scope": "maximum-over-all-batch-attempt-processes",
            "scratch_scope": "one-batch-root-high-water",
        },
        "resource_caps": {
            "active_execution_wall_seconds": 900,
            "automatic_retry": 0,
            "delete": 0,
            "network_transfer_bytes": 32 * 1024**2,
            "peak_rss_bytes": 1024**3,
            "replacement_attempts": 0,
            "scratch_high_water_bytes": 16 * 1024**2,
            "unique_run_specs_and_attempts": 28,
        },
        "reviewed_identity_inventory": reviewed_identity_inventory,
        "reviewed_source": {
            "git_commit": reviewed_git_commit,
            "git_tree": reviewed_git_tree,
            "m4_source_packet_sha256": hashlib.sha256(m4_packet_bytes).hexdigest(),
        },
        "run_specs": run_specs,
        "schema_version": "m5-seed-source-tracer-packet-v1",
        "truth_boundary": {
            "candidate_ids_in_packet": False,
            "held_out_read_authorized": False,
            "packet_build_truth_reads": 0,
            "raw_labels_in_packet": False,
            "runtime_oracle_binding_required": True,
        },
    }
    return canonical_json_bytes(packet)


def load_seed_tracer_packet(
    packet_bytes: bytes,
    *,
    expected_sha256: str,
    repository_root: Path,
    expected_git_commit: str,
    expected_git_tree: str,
) -> dict[str, Any]:
    """Strict-load and reproduce the zero-truth tracer packet."""

    if (
        not _SHA256.fullmatch(expected_sha256)
        or hashlib.sha256(packet_bytes).hexdigest() != expected_sha256
    ):
        raise ContractValidationError("seed tracer packet identity differs")
    try:
        value = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("seed tracer packet must be canonical JSON") from None
    if not isinstance(value, dict) or canonical_json_bytes(value) != packet_bytes:
        raise ContractValidationError("seed tracer packet must be canonical JSON")
    expected = build_seed_tracer_packet(
        repository_root,
        expected_git_commit,
        expected_git_tree,
    )
    if expected != packet_bytes:
        raise ContractValidationError("seed tracer packet differs from repository bytes")
    return cast(dict[str, Any], value)


__all__ = [
    "AxisComparison",
    "EffectStats",
    "PairedEffect",
    "SeedSourceResult",
    "build_seed_tracer_packet",
    "classify_seed_sources",
    "load_seed_tracer_packet",
    "paired_difference",
]
