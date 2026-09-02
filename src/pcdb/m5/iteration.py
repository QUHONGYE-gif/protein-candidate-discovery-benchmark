"""Thin end-to-end adapter for the bounded M5B development iteration."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError
from pcdb.m5.formal_reconstruction import FormalTrajectory, binary64_hex
from pcdb.m5.inventory_preflight import (
    ACCEPTED_M5A_INVENTORY_SHA256,
    ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
    extract_first_decision_state_payloads,
)
from pcdb.m5.reconstruction_smoke import compact_json_bytes
from pcdb.m5.same_state import (
    BranchOutcome,
    StateEffects,
    TaskTaxonomyInput,
    bootstrap_task_effects,
    build_state_inventory,
    classify_taxonomy,
    matched_random_batch,
)
from pcdb.replay.engine import ImmediateBatchEvaluator

_FRACTIONS = (0.01, 0.005, 0.02)
_TASKS = ("alpha-amylase", "ired")
_BRANCH_IDS = ("E", "G", *(f"M{index:02d}" for index in range(64)))
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ATTEMPT_ID = "attempt-001"
_SCIENTIFIC_PARENT = {
    "execution_commit": "f75382ae6f90267b47c4379fc58b75b61a1da885",
    "execution_tree": "fb93c07b772c49939695647c8260aa837047539d",
    "formal_contract_sha256": ("2d04187264ba73f553daae461ee7d7780608c7c2a56ee850667fae41995bac9d"),
    "inventory_sha256": ACCEPTED_M5A_INVENTORY_SHA256,
    "report_sha256": ("b199a483fd9e7a375d672e8ecf7ec5075248b2c5a729dfbc47e73fdae5641943"),
    "source_environment_digest": ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
}


@dataclass(frozen=True, slots=True)
class DevelopmentTruth:
    """Identity-bound development truth retained inside the evaluator boundary."""

    task_id: str
    rows: tuple[tuple[str, float], ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class IterationEvaluation:
    """Aggregate-only M5B result before canonical report encoding."""

    inventory_sha256: str
    primary_effects: tuple[StateEffects, ...]
    sensitivity_effects: MappingProxyType[float, tuple[StateEffects, ...]]
    consistency_effects: MappingProxyType[str, tuple[StateEffects, ...]]
    difference_counts: MappingProxyType[str, tuple[int, ...]]
    high_value_counts: MappingProxyType[str, int]
    truth_sha256: MappingProxyType[str, str]


@dataclass(frozen=True, slots=True)
class IterationAuthorization:
    """One exact authority covering validation and development truth evaluation."""

    value: MappingProxyType[str, Any]
    canonical_bytes: bytes
    sha256: str


def _mapping(value: object, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractValidationError(f"M5B iteration {name} fields drifted")
    return cast(dict[str, Any], value)


def _path(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value in {"/", "UNKNOWN"}:
        raise ContractValidationError(f"M5B iteration {name} path is invalid")
    if ".." in value.split("/"):
        raise ContractValidationError(f"M5B iteration {name} path is invalid")
    return value


def load_iteration_authorization(payload: bytes) -> IterationAuthorization:
    """Load all source, truth, runtime, budget and attempt authority before input access."""

    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(
            "M5B iteration authorization must be canonical JSON"
        ) from None
    fields = {
        "approval",
        "automatic_retries",
        "authoritative_parent",
        "resource_caps",
        "reviewed_source",
        "runner",
        "runtime_environment_digest",
        "schema_version",
        "scientific_parent",
        "study",
        "truth_boundary",
        "truth_inputs",
    }
    if (
        not isinstance(value, dict)
        or compact_json_bytes(value) != payload
        or set(value) != fields
        or value["schema_version"] != "m5b-bounded-iteration-authorization-v1"
    ):
        raise ContractValidationError("M5B iteration authorization fields drifted")
    value = cast(dict[str, Any], value)
    approval = _mapping(value["approval"], {"comment_id", "verdict"}, "approval")
    if (
        type(approval["comment_id"]) is not int
        or approval["comment_id"] <= 0
        or approval["verdict"] != "AUTHORIZE_M5B_BOUNDED_ITERATION_V1"
        or type(value["automatic_retries"]) is not int
        or value["automatic_retries"] != 0
    ):
        raise ContractValidationError("M5B iteration approval or retry binding is invalid")
    parent_fields = {
        "conditional_source_root",
        "conditional_store_root",
        "feature_root",
        "formal_attempt_root",
        "formal_contract_path",
        "pilot_source_root",
        "pilot_store_root",
    }
    parent = _mapping(value["authoritative_parent"], parent_fields, "parent")
    for field in parent_fields:
        _path(parent[field], f"parent {field}")
    reviewed = _mapping(value["reviewed_source"], {"git_commit", "git_tree"}, "source")
    if not _GIT_COMMIT.fullmatch(reviewed["git_commit"]) or not _GIT_COMMIT.fullmatch(
        reviewed["git_tree"]
    ):
        raise ContractValidationError("M5B iteration reviewed source is invalid")
    runner = _mapping(value["runner"], {"identity", "worker_command"}, "runner")
    command = runner["worker_command"]
    if (
        runner["identity"] != "scripts/m5b_iteration_worker.py-v1"
        or not isinstance(command, list)
        or len(command) != 2
    ):
        raise ContractValidationError("M5B iteration runner binding is invalid")
    for item in command:
        _path(item, "worker command")
    runtime = value["runtime_environment_digest"]
    if not isinstance(runtime, str) or not _SHA256.fullmatch(runtime):
        raise ContractValidationError("M5B iteration runtime identity is invalid")
    caps = _mapping(
        value["resource_caps"],
        {
            "cpu_seconds",
            "estimated_hourly_cost_cny",
            "maximum_cost_cny",
            "process_tree_peak_rss_bytes",
            "scratch_high_water_bytes",
            "wall_seconds",
        },
        "resource caps",
    )
    if any(
        type(caps[field]) is not int or caps[field] <= 0
        for field in (
            "cpu_seconds",
            "process_tree_peak_rss_bytes",
            "scratch_high_water_bytes",
            "wall_seconds",
        )
    ):
        raise ContractValidationError("M5B iteration resource cap is invalid")
    try:
        hourly = float(caps["estimated_hourly_cost_cny"])
        maximum = float(caps["maximum_cost_cny"])
    except (TypeError, ValueError):
        raise ContractValidationError("M5B iteration cost cap is invalid") from None
    if not math.isfinite(hourly) or not math.isfinite(maximum) or hourly <= 0 or maximum <= 0:
        raise ContractValidationError("M5B iteration cost cap is invalid")
    if _mapping(value["scientific_parent"], set(_SCIENTIFIC_PARENT), "parent") != (
        _SCIENTIFIC_PARENT
    ):
        raise ContractValidationError("M5B iteration scientific parent drifted")
    study = _mapping(value["study"], {"attempt_id", "study_root"}, "study")
    if study["attempt_id"] != _ATTEMPT_ID:
        raise ContractValidationError("M5B iteration attempt ID is invalid")
    _path(study["study_root"], "study root")
    boundary = _mapping(
        value["truth_boundary"],
        {
            "cos_publication_writes",
            "development_truth_files",
            "held_out_reads",
            "network_bytes",
        },
        "truth boundary",
    )
    expected_boundary = {
        "cos_publication_writes": 0,
        "development_truth_files": 2,
        "held_out_reads": 0,
        "network_bytes": 0,
    }
    if boundary != expected_boundary or any(type(item) is not int for item in boundary.values()):
        raise ContractValidationError("M5B iteration truth boundary drifted")
    truth_inputs = _mapping(value["truth_inputs"], set(_TASKS), "truth inputs")
    for task_id in _TASKS:
        item = _mapping(
            truth_inputs[task_id],
            {
                "hidden_truth_path",
                "hidden_truth_sha256",
                "processing_manifest_path",
            },
            f"{task_id} truth input",
        )
        _path(item["hidden_truth_path"], "development truth")
        _path(item["processing_manifest_path"], "processing manifest")
        if not isinstance(item["hidden_truth_sha256"], str) or not _SHA256.fullmatch(
            item["hidden_truth_sha256"]
        ):
            raise ContractValidationError("M5B iteration truth identity is invalid")
    canonical = bytes(payload)
    return IterationAuthorization(
        value=MappingProxyType(value),
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _parse_truth(payload: bytes) -> tuple[tuple[str, float], ...]:
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True)
    except UnicodeDecodeError as exc:
        raise ContractValidationError("M5B development truth must be UTF-8 CSV") from exc
    if reader.fieldnames != ["candidate_id", "target"]:
        raise ContractValidationError("M5B development truth fields drifted")
    values: dict[str, float] = {}
    try:
        for row in reader:
            candidate_id = row.get("candidate_id")
            raw_target = row.get("target")
            if (
                set(row) != {"candidate_id", "target"}
                or None in row
                or not isinstance(candidate_id, str)
                or len(candidate_id) != 64
                or any(character not in "0123456789abcdef" for character in candidate_id)
                or candidate_id in values
                or not isinstance(raw_target, str)
            ):
                raise ContractValidationError("M5B development truth row is invalid")
            try:
                target = float(raw_target)
            except ValueError as exc:
                raise ContractValidationError("M5B development truth target is invalid") from exc
            if not math.isfinite(target):
                raise ContractValidationError("M5B development truth target is invalid")
            values[candidate_id] = target
    except csv.Error as exc:
        raise ContractValidationError("M5B development truth CSV is invalid") from exc
    if not values:
        raise ContractValidationError("M5B development truth is empty")
    return tuple(sorted(values.items()))


def load_development_truth(
    *,
    task_id: str,
    processing_manifest: bytes,
    expected_processing_manifest_sha256: str,
    hidden_truth: bytes,
    expected_hidden_truth_sha256: str,
    candidate_ids: Sequence[str],
) -> DevelopmentTruth:
    """Load exact development truth without admitting held-out or unbound bytes."""

    if task_id not in _TASKS:
        raise ContractValidationError("M5B development truth task is invalid")
    if hashlib.sha256(processing_manifest).hexdigest() != expected_processing_manifest_sha256:
        raise ContractValidationError("M5B processing manifest identity drifted")
    if hashlib.sha256(hidden_truth).hexdigest() != expected_hidden_truth_sha256:
        raise ContractValidationError("M5B development truth identity drifted")
    try:
        manifest = json.loads(processing_manifest)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M5B processing manifest is invalid") from None
    outputs = manifest.get("output_files") if isinstance(manifest, dict) else None
    references = [
        item
        for item in outputs or ()
        if isinstance(item, dict)
        and isinstance(item.get("object_key"), str)
        and item["object_key"].endswith("/hidden-truth.csv")
    ]
    if (
        manifest.get("candidate_id_field") != "candidate_id"
        or manifest.get("candidate_id_unique") is not True
        or manifest.get("hidden_label_field") != "target"
        or manifest.get("label_direction") != "higher_is_better"
        or len(references) != 1
        or references[0].get("sha256") != expected_hidden_truth_sha256
        or references[0].get("size_bytes") != len(hidden_truth)
    ):
        raise ContractValidationError("M5B development truth manifest binding drifted")
    rows = _parse_truth(hidden_truth)
    candidate_pool = tuple(candidate_ids)
    if (
        not candidate_pool
        or len(set(candidate_pool)) != len(candidate_pool)
        or any(not isinstance(candidate_id, str) for candidate_id in candidate_pool)
    ):
        raise ContractValidationError("M5B candidate universe is invalid")
    full_truth = dict(rows)
    if not set(candidate_pool) <= set(full_truth):
        raise ContractValidationError("M5B development truth does not cover candidate universe")
    pool_rows = tuple(
        sorted((candidate_id, full_truth[candidate_id]) for candidate_id in candidate_pool)
    )
    return DevelopmentTruth(
        task_id=task_id,
        rows=pool_rows,
        sha256=expected_hidden_truth_sha256,
    )


class _TruthContext:
    def __init__(self, truth: DevelopmentTruth) -> None:
        self.evaluator = ImmediateBatchEvaluator(dict(truth.rows), _FRACTIONS)
        self.definition_counts = self.evaluator.definition_counts


class _BranchEvaluator:
    def __init__(self, context: _TruthContext) -> None:
        self._context = context

    def evaluate(
        self,
        *,
        observation_prefix_ids: tuple[str, ...],
        selected_ids: frozenset[str],
    ) -> MappingProxyType[float, BranchOutcome]:
        metrics = self._context.evaluator.evaluate(
            observation_prefix_ids=observation_prefix_ids,
            selected_ids=tuple(sorted(selected_ids)),
        )
        outcomes = {
            fraction: BranchOutcome(
                actual_high_value_count=item.actual_high_value_count,
                best_found_improvement=item.best_found_improvement,
                high_value_recall_increment=item.high_value_recall_increment,
                simple_regret_reduction=item.simple_regret_reduction,
            )
            for fraction, item in metrics.items()
        }
        return MappingProxyType(outcomes)


def _evaluate_branches(
    *,
    context: _TruthContext,
    prefix: tuple[str, ...],
    batches: Mapping[str, frozenset[str]],
) -> MappingProxyType[str, MappingProxyType[float, BranchOutcome]]:
    if set(batches) != set(_BRANCH_IDS):
        raise ContractValidationError("M5B iteration branches do not exact-cover E/G/M")
    results: dict[str, MappingProxyType[float, BranchOutcome]] = {}
    evaluators: list[_BranchEvaluator] = []
    for branch_id in _BRANCH_IDS:
        batch = batches[branch_id]
        if len(batch) != 16 or not batch.isdisjoint(prefix):
            raise ContractValidationError("M5B iteration branch batch is invalid")
        evaluator = _BranchEvaluator(context)
        evaluators.append(evaluator)
        results[branch_id] = evaluator.evaluate(
            observation_prefix_ids=prefix,
            selected_ids=batch,
        )
    if len({id(item) for item in evaluators}) != 66:
        raise ContractValidationError("M5B iteration evaluators are not branch-local")
    return MappingProxyType(results)


def evaluate_iteration(
    selected: Sequence[FormalTrajectory],
    *,
    candidate_ids_by_task: Mapping[str, Sequence[str]],
    truths: Mapping[str, DevelopmentTruth],
) -> IterationEvaluation:
    """Validate the 80-state parent and run the bounded immediate-batch intervention."""

    payloads = extract_first_decision_state_payloads(
        selected,
        candidate_ids_by_task=candidate_ids_by_task,
        parent_inventory_sha256=ACCEPTED_M5A_INVENTORY_SHA256,
        source_environment_digest=ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
    )
    inventory = build_state_inventory(payloads)
    if set(truths) != set(_TASKS):
        raise ContractValidationError("M5B development truth task coverage drifted")
    contexts = {task_id: _TruthContext(truths[task_id]) for task_id in _TASKS}
    grouped: defaultdict[tuple[str, str, int, int], dict[str, FormalTrajectory]] = defaultdict(dict)
    for item in selected:
        spec = item.prepared.run_spec
        key = (item.prepared.task_id, spec.model_id, spec.initial_sample_size, spec.seed)
        policy = "greedy" if spec.acquisition_id == "greedy-predictive-mean-v1" else "exploration"
        grouped[key][policy] = item
    state_ids = {
        (
            cast(str, state.payload["task_id"]),
            cast(str, state.payload["model_id"]),
            cast(int, state.payload["initial_sample_size"]),
            cast(int, state.payload["run_seed"]),
        ): state.state_id
        for state in inventory.states
    }
    effects: dict[float, list[StateEffects]] = {fraction: [] for fraction in _FRACTIONS}
    consistency_effects: dict[str, list[StateEffects]] = {
        "best_found_improvement": [],
        "simple_regret_reduction": [],
    }
    difference_counts: dict[str, list[int]] = {task_id: [] for task_id in _TASKS}
    for key in sorted(grouped):
        task_id, model_id, initial_size, run_seed = key
        pair = grouped[key]
        exploration = pair["exploration"].trajectory.events
        greedy = pair["greedy"].trajectory.events
        prefix = tuple(item.candidate_id for item in exploration[0].observations)
        exploration_ids = tuple(exploration[1].selected_candidate_ids)
        greedy_ids = tuple(greedy[1].selected_candidate_ids)
        remaining = tuple(
            candidate_id
            for candidate_id in candidate_ids_by_task[task_id]
            if candidate_id not in set(prefix)
        )
        batches = {
            "E": frozenset(exploration_ids),
            "G": frozenset(greedy_ids),
            **{
                f"M{replicate:02d}": matched_random_batch(
                    state_id=state_ids[key],
                    greedy_ids=greedy_ids,
                    exploration_ids=exploration_ids,
                    remaining_pool_ids=remaining,
                    replicate=replicate,
                ).selected_ids
                for replicate in range(64)
            },
        }
        outcomes = _evaluate_branches(
            context=contexts[task_id],
            prefix=prefix,
            batches=batches,
        )
        difference_counts[task_id].append(16 - len(batches["E"] & batches["G"]))
        for fraction in _FRACTIONS:
            exploration_value = outcomes["E"][fraction].high_value_recall_increment
            effects[fraction].append(
                StateEffects(
                    exploration_minus_greedy=(
                        exploration_value - outcomes["G"][fraction].high_value_recall_increment
                    ),
                    exploration_minus_matched=tuple(
                        exploration_value
                        - outcomes[f"M{replicate:02d}"][fraction].high_value_recall_increment
                        for replicate in range(64)
                    ),
                    initial_sample_size=initial_size,
                    model_id=model_id,
                    run_seed=run_seed,
                    state_id=state_ids[key],
                    task_id=task_id,
                )
            )
        for metric_id in consistency_effects:
            exploration_value = cast(float, getattr(outcomes["E"][0.01], metric_id))
            consistency_effects[metric_id].append(
                StateEffects(
                    exploration_minus_greedy=(
                        exploration_value - cast(float, getattr(outcomes["G"][0.01], metric_id))
                    ),
                    exploration_minus_matched=tuple(
                        exploration_value
                        - cast(
                            float,
                            getattr(outcomes[f"M{replicate:02d}"][0.01], metric_id),
                        )
                        for replicate in range(64)
                    ),
                    initial_sample_size=initial_size,
                    model_id=model_id,
                    run_seed=run_seed,
                    state_id=state_ids[key],
                    task_id=task_id,
                )
            )
    return IterationEvaluation(
        inventory_sha256=inventory.sha256,
        primary_effects=tuple(effects[0.01]),
        sensitivity_effects=MappingProxyType(
            {fraction: tuple(effects[fraction]) for fraction in (0.005, 0.02)}
        ),
        consistency_effects=MappingProxyType(
            {metric_id: tuple(values) for metric_id, values in consistency_effects.items()}
        ),
        difference_counts=MappingProxyType(
            {task_id: tuple(values) for task_id, values in difference_counts.items()}
        ),
        high_value_counts=MappingProxyType(
            {task_id: contexts[task_id].definition_counts[0.01] for task_id in _TASKS}
        ),
        truth_sha256=MappingProxyType({task_id: truths[task_id].sha256 for task_id in _TASKS}),
    )


def _task_point(effects: Sequence[StateEffects], task_id: str, field: str) -> float:
    selected = [item for item in effects if item.task_id == task_id]
    if len(selected) != 40:
        raise ContractValidationError("M5B sensitivity effects do not exact-cover task")
    if field == "E-G":
        return math.fsum(item.exploration_minus_greedy for item in selected) / 40
    return math.fsum(math.fsum(item.exploration_minus_matched) / 64 for item in selected) / 40


def build_iteration_report(evaluation: IterationEvaluation) -> bytes:
    """Encode one aggregate-only scientific terminal for independent validity review."""

    summaries = bootstrap_task_effects(evaluation.primary_effects)
    summary_map = {(item.task_id, item.contrast): item for item in summaries}
    taxonomy_inputs = tuple(
        TaskTaxonomyInput(
            actual_high_value_count=evaluation.high_value_counts[task_id],
            exploration_greedy_difference_counts=evaluation.difference_counts[task_id],
            frozen_candidate_count=2262 if task_id == "alpha-amylase" else 2070,
            primary_e_g=summary_map[(task_id, "E-G")],
            primary_e_m=summary_map[(task_id, "E-M")],
            required_defined=True,
            sensitivity_e_g_point_estimates=tuple(
                _task_point(evaluation.sensitivity_effects[fraction], task_id, "E-G")
                for fraction in (0.005, 0.02)
            ),
            sensitivity_e_m_point_estimates=tuple(
                _task_point(evaluation.sensitivity_effects[fraction], task_id, "E-M")
                for fraction in (0.005, 0.02)
            ),
            task_id=task_id,
        )
        for task_id in _TASKS
    )
    taxonomy = classify_taxonomy(taxonomy_inputs, validity_ok=True)

    def encoded_summary(item: Any) -> dict[str, object]:
        value = asdict(item)
        for field in ("cell_point_estimates", "ci_lower", "ci_upper", "point_estimate"):
            raw = value[field]
            value[field] = (
                [binary64_hex(item) for item in raw]
                if isinstance(raw, tuple)
                else binary64_hex(raw)
            )
        return cast(dict[str, object], value)

    consistency_summaries = {
        metric_id: [
            {
                "contrasts": {
                    "E-G": binary64_hex(
                        _task_point(evaluation.consistency_effects[metric_id], task_id, "E-G")
                    ),
                    "E-M": binary64_hex(
                        _task_point(evaluation.consistency_effects[metric_id], task_id, "E-M")
                    ),
                },
                "task_id": task_id,
            }
            for task_id in _TASKS
        ]
        for metric_id in ("best_found_improvement", "simple_regret_reduction")
    }

    report = {
        "branch_evaluations": 80 * 66,
        "consistency_summaries": consistency_summaries,
        "development_truth_files_read": 2,
        "held_out_reads": 0,
        "inventory_sha256": evaluation.inventory_sha256,
        "network_bytes": 0,
        "overall_status": taxonomy.overall_status,
        "schema_version": "m5b-bounded-iteration-result-v1",
        "selected_candidate_reveals": 80 * 66 * 16,
        "summaries": [encoded_summary(item) for item in summaries],
        "task_statuses": dict(taxonomy.task_statuses),
        "truth_sha256": dict(evaluation.truth_sha256),
    }
    payload = compact_json_bytes(report)
    if any(token in payload for token in (b"candidate_id", b"target", b"membership", b"label")):
        raise ContractValidationError("M5B iteration report leaks truth-bearing detail")
    return payload
