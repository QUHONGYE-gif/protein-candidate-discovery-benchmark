"""Boundary tests for the bounded Issue #83 failure analysis."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.contracts.run import RunSpec
from pcdb.m4.conditional_expansion import (
    SELECTED_EXPLORATIONS,
    Gate3CellDecision,
    Gate3Decision,
)
from pcdb.m5.failure_analysis import (
    CHECKPOINTS,
    DEFINITION_IDS,
    GREEDY,
    INITIAL_SIZES,
    MODELS,
    RANDOM,
    SEEDS,
    TASKS,
    AnalysisIncomplete,
    AnalysisRun,
    TerminalSeed,
    _h1_cell_predicate,
    _h2_cell_predicate,
    _h3_static_signal,
    _h4_cell_predicate,
    _h6_cell_predicate,
    _hypothesis_1,
    _hypothesis_5,
    _hypothesis_6,
    _jaccard,
    _majority_by_task,
    _pair_relation,
    _pair_stats,
    _paired,
    _rank_concordance,
    _run_index,
    build_gate_blocker_map,
    load_m2_static_rows,
)
from pcdb.replay.engine import ReplayTrajectory

ROOT = Path(__file__).resolve().parents[2]


def _run_spec(model: str, acquisition: str, initial: int, seed: int) -> RunSpec:
    return RunSpec(
        schema_version="1",
        protocol_version="m4-single-fidelity-screening-v1",
        git_commit="a" * 40,
        environment_digest="b" * 64,
        dataset_manifest_sha256="c" * 64,
        processing_manifest_sha256="d" * 64,
        split_sha256="e" * 64,
        feature_sha256="f" * 64,
        experiment_config_sha256="1" * 64,
        model_id=model,
        model_config_sha256="2" * 64,
        acquisition_id=acquisition,
        acquisition_config_sha256="3" * 64,
        seed=seed,
        initial_sample_size=initial,
        batch_size=16,
        budget=128,
    )


def _analysis_run(
    task: str,
    model: str,
    acquisition: str,
    initial: int,
    seed: int,
    *,
    recall: Decimal = Decimal(0),
    regret: Decimal = Decimal(0),
) -> AnalysisRun:
    spec = _run_spec(model, acquisition, initial, seed)
    terminal = TerminalSeed(
        task_id=task,
        model_id=model,
        acquisition_id=acquisition,
        initial_sample_size=initial,
        seed=seed,
        run_id=spec.run_id,
        recalls=tuple((definition, recall) for definition in DEFINITION_IDS),
        successes=(("upper-tail-1pct", False),),
        evidence_sha256=hashlib.sha256(spec.run_id.encode()).hexdigest(),
    )
    checkpoints = tuple(
        (
            checkpoint,
            (
                ("high_value_discovery_recall", recall),
                ("simple_regret", regret),
                ("best_found_value", Decimal(0)),
            ),
        )
        for checkpoint in CHECKPOINTS
    )
    post = tuple((checkpoint, frozenset({f"{acquisition}-{seed}"})) for checkpoint in CHECKPOINTS)
    return AnalysisRun(
        terminal=terminal,
        trajectory=cast(ReplayTrajectory, object()),
        checkpoints=checkpoints,
        post_initial_by_checkpoint=post,
    )


def _full_index() -> dict[tuple[str, str, str, int, int], AnalysisRun]:
    values = []
    for task in TASKS:
        for model in MODELS:
            acquisitions = [RANDOM, GREEDY]
            acquisitions.extend(
                acquisition
                for selected_model, acquisition in SELECTED_EXPLORATIONS
                if selected_model == model
            )
            for acquisition in acquisitions:
                for initial in INITIAL_SIZES:
                    for seed in SEEDS:
                        values.append(_analysis_run(task, model, acquisition, initial, seed))
    return _run_index(values)


def test_m2_input_is_exactly_identity_bound_and_six_row_complete() -> None:
    payload = (ROOT / "docs/M2_GATE_B_REVIEW_PACKET.md").read_bytes()

    rows = load_m2_static_rows(payload)

    assert len(rows) == 6
    assert {(row.task_id, row.model_id) for row in rows} == {
        (task, model) for task in TASKS for model in MODELS
    }
    for mutation in (payload + b"\n", payload[:-1] + bytes([payload[-1] ^ 1])):
        with pytest.raises(ContractValidationError, match="identity drifted"):
            load_m2_static_rows(mutation)
    with pytest.raises(ContractValidationError, match="identity drifted"):
        load_m2_static_rows(payload, repository_path="docs/other.md")
    with pytest.raises(ContractValidationError, match="identity drifted"):
        load_m2_static_rows(payload, git_commit="0" * 40)


def test_common_statistics_cover_zero_variance_ci_touch_and_loo_zero_sign() -> None:
    zeros = _pair_stats((Decimal(0),) * 10)
    positive = _pair_stats((Decimal(1),) * 10)
    loo_change = _pair_stats((Decimal(1),) * 9 + (Decimal(-9),))

    assert zeros.sample_sd == 0
    assert zeros.ci_lower == zeros.ci_upper == 0
    assert zeros.ci_lower <= 0 <= zeros.ci_upper
    assert not zeros.loo_sign_change
    assert positive.sample_sd == 0
    assert positive.ci_lower == positive.ci_upper == 1
    assert not (positive.ci_lower <= 0 <= positive.ci_upper)
    assert loo_change.mean == 0
    assert loo_change.loo_sign_change
    positive_to_zero = _pair_stats((Decimal(1),) * 9 + (Decimal(-8),))
    negative_to_zero = _pair_stats((Decimal(-1),) * 9 + (Decimal(8),))
    assert positive_to_zero.mean > 0 and positive_to_zero.loo_sign_change
    assert negative_to_zero.mean < 0 and negative_to_zero.loo_sign_change
    signed = _pair_stats(
        (Decimal(-2), Decimal(-1), Decimal(0), Decimal(0), Decimal(0))
        + (Decimal(1), Decimal(2), Decimal(3), Decimal(4), Decimal(5))
    )
    assert (signed.positive, signed.zero, signed.negative) == (5, 3, 2)
    for invalid_n in (2, 4, 9, 11):
        with pytest.raises(AnalysisIncomplete, match="exactly n=3, n=7, or n=10"):
            _pair_stats((Decimal(0),) * invalid_n)


def test_exact_cover_duplicate_checkpoint_and_three_of_four_boundaries() -> None:
    index = _full_index()
    runs = list(index.values())
    runs[-1] = runs[0]
    with pytest.raises(ContractValidationError, match="exact-cover"):
        _run_index(runs)

    first_key = (
        TASKS[0],
        SELECTED_EXPLORATIONS[0][0],
        SELECTED_EXPLORATIONS[0][1],
        INITIAL_SIZES[0],
        SEEDS[0],
    )
    first = index[first_key]
    index[first_key] = replace(
        first,
        checkpoints=tuple(row for row in first.checkpoints if row[0] != 64),
    )
    with pytest.raises(AnalysisIncomplete, match="required checkpoint metric is absent"):
        _hypothesis_1(index)

    flags = {
        (task, model, acquisition, initial): False
        for task in TASKS
        for model, acquisition in SELECTED_EXPLORATIONS
        for initial in INITIAL_SIZES
    }
    for task in TASKS:
        for cell in sorted(cell for cell in flags if cell[0] == task)[:3]:
            flags[cell] = True
    assert _majority_by_task(flags)
    flags[next(cell for cell in flags if cell[0] == TASKS[0] and flags[cell])] = False
    assert not _majority_by_task(flags)


def test_h1_strict_sd_and_quantization_boundaries() -> None:
    base = _pair_stats((Decimal(0),) * 10)
    start = replace(
        base,
        mean=Decimal("0.2"),
        sample_sd=Decimal("0.1"),
        positive=5,
        zero=0,
        negative=5,
    )
    equal_sd = replace(start, mean=Decimal("0.05"), positive=6, negative=4)
    predicate = _h1_cell_predicate(
        start,
        equal_sd,
        Decimal("0.05"),
        {definition: Decimal("0.05") for definition in DEFINITION_IDS},
    )

    assert predicate["quantized"]
    assert predicate["sign_decline"]
    assert not predicate["sd_decline"]
    assert not predicate["cell_support"]
    assert not predicate["cell_against"]

    declining = replace(equal_sd, sample_sd=Decimal("0.099"))
    sensitive = _h1_cell_predicate(
        start,
        declining,
        Decimal("0.04"),
        {"a": Decimal("0.01"), "b": Decimal("-0.01")},
    )
    assert sensitive["definition_sensitive"]
    assert sensitive["cell_support"]


def test_h2_same_order_half_dispersion_and_zero_boundaries() -> None:
    base = _pair_stats((Decimal(1),) * 10)
    equality = replace(
        base,
        mean=Decimal(1),
        sample_sd=Decimal(1),
        ci_lower=Decimal("0.1"),
        ci_upper=Decimal("1.9"),
    )
    result = _h2_cell_predicate(equality)
    assert result["same_order_or_larger"]
    assert not result["small_dispersion"]
    assert not result["cell_support"]
    assert not result["cell_against"]

    half = replace(equality, sample_sd=Decimal("0.5"))
    below_half = replace(equality, sample_sd=Decimal("0.499"))
    assert not _h2_cell_predicate(half)["small_dispersion"]
    assert _h2_cell_predicate(below_half)["cell_against"]

    zeros = _pair_stats((Decimal(0),) * 10)
    assert _h2_cell_predicate(zeros)["cell_support"]


def test_h4_strict_shrinkage_cross_zero_and_ci_endpoint_boundaries() -> None:
    base = _pair_stats((Decimal(1),) * 3)
    pilot = replace(base, mean=Decimal(1), ci_lower=Decimal("0.1"), ci_upper=Decimal(2))
    equal_magnitude = replace(
        _pair_stats((Decimal(-1),) * 7),
        mean=Decimal(-1),
        ci_lower=Decimal(-2),
        ci_upper=Decimal("-0.1"),
    )
    equality = _h4_cell_predicate(pilot, equal_magnitude)
    assert not equality["shrinkage"]
    assert equality["cross_zero"]
    assert not equality["ci_loses_exclusion"]

    reaches_zero = replace(
        equal_magnitude, mean=Decimal(0), ci_lower=Decimal(-1), ci_upper=Decimal(0)
    )
    boundary = _h4_cell_predicate(pilot, reaches_zero)
    assert boundary["shrinkage"]
    assert boundary["cross_zero"]
    assert boundary["ci_loses_exclusion"]


def test_exact_320_cover_and_metric_direction_fail_closed() -> None:
    index = _full_index()
    cell = (
        "alpha-amylase",
        "random-forest-sklearn-v1",
        "uncertainty-bootstrap-std-v1",
        32,
    )
    exploration_key = (*cell, 1729)
    greedy_key = (cell[0], cell[1], GREEDY, cell[3], 1729)
    index[exploration_key] = _analysis_run(*exploration_key, recall=Decimal(2), regret=Decimal(3))
    index[greedy_key] = _analysis_run(*greedy_key, recall=Decimal(1), regret=Decimal(5))

    assert _paired(index, cell, (1729,))[0] == 1
    assert _paired(index, cell, (1729,), metric_id="simple_regret")[0] == 2
    with pytest.raises(ContractValidationError, match="exact-cover"):
        _run_index(tuple(index.values())[:-1])


def test_h3_ties_and_zero_concordance_are_rank_mismatch() -> None:
    left = {MODELS[0]: Decimal(3), MODELS[1]: Decimal(2), MODELS[2]: Decimal(1)}
    reversed_rank = {MODELS[0]: Decimal(1), MODELS[1]: Decimal(2), MODELS[2]: Decimal(3)}
    one_tie = {MODELS[0]: Decimal(1), MODELS[1]: Decimal(1), MODELS[2]: Decimal(3)}

    assert _rank_concordance(left, left) == 1
    assert _rank_concordance(left, reversed_rank) == -1
    assert _rank_concordance(left, one_tie) == Decimal(-2) / Decimal(3)
    all_tied = {model: Decimal(1) for model in MODELS}
    assert _rank_concordance(left, all_tied) == 0


def test_h3_weak_and_strong_threshold_equalities() -> None:
    q = Decimal("0.05")
    weak_spearman = {model: Decimal("0.199") for model in MODELS}
    weak_primary = {model: q for model in MODELS}
    assert _h3_static_signal(weak_spearman, weak_primary, q) == "weak"

    at_weak_boundary = {model: Decimal("0.20") for model in MODELS}
    assert _h3_static_signal(at_weak_boundary, weak_primary, q) == "intermediate"

    strong_spearman = {model: Decimal("0.30") for model in MODELS}
    strong_primary = {model: Decimal(2) * q for model in MODELS}
    assert _h3_static_signal(strong_spearman, strong_primary, q) == "strong"


def test_h5_and_h6_equality_boundaries_are_exact() -> None:
    q = Decimal("0.05")
    equal_gap = _pair_relation(Decimal("0.05"), Decimal(0), q)
    half_ratio = _pair_relation(Decimal(1), Decimal("0.5"), q)

    assert not equal_gap["large_gap"]
    assert not equal_gap["sign_reversal"]
    assert half_ratio["similar_magnitude"]
    residue_reversal = _pair_relation(Decimal("1E-18"), Decimal("-6E-19"), q)
    assert residue_reversal["sign_reversal"]
    assert _jaccard(frozenset(), frozenset()) == 1
    assert _jaccard(frozenset({"a"}), frozenset({"a", "b"})) == Decimal("0.5")

    separated_boundary = _h6_cell_predicate(Decimal("0.40"), Decimal("0.50"), q, q)
    assert separated_boundary["clearly_separated"]
    assert separated_boundary["small_effect"]
    assert not separated_boundary["redundant"]
    redundant_equality = _h6_cell_predicate(Decimal("0.50"), Decimal("0.50"), q, q)
    assert redundant_equality["redundant"]
    assert redundant_equality["cell_support"]

    index = _full_index()
    h5 = _hypothesis_5(index)
    h6 = _hypothesis_6(index)
    assert isinstance(h5.details, dict)
    assert len(h5.details["task_pairs"]) == len(h5.details["initial_pairs"]) == 4
    assert isinstance(h6.details, tuple) and len(h6.details) == 8


def _cell(
    *,
    status: str,
    recall_pass: bool = False,
    success_pass: bool = False,
    sensitivity: Decimal = Decimal(0),
    primary: Decimal = Decimal("0.01"),
    success: Decimal = Decimal("0.10"),
    heterogeneous: bool = False,
    secondary_sensitivity: Decimal = Decimal(0),
) -> Gate3CellDecision:
    return Gate3CellDecision(
        task_id="alpha-amylase",
        model_id="random-forest-sklearn-v1",
        acquisition_id="uncertainty-bootstrap-std-v1",
        initial_sample_size=32,
        positive_primary_seeds=7,
        primary_recall_mean_difference=float(primary),
        success_probability_difference=float(success),
        sensitivity_mean_differences=(
            ("upper-tail-0.5pct", float(sensitivity)),
            ("upper-tail-2pct", float(secondary_sensitivity)),
        ),
        recall_pass=recall_pass,
        success_pass=success_pass,
        task_heterogeneous=heterogeneous,
        status=status,
    )


def test_h7_uses_only_authoritative_boolean_paths_and_annotation_is_nonblocking() -> None:
    variants = (
        _cell(status="fragile-inconclusive", sensitivity=Decimal("-0.0001")),
        _cell(
            status="fragile-inconclusive",
            secondary_sensitivity=Decimal("-0.0001"),
        ),
        _cell(status="eliminated", primary=Decimal(0), success=Decimal(0)),
        _cell(status="eliminated", recall_pass=True, success=Decimal("-0.01")),
        _cell(status="eliminated", success_pass=True, primary=Decimal("-0.01")),
        _cell(status="retained", recall_pass=True, heterogeneous=True),
        _cell(status="retained", success_pass=True),
        _cell(status="retained", recall_pass=True, success_pass=True),
    )
    decision = Gate3Decision(
        status="STOP",
        cell_decisions=variants,
        seed_evidence=(),
        frozen_run_ids=(),
        failure_reason=None,
    )

    rows = build_gate_blocker_map(decision)

    assert [row["category"] for row in rows] == [
        "SENSITIVITY_REVERSAL",
        "SENSITIVITY_REVERSAL",
        "NO_PRIMARY_PASS",
        "OTHER_PRIMARY_NEGATIVE",
        "OTHER_PRIMARY_NEGATIVE",
        "RETAINED",
        "RETAINED",
        "RETAINED",
    ]
    assert rows[5]["task_heterogeneous_annotation"] is True
    assert rows[5]["category"] == "RETAINED"
    first_margins = dict(cast(tuple[tuple[str, Decimal], ...], rows[0]["sensitivity_zero_margins"]))
    assert first_margins["upper-tail-2pct"] == 0
    assert rows[5]["positive_count_margin"] == 0
    assert rows[6]["primary_mean_margin"] == 0
    assert rows[7]["success_margin"] == 0
    assert build_gate_blocker_map(replace(decision, status="INCONCLUSIVE")) == ()


def test_h7_rejects_status_that_disagrees_with_the_authoritative_path() -> None:
    wrong = _cell(status="retained", sensitivity=Decimal("-0.1"))
    decision = Gate3Decision(
        status="STOP",
        cell_decisions=(wrong,) * 8,
        seed_evidence=(),
        frozen_run_ids=(),
        failure_reason=None,
    )

    with pytest.raises(ContractValidationError, match="differs"):
        build_gate_blocker_map(decision)
