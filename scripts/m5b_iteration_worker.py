#!/usr/bin/env python3
"""Validate the M5B inventory, then run one authorized truth-bearing iteration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pcdb.contracts.experiment import ContractValidationError
from pcdb.contracts.feature_identity import load_feature_matrix
from pcdb.m2.features import sequence_feature_definitions
from pcdb.m5.formal_reconstruction import (
    binary64_hex,
    build_formal_inventory,
    load_formal_contract,
)
from pcdb.m5.inventory_preflight import (
    ACCEPTED_M5A_INVENTORY_SHA256,
    ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
    extract_first_decision_state_payloads,
)
from pcdb.m5.iteration import (
    build_iteration_report,
    evaluate_iteration,
    load_development_truth,
)
from pcdb.m5.reconstruction_smoke import compact_json_bytes
from pcdb.m5.same_state import build_state_inventory


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ("git", "-C", str(root), *args),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise ContractValidationError("M5B git identity command failed") from None


def _environment_digest() -> str:
    version = subprocess.run((sys.executable, "--version"), check=True, capture_output=True)
    frozen = subprocess.run(
        (sys.executable, "-m", "pip", "freeze", "--all"),
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(version.stdout + version.stderr + frozen.stdout).hexdigest()


def _rss_bytes(who: int) -> int:
    value = resource.getrusage(who).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_identity(root: Path, expected: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = (
        _git(root, "rev-parse", "HEAD"),
        _git(root, "rev-parse", "HEAD^{tree}"),
        _git(root, "status", "--porcelain", "--untracked-files=no"),
    )
    if identity != (expected["git_commit"], expected["git_tree"], ""):
        raise ContractValidationError("accepted M5A historical source identity drifted")
    return identity


def _parent_snapshot(paths: Sequence[Path]) -> dict[str, str]:
    if len(set(paths)) != len(paths) or any(not path.is_file() for path in paths):
        raise ContractValidationError("accepted M5A parent input coverage drifted")
    return {str(path): _sha256(path) for path in sorted(paths)}


def _validate_parent_attempt(root: Path, *, formal_inventory: bytes) -> None:
    required = {
        "_SUCCESS": hashlib.sha256(b"").hexdigest(),
        "inventory.json": ACCEPTED_M5A_INVENTORY_SHA256,
        "report-A.json": "b199a483fd9e7a375d672e8ecf7ec5075248b2c5a729dfbc47e73fdae5641943",
        "report-B.json": "b199a483fd9e7a375d672e8ecf7ec5075248b2c5a729dfbc47e73fdae5641943",
    }
    if any(not (root / name).is_file() for name in required):
        raise ContractValidationError("accepted M5A attempt artifacts are unavailable")
    if {name: _sha256(root / name) for name in required} != required:
        raise ContractValidationError("accepted M5A attempt artifact identity drifted")
    if (root / "inventory.json").read_bytes() != formal_inventory:
        raise ContractValidationError("accepted M5A rebuilt inventory bytes drifted")
    result = json.loads((root / "result.json").read_bytes())
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "m5a-formal-result-v1"
        or result.get("verdict") != "RECONSTRUCTION_VALID"
        or result.get("inventory_sha256") != ACCEPTED_M5A_INVENTORY_SHA256
        or result.get("report_sha256") != required["report-A.json"]
        or result.get("selected_trajectories") != 160
        or result.get("active_execution_network_bytes") != 0
    ):
        raise ContractValidationError("accepted M5A result identity drifted")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization-sha256", required=True)
    parser.add_argument("--alpha-hidden-truth", type=Path, required=True)
    parser.add_argument("--alpha-hidden-truth-sha256", required=True)
    parser.add_argument("--alpha-processing-manifest", type=Path, required=True)
    parser.add_argument("--conditional-source-root", type=Path, required=True)
    parser.add_argument("--conditional-store-root", type=Path, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--expected-git-tree", required=True)
    parser.add_argument("--expected-runtime-environment-digest", required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--formal-attempt-root", type=Path, required=True)
    parser.add_argument("--formal-contract", type=Path, required=True)
    parser.add_argument("--formal-contract-sha256", required=True)
    parser.add_argument("--ired-hidden-truth", type=Path, required=True)
    parser.add_argument("--ired-hidden-truth-sha256", required=True)
    parser.add_argument("--ired-processing-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pilot-source-root", type=Path, required=True)
    parser.add_argument("--pilot-store-root", type=Path, required=True)
    parser.add_argument("--process-tree-peak-rss-bytes", type=int, required=True)
    parser.add_argument("--scratch-high-water-bytes", type=int, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    wall_started = time.monotonic()
    self_started = resource.getrusage(resource.RUSAGE_SELF)
    children_started = resource.getrusage(resource.RUSAGE_CHILDREN)
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise ContractValidationError("M5B build scratch must be an existing empty directory")
    if args.cpu_seconds < 1:
        raise ContractValidationError("M5B CPU cap is invalid")
    resource.setrlimit(resource.RLIMIT_CPU, (args.cpu_seconds, args.cpu_seconds))
    if (
        _git(args.source_root, "rev-parse", "HEAD") != args.expected_git_commit
        or _git(args.source_root, "rev-parse", "HEAD^{tree}") != args.expected_git_tree
        or _git(args.source_root, "status", "--porcelain", "--untracked-files=no")
    ):
        raise ContractValidationError("M5B reviewed source commit/tree/clean identity drifted")
    runtime_digest = _environment_digest()
    if runtime_digest != args.expected_runtime_environment_digest:
        raise ContractValidationError("M5B preflight runtime environment identity drifted")
    contract_bytes = args.formal_contract.read_bytes()
    if hashlib.sha256(contract_bytes).hexdigest() != args.formal_contract_sha256:
        raise ContractValidationError("accepted M5A formal contract identity drifted")
    contract = load_formal_contract(contract_bytes)
    historical_sources = {
        "conditional-expansion": args.conditional_source_root,
        "pilot": args.pilot_source_root,
    }
    source_identities = {
        name: _source_identity(root, contract["sources"][name])
        for name, root in historical_sources.items()
    }
    inventory = build_formal_inventory(
        args.source_root,
        contract=contract,
        store_roots={
            "conditional-expansion": args.conditional_store_root,
            "pilot": args.pilot_store_root,
        },
    )
    if inventory.sha256 != ACCEPTED_M5A_INVENTORY_SHA256:
        raise ContractValidationError("accepted M5A inventory identity drifted")
    _validate_parent_attempt(args.formal_attempt_root, formal_inventory=inventory.canonical_bytes)

    candidate_ids_by_task: dict[str, tuple[str, ...]] = {}
    feature_paths: list[Path] = []
    for task_id in ("alpha-amylase", "ired"):
        feature_path = (
            args.feature_root / task_id / "sequence-composition-420-v1" / "candidate-pool.csv"
        )
        feature_paths.append(feature_path)
        expected_sha256 = contract["features"][task_id]["sha256"]
        feature_payload = feature_path.read_bytes()
        if hashlib.sha256(feature_payload).hexdigest() != expected_sha256:
            raise ContractValidationError("accepted M5A candidate feature identity drifted")
        feature_matrix = load_feature_matrix(
            feature_payload,
            feature_definitions=sequence_feature_definitions(),
            expected_sha256=expected_sha256,
        )
        candidate_ids_by_task[task_id] = feature_matrix.candidate_ids

    attempt_paths = sorted(
        (
            *args.pilot_store_root.rglob("run-attempt.json"),
            *args.conditional_store_root.rglob("run-attempt.json"),
        )
    )
    if len(attempt_paths) != 344:
        raise ContractValidationError("accepted M5A parent attempt snapshot coverage drifted")
    parent_artifact_names = (
        "_SUCCESS",
        "inventory.json",
        "report-A.json",
        "report-B.json",
        "result.json",
    )
    parent_paths = [
        args.formal_contract,
        *(args.formal_attempt_root / name for name in parent_artifact_names),
        *attempt_paths,
        *(item.trajectory_path for item in inventory.selected),
        *feature_paths,
    ]
    parent_before = _parent_snapshot(parent_paths)

    state_inventory = build_state_inventory(
        extract_first_decision_state_payloads(
            inventory.selected,
            candidate_ids_by_task=candidate_ids_by_task,
            parent_inventory_sha256=inventory.sha256,
            source_environment_digest=ACCEPTED_M5A_SOURCE_ENVIRONMENT_DIGEST,
        )
    )
    if parent_before != _parent_snapshot(parent_paths) or any(
        _source_identity(historical_sources[name], contract["sources"][name])
        != source_identities[name]
        for name in historical_sources
    ):
        raise ContractValidationError("accepted M5A parent read-only identity drifted")
    truth_paths = {
        "alpha-amylase": (
            args.alpha_processing_manifest,
            args.alpha_hidden_truth,
            args.alpha_hidden_truth_sha256,
        ),
        "ired": (
            args.ired_processing_manifest,
            args.ired_hidden_truth,
            args.ired_hidden_truth_sha256,
        ),
    }
    truths = {}
    for task_id, (processing_path, truth_path, truth_sha256) in truth_paths.items():
        expected_processing_sha256 = next(
            item.prepared.run_spec.processing_manifest_sha256
            for item in inventory.selected
            if item.prepared.task_id == task_id
        )
        truths[task_id] = load_development_truth(
            task_id=task_id,
            processing_manifest=processing_path.read_bytes(),
            expected_processing_manifest_sha256=expected_processing_sha256,
            hidden_truth=truth_path.read_bytes(),
            expected_hidden_truth_sha256=truth_sha256,
            candidate_ids=candidate_ids_by_task[task_id],
        )
    report = build_iteration_report(
        evaluate_iteration(
            inventory.selected,
            candidate_ids_by_task=candidate_ids_by_task,
            truths=truths,
        )
    )
    peak_rss = max(
        _rss_bytes(resource.RUSAGE_SELF),
        _rss_bytes(resource.RUSAGE_CHILDREN),
    )
    if peak_rss > args.process_tree_peak_rss_bytes:
        raise ContractValidationError("M5B process RSS cap exceeded")
    inventory_path = args.output_root / "inventory.json"
    _write_exclusive(inventory_path, state_inventory.canonical_bytes)
    _write_exclusive(args.output_root / "result.json", report)
    self_finished = resource.getrusage(resource.RUSAGE_SELF)
    children_finished = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = (
        self_finished.ru_utime
        + self_finished.ru_stime
        + children_finished.ru_utime
        + children_finished.ru_stime
        - self_started.ru_utime
        - self_started.ru_stime
        - children_started.ru_utime
        - children_started.ru_stime
    )
    wall_seconds = time.monotonic() - wall_started
    audit = {
        "branch_evaluations": 80 * 66,
        "authorization_sha256": args.authorization_sha256,
        "cos_publication_writes": 0,
        "cpu_seconds_binary64_be_hex": binary64_hex(cpu_seconds),
        "held_out_reads": 0,
        "inventory_sha256": state_inventory.sha256,
        "network_bytes": 0,
        "parent_read_only_verified": True,
        "runtime_environment_digest": runtime_digest,
        "process_tree_peak_rss_bytes": peak_rss,
        "result_sha256": hashlib.sha256(report).hexdigest(),
        "schema_version": "m5b-bounded-iteration-audit-v1",
        "selected_candidate_reveals": 80 * 66 * 16,
        "scratch_high_water_bytes": 0,
        "state_count": len(state_inventory.states),
        "wall_seconds_binary64_be_hex": binary64_hex(wall_seconds),
    }
    scratch = _directory_bytes(args.output_root)
    while True:
        audit["scratch_high_water_bytes"] = scratch
        audit_bytes = compact_json_bytes(audit)
        updated = _directory_bytes(args.output_root) + len(audit_bytes)
        if updated == scratch:
            break
        scratch = updated
    if scratch > args.scratch_high_water_bytes:
        raise ContractValidationError("M5B scratch high-water cap exceeded")
    _write_exclusive(args.output_root / "audit.json", audit_bytes)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractValidationError as exc:
        failure_class = "budget" if "cap exceeded" in str(exc) else "validity"
        sys.stderr.buffer.write(
            compact_json_bytes(
                {
                    "failure_class": failure_class,
                    "reason": str(exc),
                    "schema_version": "m5b-bounded-iteration-worker-failure-v1",
                }
            )
        )
        raise SystemExit(2) from None
