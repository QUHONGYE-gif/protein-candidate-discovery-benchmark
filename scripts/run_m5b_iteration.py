#!/usr/bin/env python3
"""Run one authorized append-only M5B development iteration attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shlex
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError
from pcdb.m5.iteration import IterationAuthorization, load_iteration_authorization
from pcdb.m5.reconstruction_smoke import compact_json_bytes

STUDY_ID = "m5b-same-state-iteration"
_CONTRACT_BYTES = (
    b"# M5B bounded same-state iteration\n\n"
    b"One exact authorization covers outcome-free 80-state validation followed "
    b"by Alpha/IRED development truth evaluation. No held-out access or COS "
    b"publication.\n"
)


class _TransientInfrastructureError(RuntimeError):
    """Machine-classified process-launch or communication failure."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _append(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _not_started(reason: str) -> dict[str, object]:
    return {
        "attempt_id": None,
        "reason": reason,
        "schema_version": "m5b-bounded-iteration-terminal-v1",
        "status": "NOT_STARTED",
        "truth_bearing_execution_completed": False,
    }


def _paths_available(value: dict[str, Any]) -> bool:
    parent = cast(dict[str, str], value["authoritative_parent"])
    runner = cast(dict[str, object], value["runner"])
    command = cast(list[str], runner["worker_command"])
    truth_inputs = cast(dict[str, dict[str, str]], value["truth_inputs"])
    directories = (
        "conditional_source_root",
        "conditional_store_root",
        "feature_root",
        "formal_attempt_root",
        "pilot_source_root",
        "pilot_store_root",
    )
    return (
        all(Path(parent[field]).is_dir() for field in directories)
        and Path(parent["formal_contract_path"]).is_file()
        and Path(command[0]).is_file()
        and os.access(command[0], os.X_OK)
        and Path(command[1]).is_file()
        and all(
            Path(item[field]).is_file()
            for item in truth_inputs.values()
            for field in ("hidden_truth_path", "processing_manifest_path")
        )
    )


def _worker_command(
    value: dict[str, Any],
    output_root: Path,
    auth_sha256: str,
    *,
    cpu_seconds: int,
    scratch_high_water_bytes: int,
) -> tuple[str, ...]:
    parent = cast(dict[str, str], value["authoritative_parent"])
    reviewed = cast(dict[str, str], value["reviewed_source"])
    scientific = cast(dict[str, str], value["scientific_parent"])
    caps = cast(dict[str, object], value["resource_caps"])
    runner = cast(dict[str, object], value["runner"])
    command = cast(list[str], runner["worker_command"])
    truths = cast(dict[str, dict[str, str]], value["truth_inputs"])
    source_root = Path(command[1]).resolve().parents[1]
    return (
        *command,
        "--alpha-hidden-truth",
        truths["alpha-amylase"]["hidden_truth_path"],
        "--alpha-hidden-truth-sha256",
        truths["alpha-amylase"]["hidden_truth_sha256"],
        "--alpha-processing-manifest",
        truths["alpha-amylase"]["processing_manifest_path"],
        "--authorization-sha256",
        auth_sha256,
        "--conditional-source-root",
        parent["conditional_source_root"],
        "--conditional-store-root",
        parent["conditional_store_root"],
        "--cpu-seconds",
        str(cpu_seconds),
        "--expected-git-commit",
        reviewed["git_commit"],
        "--expected-git-tree",
        reviewed["git_tree"],
        "--expected-runtime-environment-digest",
        cast(str, value["runtime_environment_digest"]),
        "--feature-root",
        parent["feature_root"],
        "--formal-attempt-root",
        parent["formal_attempt_root"],
        "--formal-contract",
        parent["formal_contract_path"],
        "--formal-contract-sha256",
        scientific["formal_contract_sha256"],
        "--ired-hidden-truth",
        truths["ired"]["hidden_truth_path"],
        "--ired-hidden-truth-sha256",
        truths["ired"]["hidden_truth_sha256"],
        "--ired-processing-manifest",
        truths["ired"]["processing_manifest_path"],
        "--output-root",
        str(output_root),
        "--pilot-source-root",
        parent["pilot_source_root"],
        "--pilot-store-root",
        parent["pilot_store_root"],
        "--process-tree-peak-rss-bytes",
        str(caps["process_tree_peak_rss_bytes"]),
        "--scratch-high-water-bytes",
        str(scratch_high_water_bytes),
        "--source-root",
        str(source_root),
    )


