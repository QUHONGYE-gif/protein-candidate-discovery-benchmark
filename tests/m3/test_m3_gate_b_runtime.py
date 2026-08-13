"""Two-capability M3 Gate B publication checks for Issue #68."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pcdb.cli import build_parser
from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.run import RunSpec
from pcdb.evaluation import MetricValue, SequentialMetricSet
from pcdb.m3.gate_a_runtime import RunnableReplay
from pcdb.m3.gate_b_runtime import (
    GATE_B_APPROVAL,
    FullMatrixReadback,
    GateBAuthorization,
    GateBResourceSequence,
    SentinelReadback,
    execute_formal_scope,
    freeze_actual_inventory,
    independent_read_full_matrix,
    independent_read_sentinel,
    load_actual_inventory,
    load_sentinel_readback,
    publish_staged_remaining,
    publish_staged_sentinel,
    validate_actual_inventory,
    validate_remote_phase_start,
    write_final_readback,
    write_sentinel_readback,
)
from pcdb.replay.engine import ReplayEvent, ReplayTrajectory
from pcdb.replay.oracle import Observation
from pcdb.replay.publication import publish_replay_attempt
from pcdb.storage.local import LocalArtifactStore


def _trajectory() -> ReplayTrajectory:
    run_spec = RunSpec(
        schema_version="1",
        protocol_version="m3-single-fidelity-replay-v1",
        git_commit="f" * 40,
        environment_digest="a" * 64,
        dataset_manifest_sha256="b" * 64,
        processing_manifest_sha256="c" * 64,
        split_sha256="d" * 64,
        feature_sha256="e" * 64,
        experiment_config_sha256="1" * 64,
        model_id="ridge-sklearn-v1",
        model_config_sha256="2" * 64,
        acquisition_id="random-seeded-sha256-v1",
        acquisition_config_sha256="3" * 64,
        seed=1729,
        initial_sample_size=2,
        batch_size=2,
        budget=6,
    )
    metrics = SequentialMetricSet(
        best_found_value=MetricValue.defined(1.0),
        simple_regret=MetricValue.defined(0.0),
        high_value_discovery_recall=MetricValue.defined(1.0),
        success_at_budget=MetricValue.defined(True),
    )
    metric_sha256 = hashlib.sha256(canonical_json_bytes(asdict(metrics))).hexdigest()
    events: list[ReplayEvent] = []
    for round_index in range(3):
        candidates = tuple(f"{round_index * 2 + offset + 1:064x}" for offset in range(2))
        provisional = ReplayEvent(
            round=round_index,
            selected_candidate_ids=candidates,
            observations=tuple(
                Observation(candidate_id=candidate, label=float(round_index + offset))
                for offset, candidate in enumerate(candidates)
            ),
            distinct_revealed=(round_index + 1) * 2,
            checkpoint_sha256="0" * 64,
            metrics=metrics,
            metric_sha256=metric_sha256,
        )
        prefix = ReplayTrajectory(
            run_spec=run_spec,
            task_id="synthetic",
            events=tuple([*events, provisional]),
        )
        events.append(
            ReplayEvent(
                round=provisional.round,
                selected_candidate_ids=provisional.selected_candidate_ids,
                observations=provisional.observations,
                distinct_revealed=provisional.distinct_revealed,
                checkpoint_sha256=hashlib.sha256(prefix.checkpoint_bytes(round_index)).hexdigest(),
                metrics=metrics,
                metric_sha256=metric_sha256,
            )
        )
    return ReplayTrajectory(run_spec=run_spec, task_id="synthetic", events=tuple(events))


def _packet(trajectory: ReplayTrajectory) -> dict[str, Any]:
    prefix = "m3/issue-68/single-fidelity-v1/" + "f" * 40 + "/"
    inventory = []
    run_id = trajectory.run_id
    base = f"{prefix}runs/{run_id}/attempts/attempt-001"
    for index in range(3):
        inventory.append(
            {
                "kind": "checkpoint",
                "object_key": f"{base}/checkpoints/round-{index:03d}.json",
                "run_id": run_id,
            }
        )
    for index in range(3):
        inventory.append(
            {
                "kind": "metric",
                "object_key": f"{base}/metrics/round-{index:03d}.json",
                "run_id": run_id,
            }
        )
    inventory.extend(
        [
            {"kind": "trajectory", "object_key": f"{base}/trajectory.json", "run_id": run_id},
            {"kind": "run-attempt", "object_key": f"{base}/run-attempt.json", "run_id": run_id},
            {"kind": "success-marker", "object_key": f"{base}/_SUCCESS", "run_id": run_id},
        ]
    )
    return {
        "authorization_boundaries": {
            "gate_b_execution_authorized": False,
            "gate_b_execution_eligible": True,
            "required_approval_text": GATE_B_APPROVAL,
        },
        "evidence_store": {"bucket": "bucket", "prefix": prefix},
        "execution_caps": {"cos_net_new_bytes": 2 * 1024**3, "cos_object_count": 9},
        "expected_inventory": inventory,
        "runtime_identity": {
            "executed_environment_digest": trajectory.run_spec.environment_digest,
            "executed_git_commit": trajectory.run_spec.git_commit,
        },
        "sentinel": {"run_id": run_id},
    }


def _full_packet(trajectory: ReplayTrajectory) -> dict[str, Any]:
    packet = _packet(trajectory)
    sentinel_inventory = list(packet["expected_inventory"])
    expected = list(sentinel_inventory)
    for run_index in range(1, 18):
        run_id = f"{run_index:064x}"
        base = f"{packet['evidence_store']['prefix']}runs/{run_id}/attempts/attempt-001"
        for round_index in range(3):
            expected.append(
                {
                    "kind": "checkpoint",
                    "object_key": f"{base}/checkpoints/round-{round_index:03d}.json",
                    "run_id": run_id,
                }
            )
        for round_index in range(3):
            expected.append(
                {
                    "kind": "metric",
                    "object_key": f"{base}/metrics/round-{round_index:03d}.json",
                    "run_id": run_id,
                }
            )
        expected.extend(
            [
                {"kind": "trajectory", "object_key": f"{base}/trajectory.json", "run_id": run_id},
                {"kind": "run-attempt", "object_key": f"{base}/run-attempt.json", "run_id": run_id},
                {"kind": "success-marker", "object_key": f"{base}/_SUCCESS", "run_id": run_id},
            ]
        )
    packet["expected_inventory"] = expected
    packet["execution_caps"]["cos_object_count"] = 162
    return packet


class Objects:
    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}
        self.writes: list[str] = []

    def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None:
        identity = (bucket, key)
        if identity in self.payloads:
            raise FileExistsError(key)
        self.payloads[identity] = payload
        self.writes.append(key)

    def get(self, bucket: str, key: str) -> bytes:
        return self.payloads[(bucket, key)]

    def head(self, bucket: str, key: str) -> object:
        return type("Metadata", (), {"size_bytes": len(self.payloads[(bucket, key)])})()

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]:
        return tuple(
            key
            for stored_bucket, key in self.payloads
            if stored_bucket == bucket and key.startswith(prefix)
        )

    @property
    def transferred_bytes(self) -> int:
        return 0


def _authorization(packet: dict[str, Any]) -> GateBAuthorization:
    return GateBAuthorization(
        approval_comment_id=1,
        approval_text=str(packet["authorization_boundaries"]["required_approval_text"]),
        packet_sha256=hashlib.sha256(canonical_json_bytes(packet)).hexdigest(),
    )


def _within_caps() -> None:
    return None


def _payloads(
    tmp_path: Path,
    packet: dict[str, Any],
    trajectory: ReplayTrajectory,
    *,
    executed_git_commit: str | None = None,
    executed_environment_digest: str | None = None,
) -> dict[str, bytes]:
    store = LocalArtifactStore(tmp_path / "staged")
    prefix = str(packet["evidence_store"]["prefix"])
    publish_replay_attempt(
        trajectory,
        store=store,
        attempt_id="attempt-001",
        started_at="2026-08-13T00:00:00Z",
        finished_at="2026-08-13T00:00:01Z",
        executed_git_commit=executed_git_commit or trajectory.run_spec.git_commit,
        executed_environment_digest=(
            executed_environment_digest or trajectory.run_spec.environment_digest
        ),
        runtime_version="python-test",
        key_prefix=prefix,
    )
    return {
        item["object_key"]: store.get_bytes(item["object_key"])
        for item in packet["expected_inventory"]
    }


def test_actual_inventory_must_exact_cover_before_first_put(tmp_path: Path) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    payloads = _payloads(tmp_path, packet, trajectory)
    payloads.pop(next(iter(payloads)))
    objects = Objects()

    with pytest.raises(ContractValidationError, match="actual inventory"):
        validate_actual_inventory(packet, payloads)

    assert objects.writes == []


def test_sentinel_actual_inventory_does_not_require_other_seventeen_runs(tmp_path: Path) -> None:
    trajectory = _trajectory()
    packet = _full_packet(trajectory)
    payloads = _payloads(tmp_path, _packet(trajectory), trajectory)

    actual = validate_actual_inventory(packet, payloads, scope="sentinel")

    assert len(actual) == 9
    assert {item.run_id for item in actual} == {trajectory.run_id}


def test_writer_publishes_only_sentinel_with_success_last_and_fresh_readback(
    tmp_path: Path,
) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    payloads = _payloads(tmp_path, packet, trajectory)
    actual = validate_actual_inventory(packet, payloads)
    objects = Objects()

    published = publish_staged_sentinel(
        packet, actual, objects, _authorization(packet), resource_guard=_within_caps
    )

    sentinel = packet["sentinel"]["run_id"]
    expected = [
        item["object_key"] for item in packet["expected_inventory"] if item["run_id"] == sentinel
    ]
    assert list(published) == expected
    assert objects.writes == expected
    assert objects.writes[-1].endswith("/_SUCCESS")
    independent_read_sentinel(
        packet, objects, actual, _authorization(packet), resource_guard=_within_caps
    )

    extra = packet["evidence_store"]["prefix"] + "extra.json"
    objects.payloads[("bucket", extra)] = b"{}"
    with pytest.raises(ContractValidationError, match="exact-cover"):
        independent_read_sentinel(
            packet, objects, actual, _authorization(packet), resource_guard=_within_caps
        )


def test_writer_rejects_unreviewed_actual_before_first_put(tmp_path: Path) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    payloads = _payloads(tmp_path, packet, trajectory)
    actual = validate_actual_inventory(packet, payloads)
    drifted = (
        replace(
            actual[0],
            object_key=str(packet["evidence_store"]["prefix"]) + "unexpected.json",
        ),
        *actual[1:],
    )
    objects = Objects()

    with pytest.raises(ContractValidationError, match="exact-cover"):
        publish_staged_sentinel(
            packet, drifted, objects, _authorization(packet), resource_guard=_within_caps
        )

    assert objects.writes == []


def test_writer_requires_exact_external_gate_b_authorization_before_first_put(
    tmp_path: Path,
) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    actual = validate_actual_inventory(packet, _payloads(tmp_path, packet, trajectory))
    objects = Objects()
    authorization = replace(_authorization(packet), approval_text="NOT APPROVED")

    with pytest.raises(ContractValidationError, match="authorization"):
        publish_staged_sentinel(packet, actual, objects, authorization, resource_guard=_within_caps)

    assert objects.writes == []


def test_actual_inventory_rejects_cross_link_drift_before_first_put(tmp_path: Path) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    payloads = _payloads(tmp_path, packet, trajectory)
    metric_key = next(
        item["object_key"] for item in packet["expected_inventory"] if item["kind"] == "metric"
    )
    payloads[metric_key] = canonical_json_bytes({"drifted": True})

    with pytest.raises(ContractValidationError, match="cross-link"):
        validate_actual_inventory(packet, payloads)


def test_actual_inventory_binds_attempt_to_actual_runtime_not_runspec_source(
    tmp_path: Path,
) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    packet["runtime_identity"] = {
        "executed_environment_digest": "5" * 64,
        "executed_git_commit": "4" * 40,
    }
    payloads = _payloads(
        tmp_path,
        packet,
        trajectory,
        executed_git_commit="4" * 40,
        executed_environment_digest="5" * 64,
    )

    actual = validate_actual_inventory(packet, payloads)
    assert len(actual) == 9
    packet["runtime_identity"] = {
        "executed_environment_digest": trajectory.run_spec.environment_digest,
        "executed_git_commit": trajectory.run_spec.git_commit,
    }
    with pytest.raises(ContractValidationError, match="RunAttempt cross-link"):
        validate_actual_inventory(packet, payloads)


def test_actual_inventory_requires_packet_runtime_identity(tmp_path: Path) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    payloads = _payloads(tmp_path, packet, trajectory)
    packet.pop("runtime_identity")

    with pytest.raises(ContractValidationError, match="runtime identity"):
        validate_actual_inventory(packet, payloads)


def test_remaining_inventory_uses_cumulative_gate_b_caps(tmp_path: Path) -> None:
    trajectory = _trajectory()
    packet = _full_packet(trajectory)
    payloads = _payloads(tmp_path, _packet(trajectory), trajectory)
    sentinel = validate_actual_inventory(packet, payloads)
    sentinel_bytes = sum(item.size_bytes for item in sentinel)
    packet["execution_caps"]["cos_net_new_bytes"] = sentinel_bytes + 7
    remaining_payloads = {
        item["object_key"]: b"" if item["kind"] == "success-marker" else b"x"
        for item in packet["expected_inventory"]
        if item["run_id"] != trajectory.run_id
    }

    with pytest.raises(ContractValidationError, match="reviewed cap"):
        validate_actual_inventory(
            packet,
            remaining_payloads,
            scope="remaining-matrix",
            prior_actual=sentinel,
        )


def test_remaining_publication_requires_exact_independent_sentinel_readback(
    tmp_path: Path,
) -> None:
    trajectories = tuple(_trajectory_for_index(index) for index in range(18))
    packet = _matrix_packet(trajectories)
    payloads = _matrix_payloads(tmp_path, packet, trajectories)
    sentinel_run_id = str(packet["sentinel"]["run_id"])
    sentinel_payloads = {
        key: payload for key, payload in payloads.items() if f"/runs/{sentinel_run_id}/" in key
    }
    remaining_payloads = {
        key: payload for key, payload in payloads.items() if key not in sentinel_payloads
    }
    sentinel = validate_actual_inventory(packet, sentinel_payloads, scope="sentinel")
    remaining = validate_actual_inventory(
        packet,
        remaining_payloads,
        scope="remaining-matrix",
        prior_actual=sentinel,
    )
    objects = Objects()
    authorization = _authorization(packet)
    publish_staged_sentinel(packet, sentinel, objects, authorization, resource_guard=_within_caps)

    with pytest.raises(ContractValidationError, match="sentinel readback"):
        publish_staged_remaining(
            packet,
            sentinel,
            remaining,
            objects,
            authorization,
            SentinelReadback(
                packet_sha256="0" * 64,
                sentinel_inventory_sha256="0" * 64,
            ),
            resource_guard=_within_caps,
        )

    assert len(objects.writes) == 9
    readback = independent_read_sentinel(
        packet, objects, sentinel, authorization, resource_guard=_within_caps
    )
    published = publish_staged_remaining(
        packet,
        sentinel,
        remaining,
        objects,
        authorization,
        readback,
        resource_guard=_within_caps,
    )

    assert len(published) == 153
    assert len(objects.writes) == 162
    for run_id in {item.run_id for item in remaining}:
        run_writes = [key for key in published if f"/runs/{run_id}/" in key]
        assert len(run_writes) == 9
        assert run_writes[-1].endswith("/_SUCCESS")
    independent_read_full_matrix(
        packet,
        objects,
        (*sentinel, *remaining),
        authorization,
        resource_guard=_within_caps,
    )


def test_full_reader_rejects_missing_or_extra_object(tmp_path: Path) -> None:
    trajectories = tuple(_trajectory_for_index(index) for index in range(18))
    packet = _matrix_packet(trajectories)
    payloads = _matrix_payloads(tmp_path, packet, trajectories)
    sentinel_run_id = str(packet["sentinel"]["run_id"])
    sentinel = validate_actual_inventory(
        packet,
        {key: payload for key, payload in payloads.items() if f"/runs/{sentinel_run_id}/" in key},
    )
    remaining = validate_actual_inventory(
        packet,
        {
            key: payload
            for key, payload in payloads.items()
            if f"/runs/{sentinel_run_id}/" not in key
        },
        scope="remaining-matrix",
        prior_actual=sentinel,
    )
    objects = Objects()
    authorization = _authorization(packet)
    publish_staged_sentinel(packet, sentinel, objects, authorization, resource_guard=_within_caps)
    readback = independent_read_sentinel(
        packet, objects, sentinel, authorization, resource_guard=_within_caps
    )
    publish_staged_remaining(
        packet,
        sentinel,
        remaining,
        objects,
        authorization,
        readback,
        resource_guard=_within_caps,
    )
    missing_key = remaining[0].object_key
    del objects.payloads[("bucket", missing_key)]

    with pytest.raises(ContractValidationError, match="exact-cover"):
        independent_read_full_matrix(
            packet,
            objects,
            (*sentinel, *remaining),
            authorization,
            resource_guard=_within_caps,
        )


def test_formal_staging_runs_sentinel_before_remaining_and_does_not_put(
    tmp_path: Path,
) -> None:
    trajectories = tuple(_trajectory_for_index(index) for index in range(18))
    packet = _matrix_packet(trajectories)
    inputs = _input_payloads(packet)
    objects = Objects()
    engines: list[str] = []

    class Engine:
        def __init__(self, trajectory: ReplayTrajectory) -> None:
            self.trajectory = trajectory

        def run(
            self,
            *,
            checkpoint: bytes | None = None,
            stop_after_round: int | None = None,
        ) -> ReplayTrajectory:
            return self.trajectory

    def engine_factory(run_spec: RunSpec, payloads: dict[str, bytes]) -> RunnableReplay:
        assert set(payloads) == {
            "candidate-feature-matrix",
            "dataset-manifest",
            "feature-descriptor",
            "processing-manifest",
            "split-manifest",
            "hidden-truth",
        }
        engines.append(run_spec.run_id)
        trajectory = next(item for item in trajectories if item.run_id == run_spec.run_id)
        return Engine(trajectory)

    sentinel_inputs = _Inputs(inputs)
    sentinel = execute_formal_scope(
        packet,
        input_client=sentinel_inputs,
        engine_factory=engine_factory,
        scratch_root=tmp_path / "sentinel",
        scope="sentinel",
        authorization=_authorization(packet),
        resource_guard=_within_caps,
    )

    assert len(sentinel) == 9
    assert engines == [str(packet["sentinel"]["run_id"])]
    assert [key for operation, key in sentinel_inputs.calls if operation == "get"] == [
        str(item["object_key"])
        for item in packet["input_inventory"]
        if item["task_id"] == trajectories[0].task_id
    ]
    assert sentinel_inputs.calls[-1][1].endswith("/hidden-truth.json")
    assert objects.writes == []
    publish_staged_sentinel(
        packet,
        sentinel,
        objects,
        _authorization(packet),
        resource_guard=_within_caps,
    )
    checkpoint = independent_read_sentinel(
        packet,
        objects,
        sentinel,
        _authorization(packet),
        resource_guard=_within_caps,
    )
    remaining = execute_formal_scope(
        packet,
        input_client=_Inputs(inputs),
        engine_factory=engine_factory,
        scratch_root=tmp_path / "remaining",
        scope="remaining-matrix",
        authorization=_authorization(packet),
        prior_actual=sentinel,
        sentinel_readback=checkpoint,
        resource_guard=_within_caps,
    )

    assert len(remaining) == 153
    assert len(engines) == 18
    assert set(engines) == {item.run_id for item in trajectories}
    assert objects.writes == [item.object_key for item in sentinel]


def test_each_remaining_run_attempt_records_its_own_start_time(tmp_path: Path) -> None:
    trajectories = tuple(_trajectory_for_index(index) for index in range(18))
    packet = _matrix_packet(trajectories)
    sentinel_run_id = str(packet["sentinel"]["run_id"])
    payloads = _matrix_payloads(tmp_path, packet, trajectories)
    sentinel = validate_actual_inventory(
        packet,
        {key: payload for key, payload in payloads.items() if f"/runs/{sentinel_run_id}/" in key},
    )
    checkpoint = SentinelReadback(
        packet_sha256=_authorization(packet).packet_sha256,
        sentinel_inventory_sha256=hashlib.sha256(
            canonical_json_bytes(
                [
                    {
                        "kind": item.kind,
                        "object_key": item.object_key,
                        "run_id": item.run_id,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                    }
                    for item in sentinel
                ]
            )
        ).hexdigest(),
    )
    times = iter(
        datetime(2026, 8, 13, tzinfo=UTC) + timedelta(seconds=index) for index in range(40)
    )

    class Engine:
        def __init__(self, trajectory: ReplayTrajectory) -> None:
            self.trajectory = trajectory

        def run(
            self,
            *,
            checkpoint: bytes | None = None,
            stop_after_round: int | None = None,
        ) -> ReplayTrajectory:
            return self.trajectory

    remaining = execute_formal_scope(
        packet,
        input_client=_Inputs(_input_payloads(packet)),
        engine_factory=lambda run_spec, _inputs: Engine(
            next(item for item in trajectories if item.run_id == run_spec.run_id)
        ),
        scratch_root=tmp_path / "timed-remaining",
        scope="remaining-matrix",
        authorization=_authorization(packet),
        prior_actual=sentinel,
        sentinel_readback=checkpoint,
        resource_guard=_within_caps,
        now=lambda: next(times),
    )
    starts = {
        __import__("json").loads(item.payload)["started_at"]
        for item in remaining
        if item.kind == "run-attempt"
    }

    assert len(starts) == 17


def test_cli_exposes_one_four_phase_gate_b_entry() -> None:
    arguments = build_parser().parse_args(
        [
            "m3-gate-b",
            "--phase",
            "sentinel-write",
            "--packet",
            "/review/packet.json",
            "--packet-sha256",
            "a" * 64,
            "--approval",
            GATE_B_APPROVAL,
            "--approval-comment-id",
            "5270000000",
            "--hourly-cost-cny",
            "1.5",
            "--repository-root",
            "/review/source",
            "--scratch-root",
            "/scratch/issue-68",
        ]
    )

    assert arguments.command == "m3-gate-b"
    assert arguments.phase == "sentinel-write"
    assert arguments.approval_comment_id == 5270000000
    assert arguments.hourly_cost_cny == 1.5


def test_remote_prefix_is_checked_before_formal_generation(tmp_path: Path) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    actual = validate_actual_inventory(packet, _payloads(tmp_path, packet, trajectory))
    objects = Objects()
    objects.payloads[("bucket", str(packet["evidence_store"]["prefix"]) + "existing")] = b"x"

    with pytest.raises(ContractValidationError, match="must start empty"):
        validate_remote_phase_start(
            packet, objects, phase="sentinel-write", resource_guard=_within_caps
        )

    objects.payloads.clear()
    for item in actual:
        objects.payloads[("bucket", item.object_key)] = item.payload
    validate_remote_phase_start(
        packet,
        objects,
        phase="remaining-write",
        sentinel_actual=actual,
        resource_guard=_within_caps,
    )


def test_resource_sequence_carries_network_usage_across_all_phases(tmp_path: Path) -> None:
    packet = _matrix_packet(tuple(_trajectory_for_index(index) for index in range(18)))
    packet["execution_caps"]["max_network_transfer_bytes"] = 10
    authorization = _authorization(packet)
    started = datetime(2026, 8, 13, tzinfo=UTC)

    sentinel = GateBResourceSequence.start(
        tmp_path,
        packet,
        authorization,
        phase="sentinel-write",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )
    sentinel.complete(network_bytes=6)
    sentinel_reader = GateBResourceSequence.resume(
        tmp_path,
        packet,
        authorization,
        phase="sentinel-read",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )
    assert sentinel_reader.remaining_network_bytes == 4
    sentinel_reader.complete(network_bytes=0)
    remaining = GateBResourceSequence.resume(
        tmp_path,
        packet,
        authorization,
        phase="remaining-write",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )

    with pytest.raises(ContractValidationError, match="network"):
        remaining.guard(current_network_bytes=5)


def test_resource_sequence_start_is_exclusive_before_remote_work(tmp_path: Path) -> None:
    packet = _matrix_packet(tuple(_trajectory_for_index(index) for index in range(18)))
    authorization = _authorization(packet)
    started = datetime(2026, 8, 13, tzinfo=UTC)
    GateBResourceSequence.start(
        tmp_path,
        packet,
        authorization,
        phase="sentinel-write",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )

    assert (tmp_path / "resource-sequence-00-started.json").is_file()
    with pytest.raises(ContractValidationError, match="already exists"):
        GateBResourceSequence.start(
            tmp_path,
            packet,
            authorization,
            phase="sentinel-write",
            hourly_cost_cny=1.0,
            now=lambda: started + timedelta(hours=1),
        )


def test_resource_sequence_start_checkpoint_obeys_scratch_cap(tmp_path: Path) -> None:
    packet = _matrix_packet(tuple(_trajectory_for_index(index) for index in range(18)))
    packet["execution_caps"]["max_scratch_bytes"] = 1

    with pytest.raises(ContractValidationError, match="scratch"):
        GateBResourceSequence.start(
            tmp_path,
            packet,
            _authorization(packet),
            phase="sentinel-write",
            hourly_cost_cny=1.0,
            now=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )

    assert not tuple(tmp_path.iterdir())


def test_resource_sequence_cannot_restart_an_incomplete_later_phase(tmp_path: Path) -> None:
    packet = _matrix_packet(tuple(_trajectory_for_index(index) for index in range(18)))
    authorization = _authorization(packet)
    started = datetime(2026, 8, 13, tzinfo=UTC)
    sentinel = GateBResourceSequence.start(
        tmp_path,
        packet,
        authorization,
        phase="sentinel-write",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )
    sentinel.complete(network_bytes=0)
    GateBResourceSequence.resume(
        tmp_path,
        packet,
        authorization,
        phase="sentinel-read",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )

    with pytest.raises(ContractValidationError, match="already started"):
        GateBResourceSequence.resume(
            tmp_path,
            packet,
            authorization,
            phase="sentinel-read",
            hourly_cost_cny=1.0,
            now=lambda: started,
        )


def test_resource_sequence_carries_wall_and_scratch_across_phases(tmp_path: Path) -> None:
    packet = _matrix_packet(tuple(_trajectory_for_index(index) for index in range(18)))
    packet["execution_caps"]["max_wall_seconds"] = 10
    packet["execution_caps"]["max_scratch_bytes"] = 4096
    authorization = _authorization(packet)
    started = datetime(2026, 8, 13, tzinfo=UTC)
    clock = [started]
    sequence = GateBResourceSequence.start(
        tmp_path,
        packet,
        authorization,
        phase="sentinel-write",
        hourly_cost_cny=1.0,
        now=lambda: clock[0],
    )
    (tmp_path / "sentinel-stage").mkdir()
    (tmp_path / "sentinel-stage" / "payload").write_bytes(b"x" * 128)
    sequence.guard(current_network_bytes=0)
    sequence.complete(network_bytes=0)
    clock[0] = started + timedelta(seconds=11)
    with pytest.raises(ContractValidationError, match="wall"):
        GateBResourceSequence.resume(
            tmp_path,
            packet,
            authorization,
            phase="sentinel-read",
            hourly_cost_cny=1.0,
            now=lambda: clock[0],
        )

    scratch_packet = _matrix_packet(tuple(_trajectory_for_index(index) for index in range(18)))
    scratch_packet["execution_caps"]["max_scratch_bytes"] = 2048
    scratch_authorization = _authorization(scratch_packet)
    scratch_root = tmp_path / "scratch-cap"
    scratch_sequence = GateBResourceSequence.start(
        scratch_root,
        scratch_packet,
        scratch_authorization,
        phase="sentinel-write",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )
    (scratch_root / "sentinel-stage").mkdir()
    (scratch_root / "sentinel-stage" / "payload").write_bytes(b"x" * 128)
    scratch_sequence.complete(network_bytes=0)
    scratch_reader = GateBResourceSequence.resume(
        scratch_root,
        scratch_packet,
        scratch_authorization,
        phase="sentinel-read",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )
    scratch_reader.complete(network_bytes=0)
    scratch_remaining = GateBResourceSequence.resume(
        scratch_root,
        scratch_packet,
        scratch_authorization,
        phase="remaining-write",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )
    (scratch_root / "remaining-stage").mkdir()
    (scratch_root / "remaining-stage" / "payload").write_bytes(b"x" * 2048)
    with pytest.raises(ContractValidationError, match="scratch"):
        scratch_remaining.guard(current_network_bytes=0)


def test_resource_sequence_cost_cannot_reset_in_next_process(tmp_path: Path) -> None:
    packet = _matrix_packet(tuple(_trajectory_for_index(index) for index in range(18)))
    packet["execution_caps"]["max_cost_cny"] = 0.001
    authorization = _authorization(packet)
    started = datetime(2026, 8, 13, tzinfo=UTC)
    sequence = GateBResourceSequence.start(
        tmp_path,
        packet,
        authorization,
        phase="sentinel-write",
        hourly_cost_cny=1.0,
        now=lambda: started,
    )
    sequence.complete(network_bytes=0)

    with pytest.raises(ContractValidationError, match="cost"):
        GateBResourceSequence.resume(
            tmp_path,
            packet,
            authorization,
            phase="sentinel-read",
            hourly_cost_cny=1.0,
            now=lambda: started + timedelta(seconds=4),
        )


def test_wall_guard_stops_before_next_runspec_and_remote_operation(tmp_path: Path) -> None:
    trajectories = tuple(_trajectory_for_index(index) for index in range(18))
    packet = _matrix_packet(trajectories)
    inputs = _Inputs(_input_payloads(packet))
    sentinel_payloads = _matrix_payloads(tmp_path, packet, trajectories)
    sentinel_run_id = str(packet["sentinel"]["run_id"])
    sentinel = validate_actual_inventory(
        packet,
        {
            key: payload
            for key, payload in sentinel_payloads.items()
            if f"/runs/{sentinel_run_id}/" in key
        },
    )
    checkpoint = SentinelReadback(
        packet_sha256=_authorization(packet).packet_sha256,
        sentinel_inventory_sha256=hashlib.sha256(
            canonical_json_bytes(
                [
                    {
                        "kind": item.kind,
                        "object_key": item.object_key,
                        "run_id": item.run_id,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                    }
                    for item in sentinel
                ]
            )
        ).hexdigest(),
    )
    engine_calls: list[str] = []

    class Engine:
        def __init__(self, trajectory: ReplayTrajectory) -> None:
            self.trajectory = trajectory

        def run(
            self,
            *,
            checkpoint: bytes | None = None,
            stop_after_round: int | None = None,
        ) -> ReplayTrajectory:
            return self.trajectory

    def factory(run_spec: RunSpec, _payloads: dict[str, bytes]) -> RunnableReplay:
        engine_calls.append(run_spec.run_id)
        return Engine(next(item for item in trajectories if item.run_id == run_spec.run_id))

    def guard() -> None:
        if engine_calls:
            raise ContractValidationError("Gate B sequence wall cap exceeded")

    with pytest.raises(ContractValidationError, match="wall"):
        execute_formal_scope(
            packet,
            input_client=inputs,
            engine_factory=factory,
            scratch_root=tmp_path / "guarded-remaining",
            scope="remaining-matrix",
            authorization=_authorization(packet),
            prior_actual=sentinel,
            sentinel_readback=checkpoint,
            resource_guard=guard,
        )

    assert len(engine_calls) == 1

    trajectory = _trajectory()
    sentinel_packet = _packet(trajectory)
    sentinel_actual = validate_actual_inventory(
        sentinel_packet,
        _payloads(tmp_path / "publish", sentinel_packet, trajectory),
    )
    objects = Objects()
    guard_calls = 0

    def publication_guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if objects.writes:
            raise ContractValidationError("Gate B sequence wall cap exceeded")

    with pytest.raises(ContractValidationError, match="wall"):
        publish_staged_sentinel(
            sentinel_packet,
            sentinel_actual,
            objects,
            _authorization(sentinel_packet),
            resource_guard=publication_guard,
        )

    assert len(objects.writes) == 1
    assert guard_calls >= 2


def test_inventory_and_readback_checkpoints_are_exclusive_and_packet_bound(
    tmp_path: Path,
) -> None:
    trajectory = _trajectory()
    packet = _packet(trajectory)
    actual = validate_actual_inventory(packet, _payloads(tmp_path, packet, trajectory))
    authorization = _authorization(packet)
    inventory_path = tmp_path / "sentinel-actual-inventory.json"

    freeze_actual_inventory(inventory_path, packet, actual, authorization, scope="sentinel")
    loaded = load_actual_inventory(
        inventory_path,
        tmp_path / "staged",
        packet,
        authorization,
        scope="sentinel",
    )
    assert loaded == actual
    with pytest.raises(FileExistsError):
        freeze_actual_inventory(inventory_path, packet, actual, authorization, scope="sentinel")

    objects = Objects()
    publish_staged_sentinel(packet, actual, objects, authorization, resource_guard=_within_caps)
    readback = independent_read_sentinel(
        packet, objects, actual, authorization, resource_guard=_within_caps
    )
    readback_path = tmp_path / "sentinel-readback.json"
    write_sentinel_readback(readback_path, readback, authorization)
    assert load_sentinel_readback(readback_path, authorization) == readback

    with pytest.raises(ContractValidationError, match="full 162-object"):
        write_final_readback(
            tmp_path / "final-readback.json",
            packet,
            actual,
            authorization,
            FullMatrixReadback(
                packet_sha256=authorization.packet_sha256,
                exact_inventory_sha256="0" * 64,
            ),
        )


def _trajectory_for_index(index: int) -> ReplayTrajectory:
    base = _trajectory()
    run_spec = replace(base.run_spec, seed=1729 + index)
    task_id = f"synthetic-{index % 2}"
    events: list[ReplayEvent] = []
    for round_index, event in enumerate(base.events):
        provisional = replace(event, checkpoint_sha256="0" * 64)
        prefix = ReplayTrajectory(
            run_spec=run_spec,
            task_id=task_id,
            events=tuple([*events, provisional]),
        )
        events.append(
            replace(
                provisional,
                checkpoint_sha256=hashlib.sha256(prefix.checkpoint_bytes(round_index)).hexdigest(),
            )
        )
    return ReplayTrajectory(
        run_spec=run_spec,
        task_id=task_id,
        events=tuple(events),
    )


def _matrix_packet(trajectories: tuple[ReplayTrajectory, ...]) -> dict[str, Any]:
    packet = _packet(trajectories[0])
    expected: list[dict[str, Any]] = []
    prefix = str(packet["evidence_store"]["prefix"])
    for trajectory in trajectories:
        base = f"{prefix}runs/{trajectory.run_id}/attempts/attempt-001"
        expected.extend(
            {
                "kind": "checkpoint",
                "object_key": f"{base}/checkpoints/round-{round_index:03d}.json",
                "run_id": trajectory.run_id,
            }
            for round_index in range(3)
        )
        expected.extend(
            {
                "kind": "metric",
                "object_key": f"{base}/metrics/round-{round_index:03d}.json",
                "run_id": trajectory.run_id,
            }
            for round_index in range(3)
        )
        expected.extend(
            [
                {
                    "kind": "trajectory",
                    "object_key": f"{base}/trajectory.json",
                    "run_id": trajectory.run_id,
                },
                {
                    "kind": "run-attempt",
                    "object_key": f"{base}/run-attempt.json",
                    "run_id": trajectory.run_id,
                },
                {
                    "kind": "success-marker",
                    "object_key": f"{base}/_SUCCESS",
                    "run_id": trajectory.run_id,
                },
            ]
        )
    packet["expected_inventory"] = expected
    packet["execution_caps"]["cos_object_count"] = 162
    packet["execution_caps"].update(
        {
            "formal_attempts": 18,
            "max_network_transfer_bytes": 10 * 1024**3,
            "max_scratch_bytes": 20 * 1024**3,
            "max_wall_seconds": 6 * 60 * 60,
            "max_cost_cny": 50,
        }
    )
    packet["run_specs"] = [
        {
            "run_id": trajectory.run_id,
            "run_spec": asdict(trajectory.run_spec),
            "task_id": trajectory.task_id,
        }
        for trajectory in trajectories
    ]
    payloads = {
        (task_id, purpose): f"{task_id}:{purpose}".encode()
        for task_id in {trajectory.task_id for trajectory in trajectories}
        for purpose in (
            "candidate-feature-matrix",
            "dataset-manifest",
            "feature-descriptor",
            "processing-manifest",
            "split-manifest",
            "hidden-truth",
        )
    }
    packet["input_inventory"] = [
        {
            "bucket": "inputs",
            "object_key": f"{task_id}/{purpose}.json",
            "purpose": purpose,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "task_id": task_id,
            "truth_bearing": purpose == "hidden-truth",
        }
        for purpose in (
            "candidate-feature-matrix",
            "dataset-manifest",
            "feature-descriptor",
            "processing-manifest",
            "split-manifest",
        )
        for task_id in sorted({trajectory.task_id for trajectory in trajectories})
        for payload in (payloads[(task_id, purpose)],)
    ] + [
        {
            "bucket": "inputs",
            "object_key": f"{task_id}/hidden-truth.json",
            "purpose": "hidden-truth",
            "sha256": hashlib.sha256(payloads[(task_id, "hidden-truth")]).hexdigest(),
            "size_bytes": len(payloads[(task_id, "hidden-truth")]),
            "task_id": task_id,
            "truth_bearing": True,
        }
        for task_id in sorted({trajectory.task_id for trajectory in trajectories})
    ]
    return packet


def _input_payloads(packet: dict[str, Any]) -> dict[tuple[str, str], bytes]:
    return {
        (str(item["bucket"]), str(item["object_key"])): (
            f"{item['task_id']}:{item['purpose']}".encode()
        )
        for item in packet["input_inventory"]
    }


class _Inputs:
    def __init__(self, payloads: dict[tuple[str, str], bytes]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str]] = []

    @property
    def transferred_bytes(self) -> int:
        return 0

    def head(self, bucket: str, key: str) -> object:
        self.calls.append(("head", key))
        payload = self.payloads[(bucket, key)]
        return type(
            "Metadata",
            (),
            {
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        )()

    def get(self, bucket: str, key: str) -> bytes:
        self.calls.append(("get", key))
        return self.payloads[(bucket, key)]


def _matrix_payloads(
    tmp_path: Path,
    packet: dict[str, Any],
    trajectories: tuple[ReplayTrajectory, ...],
) -> dict[str, bytes]:
    store = LocalArtifactStore(tmp_path / "matrix-staged")
    prefix = str(packet["evidence_store"]["prefix"])
    for trajectory in trajectories:
        publish_replay_attempt(
            trajectory,
            store=store,
            attempt_id="attempt-001",
            started_at="2026-08-13T00:00:00Z",
            finished_at="2026-08-13T00:00:01Z",
            executed_git_commit=trajectory.run_spec.git_commit,
            executed_environment_digest=trajectory.run_spec.environment_digest,
            runtime_version="python-test",
            key_prefix=prefix,
        )
    return {
        str(item["object_key"]): store.get_bytes(str(item["object_key"]))
        for item in packet["expected_inventory"]
    }
