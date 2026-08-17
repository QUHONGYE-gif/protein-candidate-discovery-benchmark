"""Exact reviewed launch and Gate 3 terminal for Issue #76 conditional expansion."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.m3.gate_a import _verified_runtime_environment_digest
from pcdb.m4.conditional_expansion import (
    ConditionalRunEvidence,
    Gate3Decision,
    materialize_conditional_run_specs,
    prepare_conditional_expansion,
    reconcile_completed_pilot_attempts,
    reconcile_gate_3,
)
from pcdb.m4.conditional_runtime import (
    CONDITIONAL_EXPANSION_APPROVAL,
    ConditionalExpansionAuthorization,
    ConditionalExpansionExecutionResult,
    _validate_authorization,
    execute_reviewed_conditional_expansion_locally,
)
from pcdb.m4.execution_adapter import prepare_execution_adapter
from pcdb.m4.pilot_launch import (
    _started_attempt_count,
    _validated_scratch_root,
    _verify_runtime_source,
)
from pcdb.m4.screening import PilotRunEvidence
from pcdb.storage import LocalArtifactStore
from pcdb.storage.cos import Issue76M4ReadOnlyCosClient


@dataclass(frozen=True, slots=True)
class ConditionalLaunchResult:
    """One exact 272-run execution joined to its fresh Gate 3 reconciliation."""

    execution: ConditionalExpansionExecutionResult
    gate_3: Gate3Decision


class ConditionalAttemptFailure(RuntimeError):
    """Sanitized terminal failure from the one reviewed expansion invocation."""

    def __init__(self, *, packet_sha256: str, new_attempts_started: int) -> None:
        super().__init__("M4 conditional expansion runtime failed")
        self.packet_sha256 = packet_sha256
        self.new_attempts_started = new_attempts_started


def _verify_conditional_source(
    repository_root: Path,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
) -> None:
    _verify_runtime_source(repository_root, reviewed_git_commit, reviewed_git_tree)
    from pcdb.m4 import conditional_expansion as expansion_module
    from pcdb.m4 import conditional_runtime as runtime_module

    root = repository_root.resolve()
    expected = {
        "expansion": root / "src/pcdb/m4/conditional_expansion.py",
        "launch": root / "src/pcdb/m4/conditional_launch.py",
        "runtime": root / "src/pcdb/m4/conditional_runtime.py",
    }
    actual = {
        "expansion": Path(str(expansion_module.__file__)),
        "launch": Path(__file__),
        "runtime": Path(str(runtime_module.__file__)),
    }
    if any(actual[name].resolve() != path.resolve() for name, path in expected.items()):
        raise ContractValidationError("M4 conditional executing source differs from repository")


def run_reviewed_conditional_expansion(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    pilot_packet_bytes: bytes,
    pilot_packet_sha256: str,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
    executed_environment_digest: str,
    approval_comment_id: int,
    approval_text: str,
    input_role_arn: str,
    repository_root: Path,
    pilot_scratch_root: Path,
    scratch_root: Path,
) -> ConditionalLaunchResult:
    """Validate all bindings, execute 272 missing rows once, and fresh-reconcile Gate 3."""

    authorization = ConditionalExpansionAuthorization(
        approval_comment_id=approval_comment_id,
        approval_text=approval_text,
        packet_sha256=packet_sha256,
        reviewed_git_commit=reviewed_git_commit,
        reviewed_git_tree=reviewed_git_tree,
        executed_environment_digest=executed_environment_digest,
        execution_region="ap-chengdu",
    )
    _validate_authorization(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        expected_git_commit=reviewed_git_commit,
        expected_git_tree=reviewed_git_tree,
        authorization=authorization,
    )
    completed = reconcile_completed_pilot_attempts(
        repository_root=repository_root,
        pilot_packet_bytes=pilot_packet_bytes,
        pilot_packet_sha256=pilot_packet_sha256,
        pilot_scratch_root=pilot_scratch_root,
    )
    preparation = prepare_conditional_expansion(
        packet_bytes=packet_bytes,
        packet_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=reviewed_git_commit,
        expected_git_tree=reviewed_git_tree,
        pilot_packet_bytes=pilot_packet_bytes,
        completed_pilot_attempts=completed,
    )
    if {item.run_spec.environment_digest for item in preparation.runs} != {
        executed_environment_digest
    }:
        raise ContractValidationError("M4 conditional RunSpec environment binding drifted")
    _verify_conditional_source(repository_root, reviewed_git_commit, reviewed_git_tree)
    if _verified_runtime_environment_digest(repository_root) != executed_environment_digest:
        raise ContractValidationError(
            "M4 conditional runtime environment differs from reviewed bytes"
        )
    root = _validated_scratch_root(repository_root, scratch_root)
    input_client = Issue76M4ReadOnlyCosClient(input_role_arn)
    try:
        execution = execute_reviewed_conditional_expansion_locally(
            preparation=preparation,
            packet_bytes=packet_bytes,
            packet_sha256=packet_sha256,
            pilot_packet_bytes=pilot_packet_bytes,
            completed_pilot_attempts=completed,
            expected_git_commit=reviewed_git_commit,
            expected_git_tree=reviewed_git_tree,
            repository_root=repository_root,
            input_client=input_client,
            scratch_root=root,
            authorization=authorization,
        )
    except Exception as exc:
        raise ConditionalAttemptFailure(
            packet_sha256=packet_sha256,
            new_attempts_started=_started_attempt_count(root),
        ) from exc

    pilot_source = __import__("json").loads(pilot_packet_bytes)["reviewed_source"]
    pilot_preparation = prepare_execution_adapter(
        packet_bytes=pilot_packet_bytes,
        packet_sha256=pilot_packet_sha256,
        repository_root=repository_root,
        expected_git_commit=str(pilot_source["git_commit"]),
        expected_git_tree=str(pilot_source["git_tree"]),
    )
    matrix = materialize_conditional_run_specs(
        pilot_packet_bytes,
        reviewed_git_commit=reviewed_git_commit,
        selected_explorations=(
            ("random-forest-sklearn-v1", "uncertainty-bootstrap-std-v1"),
            ("xgboost-cpu-v1", "ucb-bootstrap-std-beta1-v1"),
        ),
    )
    pilot_runs = {item.run_spec.run_id: item for item in pilot_preparation.runs}
    completed_by_run = {item.run_id: item for item in completed}
    pilot_store = LocalArtifactStore(pilot_scratch_root.resolve() / "objects")
    pilot_evidence = tuple(
        PilotRunEvidence(
            task_id=pilot_runs[item.run_spec.run_id].task_id,
            run_spec=item.run_spec,
            store=pilot_store,
            attempt_key=completed_by_run[item.run_spec.run_id].attempt_key,
            attempt_sha256=completed_by_run[item.run_spec.run_id].attempt_sha256,
            definition_metric_key=(completed_by_run[item.run_spec.run_id].definition_metric_key),
        )
        for item in matrix.completed_overlap
    )
    expansion_store = LocalArtifactStore(root / "objects")
    expansion_evidence = tuple(
        ConditionalRunEvidence(
            task_id=prepared.task_id,
            run_spec=prepared.run_spec,
            store=expansion_store,
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
    gate_3 = reconcile_gate_3(
        pilot_evidence,
        expansion_evidence,
        expected_pilot_packet_sha256=pilot_packet_sha256,
        expected_expansion_packet_sha256=packet_sha256,
        expected_execution_region="ap-chengdu",
    )
    return ConditionalLaunchResult(execution=execution, gate_3=gate_3)


def conditional_launch_summary_bytes(result: ConditionalLaunchResult) -> bytes:
    """Return the complete non-secret Gate 3 terminal record."""

    execution = result.execution
    return canonical_json_bytes(
        {
            "cumulative_active_execution_wall_seconds": (
                execution.cumulative_active_execution_wall_seconds
            ),
            "cumulative_attempts_started": execution.cumulative_attempts_started,
            "cumulative_network_transfer_bytes": execution.cumulative_network_transfer_bytes,
            "cumulative_scratch_high_water_bytes": (execution.cumulative_scratch_high_water_bytes),
            "failure_reason": result.gate_3.failure_reason,
            "frozen_run_ids": list(result.gate_3.frozen_run_ids),
            "gate_3_cell_decisions": [asdict(item) for item in result.gate_3.cell_decisions],
            "gate_3_seed_evidence": [asdict(item) for item in result.gate_3.seed_evidence],
            "gate_3_status": result.gate_3.status,
            "new_attempts_started": len(execution.unique_run_ids_started),
            "packet_sha256": execution.packet_sha256,
            "peak_rss_bytes": execution.peak_rss_bytes,
            "record_type": "m4-conditional-launch-summary-v1",
        }
    )


def conditional_failure_summary_bytes(failure: ConditionalAttemptFailure) -> bytes:
    """Return the canonical non-secret terminal for a failed one-shot expansion."""

    return canonical_json_bytes(
        {
            "failure_reason": "runtime-failure",
            "gate_3_cell_decisions": [],
            "gate_3_seed_evidence": [],
            "gate_3_status": "INCONCLUSIVE",
            "new_attempts_started": failure.new_attempts_started,
            "packet_sha256": failure.packet_sha256,
            "record_type": "m4-conditional-launch-summary-v1",
        }
    )


def run_conditional_cli(arguments: Any) -> int:
    """Execute the SHA-bound entry and print exactly one terminal record."""

    import sys

    try:
        result = run_reviewed_conditional_expansion(
            packet_bytes=arguments.packet.read_bytes(),
            packet_sha256=arguments.packet_sha256,
            pilot_packet_bytes=arguments.pilot_packet.read_bytes(),
            pilot_packet_sha256=arguments.pilot_packet_sha256,
            reviewed_git_commit=arguments.reviewed_git_commit,
            reviewed_git_tree=arguments.reviewed_git_tree,
            executed_environment_digest=arguments.environment_digest,
            approval_comment_id=arguments.approval_comment_id,
            approval_text=arguments.approval,
            input_role_arn=arguments.input_role_arn,
            repository_root=arguments.repository_root,
            pilot_scratch_root=arguments.pilot_scratch_root,
            scratch_root=arguments.scratch_root,
        )
    except ConditionalAttemptFailure as failure:
        sys.stdout.buffer.write(conditional_failure_summary_bytes(failure))
        return 2
    sys.stdout.buffer.write(conditional_launch_summary_bytes(result))
    return 0 if result.gate_3.status in {"FROZEN", "STOP"} else 2


__all__ = [
    "CONDITIONAL_EXPANSION_APPROVAL",
    "ConditionalAttemptFailure",
    "ConditionalLaunchResult",
    "conditional_failure_summary_bytes",
    "conditional_launch_summary_bytes",
    "run_conditional_cli",
    "run_reviewed_conditional_expansion",
]
