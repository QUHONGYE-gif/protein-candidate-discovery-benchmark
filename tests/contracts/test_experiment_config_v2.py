from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from pcdb.contracts.experiment import (
    ContractValidationError,
    LoadedContract,
    canonical_json_bytes,
    load_experiment_config,
    load_high_value_result,
)
from pcdb.contracts.high_value import (
    MetricResultEvidence,
    validate_metric_result_evidence,
    validate_task_seed_aggregate,
)
from pcdb.contracts.run import RunSpec

ROOT = Path(__file__).parents[2]
SCHEMA_DIRECTORY = ROOT / "schemas"
FIXTURE_DIRECTORY = ROOT / "tests/fixtures/contracts"


def _fixture_bytes(task: str) -> bytes:
    return (FIXTURE_DIRECTORY / f"experiment-v2-{task}.json").read_bytes()


@pytest.mark.parametrize("task", ["alpha", "ired"])
def test_v2_conformance_fixture_is_canonical_and_domain_valid(task: str) -> None:
    payload = _fixture_bytes(task)

    loaded = load_experiment_config(payload)

    assert loaded.schema_version == "2"
    assert loaded.canonical_bytes == payload
    assert loaded.sha256 == hashlib.sha256(payload).hexdigest()
    assert loaded.value["held_out_authorized"] is False


def test_v2_schema_accepts_both_exact_task_bindings() -> None:
    schema = json.loads((SCHEMA_DIRECTORY / "experiment-v2.schema.json").read_text())
    validator = Draft202012Validator(schema)

    for task in ("alpha", "ired"):
        validator.validate(json.loads(_fixture_bytes(task)))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.__setitem__("proposal_status", "non-production"),
        lambda value: value.__setitem__("schema_version", "2-prototype"),
        lambda value: value.__setitem__("contract_status", "proposal"),
        lambda value: value.__setitem__("task_id", "unknown"),
    ],
)
def test_v2_schema_rejects_proposal_unknown_and_nonproduction_sentinels(
    mutator: Any,
) -> None:
    schema = json.loads((SCHEMA_DIRECTORY / "experiment-v2.schema.json").read_text())
    value = json.loads(_fixture_bytes("alpha"))
    mutator(value)

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(value)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("dataset_manifest_sha256", "f" * 64),
        ("processing_manifest_sha256", "f" * 64),
        ("split_sha256", "f" * 64),
    ],
)
def test_v2_loader_rejects_task_binding_drift(field: str, bad_value: str) -> None:
    value = json.loads(_fixture_bytes("alpha"))
    value[field] = bad_value

    with pytest.raises(ContractValidationError, match="task binding"):
        load_experiment_config(canonical_json_bytes(value))


def test_v2_loader_rejects_wrong_definition_order_or_nominal_k() -> None:
    value = json.loads(_fixture_bytes("alpha"))
    definitions = value["evaluation"]["high_value_definitions"]
    definitions[0], definitions[1] = definitions[1], definitions[0]

    with pytest.raises(ContractValidationError, match="definition"):
        load_experiment_config(canonical_json_bytes(value))

    value = json.loads(_fixture_bytes("alpha"))
    value["evaluation"]["high_value_definitions"][0]["derivation"]["nominal_k"] = 24

    with pytest.raises(ContractValidationError, match="nominal"):
        load_experiment_config(canonical_json_bytes(value))


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":"2","schema_version":"2"}\n',
        b'{"schema_version":"2","value":NaN}\n',
        b"\xef\xbb\xbf{}\n",
        b"\xff",
    ],
)
def test_loader_rejects_duplicate_nonfinite_bom_and_invalid_utf8(payload: bytes) -> None:
    with pytest.raises(ContractValidationError):
        load_experiment_config(payload)


def test_v2_loader_rejects_semantically_equal_noncanonical_bytes() -> None:
    value = json.loads(_fixture_bytes("alpha"))
    compact = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")

    with pytest.raises(ContractValidationError, match="canonical"):
        load_experiment_config(compact)


