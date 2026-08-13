"""Two-capability M3 Gate B publication checks for Issue #68."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.run import RunSpec
from pcdb.evaluation import MetricValue, SequentialMetricSet
from pcdb.m3.gate_b_runtime import (
    GATE_B_APPROVAL,
    GateBAuthorization,
    independent_read_sentinel,
    publish_staged_sentinel,
    validate_actual_inventory,
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


def _authorization(packet: dict[str, Any]) -> GateBAuthorization:
    return GateBAuthorization(
        approval_comment_id=1,
        approval_text=str(packet["authorization_boundaries"]["required_approval_text"]),
        packet_sha256=hashlib.sha256(canonical_json_bytes(packet)).hexdigest(),
    )


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

    published = publish_staged_sentinel(packet, actual, objects, _authorization(packet))

    sentinel = packet["sentinel"]["run_id"]
    expected = [
        item["object_key"] for item in packet["expected_inventory"] if item["run_id"] == sentinel
    ]
    assert list(published) == expected
    assert objects.writes == expected
    assert objects.writes[-1].endswith("/_SUCCESS")
    independent_read_sentinel(packet, objects, actual, _authorization(packet))

    extra = packet["evidence_store"]["prefix"] + "extra.json"
    objects.payloads[("bucket", extra)] = b"{}"
    with pytest.raises(ContractValidationError, match="exact-cover"):
        independent_read_sentinel(packet, objects, actual, _authorization(packet))


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
        publish_staged_sentinel(packet, drifted, objects, _authorization(packet))

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
        publish_staged_sentinel(packet, actual, objects, authorization)

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
