"""Frozen M5C ProteinGym four-assay confirmation primitives.

The module deliberately exposes only outcome-independent selection helpers and
aggregate result types. Runtime truth and static-proxy readers live in the
separate M5C worker so model/acquisition code cannot receive either capability.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from pcdb.contracts import ContractValidationError
from pcdb.m2.features import ALPHABET, sequence_feature_definitions

PROTOCOL_ID = "m5c-proteingym-one-step-v2"
BOOTSTRAP_ID = f"{PROTOCOL_ID}-bootstrap"
ASSAY_IDS = (
    "BLAT_ECOLX_Firnberg_2014",
    "CAPSD_AAV2S_Sinai_2021",
    "GFP_AEQVI_Sarkisyan_2016",
    "SPG1_STRSG_Olson_2014",
)
FROZEN_CANDIDATE_COUNTS = {
    "BLAT_ECOLX_Firnberg_2014": 4_783,
    "CAPSD_AAV2S_Sinai_2021": 42_328,
    "GFP_AEQVI_Sarkisyan_2016": 51_714,
    "SPG1_STRSG_Olson_2014": 536_962,
}
MODEL_IDS = ("random-forest-sklearn-v1", "xgboost-cpu-v1")
INITIAL_SAMPLE_SIZES = (32, 64)
RUN_SEEDS = tuple(range(1729, 1739))
HIGH_VALUE_FRACTIONS = (0.005, 0.01, 0.02)
BOOTSTRAP_DRAWS = 20_000
MATCHED_REPLICATES = 64
BATCH_SIZE = 16
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MATCHED_PREFIX = f"{PROTOCOL_ID}\0matched-random\0".encode()
_INFORMATIVE_LABELS = frozenset(
    {
        "BEHAVIOR_REDUNDANCY",
        "UNINFORMATIVE_DEVIATION",
        "OPPORTUNITY_COST",
        "CONDITIONAL_POSITIVE",
        "OTHER_NEGATIVE",
    }
)


def compact_json_bytes(value: object) -> bytes:
    """Encode the exact compact JSON used by all M5C hash rules."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _candidate_ids(values: Sequence[str], *, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not values:
        raise ContractValidationError(f"{field} must be a non-empty candidate sequence")
    result = tuple(values)
    if len(result) != len(set(result)) or any(not _SHA256.fullmatch(item) for item in result):
        raise ContractValidationError(f"{field} candidate IDs are invalid or duplicated")
    return result


def initial_selection(
    candidate_ids: Sequence[str],
    *,
    assay_id: str,
    run_seed: int,
    size: int,
    assay_ids: Sequence[str] = ASSAY_IDS,
) -> tuple[str, ...]:
    """Return the nested truth-blind initial prefix frozen by the contract."""

    candidates = _candidate_ids(candidate_ids, field="initial selection")
    if assay_id not in assay_ids or run_seed not in RUN_SEEDS or size not in INITIAL_SAMPLE_SIZES:
        raise ContractValidationError("M5C initial-selection identity drifted")
    if size > len(candidates):
        raise ContractValidationError("M5C initial selection exceeds the candidate pool")
    ranked = heapq.nsmallest(
        size,
        (
            (
                hashlib.sha256(
                    compact_json_bytes([PROTOCOL_ID, assay_id, run_seed, candidate_id])
                ).digest(),
                candidate_id,
            )
            for candidate_id in candidates
        ),
    )
    return tuple(candidate_id for _, candidate_id in ranked)


