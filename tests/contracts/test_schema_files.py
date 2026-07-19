from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMA_DIRECTORY = Path(__file__).parents[2] / "schemas"


@pytest.mark.parametrize(
    "schema_name",
    ["dataset-manifest.schema.json", "experiment.schema.json", "run-manifest.schema.json"],
)
def test_schema_is_valid_json_with_closed_top_level(schema_name: str) -> None:
    schema = json.loads((SCHEMA_DIRECTORY / schema_name).read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"]
