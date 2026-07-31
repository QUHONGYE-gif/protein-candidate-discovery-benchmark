"""Cross-artifact validation for frozen high-value metric evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pcdb.contracts.experiment import ContractValidationError, LoadedContract
from pcdb.contracts.run import RunSpec

_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@dataclass(frozen=True, slots=True)
class MetricResultEvidence:
    """Fresh-read metric bytes and the RunSpec/RunAttempt that claim them."""

    result: LoadedContract
    run_spec: RunSpec
    run_attempt: Mapping[str, Any]


def _expect_equal(actual: Any, expected: Any, field: str) -> None:
    if actual != expected:
        raise ContractValidationError(f"{field} does not exact-match its referenced identity")


def validate_metric_result_evidence(
    evidence: MetricResultEvidence,
    experiment_config: LoadedContract,
) -> None:
    """Validate one succeeded, completion-marked metric result cross-artifact chain."""

    if evidence.result.schema_version != "high-value-metric-result-v1":
        raise ContractValidationError("evidence must contain a metric-result-v1 artifact")
    if experiment_config.schema_version != "2":
        raise ContractValidationError("metric result requires Experiment Config v2")

    result = evidence.result.value
    run_spec = evidence.run_spec
    attempt = evidence.run_attempt
    config = experiment_config.value

    _expect_equal(result["experiment_config_sha256"], experiment_config.sha256, "config SHA-256")
    _expect_equal(run_spec.experiment_config_sha256, experiment_config.sha256, "RunSpec config")
    for field in (
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ):
        _expect_equal(result[field], getattr(run_spec, field), f"result {field}")
        _expect_equal(result[field], config[field], f"config {field}")
    _expect_equal(result["run_id"], run_spec.run_id, "result run_id")
    _expect_equal(result["model_id"], run_spec.model_id, "result model_id")
    _expect_equal(
        result["model_config_sha256"],
        run_spec.model_config_sha256,
        "result model_config_sha256",
    )
    _expect_equal(result["acquisition_id"], run_spec.acquisition_id, "result acquisition_id")
    _expect_equal(
        result["acquisition_config_sha256"],
        run_spec.acquisition_config_sha256,
        "result acquisition_config_sha256",
    )
    _expect_equal(result["seed"], run_spec.seed, "result seed")
    _expect_equal(result["task_id"], config["task_id"], "result task_id")
    if result["model_id"] not in config["models"]:
        raise ContractValidationError("result model_id is not registered by Experiment Config")
    if result["acquisition_id"] not in config["acquisitions"]:
        raise ContractValidationError(
            "result acquisition_id is not registered by Experiment Config"
        )

    _expect_equal(attempt.get("run_id"), run_spec.run_id, "RunAttempt run_id")
    _expect_equal(attempt.get("attempt_id"), result["attempt_id"], "RunAttempt attempt_id")
    _expect_equal(attempt.get("status"), "succeeded", "RunAttempt status")
    _expect_equal(
        attempt.get("executed_git_commit"),
        run_spec.git_commit,
        "RunAttempt executed_git_commit",
    )
    _expect_equal(
        attempt.get("executed_environment_digest"),
        run_spec.environment_digest,
        "RunAttempt executed_environment_digest",
    )
    completion_marker = attempt.get("completion_marker")
    if not isinstance(completion_marker, Mapping):
        raise ContractValidationError("succeeded RunAttempt requires a completion marker")
    object_key = completion_marker.get("object_key")
    if (
        not isinstance(object_key, str)
        or not object_key.endswith("/_SUCCESS")
        or completion_marker.get("sha256") != _EMPTY_SHA256
        or completion_marker.get("size_bytes") != 0
    ):
        raise ContractValidationError("RunAttempt completion marker is invalid")

    outputs = attempt.get("outputs")
    if not isinstance(outputs, list):
        raise ContractValidationError("RunAttempt outputs must be an inventory")
    matches = [
        item
        for item in outputs
        if isinstance(item, Mapping)
        and item.get("sha256") == evidence.result.sha256
        and item.get("size_bytes") == len(evidence.result.canonical_bytes)
        and isinstance(item.get("object_key"), str)
        and bool(item["object_key"])
    ]
    if len(matches) != 1:
        raise ContractValidationError(
            "RunAttempt outputs must contain exactly one fresh-read metric result"
        )


_AGGREGATE_RUN_SPEC_FIELDS = {
    "run_spec_schema_version": "schema_version",
    "protocol_version": "protocol_version",
    "git_commit": "git_commit",
    "environment_digest": "environment_digest",
    "dataset_manifest_sha256": "dataset_manifest_sha256",
    "processing_manifest_sha256": "processing_manifest_sha256",
    "split_sha256": "split_sha256",
    "feature_sha256": "feature_sha256",
    "experiment_config_sha256": "experiment_config_sha256",
    "model_id": "model_id",
    "model_config_sha256": "model_config_sha256",
    "acquisition_id": "acquisition_id",
    "acquisition_config_sha256": "acquisition_config_sha256",
    "initial_sample_size": "initial_sample_size",
    "batch_size": "batch_size",
    "run_budget": "budget",
}


def _matches_aggregate_identity(
    result: Mapping[str, Any],
    aggregate: Mapping[str, Any],
) -> bool:
    expected_scope = (
        "m2-static"
        if aggregate["input_metric_id"]
        in {"spearman_rank_correlation", "high_value_recall_at_nominal_k"}
        else "sequential"
    )
    return all(
        (
            result["experiment_config_sha256"] == aggregate["experiment_config_sha256"],
            result["dataset_manifest_sha256"] == aggregate["dataset_manifest_sha256"],
            result["processing_manifest_sha256"] == aggregate["processing_manifest_sha256"],
            result["split_sha256"] == aggregate["split_sha256"],
            result["task_id"] == aggregate["task_id"],
            result["model_id"] == aggregate["model_id"],
            result["model_config_sha256"] == aggregate["model_config_sha256"],
            result["acquisition_id"] == aggregate["acquisition_id"],
            result["acquisition_config_sha256"] == aggregate["acquisition_config_sha256"],
            result["definition_id"] == aggregate["definition_id"],
            result["metric_id"] == aggregate["input_metric_id"],
            result["evaluation_scope"] == expected_scope,
            result["round"] == aggregate["round"],
            result["budget"] == aggregate["budget"],
        )
    )


def validate_task_seed_aggregate(
    aggregate_result: LoadedContract,
    experiment_config: LoadedContract,
    candidates: Sequence[MetricResultEvidence],
) -> None:
    """Validate unique outcome-independent inputs and the frozen task-seed mean."""

    if aggregate_result.schema_version != "high-value-task-seed-aggregate-v1":
        raise ContractValidationError("aggregate must use task-seed-aggregate-v1")
    if experiment_config.schema_version != "2":
        raise ContractValidationError("aggregate requires Experiment Config v2")
    aggregate = aggregate_result.value
    config = experiment_config.value
    _expect_equal(
        aggregate["experiment_config_sha256"],
        experiment_config.sha256,
        "aggregate config SHA-256",
    )
    _expect_equal(aggregate["task_id"], config["task_id"], "aggregate task_id")

    ordered_inputs = aggregate["ordered_inputs"]
    if not isinstance(ordered_inputs, list):
        raise ContractValidationError("ordered_inputs must be an array")
    ordered_seeds = [item.get("seed") for item in ordered_inputs]
    registered_seeds = sorted(config["seeds"])
    if ordered_seeds != registered_seeds:
        raise ContractValidationError(
            "ordered_inputs must seed-ascending exact-cover preregistered seeds"
        )

    eligible_by_seed: dict[int, list[MetricResultEvidence]] = {
        seed: [] for seed in registered_seeds
    }
    for evidence in candidates:
        if evidence.run_attempt.get("status") != "succeeded":
            continue
        result = evidence.result.value
        seed = result.get("seed")
        if seed not in eligible_by_seed or not _matches_aggregate_identity(result, aggregate):
            continue
        validate_metric_result_evidence(evidence, experiment_config)
        for aggregate_field, run_spec_field in _AGGREGATE_RUN_SPEC_FIELDS.items():
            _expect_equal(
                aggregate[aggregate_field],
                getattr(evidence.run_spec, run_spec_field),
                f"aggregate {aggregate_field}",
            )
        eligible_by_seed[seed].append(evidence)

    selected: list[MetricResultEvidence] = []
    for entry in ordered_inputs:
        seed = entry["seed"]
        eligible = eligible_by_seed[seed]
        if len(eligible) != 1:
            raise ContractValidationError(
                f"seed {seed} must have exactly one eligible metric result, found {len(eligible)}"
            )
        evidence = eligible[0]
        result = evidence.result.value
        _expect_equal(entry["run_id"], evidence.run_spec.run_id, "ordered input run_id")
        _expect_equal(entry["attempt_id"], result["attempt_id"], "ordered input attempt_id")
        _expect_equal(
            entry["metric_result_sha256"],
            evidence.result.sha256,
            "ordered input metric_result_sha256",
        )
        selected.append(evidence)

    if any(item.result.value["status"] == "undefined" for item in selected):
        _expect_equal(aggregate["status"], "undefined", "aggregate status")
        _expect_equal(aggregate["value"], None, "aggregate value")
        _expect_equal(
            aggregate["undefined_reason"],
            "undefined_input_result",
            "aggregate undefined_reason",
        )
        return
    numeric_values = [
        int(value) if isinstance(value, bool) else value
        for value in (item.result.value["value"] for item in selected)
    ]
    if any(not isinstance(value, (int, float)) for value in numeric_values):
        raise ContractValidationError("defined aggregate inputs must be numeric or boolean")
    expected_mean = sum(numeric_values) / len(numeric_values)
    _expect_equal(aggregate["status"], "defined", "aggregate status")
    _expect_equal(aggregate["value"], expected_mean, "aggregate arithmetic mean")
    _expect_equal(aggregate["undefined_reason"], None, "aggregate undefined_reason")
