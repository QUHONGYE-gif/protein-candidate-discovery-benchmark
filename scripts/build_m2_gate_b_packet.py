#!/usr/bin/env python3
"""Build the zero-truth, zero-COS-write Issue #58 Gate B review artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.m2.gate_b import build_gate_a_known_inputs_index, build_gate_b_packet


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--gate-a-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evidence-bucket", required=True)
    parser.add_argument("--evidence-prefix", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _write(path: Path, payload: bytes, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if overwrite else "xb"
    with path.open(mode) as stream:
        stream.write(payload)


def main() -> int:
    arguments = _parser().parse_args()
    known_inputs = build_gate_a_known_inputs_index(arguments.gate_a_root)
    packet = build_gate_b_packet(
        repository_root=arguments.repository_root,
        gate_a_known_inputs_bytes=known_inputs,
        evidence_bucket=arguments.evidence_bucket,
        evidence_prefix=arguments.evidence_prefix,
    )
    m3_freeze = canonical_json_bytes(json.loads(packet)["m3_freeze_record"])
    operator_plan = canonical_json_bytes(json.loads(packet)["operator_plan"])
    _write(
        arguments.output_dir / "gate-a-known-inputs.v1.json",
        known_inputs,
        overwrite=arguments.overwrite,
    )
    _write(
        arguments.output_dir / "m3-anti-post-hoc-freeze.v1.json",
        m3_freeze,
        overwrite=arguments.overwrite,
    )
    _write(
        arguments.output_dir / "gate-b-packet.v1.json",
        packet,
        overwrite=arguments.overwrite,
    )
    _write(
        arguments.output_dir / "gate-b-operator-plan.v1.json",
        operator_plan,
        overwrite=arguments.overwrite,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
