"""Synthetic-only primitives for the bounded M5B same-state intervention."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, cast

from pcdb.contracts.experiment import ContractValidationError
from pcdb.m5.reconstruction_smoke import compact_json_bytes

STATE_SCHEMA_VERSION = "m5b-alpha-ired-state-v2"
INVENTORY_SCHEMA_VERSION = "m5b-alpha-ired-state-inventory-v2"
PARENT_INVENTORY_SHA256 = "11c3ba0f64d9db8b9967c1df1e5c4ebbf3b5b0d855b472885564ddd0ce70573b"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MATCHED_RANDOM_PREFIX = b"m5b-alpha-ired-one-step-v2\0matched-random\0"
_BOOTSTRAP_ID = "m5b-alpha-ired-one-step-v2-bootstrap"
_TASK_ORDER = ("alpha-amylase", "ired")
_MODEL_ORDER = ("random-forest-sklearn-v1", "xgboost-cpu-v1")
_INITIAL_SIZE_ORDER = (32, 64)
_RUN_SEEDS = tuple(range(1729, 1739))
_BOOTSTRAP_DRAWS = 20_000
_BRANCH_ORDER = ("E", "G", *(f"M{replicate:02d}" for replicate in range(64)))
_BRANCH_IDS = frozenset(_BRANCH_ORDER)

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
    "random-forest-sklearn-v1": {
        "exploration_acquisition_config_sha256": (
            "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6"
        ),
        "exploration_acquisition_id": "uncertainty-bootstrap-std-v1",
        "model_config_sha256": ("418b0fd6189fd9b3987620b05021e5ec5ff3018c074024cd13874cd3b9845312"),
    },
    "xgboost-cpu-v1": {
        "exploration_acquisition_config_sha256": (
            "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d"
        ),
        "exploration_acquisition_id": "ucb-bootstrap-std-beta1-v1",
        "model_config_sha256": ("31eb5bc146f4e810027de8870fa5632e09a5427bd8dddfee9261572c1e3473f2"),
    },
}
_GREEDY_BINDING = {
    "greedy_acquisition_config_sha256": (
        "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"
    ),
    "greedy_acquisition_id": "greedy-predictive-mean-v1",
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
_STATE_SHA256_FIELDS = _STATE_FIELDS - {
    "batch_size",
    "exploration_acquisition_id",
    "greedy_acquisition_id",
    "initial_sample_size",
    "model_id",
    "round_index",
    "run_seed",
    "schema_version",
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


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    """Aggregate-only immediate-batch output from one isolated evaluator."""

    actual_high_value_count: int
    best_found_improvement: float
    high_value_recall_increment: float
    simple_regret_reduction: float


@dataclass(frozen=True, slots=True)
class StateEffects:
    """Aggregate-only primary effects for one canonical M5B state."""

    exploration_minus_greedy: float
    exploration_minus_matched: tuple[float, ...]
    initial_sample_size: int
    model_id: str
    run_seed: int
    state_id: str
    task_id: str


@dataclass(frozen=True, slots=True)
class TaskContrastSummary:
    """One task-first point estimate and frozen percentile interval."""

    bootstrap_draws: int
    cell_point_estimates: tuple[float, ...]
    ci_lower: float
    ci_upper: float
    contrast: str
    point_estimate: float
    status: str
    task_id: str


@dataclass(frozen=True, slots=True)
class TaskTaxonomyInput:
    """All primary, sensitivity, and behavior inputs for one task label."""

    actual_high_value_count: int
    exploration_greedy_difference_counts: tuple[int, ...]
    frozen_candidate_count: int
    primary_e_g: TaskContrastSummary
    primary_e_m: TaskContrastSummary
    required_defined: bool
    sensitivity_e_g_point_estimates: tuple[float, ...]
    sensitivity_e_m_point_estimates: tuple[float, ...]
    task_id: str


@dataclass(frozen=True, slots=True)
class M5BTaxonomy:
    """One precedence-resolved M5B taxonomy terminal."""

    overall_status: str
    task_statuses: MappingProxyType[str, str]


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Outcome-independent retry facts for one append-only attempt."""

    attempt_id: str
    complete_terminal: bool
    external_failure_evidence: bool
    failure_class: str | None
    human_retry_authorized: bool
    input_identity_sha256: str
    unchanged_input_checksums: bool
    valid_terminal: bool


