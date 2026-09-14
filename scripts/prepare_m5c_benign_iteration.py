#!/usr/bin/env python3
"""Acquire six approved members and separate labels; never report variant values."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

from m5c_benign_scope import (
    ASSAY_IDS,
    COUNTS,
    METADATA_SHA256,
    METADATA_URL,
    STUDY_ID,
)

from pcdb.datasets.proteingym import (
    Archive,
    Member,
    RangeReader,
    _candidate_id,
    _canonical_decimal,
    _deterministic_gzip_text,
    acquire_member,
)


def write_json(path: Path, value: object) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def identity(path: Path, root: Path, rows: int | None = None) -> dict:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "path": str(path.relative_to(root)),
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "row_count": rows,
    }


def validate_variant(mutant: str, sequence: str, reference: str) -> None:
    expected = list(reference)
    seen = set()
    for substitution in mutant.split(":"):
        match = re.fullmatch(r"([ACDEFGHIKLMNPQRSTVWY])(\d+)([ACDEFGHIKLMNPQRSTVWY])", substitution)
        if match is None:
            raise ValueError("invalid substitution notation")
        before, position, after = match.groups()
        index = int(position) - 1
        if index in seen or not 0 <= index < len(reference) or reference[index] != before:
            raise ValueError("substitution does not match reference identity")
        seen.add(index)
        expected[index] = after
    if "".join(expected) != sequence:
        raise ValueError("mutant and sequence disagree")


def prepare(args: argparse.Namespace) -> dict:
    root = args.output
    inventory_bytes = args.inventory.read_bytes()
    inventory = json.loads(inventory_bytes)
    reader = RangeReader(byte_cap=100 * 1024**2)
    started = time.monotonic()
    with urlopen(METADATA_URL, timeout=45) as response:
        metadata_bytes = response.read(300001)
    if hashlib.sha256(metadata_bytes).hexdigest() != METADATA_SHA256:
        raise ValueError("upstream reference metadata identity changed")
    references = {
        r["DMS_id"]: r
        for r in csv.DictReader(io.StringIO(metadata_bytes.decode()))
        if r["DMS_id"] in ASSAY_IDS
    }
    write_json(
        root / "execution.json",
        {
            "study_id": STUDY_ID,
            "started_at": datetime.now(UTC).isoformat(),
            "entry_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
            "metadata_sha256": METADATA_SHA256,
            "assays": list(ASSAY_IDS),
            "authorization": "user explicitly approved the three-assay substitution",
            "scope": "public data ingestion only; no selection or outcome-dependent decisions",
        },
    )
    records = []
    for assay in ASSAY_IDS:
        if time.monotonic() - started > 3600:
            raise RuntimeError("ingest time cap reached")
        item_root = root / assay
        item_root.mkdir()
        acquired = {}
        for source in inventory:
            modified = {
                "truth-substitutions": "2025-04-13 02:47:58 GMT",
                "zero-shot-substitutions": "2025-04-28 02:26:40 GMT",
            }
            archive = Archive(
                source["archive_id"],
                source["url"],
                source["size_bytes"],
                source["etag"].strip('"'),
                modified[source["archive_id"]],
            )
            raw_member = next(
                x
                for x in source["selected_members"]
                if Path(x["member_name"]).name == assay + ".csv"
            )
            member = Member(
                archive.archive_id,
                Path(raw_member["member_name"]).name,
                raw_member["local_header_offset"],
                raw_member["compressed_size"],
                raw_member["uncompressed_size"],
                int(raw_member["crc32"], 16),
            )
            role = "truth" if archive.archive_id == "truth-substitutions" else "proxy"
            obtained = acquire_member(
                archive=archive,
                member=member,
                destination=item_root / f"raw-{role}.csv",
                reader=reader,
            )
            acquired[role] = obtained
            write_json(
                item_root / f"acquisition-{role}.json",
                {
                    "archive": source,
                    "member": raw_member,
                    "output": identity(obtained.path, root, obtained.row_count),
                    "requested_ranges": obtained.requested_ranges,
                },
            )
        reference = references[assay]
        if reference["raw_DMS_directionality"] != "1":
            raise ValueError("unexpected label direction")
        candidate = item_root / "candidates.csv.gz"
        truth = item_root / "hidden-truth.csv.gz"
        membership = item_root / "membership.csv.gz"
        seen = set()
        ids = set()
        with (
            acquired["truth"].path.open(newline="") as stream,
            _deterministic_gzip_text(candidate) as cf,
            _deterministic_gzip_text(truth) as tf,
            _deterministic_gzip_text(membership) as mf,
        ):
            table = csv.DictReader(stream)
            fields = table.fieldnames or []
            if len(fields) != len(set(fields)) or not {
                "mutant",
                "mutated_sequence",
                "DMS_score",
            } <= set(fields):
                raise ValueError("source fields do not identify sample and label")
            cw, tw, mw = csv.writer(cf), csv.writer(tf), csv.writer(mf)
            cw.writerow(["candidate_id", "mutant", "mutated_sequence"])
            tw.writerow(["candidate_id", "DMS_score"])
            mw.writerow(["candidate_id", "partition"])
            for row in table:
                mutant, sequence = row["mutant"], row["mutated_sequence"]
                validate_variant(mutant, sequence, reference["target_seq"])
                cid = _candidate_id(assay, mutant)
                if mutant in seen or cid in ids:
                    raise ValueError("duplicate sample identity")
                seen.add(mutant)
                ids.add(cid)
                value = _canonical_decimal(row["DMS_score"], context="approved public label")
                cw.writerow([cid, mutant, sequence])
                tw.writerow([cid, value])
                mw.writerow([cid, "candidate-pool"])
        if len(seen) != COUNTS[assay]:
            raise ValueError("source sample count differs from metadata")
        # Only ID coverage and finite ESM1v availability; no ranks, effects, or values returned.
        proxy_seen = set()
        with acquired["proxy"].path.open(newline="") as stream:
            table = csv.DictReader(stream)
            if "ESM1v_single" not in (table.fieldnames or []):
                raise ValueError("fixed static column is missing")
            for row in table:
                mutant = row["mutant"]
                if mutant not in seen or mutant in proxy_seen:
                    raise ValueError("static source does not align to candidates")
                if not math.isfinite(float(row["ESM1v_single"])):
                    raise ValueError("fixed static column is nonfinite")
                proxy_seen.add(mutant)
        if proxy_seen != seen:
            raise ValueError("static source does not exact-cover candidates")
        record = {
            "assay_id": assay,
            "candidate_count": len(seen),
            "label_direction": "higher_is_better",
            "label_field": "DMS_score",
            "observable_fields": ["mutant", "mutated_sequence"],
            "partition": "candidate-pool",
            "held_out_partitions": [],
            "sample_sequence_reference_check": "PASS",
            "id_coverage_check": "PASS",
            "source_phenotype_name": reference["raw_DMS_phenotype_name"],
            "source_paper_doi": reference["jo"],
            "label_provenance": (
                "ProteinGym experimental phenotype; "
                "GCK primary-source reconciliation required before replay"
            ),
            "artifacts": {
                "candidate": identity(candidate, root, len(seen)),
                "hidden_truth": identity(truth, root, len(seen)),
                "membership": identity(membership, root, len(seen)),
                "proxy_source": identity(acquired["proxy"].path, root, len(seen)),
                "truth_source": identity(acquired["truth"].path, root, len(seen)),
            },
        }
        for path in (candidate, truth, membership, acquired["proxy"].path, acquired["truth"].path):
            path.chmod(0o400)
        write_json(item_root / "input.json", record)
        records.append(record)
        print(json.dumps({"phase": "prepared", "assay_id": assay, "count": len(seen)}), flush=True)
    report = {
        "study_id": STUDY_ID,
        "assays": records,
        "metadata_sha256": METADATA_SHA256,
        "wall_seconds": time.monotonic() - started,
        "scientific_results": False,
        "network_bytes": reader.bytes_read + len(metadata_bytes),
    }
    write_json(root / "input.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        prepare(args)
    except Exception as exc:
        write_json(
            args.output / "terminal.json",
            {"status": "stopped", "error_type": type(exc).__name__, "reason": str(exc)},
        )
        raise
    write_json(
        args.output / "terminal.json",
        {"status": "succeeded", "finished_at": datetime.now(UTC).isoformat()},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
