#!/usr/bin/env python3
"""Run the accepted server-local eight-state M5A reconstruction smoke."""

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
from pcdb.m5.reconstruction_smoke import (
    ReconstructionStop,
    compact_json_bytes,
    compare_reconstruction_bytes,
    load_reconstruction_contract,
    validate_historical_state,
)

STUDY_ID = "m5a-reconstruction-smoke-v1"


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


def _append_line(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _peak_child_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)


def _peak_self_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)


def _git_value(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_identity(source: Path, expected: Mapping[str, Any]) -> dict[str, str]:
    actual = {
        "git_commit": _git_value(source, "rev-parse", "HEAD"),
        "git_tree": _git_value(source, "rev-parse", "HEAD^{tree}"),
        "tracked_status": _git_value(source, "status", "--porcelain", "--untracked-files=no"),
    }
    if (
        actual["git_commit"] != expected["git_commit"]
        or actual["git_tree"] != expected["git_tree"]
        or actual["tracked_status"]
    ):
        raise ReconstructionStop("historical source commit/tree/clean identity drifted")
    return actual


def _artifact_attempt_root(
    state: Mapping[str, Any],
    contract: Mapping[str, Any],
    store_roots: Mapping[str, Path],
) -> Path:
    source = state["source"]
    phase = "pilot" if source == "pilot" else "conditional-expansion"
    return (
        store_roots[source]
        / "objects"
        / "m4"
        / "issue-76"
        / phase
        / contract["sources"][source]["packet_sha256"]
        / "runs"
        / state["run_id"]
        / "attempts"
        / "attempt-001"
    )


def _load_validated_states(
    *,
    contract: Mapping[str, Any],
    store_roots: Mapping[str, Path],
    feature_root: Path,
) -> list[tuple[Mapping[str, Any], Any]]:
    feature_bytes: dict[str, bytes] = {}
    validated: list[tuple[Mapping[str, Any], Any]] = []
    for state in contract["states"]:
        task_id = state["task_id"]
        feature_bytes.setdefault(
            task_id,
            (
                feature_root / task_id / "sequence-composition-420-v1" / "candidate-pool.csv"
            ).read_bytes(),
        )
        attempt_root = _artifact_attempt_root(state, contract, store_roots)
        item = validate_historical_state(
            contract=contract,
            state=state,
            trajectory_bytes=(attempt_root / "trajectory.json").read_bytes(),
            run_attempt_bytes=(attempt_root / "run-attempt.json").read_bytes(),
            completion_marker_bytes=(attempt_root / "_SUCCESS").read_bytes(),
            candidate_feature_bytes=feature_bytes[task_id],
        )
        validated.append((state, item))
    return validated


def _preflight_value(
    *,
    contract: Mapping[str, Any],
    contract_bytes: bytes,
    validated: list[tuple[Mapping[str, Any], Any]],
    sources: Mapping[str, Path],
) -> dict[str, Any]:
    return {
        "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
        "historical_sources": {
            name: _source_identity(path, contract["sources"][name])
            for name, path in sources.items()
        },
        "schema_version": "m5a-reconstruction-smoke-preflight-v1",
        "state_count": len(validated),
        "states": [
            {
                "actual_selected_ids_sha256": state["actual_selected_ids_sha256"],
                "input_sha256": hashlib.sha256(item.prepared.input_bytes).hexdigest(),
                "prefix_observation_count": state["prefix_observation_count"],
                "remaining_count": len(item.candidate_features.candidate_ids)
                - state["prefix_observation_count"],
                "run_id": state["run_id"],
                "state_id": state["state_id"],
            }
            for state, item in validated
        ],
    }


def _worker_environment() -> dict[str, str]:
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
    return environment


def _run_attempt(args: argparse.Namespace) -> int:
    contract_bytes = args.contract.read_bytes()
    contract = load_reconstruction_contract(contract_bytes)
    sources = {"pilot": args.pilot_source, "conditional-expansion": args.conditional_source}
    stores = {
        "pilot": args.pilot_store_root,
        "conditional-expansion": args.conditional_store_root,
    }
    validated = _load_validated_states(
        contract=contract,
        store_roots=stores,
        feature_root=args.feature_root,
    )
    preflight = _preflight_value(
        contract=contract,
        contract_bytes=contract_bytes,
        validated=validated,
        sources=sources,
    )
    if args.preflight_only:
        sys.stdout.buffer.write(compact_json_bytes(preflight))
        return 0

    args.study_root.mkdir(parents=True, exist_ok=True)
    attempt_dir = args.study_root / args.attempt_id
    if attempt_dir.exists():
        raise AttemptFailure("attempt directory already exists", failure_class="validity")
    prior = sorted(args.study_root.glob("attempt-*"))
    if args.attempt_id == "attempt-001":
        if prior:
            raise AttemptFailure(
                "attempt-001 is not the first append-only attempt", failure_class="validity"
            )
        parent_attempt_id = None
    elif args.attempt_id == "attempt-002":
        ledger = args.study_root / "attempts.jsonl"
        if len(prior) != 1 or not ledger.exists():
            raise AttemptFailure(
                "attempt-002 lacks exactly one parent attempt", failure_class="validity"
            )
        last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
        if last.get("failure_class") != "transient-infrastructure":
            raise AttemptFailure(
                "retry parent is not transient-infrastructure", failure_class="validity"
            )
        parent_attempt_id = last["attempt_id"]
    else:
        raise AttemptFailure(
            "attempt ID is outside the accepted retry cap", failure_class="validity"
        )
    contract_note = args.study_root / "contract.md"
    if args.attempt_id == "attempt-001":
        _write_exclusive(
            contract_note,
            (
                "# M5A reconstruction smoke v1\n\n"
                f"Contract SHA-256: `{hashlib.sha256(contract_bytes).hexdigest()}`.\n\n"
                "Diagnostic smoke only: no new RunSpec, oracle reveal, held-out access, COS "
                "publication, formal 160-trajectory aggregation, or M5B execution.\n"
            ).encode(),
        )
    elif not contract_note.exists():
        raise AttemptFailure("retry is missing the frozen study contract", failure_class="validity")
    attempt_dir.mkdir(mode=0o700)
    command = shlex.join(sys.argv)
    execution_root = Path(__file__).resolve().parents[1]
    executed_commit = _git_value(execution_root, "rev-parse", "HEAD")
    executed_tree = _git_value(execution_root, "rev-parse", "HEAD^{tree}")
    if _git_value(execution_root, "status", "--porcelain", "--untracked-files=no"):
        raise AttemptFailure("execution checkout has tracked changes", failure_class="validity")
    _write_exclusive(attempt_dir / "command.txt", (command + "\n").encode())
    _write_exclusive(attempt_dir / "preflight.json", compact_json_bytes(preflight))
    _write_exclusive(
        attempt_dir / "config-reference.json",
        compact_json_bytes(
            {
                "contract_path": str(args.contract),
                "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
                "executed_git_commit": executed_commit,
                "executed_git_tree": executed_tree,
            }
        ),
    )

    started_at = _utc_now()
    active_started = time.monotonic()
    cap = contract["resource_caps"]
    peak_rss = _peak_self_rss_bytes()
    scratch_high_water = _directory_bytes(attempt_dir)
    state_results: list[dict[str, Any]] = []
    status = "failed"
    failure_class = "implementation"
    failure_reason: str | None = None
    try:
        for state, item in validated:
            elapsed = time.monotonic() - active_started
            if elapsed >= cap["attempt_wall_seconds"]:
                raise AttemptFailure(
                    "attempt wall cap reached before next state", failure_class="budget"
                )
            state_dir = attempt_dir / "states" / state["state_id"]
            state_dir.mkdir(parents=True)
            input_path = state_dir / "input.json"
            _write_exclusive(input_path, item.prepared.input_bytes)
            feature_path = (
                args.feature_root
                / state["task_id"]
                / "sequence-composition-420-v1"
                / "candidate-pool.csv"
            )
            state_started = time.monotonic()
            outputs: list[Path] = []
            worker_runs: list[dict[str, Any]] = []
            for label in ("A", "B"):
                remaining_state = cap["state_cumulative_wall_seconds"] - (
                    time.monotonic() - state_started
                )
                remaining_attempt = cap["attempt_wall_seconds"] - (
                    time.monotonic() - active_started
                )
                timeout = min(remaining_state, remaining_attempt)
                if timeout <= 0:
                    raise AttemptFailure(
                        "state or attempt wall cap reached", failure_class="budget"
                    )
                output = state_dir / f"ranking-{label}.json"
                worker_command = (
                    str(args.python),
                    str(args.worker_script),
                    "--historical-source",
                    str(sources[state["source"]]),
                    "--input",
                    str(input_path),
                    "--candidate-features",
                    str(feature_path),
                    "--output",
                    str(output),
                )
                run_started = time.monotonic()
                try:
                    completed = subprocess.run(
                        worker_command,
                        capture_output=True,
                        env=_worker_environment(),
                        timeout=timeout,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise AttemptFailure(
                        "worker exceeded state wall cap", failure_class="budget"
                    ) from exc
                run_wall = time.monotonic() - run_started
                _write_exclusive(state_dir / f"stdout-{label}.log", completed.stdout)
                _write_exclusive(state_dir / f"stderr-{label}.log", completed.stderr)
                if completed.returncode != 0:
                    raise AttemptFailure(
                        f"worker {state['state_id']}/{label} exited {completed.returncode}",
                        failure_class="implementation",
                    )
                outputs.append(output)
                worker_runs.append(
                    {
                        "label": label,
                        "returncode": completed.returncode,
                        "wall_seconds": run_wall,
                    }
                )
                peak_rss = max(peak_rss, _peak_self_rss_bytes(), _peak_child_rss_bytes())
                scratch_high_water = max(scratch_high_water, _directory_bytes(attempt_dir))
                if peak_rss > cap["process_tree_peak_rss_bytes"]:
                    raise AttemptFailure("process-tree RSS cap exceeded", failure_class="budget")
                if scratch_high_water > cap["scratch_high_water_bytes"]:
                    raise AttemptFailure("scratch high-water cap exceeded", failure_class="budget")
            comparison = compare_reconstruction_bytes(
                first=outputs[0].read_bytes(),
                second=outputs[1].read_bytes(),
                actual_selected_ids=item.prepared.actual_selected_ids,
            )
            state_wall = time.monotonic() - state_started
            if state_wall > cap["state_cumulative_wall_seconds"]:
                raise AttemptFailure("state cumulative wall cap exceeded", failure_class="budget")
            state_result = {
                **comparison,
                "actual_selected_ids_sha256": state["actual_selected_ids_sha256"],
                "state_id": state["state_id"],
                "state_wall_seconds": state_wall,
                "worker_runs": worker_runs,
            }
            _write_exclusive(state_dir / "result.json", compact_json_bytes(state_result))
            state_results.append(state_result)
        active_wall = time.monotonic() - active_started
        if active_wall > cap["attempt_wall_seconds"]:
            raise AttemptFailure("attempt wall cap exceeded", failure_class="budget")
        result = {
            "active_execution_network_bytes": 0,
            "active_execution_wall_seconds": active_wall,
            "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
            "peak_child_rss_bytes": peak_rss,
            "schema_version": "m5a-reconstruction-smoke-result-v1",
            "scratch_high_water_bytes": scratch_high_water,
            "state_count": len(state_results),
            "states": state_results,
            "verdict": "PASS",
        }
        _write_exclusive(attempt_dir / "result.json", compact_json_bytes(result))
        status = "succeeded"
        failure_class = "none"
        result_summary = "PASS: 8/8 states, 16/16 reconstructions, byte and ordered top-k exact"
    except ReconstructionStop as exc:
        active_wall = time.monotonic() - active_started
        failure_class = "validity"
        failure_reason = str(exc)
        result_summary = None
    except ContractValidationError as exc:
        active_wall = time.monotonic() - active_started
        failure_class = "validity"
        failure_reason = str(exc)
        result_summary = None
    except AttemptFailure as exc:
        active_wall = time.monotonic() - active_started
        failure_class = exc.failure_class
        failure_reason = str(exc)
        result_summary = None
    finished_at = _utc_now()
    ledger = {
        "attempt_id": args.attempt_id,
        "command": command,
        "config_identity": [
            f"{args.contract}:{hashlib.sha256(contract_bytes).hexdigest()}",
            f"git:{executed_commit}:{executed_tree}",
        ],
        "data_identities": [
            f"state:{state['state_id']}:{state['run_id']}:{state['trajectory_sha256']}"
            for state in contract["states"]
        ],
        "executed_git_commit": executed_commit,
        "active_execution_wall_seconds": active_wall,
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
    _append_line(args.study_root / "attempts.jsonl", compact_json_bytes(ledger))
    decision = (
        "# Decision\n\nPASS. Smoke is diagnostic only; it does not complete formal M5A and does "
        "not authorize the 160-trajectory analysis or M5B.\n"
        if status == "succeeded"
        else f"# Decision\n\nSTOP. Failure class: {failure_class}. Reason: {failure_reason}.\n"
    )
    _write_exclusive(attempt_dir / "decision.md", decision.encode())
    if status == "succeeded" or failure_class in {"validity", "budget", "implementation"}:
        _write_exclusive(args.study_root / "decision.md", decision.encode())
    if status != "succeeded":
        print(json.dumps(ledger, separators=(",", ":"), sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(ledger, separators=(",", ":"), sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument(
        "--attempt-id", choices=("attempt-001", "attempt-002"), default="attempt-001"
    )
    parser.add_argument("--pilot-store-root", type=Path, required=True)
    parser.add_argument("--conditional-store-root", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--pilot-source", type=Path, required=True)
    parser.add_argument("--conditional-source", type=Path, required=True)
    parser.add_argument("--worker-script", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    return _run_attempt(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
