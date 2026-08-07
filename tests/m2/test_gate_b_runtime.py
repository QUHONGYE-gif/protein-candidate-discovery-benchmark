"""Concrete, fail-closed Issue #58 Gate B runtime tests."""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from pcdb.cli import build_parser
from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_high_value_result,
)
from pcdb.m2.gate_b import load_gate_b_packet
from pcdb.m2.gate_b_evaluator import FrozenM2AggregateEvaluator, TruthBundle
from pcdb.m2.gate_b_runtime import (
    GATE_B_APPROVAL,
    GATE_B_PACKET_SHA256,
    FrozenGateBRuntime,
    GateBEvaluationRequest,
    ObjectMetadata,
)
from pcdb.storage.cos import CosCredentials, CosXmlObjectClient, sign_cos_request

ROOT = Path(__file__).resolve().parents[2]
PACKET = ROOT / "manifests/experiments/m2/issue-58/gate-b-packet.v1.json"


class _FakeObjects:
    def __init__(
        self,
        *,
        keys: tuple[str, ...] = (),
        payloads: dict[tuple[str, str], bytes] | None = None,
    ) -> None:
        self.keys = list(keys)
        self.payloads = payloads or {}
        self.reads = 0
        self.writes = 0

    def head(self, bucket: str, key: str) -> ObjectMetadata:
        self.reads += 1
        return ObjectMetadata(size_bytes=len(self.payloads[(bucket, key)]))

    def get(self, bucket: str, key: str) -> bytes:
        self.reads += 1
        return self.payloads[(bucket, key)]

    def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None:
        self.writes += 1
        if (bucket, key) in self.payloads:
            raise AssertionError("overwrite attempted")
        self.payloads[(bucket, key)] = payload
        self.keys.append(key)

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]:
        return tuple(key for key in self.keys if key.startswith(prefix))


class _NeverEvaluator:
    calls = 0

    def evaluate(self, request: object) -> dict[str, bytes]:
        self.calls += 1
        raise AssertionError("truth evaluator must not be reached")


class _MetricDriftEvaluator:
    def __init__(self, result_count: int) -> None:
        self.result_count = result_count

    def evaluate(self, request: object) -> dict[str, bytes]:
        keys = cast(Any, request).output_object_keys
        result = {key: b"{}" for key in keys[: self.result_count]}
        if self.result_count > 4:
            result["outside-prefix/extra.json"] = b"{}"
        return result


def _runtime(objects: _FakeObjects) -> FrozenGateBRuntime:
    return FrozenGateBRuntime(
        repository_root=ROOT,
        gate_a_root=ROOT / "not-used",
        scratch_root=ROOT / ".gate-b-test-not-created",
        objects=objects,
        evaluator=_NeverEvaluator(),
        approval=GATE_B_APPROVAL,
    )


def test_exact_packet_identity_is_frozen() -> None:
    payload = PACKET.read_bytes()

    assert hashlib.sha256(payload).hexdigest() == GATE_B_PACKET_SHA256
    assert load_gate_b_packet(payload).canonical_bytes == payload


def test_runtime_rejects_wrong_approval_before_cos_or_truth() -> None:
    objects = _FakeObjects()
    runtime = FrozenGateBRuntime(
        repository_root=ROOT,
        gate_a_root=ROOT / "not-used",
        scratch_root=ROOT / ".gate-b-test-not-created",
        objects=objects,
        evaluator=_NeverEvaluator(),
        approval="not-approved",
    )

    with pytest.raises(ContractValidationError, match="formal approval"):
        runtime.bind_reviewed_packet(load_gate_b_packet(PACKET.read_bytes()).value)

    assert objects.reads == 0
    assert objects.writes == 0


def test_runtime_rejects_nonempty_exact_prefix_before_cos_writes_or_truth() -> None:
    packet = load_gate_b_packet(PACKET.read_bytes()).value
    key = packet["evidence_inventory"][0]["object_key"]
    objects = _FakeObjects(keys=(key,))
    runtime = _runtime(objects)

    with pytest.raises(ContractValidationError, match="must be empty"):
        runtime.bind_reviewed_packet(packet)

    assert objects.reads == 0
    assert objects.writes == 0
    assert runtime.evaluator_calls == 0


def test_runtime_rejects_wrong_external_key_before_read_write_or_truth() -> None:
    objects = _FakeObjects()
    runtime = _runtime(objects)
    runtime.bind_reviewed_packet(load_gate_b_packet(PACKET.read_bytes()).value)

    with pytest.raises(ContractValidationError, match="accepted input set"):
        runtime.validate_external_manifest_reference(
            {
                "bucket": "pcdb-public-data-1408226911",
                "object_key": "manifests/not-reviewed.json",
                "sha256": "0" * 64,
                "size_bytes": 0,
            }
        )

    assert objects.reads == 0
    assert objects.writes == 0
    assert runtime.evaluator_calls == 0


