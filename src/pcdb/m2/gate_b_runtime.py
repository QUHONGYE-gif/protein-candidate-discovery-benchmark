"""Concrete fail-closed runtime for the frozen Issue #58 Gate B packet.

This module deliberately implements one reviewed packet rather than a general-purpose
experiment runner.  The packet remains the authority for ordering and object identity;
``execute_gate_b_operator`` remains the only dispatcher.
"""

from __future__ import annotations

import hashlib
import os
import platform
import resource
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_high_value_result,
)
from pcdb.contracts.prediction import load_prediction_artifact, load_run_attempt_v1
from pcdb.m2.gate_b import execute_gate_b_operator, load_gate_b_packet, load_m3_freeze_record

GATE_B_PACKET_SHA256 = "f4b26934ab22b9c74feaf05f970b0ea96f529c59045703322b5f5b242d9428f9"
GATE_B_APPROVAL = "APPROVED FOR ALPHA + IRED THREE-MODEL M2 FORMAL EXECUTION"
_EVIDENCE_BUCKET = "pcdb-benchmark-artifacts-1408226911"
_EVIDENCE_PREFIX = "m2/issue-58/alpha-ired-static-v1/9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5/"


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    """Minimal object metadata needed for fresh identity checks."""

    size_bytes: int
    etag: str | None = None


class GateBObjectClient(Protocol):
    """COS capabilities exposed to the operator; deletion is intentionally absent."""

    def head(self, bucket: str, key: str) -> ObjectMetadata: ...

    def get(self, bucket: str, key: str) -> bytes: ...

    def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None: ...

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]: ...


class AggregateEvaluator(Protocol):
    """Capability-separated truth owner returning aggregate result bytes only."""

    def evaluate(self, request: object) -> Mapping[str, bytes]: ...


@dataclass(frozen=True, slots=True)
class GateBEvaluationRequest:
    """Truth-free request handed across the evaluator capability boundary."""

    task_id: str
    model_id: str
    run_id: str
    attempt_id: str
    prediction_bytes: bytes
    output_object_keys: tuple[str, ...]
    run_spec: Mapping[str, Any]
    experiment_config_bytes: bytes


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


