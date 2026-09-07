#!/usr/bin/env python3
"""Guard and record one append-only M5C ProteinGym main attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shlex
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pcdb.m5.proteingym_confirmation import compact_json_bytes
from pcdb.m5.proteingym_runtime import build_run_identity, write_once

EXPECTED_CONTRACT_DIGEST = "abc16ed38664d63efede535de3ecd8b8552031e52b156c37023e7b7e717d5278"
EXPECTED_INPUT_MANIFEST_DIGEST = "130d9f3d9086274c7209158cdcbb3a4054ff3742e3711220542adecae14828fc"
STUDY_ROOT = Path("/srv/pcdb/runs/M5C")
WALL_CAP_SECONDS = 86_400
CPU_CAP_SECONDS = 1_382_400
RSS_CAP_BYTES = 48_000_000_000
SCRATCH_CAP_BYTES = 32_212_254_720


class AttemptFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_class: str,
        cpu_seconds: int = 0,
        peak_rss_bytes: int = 0,
        scratch_bytes: int = 0,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.cpu_seconds = cpu_seconds
        self.peak_rss_bytes = peak_rss_bytes
        self.scratch_bytes = scratch_bytes


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_identity(source: Path, commit: str, tree: str) -> None:
    observed = (
        _git(source, "rev-parse", "HEAD"),
        _git(source, "rev-parse", "HEAD^{tree}"),
        _git(source, "status", "--porcelain"),
    )
    if observed != (commit, tree, ""):
        raise AttemptFailure(
            "reviewed source commit/tree/clean identity drifted", failure_class="validity"
        )


def _environment(python: Path) -> tuple[str, bytes]:
    version = subprocess.run(
        (str(python), "--version"), check=True, capture_output=True, text=True
    ).stdout.strip()
    frozen = subprocess.run(
        (str(python), "-m", "pip", "freeze", "--all"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    value = {
        "hostname": socket.gethostname(),
        "machine": platform.machine(),
        "packages": sorted(frozen),
        "python": version,
        "schema_version": "m5c-runtime-environment-v1",
        "thread_limits": {
            "BLIS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
        },
    }
    payload = compact_json_bytes(value)
    return hashlib.sha256(payload).hexdigest(), payload


def _worker_environment(source: Path) -> dict[str, str]:
    value = os.environ.copy()
    value.update(
        {
            "BLIS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "PYTHONPATH": str(source / "src"),
            "VECLIB_MAXIMUM_THREADS": "1",
        }
    )
    return value


def _run_check(command: tuple[str, ...], name: str, environment: dict[str, str]) -> bytes:
    completed = subprocess.run(command, capture_output=True, env=environment)
    if completed.returncode:
        reason = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AttemptFailure(f"{name} failed: {reason}", failure_class="validity")
    try:
        value = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AttemptFailure(f"{name} emitted invalid JSON", failure_class="validity") from None
    if value.get("overall_status") != "pass":
        raise AttemptFailure(f"{name} did not pass", failure_class="validity")
    return completed.stdout


def _contract_command(
    args: argparse.Namespace,
    *,
    run_identity: Path | None,
    environment_digest: str | None,
) -> tuple[str, ...]:
    command = [
        str(args.python),
        "-m",
        "pcdb.cli",
        "governance-check",
        "contract",
        "--manifest",
        str(args.contract_manifest),
        "--gate-a",
        str(args.gate_a),
        "--config",
        str(args.governance_config),
        "--source-root",
        str(args.source_root),
        "--runtime-git-commit",
        args.expected_git_commit,
        "--runtime-git-tree",
        args.expected_git_tree,
    ]
    if run_identity is not None and environment_digest is not None:
        command.extend(
            (
                "--run-identity",
                str(run_identity),
                "--runtime-environment-digest",
                environment_digest,
            )
        )
    return tuple(command)


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _cpu_time_seconds(value: str) -> int:
    days = 0
    clock = value.strip()
    if "-" in clock:
        day_text, clock = clock.split("-", 1)
        days = int(day_text)
    fields = [int(item) for item in clock.split(":")]
    if len(fields) == 2:
        hours, minutes, seconds = 0, fields[0], fields[1]
    else:
        hours, minutes, seconds = fields
    return days * 86_400 + hours * 3_600 + minutes * 60 + seconds


def _process_group_usage(process_group: int) -> tuple[int, int]:
    completed = subprocess.run(
        ("ps", "-o", "rss=,time=", "-g", str(process_group)),
        check=False,
        capture_output=True,
        text=True,
    )
    rss_kib = 0
    cpu_seconds = 0
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2:
            rss_kib += int(fields[0])
            cpu_seconds += _cpu_time_seconds(fields[1])
    return rss_kib * 1024, cpu_seconds


def _limit_child() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (RSS_CAP_BYTES, RSS_CAP_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (WALL_CAP_SECONDS, WALL_CAP_SECONDS))


def _run_worker(
    command: tuple[str, ...],
    *,
    attempt_root: Path,
    environment: dict[str, str],
) -> tuple[subprocess.CompletedProcess[bytes], int, int, int]:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
            preexec_fn=_limit_child,
        )
    except OSError as exc:
        raise AttemptFailure(
            "M5C worker launch failed", failure_class="transient-infrastructure"
        ) from exc
    started = time.monotonic()
    peak_rss = 0
    cpu_high_water = 0
    scratch_high_water = 0
    while process.poll() is None:
        elapsed = time.monotonic() - started
        rss_bytes, cpu_seconds = _process_group_usage(process.pid)
        peak_rss = max(peak_rss, rss_bytes)
        cpu_high_water = max(cpu_high_water, cpu_seconds)
        scratch_high_water = max(scratch_high_water, _directory_bytes(attempt_root))
        if elapsed > WALL_CAP_SECONDS:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise AttemptFailure(
                "M5C cumulative wall cap exceeded",
                failure_class="budget",
                cpu_seconds=cpu_high_water,
                peak_rss_bytes=peak_rss,
                scratch_bytes=scratch_high_water,
            )
        if peak_rss > RSS_CAP_BYTES:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise AttemptFailure(
                "M5C process-tree RSS cap exceeded",
                failure_class="budget",
                cpu_seconds=cpu_high_water,
                peak_rss_bytes=peak_rss,
                scratch_bytes=scratch_high_water,
            )
        if cpu_high_water > CPU_CAP_SECONDS:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise AttemptFailure(
                "M5C cumulative CPU cap exceeded",
                failure_class="budget",
                cpu_seconds=cpu_high_water,
                peak_rss_bytes=peak_rss,
                scratch_bytes=scratch_high_water,
            )
        if scratch_high_water > SCRATCH_CAP_BYTES:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise AttemptFailure(
                "M5C scratch high-water cap exceeded",
                failure_class="budget",
                cpu_seconds=cpu_high_water,
                peak_rss_bytes=peak_rss,
                scratch_bytes=scratch_high_water,
            )
        time.sleep(5)
    stdout, stderr = process.communicate()
    return (
        subprocess.CompletedProcess(command, process.returncode, stdout, stderr),
        cpu_high_water,
        peak_rss,
        scratch_high_water,
    )


def _append_ledger(path: Path, value: dict[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(compact_json_bytes(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _validate_attempt(args: argparse.Namespace) -> str | None:
    if not STUDY_ROOT.is_dir():
        raise AttemptFailure("M5C server namespace is not provisioned", failure_class="validity")
    processes = subprocess.run(
        ("ps", "-eo", "pid=,ppid=,args="),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    parsed: dict[int, tuple[int, str]] = {}
    for line in processes:
        fields = line.strip().split(maxsplit=2)
        if len(fields) == 3:
            parsed[int(fields[0])] = (int(fields[1]), fields[2])
    excluded = {os.getpid()}
    parent = os.getppid()
    while parent and parent not in excluded:
        excluded.add(parent)
        parent = parsed.get(parent, (0, ""))[0]
    active = []
    for process_id, (_, command) in parsed.items():
        if process_id in excluded:
            continue
        if any(
            name in command
            for name in (
                "m5c_proteingym_worker.py",
                "reproduce_m5c_proteingym.py",
                "run_m5c_proteingym.py",
            )
        ):
            active.append(process_id)
    if active:
        raise AttemptFailure("another M5C execution process is active", failure_class="validity")
    successes = tuple(STUDY_ROOT.glob("attempt-*/_SUCCESS"))
    if successes:
        raise AttemptFailure(
            "M5C already has a canonical successful terminal", failure_class="validity"
        )
    prior = tuple(sorted(path for path in STUDY_ROOT.glob("attempt-*") if path.is_dir()))
    if args.attempt_id == "attempt-001":
        if prior:
            raise AttemptFailure(
                "attempt-001 is not the append-only first attempt", failure_class="validity"
            )
        return None
    if args.attempt_id != "attempt-002" or len(prior) != 1 or prior[0].name != "attempt-001":
        raise AttemptFailure(
            "M5C attempt exceeds the two-attempt contract", failure_class="validity"
        )
    try:
        terminal = json.loads((prior[0] / "terminal.json").read_bytes())
    except (OSError, json.JSONDecodeError):
        raise AttemptFailure(
            "M5C retry parent terminal is invalid", failure_class="validity"
        ) from None
    permitted = terminal.get("failure_class") == "transient-infrastructure" or (
        args.reviewed_repair and terminal.get("complete_scientific_result") is False
    )
    if terminal.get("status") != "STOPPED" or not permitted:
        raise AttemptFailure(
            "M5C retry is not transient or pre-result reviewed repair", failure_class="validity"
        )
    return "attempt-001"


def _validate_retry_identity(
    args: argparse.Namespace,
    *,
    parent_attempt: str | None,
    environment_digest: str,
) -> None:
    if parent_attempt is None:
        return
    try:
        prior = json.loads((STUDY_ROOT / parent_attempt / "run-identity.json").read_bytes())
    except (OSError, json.JSONDecodeError):
        raise AttemptFailure(
            "M5C retry parent identity is invalid", failure_class="validity"
        ) from None
    if (
        prior.get("contract_digest") != EXPECTED_CONTRACT_DIGEST
        or prior.get("input_manifest_digest") != EXPECTED_INPUT_MANIFEST_DIGEST
    ):
        raise AttemptFailure("M5C retry contract/input identity drifted", failure_class="validity")
    same_source = (
        prior.get("git_commit") == args.expected_git_commit
        and prior.get("git_tree") == args.expected_git_tree
    )
    if args.reviewed_repair:
        if same_source:
            raise AttemptFailure(
                "M5C reviewed repair requires a new source identity", failure_class="validity"
            )
    elif not same_source or prior.get("environment_digest") != environment_digest:
        raise AttemptFailure(
            "M5C transient retry must preserve source/environment identity",
            failure_class="validity",
        )


def _run(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    if socket.gethostname() != "ps":
        raise AttemptFailure(
            "formal M5C execution host must have kernel hostname ps", failure_class="validity"
        )
    expected_paths = {
        args.contract_manifest: args.source_root
        / "manifests/experiments/m5c/gate-a-proposal-v3/milestone-contract-manifest.json",
        args.gate_a: args.source_root
        / "manifests/experiments/m5c/gate-a-proposal-v3/gate-a-decision.proposal.json",
        args.governance_config: args.source_root / "configs/governance/v2.json",
        args.input_manifest: args.source_root
        / "manifests/experiments/m5c/preflight-result-v5/input-capability-manifest.json",
    }
    if any(
        actual.resolve() != expected.resolve() for actual, expected in expected_paths.items()
    ) or (
        args.input_root.resolve()
        != Path("/srv/pcdb/data/raw/proteingym/v1.3/four-assay-unified-v1").resolve()
    ):
        raise AttemptFailure("M5C source or staged input path drifted", failure_class="validity")
    _source_identity(args.source_root, args.expected_git_commit, args.expected_git_tree)
    input_bytes = args.input_manifest.read_bytes()
    if hashlib.sha256(input_bytes).hexdigest() != EXPECTED_INPUT_MANIFEST_DIGEST:
        raise AttemptFailure(
            "authorized input-capability manifest drifted", failure_class="validity"
        )
    manifest = json.loads(args.contract_manifest.read_bytes())
    if manifest.get("contract_digest") != EXPECTED_CONTRACT_DIGEST:
        raise AttemptFailure(
            "authorized scientific contract digest drifted", failure_class="validity"
        )
    parent_attempt = _validate_attempt(args)
    environment_digest, environment_payload = _environment(args.python)
    _validate_retry_identity(
        args,
        parent_attempt=parent_attempt,
        environment_digest=environment_digest,
    )
    environment = _worker_environment(args.source_root)
    gate_check = _run_check(
        _contract_command(args, run_identity=None, environment_digest=None),
        "fresh Gate A contract Check",
        environment,
    )
    server_check = _run_check(
        (
            str(args.python),
            "-m",
            "pcdb.cli",
            "governance-check",
            "server",
            "--config",
            str(args.governance_config),
            "--expected-peak-bytes",
            str(RSS_CAP_BYTES),
        ),
        "target server preflight",
        environment,
    )
    identity_bytes = build_run_identity(
        attempt_id=args.attempt_id,
        contract_digest=EXPECTED_CONTRACT_DIGEST,
        input_manifest_digest=EXPECTED_INPUT_MANIFEST_DIGEST,
        git_commit=args.expected_git_commit,
        git_tree=args.expected_git_tree,
        environment_digest=environment_digest,
    )
    if args.preflight_only:
        return {
            "environment_digest": environment_digest,
            "prospective_run_identity_sha256": hashlib.sha256(identity_bytes).hexdigest(),
            "schema_version": "m5c-proteingym-preflight-v1",
            "status": "PASS",
        }, 0

    attempt_root = STUDY_ROOT / args.attempt_id
    attempt_root.mkdir(mode=0o700)
    identity_path = attempt_root / "run-identity.json"
    write_once(identity_path, identity_bytes)
    write_once(attempt_root / "environment.json", environment_payload)
    write_once(attempt_root / "gate-check.json", gate_check)
    write_once(attempt_root / "server-check.json", server_check)
    command = (
        str(args.python),
        str(args.source_root / "scripts" / "m5c_proteingym_worker.py"),
        "--attempt-root",
        str(attempt_root),
        "--input-manifest",
        str(args.input_manifest),
        "--input-manifest-sha256",
        EXPECTED_INPUT_MANIFEST_DIGEST,
        "--input-root",
        str(args.input_root),
        "--workers",
        str(args.workers),
    )
    started_at = _utc_now()
    started = time.monotonic()
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    status = "failed"
    failure_class = "implementation"
    failure_reason: str | None = None
    result: dict[str, Any] | None = None
    peak_rss = 0
    monitored_cpu_seconds = 0
    scratch_high_water = 0
    try:
        run_check = _run_check(
            _contract_command(
                args,
                run_identity=identity_path,
                environment_digest=environment_digest,
            ),
            "Run-pinned contract Check",
            environment,
        )
        write_once(attempt_root / "run-check.json", run_check)
        write_once(attempt_root / "command.txt", (shlex.join(command) + "\n").encode())
        completed, monitored_cpu_seconds, peak_rss, scratch_high_water = _run_worker(
            command,
            attempt_root=attempt_root,
            environment=environment,
        )
        write_once(attempt_root / "stdout.log", completed.stdout)
        write_once(attempt_root / "stderr.log", completed.stderr)
        if completed.returncode:
            try:
                failure = json.loads(completed.stderr)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise AttemptFailure(
                    "M5C worker failed without structured evidence", failure_class="implementation"
                ) from None
            raise AttemptFailure(
                str(failure.get("reason", "M5C worker failed")),
                failure_class=str(failure.get("failure_class", "implementation")),
            )
        result = json.loads(completed.stdout)
        aggregate = attempt_root / "aggregate" / "aggregate-input.json"
        audit = attempt_root / "aggregate" / "audit.json"
        report = attempt_root / "aggregate" / "report.json"
        inventory = attempt_root / "aggregate" / "inventory.json"
        if (
            result.get("schema_version") != "m5c-proteingym-worker-success-v1"
            or result.get("assay_count") != 4
            or result.get("state_count") != 160
            or result.get("aggregate_input_sha256")
            != hashlib.sha256(aggregate.read_bytes()).hexdigest()
            or result.get("audit_sha256") != hashlib.sha256(audit.read_bytes()).hexdigest()
            or result.get("report_sha256") != hashlib.sha256(report.read_bytes()).hexdigest()
            or result.get("inventory_sha256") != hashlib.sha256(inventory.read_bytes()).hexdigest()
        ):
            raise AttemptFailure("M5C worker terminal identity drifted", failure_class="validity")
        status = "succeeded"
        failure_class = "none"
    except (AttemptFailure, OSError, json.JSONDecodeError) as exc:
        failure_class = exc.failure_class if isinstance(exc, AttemptFailure) else "implementation"
        if isinstance(exc, AttemptFailure):
            monitored_cpu_seconds = max(monitored_cpu_seconds, exc.cpu_seconds)
            peak_rss = max(peak_rss, exc.peak_rss_bytes)
            scratch_high_water = max(scratch_high_water, exc.scratch_bytes)
        failure_reason = str(exc)
        for name in ("stdout.log", "stderr.log"):
            path = attempt_root / name
            if not path.exists():
                write_once(path, b"")
    finished_at = _utc_now()
    children_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    child_cpu_seconds = (
        children_after.ru_utime
        + children_after.ru_stime
        - children_before.ru_utime
        - children_before.ru_stime
    )
    cpu_seconds = max(child_cpu_seconds, monitored_cpu_seconds)
    terminal: dict[str, object] = {
        "attempt_id": args.attempt_id,
        "complete_scientific_result": status == "succeeded",
        "failure_class": failure_class,
        "failure_reason": failure_reason,
        "finished_at": finished_at,
        "observed_usage": {
            "cost_cny": 0,
            "cpu_seconds": cpu_seconds,
            "network_bytes_upper_bound": 1_048_576,
            "peak_rss_bytes": peak_rss,
            "scratch_bytes": scratch_high_water,
            "wall_seconds": time.monotonic() - started,
        },
        "parent_attempt_id": parent_attempt,
        "result": result,
        "schema_version": "m5c-proteingym-main-terminal-v1",
        "started_at": started_at,
        "status": "COMPLETE_FOR_SCIENTIFIC_VALIDITY_REVIEW" if status == "succeeded" else "STOPPED",
    }
    terminal_bytes = compact_json_bytes(terminal)
    write_once(attempt_root / "terminal.json", terminal_bytes)
    _append_ledger(
        STUDY_ROOT / "attempts.jsonl",
        {
            "attempt_id": args.attempt_id,
            "executed_git_commit": args.expected_git_commit,
            "failure_class": failure_class,
            "finished_at": finished_at,
            "parent_attempt_id": parent_attempt,
            "selection_disposition": "canonical" if status == "succeeded" else "discarded",
            "started_at": started_at,
            "status": status,
            "study_id": "m5c-proteingym-prospective-v1",
            "terminal_sha256": hashlib.sha256(terminal_bytes).hexdigest(),
        },
    )
    if status == "succeeded":
        write_once(attempt_root / "_SUCCESS", b"")
    return terminal, 0 if status == "succeeded" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", choices=("attempt-001", "attempt-002"), required=True)
    parser.add_argument("--contract-manifest", required=True, type=Path)
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--expected-git-tree", required=True)
    parser.add_argument("--gate-a", required=True, type=Path)
    parser.add_argument("--governance-config", required=True, type=Path)
    parser.add_argument("--input-manifest", required=True, type=Path)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--reviewed-repair", action="store_true")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be in [1,16]")
    try:
        terminal, exit_code = _run(args)
    except (AttemptFailure, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        terminal = {
            "complete_scientific_result": False,
            "reason": str(exc),
            "schema_version": "m5c-proteingym-not-started-v1",
            "status": "NOT_STARTED",
        }
        exit_code = 2
    sys.stdout.buffer.write(compact_json_bytes(terminal))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