def state_identity(
    *,
    assay_id: str,
    model_id: str,
    model_config_sha256: str,
    initial_sample_size: int,
    run_seed: int,
    candidate_pool_sha256: str,
    feature_sha256: str,
    assay_ids: Sequence[str] = ASSAY_IDS,
) -> str:
    """Return the exact content-derived state identity from the Gate A rule."""

    if (
        assay_id not in assay_ids
        or model_id not in MODEL_IDS
        or initial_sample_size not in INITIAL_SAMPLE_SIZES
        or run_seed not in RUN_SEEDS
        or any(
            not _SHA256.fullmatch(value)
            for value in (model_config_sha256, candidate_pool_sha256, feature_sha256)
        )
    ):
        raise ContractValidationError("M5C state identity input drifted")
    return hashlib.sha256(
        compact_json_bytes(
            [
                PROTOCOL_ID,
                assay_id,
                model_id,
                model_config_sha256,
                initial_sample_size,
                run_seed,
                candidate_pool_sha256,
                feature_sha256,
            ]
        )
    ).hexdigest()


def matched_random_batch(
    *,
    state_id: str,
    greedy_ids: Sequence[str],
    exploration_ids: Sequence[str],
    remaining_pool: Sequence[str],
    replicate: int,
) -> tuple[str, ...]:
    """Build one exact-overlap deterministic Matched Random branch."""

    greedy = _candidate_ids(greedy_ids, field="Greedy batch")
    exploration = _candidate_ids(exploration_ids, field="Exploration batch")
    remaining = _candidate_ids(remaining_pool, field="remaining pool")
    if (
        not _SHA256.fullmatch(state_id)
        or len(greedy) != BATCH_SIZE
        or len(exploration) != BATCH_SIZE
        or not 0 <= replicate < MATCHED_REPLICATES
        or not set(greedy) <= set(remaining)
        or not set(exploration) <= set(remaining)
    ):
        raise ContractValidationError("M5C matched-random identity or batch drifted")
    greedy_set = set(greedy)
    overlap = greedy_set & set(exploration)
    required = BATCH_SIZE - len(overlap)
    eligible = set(remaining) - greedy_set
    if len(eligible) < required:
        raise ContractValidationError("M5C matched-random pool is too small")
    return _matched_random_validated(
        state_id=state_id,
        greedy_set=greedy_set,
        overlap=overlap,
        eligible=tuple(eligible),
        replicate=replicate,
    )


def _matched_random_validated(
    *,
    state_id: str,
    greedy_set: set[str],
    overlap: set[str],
    eligible: Sequence[str],
    replicate: int,
) -> tuple[str, ...]:
    required = BATCH_SIZE - len(overlap)
    seed_bytes = hashlib.sha256(
        _MATCHED_PREFIX + bytes.fromhex(state_id) + replicate.to_bytes(4, "big")
    ).digest()[:8]
    sampled = tuple(
        candidate_id
        for _, candidate_id in heapq.nsmallest(
            required,
            (
                (hashlib.sha256(seed_bytes + bytes.fromhex(candidate_id)).digest(), candidate_id)
                for candidate_id in eligible
            ),
        )
    )
    selected = tuple(sorted((*overlap, *sampled)))
    if len(selected) != BATCH_SIZE or set(selected) & greedy_set != overlap:
        raise ContractValidationError("M5C matched-random exact overlap drifted")
    return selected


def matched_random_batches(
    *,
    state_id: str,
    greedy_ids: Sequence[str],
    exploration_ids: Sequence[str],
    remaining_pool: Sequence[str],
) -> tuple[tuple[str, ...], ...]:
    """Build all 64 controls while validating the large pool only once."""

    greedy = _candidate_ids(greedy_ids, field="Greedy batch")
    exploration = _candidate_ids(exploration_ids, field="Exploration batch")
    remaining = _candidate_ids(remaining_pool, field="remaining pool")
    if (
        not _SHA256.fullmatch(state_id)
        or len(greedy) != BATCH_SIZE
        or len(exploration) != BATCH_SIZE
        or not set(greedy) <= set(remaining)
        or not set(exploration) <= set(remaining)
    ):
        raise ContractValidationError("M5C matched-random identity or batch drifted")
    greedy_set = set(greedy)
    overlap = greedy_set & set(exploration)
    eligible = tuple(set(remaining) - greedy_set)
    if len(eligible) < BATCH_SIZE - len(overlap):
        raise ContractValidationError("M5C matched-random pool is too small")
    return tuple(
        _matched_random_validated(
            state_id=state_id,
            greedy_set=greedy_set,
            overlap=overlap,
            eligible=eligible,
            replicate=replicate,
        )
        for replicate in range(MATCHED_REPLICATES)
    )


