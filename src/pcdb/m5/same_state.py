"""Synthetic-only primitives for the bounded M5B same-state intervention."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError
from pcdb.m5.reconstruction_smoke import compact_json_bytes

STATE_SCHEMA_VERSION = "m5b-alpha-ired-state-v2"
INVENTORY_SCHEMA_VERSION = "m5b-alpha-ired-state-inventory-v2"
PARENT_INVENTORY_SHA256 = "11c3ba0f64d9db8b9967c1df1e5c4ebbf3b5b0d855b472885564ddd0ce70573b"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MATCHED_RANDOM_PREFIX = b"m5b-alpha-ired-one-step-v2\0matched-random\0"

_TASK_BINDINGS = {
    "alpha-amylase": {
        "dataset_manifest_sha256": (
            "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac"
        ),
        "experiment_config_sha256": (
            "193f8ee0102a654e63cd7ea31fc77a93bcf7f5fae0c0305570117eac54c097aa"
        ),
        "feature_sha256": "9a89f12e7c521462a412320937f4455200c75282fae8196d1302b0eac0bf5596",
        "processing_manifest_sha256": (
            "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec"
        ),
        "split_sha256": "c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a",
    },
    "ired": {
        "dataset_manifest_sha256": (
            "37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643"
        ),
        "experiment_config_sha256": (
            "7bfdc48ca2f4d41f039b8f119c3dca5910e9eb26c156cdee53f584b4af7c38fa"
        ),
        "feature_sha256": "1573d70651f2f6450bc354960b170dff2b8b19fd4766f1e0fc6a9622f4e6cb6b",
        "processing_manifest_sha256": (
            "929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba"
        ),
        "split_sha256": "9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e",
    },
}

_STATE_FIELDS = {
    "attempt_sha256_exploration",
    "attempt_sha256_greedy",
    "batch_size",
    "candidate_universe_sha256",
    "dataset_manifest_sha256",
    "experiment_config_sha256",
    "exploration_acquisition_config_sha256",
    "exploration_acquisition_id",
    "exploration_run_id",
    "exploration_trajectory_sha256",
    "feature_sha256",
    "greedy_acquisition_config_sha256",
    "greedy_acquisition_id",
    "greedy_run_id",
    "greedy_trajectory_sha256",
    "initial_sample_size",
    "model_config_sha256",
    "model_id",
    "observation_prefix_sha256",
    "parent_inventory_sha256",
    "processing_manifest_sha256",
    "remaining_pool_sha256",
    "round_index",
    "run_seed",
    "schema_version",
    "source_environment_digest",
    "split_sha256",
    "task_id",
}


@dataclass(frozen=True, slots=True)
class SameState:
    """One strict canonical M5B state and its only content-derived identity."""

    state_id: str
    payload: MappingProxyType[str, Any]
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class StateInventory:
    """Exact-cover, outcome-free child inventory for later execution review."""

    states: tuple[SameState, ...]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class MatchedRandomBatch:
    """One deterministic matched control represented as a candidate set."""

    selected_ids: frozenset[str]
    sampled_ids: tuple[str, ...]


def load_same_state(payload: bytes) -> SameState:
    """Load one exact-key canonical state without reading branch truth."""

    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M5B state must be canonical UTF-8 JSON") from None
    if not isinstance(value, dict) or compact_json_bytes(value) != payload:
        raise ContractValidationError("M5B state bytes are not canonical")
    if set(value) != _STATE_FIELDS:
        raise ContractValidationError("M5B state fields drifted")
    if value["schema_version"] != STATE_SCHEMA_VERSION:
        raise ContractValidationError("M5B state schema version drifted")
    task_binding = _TASK_BINDINGS.get(value["task_id"])
    if task_binding is None or any(
        value[field] != expected for field, expected in task_binding.items()
    ):
        raise ContractValidationError("M5B task binding drifted")
    canonical = bytes(payload)
    return SameState(
        state_id=hashlib.sha256(canonical).hexdigest(),
        payload=MappingProxyType(cast(dict[str, Any], value)),
        canonical_bytes=canonical,
    )


def build_state_inventory(payloads: Sequence[bytes]) -> StateInventory:
    """Build the canonical exact-cover 80-state inventory from identity-only bytes."""

    states = tuple(load_same_state(payload) for payload in payloads)
    expected = {
        (task_id, model_id, initial_sample_size, run_seed)
        for task_id in _TASK_BINDINGS
        for model_id in (
            "random-forest-sklearn-v1",
            "xgboost-cpu-v1",
        )
        for initial_sample_size in (32, 64)
        for run_seed in range(1729, 1739)
    }
    actual = {
        (
            state.payload["task_id"],
            state.payload["model_id"],
            state.payload["initial_sample_size"],
            state.payload["run_seed"],
        )
        for state in states
    }
    if len(states) != 80 or len({state.state_id for state in states}) != 80 or actual != expected:
        raise ContractValidationError("M5B inventory does not exact-cover 80 unique states")
    if any(state.payload["parent_inventory_sha256"] != PARENT_INVENTORY_SHA256 for state in states):
        raise ContractValidationError("M5B parent inventory identity drifted")
    ordered = tuple(
        sorted(
            states,
            key=lambda state: (
                state.payload["task_id"],
                state.payload["model_id"],
                state.payload["initial_sample_size"],
                state.payload["run_seed"],
                state.state_id,
            ),
        )
    )
    canonical = compact_json_bytes(
        {
            "parent_inventory_sha256": PARENT_INVENTORY_SHA256,
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "states": [
                {
                    "state_id": state.state_id,
                    "state_payload": dict(state.payload),
                }
                for state in ordered
            ],
        }
    )
    return StateInventory(
        states=ordered,
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def matched_random_batch(
    *,
    state_id: str,
    greedy_ids: Sequence[str],
    exploration_ids: Sequence[str],
    remaining_pool_ids: Sequence[str],
    replicate: int,
) -> MatchedRandomBatch:
    """Select the v2 matched control without consuming branch outcomes."""

    if not _SHA256.fullmatch(state_id):
        raise ContractValidationError("M5B state ID must be lowercase SHA-256")
    if isinstance(replicate, bool) or not isinstance(replicate, int) or not 0 <= replicate < 64:
        raise ContractValidationError("M5B matched-random replicate must be in 0..63")
    greedy = tuple(greedy_ids)
    exploration = tuple(exploration_ids)
    pool = tuple(remaining_pool_ids)
    all_ids = (*greedy, *exploration, *pool)
    if any(
        not isinstance(candidate_id, str) or not _SHA256.fullmatch(candidate_id)
        for candidate_id in all_ids
    ):
        raise ContractValidationError("M5B candidate ID must be lowercase SHA-256")
    if len(greedy) != 16 or len(set(greedy)) != 16:
        raise ContractValidationError("M5B Greedy branch must be exact Top-16")
    if len(exploration) != 16 or len(set(exploration)) != 16:
        raise ContractValidationError("M5B Exploration branch must be exact Top-16")
    if len(set(pool)) != len(pool):
        raise ContractValidationError("M5B remaining pool contains duplicate candidates")
    pool_set = frozenset(pool)
    greedy_set = frozenset(greedy)
    exploration_set = frozenset(exploration)
    if not greedy_set <= pool_set or not exploration_set <= pool_set:
        raise ContractValidationError("M5B branch selection is outside the remaining pool")
    overlap = greedy_set & exploration_set
    draw_count = 16 - len(overlap)
    eligible = pool_set - greedy_set
    if len(eligible) < draw_count:
        raise ContractValidationError("M5B matched-random eligible pool is too small")
    seed_bytes = hashlib.sha256(
        _MATCHED_RANDOM_PREFIX + bytes.fromhex(state_id) + replicate.to_bytes(4, "big")
    ).digest()[:8]
    sampled = tuple(
        sorted(
            eligible,
            key=lambda candidate_id: (
                hashlib.sha256(seed_bytes + bytes.fromhex(candidate_id)).digest(),
                candidate_id,
            ),
        )[:draw_count]
    )
    selected = overlap | frozenset(sampled)
    if len(selected) != 16 or selected & greedy_set != overlap:
        raise ContractValidationError("M5B matched-random exact overlap drifted")
    return MatchedRandomBatch(selected_ids=selected, sampled_ids=sampled)
