"""CLI adapters for Governance v2 Checks and terminal archive publication."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pcdb.contracts.experiment import ContractValidationError
from pcdb.governance.archive import (
    ArchiveAttemptRecorder,
    CosArchiveStore,
    ImmutableArchiveStore,
    LocalArchiveStore,
    archive_terminal_milestone,
)
from pcdb.governance.checks import CheckReport, CheckViolation
from pcdb.governance.contract import contract_check_report
from pcdb.governance.repository import repository_check_report
from pcdb.governance.server import server_preflight_report


def add_governance_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register Governance v2 without altering historical CLI commands."""

    check = commands.add_parser(
        "governance-check",
        help="run versioned Governance v2 machine Checks; never emits approval events",
    )
    modes = check.add_subparsers(dest="governance_check_mode", required=True)
    repository = modes.add_parser("repository", help="run static docs/schema/example/CI checks")
    repository.add_argument("--repository-root", type=Path, default=Path("."))
    repository.add_argument("--output", type=Path)

    contract = modes.add_parser("contract", help="check one contract, Gates and optional run")
    contract.add_argument("--manifest", required=True, type=Path)
    contract.add_argument("--gate-a", required=True, type=Path)
    contract.add_argument("--gate-b", type=Path)
    contract.add_argument("--run-identity", type=Path)
    contract.add_argument("--config", required=True, type=Path)
    contract.add_argument("--source-root", required=True, type=Path)
    contract.add_argument("--runtime-git-commit")
    contract.add_argument("--runtime-git-tree")
    contract.add_argument("--runtime-environment-digest")
    contract.add_argument("--allow-example", action="store_true")
    contract.add_argument("--output", type=Path)

    server = modes.add_parser("server", help="check server namespace, ACL and capacity")
    server.add_argument("--config", required=True, type=Path)
    server.add_argument("--expected-peak-bytes", required=True, type=int)
    server.add_argument("--available-bytes", required=True, type=int)
    server.add_argument("--observed-group", required=True)
    server.add_argument("--observed-writer", action="append", default=[])
    server.add_argument("--output", type=Path)

    archive = commands.add_parser(
        "governance-archive",
        help="publish one valid Gate B terminal to a new immutable v2 archive prefix",
    )
    archive.add_argument("--request", required=True, type=Path)
    archive.add_argument("--source-root", required=True, type=Path)
    archive.add_argument("--gate-b", required=True, type=Path)
    archive.add_argument("--config", required=True, type=Path)
    destination = archive.add_mutually_exclusive_group(required=True)
    destination.add_argument("--local-store", type=Path)
    destination.add_argument("--cos-bucket")
    archive.add_argument("--cos-region", default="ap-beijing")
    archive.add_argument("--max-transfer-bytes", type=int, default=10_737_418_240)
    archive.add_argument("--attempt-root", required=True, type=Path)
    archive.add_argument("--output", type=Path)


def _emit(value: dict[str, object], output: Path | None) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if output is None:
        sys.stdout.buffer.write(payload)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)


def run_governance_check_cli(arguments: argparse.Namespace) -> int:
    """Run one Check mode and emit the common result contract."""

    try:
        report: CheckReport
        if arguments.governance_check_mode == "repository":
            report = repository_check_report(arguments.repository_root)
        elif arguments.governance_check_mode == "contract":
            report = contract_check_report(
                manifest_path=arguments.manifest,
                gate_a_path=arguments.gate_a,
                gate_b_path=arguments.gate_b,
                run_identity_path=arguments.run_identity,
                config_path=arguments.config,
                source_root=arguments.source_root,
                runtime_git_commit=arguments.runtime_git_commit,
                runtime_git_tree=arguments.runtime_git_tree,
                runtime_environment_digest=arguments.runtime_environment_digest,
                require_final=not arguments.allow_example,
            )
        elif arguments.governance_check_mode == "server":
            report = server_preflight_report(
                config_path=arguments.config,
                expected_peak_bytes=arguments.expected_peak_bytes,
                available_bytes=arguments.available_bytes,
                observed_group=arguments.observed_group,
                observed_writers=arguments.observed_writer,
            )
        else:
            raise CheckViolation("unknown governance check mode")
    except CheckViolation as exc:
        print(f"governance check error: {exc}", file=sys.stderr)
        return 2
    _emit(report.as_dict(), arguments.output)
    return report.exit_code


def run_governance_archive_cli(arguments: argparse.Namespace) -> int:
    """Run the independent terminal milestone archive entry point."""

    try:
        store: ImmutableArchiveStore
        if arguments.local_store is not None:
            store = LocalArchiveStore(arguments.local_store)
        else:
            store = CosArchiveStore(
                bucket=arguments.cos_bucket,
                region=arguments.cos_region,
                max_transfer_bytes=arguments.max_transfer_bytes,
            )
        receipt = archive_terminal_milestone(
            request_path=arguments.request,
            source_root=arguments.source_root,
            gate_b_path=arguments.gate_b,
            config_path=arguments.config,
            store=store,
            recorder=ArchiveAttemptRecorder(arguments.attempt_root),
        )
    except (CheckViolation, ContractValidationError, FileExistsError, OSError) as exc:
        print(f"governance archive error: {exc}", file=sys.stderr)
        return 2
    _emit(receipt.as_dict(), arguments.output)
    return 0
