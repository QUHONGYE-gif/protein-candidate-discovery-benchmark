"""Common fail-closed Check result contract for Governance v2."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pcdb import __version__
from pcdb.contracts.experiment import canonical_json_bytes

CheckStatus = Literal["pass", "fail", "error"]
CheckCategory = Literal[
    "schema_domain",
    "authenticated_human_evidence",
    "hash_exact_bytes",
    "source_environment_identity",
    "prefix_storage_safety",
    "resource_cost_cap",
    "hidden_label_leakage",
    "inventory_cross_links",
    "completion_marker",
    "ci",
    "archive_readback",
]


class CheckViolation(ValueError):
    """An invariant is known not to hold."""


@dataclass(frozen=True, slots=True)
class CheckEvidence:
    """Successful Check reason and optional evidence fields."""

    reason: str
    evidence: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One versioned, machine-readable invariant result."""

    name: str
    version: str
    category: CheckCategory
    status: CheckStatus
    reason: str
    input_digests: tuple[str, ...]
    blocks: tuple[str, ...]
    evidence: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON representation, omitting absent evidence."""

        value = asdict(self)
        value["input_digests"] = list(self.input_digests)
        value["blocks"] = list(self.blocks)
        if self.evidence is None:
            del value["evidence"]
        return value


@dataclass(frozen=True, slots=True)
class CheckReport:
    """A collection of Checks; it never represents human approval."""

    report_id: str
    checked_at: str
    overall_status: CheckStatus
    checks: tuple[CheckResult, ...]
    git_commit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the persisted v1 report contract."""

        tool: dict[str, str] = {"name": "pcdb-governance-check", "version": __version__}
        if self.git_commit is not None:
            tool["git_commit"] = self.git_commit
        return {
            "approval_events_emitted": 0,
            "checked_at": self.checked_at,
            "checks": [item.as_dict() for item in self.checks],
            "overall_status": self.overall_status,
            "report_id": self.report_id,
            "schema_version": "pcdb-governance-check-report-v1",
            "tool": tool,
        }

    @property
    def exit_code(self) -> int:
        """Return zero only when every invariant is known to pass."""

        return 0 if self.overall_status == "pass" else 2

    def require_pass(self) -> None:
        """Fail closed for callers that guard a side effect."""

        if self.overall_status != "pass":
            failed = ", ".join(
                f"{item.name}={item.status}" for item in self.checks if item.status != "pass"
            )
            raise CheckViolation(f"Governance v2 Checks did not pass: {failed}")


def sha256_bytes(payload: bytes) -> str:
    """Return the lowercase SHA-256 for exact bytes."""

    return hashlib.sha256(payload).hexdigest()


def exact_file(path: Path, *, size_bytes: int, sha256: str) -> CheckEvidence:
    """Verify one path against its exact-byte identity."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CheckViolation(f"cannot read exact-byte input {path}: {exc}") from exc
    actual_sha256 = sha256_bytes(payload)
    if len(payload) != size_bytes or actual_sha256 != sha256:
        raise CheckViolation(
            f"exact-byte mismatch for {path}: expected {size_bytes}/{sha256}, "
            f"got {len(payload)}/{actual_sha256}"
        )
    return CheckEvidence(
        reason="exact bytes match declared size and SHA-256",
        evidence={"path": str(path), "sha256": actual_sha256, "size_bytes": len(payload)},
    )


def safe_relative_path(root: Path, value: str) -> Path:
    """Resolve a manifest path without allowing absolute paths or traversal."""

    candidate = Path(value)
    if not value or candidate.is_absolute() or ".." in candidate.parts:
        raise CheckViolation(f"unsafe relative path: {value!r}")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise CheckViolation(f"path escapes source root: {value!r}")
    return resolved


def capture_check(
    *,
    name: str,
    category: CheckCategory,
    blocks: Sequence[str],
    input_digests: Sequence[str],
    operation: Callable[[], CheckEvidence],
) -> CheckResult:
    """Execute one invariant and normalize pass/fail/error semantics."""

    try:
        result = operation()
    except CheckViolation as exc:
        return CheckResult(
            name=name,
            version="1",
            category=category,
            status="fail",
            reason=str(exc),
            input_digests=tuple(sorted(set(input_digests))),
            blocks=tuple(blocks),
        )
    except Exception as exc:  # noqa: BLE001 - unknown means error and must fail closed
        return CheckResult(
            name=name,
            version="1",
            category=category,
            status="error",
            reason=f"{type(exc).__name__}: {exc}",
            input_digests=tuple(sorted(set(input_digests))),
            blocks=tuple(blocks),
        )
    return CheckResult(
        name=name,
        version="1",
        category=category,
        status="pass",
        reason=result.reason,
        input_digests=tuple(sorted(set(input_digests))),
        blocks=tuple(blocks),
        evidence=result.evidence,
    )


def build_report(
    checks: Sequence[CheckResult],
    *,
    checked_at: str | None = None,
    git_commit: str | None = None,
) -> CheckReport:
    """Build a deterministic-identity report from one completed Check set."""

    if not checks:
        raise ValueError("a Check report must contain at least one result")
    timestamp = checked_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    statuses = {item.status for item in checks}
    overall: CheckStatus
    if "error" in statuses:
        overall = "error"
    elif "fail" in statuses:
        overall = "fail"
    else:
        overall = "pass"
    identity = {
        "checked_at": timestamp,
        "checks": [item.as_dict() for item in checks],
        "git_commit": git_commit,
        "overall_status": overall,
        "tool_version": __version__,
    }
    return CheckReport(
        report_id=sha256_bytes(canonical_json_bytes(identity)),
        checked_at=timestamp,
        overall_status=overall,
        checks=tuple(checks),
        git_commit=git_commit,
    )
