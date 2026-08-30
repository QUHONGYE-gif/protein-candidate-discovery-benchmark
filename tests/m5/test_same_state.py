"""Synthetic-only contract checks for the M5B same-state intervention."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.m5.reconstruction_smoke import compact_json_bytes
from pcdb.m5.same_state import (
    AttemptRecord,
    BranchOutcome,
    StateEffects,
    TaskContrastSummary,
    TaskTaxonomyInput,
    bootstrap_index,
    bootstrap_task_effects,
    build_state_inventory,
    classify_contrast,
    classify_taxonomy,
    evaluate_isolated_branches,
    load_same_state,
    matched_random_batch,
    matched_replicate_bootstrap_mean,
    percentile_type7,
    select_canonical_attempt,
)

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


_EXPECTED_BRANCH_IDS = ("E", "G", *(f"M{replicate:02d}" for replicate in range(64)))


def _complete_branch_batches() -> dict[str, frozenset[str]]:
    return {
        branch_id: frozenset(_digest(f"{branch_id}:{index}") for index in range(16))
        for branch_id in _EXPECTED_BRANCH_IDS
    }


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


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("batch_size", 15),
        ("round_index", 2),
        ("run_seed", 1739),
        ("initial_sample_size", 48),
        ("parent_inventory_sha256", "b" * 64),
        ("model_config_sha256", "b" * 64),
        ("exploration_acquisition_config_sha256", "b" * 64),
        ("attempt_sha256_greedy", "B" * 64),
    ),
)
def test_state_loader_rejects_v2_scientific_identity_drift(
    field: str,
    value: object,
) -> None:
    state = _state_payload()
    state[field] = value

    with pytest.raises(ContractValidationError, match="identity"):
        load_same_state(compact_json_bytes(state))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("batch_size", 16.0),
        ("round_index", 1.0),
        ("initial_sample_size", 32.0),
        ("run_seed", 1729.0),
        ("batch_size", True),
        ("round_index", True),
        ("initial_sample_size", True),
        ("run_seed", True),
    ),
)
def test_state_loader_rejects_non_integer_scientific_identity_aliases(
    field: str,
    value: object,
) -> None:
    state = _state_payload()
    state[field] = value

    with pytest.raises(ContractValidationError, match="identity"):
        load_same_state(compact_json_bytes(state))


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


@dataclass
class _SpyEvaluator:
    evaluator_id: int
    calls: list[tuple[tuple[str, ...], frozenset[str]]] = field(default_factory=list)

    def evaluate_batch(
        self,
        *,
        observation_prefix_ids: tuple[str, ...],
        selected_ids: frozenset[str],
    ) -> BranchOutcome:
        self.calls.append((observation_prefix_ids, selected_ids))
        return BranchOutcome(
            actual_high_value_count=1,
            best_found_improvement=0.2,
            high_value_recall_increment=0.1,
            simple_regret_reduction=0.2,
        )


@dataclass(frozen=True)
class _FixedOutcomeEvaluator:
    outcome: BranchOutcome

    def evaluate_batch(
        self,
        *,
        observation_prefix_ids: tuple[str, ...],
        selected_ids: frozenset[str],
    ) -> BranchOutcome:
        return self.outcome


def test_each_branch_uses_a_fresh_evaluator_from_the_same_immutable_prefix() -> None:
    prefix = tuple(_digest(f"prefix:{index}") for index in range(4))
    branch_batches = _complete_branch_batches()
    evaluators: list[_SpyEvaluator] = []

    def factory() -> _SpyEvaluator:
        evaluator = _SpyEvaluator(len(evaluators) + 1)
        evaluators.append(evaluator)
        return evaluator

    outcomes = evaluate_isolated_branches(
        evaluator_factory=factory,
        observation_prefix_ids=prefix,
        branch_batches=branch_batches,
    )

    assert tuple(outcomes) == _EXPECTED_BRANCH_IDS
    assert len(evaluators) == 66
    assert len({id(evaluator) for evaluator in evaluators}) == 66
    assert [evaluator.calls for evaluator in evaluators] == [
        [(prefix, branch_batches[branch_id])] for branch_id in _EXPECTED_BRANCH_IDS
    ]
    assert outcomes["E"].high_value_recall_increment == 0.1
    assert outcomes["G"].high_value_recall_increment == 0.1
    assert outcomes["M63"].high_value_recall_increment == 0.1


@pytest.mark.parametrize(
    "branch_ids",
    (
        _EXPECTED_BRANCH_IDS[1:],
        (_EXPECTED_BRANCH_IDS[0], *_EXPECTED_BRANCH_IDS[2:]),
        tuple(branch_id for branch_id in _EXPECTED_BRANCH_IDS if branch_id != "M17"),
        tuple(
            "matched-17" if branch_id == "M17" else branch_id for branch_id in _EXPECTED_BRANCH_IDS
        ),
        (*_EXPECTED_BRANCH_IDS, "extra"),
        (*_EXPECTED_BRANCH_IDS, "M64"),
    ),
)
def test_branch_dispatch_rejects_any_non_exact_branch_cover_before_evaluation(
    branch_ids: tuple[str, ...],
) -> None:
    factory_calls = 0

    def factory() -> _SpyEvaluator:
        nonlocal factory_calls
        factory_calls += 1
        return _SpyEvaluator(factory_calls)

    with pytest.raises(ContractValidationError, match="exact-cover"):
        evaluate_isolated_branches(
            evaluator_factory=factory,
            observation_prefix_ids=tuple(_digest(f"prefix:{index}") for index in range(4)),
            branch_batches={
                branch_id: frozenset(_digest(f"{branch_id}:{index}") for index in range(16))
                for branch_id in branch_ids
            },
        )

    assert factory_calls == 0


def test_branch_dispatch_rejects_a_shared_evaluator_instance() -> None:
    prefix = tuple(_digest(f"prefix:{index}") for index in range(4))
    branches = _complete_branch_batches()
    shared = _SpyEvaluator(1)

    with pytest.raises(ContractValidationError, match="branch-local"):
        evaluate_isolated_branches(
            evaluator_factory=lambda: shared,
            observation_prefix_ids=prefix,
            branch_batches=branches,
        )


def test_branch_dispatch_keeps_fresh_evaluators_alive_for_the_complete_call() -> None:
    prefix = tuple(_digest(f"prefix:{index}") for index in range(4))
    branches = _complete_branch_batches()
    next_id = iter(range(1, 67))

    outcomes = evaluate_isolated_branches(
        evaluator_factory=lambda: _SpyEvaluator(next(next_id)),
        observation_prefix_ids=prefix,
        branch_batches=branches,
    )

    assert tuple(outcomes) == _EXPECTED_BRANCH_IDS


@pytest.mark.parametrize(
    "outcome",
    (
        BranchOutcome(1, -0.1, 0.1, 0.1),
        BranchOutcome(1, 0.1, -0.1, 0.1),
        BranchOutcome(1, 0.1, 1.1, 0.1),
        BranchOutcome(1, 0.1, 0.1, -0.1),
        BranchOutcome(1, float("nan"), 0.1, 0.1),
        BranchOutcome(1, 0.1, float("inf"), 0.1),
        BranchOutcome(1, 0.1, 0.1, float("-inf")),
    ),
)
def test_branch_dispatch_rejects_impossible_aggregate_metrics(
    outcome: BranchOutcome,
) -> None:
    with pytest.raises(ContractValidationError, match="aggregate outcome"):
        evaluate_isolated_branches(
            evaluator_factory=lambda: _FixedOutcomeEvaluator(outcome),
            observation_prefix_ids=tuple(_digest(f"prefix:{index}") for index in range(4)),
            branch_batches=_complete_branch_batches(),
        )


@pytest.mark.parametrize("actual_high_value_count", (1.0, 1.5, True, "one"))
def test_branch_dispatch_rejects_non_integer_high_value_counts(
    actual_high_value_count: object,
) -> None:
    outcome = BranchOutcome(
        actual_high_value_count=cast(Any, actual_high_value_count),
        best_found_improvement=0.1,
        high_value_recall_increment=0.1,
        simple_regret_reduction=0.1,
    )

    with pytest.raises(ContractValidationError, match="aggregate outcome"):
        evaluate_isolated_branches(
            evaluator_factory=lambda: _FixedOutcomeEvaluator(outcome),
            observation_prefix_ids=tuple(_digest(f"prefix:{index}") for index in range(4)),
            branch_batches=_complete_branch_batches(),
        )


@pytest.mark.parametrize(
    "outcome",
    (
        BranchOutcome(0, 0.0, 0.0, 0.0),
        BranchOutcome(16, 0.0, 1.0, 0.0),
    ),
)
def test_branch_dispatch_accepts_valid_aggregate_metric_boundaries(
    outcome: BranchOutcome,
) -> None:
    outcomes = evaluate_isolated_branches(
        evaluator_factory=lambda: _FixedOutcomeEvaluator(outcome),
        observation_prefix_ids=tuple(_digest(f"prefix:{index}") for index in range(4)),
        branch_batches=_complete_branch_batches(),
    )

    assert tuple(outcomes) == _EXPECTED_BRANCH_IDS
    assert set(outcomes.values()) == {outcome}


def test_same_state_module_never_calls_hidden_oracle_reveal() -> None:
    module_path = Path(__file__).resolve().parents[2] / "src/pcdb/m5/same_state.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    reveal_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reveal"
    ]
    assert reveal_calls == []


def test_counter_based_bootstrap_index_matches_the_v2_golden_formula() -> None:
    payload = ("seed-cluster", "alpha-amylase", 32, 17, 4)
    golden_bytes = compact_json_bytes(["m5b-alpha-ired-one-step-v2-bootstrap", *payload])
    expected = int.from_bytes(hashlib.sha256(golden_bytes).digest()[:8], "big") % 10

    assert bootstrap_index(10, payload) == expected
    assert bootstrap_index(10, payload) == bootstrap_index(10, payload)


def test_type7_percentile_uses_the_frozen_9875_percent_ci_endpoints() -> None:
    draws = (0.0, 10.0, 20.0, 30.0, 40.0)

    assert percentile_type7(draws, 0.00625) == 0.25
    assert percentile_type7(draws, 0.99375) == 39.75
    assert percentile_type7(draws, 0.5) == 20.0


@pytest.mark.parametrize(
    ("lower", "upper", "expected"),
    (
        (0.11, 0.30, "SUPERIOR"),
        (-0.30, -0.11, "INFERIOR"),
        (-0.10, 0.10, "EQUIVALENT"),
        (-0.09, 0.20, "NONINFERIOR_ONLY"),
        (-0.20, 0.20, "INCONCLUSIVE"),
    ),
)
def test_contrast_status_has_one_frozen_boundary_result(
    lower: float,
    upper: float,
    expected: str,
) -> None:
    assert classify_contrast(lower=lower, upper=upper, delta=0.10) == expected


def test_task_first_bootstrap_runs_the_frozen_20000_draw_protocol() -> None:
    effects = tuple(
        StateEffects(
            exploration_minus_greedy=0.20 if task_id == "alpha-amylase" else -0.20,
            exploration_minus_matched=((0.15 if task_id == "alpha-amylase" else -0.15),) * 64,
            initial_sample_size=initial_sample_size,
            model_id=model_id,
            run_seed=run_seed,
            state_id=_digest(f"effect:{task_id}:{model_id}:{initial_sample_size}:{run_seed}"),
            task_id=task_id,
        )
        for task_id in _TASK_BINDINGS
        for model_id in _MODEL_BINDINGS
        for initial_sample_size in (32, 64)
        for run_seed in range(1729, 1739)
    )

    summaries = {
        (summary.task_id, summary.contrast): summary for summary in bootstrap_task_effects(effects)
    }

    assert set(summaries) == {
        ("alpha-amylase", "E-G"),
        ("alpha-amylase", "E-M"),
        ("ired", "E-G"),
        ("ired", "E-M"),
    }
    assert all(summary.bootstrap_draws == 20_000 for summary in summaries.values())
    assert summaries[("alpha-amylase", "E-G")].status == "SUPERIOR"
    assert summaries[("alpha-amylase", "E-M")].status == "SUPERIOR"
    assert summaries[("ired", "E-G")].status == "INFERIOR"
    assert summaries[("ired", "E-M")].status == "INFERIOR"
    assert summaries[("alpha-amylase", "E-G")].ci_lower == pytest.approx(0.20)
    assert summaries[("alpha-amylase", "E-G")].ci_upper == pytest.approx(0.20)


def test_matched_replicate_bootstrap_mean_uses_all_64_frozen_counters() -> None:
    state = StateEffects(
        exploration_minus_greedy=0.0,
        exploration_minus_matched=tuple(index / 64 for index in range(64)),
        initial_sample_size=32,
        model_id="random-forest-sklearn-v1",
        run_seed=1729,
        state_id=_digest("matched-bootstrap-state"),
        task_id="alpha-amylase",
    )
    expected = (
        sum(
            state.exploration_minus_matched[
                bootstrap_index(
                    64,
                    ("m-replicate", state.state_id, 23, 2, replicate_slot),
                )
            ]
            for replicate_slot in range(64)
        )
        / 64
    )

    assert matched_replicate_bootstrap_mean(state, draw=23, occurrence=2) == pytest.approx(expected)


def _contrast_summary(
    task_id: str,
    contrast: str,
    status: str,
    point_estimate: float | None = None,
    cell_point_estimates: tuple[float, ...] | None = None,
) -> TaskContrastSummary:
    defaults = {
        "SUPERIOR": (0.10, 0.10, 0.10),
        "INFERIOR": (-0.10, -0.10, -0.10),
        "EQUIVALENT": (0.0, 0.0, 0.0),
        "NONINFERIOR_ONLY": (0.02, -0.04, 0.08),
        "INCONCLUSIVE": (0.0, -0.10, 0.10),
    }
    default_point, lower, upper = defaults[status]
    resolved_point = default_point if point_estimate is None else point_estimate
    return TaskContrastSummary(
        bootstrap_draws=20_000,
        cell_point_estimates=cell_point_estimates or (resolved_point,) * 4,
        ci_lower=lower if point_estimate is None else resolved_point,
        ci_upper=upper if point_estimate is None else resolved_point,
        contrast=contrast,
        point_estimate=resolved_point,
        status=status,
        task_id=task_id,
    )


def _taxonomy_input(
    task_id: str,
    *,
    eg_status: str,
    em_status: str,
    differences: tuple[int, ...],
) -> TaskTaxonomyInput:
    e_g = _contrast_summary(task_id, "E-G", eg_status)
    e_m = _contrast_summary(task_id, "E-M", em_status)
    return TaskTaxonomyInput(
        actual_high_value_count=23 if task_id == "alpha-amylase" else 21,
        exploration_greedy_difference_counts=differences,
        frozen_candidate_count=2262 if task_id == "alpha-amylase" else 2070,
        primary_e_g=e_g,
        primary_e_m=e_m,
        required_defined=True,
        sensitivity_e_g_point_estimates=(e_g.point_estimate,) * 2,
        sensitivity_e_m_point_estimates=(e_m.point_estimate,) * 2,
        task_id=task_id,
    )


@pytest.mark.parametrize(
    ("eg_status", "em_status", "differences", "expected"),
    (
        ("EQUIVALENT", "EQUIVALENT", (1,) * 40, "BEHAVIOR_REDUNDANCY"),
        ("INFERIOR", "EQUIVALENT", (3,) * 40, "UNINFORMATIVE_DEVIATION"),
        ("EQUIVALENT", "SUPERIOR", (3,) * 40, "OPPORTUNITY_COST"),
        ("SUPERIOR", "SUPERIOR", (3,) * 40, "CONDITIONAL_POSITIVE"),
        ("INFERIOR", "INCONCLUSIVE", (3,) * 40, "OTHER_NEGATIVE"),
        ("NONINFERIOR_ONLY", "NONINFERIOR_ONLY", (3,) * 40, "INCONCLUSIVE"),
    ),
)
def test_task_mechanism_and_overall_rollup_are_single_valued(
    eg_status: str,
    em_status: str,
    differences: tuple[int, ...],
    expected: str,
) -> None:
    inputs = tuple(
        _taxonomy_input(
            task_id,
            eg_status=eg_status,
            em_status=em_status,
            differences=differences,
        )
        for task_id in _TASK_BINDINGS
    )

    taxonomy = classify_taxonomy(inputs, validity_ok=True)

    assert taxonomy.overall_status == expected
    assert dict(taxonomy.task_statuses) == {
        "alpha-amylase": expected,
        "ired": expected,
    }


def test_taxonomy_precedence_is_validity_then_undefined_fragility_instability() -> None:
    base = tuple(
        TaskTaxonomyInput(
            actual_high_value_count=23 if task_id == "alpha-amylase" else 21,
            exploration_greedy_difference_counts=(3,) * 40,
            frozen_candidate_count=2262 if task_id == "alpha-amylase" else 2070,
            primary_e_g=_contrast_summary(task_id, "E-G", "SUPERIOR", 0.05),
            primary_e_m=_contrast_summary(task_id, "E-M", "SUPERIOR", 0.05),
            required_defined=True,
            sensitivity_e_g_point_estimates=(0.04, 0.06),
            sensitivity_e_m_point_estimates=(0.04, 0.06),
            task_id=task_id,
        )
        for task_id in _TASK_BINDINGS
    )
    undefined = (replace(base[0], required_defined=False), base[1])
    fragile = (replace(base[0], actual_high_value_count=46), base[1])
    unstable = (
        replace(
            base[0],
            primary_e_g=replace(
                base[0].primary_e_g,
                cell_point_estimates=(0.10, -0.10, 0.05, 0.05),
            ),
        ),
        base[1],
    )

    assert classify_taxonomy(fragile, validity_ok=False).overall_status == "VALIDITY_STOP"
    assert classify_taxonomy(undefined, validity_ok=True).overall_status == (
        "UNDEFINED_INCONCLUSIVE"
    )
    assert classify_taxonomy(fragile, validity_ok=True).overall_status == "FRAGILE_INCONCLUSIVE"
    assert classify_taxonomy(unstable, validity_ok=True).overall_status == "UNSTABLE"


def test_taxonomy_rejects_a_status_that_disagrees_with_its_frozen_ci() -> None:
    inputs = tuple(
        _taxonomy_input(
            task_id,
            eg_status="SUPERIOR",
            em_status="SUPERIOR",
            differences=(3,) * 40,
        )
        for task_id in _TASK_BINDINGS
    )
    forged = (
        replace(
            inputs[0],
            primary_e_g=replace(inputs[0].primary_e_g, status="EQUIVALENT"),
        ),
        inputs[1],
    )

    with pytest.raises(ContractValidationError, match="taxonomy input"):
        classify_taxonomy(forged, validity_ok=True)


def _attempt(
    attempt_id: str,
    *,
    complete: bool,
    valid: bool,
    failure_class: str | None = None,
    external_evidence: bool = False,
    unchanged_inputs: bool = True,
    human_retry_authorized: bool = False,
    input_identity: str | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=attempt_id,
        complete_terminal=complete,
        external_failure_evidence=external_evidence,
        failure_class=failure_class,
        human_retry_authorized=human_retry_authorized,
        input_identity_sha256=input_identity or _digest("frozen-m5b-input"),
        unchanged_input_checksums=unchanged_inputs,
        valid_terminal=valid,
    )


def test_retry_and_canonical_result_rules_reject_outcome_selected_attempts() -> None:
    first_complete = _attempt("attempt-001", complete=True, valid=True)
    complete_selection = select_canonical_attempt((first_complete,))
    forbidden_second = _attempt(
        "attempt-002",
        complete=True,
        valid=True,
        human_retry_authorized=True,
    )
    timeout = _attempt(
        "attempt-001",
        complete=False,
        valid=False,
        failure_class="timeout",
        external_evidence=True,
    )
    preempted = _attempt(
        "attempt-001",
        complete=False,
        valid=False,
        failure_class="scheduler_preemption",
        external_evidence=True,
    )
    authorized_second = _attempt(
        "attempt-002",
        complete=True,
        valid=True,
        human_retry_authorized=True,
    )

    assert complete_selection.status == "COMPLETE"
    assert complete_selection.canonical_attempt_id == "attempt-001"
    assert complete_selection.retry_eligible is False
    assert select_canonical_attempt((first_complete, forbidden_second)).status == "VALIDITY_STOP"
    assert select_canonical_attempt((timeout,)).retry_eligible is False
    assert select_canonical_attempt((preempted,)).retry_eligible is True
    assert select_canonical_attempt((preempted, authorized_second)).canonical_attempt_id == (
        "attempt-002"
    )
    drifted_second = replace(
        authorized_second,
        input_identity_sha256=_digest("drifted-input"),
    )
    assert select_canonical_attempt((preempted, drifted_second)).status == "VALIDITY_STOP"
