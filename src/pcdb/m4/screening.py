"""Truth-free materialization for exact-cover M4 task-seed aggregates."""

from __future__ import annotations

from collections.abc import Sequence

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