@dataclass(frozen=True, slots=True)
class CanonicalAttemptSelection:
    """Canonical-first-complete result without outcome selection."""

    canonical_attempt_id: str | None
    retry_eligible: bool
    status: str


class BranchEvaluator(Protocol):
    """Capability-limited evaluator surface used by the M5B dispatcher."""

    def evaluate_batch(
        self,
        *,
        observation_prefix_ids: tuple[str, ...],
        selected_ids: frozenset[str],
    ) -> BranchOutcome: ...


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
    model_binding = _MODEL_BINDINGS.get(value["model_id"])
    if (
        model_binding is None
        or any(value[field] != expected for field, expected in model_binding.items())
        or any(value[field] != expected for field, expected in _GREEDY_BINDING.items())
        or any(
            type(value[field]) is not int
            for field in ("batch_size", "round_index", "initial_sample_size", "run_seed")
        )
        or value["batch_size"] != 16
        or value["round_index"] != 1
        or value["initial_sample_size"] not in {32, 64}
        or value["run_seed"] not in range(1729, 1739)
        or value["parent_inventory_sha256"] != PARENT_INVENTORY_SHA256
        or any(
            not isinstance(value[field], str) or not _SHA256.fullmatch(value[field])
            for field in _STATE_SHA256_FIELDS
        )
    ):
        raise ContractValidationError("M5B scientific identity drifted")
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


def evaluate_isolated_branches(
    *,
    evaluator_factory: Callable[[], BranchEvaluator],
    observation_prefix_ids: Sequence[str],
    branch_batches: Mapping[str, frozenset[str]],
) -> MappingProxyType[str, BranchOutcome]:
    """Evaluate each branch with a fresh aggregate-only evaluator capability."""

    prefix = tuple(observation_prefix_ids)
    if len(set(prefix)) != len(prefix) or any(not _SHA256.fullmatch(item) for item in prefix):
        raise ContractValidationError("M5B observation prefix candidate IDs are invalid")
    if set(branch_batches) != _BRANCH_IDS:
        raise ContractValidationError("M5B branches must exact-cover E, G, and M00..M63")
    outcomes: dict[str, BranchOutcome] = {}
    evaluator_ids: set[int] = set()
    evaluators: list[BranchEvaluator] = []
    for branch_id in _BRANCH_ORDER:
        selected = branch_batches[branch_id]
        if (
            not isinstance(selected, frozenset)
            or len(selected) != 16
            or any(not _SHA256.fullmatch(item) for item in selected)
            or not selected.isdisjoint(prefix)
        ):
            raise ContractValidationError("M5B branch batch must contain 16 unrevealed candidates")
        evaluator = evaluator_factory()
        evaluator_identity = id(evaluator)
        if evaluator_identity in evaluator_ids:
            raise ContractValidationError("M5B branches must use branch-local evaluator instances")
        evaluator_ids.add(evaluator_identity)
        evaluators.append(evaluator)
        outcome = evaluator.evaluate_batch(
            observation_prefix_ids=prefix,
            selected_ids=selected,
        )
        if (
            not isinstance(outcome, BranchOutcome)
            or isinstance(outcome.actual_high_value_count, bool)
            or not 0 <= outcome.actual_high_value_count <= 16
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                for value in (
                    outcome.best_found_improvement,
                    outcome.high_value_recall_increment,
                    outcome.simple_regret_reduction,
                )
            )
            or outcome.best_found_improvement < 0
            or not 0 <= outcome.high_value_recall_increment <= 1
            or outcome.simple_regret_reduction < 0
        ):
            raise ContractValidationError("M5B evaluator returned an invalid aggregate outcome")
        outcomes[branch_id] = outcome
    return MappingProxyType(outcomes)


