from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from pcdb.cli import build_parser
from pcdb.contracts.experiment import ContractValidationError
from pcdb.evaluation import MetricValue, SequentialMetricSet
from pcdb.m5.seed_tracer import build_seed_tracer_packet
from pcdb.m5.seed_tracer_runtime import (
    ACCEPTED_HOURLY_COST_CNY,
    SEED_TRACER_APPROVAL,
    SeedTracerAuthorization,
    TracerRun,
    execute_reviewed_seed_tracer_locally,
    prepare_seed_tracer_execution,
    seed_tracer_summary_bytes,
)
from pcdb.replay.engine import ReplayTrajectory
from pcdb.replay.publication import ReplayPublication

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_COMMIT = "a21585853be3b54de99875c0de8a79e54b34e463"
REVIEWED_TREE = "99c0d3b43ef6df7e1f5373a617ac0962e62fe654"
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class _InputClient:
    transferred_bytes = 0

    def head(self, bucket: str, key: str) -> object:
        raise AssertionError((bucket, key))

    def get(self, bucket: str, key: str) -> bytes:
        raise AssertionError((bucket, key))


@dataclass(frozen=True)
class _Event:
    selected_candidate_ids: tuple[str, ...]
    metrics: SequentialMetricSet


@dataclass(frozen=True)
class _Trajectory:
    run_id: str
    events: tuple[_Event, ...]
    complete: bool = True
    sha256: str = "a" * 64


class _Engine:
    def __init__(self, prepared: TracerRun, *, mismatched_initial: bool = False) -> None:
        self._prepared = prepared
        self._mismatched_initial = mismatched_initial
        base = float(prepared.initial_seed - 1729) / 100
        if prepared.internal_seed is not None:
            base += float(prepared.internal_seed - 2719) / 1000
        if prepared.run_spec.acquisition_id == "greedy-predictive-mean-v1":
            base = 0.2
        self._metrics = SequentialMetricSet(
            best_found_value=MetricValue.defined(1.0 + base),
            simple_regret=MetricValue.defined(1.0 - base),
            high_value_discovery_recall=MetricValue.defined(0.4 + base),
            success_at_budget=MetricValue.defined(base > 0),
        )

    def run(self) -> _Trajectory:
        suffix = self._prepared.run_spec.acquisition_id if self._mismatched_initial else "shared"
        initial = (f"initial-{self._prepared.initial_seed}-{suffix}",)
        post = (
            f"post-{self._prepared.initial_seed}-{self._prepared.internal_seed}-"
            f"{self._prepared.run_spec.acquisition_id}",
        )
        return _Trajectory(
            run_id=self._prepared.run_spec.run_id,
            events=(_Event(initial, self._metrics), _Event(post, self._metrics)),
        )

    def final_m4_definition_metrics(self) -> tuple[tuple[str, SequentialMetricSet], ...]:
        return (
            ("upper-tail-1pct", self._metrics),
            ("upper-tail-0.5pct", self._metrics),
            ("upper-tail-2pct", self._metrics),
        )


def _packet() -> tuple[bytes, str]:
    payload = build_seed_tracer_packet(ROOT, REVIEWED_COMMIT, REVIEWED_TREE)
    return payload, hashlib.sha256(payload).hexdigest()


def _authorization(packet_sha256: str, environment_digest: str) -> SeedTracerAuthorization:
    return SeedTracerAuthorization(
        approval_comment_id=5327882037,
        approval_text=SEED_TRACER_APPROVAL,
        packet_sha256=packet_sha256,
        reviewed_git_commit=REVIEWED_COMMIT,
        reviewed_git_tree=REVIEWED_TREE,
        executed_environment_digest=environment_digest,
        execution_region="ap-chengdu",
        hourly_cost_cny=ACCEPTED_HOURLY_COST_CNY,
    )


def _patch_execution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mismatched_initial: bool = False,
) -> None:
    monkeypatch.setattr(
        "pcdb.m5.seed_tracer_runtime._read_reviewed_inputs",
        lambda *_args, **_kwargs: {"alpha-amylase": {}, "ired": {}},
    )
    monkeypatch.setattr(
        "pcdb.m5.seed_tracer_runtime._build_tracer_engine",
        lambda *, prepared, **_kwargs: _Engine(
            prepared,
            mismatched_initial=mismatched_initial,
        ),
    )

    def publish(
        trajectory: ReplayTrajectory,
        *,
        additional_outputs: Any,
        **_kwargs: Any,
    ) -> ReplayPublication:
        metric_key = additional_outputs[0][0]
        return ReplayPublication(
            attempt_key=f"{trajectory.run_id}/run-attempt.json",
            attempt_sha256="b" * 64,
            marker_key=f"{trajectory.run_id}/_SUCCESS",
            output_keys=(metric_key,),
        )

    monkeypatch.setattr("pcdb.m5.seed_tracer_runtime.publish_replay_attempt", publish)


