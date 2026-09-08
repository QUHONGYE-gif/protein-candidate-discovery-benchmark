#!/usr/bin/env python3
"""Reconcile processed GCK labels to the authors' measured table, aggregate only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path

SOURCE_SHA = "f5a6737cc4c39a777d3a4f71bc2612757d70af428229e666c3b6b728959c164e"
AMINO_ACIDS = dict(
    zip(
        [
            "Ala",
            "Cys",
            "Asp",
            "Glu",
            "Phe",
            "Gly",
            "His",
            "Ile",
            "Lys",
            "Leu",
            "Met",
            "Asn",
            "Pro",
            "Gln",
            "Arg",
            "Ser",
            "Thr",
            "Val",
            "Trp",
            "Tyr",
        ],
        "ACDEFGHIKLMNPQRSTVWY",
        strict=True,
    )
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError("primary-source identity mismatch")
    lines = [line for line in raw.decode().splitlines() if line and not line.startswith("#")]
    measured = {}
    for row in csv.DictReader(lines):
        match = re.fullmatch(r"p\.([A-Z][a-z]{2})(\d+)([A-Z][a-z]{2})", row["hgvs_pro"])
        if match is None:
            continue
        before, position, after = match.groups()
        if before not in AMINO_ACIDS or after not in AMINO_ACIDS or before == after:
            continue
        mutant = f"{AMINO_ACIDS[before]}{position}{AMINO_ACIDS[after]}"
        if mutant in measured:
            raise ValueError("duplicate primary-source missense identity")
        measured[mutant] = float(row["score"])
    missing = mismatched = matched = 0
    observed = set()
    with args.truth.open(newline="") as stream:
        for row in csv.DictReader(stream):
            mutant = row["mutant"]
            if mutant in observed:
                raise ValueError("duplicate processed missense identity")
            observed.add(mutant)
            if mutant not in measured:
                missing += 1
            elif not math.isfinite(measured[mutant]) or not math.isclose(
                float(row["DMS_score"]), measured[mutant], rel_tol=1e-9, abs_tol=1e-9
            ):
                mismatched += 1
            else:
                matched += 1
    with args.truth.open("rb") as stream:
        truth_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        "status": "PASS"
        if matched == 8570 and missing == mismatched == 0 and observed == set(measured)
        else "STOP_LABEL_PROVENANCE",
        "matched_count": matched,
        "missing_count": missing,
        "mismatched_count": mismatched,
        "measured_missense_count": len(measured),
        "processed_count": len(observed),
        "exact_missense_identity_cover": observed == set(measured),
        "score_comparison": "identity-joined, higher-is-better, absolute/relative tolerance 1e-9",
        "source_sha256": SOURCE_SHA,
        "proteingym_truth_sha256": truth_sha,
        "source_url": "https://raw.githubusercontent.com/KULL-Centre/_2023_Gersing_GCK/"
        "8315af464b5875469cf2780f12ccf61c8411c347/input_notebook_SI/select_t1_simple_aa.csv",
        "source_role": "measured amino-acid score table; separate imputation table not used",
        "entry_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "variant_values_disclosed": False,
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
