"""Bounded, observable-only M5A acquisition reconstruction smoke."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError
from pcdb.contracts.feature_identity import LoadedFeatureMatrix, load_feature_matrix
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.m2.features import sequence_feature_definitions
from pcdb.replay.engine import ReplayTrajectory, load_replay_trajectory

INPUT_SCHEMA_VERSION = "m5a-reconstruction-input-v1"
ARTIFACT_SCHEMA_VERSION = "m5a-reconstruction-smoke-artifact-v1"
_CANDIDATE_ID = re.compile(r"^[0-9a-f]{64}$")
_BINARY64_HEX = re.compile(r"^[0-9a-f]{16}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_MODEL_CONFIG_SHA256 = {
    "random-forest-sklearn-v1": (
        "418b0fd6189fd9b3987620b05021e5ec5ff3018c074024cd13874cd3b9845312"
    ),
    "xgboost-cpu-v1": "31eb5bc146f4e810027de8870fa5632e09a5427bd8dddfee9261572c1e3473f2",
}
_ACQUISITION_CONFIG_SHA256 = {
    "uncertainty-bootstrap-std-v1": (
        "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6"
    ),
    "ucb-bootstrap-std-beta1-v1": (
        "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d"
    ),
}
_STRATA = (
    ("alpha-amylase", "random-forest-sklearn-v1", "uncertainty-bootstrap-std-v1", 32),
    ("alpha-amylase", "random-forest-sklearn-v1", "uncertainty-bootstrap-std-v1", 64),
    ("alpha-amylase", "xgboost-cpu-v1", "ucb-bootstrap-std-beta1-v1", 32),
    ("alpha-amylase", "xgboost-cpu-v1", "ucb-bootstrap-std-beta1-v1", 64),
    ("ired", "random-forest-sklearn-v1", "uncertainty-bootstrap-std-v1", 32),
    ("ired", "random-forest-sklearn-v1", "uncertainty-bootstrap-std-v1", 64),
    ("ired", "xgboost-cpu-v1", "ucb-bootstrap-std-beta1-v1", 32),
    ("ired", "xgboost-cpu-v1", "ucb-bootstrap-std-beta1-v1", 64),
)


class ReconstructionStop(RuntimeError):
    """Fail-closed terminal state for a validity or exactness failure."""


@dataclass(frozen=True, slots=True)
class PreparedReconstruction:
    """Prefix-only child input plus parent-only recorded comparator target."""

    input_bytes: bytes
    actual_selected_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidatedHistoricalState:
    """Exact historical state after completion and identity checks."""

    trajectory: ReplayTrajectory
    candidate_features: LoadedFeatureMatrix
    prepared: PreparedReconstruction


def compact_json_bytes(value: object) -> bytes:
    """Render one compact, UTF-8, key-sorted, finite JSON artifact."""

    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("value is not finite compact JSON") from exc
    return (rendered + "\n").encode("utf-8")


def _load_compact_json(payload: bytes, *, artifact: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(f"{artifact} must be compact UTF-8 JSON") from None
    if not isinstance(value, dict) or compact_json_bytes(value) != payload:
        raise ContractValidationError(f"{artifact} bytes are not compact canonical JSON")
    return cast(dict[str, Any], value)


def binary64_be_hex(value: float) -> str:
    """Encode one finite IEEE-754 binary64 as lower-case big-endian hex."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError("score must be a finite binary64 value")
    converted = float(value)
    if not math.isfinite(converted):
        raise ContractValidationError("score must be a finite binary64 value")
    encoded = struct.pack(">d", converted).hex()
    if not _BINARY64_HEX.fullmatch(encoded):  # pragma: no cover - struct invariant
        raise ContractValidationError("binary64 score encoding drifted")
    return encoded


def _sequence_sha256(values: Sequence[object]) -> str:
    return hashlib.sha256(compact_json_bytes(list(values))).hexdigest()


