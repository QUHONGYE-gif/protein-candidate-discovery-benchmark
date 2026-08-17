"""Reviewed production entry that joins the Issue #76 pilot runtime and screening."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.m3.gate_a import _verified_runtime_environment_digest
from pcdb.m4.execution_adapter import M4ExecutionPreparation, prepare_execution_adapter
from pcdb.m4.pilot_runtime import (
    PILOT_APPROVAL,
    PILOT_EXECUTION_REGION,
    PilotAuthorization,
    PilotExecutionResult,
    execute_reviewed_pilot_locally,
)
from pcdb.m4.screening import (
    PilotRunEvidence,
    PilotScreeningDecision,
    evaluate_pilot_screening,
)
from pcdb.storage import LocalArtifactStore
from pcdb.storage.cos import Issue76M4ReadOnlyCosClient

_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_SCREENING_STATES = {"EXPAND", "STOP", "INCONCLUSIVE-FAILURE"}


@dataclass(frozen=True, slots=True)
class PilotLaunchResult:
    """One runtime result joined directly to its truth-free screening result."""

    execution: PilotExecutionResult
    screening: PilotScreeningDecision


class PilotAttemptFailure(RuntimeError):
    """A sanitized terminal failure from the single reviewed runtime invocation."""

    def __init__(self, *, packet_sha256: str, attempts_started: int) -> None:
        super().__init__("M4 pilot runtime failed")
        self.packet_sha256 = packet_sha256
        self.attempts_started = attempts_started


def _entry_packet_bindings(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
    executed_environment_digest: str,
    approval_comment_id: int,
    approval_text: str,
) -> None:
    if (
        not _SHA256.fullmatch(packet_sha256)
        or hashlib.sha256(packet_bytes).hexdigest() != packet_sha256
        or not _GIT_OBJECT.fullmatch(reviewed_git_commit)
        or not _GIT_OBJECT.fullmatch(reviewed_git_tree)
        or not _SHA256.fullmatch(executed_environment_digest)
        or isinstance(approval_comment_id, bool)
        or not isinstance(approval_comment_id, int)
        or approval_comment_id <= 0
        or approval_text != PILOT_APPROVAL
    ):
        raise ContractValidationError("M4 pilot launch authorization or identity drifted")
    try:
        packet = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("M4 pilot launch packet must be canonical JSON") from None
    reviewed_source = packet.get("reviewed_source") if isinstance(packet, dict) else None
    environment = packet.get("environment_candidate") if isinstance(packet, dict) else None
    if (
        not isinstance(packet, dict)
        or canonical_json_bytes(packet) != packet_bytes
        or reviewed_source != {"git_commit": reviewed_git_commit, "git_tree": reviewed_git_tree}
        or not isinstance(environment, dict)
        or environment.get("sha256") != executed_environment_digest
        or packet.get("initial_resource_ledger")
        != {
            "active_execution_wall_seconds": 0,
            "attempts_started": 0,
            "network_transfer_bytes": 0,
            "paid_compute_cny": 0,
            "scratch_high_water_bytes": 0,
            "unique_run_ids_started": [],
        }
    ):
        raise ContractValidationError("M4 pilot launch packet binding or zero ledger drifted")


def _verify_runtime_source(
    repository_root: Path,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
) -> None:
    """Require the running M4 modules to come from one exact clean reviewed checkout."""

    from pcdb.m4 import execution_adapter as execution_adapter_module
    from pcdb.m4 import pilot_runtime as pilot_runtime_module
    from pcdb.m4 import screening as screening_module
    from pcdb.replay import engine as replay_engine_module
    from pcdb.replay import oracle as hidden_oracle_module
    from pcdb.storage import cos as cos_module

    root = repository_root.resolve()
    expected_files = {
        "execution_adapter": root / "src/pcdb/m4/execution_adapter.py",
        "hidden_oracle": root / "src/pcdb/replay/oracle.py",
        "pilot_launch": root / "src/pcdb/m4/pilot_launch.py",
        "pilot_runtime": root / "src/pcdb/m4/pilot_runtime.py",
        "replay_engine": root / "src/pcdb/replay/engine.py",
        "screening": root / "src/pcdb/m4/screening.py",
        "storage_cos": root / "src/pcdb/storage/cos.py",
    }
    module_files = {
        "execution_adapter": Path(str(execution_adapter_module.__file__)),
        "hidden_oracle": Path(str(hidden_oracle_module.__file__)),
        "pilot_launch": Path(__file__),
        "pilot_runtime": Path(str(pilot_runtime_module.__file__)),
        "replay_engine": Path(str(replay_engine_module.__file__)),
        "screening": Path(str(screening_module.__file__)),
        "storage_cos": Path(str(cos_module.__file__)),
    }
    if any(
        module_files[name].resolve() != expected_file.resolve()
        for name, expected_file in expected_files.items()
    ):
        raise ContractValidationError("M4 pilot executing source differs from repository root")
    try:
        checked_out_commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        checked_out_tree = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        worktree_status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractValidationError("M4 pilot runtime source could not be verified") from exc
    if checked_out_commit != reviewed_git_commit:
        raise ContractValidationError("M4 pilot checkout commit differs from reviewed source")
    if checked_out_tree != reviewed_git_tree:
        raise ContractValidationError("M4 pilot checkout tree differs from reviewed source")
    if worktree_status:
        raise ContractValidationError("M4 pilot checkout must be clean")


def _validated_scratch_root(repository_root: Path, scratch_root: Path) -> Path:
    root = scratch_root.resolve()
    repository = repository_root.resolve()
    if root.is_relative_to(repository):
        relative = root.relative_to(repository)
        if not relative.parts or relative.parts[0] != ".scratch":
            raise ContractValidationError(
                "repository-local M4 pilot scratch must stay inside .scratch"
            )
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ContractValidationError("M4 pilot scratch root must start empty")
    return root


def _screening_evidence(
    *,
    preparation: M4ExecutionPreparation,
    execution: PilotExecutionResult,
    scratch_root: Path,
) -> tuple[PilotRunEvidence, ...]:
    if (
        len(preparation.runs) != 72
        or len(execution.publications) != 72
        or len(execution.definition_metric_keys) != 72
    ):
        raise ContractValidationError("M4 pilot execution cannot exact-cover screening evidence")
    store = LocalArtifactStore(scratch_root / "objects")
    return tuple(
        PilotRunEvidence(
            task_id=prepared.task_id,
            run_spec=prepared.run_spec,
            store=store,
            attempt_key=publication.attempt_key,
            attempt_sha256=publication.attempt_sha256,
            definition_metric_key=definition_key,
        )
        for prepared, publication, definition_key in zip(
            preparation.runs,
            execution.publications,
            execution.definition_metric_keys,
            strict=True,
        )
    )


def _started_attempt_count(scratch_root: Path) -> int:
    ledger = scratch_root.resolve() / "ledger" / "attempts"
    if not ledger.is_dir():
        return 0
    return sum(1 for path in ledger.glob("*.started.json") if path.is_file())


def run_reviewed_pilot(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
    executed_environment_digest: str,
    approval_comment_id: int,
    approval_text: str,
    input_role_arn: str,
    repository_root: Path,
    scratch_root: Path,
) -> PilotLaunchResult:
    """Validate all launch bindings, execute once, then screen once without expansion."""

    _entry_packet_bindings(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        reviewed_git_commit=reviewed_git_commit,
        reviewed_git_tree=reviewed_git_tree,
        executed_environment_digest=executed_environment_digest,
        approval_comment_id=approval_comment_id,
        approval_text=approval_text,
    )
    preparation = prepare_execution_adapter(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=reviewed_git_commit,
        expected_git_tree=reviewed_git_tree,
    )
    if {item.run_spec.environment_digest for item in preparation.runs} != {
        executed_environment_digest
    }:
        raise ContractValidationError("M4 pilot RunSpec environment binding drifted")
    _verify_runtime_source(repository_root, reviewed_git_commit, reviewed_git_tree)
    if _verified_runtime_environment_digest(repository_root) != executed_environment_digest:
        raise ContractValidationError("M4 pilot runtime environment differs from reviewed bytes")
    root = _validated_scratch_root(repository_root, scratch_root)
    authorization = PilotAuthorization(
        approval_comment_id=approval_comment_id,
        approval_text=approval_text,
        packet_sha256=packet_sha256,
        reviewed_git_commit=reviewed_git_commit,
        reviewed_git_tree=reviewed_git_tree,
        executed_environment_digest=executed_environment_digest,
        execution_region=PILOT_EXECUTION_REGION,
    )
    input_client = Issue76M4ReadOnlyCosClient(input_role_arn)
    try:
        execution = execute_reviewed_pilot_locally(
            preparation=preparation,
            packet_bytes=packet_bytes,
            packet_sha256=packet_sha256,
            expected_git_commit=reviewed_git_commit,
            expected_git_tree=reviewed_git_tree,
            repository_root=repository_root,
            input_client=input_client,
            scratch_root=root,
            authorization=authorization,
        )
    except Exception as exc:
        raise PilotAttemptFailure(
            packet_sha256=packet_sha256,
            attempts_started=_started_attempt_count(root),
        ) from exc
    evidence = _screening_evidence(
        preparation=preparation,
        execution=execution,
        scratch_root=root,
    )
    screening = evaluate_pilot_screening(
        evidence,
        expected_runs=tuple((item.task_id, item.run_spec) for item in preparation.runs),
        expected_packet_sha256=packet_sha256,
        expected_execution_region=PILOT_EXECUTION_REGION,
    )
    if screening.status not in _ALLOWED_SCREENING_STATES:
        raise ContractValidationError("M4 pilot screening returned an unauthorized state")
    return PilotLaunchResult(execution=execution, screening=screening)


def pilot_launch_summary_bytes(result: PilotLaunchResult) -> bytes:
    """Return a compact non-secret operator summary for stdout."""

    execution = result.execution
    return canonical_json_bytes(
        {
            "active_execution_wall_seconds": execution.active_execution_wall_seconds,
            "attempts_started": len(execution.unique_run_ids_started),
            "failure_reason": result.screening.failure_reason,
            "network_transfer_bytes": execution.network_transfer_bytes,
            "packet_sha256": execution.packet_sha256,
            "peak_rss_bytes": execution.peak_rss_bytes,
            "record_type": "m4-pilot-launch-summary-v1",
            "scratch_high_water_bytes": execution.scratch_high_water_bytes,
            "screening_status": result.screening.status,
            "selected_explorations": [
                list(item) for item in result.screening.selected_explorations
            ],
            "screening_model_decisions": [
                asdict(item) for item in result.screening.model_decisions
            ],
        }
    )


def pilot_launch_failure_summary_bytes(failure: PilotAttemptFailure) -> bytes:
    """Return the canonical non-secret terminal record for an incomplete pilot."""

    return canonical_json_bytes(
        {
            "attempts_started": failure.attempts_started,
            "failure_reason": "runtime-failure",
            "packet_sha256": failure.packet_sha256,
            "record_type": "m4-pilot-launch-summary-v1",
            "screening_status": "INCONCLUSIVE-FAILURE",
            "selected_explorations": [],
            "screening_model_decisions": [],
        }
    )


def run_pilot_cli(arguments: Any) -> int:
    """Execute the SHA-bound CLI entry and print its non-secret terminal state."""

    import sys

    try:
        result = run_reviewed_pilot(
            packet_bytes=arguments.packet.read_bytes(),
            packet_sha256=arguments.packet_sha256,
            reviewed_git_commit=arguments.reviewed_git_commit,
            reviewed_git_tree=arguments.reviewed_git_tree,
            executed_environment_digest=arguments.environment_digest,
            approval_comment_id=arguments.approval_comment_id,
            approval_text=arguments.approval,
            input_role_arn=arguments.input_role_arn,
            repository_root=arguments.repository_root,
            scratch_root=arguments.scratch_root,
        )
    except PilotAttemptFailure as failure:
        sys.stdout.buffer.write(pilot_launch_failure_summary_bytes(failure))
        return 2
    sys.stdout.buffer.write(pilot_launch_summary_bytes(result))
    return 2 if result.screening.status == "INCONCLUSIVE-FAILURE" else 0


__all__ = [
    "PilotLaunchResult",
    "PilotAttemptFailure",
    "pilot_launch_failure_summary_bytes",
    "pilot_launch_summary_bytes",
    "run_pilot_cli",
    "run_reviewed_pilot",
]
