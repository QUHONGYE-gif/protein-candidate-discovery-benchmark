"""Zero-side-effect M3 review packet checks for Issue #68 Gate A."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

import pytest

from pcdb.cli import main
from pcdb.contracts import ContractValidationError, RunSpec
from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.m3.gate_a import (
    _verified_runtime_environment_digest,
    _verify_runtime_source,
    build_gate_a_packet,
    load_gate_a_packet,
    packet_identity,
    validate_gate_a_entry,
)

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_COMMIT = "a" * 40
REVIEWED_TREE = "b" * 40


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _packet() -> tuple[bytes, dict[str, Any]]:
    payload = build_gate_a_packet(ROOT, REVIEWED_COMMIT, REVIEWED_TREE)
    return payload, json.loads(payload)


def test_packet_materializes_exact_18_run_specs_and_one_lowest_cost_sentinel() -> None:
    payload, packet = _packet()

    assert canonical_json_bytes(packet) == payload
    assert packet_identity(payload) == (len(payload), hashlib.sha256(payload).hexdigest())
    assert packet["reviewed_source"] == {
        "git_commit": REVIEWED_COMMIT,
        "git_tree": REVIEWED_TREE,
    }

    runs = packet["run_specs"]
    assert isinstance(runs, list)
    assert len(runs) == 18
    combinations = set()
    for item in runs:
        assert isinstance(item, dict)
        value = item["run_spec"]
        assert isinstance(value, dict)
        run_spec = RunSpec(**value)
        assert set(value) == {field.name for field in fields(RunSpec)}
        assert item["run_id"] == run_spec.run_id
        assert run_spec.git_commit == REVIEWED_COMMIT
        assert run_spec.protocol_version == "m3-single-fidelity-replay-v1"
        assert (run_spec.initial_sample_size, run_spec.batch_size, run_spec.budget) == (
            16,
            8,
            32,
        )
        combinations.add(
            (item["task_id"], run_spec.model_id, run_spec.acquisition_id, run_spec.seed)
        )
    assert len(combinations) == 18

    sentinel = packet["sentinel"]
    assert sentinel["task_id"] == "ired"
    assert sentinel["model_id"] == "ridge-sklearn-v1"
    assert sentinel["acquisition_id"] == "random-seeded-sha256-v1"
    assert sentinel["run_id"] in {item["run_id"] for item in runs}


def test_packet_freezes_exact_read_only_inputs_and_zero_write_gate_a_policy() -> None:
    _, packet = _packet()

    inventory = packet["input_inventory"]
    assert isinstance(inventory, list)
    assert len(inventory) == 12
    exact_keys = {(item["bucket"], item["object_key"]) for item in inventory}
    assert len(exact_keys) == len(inventory)
    assert all(item["size_bytes"] > 0 for item in inventory)
    assert all(len(item["sha256"]) == 64 for item in inventory)
    assert [item["truth_bearing"] for item in inventory[-2:]] == [True, True]
    assert not any(item["truth_bearing"] for item in inventory[:-2])

    assert packet["cam_policy"] == {
        "allowed_actions": [
            "name/cos:GetBucket",
            "name/cos:GetObject",
            "name/cos:HeadObject",
        ],
        "delete_object": 0,
        "exact_object_only": True,
        "put_object": 0,
    }
    policy = packet["execution_policy"]
    assert policy["cos_puts"] == 0
    assert policy["formal_attempts"] == 0
    assert policy["automatic_retry"] == 0
    assert policy["replacement_attempts"] == 0
    assert packet["authorization_boundaries"] == {
        "gate_a_execution_authorized": False,
        "gate_b_execution_authorized": False,
        "packet_review_authorizes_candidate_truth_read": False,
        "packet_review_authorizes_cos_access": False,
        "packet_review_authorizes_server_access": False,
    }


def test_loader_rebuilds_from_reviewed_repository_bytes_and_fails_closed_on_tamper() -> None:
    payload, _ = _packet()
    digest = hashlib.sha256(payload).hexdigest()

    loaded = load_gate_a_packet(
        payload,
        expected_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    assert loaded["schema_version"] == "m3-gate-a-review-packet-v1"

    tampered = payload.replace(b'"cos_puts": 0', b'"cos_puts": 1')
    with pytest.raises(ContractValidationError, match="identity"):
        load_gate_a_packet(
            tampered,
            expected_sha256=digest,
            repository_root=ROOT,
            expected_git_commit=REVIEWED_COMMIT,
            expected_git_tree=REVIEWED_TREE,
        )


def test_entry_validation_precedes_real_input_reads_and_requires_empty_scratch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, packet = _packet()
    digest = hashlib.sha256(payload).hexdigest()
    environment = packet["environment_candidate"]
    scratch = tmp_path / "scratch"
    monkeypatch.setattr("pcdb.m3.gate_a._verify_runtime_source", lambda *_: None)
    monkeypatch.setattr(
        "pcdb.m3.gate_a._verified_runtime_environment_digest",
        lambda *_: environment["sha256"],
    )

    validated = validate_gate_a_entry(
        packet_bytes=payload,
        packet_sha256=digest,
        repository_root=ROOT,
        scratch_root=scratch,
    )
    assert validated == scratch.resolve()
    assert not scratch.exists()

    with pytest.raises(ContractValidationError, match="ignored .scratch"):
        validate_gate_a_entry(
            packet_bytes=payload,
            packet_sha256=digest,
            repository_root=ROOT,
            scratch_root=ROOT / "gate-a-output",
        )

    scratch.mkdir()
    (scratch / "unexpected").write_text("occupied", encoding="utf-8")
    with pytest.raises(ContractValidationError, match="empty"):
        validate_gate_a_entry(
            packet_bytes=payload,
            packet_sha256=digest,
            repository_root=ROOT,
            scratch_root=scratch,
        )


@pytest.mark.parametrize(
    ("head", "tree", "status", "message"),
    [
        ("c" * 40, REVIEWED_TREE, "", "commit"),
        (REVIEWED_COMMIT, "d" * 40, "", "tree"),
        (REVIEWED_COMMIT, REVIEWED_TREE, "?? unexpected.txt", "clean"),
    ],
)
def test_runtime_source_rejects_actual_checkout_drift(
    monkeypatch: pytest.MonkeyPatch,
    head: str,
    tree: str,
    status: str,
    message: str,
) -> None:
    outputs = iter((head + "\n", tree + "\n", status))
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: _Completed(next(outputs)),
    )

    with pytest.raises(ContractValidationError, match=message):
        _verify_runtime_source(
            ROOT,
            REVIEWED_COMMIT,
            REVIEWED_TREE,
            {
                "gate_a": ROOT / "src/pcdb/m3/gate_a.py",
                "replay_engine": ROOT / "src/pcdb/replay/engine.py",
                "hidden_oracle": ROOT / "src/pcdb/replay/oracle.py",
            },
        )


def test_runtime_source_accepts_exact_clean_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter((REVIEWED_COMMIT + "\n", REVIEWED_TREE + "\n", ""))
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: _Completed(next(outputs)),
    )

    _verify_runtime_source(
        ROOT,
        REVIEWED_COMMIT,
        REVIEWED_TREE,
        {
            "gate_a": ROOT / "src/pcdb/m3/gate_a.py",
            "replay_engine": ROOT / "src/pcdb/replay/engine.py",
            "hidden_oracle": ROOT / "src/pcdb/replay/oracle.py",
        },
    )


def test_runtime_source_rejects_imported_module_outside_reviewed_checkout() -> None:
    with pytest.raises(ContractValidationError, match="executing source"):
        _verify_runtime_source(
            ROOT,
            REVIEWED_COMMIT,
            REVIEWED_TREE,
            {
                "gate_a": ROOT / "src/pcdb/m3/gate_a.py",
                "replay_engine": ROOT / "src/pcdb/replay/engine.py",
                "hidden_oracle": Path("/tmp/substituted-oracle.py"),
            },
        )


def _mock_reviewed_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    path = ROOT / "configs/environments/m2-gate-a-linux-x86_64.json"
    manifest = json.loads(path.read_bytes())
    versions = {item["distribution"]: item["version"] for item in manifest["packages"]}
    monkeypatch.setattr("platform.machine", lambda: manifest["architecture"])
    monkeypatch.setattr(
        "platform.python_implementation", lambda: manifest["python"]["implementation"]
    )
    monkeypatch.setattr("platform.python_version", lambda: manifest["python"]["version"])
    monkeypatch.setattr(
        "platform.freedesktop_os_release",
        lambda: {"PRETTY_NAME": manifest["operating_system"]},
    )
    monkeypatch.setattr("importlib.metadata.version", lambda name: versions[name])
    for name, value in manifest["thread_policy"]["environment"].items():
        monkeypatch.setenv(name, value)
    return cast(dict[str, Any], manifest)


def test_runtime_environment_digest_requires_actual_reviewed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = ROOT / "configs/environments/m2-gate-a-linux-x86_64.json"
    _mock_reviewed_environment(monkeypatch)

    assert (
        _verified_runtime_environment_digest(ROOT) == hashlib.sha256(path.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("drift", ["platform", "package", "thread-policy"])
def test_runtime_environment_rejects_actual_environment_drift(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    manifest = _mock_reviewed_environment(monkeypatch)
    if drift == "platform":
        monkeypatch.setattr("platform.machine", lambda: "drifted")
        message = "platform drifted"
    elif drift == "package":
        monkeypatch.setattr("importlib.metadata.version", lambda _name: "drifted")
        message = "package version drifted"
    else:
        first_name = next(iter(manifest["thread_policy"]["environment"]))
        monkeypatch.setenv(first_name, "drifted")
        message = "thread policy drifted"

    with pytest.raises(ContractValidationError, match=message):
        _verified_runtime_environment_digest(ROOT)


def test_entry_derives_source_and_environment_instead_of_accepting_identity_strings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, packet = _packet()
    calls = {"source": 0, "environment": 0}

    def verify_source(*_: object) -> None:
        calls["source"] += 1

    def verify_environment(*_: object) -> str:
        calls["environment"] += 1
        return cast(str, packet["environment_candidate"]["sha256"])

    monkeypatch.setattr("pcdb.m3.gate_a._verify_runtime_source", verify_source)
    monkeypatch.setattr(
        "pcdb.m3.gate_a._verified_runtime_environment_digest",
        verify_environment,
    )

    validate_gate_a_entry(
        packet_bytes=payload,
        packet_sha256=hashlib.sha256(payload).hexdigest(),
        repository_root=ROOT,
        scratch_root=tmp_path / "scratch",
    )

    assert calls == {"source": 1, "environment": 1}


def test_packet_cli_prints_reproducible_review_bytes_without_writing_files(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "m3-gate-a-packet",
            "--reviewed-git-commit",
            REVIEWED_COMMIT,
            "--reviewed-git-tree",
            REVIEWED_TREE,
            "--repository-root",
            str(ROOT),
        ]
    )

    assert result == 0
    assert capsys.readouterr().out.encode() == build_gate_a_packet(
        ROOT,
        REVIEWED_COMMIT,
        REVIEWED_TREE,
    )
