from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.m2.models import model_config_bytes
from pcdb.m3.gate_a_runtime import (
    GATE_A_APPROVAL,
    execute_gate_a_preflight,
    execute_gate_a_sentinel,
    write_gate_a_evidence,
)
from pcdb.replay import ReplayEngine
from pcdb.storage.cos import (
    CosCredentials,
    Issue68GateAReadOnlyCosClient,
    Issue68GateBReadOnlyCosClient,
    Issue68GateBWriterCosClient,
)
from tests.replay.test_m3_replay import (
    _experiment_config_bytes,
    _feature_descriptor_bytes,
    _hidden_truth_bytes,
    _loaded_features,
    _processing_manifest_bytes,
    _random_run_spec,
)


class _ReadOnlyClient:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = objects
        self.calls: list[tuple[str, str, str]] = []

    def head(self, bucket: str, key: str) -> tuple[int, str]:
        self.calls.append(("HEAD", bucket, key))
        payload = self.objects[(bucket, key)]
        return len(payload), hashlib.sha256(payload).hexdigest()

    def get(self, bucket: str, key: str) -> bytes:
        self.calls.append(("GET", bucket, key))
        return self.objects[(bucket, key)]

    @property
    def transferred_bytes(self) -> int:
        return sum(
            len(self.objects[(bucket, key)])
            for method, bucket, key in self.calls
            if method == "GET"
        )


def test_gate_a_sentinel_double_run_and_recovery_are_exact_and_truth_is_read_last(
    tmp_path: Path,
) -> None:
    candidate_ids = tuple(f"{index:064x}" for index in range(1, 41))
    truth = {candidate_id: float(index) for index, candidate_id in enumerate(candidate_ids)}
    run_spec = _random_run_spec(
        candidate_ids,
        truth=truth,
        initial_sample_size=16,
        batch_size=8,
        budget=32,
    )
    features = _loaded_features(candidate_ids)
    processing = _processing_manifest_bytes(truth)
    descriptor = _feature_descriptor_bytes(
        features,
        processing_manifest_sha256=hashlib.sha256(processing).hexdigest(),
    )
    payloads = {
        "candidate-feature-matrix": features.canonical_bytes,
        "dataset-manifest": canonical_json_bytes({"fixture": "dataset"}),
        "feature-descriptor": descriptor,
        "processing-manifest": processing,
        "split-manifest": canonical_json_bytes({"fixture": "split"}),
        "hidden-truth": _hidden_truth_bytes(truth),
    }
    bucket = "fixture-bucket"
    inventory = []
    objects: dict[tuple[str, str], bytes] = {}
    for purpose, payload in payloads.items():
        key = f"fixture/{purpose}"
        objects[(bucket, key)] = payload
        inventory.append(
            {
                "bucket": bucket,
                "object_key": key,
                "purpose": purpose,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "task_id": "synthetic-toy",
                "truth_bearing": purpose == "hidden-truth",
            }
        )
    # Gate A HEADs both frozen task inventories before any body read. Only the
    # sentinel task is then fresh-GET, with hidden truth last.
    other_inventory = []
    for item in inventory:
        other = dict(item)
        other["object_key"] = str(item["object_key"]).replace("fixture/", "other/")
        other["task_id"] = "alpha-amylase"
        objects[(bucket, str(other["object_key"]))] = objects[(bucket, str(item["object_key"]))]
        other_inventory.append(other)
    inventory = other_inventory[:-1] + inventory[:-1] + [other_inventory[-1], inventory[-1]]
    packet = {
        "authorization_boundaries": {
            "gate_a_execution_authorized": False,
            "gate_b_execution_authorized": False,
            "packet_review_authorizes_candidate_truth_read": False,
            "packet_review_authorizes_cos_access": False,
            "packet_review_authorizes_server_access": False,
        },
        "execution_policy": {
            "cos_puts": 0,
            "formal_attempts": 0,
            "automatic_retry": 0,
            "manual_entry_starts": 1,
            "max_cost_cny": 50,
            "max_network_transfer_bytes": 1_000_000,
            "max_ram_bytes": 1_000_000_000_000,
            "max_scratch_bytes": 1_000_000,
            "max_vcpu": 1024,
            "max_wall_seconds": 60,
            "overwrite": 0,
            "replacement_attempts": 0,
            "required_checks": [
                "same-runspec-double-run",
                "interrupted-then-recovered",
                "no-repeat-purchase",
                "budget-monotonic",
                "four-decision-metrics",
                "resource-caps",
            ],
        },
        "input_inventory": inventory,
        "reviewed_source": {"git_commit": run_spec.git_commit, "git_tree": "a" * 40},
        "run_specs": [
            {
                "run_id": run_spec.run_id,
                "run_spec": asdict(run_spec),
                "task_id": "synthetic-toy",
            }
        ],
        "schema_version": "m3-gate-a-review-packet-v1",
        "sentinel": {
            "acquisition_id": run_spec.acquisition_id,
            "model_id": run_spec.model_id,
            "run_id": run_spec.run_id,
            "task_id": "synthetic-toy",
        },
    }
    client = _ReadOnlyClient(objects)

    def engine_factory(received_run_spec: object, received: dict[str, bytes]) -> ReplayEngine:
        assert received_run_spec == run_spec
        assert received == payloads
        return ReplayEngine.from_reviewed_inputs(
            run_spec=run_spec,
            experiment_config=_experiment_config_bytes(
                processing_manifest_sha256=hashlib.sha256(processing).hexdigest(),
                initial_sample_size=16,
                batch_size=8,
                budget=32,
            ),
            candidate_features=features,
            feature_descriptor=received["feature-descriptor"],
            processing_manifest=received["processing-manifest"],
            hidden_truth=received["hidden-truth"],
            task_id="synthetic-toy",
            model_config=model_config_bytes(run_spec.model_id),
        )

    evidence_bytes = execute_gate_a_sentinel(
        packet,
        input_client=client,
        engine_factory=engine_factory,
        scratch_root=tmp_path / "gate-a-scratch",
    )
    evidence = json.loads(evidence_bytes)

    assert evidence["schema_version"] == "m3-gate-a-evidence-v1"
    assert evidence["verdict"] == "INCONCLUSIVE"
    assert evidence["checks"]["resource-caps"] == "INCONCLUSIVE"
    assert evidence["checks"]["same-runspec-double-run"] == "PASS"
    assert evidence["run_id"] == run_spec.run_id
    assert evidence["trajectory_sha256"]["first"] == evidence["trajectory_sha256"]["second"]
    assert evidence["trajectory_sha256"]["first"] == evidence["trajectory_sha256"]["recovered"]
    assert evidence["distinct_reveal_boundaries"] == [16, 24, 32]
    assert evidence["cos_puts"] == 0
    assert evidence["resource_observations"]["scratch_bytes"] > 0
    assert evidence["resource_observations"]["cost_cny"] == {
        "reviewed_ceiling": 50,
        "status": "requires-independent-operator-billing-check",
    }
    checkpoint = tmp_path / "gate-a-scratch" / "interrupted-checkpoint.json"
    assert checkpoint.is_file()
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == evidence["checkpoint_sha256"]
    assert evidence["resource_observations"]["network_transfer_bytes"] == sum(
        len(value) for value in payloads.values()
    )
    assert canonical_json_bytes(evidence) == evidence_bytes
    assert [call[0] for call in client.calls[:12]] == ["HEAD"] * 12
    assert [call[0] for call in client.calls[12:]] == ["GET"] * 6
    assert client.calls[-1][2].endswith("hidden-truth")