def test_runtime_rejects_output_prefix_escape_before_read_write_or_truth() -> None:
    objects = _FakeObjects()
    runtime = _runtime(objects)
    runtime.bind_reviewed_packet(load_gate_b_packet(PACKET.read_bytes()).value)

    with pytest.raises(ContractValidationError, match="escaped the reviewed prefix"):
        runtime.publish_known_shared({"object_key": "m2/issue-58/not-reviewed.json"})

    assert objects.reads == 0
    assert objects.writes == 0
    assert runtime.evaluator_calls == 0


def test_partial_checkpoint_inventory_fails_closed() -> None:
    packet = load_gate_b_packet(PACKET.read_bytes()).value
    checkpoint = next(
        step
        for step in packet["operator_plan"]["steps"]
        if step["operation"] == "independent-fresh-readback-exact-cover-alpha-ridge-checkpoint"
    )
    objects = _FakeObjects()
    runtime = _runtime(objects)
    runtime.bind_reviewed_packet(packet)

    with pytest.raises(ContractValidationError, match="exact-cover"):
        runtime.readback_exact_keys(checkpoint)

    assert objects.writes == 0
    assert runtime.evaluator_calls == 0


def test_cos_signature_binds_forbid_overwrite_and_temporary_token() -> None:
    credentials = CosCredentials(
        secret_id="AKIDEXAMPLE",
        secret_key="secret-example",
        token="temporary-token",
    )

    headers = sign_cos_request(
        method="PUT",
        path="/evidence/a.json",
        query={},
        headers={"host": "bucket.cos.ap-beijing.myqcloud.com", "x-cos-forbid-overwrite": "true"},
        credentials=credentials,
        start_time=1_700_000_000,
        expires_seconds=600,
    )

    assert headers["x-cos-security-token"] == "temporary-token"
    assert "x-cos-forbid-overwrite" in headers["authorization"]
    assert credentials.secret_key not in str(headers)
    assert headers["authorization"].startswith("q-sign-algorithm=sha1&q-ak=AKIDEXAMPLE")


