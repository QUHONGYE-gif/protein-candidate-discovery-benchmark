#!/usr/bin/env python3
"""Sequential connection/full attempts within the approved shared resource cap."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from m5c_benign_scope import ASSAY_IDS, STUDY_ID


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gck-evidence", type=Path, required=True)
    parser.add_argument("--assay", choices=ASSAY_IDS, action="append")
    args = parser.parse_args()
    assays = tuple(args.assay or ASSAY_IDS)
    if len(set(assays)) != len(assays):
        raise ValueError("duplicate assay in bounded series")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    input_sha = hashlib.sha256((args.input_root / "input.json").read_bytes()).hexdigest()
    evidence_sha = hashlib.sha256(args.gck_evidence.read_bytes()).hexdigest()
    records = []
    for assay in assays:
        assay_started = time.monotonic()
        for mode in ("one-state", "full"):
            attempt = args.output / assay / f"{mode}-001"
            command = [
                sys.executable,
                str(Path(__file__).with_name("run_m5c_benign_iteration.py")),
                "--input-root",
                str(args.input_root),
                "--input-sha",
                input_sha,
                "--assay",
                assay,
                "--attempt-root",
                str(attempt),
                "--mode",
                mode,
            ]
            if assay == ASSAY_IDS[2]:
                command += [
                    "--label-evidence",
                    str(args.gck_evidence),
                    "--label-evidence-sha",
                    evidence_sha,
                ]
            remaining = min(
                21600 - (time.monotonic() - started), 7200 - (time.monotonic() - assay_started)
            )
            if remaining <= 0:
                raise RuntimeError("approved series time cap reached")
            record = {
                "assay_id": assay,
                "mode": mode,
                "command": command,
                "started_at": datetime.now(UTC).isoformat(),
                "status": "running",
            }
            with (args.output / "attempts.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            with (args.output / f"{assay}-{mode}.log").open("x") as log:
                process = subprocess.Popen(
                    command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
                )
                try:
                    code = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise RuntimeError("approved attempt time cap reached") from None
            terminal = json.loads((attempt / "terminal.json").read_text())
            result = json.loads((attempt / "result.json").read_text()) if code == 0 else {}
            expected = 1 if mode == "one-state" else 40
            passed = (
                code == 0
                and terminal["status"] == "succeeded"
                and result.get("completed_states") == expected
                and all(
                    result.get(key) == "PASS"
                    for key in (
                        "candidate_identity_checks",
                        "truth_alignment_check",
                        "membership_alignment_check",
                    )
                )
            )
            record.update(status="succeeded" if passed else "stopped", terminal=terminal)
            records.append(record)
            with (args.output / "attempts.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            print(
                json.dumps({"assay_id": assay, "mode": mode, "status": record["status"]}),
                flush=True,
            )
            if not passed:
                return 1
    with (args.output / "terminal.json").open("x") as stream:
        json.dump(
            {
                "study_id": STUDY_ID,
                "status": "succeeded",
                "attempts": len(records),
                "completed_states": 40 * len(assays),
                "branch_evaluations": 2640 * len(assays),
                "wall_seconds": time.monotonic() - started,
                "finished_at": datetime.now(UTC).isoformat(),
            },
            stream,
            indent=2,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
