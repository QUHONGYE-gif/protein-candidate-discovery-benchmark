"""Read-only Gate A sentinel execution for the frozen M3 protocol."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.feature_identity import load_feature_matrix
from pcdb.contracts.run import RunSpec
from pcdb.m2.features import sequence_feature_definitions
from pcdb.m2.models import model_config_bytes
from pcdb.m3.gate_a import validate_gate_a_entry
from pcdb.replay.engine import ReplayEngine, ReplayTrajectory

_REQUIRED_PURPOSES = (
    "candidate-feature-matrix",
    "dataset-manifest",
    "feature-descriptor",
    "processing-manifest",
    "split-manifest",
    "hidden-truth",
)
_REQUIRED_CHECKS = {
    "same-runspec-double-run",
    "interrupted-then-recovered",
    "no-repeat-purchase",
    "budget-monotonic",
    "four-decision-metrics",
    "resource-caps",
}
GATE_A_APPROVAL = "APPROVED FOR M3 IRED SENTINEL GATE A READ-ONLY PREFLIGHT"


class ReadOnlyInputClient(Protocol):
    """The only remote operations Gate A may use."""

    def head(self, bucket: str, key: str) -> object:
        """Return object metadata without reading its body."""
        ...

    def get(self, bucket: str, key: str) -> bytes:
        """Fresh-read one exact reviewed object."""
        ...

    @property
    def transferred_bytes(self) -> int:
        """Return payload bytes transferred by this client."""
        ...


class RunnableReplay(Protocol):
    """Small execution surface shared by the real ReplayEngine and synthetic tests."""

    def run(
        self,
        *,
        checkpoint: bytes | None = None,
        stop_after_round: int | None = None,
    ) -> ReplayTrajectory: ...


EngineFactory = Callable[[RunSpec, dict[str, bytes]], RunnableReplay]


def _run_spec(value: object) -> RunSpec:
    if not isinstance(value, dict):
        raise ContractValidationError("Gate A sentinel RunSpec is invalid")
    try:
        return RunSpec(**value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("Gate A sentinel RunSpec is invalid") from exc


def _head_size_and_sha256(metadata: object) -> tuple[int, str | None]:
    if isinstance(metadata, tuple) and len(metadata) == 2:
        size, digest = metadata
    else:
        size = getattr(metadata, "size_bytes", None)
        digest = getattr(metadata, "sha256", None)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ContractValidationError("Gate A HEAD returned invalid object size")
    if digest is not None and not isinstance(digest, str):
        raise ContractValidationError("Gate A HEAD returned invalid object digest")
    return size, digest


def _validated_inventory(packet: Mapping[str, object]) -> list[dict[str, object]]:
    raw_inventory = packet.get("input_inventory")
    if not isinstance(raw_inventory, list) or not raw_inventory:
        raise ContractValidationError("Gate A input inventory is invalid")
    inventory: list[dict[str, object]] = []
    identities: set[tuple[str, str]] = set()
    for raw in raw_inventory:
        if not isinstance(raw, dict):
            raise ContractValidationError("Gate A input inventory member is invalid")
        item = cast(dict[str, object], raw)
        try:
            identity = (str(item["bucket"]), str(item["object_key"]))
            size = item["size_bytes"]
            digest = item["sha256"]
            purpose = item["purpose"]
            task_id = item["task_id"]
            truth_bearing = item["truth_bearing"]
        except KeyError as exc:
            raise ContractValidationError("Gate A input inventory member is incomplete") from exc
        if (
            not all(identity)
            or identity in identities
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or not isinstance(digest, str)
            or len(digest) != 64
            or not isinstance(purpose, str)
            or not isinstance(task_id, str)
            or not isinstance(truth_bearing, bool)
            or truth_bearing != (purpose == "hidden-truth")
        ):
            raise ContractValidationError("Gate A input inventory member is invalid")
        identities.add(identity)
        inventory.append(item)
    if len(inventory) != 12:
        raise ContractValidationError("Gate A input inventory must remain an exact 12-object set")
    tasks = {str(item["task_id"]) for item in inventory}
    if len(tasks) != 2:
        raise ContractValidationError("Gate A input inventory must contain exactly two tasks")
    for task_id in tasks:
        purposes = [item["purpose"] for item in inventory if item["task_id"] == task_id]
        if sorted(cast(list[str], purposes)) != sorted(_REQUIRED_PURPOSES):
            raise ContractValidationError("Gate A task input purpose set drifted")
    truth_positions = [index for index, item in enumerate(inventory) if item["truth_bearing"]]
    if truth_positions != [10, 11]:
        raise ContractValidationError("Gate A truth-bearing objects must remain last")
    return inventory


def _sentinel_context(
    packet: Mapping[str, object], inventory: list[dict[str, object]]
) -> tuple[RunSpec, str, list[dict[str, object]], Mapping[str, object]]:
    sentinel = packet.get("sentinel")
    raw_specs = packet.get("run_specs")
    policy = packet.get("execution_policy")
    if (
        not isinstance(sentinel, dict)
        or not isinstance(raw_specs, list)
        or not isinstance(policy, dict)
    ):
        raise ContractValidationError("Gate A sentinel packet fields are invalid")
    run_id = sentinel.get("run_id")
    task_id = sentinel.get("task_id")
    matches = [
        item for item in raw_specs if isinstance(item, dict) and item.get("run_id") == run_id
    ]
    if len(matches) != 1 or not isinstance(task_id, str) or not task_id:
        raise ContractValidationError("Gate A sentinel identity is unresolved")
    run_spec = _run_spec(matches[0].get("run_spec"))
    if (
        run_spec.run_id != run_id
        or matches[0].get("task_id") != task_id
        or run_spec.model_id != sentinel.get("model_id")
        or run_spec.acquisition_id != sentinel.get("acquisition_id")
    ):
        raise ContractValidationError("Gate A sentinel differs from its RunSpec")
    task_inventory = [item for item in inventory if item["task_id"] == task_id]
    if [item["purpose"] for item in task_inventory] != list(_REQUIRED_PURPOSES):
        raise ContractValidationError("Gate A sentinel input order or exact purpose set drifted")
    if (
        policy.get("cos_puts") != 0
        or policy.get("formal_attempts") != 0
        or policy.get("manual_entry_starts") != 1
        or policy.get("automatic_retry") != 0
        or policy.get("overwrite") != 0
        or policy.get("replacement_attempts") != 0
        or set(cast(list[object], policy.get("required_checks"))) != _REQUIRED_CHECKS
    ):
        raise ContractValidationError("Gate A execution policy drifted")
    boundaries = packet.get("authorization_boundaries")
    if not isinstance(boundaries, dict) or boundaries != {
        "gate_a_execution_authorized": False,
        "gate_b_execution_authorized": False,
        "packet_review_authorizes_candidate_truth_read": False,
        "packet_review_authorizes_cos_access": False,
        "packet_review_authorizes_server_access": False,
    }:
        raise ContractValidationError("Gate A packet authorization boundary drifted")
    return run_spec, task_id, task_inventory, cast(Mapping[str, object], policy)


def _head_all(client: ReadOnlyInputClient, inventory: list[dict[str, object]]) -> None:
    for item in inventory:
        size, digest = _head_size_and_sha256(
            client.head(str(item["bucket"]), str(item["object_key"]))
        )
        if size != item["size_bytes"] or (digest is not None and digest != item["sha256"]):
            raise ContractValidationError("Gate A HEAD identity differs from reviewed input")


def _read_sentinel_inputs(
    client: ReadOnlyInputClient, inventory: list[dict[str, object]]
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for item in inventory:
        purpose = str(item["purpose"])
        payload = client.get(str(item["bucket"]), str(item["object_key"]))
        if (
            len(payload) != item["size_bytes"]
            or hashlib.sha256(payload).hexdigest() != item["sha256"]
        ):
            raise ContractValidationError("Gate A fresh GET differs from reviewed input")
        payloads[purpose] = payload
    return payloads


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    return int(value if platform.system() == "Darwin" else value * 1024)


def _visible_vcpu() -> int:
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def build_reviewed_replay_engine(
    repository_root: Path,
    run_spec: RunSpec,
    inputs: dict[str, bytes],
) -> ReplayEngine:
    """Build one real-input ReplayEngine behind its truth-owning constructor."""

    required = set(_REQUIRED_PURPOSES)
    if set(inputs) != required:
        raise ContractValidationError("Gate A engine inputs do not exact-cover reviewed purposes")
    task_ids = []
    for task in ("alpha-amylase", "ired"):
        path = repository_root / f"configs/experiments/m3-single-fidelity/{task}.json"
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == (
            run_spec.experiment_config_sha256
        ):
            task_ids.append(task)
    if len(task_ids) != 1:
        raise ContractValidationError("Gate A Experiment Config identity is unresolved")
    task_id = task_ids[0]
    experiment_config = (
        repository_root / f"configs/experiments/m3-single-fidelity/{task_id}.json"
    ).read_bytes()
    candidate_features = load_feature_matrix(
        inputs["candidate-feature-matrix"],
        feature_definitions=sequence_feature_definitions(),
    )
    return ReplayEngine.from_reviewed_inputs(
        run_spec=run_spec,
        experiment_config=experiment_config,
        feature_descriptor=inputs["feature-descriptor"],
        processing_manifest=inputs["processing-manifest"],
        hidden_truth=inputs["hidden-truth"],
        task_id=task_id,
        candidate_features=candidate_features,
        model_config=model_config_bytes(run_spec.model_id),
    )


def _trajectory_checks(trajectory: ReplayTrajectory, run_spec: RunSpec) -> list[int]:
    if trajectory.run_id != run_spec.run_id or not trajectory.complete:
        raise ContractValidationError("Gate A trajectory is incomplete or has wrong identity")
    selected = [
        candidate_id for event in trajectory.events for candidate_id in event.selected_candidate_ids
    ]
    if len(selected) != len(set(selected)) or len(selected) != run_spec.budget:
        raise ContractValidationError("Gate A trajectory repeats or miscounts a purchase")
    expected_boundaries = [run_spec.initial_sample_size]
    while expected_boundaries[-1] < run_spec.budget:
        expected_boundaries.append(expected_boundaries[-1] + run_spec.batch_size)
    boundaries = [event.distinct_revealed for event in trajectory.events]
    if boundaries != expected_boundaries:
        raise ContractValidationError("Gate A budget boundaries are not exact and monotonic")
    if any(event.metrics is None or event.metric_sha256 is None for event in trajectory.events):
        raise ContractValidationError("Gate A trajectory omitted decision metrics")
    return boundaries


def execute_gate_a_sentinel(
    packet: Mapping[str, object],
    *,
    input_client: ReadOnlyInputClient,
    engine_factory: EngineFactory,
    scratch_root: Path,
) -> bytes:
    """Run the reviewed read-only sentinel double-run and recovery checks."""

    started = time.perf_counter()
    inventory = _validated_inventory(packet)
    run_spec, task_id, task_inventory, policy = _sentinel_context(packet, inventory)
    _head_all(input_client, inventory)
    inputs = _read_sentinel_inputs(input_client, task_inventory)

    first = engine_factory(run_spec, inputs).run()
    boundaries = _trajectory_checks(first, run_spec)
    second = engine_factory(run_spec, inputs).run()
    _trajectory_checks(second, run_spec)
    if first.canonical_bytes != second.canonical_bytes:
        raise ContractValidationError("Gate A same-RunSpec trajectories differ")

    interrupted = engine_factory(run_spec, inputs).run(stop_after_round=0)
    checkpoint_bytes = interrupted.latest_checkpoint_bytes
    checkpoint_path = scratch_root.resolve() / "interrupted-checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_descriptor = os.open(
        checkpoint_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(checkpoint_descriptor, "wb") as checkpoint_file:
        checkpoint_file.write(checkpoint_bytes)
        checkpoint_file.flush()
        os.fsync(checkpoint_file.fileno())
    recovered_checkpoint = checkpoint_path.read_bytes()
    if recovered_checkpoint != checkpoint_bytes:
        raise ContractValidationError("Gate A checkpoint fresh readback drifted")
    recovered = engine_factory(run_spec, inputs).run(checkpoint=recovered_checkpoint)
    _trajectory_checks(recovered, run_spec)
    if first.canonical_bytes != recovered.canonical_bytes:
        raise ContractValidationError("Gate A recovered trajectory differs from uninterrupted run")

    wall_seconds = time.perf_counter() - started
    materialized_bytes = sum(len(payload) for payload in inputs.values()) + sum(
        len(payload)
        for payload in (
            first.canonical_bytes,
            second.canonical_bytes,
            checkpoint_bytes,
            recovered.canonical_bytes,
        )
    )
    max_wall = policy.get("max_wall_seconds")
    max_scratch = policy.get("max_scratch_bytes")
    max_network = policy.get("max_network_transfer_bytes")
    max_ram = policy.get("max_ram_bytes")
    max_vcpu = policy.get("max_vcpu")
    max_cost = policy.get("max_cost_cny")
    network_bytes = input_client.transferred_bytes
    peak_rss_bytes = _peak_rss_bytes()
    visible_vcpu = _visible_vcpu()
    scratch_bytes = checkpoint_path.stat().st_size
    if (
        isinstance(max_wall, bool)
        or not isinstance(max_wall, (int, float))
        or wall_seconds > max_wall
        or isinstance(max_scratch, bool)
        or not isinstance(max_scratch, int)
        or scratch_bytes > max_scratch
        or isinstance(max_network, bool)
        or not isinstance(max_network, int)
        or network_bytes > max_network
        or isinstance(max_ram, bool)
        or not isinstance(max_ram, int)
        or peak_rss_bytes > max_ram
        or isinstance(max_vcpu, bool)
        or not isinstance(max_vcpu, int)
        or visible_vcpu > max_vcpu
        or isinstance(max_cost, bool)
        or not isinstance(max_cost, (int, float))
        or max_cost <= 0
    ):
        raise ContractValidationError("Gate A sentinel exceeded a reviewed resource cap")

    checks = {name: "PASS" for name in sorted(_REQUIRED_CHECKS)}
    checks["resource-caps"] = "INCONCLUSIVE"
    evidence = {
        "checks": checks,
        "checkpoint_sha256": hashlib.sha256(checkpoint_bytes).hexdigest(),
        "cos_puts": 0,
        "distinct_reveal_boundaries": boundaries,
        "formal_attempts": 0,
        "input_get_count": len(task_inventory),
        "input_head_count": len(inventory),
        "materialized_bytes": materialized_bytes,
        "resource_observations": {
            "cost_cny": {
                "reviewed_ceiling": max_cost,
                "status": "requires-independent-operator-billing-check",
            },
            "network_transfer_bytes": network_bytes,
            "peak_rss_bytes": peak_rss_bytes,
            "scratch_bytes": scratch_bytes,
            "visible_vcpu": visible_vcpu,
            "wall_seconds": wall_seconds,
        },
        "run_id": run_spec.run_id,
        "run_spec": asdict(run_spec),
        "schema_version": "m3-gate-a-evidence-v1",
        "task_id": task_id,
        "trajectory_sha256": {
            "first": first.sha256,
            "recovered": recovered.sha256,
            "second": second.sha256,
        },
        "verdict": "INCONCLUSIVE",
        "verdict_reason": "actual cloud billing requires independent operator review",
        "wall_seconds_observed": wall_seconds,
    }
    return canonical_json_bytes(evidence)


def execute_gate_a_preflight(
    *,
    approval: str,
    packet_bytes: bytes,
    packet_sha256: str,
    repository_root: Any,
    scratch_root: Any,
    input_client: ReadOnlyInputClient,
    engine_factory: EngineFactory,
) -> bytes:
    """Attest reviewed entry before delegating to the read-only sentinel runner."""

    if approval != GATE_A_APPROVAL:
        raise ContractValidationError("Issue #68 Gate A approval is absent or drifted")
    validate_gate_a_entry(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        repository_root=repository_root,
        scratch_root=scratch_root,
    )
    try:
        packet = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("Gate A packet must be canonical JSON") from None
    if not isinstance(packet, dict) or canonical_json_bytes(packet) != packet_bytes:
        raise ContractValidationError("Gate A packet must be canonical JSON")
    sentinel_evidence = execute_gate_a_sentinel(
        packet,
        input_client=input_client,
        engine_factory=engine_factory,
        scratch_root=scratch_root,
    )
    evidence = cast(dict[str, object], json.loads(sentinel_evidence))
    evidence["gate_a_packet_sha256"] = packet_sha256
    evidence["reviewed_source"] = packet["reviewed_source"]
    evidence["authorization"] = "exact-external-approval-accepted"
    return canonical_json_bytes(evidence)


def write_gate_a_evidence(path: Path, payload: bytes) -> None:
    """Create one local evidence file without allowing replacement."""

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


def run_gate_a_cli(arguments: Any) -> int:
    """Execute one explicitly authorized Issue #68 Gate A sentinel preflight."""

    from pcdb.storage.cos import Issue68GateAReadOnlyCosClient

    packet_bytes = arguments.packet.read_bytes()
    client = Issue68GateAReadOnlyCosClient(
        max_transfer_bytes=10_737_418_240,
    )
    repository_root = arguments.repository_root.resolve()
    evidence = execute_gate_a_preflight(
        approval=arguments.approval,
        packet_bytes=packet_bytes,
        packet_sha256=arguments.packet_sha256,
        repository_root=repository_root,
        scratch_root=arguments.scratch_root,
        input_client=client,
        engine_factory=lambda run_spec, inputs: build_reviewed_replay_engine(
            repository_root,
            run_spec,
            inputs,
        ),
    )
    evidence_path = arguments.scratch_root.resolve() / "gate-a-evidence.json"
    write_gate_a_evidence(evidence_path, evidence)
    size = len(evidence)
    digest = hashlib.sha256(evidence).hexdigest()
    sys.stdout.write(f"evidence_path={evidence_path}\nsize_bytes={size}\nsha256={digest}\n")
    return 0


__all__ = [
    "EngineFactory",
    "GATE_A_APPROVAL",
    "ReadOnlyInputClient",
    "RunnableReplay",
    "build_reviewed_replay_engine",
    "execute_gate_a_preflight",
    "execute_gate_a_sentinel",
    "run_gate_a_cli",
    "write_gate_a_evidence",
]
