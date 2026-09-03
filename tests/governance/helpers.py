from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pcdb.governance.attestation import (
    ATTESTATION_MARKER,
    DecisionEvidenceVerifier,
    decision_payload_digest,
    validate_attestation_shape,
)
from pcdb.governance.checks import sha256_bytes

REPOSITORY = "QUHONGYE-gif/protein-candidate-discovery-benchmark"


def attach_example_attestations(decision: dict[str, Any], *, first_comment_id: int) -> None:
    digest = decision_payload_digest(decision)
    records = decision.get("approvals", decision.get("reviewers"))
    assert isinstance(records, list)
    for index, record in enumerate(records):
        assert isinstance(record, dict)
        actor = record["authority"]
        body = f"Test-only attestation.\n{ATTESTATION_MARKER}: {digest}\n"
        record["attestation"] = {
            "actor": actor,
            "body": body,
            "body_sha256": sha256_bytes(body.encode()),
            "comment_id": first_comment_id + index,
            "decision_digest": digest,
            "node_id": f"TEST_COMMENT_{first_comment_id + index}",
            "repository": REPOSITORY,
            "schema_version": "pcdb-github-comment-attestation-v1",
            "updated_at": "2026-09-02T00:00:00Z",
        }


class FakeDecisionEvidenceVerifier(DecisionEvidenceVerifier):
    def __init__(self) -> None:
        self.verified_comment_ids: list[int] = []

    def verify(
        self,
        evidence: Mapping[str, Any],
        *,
        expected_actor: str,
        expected_repository: str,
        expected_decision_digest: str,
    ) -> Mapping[str, Any]:
        value = validate_attestation_shape(
            evidence,
            field="test attestation",
            expected_actor=expected_actor,
            expected_repository=expected_repository,
            expected_decision_digest=expected_decision_digest,
        )
        self.verified_comment_ids.append(int(value["comment_id"]))
        return value