class FrozenGateBRuntime:
    """Issue #58 runtime bound to one packet, one prefix, and one no-retry attempt."""

    def __init__(
        self,
        *,
        repository_root: Path,
        gate_a_root: Path,
        scratch_root: Path,
        objects: GateBObjectClient,
        evaluator: AggregateEvaluator,
        approval: str,
        monotonic: Any = time.monotonic,
        now: Any = lambda: datetime.now(UTC),
    ) -> None:
        self._repository_root = repository_root.resolve()
        self._gate_a_root = gate_a_root.resolve()
        self._scratch_root = scratch_root.resolve()
        self._objects = objects
        self._evaluator = evaluator
        self._approval = approval
        self._monotonic = monotonic
        self._now = now
        self._started = float(monotonic())
        self._packet: Mapping[str, Any] | None = None
        self._inventory: dict[str, Mapping[str, Any]] = {}
        self._payloads: dict[str, bytes] = {}
        self._external: dict[str, bytes] = {}
        self._written_bytes = 0
        self.evaluator_calls = 0

    def bind_reviewed_packet(self, packet: Mapping[str, Any]) -> None:
        """Bind exact reviewed bytes and prove the target prefix starts empty."""

        if self._approval != GATE_B_APPROVAL:
            raise ContractValidationError("Issue #58 formal approval is absent or drifted")
        packet_bytes = canonical_json_bytes(_plain(packet))
        if hashlib.sha256(packet_bytes).hexdigest() != GATE_B_PACKET_SHA256:
            raise ContractValidationError("Issue #58 Gate B packet identity drifted")
        if packet["evidence_store"] != {
            "bucket": _EVIDENCE_BUCKET,
            "prefix": _EVIDENCE_PREFIX,
        }:
            raise ContractValidationError("Issue #58 evidence destination drifted")
        existing = self._objects.list_keys(_EVIDENCE_BUCKET, _EVIDENCE_PREFIX)
        if existing:
            raise ContractValidationError("Issue #58 evidence prefix must be empty")
        self._packet = packet
        self._inventory = {str(item["object_key"]): item for item in packet["evidence_inventory"]}

    def _bound(self) -> Mapping[str, Any]:
        if self._packet is None:
            raise ContractValidationError("Gate B runtime is not bound to the reviewed packet")
        return self._packet

    def _check_caps(self, *, added_bytes: int | None = None) -> None:
        caps = self._bound()["execution_caps"]
        if float(self._monotonic()) - self._started > caps["max_wall_seconds"]:
            raise ContractValidationError("Gate B wall-time cap exceeded")
        if (
            added_bytes is not None
            and self._written_bytes + added_bytes > caps["cos_net_new_bytes"]
        ):
            raise ContractValidationError("Gate B COS byte cap exceeded")
        if added_bytes is not None and len(self._payloads) + 1 > caps["cos_object_count"]:
            raise ContractValidationError("Gate B COS object cap exceeded")

    def _inventory_item(self, key: object, *, kind: str | None = None) -> Mapping[str, Any]:
        if not isinstance(key, str) or not key.startswith(_EVIDENCE_PREFIX):
            raise ContractValidationError("Gate B object key escaped the reviewed prefix")
        item = self._inventory.get(key)
        if item is None:
            raise ContractValidationError("Gate B object key is absent from reviewed inventory")
        if kind is not None and item["kind"] != kind:
            raise ContractValidationError("Gate B inventory kind drifted")
        return item

    @staticmethod
    def _verify(payload: bytes, *, sha256: object, size_bytes: object, field: str) -> None:
        if not isinstance(size_bytes, int) or len(payload) != size_bytes:
            raise ContractValidationError(f"{field} size_bytes drifted")
        if not isinstance(sha256, str) or hashlib.sha256(payload).hexdigest() != sha256:
            raise ContractValidationError(f"{field} SHA-256 drifted")

    def _known_payload(self, item: Mapping[str, Any]) -> bytes:
        payload = item["payload"]
        source = payload["source"]
        kind = source["kind"]
        data: bytes
        if kind in {"gate-a-scratch", "gate-a-sealed-prediction"}:
            path = self._gate_a_root / source["logical_path"]
            data = path.read_bytes()
        elif kind == "reviewed-git":
            data = (self._repository_root / source["logical_path"]).read_bytes()
        elif kind == "reviewed-code-constant":
            from pcdb.contracts.prediction import M2_STATIC_ACQUISITION_CONFIG_BYTES

            data = M2_STATIC_ACQUISITION_CONFIG_BYTES
        elif kind == "packet-derived" and source["logical_path"] == "packet:m3_freeze_record":
            data = canonical_json_bytes(_plain(self._bound()["m3_freeze_record"]))
        elif kind == "constant-empty-bytes":
            data = b""
        else:
            raise ContractValidationError("Gate B known-payload source is unsupported")
        self._verify(
            data,
            sha256=payload["sha256"],
            size_bytes=payload["size_bytes"],
            field="Gate B known payload",
        )
        return data

    def _put(self, key: str, payload: bytes) -> None:
        self._inventory_item(key)
        if key in self._payloads:
            raise ContractValidationError("Gate B runtime attempted a duplicate publication")
        self._check_caps(added_bytes=len(payload))
        self._objects.put_forbid_overwrite(_EVIDENCE_BUCKET, key, payload)
        self._payloads[key] = payload
        self._written_bytes += len(payload)

    def _fresh(self, key: str, expected: bytes) -> bytes:
        self._inventory_item(key)
        metadata = self._objects.head(_EVIDENCE_BUCKET, key)
        if metadata.size_bytes != len(expected):
            raise ContractValidationError("Gate B fresh HEAD size drifted")
        fresh = self._objects.get(_EVIDENCE_BUCKET, key)
        self._verify(
            fresh,
            sha256=hashlib.sha256(expected).hexdigest(),
            size_bytes=len(expected),
            field="Gate B fresh readback",
        )
        return fresh

    def validate_external_manifest_reference(self, step: Mapping[str, Any]) -> None:
        self._check_caps()
        boundary = self._bound()["credential_boundary"]["accepted_inputs"]
        key = step["object_key"]
        if step["bucket"] != boundary["bucket"] or key not in boundary["exact_object_keys"]:
            raise ContractValidationError("Gate B external key escaped the accepted input set")
        metadata = self._objects.head(str(step["bucket"]), str(key))
        if metadata.size_bytes != step["size_bytes"]:
            raise ContractValidationError("external manifest HEAD size drifted")
        payload = self._objects.get(str(step["bucket"]), str(key))
        self._verify(
            payload,
            sha256=step["sha256"],
            size_bytes=step["size_bytes"],
            field="external manifest",
        )
        self._external[str(key)] = payload

    def publish_known_shared(self, step: Mapping[str, Any]) -> None:
        item = self._inventory_item(step["object_key"])
        self._put(str(step["object_key"]), self._known_payload(item))

    def validate_m3_freeze(self, step: Mapping[str, Any]) -> None:
        key = str(step["object_key"])
        self._inventory_item(key, kind="m3-freeze-record")
        load_m3_freeze_record(self._fresh(key, self._payloads[key]))

    def validate_sealed_prediction(self, step: Mapping[str, Any]) -> None:
        item = self._inventory_item(step["object_key"], kind="prediction")
        prediction = load_prediction_artifact(self._known_payload(item))
        for field in ("run_id", "attempt_id", "task_id", "model_id"):
            if prediction.value[field] != step[field]:
                raise ContractValidationError(f"sealed prediction {field} drifted")

    def publish_sealed_prediction(self, step: Mapping[str, Any]) -> None:
        item = self._inventory_item(step["object_key"], kind="prediction")
        self._put(str(step["object_key"]), self._known_payload(item))

    def readback_sealed_prediction(self, step: Mapping[str, Any]) -> None:
        key = str(step["object_key"])
        fresh = self._fresh(key, self._payloads[key])
        load_prediction_artifact(fresh)

    def _run_spec(self, run_id: str) -> Mapping[str, Any]:
        return next(
            run["run_spec_value"]
            for task in self._bound()["gate_a_known_inputs"]["value"]["tasks"]
            for run in task["runs"]
            if run["run_id"] == run_id
        )

    def _experiment_bytes(self, task_id: str) -> bytes:
        return (
            self._repository_root / f"configs/experiments/m2-static/{task_id}.json"
        ).read_bytes()

    def evaluate_four_aggregate_results(self, step: Mapping[str, Any]) -> None:
        if step["persisted_candidate_level_outputs"] != 0:
            raise ContractValidationError("candidate-level evaluator output is forbidden")
        run_id = str(step["run_id"])
        prediction_item = next(
            item
            for item in self._inventory.values()
            if item["kind"] == "prediction" and item["run_id"] == run_id
        )
        prediction_key = str(prediction_item["object_key"])
        prediction = self._fresh(prediction_key, self._payloads[prediction_key])
        request = GateBEvaluationRequest(
            task_id=str(step["task_id"]),
            model_id=str(step["model_id"]),
            run_id=run_id,
            attempt_id=str(step["attempt_id"]),
            prediction_bytes=prediction,
            output_object_keys=tuple(str(key) for key in step["output_object_keys"]),
            run_spec=self._run_spec(run_id),
            experiment_config_bytes=self._experiment_bytes(str(step["task_id"])),
        )
        self.evaluator_calls += 1
        results = self._evaluator.evaluate(request)
        if set(results) != set(request.output_object_keys) or len(results) != 4:
            raise ContractValidationError("Gate B evaluator must exact-return four metric keys")
        identities: set[tuple[str, str | None]] = set()
        for key, payload in results.items():
            if not isinstance(payload, bytes):
                raise ContractValidationError("Gate B evaluator returned non-byte output")
            item = self._inventory_item(key, kind="metric-result")
            if item["run_id"] != run_id:
                raise ContractValidationError("Gate B evaluator metric run binding drifted")
            result = load_high_value_result(
                payload, expected_schema_version="high-value-metric-result-v1"
            )
            identity = (str(result.value["metric_id"]), result.value["definition_id"])
            expected_definition = next(
                (
                    definition
                    for definition in ("upper-tail-0.5pct", "upper-tail-1pct", "upper-tail-2pct")
                    if key.endswith(f"metric-high-value-recall-{definition}.json")
                ),
                None,
            )
            expected_identity = (
                ("spearman_rank_correlation", None)
                if key.endswith("metric-spearman-rank-correlation.json")
                else ("high_value_recall_at_nominal_k", expected_definition)
            )
            if (
                identity != expected_identity
                or result.value["run_id"] != run_id
                or result.value["attempt_id"] != request.attempt_id
                or result.value["task_id"] != request.task_id
                or result.value["model_id"] != request.model_id
                or result.value["input_artifact_sha256"] != hashlib.sha256(prediction).hexdigest()
                or result.value["experiment_config_sha256"]
                != request.run_spec["experiment_config_sha256"]
                or result.value["dataset_manifest_sha256"]
                != request.run_spec["dataset_manifest_sha256"]
                or result.value["processing_manifest_sha256"]
                != request.run_spec["processing_manifest_sha256"]
                or result.value["split_sha256"] != request.run_spec["split_sha256"]
                or result.value["model_config_sha256"] != request.run_spec["model_config_sha256"]
                or result.value["acquisition_config_sha256"]
                != request.run_spec["acquisition_config_sha256"]
                or result.value["seed"] != request.run_spec["seed"]
            ):
                raise ContractValidationError("Gate B evaluator metric identity drifted")
            identities.add(identity)
            self._payloads[key] = payload
        if identities != {
            ("spearman_rank_correlation", None),
            ("high_value_recall_at_nominal_k", "upper-tail-0.5pct"),
            ("high_value_recall_at_nominal_k", "upper-tail-1pct"),
            ("high_value_recall_at_nominal_k", "upper-tail-2pct"),
        }:
            raise ContractValidationError("Gate B evaluator metric identity set drifted")

    def publish_aggregate_metric_result(self, step: Mapping[str, Any]) -> None:
        key = str(step["object_key"])
        payload = self._payloads.pop(key)
        self._put(key, payload)

    def readback_four_aggregate_results(self, step: Mapping[str, Any]) -> None:
        for key in step["object_keys"]:
            self._fresh(str(key), self._payloads[str(key)])

    def generate_run_attempt(self, step: Mapping[str, Any]) -> None:
        keys = tuple(str(key) for key in step["referenced_object_keys"])
        outputs = [
            {
                "object_key": key,
                "sha256": hashlib.sha256(self._payloads[key]).hexdigest(),
                "size_bytes": len(self._payloads[key]),
            }
            for key in keys
        ]
        now = self._now().astimezone(UTC).isoformat().replace("+00:00", "Z")
        marker_key = str(step["output_object_key"]).removesuffix("run-attempt.json") + "_SUCCESS"
        run_spec = self._run_spec(str(step["run_id"]))
        payload = canonical_json_bytes(
            {
                "schema_version": "1",
                "run_id": step["run_id"],
                "attempt_id": step["attempt_id"],
                "status": "succeeded",
                "started_at": now,
                "finished_at": now,
                "executed_git_commit": run_spec["git_commit"],
                "executed_environment_digest": run_spec["environment_digest"],
                "compute": {
                    "executor_type": "compute_server",
                    "runtime_version": f"python-{platform.python_version()}",
                    "region": "ap-beijing",
                },
                "outputs": outputs,
                "completion_marker": {
                    "object_key": marker_key,
                    "sha256": hashlib.sha256(b"").hexdigest(),
                    "size_bytes": 0,
                },
            }
        )
        load_run_attempt_v1(payload)
        self._payloads[str(step["output_object_key"])] = payload

    def publish_run_attempt(self, step: Mapping[str, Any]) -> None:
        key = str(step["object_key"])
        payload = self._payloads.pop(key)
        self._put(key, payload)

    def readback_six_payload_objects(self, step: Mapping[str, Any]) -> None:
        for key in step["object_keys"]:
            self._fresh(str(key), self._payloads[str(key)])

    def publish_empty_success_marker(self, step: Mapping[str, Any]) -> None:
        key = str(step["object_key"])
        self._inventory_item(key, kind="success-marker")
        self._put(key, b"")

    def readback_exact_keys(self, step: Mapping[str, Any]) -> None:
        required = tuple(str(key) for key in step["required_exact_keys"])
        actual = self._objects.list_keys(_EVIDENCE_BUCKET, _EVIDENCE_PREFIX)
        if len(actual) != len(set(actual)) or set(actual) != set(required):
            raise ContractValidationError("Gate B exact-cover readback drifted")
        for key in required:
            self._fresh(key, self._payloads[key])


