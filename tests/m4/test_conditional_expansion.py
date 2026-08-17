"""TDD coverage for the bounded Issue #76 conditional expansion."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from pcdb.cli import main
from pcdb.contracts import ContractValidationError
from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.m4.conditional_expansion import (
    PILOT_RESOURCE_USAGE,
    SELECTED_EXPLORATIONS,
    CompletedPilotAttempt,
    ConditionalRunMetrics,
    build_conditional_expansion_packet,
    cumulative_resource_ledger,
    evaluate_gate_3,
    load_conditional_expansion_packet,
    materialize_conditional_run_specs,
    prepare_conditional_expansion,
    reconcile_completed_pilot_attempts,
    reconcile_gate_3,
)
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet
from pcdb.m4.screening import PilotScreeningDecision

ROOT = Path(__file__).resolve().parents[2]
PILOT_COMMIT = "a" * 40
PILOT_TREE = "b" * 40
EXPANSION_COMMIT = "c" * 40


def _pilot_packet() -> bytes:
    return build_pilot_preflight_packet(ROOT, PILOT_COMMIT, PILOT_TREE)


def _completed_attempts() -> tuple[CompletedPilotAttempt, ...]:
    packet = _pilot_packet()
    packet_sha256 = hashlib.sha256(packet).hexdigest()
    values = []
    for index, item in enumerate(__import__("json").loads(packet)["run_specs"]):
        base = f"m4/issue-76/pilot/{packet_sha256}/runs/{item['run_id']}/attempts/attempt-001"
        values.append(
            CompletedPilotAttempt(
                run_id=item["run_id"],
                attempt_key=f"{base}/run-attempt.json",
                attempt_sha256=f"{index + 1:064x}",
                definition_metric_key=f"{base}/pilot-final-definition-metrics.json",
            )
        )
    return tuple(values)


def test_materialization_exactly_selects_320_with_48_overlap_and_272_missing() -> None:
    matrix = materialize_conditional_run_specs(
        _pilot_packet(),
        reviewed_git_commit=EXPANSION_COMMIT,
        selected_explorations=SELECTED_EXPLORATIONS,
    )

    assert len(matrix.candidate_runs) == 320
    assert len(matrix.completed_overlap) == 48
    assert len(matrix.missing_runs) == 272
    assert len(matrix.excluded_pilot_run_ids) == 24
    assert len({item.run_spec.run_id for item in matrix.candidate_runs}) == 320
    assert {item.run_spec.run_id for item in matrix.completed_overlap}.isdisjoint(
        item.run_spec.run_id for item in matrix.missing_runs
    )
    assert {item.run_spec.git_commit for item in matrix.completed_overlap} == {PILOT_COMMIT}
    assert {item.run_spec.git_commit for item in matrix.missing_runs} == {EXPANSION_COMMIT}
    assert {
        (item.run_spec.model_id, item.run_spec.acquisition_id)
        for item in matrix.candidate_runs
        if item.run_spec.acquisition_id
        not in {
            "random-seeded-sha256-v1",
            "greedy-predictive-mean-v1",
        }
    } == set(SELECTED_EXPLORATIONS)


def test_materialization_rejects_unselected_or_duplicate_exploration() -> None:
    for selections in (
        (("ridge-sklearn-v1", "uncertainty-bootstrap-std-v1"),),
        (*SELECTED_EXPLORATIONS, SELECTED_EXPLORATIONS[0]),
    ):
        with pytest.raises(ContractValidationError, match="selected exploration"):
            materialize_conditional_run_specs(
                _pilot_packet(),
                reviewed_git_commit=EXPANSION_COMMIT,
                selected_explorations=selections,
            )


def test_cumulative_ledger_starts_from_consumed_pilot_and_cannot_reset() -> None:
    ledger = cumulative_resource_ledger(PILOT_RESOURCE_USAGE)

    assert ledger == {
        "active_execution_wall_seconds": 1581.3715364050004,
        "attempts_started": 72,
        "network_transfer_bytes": 32716485,
        "scratch_high_water_bytes": 15083519,
    }
    with pytest.raises(ContractValidationError, match="pilot resource usage"):
        cumulative_resource_ledger(replace(PILOT_RESOURCE_USAGE, attempts_started=0))


def test_expansion_packet_binds_prior_72_chains_and_zero_execution_authority() -> None:
    pilot = _pilot_packet()
    payload = build_conditional_expansion_packet(
        ROOT,
        reviewed_git_commit=EXPANSION_COMMIT,
        reviewed_git_tree="d" * 40,
        pilot_packet_bytes=pilot,
        completed_pilot_attempts=_completed_attempts(),
    )
    packet = __import__("json").loads(payload)

    assert canonical_json_bytes(packet) == payload
    assert packet["matrix"] == {
        "candidate_run_specs": 320,
        "completed_overlap": 48,
        "excluded_pilot_run_specs": 24,
        "missing_run_specs": 272,
        "pilot_completed_run_specs": 72,
        "pilot_plus_expansion_attempts": 344,
    }
    assert len(packet["candidate_run_specs"]) == 320
    assert len(packet["missing_run_specs"]) == 272
    assert len(packet["completed_pilot_attempts"]) == 72
    assert packet["initial_resource_ledger"] == cumulative_resource_ledger(PILOT_RESOURCE_USAGE)
    assert packet["authorization_boundaries"] == {
        "candidate_truth_read_authorized": False,
        "conditional_execution_authorized": False,
        "cos_write_list_delete_authorized": False,
        "held_out_read_authorized": False,
        "packet_review_authorizes_execution": False,
    }

    digest = hashlib.sha256(payload).hexdigest()
    loaded = load_conditional_expansion_packet(
        payload,
        expected_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=EXPANSION_COMMIT,
        expected_git_tree="d" * 40,
        pilot_packet_bytes=pilot,
        completed_pilot_attempts=_completed_attempts(),
    )
    assert loaded["schema_version"] == "m4-conditional-expansion-packet-v1"

    with pytest.raises(ContractValidationError, match="identity"):
        load_conditional_expansion_packet(
            payload.replace(b'"missing_run_specs": 272', b'"missing_run_specs": 271'),
            expected_sha256=digest,
            repository_root=ROOT,
            expected_git_commit=EXPANSION_COMMIT,
            expected_git_tree="d" * 40,
            pilot_packet_bytes=pilot,
            completed_pilot_attempts=_completed_attempts(),
        )


def test_completed_pilot_inventory_is_derived_from_fresh_72_chain_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet = _pilot_packet()
    digest = hashlib.sha256(packet).hexdigest()
    observed_evidence: list[object] = []

    class Store:
        def __init__(self, _: Path) -> None:
            pass

        def get_bytes(self, key: str) -> bytes:
            return key.encode()

    def screen(evidence: object, **_: object) -> PilotScreeningDecision:
        observed_evidence.extend(evidence)  # type: ignore[arg-type]
        return PilotScreeningDecision(
            status="EXPAND",
            selected_explorations=SELECTED_EXPLORATIONS,
            model_decisions=(),
            failure_reason=None,
        )

    monkeypatch.setattr("pcdb.m4.conditional_expansion.LocalArtifactStore", Store)
    monkeypatch.setattr("pcdb.m4.conditional_expansion.evaluate_pilot_screening", screen)

    completed = reconcile_completed_pilot_attempts(
        repository_root=ROOT,
        pilot_packet_bytes=packet,
        pilot_packet_sha256=digest,
        pilot_scratch_root=tmp_path,
    )

    assert len(completed) == 72
    assert len(observed_evidence) == 72
    assert [item.run_id for item in completed] == [
        item["run_id"] for item in __import__("json").loads(packet)["run_specs"]
    ]


def test_expansion_preparation_exposes_only_272_missing_runs_and_zero_authority() -> None:
    pilot = _pilot_packet()
    packet = build_conditional_expansion_packet(
        ROOT,
        reviewed_git_commit=EXPANSION_COMMIT,
        reviewed_git_tree="d" * 40,
        pilot_packet_bytes=pilot,
        completed_pilot_attempts=_completed_attempts(),
    )
    preparation = prepare_conditional_expansion(
        packet_bytes=packet,
        packet_sha256=hashlib.sha256(packet).hexdigest(),
        repository_root=ROOT,
        expected_git_commit=EXPANSION_COMMIT,
        expected_git_tree="d" * 40,
        pilot_packet_bytes=pilot,
        completed_pilot_attempts=_completed_attempts(),
    )

    assert len(preparation.runs) == 272
    assert len({item.run_spec.run_id for item in preparation.runs}) == 272
    assert preparation.attempts_authorized == 0
    assert preparation.oracle_calls_authorized == 0
    assert preparation.writes_authorized == 0


def _metrics_for_matrix(*, retain_rf: bool) -> tuple[ConditionalRunMetrics, ...]:
    matrix = materialize_conditional_run_specs(
        _pilot_packet(),
        reviewed_git_commit=EXPANSION_COMMIT,
        selected_explorations=SELECTED_EXPLORATIONS,
    )
    values: list[ConditionalRunMetrics] = []
    for prepared in matrix.candidate_runs:
        acquisition_id = prepared.run_spec.acquisition_id
        primary = 0.50
        successes = False
        if (
            retain_rf
            and prepared.run_spec.model_id == "random-forest-sklearn-v1"
            and acquisition_id == "uncertainty-bootstrap-std-v1"
        ):
            primary = 0.52
            successes = prepared.run_spec.seed != 1729
        values.append(
            ConditionalRunMetrics(
                task_id=prepared.task_id,
                run_spec=prepared.run_spec,
                recalls=(
                    ("upper-tail-1pct", primary),
                    ("upper-tail-0.5pct", primary),
                    ("upper-tail-2pct", primary),
                ),
                successes=(("upper-tail-1pct", successes),),
                evidence_sha256=hashlib.sha256(prepared.run_spec.run_id.encode()).hexdigest(),
            )
        )
    return tuple(values)


def test_gate_3_requires_exact_320_cover_and_returns_only_frozen_stop_or_inconclusive() -> None:
    retained = evaluate_gate_3(_metrics_for_matrix(retain_rf=True))
    stopped = evaluate_gate_3(_metrics_for_matrix(retain_rf=False))
    incomplete = evaluate_gate_3(_metrics_for_matrix(retain_rf=True)[:-1])

    assert retained.status == "FROZEN"
    assert len(retained.seed_evidence) == 320
    assert len({item.run_id for item in retained.seed_evidence}) == 320
    assert {
        (item.task_id, item.model_id, item.acquisition_id, item.initial_sample_size)
        for item in retained.cell_decisions
        if item.status == "retained"
    } == {
        (
            task_id,
            "random-forest-sklearn-v1",
            "uncertainty-bootstrap-std-v1",
            initial_size,
        )
        for task_id in ("alpha-amylase", "ired")
        for initial_size in (32, 64)
    }
    assert stopped.status == "STOP"
    assert incomplete.status == "INCONCLUSIVE"
    assert incomplete.failure_reason == "Gate 3 evidence does not exact-cover 320 RunSpecs"


def test_gate_3_reconciliation_fresh_loads_exactly_48_pilot_and_272_expansion_chains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _metrics_for_matrix(retain_rf=True)
    pilot_metrics = iter(metrics[:48])
    expansion_metrics = iter(metrics[48:])
    pilot_evidence = tuple(object() for _ in range(48))
    expansion_evidence = tuple(object() for _ in range(272))
    monkeypatch.setattr(
        "pcdb.m4.conditional_expansion.load_pilot_conditional_metrics",
        lambda *_args, **_kwargs: next(pilot_metrics),
    )
    monkeypatch.setattr(
        "pcdb.m4.conditional_expansion.load_conditional_run_metrics",
        lambda *_args, **_kwargs: next(expansion_metrics),
    )

    decision = reconcile_gate_3(
        pilot_evidence,  # type: ignore[arg-type]
        expansion_evidence,  # type: ignore[arg-type]
        expected_pilot_packet_sha256="a" * 64,
        expected_expansion_packet_sha256="b" * 64,
        expected_execution_region="ap-chengdu",
    )

    assert decision.status == "FROZEN"
    assert (
        reconcile_gate_3(
            pilot_evidence[:-1],  # type: ignore[arg-type]
            expansion_evidence,  # type: ignore[arg-type]
            expected_pilot_packet_sha256="a" * 64,
            expected_expansion_packet_sha256="b" * 64,
            expected_execution_region="ap-chengdu",
        ).status
        == "INCONCLUSIVE"
    )


def test_preflight_cli_fresh_reconciles_pilot_and_prints_review_only_packet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pilot = _pilot_packet()
    pilot_path = tmp_path / "pilot.json"
    pilot_path.write_bytes(pilot)
    monkeypatch.setattr(
        "pcdb.m4.conditional_expansion.reconcile_completed_pilot_attempts",
        lambda **_: _completed_attempts(),
    )

    result = main(
        [
            "m4-conditional-preflight",
            "--reviewed-git-commit",
            EXPANSION_COMMIT,
            "--reviewed-git-tree",
            "d" * 40,
            "--repository-root",
            str(ROOT),
            "--pilot-packet",
            str(pilot_path),
            "--pilot-packet-sha256",
            hashlib.sha256(pilot).hexdigest(),
            "--pilot-scratch-root",
            str(tmp_path / "pilot-scratch"),
        ]
    )

    assert result == 0
    assert capsys.readouterr().out.encode() == build_conditional_expansion_packet(
        ROOT,
        reviewed_git_commit=EXPANSION_COMMIT,
        reviewed_git_tree="d" * 40,
        pilot_packet_bytes=pilot,
        completed_pilot_attempts=_completed_attempts(),
    )