def test_cos_client_uses_single_no_retry_forbid_overwrite_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []

    class Response:
        headers = {"content-length": "0"}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b""

    class Opener:
        def open(self, request: Any, timeout: int) -> Response:
            requests.append(request)
            assert timeout == 60
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *_: Opener())
    credentials = CosCredentials("AKIDEXAMPLE", "secret-example", "temporary-token")
    client = CosXmlObjectClient(credentials=lambda: credentials)

    client.put_forbid_overwrite("bucket-1408226911", "exact/prefix/result.json", b"payload")

    assert len(requests) == 1
    request = requests[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.method == "PUT"
    assert request.data == b"payload"
    assert headers["x-cos-forbid-overwrite"] == "true"
    assert headers["x-cos-security-token"] == "temporary-token"
    assert "x-cos-security-token" in headers["authorization"]


def test_cli_exposes_one_explicit_gate_b_entry() -> None:
    arguments = build_parser().parse_args(
        [
            "m2-gate-b",
            "--packet",
            str(PACKET),
            "--packet-sha256",
            GATE_B_PACKET_SHA256,
            "--approval",
            GATE_B_APPROVAL,
            "--runtime-git-commit",
            "a" * 40,
            "--gate-a-root",
            "/gate-a",
            "--repository-root",
            str(ROOT),
            "--scratch-root",
            "/scratch",
        ]
    )

    assert arguments.command == "m2-gate-b"
    assert arguments.packet_sha256 == GATE_B_PACKET_SHA256


@pytest.mark.parametrize("result_count", [3, 5])
def test_metric_count_drift_stops_before_publication(result_count: int) -> None:
    packet = load_gate_b_packet(PACKET.read_bytes()).value
    step = next(
        item
        for item in packet["operator_plan"]["steps"]
        if item["operation"] == "evaluator-read-candidate-truth-return-four-aggregate-results"
    )
    prediction = next(
        item
        for item in packet["evidence_inventory"]
        if item["kind"] == "prediction" and item["run_id"] == step["run_id"]
    )
    key = prediction["object_key"]
    objects = _FakeObjects(payloads={("pcdb-benchmark-artifacts-1408226911", key): b"sealed"})
    runtime = FrozenGateBRuntime(
        repository_root=ROOT,
        gate_a_root=ROOT / "not-used",
        scratch_root=ROOT / ".gate-b-test-not-created",
        objects=objects,
        evaluator=_MetricDriftEvaluator(result_count),
        approval=GATE_B_APPROVAL,
    )
    runtime.bind_reviewed_packet(packet)
    runtime._payloads[key] = b"sealed"  # exercise the post-publication evaluator boundary

    with pytest.raises(ContractValidationError, match="exact-return four"):
        runtime.evaluate_four_aggregate_results(step)

    assert runtime.evaluator_calls == 1
    assert objects.writes == 0


def test_wall_cap_drift_stops_before_external_read_write_or_truth() -> None:
    values = iter((0.0, 14_401.0))
    objects = _FakeObjects()
    runtime = FrozenGateBRuntime(
        repository_root=ROOT,
        gate_a_root=ROOT / "not-used",
        scratch_root=ROOT / ".gate-b-test-not-created",
        objects=objects,
        evaluator=_NeverEvaluator(),
        approval=GATE_B_APPROVAL,
        monotonic=lambda: next(values),
    )
    packet = load_gate_b_packet(PACKET.read_bytes()).value
    runtime.bind_reviewed_packet(packet)
    step = packet["operator_plan"]["steps"][0]

    with pytest.raises(ContractValidationError, match="wall-time cap"):
        runtime.validate_external_manifest_reference(step)

    assert objects.reads == 0
    assert objects.writes == 0
    assert runtime.evaluator_calls == 0


def test_forbid_overwrite_failure_stops_before_evaluator() -> None:
    packet = load_gate_b_packet(PACKET.read_bytes()).value
    step = next(
        item
        for item in packet["operator_plan"]["steps"]
        if item["operation"] == "forbid-overwrite-put-known-shared-payload"
        and "shared/configs/experiments/alpha-amylase.json" in item["object_key"]
    )

    class ExistingObjects(_FakeObjects):
        def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None:
            self.writes += 1
            raise FileExistsError("already exists")

    objects = ExistingObjects()
    runtime = _runtime(objects)
    runtime.bind_reviewed_packet(packet)

    with pytest.raises(FileExistsError, match="already exists"):
        runtime.publish_known_shared(step)

    assert objects.writes == 1
    assert runtime.evaluator_calls == 0


def test_concrete_evaluator_returns_only_four_aggregate_artifacts() -> None:
    packet = load_gate_b_packet(PACKET.read_bytes()).value
    step = next(
        item
        for item in packet["operator_plan"]["steps"]
        if item["operation"] == "evaluator-read-candidate-truth-return-four-aggregate-results"
    )
    run_spec = next(
        run["run_spec_value"]
        for task in packet["gate_a_known_inputs"]["value"]["tasks"]
        for run in task["runs"]
        if run["run_id"] == step["run_id"]
    )
    candidate_ids = sorted(
        hashlib.sha256(f"candidate-{index}".encode()).hexdigest() for index in range(2262)
    )
    experiment_bytes = (ROOT / "configs/experiments/m2-static/alpha-amylase.json").read_bytes()
    prediction_bytes = canonical_json_bytes(
        {
            "schema_version": "m2-prediction-artifact-v1",
            "protocol_version": run_spec["protocol_version"],
            "experiment_config_sha256": run_spec["experiment_config_sha256"],
            "dataset_manifest_sha256": run_spec["dataset_manifest_sha256"],
            "processing_manifest_sha256": run_spec["processing_manifest_sha256"],
            "split_sha256": run_spec["split_sha256"],
            "feature_sha256": run_spec["feature_sha256"],
            "run_id": step["run_id"],
            "attempt_id": step["attempt_id"],
            "task_id": step["task_id"],
            "candidate_role": "candidate-pool",
            "candidate_count": len(candidate_ids),
            "model_id": step["model_id"],
            "model_config_sha256": run_spec["model_config_sha256"],
            "acquisition_id": run_spec["acquisition_id"],
            "acquisition_config_sha256": run_spec["acquisition_config_sha256"],
            "seed": run_spec["seed"],
            "predictions": [
                {"candidate_id": candidate, "prediction": struct.pack(">d", float(index)).hex()}
                for index, candidate in enumerate(candidate_ids)
            ],
        }
    )

    class FakeTruthReader:
        def read(self, task_id: str, spec: Mapping[str, Any]) -> TruthBundle:
            assert task_id == "alpha-amylase"
            assert spec == run_spec
            return TruthBundle(
                candidate_ids=frozenset(candidate_ids),
                truth_rows=tuple(
                    (candidate, float(index)) for index, candidate in enumerate(candidate_ids)
                ),
            )

    results = FrozenM2AggregateEvaluator(FakeTruthReader()).evaluate(
        GateBEvaluationRequest(
            task_id=step["task_id"],
            model_id=step["model_id"],
            run_id=step["run_id"],
            attempt_id=step["attempt_id"],
            prediction_bytes=prediction_bytes,
            output_object_keys=tuple(step["output_object_keys"]),
            run_spec=run_spec,
            experiment_config_bytes=experiment_bytes,
        )
    )

    assert set(results) == set(step["output_object_keys"])
    assert len(results) == 4
    for payload in results.values():
        loaded = load_high_value_result(
            payload, expected_schema_version="high-value-metric-result-v1"
        )
        assert "candidate_ids" not in loaded.value
        assert "high_value_membership" not in json.loads(payload)
