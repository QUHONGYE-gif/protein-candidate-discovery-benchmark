"""Exact-entry and least-privilege checks for the Issue #76 M4 pilot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pcdb.cli import build_parser
from pcdb.contracts import ContractValidationError
from pcdb.m4.execution_adapter import prepare_execution_adapter
from pcdb.m4.pilot_launch import run_reviewed_pilot
from pcdb.m4.pilot_preflight import build_pilot_preflight_packet
from pcdb.m4.pilot_runtime import (
    PILOT_APPROVAL,
    PILOT_EXECUTION_REGION,
    PilotExecutionResult,
)
from pcdb.m4.screening import PilotScreeningDecision
from pcdb.replay.publication import ReplayPublication
from pcdb.storage.cos import (
    AssumeRoleCredentialsProvider,
    CosCredentials,
    CvmRoleCredentialsProvider,
    Issue76M4ReadOnlyCosClient,
)

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_COMMIT = "a" * 40
REVIEWED_TREE = "b" * 40
ENVIRONMENT_DIGEST = "0994a73f124c2baa6f330bf8739db1673217bdec6ad7d9046ded908fc8dba4fc"
ROLE_ARN = "qcs::cam::uin/10001:roleName/PCDBIssue76M4InputReader"


def _packet() -> tuple[bytes, str]:
    payload = build_pilot_preflight_packet(ROOT, REVIEWED_COMMIT, REVIEWED_TREE)
    return payload, hashlib.sha256(payload).hexdigest()


def _publications() -> tuple[ReplayPublication, ...]:
    return tuple(
        ReplayPublication(
            attempt_key=f"runs/{index}/run-attempt.json",
            attempt_sha256=f"{index:064x}",
            marker_key=f"runs/{index}/_SUCCESS",
            output_keys=(f"runs/{index}/pilot-final-definition-metrics.json",),
        )
        for index in range(72)
    )


def test_assume_role_uses_exact_issuer_chain_and_never_exposes_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer = CosCredentials(
        "AKIDISSUER",
        "issuer-secret-must-not-leak",
        "issuer-token",
        expires_at=1_700_100_000,
    )
    requests: list[Any] = []

    class Response:
        headers: dict[str, str] = {}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "Response": {
                        "Credentials": {
                            "TmpSecretId": "AKIDREADER",
                            "TmpSecretKey": "reader-secret",
                            "Token": "reader-token",
                        },
                        "ExpiredTime": 1_700_021_600,
                        "Expiration": "2023-11-15T04:46:40Z",
                        "RequestId": "request-id",
                    }
                }
            ).encode()

    class Opener:
        def open(self, request: Any, timeout: int) -> Response:
            requests.append(request)
            assert timeout == 10
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *_: Opener())
    provider = AssumeRoleCredentialsProvider(
        ROLE_ARN,
        issuer_credentials=lambda: issuer,
        now=lambda: 1_700_000_000,
    )

    credentials = provider.get()

    assert credentials == CosCredentials(
        "AKIDREADER",
        "reader-secret",
        "reader-token",
        expires_at=1_700_021_600,
    )
    assert provider.get() is credentials
    assert len(requests) == 1
    request = requests[0]
    body = json.loads(request.data)
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.full_url == "https://sts.tencentcloudapi.com/"
    assert body == {
        "DurationSeconds": 21_600,
        "RoleArn": ROLE_ARN,
        "RoleSessionName": "pcdb-issue76-m4-pilot",
    }
    assert headers["x-tc-action"] == "AssumeRole"
    assert headers["x-tc-version"] == "2018-08-13"
    assert headers["x-tc-region"] == PILOT_EXECUTION_REGION
    assert headers["x-tc-token"] == "issuer-token"
    assert "issuer-secret-must-not-leak" not in str(headers)
    assert "issuer-secret-must-not-leak" not in str(request.data)


def test_assume_role_rejects_wrong_target_before_issuer_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer_calls = 0
    network_calls = 0

    def issuer() -> CosCredentials:
        nonlocal issuer_calls
        issuer_calls += 1
        raise AssertionError("wrong target must not read issuer credentials")

    class Opener:
        def open(self, *_: object, **__: object) -> None:
            nonlocal network_calls
            network_calls += 1
            raise AssertionError("wrong target must not reach STS")

    monkeypatch.setattr("urllib.request.build_opener", lambda *_: Opener())
    with pytest.raises(ContractValidationError, match="Issue #76 input-reader role"):
        AssumeRoleCredentialsProvider(
            "qcs::cam::uin/10001:roleName/Administrator",
            issuer_credentials=issuer,
        )

    assert issuer_calls == 0
    assert network_calls == 0


def test_assume_role_rejects_expiry_drift_without_returning_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "Response": {
                        "Credentials": {
                            "TmpSecretId": "AKIDREADER",
                            "TmpSecretKey": "reader-secret",
                            "Token": "reader-token",
                        },
                        "ExpiredTime": 1_700_000_100,
                    }
                }
            ).encode()

    class Opener:
        def open(self, *_: object, **__: object) -> Response:
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *_: Opener())
    provider = AssumeRoleCredentialsProvider(
        ROLE_ARN,
        issuer_credentials=lambda: CosCredentials(
            "AKIDISSUER",
            "issuer-secret-must-not-leak",
            "issuer-token",
        ),
        now=lambda: 1_700_000_000,
    )

    with pytest.raises(ContractValidationError, match="credential response drifted") as error:
        provider.get()

    assert "issuer-secret-must-not-leak" not in str(error.value)
    assert "reader-secret" not in str(error.value)


def test_cvm_metadata_role_is_exact_issuer_not_direct_input_reader() -> None:
    CvmRoleCredentialsProvider("PCDBStsIssuer")
    with pytest.raises(ContractValidationError, match="unexpected PCDB CVM role"):
        CvmRoleCredentialsProvider("PCDBIssue76M4InputReader")


def test_issue76_input_client_exposes_only_head_get_and_exact_transfer_cap() -> None:
    client = Issue76M4ReadOnlyCosClient(
        ROLE_ARN,
        credentials=lambda: CosCredentials("id", "secret", "token"),
    )

    assert client.transferred_bytes == 0
    assert not hasattr(client, "put_forbid_overwrite")
    assert not hasattr(client, "list_keys")
    assert not hasattr(client, "delete")
    with pytest.raises(ContractValidationError, match="2 GiB"):
        Issue76M4ReadOnlyCosClient(
            ROLE_ARN,
            credentials=lambda: CosCredentials("id", "secret", "token"),
            max_transfer_bytes=1,
        )


def test_launch_validates_source_environment_and_empty_scratch_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    client_calls = 0

    class Client:
        def __init__(self, *_: object, **__: object) -> None:
            nonlocal client_calls
            client_calls += 1

    monkeypatch.setattr("pcdb.m4.pilot_launch.Issue76M4ReadOnlyCosClient", Client)
    monkeypatch.setattr(
        "pcdb.m4.pilot_launch._verify_runtime_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ContractValidationError("source identity drifted")
        ),
    )

    with pytest.raises(ContractValidationError, match="source identity"):
        run_reviewed_pilot(
            packet_bytes=packet,
            packet_sha256=digest,
            reviewed_git_commit=REVIEWED_COMMIT,
            reviewed_git_tree=REVIEWED_TREE,
            executed_environment_digest=ENVIRONMENT_DIGEST,
            approval_comment_id=123,
            approval_text=PILOT_APPROVAL,
            input_role_arn=ROLE_ARN,
            repository_root=ROOT,
            scratch_root=tmp_path / "pilot",
        )

    assert client_calls == 0
    assert not (tmp_path / "pilot").exists()


def test_launch_rejects_packet_drift_before_credentials_or_scratch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    client_calls = 0

    class Client:
        def __init__(self, *_: object, **__: object) -> None:
            nonlocal client_calls
            client_calls += 1

    monkeypatch.setattr("pcdb.m4.pilot_launch.Issue76M4ReadOnlyCosClient", Client)
    with pytest.raises(ContractValidationError, match="identity drifted"):
        run_reviewed_pilot(
            packet_bytes=packet + b"\n",
            packet_sha256=digest,
            reviewed_git_commit=REVIEWED_COMMIT,
            reviewed_git_tree=REVIEWED_TREE,
            executed_environment_digest=ENVIRONMENT_DIGEST,
            approval_comment_id=123,
            approval_text=PILOT_APPROVAL,
            input_role_arn=ROLE_ARN,
            repository_root=ROOT,
            scratch_root=tmp_path / "pilot",
        )

    assert client_calls == 0
    assert not (tmp_path / "pilot").exists()


def test_launch_rejects_nonempty_scratch_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    scratch = tmp_path / "pilot"
    scratch.mkdir()
    (scratch / "stale").write_text("not empty")
    client_calls = 0

    class Client:
        def __init__(self, *_: object, **__: object) -> None:
            nonlocal client_calls
            client_calls += 1

    monkeypatch.setattr("pcdb.m4.pilot_launch._verify_runtime_source", lambda *_: None)
    monkeypatch.setattr(
        "pcdb.m4.pilot_launch._verified_runtime_environment_digest",
        lambda *_: ENVIRONMENT_DIGEST,
    )
    monkeypatch.setattr("pcdb.m4.pilot_launch.Issue76M4ReadOnlyCosClient", Client)

    with pytest.raises(ContractValidationError, match="start empty"):
        run_reviewed_pilot(
            packet_bytes=packet,
            packet_sha256=digest,
            reviewed_git_commit=REVIEWED_COMMIT,
            reviewed_git_tree=REVIEWED_TREE,
            executed_environment_digest=ENVIRONMENT_DIGEST,
            approval_comment_id=123,
            approval_text=PILOT_APPROVAL,
            input_role_arn=ROLE_ARN,
            repository_root=ROOT,
            scratch_root=scratch,
        )

    assert client_calls == 0


def test_launch_calls_runtime_and_screening_once_without_conditional_expansion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    preparation = prepare_execution_adapter(
        packet_bytes=packet,
        packet_sha256=digest,
        repository_root=ROOT,
        expected_git_commit=REVIEWED_COMMIT,
        expected_git_tree=REVIEWED_TREE,
    )
    preparation = replace(
        preparation,
        runs=tuple(
            replace(
                item,
                run_spec=replace(item.run_spec, environment_digest=ENVIRONMENT_DIGEST),
            )
            for item in preparation.runs
        ),
    )
    runtime_calls = 0
    screening_calls = 0

    class Client:
        transferred_bytes = 0

        def __init__(self, role_arn: str) -> None:
            assert role_arn == ROLE_ARN

    publications = _publications()
    execution = PilotExecutionResult(
        packet_sha256=digest,
        publications=publications,
        definition_metric_keys=tuple(item.output_keys[0] for item in publications),
        unique_run_ids_started=tuple(item.run_spec.run_id for item in preparation.runs),
        network_transfer_bytes=123,
        scratch_high_water_bytes=456,
        peak_rss_bytes=789,
        active_execution_wall_seconds=10.0,
    )
    decision = PilotScreeningDecision(
        status="STOP",
        selected_explorations=(),
        model_decisions=(),
        failure_reason=None,
    )

    monkeypatch.setattr("pcdb.m4.pilot_launch._verify_runtime_source", lambda *_: None)
    monkeypatch.setattr(
        "pcdb.m4.pilot_launch._verified_runtime_environment_digest",
        lambda *_: ENVIRONMENT_DIGEST,
    )
    monkeypatch.setattr(
        "pcdb.m4.pilot_launch.prepare_execution_adapter",
        lambda **_: preparation,
    )
    monkeypatch.setattr("pcdb.m4.pilot_launch.Issue76M4ReadOnlyCosClient", Client)

    def execute(**kwargs: Any) -> PilotExecutionResult:
        nonlocal runtime_calls
        runtime_calls += 1
        assert kwargs["preparation"] is preparation
        assert kwargs["authorization"].approval_comment_id == 123
        return execution

    def screen(evidence: Any, **kwargs: Any) -> PilotScreeningDecision:
        nonlocal screening_calls
        screening_calls += 1
        assert len(evidence) == 72
        assert kwargs["expected_runs"] == tuple(
            (item.task_id, item.run_spec) for item in preparation.runs
        )
        return decision

    monkeypatch.setattr("pcdb.m4.pilot_launch.execute_reviewed_pilot_locally", execute)
    monkeypatch.setattr("pcdb.m4.pilot_launch.evaluate_pilot_screening", screen)

    result = run_reviewed_pilot(
        packet_bytes=packet,
        packet_sha256=digest,
        reviewed_git_commit=REVIEWED_COMMIT,
        reviewed_git_tree=REVIEWED_TREE,
        executed_environment_digest=ENVIRONMENT_DIGEST,
        approval_comment_id=123,
        approval_text=PILOT_APPROVAL,
        input_role_arn=ROLE_ARN,
        repository_root=ROOT,
        scratch_root=tmp_path / "pilot",
    )

    assert result.execution is execution
    assert result.screening is decision
    assert runtime_calls == 1
    assert screening_calls == 1


def test_launch_runtime_failure_stops_without_screening_or_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, digest = _packet()
    runtime_calls = 0
    screening_calls = 0

    monkeypatch.setattr("pcdb.m4.pilot_launch._verify_runtime_source", lambda *_: None)
    monkeypatch.setattr(
        "pcdb.m4.pilot_launch._verified_runtime_environment_digest",
        lambda *_: ENVIRONMENT_DIGEST,
    )
    monkeypatch.setattr(
        "pcdb.m4.pilot_launch.Issue76M4ReadOnlyCosClient",
        lambda *_: object(),
    )

    def execute(**_: Any) -> PilotExecutionResult:
        nonlocal runtime_calls
        runtime_calls += 1
        raise RuntimeError("attempt failed")

    def screen(*_: Any, **__: Any) -> PilotScreeningDecision:
        nonlocal screening_calls
        screening_calls += 1
        raise AssertionError("failed runtime must not screen")

    monkeypatch.setattr("pcdb.m4.pilot_launch.execute_reviewed_pilot_locally", execute)
    monkeypatch.setattr("pcdb.m4.pilot_launch.evaluate_pilot_screening", screen)

    with pytest.raises(RuntimeError, match="attempt failed"):
        run_reviewed_pilot(
            packet_bytes=packet,
            packet_sha256=digest,
            reviewed_git_commit=REVIEWED_COMMIT,
            reviewed_git_tree=REVIEWED_TREE,
            executed_environment_digest=ENVIRONMENT_DIGEST,
            approval_comment_id=123,
            approval_text=PILOT_APPROVAL,
            input_role_arn=ROLE_ARN,
            repository_root=ROOT,
            scratch_root=tmp_path / "pilot",
        )

    assert runtime_calls == 1
    assert screening_calls == 0


def test_cli_exposes_one_sha_bound_m4_pilot_entry() -> None:
    arguments = build_parser().parse_args(
        [
            "m4-pilot-run",
            "--packet",
            "/reviewed/pilot.json",
            "--packet-sha256",
            "d" * 64,
            "--approval",
            PILOT_APPROVAL,
            "--approval-comment-id",
            "123",
            "--reviewed-git-commit",
            REVIEWED_COMMIT,
            "--reviewed-git-tree",
            REVIEWED_TREE,
            "--environment-digest",
            ENVIRONMENT_DIGEST,
            "--input-role-arn",
            ROLE_ARN,
            "--repository-root",
            str(ROOT),
            "--scratch-root",
            "/scratch/issue-76",
        ]
    )

    assert arguments.command == "m4-pilot-run"
    assert arguments.packet_sha256 == "d" * 64
    assert arguments.approval_comment_id == 123
    assert arguments.input_role_arn == ROLE_ARN
