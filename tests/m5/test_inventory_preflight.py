"""Outcome-free readiness checks for the M5B 80-state inventory preflight."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.contracts.run import RunSpec
from pcdb.m4.execution_adapter import PreparedRun
from pcdb.m5.formal_reconstruction import FormalTrajectory
from pcdb.m5.inventory_preflight import (
    ACCEPTED_M5A_INVENTORY_SHA256,
    ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
    extract_first_decision_state_payloads,
)
from pcdb.m5.iteration import (
    DevelopmentTruth,
    build_iteration_report,
    evaluate_iteration,
    load_development_truth,
    load_iteration_authorization,
)
from pcdb.m5.reconstruction_smoke import compact_json_bytes
from pcdb.m5.same_state import build_state_inventory
from pcdb.replay.engine import ImmediateBatchEvaluator, ReplayEvent, ReplayTrajectory
from pcdb.replay.oracle import Observation

_TASK_BINDINGS = {
    "alpha-amylase": {
        "candidate_count": 2262,
        "dataset_manifest_sha256": (
            "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac"
        ),
        "experiment_config_sha256": (
            "193f8ee0102a654e63cd7ea31fc77a93bcf7f5fae0c0305570117eac54c097aa"
        ),
        "feature_sha256": "9a89f12e7c521462a412320937f4455200c75282fae8196d1302b0eac0bf5596",
        "processing_manifest_sha256": (
            "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec"
        ),
        "split_sha256": "c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a",
    },
    "ired": {
        "candidate_count": 2070,
        "dataset_manifest_sha256": (
            "37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643"
        ),
        "experiment_config_sha256": (
            "7bfdc48ca2f4d41f039b8f119c3dca5910e9eb26c156cdee53f584b4af7c38fa"
        ),
        "feature_sha256": "1573d70651f2f6450bc354960b170dff2b8b19fd4766f1e0fc6a9622f4e6cb6b",
        "processing_manifest_sha256": (
            "929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba"
        ),
        "split_sha256": "9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e",
    },
}
_MODEL_BINDINGS = {
    "random-forest-sklearn-v1": {
        "exploration_acquisition_config_sha256": (
            "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6"
        ),
        "exploration_acquisition_id": "uncertainty-bootstrap-std-v1",
        "model_config_sha256": ("418b0fd6189fd9b3987620b05021e5ec5ff3018c074024cd13874cd3b9845312"),
    },
    "xgboost-cpu-v1": {
        "exploration_acquisition_config_sha256": (
            "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d"
        ),
        "exploration_acquisition_id": "ucb-bootstrap-std-beta1-v1",
        "model_config_sha256": ("31eb5bc146f4e810027de8870fa5632e09a5427bd8dddfee9261572c1e3473f2"),
    },
}
_GREEDY_CONFIG_SHA256 = "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _authorization() -> dict[str, object]:
    return {
        "approval": {
            "comment_id": 123456789,
            "verdict": "AUTHORIZE_M5B_OUTCOME_FREE_PREFLIGHT_V1",
        },
        "automatic_retries": 0,
        "authoritative_parent": {
            "access_method": "server-local-read-only-paths-v1",
            "conditional_source_root": "/srv/pcdb/source/conditional",
            "conditional_store_root": "/srv/pcdb/store/conditional",
            "feature_root": "/srv/pcdb/features",
            "formal_attempt_root": "/srv/pcdb/m5a/attempt-001",
            "formal_contract_path": "/srv/pcdb/contracts/m5a-formal.json",
            "pilot_source_root": "/srv/pcdb/source/pilot",
            "pilot_store_root": "/srv/pcdb/store/pilot",
        },
        "extractor": {
            "identity": "pcdb.m5.inventory_preflight.extract_first_decision_state_payloads-v1",
            "worker_command": [
                "/srv/pcdb/venv/bin/python",
                "/srv/pcdb/source/reviewed/scripts/m5b_inventory_preflight_worker.py",
            ],
        },
        "preflight_runtime_environment_digest": _digest("preflight-runtime"),
        "resource_caps": {
            "cpu_seconds": 600,
            "process_tree_peak_rss_bytes": 1_073_741_824,
            "scratch_high_water_bytes": 134_217_728,
            "wall_seconds": 900,
        },
        "reviewed_source": {
            "git_commit": "a" * 40,
            "git_tree": "b" * 40,
        },
        "schema_version": "m5b-inventory-preflight-authorization-v1",
        "scientific_parent": {
            "execution_commit": "f75382ae6f90267b47c4379fc58b75b61a1da885",
            "execution_tree": "fb93c07b772c49939695647c8260aa837047539d",
            "formal_contract_sha256": (
                "2d04187264ba73f553daae461ee7d7780608c7c2a56ee850667fae41995bac9d"
            ),
            "inventory_sha256": ACCEPTED_M5A_INVENTORY_SHA256,
            "report_sha256": ("b199a483fd9e7a375d672e8ecf7ec5075248b2c5a729dfbc47e73fdae5641943"),
            "source_environment_digest": ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
        },
        "scratch": {
            "build_a": "/srv/pcdb/scratch/m5b/build-A",
            "build_b": "/srv/pcdb/scratch/m5b/build-B",
            "record_root": "/srv/pcdb/scratch/m5b/record",
        },
        "truth_boundary": {
            "active_network_bytes": 0,
            "candidate_truth_reads": 0,
            "held_out_reads": 0,
            "hidden_oracle_reads": 0,
        },
    }


def _candidate_universe(task_id: str) -> tuple[str, ...]:
    raw_count = _TASK_BINDINGS[task_id]["candidate_count"]
    assert isinstance(raw_count, int)
    count = raw_count
    return tuple(_digest(f"{task_id}:candidate:{index}") for index in range(count))


def _run_spec(
    *,
    task_id: str,
    model_id: str,
    initial_sample_size: int,
    run_seed: int,
    exploration: bool,
) -> RunSpec:
    task = _TASK_BINDINGS[task_id]
    model = _MODEL_BINDINGS[model_id]
    acquisition_id = (
        str(model["exploration_acquisition_id"]) if exploration else "greedy-predictive-mean-v1"
    )
    acquisition_sha256 = (
        str(model["exploration_acquisition_config_sha256"])
        if exploration
        else _GREEDY_CONFIG_SHA256
    )
    return RunSpec(
        schema_version="1",
        protocol_version="m4-single-fidelity-screening-v1",
        git_commit="c" * 40,
        environment_digest=_digest("m4-runtime"),
        dataset_manifest_sha256=str(task["dataset_manifest_sha256"]),
        processing_manifest_sha256=str(task["processing_manifest_sha256"]),
        split_sha256=str(task["split_sha256"]),
        feature_sha256=str(task["feature_sha256"]),
        experiment_config_sha256=str(task["experiment_config_sha256"]),
        model_id=model_id,
        model_config_sha256=str(model["model_config_sha256"]),
        acquisition_id=acquisition_id,
        acquisition_config_sha256=acquisition_sha256,
        seed=run_seed,
        initial_sample_size=initial_sample_size,
        batch_size=16,
        budget=128,
    )


def _trajectory(
    *,
    task_id: str,
    model_id: str,
    initial_sample_size: int,
    run_seed: int,
    exploration: bool,
    candidate_ids: tuple[str, ...],
) -> FormalTrajectory:
    run_spec = _run_spec(
        task_id=task_id,
        model_id=model_id,
        initial_sample_size=initial_sample_size,
        run_seed=run_seed,
        exploration=exploration,
    )
    prefix = candidate_ids[:initial_sample_size]
    first_batch_start = initial_sample_size + (8 if exploration else 0)
    selections = [prefix, candidate_ids[first_batch_start : first_batch_start + 16]]
    revealed = set(selections[0]) | set(selections[1])
    cursor = initial_sample_size + 32
    while len(revealed) < 128:
        batch = tuple(item for item in candidate_ids[cursor:] if item not in revealed)[:16]
        selections.append(batch)
        revealed.update(batch)
        cursor += 16
    events: list[ReplayEvent] = []
    total = 0
    for round_index, selected in enumerate(selections):
        observations = tuple(
            Observation(candidate_id=item, label=float(index + round_index) / 17.0)
            for index, item in enumerate(selected)
        )
        total += len(selected)
        events.append(
            ReplayEvent(
                round=round_index,
                selected_candidate_ids=tuple(selected),
                observations=observations,
                distinct_revealed=total,
                checkpoint_sha256=_digest(f"checkpoint:{run_spec.run_id}:{round_index}"),
            )
        )
    trajectory = ReplayTrajectory(run_spec=run_spec, task_id=task_id, events=tuple(events))
    policy = "exploration" if exploration else "greedy"
    return FormalTrajectory(
        prepared=PreparedRun(task_id=task_id, run_spec=run_spec),
        source="conditional-expansion",
        attempt_path=Path(f"/{task_id}/{run_spec.run_id}/run-attempt.json"),
        attempt_sha256=_digest(f"attempt:{run_spec.run_id}:{policy}"),
        trajectory_path=Path(f"/{task_id}/{run_spec.run_id}/trajectory.json"),
        trajectory_sha256=_digest(f"trajectory:{run_spec.run_id}:{policy}"),
        trajectory=trajectory,
    )


def _selected_trajectories() -> tuple[tuple[FormalTrajectory, ...], dict[str, tuple[str, ...]]]:
    universes = {task_id: _candidate_universe(task_id) for task_id in _TASK_BINDINGS}
    selected = tuple(
        _trajectory(
            task_id=task_id,
            model_id=model_id,
            initial_sample_size=initial_sample_size,
            run_seed=run_seed,
            exploration=exploration,
            candidate_ids=universes[task_id],
        )
        for task_id in reversed(tuple(_TASK_BINDINGS))
        for model_id in reversed(tuple(_MODEL_BINDINGS))
        for initial_sample_size in (64, 32)
        for run_seed in reversed(range(1729, 1739))
        for exploration in (True, False)
    )
    return selected, universes


def test_extractor_uniquely_builds_the_80_state_outcome_free_inventory() -> None:
    selected, universes = _selected_trajectories()

    payloads = extract_first_decision_state_payloads(
        selected,
        candidate_ids_by_task=universes,
        parent_inventory_sha256=ACCEPTED_M5A_INVENTORY_SHA256,
        source_environment_digest=ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
    )
    inventory = build_state_inventory(payloads)

    assert len(payloads) == 80
    assert len(inventory.states) == 80
    assert b"candidate_id" not in inventory.canonical_bytes
    assert b"value_binary64_be_hex" not in inventory.canonical_bytes
    assert payloads == extract_first_decision_state_payloads(
        tuple(reversed(selected)),
        candidate_ids_by_task=universes,
        parent_inventory_sha256=ACCEPTED_M5A_INVENTORY_SHA256,
        source_environment_digest=ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
    )


def test_extractor_accepts_selected_pilot_source_provenance() -> None:
    selected, universes = _selected_trajectories()
    mixed_sources = tuple(
        replace(item, source="pilot") if index < 24 else item for index, item in enumerate(selected)
    )

    payloads = extract_first_decision_state_payloads(
        mixed_sources,
        candidate_ids_by_task=universes,
        parent_inventory_sha256=ACCEPTED_M5A_INVENTORY_SHA256,
        source_environment_digest=ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
    )

    assert len(payloads) == 80
    with pytest.raises(ContractValidationError, match="source identity"):
        extract_first_decision_state_payloads(
            (replace(selected[0], source="unknown"), *selected[1:]),
            candidate_ids_by_task=universes,
            parent_inventory_sha256=ACCEPTED_M5A_INVENTORY_SHA256,
            source_environment_digest=ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
        )


def test_extractor_stops_on_pair_prefix_or_parent_identity_drift() -> None:
    selected, universes = _selected_trajectories()
    first = selected[0]
    changed_event = replace(
        first.trajectory.events[0],
        observations=(
            replace(first.trajectory.events[0].observations[0], label=999.0),
            *first.trajectory.events[0].observations[1:],
        ),
    )
    changed = replace(
        first,
        trajectory=replace(
            first.trajectory,
            events=(changed_event, *first.trajectory.events[1:]),
        ),
    )

    with pytest.raises(ContractValidationError, match="observation prefix"):
        extract_first_decision_state_payloads(
            (changed, *selected[1:]),
            candidate_ids_by_task=universes,
            parent_inventory_sha256=ACCEPTED_M5A_INVENTORY_SHA256,
            source_environment_digest=ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
        )
    with pytest.raises(ContractValidationError, match="parent inventory"):
        extract_first_decision_state_payloads(
            selected,
            candidate_ids_by_task=universes,
            parent_inventory_sha256="0" * 64,
            source_environment_digest=ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
        )


def _run_iteration_cli(authorization_path: Path) -> subprocess.CompletedProcess[str]:
    repository_root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        (
            sys.executable,
            str(repository_root / "scripts" / "run_m5b_iteration.py"),
            "--authorization",
            str(authorization_path),
        ),
        check=False,
        capture_output=True,
        cwd=repository_root,
        env={"PYTHONPATH": str(repository_root / "src")},
        text=True,
    )


def test_iteration_runner_missing_authorization_does_not_start_or_write(tmp_path: Path) -> None:
    missing = tmp_path / "missing-authorization.json"

    completed = _run_iteration_cli(missing)
    terminal = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert terminal["status"] == "NOT_STARTED"
    assert terminal["attempt_id"] is None
    assert terminal["truth_bearing_execution_completed"] is False
    assert tuple(tmp_path.iterdir()) == ()


def _iteration_authorization(tmp_path: Path) -> dict[str, object]:
    value = _authorization()
    value.pop("automatic_retries")
    value.pop("extractor")
    value.pop("preflight_runtime_environment_digest")
    value.pop("scratch")
    value["schema_version"] = "m5b-bounded-iteration-authorization-v2"
    value["approval"] = {
        "comment_id": 123456789,
        "verdict": "AUTHORIZE_M5B_BOUNDED_ITERATION_V2",
    }
    value["maximum_automatic_retries"] = 2
    parent = value["authoritative_parent"]
    assert isinstance(parent, dict)
    parent.pop("access_method")
    resource_caps = value["resource_caps"]
    assert isinstance(resource_caps, dict)
    value["resource_caps"] = {
        **resource_caps,
        "estimated_hourly_cost_cny": "1.62",
        "maximum_cost_cny": "8.10",
    }
    value["runner"] = {
        "identity": "scripts/m5b_iteration_worker.py-v1",
        "worker_command": [sys.executable, str(tmp_path / "worker.py")],
    }
    value["runtime_environment_digest"] = _digest("iteration-runtime")
    value["study"] = {
        "primary_attempt_id": "attempt-001",
        "study_root": str(tmp_path / "study"),
    }
    value["truth_boundary"] = {
        "cos_publication_writes": 0,
        "development_truth_files": 2,
        "held_out_reads": 0,
        "network_bytes": 0,
    }
    value["truth_inputs"] = {
        task_id: {
            "hidden_truth_path": str(tmp_path / f"{task_id}-truth.csv"),
            "hidden_truth_sha256": _digest(f"{task_id}-truth"),
            "processing_manifest_path": str(tmp_path / f"{task_id}-processing.json"),
        }
        for task_id in ("alpha-amylase", "ired")
    }
    return value


def test_iteration_authorization_exact_binds_truth_attempt_and_budget(tmp_path: Path) -> None:
    payload = compact_json_bytes(_iteration_authorization(tmp_path))

    loaded = load_iteration_authorization(payload)

    assert loaded.canonical_bytes == payload
    assert loaded.sha256 == hashlib.sha256(payload).hexdigest()
    assert loaded.value["maximum_automatic_retries"] == 2


@pytest.mark.parametrize("attempt_id", ("attempt-000", "attempt-2", "attempt-1000"))
def test_iteration_authorization_rejects_noncanonical_attempt(
    tmp_path: Path, attempt_id: str
) -> None:
    value = _iteration_authorization(tmp_path)
    value["study"] = {
        "primary_attempt_id": attempt_id,
        "study_root": str(tmp_path / "study"),
    }

    with pytest.raises(ContractValidationError, match="attempt ID"):
        load_iteration_authorization(compact_json_bytes(value))


@pytest.mark.parametrize(
    ("field", "invalid", "match"),
    (
        ("maximum_automatic_retries", 3, "approval or retry"),
        ("runtime_environment_digest", "UNKNOWN", "runtime"),
        (
            "study",
            {"primary_attempt_id": "attempt-001", "study_root": "relative"},
            "path",
        ),
        (
            "truth_boundary",
            {
                "cos_publication_writes": 0,
                "development_truth_files": 2,
                "held_out_reads": 1,
                "network_bytes": 0,
            },
            "truth boundary",
        ),
    ),
)
def test_iteration_authorization_rejects_unbound_or_expanded_authority(
    tmp_path: Path,
    field: str,
    invalid: object,
    match: str,
) -> None:
    value = _iteration_authorization(tmp_path)
    value[field] = invalid

    with pytest.raises(ContractValidationError, match=match):
        load_iteration_authorization(compact_json_bytes(value))


@pytest.mark.parametrize(
    ("worker_body", "expected_failure_class"),
    (
        ("raise SystemExit(17)\n", "implementation"),
        (
            "import json, sys\n"
            'print(json.dumps({"failure_class": "validity", '
            '"reason": "synthetic validity stop", '
            '"schema_version": "m5b-bounded-iteration-worker-failure-v1"}, '
            'sort_keys=True, separators=(",", ":")), file=sys.stderr)\n'
            "raise SystemExit(2)\n",
            "validity",
        ),
        (
            "import json, sys\n"
            'print(json.dumps({"failure_class": "budget", '
            '"reason": "synthetic budget stop", '
            '"schema_version": "m5b-bounded-iteration-worker-failure-v1"}, '
            'sort_keys=True, separators=(",", ":")), file=sys.stderr)\n'
            "raise SystemExit(2)\n",
            "budget",
        ),
    ),
)
def test_iteration_runner_records_one_append_only_nonretryable_attempt(
    tmp_path: Path, worker_body: str, expected_failure_class: str
) -> None:
    value = _iteration_authorization(tmp_path)
    parent = value["authoritative_parent"]
    assert isinstance(parent, dict)
    parent_directories = {
        "conditional_source_root": tmp_path / "conditional-source",
        "conditional_store_root": tmp_path / "conditional-store",
        "feature_root": tmp_path / "features",
        "formal_attempt_root": tmp_path / "formal-attempt",
        "pilot_source_root": tmp_path / "pilot-source",
        "pilot_store_root": tmp_path / "pilot-store",
    }
    for field, directory in parent_directories.items():
        directory.mkdir()
        parent[field] = str(directory)
    formal_contract = tmp_path / "formal-contract.json"
    formal_contract.write_bytes(b"{}\n")
    parent["formal_contract_path"] = str(formal_contract)
    failing_worker = tmp_path / "intentionally-failing-worker.py"
    failing_worker.write_text(worker_body, encoding="utf-8")
    runner = value["runner"]
    assert isinstance(runner, dict)
    runner["worker_command"] = [
        sys.executable,
        str(failing_worker),
    ]
    truth_inputs = value["truth_inputs"]
    assert isinstance(truth_inputs, dict)
    for item in truth_inputs.values():
        assert isinstance(item, dict)
        Path(item["hidden_truth_path"]).write_bytes(b"candidate_id,target\n")
        Path(item["processing_manifest_path"]).write_bytes(b"{}\n")
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_bytes(compact_json_bytes(value))

    completed = _run_iteration_cli(authorization_path)
    terminal = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert terminal["status"] == "STOPPED"
    assert terminal["attempt_id"] == "attempt-001"
    assert terminal["truth_bearing_execution_completed"] is False
    attempt_root = tmp_path / "study" / "attempt-001"
    assert (attempt_root / "stderr.log").is_file()
    assert json.loads((attempt_root / "terminal.json").read_bytes()) == terminal
    ledger = json.loads((tmp_path / "study" / "attempts.jsonl").read_text())
    assert ledger["attempt_id"] == "attempt-001"
    assert ledger["status"] == "failed"
    assert ledger["failure_class"] == expected_failure_class
    assert not (attempt_root / "_SUCCESS").exists()
    assert not (tmp_path / "study" / "attempt-002").exists()


def test_iteration_runner_writes_success_marker_last_for_valid_terminal(tmp_path: Path) -> None:
    value = _iteration_authorization(tmp_path)
    parent = value["authoritative_parent"]
    assert isinstance(parent, dict)
    for field in (
        "conditional_source_root",
        "conditional_store_root",
        "feature_root",
        "formal_attempt_root",
        "pilot_source_root",
        "pilot_store_root",
    ):
        directory = tmp_path / field
        directory.mkdir()
        parent[field] = str(directory)
    formal_contract = tmp_path / "formal-contract.json"
    formal_contract.write_bytes(b"{}\n")
    parent["formal_contract_path"] = str(formal_contract)
    truth_inputs = value["truth_inputs"]
    assert isinstance(truth_inputs, dict)
    for item in truth_inputs.values():
        assert isinstance(item, dict)
        Path(item["hidden_truth_path"]).write_bytes(b"candidate_id,target\n")
        Path(item["processing_manifest_path"]).write_bytes(b"{}\n")
    worker = tmp_path / "successful-worker.py"
    worker.write_text(
        """\
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--authorization-sha256", required=True)
parser.add_argument("--expected-runtime-environment-digest", required=True)
parser.add_argument("--output-root", type=Path, required=True)
args, _ = parser.parse_known_args()
inventory = b"{}\\n"
inventory_sha256 = hashlib.sha256(inventory).hexdigest()
result = json.dumps({
    "inventory_sha256": inventory_sha256,
    "overall_status": "INCONCLUSIVE",
    "schema_version": "m5b-bounded-iteration-result-v1",
}, sort_keys=True, separators=(",", ":")).encode() + b"\\n"
audit = json.dumps({
    "authorization_sha256": args.authorization_sha256,
    "branch_evaluations": 5280,
    "cos_publication_writes": 0,
    "held_out_reads": 0,
    "inventory_sha256": inventory_sha256,
    "network_bytes": 0,
    "parent_read_only_verified": True,
    "result_sha256": hashlib.sha256(result).hexdigest(),
    "runtime_environment_digest": args.expected_runtime_environment_digest,
    "schema_version": "m5b-bounded-iteration-audit-v1",
    "selected_candidate_reveals": 84480,
}, sort_keys=True, separators=(",", ":")).encode() + b"\\n"
args.output_root.joinpath("inventory.json").write_bytes(inventory)
args.output_root.joinpath("result.json").write_bytes(result)
args.output_root.joinpath("audit.json").write_bytes(audit)
""",
        encoding="utf-8",
    )
    runner = value["runner"]
    assert isinstance(runner, dict)
    runner["worker_command"] = [sys.executable, str(worker)]
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_bytes(compact_json_bytes(value))

    completed = _run_iteration_cli(authorization_path)
    terminal = json.loads(completed.stdout)
    attempt_root = tmp_path / "study" / "attempt-001"

    assert completed.returncode == 0
    assert terminal["status"] == "COMPLETE_FOR_SCIENTIFIC_VALIDITY_REVIEW"
    assert (attempt_root / "_SUCCESS").read_bytes() == b""
    assert json.loads((attempt_root / "terminal.json").read_bytes()) == terminal
    ledger = json.loads((tmp_path / "study" / "attempts.jsonl").read_text())
    assert ledger["status"] == "succeeded"


def test_iteration_runner_retries_only_transient_failures_append_only(tmp_path: Path) -> None:
    value = _iteration_authorization(tmp_path)
    value["study"] = {
        "primary_attempt_id": "attempt-002",
        "study_root": str(tmp_path / "study"),
    }
    parent = value["authoritative_parent"]
    assert isinstance(parent, dict)
    for field in (
        "conditional_source_root",
        "conditional_store_root",
        "feature_root",
        "formal_attempt_root",
        "pilot_source_root",
        "pilot_store_root",
    ):
        directory = tmp_path / field
        directory.mkdir()
        parent[field] = str(directory)
    formal_contract = tmp_path / "formal-contract.json"
    formal_contract.write_bytes(b"{}\n")
    parent["formal_contract_path"] = str(formal_contract)
    truth_inputs = value["truth_inputs"]
    assert isinstance(truth_inputs, dict)
    for item in truth_inputs.values():
        assert isinstance(item, dict)
        Path(item["hidden_truth_path"]).write_bytes(b"candidate_id,target\n")
        Path(item["processing_manifest_path"]).write_bytes(b"{}\n")

    study_root = tmp_path / "study"
    prior_root = study_root / "attempt-001"
    prior_root.mkdir(parents=True)
    (study_root / "contract.md").write_bytes(
        b"# M5B bounded same-state iteration\n\n"
        b"One exact authorization covers outcome-free 80-state validation followed "
        b"by Alpha/IRED development truth evaluation. No held-out access or COS "
        b"publication.\n"
    )
    (prior_root / "terminal.json").write_bytes(
        compact_json_bytes(
            {
                "attempt_id": "attempt-001",
                "reason": "reviewed implementation diagnostic",
                "schema_version": "m5b-bounded-iteration-terminal-v1",
                "status": "STOPPED",
                "truth_bearing_execution_completed": False,
            }
        )
    )
    (study_root / "attempts.jsonl").write_bytes(
        compact_json_bytes(
            {
                "attempt_id": "attempt-001",
                "command": "prior-reviewed-command",
                "config_identity": ["authorization:" + _digest("prior-authorization")],
                "data_identities": ["m5a-inventory:" + ACCEPTED_M5A_INVENTORY_SHA256],
                "executed_git_commit": "a" * 40,
                "failure_class": "validity",
                "failure_reason": "reviewed implementation diagnostic",
                "finished_at": "2026-09-02T00:00:01Z",
                "parent_attempt_id": None,
                "result_summary": None,
                "selection_disposition": "discarded",
                "started_at": "2026-09-02T00:00:00Z",
                "status": "failed",
                "study_id": "m5b-same-state-iteration",
            }
        )
    )

    worker = tmp_path / "transient-twice-then-success.py"
    worker.write_text(
        """\
