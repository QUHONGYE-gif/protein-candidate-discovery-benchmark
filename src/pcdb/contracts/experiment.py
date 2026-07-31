"""Versioned Experiment Config loading and canonical identity."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NoReturn, cast

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
_NOMINAL_K_BY_TASK = {
    "alpha-amylase": (23, 12, 46),
    "ired": (21, 11, 42),
}
_RANKING_POLICY = {
    "boundary_tie_policy": "include_all_equal",
    "duplicate_candidate_id_policy": "error",
    "label_direction": "higher_is_better",
    "non_finite_policy": "error",
    "secondary_order": "candidate_id_ascending",
}
_REPORTING_POLICY = {
    "required_outputs": [
        "nominal_k",
        "actual_high_value_count",
        "boundary_score",
        "boundary_tie_count",
    ]
}
_DEFINITION_KEYS = {
    "candidate_role",
    "definition_id",
    "derivation",
    "frozen_candidate_count",
    "kind",
    "purpose",
    "ranking_policy",
    "reporting_policy",
    "value",
}
_BASE_V2_KEYS = {
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
_EVALUATION_KEYS = {
    "access_boundary",
    "aggregation",
    "high_value_definitions",
    "interpretation_policy",
    "metric_sets",
    "primary_high_value_definition_id",
}
_ACCESS_BOUNDARY = {
    "owner": "evaluator-or-hidden-oracle",
    "prohibited_consumers": [
        "candidate-features",
        "surrogate-model",
        "calibration",
        "acquisition-strategy",
    ],
}
_METRIC_SETS = {
    "m2_static": ["spearman_rank_correlation", "high_value_recall_at_nominal_k"],
    "m3_m5_sequential": [
        "best_found_value",
        "simple_regret",
        "high_value_discovery_recall",
        "success_at_budget",
    ],
    "task_seed_aggregates": ["success_probability"],
}
_AGGREGATION = {
    "dimensionless_equal_weight_cross_task_metrics": [
        "spearman_rank_correlation",
        "high_value_recall_at_nominal_k",
        "high_value_discovery_recall",
        "success_probability",
    ],
    "pooled_seed_primary": False,
    "primary_unit": "task",
    "raw_unitful_cross_task_aggregate": False,
    "seed_role": "within-task-algorithmic-randomness",
}
_INTERPRETATION_POLICY = {
    "comparison_rule": "strict_sign_reversal",
    "primary_tie_expansion_fraction_limit": 0.02,
    "sensitivity_fractions": [0.005, 0.02],
    "triggered_status": "fragile-inconclusive",
}
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ExperimentConfigError(ValueError):
    """Raised when Experiment Config bytes violate the production contract."""


@dataclass(frozen=True, slots=True)
class LoadedExperimentConfig:
    """One validated Experiment Config and the exact bytes bound by RunSpec."""

    value: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str

    @property
    def schema_version(self) -> str:
        """Return the explicitly dispatched Experiment Config version."""

        return str(self.value["schema_version"])


def canonical_json_bytes(value: object) -> bytes:
    """Render the versioned canonical JSON bytes used by v2 identities."""

    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ExperimentConfigError("value cannot be rendered as finite canonical JSON") from error
    return (rendered + "\n").encode("utf-8")


def _reject_constant(value: str) -> NoReturn:
    raise ExperimentConfigError(f"non-finite JSON constant is forbidden: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ExperimentConfigError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _parse_json(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise ExperimentConfigError("Experiment Config must not be empty")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ExperimentConfigError("Experiment Config must not contain a UTF-8 BOM")
    try:
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as error:
        raise ExperimentConfigError("Experiment Config must be valid UTF-8 JSON") from error
    except json.JSONDecodeError as error:
        raise ExperimentConfigError("Experiment Config must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ExperimentConfigError("Experiment Config must be a JSON object")
    return value


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _validate_array(
    value: object,
    *,
    field: str,
    item_type: type[str] | type[int],
    minimum: int | None = None,
) -> None:
    if not isinstance(value, list) or not value:
        raise ExperimentConfigError(f"v2 {field} must be a non-empty array")
    if any(type(item) is not item_type for item in value):
        raise ExperimentConfigError(f"v2 {field} contains an invalid item type")
    if len(set(value)) != len(value):
        raise ExperimentConfigError(f"v2 {field} must not contain duplicates")
    if item_type is str and any(not item for item in value):
        raise ExperimentConfigError(f"v2 {field} strings must not be empty")
    if minimum is not None and any(item < minimum for item in value):
        raise ExperimentConfigError(f"v2 {field} contains an out-of-range integer")


def _validate_v2_binding(value: dict[str, Any]) -> None:
    expected_keys = set(_BASE_V2_KEYS)
    held_out_authorized = value.get("held_out_authorized")
    if held_out_authorized is True:
        expected_keys.add("held_out_authorization_reference")
    if set(value) != expected_keys:
        raise ExperimentConfigError("v2 top-level fields do not match the production contract")
    if value.get("contract_status") != "production":
        raise ExperimentConfigError("v2 contract_status must be 'production'")
    experiment_id = value.get("experiment_id")
    if not isinstance(experiment_id, str) or not _IDENTIFIER_PATTERN.fullmatch(experiment_id):
        raise ExperimentConfigError("v2 experiment_id is invalid")
    if value.get("phase") not in {"smoke", "development", "pilot", "frozen"}:
        raise ExperimentConfigError("v2 phase is invalid")
    if type(held_out_authorized) is not bool:
        raise ExperimentConfigError("v2 held_out_authorized must be boolean")
    if held_out_authorized is True:
        reference = value.get("held_out_authorization_reference")
        if not isinstance(reference, str) or not reference:
            raise ExperimentConfigError(
                "held-out access requires a non-empty authorization reference"
            )

    _validate_array(value.get("models"), field="models", item_type=str)
    _validate_array(value.get("acquisitions"), field="acquisitions", item_type=str)
    _validate_array(value.get("seeds"), field="seeds", item_type=int, minimum=0)
    for field in ("initial_sample_sizes", "batch_sizes", "budgets"):
        _validate_array(value.get(field), field=field, item_type=int, minimum=1)

    task_id = value.get("task_id")
    if not isinstance(task_id, str) or task_id not in _TASK_BINDINGS:
        raise ExperimentConfigError("v2 task_id is not an approved task binding")
    binding = _TASK_BINDINGS[task_id]
    for field in ("dataset_manifest_sha256", "processing_manifest_sha256", "split_sha256"):
        if value.get(field) != binding[field]:
            raise ExperimentConfigError(f"{task_id} {field} does not match the frozen binding")

    evaluation = value.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ExperimentConfigError("v2 evaluation must be an object")
    if set(evaluation) != _EVALUATION_KEYS:
        raise ExperimentConfigError("v2 evaluation fields do not match the production contract")
    if evaluation.get("primary_high_value_definition_id") != "upper-tail-1pct":
        raise ExperimentConfigError("v2 primary high-value definition drifted")
    frozen_sections = {
        "access_boundary": _ACCESS_BOUNDARY,
        "aggregation": _AGGREGATION,
        "interpretation_policy": _INTERPRETATION_POLICY,
        "metric_sets": _METRIC_SETS,
    }
    for field, expected in frozen_sections.items():
        if evaluation.get(field) != expected:
            raise ExperimentConfigError(f"v2 {field} does not match the frozen contract")
    definitions = evaluation.get("high_value_definitions")
    if not isinstance(definitions, list) or len(definitions) != len(_DEFINITION_SPECS):
        raise ExperimentConfigError("v2 definition contract requires exactly three definitions")
    for index, definition in enumerate(definitions):
        if not isinstance(definition, dict):
            raise ExperimentConfigError("each v2 definition contract entry must be an object")
        if set(definition) != _DEFINITION_KEYS:
            raise ExperimentConfigError("v2 definition contract fields do not match")
        definition_id, purpose, fraction = _DEFINITION_SPECS[index]
        expected_derivation = {
            "fraction": fraction,
            "nominal_k": _NOMINAL_K_BY_TASK[task_id][index],
            "rounding": "ceil",
        }
        expected_scalars = {
            "candidate_role": "candidate-pool",
            "definition_id": definition_id,
            "derivation": expected_derivation,
            "frozen_candidate_count": binding["frozen_candidate_count"],
            "kind": "quantile",
            "purpose": purpose,
            "ranking_policy": _RANKING_POLICY,
            "reporting_policy": _REPORTING_POLICY,
            "value": fraction,
        }
        if definition != expected_scalars:
            raise ExperimentConfigError("v2 definition contract does not match the frozen task")


def load_experiment_config(payload: bytes) -> LoadedExperimentConfig:
    """Load an Experiment Config without silently migrating its version."""

    value = _parse_json(payload)
    schema_version = value.get("schema_version")
    if schema_version == "2":
        if canonical_json_bytes(value) != payload:
            raise ExperimentConfigError("v2 Experiment Config bytes are not canonical")
        _validate_v2_binding(value)
    elif schema_version != "1":
        raise ExperimentConfigError("unsupported Experiment Config schema_version")
    return LoadedExperimentConfig(
        value=cast(Mapping[str, Any], _deep_freeze(value)),
        canonical_bytes=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
