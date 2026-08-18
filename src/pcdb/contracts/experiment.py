"""Strict production Experiment Config and high-value result contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_TASK_BINDINGS: dict[str, dict[str, str | int]] = {
    "alpha-amylase": {
        "dataset_manifest_sha256": (
            "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac"
        ),
        "processing_manifest_sha256": (
            "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec"
        ),
        "split_sha256": "c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a",
        "frozen_candidate_count": 2262,
    },
    "ired": {
        "dataset_manifest_sha256": (
            "37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643"
        ),
        "processing_manifest_sha256": (
            "929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba"
        ),
        "split_sha256": "9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e",
        "frozen_candidate_count": 2070,
    },
}

_DEFINITION_SPECS = (
    ("upper-tail-1pct", "primary", 0.01),
    ("upper-tail-0.5pct", "sensitivity", 0.005),
    ("upper-tail-2pct", "sensitivity", 0.02),
)
_M2_METRICS = ("spearman_rank_correlation", "high_value_recall_at_nominal_k")
_SEQUENTIAL_METRICS = (
    "best_found_value",
    "simple_regret",
    "high_value_discovery_recall",
    "success_at_budget",
)
_TASK_AGGREGATES = ("success_probability",)
_CROSS_TASK_METRICS = (
    "spearman_rank_correlation",
    "high_value_recall_at_nominal_k",
    "high_value_discovery_recall",
    "success_probability",
)
_PROHIBITED_CONSUMERS = (
    "candidate-features",
    "surrogate-model",
    "calibration",
    "acquisition-strategy",
)
_REPORTING_OUTPUTS = (
    "nominal_k",
    "actual_high_value_count",
    "boundary_score",
    "boundary_tie_count",
)


class ContractValidationError(ValueError):
    """Raised when persisted contract bytes fail closed validation."""


@dataclass(frozen=True, slots=True)
class LoadedContract:
    """One parsed contract whose exact accepted bytes define its identity."""

    schema_version: str
    value: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Render the frozen pretty-JSON canonical representation."""

    try:
        rendered = json.dumps(
            _json_compatible(value),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("value is not finite canonical JSON") from exc
    return (rendered + "\n").encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ContractValidationError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"duplicate object key is forbidden: {key}")
        result[key] = value
    return result


