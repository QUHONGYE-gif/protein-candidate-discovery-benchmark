"""Repository-wide Governance v2 documentation, schema and CI checks."""

from __future__ import annotations

import json
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