def load_reconstruction_contract(payload: bytes) -> Mapping[str, Any]:
    """Strict-load the accepted eight-stratum smoke contract."""

    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("reconstruction contract must be UTF-8 JSON") from None
    top_fields = {
        "artifact_contract",
        "features",
        "resource_caps",
        "retry_policy",
        "schema_version",
        "selection_rule",
        "shared_run_spec",
        "sources",
        "states",
        "tasks",
    }
    if not isinstance(value, dict) or set(value) != top_fields:
        raise ContractValidationError("reconstruction contract fields drifted")
    if value["schema_version"] != "m5a-reconstruction-smoke-contract-v1":
        raise ContractValidationError("reconstruction contract schema version drifted")
    if value["resource_caps"] != {
        "active_execution_network_bytes": 0,
        "attempt_wall_seconds": 1800,
        "process_tree_peak_rss_bytes": 2 * 1024**3,
        "scratch_high_water_bytes": 512 * 1024**2,
        "state_cumulative_wall_seconds": 180,
        "vcpu": 2,
    }:
        raise ContractValidationError("reconstruction resource caps drifted")
    if value["retry_policy"] != {
        "automatic_retries": 0,
        "maximum_attempts": 2,
        "maximum_cumulative_wall_seconds": 3600,
        "retryable_failure_class": "transient-infrastructure",
    }:
        raise ContractValidationError("reconstruction retry policy drifted")
    selection = value["selection_rule"]
    if not isinstance(selection, dict) or selection.get("outcome_free") is not True:
        raise ContractValidationError("state selection is not explicitly outcome-free")
    if selection.get("seed") != 1729 or selection.get("state_count") != 8:
        raise ContractValidationError("state selection seed or count drifted")
    states = value["states"]
    if not isinstance(states, list) or len(states) != len(_STRATA):
        raise ContractValidationError("contract must contain exactly eight states")
    state_fields = {
        "acquisition_id",
        "actual_selected_ids_sha256",
        "checkpoint_sha256",
        "initial_sample_size",
        "model_id",
        "observation_prefix_sha256",
        "prefix_observation_count",
        "remaining_candidate_ids_sha256",
        "round",
        "run_attempt_sha256",
        "run_id",
        "source",
        "state_id",
        "stratum_ordinal",
        "task_id",
        "trajectory_sha256",
    }
    for ordinal, (state, stratum) in enumerate(zip(states, _STRATA, strict=True)):
        if not isinstance(state, dict) or set(state) != state_fields:
            raise ContractValidationError("state identity fields drifted")
        task_id, model_id, acquisition_id, initial_size = stratum
        denominator = (128 - initial_size) // 16
        expected_round = 1 + ordinal % denominator
        expected_source = "pilot" if initial_size == 32 else "conditional-expansion"
        if (
            state["state_id"] != f"S{ordinal}"
            or state["stratum_ordinal"] != ordinal
            or state["task_id"] != task_id
            or state["model_id"] != model_id
            or state["acquisition_id"] != acquisition_id
            or state["initial_sample_size"] != initial_size
            or state["round"] != expected_round
            or state["prefix_observation_count"] != initial_size + (expected_round - 1) * 16
            or state["source"] != expected_source
        ):
            raise ContractValidationError("state differs from the frozen ordinal rule")
        hash_fields = (
            "actual_selected_ids_sha256",
            "checkpoint_sha256",
            "observation_prefix_sha256",
            "remaining_candidate_ids_sha256",
            "run_attempt_sha256",
            "run_id",
            "trajectory_sha256",
        )
        if any(
            not isinstance(state[field], str) or not _SHA256.fullmatch(state[field])
            for field in hash_fields
        ):
            raise ContractValidationError("state contains an invalid SHA-256 identity")
    return MappingProxyType(cast(dict[str, Any], value))


def _expect_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        raise ReconstructionStop(f"{field} identity drifted")