def _run_worker(command: tuple[str, ...], wall_seconds: int) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment.update(
        {
            "BLIS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
        }
    )
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
    except OSError as exc:
        raise _TransientInfrastructureError("M5B worker process launch failed") from exc
    try:
        stdout, stderr = process.communicate(timeout=wall_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise ContractValidationError("M5B iteration wall cap exceeded") from None
    except OSError as exc:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise _TransientInfrastructureError("M5B worker process communication failed") from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _worker_failure(stderr: bytes) -> tuple[str, str]:
    try:
        value = json.loads(stderr)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "implementation", "M5B iteration worker failed without a structured terminal"
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "m5b-bounded-iteration-worker-failure-v1"
        or value.get("failure_class") not in {"budget", "transient-infrastructure", "validity"}
        or not isinstance(value.get("reason"), str)
    ):
        return "implementation", "M5B iteration worker failure terminal drifted"
    return cast(str, value["failure_class"]), cast(str, value["reason"])


def _directory_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _children_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def _prepare_study(study_root: Path, primary_attempt_id: str) -> str | None:
    primary_number = int(primary_attempt_id.removeprefix("attempt-"))
    expected_prior = tuple(f"attempt-{index:03d}" for index in range(1, primary_number))
    ledger_path = study_root / "attempts.jsonl"
    try:
        ledgers = (
            tuple(json.loads(line) for line in ledger_path.read_text().splitlines())
            if ledger_path.exists()
            else ()
        )
    except (OSError, json.JSONDecodeError):
        raise ContractValidationError("M5B prior attempt ledger is invalid") from None
    actual_prior = tuple(
        item.get("attempt_id") if isinstance(item, dict) else None for item in ledgers
    )
    actual_parents = tuple(
        item.get("parent_attempt_id") if isinstance(item, dict) else None for item in ledgers
    )
    expected_parents = (None, *expected_prior[:-1]) if expected_prior else ()
    prior_directories = tuple(
        path.name for path in sorted(study_root.glob("attempt-*")) if path.is_dir()
    )
    if (
        actual_prior != expected_prior
        or actual_parents != expected_parents
        or prior_directories != expected_prior
        or any(not isinstance(item, dict) or item.get("status") != "failed" for item in ledgers)
    ):
        raise ContractValidationError("M5B prior attempt chain is not canonical and append-only")
    for attempt_id in expected_prior:
        terminal_path = study_root / attempt_id / "terminal.json"
        try:
            terminal = json.loads(terminal_path.read_bytes())
        except (OSError, json.JSONDecodeError):
            raise ContractValidationError(
                "M5B prior attempt terminal prevents a new primary attempt"
            ) from None
        if (
            not isinstance(terminal, dict)
            or terminal.get("attempt_id") != attempt_id
            or terminal.get("status") != "STOPPED"
            or (study_root / attempt_id / "_SUCCESS").exists()
        ):
            raise ContractValidationError(
                "M5B prior attempt terminal prevents a new primary attempt"
            )
    study_root.mkdir(parents=True, exist_ok=True)
    contract_path = study_root / "contract.md"
    if not contract_path.exists():
        _write_exclusive(contract_path, _CONTRACT_BYTES)
    elif contract_path.read_bytes() != _CONTRACT_BYTES:
        raise ContractValidationError("M5B study contract identity drifted")
    return expected_prior[-1] if expected_prior else None


