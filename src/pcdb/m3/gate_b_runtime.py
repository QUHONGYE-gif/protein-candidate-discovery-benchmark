"""Fail-closed staging and two-capability publication for Issue #68 Gate B."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pcdb.contracts.experiment import ContractValidationError
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.replay.engine import load_replay_trajectory


class GateBObjectClient(Protocol):
    """Writer/reader object surface; role separation is enforced by the entrypoint."""

    def head(self, bucket: str, key: str) -> object: ...

    def get(self, bucket: str, key: str) -> bytes: ...

    def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None: ...

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ActualObject:
    """One locally validated runtime-generated object frozen before first PUT."""

    kind: str
    object_key: str
    payload: bytes
    run_id: str
    sha256: str
    size_bytes: int


def _expected(packet: Mapping[str, object]) -> list[Mapping[str, object]]:
    inventory = packet.get("expected_inventory")
    caps = packet.get("execution_caps")
    if (
        not isinstance(inventory, list)
        or not isinstance(caps, Mapping)
        or len(inventory) != caps.get("cos_object_count")
    ):
        raise ContractValidationError("Gate B expected inventory cardinality drifted")
    if any(not isinstance(item, Mapping) for item in inventory):
        raise ContractValidationError("Gate B expected inventory member is invalid")
    return inventory


def validate_actual_inventory(
    packet: Mapping[str, object],
    payloads: Mapping[str, bytes],
    *,
    scope: str = "sentinel",
    executed_git_commit: str | None = None,
    executed_environment_digest: str | None = None,
) -> tuple[ActualObject, ...]:
    """Freeze exact keys, sizes and SHA-256 only after all payloads exist locally."""

    all_expected = _expected(packet)
    sentinel = packet.get("sentinel")
    if not isinstance(sentinel, Mapping) or not isinstance(sentinel.get("run_id"), str):
        raise ContractValidationError("Gate B sentinel identity is invalid")
    if scope == "sentinel":
        expected = [item for item in all_expected if item["run_id"] == sentinel["run_id"]]
    elif scope == "remaining-matrix":
        expected = [item for item in all_expected if item["run_id"] != sentinel["run_id"]]
    else:
        raise ContractValidationError("Gate B actual inventory scope is invalid")
    if len(expected) != (9 if scope == "sentinel" else 153):
        raise ContractValidationError("Gate B actual inventory scope cardinality drifted")
    expected_keys = [str(item["object_key"]) for item in expected]
    if len(expected_keys) != len(set(expected_keys)) or set(payloads) != set(expected_keys):
        raise ContractValidationError(
            "Gate B actual inventory does not exact-cover expected schema"
        )
    caps = packet.get("execution_caps")
    if not isinstance(caps, Mapping):
        raise ContractValidationError("Gate B execution caps are invalid")
    total = 0
    actual: list[ActualObject] = []
    for item in expected:
        key = str(item["object_key"])
        payload = payloads[key]
        if not isinstance(payload, bytes):
            raise ContractValidationError("Gate B actual inventory payload must be bytes")
        kind = str(item["kind"])
        if (kind == "success-marker") != (payload == b""):
            raise ContractValidationError("Gate B success marker bytes drifted")
        total += len(payload)
        actual.append(
            ActualObject(
                kind=kind,
                object_key=key,
                payload=payload,
                run_id=str(item["run_id"]),
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
    if total > int(caps["cos_net_new_bytes"]) or len(actual) > int(caps["cos_object_count"]):
        raise ContractValidationError("Gate B actual inventory exceeds a reviewed cap")
    _validate_cross_links(
        tuple(actual),
        executed_git_commit=executed_git_commit,
        executed_environment_digest=executed_environment_digest,
    )
    return tuple(actual)


def _validate_cross_links(
    actual: tuple[ActualObject, ...],
    *,
    executed_git_commit: str | None,
    executed_environment_digest: str | None,
) -> None:
    by_run: dict[str, dict[str, ActualObject]] = {}
    for item in actual:
        by_run.setdefault(item.run_id, {})[item.object_key] = item
    for run_id, members in by_run.items():
        trajectories = [item for item in members.values() if item.kind == "trajectory"]
        attempts = [item for item in members.values() if item.kind == "run-attempt"]
        markers = [item for item in members.values() if item.kind == "success-marker"]
        if len(trajectories) != 1 or len(attempts) != 1 or len(markers) != 1:
            raise ContractValidationError("Gate B actual inventory terminal artifacts drifted")
        trajectory_item = trajectories[0]
        trajectory = load_replay_trajectory(trajectory_item.payload)
        if trajectory.run_id != run_id or len(trajectory.events) != 3:
            raise ContractValidationError("Gate B actual trajectory identity drifted")
        base = trajectory_item.object_key.removesuffix("/trajectory.json")
        expected_outputs = {trajectory_item.object_key: trajectory_item}
        for round_index, event in enumerate(trajectory.events):
            checkpoint_key = f"{base}/checkpoints/round-{round_index:03d}.json"
            metric_key = f"{base}/metrics/round-{round_index:03d}.json"
            checkpoint = members.get(checkpoint_key)
            metric = members.get(metric_key)
            if (
                checkpoint is None
                or metric is None
                or checkpoint.sha256 != event.checkpoint_sha256
                or metric.sha256 != event.metric_sha256
                or checkpoint.payload != trajectory.checkpoint_bytes(round_index)
                or metric.payload != trajectory.metric_bytes(round_index)
            ):
                raise ContractValidationError("Gate B actual trajectory cross-link drifted")
            expected_outputs[checkpoint_key] = checkpoint
            expected_outputs[metric_key] = metric
        attempt = load_run_attempt_v1(attempts[0].payload)
        output_refs = {
            str(item["object_key"]): (str(item["sha256"]), int(item["size_bytes"]))
            for item in attempt.value["outputs"]
        }
        expected_refs = {
            key: (item.sha256, item.size_bytes) for key, item in expected_outputs.items()
        }
        marker = attempt.value["completion_marker"]
        if (
            attempt.value["status"] != "succeeded"
            or attempt.value["run_id"] != run_id
            or (
                executed_git_commit is not None
                and attempt.value["executed_git_commit"] != executed_git_commit
            )
            or (
                executed_environment_digest is not None
                and attempt.value["executed_environment_digest"] != executed_environment_digest
            )
            or output_refs != expected_refs
            or marker["object_key"] != markers[0].object_key
            or markers[0].payload != b""
        ):
            raise ContractValidationError("Gate B actual RunAttempt cross-link drifted")


def _fresh(client: GateBObjectClient, bucket: str, item: ActualObject) -> None:
    metadata = client.head(bucket, item.object_key)
    if getattr(metadata, "size_bytes", None) != item.size_bytes:
        raise ContractValidationError("Gate B fresh HEAD size drifted")
    payload = client.get(bucket, item.object_key)
    if len(payload) != item.size_bytes or hashlib.sha256(payload).hexdigest() != item.sha256:
        raise ContractValidationError("Gate B fresh readback identity drifted")


def _sentinel_items(
    packet: Mapping[str, object], actual: tuple[ActualObject, ...]
) -> tuple[ActualObject, ...]:
    sentinel = packet.get("sentinel")
    if not isinstance(sentinel, Mapping) or not isinstance(sentinel.get("run_id"), str):
        raise ContractValidationError("Gate B sentinel identity is invalid")
    items = tuple(item for item in actual if item.run_id == sentinel["run_id"])
    if (
        len(items) != 9
        or items[-1].kind != "success-marker"
        or any(item.kind == "success-marker" for item in items[:-1])
    ):
        raise ContractValidationError("Gate B sentinel inventory or publication order drifted")
    return items


def publish_staged_sentinel(
    packet: Mapping[str, object],
    actual: tuple[ActualObject, ...],
    writer: GateBObjectClient,
) -> tuple[str, ...]:
    """Publish only the lowest-cost sentinel, verifying every PUT immediately."""

    store = packet.get("evidence_store")
    if not isinstance(store, Mapping):
        raise ContractValidationError("Gate B evidence store is invalid")
    bucket, prefix = str(store["bucket"]), str(store["prefix"])
    if writer.list_keys(bucket, prefix):
        raise ContractValidationError("Gate B evidence prefix must start empty")
    items = _sentinel_items(packet, actual)
    for item in items:
        writer.put_forbid_overwrite(bucket, item.object_key, item.payload)
        _fresh(writer, bucket, item)
    return tuple(item.object_key for item in items)


def independent_read_sentinel(
    packet: Mapping[str, object],
    reader: GateBObjectClient,
    actual: tuple[ActualObject, ...],
) -> None:
    """Exact-cover one sentinel using a capability-separated read-only identity."""

    store = packet.get("evidence_store")
    if not isinstance(store, Mapping):
        raise ContractValidationError("Gate B evidence store is invalid")
    bucket, prefix = str(store["bucket"]), str(store["prefix"])
    items = _sentinel_items(packet, actual)
    expected = tuple(item.object_key for item in items)
    observed = reader.list_keys(bucket, prefix)
    if len(observed) != len(set(observed)) or set(observed) != set(expected):
        raise ContractValidationError("Gate B independent reader exact-cover drifted")
    for item in items:
        _fresh(reader, bucket, item)


__all__ = [
    "ActualObject",
    "GateBObjectClient",
    "independent_read_sentinel",
    "publish_staged_sentinel",
    "validate_actual_inventory",
]
