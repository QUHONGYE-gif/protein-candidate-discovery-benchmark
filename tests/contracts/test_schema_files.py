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
    "experiment-v2.schema.json",
    "m2-feature-artifact-v1.schema.json",
    "m2-feature-bundle-descriptor-v1.schema.json",
    "m2-feature-config-v1.schema.json",
    "m2-feature-fitted-state-json-v1.schema.json",
    "m2-prediction-artifact-v1.schema.json",
}


def _schema_fixture(schema_name: str, *, valid: bool) -> object:
    if schema_name == "experiment-v2.schema.json":
        fixture = json.loads(
            (FIXTURE_DIRECTORY / "experiment-v2-alpha.json").read_text(encoding="utf-8")
        )
        if not valid:
            fixture["contract_status"] = "proposal"
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