def bootstrap_index(n: int, payload: Sequence[object]) -> int:
    """Return the frozen counter-based SHA-256 bootstrap index."""

    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        raise ContractValidationError("M5C bootstrap population size must be positive")
    digest = hashlib.sha256(compact_json_bytes([BOOTSTRAP_ID, *payload])).digest()
    return int.from_bytes(digest[:8], "big") % n


def percentile_type7(values: Sequence[float], probability: float) -> float:
    """Return a finite Hyndman-Fan type-7 percentile."""

    converted = tuple(float(value) for value in values)
    if not converted or any(not math.isfinite(value) for value in converted):
        raise ContractValidationError("M5C percentile values must be finite and non-empty")
    if not math.isfinite(float(probability)) or not 0 <= float(probability) <= 1:
        raise ContractValidationError("M5C percentile probability must be in [0,1]")
    ordered = sorted(converted)
    h = (len(ordered) - 1) * float(probability)
    lower = math.floor(h)
    upper = math.ceil(h)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (h - lower) * (ordered[upper] - ordered[lower])


def classify_contrast(*, lower: float, upper: float, delta: float) -> str:
    """Apply the frozen single-valued contrast precedence."""

    if (
        any(not math.isfinite(float(value)) for value in (lower, upper, delta))
        or lower > upper
        or delta <= 0
    ):
        raise ContractValidationError("M5C contrast interval or margin is invalid")
    if lower > delta:
        return "SUPERIOR"
    if upper < -delta:
        return "INFERIOR"
    if lower >= -delta and upper <= delta:
        return "EQUIVALENT"
    if lower > -delta:
        return "NONINFERIOR_ONLY"
    return "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class StateEffects:
    """Candidate-free primary effects for one M5C state."""

    assay_id: str
    exploration_minus_greedy: float
    exploration_minus_matched: tuple[float, ...]
    initial_sample_size: int
    model_id: str
    run_seed: int
    state_id: str


@dataclass(frozen=True, slots=True)
class ContrastSummary:
    """One assay-first point estimate and frozen percentile interval."""

    assay_id: str
    bootstrap_draws: int
    cell_point_estimates: tuple[float, ...]
    ci_lower: float
    ci_upper: float
    contrast: str
    point_estimate: float
    status: str


def _effect_index(
    effects: Sequence[StateEffects], assay_id: str
) -> dict[tuple[str, int, int], StateEffects]:
    expected = {
        (model_id, initial_size, seed)
        for model_id in MODEL_IDS
        for initial_size in INITIAL_SAMPLE_SIZES
        for seed in RUN_SEEDS
    }
    keyed: dict[tuple[str, int, int], StateEffects] = {}
    seen_ids: set[str] = set()
    for state in effects:
        key = (state.model_id, state.initial_sample_size, state.run_seed)
        if (
            state.assay_id != assay_id
            or key in keyed
            or not _SHA256.fullmatch(state.state_id)
            or state.state_id in seen_ids
            or not math.isfinite(float(state.exploration_minus_greedy))
            or len(state.exploration_minus_matched) != MATCHED_REPLICATES
            or any(not math.isfinite(float(value)) for value in state.exploration_minus_matched)
        ):
            raise ContractValidationError("M5C state effects are invalid or duplicated")
        keyed[key] = state
        seen_ids.add(state.state_id)
    if set(keyed) != expected:
        raise ContractValidationError("M5C effects do not exact-cover 40 assay states")
    return keyed


