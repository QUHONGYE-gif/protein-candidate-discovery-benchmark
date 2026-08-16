"""Truth-free materialization for exact-cover M4 task-seed aggregates."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pcdb.contracts.experiment import (
    ContractValidationError,
    LoadedContract,
    canonical_json_bytes,
    load_high_value_result,
)
from pcdb.contracts.high_value import (
    MetricResultEvidence,
    validate_metric_result_evidence,
    validate_task_seed_aggregate,
)
from pcdb.contracts.run import RunSpec

_PILOT_TASKS = ("alpha-amylase", "ired")
_PILOT_MODELS = (
    "ridge-sklearn-v1",
    "random-forest-sklearn-v1",
    "xgboost-cpu-v1",
)
_PILOT_ACQUISITIONS = (
    "random-seeded-sha256-v1",
    "greedy-predictive-mean-v1",
    "uncertainty-bootstrap-std-v1",
    "ucb-bootstrap-std-beta1-v1",
)
_EXPLORATION_ACQUISITIONS = _PILOT_ACQUISITIONS[2:]
_PILOT_SEEDS = (1729, 1730, 1731)
_DEFINITION_IDS = (
    "upper-tail-1pct",
    "upper-tail-0.5pct",
    "upper-tail-2pct",
)


@dataclass(frozen=True, slots=True)
class PilotRunRecall:
    """Truth-free final-budget recalls bound to one immutable pilot trajectory."""

    task_id: str
    run_spec: RunSpec
    trajectory_sha256: str
    recalls: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class PilotTaskDecision:
    """One task/model/exploration application of the frozen three-seed rule."""

    task_id: str
    model_id: str
    acquisition_id: str
    positive_primary_seeds: int
    primary_mean_difference: float
    sensitivity_mean_differences: tuple[tuple[str, float], ...]
    status: str


@dataclass(frozen=True, slots=True)
class PilotModelDecision:
    """Deterministic selection of at most one exploration for one model."""

    model_id: str
    selected_acquisition_id: str | None
    selection_status: str
    eligible_acquisition_ids: tuple[str, ...]
    eliminated_acquisition_ids: tuple[str, ...]
    task_decisions: tuple[PilotTaskDecision, ...]


@dataclass(frozen=True, slots=True)
class PilotScreeningDecision:
    """The pilot's only allowed next state: expand, stop, or incomplete failure."""

    status: str
    selected_explorations: tuple[tuple[str, str], ...]
    model_decisions: tuple[PilotModelDecision, ...]
    failure_reason: str | None


def _defined_metric_value(value: object, *, metric_id: str) -> float | bool:
    if not isinstance(value, dict) or set(value) != {"status", "undefined_reason", "value"}:
        raise ContractValidationError("M4 pilot definition metric fields drifted")
    metric_value = value.get("value")
    if value.get("status") != "defined" or value.get("undefined_reason") is not None:
        raise ContractValidationError("M4 pilot final definition metric is undefined")
    if metric_id == "success_at_budget":
        if not isinstance(metric_value, bool):
            raise ContractValidationError("M4 pilot success_at_budget metric is invalid")
        return metric_value
    if (
        isinstance(metric_value, bool)
        or not isinstance(metric_value, (int, float))
        or not math.isfinite(float(metric_value))
    ):
        raise ContractValidationError("M4 pilot numeric definition metric is invalid")
    return float(metric_value)


