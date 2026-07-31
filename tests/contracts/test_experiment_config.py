from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from pcdb.contracts import ExperimentConfigError, canonical_json_bytes, load_experiment_config

REPOSITORY_ROOT = Path(__file__).parents[2]
CONFORMANCE_DIRECTORY = REPOSITORY_ROOT / "configs/experiments/conformance"
V2_SCHEMA_PATH = REPOSITORY_ROOT / "schemas/experiment-v2.schema.json"
TASK_BINDINGS_PATH = REPOSITORY_ROOT / "tests/fixtures/contracts/experiment-v2-task-bindings.json"


def test_loader_dispatches_existing_v1_without_migration() -> None:
    config_path = REPOSITORY_ROOT / "configs/experiments/smoke/baseline.json"
    payload = config_path.read_bytes()

    loaded = load_experiment_config(payload)

    assert loaded.schema_version == "1"
    assert loaded.canonical_bytes == payload
    assert loaded.sha256 == hashlib.sha256(payload).hexdigest()


def test_loader_accepts_canonical_alpha_v2_conformance_config() -> None:
    payload = (CONFORMANCE_DIRECTORY / "alpha-amylase-high-value-v2.json").read_bytes()

    loaded = load_experiment_config(payload)

    assert loaded.schema_version == "2"
    assert loaded.value["task_id"] == "alpha-amylase"
    assert loaded.value["contract_status"] == "production"
    assert loaded.sha256 == hashlib.sha256(payload).hexdigest()


def test_alpha_v2_conformance_config_matches_production_schema() -> None:
    schema = json.loads(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    config = json.loads(
        (CONFORMANCE_DIRECTORY / "alpha-amylase-high-value-v2.json").read_text(encoding="utf-8")
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(config)


def test_loader_rejects_canonical_v2_with_drifted_nominal_k() -> None:
    original = json.loads(
        (CONFORMANCE_DIRECTORY / "alpha-amylase-high-value-v2.json").read_text(encoding="utf-8")
    )
    drifted = deepcopy(original)
    drifted["evaluation"]["high_value_definitions"][0]["derivation"]["nominal_k"] = 22

    with pytest.raises(ExperimentConfigError, match="definition contract"):
        load_experiment_config(canonical_json_bytes(drifted))


def test_loader_rejects_canonical_v2_with_unknown_field() -> None:
    original = json.loads(
        (CONFORMANCE_DIRECTORY / "alpha-amylase-high-value-v2.json").read_text(encoding="utf-8")
    )
    drifted = deepcopy(original)
    drifted["unknown_field"] = "forbidden"

    with pytest.raises(ExperimentConfigError, match="fields"):
        load_experiment_config(canonical_json_bytes(drifted))


def test_loader_accepts_ired_exact_binding_conformance_fixture() -> None:
    alpha = json.loads(
        (CONFORMANCE_DIRECTORY / "alpha-amylase-high-value-v2.json").read_text(encoding="utf-8")
    )
    bindings = json.loads(TASK_BINDINGS_PATH.read_text(encoding="utf-8"))
    ired = deepcopy(alpha)
    ired["experiment_id"] = "issue-36-ired-high-value-v2-conformance"
    ired["task_id"] = "ired"
    for field in ("dataset_manifest_sha256", "processing_manifest_sha256", "split_sha256"):
        ired[field] = bindings["ired"][field]
    for definition, nominal_k in zip(
        ired["evaluation"]["high_value_definitions"],
        bindings["ired"]["nominal_k"],
        strict=True,
    ):
        definition["frozen_candidate_count"] = bindings["ired"]["frozen_candidate_count"]
        definition["derivation"]["nominal_k"] = nominal_k

    loaded = load_experiment_config(canonical_json_bytes(ired))

    assert loaded.value["task_id"] == "ired"


def test_loader_rejects_noncanonical_v2_bytes() -> None:
    canonical = (CONFORMANCE_DIRECTORY / "alpha-amylase-high-value-v2.json").read_bytes()

    with pytest.raises(ExperimentConfigError, match="not canonical"):
        load_experiment_config(
            canonical.replace(b'{\n  "acquisitions"', b'{\n   "acquisitions"', 1)
        )


def test_loader_rejects_prototype_sentinel() -> None:
    with pytest.raises(ExperimentConfigError, match="unsupported"):
        load_experiment_config(b'{"schema_version":"2-prototype"}')


def test_loader_rejects_duplicate_json_key() -> None:
    with pytest.raises(ExperimentConfigError, match="duplicate"):
        load_experiment_config(b'{"schema_version":"1","schema_version":"1"}')


def test_loaded_v2_value_is_deeply_immutable() -> None:
    loaded = load_experiment_config(
        (CONFORMANCE_DIRECTORY / "alpha-amylase-high-value-v2.json").read_bytes()
    )
    mutable_view = cast(dict[str, Any], loaded.value)

    with pytest.raises(TypeError):
        mutable_view["task_id"] = "ired"
    with pytest.raises(TypeError):
        mutable_view["evaluation"]["high_value_definitions"][0]["purpose"] = "sensitivity"
