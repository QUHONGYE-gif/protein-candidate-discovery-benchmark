"""Authenticated GitHub comment evidence for human Governance v2 decisions."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol, cast

from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.governance.checks import CheckViolation, sha256_bytes

ATTESTATION_SCHEMA = "pcdb-github-comment-attestation-v1"
ATTESTATION_MARKER = "PCDB-GOVERNANCE-DECISION-SHA256"


class DecisionEvidenceVerifier(Protocol):
    """Trusted boundary for resolving one external human decision object."""

    def verify(
        self,
        evidence: Mapping[str, Any],
        *,
        expected_actor: str,
        expected_repository: str,
        expected_decision_digest: str,
    ) -> Mapping[str, Any]:
        """Verify actor, object version, exact body and decision digest."""
        ...


def decision_payload_digest(value: Mapping[str, Any]) -> str:
    """Hash a decision after removing transport attestations from its payload."""

    def strip(item: object) -> object:
        if isinstance(item, dict):
            return {key: strip(child) for key, child in item.items() if key != "attestation"}
        if isinstance(item, list):
            return [strip(child) for child in item]
        return item

    return sha256_bytes(canonical_json_bytes(strip(dict(value))))


def validate_attestation_shape(
    evidence: object,
    *,
    field: str,
    expected_actor: str,
    expected_repository: str,
    expected_decision_digest: str,
) -> Mapping[str, Any]:
    """Validate the closed local envelope before crossing the network boundary."""

    if not isinstance(evidence, dict):
        raise CheckViolation(f"{field} must be an object")
    value = cast(dict[str, Any], evidence)
    required = {
        "schema_version",
        "repository",
        "comment_id",
        "node_id",
        "actor",
        "updated_at",
        "body",
        "body_sha256",
        "decision_digest",
    }
    if set(value) != required:
        raise CheckViolation(
            f"{field} fields differ: missing={sorted(required - set(value))}, "
            f"extra={sorted(set(value) - required)}"
        )
    if (
        value["schema_version"] != ATTESTATION_SCHEMA
        or value["repository"] != expected_repository
        or value["actor"] != expected_actor
        or value["decision_digest"] != expected_decision_digest
        or type(value["comment_id"]) is not int
        or value["comment_id"] < 1
        or not isinstance(value["node_id"], str)
        or not value["node_id"]
        or not isinstance(value["updated_at"], str)
        or not value["updated_at"]
        or not isinstance(value["body"], str)
        or not value["body"]
        or not isinstance(value["body_sha256"], str)
        or len(value["body_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in value["body_sha256"])
    ):
        raise CheckViolation(f"{field} identity or digest is invalid")
    if sha256_bytes(value["body"].encode("utf-8")) != value["body_sha256"]:
        raise CheckViolation(f"{field} body differs from its exact SHA-256")
    marker = f"{ATTESTATION_MARKER}: {expected_decision_digest}"
    if marker not in {line.strip() for line in value["body"].splitlines()}:
        raise CheckViolation(f"{field} body does not bind the exact decision digest")
    return value


class GitHubCommentEvidenceVerifier:
    """Resolve GitHub's authenticated comment actor and exact current object version."""

    def __init__(self, *, token: str | None = None, timeout_seconds: int = 20) -> None:
        self._token = token
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> GitHubCommentEvidenceVerifier:
        """Use an optional read-only GitHub token without exposing it in CLI arguments."""

        return cls(token=os.environ.get("GITHUB_TOKEN"))

    def verify(
        self,
        evidence: Mapping[str, Any],
        *,
        expected_actor: str,
        expected_repository: str,
        expected_decision_digest: str,
    ) -> Mapping[str, Any]:
        """Fetch and match a GitHub issue/PR comment; any unknown state fails closed."""

        local = validate_attestation_shape(
            evidence,
            field="GitHub decision attestation",
            expected_actor=expected_actor,
            expected_repository=expected_repository,
            expected_decision_digest=expected_decision_digest,
        )
        url = (
            f"https://api.github.com/repos/{expected_repository}/issues/comments/"
            f"{local['comment_id']}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "pcdb-governance-check",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                remote = json.loads(response.read())
        except (
            OSError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ) as exc:
            raise CheckViolation(
                f"cannot authenticate GitHub decision comment {local['comment_id']}: {exc}"
            ) from exc
        if not isinstance(remote, dict) or not isinstance(remote.get("user"), dict):
            raise CheckViolation("GitHub decision comment response is not an authenticated object")
        user = cast(dict[str, Any], remote["user"])
        body = remote.get("body")
        remote_actor = f"github:{user.get('login')}"
        if (
            remote.get("id") != local["comment_id"]
            or remote.get("node_id") != local["node_id"]
            or remote.get("updated_at") != local["updated_at"]
            or remote_actor.lower() != expected_actor.lower()
            or not isinstance(body, str)
            or body != local["body"]
            or sha256_bytes(body.encode("utf-8")) != local["body_sha256"]
        ):
            raise CheckViolation("GitHub decision actor, object version or exact body drifted")
        marker = f"{ATTESTATION_MARKER}: {expected_decision_digest}"
        if marker not in {line.strip() for line in body.splitlines()}:
            raise CheckViolation("GitHub decision comment does not bind the exact decision digest")
        return {
            "actor": remote_actor,
            "body_sha256": local["body_sha256"],
            "comment_id": local["comment_id"],
            "node_id": local["node_id"],
            "repository": expected_repository,
            "updated_at": local["updated_at"],
        }