def _run_attempt(
    *,
    loaded: IterationAuthorization,
    value: dict[str, Any],
    study_root: Path,
    attempt_id: str,
    parent_attempt_id: str | None,
    chain_started: float,
    children_cpu_started: float,
) -> tuple[dict[str, object], int, str]:
    caps = cast(dict[str, object], value["resource_caps"])
    elapsed_before = time.monotonic() - chain_started
    cpu_used_before = _children_cpu_seconds() - children_cpu_started
    scratch_used_before = _directory_bytes(study_root)
    wall_seconds = int(cast(int, caps["wall_seconds"]) - elapsed_before)
    cpu_seconds = int(cast(int, caps["cpu_seconds"]) - cpu_used_before)
    scratch_bytes = cast(int, caps["scratch_high_water_bytes"]) - scratch_used_before
    if min(wall_seconds, cpu_seconds, scratch_bytes) < 1:
        return _not_started("M5B cumulative resource cap is exhausted"), 2, "budget"
    attempt_root = study_root / attempt_id
    artifacts = attempt_root / "artifacts"
    try:
        if attempt_root.exists():
            raise ContractValidationError("M5B attempt directory already exists")
        attempt_root.mkdir(mode=0o700)
        artifacts.mkdir(mode=0o700)
        _write_exclusive(attempt_root / "authorization.json", loaded.canonical_bytes)
    except (OSError, ContractValidationError) as exc:
        return _not_started(str(exc)), 2, "implementation"

    command = _worker_command(
        value,
        artifacts,
        loaded.sha256,
        cpu_seconds=cpu_seconds,
        scratch_high_water_bytes=scratch_bytes,
    )
    _write_exclusive(attempt_root / "command.txt", (shlex.join(command) + "\n").encode())
    started_at = _utc_now()
    status = "failed"
    failure_class = "implementation"
    failure_reason: str | None = None
    result_summary: str | None = None
    terminal: dict[str, object]
    try:
        completed = _run_worker(command, wall_seconds)
        _write_exclusive(attempt_root / "stdout.log", completed.stdout)
        _write_exclusive(attempt_root / "stderr.log", completed.stderr)
        elapsed = time.monotonic() - chain_started
        estimated_cost = (
            elapsed / 3600 * float(cast(str | float | int, caps["estimated_hourly_cost_cny"]))
        )
        if _directory_bytes(study_root) > cast(int, caps["scratch_high_water_bytes"]):
            failure_class = "budget"
            raise ContractValidationError("M5B iteration cumulative scratch cap exceeded")
        if estimated_cost > float(cast(str | float | int, caps["maximum_cost_cny"])):
            failure_class = "budget"
            raise ContractValidationError("M5B iteration cumulative cost cap exceeded")
        if completed.returncode != 0:
            failure_class, worker_reason = _worker_failure(completed.stderr)
            raise ContractValidationError(worker_reason)
        result_bytes = (artifacts / "result.json").read_bytes()
        inventory_bytes = (artifacts / "inventory.json").read_bytes()
        result = json.loads(result_bytes)
        audit = json.loads((artifacts / "audit.json").read_bytes())
        if (
            result.get("schema_version") != "m5b-bounded-iteration-result-v1"
            or audit.get("schema_version") != "m5b-bounded-iteration-audit-v1"
            or audit.get("authorization_sha256") != loaded.sha256
            or audit.get("branch_evaluations") != 80 * 66
            or audit.get("selected_candidate_reveals") != 80 * 66 * 16
            or audit.get("parent_read_only_verified") is not True
            or audit.get("held_out_reads") != 0
            or audit.get("network_bytes") != 0
            or audit.get("cos_publication_writes") != 0
            or audit.get("runtime_environment_digest") != value["runtime_environment_digest"]
            or audit.get("result_sha256") != hashlib.sha256(result_bytes).hexdigest()
            or audit.get("inventory_sha256") != hashlib.sha256(inventory_bytes).hexdigest()
            or result.get("inventory_sha256") != audit.get("inventory_sha256")
        ):
            failure_class = "validity"
            raise ContractValidationError("M5B iteration result or boundary audit drifted")
        status = "succeeded"
        failure_class = "none"
        result_summary = cast(str, result["overall_status"])
        terminal = {
            "attempt_id": attempt_id,
            "inventory_sha256": result["inventory_sha256"],
            "result_sha256": audit["result_sha256"],
            "schema_version": "m5b-bounded-iteration-terminal-v1",
            "scientific_status": result_summary,
            "status": "COMPLETE_FOR_SCIENTIFIC_VALIDITY_REVIEW",
            "truth_bearing_execution_completed": True,
        }
    except (_TransientInfrastructureError, OSError, ContractValidationError, ValueError) as exc:
        if isinstance(exc, _TransientInfrastructureError):
            failure_class = "transient-infrastructure"
        elif "cap exceeded" in str(exc):
            failure_class = "budget"
        for name in ("stdout.log", "stderr.log"):
            path = attempt_root / name
            if not path.exists():
                _write_exclusive(path, b"")
        failure_reason = str(exc)
        terminal = {
            "attempt_id": attempt_id,
            "reason": failure_reason,
            "schema_version": "m5b-bounded-iteration-terminal-v1",
            "status": "STOPPED",
            "truth_bearing_execution_completed": False,
        }
    finished_at = _utc_now()
    _write_exclusive(attempt_root / "terminal.json", compact_json_bytes(terminal))
    ledger = {
        "attempt_id": attempt_id,
        "command": shlex.join(command),
        "config_identity": [f"authorization:{loaded.sha256}"],
        "data_identities": [f"m5a-inventory:{value['scientific_parent']['inventory_sha256']}"],
        "executed_git_commit": value["reviewed_source"]["git_commit"],
        "failure_class": failure_class,
        "failure_reason": failure_reason,
        "finished_at": finished_at,
        "parent_attempt_id": parent_attempt_id,
        "result_summary": result_summary,
        "selection_disposition": "diagnostic" if status == "succeeded" else "discarded",
        "started_at": started_at,
        "status": status,
        "study_id": STUDY_ID,
    }
    _append(study_root / "attempts.jsonl", compact_json_bytes(ledger))
    if status == "succeeded":
        marker_path = attempt_root / "_SUCCESS"
        _write_exclusive(marker_path, b"")
        if marker_path.read_bytes() != b"":
            raise ContractValidationError("M5B success marker identity drifted")
    return terminal, 0 if status == "succeeded" else 2, failure_class


