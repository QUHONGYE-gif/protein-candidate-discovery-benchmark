"""Fail-closed runtime tests for the Issue #76 272-run expansion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pcdb.contracts import ContractValidationError
from pcdb.m4.conditional_expansion import (
    CompletedPilotAttempt,
    build_conditional_expansion_packet,
    prepare_conditional_expansion,
)
from pcdb.m4.conditional_runtime import (
    CONDITIONAL_EXPANSION_APPROVAL,
    ConditionalExpansionAuthorization,
    execute_reviewed_conditional_expansion_locally,
)
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet
from pcdb.replay.publication import ReplayPublication

ROOT = Path(__file__).resolve().parents[2]
PILOT_COMMIT = "a" * 40
PILOT_TREE = "b" * 40
EXPANSION_COMMIT = "c" * 40
EXPANSION_TREE = "d" * 40
ENVIRONMENT = "0994a73f124c2baa6f330bf8739db1673217bdec6ad7d9046ded908fc8dba4fc"


def _inputs() -> tuple[bytes, tuple[CompletedPilotAttempt, ...], bytes, object]:
    pilot = build_pilot_preflight_packet(ROOT, PILOT_COMMIT, PILOT_TREE)
    pilot_sha = hashlib.sha256(pilot).hexdigest()
    completed = tuple(
        CompletedPilotAttempt(
            run_id=item["run_id"],
            attempt_key=(
                f"m4/issue-76/pilot/{pilot_sha}/runs/{item['run_id']}/"
                "attempts/attempt-001/run-attempt.json"
            ),
            attempt_sha256=f"{index + 1:064x}",
            definition_metric_key=(
                f"m4/issue-76/pilot/{pilot_sha}/runs/{item['run_id']}/"
                "attempts/attempt-001/pilot-final-definition-metrics.json"
            ),
        )
        for index, item in enumerate(json.loads(pilot)["run_specs"])
    )
    packet = build_conditional_expansion_packet(
        ROOT,
        reviewed_git_commit=EXPANSION_COMMIT,
        reviewed_git_tree=EXPANSION_TREE,
        pilot_packet_bytes=pilot,
        completed_pilot_attempts=completed,
    )
    preparation = prepare_conditional_expansion(
        packet_bytes=packet,
        packet_sha256=hashlib.sha256(packet).hexdigest(),
        repository_root=ROOT,
        expected_git_commit=EXPANSION_COMMIT,
        expected_git_tree=EXPANSION_TREE,
        pilot_packet_bytes=pilot,
        completed_pilot_attempts=completed,
    )
    return pilot, completed, packet, preparation


def test_missing_exact_authorization_fails_before_remote_input_or_scratch(tmp_path: Path) -> None:
    pilot, completed, packet, preparation = _inputs()

    class InputClient:
        transferred_bytes = 0

        def head(self, *_: object) -> object:
            raise AssertionError("authorization drift reached remote HEAD")

        def get(self, *_: object) -> bytes:
            raise AssertionError("authorization drift reached remote GET")

    scratch = tmp_path / "expansion"
    with pytest.raises(ContractValidationError, match="external authorization"):
        execute_reviewed_conditional_expansion_locally(
            preparation=preparation,  # type: ignore[arg-type]
            packet_bytes=packet,
            packet_sha256=hashlib.sha256(packet).hexdigest(),
            pilot_packet_bytes=pilot,
            completed_pilot_attempts=completed,
            expected_git_commit=EXPANSION_COMMIT,
            expected_git_tree=EXPANSION_TREE,
            repository_root=ROOT,
            input_client=InputClient(),
            scratch_root=scratch,
            authorization=ConditionalExpansionAuthorization(
                approval_comment_id=1,
                approval_text=CONDITIONAL_EXPANSION_APPROVAL + "-drift",
                packet_sha256=hashlib.sha256(packet).hexdigest(),
                reviewed_git_commit=EXPANSION_COMMIT,
                reviewed_git_tree=EXPANSION_TREE,
                executed_environment_digest=ENVIRONMENT,
                execution_region="ap-chengdu",
            ),
        )

    assert not scratch.exists()


def test_exact_runtime_starts_only_272_missing_attempts_and_reaches_344_cumulative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pilot, completed, packet, preparation = _inputs()
    packet_sha256 = hashlib.sha256(packet).hexdigest()

    class InputClient:
        transferred_bytes = 0

    class Trajectory:
        def __init__(self, run_id: str) -> None:
            self.run_id = run_id
            self.complete = True
            self.sha256 = hashlib.sha256(run_id.encode()).hexdigest()

    class Engine:
        def __init__(self, run_id: str) -> None:
            self.run_id = run_id

        def run(self) -> Trajectory:
            return Trajectory(self.run_id)

        def final_m4_definition_metrics(self) -> tuple[object, ...]:
            return ()

    monkeypatch.setattr(
        "pcdb.m4.conditional_runtime._read_reviewed_inputs",
        lambda preparation, *_: {task.task_id: {} for task in preparation.tasks},
    )
    monkeypatch.setattr(
        "pcdb.m4.conditional_runtime._build_replay_engine_from_validated_preparation",
        lambda **values: Engine(values["run_spec"].run_id),
    )
    monkeypatch.setattr(
        "pcdb.m4.conditional_runtime._conditional_definition_metric_record",
        lambda **_: b"{}\n",
    )

    def publish(trajectory: Trajectory, **values: object) -> ReplayPublication:
        output_key = values["additional_outputs"][0][0]  # type: ignore[index]
        prefix = values["key_prefix"]
        return ReplayPublication(
            attempt_key=(
                f"{prefix}/runs/{trajectory.run_id}/attempts/attempt-001/run-attempt.json"
            ),
            attempt_sha256=hashlib.sha256(trajectory.run_id.encode()).hexdigest(),
            marker_key=f"{prefix}/runs/{trajectory.run_id}/attempts/attempt-001/_SUCCESS",
            output_keys=(output_key,),
        )

    monkeypatch.setattr("pcdb.m4.conditional_runtime.publish_replay_attempt", publish)
    scratch = tmp_path / "expansion"
    result = execute_reviewed_conditional_expansion_locally(
        preparation=preparation,  # type: ignore[arg-type]
        packet_bytes=packet,
        packet_sha256=packet_sha256,
        pilot_packet_bytes=pilot,
        completed_pilot_attempts=completed,
        expected_git_commit=EXPANSION_COMMIT,
        expected_git_tree=EXPANSION_TREE,
        repository_root=ROOT,
        input_client=InputClient(),  # type: ignore[arg-type]
        scratch_root=scratch,
        authorization=ConditionalExpansionAuthorization(
            approval_comment_id=123,
            approval_text=CONDITIONAL_EXPANSION_APPROVAL,
            packet_sha256=packet_sha256,
            reviewed_git_commit=EXPANSION_COMMIT,
            reviewed_git_tree=EXPANSION_TREE,
            executed_environment_digest=ENVIRONMENT,
            execution_region="ap-chengdu",
        ),
        clock=lambda: 1.0,
    )

    assert len(result.publications) == 272
    assert len(result.unique_run_ids_started) == 272
    assert result.cumulative_attempts_started == 344
    assert len(list((scratch / "ledger" / "attempts").glob("*.started.json"))) == 272
