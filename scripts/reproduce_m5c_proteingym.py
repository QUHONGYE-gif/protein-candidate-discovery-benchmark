#!/usr/bin/env python3
"""Reproduce M5C inference from one sealed aggregate input only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pcdb.contracts import ContractValidationError
from pcdb.m5.proteingym_confirmation import ASSAY_IDS, compact_json_bytes
from pcdb.m5.proteingym_runtime import (
    build_cohort_report,
    verify_assay_aggregate,
    write_once,
)

MAX_INPUT_BYTES = 1_073_741_824
MAX_RSS_BYTES = 4_000_000_000
MAX_SCRATCH_BYTES = 2_147_483_648
MAX_WALL_SECONDS = 3_600


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _environment_digest() -> str:
    payload = compact_json_bytes(
        {
            "hostname": socket.gethostname(),
            "python": sys.version,
            "schema_version": "m5c-review-environment-v1",
        }
    )
    return hashlib.sha256(payload).hexdigest()


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _run(args: argparse.Namespace) -> dict[str, object]:
    if socket.gethostname() != "ps":
        raise ContractValidationError("M5C reproduction host must have kernel hostname ps")
    if (
        args.output_root.parent.resolve() != Path("/srv/pcdb/runs/M5C").resolve()
        or re.fullmatch(r"review-attempt-[0-9]{3}", args.output_root.name) is None
    ):
        raise ContractValidationError(
            "M5C reproduction output root is outside its server namespace"
        )
    observed_source = (
        _git(args.source_root, "rev-parse", "HEAD"),
        _git(args.source_root, "rev-parse", "HEAD^{tree}"),
        _git(args.source_root, "status", "--porcelain"),
    )
    if observed_source != (args.expected_git_commit, args.expected_git_tree, ""):
        raise ContractValidationError("M5C reviewed reproduction source identity drifted")
    payload = args.aggregate_input.read_bytes()
    if (
        len(payload) > MAX_INPUT_BYTES
        or hashlib.sha256(payload).hexdigest() != args.aggregate_input_sha256
        or b"candidate_id" in payload
    ):
        raise ContractValidationError("M5C sealed aggregate identity or privacy boundary drifted")
    try:
        aggregate = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M5C sealed aggregate JSON is invalid") from None
    if (
        not isinstance(aggregate, dict)
        or aggregate.get("schema_version") != "m5c-proteingym-sealed-aggregate-input-v1"
        or aggregate.get("contract_digest")
        != "abc16ed38664d63efede535de3ecd8b8552031e52b156c37023e7b7e717d5278"
        or aggregate.get("input_capability_manifest_sha256")
        != "130d9f3d9086274c7209158cdcbb3a4054ff3742e3711220542adecae14828fc"
    ):
        raise ContractValidationError("M5C sealed aggregate contract binding drifted")
    reports = aggregate.get("assays")
    if (
        not isinstance(reports, list)
        or tuple(item.get("assay_id") if isinstance(item, dict) else None for item in reports)
        != ASSAY_IDS
    ):
        raise ContractValidationError("M5C sealed aggregate assay cohort drifted")
    identity = {
        "aggregate_input_sha256": args.aggregate_input_sha256,
        "contract_digest": "abc16ed38664d63efede535de3ecd8b8552031e52b156c37023e7b7e717d5278",
        "environment_digest": _environment_digest(),
        "git_commit": args.expected_git_commit,
        "git_tree": args.expected_git_tree,
        "review_attempt_id": args.output_root.name,
        "schema_version": "m5c-proteingym-reproduction-identity-v1",
        "source_clean": True,
    }
    if args.output_root.exists():
        raise FileExistsError(args.output_root)
    args.output_root.mkdir(mode=0o700)
    write_once(args.output_root / "identity.json", compact_json_bytes(identity))
    started_at = _utc_now()
    started = time.monotonic()
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MAX_RSS_BYTES, MAX_RSS_BYTES))
        for report in reports:
            verify_assay_aggregate(cast(dict[str, object], report))
            if time.monotonic() - started > MAX_WALL_SECONDS:
                raise ContractValidationError("M5C reproduction wall cap exceeded")
        result = build_cohort_report(
            cast(list[dict[str, object]], reports),
            attempt_id=cast(str, aggregate["attempt_id"]),
        )
        result_bytes = compact_json_bytes(result)
        write_once(args.output_root / "report.json", result_bytes)
        if _directory_bytes(args.output_root) > MAX_SCRATCH_BYTES:
            raise ContractValidationError("M5C reproduction scratch cap exceeded")
    except (ContractValidationError, OSError, ValueError) as exc:
        stopped = {
            "aggregate_input_sha256": args.aggregate_input_sha256,
            "reason": str(exc),
            "schema_version": "m5c-proteingym-reproduction-terminal-v1",
            "status": "STOPPED",
        }
        write_once(args.output_root / "terminal.json", compact_json_bytes(stopped))
        raise
    terminal = {
        "aggregate_input_sha256": args.aggregate_input_sha256,
        "cos_writes": 0,
        "finished_at": _utc_now(),
        "network_bytes": 0,
        "report_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "schema_version": "m5c-proteingym-reproduction-terminal-v1",
        "started_at": started_at,
        "status": "COMPLETE",
        "wall_seconds": time.monotonic() - started,
    }
    write_once(args.output_root / "terminal.json", compact_json_bytes(terminal))
    write_once(args.output_root / "_SUCCESS", b"")
    return terminal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate-input", required=True, type=Path)
    parser.add_argument("--aggregate-input-sha256", required=True)
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--expected-git-tree", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = _run(args)
    except (ContractValidationError, FileExistsError, OSError, subprocess.SubprocessError) as exc:
        sys.stderr.buffer.write(
            compact_json_bytes(
                {
                    "reason": str(exc),
                    "schema_version": "m5c-proteingym-reproduction-failure-v1",
                    "status": "STOPPED",
                }
            )
        )
        return 2
    sys.stdout.buffer.write(compact_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