def load_pilot_run_recall(
    payload: bytes,
    *,
    run_spec: RunSpec,
    expected_task_id: str,
    expected_packet_sha256: str,
    expected_trajectory_sha256: str,
) -> PilotRunRecall:
    """Strict-load one local non-canonical definition record without exposing membership."""

    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M4 pilot definition record must be canonical JSON") from None
    fields = {
        "attempt_id",
        "budget",
        "definitions",
        "packet_sha256",
        "record_type",
        "run_id",
        "task_id",
        "trajectory_sha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or canonical_json_bytes(value) != payload
    ):
        raise ContractValidationError("M4 pilot definition record fields drifted")
    task_id = value.get("task_id")
    trajectory_sha256 = value.get("trajectory_sha256")
    if (
        value.get("record_type") != "m4-pilot-final-definition-metrics-v1"
        or value.get("attempt_id") != "attempt-001"
        or value.get("packet_sha256") != expected_packet_sha256
        or value.get("run_id") != run_spec.run_id
        or value.get("budget") != run_spec.budget
        or task_id != expected_task_id
        or expected_task_id not in _PILOT_TASKS
        or not isinstance(trajectory_sha256, str)
        or trajectory_sha256 != expected_trajectory_sha256
        or len(expected_trajectory_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_trajectory_sha256)
        or run_spec.protocol_version != "m4-single-fidelity-screening-v1"
        or run_spec.model_id not in _PILOT_MODELS
        or run_spec.acquisition_id not in _PILOT_ACQUISITIONS
        or run_spec.seed not in _PILOT_SEEDS
        or (run_spec.initial_sample_size, run_spec.batch_size, run_spec.budget) != (32, 16, 128)
    ):
        raise ContractValidationError("M4 pilot definition record identity drifted")
    definitions = value.get("definitions")
    if (
        not isinstance(definitions, list)
        or len(definitions) != 3
        or any(not isinstance(item, dict) for item in definitions)
        or tuple(item.get("definition_id") for item in definitions) != _DEFINITION_IDS
    ):
        raise ContractValidationError("M4 pilot definition metric set drifted")
    recalls: list[tuple[str, float]] = []
    for item in definitions:
        if set(item) != {"definition_id", "metrics"} or not isinstance(item["metrics"], dict):
            raise ContractValidationError("M4 pilot definition metric member drifted")
        metrics = item["metrics"]
        if set(metrics) != {
            "best_found_value",
            "high_value_discovery_recall",
            "simple_regret",
            "success_at_budget",
        }:
            raise ContractValidationError("M4 pilot sequential metric set drifted")
        for metric_id in metrics:
            _defined_metric_value(metrics[metric_id], metric_id=metric_id)
        recall = _defined_metric_value(
            metrics["high_value_discovery_recall"],
            metric_id="high_value_discovery_recall",
        )
        if not isinstance(recall, float) or not 0 <= recall <= 1:
            raise ContractValidationError("M4 pilot recall metric is outside [0, 1]")
        recalls.append((str(item["definition_id"]), recall))
    return PilotRunRecall(
        task_id=str(task_id),
        run_spec=run_spec,
        trajectory_sha256=trajectory_sha256,
        recalls=tuple(recalls),
    )


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, start=Decimal(0)) / Decimal(len(values))


