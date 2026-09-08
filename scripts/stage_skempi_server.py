#!/usr/bin/env python3
"""Recover the accepted SKEMPI bytes onto the sole authorized server; no COS writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import time
from pathlib import Path
from urllib.request import urlopen

FILES = {
    "skempi_v2.csv": (1602208, "76f8e60d09eaa4f05fb265063886859a86895c0b2fb225e31fe82928a883972d"),
    "SKEMPI2_PDBs.tgz": (
        30482090,
        "82418131a3864aa8484ea6ec300e58f9bc0349e21530f83f7ce336b41589a95a",
    ),
}


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    if platform.node() != "VM-0-8-debian":
        raise ValueError("wrong server; expected 129.28.97.247 / VM-0-8-debian")
    if not args.attempt_id or any(
        c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.attempt_id
    ):
        raise ValueError("unsafe attempt ID")
    os.umask(0o077)
    root = Path("/srv/pcdb")
    for name in (
        "data/raw",
        "data/processed",
        "data/embeddings",
        "data/splits",
        "runs",
        "releases",
        "scratch",
    ):
        p = root / name
        if not p.exists():
            p.mkdir(parents=True, mode=0o2770)
            p.chmod(0o2770)
    attempt = root / "scratch" / args.attempt_id
    attempt.mkdir(mode=0o700)
    destination = root / "data/raw/skempi/v2.0/2018-06/source-bundle"
    destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    record: dict[str, object] = {
        "server": "129.28.97.247",
        "hostname": platform.node(),
        "issue": 104,
        "mode": "opaque-byte recovery from frozen official URLs",
        "cos_write_count": 0,
        "cos_delete_count": 0,
        "objects": [],
    }
    objects = []
    downloaded = 0
    try:
        for name, (size, expected) in FILES.items():
            target = destination / name
            url = "https://life.bsc.es/pid/skempi2/database/download/" + name
            reused = target.exists()
            if not reused:
                stage = attempt / (name + ".part")
                with urlopen(url, timeout=45) as response, stage.open("xb") as out:  # noqa: S310
                    count = 0
                    while block := response.read(1024 * 1024):
                        count += len(block)
                        downloaded += len(block)
                        if count > size or time.monotonic() - started > 300:
                            raise ValueError("source size or 300-second recovery budget exceeded")
                        out.write(block)
                if stage.stat().st_size != size or digest(stage) != expected:
                    raise ValueError("frozen source identity mismatch: " + name)
                os.link(stage, target)  # Atomic, exclusive, no successful overwrite.
                target.chmod(0o440)
            if target.is_symlink() or target.stat().st_size != size or digest(target) != expected:
                raise ValueError("server readback identity mismatch: " + name)
            objects.append(
                {
                    "path": str(target),
                    "size_bytes": size,
                    "sha256": expected,
                    "reused": reused,
                    "source_url": url,
                }
            )
        record["status"] = "RAW_IDENTITY_VERIFIED"
    except Exception as error:
        record["status"] = "FAILED"
        record["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        record.update(
            objects=objects, downloaded_bytes=downloaded, wall_seconds=time.monotonic() - started
        )
        with (attempt / "receipt.json").open("x") as out:
            json.dump(record, out, sort_keys=True, indent=2)
            out.write("\n")
        print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
