"""Fail-closed staging and two-capability publication for Issue #68 Gate B."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.replay.engine import load_replay_trajectory

GATE_B_APPROVAL = "APPROVED FOR M3 ALPHA + IRED GATE B FORMAL EXECUTION AND PUBLICATION"


class GateBObjectClient(Protocol):
    """Writer/reader object surface; role separation is enforced by the entrypoint."""

    def head(self, bucket: str, key: str) -> object: ...

    def get(self, bucket: str, key: str) -> bytes: ...

    def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None: ...

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class GateBAuthorization:
    """External one-Gate-B approval bound to exact packet bytes."""

    approval_comment_id: int
    approval_text: str
    packet_sha256: str


@dataclass(frozen=True, slots=True)
class ActualObject:
    """One locally validated runtime-generated object frozen before first PUT."""

    kind: str
    object_key: str
    payload: bytes
    run_id: str
    sha256: str
    size_bytes: int


def _validate_authorization(
    packet: Mapping[str, object], authorization: GateBAuthorization
) -> None:
    boundaries = packet.get("authorization_boundaries")
    if not isinstance(boundaries, Mapping):
        raise ContractValidationError("Gate B authorization boundary is invalid")
    required_text = boundaries.get("required_approval_text")
    if (
        isinstance(authorization.approval_comment_id, bool)
        or authorization.approval_comment_id <= 0
        or boundaries.get("gate_b_execution_eligible") is not True
        or not isinstance(required_text, str)
        or required_text != GATE_B_APPROVAL
        or authorization.approval_text != required_text
        or authorization.packet_sha256
        != hashlib.sha256(canonical_json_bytes(dict(packet))).hexdigest()
    ):
        raise ContractValidationError("Gate B external authorization is absent or drifted")


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
    prior_actual: tuple[ActualObject, ...] = (),
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
    runtime_identity = packet.get("runtime_identity")
    if not isinstance(runtime_identity, Mapping):
        raise ContractValidationError("Gate B runtime identity is absent")
    executed_git_commit = runtime_identity.get("executed_git_commit")
    executed_environment_digest = runtime_identity.get("executed_environment_digest")
    if not isinstance(executed_git_commit, str) or not isinstance(executed_environment_digest, str):
        raise ContractValidationError("Gate B runtime identity is invalid")
    prior_object_count = 0
    prior_net_new_bytes = 0
    if scope == "remaining-matrix":
        if not prior_actual:
            raise ContractValidationError(
                "Gate B remaining inventory requires validated sentinel accounting"
            )
        sentinel_items = _sentinel_items(packet, prior_actual)
        if sentinel_items != prior_actual:
            raise ContractValidationError("Gate B prior inventory is not the exact sentinel")
        _validate_frozen_identity(prior_actual)
        _validate_cross_links(
            prior_actual,
            executed_git_commit=executed_git_commit,
            executed_environment_digest=executed_environment_digest,
        )
        prior_object_count = len(prior_actual)
        prior_net_new_bytes = sum(item.size_bytes for item in prior_actual)
    elif prior_actual:
        raise ContractValidationError("Gate B sentinel cannot carry prior publication usage")
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
    if prior_net_new_bytes + total > int(caps["cos_net_new_bytes"]) or prior_object_count + len(
        actual
    ) > int(caps["cos_object_count"]):
        raise ContractValidationError("Gate B actual inventory exceeds a reviewed cap")
    _validate_cross_links(
        tuple(actual),
        executed_git_commit=executed_git_commit,
        executed_environment_digest=executed_environment_digest,
    )
    return tuple(actual)


def _validate_frozen_identity(actual: tuple[ActualObject, ...]) -> None:
    if any(
        item.size_bytes != len(item.payload)
        or item.sha256 != hashlib.sha256(item.payload).hexdigest()
        for item in actual
    ):
        raise ContractValidationError("Gate B frozen actual identity drifted")


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
    expected = tuple(
        (str(item["run_id"]), str(item["kind"]), str(item["object_key"]))
        for item in _expected(packet)
        if item["run_id"] == sentinel["run_id"]
    )
    observed = tuple((item.run_id, item.kind, item.object_key) for item in items)
    if (
        len(items) != 9
        or observed != expected
        or items[-1].kind != "success-marker"
        or any(item.kind == "success-marker" for item in items[:-1])
    ):
        raise ContractValidationError(
            "Gate B sentinel inventory does not exact-cover expected schema"
        )
    return items


def publish_staged_sentinel(
    packet: Mapping[str, object],
    actual: tuple[ActualObject, ...],
    writer: GateBObjectClient,
    authorization: GateBAuthorization,
) -> tuple[str, ...]:
    """Publish only the lowest-cost sentinel, verifying every PUT immediately."""

    _validate_authorization(packet, authorization)
    _validate_frozen_identity(actual)
    validated = validate_actual_inventory(
        packet,
        {item.object_key: item.payload for item in actual},
        scope="sentinel",
    )
    if validated != actual:
        raise ContractValidationError("Gate B staged sentinel validation drifted")
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
    authorization: GateBAuthorization,
) -> None:
    """Exact-cover one sentinel using a capability-separated read-only identity."""

    _validate_authorization(packet, authorization)
    _validate_frozen_identity(actual)
    validated = validate_actual_inventory(
        packet,
        {item.object_key: item.payload for item in actual},
        scope="sentinel",
    )
    if validated != actual:
        raise ContractValidationError("Gate B sentinel readback inventory drifted")
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
    "GATE_B_APPROVAL",
    "GateBAuthorization",
    "GateBObjectClient",
    "independent_read_sentinel",
    "publish_staged_sentinel",
    "validate_actual_inventory",
]