def _enforce_process_caps(packet: Mapping[str, Any]) -> None:
    """Apply the reviewed Linux CPU/RAM caps before any COS or truth access."""

    caps = packet["execution_caps"]
    get_affinity = getattr(os, "sched_getaffinity", None)
    set_affinity = getattr(os, "sched_setaffinity", None)
    if callable(get_affinity) and callable(set_affinity):
        current = sorted(get_affinity(0))
        set_affinity(0, current[: int(caps["max_vcpu"])])
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    requested = int(caps["max_ram_bytes"])
    new_soft = requested if soft == resource.RLIM_INFINITY else min(soft, requested)
    new_hard = hard
    if hard == resource.RLIM_INFINITY or hard > requested:
        new_hard = requested
    resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard))


def run_gate_b_cli(arguments: Any) -> int:
    """Execute the unique formal entry; this function is never called by CI."""

    packet_bytes = arguments.packet.read_bytes()
    if arguments.packet_sha256 != GATE_B_PACKET_SHA256:
        raise ContractValidationError("Gate B CLI packet SHA-256 argument drifted")
    if hashlib.sha256(packet_bytes).hexdigest() != GATE_B_PACKET_SHA256:
        raise ContractValidationError("Gate B CLI packet bytes drifted")
    loaded = load_gate_b_packet(packet_bytes)
    runtime_commit = str(arguments.runtime_git_commit)
    if len(runtime_commit) != 40 or any(
        character not in "0123456789abcdef" for character in runtime_commit
    ):
        raise ContractValidationError("Gate B runtime commit argument drifted")
    try:
        checked_out_commit = subprocess.run(
            ["git", "-C", str(arguments.repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractValidationError("Gate B runtime commit could not be verified") from exc
    if checked_out_commit != runtime_commit:
        raise ContractValidationError("Gate B checkout does not match the reviewed runtime commit")
    scratch_root = arguments.scratch_root.resolve()
    if scratch_root.exists() and any(scratch_root.iterdir()):
        raise ContractValidationError("Gate B scratch root must start empty")
    scratch_root.mkdir(parents=True, exist_ok=True)
    _enforce_process_caps(loaded.value)

    from pcdb.m2.gate_b_evaluator import CosTruthBundleReader, FrozenM2AggregateEvaluator
    from pcdb.storage.cos import CosXmlObjectClient

    objects = CosXmlObjectClient()
    runtime = FrozenGateBRuntime(
        repository_root=arguments.repository_root,
        gate_a_root=arguments.gate_a_root,
        scratch_root=scratch_root,
        objects=objects,
        evaluator=FrozenM2AggregateEvaluator(CosTruthBundleReader(objects)),
        approval=arguments.approval,
    )
    execute_gate_b_operator(packet_bytes, runtime)
    return 0
