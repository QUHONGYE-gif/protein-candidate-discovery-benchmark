"""Frozen M5C four-assay confirmation invariants."""

from __future__ import annotations

import hashlib
import json
import math

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.m5 import proteingym_confirmation as m5c
from pcdb.m5.proteingym_confirmation import (
    ASSAY_IDS,
    MODEL_IDS,
    AssayTaxonomyInput,
    ContrastSummary,
    StateEffects,
    bootstrap_assay_effects,
    bootstrap_index,
    build_sequence_composition_matrix,
    classify_assay,
    classify_cohort,
    initial_selection,
    matched_random_batch,
    state_identity,
)


def _candidate(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def test_initial_selection_is_nested_and_uses_the_exact_contract_tuple() -> None:
    candidates = tuple(_candidate(f"candidate-{index}") for index in range(100))
    assay_id = ASSAY_IDS[0]
    selected_32 = initial_selection(candidates, assay_id=assay_id, run_seed=1729, size=32)
    selected_64 = initial_selection(candidates, assay_id=assay_id, run_seed=1729, size=64)

    expected = tuple(
        candidate_id
        for _, candidate_id in sorted(
            (
                hashlib.sha256(
                    _compact(
                        [
                            "m5c-proteingym-one-step-v2",
                            assay_id,
                            1729,
                            candidate_id,
                        ]
                    )
                ).digest(),
                candidate_id,
            )
            for candidate_id in candidates
        )
    )
    assert selected_32 == expected[:32]
    assert selected_64 == expected[:64]
    assert selected_64[:32] == selected_32


def test_state_and_matched_random_follow_exact_frozen_hash_rules() -> None:
    candidates = tuple(_candidate(f"candidate-{index}") for index in range(100))
    state_id = state_identity(
        assay_id=ASSAY_IDS[1],
        model_id=MODEL_IDS[0],
        model_config_sha256="1" * 64,
        initial_sample_size=32,
        run_seed=1729,
        candidate_pool_sha256="2" * 64,
        feature_sha256="3" * 64,
    )
    assert (
        state_id
        == hashlib.sha256(
            _compact(
                [
                    "m5c-proteingym-one-step-v2",
                    ASSAY_IDS[1],
                    MODEL_IDS[0],
                    "1" * 64,
                    32,
                    1729,
                    "2" * 64,
                    "3" * 64,
                ]
            )
        ).hexdigest()
    )

    greedy = tuple(candidates[:16])
    exploration = tuple((*candidates[:5], *candidates[20:31]))
    matched = matched_random_batch(
        state_id=state_id,
        greedy_ids=greedy,
        exploration_ids=exploration,
        remaining_pool=candidates,
        replicate=7,
    )
    assert len(matched) == 16
    assert set(matched) & set(greedy) == set(exploration) & set(greedy)
    assert matched == matched_random_batch(
        state_id=state_id,
        greedy_ids=greedy,
        exploration_ids=exploration,
        remaining_pool=tuple(reversed(candidates)),
        replicate=7,
    )


def _effects(*, assay_id: str, shift: float = 0.0) -> tuple[StateEffects, ...]:
    values: list[StateEffects] = []
    for model_index, model_id in enumerate(MODEL_IDS):
        for initial_index, initial_size in enumerate((32, 64)):
            for seed_index, run_seed in enumerate(range(1729, 1739)):
                base = shift + (model_index - initial_index) / 10_000 + seed_index / 100_000
                values.append(
                    StateEffects(
                        assay_id=assay_id,
                        exploration_minus_greedy=base,
                        exploration_minus_matched=tuple(
                            base + (replicate - 31.5) / 1_000_000 for replicate in range(64)
                        ),
                        initial_sample_size=initial_size,
                        model_id=model_id,
                        run_seed=run_seed,
                        state_id=_candidate(f"{assay_id}:{model_id}:{initial_size}:{run_seed}"),
                    )
                )
    return tuple(values)


def test_bootstrap_uses_shared_seed_slots_and_m5c_interval_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert m5c.BOOTSTRAP_DRAWS == 20_000
    monkeypatch.setattr(m5c, "BOOTSTRAP_DRAWS", 128)
    assay_id = ASSAY_IDS[0]
    summaries = bootstrap_assay_effects(
        _effects(assay_id=assay_id, shift=0.01),
        assay_id=assay_id,
        delta=1 / 48,
    )
    by_contrast = {summary.contrast: summary for summary in summaries}
    assert set(by_contrast) == {"E-G", "E-M"}
    assert all(summary.bootstrap_draws == 128 for summary in summaries)
    assert all(len(summary.cell_point_estimates) == 4 for summary in summaries)
    assert all(
        summary.ci_lower <= summary.point_estimate <= summary.ci_upper for summary in summaries
    )
    assert bootstrap_index(10, ("seed-cluster", assay_id, 13, 2)) == bootstrap_index(
        10, ("seed-cluster", assay_id, 13, 2)
    )


def _summary(contrast: str, status: str, point: float = 0.0) -> ContrastSummary:
    bounds = {
        "SUPERIOR": (0.2, 0.3),
        "INFERIOR": (-0.3, -0.2),
        "EQUIVALENT": (-0.01, 0.01),
        "NONINFERIOR_ONLY": (-0.01, 0.2),
        "INCONCLUSIVE": (-0.2, 0.2),
    }
    lower, upper = bounds[status]
    return ContrastSummary(
        assay_id=ASSAY_IDS[0],
        bootstrap_draws=20_000,
        cell_point_estimates=(point, point, point, point),
        ci_lower=lower,
        ci_upper=upper,
        contrast=contrast,
        point_estimate=point,
        status=status,
    )


def test_assay_and_cohort_taxonomy_apply_registered_precedence() -> None:
    taxonomy_input = AssayTaxonomyInput(
        actual_primary_high_value_count=48,
        assay_id=ASSAY_IDS[0],
        exploration_greedy_difference_counts=(0,) * 40,
        frozen_candidate_count=4783,
        primary_e_g=_summary("E-G", "EQUIVALENT"),
        primary_e_m=_summary("E-M", "EQUIVALENT"),
        required_defined=True,
        sensitivity_e_g_point_estimates=(0.0, 0.0),
        sensitivity_e_m_point_estimates=(0.0, 0.0),
        validity_ok=True,
    )
    assert classify_assay(taxonomy_input) == "BEHAVIOR_REDUNDANCY"

    labels = {
        ASSAY_IDS[0]: "BEHAVIOR_REDUNDANCY",
        ASSAY_IDS[1]: "BEHAVIOR_REDUNDANCY",
        ASSAY_IDS[2]: "INCONCLUSIVE",
        ASSAY_IDS[3]: "UNDEFINED_INCONCLUSIVE",
    }
    cohort = classify_cohort(labels, primary_statuses={assay: {} for assay in ASSAY_IDS})
    assert cohort.status == "REPLICATED_BEHAVIOR_REDUNDANCY"
    assert cohort.heterogeneity_reason is None

    conflict = dict(labels)
    conflict[ASSAY_IDS[1]] = "OPPORTUNITY_COST"
    cohort = classify_cohort(conflict, primary_statuses={assay: {} for assay in ASSAY_IDS})
    assert cohort.status == "HETEROGENEOUS"
    assert cohort.heterogeneity_reason == "INFORMATIVE_LABEL_CONFLICT"

    directional = {assay: {"E-G": "EQUIVALENT", "E-M": "EQUIVALENT"} for assay in ASSAY_IDS}
    directional[ASSAY_IDS[0]]["E-G"] = "SUPERIOR"
    directional[ASSAY_IDS[1]]["E-G"] = "INFERIOR"
    cohort = classify_cohort(labels, primary_statuses=directional)
    assert cohort.status == "HETEROGENEOUS"
    assert cohort.heterogeneity_reason == "DIRECTIONAL_REVERSAL"


def test_sequence_composition_matrix_is_finite_and_exact() -> None:
    matrix = build_sequence_composition_matrix(("AC", "AA"))
    assert matrix.shape == (2, 420)
    assert matrix.dtype.name == "float64"
    assert math.isclose(float(matrix[0].sum()), 2.0)
    assert math.isclose(float(matrix[1].sum()), 2.0)
    with pytest.raises(ContractValidationError, match="sequence"):
        build_sequence_composition_matrix(("AX",))