def test_gate_a_public_entry_fails_before_any_remote_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _ReadOnlyClient({})
    packet_bytes = canonical_json_bytes({"schema_version": "m3-gate-a-review-packet-v1"})

    def reject_entry(**_: object) -> Path:
        raise ContractValidationError("source drift")

    monkeypatch.setattr("pcdb.m3.gate_a_runtime.validate_gate_a_entry", reject_entry)

    with pytest.raises(ContractValidationError, match="source drift"):
        execute_gate_a_preflight(
            approval=GATE_A_APPROVAL,
            packet_bytes=packet_bytes,
            packet_sha256=hashlib.sha256(packet_bytes).hexdigest(),
            repository_root=tmp_path,
            scratch_root=tmp_path / "scratch",
            input_client=client,
            engine_factory=lambda _run_spec, _inputs: pytest.fail("engine must not start"),
        )

    assert client.calls == []


def test_gate_a_public_entry_requires_exact_external_approval_before_attestation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _ReadOnlyClient({})
    packet_bytes = canonical_json_bytes({"schema_version": "m3-gate-a-review-packet-v1"})
    entry_calls = 0

    def attest(**_: object) -> Path:
        nonlocal entry_calls
        entry_calls += 1
        return tmp_path / "scratch"

    monkeypatch.setattr("pcdb.m3.gate_a_runtime.validate_gate_a_entry", attest)

    with pytest.raises(ContractValidationError, match="Gate A approval"):
        execute_gate_a_preflight(
            approval="not-approved",
            packet_bytes=packet_bytes,
            packet_sha256=hashlib.sha256(packet_bytes).hexdigest(),
            repository_root=tmp_path,
            scratch_root=tmp_path / "scratch",
            input_client=client,
            engine_factory=lambda _run_spec, _inputs: pytest.fail("engine must not start"),
        )

    assert entry_calls == 0
    assert client.calls == []


def test_gate_a_evidence_is_created_exclusively_after_success(tmp_path: Path) -> None:
    target = tmp_path / "gate-a-evidence.json"
    payload = canonical_json_bytes({"schema_version": "m3-gate-a-evidence-v1"})

    write_gate_a_evidence(target, payload)

    assert target.read_bytes() == payload
    with pytest.raises(FileExistsError):
        write_gate_a_evidence(target, b"replacement")
    assert target.read_bytes() == payload


def test_gate_a_cos_capability_exposes_no_write_or_list_methods() -> None:
    client = Issue68GateAReadOnlyCosClient(
        credentials=lambda: CosCredentials("id", "key", "token"),
    )

    assert callable(client.head)
    assert callable(client.get)
    assert not hasattr(client, "put_forbid_overwrite")
    assert not hasattr(client, "list_keys")


def test_gate_b_cos_capabilities_separate_writer_and_independent_reader() -> None:
    def credentials() -> CosCredentials:
        return CosCredentials("id", "key", "token")

    writer = Issue68GateBWriterCosClient(credentials=credentials)
    reader = Issue68GateBReadOnlyCosClient(credentials=credentials)

    assert all(
        callable(getattr(writer, method))
        for method in ("head", "get", "list_keys", "put_forbid_overwrite")
    )
    assert all(callable(getattr(reader, method)) for method in ("head", "get", "list_keys"))
    assert not hasattr(reader, "put_forbid_overwrite")
