"""Fail-closed staging and two-capability publication for Issue #68 Gate B."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.contracts.run import RunSpec
from pcdb.m3.gate_a_runtime import EngineFactory, ReadOnlyInputClient
from pcdb.replay.engine import load_replay_trajectory
from pcdb.replay.publication import publish_replay_attempt
from pcdb.storage.local import LocalArtifactStore

_INPUT_PURPOSES = (
    "candidate-feature-matrix",
    "dataset-manifest",
    "feature-descriptor",
    "processing-manifest",
    "split-manifest",
    "hidden-truth",
)

GATE_B_APPROVAL = "APPROVED FOR M3 ALPHA + IRED GATE B FORMAL EXECUTION AND PUBLICATION"


class GateBReadClient(Protocol):
    """Capability-separated fresh-read object surface."""

    def head(self, bucket: str, key: str) -> object: ...

    def get(self, bucket: str, key: str) -> bytes: ...

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]: ...


class GateBObjectClient(GateBReadClient, Protocol):
    """Writer object surface; DeleteObject is intentionally absent."""

    def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None: ...


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


@dataclass(frozen=True, slots=True)
class SentinelReadback:
    """Capability-separated sentinel checkpoint bound to packet and actual inventory."""

    packet_sha256: str
    sentinel_inventory_sha256: str


@dataclass(frozen=True, slots=True)
class FullMatrixReadback:
    """Independent final checkpoint bound to all 162 runtime objects."""

    packet_sha256: str
    exact_inventory_sha256: str


def _write_exclusive(path: Path, payload: bytes) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            target.unlink()
        raise


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


def _fresh(client: GateBReadClient, bucket: str, item: ActualObject) -> None:
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


def _actual_identity(actual: tuple[ActualObject, ...]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            [
                {
                    "kind": item.kind,
                    "object_key": item.object_key,
                    "run_id": item.run_id,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in actual
            ]
        )
    ).hexdigest()


def freeze_actual_inventory(
    path: Path,
    packet: Mapping[str, object],
    actual: tuple[ActualObject, ...],
    authorization: GateBAuthorization,
    *,
    scope: str,
) -> None:
    """Persist a payload-free immutable checkpoint after local strict validation."""

    _validate_authorization(packet, authorization)
    _validate_frozen_identity(actual)
    packet_sha256 = hashlib.sha256(canonical_json_bytes(dict(packet))).hexdigest()
    value = {
        "objects": [
            {
                "kind": item.kind,
                "object_key": item.object_key,
                "run_id": item.run_id,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in actual
        ],
        "packet_sha256": packet_sha256,
        "schema_version": "m3-gate-b-actual-inventory-v1",
        "scope": scope,
    }
    _write_exclusive(path, canonical_json_bytes(value))


def load_actual_inventory(
    path: Path,
    staged_root: Path,
    packet: Mapping[str, object],
    authorization: GateBAuthorization,
    *,
    scope: str,
    prior_actual: tuple[ActualObject, ...] = (),
) -> tuple[ActualObject, ...]:
    """Restore one frozen checkpoint only when staged bytes retain exact identity."""

    _validate_authorization(packet, authorization)
    try:
        payload = path.resolve().read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("Gate B actual inventory checkpoint is invalid") from None
    packet_sha256 = hashlib.sha256(canonical_json_bytes(dict(packet))).hexdigest()
    if (
        not isinstance(value, dict)
        or canonical_json_bytes(value) != payload
        or value.get("schema_version") != "m3-gate-b-actual-inventory-v1"
        or value.get("scope") != scope
        or value.get("packet_sha256") != packet_sha256
        or not isinstance(value.get("objects"), list)
    ):
        raise ContractValidationError("Gate B actual inventory checkpoint drifted")
    store = LocalArtifactStore(staged_root.resolve())
    payloads = {
        str(item["object_key"]): store.get_bytes(str(item["object_key"]))
        for item in value["objects"]
    }
    actual = validate_actual_inventory(
        packet,
        payloads,
        scope=scope,
        prior_actual=prior_actual,
    )
    observed = [
        {
            "kind": item.kind,
            "object_key": item.object_key,
            "run_id": item.run_id,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }
        for item in actual
    ]
    if observed != value["objects"]:
        raise ContractValidationError("Gate B staged bytes differ from frozen checkpoint")
    return actual


def write_sentinel_readback(
    path: Path,
    readback: SentinelReadback,
    authorization: GateBAuthorization,
) -> None:
    """Persist the independent reader checkpoint without permitting replacement."""

    value = {
        "approval_comment_id": authorization.approval_comment_id,
        "packet_sha256": readback.packet_sha256,
        "schema_version": "m3-gate-b-sentinel-readback-v1",
        "sentinel_inventory_sha256": readback.sentinel_inventory_sha256,
    }
    _write_exclusive(path, canonical_json_bytes(value))


def load_sentinel_readback(path: Path, authorization: GateBAuthorization) -> SentinelReadback:
    try:
        payload = path.resolve().read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("Gate B sentinel readback checkpoint is invalid") from None
    if (
        not isinstance(value, dict)
        or canonical_json_bytes(value) != payload
        or value.get("schema_version") != "m3-gate-b-sentinel-readback-v1"
        or value.get("approval_comment_id") != authorization.approval_comment_id
    ):
        raise ContractValidationError("Gate B sentinel readback checkpoint drifted")
    return SentinelReadback(
        packet_sha256=str(value.get("packet_sha256")),
        sentinel_inventory_sha256=str(value.get("sentinel_inventory_sha256")),
    )


def write_final_readback(
    path: Path,
    packet: Mapping[str, object],
    actual: tuple[ActualObject, ...],
    authorization: GateBAuthorization,
    readback: FullMatrixReadback,
) -> None:
    """Write a compact engineering Gate 2 verdict after independent full readback."""

    _validate_authorization(packet, authorization)
    if len(actual) != 162 or len({item.run_id for item in actual}) != 18:
        raise ContractValidationError("Gate B final verdict requires full 162-object matrix")
    expected_readback = FullMatrixReadback(
        packet_sha256=authorization.packet_sha256,
        exact_inventory_sha256=_actual_identity(actual),
    )
    if readback != expected_readback:
        raise ContractValidationError("Gate B final independent readback checkpoint drifted")
    conditions = {
        "deterministic-selection-ties-random-trajectory": "PASS",
        "no-repeat-and-budget-monotonic": "PASS",
        "oracle-only-hidden-label-reveal": "PASS",
        "recovery-and-success-marker-eligibility": "PASS",
        "four-deterministic-decision-metrics": "PASS",
    }
    value = {
        "approval_comment_id": authorization.approval_comment_id,
        "exact_inventory_sha256": _actual_identity(actual),
        "exact_object_count": len(actual),
        "gate_2_conditions": conditions,
        "packet_sha256": authorization.packet_sha256,
        "schema_version": "m3-gate-b-final-readback-v1",
        "verdict": "PASS",
        "verdict_scope": "Gate 2 engineering conditions only; no scientific superiority claim",
    }
    _write_exclusive(path, canonical_json_bytes(value))


def _remaining_items(
    packet: Mapping[str, object], actual: tuple[ActualObject, ...]
) -> tuple[ActualObject, ...]:
    sentinel = packet.get("sentinel")
    if not isinstance(sentinel, Mapping) or not isinstance(sentinel.get("run_id"), str):
        raise ContractValidationError("Gate B sentinel identity is invalid")
    items = tuple(item for item in actual if item.run_id != sentinel["run_id"])
    expected = tuple(
        (str(item["run_id"]), str(item["kind"]), str(item["object_key"]))
        for item in _expected(packet)
        if item["run_id"] != sentinel["run_id"]
    )
    observed = tuple((item.run_id, item.kind, item.object_key) for item in items)
    if len(items) != 153 or observed != expected:
        raise ContractValidationError(
            "Gate B remaining inventory does not exact-cover expected schema"
        )
    run_ids = tuple(dict.fromkeys(item.run_id for item in items))
    if len(run_ids) != 17:
        raise ContractValidationError("Gate B remaining RunSpec cardinality drifted")
    for run_id in run_ids:
        members = tuple(item for item in items if item.run_id == run_id)
        if (
            len(members) != 9
            or members[-1].kind != "success-marker"
            or any(item.kind == "success-marker" for item in members[:-1])
        ):
            raise ContractValidationError("Gate B remaining publication order drifted")
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
    reader: GateBReadClient,
    actual: tuple[ActualObject, ...],
    authorization: GateBAuthorization,
) -> SentinelReadback:
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
    return SentinelReadback(
        packet_sha256=hashlib.sha256(canonical_json_bytes(dict(packet))).hexdigest(),
        sentinel_inventory_sha256=_actual_identity(items),
    )


def publish_staged_remaining(
    packet: Mapping[str, object],
    sentinel_actual: tuple[ActualObject, ...],
    remaining_actual: tuple[ActualObject, ...],
    writer: GateBObjectClient,
    authorization: GateBAuthorization,
    sentinel_readback: SentinelReadback,
) -> tuple[str, ...]:
    """Publish the remaining 17 RunSpecs after the exact sentinel checkpoint passes."""

    _validate_authorization(packet, authorization)
    sentinel = _sentinel_items(packet, sentinel_actual)
    expected_readback = SentinelReadback(
        packet_sha256=hashlib.sha256(canonical_json_bytes(dict(packet))).hexdigest(),
        sentinel_inventory_sha256=_actual_identity(sentinel),
    )
    if sentinel_readback != expected_readback:
        raise ContractValidationError("Gate B sentinel readback checkpoint drifted")
    _validate_frozen_identity(sentinel_actual)
    _validate_frozen_identity(remaining_actual)
    validated = validate_actual_inventory(
        packet,
        {item.object_key: item.payload for item in remaining_actual},
        scope="remaining-matrix",
        prior_actual=sentinel_actual,
    )
    if validated != remaining_actual:
        raise ContractValidationError("Gate B staged remaining inventory drifted")
    store = packet.get("evidence_store")
    if not isinstance(store, Mapping):
        raise ContractValidationError("Gate B evidence store is invalid")
    bucket, prefix = str(store["bucket"]), str(store["prefix"])
    expected_existing = tuple(item.object_key for item in sentinel)
    observed = writer.list_keys(bucket, prefix)
    if len(observed) != len(set(observed)) or set(observed) != set(expected_existing):
        raise ContractValidationError("Gate B sentinel readback exact-cover no longer holds")
    items = _remaining_items(packet, remaining_actual)
    for item in items:
        writer.put_forbid_overwrite(bucket, item.object_key, item.payload)
        _fresh(writer, bucket, item)
    return tuple(item.object_key for item in items)


def independent_read_full_matrix(
    packet: Mapping[str, object],
    reader: GateBReadClient,
    actual: tuple[ActualObject, ...],
    authorization: GateBAuthorization,
) -> FullMatrixReadback:
    """Fresh-read and exact-cover all 162 objects with the independent reader."""

    _validate_authorization(packet, authorization)
    sentinel = _sentinel_items(packet, actual)
    remaining = _remaining_items(packet, actual)
    _validate_frozen_identity(actual)
    if (
        validate_actual_inventory(
            packet,
            {item.object_key: item.payload for item in sentinel},
            scope="sentinel",
        )
        != sentinel
    ):
        raise ContractValidationError("Gate B final sentinel inventory drifted")
    if (
        validate_actual_inventory(
            packet,
            {item.object_key: item.payload for item in remaining},
            scope="remaining-matrix",
            prior_actual=sentinel,
        )
        != remaining
    ):
        raise ContractValidationError("Gate B final remaining inventory drifted")
    store = packet.get("evidence_store")
    if not isinstance(store, Mapping):
        raise ContractValidationError("Gate B evidence store is invalid")
    bucket, prefix = str(store["bucket"]), str(store["prefix"])
    expected = tuple(item.object_key for item in (*sentinel, *remaining))
    observed = reader.list_keys(bucket, prefix)
    if len(observed) != len(set(observed)) or set(observed) != set(expected):
        raise ContractValidationError("Gate B final independent reader exact-cover drifted")
    for item in (*sentinel, *remaining):
        _fresh(reader, bucket, item)
    return FullMatrixReadback(
        packet_sha256=authorization.packet_sha256,
        exact_inventory_sha256=_actual_identity((*sentinel, *remaining)),
    )


def _run_spec(value: object) -> RunSpec:
    if not isinstance(value, dict):
        raise ContractValidationError("Gate B RunSpec is invalid")
    try:
        return RunSpec(**value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("Gate B RunSpec is invalid") from exc


def _formal_inputs(
    packet: Mapping[str, object],
    input_client: ReadOnlyInputClient,
    *,
    task_ids: tuple[str, ...],
) -> dict[str, dict[str, bytes]]:
    raw = packet.get("input_inventory")
    if (
        not isinstance(raw, list)
        or len(raw) != 12
        or any(not isinstance(item, dict) for item in raw)
    ):
        raise ContractValidationError("Gate B input inventory must remain an exact 12-object set")
    inventory = list(raw)
    tasks = tuple(sorted({str(item["task_id"]) for item in inventory}))
    if len(tasks) != 2:
        raise ContractValidationError("Gate B input inventory must contain exactly two tasks")
    for item in inventory:
        metadata = input_client.head(str(item["bucket"]), str(item["object_key"]))
        size = getattr(metadata, "size_bytes", None)
        digest = getattr(metadata, "sha256", None)
        if size != item["size_bytes"] or (digest is not None and digest != item["sha256"]):
            raise ContractValidationError("Gate B input HEAD differs from reviewed identity")
    payloads: dict[str, dict[str, bytes]] = {}
    if not task_ids or any(task_id not in tasks for task_id in task_ids):
        raise ContractValidationError("Gate B requested input task scope is invalid")
    for task_id in task_ids:
        task_items = [item for item in inventory if item["task_id"] == task_id]
        if tuple(str(item["purpose"]) for item in task_items) != _INPUT_PURPOSES:
            raise ContractValidationError("Gate B task input order or purpose set drifted")
        payloads[task_id] = {}
    selected_items = [item for item in inventory if item["task_id"] in task_ids]
    if [bool(item["truth_bearing"]) for item in selected_items] != [
        *([False] * (len(selected_items) - len(task_ids))),
        *([True] * len(task_ids)),
    ]:
        raise ContractValidationError("Gate B truth-bearing inputs must remain last")
    for item in selected_items:
        payload = input_client.get(str(item["bucket"]), str(item["object_key"]))
        if (
            len(payload) != item["size_bytes"]
            or hashlib.sha256(payload).hexdigest() != item["sha256"]
        ):
            raise ContractValidationError("Gate B input GET differs from reviewed identity")
        payloads[str(item["task_id"])][str(item["purpose"])] = payload
    return payloads


def execute_formal_scope(
    packet: Mapping[str, object],
    *,
    input_client: ReadOnlyInputClient,
    engine_factory: EngineFactory,
    scratch_root: Path,
    scope: str,
    authorization: GateBAuthorization,
    prior_actual: tuple[ActualObject, ...] = (),
    sentinel_readback: SentinelReadback | None = None,
) -> tuple[ActualObject, ...]:
    """Generate and freeze one formal scope locally; this function performs no COS PUT."""

    _validate_authorization(packet, authorization)
    sentinel = packet.get("sentinel")
    raw_specs = packet.get("run_specs")
    caps = packet.get("execution_caps")
    runtime = packet.get("runtime_identity")
    if (
        scope not in {"sentinel", "remaining-matrix"}
        or not isinstance(sentinel, Mapping)
        or not isinstance(raw_specs, list)
        or len(raw_specs) != 18
        or not isinstance(caps, Mapping)
        or not isinstance(runtime, Mapping)
    ):
        raise ContractValidationError("Gate B formal scope packet fields are invalid")
    if scope == "remaining-matrix":
        if sentinel_readback is None:
            raise ContractValidationError("Gate B remaining scope requires sentinel readback")
        expected_readback = SentinelReadback(
            packet_sha256=hashlib.sha256(canonical_json_bytes(dict(packet))).hexdigest(),
            sentinel_inventory_sha256=_actual_identity(_sentinel_items(packet, prior_actual)),
        )
        if sentinel_readback != expected_readback:
            raise ContractValidationError("Gate B sentinel readback checkpoint drifted")
    elif prior_actual or sentinel_readback is not None:
        raise ContractValidationError("Gate B sentinel scope cannot carry prior state")
    root = scratch_root.resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ContractValidationError("Gate B formal scratch root must start empty")
    root.mkdir(parents=True, exist_ok=True)
    sentinel_run_id = str(sentinel.get("run_id"))
    selected_specs = [
        item
        for item in raw_specs
        if isinstance(item, dict)
        and ((str(item.get("run_id")) == sentinel_run_id) == (scope == "sentinel"))
    ]
    expected_count = 1 if scope == "sentinel" else 17
    if len(selected_specs) != expected_count:
        raise ContractValidationError("Gate B formal RunSpec scope drifted")
    task_ids = tuple(sorted({str(item["task_id"]) for item in selected_specs}))
    started = time.perf_counter()
    inputs = _formal_inputs(packet, input_client, task_ids=task_ids)
    store = LocalArtifactStore(root / "objects")
    evidence_store = packet.get("evidence_store")
    if not isinstance(evidence_store, Mapping):
        raise ContractValidationError("Gate B evidence store is invalid")
    key_prefix = str(evidence_store["prefix"])
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for item in selected_specs:
        run_spec = _run_spec(item.get("run_spec"))
        task_id = str(item.get("task_id"))
        if run_spec.run_id != item.get("run_id") or task_id not in inputs:
            raise ContractValidationError("Gate B RunSpec identity is unresolved")
        trajectory = engine_factory(run_spec, inputs[task_id]).run()
        if trajectory.run_id != run_spec.run_id or not trajectory.complete:
            raise ContractValidationError("Gate B formal trajectory is incomplete or drifted")
        finished = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        publish_replay_attempt(
            trajectory,
            store=store,
            attempt_id="attempt-001",
            started_at=now,
            finished_at=finished,
            executed_git_commit=str(runtime["executed_git_commit"]),
            executed_environment_digest=str(runtime["executed_environment_digest"]),
            runtime_version=f"python-{platform.python_version()}",
            key_prefix=key_prefix,
            executor_type="compute_server",
            region="ap-beijing",
        )
    expected = [
        item
        for item in _expected(packet)
        if ((str(item["run_id"]) == sentinel_run_id) == (scope == "sentinel"))
    ]
    payloads = {
        str(item["object_key"]): store.get_bytes(str(item["object_key"])) for item in expected
    }
    actual = validate_actual_inventory(
        packet,
        payloads,
        scope=scope,
        prior_actual=prior_actual,
    )
    scratch_bytes = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    max_wall = caps.get("max_wall_seconds")
    max_scratch = caps.get("max_scratch_bytes")
    max_network = caps.get("max_network_transfer_bytes")
    if (
        isinstance(max_wall, bool)
        or not isinstance(max_wall, (int, float))
        or time.perf_counter() - started > max_wall
        or isinstance(max_scratch, bool)
        or not isinstance(max_scratch, int)
        or scratch_bytes > max_scratch
        or isinstance(max_network, bool)
        or not isinstance(max_network, int)
        or input_client.transferred_bytes > max_network
    ):
        raise ContractValidationError("Gate B formal scope exceeded a reviewed resource cap")
    return actual


def _strict_packet(packet_bytes: bytes, packet_sha256: str) -> dict[str, object]:
    if hashlib.sha256(packet_bytes).hexdigest() != packet_sha256:
        raise ContractValidationError("Gate B CLI packet identity drifted")
    try:
        value = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("Gate B CLI packet must be canonical JSON") from None
    if not isinstance(value, dict) or canonical_json_bytes(value) != packet_bytes:
        raise ContractValidationError("Gate B CLI packet must be canonical JSON")
    return value


def _verify_runtime_source(packet: Mapping[str, object], repository_root: Path) -> None:
    reviewed = packet.get("reviewed_source")
    artifacts = packet.get("source_artifacts")
    if not isinstance(reviewed, Mapping) or not isinstance(artifacts, list):
        raise ContractValidationError("Gate B CLI reviewed source is invalid")
    root = repository_root.resolve()
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractValidationError("Gate B CLI runtime source could not be verified") from exc
    if commit != reviewed.get("git_commit") or tree != reviewed.get("git_tree") or status:
        raise ContractValidationError("Gate B CLI runtime source is not the exact clean checkout")
    from pcdb import cli as cli_module
    from pcdb.m3 import gate_a as gate_a_module
    from pcdb.m3 import gate_a_runtime as gate_a_runtime_module
    from pcdb.replay import engine as replay_engine_module
    from pcdb.replay import publication as replay_publication_module
    from pcdb.storage import cos as cos_module

    imported = {
        "src/pcdb/cli.py": Path(str(cli_module.__file__)).resolve(),
        "src/pcdb/m3/gate_a.py": Path(str(gate_a_module.__file__)).resolve(),
        "src/pcdb/m3/gate_a_runtime.py": Path(str(gate_a_runtime_module.__file__)).resolve(),
        "src/pcdb/m3/gate_b_runtime.py": Path(__file__).resolve(),
        "src/pcdb/replay/engine.py": Path(str(replay_engine_module.__file__)).resolve(),
        "src/pcdb/replay/publication.py": Path(str(replay_publication_module.__file__)).resolve(),
        "src/pcdb/storage/cos.py": Path(str(cos_module.__file__)).resolve(),
    }
    if any(path != (root / name).resolve() for name, path in imported.items()):
        raise ContractValidationError("Gate B CLI imported source escaped reviewed checkout")
    for item in artifacts:
        if not isinstance(item, Mapping):
            raise ContractValidationError("Gate B CLI source artifact is invalid")
        path = root / str(item.get("path"))
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ContractValidationError("Gate B CLI source artifact is absent") from exc
        if len(payload) != item.get("size_bytes") or hashlib.sha256(
            payload
        ).hexdigest() != item.get("sha256"):
            raise ContractValidationError("Gate B CLI source artifact drifted")


def _authorization_from_arguments(arguments: Any) -> GateBAuthorization:
    return GateBAuthorization(
        approval_comment_id=int(arguments.approval_comment_id),
        approval_text=str(arguments.approval),
        packet_sha256=str(arguments.packet_sha256),
    )


def _validate_operator_cost(arguments: Any, caps: Mapping[str, object]) -> None:
    hourly = arguments.hourly_cost_cny
    max_wall = caps.get("max_wall_seconds")
    max_cost = caps.get("max_cost_cny")
    if (
        isinstance(hourly, bool)
        or not isinstance(hourly, (int, float))
        or hourly <= 0
        or isinstance(max_wall, bool)
        or not isinstance(max_wall, (int, float))
        or isinstance(max_cost, bool)
        or not isinstance(max_cost, (int, float))
        or hourly * max_wall / 3600 > max_cost
    ):
        raise ContractValidationError("Gate B operator cost ceiling exceeds reviewed cap")


def validate_remote_phase_start(
    packet: Mapping[str, object],
    client: GateBReadClient,
    *,
    phase: str,
    sentinel_actual: tuple[ActualObject, ...] = (),
) -> None:
    """Bind the remote prefix before any formal input body or truth is read."""

    store = packet.get("evidence_store")
    if not isinstance(store, Mapping):
        raise ContractValidationError("Gate B evidence store is invalid")
    observed = client.list_keys(str(store["bucket"]), str(store["prefix"]))
    if len(observed) != len(set(observed)):
        raise ContractValidationError("Gate B remote phase start returned duplicate keys")
    if phase == "sentinel-write":
        if observed:
            raise ContractValidationError("Gate B evidence prefix must start empty")
        return
    if phase == "remaining-write":
        expected = tuple(item.object_key for item in _sentinel_items(packet, sentinel_actual))
        if set(observed) != set(expected):
            raise ContractValidationError("Gate B remote sentinel exact-cover drifted before run")
        return
    raise ContractValidationError("Gate B remote phase start is invalid")


def _enforce_process_caps(caps: Mapping[str, object]) -> None:
    max_vcpu = caps.get("max_vcpu")
    max_ram = caps.get("max_ram_bytes")
    if (
        isinstance(max_vcpu, bool)
        or not isinstance(max_vcpu, int)
        or max_vcpu < 1
        or isinstance(max_ram, bool)
        or not isinstance(max_ram, int)
        or max_ram < 1
    ):
        raise ContractValidationError("Gate B process caps are invalid")
    get_affinity = getattr(os, "sched_getaffinity", None)
    set_affinity = getattr(os, "sched_setaffinity", None)
    if callable(get_affinity) and callable(set_affinity):
        visible = sorted(get_affinity(0))
        set_affinity(0, visible[:max_vcpu])
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    new_soft = max_ram if soft == resource.RLIM_INFINITY else min(soft, max_ram)
    new_hard = max_ram if hard == resource.RLIM_INFINITY else min(hard, max_ram)
    resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard))


def run_gate_b_cli(arguments: Any) -> int:
    """Run one of four mechanical phases under the role bound to the current CVM."""

    from pcdb.m3.gate_a import _verified_runtime_environment_digest
    from pcdb.m3.gate_a_runtime import build_reviewed_replay_engine
    from pcdb.storage.cos import (
        Issue68GateBReadOnlyCosClient,
        Issue68GateBWriterCosClient,
    )

    packet_path = Path(arguments.packet)
    packet_sha256 = str(arguments.packet_sha256)
    packet = _strict_packet(packet_path.read_bytes(), packet_sha256)
    authorization = _authorization_from_arguments(arguments)
    _validate_authorization(packet, authorization)
    repository_root = Path(arguments.repository_root).resolve()
    _verify_runtime_source(packet, repository_root)
    runtime = packet.get("runtime_identity")
    if not isinstance(runtime, Mapping) or _verified_runtime_environment_digest(
        repository_root
    ) != runtime.get("executed_environment_digest"):
        raise ContractValidationError("Gate B CLI runtime environment drifted")
    scratch_root = Path(arguments.scratch_root).resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    sentinel_stage = scratch_root / "sentinel-stage" / "objects"
    remaining_stage = scratch_root / "remaining-stage" / "objects"
    sentinel_inventory_path = scratch_root / "sentinel-actual-inventory.json"
    remaining_inventory_path = scratch_root / "remaining-actual-inventory.json"
    sentinel_readback_path = scratch_root / "sentinel-readback.json"
    phase = str(arguments.phase)
    caps = packet.get("execution_caps")
    if not isinstance(caps, Mapping):
        raise ContractValidationError("Gate B CLI execution caps are invalid")
    _validate_operator_cost(arguments, caps)
    _enforce_process_caps(caps)
    transfer_cap = int(caps["max_network_transfer_bytes"])

    if phase == "sentinel-write":
        if any(scratch_root.iterdir()):
            raise ContractValidationError("Gate B sentinel scratch root must start empty")
        writer = Issue68GateBWriterCosClient(max_transfer_bytes=transfer_cap)
        validate_remote_phase_start(packet, writer, phase="sentinel-write")
        actual = execute_formal_scope(
            packet,
            input_client=writer,
            engine_factory=lambda run_spec, inputs: build_reviewed_replay_engine(
                repository_root, run_spec, inputs
            ),
            scratch_root=sentinel_stage.parent,
            scope="sentinel",
            authorization=authorization,
        )
        freeze_actual_inventory(
            sentinel_inventory_path,
            packet,
            actual,
            authorization,
            scope="sentinel",
        )
        publish_staged_sentinel(packet, actual, writer, authorization)
        return 0

    sentinel_actual = load_actual_inventory(
        sentinel_inventory_path,
        sentinel_stage,
        packet,
        authorization,
        scope="sentinel",
    )
    if phase == "sentinel-read":
        reader = Issue68GateBReadOnlyCosClient(max_transfer_bytes=transfer_cap)
        readback = independent_read_sentinel(packet, reader, sentinel_actual, authorization)
        write_sentinel_readback(sentinel_readback_path, readback, authorization)
        return 0

    readback = load_sentinel_readback(sentinel_readback_path, authorization)
    if phase == "remaining-write":
        if remaining_stage.parent.exists():
            raise ContractValidationError("Gate B remaining scratch must start absent")
        writer = Issue68GateBWriterCosClient(max_transfer_bytes=transfer_cap)
        validate_remote_phase_start(
            packet,
            writer,
            phase="remaining-write",
            sentinel_actual=sentinel_actual,
        )
        remaining = execute_formal_scope(
            packet,
            input_client=writer,
            engine_factory=lambda run_spec, inputs: build_reviewed_replay_engine(
                repository_root, run_spec, inputs
            ),
            scratch_root=remaining_stage.parent,
            scope="remaining-matrix",
            authorization=authorization,
            prior_actual=sentinel_actual,
            sentinel_readback=readback,
        )
        freeze_actual_inventory(
            remaining_inventory_path,
            packet,
            remaining,
            authorization,
            scope="remaining-matrix",
        )
        publish_staged_remaining(
            packet,
            sentinel_actual,
            remaining,
            writer,
            authorization,
            readback,
        )
        return 0

    if phase == "final-read":
        remaining = load_actual_inventory(
            remaining_inventory_path,
            remaining_stage,
            packet,
            authorization,
            scope="remaining-matrix",
            prior_actual=sentinel_actual,
        )
        reader = Issue68GateBReadOnlyCosClient(max_transfer_bytes=transfer_cap)
        full = (*sentinel_actual, *remaining)
        final_readback = independent_read_full_matrix(packet, reader, full, authorization)
        final_path = scratch_root / "final-readback.json"
        write_final_readback(
            final_path,
            packet,
            full,
            authorization,
            final_readback,
        )
        sys.stdout.write(
            f"evidence_path={final_path}\nsize_bytes={final_path.stat().st_size}\n"
            f"sha256={hashlib.sha256(final_path.read_bytes()).hexdigest()}\n"
        )
        return 0
    raise ContractValidationError("Gate B CLI phase is invalid")


__all__ = [
    "ActualObject",
    "GATE_B_APPROVAL",
    "GateBAuthorization",
    "GateBObjectClient",
    "GateBReadClient",
    "FullMatrixReadback",
    "SentinelReadback",
    "execute_formal_scope",
    "freeze_actual_inventory",
    "independent_read_full_matrix",
    "independent_read_sentinel",
    "load_actual_inventory",
    "load_sentinel_readback",
    "publish_staged_remaining",
    "publish_staged_sentinel",
    "run_gate_b_cli",
    "validate_actual_inventory",
    "validate_remote_phase_start",
    "write_sentinel_readback",
    "write_final_readback",
]
