#!/usr/bin/env python3
"""Print the canonical Issue #58 Gate A packet for an exact reviewed commit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pcdb.m2.gate_a import build_gate_a_packet, packet_identity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--reviewed-git-commit", required=True)
    parser.add_argument("--identity-only", action="store_true")
    arguments = parser.parse_args()
    packet = build_gate_a_packet(
        arguments.repository_root.resolve(),
        arguments.reviewed_git_commit,
    )
    size_bytes, sha256 = packet_identity(packet)
    if arguments.identity_only:
        print(f"size_bytes={size_bytes}")
        print(f"sha256={sha256}")
    else:
        sys.stdout.buffer.write(packet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
