#!/usr/bin/env python3
"""Run the exact-select 160-trajectory formal M5A reconstruction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pcdb.contracts.experiment import ContractValidationError
from pcdb.m5.formal_reconstruction import (
    build_formal_inventory,
    build_formal_report,
    load_formal_contract,
)
from pcdb.m5.reconstruction_smoke import compact_json_bytes

STUDY_ID = "m5a-formal-reconstruction-v1"


class AttemptFailure(RuntimeError):
    def __init__(self, message: str, *, failure_class: str) -> None:
        super().__init__(message)
        self.failure_class = failure_class


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


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_identity(source: Path, expected: Mapping[str, Any]) -> dict[str, str]:
    value = {
        "git_commit": _git(source, "rev-parse", "HEAD"),
        "git_tree": _git(source, "rev-parse", "HEAD^{tree}"),
        "tracked_status": _git(source, "status", "--porcelain", "--untracked-files=no"),
    }
    expected_value = {
        "git_commit": expected["git_commit"],
        "git_tree": expected["git_tree"],
        "tracked_status": "",
    }
    if value != expected_value:
        raise AttemptFailure(
            "historical source commit/tree/clean identity drifted",
            failure_class="validity",
        )
    return value


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _rss_bytes(who: int) -> int:
    value = resource.getrusage(who).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)


def _environment_digest(python: Path) -> str:
    version = subprocess.run(
        (str(python), "--version"),
        check=True,
        capture_output=True,
        text=True,
    )
    frozen = subprocess.run(
        (str(python), "-m", "pip", "freeze", "--all"),
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(
        version.stdout.encode() + version.stderr.encode() + frozen.stdout
    ).hexdigest()


def _worker_environment() -> dict[str, str]:
    value = os.environ.copy()
    value.update(
        {
            "BLIS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
        }
    )
    return value


def _validate_attempt_slot(study_root: Path, attempt_id: str) -> str | None:
    prior = sorted(study_root.glob("attempt-*")) if study_root.exists() else []
    if attempt_id == "attempt-001":
        if prior:
            raise AttemptFailure("attempt-001 is not append-only first", failure_class="validity")
        return None
    if attempt_id != "attempt-002" or len(prior) != 1:
        raise AttemptFailure("attempt ID exceeds retry contract", failure_class="validity")
    ledger = study_root / "attempts.jsonl"
    if not ledger.exists():
        raise AttemptFailure("retry lacks parent ledger", failure_class="validity")
    parent = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    if parent.get("failure_class") != "transient-infrastructure":
        raise AttemptFailure("retry parent is not infrastructure-only", failure_class="validity")
    return str(parent["attempt_id"])


def _run(args: argparse.Namespace) -> int:
    contract_bytes = args.contract.read_bytes()
    contract = load_formal_contract(contract_bytes)
    sources = {"pilot": args.pilot_source, "conditional-expansion": args.conditional_source}
    stores = {"pilot": args.pilot_store_root, "conditional-expansion": args.conditional_store_root}
    for name, source in sources.items():
        _source_identity(source, contract["sources"][name])
    inventory = build_formal_inventory(
        Path(__file__).resolve().parents[1],
        contract=contract,
        store_roots=stores,
    )
    preflight = {
        "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
        "excluded_attempts": len(inventory.excluded),
        "inventory_sha256": inventory.sha256,
        "schema_version": "m5a-formal-preflight-v1",
        "selected_trajectories": len(inventory.selected),
        "successful_attempt_inventory": len(inventory.selected) + len(inventory.excluded),
    }
    if args.preflight_only:
        sys.stdout.buffer.write(compact_json_bytes(preflight))
        return 0

    parent_attempt_id = _validate_attempt_slot(args.study_root, args.attempt_id)
    args.study_root.mkdir(parents=True, exist_ok=True)
    attempt_dir = args.study_root / args.attempt_id
    if attempt_dir.exists():
        raise AttemptFailure("attempt directory already exists", failure_class="validity")
    if args.attempt_id == "attempt-001":
        _write_exclusive(
            args.study_root / "contract.md",
            (
                "# Formal M5A reconstruction v1\n\n"
                f"Contract SHA-256: `{preflight['contract_sha256']}`.\n\n"
                "Exact-select 160 historical trajectories and 800 decision states. No new "
                "RunSpec, benchmark attempt, oracle reveal, held-out access, COS publication, "
                "M5B execution, or scientific-mechanism confirmation.\n"
            ).encode(),
        )
    attempt_dir.mkdir(mode=0o700)
    execution_root = Path(__file__).resolve().parents[1]
    execution_commit = _git(execution_root, "rev-parse", "HEAD")
    execution_tree = _git(execution_root, "rev-parse", "HEAD^{tree}")
    if _git(execution_root, "status", "--porcelain", "--untracked-files=no"):
        raise AttemptFailure("execution checkout has tracked changes", failure_class="validity")
    environment_digest = _environment_digest(args.python)
    command = shlex.join(sys.argv)
    _write_exclusive(attempt_dir / "command.txt", (command + "\n").encode())
    _write_exclusive(attempt_dir / "preflight.json", compact_json_bytes(preflight))
    _write_exclusive(attempt_dir / "inventory.json", inventory.canonical_bytes)

    started_at = _utc_now()
    active_started = time.monotonic()
    cap = contract["resource_caps"]
    peak_rss = _rss_bytes(resource.RUSAGE_SELF)
    scratch_high_water = _directory_bytes(attempt_dir)
    trajectory_wall: dict[int, float] = {index: 0.0 for index in range(160)}
    status = "failed"
    failure_class = "implementation"
    failure_reason: str | None = None
    result_summary: str | None = None
    try:
        reports: list[bytes] = []
        aggregate_hashes: list[str] = []
        for label in ("A", "B"):
            aggregate_root = attempt_dir / f"aggregate-{label}"
            aggregate_root.mkdir()
            payloads: list[bytes] = []
            for index, item in enumerate(inventory.selected):
                elapsed = time.monotonic() - active_started
                if elapsed >= cap["attempt_wall_seconds"]:
                    raise AttemptFailure("attempt wall cap reached", failure_class="budget")
                remaining_trajectory = (
                    cap["trajectory_two_run_cumulative_wall_seconds"] - trajectory_wall[index]
                )
                timeout = min(remaining_trajectory, cap["attempt_wall_seconds"] - elapsed)
                if timeout <= 0:
                    raise AttemptFailure("trajectory wall cap reached", failure_class="budget")
                feature_path = (
                    args.feature_root
                    / item.prepared.task_id
                    / "sequence-composition-420-v1"
                    / "candidate-pool.csv"
                )
                output = aggregate_root / f"T{index:03d}.json"
                source_contract = contract["sources"][item.source]
                worker_command = (
                    str(args.python),
                    str(args.worker_script),
                    "--historical-source",
                    str(sources[item.source]),
                    "--source-commit",
                    source_contract["git_commit"],
                    "--source-tree",
                    source_contract["git_tree"],
                    "--trajectory",
                    str(item.trajectory_path),
                    "--trajectory-sha256",
                    item.trajectory_sha256,
                    "--candidate-features",
                    str(feature_path),
                    "--feature-sha256",
                    contract["features"][item.prepared.task_id]["sha256"],
                    "--output",
                    str(output),
                )
                worker_started = time.monotonic()
                try:
                    completed = subprocess.run(
                        worker_command,
                        capture_output=True,
                        env=_worker_environment(),
                        timeout=timeout,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise AttemptFailure(
                        "trajectory worker wall cap exceeded",
                        failure_class="budget",
                    ) from exc
                trajectory_wall[index] += time.monotonic() - worker_started
                _write_exclusive(aggregate_root / f"T{index:03d}.stdout", completed.stdout)
                _write_exclusive(aggregate_root / f"T{index:03d}.stderr", completed.stderr)
                if completed.returncode != 0:
                    raise AttemptFailure(
                        f"worker {label}/T{index:03d} exited {completed.returncode}",
                        failure_class="implementation",
                    )
                payloads.append(output.read_bytes())
                peak_rss = max(
                    peak_rss,
                    _rss_bytes(resource.RUSAGE_SELF),
                    _rss_bytes(resource.RUSAGE_CHILDREN),
                )
                scratch_high_water = max(scratch_high_water, _directory_bytes(attempt_dir))
                if peak_rss > cap["process_tree_peak_rss_bytes"]:
                    raise AttemptFailure("process-tree RSS cap exceeded", failure_class="budget")
                if scratch_high_water > cap["scratch_high_water_bytes"]:
                    raise AttemptFailure("scratch high-water cap exceeded", failure_class="budget")
            report = build_formal_report(
                payloads,
                inventory_sha256=inventory.sha256,
                analysis_commit=execution_commit,
                analysis_tree=execution_tree,
                environment_digest=environment_digest,
            )
            _write_exclusive(attempt_dir / f"report-{label}.json", report)
            reports.append(report)
            aggregate_hashes.append(hashlib.sha256(b"".join(payloads)).hexdigest())
        if reports[0] != reports[1]:
            raise AttemptFailure(
                "repeated aggregate reports are not byte-identical",
                failure_class="validity",
            )
        active_wall = time.monotonic() - active_started
        if active_wall > cap["attempt_wall_seconds"]:
            raise AttemptFailure("attempt wall cap exceeded", failure_class="budget")
        result = {
            "active_execution_network_bytes": 0,
            "active_execution_wall_seconds": active_wall,
            "aggregate_input_sha256": aggregate_hashes,
            "decision_states_per_aggregate": 800,
            "excluded_successful_attempts": 184,
            "inventory_sha256": inventory.sha256,
            "peak_process_tree_rss_bytes": peak_rss,
            "report_byte_identical": True,
            "report_sha256": hashlib.sha256(reports[0]).hexdigest(),
            "schema_version": "m5a-formal-result-v1",
            "scratch_high_water_bytes": scratch_high_water,
            "selected_trajectories": 160,
            "verdict": "RECONSTRUCTION_VALID",
        }
        _write_exclusive(attempt_dir / "result.json", compact_json_bytes(result))
        _write_exclusive(attempt_dir / "_SUCCESS", b"")
        status = "succeeded"
        failure_class = "none"
        result_summary = (
            "RECONSTRUCTION_VALID: 160 trajectories, 800 states, two byte-identical aggregates"
        )
    except (ContractValidationError, AttemptFailure, ValueError) as exc:
        active_wall = time.monotonic() - active_started
        failure_class = exc.failure_class if isinstance(exc, AttemptFailure) else "validity"
        failure_reason = str(exc)
    ledger = {
        "attempt_id": args.attempt_id,
        "command": command,
        "config_identity": [
            f"{args.contract}:{preflight['contract_sha256']}",
            f"inventory:{inventory.sha256}",
        ],
        "cost_summary": {
            "active_wall_seconds": active_wall,
            "estimated_hourly_cost_cny": cap["estimated_hourly_cost_cny"],
            "estimated_maximum_cost_cny": cap["estimated_maximum_cost_cny"],
        },
        "data_scope": "historical-revealed-observations-only;no-held-out;no-oracle;no-COS",
        "failure_class": failure_class,
        "failure_reason": failure_reason,
        "finished_at": _utc_now(),
        "git_commit": execution_commit,
        "git_tree": execution_tree,
        "input_identity": [preflight["contract_sha256"], inventory.sha256, environment_digest],
        "output_paths": [
            str(attempt_dir / name)
            for name in ("result.json", "report-A.json", "report-B.json")
            if (attempt_dir / name).exists()
        ],
        "parent_attempt_id": parent_attempt_id,
        "result_summary": result_summary,
        "schema_version": "research-iteration-attempt-ledger-v1",
        "started_at": started_at,
        "status": status,
        "study_id": STUDY_ID,
    }
    _append(args.study_root / "attempts.jsonl", compact_json_bytes(ledger))
    if status != "succeeded":
        print(compact_json_bytes(ledger).decode(), file=sys.stderr, end="")
        return 1
    print(compact_json_bytes(ledger).decode(), end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--attempt-id", default="attempt-001")
    parser.add_argument("--pilot-store-root", type=Path, required=True)
    parser.add_argument("--conditional-store-root", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--pilot-source", type=Path, required=True)
    parser.add_argument("--conditional-source", type=Path, required=True)
    parser.add_argument("--worker-script", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    return _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
