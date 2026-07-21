from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

SCHEMA_DIRECTORY = Path(__file__).parents[2] / "schemas"
REPOSITORY_ROOT = Path(__file__).parents[2]
FIXTURE_DIRECTORY = REPOSITORY_ROOT / "tests/fixtures/contracts"

SCHEMA_NAMES = [path.name for path in sorted(SCHEMA_DIRECTORY.glob("*.schema.json"))]


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

    assert set(fixtures) == set(SCHEMA_NAMES)


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_valid_manifest_fixture_matches_schema(schema_name: str) -> None:
    schema = json.loads((SCHEMA_DIRECTORY / schema_name).read_text(encoding="utf-8"))
    fixtures = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text(encoding="utf-8"))

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(fixtures[schema_name])


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_invalid_manifest_fixture_is_rejected(schema_name: str) -> None:
    schema = json.loads((SCHEMA_DIRECTORY / schema_name).read_text(encoding="utf-8"))
    fixtures = json.loads(
        (FIXTURE_DIRECTORY / "invalid-manifests.json").read_text(encoding="utf-8")
    )

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(fixtures[schema_name])


def test_processing_manifest_requires_output_row_count() -> None:
    schema = json.loads(
        (SCHEMA_DIRECTORY / "processing-manifest.schema.json").read_text(encoding="utf-8")
    )
    fixtures = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text(encoding="utf-8"))
    manifest = fixtures["processing-manifest.schema.json"]
    del manifest["output_files"][0]["row_count"]

    with pytest.raises(ValidationError, match="row_count"):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