def bootstrap_index(n: int, payload: Sequence[object]) -> int:
    """Return the v2 counter-based SHA-256 bootstrap index."""

    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        raise ContractValidationError("M5B bootstrap population size must be positive")
    digest = hashlib.sha256(compact_json_bytes([_BOOTSTRAP_ID, *payload])).digest()
    return int.from_bytes(digest[:8], "big") % n


def percentile_type7(values: Sequence[float], probability: float) -> float:
    """Return one finite Hyndman-Fan type 7 percentile."""

    converted = tuple(float(value) for value in values)
    if not converted or any(not math.isfinite(value) for value in converted):
        raise ContractValidationError("M5B percentile values must be finite and non-empty")
    if (
        isinstance(probability, bool)
        or not isinstance(probability, (int, float))
        or not math.isfinite(float(probability))
        or not 0 <= float(probability) <= 1
    ):
        raise ContractValidationError("M5B percentile probability must be in [0,1]")
    ordered = sorted(converted)
    h = (len(ordered) - 1) * float(probability)
    lower = math.floor(h)
    upper = math.ceil(h)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (h - lower) * (ordered[upper] - ordered[lower])


def classify_contrast(*, lower: float, upper: float, delta: float) -> str:
    """Return the single-valued v2 status for one task contrast interval."""

    converted = (float(lower), float(upper), float(delta))
    if (
        any(not math.isfinite(value) for value in converted)
        or converted[0] > converted[1]
        or converted[2] <= 0
    ):
        raise ContractValidationError("M5B contrast interval or margin is invalid")
    lower_value, upper_value, margin = converted
    if lower_value > margin:
        return "SUPERIOR"
    if upper_value < -margin:
        return "INFERIOR"
    if lower_value >= -margin and upper_value <= margin:
        return "EQUIVALENT"
    if lower_value > -margin:
        return "NONINFERIOR_ONLY"
    return "INCONCLUSIVE"


def matched_replicate_bootstrap_mean(
    state: StateEffects,
    *,
    draw: int,
    occurrence: int,
) -> float:
    """Resample one state's 64 matched replicates with frozen counters."""

    values = state.exploration_minus_matched
    if (
        isinstance(draw, bool)
        or not isinstance(draw, int)
        or draw < 0
        or isinstance(occurrence, bool)
        or not isinstance(occurrence, int)
        or occurrence < 0
        or not _SHA256.fullmatch(state.state_id)
        or len(values) != 64
        or any(not math.isfinite(float(value)) for value in values)
    ):
        raise ContractValidationError("M5B matched replicate bootstrap input is invalid")
    if all(value == values[0] for value in values[1:]):
        return values[0]
    sampled = (
        values[
            bootstrap_index(
                64,
                ("m-replicate", state.state_id, draw, occurrence, replicate_slot),
            )
        ]
        for replicate_slot in range(64)
    )
    return math.fsum(sampled) / 64


def _validated_effects(
    effects: Sequence[StateEffects],
) -> dict[tuple[str, str, int, int], StateEffects]:
    expected = {
        (task_id, model_id, initial_sample_size, run_seed)
        for task_id in _TASK_ORDER
        for model_id in _MODEL_ORDER
        for initial_sample_size in _INITIAL_SIZE_ORDER
        for run_seed in _RUN_SEEDS
    }
    keyed: dict[tuple[str, str, int, int], StateEffects] = {}
    state_ids: set[str] = set()
    for state in effects:
        key = (state.task_id, state.model_id, state.initial_sample_size, state.run_seed)
        if (
            key in keyed
            or not _SHA256.fullmatch(state.state_id)
            or state.state_id in state_ids
            or not math.isfinite(float(state.exploration_minus_greedy))
            or len(state.exploration_minus_matched) != 64
            or any(not math.isfinite(float(value)) for value in state.exploration_minus_matched)
        ):
            raise ContractValidationError("M5B state effects are invalid or duplicated")
        keyed[key] = state
        state_ids.add(state.state_id)
    if set(keyed) != expected:
        raise ContractValidationError("M5B state effects do not exact-cover 80 states")
    return keyed