def _parse_json_bytes(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise ContractValidationError("empty JSON artifact is forbidden")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ContractValidationError("UTF-8 BOM is forbidden")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractValidationError("artifact must be valid UTF-8") from exc
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ContractValidationError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError("artifact must be strict JSON") from exc
    if not isinstance(parsed, dict):
        raise ContractValidationError("top-level JSON value must be an object")
    return parsed


def _require_exact_keys(value: dict[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractValidationError(f"{field} fields differ: missing={missing}, extra={extra}")


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ContractValidationError(f"{field} must be a canonical identifier")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_unique_strings(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ContractValidationError(f"{field} must contain unique non-empty strings")
    return value


def _require_unique_positive_ints(value: Any, field: str, *, zero_allowed: bool = False) -> None:
    minimum = 0 if zero_allowed else 1
    if (
        not isinstance(value, list)
        or not value
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < minimum for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise ContractValidationError(f"{field} must contain unique integers >= {minimum}")


def _validate_definition(
    value: Any,
    *,
    expected_id: str,
    expected_purpose: str,
    expected_fraction: float,
    candidate_count: int,
) -> None:
    if not isinstance(value, dict):
        raise ContractValidationError("high-value definition must be an object")
    _require_exact_keys(
        value,
        {
            "candidate_role",
            "definition_id",
            "derivation",
            "frozen_candidate_count",
            "kind",
            "purpose",
            "ranking_policy",
            "reporting_policy",
            "value",
        },
        "high-value definition",
    )
    if (
        value["definition_id"] != expected_id
        or value["purpose"] != expected_purpose
        or value["kind"] != "quantile"
        or value["value"] != expected_fraction
        or value["candidate_role"] != "candidate-pool"
        or value["frozen_candidate_count"] != candidate_count
    ):
        raise ContractValidationError("high-value definition does not match the frozen definition")

    ranking = value["ranking_policy"]
    if not isinstance(ranking, dict):
        raise ContractValidationError("ranking_policy must be an object")
    expected_ranking = {
        "boundary_tie_policy": "include_all_equal",
        "duplicate_candidate_id_policy": "error",
        "label_direction": "higher_is_better",
        "non_finite_policy": "error",
        "secondary_order": "candidate_id_ascending",
    }
    if ranking != expected_ranking:
        raise ContractValidationError("ranking_policy does not match the frozen policy")

    reporting = value["reporting_policy"]
    if reporting != {"required_outputs": list(_REPORTING_OUTPUTS)}:
        raise ContractValidationError("reporting_policy does not match the frozen policy")

    derivation = value["derivation"]
    if not isinstance(derivation, dict):
        raise ContractValidationError("definition derivation must be an object")
    _require_exact_keys(derivation, {"fraction", "nominal_k", "rounding"}, "derivation")
    nominal_k = math.ceil(expected_fraction * candidate_count)
    if derivation != {
        "fraction": expected_fraction,
        "nominal_k": nominal_k,
        "rounding": "ceil",
    }:
        raise ContractValidationError("quantile nominal K does not match the frozen derivation")


def _validate_v2(value: dict[str, Any]) -> None:
    expected_fields = {
        "acquisitions",
        "batch_sizes",
        "budgets",
        "contract_status",
        "dataset_manifest_sha256",
        "evaluation",
        "experiment_id",
        "held_out_authorized",
        "initial_sample_sizes",
        "models",
        "phase",
        "processing_manifest_sha256",
        "schema_version",
        "seeds",
        "split_sha256",
        "task_id",
    }
    if "held_out_authorization_reference" in value:
        expected_fields.add("held_out_authorization_reference")
    _require_exact_keys(
        value,
        expected_fields,
        "Experiment Config v2",
    )
    if value["schema_version"] != "2" or value["contract_status"] != "production":
        raise ContractValidationError("v2 production sentinel is invalid")
    _require_identifier(value["experiment_id"], "experiment_id")
    if value["phase"] not in {"smoke", "development", "pilot", "frozen"}:
        raise ContractValidationError("phase is invalid")
    task_id = value["task_id"]
    if task_id not in _TASK_BINDINGS:
        raise ContractValidationError("task binding is unknown")
    binding = _TASK_BINDINGS[task_id]
    for field in ("dataset_manifest_sha256", "processing_manifest_sha256", "split_sha256"):
        _require_sha256(value[field], field)
        if value[field] != binding[field]:
            raise ContractValidationError(f"{field} task binding does not match")
    _require_unique_strings(value["models"], "models")
    _require_unique_strings(value["acquisitions"], "acquisitions")
    _require_unique_positive_ints(value["seeds"], "seeds", zero_allowed=True)
    _require_unique_positive_ints(value["initial_sample_sizes"], "initial_sample_sizes")
    _require_unique_positive_ints(value["batch_sizes"], "batch_sizes")
    _require_unique_positive_ints(value["budgets"], "budgets")
    held_out_authorized = value["held_out_authorized"]
    authorization_reference = value.get("held_out_authorization_reference")
    if not isinstance(held_out_authorized, bool):
        raise ContractValidationError("held-out authorization flag must be boolean")
    if held_out_authorized:
        if not isinstance(authorization_reference, str) or not authorization_reference:
            raise ContractValidationError(
                "held-out authorization requires a non-empty authorization reference"
            )
    elif authorization_reference is not None:
        raise ContractValidationError(
            "held-out authorization reference is forbidden while authorization is false"
        )

    evaluation = value["evaluation"]
    if not isinstance(evaluation, dict):
        raise ContractValidationError("evaluation must be an object")
    _require_exact_keys(
        evaluation,
        {
            "access_boundary",
            "aggregation",
            "high_value_definitions",
            "interpretation_policy",
            "metric_sets",
            "primary_high_value_definition_id",
        },
        "evaluation",
    )
    if evaluation["primary_high_value_definition_id"] != "upper-tail-1pct":
        raise ContractValidationError("primary high-value definition is invalid")
    definitions = evaluation["high_value_definitions"]
    if not isinstance(definitions, list) or len(definitions) != len(_DEFINITION_SPECS):
        raise ContractValidationError("high-value definitions must have the frozen cardinality")
    candidate_count = int(binding["frozen_candidate_count"])
    for definition, expected in zip(definitions, _DEFINITION_SPECS, strict=True):
        _validate_definition(
            definition,
            expected_id=expected[0],
            expected_purpose=expected[1],
            expected_fraction=expected[2],
            candidate_count=candidate_count,
        )

    if evaluation["access_boundary"] != {
        "owner": "evaluator-or-hidden-oracle",
        "prohibited_consumers": list(_PROHIBITED_CONSUMERS),
    }:
        raise ContractValidationError("access boundary does not match the frozen policy")
    if evaluation["metric_sets"] != {
        "m2_static": list(_M2_METRICS),
        "m3_m5_sequential": list(_SEQUENTIAL_METRICS),
        "task_seed_aggregates": list(_TASK_AGGREGATES),
    }:
        raise ContractValidationError("metric sets do not match the frozen contract")
    if evaluation["aggregation"] != {
        "cross_task_equal_weight_metric_ids": list(_CROSS_TASK_METRICS),
        "pooled_seed_primary": False,
        "primary_unit": "task",
        "raw_unitful_cross_task_aggregate": False,
        "seed_role": "within-task-algorithmic-randomness",
    }:
        raise ContractValidationError("aggregation does not match the frozen contract")
    if evaluation["interpretation_policy"] != {
        "comparison_rule": "strict_sign_reversal",
        "primary_tie_expansion_fraction_limit": 0.02,
        "sensitivity_fractions": [0.005, 0.02],
        "triggered_status": "fragile-inconclusive",
    }:
        raise ContractValidationError("interpretation policy does not match the frozen contract")


def _validate_v3(value: dict[str, Any]) -> None:
    """Validate the single-run M5 seed-source tracer identity contract."""

    expected_fields = {
        "acquisitions",
        "batch_sizes",
        "budgets",
        "contract_status",
        "dataset_manifest_sha256",
        "evaluation",
        "experiment_id",
        "held_out_authorized",
        "initial_sample_sizes",
        "models",
        "phase",
        "processing_manifest_sha256",
        "protocol_version",
        "schema_version",
        "seed_axes",
        "seeds",
        "split_sha256",
        "task_id",
    }
    _require_exact_keys(value, expected_fields, "Experiment Config v3")
    v2_compatible = {
        key: item for key, item in value.items() if key not in {"protocol_version", "seed_axes"}
    }
    v2_compatible["schema_version"] = "2"
    _validate_v2(v2_compatible)
    if (
        value["schema_version"] != "3"
        or value["protocol_version"] != "m5-seed-source-tracer-v1"
        or value["phase"] != "development"
        or value["held_out_authorized"] is not False
        or value["models"] != ["random-forest-sklearn-v1"]
        or value["initial_sample_sizes"] != [32]
        or value["batch_sizes"] != [16]
        or value["budgets"] != [128]
        or len(value["seeds"]) != 1
        or len(value["acquisitions"]) != 1
        or value["acquisitions"][0]
        not in {"uncertainty-bootstrap-std-v1", "greedy-predictive-mean-v1"}
    ):
        raise ContractValidationError("M5 tracer Experiment Config profile drifted")
    seed_axes = value["seed_axes"]
    if not isinstance(seed_axes, dict):
        raise ContractValidationError("M5 tracer seed_axes must be an object")
    _require_exact_keys(
        seed_axes,
        {
            "estimator_seed",
            "estimator_seed_binding",
            "initial_seed",
            "initial_seed_binding",
            "internal_seed",
            "internal_seed_binding",
        },
        "M5 tracer seed_axes",
    )
    initial_seed = seed_axes["initial_seed"]
    internal_seed = seed_axes["internal_seed"]
    if (
        isinstance(initial_seed, bool)
        or not isinstance(initial_seed, int)
        or initial_seed not in range(1729, 1734)
        or value["seeds"] != [initial_seed]
        or seed_axes["estimator_seed"] != 1729
        or seed_axes["initial_seed_binding"] != "run-spec.seed"
        or seed_axes["internal_seed_binding"] != "experiment-config-sha256"
        or seed_axes["estimator_seed_binding"] != "frozen-model-config"
    ):
        raise ContractValidationError("M5 tracer seed-axis identity drifted")
    acquisition_id = value["acquisitions"][0]
    if acquisition_id == "uncertainty-bootstrap-std-v1":
        if (
            isinstance(internal_seed, bool)
            or not isinstance(internal_seed, int)
            or internal_seed not in range(2719, 2724)
        ):
            raise ContractValidationError("M5 exploration requires one frozen internal seed")
    elif internal_seed is not None:
        raise ContractValidationError("M5 Greedy forbids an internal seed")


def _validate_v1(value: dict[str, Any]) -> None:
    required = {
        "acquisitions",
        "batch_sizes",
        "budgets",
        "dataset_manifest_sha256",
        "evaluation",
        "experiment_id",
        "held_out_authorized",
        "initial_sample_sizes",
        "models",
        "phase",
        "processing_manifest_sha256",
        "schema_version",
        "seeds",
        "split_sha256",
    }
    if not required <= set(value):
        raise ContractValidationError("Experiment Config v1 is missing required fields")
    if value["schema_version"] != "1":
        raise ContractValidationError("Experiment Config v1 schema_version is invalid")


def load_experiment_config(payload: bytes) -> LoadedContract:
    """Dispatch versioned Experiment Configs with canonical domain validation."""

    value = _parse_json_bytes(payload)
    version = value.get("schema_version")
    if version == "1":
        _validate_v1(value)
        accepted_bytes = payload
    elif version == "2":
        if canonical_json_bytes(value) != payload:
            raise ContractValidationError("Experiment Config v2 bytes are not canonical")
        _validate_v2(value)
        accepted_bytes = payload
    elif version == "3":
        if canonical_json_bytes(value) != payload:
            raise ContractValidationError("Experiment Config v3 bytes are not canonical")
        _validate_v3(value)
        accepted_bytes = payload
    else:
        raise ContractValidationError(f"unknown Experiment Config schema_version: {version!r}")
    return LoadedContract(
        schema_version=version,
        value=_deep_freeze(value),
        canonical_bytes=accepted_bytes,
        sha256=hashlib.sha256(accepted_bytes).hexdigest(),
    )


_RESULT_FIELDS = {
    "acquisition_config_sha256",
    "acquisition_id",
    "attempt_id",
    "budget",
    "candidate_role",
    "dataset_manifest_sha256",
    "definition_id",
    "evaluation_scope",
    "experiment_config_sha256",
    "input_artifact_sha256",
    "metric_id",
    "model_config_sha256",
    "model_id",
    "processing_manifest_sha256",
    "round",
    "run_id",
    "schema_version",
    "seed",
    "split_sha256",
    "status",
    "support",
    "task_id",
    "undefined_reason",
    "value",
}
_AGGREGATE_FIELDS = {
    "acquisition_config_sha256",
    "acquisition_id",
    "batch_size",
    "budget",
    "dataset_manifest_sha256",
    "definition_id",
    "environment_digest",
    "evaluation_scope",
    "experiment_config_sha256",
    "feature_sha256",
    "git_commit",
    "initial_sample_size",
    "input_metric_id",
    "metric_id",
    "model_config_sha256",
    "model_id",
    "ordered_inputs",
    "processing_manifest_sha256",
    "protocol_version",
    "round",
    "run_budget",
    "run_spec_schema_version",
    "schema_version",
    "split_sha256",
    "status",
    "task_id",
    "undefined_reason",
    "value",
}


def _validate_metric_result_intrinsic(value: dict[str, Any]) -> None:
    for field in (
        "experiment_config_sha256",
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
        "run_id",
        "input_artifact_sha256",
        "model_config_sha256",
        "acquisition_config_sha256",
    ):
        _require_sha256(value[field], field)
    _require_identifier(value["attempt_id"], "attempt_id")
    if value["task_id"] not in _TASK_BINDINGS:
        raise ContractValidationError("metric result task_id is unknown")
    if not isinstance(value["model_id"], str) or not value["model_id"]:
        raise ContractValidationError("metric result model_id must not be empty")
    if not isinstance(value["acquisition_id"], str) or not value["acquisition_id"]:
        raise ContractValidationError("metric result acquisition_id must not be empty")
    if isinstance(value["seed"], bool) or not isinstance(value["seed"], int) or value["seed"] < 0:
        raise ContractValidationError("metric result seed must be non-negative")
    if value["candidate_role"] != "candidate-pool":
        raise ContractValidationError("metric result candidate role is invalid")

    metric_id = value["metric_id"]
    metrics = {*_M2_METRICS, *_SEQUENTIAL_METRICS}
    if metric_id not in metrics:
        raise ContractValidationError("metric result metric_id is unknown")
    definition_independent = {
        "spearman_rank_correlation",
        "best_found_value",
        "simple_regret",
    }
    if metric_id in definition_independent:
        if value["definition_id"] is not None:
            raise ContractValidationError("definition-independent metric requires null definition")
        expected_support = {"evaluated_candidate_count"}
    else:
        if value["definition_id"] not in {item[0] for item in _DEFINITION_SPECS}:
            raise ContractValidationError(
                "definition-dependent metric requires a frozen definition"
            )
        expected_support = {
            "actual_high_value_count",
            "boundary_score",
            "boundary_tie_count",
            "evaluated_candidate_count",
            "frozen_candidate_count",
            "nominal_k",
        }
    support = value["support"]
    if not isinstance(support, dict) or set(support) != expected_support:
        raise ContractValidationError("metric result support fields are invalid")
    evaluated_count = support["evaluated_candidate_count"]
    if (
        isinstance(evaluated_count, bool)
        or not isinstance(evaluated_count, int)
        or evaluated_count < 0
    ):
        raise ContractValidationError("evaluated_candidate_count must be non-negative")
    if metric_id not in definition_independent:
        frozen_count = support["frozen_candidate_count"]
        nominal_k = support["nominal_k"]
        actual_count = support["actual_high_value_count"]
        tie_count = support["boundary_tie_count"]
        integer_counts = (frozen_count, nominal_k, actual_count, tie_count)
        if (
            any(isinstance(item, bool) or not isinstance(item, int) for item in integer_counts)
            or not 1 <= nominal_k <= actual_count <= frozen_count
            or not 1 <= tie_count <= actual_count
            or evaluated_count > frozen_count
        ):
            raise ContractValidationError("high-value support counts are inconsistent")

    if value["evaluation_scope"] == "m2-static":
        if (
            metric_id not in _M2_METRICS
            or value["round"] is not None
            or value["budget"] is not None
        ):
            raise ContractValidationError("m2-static metric scope is invalid")
    elif value["evaluation_scope"] == "sequential":
        if metric_id not in _SEQUENTIAL_METRICS:
            raise ContractValidationError("sequential metric scope is invalid")
        for field in ("round", "budget"):
            raw = value[field]
            minimum = 0 if field == "round" else 1
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
                raise ContractValidationError(f"sequential metric {field} is invalid")
    else:
        raise ContractValidationError("metric result evaluation_scope is unknown")

    status = value["status"]
    if status == "defined":
        if value["undefined_reason"] is not None:
            raise ContractValidationError("defined metric requires null undefined_reason")
        metric_value = value["value"]
        if metric_id == "success_at_budget":
            if not isinstance(metric_value, bool):
                raise ContractValidationError("success_at_budget value must be boolean")
        elif (
            isinstance(metric_value, bool)
            or not isinstance(metric_value, (int, float))
            or not math.isfinite(float(metric_value))
        ):
            raise ContractValidationError("defined numeric metric value must be finite")
        if (
            metric_id
            in {
                "high_value_recall_at_nominal_k",
                "high_value_discovery_recall",
            }
            and not 0 <= float(metric_value) <= 1
        ):
            raise ContractValidationError("recall metric value must be within [0, 1]")
        if metric_id == "spearman_rank_correlation" and not -1 <= float(metric_value) <= 1:
            raise ContractValidationError("Spearman metric value must be within [-1, 1]")
        if metric_id == "simple_regret" and float(metric_value) < 0:
            raise ContractValidationError("simple regret must not be negative")
    elif status == "undefined":
        if value["value"] is not None:
            raise ContractValidationError("undefined metric requires null value")
        allowed_reasons = {
            "spearman_rank_correlation": {
                "insufficient_observations",
                "constant_target",
                "constant_prediction",
                "constant_target_and_prediction",
            },
            "best_found_value": {"no_revealed_observations"},
            "simple_regret": {"no_revealed_observations"},
            "success_at_budget": {"budget_not_reached"},
        }.get(metric_id, set())
        if value["undefined_reason"] not in allowed_reasons:
            raise ContractValidationError("undefined reason is invalid for metric_id")
    else:
        raise ContractValidationError("metric result status is unknown")


def _validate_aggregate_intrinsic(value: dict[str, Any]) -> None:
    for field in (
        "experiment_config_sha256",
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
        "environment_digest",
        "feature_sha256",
        "model_config_sha256",
        "acquisition_config_sha256",
    ):
        _require_sha256(value[field], field)
    if not isinstance(value["git_commit"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", value["git_commit"]
    ):
        raise ContractValidationError("aggregate git_commit must be a full lowercase SHA")
    if value["run_spec_schema_version"] != "1":
        raise ContractValidationError("aggregate RunSpec schema version is invalid")
    if value["evaluation_scope"] != "task-seed-aggregate":
        raise ContractValidationError("aggregate evaluation_scope is invalid")
    for field in ("initial_sample_size", "batch_size", "run_budget"):
        raw = value[field]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise ContractValidationError(f"aggregate {field} must be positive")

    input_metric = value["input_metric_id"]
    expected_metric = "success_probability" if input_metric == "success_at_budget" else input_metric
    if input_metric not in {*_M2_METRICS, *_SEQUENTIAL_METRICS}:
        raise ContractValidationError("aggregate input_metric_id is unknown")
    if value["metric_id"] != expected_metric:
        raise ContractValidationError("aggregate metric_id does not match input metric")
    if input_metric in {
        "spearman_rank_correlation",
        "best_found_value",
        "simple_regret",
    }:
        if value["definition_id"] is not None:
            raise ContractValidationError(
                "definition-independent aggregate requires null definition"
            )
    elif value["definition_id"] not in {item[0] for item in _DEFINITION_SPECS}:
        raise ContractValidationError("definition-dependent aggregate requires a frozen definition")

    is_static = input_metric in _M2_METRICS
    for field in ("round", "budget"):
        raw = value[field]
        if is_static:
            if raw is not None:
                raise ContractValidationError("static aggregate round and budget must be null")
        else:
            minimum = 0 if field == "round" else 1
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
                raise ContractValidationError(f"sequential aggregate {field} is invalid")

    ordered_inputs = value["ordered_inputs"]
    if not isinstance(ordered_inputs, list) or not ordered_inputs:
        raise ContractValidationError("aggregate ordered_inputs must not be empty")
    seeds: list[int] = []
    for entry in ordered_inputs:
        if not isinstance(entry, dict):
            raise ContractValidationError("aggregate ordered input must be an object")
        _require_exact_keys(
            entry,
            {"attempt_id", "metric_result_sha256", "run_id", "seed"},
            "aggregate ordered input",
        )
        seed = entry["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ContractValidationError("aggregate ordered input seed is invalid")
        seeds.append(seed)
        _require_identifier(entry["attempt_id"], "ordered input attempt_id")
        _require_sha256(entry["run_id"], "ordered input run_id")
        _require_sha256(
            entry["metric_result_sha256"],
            "ordered input metric_result_sha256",
        )
    if seeds != sorted(set(seeds)):
        raise ContractValidationError("aggregate ordered inputs must use unique ascending seeds")

    status = value["status"]
    if status == "defined":
        metric_value = value["value"]
        if (
            isinstance(metric_value, bool)
            or not isinstance(metric_value, (int, float))
            or not math.isfinite(float(metric_value))
            or value["undefined_reason"] is not None
        ):
            raise ContractValidationError("defined aggregate value is invalid")
        if (
            value["metric_id"]
            in {
                "high_value_recall_at_nominal_k",
                "high_value_discovery_recall",
                "success_probability",
            }
            and not 0 <= float(metric_value) <= 1
        ):
            raise ContractValidationError("bounded aggregate value must be within [0, 1]")
        if value["metric_id"] == "spearman_rank_correlation" and not -1 <= float(metric_value) <= 1:
            raise ContractValidationError("Spearman aggregate must be within [-1, 1]")
        if value["metric_id"] == "simple_regret" and float(metric_value) < 0:
            raise ContractValidationError("simple-regret aggregate must not be negative")
    elif status == "undefined":
        if value["value"] is not None or value["undefined_reason"] != "undefined_input_result":
            raise ContractValidationError("undefined aggregate state is invalid")
    else:
        raise ContractValidationError("aggregate status is unknown")


def load_high_value_result(
    payload: bytes,
    *,
    expected_schema_version: str,
    expected_sha256: str | None = None,
) -> LoadedContract:
    """Validate canonical result bytes before any cross-artifact validation."""

    value = _parse_json_bytes(payload)
    version = value.get("schema_version")
    if version != expected_schema_version:
        raise ContractValidationError(
            f"schema_version {version!r} does not match {expected_schema_version!r}"
        )
    if canonical_json_bytes(value) != payload:
        raise ContractValidationError("high-value result bytes are not canonical")
    if version == "high-value-metric-result-v1":
        _require_exact_keys(value, _RESULT_FIELDS, "metric result")
        _validate_metric_result_intrinsic(value)
    elif version == "high-value-task-seed-aggregate-v1":
        _require_exact_keys(value, _AGGREGATE_FIELDS, "task-seed aggregate")
        _validate_aggregate_intrinsic(value)
    else:
        raise ContractValidationError(f"unknown high-value result schema_version: {version!r}")
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractValidationError("high-value result SHA-256 does not match")
    return LoadedContract(
        schema_version=version,
        value=_deep_freeze(value),
        canonical_bytes=payload,
        sha256=digest,
    )
