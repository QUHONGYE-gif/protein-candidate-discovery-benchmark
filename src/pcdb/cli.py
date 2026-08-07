"""Command-line entry point for repository smoke checks."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from pcdb import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="pcdb")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    synthetic = commands.add_parser(
        "m2-synthetic",
        help="run the bounded synthetic feature/model producer into local scratch",
    )
    synthetic.add_argument("--roles", required=True, type=Path)
    synthetic.add_argument("--development-observations", required=True, type=Path)
    synthetic.add_argument("--experiment-config", required=True, type=Path)
    synthetic.add_argument(
        "--model-id",
        choices=("ridge-sklearn-v1", "random-forest-sklearn-v1", "xgboost-cpu-v1"),
        required=True,
    )
    synthetic.add_argument(
        "--attempt-id",
        default="synthetic-attempt-001",
    )
    synthetic.add_argument("--scratch-root", required=True, type=Path)
    preflight = commands.add_parser(
        "m2-preflight",
        help="run the frozen truth-owner projection and three-model Gate A chain in local scratch",
    )
    preflight.add_argument(
        "--benchmark-task-id",
        choices=("alpha-amylase", "ired"),
        required=True,
    )
    preflight.add_argument("--processing-config", required=True, type=Path)
    preflight.add_argument("--processing-manifest", required=True, type=Path)
    preflight.add_argument("--split-manifest", required=True, type=Path)
    preflight.add_argument("--membership", required=True, type=Path)
    preflight.add_argument("--roles", required=True, type=Path)
    preflight.add_argument("--hidden-truth", required=True, type=Path)
    preflight.add_argument("--experiment-config", required=True, type=Path)
    preflight.add_argument("--gate-a-packet", required=True, type=Path)
    preflight.add_argument("--gate-a-packet-sha256", required=True)
    preflight.add_argument("--git-commit", required=True)
    preflight.add_argument("--environment-digest", required=True)
    preflight.add_argument("--repository-root", required=True, type=Path)
    preflight.add_argument("--scratch-root", required=True, type=Path)
    gate_b = commands.add_parser(
        "m2-gate-b",
        help="execute the one frozen Issue #58 Gate B packet with CVM-role credentials",
    )
    gate_b.add_argument("--packet", required=True, type=Path)
    gate_b.add_argument("--packet-sha256", required=True)
    gate_b.add_argument("--approval", required=True)
    gate_b.add_argument("--runtime-git-commit", required=True)
    gate_b.add_argument("--gate-a-root", required=True, type=Path)
    gate_b.add_argument("--repository-root", required=True, type=Path)
    gate_b.add_argument("--scratch-root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    arguments = build_parser().parse_args(argv)
    if arguments.command == "m2-synthetic":
        from pcdb.m2.cli import run_synthetic_cli

        return run_synthetic_cli(arguments)
    if arguments.command == "m2-preflight":
        from pcdb.m2.cli import run_preflight_cli

        return run_preflight_cli(arguments)
    if arguments.command == "m2-gate-b":
        from pcdb.m2.gate_b_runtime import run_gate_b_cli

        return run_gate_b_cli(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