def bootstrap_assay_effects(
    effects: Sequence[StateEffects],
    *,
    assay_id: str,
    delta: float,
    assay_ids: Sequence[str] = ASSAY_IDS,
) -> tuple[ContrastSummary, ...]:
    """Run the exact shared-seed 20,000-draw assay-level bootstrap."""

    if assay_id not in assay_ids or not math.isfinite(delta) or delta <= 0:
        raise ContractValidationError("M5C bootstrap assay or margin is invalid")
    keyed = _effect_index(effects, assay_id)
    greedy_cells: list[float] = []
    matched_cells: list[float] = []
    for model_id in MODEL_IDS:
        for initial_size in INITIAL_SAMPLE_SIZES:
            states = tuple(keyed[(model_id, initial_size, seed)] for seed in RUN_SEEDS)
            greedy_cells.append(math.fsum(state.exploration_minus_greedy for state in states) / 10)
            matched_cells.append(
                math.fsum(math.fsum(state.exploration_minus_matched) / 64 for state in states) / 10
            )

    greedy_draws: list[float] = []
    matched_draws: list[float] = []
    for draw in range(BOOTSTRAP_DRAWS):
        seed_indices = tuple(
            bootstrap_index(10, ("seed-cluster", assay_id, draw, slot)) for slot in range(10)
        )
        cell_greedy: list[float] = []
        cell_matched: list[float] = []
        for model_id in MODEL_IDS:
            for initial_size in INITIAL_SAMPLE_SIZES:
                occurrences: dict[int, int] = {}
                selected_greedy: list[float] = []
                selected_matched: list[float] = []
                for seed_index in seed_indices:
                    state = keyed[(model_id, initial_size, RUN_SEEDS[seed_index])]
                    occurrence = occurrences.get(seed_index, 0)
                    occurrences[seed_index] = occurrence + 1
                    selected_greedy.append(state.exploration_minus_greedy)
                    sampled = (
                        state.exploration_minus_matched[
                            bootstrap_index(
                                MATCHED_REPLICATES,
                                (
                                    "m-replicate",
                                    state.state_id,
                                    draw,
                                    occurrence,
                                    replicate_slot,
                                ),
                            )
                        ]
                        for replicate_slot in range(MATCHED_REPLICATES)
                    )
                    selected_matched.append(math.fsum(sampled) / MATCHED_REPLICATES)
                cell_greedy.append(math.fsum(selected_greedy) / 10)
                cell_matched.append(math.fsum(selected_matched) / 10)
        greedy_draws.append(math.fsum(cell_greedy) / 4)
        matched_draws.append(math.fsum(cell_matched) / 4)

    summaries: list[ContrastSummary] = []
    for contrast, points, draws in (
        ("E-G", tuple(greedy_cells), greedy_draws),
        ("E-M", tuple(matched_cells), matched_draws),
    ):
        lower = percentile_type7(draws, 0.003125)
        upper = percentile_type7(draws, 0.996875)
        point = math.fsum(points) / 4
        summaries.append(
            ContrastSummary(
                assay_id=assay_id,
                bootstrap_draws=BOOTSTRAP_DRAWS,
                cell_point_estimates=points,
                ci_lower=lower,
                ci_upper=upper,
                contrast=contrast,
                point_estimate=point,
                status=classify_contrast(lower=lower, upper=upper, delta=delta),
            )
        )
    return tuple(summaries)


@dataclass(frozen=True, slots=True)
class AssayTaxonomyInput:
    """All registered aggregate inputs for one assay taxonomy."""

    actual_primary_high_value_count: int
    assay_id: str
    exploration_greedy_difference_counts: tuple[int, ...]
    frozen_candidate_count: int
    primary_e_g: ContrastSummary
    primary_e_m: ContrastSummary
    required_defined: bool
    sensitivity_e_g_point_estimates: tuple[float, ...]
    sensitivity_e_m_point_estimates: tuple[float, ...]
    validity_ok: bool


