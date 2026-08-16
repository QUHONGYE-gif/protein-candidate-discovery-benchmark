"""Zero-truth M4 input inventory and execution-adapter boundary checks."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.m4.execution_adapter import (
    InputObjectIdentity,
    TaskInputInventory,
    _build_replay_engine_from_validated_preparation,
    build_reviewed_replay_engine,
    prepare_execution_adapter,
)
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet
from pcdb.replay import ReplayEngine

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
INVENTORY_SIZE_BYTES = 4_845
INVENTORY_SHA256 = "56058b9f6cfa22a8c249931a8af2c7371cd4b597620df6c0789a53984d55c252"


class _BodyReadSpy(Mapping[str, bytes]):
    def __init__(self, values: Mapping[str, bytes]) -> None:
        self._values = dict(values)
        self.reads: list[str] = []

    def __getitem__(self, key: str) -> bytes:
        self.reads.append(key)
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


def _packet() -> tuple[bytes, str]:
    payload = build_pilot_preflight_packet(ROOT, REVIEWED_COMMIT, REVIEWED_TREE)
    return payload, hashlib.sha256(payload).hexdigest()


def _synthetic_inventory(task_id: str, values: Mapping[str, bytes]) -> TaskInputInventory:
    return TaskInputInventory(
        task_id=task_id,
        objects=tuple(
            InputObjectIdentity(
                bucket="synthetic-metadata-only",
                object_key=f"synthetic/{task_id}/{purpose}",
                purpose=purpose,
                sha256=hashlib.sha256(values[purpose]).hexdigest(),
                size_bytes=len(values[purpose]),
                task_id=task_id,
                truth_bearing=purpose == "hidden-truth",
            )
            for purpose in PURPOSES
        ),
    )


def test_preparation_binds_exact_inventory_runs_and_zero_authority() -> None:
    packet, digest = _packet()

    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )

    assert preparation.reviewed_git_commit == REVIEWED_COMMIT
    assert preparation.reviewed_git_tree == REVIEWED_TREE
    assert preparation.packet_sha256 == digest
    assert preparation.oracle_code_path == (
        "pcdb.m4.execution_adapter.build_reviewed_replay_engine"
        " -> pcdb.replay.engine.ReplayEngine.from_reviewed_inputs"
        " -> pcdb.replay.oracle.HiddenOracle"
    )
    assert preparation.candidate_body_reads_authorized is False
    assert preparation.oracle_calls_authorized == 0
    assert preparation.attempts_authorized == 0
    assert preparation.writes_authorized == 0
    assert preparation.inventory_size_bytes == INVENTORY_SIZE_BYTES
    assert preparation.inventory_sha256 == INVENTORY_SHA256
    assert len(preparation.runs) == 72
    assert len({item.run_spec.run_id for item in preparation.runs}) == 72
    assert [item.task_id for item in preparation.tasks] == ["alpha-amylase", "ired"]
    assert [
        sum(item.task_id == task.task_id for item in preparation.runs) for task in preparation.tasks
    ] == [
        36,
        36,
    ]

    objects = [item for task in preparation.tasks for item in task.objects]
    assert len(objects) == 12
    assert len({(item.bucket, item.object_key) for item in objects}) == 12
    assert all(item.size_bytes > 0 and len(item.sha256) == 64 for item in objects)
    for task in preparation.tasks:
        assert tuple(item.purpose for item in task.objects) == PURPOSES
        assert [item.truth_bearing for item in task.objects] == [
            False,
            False,
            False,
            False,
            False,
            True,
        ]
        task_runs = [item.run_spec for item in preparation.runs if item.task_id == task.task_id]
        identities = {item.purpose: item.sha256 for item in task.objects}
        assert {item.dataset_manifest_sha256 for item in task_runs} == {
            identities["dataset-manifest"]
        }
        assert {item.processing_manifest_sha256 for item in task_runs} == {
            identities["processing-manifest"]
        }
        assert {item.split_sha256 for item in task_runs} == {identities["split-manifest"]}
        assert {item.feature_sha256 for item in task_runs} == {identities["feature-descriptor"]}


def test_preparation_reads_only_exact_tracked_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet, digest = _packet()
    reads: list[Path] = []
    original = Path.read_bytes

    def record(path: Path) -> bytes:
        reads.append(path.resolve())
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", record)
    prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )

    assert {path.relative_to(ROOT).as_posix() for path in reads} == {
        "configs/acquisitions/m3/greedy-predictive-mean-v1.json",
        "configs/acquisitions/m3/random-seeded-sha256-v1.json",
        "configs/acquisitions/m3/uncertainty-bootstrap-std-v1.json",
        "configs/acquisitions/m4/ucb-bootstrap-std-beta1-v1.json",
        "configs/environments/m2-gate-a-linux-x86_64.json",
        "configs/experiments/m4-single-fidelity/alpha-amylase.json",
        "configs/experiments/m4-single-fidelity/ired.json",
        "manifests/experiments/m2/issue-58/gate-b-packet.v1.json",
        "manifests/experiments/m3/issue-66/protocol-freeze.v1.json",
        "manifests/experiments/m3/issue-66/run-spec-matrix.v1.json",
    }
    assert not any("hidden-truth" in path.name for path in reads)


def test_preparation_never_writes_or_starts_an_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet, digest = _packet()

    def reject(*_: object, **__: object) -> Any:
        raise AssertionError("zero-truth preparation attempted a write")

    monkeypatch.setattr(Path, "write_bytes", reject)
    monkeypatch.setattr(Path, "write_text", reject)
    monkeypatch.setattr(Path, "mkdir", reject)
    monkeypatch.setattr(Path, "touch", reject)

    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )

    assert preparation.attempts_authorized == 0
    assert preparation.writes_authorized == 0


def test_packet_rejection_precedes_input_inventory_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet, digest = _packet()
    tampered = packet.replace(
        b'"pilot_execution_authorized": false',
        b'"pilot_execution_authorized": true',
    )

    def reject(*_: object, **__: object) -> Any:
        raise AssertionError("input inventory resolved before packet identity")

    monkeypatch.setattr(
        "pcdb.m4.execution_adapter.reviewed_input_inventory_metadata",
        reject,
    )

    with pytest.raises(ContractValidationError, match="identity"):
        prepare_execution_adapter(
            packet_bytes=tampered,
            packet_sha256=digest,
            repository_root=ROOT,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
        )


def test_reviewed_builder_rejects_forged_preparation_before_body_read() -> None:
    packet, digest = _packet()
    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    first_task = preparation.tasks[0]
    first_object = first_task.objects[0]
    first_run = preparation.runs[0]
    forged = {
        "object-key": replace(
            preparation,
            tasks=(
                replace(
                    first_task,
                    objects=(
                        replace(first_object, object_key=f"{first_object.object_key}.forged"),
                        *first_task.objects[1:],
                    ),
                ),
                *preparation.tasks[1:],
            ),
        ),
        "task": replace(
            preparation,
            tasks=(replace(first_task, task_id="ired"), *preparation.tasks[1:]),
        ),
        "run": replace(
            preparation,
            runs=(replace(first_run, task_id="ired"), *preparation.runs[1:]),
        ),
        "packet-digest": replace(preparation, packet_sha256="0" * 64),
        "reviewed-commit": replace(preparation, reviewed_git_commit="0" * 40),
        "reviewed-tree": replace(preparation, reviewed_git_tree="0" * 40),
        "metadata-paths": replace(
            preparation,
            metadata_paths=(*preparation.metadata_paths, "forged-metadata-path"),
        ),
        "inventory-size": replace(
            preparation,
            inventory_size_bytes=preparation.inventory_size_bytes + 1,
        ),
        "inventory-digest": replace(preparation, inventory_sha256="0" * 64),
        "oracle-path": replace(preparation, oracle_code_path="forged-oracle-path"),
        "body-authority": replace(preparation, candidate_body_reads_authorized=True),
        "oracle-authority": replace(preparation, oracle_calls_authorized=1),
        "attempt-authority": replace(preparation, attempts_authorized=1),
        "write-authority": replace(preparation, writes_authorized=1),
    }

    for field, candidate in forged.items():
        inputs = _BodyReadSpy({purpose: b"must-not-read" for purpose in PURPOSES})
        with pytest.raises(
            ContractValidationError,
            match="differs from the reviewed packet",
        ):
            build_reviewed_replay_engine(
                preparation=candidate,
                packet_bytes=packet,
                packet_sha256=digest,
                expected_git_commit=REVIEWED_COMMIT,
                expected_git_tree=REVIEWED_TREE,
                run_spec=first_run.run_spec,
                inputs=inputs,
                repository_root=ROOT,
            )
        assert inputs.reads == [], field


def test_reviewed_builder_rebinds_canonical_preparation_before_delegating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet, digest = _packet()
    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    inputs = _BodyReadSpy({})
    sentinel_engine = object()
    calls: list[dict[str, object]] = []

    def capture(**kwargs: object) -> object:
        calls.append(kwargs)
        return sentinel_engine

    monkeypatch.setattr(
        "pcdb.m4.execution_adapter._build_replay_engine_from_validated_preparation",
        capture,
    )

    result = build_reviewed_replay_engine(
        preparation=preparation,
        packet_bytes=packet,
        packet_sha256=digest,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
        run_spec=preparation.runs[0].run_spec,
        inputs=inputs,
        repository_root=ROOT,
    )

    assert result is sentinel_engine
    assert len(calls) == 1
    assert calls[0]["preparation"] == preparation
    assert calls[0]["run_spec"] == preparation.runs[0].run_spec
    assert calls[0]["inputs"] is inputs
    assert inputs.reads == []


def test_adapter_rejects_nontruth_drift_before_hidden_truth_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet, digest = _packet()
    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    prepared_run = preparation.runs[0]
    values = {purpose: f"synthetic-{purpose}".encode() for purpose in PURPOSES}
    synthetic_task = _synthetic_inventory(prepared_run.task_id, values)
    preparation = replace(
        preparation,
        tasks=(synthetic_task,),
        runs=(prepared_run,),
    )
    drifted = dict(values)
    drifted["feature-descriptor"] += b"-drift"
    inputs = _BodyReadSpy(drifted)

    def reject(*_: object, **__: object) -> Any:
        raise AssertionError("truth owner reached after nontruth drift")

    monkeypatch.setattr(ReplayEngine, "from_reviewed_inputs", classmethod(reject))

    with pytest.raises(ContractValidationError, match="input identity"):
        _build_replay_engine_from_validated_preparation(
            preparation=preparation,
            run_spec=prepared_run.run_spec,
            inputs=inputs,
            repository_root=ROOT,
        )

    assert "hidden-truth" not in inputs.reads


def test_adapter_delegates_once_to_unique_truth_owner_with_truth_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet, digest = _packet()
    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    prepared_run = preparation.runs[0]
    values = {purpose: f"synthetic-{purpose}".encode() for purpose in PURPOSES}
    preparation = replace(
        preparation,
        tasks=(_synthetic_inventory(prepared_run.task_id, values),),
        runs=(prepared_run,),
    )
    inputs = _BodyReadSpy(values)
    loaded_features = object()
    sentinel_engine = object()
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "pcdb.m4.execution_adapter.load_feature_matrix",
        lambda *_args, **_kwargs: loaded_features,
    )

    def capture(_cls: type[ReplayEngine], /, **kwargs: object) -> object:
        calls.append(kwargs)
        return sentinel_engine

    monkeypatch.setattr(ReplayEngine, "from_reviewed_inputs", classmethod(capture))

    result = _build_replay_engine_from_validated_preparation(
        preparation=preparation,
        run_spec=prepared_run.run_spec,
        inputs=inputs,
        repository_root=ROOT,
    )

    assert result is sentinel_engine
    assert inputs.reads[-1] == "hidden-truth"
    assert len(calls) == 1
    assert calls[0]["run_spec"] == prepared_run.run_spec
    assert calls[0]["task_id"] == prepared_run.task_id
    assert calls[0]["candidate_features"] is loaded_features
    assert calls[0]["hidden_truth"] == values["hidden-truth"]