import argparse
import hashlib
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--authorization-sha256", required=True)
parser.add_argument("--expected-runtime-environment-digest", required=True)
parser.add_argument("--output-root", type=Path, required=True)
args, _ = parser.parse_known_args()
attempt_id = args.output_root.parent.name
if attempt_id in {"attempt-002", "attempt-003"}:
    print(json.dumps({
        "failure_class": "transient-infrastructure",
        "reason": "synthetic transient infrastructure failure",
        "schema_version": "m5b-bounded-iteration-worker-failure-v1",
    }, sort_keys=True, separators=(",", ":")), file=sys.stderr)
    raise SystemExit(2)
inventory = b"{}\\n"
inventory_sha256 = hashlib.sha256(inventory).hexdigest()
result = json.dumps({
    "inventory_sha256": inventory_sha256,
    "overall_status": "INCONCLUSIVE",
    "schema_version": "m5b-bounded-iteration-result-v1",
}, sort_keys=True, separators=(",", ":")).encode() + b"\\n"
audit = json.dumps({
    "authorization_sha256": args.authorization_sha256,
    "branch_evaluations": 5280,
    "cos_publication_writes": 0,
    "held_out_reads": 0,
    "inventory_sha256": inventory_sha256,
    "network_bytes": 0,
    "parent_read_only_verified": True,
    "result_sha256": hashlib.sha256(result).hexdigest(),
    "runtime_environment_digest": args.expected_runtime_environment_digest,
    "schema_version": "m5b-bounded-iteration-audit-v1",
    "selected_candidate_reveals": 84480,
}, sort_keys=True, separators=(",", ":")).encode() + b"\\n"
args.output_root.joinpath("inventory.json").write_bytes(inventory)
args.output_root.joinpath("result.json").write_bytes(result)
args.output_root.joinpath("audit.json").write_bytes(audit)
""",
        encoding="utf-8",
    )
    runner = value["runner"]
    assert isinstance(runner, dict)
    runner["worker_command"] = [sys.executable, str(worker)]
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_bytes(compact_json_bytes(value))

    completed = _run_iteration_cli(authorization_path)
    terminal = json.loads(completed.stdout)
    ledgers = [
        json.loads(line) for line in (study_root / "attempts.jsonl").read_text().splitlines()
    ]

    assert completed.returncode == 0
    assert terminal["attempt_id"] == "attempt-004"
    assert terminal["status"] == "COMPLETE_FOR_SCIENTIFIC_VALIDITY_REVIEW"
    assert [item["attempt_id"] for item in ledgers] == [
        "attempt-001",
        "attempt-002",
        "attempt-003",
        "attempt-004",
    ]
    assert [item["parent_attempt_id"] for item in ledgers[1:]] == [
        "attempt-001",
        "attempt-002",
        "attempt-003",
    ]
    assert [item["failure_class"] for item in ledgers[1:]] == [
        "transient-infrastructure",
        "transient-infrastructure",
        "none",
    ]
    assert not (study_root / "attempt-002" / "_SUCCESS").exists()
    assert not (study_root / "attempt-003" / "_SUCCESS").exists()
    assert (study_root / "attempt-004" / "_SUCCESS").read_bytes() == b""
    assert not (study_root / "attempt-005").exists()
    study = value["study"]
    assert isinstance(study, dict)
    study["primary_attempt_id"] = "attempt-005"
    authorization_path.write_bytes(compact_json_bytes(value))

    forbidden_after_complete = _run_iteration_cli(authorization_path)

    assert forbidden_after_complete.returncode == 2
    assert json.loads(forbidden_after_complete.stdout)["status"] == "NOT_STARTED"
    assert not (study_root / "attempt-005").exists()


def test_development_truth_binds_full_superset_but_retains_only_candidate_pool() -> None:
    candidate_ids = tuple(_digest(f"pool-{index}") for index in range(3))
    outside_ids = tuple(_digest(f"outside-{index}") for index in range(2))
    full_rows = (*candidate_ids, *outside_ids)
    hidden_truth = (
        "candidate_id,target\n"
        + "".join(f"{candidate_id},{index}.0\n" for index, candidate_id in enumerate(full_rows))
    ).encode()
    truth_sha256 = hashlib.sha256(hidden_truth).hexdigest()
    processing_manifest = compact_json_bytes(
        {
            "candidate_id_field": "candidate_id",
            "candidate_id_unique": True,
            "hidden_label_field": "target",
            "label_direction": "higher_is_better",
            "output_files": [
                {
                    "object_key": "development/superset/hidden-truth.csv",
                    "sha256": truth_sha256,
                    "size_bytes": len(hidden_truth),
                }
            ],
        }
    )

    def load(candidate_pool: tuple[str, ...]) -> DevelopmentTruth:
        return load_development_truth(
            task_id="alpha-amylase",
            processing_manifest=processing_manifest,
            expected_processing_manifest_sha256=hashlib.sha256(processing_manifest).hexdigest(),
            hidden_truth=hidden_truth,
            expected_hidden_truth_sha256=truth_sha256,
            candidate_ids=candidate_pool,
        )

    loaded = load(candidate_ids)

    assert loaded.sha256 == truth_sha256
    assert dict(loaded.rows) == {
        candidate_id: float(full_rows.index(candidate_id)) for candidate_id in candidate_ids
    }
    assert not set(dict(loaded.rows)) & set(outside_ids)
    with pytest.raises(ContractValidationError, match="cover candidate universe"):
        load((*candidate_ids, _digest("missing")))
    with pytest.raises(ContractValidationError, match="candidate universe"):
        load((*candidate_ids, candidate_ids[0]))


def _development_truths(
    universes: dict[str, tuple[str, ...]],
) -> dict[str, DevelopmentTruth]:
    truths: dict[str, DevelopmentTruth] = {}
    for task_id, candidate_ids in universes.items():
        hidden_truth = (
            "candidate_id,target\n"
            + "".join(
                f"{candidate_id},{len(candidate_ids) - index}.0\n"
                for index, candidate_id in enumerate(candidate_ids)
            )
        ).encode()
        truth_sha256 = hashlib.sha256(hidden_truth).hexdigest()
        processing_manifest = compact_json_bytes(
            {
                "candidate_id_field": "candidate_id",
                "candidate_id_unique": True,
                "hidden_label_field": "target",
                "label_direction": "higher_is_better",
                "output_files": [
                    {
                        "object_key": f"development/{task_id}/hidden-truth.csv",
                        "sha256": truth_sha256,
                        "size_bytes": len(hidden_truth),
                    }
                ],
            }
        )
        truths[task_id] = load_development_truth(
            task_id=task_id,
            processing_manifest=processing_manifest,
            expected_processing_manifest_sha256=hashlib.sha256(processing_manifest).hexdigest(),
            hidden_truth=hidden_truth,
            expected_hidden_truth_sha256=truth_sha256,
            candidate_ids=candidate_ids,
        )
    return truths


def test_bounded_iteration_continues_from_valid_inventory_to_aggregate_terminal() -> None:
    selected, universes = _selected_trajectories()

    evaluation = evaluate_iteration(
        selected,
        candidate_ids_by_task=universes,
        truths=_development_truths(universes),
    )
    report = build_iteration_report(evaluation)
    value = json.loads(report)

    assert len(evaluation.primary_effects) == 80
    assert {task_id: len(items) for task_id, items in evaluation.difference_counts.items()} == {
        "alpha-amylase": 40,
        "ired": 40,
    }
    assert value["schema_version"] == "m5b-bounded-iteration-result-v1"
    assert value["inventory_sha256"] == evaluation.inventory_sha256
    assert value["branch_evaluations"] == 5280
    assert value["selected_candidate_reveals"] == 84480
    assert value["held_out_reads"] == 0
    assert set(value["consistency_summaries"]) == {
        "best_found_improvement",
        "simple_regret_reduction",
    }
    assert all(
        len(value["consistency_summaries"][metric_id]) == 2
        for metric_id in value["consistency_summaries"]
    )
    assert all(
        set(item) == {"contrasts", "task_id"} and set(item["contrasts"]) == {"E-G", "E-M"}
        for summaries in value["consistency_summaries"].values()
        for item in summaries
    )
    changed_consistency = {
        metric_id: tuple(
            replace(
                item,
                exploration_minus_greedy=-1000.0,
                exploration_minus_matched=tuple(1000.0 for _ in range(64)),
            )
            for item in items
        )
        for metric_id, items in evaluation.consistency_effects.items()
    }
    changed = json.loads(
        build_iteration_report(
            replace(
                evaluation,
                consistency_effects=MappingProxyType(changed_consistency),
            )
        )
    )
    assert changed["overall_status"] == value["overall_status"]
    assert changed["task_statuses"] == value["task_statuses"]
    assert b"candidate_id" not in report
    assert b"target" not in report
    assert b"membership" not in report


def test_immediate_batch_evaluator_normalizes_invalid_truth_and_candidate_ids() -> None:
    with pytest.raises(ContractValidationError, match="truth boundary"):
        ImmediateBatchEvaluator({}, (0.01,))

    truth = {f"candidate-{index:02d}": float(index) for index in range(32)}
    evaluator = ImmediateBatchEvaluator(truth, (0.01,))

    with pytest.raises(ContractValidationError, match="truth universe"):
        evaluator.evaluate(
            observation_prefix_ids=("outside-truth",),
            selected_ids=tuple(f"candidate-{index:02d}" for index in range(16)),
        )