def _sign_reversed(primary: float, sensitivities: Sequence[float]) -> bool:
    return any(
        primary != 0 and value != 0 and (primary > 0) != (value > 0) for value in sensitivities
    )


def classify_assay(item: AssayTaxonomyInput, *, assay_ids: Sequence[str] = ASSAY_IDS) -> str:
    """Apply the frozen per-assay M5C taxonomy precedence."""

    summaries = (item.primary_e_g, item.primary_e_m)
    if (
        item.assay_id not in assay_ids
        or isinstance(item.frozen_candidate_count, bool)
        or item.frozen_candidate_count <= 0
        or isinstance(item.actual_primary_high_value_count, bool)
        or not 0 <= item.actual_primary_high_value_count <= item.frozen_candidate_count
    ):
        raise ContractValidationError("M5C assay taxonomy input is invalid")
    delta = 1 / math.ceil(0.01 * item.frozen_candidate_count)
    if (
        len(item.exploration_greedy_difference_counts) != 40
        or any(
            type(value) is not int or not 0 <= value <= BATCH_SIZE
            for value in item.exploration_greedy_difference_counts
        )
        or len(item.sensitivity_e_g_point_estimates) != 2
        or len(item.sensitivity_e_m_point_estimates) != 2
        or any(
            not math.isfinite(float(value))
            for value in (
                *item.sensitivity_e_g_point_estimates,
                *item.sensitivity_e_m_point_estimates,
            )
        )
        or item.primary_e_g.contrast != "E-G"
        or item.primary_e_m.contrast != "E-M"
        or item.primary_e_g.assay_id != item.assay_id
        or item.primary_e_m.assay_id != item.assay_id
        or not isinstance(item.required_defined, bool)
        or not isinstance(item.validity_ok, bool)
        or any(
            summary.bootstrap_draws != BOOTSTRAP_DRAWS
            or len(summary.cell_point_estimates) != 4
            or any(
                not math.isfinite(float(value))
                for value in (
                    summary.point_estimate,
                    summary.ci_lower,
                    summary.ci_upper,
                    *summary.cell_point_estimates,
                )
            )
            or summary.status
            != classify_contrast(
                lower=summary.ci_lower,
                upper=summary.ci_upper,
                delta=delta,
            )
            for summary in summaries
        )
    ):
        raise ContractValidationError("M5C assay taxonomy input is invalid")
    if not item.validity_ok:
        return "VALIDITY_STOP"
    if not item.required_defined:
        return "UNDEFINED_INCONCLUSIVE"
    if (
        item.actual_primary_high_value_count / item.frozen_candidate_count > 0.02
        or _sign_reversed(
            item.primary_e_g.point_estimate,
            item.sensitivity_e_g_point_estimates,
        )
        or _sign_reversed(
            item.primary_e_m.point_estimate,
            item.sensitivity_e_m_point_estimates,
        )
    ):
        return "FRAGILE_INCONCLUSIVE"
    if any(
        any(value > delta for value in summary.cell_point_estimates)
        and any(value < -delta for value in summary.cell_point_estimates)
        for summary in summaries
    ):
        return "UNSTABLE"
    e_g = item.primary_e_g.status
    e_m = item.primary_e_m.status
    differences = item.exploration_greedy_difference_counts
    if (
        math.fsum(differences) / len(differences) <= 1
        and max(differences) <= 2
        and e_g == e_m == "EQUIVALENT"
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


@dataclass(frozen=True, slots=True)
class CohortClassification:
    """Registered cohort terminal and optional heterogeneity reason."""

    heterogeneity_reason: str | None
    status: str


def classify_cohort(
    assay_labels: Mapping[str, str],
    *,
    primary_statuses: Mapping[str, Mapping[str, str]],
) -> CohortClassification:
    """Apply the frozen eligibility, validity, conflict and replication roll-up."""

    if set(assay_labels) - set(ASSAY_IDS) or set(primary_statuses) != set(assay_labels):
        raise ContractValidationError("M5C cohort assay identities drifted")
    if len(assay_labels) < 3:
        return CohortClassification(None, "ELIGIBILITY_INCONCLUSIVE")
    if any(label == "VALIDITY_STOP" for label in assay_labels.values()):
        return CohortClassification(None, "VALIDITY_STOP")
    for contrast in ("E-G", "E-M"):
        statuses = {
            values.get(contrast)
            for values in primary_statuses.values()
            if values.get(contrast) is not None
        }
        if {"SUPERIOR", "INFERIOR"} <= statuses:
            return CohortClassification("DIRECTIONAL_REVERSAL", "HETEROGENEOUS")
    informative = [label for label in assay_labels.values() if label in _INFORMATIVE_LABELS]
    distinct = set(informative)
    if len(distinct) >= 2:
        return CohortClassification("INFORMATIVE_LABEL_CONFLICT", "HETEROGENEOUS")
    if len(distinct) == 1 and len(informative) >= 2:
        return CohortClassification(None, f"REPLICATED_{informative[0]}")
    return CohortClassification(None, "INCONCLUSIVE")


def build_sequence_composition_matrix(sequences: Sequence[str]) -> NDArray[np.float64]:
    """Build the exact frozen 420 float64 composition features in registry order."""

    if isinstance(sequences, (str, bytes, bytearray)) or not sequences:
        raise ContractValidationError("M5C sequence input must be non-empty")
    definitions = sequence_feature_definitions()
    feature_ids = tuple(str(item["feature_id"]) for item in definitions)
    columns: list[tuple[str, str]] = []
    for feature_id in feature_ids:
        if feature_id.startswith("aa-fraction-"):
            columns.append(("aa", feature_id.removeprefix("aa-fraction-").upper()))
        elif feature_id.startswith("dipeptide-fraction-"):
            columns.append(("di", feature_id.removeprefix("dipeptide-fraction-").upper()))
        else:
            raise RuntimeError("frozen sequence-composition feature registry drifted")
    matrix = np.zeros((len(sequences), len(columns)), dtype=np.float64)
    aa_columns = {value: index for index, (kind, value) in enumerate(columns) if kind == "aa"}
    di_columns = {value: index for index, (kind, value) in enumerate(columns) if kind == "di"}
    for row_index, sequence in enumerate(sequences):
        if (
            not isinstance(sequence, str)
            or len(sequence) < 2
            or sequence != sequence.upper()
            or any(residue not in ALPHABET for residue in sequence)
        ):
            raise ContractValidationError("M5C sequence is outside the frozen alphabet")
        aa_denominator = len(sequence)
        di_denominator = len(sequence) - 1
        for residue in sequence:
            matrix[row_index, aa_columns[residue]] += 1 / aa_denominator
        for first, second in zip(sequence, sequence[1:], strict=False):
            matrix[row_index, di_columns[first + second]] += 1 / di_denominator
    if matrix.shape != (len(sequences), 420) or not np.isfinite(matrix).all():
        raise ContractValidationError("M5C feature matrix identity drifted")
    return matrix


__all__ = [
    "ASSAY_IDS",
    "AssayTaxonomyInput",
    "BOOTSTRAP_DRAWS",
    "BATCH_SIZE",
    "CohortClassification",
    "ContrastSummary",
    "FROZEN_CANDIDATE_COUNTS",
    "HIGH_VALUE_FRACTIONS",
    "INITIAL_SAMPLE_SIZES",
    "MATCHED_REPLICATES",
    "MODEL_IDS",
    "PROTOCOL_ID",
    "RUN_SEEDS",
    "StateEffects",
    "bootstrap_assay_effects",
    "bootstrap_index",
    "build_sequence_composition_matrix",
    "classify_assay",
    "classify_cohort",
    "classify_contrast",
    "compact_json_bytes",
    "initial_selection",
    "matched_random_batch",
    "matched_random_batches",
    "percentile_type7",
    "state_identity",
]