def test_v1_dispatch_preserves_existing_fixture_behavior() -> None:
    payload = (ROOT / "configs/experiments/smoke/baseline.json").read_bytes()

    loaded = load_experiment_config(payload)

    assert loaded.schema_version == "1"
    assert loaded.value["experiment_id"] == "smoke-baseline-v1"
    assert loaded.sha256 == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    ("schema_name", "version"),
    [
        ("high-value-metric-result.schema.json", "high-value-metric-result-v1"),
        (
            "high-value-task-seed-aggregate.schema.json",
            "high-value-task-seed-aggregate-v1",
        ),
    ],
)
def test_result_artifact_loader_enforces_schema_dispatch_and_canonical_bytes(
    schema_name: str,
    version: str,
) -> None:
    fixtures = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text())
    value = fixtures[schema_name]
    payload = canonical_json_bytes(value)

    loaded = load_high_value_result(payload, expected_schema_version=version)

    assert loaded.schema_version == version
    assert loaded.canonical_bytes == payload
    assert loaded.sha256 == hashlib.sha256(payload).hexdigest()

    noncanonical = json.dumps(value, sort_keys=True).encode()
    with pytest.raises(ContractValidationError, match="canonical"):
        load_high_value_result(noncanonical, expected_schema_version=version)

    wrong = copy.deepcopy(value)
    wrong["schema_version"] = "unknown"
    with pytest.raises(ContractValidationError, match="schema_version"):
        load_high_value_result(
            canonical_json_bytes(wrong),
            expected_schema_version=version,
        )


def test_metric_result_loader_rejects_metric_specific_state_drift() -> None:
    fixtures = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text())
    value = fixtures["high-value-metric-result.schema.json"]
    value["metric_id"] = "success_at_budget"
    value["definition_id"] = "upper-tail-1pct"
    value["value"] = 1.0
    value["support"] = {
        "frozen_candidate_count": 2262,
        "nominal_k": 23,
        "actual_high_value_count": 23,
        "boundary_score": 1.0,
        "boundary_tie_count": 1,
        "evaluated_candidate_count": 32,
    }

    with pytest.raises(ContractValidationError, match="boolean"):
        load_high_value_result(
            canonical_json_bytes(value),
            expected_schema_version="high-value-metric-result-v1",
        )

    value["status"] = "undefined"
    value["value"] = None
    value["undefined_reason"] = "constant_target"
    with pytest.raises(ContractValidationError, match="undefined reason"):
        load_high_value_result(
            canonical_json_bytes(value),
            expected_schema_version="high-value-metric-result-v1",
        )


def test_aggregate_loader_rejects_nonascending_or_duplicate_seeds() -> None:
    fixtures = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text())
    value = fixtures["high-value-task-seed-aggregate.schema.json"]
    value["ordered_inputs"].reverse()

    with pytest.raises(ContractValidationError, match="ascending"):
        load_high_value_result(
            canonical_json_bytes(value),
            expected_schema_version="high-value-task-seed-aggregate-v1",
        )


def _run_spec(config_sha256: str, seed: int) -> RunSpec:
    return RunSpec(
        schema_version="1",
        protocol_version="synthetic-protocol-v1",
        git_commit="a" * 40,
        environment_digest="b" * 64,
        dataset_manifest_sha256=(
            "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac"
        ),
        processing_manifest_sha256=(
            "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec"
        ),
        split_sha256="c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a",
        feature_sha256="c" * 64,
        experiment_config_sha256=config_sha256,
        model_id="synthetic-model",
        model_config_sha256="4" * 64,
        acquisition_id="synthetic-acquisition",
        acquisition_config_sha256="5" * 64,
        seed=seed,
        initial_sample_size=16,
        batch_size=8,
        budget=32,
    )