def evaluate_pilot_screening(
    records: Sequence[PilotRunRecall],
    *,
    expected_runs: Sequence[tuple[str, RunSpec]],
) -> PilotScreeningDecision:
    """Apply the prospective three-seed task-first selection rule without post-result choices."""

    expected = {
        (task_id, model_id, acquisition_id, seed)
        for task_id in _PILOT_TASKS
        for model_id in _PILOT_MODELS
        for acquisition_id in _PILOT_ACQUISITIONS
        for seed in _PILOT_SEEDS
    }
    by_key = {
        (
            record.task_id,
            record.run_spec.model_id,
            record.run_spec.acquisition_id,
            record.run_spec.seed,
        ): record
        for record in records
    }
    expected_by_run_id = {
        run_spec.run_id: (task_id, run_spec) for task_id, run_spec in expected_runs
    }
    if (
        len(records) != 72
        or len(by_key) != 72
        or set(by_key) != expected
        or len({record.run_spec.run_id for record in records}) != 72
        or len(expected_runs) != 72
        or len(expected_by_run_id) != 72
        or any(
            expected_by_run_id.get(record.run_spec.run_id) != (record.task_id, record.run_spec)
            for record in records
        )
        or any(
            tuple(definition_id for definition_id, _ in record.recalls) != _DEFINITION_IDS
            for record in records
        )
    ):
        return PilotScreeningDecision(
            status="INCONCLUSIVE-FAILURE",
            selected_explorations=(),
            model_decisions=(),
            failure_reason="pilot evidence does not exact-cover the reviewed 72 RunSpecs",
        )

    task_decisions: dict[tuple[str, str, str], PilotTaskDecision] = {}
    ranking: dict[tuple[str, str], tuple[int, Decimal, Decimal]] = {}
    for model_id in _PILOT_MODELS:
        for acquisition_id in _EXPLORATION_ACQUISITIONS:
            primary_means: list[Decimal] = []
            pass_count = 0
            for task_id in _PILOT_TASKS:
                differences: dict[str, list[Decimal]] = {
                    definition_id: [] for definition_id in _DEFINITION_IDS
                }
                for seed in _PILOT_SEEDS:
                    exploration = dict(by_key[(task_id, model_id, acquisition_id, seed)].recalls)
                    greedy = dict(
                        by_key[(task_id, model_id, "greedy-predictive-mean-v1", seed)].recalls
                    )
                    for definition_id in _DEFINITION_IDS:
                        differences[definition_id].append(
                            Decimal(str(exploration[definition_id]))
                            - Decimal(str(greedy[definition_id]))
                        )
                primary = differences["upper-tail-1pct"]
                primary_mean = _mean(primary)
                sensitivity_means = tuple(
                    (definition_id, _mean(differences[definition_id]))
                    for definition_id in _DEFINITION_IDS[1:]
                )
                primary_pass = sum(value > 0 for value in primary) >= 2 and primary_mean >= Decimal(
                    "0.01"
                )
                if primary_pass and any(value < 0 for _, value in sensitivity_means):
                    status = "fragile-inconclusive"
                elif primary_pass:
                    status = "task-pass"
                    pass_count += 1
                else:
                    status = "task-fail"
                task_decisions[(task_id, model_id, acquisition_id)] = PilotTaskDecision(
                    task_id=task_id,
                    model_id=model_id,
                    acquisition_id=acquisition_id,
                    positive_primary_seeds=sum(value > 0 for value in primary),
                    primary_mean_difference=float(primary_mean),
                    sensitivity_mean_differences=tuple(
                        (definition_id, float(value)) for definition_id, value in sensitivity_means
                    ),
                    status=status,
                )
                primary_means.append(primary_mean)
            ranking[(model_id, acquisition_id)] = (
                pass_count,
                min(primary_means),
                sum(primary_means, start=Decimal(0)),
            )

    model_decisions: list[PilotModelDecision] = []
    selected_explorations: list[tuple[str, str]] = []
    for model_id in _PILOT_MODELS:
        eligible = tuple(
            acquisition_id
            for acquisition_id in _EXPLORATION_ACQUISITIONS
            if ranking[(model_id, acquisition_id)][0] >= 1
        )
        if eligible:
            selected = sorted(
                eligible,
                key=lambda acquisition_id: (
                    tuple(-value for value in ranking[(model_id, acquisition_id)]),
                    acquisition_id,
                ),
            )[0]
            selected_explorations.append((model_id, selected))
            selected_tasks = tuple(
                task_decisions[(task_id, model_id, selected)] for task_id in _PILOT_TASKS
            )
            if sum(item.status == "task-pass" for item in selected_tasks) == 2:
                selection_status = "cross-task-directional"
            elif any(item.primary_mean_difference < 0 for item in selected_tasks):
                selection_status = "retain-as-heterogeneous"
            else:
                selection_status = "single-task-directional"
        else:
            selected = None
            selection_status = "pilot-eliminated"
        model_decisions.append(
            PilotModelDecision(
                model_id=model_id,
                selected_acquisition_id=selected,
                selection_status=selection_status,
                eligible_acquisition_ids=eligible,
                eliminated_acquisition_ids=tuple(
                    item for item in _EXPLORATION_ACQUISITIONS if item != selected
                ),
                task_decisions=tuple(
                    task_decisions[(task_id, model_id, acquisition_id)]
                    for acquisition_id in _EXPLORATION_ACQUISITIONS
                    for task_id in _PILOT_TASKS
                ),
            )
        )
    return PilotScreeningDecision(
        status="EXPAND" if selected_explorations else "STOP",
        selected_explorations=tuple(selected_explorations),
        model_decisions=tuple(model_decisions),
        failure_reason=None,
    )


