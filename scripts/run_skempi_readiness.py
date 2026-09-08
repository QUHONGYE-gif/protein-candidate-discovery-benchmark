#!/usr/bin/env python3
"""Two isolated SKEMPI readiness builds on 129.28.97.247; no scientific run."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

from pcdb.datasets.skempi_readiness import build, canonical, sha

RSS_BYTES = 1610612736
SCRATCH_BYTES = 1073741824
WALL_SECONDS = 1200
RAW_ROOT = Path("/srv/pcdb/data/raw/skempi/v2.0/2018-06/source-bundle")
REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "manifests/datasets/skempi/v2.0/2018-06/source-bundle/dataset-manifest.json"


def write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--child", choices=["a", "b"])
    args = parser.parse_args()
    if platform.node() != "VM-0-8-debian":
        raise ValueError("sole authorized host is 129.28.97.247 / VM-0-8-debian")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.attempt_id):
        raise ValueError("unsafe attempt ID")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip()
    if head != args.git_commit or dirty:
        raise ValueError("execution checkout is not the clean pinned Git commit")
    os.umask(0o077)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    attempt = Path("/srv/pcdb/runs/M5D") / args.attempt_id
    if args.child:
        resource.setrlimit(resource.RLIMIT_AS, (RSS_BYTES, RSS_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (WALL_SECONDS, WALL_SECONDS))
        destination = attempt / args.child
        destination.mkdir()
        payloads = build(RAW_ROOT, MANIFEST, args.git_commit)
        if sum(map(len, payloads.values())) > SCRATCH_BYTES // 2:
            raise ValueError("scratch cap exceeded")
        for name, data in sorted(payloads.items()):
            write_new(destination / name, data)
        return
    attempt.parent.mkdir(exist_ok=True)
    attempt.mkdir()
    started = time.monotonic()
    record: dict[str, object] = {
        "issue": 104,
        "server": "129.28.97.247",
        "hostname": platform.node(),
        "git_commit": head,
        "python": sys.version,
        "status": "FAILED",
        "kind": "processing_readiness_only",
        "limits": {
            "rss_bytes": RSS_BYTES,
            "scratch_bytes": SCRATCH_BYTES,
            "wall_seconds": WALL_SECONDS,
            "workers": 1,
        },
        "cos_write_count": 0,
        "model_fit_count": 0,
        "scientific_result": "NOT_EVALUATED",
    }
    write_new(attempt / "run-identity.json", canonical(record))
    try:
        for child in ("a", "b"):
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--attempt-id",
                args.attempt_id,
                "--git-commit",
                args.git_commit,
                "--child",
                child,
            ]
            with (attempt / (child + ".log")).open("xb") as log:
                subprocess.run(
                    command,
                    cwd=REPO,
                    check=True,
                    stdout=log,
                    stderr=log,
                    timeout=max(1, WALL_SECONDS - (time.monotonic() - started)),
                )
        a = {p.name: sha(p.read_bytes()) for p in (attempt / "a").iterdir()}
        b = {p.name: sha(p.read_bytes()) for p in (attempt / "b").iterdir()}
        if not a or a != b:
            raise ValueError("independent A/B output identity mismatch")
        audit = json.loads((attempt / "a/processing-audit.json").read_bytes())
        record.update(
            status="READINESS_BUILDS_VERIFIED",
            audit=audit,
            outputs=a,
            two_clean_processes=True,
            byte_identical=True,
        )
    except Exception as error:
        record["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        record["wall_seconds"] = time.monotonic() - started
        record["peak_child_rss_bytes"] = (
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
        )
        record["scratch_bytes"] = sum(p.stat().st_size for p in attempt.rglob("*") if p.is_file())
        if record["scratch_bytes"] > SCRATCH_BYTES or record["wall_seconds"] > WALL_SECONDS:
            record["status"] = "FAILED_RESOURCE_CAP"
        write_new(attempt / "terminal.json", canonical(record))
        print(json.dumps(record, sort_keys=True))
    if record["status"] != "READINESS_BUILDS_VERIFIED":
        raise ValueError("readiness failed")
    write_new(attempt / "_SUCCESS", b"")


if __name__ == "__main__":
    main()
