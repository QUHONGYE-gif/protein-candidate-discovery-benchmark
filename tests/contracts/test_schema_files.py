from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

SCHEMA_DIRECTORY = Path(__file__).parents[2] / "schemas"
REPOSITORY_ROOT = Path(__file__).parents[2]
FIXTURE_DIRECTORY = REPOSITORY_ROOT / "tests/fixtures/contracts"

SCHEMA_NAMES = [path.name for path in sorted(SCHEMA_DIRECTORY.glob("*.schema.json"))]
STANDALONE_FIXTURE_SCHEMAS = {
    "archive-attempt-record.schema.json",
    "check-report.schema.json",
    "experiment-v2.schema.json",
    "experiment-v3.schema.json",
    "gate-a-decision.schema.json",
    "gate-b-decision.schema.json",
    "governance-config.schema.json",
    "governance-run-identity.schema.json",
    "m2-feature-artifact-v1.schema.json",
    "m2-feature-bundle-descriptor-v1.schema.json",
    "m2-feature-config-v1.schema.json",
    "m2-feature-fitted-state-json-v1.schema.json",
    "m2-prediction-artifact-v1.schema.json",
    "milestone-archive-request.schema.json",
    "milestone-archive-manifest.schema.json",
    "milestone-contract-manifest.schema.json",
}