def test_prepare_exact_28_run_execution() -> None:
    payload, sha256 = _packet()
    preparation = prepare_seed_tracer_execution(
        packet_bytes=payload,
        packet_sha256=sha256,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    assert len(preparation.runs) == len(preparation.execution.runs) == 28
    assert len(preparation.execution.tasks) == 2
    assert len({item.run_spec.run_id for item in preparation.runs}) == 28
    input_bytes = sum(
        item.size_bytes for task in preparation.execution.tasks for item in task.objects
    )
    assert 16 * 1024**2 < input_bytes < 32 * 1024**2


def test_cli_exposes_preflight_and_run_entries() -> None:
    parser = build_parser()
    preflight = parser.parse_args(
        [
            "m5-seed-tracer-preflight",
            "--reviewed-git-commit",
            REVIEWED_COMMIT,
            "--reviewed-git-tree",
            REVIEWED_TREE,
            "--repository-root",
            str(ROOT),
        ]
    )
    assert preflight.command == "m5-seed-tracer-preflight"
    run = parser.parse_args(
        [
            "m5-seed-tracer-run",
            "--packet",
            "packet.json",
            "--packet-sha256",
            "a" * 64,
            "--approval",
            SEED_TRACER_APPROVAL,
            "--approval-comment-id",
            "1",
            "--reviewed-git-commit",
            REVIEWED_COMMIT,
            "--reviewed-git-tree",
            REVIEWED_TREE,
            "--environment-digest",
            "b" * 64,
            "--hourly-cost-cny",
            "1.62",
            "--input-role-arn",
            "role",
            "--repository-root",
            str(ROOT),
            "--scratch-root",
            "scratch",
        ]
    )
    assert run.command == "m5-seed-tracer-run"


def test_fake_exact_execution_produces_one_aggregate_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, sha256 = _packet()
    preparation = prepare_seed_tracer_execution(
        packet_bytes=payload,
        packet_sha256=sha256,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    _patch_execution(monkeypatch)
    result = execute_reviewed_seed_tracer_locally(
        preparation=preparation,
        packet_bytes=payload,
        packet_sha256=sha256,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
        repository_root=ROOT,
        input_client=_InputClient(),
        scratch_root=tmp_path / "scratch",
        authorization=_authorization(
            sha256,
            preparation.runs[0].run_spec.environment_digest,
        ),
        now=lambda: NOW,
    )
    assert len(result.publications) == len(result.unique_run_ids_started) == 28
    assert len(result.metric_keys) == 28
    assert result.verdict.verdict in {
        "INITIAL-DOMINATED",
        "INTERNAL-DOMINATED",
        "MIXED-INCONCLUSIVE",
    }
    summary = seed_tracer_summary_bytes(result)
    assert b"candidate_id" not in summary
    assert b"raw_labels" not in summary
    assert (tmp_path / "scratch/seed-tracer-result.json").read_bytes() == result.result_bytes


def test_initial_pair_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, sha256 = _packet()
    preparation = prepare_seed_tracer_execution(
        packet_bytes=payload,
        packet_sha256=sha256,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    _patch_execution(monkeypatch, mismatched_initial=True)
    with pytest.raises(ContractValidationError, match="paired initial selections differ"):
        execute_reviewed_seed_tracer_locally(
            preparation=preparation,
            packet_bytes=payload,
            packet_sha256=sha256,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
            repository_root=ROOT,
            input_client=_InputClient(),
            scratch_root=tmp_path / "scratch",
            authorization=_authorization(
                sha256,
                preparation.runs[0].run_spec.environment_digest,
            ),
            now=lambda: NOW,
        )


def test_peak_rss_cap_stops_before_first_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, sha256 = _packet()
    preparation = prepare_seed_tracer_execution(
        packet_bytes=payload,
        packet_sha256=sha256,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    _patch_execution(monkeypatch)
    values = iter((1024**3 - 1, 1024**3))
    monkeypatch.setattr(
        "pcdb.m5.seed_tracer_runtime._peak_rss_bytes",
        lambda: next(values, 1024**3),
    )
    with pytest.raises(ContractValidationError, match="resource cap reached"):
        execute_reviewed_seed_tracer_locally(
            preparation=preparation,
            packet_bytes=payload,
            packet_sha256=sha256,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
            repository_root=ROOT,
            input_client=_InputClient(),
            scratch_root=tmp_path / "scratch",
            authorization=_authorization(
                sha256,
                preparation.runs[0].run_spec.environment_digest,
            ),
            now=lambda: NOW,
        )
    assert not (tmp_path / "scratch/ledger/attempts").exists()


def test_rate_drift_fails_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, sha256 = _packet()
    preparation = prepare_seed_tracer_execution(
        packet_bytes=payload,
        packet_sha256=sha256,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    _patch_execution(monkeypatch)
    authorization = _authorization(
        sha256,
        preparation.runs[0].run_spec.environment_digest,
    )
    drifted = SimpleNamespace(**{**asdict(authorization), "hourly_cost_cny": Decimal("1.63")})
    with pytest.raises(ContractValidationError, match="accepted current rate"):
        execute_reviewed_seed_tracer_locally(
            preparation=preparation,
            packet_bytes=payload,
            packet_sha256=sha256,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
            repository_root=ROOT,
            input_client=_InputClient(),
            scratch_root=tmp_path / "scratch",
            authorization=cast(SeedTracerAuthorization, drifted),
            now=lambda: NOW,
        )