def validate_historical_state(
    *,
    contract: Mapping[str, Any],
    state: Mapping[str, Any],
    trajectory_bytes: bytes,
    run_attempt_bytes: bytes,
    completion_marker_bytes: bytes,
    candidate_feature_bytes: bytes,
) -> ValidatedHistoricalState:
    """Validate one completion-marked trajectory before any reconstruction scoring."""

    _expect_equal(
        hashlib.sha256(trajectory_bytes).hexdigest(), state["trajectory_sha256"], "trajectory"
    )
    _expect_equal(
        hashlib.sha256(run_attempt_bytes).hexdigest(), state["run_attempt_sha256"], "RunAttempt"
    )
    if completion_marker_bytes != b"":
        raise ReconstructionStop("completion marker is not the exact empty marker")
    trajectory = load_replay_trajectory(trajectory_bytes)
    attempt = load_run_attempt_v1(run_attempt_bytes)
    run_spec = trajectory.run_spec
    source = contract["sources"][state["source"]]
    task = contract["tasks"][state["task_id"]]
    shared = contract["shared_run_spec"]
    for field, expected in shared.items():
        _expect_equal(getattr(run_spec, field), expected, f"RunSpec.{field}")
    for field, expected in task.items():
        _expect_equal(getattr(run_spec, field), expected, f"RunSpec.{field}")
    for field, expected in (
        ("run_id", state["run_id"]),
        ("task_id", state["task_id"]),
    ):
        _expect_equal(getattr(trajectory, field), expected, f"trajectory.{field}")
    for field, expected in (
        ("git_commit", source["git_commit"]),
        ("initial_sample_size", state["initial_sample_size"]),
        ("model_id", state["model_id"]),
        ("model_config_sha256", _MODEL_CONFIG_SHA256[state["model_id"]]),
        ("acquisition_id", state["acquisition_id"]),
        (
            "acquisition_config_sha256",
            _ACQUISITION_CONFIG_SHA256[state["acquisition_id"]],
        ),
    ):
        _expect_equal(getattr(run_spec, field), expected, f"RunSpec.{field}")
    attempt_value = attempt.value
    for field, expected in (
        ("attempt_id", "attempt-001"),
        ("run_id", state["run_id"]),
        ("status", "succeeded"),
        ("executed_git_commit", source["git_commit"]),
        ("executed_environment_digest", shared["environment_digest"]),
    ):
        _expect_equal(attempt_value[field], expected, f"RunAttempt.{field}")
    marker = attempt_value["completion_marker"]
    _expect_equal(marker["sha256"], hashlib.sha256(b"").hexdigest(), "completion marker")
    _expect_equal(marker["size_bytes"], 0, "completion marker size")
    trajectory_refs = [
        output
        for output in attempt_value["outputs"]
        if str(output["object_key"]).endswith("/trajectory.json")
    ]
    if len(trajectory_refs) != 1:
        raise ReconstructionStop("RunAttempt does not exact-reference one trajectory")
    _expect_equal(trajectory_refs[0]["sha256"], state["trajectory_sha256"], "trajectory output")
    _expect_equal(trajectory_refs[0]["size_bytes"], len(trajectory_bytes), "trajectory output size")

    feature = contract["features"][state["task_id"]]
    _expect_equal(
        hashlib.sha256(candidate_feature_bytes).hexdigest(), feature["sha256"], "candidate feature"
    )
    _expect_equal(len(candidate_feature_bytes), feature["size_bytes"], "candidate feature size")
    candidate_features = load_feature_matrix(
        candidate_feature_bytes,
        feature_definitions=sequence_feature_definitions(),
        expected_sha256=feature["sha256"],
    )
    _expect_equal(
        len(candidate_features.candidate_ids), feature["row_count"], "candidate feature row count"
    )

    state_identity = {
        "packet_sha256": source["packet_sha256"],
        "source_git_commit": source["git_commit"],
        "source_git_tree": source["git_tree"],
        "state_id": state["state_id"],
        "trajectory_sha256": state["trajectory_sha256"],
    }
    prepared = prepare_reconstruction_input(
        trajectory=trajectory,
        round_index=state["round"],
        candidate_ids=candidate_features.candidate_ids,
        state_identity=state_identity,
        candidate_feature_sha256=feature["sha256"],
    )
    prepared_value = _load_compact_json(prepared.input_bytes, artifact="reconstruction input")
    prepared_identity = prepared_value["input_identity"]
    for field in (
        "checkpoint_sha256",
        "observation_prefix_sha256",
        "remaining_candidate_ids_sha256",
    ):
        _expect_equal(prepared_identity[field], state[field], field)
    _expect_equal(
        len(prepared_value["observations"]),
        state["prefix_observation_count"],
        "observation prefix count",
    )
    _expect_equal(
        _sequence_sha256(prepared.actual_selected_ids),
        state["actual_selected_ids_sha256"],
        "actual selected IDs",
    )
    return ValidatedHistoricalState(
        trajectory=trajectory,
        candidate_features=candidate_features,
        prepared=prepared,
    )


def prepare_reconstruction_input(
    *,
    trajectory: ReplayTrajectory,
    round_index: int,
    candidate_ids: tuple[str, ...],
    state_identity: Mapping[str, object],
    candidate_feature_sha256: str,
) -> PreparedReconstruction:
    """Build a target-blind input from events strictly before ``round_index``."""

    if (
        isinstance(round_index, bool)
        or not isinstance(round_index, int)
        or not 1 <= round_index < len(trajectory.events)
    ):
        raise ContractValidationError("reconstruction round is outside acquired rounds")
    if (
        not candidate_ids
        or tuple(sorted(candidate_ids)) != candidate_ids
        or len(candidate_ids) != len(set(candidate_ids))
        or any(not _CANDIDATE_ID.fullmatch(candidate_id) for candidate_id in candidate_ids)
    ):
        raise ContractValidationError(
            "candidate universe must be non-empty ASCII-sorted unique lower-case SHA-256 IDs"
        )
    if not _CANDIDATE_ID.fullmatch(candidate_feature_sha256):
        raise ContractValidationError("candidate feature SHA-256 is invalid")

    prefix_events = trajectory.events[:round_index]
    observations = tuple(
        observation for event in prefix_events for observation in event.observations
    )
    observed_ids = tuple(observation.candidate_id for observation in observations)
    if len(observed_ids) != len(set(observed_ids)) or not set(observed_ids) <= set(candidate_ids):
        raise ContractValidationError("observation prefix differs from candidate universe")
    remaining_ids = tuple(
        candidate_id for candidate_id in candidate_ids if candidate_id not in set(observed_ids)
    )
    if len(remaining_ids) < trajectory.run_spec.batch_size:
        raise ContractValidationError("remaining candidate universe is smaller than batch size")

    observation_value = [
        {"candidate_id": observation.candidate_id, "value": observation.label}
        for observation in observations
    ]
    identity = dict(state_identity)
    identity.update(
        {
            "candidate_feature_sha256": candidate_feature_sha256,
            "checkpoint_sha256": prefix_events[-1].checkpoint_sha256,
            "observation_prefix_sha256": _sequence_sha256(observation_value),
            "remaining_candidate_ids_sha256": _sequence_sha256(remaining_ids),
            "run_id": trajectory.run_id,
            "run_spec_sha256": hashlib.sha256(
                trajectory.run_spec.canonical_json().encode("utf-8")
            ).hexdigest(),
        }
    )
    input_value = {
        "batch_size": trajectory.run_spec.batch_size,
        "input_identity": identity,
        "observations": observation_value,
        "remaining_candidate_ids": list(remaining_ids),
        "round": round_index,
        "run_spec": asdict(trajectory.run_spec),
        "schema_version": INPUT_SCHEMA_VERSION,
        "task_id": trajectory.task_id,
        "tie_break": "score-descending-then-candidate-id-ascending-ascii",
    }
    return PreparedReconstruction(
        input_bytes=compact_json_bytes(input_value),
        actual_selected_ids=trajectory.events[round_index].selected_candidate_ids,
    )