def _schema_fixture(schema_name: str, *, valid: bool) -> object:
    governance_examples = REPOSITORY_ROOT / "manifests/governance/examples/m5-example"
    governance_paths = {
        "gate-a-decision.schema.json": governance_examples / "gate-a-decision.json",
        "gate-b-decision.schema.json": governance_examples / "gate-b-decision.json",
        "governance-config.schema.json": REPOSITORY_ROOT / "configs/governance/v2.json",
        "governance-run-identity.schema.json": governance_examples / "run-identity.json",
        "milestone-contract-manifest.schema.json": (
            governance_examples / "milestone-contract-manifest.json"
        ),
    }
    if schema_name in governance_paths:
        fixture = json.loads(governance_paths[schema_name].read_text(encoding="utf-8"))
        if not valid:
            if schema_name == "gate-a-decision.schema.json":
                fixture["approvals"] = []
            elif schema_name == "gate-b-decision.schema.json":
                fixture["verdict"] = "pass"
            elif schema_name == "governance-config.schema.json":
                fixture["server"]["capacity"]["hard_cap_fraction"] = 1.0
            elif schema_name == "governance-run-identity.schema.json":
                fixture["cos_write_count"] = 1
            else:
                fixture["run_policy"]["ordinary_run_cos_writes"] = 1
        return fixture
    if schema_name == "check-report.schema.json":
        fixture = {
            "approval_events_emitted": 0,
            "checked_at": "2026-09-02T00:00:00Z",
            "checks": [
                {
                    "blocks": ["ci"],
                    "category": "ci",
                    "input_digests": [],
                    "name": "example-ci-check",
                    "reason": "example passed",
                    "status": "pass",
                    "version": "1",
                }
            ],
            "overall_status": "pass",
            "report_id": "a" * 64,
            "schema_version": "pcdb-governance-check-report-v1",
            "tool": {"name": "pcdb-governance-check", "version": "0.1.0"},
        }
        if not valid:
            fixture["approval_events_emitted"] = 1
        return fixture
    if schema_name == "milestone-archive-request.schema.json":
        reference = {
            "archive_path": "contract.json",
            "path": "contract.json",
            "sha256": "a" * 64,
            "size_bytes": 1,
        }
        roles = (
            "contract_manifest",
            "run_identity",
            "final_result",
            "important_log",
            "summary",
            "provenance",
            "gate_b_decision",
        )
        fixture = {
            "archive_id": "archive-001",
            "attempt_id": "attempt-001",
            "contract_manifest": reference,
            "destination_prefix": "milestone-archives/v2/M5-EXAMPLE/archive-001/",
            "gate_b_decision": {**reference, "archive_path": "gate-b.json"},
            "limits": {"max_bytes": 1000, "max_objects": 10},
            "milestone_id": "M5-EXAMPLE",
            "request_status": "example",
            "schema_version": "pcdb-milestone-archive-request-v1",
            "source_inventory": [
                {
                    **reference,
                    "archive_path": f"{role}.json",
                    "path": f"{role}.json",
                    "role": role,
                }
                for role in roles
            ],
            "terminal_class": "scientific_terminal",
        }
        if not valid:
            fixture["destination_prefix"] = "legacy/archive-001/"
        return fixture
    if schema_name == "milestone-archive-manifest.schema.json":
        roles = (
            "contract_manifest",
            "run_identity",
            "final_result",
            "important_log",
            "summary",
            "provenance",
            "gate_b_decision",
        )
        fixture = {
            "archive_id": "archive-001",
            "completion_marker": {
                "path": "_SUCCESS",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "size_bytes": 0,
            },
            "evidence_digest": "c" * 64,
            "gate_b_verdict": "accept",
            "milestone_id": "M5-EXAMPLE",
            "objects": [
                {
                    "path": f"{role}.json",
                    "role": role,
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                }
                for role in roles
            ],
            "request_sha256": "b" * 64,
            "retention": "no_automatic_deletion",
            "schema_version": "pcdb-milestone-archive-manifest-v1",
        }
        if not valid:
            fixture["completion_marker"]["size_bytes"] = 1
        return fixture
    if schema_name == "archive-attempt-record.schema.json":
        fixture = {
            "archive_id": "archive-001",
            "attempt_id": "attempt-001",
            "destination_prefix": "milestone-archives/v2/M5-EXAMPLE/archive-001/",
            "detail": {},
            "milestone_id": "M5-EXAMPLE",
            "recorded_at": "2026-09-02T00:00:00Z",
            "request_sha256": "a" * 64,
            "schema_version": "pcdb-archive-attempt-record-v1",
            "status": "started",
            "terminal_class": "scientific_terminal",
        }
        if not valid:
            fixture["status"] = "complete"
        return fixture
    if schema_name == "experiment-v2.schema.json":
        fixture = json.loads(
            (FIXTURE_DIRECTORY / "experiment-v2-alpha.json").read_text(encoding="utf-8")
        )
        if not valid:
            fixture["contract_status"] = "proposal"
        return fixture
    if schema_name == "experiment-v3.schema.json":
        fixture = json.loads(
            (FIXTURE_DIRECTORY / "experiment-v2-alpha.json").read_text(encoding="utf-8")
        )
        fixture.update(
            {
                "acquisitions": ["uncertainty-bootstrap-std-v1"],
                "batch_sizes": [16],
                "budgets": [128],
                "experiment_id": "m5-seed-tracer.alpha-amylase.uncertainty.i1729.r2719",
                "initial_sample_sizes": [32],
                "models": ["random-forest-sklearn-v1"],
                "phase": "development",
                "protocol_version": "m5-seed-source-tracer-v1",
                "schema_version": "3",
                "seed_axes": {
                    "estimator_seed": 1729,
                    "estimator_seed_binding": "frozen-model-config",
                    "initial_seed": 1729,
                    "initial_seed_binding": "run-spec.seed",
                    "internal_seed": 2719,
                    "internal_seed_binding": "experiment-config-sha256",
                },
                "seeds": [1729],
            }
        )
        if not valid:
            fixture["seed_axes"]["internal_seed"] = None
        return fixture
    if schema_name == "m2-feature-artifact-v1.schema.json":
        fixture = json.loads(
            (FIXTURE_DIRECTORY / "m2-feature-artifact-v1-valid.json").read_text(encoding="utf-8")
        )
        if not valid:
            fixture["contract_status"] = "proposal"
        return fixture
    if schema_name == "m2-feature-bundle-descriptor-v1.schema.json":
        fixture = json.loads(
            (FIXTURE_DIRECTORY / "m2-feature-bundle-descriptor-v1-golden.json").read_text(
                encoding="utf-8"
            )
        )
        if not valid:
            fixture["protocol_version"] = "legacy"
        return fixture
    if schema_name == "m2-feature-config-v1.schema.json":
        fixture = json.loads(
            (FIXTURE_DIRECTORY / "m2-feature-config-v1-golden.json").read_text(encoding="utf-8")
        )
        if not valid:
            fixture["schema_version"] = "unknown"
        return fixture
    if schema_name == "m2-feature-fitted-state-json-v1.schema.json":
        fixture = json.loads(
            (FIXTURE_DIRECTORY / "m2-feature-fitted-state-json-v1-golden.json").read_text(
                encoding="utf-8"
            )
        )
        if not valid:
            fixture["fit_role"] = "validation"
        return fixture
    if schema_name == "m2-prediction-artifact-v1.schema.json":
        fixture = json.loads(
            (FIXTURE_DIRECTORY / "m2-prediction-artifact-v1-template.json").read_text(
                encoding="utf-8"
            )
        )
        fixture["predictions"] = [
            {
                "candidate_id": hashlib.sha256(f"candidate:{index}".encode()).hexdigest(),
                "prediction": "0000000000000000",
            }
            for index in range(fixture["candidate_count"])
        ]
        if not valid:
            fixture["acquisition_id"] = "adaptive"
        return fixture
    filename = "valid-manifests.json" if valid else "invalid-manifests.json"
    fixtures = json.loads((FIXTURE_DIRECTORY / filename).read_text(encoding="utf-8"))
    return fixtures[schema_name]


