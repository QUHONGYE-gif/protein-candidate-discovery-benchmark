"""Repository-wide Governance v2 documentation, schema and CI checks."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pcdb.governance.checks import (
    CheckEvidence,
    CheckReport,
    CheckViolation,
    build_report,
    capture_check,
    sha256_bytes,
)
from pcdb.governance.contract import (
    GOVERNANCE_RFC_SHA256,
    contract_check_report,
    load_json,
    validate_governance_config,
)

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MIRA_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")


def validate_tracking_declaration(declaration: object, policy: object) -> None:
    """Fail closed when a PR tracking declaration omits authority-specific identity."""

    if not isinstance(declaration, dict) or not isinstance(policy, dict):
        raise CheckViolation("tracking declaration and policy must be objects")
    authority = declaration.get("authority")
    allowed = policy.get("allowed")
    common = policy.get("common_required_fields")
    authority_fields = policy.get("authority_required_fields")
    object_fields = policy.get("multica_object_required_fields")
    if (
        not isinstance(authority, str)
        or not isinstance(allowed, list)
        or authority not in allowed
        or not isinstance(common, list)
        or not isinstance(authority_fields, dict)
        or not isinstance(authority_fields.get(authority), list)
        or not isinstance(object_fields, list)
    ):
        raise CheckViolation("tracking authority policy or selection is invalid")

    required = {*common, *authority_fields[authority]}
    actual = set(declaration)
    if actual != required:
        raise CheckViolation(
            "tracking declaration fields differ for "
            f"{authority}: missing={sorted(required - actual)}, extra={sorted(actual - required)}"
        )
    relationship = declaration.get("authorization_and_acceptance")
    if not isinstance(relationship, str) or not relationship.strip():
        raise CheckViolation("tracking declaration must state authorization and acceptance")

    if authority == "github_issue":
        repository = declaration.get("repository")
        issue_number = declaration.get("issue_number")
        url = declaration.get("url")
        if (
            not isinstance(repository, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or isinstance(issue_number, bool)
            or not isinstance(issue_number, int)
            or issue_number < 1
            or url != f"https://github.com/{repository}/issues/{issue_number}"
        ):
            raise CheckViolation("GitHub tracking authority identity or URL is invalid")
        return

    workspace_id = declaration.get("workspace_id")
    objects = declaration.get("objects")
    if (
        not isinstance(workspace_id, str)
        or not UUID_PATTERN.fullmatch(workspace_id)
        or not isinstance(objects, list)
        or not objects
    ):
        raise CheckViolation("Multica tracking authority workspace or objects are invalid")
    identities: set[tuple[str, str, str]] = set()
    for item in objects:
        if not isinstance(item, dict) or set(item) != set(object_fields):
            raise CheckViolation("Multica tracking object fields differ from policy")
        object_id = item.get("object_id")
        key = item.get("key")
        url = item.get("url")
        if (
            not isinstance(object_id, str)
            or not UUID_PATTERN.fullmatch(object_id)
            or not isinstance(key, str)
            or not MIRA_KEY_PATTERN.fullmatch(key)
            or not isinstance(url, str)
            or not re.fullmatch(rf"https://[^\s]+/issues/{re.escape(key)}", url)
        ):
            raise CheckViolation("Multica tracking object identity or URL is invalid")
        identities.add((object_id, key, url))
    if len(identities) != len(objects):
        raise CheckViolation("Multica tracking objects must be unique")


def repository_check_report(repository_root: Path) -> CheckReport:
    """Run the same static Governance v2 checks used by local development and CI."""

    root = repository_root.resolve()
    config_path = root / "configs/governance/v2.json"
    config = load_json(config_path)
    git_state: dict[str, str] = {}

    def schemas() -> CheckEvidence:
        from jsonschema import Draft202012Validator

        schema_paths = sorted((root / "schemas").glob("*.schema.json"))
        if not schema_paths:
            raise CheckViolation("no JSON schemas were found")
        for path in schema_paths:
            value = json.loads(path.read_bytes())
            Draft202012Validator.check_schema(value)
            if value.get("type") != "object" or value.get("additionalProperties") is not False:
                raise CheckViolation(f"schema top level is not closed: {path.name}")
        return CheckEvidence(
            reason="all repository JSON schemas are valid draft 2020-12 closed objects",
            evidence={"schema_count": len(schema_paths)},
        )

    def active_docs() -> CheckEvidence:
        from jsonschema import Draft202012Validator

        governance = (root / "docs/GOVERNANCE_V2.md").read_bytes()
        if sha256_bytes(governance) != GOVERNANCE_RFC_SHA256:
            raise CheckViolation("active Governance v2 RFC differs from the accepted exact bytes")
        required = (
            "docs/LEGACY_EXECUTION_SURFACE.md",
            "docs/GOVERNANCE_V2_RUNBOOK.md",
            "docs/adr/0005-adopt-governance-v2-for-new-milestones.md",
            "configs/governance/v2.json",
        )
        missing = [item for item in required if not (root / item).is_file()]
        if missing:
            raise CheckViolation(f"active Governance v2 files are missing: {missing}")
        config_schema = json.loads((root / "schemas/governance-config.schema.json").read_bytes())
        schema_errors = sorted(
            Draft202012Validator(config_schema).iter_errors(config.value),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if schema_errors:
            raise CheckViolation(
                f"Governance v2 config differs from its schema: {schema_errors[0].message}"
            )
        validate_governance_config(config.value)
        return CheckEvidence(
            reason=(
                "accepted RFC bytes, prospective adoption ADR, runbook, legacy index and "
                "deployment config exist"
            ),
            evidence={"rfc_sha256": GOVERNANCE_RFC_SHA256},
        )

    def legacy_surface() -> CheckEvidence:
        paths = (
            "src/pcdb/m2",
            "src/pcdb/m3",
            "src/pcdb/m4",
            "src/pcdb/m5",
            "src/pcdb/storage/cos.py",
            "manifests/experiments/m2",
            "manifests/experiments/m3",
        )
        missing = [item for item in paths if not (root / item).exists()]
        if missing:
            raise CheckViolation(f"Legacy Execution Surface moved or disappeared: {missing}")
        return CheckEvidence(
            reason="all indexed historical execution surfaces remain addressable",
            evidence={"paths": list(paths)},
        )

    def repository_workflow() -> CheckEvidence:
        workflow = config.value["repository_workflow"]
        if not isinstance(workflow, dict):
            raise CheckViolation("repository workflow config is not an object")
        active_paths = (
            "AGENTS.md",
            "CONTRIBUTING.md",
            "README.md",
            "docs/GOVERNANCE_V2_RUNBOOK.md",
            "docs/PROJECT_DIRECTION.md",
            "docs/agents/issue-tracker.md",
            ".github/pull_request_template.md",
            "manifests/governance/examples/README.md",
        )
        active_docs = {path: (root / path).read_text(encoding="utf-8") for path in active_paths}
        classification_paths = (
            "AGENTS.md",
            "CONTRIBUTING.md",
            "docs/GOVERNANCE_V2_RUNBOOK.md",
            "docs/agents/issue-tracker.md",
            ".github/pull_request_template.md",
            "manifests/governance/examples/README.md",
        )
        missing_classes = [
            path
            for path in classification_paths
            if "Major / semantic PR" not in active_docs[path]
            or "Maintenance batch PR" not in active_docs[path]
        ]
        if missing_classes:
            raise CheckViolation(
                f"repository workflow classes are missing from active files: {missing_classes}"
            )

        stale_phrases = ("Fast path", "Large/Risk path", "Large-Risk path")
        stale_locations = [
            f"{path}:{phrase}"
            for path in classification_paths
            for phrase in stale_phrases
            if phrase in active_docs[path]
        ]
        if stale_locations:
            raise CheckViolation(f"legacy PR classification remains active: {stale_locations}")

        maintenance = workflow["maintenance_batch"]
        if not isinstance(maintenance, dict):
            raise CheckViolation("repository workflow maintenance policy is not an object")
        limit = maintenance["logical_change_limit"]
        trigger_markers = {
            "AGENTS.md": (
                f"earliest of {limit} logical small changes",
                "current Issue/milestone ending",
                "next experiment needing its information",
            ),
            "CONTRIBUTING.md": (
                f"累计 {limit} 个逻辑小变化",
                "当前 Issue/milestone 结束",
                "下一段实验需要其中信息",
            ),
            "docs/GOVERNANCE_V2_RUNBOOK.md": (
                f"First of: {limit} logical small changes",
                "current Issue/milestone closeout",
                "next experiment needs the information",
            ),
            ".github/pull_request_template.md": (
                f"累计 {limit} 个逻辑小变化",
                "当前 Issue/milestone 结束",
                "下一段实验需要其中信息",
            ),
        }
        missing_triggers = [
            f"{path}:{marker}"
            for path, markers in trigger_markers.items()
            for marker in markers
            if marker not in active_docs[path]
        ]
        if missing_triggers:
            raise CheckViolation(
                f"maintenance batch triggers differ from governance config: {missing_triggers}"
            )

        category_docs = ("CONTRIBUTING.md", "docs/GOVERNANCE_V2_RUNBOOK.md")
        missing_categories = [
            f"{path}:{category}"
            for path in category_docs
            for group in ("major_semantic", "maintenance_batch")
            for category in workflow[group]["categories"]
            if category.replace("_or_", "/").replace("_", " ") not in active_docs[path].lower()
        ]
        missing_boundaries = [
            boundary
            for boundary in workflow["major_semantic"]["semantic_boundaries"]
            if boundary.replace("_", " ")
            not in active_docs["docs/GOVERNANCE_V2_RUNBOOK.md"].lower()
        ]
        if missing_categories or missing_boundaries:
            raise CheckViolation(
                "PR category or semantic-boundary examples differ from governance config: "
                f"categories={missing_categories}, boundaries={missing_boundaries}"
            )

        tracking_authority = workflow["tracking_authority"]
        tracking_examples = workflow["tracking_examples"]
        if not isinstance(tracking_authority, dict) or not isinstance(tracking_examples, dict):
            raise CheckViolation("tracking authority policy and examples must be objects")
        allowed_authorities = tracking_authority.get("allowed")
        if allowed_authorities != ["github_issue", "multica"]:
            raise CheckViolation("Major tracking authority must allow GitHub Issue or Multica")
        for authority in allowed_authorities:
            if authority not in tracking_examples:
                raise CheckViolation(f"tracking example is missing for authority: {authority}")
            validate_tracking_declaration(tracking_examples[authority], tracking_authority)

        authority_docs = (
            "AGENTS.md",
            "CONTRIBUTING.md",
            "docs/GOVERNANCE_V2_RUNBOOK.md",
            "docs/agents/issue-tracker.md",
            ".github/pull_request_template.md",
        )
        authority_markers = (
            "github_issue",
            "multica",
            "authorization_and_acceptance",
            "workspace_id",
            "object_id",
        )
        missing_authority_policy = [
            f"{path}:{marker}"
            for path in authority_docs
            for marker in authority_markers
            if marker not in active_docs[path]
        ]
        if missing_authority_policy:
            raise CheckViolation(
                f"tracking authority choices or required fields drifted: {missing_authority_policy}"
            )

        cross_links = workflow["cross_links"]
        if not isinstance(cross_links, dict):
            raise CheckViolation("repository workflow cross-link policy is not an object")
        example_docs = ("CONTRIBUTING.md", "docs/GOVERNANCE_V2_RUNBOOK.md")
        missing_examples = [
            f"{path}:{field}"
            for path in example_docs
            for field in (
                "github_authority_multica_title",
                "github_authority_multica_source",
                "github_authority_backlink",
            )
            if cross_links[field] not in active_docs[path]
        ]
        checklist_markers = (
            f"Maintenance batch (3/{limit} logical changes)",
            "Semantic/data/interface impact: none",
        )
        missing_examples.extend(
            f"{path}:parent_checklist"
            for path in example_docs
            if any(marker not in active_docs[path] for marker in checklist_markers)
        )
        if missing_examples:
            raise CheckViolation(
                f"cross-link or parent checklist examples drifted: {missing_examples}"
            )
        mapping_markers = {
            "CONTRIBUTING.md": "不要求一一对应",
            "docs/GOVERNANCE_V2_RUNBOOK.md": (
                "one GitHub Issue may cover several Multica execution issues"
            ),
        }
        if (
            cross_links["mapping"] != "many_to_one"
            or cross_links["required_only_when"] != "github_issue"
            or any(marker not in active_docs[path] for path, marker in mapping_markers.items())
        ):
            raise CheckViolation("Multica/GitHub many-to-one tracking policy drifted")

        issue_forms = sorted((root / ".github/ISSUE_TEMPLATE").glob("*.yml"))
        issue_forms = [path for path in issue_forms if path.name != "config.yml"]
        selection_triggers = tracking_authority["selection_required_for"]
        if not isinstance(selection_triggers, list):
            raise CheckViolation("tracking-authority selection triggers are not a list")
        invalid_forms = []
        for path in issue_forms:
            content = path.read_text(encoding="utf-8")
            if (
                "id: tracking_threshold_reason" not in content
                or "id: github_tracking_authority" not in content
                or any(trigger not in content for trigger in selection_triggers)
            ):
                invalid_forms.append(str(path.relative_to(root)))
        if not issue_forms or invalid_forms:
            raise CheckViolation(
                "GitHub Issue forms do not enforce authority selection and threshold: "
                f"{invalid_forms}"
            )

        pull_request_template = active_docs[".github/pull_request_template.md"]
        if any(line.strip() == "Closes #" for line in pull_request_template.splitlines()):
            raise CheckViolation("pull request template contains an unconditional close intent")

        forbidden = workflow["forbidden_tracking_files"]
        if not isinstance(forbidden, list):
            raise CheckViolation("forbidden tracking files are not a list")
        forbidden_paths = [
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file() and path.name in forbidden
        ]
        if forbidden_paths:
            raise CheckViolation(
                f"forbidden workflow ledger or registry files exist: {forbidden_paths}"
            )

        status_markers = {
            "AGENTS.md": ("Gate A scientific-state transition", "Gate B terminal"),
            "CONTRIBUTING.md": ("Gate A 触发科学阶段迁移", "Gate B 产生 terminal"),
            "README.md": ("Gate A scientific transition", "Gate B terminal"),
            "docs/GOVERNANCE_V2_RUNBOOK.md": (
                "Gate A scientific transition",
                "Gate B terminal",
            ),
            "docs/PROJECT_DIRECTION.md": (
                "Gate A 触发 scientific state",
                "Gate B 产生 terminal",
            ),
            ".github/pull_request_template.md": (
                "Gate A scientific transition",
                "Gate B terminal",
            ),
        }
        missing_status_rules = [
            f"{path}:{marker}"
            for path, markers in status_markers.items()
            for marker in (*markers, "milestone closeout")
            if marker not in active_docs[path]
        ]
        if missing_status_rules:
            raise CheckViolation(
                f"STATUS update rules differ from governance config: {missing_status_rules}"
            )

        return CheckEvidence(
            reason=(
                "repository workflow config, active docs, templates and examples agree without "
                "a third tracking ledger"
            ),
            evidence={
                "active_document_count": len(active_docs),
                "allowed_tracking_authorities": allowed_authorities,
                "cross_link_mapping": cross_links["mapping"],
                "issue_form_count": len(issue_forms),
                "maintenance_logical_change_limit": limit,
                "status_update_events": workflow["status"]["update_events"],
                "tracking_authority_trigger_count": len(selection_triggers),
            },
        )

    def ci_entry() -> CheckEvidence:
        makefile = (root / "Makefile").read_text(encoding="utf-8")
        workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        if (
            "governance-check:" not in makefile
            or "check: lint governance-check test" not in makefile
        ):
            raise CheckViolation("make check does not invoke the shared governance-check target")
        if "run: make check" not in workflow:
            raise CheckViolation("CI does not invoke the same make check entry point as local use")
        for field, revision in (("commit", "HEAD^{commit}"), ("tree", "HEAD^{tree}")):
            git_state[field] = subprocess.run(
                ("git", "rev-parse", revision),
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        return CheckEvidence(
            reason="local and CI checks share make check and pcdb governance-check repository",
            evidence={
                "ci_required_enforcement_changed": False,
                "git_commit": git_state["commit"],
                "git_tree": git_state["tree"],
            },
        )

    checks = [
        capture_check(
            name="governance-json-schemas",
            category="schema_domain",
            blocks=("ci", "contract_acceptance"),
            input_digests=(config.sha256,),
            operation=schemas,
        ),
        capture_check(
            name="governance-active-docs",
            category="hash_exact_bytes",
            blocks=("ci", "governance_v2_use"),
            input_digests=(config.sha256, GOVERNANCE_RFC_SHA256),
            operation=active_docs,
        ),
        capture_check(
            name="legacy-execution-surface",
            category="inventory_cross_links",
            blocks=("ci", "legacy_compatibility"),
            input_digests=(GOVERNANCE_RFC_SHA256,),
            operation=legacy_surface,
        ),
        capture_check(
            name="repository-workflow-v2",
            category="inventory_cross_links",
            blocks=("ci", "operator_documentation", "pr_engineering_quality"),
            input_digests=(config.sha256, GOVERNANCE_RFC_SHA256),
            operation=repository_workflow,
        ),
        capture_check(
            name="governance-ci-entry",
            category="ci",
            blocks=("ci", "pr_engineering_quality"),
            input_digests=(),
            operation=ci_entry,
        ),
    ]

    example_root = root / "manifests/governance/examples/m5-example"
    if example_root.is_dir():
        example = contract_check_report(
            manifest_path=example_root / "milestone-contract-manifest.json",
            gate_a_path=example_root / "gate-a-decision.json",
            gate_b_path=example_root / "gate-b-decision.json",
            run_identity_path=example_root / "run-identity.json",
            config_path=config_path,
            source_root=example_root,
            require_final=False,
        )
        checks.extend(example.checks)
    else:
        checks.append(
            capture_check(
                name="governance-examples",
                category="schema_domain",
                blocks=("ci", "operator_documentation"),
                input_digests=(),
                operation=_missing_examples,
            )
        )
    return build_report(checks, git_commit=git_state.get("commit"))


def _missing_examples() -> CheckEvidence:
    raise CheckViolation("Governance v2 manifest and Gate decision examples are missing")
