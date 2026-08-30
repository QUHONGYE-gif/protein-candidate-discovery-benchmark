"""Synthetic-only contract checks for the M5B same-state intervention."""

from __future__ import annotations

import hashlib
import json

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.m5.reconstruction_smoke import compact_json_bytes
from pcdb.m5.same_state import build_state_inventory, load_same_state, matched_random_batch

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

_MODEL_BINDINGS = {
    "random-forest-sklearn-v1": (
        "418b0fd6189fd9b3987620b05021e5ec5ff3018c074024cd13874cd3b9845312",
        "uncertainty-bootstrap-std-v1",
        "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6",
    ),
    "xgboost-cpu-v1": (
        "31eb5bc146f4e810027de8870fa5632e09a5427bd8dddfee9261572c1e3473f2",
        "ucb-bootstrap-std-beta1-v1",
        "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d",
    ),
}


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _state_payload(
    *,
    task_id: str = "alpha-amylase",
    model_id: str = "random-forest-sklearn-v1",
    initial_sample_size: int = 32,
    run_seed: int = 1729,
) -> dict[str, object]:
    identity = f"{task_id}:{model_id}:{initial_sample_size}:{run_seed}"
    model_config, acquisition_id, acquisition_config = _MODEL_BINDINGS[model_id]
    return {
        "attempt_sha256_exploration": _digest(f"{identity}:exploration-attempt"),
        "attempt_sha256_greedy": _digest(f"{identity}:greedy-attempt"),
        "batch_size": 16,
        "candidate_universe_sha256": _digest(f"{task_id}:candidate-universe"),
        **_TASK_BINDINGS[task_id],
        "exploration_acquisition_config_sha256": acquisition_config,
        "exploration_acquisition_id": acquisition_id,
        "exploration_run_id": _digest(f"{identity}:exploration-run"),
        "exploration_trajectory_sha256": _digest(f"{identity}:exploration-trajectory"),
        "greedy_acquisition_config_sha256": (
            "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"
        ),
        "greedy_acquisition_id": "greedy-predictive-mean-v1",
        "greedy_run_id": _digest(f"{identity}:greedy-run"),
        "greedy_trajectory_sha256": _digest(f"{identity}:greedy-trajectory"),
        "initial_sample_size": initial_sample_size,
        "model_config_sha256": model_config,
        "model_id": model_id,
        "observation_prefix_sha256": _digest(f"{identity}:observation-prefix"),
        "parent_inventory_sha256": (
            "11c3ba0f64d9db8b9967c1df1e5c4ebbf3b5b0d855b472885564ddd0ce70573b"
        ),
        "remaining_pool_sha256": _digest(f"{identity}:remaining-pool"),
        "round_index": 1,
        "run_seed": run_seed,
        "schema_version": "m5b-alpha-ired-state-v2",
        "source_environment_digest": _digest("synthetic-environment"),
        "task_id": task_id,
    }


def test_canonical_state_bytes_determine_the_only_state_id() -> None:
    payload = compact_json_bytes(_state_payload())

    state = load_same_state(payload)

    assert state.canonical_bytes == payload
    assert state.state_id == hashlib.sha256(payload).hexdigest()
    assert state.payload["task_id"] == "alpha-amylase"


def test_state_loader_rejects_task_identity_drift_before_truth_read() -> None:
    value = _state_payload()
    value["dataset_manifest_sha256"] = "b" * 64

    with pytest.raises(ContractValidationError, match="task binding"):
        load_same_state(compact_json_bytes(value))


def test_inventory_exact_covers_and_orders_the_80_outcome_free_states() -> None:
    payloads = [
        compact_json_bytes(
            _state_payload(
                task_id=task_id,
                model_id=model_id,
                initial_sample_size=initial_sample_size,
                run_seed=run_seed,
            )
        )
        for task_id in reversed(tuple(_TASK_BINDINGS))
        for model_id in reversed(tuple(_MODEL_BINDINGS))
        for initial_sample_size in (64, 32)
        for run_seed in reversed(range(1729, 1739))
    ]

    inventory = build_state_inventory(payloads)
    value = json.loads(inventory.canonical_bytes)

    assert len(inventory.states) == 80
    assert inventory.sha256 == hashlib.sha256(inventory.canonical_bytes).hexdigest()
    assert [
        (
            item["state_payload"]["task_id"],
            item["state_payload"]["model_id"],
            item["state_payload"]["initial_sample_size"],
            item["state_payload"]["run_seed"],
            item["state_id"],
        )
        for item in value["states"]
    ] == sorted(
        (
            item["state_payload"]["task_id"],
            item["state_payload"]["model_id"],
            item["state_payload"]["initial_sample_size"],
            item["state_payload"]["run_seed"],
            item["state_id"],
        )
        for item in value["states"]
    )


def test_matched_random_preserves_exact_greedy_overlap_and_frozen_draw() -> None:
    state = load_same_state(compact_json_bytes(_state_payload()))
    remaining_pool = tuple(_digest(f"candidate:{index}") for index in range(64))
    greedy = remaining_pool[:16]
    exploration = (*greedy[:4], *remaining_pool[16:28])

    selection = matched_random_batch(
        state_id=state.state_id,
        greedy_ids=greedy,
        exploration_ids=exploration,
        remaining_pool_ids=remaining_pool,
        replicate=0,
    )

    seed_bytes = hashlib.sha256(
        b"m5b-alpha-ired-one-step-v2\0matched-random\0"
        + bytes.fromhex(state.state_id)
        + (0).to_bytes(4, "big")
    ).digest()[:8]
    expected_draw = tuple(
        sorted(
            set(remaining_pool) - set(greedy),
            key=lambda candidate_id: (
                hashlib.sha256(seed_bytes + bytes.fromhex(candidate_id)).digest(),
                candidate_id,
            ),
        )[:12]
    )
    exact_overlap = frozenset(exploration) & frozenset(greedy)

    assert selection.sampled_ids == expected_draw
    assert selection.selected_ids == exact_overlap | frozenset(expected_draw)
    assert selection.selected_ids & frozenset(greedy) == exact_overlap
    assert len(selection.selected_ids) == 16
