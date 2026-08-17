"""Fail-closed orchestration checks for the bounded Issue #76 pilot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pcdb.contracts import ContractValidationError, load_run_attempt_v1
from pcdb.contracts.experiment import canonical_json_bytes
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
    PILOT_EXECUTION_REGION,
    PilotAuthorization,
    execute_reviewed_pilot_locally,
)
from pcdb.m4.screening import PilotRunEvidence, load_pilot_run_recall
from pcdb.replay import (
    Observation,
    ReplayEvent,
    ReplayPublication,
    ReplayTrajectory,
    publish_replay_attempt,
)
from pcdb.storage import LocalArtifactStore

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
        execution_region=PILOT_EXECUTION_REGION,
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

    def publish(trajectory: _Trajectory, **kwargs: Any) -> ReplayPublication:
        publication_calls.append(trajectory.run_id)
        additional_outputs = kwargs["additional_outputs"]
        assert len(additional_outputs) == 1
        definition_key, _ = additional_outputs[0]
        assert kwargs["region"] == PILOT_EXECUTION_REGION
        return ReplayPublication(
            attempt_key=f"runs/{trajectory.run_id}/attempts/attempt-001/run-attempt.json",
            attempt_sha256="c" * 64,
            marker_key=f"runs/{trajectory.run_id}/attempts/attempt-001/_SUCCESS",
            output_keys=(definition_key,),
        )

    monkeypatch.setattr("pcdb.m4.pilot_runtime.publish_replay_attempt", publish)
    return run_calls, publication_calls


def _complete_m4_trajectory(run_spec: Any, task_id: str) -> ReplayTrajectory:
    metrics = SequentialMetricSet(
        best_found_value=MetricValue.defined(1.0),
        simple_regret=MetricValue.defined(0.0),
        high_value_discovery_recall=MetricValue.defined(0.5),
        success_at_budget=MetricValue.defined(True),
    )
    metric_sha256 = hashlib.sha256(canonical_json_bytes(asdict(metrics))).hexdigest()
    candidate_ids = tuple(f"{index:064x}" for index in range(1, 129))
    events: list[ReplayEvent] = []
    offset = 0
    for round_index, size in enumerate((32, 16, 16, 16, 16, 16, 16)):
        selected = candidate_ids[offset : offset + size]
        offset += size
        provisional = ReplayEvent(
            round=round_index,
            selected_candidate_ids=selected,
            observations=tuple(
                Observation(candidate_id=candidate_id, label=float(index))
                for index, candidate_id in enumerate(selected)
            ),
            distinct_revealed=offset,
            checkpoint_sha256="0" * 64,
            metrics=metrics,
            metric_sha256=metric_sha256,
        )
        prefix = ReplayTrajectory(
            run_spec=run_spec,
            task_id=task_id,
            events=(*events, provisional),
        )
        events.append(
            replace(
                provisional,
                checkpoint_sha256=hashlib.sha256(prefix.checkpoint_bytes(round_index)).hexdigest(),
            )
        )
    return ReplayTrajectory(run_spec=run_spec, task_id=task_id, events=tuple(events))


def _definition_record(
    *,
    packet_sha256: str,
    task_id: str,
    trajectory: ReplayTrajectory,
) -> bytes:
    metrics = SequentialMetricSet(
        best_found_value=MetricValue.defined(1.0),
        simple_regret=MetricValue.defined(0.0),
        high_value_discovery_recall=MetricValue.defined(0.5),
        success_at_budget=MetricValue.defined(True),
    )
    return canonical_json_bytes(
        {
            "attempt_id": "attempt-001",
            "budget": trajectory.run_spec.budget,
            "definitions": [
                {"definition_id": definition_id, "metrics": asdict(metrics)}
                for definition_id in (
                    "upper-tail-1pct",
                    "upper-tail-0.5pct",
                    "upper-tail-2pct",
                )
            ],
            "packet_sha256": packet_sha256,
            "record_type": "m4-pilot-final-definition-metrics-v1",
            "run_id": trajectory.run_id,
            "task_id": task_id,
            "trajectory_sha256": trajectory.sha256,
        }
    )


def _published_evidence(
    root: Path,
) -> tuple[PilotRunEvidence, ReplayPublication, ReplayTrajectory, str]:
    packet, packet_sha256 = _packet()
    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=packet_sha256,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    prepared = preparation.runs[0]
    trajectory = _complete_m4_trajectory(prepared.run_spec, prepared.task_id)
    store = LocalArtifactStore(root)
    key_prefix = f"m4/issue-76/pilot/{packet_sha256}"
    definition_key = (
        f"{key_prefix}/runs/{trajectory.run_id}/attempts/attempt-001/"
        "pilot-final-definition-metrics.json"
    )
    definition_record = _definition_record(
        packet_sha256=packet_sha256,
        task_id=prepared.task_id,
        trajectory=trajectory,
    )
    publication = publish_replay_attempt(
        trajectory,
        store=store,
        attempt_id="attempt-001",
        started_at="2026-08-17T00:00:00Z",
        finished_at="2026-08-17T00:00:01Z",
        executed_git_commit=prepared.run_spec.git_commit,
        executed_environment_digest=prepared.run_spec.environment_digest,
        runtime_version="python-test",
        key_prefix=key_prefix,
        executor_type="compute_server",
        region=PILOT_EXECUTION_REGION,
        additional_outputs=((definition_key, definition_record),),
    )
    return (
        PilotRunEvidence(
            task_id=prepared.task_id,
            run_spec=prepared.run_spec,
            store=store,
            attempt_key=publication.attempt_key,
            attempt_sha256=publication.attempt_sha256,
            definition_metric_key=definition_key,
        ),
        publication,
        trajectory,
        packet_sha256,
    )


def _copy_evidence_store(
    source: LocalArtifactStore,
    target_root: Path,
    publication: ReplayPublication,
    *,
    omit: tuple[str, ...] = (),
    replacements: Mapping[str, bytes] | None = None,
) -> LocalArtifactStore:
    target = LocalArtifactStore(target_root)
    attempt = load_run_attempt_v1(source.get_bytes(publication.attempt_key))
    keys = (
        *(str(item["object_key"]) for item in attempt.value["outputs"]),
        publication.attempt_key,
        publication.marker_key,
    )
    replacements = {} if replacements is None else replacements
    for key in keys:
        if key not in omit:
            payload = replacements[key] if key in replacements else source.get_bytes(key)
            target.put_bytes(key, payload)
    return target


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


def test_execution_region_drift_fails_before_inputs_or_scratch(tmp_path: Path) -> None:
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
            authorization=replace(_authorization(digest), execution_region="ap-beijing"),
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
    assert result.definition_metric_keys[0] in result.publications[0].output_keys


def test_pilot_recall_requires_fresh_succeeded_attempt_output_and_chengdu_region(
    tmp_path: Path,
) -> None:
    evidence, publication, trajectory, packet_sha256 = _published_evidence(tmp_path / "complete")

    loaded = load_pilot_run_recall(
        evidence,
        expected_packet_sha256=packet_sha256,
        expected_execution_region=PILOT_EXECUTION_REGION,
    )
    attempt = load_run_attempt_v1(evidence.store.get_bytes(publication.attempt_key))

    assert loaded.task_id == evidence.task_id
    assert loaded.trajectory_sha256 == trajectory.sha256
    assert dict(loaded.recalls) == {
        "upper-tail-1pct": 0.5,
        "upper-tail-0.5pct": 0.5,
        "upper-tail-2pct": 0.5,
    }
    assert attempt.value["compute"]["region"] == "ap-chengdu"
    assert evidence.definition_metric_key in {
        str(item["object_key"]) for item in attempt.value["outputs"]
    }


def test_pilot_recall_rejects_incomplete_failed_or_drifted_evidence_chain(
    tmp_path: Path,
) -> None:
    evidence, publication, _trajectory, packet_sha256 = _published_evidence(tmp_path / "source")
    source = evidence.store
    assert isinstance(source, LocalArtifactStore)
    attempt = load_run_attempt_v1(source.get_bytes(publication.attempt_key))
    trajectory_key = next(
        str(item["object_key"])
        for item in attempt.value["outputs"]
        if str(item["object_key"]).endswith("/trajectory.json")
    )

    sidecar_only = LocalArtifactStore(tmp_path / "sidecar-only")
    sidecar_only.put_bytes(
        evidence.definition_metric_key,
        source.get_bytes(evidence.definition_metric_key),
    )
    with pytest.raises(ContractValidationError, match="fresh evidence"):
        load_pilot_run_recall(
            replace(evidence, store=sidecar_only),
            expected_packet_sha256=packet_sha256,
            expected_execution_region=PILOT_EXECUTION_REGION,
        )

    missing_marker = _copy_evidence_store(
        source,
        tmp_path / "missing-marker",
        publication,
        omit=(publication.marker_key,),
    )
    with pytest.raises(ContractValidationError, match="eligible"):
        load_pilot_run_recall(
            replace(evidence, store=missing_marker),
            expected_packet_sha256=packet_sha256,
            expected_execution_region=PILOT_EXECUTION_REGION,
        )

    failed_value = json.loads(source.get_bytes(publication.attempt_key))
    failed_value["status"] = "failed"
    failed_value.pop("completion_marker")
    failed_value["failure"] = {
        "code": "publication-failed",
        "message": "synthetic failure",
        "retryable": False,
    }
    failed_bytes = canonical_json_bytes(failed_value)
    failed_store = _copy_evidence_store(
        source,
        tmp_path / "failed-attempt",
        publication,
        omit=(publication.marker_key,),
        replacements={publication.attempt_key: failed_bytes},
    )
    with pytest.raises(ContractValidationError, match="succeeded"):
        load_pilot_run_recall(
            replace(
                evidence,
                store=failed_store,
                attempt_sha256=hashlib.sha256(failed_bytes).hexdigest(),
            ),
            expected_packet_sha256=packet_sha256,
            expected_execution_region=PILOT_EXECUTION_REGION,
        )

    trajectory_drift = _copy_evidence_store(
        source,
        tmp_path / "trajectory-drift",
        publication,
        replacements={trajectory_key: source.get_bytes(trajectory_key) + b"\n"},
    )
    with pytest.raises(ContractValidationError, match="eligible"):
        load_pilot_run_recall(
            replace(evidence, store=trajectory_drift),
            expected_packet_sha256=packet_sha256,
            expected_execution_region=PILOT_EXECUTION_REGION,
        )

    sidecar_drift = _copy_evidence_store(
        source,
        tmp_path / "sidecar-drift",
        publication,
        replacements={
            evidence.definition_metric_key: source.get_bytes(evidence.definition_metric_key) + b"\n"
        },
    )
    with pytest.raises(ContractValidationError, match="eligible"):
        load_pilot_run_recall(
            replace(evidence, store=sidecar_drift),
            expected_packet_sha256=packet_sha256,
            expected_execution_region=PILOT_EXECUTION_REGION,
        )

    with pytest.raises(ContractValidationError, match="region"):
        load_pilot_run_recall(
            evidence,
            expected_packet_sha256=packet_sha256,
            expected_execution_region="ap-beijing",
        )


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
