"""Fail-closed orchestration checks for the bounded Issue #76 pilot."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.evaluation import MetricValue, SequentialMetricSet
from pcdb.m4.execution_adapter import (
    InputObjectIdentity,
    M4ExecutionPreparation,
    TaskInputInventory,
    prepare_execution_adapter,
)
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet
from pcdb.m4.pilot_runtime import (
    PILOT_APPROVAL,
    PilotAuthorization,
    execute_reviewed_pilot_locally,
)
from pcdb.m4.screening import load_pilot_run_recall
from pcdb.replay import ReplayPublication

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_COMMIT = "a" * 40
REVIEWED_TREE = "b" * 40
PURPOSES = (
    "candidate-feature-matrix",
    "dataset-manifest",
    "feature-descriptor",
    "processing-manifest",
    "split-manifest",
    "hidden-truth",
)


class _Metadata:
    def __init__(self, payload: bytes) -> None:
        self.size_bytes = len(payload)
        self.sha256 = hashlib.sha256(payload).hexdigest()


class _InputClient:
    def __init__(self, values: Mapping[tuple[str, str], bytes]) -> None:
        self.values = dict(values)
        self.operations: list[tuple[str, str]] = []
        self._transferred_bytes = 0

    @property
    def transferred_bytes(self) -> int:
        return self._transferred_bytes

    def head(self, bucket: str, key: str) -> _Metadata:
        self.operations.append(("HEAD", key))
        return _Metadata(self.values[(bucket, key)])

    def get(self, bucket: str, key: str) -> bytes:
        self.operations.append(("GET", key))
        payload = self.values[(bucket, key)]
        self._transferred_bytes += len(payload)
        return payload


class _Trajectory:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.complete = True

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.run_id.encode()).hexdigest()


class _Engine:
    def __init__(self, run_id: str, calls: list[str], *, fail: bool = False) -> None:
        self._run_id = run_id
        self._calls = calls
        self._fail = fail

    def run(self) -> _Trajectory:
        self._calls.append(self._run_id)
        if self._fail:
            raise RuntimeError("synthetic pilot failure")
        return _Trajectory(self._run_id)

    def final_m4_definition_metrics(self) -> tuple[tuple[str, SequentialMetricSet], ...]:
        metrics = SequentialMetricSet(
            best_found_value=MetricValue.defined(1.0),
            simple_regret=MetricValue.defined(0.0),
            high_value_discovery_recall=MetricValue.defined(0.5),
            success_at_budget=MetricValue.defined(True),
        )
        return tuple(
            (definition_id, metrics)
            for definition_id in (
                "upper-tail-1pct",
                "upper-tail-0.5pct",
                "upper-tail-2pct",
            )
        )


def _packet() -> tuple[bytes, str]:
    payload = build_pilot_preflight_packet(ROOT, REVIEWED_COMMIT, REVIEWED_TREE)
    return payload, hashlib.sha256(payload).hexdigest()


def _authorization(packet_sha256: str) -> PilotAuthorization:
    return PilotAuthorization(
        approval_comment_id=123,
        approval_text=PILOT_APPROVAL,
        packet_sha256=packet_sha256,
        reviewed_git_commit=REVIEWED_COMMIT,
        reviewed_git_tree=REVIEWED_TREE,
        executed_environment_digest=(
            "0994a73f124c2baa6f330bf8739db1673217bdec6ad7d9046ded908fc8dba4fc"
        ),
    )


def _synthetic_preparation(
    preparation: M4ExecutionPreparation,
) -> tuple[M4ExecutionPreparation, dict[tuple[str, str], bytes]]:
    values: dict[tuple[str, str], bytes] = {}
    tasks: list[TaskInputInventory] = []
    for task in preparation.tasks:
        objects = []
        for purpose in PURPOSES:
            payload = f"{task.task_id}:{purpose}".encode()
            bucket = "reviewed-public-inputs"
            key = f"{task.task_id}/{purpose}"
            values[(bucket, key)] = payload
            objects.append(
                InputObjectIdentity(
                    bucket=bucket,
                    object_key=key,
                    purpose=purpose,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                    task_id=task.task_id,
                    truth_bearing=purpose == "hidden-truth",
                )
            )
        tasks.append(TaskInputInventory(task_id=task.task_id, objects=tuple(objects)))
    return replace(preparation, tasks=tuple(tasks)), values


def _install_runtime_fakes(
    monkeypatch: pytest.MonkeyPatch,
    preparation: M4ExecutionPreparation,
    *,
    fail_run_id: str | None = None,
) -> tuple[list[str], list[str]]:
    run_calls: list[str] = []
    publication_calls: list[str] = []
    monkeypatch.setattr(
        "pcdb.m4.pilot_runtime.prepare_execution_adapter",
        lambda **_kwargs: preparation,
    )

    def engine(**kwargs: Any) -> _Engine:
        run_id = kwargs["run_spec"].run_id
        return _Engine(run_id, run_calls, fail=run_id == fail_run_id)

    monkeypatch.setattr("pcdb.m4.pilot_runtime.build_reviewed_replay_engine", engine)

    def publish(trajectory: _Trajectory, **_kwargs: Any) -> ReplayPublication:
        publication_calls.append(trajectory.run_id)
        return ReplayPublication(
            attempt_key=f"runs/{trajectory.run_id}/attempts/attempt-001/run-attempt.json",
            attempt_sha256="c" * 64,
            marker_key=f"runs/{trajectory.run_id}/attempts/attempt-001/_SUCCESS",
            output_keys=(),
        )

    monkeypatch.setattr("pcdb.m4.pilot_runtime.publish_replay_attempt", publish)
    return run_calls, publication_calls


def test_missing_external_authorization_fails_before_inputs_or_scratch(
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    preparation, values = _synthetic_preparation(preparation)
    client = _InputClient(values)
    scratch = tmp_path / "pilot"

    with pytest.raises(ContractValidationError, match="authorization"):
        execute_reviewed_pilot_locally(
            preparation=preparation,
            packet_bytes=packet,
            packet_sha256=digest,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
            repository_root=ROOT,
            input_client=client,
            scratch_root=scratch,
            authorization=replace(_authorization(digest), approval_text="not approved"),
        )

    assert client.operations == []
    assert not scratch.exists()


def test_exact_pilot_heads_all_then_reads_nontruth_before_truth_and_runs_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    canonical = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    preparation, values = _synthetic_preparation(canonical)
    run_calls, publication_calls = _install_runtime_fakes(monkeypatch, preparation)
    client = _InputClient(values)
    current = datetime(2026, 8, 16, tzinfo=UTC)

    def now() -> datetime:
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    result = execute_reviewed_pilot_locally(
        preparation=preparation,
        packet_bytes=packet,
        packet_sha256=digest,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
        repository_root=ROOT,
        input_client=client,
        scratch_root=tmp_path / "pilot",
        authorization=_authorization(digest),
        now=now,
    )

    assert len(result.publications) == 72
    assert len(result.definition_metric_keys) == 72
    assert run_calls == [item.run_spec.run_id for item in preparation.runs]
    assert publication_calls == run_calls
    assert result.unique_run_ids_started == tuple(run_calls)
    assert len(set(run_calls)) == 72
    assert len(list((tmp_path / "pilot" / "ledger" / "attempts").glob("*.started.json"))) == 72

    methods = [method for method, _ in client.operations]
    assert methods[:12] == ["HEAD"] * 12
    assert methods[12:] == ["GET"] * 12
    get_keys = [key for method, key in client.operations if method == "GET"]
    assert all("hidden-truth" not in key for key in get_keys[:-2])
    assert [key.rsplit("/", 1)[-1] for key in get_keys[-2:]] == [
        "hidden-truth",
        "hidden-truth",
    ]
    first_metric = (tmp_path / "pilot" / "objects" / result.definition_metric_keys[0]).read_bytes()
    loaded = load_pilot_run_recall(
        first_metric,
        run_spec=preparation.runs[0].run_spec,
        expected_task_id=preparation.runs[0].task_id,
        expected_packet_sha256=digest,
        expected_trajectory_sha256=hashlib.sha256(
            preparation.runs[0].run_spec.run_id.encode()
        ).hexdigest(),
    )
    assert loaded.task_id == preparation.runs[0].task_id
    assert dict(loaded.recalls) == {
        "upper-tail-1pct": 0.5,
        "upper-tail-0.5pct": 0.5,
        "upper-tail-2pct": 0.5,
    }


def test_input_identity_failure_starts_no_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    canonical = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    preparation, values = _synthetic_preparation(canonical)
    _install_runtime_fakes(monkeypatch, preparation)
    first = preparation.tasks[0].objects[0]
    values[(first.bucket, first.object_key)] += b"-drift"

    with pytest.raises(ContractValidationError, match="HEAD identity"):
        execute_reviewed_pilot_locally(
            preparation=preparation,
            packet_bytes=packet,
            packet_sha256=digest,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
            repository_root=ROOT,
            input_client=_InputClient(values),
            scratch_root=tmp_path / "pilot",
            authorization=_authorization(digest),
        )

    assert not (tmp_path / "pilot" / "ledger" / "attempts").exists()


def test_first_run_failure_is_counted_once_and_stops_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    canonical = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    preparation, values = _synthetic_preparation(canonical)
    first_run_id = preparation.runs[0].run_spec.run_id
    run_calls, publication_calls = _install_runtime_fakes(
        monkeypatch,
        preparation,
        fail_run_id=first_run_id,
    )

    with pytest.raises(RuntimeError, match="synthetic pilot failure"):
        execute_reviewed_pilot_locally(
            preparation=preparation,
            packet_bytes=packet,
            packet_sha256=digest,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
            repository_root=ROOT,
            input_client=_InputClient(values),
            scratch_root=tmp_path / "pilot",
            authorization=_authorization(digest),
        )

    assert run_calls == [first_run_id]
    assert publication_calls == []
    markers = list((tmp_path / "pilot" / "ledger" / "attempts").glob("*.started.json"))
    assert len(markers) == 1
    with pytest.raises(ContractValidationError, match="start empty"):
        execute_reviewed_pilot_locally(
            preparation=preparation,
            packet_bytes=packet,
            packet_sha256=digest,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
            repository_root=ROOT,
            input_client=_InputClient(values),
            scratch_root=tmp_path / "pilot",
            authorization=_authorization(digest),
        )