def ranking_artifact_bytes(
    *,
    input_value: Mapping[str, object],
    scores: Sequence[tuple[str, float]],
) -> bytes:
    """Encode a full remaining-pool ranking under the frozen exactness contract."""

    if input_value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ContractValidationError("reconstruction input schema version drifted")
    remaining = input_value.get("remaining_candidate_ids")
    batch_size = input_value.get("batch_size")
    identity = input_value.get("input_identity")
    if (
        not isinstance(remaining, list)
        or not remaining
        or any(not isinstance(item, str) or not _CANDIDATE_ID.fullmatch(item) for item in remaining)
        or len(remaining) != len(set(remaining))
        or isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= len(remaining)
        or not isinstance(identity, Mapping)
    ):
        raise ContractValidationError("reconstruction input identity or universe is invalid")
    if (
        len(scores) != len(remaining)
        or len({candidate_id for candidate_id, _ in scores}) != len(scores)
        or {candidate_id for candidate_id, _ in scores} != set(remaining)
    ):
        raise ContractValidationError("scores must exact-cover the remaining candidate universe")

    normalized: list[tuple[str, float]] = []
    for candidate_id, score in scores:
        if not isinstance(candidate_id, str) or not _CANDIDATE_ID.fullmatch(candidate_id):
            raise ContractValidationError("score candidate ID is invalid")
        converted = float(score)
        binary64_be_hex(converted)
        normalized.append((candidate_id, converted))
    ranked = sorted(normalized, key=lambda item: (-item[1], item[0]))
    value = {
        "input_identity": dict(identity),
        "ranking": [
            {
                "candidate_id": candidate_id,
                "score_binary64_be_hex": binary64_be_hex(score),
            }
            for candidate_id, score in ranked
        ],
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "top_k": [candidate_id for candidate_id, _ in ranked[:batch_size]],
    }
    return compact_json_bytes(value)


def compare_reconstruction_bytes(
    *,
    first: bytes,
    second: bytes,
    actual_selected_ids: tuple[str, ...],
) -> dict[str, object]:
    """Require two byte-identical artifacts and one exact ordered policy match."""

    if first != second:
        raise ReconstructionStop("repeated reconstructions are not byte-identical")
    value = _load_compact_json(first, artifact="ranking artifact")
    if set(value) != {"input_identity", "ranking", "schema_version", "top_k"}:
        raise ReconstructionStop("ranking artifact fields drifted")
    if value["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ReconstructionStop("ranking artifact schema version drifted")
    top_k = value["top_k"]
    if not isinstance(top_k, list) or tuple(top_k) != actual_selected_ids:
        raise ReconstructionStop("reconstructed top-k has a selected-ID/order mismatch")
    return {
        "artifact_sha256": hashlib.sha256(first).hexdigest(),
        "byte_identical": True,
        "ordered_top_k_exact_match": True,
        "top_k": top_k,
    }


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "INPUT_SCHEMA_VERSION",
    "PreparedReconstruction",
    "ReconstructionStop",
    "ValidatedHistoricalState",
    "binary64_be_hex",
    "compact_json_bytes",
    "compare_reconstruction_bytes",
    "load_reconstruction_contract",
    "prepare_reconstruction_input",
    "ranking_artifact_bytes",
    "validate_historical_state",
]
