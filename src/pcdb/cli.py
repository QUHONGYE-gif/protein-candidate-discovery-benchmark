"""Command-line entry point for repository smoke checks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pcdb import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="pcdb")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    from pcdb.governance.cli import add_governance_commands

    add_governance_commands(commands)
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
    m3_packet = commands.add_parser(
        "m3-gate-a-packet",
        help="print the zero-real-input Issue #68 Gate A review packet",
    )
    m3_packet.add_argument("--reviewed-git-commit", required=True)
    m3_packet.add_argument("--reviewed-git-tree", required=True)
    m3_packet.add_argument("--repository-root", required=True, type=Path)
    m3_gate_a = commands.add_parser(
        "m3-gate-a",
        help="run one explicitly authorized read-only Issue #68 sentinel preflight",
    )
    m3_gate_a.add_argument("--packet", required=True, type=Path)
    m3_gate_a.add_argument("--packet-sha256", required=True)
    m3_gate_a.add_argument("--approval", required=True)
    m3_gate_a.add_argument("--repository-root", required=True, type=Path)
    m3_gate_a.add_argument("--scratch-root", required=True, type=Path)
    m3_gate_b_packet = commands.add_parser(
        "m3-gate-b-packet",
        help="print the zero-execution Issue #68 Gate B review packet",
    )
    m3_gate_b_packet.add_argument("--reviewed-git-commit", required=True)
    m3_gate_b_packet.add_argument("--reviewed-git-tree", required=True)
    m3_gate_b_packet.add_argument("--repository-root", required=True, type=Path)
    m3_gate_b_packet.add_argument("--gate-a-repository-root", required=True, type=Path)
    m3_gate_b_packet.add_argument("--gate-a-packet", required=True, type=Path)
    m3_gate_b_packet.add_argument("--gate-a-packet-sha256", required=True)
    m3_gate_b_packet.add_argument("--gate-a-evidence", required=True, type=Path)
    m3_gate_b_packet.add_argument("--gate-a-evidence-sha256", required=True)
    m3_gate_b_packet.add_argument(
        "--gate-a-operator-attestation-comment-id",
        required=True,
        type=int,
    )
    m3_gate_b_packet.add_argument(
        "--gate-a-evidence-acceptance-comment-id",
        type=int,
    )
    m3_gate_b = commands.add_parser(
        "m3-gate-b",
        help="execute one authorized Issue #68 Gate B sequence through four mechanical phases",
    )
    m3_gate_b.add_argument(
        "--phase",
        choices=("sentinel-write", "sentinel-read", "remaining-write", "final-read"),
        required=True,
    )
    m3_gate_b.add_argument("--packet", required=True, type=Path)
    m3_gate_b.add_argument("--packet-sha256", required=True)
    m3_gate_b.add_argument("--approval", required=True)
    m3_gate_b.add_argument("--approval-comment-id", required=True, type=int)
    m3_gate_b.add_argument("--hourly-cost-cny", required=True, type=float)
    m3_gate_b.add_argument("--repository-root", required=True, type=Path)
    m3_gate_b.add_argument("--scratch-root", required=True, type=Path)
    m4_pilot_preflight = commands.add_parser(
        "m4-pilot-preflight",
        help="print the zero-truth Issue #76 pilot preflight packet",
    )
    m4_pilot_preflight.add_argument("--reviewed-git-commit", required=True)
    m4_pilot_preflight.add_argument("--reviewed-git-tree", required=True)
    m4_pilot_preflight.add_argument("--repository-root", required=True, type=Path)
    m4_pilot_run = commands.add_parser(
        "m4-pilot-run",
        help="execute one exact authorized Issue #76 pilot and its screening once",
    )
    m4_pilot_run.add_argument("--packet", required=True, type=Path)
    m4_pilot_run.add_argument("--packet-sha256", required=True)
    m4_pilot_run.add_argument("--approval", required=True)
    m4_pilot_run.add_argument("--approval-comment-id", required=True, type=int)
    m4_pilot_run.add_argument("--reviewed-git-commit", required=True)
    m4_pilot_run.add_argument("--reviewed-git-tree", required=True)
    m4_pilot_run.add_argument("--environment-digest", required=True)
    m4_pilot_run.add_argument("--input-role-arn", required=True)
    m4_pilot_run.add_argument("--repository-root", required=True, type=Path)
    m4_pilot_run.add_argument("--scratch-root", required=True, type=Path)
    m4_conditional_preflight = commands.add_parser(
        "m4-conditional-preflight",
        help="fresh-reconcile the pilot and print the review-only 272-run expansion packet",
    )
    m4_conditional_preflight.add_argument("--reviewed-git-commit", required=True)
    m4_conditional_preflight.add_argument("--reviewed-git-tree", required=True)
    m4_conditional_preflight.add_argument("--repository-root", required=True, type=Path)
    m4_conditional_preflight.add_argument("--pilot-packet", required=True, type=Path)
    m4_conditional_preflight.add_argument("--pilot-packet-sha256", required=True)
    m4_conditional_preflight.add_argument("--pilot-scratch-root", required=True, type=Path)
    m4_conditional_run = commands.add_parser(
        "m4-conditional-run",
        help="execute one exact authorized 272-run expansion and fresh Gate 3 reconciliation",
    )
    m4_conditional_run.add_argument("--packet", required=True, type=Path)
    m4_conditional_run.add_argument("--packet-sha256", required=True)
    m4_conditional_run.add_argument("--pilot-packet", required=True, type=Path)
    m4_conditional_run.add_argument("--pilot-packet-sha256", required=True)
    m4_conditional_run.add_argument("--approval", required=True)
    m4_conditional_run.add_argument("--approval-comment-id", required=True, type=int)
    m4_conditional_run.add_argument("--reviewed-git-commit", required=True)
    m4_conditional_run.add_argument("--reviewed-git-tree", required=True)
    m4_conditional_run.add_argument("--environment-digest", required=True)
    m4_conditional_run.add_argument("--input-role-arn", required=True)
    m4_conditional_run.add_argument("--repository-root", required=True, type=Path)
    m4_conditional_run.add_argument("--pilot-scratch-root", required=True, type=Path)
    m4_conditional_run.add_argument("--scratch-root", required=True, type=Path)
    proteingym = commands.add_parser(
        "proteingym-four-assay",
        help=(
            "execute one authorized ProteinGym four-assay acquisition and "
            "data-readiness publication"
        ),
    )
    proteingym.add_argument("--config", required=True, type=Path)
    proteingym.add_argument("--reviewed-git-commit", required=True)
    proteingym.add_argument("--approval", required=True)
    proteingym.add_argument("--approval-comment-id", required=True, type=int)
    proteingym.add_argument("--cos-destination", required=True)
    proteingym.add_argument("--repository-root", required=True, type=Path)
    proteingym.add_argument("--scratch-root", required=True, type=Path)
    proteingym_bundle = commands.add_parser(
        "proteingym-local-bundle",
        help="acquire and seal the authorized private ProteinGym local handoff bundle",
    )
    proteingym_bundle.add_argument("--config", required=True, type=Path)
    proteingym_bundle.add_argument("--reviewed-git-commit", required=True)
    proteingym_bundle.add_argument("--approval", required=True)
    proteingym_bundle.add_argument("--approval-comment-id", required=True, type=int)
    proteingym_bundle.add_argument("--original-approval-comment-id", required=True, type=int)
    proteingym_bundle.add_argument("--repository-root", required=True, type=Path)
    proteingym_bundle.add_argument("--scratch-root", required=True, type=Path)
    proteingym_continue = commands.add_parser(
        "proteingym-continue",
        help="continue ProteinGym publication with prefix-wide exact-match reuse",
    )
    proteingym_continue.add_argument("--config", required=True, type=Path)
    proteingym_continue.add_argument("--reviewed-git-commit", required=True)
    proteingym_continue.add_argument("--original-reviewed-git-commit", required=True)
    proteingym_continue.add_argument("--approval", required=True)
    proteingym_continue.add_argument("--approval-comment-id", required=True, type=int)
    proteingym_continue.add_argument("--bundle-approval-comment-id", required=True, type=int)
    proteingym_continue.add_argument("--original-approval-comment-id", required=True, type=int)
    proteingym_continue.add_argument(
        "--prior-continuation-approval-comment-id", action="append", default=[], type=int
    )
    proteingym_continue.add_argument("--cos-destination", required=True)
    proteingym_continue.add_argument("--repository-root", required=True, type=Path)
    proteingym_continue.add_argument("--bundle-root", required=True, type=Path)
    proteingym_continue.add_argument("--scratch-root", required=True, type=Path)
    m5_seed_tracer_preflight = commands.add_parser(
        "m5-seed-tracer-preflight",
        help="print the zero-truth exact 28-RunSpec Issue #85 tracer packet",
    )
    m5_seed_tracer_preflight.add_argument("--reviewed-git-commit", required=True)
    m5_seed_tracer_preflight.add_argument("--reviewed-git-tree", required=True)
    m5_seed_tracer_preflight.add_argument("--repository-root", required=True, type=Path)
    m5_seed_tracer_run = commands.add_parser(
        "m5-seed-tracer-run",
        help="execute one exact authorized Issue #85 28-RunSpec tracer once",
    )
    m5_seed_tracer_run.add_argument("--packet", required=True, type=Path)
    m5_seed_tracer_run.add_argument("--packet-sha256", required=True)
    m5_seed_tracer_run.add_argument("--approval", required=True)
    m5_seed_tracer_run.add_argument("--approval-comment-id", required=True, type=int)
    m5_seed_tracer_run.add_argument("--reviewed-git-commit", required=True)
    m5_seed_tracer_run.add_argument("--reviewed-git-tree", required=True)
    m5_seed_tracer_run.add_argument("--environment-digest", required=True)
    m5_seed_tracer_run.add_argument("--hourly-cost-cny", required=True)
    m5_seed_tracer_run.add_argument("--input-role-arn", required=True)
    m5_seed_tracer_run.add_argument("--repository-root", required=True, type=Path)
    m5_seed_tracer_run.add_argument("--scratch-root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    arguments = build_parser().parse_args(argv)
    if arguments.command == "governance-check":
        from pcdb.governance.cli import run_governance_check_cli

        return run_governance_check_cli(arguments)
    if arguments.command == "governance-archive":
        from pcdb.governance.cli import run_governance_archive_cli

        return run_governance_archive_cli(arguments)
    if arguments.command == "m2-synthetic":
        from pcdb.m2.cli import run_synthetic_cli

        return run_synthetic_cli(arguments)
    if arguments.command == "m2-preflight":
        from pcdb.m2.cli import run_preflight_cli

        return run_preflight_cli(arguments)
    if arguments.command == "m2-gate-b":
        from pcdb.m2.gate_b_runtime import run_gate_b_cli

        return run_gate_b_cli(arguments)
    if arguments.command == "m3-gate-a-packet":
        from pcdb.m3.gate_a import build_gate_a_packet

        payload = build_gate_a_packet(
            arguments.repository_root,
            arguments.reviewed_git_commit,
            arguments.reviewed_git_tree,
        )
        sys.stdout.buffer.write(payload)
        return 0
    if arguments.command == "m3-gate-a":
        from pcdb.m3.gate_a_runtime import run_gate_a_cli

        return run_gate_a_cli(arguments)
    if arguments.command == "m3-gate-b-packet":
        from pcdb.m3.gate_b import build_gate_b_packet

        payload = build_gate_b_packet(
            arguments.repository_root,
            reviewed_git_commit=arguments.reviewed_git_commit,
            reviewed_git_tree=arguments.reviewed_git_tree,
            gate_a_repository_root=arguments.gate_a_repository_root,
            gate_a_packet_bytes=arguments.gate_a_packet.read_bytes(),
            gate_a_packet_sha256=arguments.gate_a_packet_sha256,
            gate_a_evidence_bytes=arguments.gate_a_evidence.read_bytes(),
            gate_a_evidence_sha256=arguments.gate_a_evidence_sha256,
            gate_a_operator_attestation_comment_id=(
                arguments.gate_a_operator_attestation_comment_id
            ),
            gate_a_evidence_acceptance_comment_id=(arguments.gate_a_evidence_acceptance_comment_id),
        )
        sys.stdout.buffer.write(payload)
        return 0
    if arguments.command == "m3-gate-b":
        from pcdb.m3.gate_b_runtime import run_gate_b_cli

        return run_gate_b_cli(arguments)
    if arguments.command == "m4-pilot-preflight":
        from pcdb.m4.pilot_preflight import build_pilot_preflight_packet

        payload = build_pilot_preflight_packet(
            arguments.repository_root,
            arguments.reviewed_git_commit,
            arguments.reviewed_git_tree,
        )
        sys.stdout.buffer.write(payload)
        return 0
    if arguments.command == "m4-pilot-run":
        from pcdb.m4.pilot_launch import run_pilot_cli

        return run_pilot_cli(arguments)
    if arguments.command == "m4-conditional-preflight":
        from pcdb.m4.conditional_expansion import (
            build_conditional_expansion_packet,
            reconcile_completed_pilot_attempts,
        )

        pilot_packet = arguments.pilot_packet.read_bytes()
        completed = reconcile_completed_pilot_attempts(
            repository_root=arguments.repository_root,
            pilot_packet_bytes=pilot_packet,
            pilot_packet_sha256=arguments.pilot_packet_sha256,
            pilot_scratch_root=arguments.pilot_scratch_root,
        )
        payload = build_conditional_expansion_packet(
            arguments.repository_root,
            reviewed_git_commit=arguments.reviewed_git_commit,
            reviewed_git_tree=arguments.reviewed_git_tree,
            pilot_packet_bytes=pilot_packet,
            completed_pilot_attempts=completed,
        )
        sys.stdout.buffer.write(payload)
        return 0
    if arguments.command == "m4-conditional-run":
        from pcdb.m4.conditional_launch import run_conditional_cli

        return run_conditional_cli(arguments)
    if arguments.command == "proteingym-four-assay":
        from pcdb.datasets.proteingym import run_cli

        return run_cli(arguments)
    if arguments.command == "proteingym-local-bundle":
        from pcdb.datasets.proteingym import run_local_bundle_cli

        return run_local_bundle_cli(arguments)
    if arguments.command == "proteingym-continue":
        from pcdb.datasets.proteingym import run_continuation_cli

        return run_continuation_cli(arguments)
    if arguments.command == "m5-seed-tracer-preflight":
        from pcdb.m5.seed_tracer import build_seed_tracer_packet

        payload = build_seed_tracer_packet(
            arguments.repository_root,
            arguments.reviewed_git_commit,
            arguments.reviewed_git_tree,
        )
        sys.stdout.buffer.write(payload)
        return 0
    if arguments.command == "m5-seed-tracer-run":
        from pcdb.m5.seed_tracer_runtime import run_seed_tracer_cli

        return run_seed_tracer_cli(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