@pytest.mark.parametrize(
    "schema_name",
    SCHEMA_NAMES,
)
def test_schema_is_valid_draft_2020_12_with_closed_top_level(schema_name: str) -> None:
    schema = json.loads((SCHEMA_DIRECTORY / schema_name).read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"]


def test_smoke_experiment_matches_experiment_schema() -> None:
    schema = json.loads((SCHEMA_DIRECTORY / "experiment.schema.json").read_text(encoding="utf-8"))
    config = json.loads(
        (REPOSITORY_ROOT / "configs/experiments/smoke/baseline.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(config)


@pytest.mark.parametrize("fixture_name", ["valid-manifests.json", "invalid-manifests.json"])
def test_manifest_fixture_set_covers_every_schema(fixture_name: str) -> None:
    fixtures = json.loads((FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8"))

    assert set(fixtures) == set(SCHEMA_NAMES) - STANDALONE_FIXTURE_SCHEMAS


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_valid_manifest_fixture_matches_schema(schema_name: str) -> None:
    schema = json.loads((SCHEMA_DIRECTORY / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        _schema_fixture(schema_name, valid=True)
    )


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_invalid_manifest_fixture_is_rejected(schema_name: str) -> None:
    schema = json.loads((SCHEMA_DIRECTORY / schema_name).read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            _schema_fixture(schema_name, valid=False)
        )


def test_processing_manifest_v1_accepts_legacy_output_without_row_count() -> None:
    schema = json.loads(
        (SCHEMA_DIRECTORY / "processing-manifest.schema.json").read_text(encoding="utf-8")
    )
    fixtures = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text(encoding="utf-8"))
    manifest = fixtures["processing-manifest.schema.json"]
    del manifest["output_files"][0]["row_count"]

    artifact_schema = schema["$defs"]["artifact"]
    assert "row_count" in artifact_schema["properties"]
    assert "row_count" not in artifact_schema["required"]
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)


def test_split_manifest_v1_optional_evidence_extension_is_backward_compatible() -> None:
    schema = json.loads(
        (SCHEMA_DIRECTORY / "split-manifest.schema.json").read_text(encoding="utf-8")
    )
    fixtures = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text(encoding="utf-8"))
    legacy_manifest = fixtures["split-manifest.schema.json"]

    optional_fields = {
        "split_config_sha256",
        "git_commit",
        "audit_file",
        "partition_summary",
        "group_summary",
        "known_limitations",
    }
    assert optional_fields <= set(schema["properties"])
    assert optional_fields.isdisjoint(schema["required"])
    assert "row_count" in schema["properties"]["membership_file"]["properties"]
    assert "row_count" not in schema["properties"]["membership_file"]["required"]
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(legacy_manifest)