def _point_estimates(
    keyed: Mapping[tuple[str, str, int, int], StateEffects],
    task_id: str,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    greedy_cells: list[float] = []
    matched_cells: list[float] = []
    for initial_sample_size in _INITIAL_SIZE_ORDER:
        for model_id in _MODEL_ORDER:
            states = tuple(
                keyed[(task_id, model_id, initial_sample_size, run_seed)] for run_seed in _RUN_SEEDS
            )
            greedy_cells.append(
                math.fsum(state.exploration_minus_greedy for state in states) / len(states)
            )
            matched_cells.append(
                math.fsum(math.fsum(state.exploration_minus_matched) / 64 for state in states)
                / len(states)
            )
    return tuple(greedy_cells), tuple(matched_cells)


def bootstrap_task_effects(
    effects: Sequence[StateEffects],
) -> tuple[TaskContrastSummary, ...]:
    """Run the exact task-first 20,000-draw v2 bootstrap over 80 states."""

    keyed = _validated_effects(effects)
    summaries: list[TaskContrastSummary] = []
    margins = {"alpha-amylase": 1 / 23, "ired": 1 / 21}
    for task_id in _TASK_ORDER:
        greedy_cells, matched_cells = _point_estimates(keyed, task_id)
        greedy_draws: list[float] = []
        matched_draws: list[float] = []
        for draw in range(_BOOTSTRAP_DRAWS):
            greedy_draw_cells: list[float] = []
            matched_draw_cells: list[float] = []
            for initial_sample_size in _INITIAL_SIZE_ORDER:
                seed_indices = tuple(
                    bootstrap_index(
                        10,
                        ("seed-cluster", task_id, initial_sample_size, draw, slot),
                    )
                    for slot in range(10)
                )
                for model_id in _MODEL_ORDER:
                    occurrences: dict[int, int] = {}
                    greedy_values: list[float] = []
                    matched_values: list[float] = []
                    for seed_index in seed_indices:
                        state = keyed[
                            (
                                task_id,
                                model_id,
                                initial_sample_size,
                                _RUN_SEEDS[seed_index],
                            )
                        ]
                        occurrence = occurrences.get(seed_index, 0)
                        occurrences[seed_index] = occurrence + 1
                        greedy_values.append(state.exploration_minus_greedy)
                        matched_values.append(
                            matched_replicate_bootstrap_mean(
                                state,
                                draw=draw,
                                occurrence=occurrence,
                            )
                        )
                    greedy_draw_cells.append(math.fsum(greedy_values) / 10)
                    matched_draw_cells.append(math.fsum(matched_values) / 10)
            greedy_draws.append(math.fsum(greedy_draw_cells) / 4)
            matched_draws.append(math.fsum(matched_draw_cells) / 4)
        for contrast, cell_points, draws in (
            ("E-G", greedy_cells, greedy_draws),
            ("E-M", matched_cells, matched_draws),
        ):
            point_estimate = math.fsum(cell_points) / 4
            lower = percentile_type7(draws, 0.00625)
            upper = percentile_type7(draws, 0.99375)
            summaries.append(
                TaskContrastSummary(
                    bootstrap_draws=_BOOTSTRAP_DRAWS,
                    cell_point_estimates=cell_points,
                    ci_lower=lower,
                    ci_upper=upper,
                    contrast=contrast,
                    point_estimate=point_estimate,
                    status=classify_contrast(
                        lower=lower,
                        upper=upper,
                        delta=margins[task_id],
                    ),
                    task_id=task_id,
                )
            )
    return tuple(summaries)


def _sign_reversed(primary: float, sensitivities: Sequence[float]) -> bool:
    return any(
        primary != 0 and sensitivity != 0 and (primary > 0) != (sensitivity > 0)
        for sensitivity in sensitivities
    )


def _task_mechanism(item: TaskTaxonomyInput) -> str:
    e_g = item.primary_e_g.status
    e_m = item.primary_e_m.status
    differences = item.exploration_greedy_difference_counts
    if (
        math.fsum(abs(value) for value in differences) / len(differences) <= 1
        and max(abs(value) for value in differences) <= 2
        and e_g == "EQUIVALENT"
        and e_m == "EQUIVALENT"
    ):
        return "BEHAVIOR_REDUNDANCY"
    if e_m == "EQUIVALENT" and e_g == "INFERIOR":
        return "UNINFORMATIVE_DEVIATION"
    if e_m == "SUPERIOR" and e_g in {"EQUIVALENT", "INFERIOR"}:
        return "OPPORTUNITY_COST"
    if e_m == "SUPERIOR" and e_g == "SUPERIOR":
        return "CONDITIONAL_POSITIVE"
    if "INFERIOR" in {e_g, e_m}:
        return "OTHER_NEGATIVE"
    return "INCONCLUSIVE"


def classify_taxonomy(
    inputs: Sequence[TaskTaxonomyInput],
    *,
    validity_ok: bool,
) -> M5BTaxonomy:
    """Apply the one frozen precedence and task/overall roll-up."""

    keyed = {item.task_id: item for item in inputs}
    if set(keyed) != set(_TASK_ORDER) or len(inputs) != len(_TASK_ORDER):
        raise ContractValidationError("M5B taxonomy inputs must exact-cover both tasks")
    expected_counts = {"alpha-amylase": 2262, "ired": 2070}
    margins = {"alpha-amylase": 1 / 23, "ired": 1 / 21}
    valid_statuses = {
        "EQUIVALENT",
        "INCONCLUSIVE",
        "INFERIOR",
        "NONINFERIOR_ONLY",
        "SUPERIOR",
    }
    for task_id, item in keyed.items():
        if (
            item.frozen_candidate_count != expected_counts[task_id]
            or isinstance(item.actual_high_value_count, bool)
            or not isinstance(item.actual_high_value_count, int)
            or not 0 <= item.actual_high_value_count <= item.frozen_candidate_count
            or not isinstance(item.required_defined, bool)
            or len(item.exploration_greedy_difference_counts) != 40
            or any(
                isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 16
                for value in item.exploration_greedy_difference_counts
            )
            or item.primary_e_g.task_id != task_id
            or item.primary_e_g.contrast != "E-G"
            or item.primary_e_m.task_id != task_id
            or item.primary_e_m.contrast != "E-M"
            or item.primary_e_g.status not in valid_statuses
            or item.primary_e_m.status not in valid_statuses
            or item.primary_e_g.bootstrap_draws != _BOOTSTRAP_DRAWS
            or item.primary_e_m.bootstrap_draws != _BOOTSTRAP_DRAWS
            or len(item.primary_e_g.cell_point_estimates) != 4
            or len(item.primary_e_m.cell_point_estimates) != 4
            or len(item.sensitivity_e_g_point_estimates) != 2
            or len(item.sensitivity_e_m_point_estimates) != 2
            or any(
                not math.isfinite(float(value))
                for value in (
                    item.primary_e_g.point_estimate,
                    item.primary_e_g.ci_lower,
                    item.primary_e_g.ci_upper,
                    item.primary_e_m.point_estimate,
                    item.primary_e_m.ci_lower,
                    item.primary_e_m.ci_upper,
                    *item.primary_e_g.cell_point_estimates,
                    *item.primary_e_m.cell_point_estimates,
                    *item.sensitivity_e_g_point_estimates,
                    *item.sensitivity_e_m_point_estimates,
                )
            )
            or item.primary_e_g.ci_lower > item.primary_e_g.ci_upper
            or item.primary_e_m.ci_lower > item.primary_e_m.ci_upper
            or item.primary_e_g.status
            != classify_contrast(
                lower=item.primary_e_g.ci_lower,
                upper=item.primary_e_g.ci_upper,
                delta=margins[task_id],
            )
            or item.primary_e_m.status
            != classify_contrast(
                lower=item.primary_e_m.ci_lower,
                upper=item.primary_e_m.ci_upper,
                delta=margins[task_id],
            )
        ):
            raise ContractValidationError("M5B taxonomy input is invalid")

    def terminal(status: str) -> M5BTaxonomy:
        return M5BTaxonomy(
            overall_status=status,
            task_statuses=MappingProxyType({task_id: status for task_id in _TASK_ORDER}),
        )

    if not validity_ok:
        return terminal("VALIDITY_STOP")
    if any(not item.required_defined for item in inputs):
        return terminal("UNDEFINED_INCONCLUSIVE")
    if any(
        item.actual_high_value_count / item.frozen_candidate_count > 0.02
        or _sign_reversed(
            item.primary_e_g.point_estimate,
            item.sensitivity_e_g_point_estimates,
        )
        or _sign_reversed(
            item.primary_e_m.point_estimate,
            item.sensitivity_e_m_point_estimates,
        )
        for item in inputs
    ):
        return terminal("FRAGILE_INCONCLUSIVE")
    if any(
        any(value > margins[item.task_id] for value in summary.cell_point_estimates)
        and any(value < -margins[item.task_id] for value in summary.cell_point_estimates)
        for item in inputs
        for summary in (item.primary_e_g, item.primary_e_m)
    ):
        return terminal("UNSTABLE")
    task_statuses = {task_id: _task_mechanism(keyed[task_id]) for task_id in _TASK_ORDER}
    labels = set(task_statuses.values())
    if "INCONCLUSIVE" in labels:
        overall = "INCONCLUSIVE"
    elif len(labels) == 1:
        overall = labels.pop()
    else:
        overall = "UNSTABLE"
    return M5BTaxonomy(
        overall_status=overall,
        task_statuses=MappingProxyType(task_statuses),
    )


def _retry_eligible(attempt: AttemptRecord) -> bool:
    if attempt.complete_terminal or not attempt.external_failure_evidence:
        return False
    if attempt.failure_class in {"scheduler_preemption", "host_interruption"}:
        return True
    return attempt.failure_class == "transient_local_io" and attempt.unchanged_input_checksums


def select_canonical_attempt(
    attempts: Sequence[AttemptRecord],
) -> CanonicalAttemptSelection:
    """Apply the v2 no-automatic-retry and first-complete terminal rule."""

    if not 1 <= len(attempts) <= 2:
        raise ContractValidationError(
            "M5B attempts must contain attempt-001 and at most attempt-002"
        )
    expected_ids = tuple(f"attempt-{index:03d}" for index in range(1, len(attempts) + 1))
    if tuple(attempt.attempt_id for attempt in attempts) != expected_ids:
        raise ContractValidationError("M5B attempt IDs are not canonical and ordered")
    if any(
        not _SHA256.fullmatch(attempt.input_identity_sha256)
        or not isinstance(attempt.complete_terminal, bool)
        or not isinstance(attempt.valid_terminal, bool)
        or not isinstance(attempt.external_failure_evidence, bool)
        or not isinstance(attempt.unchanged_input_checksums, bool)
        or not isinstance(attempt.human_retry_authorized, bool)
        or (attempt.valid_terminal and not attempt.complete_terminal)
        for attempt in attempts
    ):
        raise ContractValidationError("M5B attempt record is invalid")
    first = attempts[0]
    first_retry_eligible = _retry_eligible(first)
    if first.complete_terminal:
        if len(attempts) == 2 or not first.valid_terminal:
            return CanonicalAttemptSelection(None, False, "VALIDITY_STOP")
        return CanonicalAttemptSelection(first.attempt_id, False, "COMPLETE")
    if len(attempts) == 1:
        return CanonicalAttemptSelection(None, first_retry_eligible, "NO_SCIENTIFIC_RESULT")
    second = attempts[1]
    if (
        not first_retry_eligible
        or not second.human_retry_authorized
        or second.input_identity_sha256 != first.input_identity_sha256
    ):
        return CanonicalAttemptSelection(None, False, "VALIDITY_STOP")
    if second.complete_terminal:
        if not second.valid_terminal:
            return CanonicalAttemptSelection(None, False, "VALIDITY_STOP")
        return CanonicalAttemptSelection(second.attempt_id, False, "COMPLETE")
    return CanonicalAttemptSelection(None, False, "NO_SCIENTIFIC_RESULT")
