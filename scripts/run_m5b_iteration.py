#!/usr/bin/env python3
"""Run one authorized append-only M5B development iteration attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError
from pcdb.m5.iteration import load_iteration_authorization
from pcdb.m5.reconstruction_smoke import compact_json_bytes

STUDY_ID = "m5b-same-state-iteration"
_CONTRACT_BYTES = (
    b"# M5B bounded same-state iteration\n\n"
    b"One exact authorization covers outcome-free 80-state validation followed "
    b"by Alpha/IRED development truth evaluation. No held-out access or COS "
    b"publication.\n"
)


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


def _worker_command(value: dict[str, Any], output_root: Path, auth_sha256: str) -> tuple[str, ...]:
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
        str(caps["cpu_seconds"]),
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
        str(caps["scratch_high_water_bytes"]),
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
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=wall_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise ContractValidationError("M5B iteration wall cap exceeded") from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _worker_failure(stderr: bytes) -> tuple[str, str]:
    try:
        value = json.loads(stderr)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "implementation", "M5B iteration worker failed without a structured terminal"
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "m5b-bounded-iteration-worker-failure-v1"
        or value.get("failure_class") not in {"budget", "validity"}
        or not isinstance(value.get("reason"), str)
    ):
        return "implementation", "M5B iteration worker failure terminal drifted"
    return cast(str, value["failure_class"]), cast(str, value["reason"])


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
    attempt_id = study["attempt_id"]
    attempt_root = study_root / attempt_id
    artifacts = attempt_root / "artifacts"
    try:
        study_root.mkdir(parents=True, exist_ok=True)
        if attempt_root.exists():
            raise ContractValidationError("M5B attempt directory already exists")
        attempt_root.mkdir(mode=0o700)
        artifacts.mkdir(mode=0o700)
        contract_path = study_root / "contract.md"
        if not contract_path.exists():
            _write_exclusive(contract_path, _CONTRACT_BYTES)
        elif contract_path.read_bytes() != _CONTRACT_BYTES:
            raise ContractValidationError("M5B study contract identity drifted")
        _write_exclusive(attempt_root / "authorization.json", loaded.canonical_bytes)
    except (OSError, ContractValidationError) as exc:
        return _not_started(str(exc)), 2

    command = _worker_command(value, artifacts, loaded.sha256)
    _write_exclusive(attempt_root / "command.txt", (shlex.join(command) + "\n").encode())
    started_at = _utc_now()
    started = time.monotonic()
    status = "failed"
    failure_class = "implementation"
    failure_reason: str | None = None
    result_summary: str | None = None
    terminal: dict[str, object]
    try:
        caps = cast(dict[str, object], value["resource_caps"])
        completed = _run_worker(command, cast(int, caps["wall_seconds"]))
        _write_exclusive(attempt_root / "stdout.log", completed.stdout)
        _write_exclusive(attempt_root / "stderr.log", completed.stderr)
        if completed.returncode != 0:
            failure_class, worker_reason = _worker_failure(completed.stderr)
            raise ContractValidationError(worker_reason)
        result_bytes = (artifacts / "result.json").read_bytes()
        inventory_bytes = (artifacts / "inventory.json").read_bytes()
        result = json.loads(result_bytes)
        audit = json.loads((artifacts / "audit.json").read_bytes())
        elapsed = time.monotonic() - started
        estimated_cost = (
            elapsed / 3600 * float(cast(str | float | int, caps["estimated_hourly_cost_cny"]))
        )
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
        if estimated_cost > float(cast(str | float | int, caps["maximum_cost_cny"])):
            failure_class = "budget"
            raise ContractValidationError("M5B iteration coarse cost cap exceeded")
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
    except (OSError, ContractValidationError, ValueError) as exc:
        if isinstance(exc, OSError):
            failure_class = "transient-infrastructure"
        elif "cap exceeded" in str(exc):
            failure_class = "budget"
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
        "parent_attempt_id": None,
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
    return terminal, 0 if status == "succeeded" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    args = parser.parse_args()
    terminal, exit_code = _run(args.authorization)
    sys.stdout.buffer.write(compact_json_bytes(terminal))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