def _run(authorization_path: Path) -> tuple[dict[str, object], int]:
    try:
        loaded = load_iteration_authorization(authorization_path.read_bytes())
    except (OSError, ContractValidationError) as exc:
        return _not_started(str(exc)), 2
    value = dict(loaded.value)
    if not _paths_available(value):
        return _not_started("authorized source, parent, truth, or runner path is unavailable"), 2
    study = cast(dict[str, str], value["study"])
    study_root = Path(study["study_root"])
    primary_attempt_id = study["primary_attempt_id"]
    try:
        parent_attempt_id = _prepare_study(study_root, primary_attempt_id)
    except (OSError, ContractValidationError) as exc:
        return _not_started(str(exc)), 2
    primary_number = int(primary_attempt_id.removeprefix("attempt-"))
    maximum_retries = cast(int, value["maximum_automatic_retries"])
    chain_started = time.monotonic()
    children_cpu_started = _children_cpu_seconds()
    terminal: dict[str, object] = _not_started("M5B controller did not start an attempt")
    for offset in range(maximum_retries + 1):
        attempt_id = f"attempt-{primary_number + offset:03d}"
        terminal, exit_code, failure_class = _run_attempt(
            loaded=loaded,
            value=value,
            study_root=study_root,
            attempt_id=attempt_id,
            parent_attempt_id=parent_attempt_id,
            chain_started=chain_started,
            children_cpu_started=children_cpu_started,
        )
        if exit_code == 0 or failure_class != "transient-infrastructure":
            return terminal, exit_code
        parent_attempt_id = attempt_id
    return terminal, 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    args = parser.parse_args()
    terminal, exit_code = _run(args.authorization)
    sys.stdout.buffer.write(compact_json_bytes(terminal))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
