from __future__ import annotations

import json
from types import TracebackType
from typing import Any

import pytest

from pcdb.governance.attestation import (
    ATTESTATION_MARKER,
    GitHubCommentEvidenceVerifier,
    decision_payload_digest,
)
from pcdb.governance.checks import CheckViolation, sha256_bytes

REPOSITORY = "QUHONGYE-gif/protein-candidate-discovery-benchmark"


class Response:
    def __init__(self, value: object) -> None:
        self.payload = json.dumps(value).encode()

    def __enter__(self) -> Response:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _evidence(*, remote_actor: str = "willzhang") -> tuple[dict[str, Any], dict[str, Any], str]:
    decision = {"decision_id": "gate-a-test", "verdict": "authorized"}
    digest = decision_payload_digest(decision)
    body = f"I approve this exact decision.\n\n{ATTESTATION_MARKER}: {digest}\n"
    remote = {
        "body": body,
        "id": 123,
        "node_id": "IC_kwDO-test",
        "updated_at": "2026-09-02T00:00:00Z",
        "user": {"login": remote_actor},
    }
    evidence = {
        "actor": "github:willzhang",
        "body": body,
        "body_sha256": sha256_bytes(body.encode()),
        "comment_id": 123,
        "decision_digest": digest,
        "node_id": "IC_kwDO-test",
        "repository": REPOSITORY,
        "schema_version": "pcdb-github-comment-attestation-v1",
        "updated_at": "2026-09-02T00:00:00Z",
    }
    return evidence, remote, digest


def test_github_comment_verifier_authenticates_actor_version_body_and_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, remote, digest = _evidence()
    monkeypatch.setattr(
        "pcdb.governance.attestation.urllib.request.urlopen",
        lambda request, timeout: Response(remote),
    )

    verified = GitHubCommentEvidenceVerifier().verify(
        evidence,
        expected_actor="github:willzhang",
        expected_repository=REPOSITORY,
        expected_decision_digest=digest,
    )

    assert verified["comment_id"] == 123
    assert verified["actor"] == "github:willzhang"


def test_github_comment_verifier_rejects_self_reported_actor_when_remote_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, remote, digest = _evidence(remote_actor="attacker")
    monkeypatch.setattr(
        "pcdb.governance.attestation.urllib.request.urlopen",
        lambda request, timeout: Response(remote),
    )

    with pytest.raises(CheckViolation, match="actor, object version or exact body drifted"):
        GitHubCommentEvidenceVerifier().verify(
            evidence,
            expected_actor="github:willzhang",
            expected_repository=REPOSITORY,
            expected_decision_digest=digest,
        )


def test_github_comment_verifier_fails_closed_on_malformed_remote_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, _, digest = _evidence()
    response = Response({})
    response.payload = b"not-json"
    monkeypatch.setattr(
        "pcdb.governance.attestation.urllib.request.urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(CheckViolation, match="cannot authenticate GitHub decision comment"):
        GitHubCommentEvidenceVerifier().verify(
            evidence,
            expected_actor="github:willzhang",
            expected_repository=REPOSITORY,
            expected_decision_digest=digest,
        )
