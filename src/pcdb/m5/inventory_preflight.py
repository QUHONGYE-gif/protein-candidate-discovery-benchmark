"""Outcome-free extraction of the accepted M5A parent into M5B state identities."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from pcdb.contracts.experiment import ContractValidationError
from pcdb.m5.formal_reconstruction import FormalTrajectory, binary64_hex
from pcdb.m5.reconstruction_smoke import compact_json_bytes
from pcdb.m5.same_state import STATE_SCHEMA_VERSION

ACCEPTED_M5A_INVENTORY_SHA256 = "11c3ba0f64d9db8b9967c1df1e5c4ebbf3b5b0d855b472885564ddd0ce70573b"
ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST = (
    "863ddd6ce1e79542013efe338e7a65cef2bb35ed3c6c53645630ba7278c3732d"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TASK_ORDER = ("alpha-amylase", "ired")
_MODEL_ORDER = ("random-forest-sklearn-v1", "xgboost-cpu-v1")
_INITIAL_SIZES = (32, 64)
_RUN_SEEDS = tuple(range(1729, 1739))
_CANDIDATE_COUNTS = {"alpha-amylase": 2262, "ired": 2070}
_MODEL_EXPLORATION = {
    "random-forest-sklearn-v1": (
        "uncertainty-bootstrap-std-v1",
        "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6",
    ),
    "xgboost-cpu-v1": (
        "ucb-bootstrap-std-beta1-v1",
        "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d",
    ),
}
_GREEDY = (
    "greedy-predictive-mean-v1",
    "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15",
)


def _trajectory_key(item: FormalTrajectory) -> tuple[str, str, int, int]:
    spec = item.prepared.run_spec
    return (item.prepared.task_id, spec.model_id, spec.initial_sample_size, spec.seed)


def _policy(item: FormalTrajectory) -> str:
    spec = item.prepared.run_spec
    exploration = _MODEL_EXPLORATION.get(spec.model_id)
    if (
        exploration is not None
        and (
            spec.acquisition_id,
            spec.acquisition_config_sha256,
        )
        == exploration
    ):
        return "exploration"
    if (spec.acquisition_id, spec.acquisition_config_sha256) == _GREEDY:
        return "greedy"
    raise ContractValidationError("M5B selected trajectory policy identity drifted")


def _observation_prefix(item: FormalTrajectory) -> tuple[bytes, frozenset[str]]:
    events = item.trajectory.events
    initial_size = item.prepared.run_spec.initial_sample_size
    if len(events) < 2 or events[0].round != 0 or events[1].round != 1:
        raise ContractValidationError("M5B first-decision event boundary drifted")
    observations = events[0].observations
    if len(observations) != initial_size:
        raise ContractValidationError("M5B observation prefix size drifted")
    ids = tuple(observation.candidate_id for observation in observations)
    if len(set(ids)) != initial_size or tuple(events[0].selected_candidate_ids) != ids:
        raise ContractValidationError("M5B observation prefix identity drifted")
    canonical = compact_json_bytes(
        [
            {
                "candidate_id": observation.candidate_id,
                "value_binary64_be_hex": binary64_hex(observation.label),
            }
            for observation in sorted(observations, key=lambda value: value.candidate_id)
        ]
    )
    return canonical, frozenset(ids)


def _validate_pair_identity(
    exploration: FormalTrajectory,
    greedy: FormalTrajectory,
) -> None:
    for item in (exploration, greedy):
        if (
            item.prepared.task_id != item.trajectory.task_id
            or item.prepared.run_spec != item.trajectory.run_spec
            or item.source != "conditional-expansion"
            or not _SHA256.fullmatch(item.attempt_sha256)
            or not _SHA256.fullmatch(item.trajectory_sha256)
        ):
            raise ContractValidationError("M5B formal trajectory source identity drifted")
    ignored = {"acquisition_id", "acquisition_config_sha256"}
    left = asdict(exploration.prepared.run_spec)
    right = asdict(greedy.prepared.run_spec)
    if {key: value for key, value in left.items() if key not in ignored} != {
        key: value for key, value in right.items() if key not in ignored
    }:
        raise ContractValidationError("M5B paired RunSpec identity drifted")


def extract_first_decision_state_payloads(
    selected: Sequence[FormalTrajectory],
    *,
    candidate_ids_by_task: Mapping[str, Sequence[str]],
    parent_inventory_sha256: str,
    source_environment_digest: str,
) -> tuple[bytes, ...]:
    """Extract only outcome-free first-decision identities from the accepted M5A parent."""

    if parent_inventory_sha256 != ACCEPTED_M5A_INVENTORY_SHA256:
        raise ContractValidationError("M5B parent inventory identity drifted")
    if source_environment_digest != ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST:
        raise ContractValidationError("M5B source environment identity drifted")
    if set(candidate_ids_by_task) != set(_TASK_ORDER):
        raise ContractValidationError("M5B candidate universe task coverage drifted")
    universes: dict[str, tuple[str, ...]] = {}
    for task_id in _TASK_ORDER:
        candidate_ids = tuple(candidate_ids_by_task[task_id])
        if (
            len(candidate_ids) != _CANDIDATE_COUNTS[task_id]
            or len(set(candidate_ids)) != len(candidate_ids)
            or any(
                not isinstance(item, str) or not _SHA256.fullmatch(item) for item in candidate_ids
            )
        ):
            raise ContractValidationError("M5B candidate universe identity drifted")
        universes[task_id] = candidate_ids

    grouped: defaultdict[tuple[str, str, int, int], dict[str, FormalTrajectory]] = defaultdict(dict)
    for item in selected:
        key = _trajectory_key(item)
        policy = _policy(item)
        if key[0] not in _TASK_ORDER or key[1] not in _MODEL_ORDER:
            raise ContractValidationError("M5B trajectory cell identity drifted")
        if key[2] not in _INITIAL_SIZES or key[3] not in _RUN_SEEDS or policy in grouped[key]:
            raise ContractValidationError("M5B trajectory cell coverage drifted")
        grouped[key][policy] = item
    expected = {
        (task_id, model_id, initial_size, seed)
        for task_id in _TASK_ORDER
        for model_id in _MODEL_ORDER
        for initial_size in _INITIAL_SIZES
        for seed in _RUN_SEEDS
    }
    if (
        set(grouped) != expected
        or len(selected) != 160
        or any(set(pair) != {"exploration", "greedy"} for pair in grouped.values())
    ):
        raise ContractValidationError("M5B trajectory inventory must exact-cover 80 pairs")

    payloads: list[bytes] = []
    for key in sorted(grouped):
        task_id, model_id, initial_size, run_seed = key
        exploration = grouped[key]["exploration"]
        greedy = grouped[key]["greedy"]
        _validate_pair_identity(exploration, greedy)
        exploration_prefix, exploration_prefix_ids = _observation_prefix(exploration)
        greedy_prefix, greedy_prefix_ids = _observation_prefix(greedy)
        if exploration_prefix != greedy_prefix or exploration_prefix_ids != greedy_prefix_ids:
            raise ContractValidationError("M5B paired observation prefix drifted")
        universe = universes[task_id]
        universe_set = frozenset(universe)
        if not exploration_prefix_ids <= universe_set:
            raise ContractValidationError("M5B observation prefix is outside candidate universe")
        for item in (exploration, greedy):
            selected_ids = tuple(item.trajectory.events[1].selected_candidate_ids)
            if (
                len(selected_ids) != 16
                or len(set(selected_ids)) != 16
                or not frozenset(selected_ids) <= universe_set - exploration_prefix_ids
            ):
                raise ContractValidationError("M5B first-decision batch identity drifted")
        task_binding = {
            field: getattr(exploration.prepared.run_spec, field)
            for field in (
                "dataset_manifest_sha256",
                "experiment_config_sha256",
                "feature_sha256",
                "processing_manifest_sha256",
                "split_sha256",
            )
        }
        model_binding = _MODEL_EXPLORATION[model_id]
        state = {
            "attempt_sha256_exploration": exploration.attempt_sha256,
            "attempt_sha256_greedy": greedy.attempt_sha256,
            "batch_size": 16,
            "candidate_universe_sha256": hashlib.sha256(
                compact_json_bytes(sorted(universe))
            ).hexdigest(),
            **task_binding,
            "exploration_acquisition_config_sha256": model_binding[1],
            "exploration_acquisition_id": model_binding[0],
            "exploration_run_id": exploration.prepared.run_spec.run_id,
            "exploration_trajectory_sha256": exploration.trajectory_sha256,
            "greedy_acquisition_config_sha256": _GREEDY[1],
            "greedy_acquisition_id": _GREEDY[0],
            "greedy_run_id": greedy.prepared.run_spec.run_id,
            "greedy_trajectory_sha256": greedy.trajectory_sha256,
            "initial_sample_size": initial_size,
            "model_config_sha256": exploration.prepared.run_spec.model_config_sha256,
            "model_id": model_id,
            "observation_prefix_sha256": hashlib.sha256(exploration_prefix).hexdigest(),
            "parent_inventory_sha256": parent_inventory_sha256,
            "remaining_pool_sha256": hashlib.sha256(
                compact_json_bytes(sorted(universe_set - exploration_prefix_ids))
            ).hexdigest(),
            "round_index": 1,
            "run_seed": run_seed,
            "schema_version": STATE_SCHEMA_VERSION,
            "source_environment_digest": source_environment_digest,
            "task_id": task_id,
        }
        payloads.append(compact_json_bytes(state))
    return tuple(payloads)
