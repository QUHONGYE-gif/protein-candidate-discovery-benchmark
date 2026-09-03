"""Compact, immutable Governance v2 terminal milestone archiver."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.governance.attestation import DecisionEvidenceVerifier
from pcdb.governance.checks import (
    CheckEvidence,
    CheckReport,
    CheckViolation,
    build_report,
    capture_check,
    exact_file,
    safe_relative_path,
    sha256_bytes,
)
from pcdb.governance.contract import (
    TERMINAL_VERDICTS,
    contract_check_report,
    load_json,
)
from pcdb.storage.local import LocalArtifactStore

EXCLUDED_ATTEMPT_CLASSES = {
    "invalid_run",
    "debug_run",
    "aborted_attempt",
    "failed_implementation_run",
}
REQUIRED_ARCHIVE_ROLES = {
    "contract_manifest",
    "run_identity",
    "final_result",
    "important_log",
    "summary",
    "provenance",
    "gate_b_decision",
}
EVIDENCE_ARCHIVE_ROLES = REQUIRED_ARCHIVE_ROLES - {"contract_manifest", "gate_b_decision"}
_PREFIX = re.compile(
    r"^milestone-archives/v2/(?P<milestone>[A-Za-z0-9][A-Za-z0-9._-]*)/"
    r"(?P<archive>[a-z0-9][a-z0-9._-]*)/$"
)


class ImmutableArchiveStore(Protocol):
    """No-delete archive capability used by both local tests and COS."""

    def list_keys(self, prefix: str) -> tuple[str, ...]:
        """List the exact keys under one archive prefix."""
        ...

    def put_forbid_overwrite(self, key: str, payload: bytes) -> None:
        """Create one object and fail if it already exists."""
        ...

    def get(self, key: str) -> bytes:
        """Fresh-read one complete object."""
        ...


class LocalArchiveStore:
    """Filesystem-backed immutable object store for local verification."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._store = LocalArtifactStore(self._root)

    def list_keys(self, prefix: str) -> tuple[str, ...]:
        prefix_path = self._root.joinpath(*PurePosixPath(prefix).parts)
        if not prefix_path.exists():
            return ()
        return tuple(
            sorted(
                path.relative_to(self._root).as_posix()
                for path in prefix_path.rglob("*")
                if path.is_file()
            )
        )

    def put_forbid_overwrite(self, key: str, payload: bytes) -> None:
        self._store.put_bytes(key, payload)

    def get(self, key: str) -> bytes:
        return self._store.get_bytes(key)


class CosArchiveStore:
    """Governance v2 wrapper around the existing no-delete COS XML client."""

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        max_transfer_bytes: int,
    ) -> None:
        from pcdb.storage.cos import CosXmlObjectClient, EnvironmentCredentialsProvider

        provider = EnvironmentCredentialsProvider()
        self._bucket = bucket
        self._client = CosXmlObjectClient(
            region=region,
            credentials=provider.get,
            max_transfer_bytes=max_transfer_bytes,
        )

    def list_keys(self, prefix: str) -> tuple[str, ...]:
        return self._client.list_keys(self._bucket, prefix)

    def put_forbid_overwrite(self, key: str, payload: bytes) -> None:
        self._client.put_forbid_overwrite(self._bucket, key, payload)

    def get(self, key: str) -> bytes:
        return self._client.get(self._bucket, key)


@dataclass(frozen=True, slots=True)
class ArchiveObject:
    """One exact source object and its destination path."""

    role: str
    path: str
    archive_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveRequest:
    """Strict archive request independent of any storage implementation."""

    request_status: str
    archive_id: str
    milestone_id: str
    attempt_id: str
    terminal_class: str
    destination_prefix: str
    contract_manifest: ArchiveObject
    gate_b_decision: ArchiveObject
    source_inventory: tuple[ArchiveObject, ...]
    max_objects: int
    max_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveReceipt:
    """Terminal result returned by the archive entry point."""

    status: str
    archive_id: str
    milestone_id: str
    destination_prefix: str
    object_count: int
    total_bytes: int
    archive_manifest_sha256: str | None
    completion_marker_sha256: str | None
    check_report: CheckReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive_id": self.archive_id,
            "archive_manifest_sha256": self.archive_manifest_sha256,
            "check_report": self.check_report.as_dict(),
            "completion_marker_sha256": self.completion_marker_sha256,
            "destination_prefix": self.destination_prefix,
            "milestone_id": self.milestone_id,
            "object_count": self.object_count,
            "schema_version": "pcdb-milestone-archive-receipt-v1",
            "status": self.status,
            "total_bytes": self.total_bytes,
        }