def materialize_task_seed_aggregate(
    experiment_config: LoadedContract,
    candidates: Sequence[MetricResultEvidence],
    *,
    input_metric_id: str,
) -> LoadedContract:
    """Build an aggregate only after one valid result exact-covers every registered seed."""

    registered_seeds = tuple(sorted(experiment_config.value["seeds"]))
    by_seed: dict[int, list[MetricResultEvidence]] = {seed: [] for seed in registered_seeds}
    for evidence in candidates:
        if (
            evidence.result.value["metric_id"] == input_metric_id
            and evidence.result.value["seed"] in by_seed
            and evidence.run_attempt.get("status") == "succeeded"
        ):
            by_seed[evidence.result.value["seed"]].append(evidence)
    if any(len(by_seed[seed]) != 1 for seed in registered_seeds):
        raise ContractValidationError(
            "task-seed aggregate requires exactly one succeeded metric result per registered seed"
        )

    selected = [by_seed[seed][0] for seed in registered_seeds]
    for evidence in selected:
        validate_metric_result_evidence(evidence, experiment_config)
    first = selected[0]
    first_result = first.result.value
    first_run_spec = first.run_spec
    if any(item.result.value["status"] == "undefined" for item in selected):
        status = "undefined"
        value: float | None = None
        undefined_reason: str | None = "undefined_input_result"
    else:
        numeric_values = [
            int(item.result.value["value"])
            if isinstance(item.result.value["value"], bool)
            else item.result.value["value"]
            for item in selected
        ]
        status = "defined"
        value = sum(numeric_values) / len(numeric_values)
        undefined_reason = None
    metric_id = "success_probability" if input_metric_id == "success_at_budget" else input_metric_id
    aggregate_value = {
        "acquisition_config_sha256": first_run_spec.acquisition_config_sha256,
        "acquisition_id": first_run_spec.acquisition_id,
        "batch_size": first_run_spec.batch_size,
        "budget": first_result["budget"],
        "dataset_manifest_sha256": first_run_spec.dataset_manifest_sha256,
        "definition_id": first_result["definition_id"],
        "environment_digest": first_run_spec.environment_digest,
        "evaluation_scope": "task-seed-aggregate",
        "experiment_config_sha256": experiment_config.sha256,
        "feature_sha256": first_run_spec.feature_sha256,
        "git_commit": first_run_spec.git_commit,
        "initial_sample_size": first_run_spec.initial_sample_size,
        "input_metric_id": input_metric_id,
        "metric_id": metric_id,
        "model_config_sha256": first_run_spec.model_config_sha256,
        "model_id": first_run_spec.model_id,
        "ordered_inputs": [
            {
                "attempt_id": evidence.result.value["attempt_id"],
                "metric_result_sha256": evidence.result.sha256,
                "run_id": evidence.run_spec.run_id,
                "seed": evidence.run_spec.seed,
            }
            for evidence in selected
        ],
        "processing_manifest_sha256": first_run_spec.processing_manifest_sha256,
        "protocol_version": first_run_spec.protocol_version,
        "round": first_result["round"],
        "run_budget": first_run_spec.budget,
        "run_spec_schema_version": first_run_spec.schema_version,
        "schema_version": "high-value-task-seed-aggregate-v1",
        "split_sha256": first_run_spec.split_sha256,
        "status": status,
        "task_id": first_result["task_id"],
        "undefined_reason": undefined_reason,
        "value": value,
    }
    aggregate = load_high_value_result(
        canonical_json_bytes(aggregate_value),
        expected_schema_version="high-value-task-seed-aggregate-v1",
    )
    validate_task_seed_aggregate(aggregate, experiment_config, candidates)
    return aggregate
