"""Milestone Contract Manifest and Gate decision validation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import canonical_json_bytes
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

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAJOR_BOUNDARIES = {
    "held_out_boundary",
    "primary_endpoint",
    "dataset_inclusion",
    "cost_semantics",
}
TERMINAL_VERDICTS = {"accept", "reject", "inconclusive"}
GOVERNANCE_RFC_SHA256 = "e9f5fbdadfc3f82fe1011fbc7e1e55ecc4d4c6a0c86db15473da3cf76b7a05d1"


@dataclass(frozen=True, slots=True)
class LoadedJson:
    """One strict JSON object bound to its exact input bytes."""

    value: Mapping[str, Any]
    payload: bytes
    sha256: str


def _reject_constant(value: str) -> None:
    raise CheckViolation(f"non-finite JSON constant is forbidden: {value}")


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CheckViolation(f"duplicate JSON key is forbidden: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> LoadedJson:
    """Load one strict top-level JSON object without normalizing its identity."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CheckViolation(f"cannot read JSON artifact {path}: {exc}") from exc
    if not payload or payload.startswith(b"\xef\xbb\xbf"):
        raise CheckViolation(f"JSON artifact is empty or has a UTF-8 BOM: {path}")
    try:
        parsed = json.loads(
            payload,
            object_pairs_hook=_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckViolation(f"JSON artifact is invalid: {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CheckViolation(f"JSON artifact must contain a top-level object: {path}")
    return LoadedJson(value=parsed, payload=payload, sha256=sha256_bytes(payload))


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckViolation(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _exact_fields(
    value: Mapping[str, Any], required: set[str], field: str, *, optional: set[str] | None = None
) -> None:
    optional = set() if optional is None else optional
    actual = set(value)
    if not required <= actual or actual - required - optional:
        raise CheckViolation(
            f"{field} fields differ: missing={sorted(required - actual)}, "
            f"extra={sorted(actual - required - optional)}"
        )


def _strings(value: object, field: str, *, count: int | None = None) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
        or (count is not None and len(value) != count)
    ):
        suffix = "" if count is None else f" with exactly {count} entries"
        raise CheckViolation(f"{field} must contain unique non-empty strings{suffix}")
    return tuple(value)


def _string_list(value: object, field: str, *, allow_empty: bool) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        qualifier = "possibly empty" if allow_empty else "non-empty"
        raise CheckViolation(f"{field} must be a {qualifier} unique string list")
    return tuple(value)


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CheckViolation(f"{field} must be a non-empty string")
    return value


def _date_time(value: object, field: str) -> str:
    text = _nonempty(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CheckViolation(f"{field} must be an RFC 3339 date-time") from exc
    if parsed.tzinfo is None:
        raise CheckViolation(f"{field} must include a timezone")
    return text


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise CheckViolation(f"{field} must be a lowercase SHA-256 digest")
    return value


def validate_governance_config(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the active authority and server/archive deployment contract."""

    _exact_fields(
        value,
        {"schema_version", "governance_version", "scientific_authorities", "server", "archive"},
        "governance config",
    )
    if value["schema_version"] != "pcdb-governance-config-v1" or value["governance_version"] != "2":
        raise CheckViolation("governance config version is not v2/v1")
    raw_authorities = value["scientific_authorities"]
    if not isinstance(raw_authorities, list) or len(raw_authorities) != 2:
        raise CheckViolation("governance config must name exactly two scientific authorities")
    handles: list[str] = []
    for index, raw in enumerate(raw_authorities):
        item = _mapping(raw, f"scientific_authorities[{index}]")
        _exact_fields(item, {"handle", "display_name"}, f"scientific_authorities[{index}]")
        handle = item["handle"]
        if (
            not isinstance(handle, str)
            or not re.fullmatch(r"(?:github|git):[A-Za-z0-9._-]+", handle)
            or not isinstance(item["display_name"], str)
            or not item["display_name"]
        ):
            raise CheckViolation("scientific authority identity is invalid")
        handles.append(handle)
    if len(set(handles)) != 2:
        raise CheckViolation("scientific authority handles must be distinct")

    server = _mapping(value["server"], "server")
    _exact_fields(
        server,
        {
            "root",
            "subdirectories",
            "project_group",
            "service_accounts",
            "writers",
            "capacity",
            "backup",
        },
        "server",
    )
    required_subdirectories = {
        "data/raw",
        "data/processed",
        "data/embeddings",
        "data/splits",
        "runs",
        "releases",
        "scratch",
    }
    subdirectories = set(_strings(server["subdirectories"], "server.subdirectories"))
    services = _strings(server["service_accounts"], "server.service_accounts")
    writers = set(_strings(server["writers"], "server.writers"))
    if (
        server["root"] != "/srv/pcdb"
        or server["project_group"] != "pcdb"
        or subdirectories != required_subdirectories
        or writers != {*handles, *(f"service:{item}" for item in services)}
    ):
        raise CheckViolation("server namespace, group or exact writer set drifted")
    capacity = _mapping(server["capacity"], "server.capacity")
    if capacity != {"hard_cap_fraction": 0.7, "target_fraction": 0.5}:
        raise CheckViolation("capacity target/hard-cap must remain 50%/70%")
    backup = _mapping(server["backup"], "server.backup")
    if backup != {
        "high_cost_trajectories_rpo_hours": 24,
        "public_raw": "source_url_and_sha256_redownloadable",
        "recomputable_processed": "recompute_allowed",
        "release_candidates": "daily_snapshot_or_rsync",
    }:
        raise CheckViolation("backup/RPO policy drifted")
    archive = _mapping(value["archive"], "archive")
    if archive != {
        "allowed_terminal_verdicts": ["accept", "reject", "inconclusive"],
        "excluded_attempt_classes": [
            "invalid_run",
            "debug_run",
            "aborted_attempt",
            "failed_implementation_run",
        ],
        "prefix": "milestone-archives/v2",
        "retention": "no_automatic_deletion",
    }:
        raise CheckViolation("archive eligibility, prefix or retention policy drifted")
    return tuple(handles)


def validate_gate_a(
    value: Mapping[str, Any],
    *,
    authorities: Sequence[str],
    contract_id: str,
    contract_version: int,
    contract_digest: str,
    major_boundaries: set[str],
    require_final: bool,
) -> None:
    """Validate human Gate A roles and all explicit two-person boundaries."""

    required = {
        "schema_version",
        "record_status",
        "decision_id",
        "contract_id",
        "contract_version",
        "contract_digest",
        "formulation",
        "proposers",
        "approvals",
        "verdict",
        "decided_at",
        "rationale",
    }
    _exact_fields(value, required, "Gate A decision")
    if (
        value["schema_version"] != "pcdb-gate-a-decision-v1"
        or value["contract_id"] != contract_id
        or value["contract_version"] != contract_version
        or value["contract_digest"] != contract_digest
        or value["verdict"] != "authorized"
        or value["record_status"] not in {"example", "final"}
        or (require_final and value["record_status"] != "final")
    ):
        raise CheckViolation("Gate A version, binding, status or authorized verdict is invalid")
    if not IDENTIFIER_PATTERN.fullmatch(_nonempty(value["decision_id"], "Gate A decision_id")):
        raise CheckViolation("Gate A decision_id is invalid")
    _date_time(value["decided_at"], "Gate A decided_at")
    _nonempty(value["rationale"], "Gate A rationale")
    authority_set = set(authorities)
    proposers = set(_strings(value["proposers"], "Gate A proposers"))
    if not proposers <= authority_set:
        raise CheckViolation("Gate A proposer is not a configured scientific authority")
    approvals = value["approvals"]
    if not isinstance(approvals, list) or not approvals:
        raise CheckViolation("Gate A must contain human approval records")
    approval_authorities: set[str] = set()
    boundary_by_authority: dict[str, set[str]] = {}
    for index, raw in enumerate(approvals):
        item = _mapping(raw, f"Gate A approvals[{index}]")
        _exact_fields(
            item,
            {"authority", "verdict", "decided_at", "explicit_boundaries"},
            f"Gate A approvals[{index}]",
        )
        authority = item["authority"]
        _date_time(item["decided_at"], f"Gate A approvals[{index}].decided_at")
        boundaries = set(
            _string_list(
                item["explicit_boundaries"],
                f"Gate A approvals[{index}].explicit_boundaries",
                allow_empty=True,
            )
        )
        if authority not in authority_set or item["verdict"] != "authorized":
            raise CheckViolation("Gate A approval authority or verdict is invalid")
        if not boundaries <= MAJOR_BOUNDARIES:
            raise CheckViolation("Gate A explicit boundary is unknown")
        if authority in approval_authorities:
            raise CheckViolation("Gate A authority must not have duplicate approval records")
        approval_authorities.add(authority)
        boundary_by_authority[str(authority)] = boundaries
    formulation = value["formulation"]
    if formulation == "proposed_and_approved":
        if len(proposers) != 1 or not any(item not in proposers for item in approval_authorities):
            raise CheckViolation("Gate A requires one proposer and another approving collaborator")
    elif formulation == "jointly_approved":
        if proposers != authority_set or approval_authorities != authority_set:
            raise CheckViolation("jointly approved Gate A requires both authorities")
    else:
        raise CheckViolation("Gate A formulation is invalid")
    if major_boundaries and any(
        not major_boundaries <= boundary_by_authority.get(authority, set())
        for authority in authority_set
    ):
        raise CheckViolation("major Gate A boundaries require both authorities' explicit approval")


def validate_gate_b(
    value: Mapping[str, Any],
    *,
    authorities: Sequence[str],
    contract_id: str,
    contract_version: int,
    contract_digest: str,
    major_boundaries: set[str],
    require_final: bool,
) -> str:
    """Validate Gate B scientific review without inferring it from Checks."""

    required = {
        "schema_version",
        "record_status",
        "decision_id",
        "contract_id",
        "contract_version",
        "contract_digest",
        "evidence_digest",
        "reviewers",
        "explicit_boundary_approvals",
        "verdict",
        "decided_at",
        "rationale",
        "claim_boundary",
    }
    _exact_fields(value, required, "Gate B decision")
    verdict = value["verdict"]
    if (
        value["schema_version"] != "pcdb-gate-b-decision-v1"
        or value["contract_id"] != contract_id
        or value["contract_version"] != contract_version
        or value["contract_digest"] != contract_digest
        or verdict not in TERMINAL_VERDICTS
        or not SHA256_PATTERN.fullmatch(str(value["evidence_digest"]))
        or value["record_status"] not in {"example", "final"}
        or (require_final and value["record_status"] != "final")
    ):
        raise CheckViolation("Gate B version, binding, status, evidence or verdict is invalid")
    if not IDENTIFIER_PATTERN.fullmatch(_nonempty(value["decision_id"], "Gate B decision_id")):
        raise CheckViolation("Gate B decision_id is invalid")
    _date_time(value["decided_at"], "Gate B decided_at")
    _nonempty(value["rationale"], "Gate B rationale")
    _nonempty(value["claim_boundary"], "Gate B claim_boundary")
    authority_set = set(authorities)
    reviewers = value["reviewers"]
    if not isinstance(reviewers, list) or not reviewers:
        raise CheckViolation("Gate B requires at least one human reviewer")
    reviewer_authorities: set[str] = set()
    for index, raw in enumerate(reviewers):
        item = _mapping(raw, f"Gate B reviewers[{index}]")
        _exact_fields(item, {"authority", "relation"}, f"Gate B reviewers[{index}]")
        if item["authority"] not in authority_set or item["relation"] not in {
            "not_summary_preparer",
            "summary_preparer",
            "internal_non_independent_review",
        }:
            raise CheckViolation("Gate B reviewer identity or relation is invalid")
        reviewer_authorities.add(str(item["authority"]))
    if len(reviewer_authorities) != len(reviewers):
        raise CheckViolation("Gate B reviewer authorities must be unique")
    approvals = value["explicit_boundary_approvals"]
    if not isinstance(approvals, list):
        raise CheckViolation("Gate B explicit boundary approvals must be a list")
    boundary_by_authority: dict[str, set[str]] = {}
    for index, raw in enumerate(approvals):
        item = _mapping(raw, f"Gate B boundary approvals[{index}]")
        _exact_fields(item, {"authority", "boundaries"}, "Gate B boundary approval")
        authority = item["authority"]
        boundaries = set(
            _string_list(
                item["boundaries"],
                f"Gate B boundary approvals[{index}].boundaries",
                allow_empty=True,
            )
        )
        if authority not in authority_set or not boundaries <= MAJOR_BOUNDARIES:
            raise CheckViolation("Gate B explicit boundary approval is invalid")
        if authority in boundary_by_authority:
            raise CheckViolation("Gate B authority has duplicate boundary approvals")
        boundary_by_authority[str(authority)] = boundaries
    if major_boundaries and any(
        not major_boundaries <= boundary_by_authority.get(authority, set())
        for authority in authority_set
    ):
        raise CheckViolation("major Gate B boundaries require both authorities' explicit approval")
    return str(verdict)


def validate_run_identity(
    value: Mapping[str, Any],
    *,
    contract_digest: str,
    runtime_git_commit: str | None = None,
    runtime_git_tree: str | None = None,
    runtime_environment_digest: str | None = None,
) -> None:
    """Validate one M5+ server-first, zero-COS-write run identity and its caps."""

    required = {
        "schema_version",
        "run_id",
        "attempt_id",
        "contract_digest",
        "input_manifest_digest",
        "git_commit",
        "git_tree",
        "source_clean",
        "environment_digest",
        "attempt_root",
        "cos_write_count",
        "resource_caps",
        "observed_usage",
    }
    _exact_fields(value, required, "run identity")
    if (
        value["schema_version"] != "pcdb-governance-run-identity-v1"
        or value["contract_digest"] != contract_digest
        or not SHA256_PATTERN.fullmatch(str(value["run_id"]))
        or not SHA256_PATTERN.fullmatch(str(value["input_manifest_digest"]))
        or not GIT_PATTERN.fullmatch(str(value["git_commit"]))
        or not GIT_PATTERN.fullmatch(str(value["git_tree"]))
        or value["source_clean"] is not True
        or not SHA256_PATTERN.fullmatch(str(value["environment_digest"]))
        or not re.fullmatch(
            r"/srv/pcdb/runs/[A-Za-z0-9._-]+/[a-z0-9][a-z0-9._-]*",
            str(value["attempt_root"]),
        )
        or value["cos_write_count"] != 0
    ):
        raise CheckViolation(
            "run identity, server-first path, clean source or COS-zero invariant failed"
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", str(value["attempt_id"])):
        raise CheckViolation("run identity attempt_id is invalid")
    expected_runtime = (
        ("git_commit", runtime_git_commit),
        ("git_tree", runtime_git_tree),
        ("environment_digest", runtime_environment_digest),
    )
    for field, expected in expected_runtime:
        if expected is not None and value[field] != expected:
            raise CheckViolation(f"runtime {field} does not match the Run-pinned identity")


def contract_check_report(
    *,
    manifest_path: Path,
    gate_a_path: Path,
    config_path: Path,
    source_root: Path,
    gate_b_path: Path | None = None,
    run_identity_path: Path | None = None,
    runtime_git_commit: str | None = None,
    runtime_git_tree: str | None = None,
    runtime_environment_digest: str | None = None,
    require_final: bool = True,
) -> CheckReport:
    """Run the contract/hash/Gate/leakage/cross-link/identity Check suite."""

    loaded: dict[str, LoadedJson] = {}

    def schema_domain() -> CheckEvidence:
        loaded["config"] = load_json(config_path)
        loaded["manifest"] = load_json(manifest_path)
        loaded["gate_a"] = load_json(gate_a_path)
        if gate_b_path is not None:
            loaded["gate_b"] = load_json(gate_b_path)
        if run_identity_path is not None:
            loaded["run_identity"] = load_json(run_identity_path)
        authorities = validate_governance_config(loaded["config"].value)
        manifest = loaded["manifest"].value
        required = {
            "schema_version",
            "manifest_status",
            "manifest_revision",
            "milestone_id",
            "contract_id",
            "contract_version",
            "contract_digest",
            "scientific_authorities",
            "scientific_contract",
            "major_boundaries",
            "gate_a",
            "run_policy",
        }
        _exact_fields(
            manifest,
            required,
            "contract manifest",
            optional={"gate_b", "input_artifacts"},
        )
        if (
            manifest["schema_version"] != "pcdb-milestone-contract-manifest-v1"
            or manifest["manifest_status"] not in {"example", "authorized", "terminal"}
            or type(manifest["manifest_revision"]) is not int
            or manifest["manifest_revision"] < 1
            or not IDENTIFIER_PATTERN.fullmatch(str(manifest["milestone_id"]))
            or not IDENTIFIER_PATTERN.fullmatch(str(manifest["contract_id"]))
            or type(manifest["contract_version"]) is not int
            or manifest["contract_version"] < 1
        ):
            raise CheckViolation("contract manifest identity or version is invalid")
        if require_final and manifest["manifest_status"] == "example":
            raise CheckViolation("example contract manifests cannot guard production actions")
        if gate_b_path is not None and manifest["manifest_status"] not in {"example", "terminal"}:
            raise CheckViolation("a Gate B decision requires a terminal manifest revision")
        manifest_authorities = _strings(
            manifest["scientific_authorities"], "manifest scientific authorities", count=2
        )
        if tuple(manifest_authorities) != tuple(authorities):
            raise CheckViolation("manifest scientific authorities differ from active governance")
        scientific_contract = _mapping(manifest["scientific_contract"], "scientific contract")
        scientific_fields = {
            "research_question",
            "dataset_split_semantics",
            "candidate_high_value_definition",
            "primary_metrics",
            "budget_cost_model",
            "comparison_set",
            "held_out_boundary",
            "statistical_rule",
            "known_risks",
            "allowed_deviations",
        }
        _exact_fields(scientific_contract, scientific_fields, "scientific contract")
        for field in scientific_fields - {
            "primary_metrics",
            "comparison_set",
            "known_risks",
            "allowed_deviations",
        }:
            if not isinstance(scientific_contract[field], str) or not scientific_contract[field]:
                raise CheckViolation(f"scientific contract field is empty: {field}")
        _strings(scientific_contract["primary_metrics"], "primary metrics")
        _strings(scientific_contract["comparison_set"], "comparison set")
        for field in ("known_risks", "allowed_deviations"):
            _string_list(
                scientific_contract[field],
                f"scientific contract {field}",
                allow_empty=True,
            )
        _sha256(manifest["contract_digest"], "contract digest")
        boundaries = set(
            _string_list(
                manifest["major_boundaries"],
                "major boundaries",
                allow_empty=True,
            )
        )
        if not boundaries <= MAJOR_BOUNDARIES:
            raise CheckViolation("contract manifest contains an unknown major boundary")
        policy = _mapping(manifest["run_policy"], "run policy")
        if policy != {
            "attempt_semantics": "append_only",
            "completion_marker": "_SUCCESS_last",
            "ordinary_run_cos_writes": 0,
            "storage_mode": "server_first",
        }:
            raise CheckViolation(
                "M5+ run policy must be server-first with zero ordinary COS writes"
            )
        return CheckEvidence(
            reason="governance config and milestone contract structure are valid",
            evidence={"authorities": list(authorities), "milestone_id": manifest["milestone_id"]},
        )

    schema_result = capture_check(
        name="governance-schema-domain",
        category="schema_domain",
        blocks=("contract_acceptance", "formal_execution", "archive"),
        input_digests=(),
        operation=schema_domain,
    )
    if schema_result.status != "pass":
        return build_report((schema_result,), git_commit=runtime_git_commit)

    config = loaded["config"]
    manifest = loaded["manifest"]
    gate_a = loaded["gate_a"]
    authorities = validate_governance_config(config.value)
    contract_digest = str(manifest.value["contract_digest"])
    contract_id = str(manifest.value["contract_id"])
    contract_version = int(manifest.value["contract_version"])
    boundaries = set(cast(list[str], manifest.value["major_boundaries"]))

    def hashes() -> CheckEvidence:
        actual_contract_digest = sha256_bytes(
            canonical_json_bytes(manifest.value["scientific_contract"])
        )
        if actual_contract_digest != contract_digest:
            raise CheckViolation(
                "contract digest does not match canonical scientific_contract bytes"
            )
        gate_a_ref = _mapping(manifest.value["gate_a"], "manifest gate_a")
        _exact_fields(gate_a_ref, {"path", "sha256"}, "manifest gate_a")
        expected_gate_a = safe_relative_path(source_root, str(gate_a_ref["path"]))
        if expected_gate_a.resolve() != gate_a_path.resolve():
            raise CheckViolation("Gate A CLI path differs from the manifest cross-link")
        exact_file(
            gate_a_path,
            size_bytes=len(gate_a.payload),
            sha256=_sha256(gate_a_ref["sha256"], "Gate A reference SHA-256"),
        )
        for index, raw in enumerate(cast(list[object], manifest.value.get("input_artifacts", []))):
            artifact = _mapping(raw, f"input_artifacts[{index}]")
            _exact_fields(
                artifact,
                {"role", "path", "size_bytes", "sha256", "visibility", "consumers"},
                f"input_artifacts[{index}]",
            )
            if (
                not isinstance(artifact["role"], str)
                or not artifact["role"]
                or artifact["visibility"]
                not in {"observable", "revealed_evidence", "hidden_truth", "held_out"}
                or type(artifact["size_bytes"]) is not int
                or artifact["size_bytes"] < 0
            ):
                raise CheckViolation(f"input_artifacts[{index}] fields are invalid")
            consumers = _string_list(
                artifact["consumers"],
                f"input_artifacts[{index}].consumers",
                allow_empty=False,
            )
            if not set(consumers) <= {
                "runner",
                "surrogate_model",
                "acquisition_strategy",
                "hidden_oracle",
                "evaluator",
                "reporting",
            }:
                raise CheckViolation(f"input_artifacts[{index}] consumer is invalid")
            exact_file(
                safe_relative_path(source_root, str(artifact["path"])),
                size_bytes=artifact["size_bytes"],
                sha256=_sha256(artifact["sha256"], "input artifact SHA-256"),
            )
        return CheckEvidence(
            reason="contract, decision and input exact-byte identities match",
            evidence={"contract_digest": actual_contract_digest},
        )

    def gate_decisions() -> CheckEvidence:
        validate_gate_a(
            gate_a.value,
            authorities=authorities,
            contract_id=contract_id,
            contract_version=contract_version,
            contract_digest=contract_digest,
            major_boundaries=boundaries,
            require_final=require_final,
        )
        if gate_b_path is not None:
            gate_b = loaded["gate_b"]
            gate_b_ref = _mapping(manifest.value.get("gate_b"), "manifest gate_b")
            _exact_fields(gate_b_ref, {"path", "sha256", "verdict"}, "manifest gate_b")
            if safe_relative_path(source_root, str(gate_b_ref["path"])).resolve() != (
                gate_b_path.resolve()
            ):
                raise CheckViolation("Gate B CLI path differs from the manifest cross-link")
            exact_file(
                gate_b_path,
                size_bytes=len(gate_b.payload),
                sha256=_sha256(gate_b_ref["sha256"], "Gate B reference SHA-256"),
            )
            verdict = validate_gate_b(
                gate_b.value,
                authorities=authorities,
                contract_id=contract_id,
                contract_version=contract_version,
                contract_digest=contract_digest,
                major_boundaries=boundaries,
                require_final=require_final,
            )
            if gate_b_ref["verdict"] != verdict:
                raise CheckViolation("manifest and Gate B verdicts differ")
        elif manifest.value["manifest_status"] == "terminal" or "gate_b" in manifest.value:
            raise CheckViolation("terminal contract manifest requires its exact Gate B decision")
        return CheckEvidence(
            reason="Gate A/B human decision roles and bindings satisfy Governance v2",
            evidence={"gate_a_decision_id": gate_a.value["decision_id"]},
        )

    def leakage() -> CheckEvidence:
        forbidden_consumers = {"surrogate_model", "acquisition_strategy", "reporting"}
        inspected = 0
        for raw in cast(list[object], manifest.value.get("input_artifacts", [])):
            artifact = _mapping(raw, "input artifact")
            visibility = artifact["visibility"]
            consumers = set(_strings(artifact["consumers"], "input artifact consumers"))
            if visibility in {"hidden_truth", "held_out"} and consumers & forbidden_consumers:
                raise CheckViolation(
                    f"{visibility} artifact {artifact['path']} reaches a prohibited consumer"
                )
            if visibility in {"hidden_truth", "held_out"} and not consumers <= {
                "hidden_oracle",
                "evaluator",
            }:
                raise CheckViolation(f"{visibility} artifact capability boundary is invalid")
            inspected += 1
        return CheckEvidence(
            reason="hidden/held-out artifacts remain inside oracle/evaluator capabilities",
            evidence={"artifacts_inspected": inspected},
        )

    def cross_links() -> CheckEvidence:
        input_artifacts = [
            _mapping(item, "input artifact")
            for item in cast(list[object], manifest.value.get("input_artifacts", []))
        ]
        paths = [str(item["path"]) for item in input_artifacts]
        if len(paths) != len(set(paths)):
            raise CheckViolation("contract input inventory contains duplicate paths")
        if gate_a.value["contract_digest"] != contract_digest:
            raise CheckViolation("Gate A contract digest cross-link drifted")
        if gate_b_path is not None and loaded["gate_b"].value["contract_digest"] != contract_digest:
            raise CheckViolation("Gate B contract digest cross-link drifted")
        if run_identity_path is not None:
            input_digest = loaded["run_identity"].value["input_manifest_digest"]
            if input_digest not in {item["sha256"] for item in input_artifacts}:
                raise CheckViolation(
                    "run identity input manifest digest is absent from the contract inventory"
                )
        return CheckEvidence(
            reason="manifest, decisions and input inventory form exact bidirectional bindings",
            evidence={"input_count": len(paths)},
        )

    checks = [
        schema_result,
        capture_check(
            name="contract-exact-bytes",
            category="hash_exact_bytes",
            blocks=("formal_execution", "archive"),
            input_digests=(manifest.sha256, gate_a.sha256),
            operation=hashes,
        ),
        capture_check(
            name="scientific-gate-bindings",
            category="schema_domain",
            blocks=("formal_execution", "archive"),
            input_digests=(contract_digest, gate_a.sha256),
            operation=gate_decisions,
        ),
        capture_check(
            name="hidden-label-capability-boundary",
            category="hidden_label_leakage",
            blocks=("truth_access", "formal_execution", "reporting"),
            input_digests=(manifest.sha256,),
            operation=leakage,
        ),
        capture_check(
            name="contract-inventory-cross-links",
            category="inventory_cross_links",
            blocks=("evidence_seal", "archive"),
            input_digests=(manifest.sha256,),
            operation=cross_links,
        ),
    ]
    if run_identity_path is not None:
        run_identity = loaded["run_identity"]
        checks.append(
            capture_check(
                name="run-source-environment-identity",
                category="source_environment_identity",
                blocks=("formal_execution", "evidence_seal"),
                input_digests=(contract_digest, run_identity.sha256),
                operation=lambda: _validate_run_identity_evidence(
                    run_identity.value,
                    contract_digest=contract_digest,
                    runtime_git_commit=runtime_git_commit,
                    runtime_git_tree=runtime_git_tree,
                    runtime_environment_digest=runtime_environment_digest,
                ),
            )
        )
        checks.append(
            capture_check(
                name="run-resource-cost-cap",
                category="resource_cost_cap",
                blocks=("formal_execution", "evidence_seal"),
                input_digests=(run_identity.sha256,),
                operation=lambda: _resource_evidence(run_identity.value),
            )
        )
    return build_report(checks, git_commit=runtime_git_commit)


def _validate_run_identity_evidence(
    value: Mapping[str, Any],
    *,
    contract_digest: str,
    runtime_git_commit: str | None,
    runtime_git_tree: str | None,
    runtime_environment_digest: str | None,
) -> CheckEvidence:
    validate_run_identity(
        value,
        contract_digest=contract_digest,
        runtime_git_commit=runtime_git_commit,
        runtime_git_tree=runtime_git_tree,
        runtime_environment_digest=runtime_environment_digest,
    )
    return CheckEvidence(
        reason="run is bound to clean source, environment, inputs and a server-first attempt",
        evidence={"run_id": value["run_id"], "attempt_id": value["attempt_id"]},
    )


def _resource_evidence(value: Mapping[str, Any]) -> CheckEvidence:
    caps = _mapping(value["resource_caps"], "resource caps")
    usage = _mapping(value["observed_usage"], "observed usage")
    resource_fields = {
        "wall_seconds",
        "cpu_seconds",
        "peak_rss_bytes",
        "scratch_bytes",
        "network_bytes",
        "cost",
    }
    _exact_fields(caps, resource_fields, "resource caps")
    _exact_fields(usage, resource_fields, "observed usage")
    for field in resource_fields:
        limit = caps[field]
        observed = usage[field]
        if (
            isinstance(limit, bool)
            or isinstance(observed, bool)
            or not isinstance(limit, (int, float))
            or not isinstance(observed, (int, float))
            or limit < 0
            or observed < 0
            or observed > limit
        ):
            raise CheckViolation(f"resource/cost cap exceeded or invalid: {field}")
    return CheckEvidence(
        reason="observed resource and cost values are within all declared caps",
        evidence={"caps": dict(caps), "usage": dict(usage)},
    )