def _metric_evidence(
    experiment_config_sha256: str,
    *,
    seed: int,
    value: bool,
    attempt_id: str = "attempt-001",
) -> MetricResultEvidence:
    run_spec = _run_spec(experiment_config_sha256, seed)
    raw = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text())
    result = copy.deepcopy(raw["high-value-metric-result.schema.json"])
    result.update(
        {
            "experiment_config_sha256": experiment_config_sha256,
            "run_id": run_spec.run_id,
            "attempt_id": attempt_id,
            "metric_id": "success_at_budget",
            "definition_id": "upper-tail-1pct",
            "seed": seed,
            "status": "defined",
            "value": value,
            "undefined_reason": None,
            "support": {
                "frozen_candidate_count": 2262,
                "nominal_k": 23,
                "actual_high_value_count": 23,
                "boundary_score": 1.0,
                "boundary_tie_count": 1,
                "evaluated_candidate_count": 32,
            },
        }
    )
    loaded = load_high_value_result(
        canonical_json_bytes(result),
        expected_schema_version="high-value-metric-result-v1",
    )
    attempt = {
        "run_id": run_spec.run_id,
        "attempt_id": attempt_id,
        "status": "succeeded",
        "executed_git_commit": run_spec.git_commit,
        "executed_environment_digest": run_spec.environment_digest,
        "outputs": [
            {
                "object_key": f"runs/{run_spec.run_id}/{attempt_id}/metric.json",
                "sha256": loaded.sha256,
                "size_bytes": len(loaded.canonical_bytes),
            }
        ],
        "completion_marker": {
            "object_key": f"runs/{run_spec.run_id}/{attempt_id}/_SUCCESS",
            "sha256": ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
            "size_bytes": 0,
        },
    }
    return MetricResultEvidence(result=loaded, run_spec=run_spec, run_attempt=attempt)


def _aggregate(
    experiment_config_sha256: str,
    evidences: list[MetricResultEvidence],
) -> LoadedContract:
    raw = json.loads((FIXTURE_DIRECTORY / "valid-manifests.json").read_text())
    aggregate = copy.deepcopy(raw["high-value-task-seed-aggregate.schema.json"])
    aggregate["experiment_config_sha256"] = experiment_config_sha256
    aggregate["ordered_inputs"] = [
        {
            "seed": evidence.run_spec.seed,
            "run_id": evidence.run_spec.run_id,
            "attempt_id": evidence.result.value["attempt_id"],
            "metric_result_sha256": evidence.result.sha256,
        }
        for evidence in evidences
    ]
    return load_high_value_result(
        canonical_json_bytes(aggregate),
        expected_schema_version="high-value-task-seed-aggregate-v1",
    )


def test_cross_artifact_validator_accepts_unique_succeeded_seed_evidence() -> None:
    config = load_experiment_config(_fixture_bytes("alpha"))
    evidences = [
        _metric_evidence(config.sha256, seed=0, value=True),
        _metric_evidence(config.sha256, seed=1, value=False),
    ]
    aggregate = _aggregate(config.sha256, evidences)

    for evidence in evidences:
        validate_metric_result_evidence(evidence, config)
    validate_task_seed_aggregate(aggregate, config, evidences)


def test_cross_artifact_validator_rejects_multiple_eligible_attempts() -> None:
    config = load_experiment_config(_fixture_bytes("alpha"))
    evidences = [
        _metric_evidence(config.sha256, seed=0, value=True),
        _metric_evidence(config.sha256, seed=1, value=False),
    ]
    aggregate = _aggregate(config.sha256, evidences)
    duplicate = _metric_evidence(
        config.sha256,
        seed=0,
        value=True,
        attempt_id="attempt-002",
    )

    with pytest.raises(ContractValidationError, match="exactly one eligible"):
        validate_task_seed_aggregate(aggregate, config, [*evidences, duplicate])


def test_cross_artifact_validator_requires_valid_completion_marker() -> None:
    config = load_experiment_config(_fixture_bytes("alpha"))
    evidence = _metric_evidence(config.sha256, seed=0, value=True)
    attempt = dict(evidence.run_attempt)
    attempt["completion_marker"] = {
        "object_key": "runs/not-success",
        "sha256": "0" * 64,
        "size_bytes": 1,
    }

    with pytest.raises(ContractValidationError, match="completion marker"):
        validate_metric_result_evidence(
            MetricResultEvidence(
                result=evidence.result,
                run_spec=evidence.run_spec,
                run_attempt=attempt,
            ),
            config,
        )