class ArchiveAttemptRecorder:
    """Append-only server attempt ledger; each state is a separate object."""

    def __init__(self, root: Path) -> None:
        self._store = LocalArtifactStore(root)

    def record(
        self,
        request: ArchiveRequest,
        *,
        status: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        payload = canonical_json_bytes(
            {
                "archive_id": request.archive_id,
                "attempt_id": request.attempt_id,
                "destination_prefix": request.destination_prefix,
                "detail": {} if detail is None else dict(detail),
                "milestone_id": request.milestone_id,
                "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "request_sha256": request.sha256,
                "schema_version": "pcdb-archive-attempt-record-v1",
                "status": status,
                "terminal_class": request.terminal_class,
            }
        )
        key = f"{request.milestone_id}/archive-attempts/{request.attempt_id}.{status}.json"
        self._store.put_bytes(key, payload)


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckViolation(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _fields(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise CheckViolation(
            f"{field} fields differ: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _source(value: object, field: str, *, require_role: bool) -> ArchiveObject:
    item = _mapping(value, field)
    expected = {"path", "archive_path", "size_bytes", "sha256"}
    if require_role:
        expected.add("role")
    _fields(item, expected, field)
    path = item["path"]
    archive_path = item["archive_path"]
    size_bytes = item["size_bytes"]
    sha256 = item["sha256"]
    role = item.get("role", "")
    for candidate, name in ((path, "path"), (archive_path, "archive_path")):
        pure = PurePosixPath(str(candidate))
        if (
            not isinstance(candidate, str)
            or not candidate
            or pure.is_absolute()
            or ".." in pure.parts
        ):
            raise CheckViolation(f"{field}.{name} must be a safe relative POSIX path")
    if archive_path in {"_SUCCESS", "archive-manifest.json"}:
        raise CheckViolation(f"{field}.archive_path is reserved")
    if (
        type(size_bytes) is not int
        or size_bytes < 0
        or not isinstance(sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        or (require_role and role not in REQUIRED_ARCHIVE_ROLES)
    ):
        raise CheckViolation(f"{field} size, SHA-256 or role is invalid")
    return ArchiveObject(
        role=str(role),
        path=str(path),
        archive_path=str(archive_path),
        size_bytes=size_bytes,
        sha256=sha256,
    )


def load_archive_request(path: Path) -> ArchiveRequest:
    """Load and strictly validate one exact archive request."""

    loaded = load_json(path)
    value = loaded.value
    expected = {
        "schema_version",
        "request_status",
        "archive_id",
        "milestone_id",
        "attempt_id",
        "terminal_class",
        "destination_prefix",
        "contract_manifest",
        "gate_b_decision",
        "source_inventory",
        "limits",
    }
    _fields(value, expected, "archive request")
    terminal_class = value["terminal_class"]
    prefix = value["destination_prefix"]
    match = _PREFIX.fullmatch(str(prefix))
    if (
        value["schema_version"] != "pcdb-milestone-archive-request-v1"
        or value["request_status"] not in {"example", "final"}
        or terminal_class not in {"scientific_terminal", *EXCLUDED_ATTEMPT_CLASSES}
        or match is None
        or match.group("milestone") != value["milestone_id"]
        or match.group("archive") != value["archive_id"]
    ):
        raise CheckViolation("archive request version, class, identity or prefix is invalid")
    for field in ("archive_id", "attempt_id"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", str(value[field])):
            raise CheckViolation(f"archive request {field} is invalid")
    inventory_raw = value["source_inventory"]
    if not isinstance(inventory_raw, list):
        raise CheckViolation("archive source_inventory must be a list")
    inventory = tuple(
        _source(item, f"source_inventory[{index}]", require_role=True)
        for index, item in enumerate(inventory_raw)
    )
    roles = {item.role for item in inventory}
    role_counts = {role: sum(item.role == role for item in inventory) for role in roles}
    paths = [item.path for item in inventory]
    archive_paths = [item.archive_path for item in inventory]
    if roles != REQUIRED_ARCHIVE_ROLES:
        raise CheckViolation(
            f"archive roles are not exact: missing={sorted(REQUIRED_ARCHIVE_ROLES - roles)}"
        )
    singleton_roles = REQUIRED_ARCHIVE_ROLES - {"final_result", "important_log"}
    if any(role_counts[role] != 1 for role in singleton_roles):
        raise CheckViolation(
            "archive contract, identity, summary and decision roles must be unique"
        )
    if len(paths) != len(set(paths)) or len(archive_paths) != len(set(archive_paths)):
        raise CheckViolation("archive inventory source or destination paths are duplicated")
    contract = _source(value["contract_manifest"], "contract_manifest", require_role=False)
    gate_b = _source(value["gate_b_decision"], "gate_b_decision", require_role=False)
    role_map = {item.role: item for item in inventory}
    if contract != ArchiveObject(role="", **_object_without_role(role_map["contract_manifest"])):
        raise CheckViolation("contract_manifest does not match its source inventory entry")
    if gate_b != ArchiveObject(role="", **_object_without_role(role_map["gate_b_decision"])):
        raise CheckViolation("gate_b_decision does not match its source inventory entry")
    limits = _mapping(value["limits"], "archive limits")
    _fields(limits, {"max_objects", "max_bytes"}, "archive limits")
    max_objects = limits["max_objects"]
    max_bytes = limits["max_bytes"]
    if (
        type(max_objects) is not int
        or not 3 <= max_objects <= 1000
        or type(max_bytes) is not int
        or max_bytes < 1
    ):
        raise CheckViolation("archive object/byte limits are invalid")
    return ArchiveRequest(
        request_status=str(value["request_status"]),
        archive_id=str(value["archive_id"]),
        milestone_id=str(value["milestone_id"]),
        attempt_id=str(value["attempt_id"]),
        terminal_class=str(terminal_class),
        destination_prefix=str(prefix),
        contract_manifest=contract,
        gate_b_decision=gate_b,
        source_inventory=inventory,
        max_objects=max_objects,
        max_bytes=max_bytes,
        sha256=loaded.sha256,
    )


def _object_without_role(item: ArchiveObject) -> dict[str, Any]:
    return {
        "archive_path": item.archive_path,
        "path": item.path,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
    }


def archive_evidence_digest(inventory: tuple[ArchiveObject, ...]) -> str:
    """Bind Gate B to the exact non-decision evidence proposed for archive."""

    evidence = [
        {
            "archive_path": item.archive_path,
            "path": item.path,
            "role": item.role,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }
        for item in sorted(inventory, key=lambda item: (item.role, item.archive_path))
        if item.role in EVIDENCE_ARCHIVE_ROLES
    ]
    return sha256_bytes(canonical_json_bytes(evidence))


def _archive_manifest(request: ArchiveRequest, *, verdict: str) -> bytes:
    return canonical_json_bytes(
        {
            "archive_id": request.archive_id,
            "completion_marker": {
                "path": "_SUCCESS",
                "sha256": sha256_bytes(b""),
                "size_bytes": 0,
            },
            "evidence_digest": archive_evidence_digest(request.source_inventory),
            "gate_b_verdict": verdict,
            "milestone_id": request.milestone_id,
            "objects": [
                {
                    "path": item.archive_path,
                    "role": item.role,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in request.source_inventory
            ],
            "request_sha256": request.sha256,
            "retention": "no_automatic_deletion",
            "schema_version": "pcdb-milestone-archive-manifest-v1",
        }
    )


def archive_terminal_milestone(
    *,
    request_path: Path,
    source_root: Path,
    gate_b_path: Path,
    config_path: Path,
    store: ImmutableArchiveStore,
    recorder: ArchiveAttemptRecorder,
    evidence_verifier: DecisionEvidenceVerifier | None = None,
) -> ArchiveReceipt:
    """Archive one valid scientific terminal or record an excluded attempt without COS writes."""

    request = load_archive_request(request_path)
    if request.terminal_class in EXCLUDED_ATTEMPT_CLASSES:
        result = capture_check(
            name="archive-terminal-eligibility",
            category="schema_domain",
            blocks=("archive",),
            input_digests=(request.sha256,),
            operation=lambda: CheckEvidence(
                reason="attempt class is excluded from milestone archive by policy",
                evidence={"terminal_class": request.terminal_class, "cos_writes": 0},
            ),
        )
        report = build_report((result,))
        recorder.record(request, status="skipped", detail={"reason": result.reason})
        return ArchiveReceipt(
            status="skipped",
            archive_id=request.archive_id,
            milestone_id=request.milestone_id,
            destination_prefix=request.destination_prefix,
            object_count=0,
            total_bytes=0,
            archive_manifest_sha256=None,
            completion_marker_sha256=None,
            check_report=report,
        )
    if request.request_status != "final":
        recorder.record(
            request,
            status="failed",
            detail={"error": "example archive requests cannot publish"},
        )
        raise CheckViolation("example archive requests cannot publish")

    try:
        manifest_path = safe_relative_path(source_root, request.contract_manifest.path)
        manifest = load_json(manifest_path)
        gate_a_ref = _mapping(manifest.value.get("gate_a"), "contract manifest gate_a")
        gate_a_path = safe_relative_path(source_root, str(gate_a_ref.get("path", "")))
        run_identity = next(
            item for item in request.source_inventory if item.role == "run_identity"
        )
        expected_gate_b_path = safe_relative_path(source_root, request.gate_b_decision.path)
        if expected_gate_b_path.resolve() != gate_b_path.resolve():
            raise CheckViolation("archive Gate B path differs from its exact inventory entry")
        exact_file(
            gate_b_path,
            size_bytes=request.gate_b_decision.size_bytes,
            sha256=request.gate_b_decision.sha256,
        )
        contract_report = contract_check_report(
            manifest_path=manifest_path,
            gate_a_path=gate_a_path,
            gate_b_path=gate_b_path,
            run_identity_path=safe_relative_path(source_root, run_identity.path),
            config_path=config_path,
            source_root=source_root,
            evidence_verifier=evidence_verifier,
            require_final=True,
        )
        contract_report.require_pass()
        gate_b = load_json(gate_b_path)
        verdict = str(gate_b.value.get("verdict"))
        if verdict not in TERMINAL_VERDICTS:
            raise CheckViolation("archive requires an accept/reject/inconclusive Gate B verdict")
        if gate_b.value.get("evidence_digest") != archive_evidence_digest(request.source_inventory):
            raise CheckViolation(
                "Gate B evidence digest differs from the exact proposed archive inventory"
            )
    except Exception as exc:
        recorder.record(
            request,
            status="failed",
            detail={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise

    payloads: dict[str, bytes] = {}

    def inventory_check() -> CheckEvidence:
        total = 0
        for item in request.source_inventory:
            path = safe_relative_path(source_root, item.path)
            exact_file(path, size_bytes=item.size_bytes, sha256=item.sha256)
            payload = path.read_bytes()
            payloads[item.archive_path] = payload
            total += len(payload)
        manifest_bytes = _archive_manifest(request, verdict=verdict)
        total += len(manifest_bytes)
        object_count = len(payloads) + 2
        if object_count > request.max_objects or total > request.max_bytes:
            raise CheckViolation(
                f"archive would exceed cap: objects={object_count}/{request.max_objects}, "
                f"bytes={total}/{request.max_bytes}"
            )
        payloads["archive-manifest.json"] = manifest_bytes
        return CheckEvidence(
            reason="source inventory is exact, compact and within object/byte caps",
            evidence={"object_count_with_marker": object_count, "total_bytes": total},
        )

    def prefix_check() -> CheckEvidence:
        match = _PREFIX.fullmatch(request.destination_prefix)
        if match is None or store.list_keys(request.destination_prefix):
            raise CheckViolation("archive prefix is invalid or not empty")
        return CheckEvidence(
            reason="new Governance v2 archive prefix is valid and empty",
            evidence={"prefix": request.destination_prefix},
        )

    preflight = build_report(
        (
            capture_check(
                name="archive-terminal-eligibility",
                category="schema_domain",
                blocks=("archive",),
                input_digests=(request.sha256, gate_b.sha256),
                operation=lambda: CheckEvidence(
                    reason="valid Gate B scientific terminal is eligible for mandatory archive",
                    evidence={"verdict": verdict},
                ),
            ),
            capture_check(
                name="archive-source-inventory",
                category="inventory_cross_links",
                blocks=("archive",),
                input_digests=(request.sha256,),
                operation=inventory_check,
            ),
            capture_check(
                name="archive-prefix-safety",
                category="prefix_storage_safety",
                blocks=("archive_first_write",),
                input_digests=(request.sha256,),
                operation=prefix_check,
            ),
        )
    )
    if preflight.overall_status != "pass":
        recorder.record(
            request,
            status="failed",
            detail={"preflight_report": preflight.report_id},
        )
    preflight.require_pass()
    recorder.record(request, status="started", detail={"preflight_report": preflight.report_id})

    expected_keys: set[str] = set()
    try:
        for relative_path, payload in sorted(payloads.items()):
            key = request.destination_prefix + relative_path
            store.put_forbid_overwrite(key, payload)
            readback = store.get(key)
            if readback != payload or sha256_bytes(readback) != sha256_bytes(payload):
                raise CheckViolation(f"fresh archive readback mismatch: {key}")
            expected_keys.add(key)
        if set(store.list_keys(request.destination_prefix)) != expected_keys:
            raise CheckViolation("pre-marker archive inventory differs from exact plan")

        marker_key = request.destination_prefix + "_SUCCESS"
        store.put_forbid_overwrite(marker_key, b"")
        marker_payload = store.get(marker_key)
        expected_keys.add(marker_key)
        if (
            marker_payload != b""
            or set(store.list_keys(request.destination_prefix)) != expected_keys
        ):
            raise CheckViolation("completion marker or final archive inventory readback failed")
    except Exception as exc:
        recorder.record(
            request,
            status="failed",
            detail={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise

    archive_manifest = payloads["archive-manifest.json"]
    final_archive_checks = (
        capture_check(
            name="archive-fresh-readback",
            category="archive_readback",
            blocks=("archive_immutable", "milestone_closeout"),
            input_digests=(request.sha256, sha256_bytes(archive_manifest)),
            operation=lambda: CheckEvidence(
                reason="every archive object matches fresh readback and exact final inventory",
                evidence={"keys": sorted(expected_keys)},
            ),
        ),
        capture_check(
            name="archive-completion-marker",
            category="completion_marker",
            blocks=("archive_completion", "milestone_closeout"),
            input_digests=(sha256_bytes(b""),),
            operation=lambda: CheckEvidence(
                reason="empty _SUCCESS was written last and fresh-read successfully",
                evidence={"key": request.destination_prefix + "_SUCCESS"},
            ),
        ),
    )
    final_report = build_report((*contract_report.checks, *preflight.checks, *final_archive_checks))
    receipt = ArchiveReceipt(
        status="archived",
        archive_id=request.archive_id,
        milestone_id=request.milestone_id,
        destination_prefix=request.destination_prefix,
        object_count=len(expected_keys),
        total_bytes=sum(len(item) for item in payloads.values()),
        archive_manifest_sha256=sha256_bytes(archive_manifest),
        completion_marker_sha256=sha256_bytes(b""),
        check_report=final_report,
    )
    recorder.record(
        request,
        status="succeeded",
        detail={
            "archive_manifest_sha256": receipt.archive_manifest_sha256,
            "check_report": final_report.report_id,
            "object_count": receipt.object_count,
        },
    )
    return receipt
