from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from pcdb.contracts.experiment import (
    ContractValidationError,
    LoadedContract,
    canonical_json_bytes,
)
from pcdb.contracts.feature import load_feature_artifact
from pcdb.contracts.feature_identity import (
    build_feature_bundle_descriptor,
    canonical_feature_json_bytes,
    encode_feature_config,
    encode_feature_fitted_state,
    encode_feature_matrix,
    load_feature_bundle_descriptor,
    load_feature_config,
    load_feature_fitted_state,
    load_feature_matrix,
    validate_feature_bundle_identity,
    validate_run_spec_feature_identity,
)
from pcdb.contracts.run import RunSpec

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/contracts"
GOLDEN_ROOT = "99eb2b96c9c83d5f4631a48b81ba52f876f1de12ee381f623fd449b2311917cb"


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _golden_descriptor() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(_fixture_bytes("m2-feature-bundle-descriptor-v1-golden.json")),
    )


def _run_spec(*, protocol_version: str, feature_sha256: str) -> RunSpec:
    return RunSpec(
        schema_version="1",
        protocol_version=protocol_version,
        git_commit="d" * 40,
        environment_digest="e" * 64,
        dataset_manifest_sha256="a" * 64,
        processing_manifest_sha256="b" * 64,
        split_sha256="c" * 64,
        feature_sha256=feature_sha256,
        experiment_config_sha256="1" * 64,
        model_id="ridge",
        model_config_sha256="2" * 64,
        acquisition_id="greedy",
        acquisition_config_sha256="3" * 64,
        seed=0,
        initial_sample_size=1,
        batch_size=1,
        budget=1,
    )


def test_approved_golden_json_members_have_exact_identity() -> None:
    config_bytes = _fixture_bytes("m2-feature-config-v1-golden.json")
    state_bytes = _fixture_bytes("m2-feature-fitted-state-json-v1-golden.json")
    descriptor_bytes = _fixture_bytes("m2-feature-bundle-descriptor-v1-golden.json")

    assert len(config_bytes) == 404
    assert hashlib.sha256(config_bytes).hexdigest() == (
        "733eef5f34a5202b408da98de8f5c27624c317689a810966019dac2fc4dea783"
    )
    assert len(state_bytes) == 686
    assert hashlib.sha256(state_bytes).hexdigest() == (
        "b895ce5c2084c0569a887acb7765ff35b9ebc788b9536139783b4dcb7baa2411"
    )
    assert len(descriptor_bytes) == 4944
    assert hashlib.sha256(descriptor_bytes).hexdigest() == (
        "99eb2b96c9c83d5f4631a48b81ba52f876f1de12ee381f623fd449b2311917cb"
    )

    load_feature_config(config_bytes)
    load_feature_fitted_state(
        state_bytes,
        tensor_definitions=[{"dtype": "float64", "name": "mean", "shape": []}],
    )
    descriptor = load_feature_bundle_descriptor(descriptor_bytes)
    assert descriptor.sha256 == GOLDEN_ROOT


@pytest.mark.parametrize(
    ("filename", "expected_sha256"),
    [
        (
            "m2-feature-development-v1-golden.csv",
            "2678314a9eb4a5e20d2fcc4caf51a0327aae0496d365f9e498a1eb01b8b4ac7d",
        ),
        (
            "m2-feature-validation-v1-golden.csv",
            "7b329666e03000719ac79eea4662d760c4905968564917643594b2afd16d7821",
        ),
        (
            "m2-feature-candidate-pool-v1-golden.csv",
            "b7c10681eff3d807db4537a7954477a14f83f2e4e667564021ed47a59dc4ad4c",
        ),
    ],
)
def test_approved_golden_matrices_have_exact_identity(
    filename: str,
    expected_sha256: str,
) -> None:
    payload = _fixture_bytes(filename)
    feature_definitions = _golden_descriptor()["feature_definitions"]

    loaded = load_feature_matrix(payload, feature_definitions=feature_definitions)

    assert len(payload) == 109
    assert loaded.sha256 == expected_sha256
    assert loaded.feature_ids == ("count", "score")
    assert (
        encode_feature_matrix(
            loaded.rows,
            feature_definitions=feature_definitions,
        )
        == payload
    )


def test_canonical_json_and_member_encoders_reproduce_golden_bytes() -> None:
    config = json.loads(_fixture_bytes("m2-feature-config-v1-golden.json"))
    state = json.loads(_fixture_bytes("m2-feature-fitted-state-json-v1-golden.json"))

    assert canonical_feature_json_bytes(config) == _fixture_bytes(
        "m2-feature-config-v1-golden.json"
    )
    assert encode_feature_config(config) == _fixture_bytes("m2-feature-config-v1-golden.json")
    assert encode_feature_fitted_state(
        state,
        tensor_definitions=[{"dtype": "float64", "name": "mean", "shape": []}],
    ) == _fixture_bytes("m2-feature-fitted-state-json-v1-golden.json")


def test_member_encoders_normalize_only_the_frozen_array_orders() -> None:
    config = json.loads(_fixture_bytes("m2-feature-config-v1-golden.json"))
    config["derivations"].reverse()
    assert encode_feature_config(config) == _fixture_bytes("m2-feature-config-v1-golden.json")

    definitions = _golden_descriptor()["feature_definitions"]
    rows = [
        {"candidate_id": "1" * 64, "count": 1, "score": 1.0},
        {"candidate_id": "0" * 64, "count": 0, "score": 0.0},
    ]
    assert encode_feature_matrix(
        rows,
        feature_definitions=definitions,
    ) == encode_feature_matrix(
        list(reversed(rows)),
        feature_definitions=definitions,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload[:-1],
        lambda payload: payload + b"\n",
        lambda payload: b"\xef\xbb\xbf" + payload,
        lambda payload: payload.replace(b'  "derivations"', b' "derivations"', 1),
        lambda payload: payload.replace(
            b'"schema_version"',
            b'"unknown": 0,\n  "schema_version"',
            1,
        ),
    ],
)
def test_feature_config_rejects_noncanonical_or_unknown_bytes(mutation: object) -> None:
    payload = _fixture_bytes("m2-feature-config-v1-golden.json")

    with pytest.raises(ContractValidationError):
        load_feature_config(mutation(payload))  # type: ignore[operator]


def test_feature_config_rejects_duplicate_key_and_derivation_drift() -> None:
    payload = _fixture_bytes("m2-feature-config-v1-golden.json")
    duplicate = payload.replace(
        b'  "feature_config_id":',
        b'  "feature_config_id": "duplicate",\n  "feature_config_id":',
        1,
    )
    with pytest.raises(ContractValidationError, match="duplicate"):
        load_feature_config(duplicate)

    config = json.loads(payload)
    config["derivations"].reverse()
    with pytest.raises(ContractValidationError, match="ordered"):
        load_feature_config(canonical_feature_json_bytes(config))

    definitions = _golden_descriptor()["feature_definitions"]
    config = json.loads(payload)
    config["derivations"].pop()
    with pytest.raises(ContractValidationError, match="exact-cover"):
        load_feature_config(
            canonical_feature_json_bytes(config),
            feature_definitions=definitions,
        )


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        ("int32", "01"),
        ("int32", "2147483648"),
        ("float32", "7f800000"),
        ("float64", "7ff0000000000000"),
        ("bool", 1),
        ("utf8", "e\u0301"),
    ],
)
def test_feature_config_rejects_noncanonical_typed_values(dtype: str, value: object) -> None:
    config = json.loads(_fixture_bytes("m2-feature-config-v1-golden.json"))
    config["derivations"][0]["parameters"] = [
        {"dtype": dtype, "name": "parameter", "shape": [], "values": [value]}
    ]

    with pytest.raises(ContractValidationError):
        load_feature_config(canonical_feature_json_bytes(config))


def test_feature_config_rejects_duplicate_parameter_and_shape_cardinality_drift() -> None:
    config = json.loads(_fixture_bytes("m2-feature-config-v1-golden.json"))
    parameter = {
        "dtype": "int32",
        "name": "window",
        "shape": [2],
        "values": ["1"],
    }
    config["derivations"][0]["parameters"] = [parameter]
    with pytest.raises(ContractValidationError, match="shape"):
        load_feature_config(canonical_feature_json_bytes(config))

    parameter["shape"] = []
    config["derivations"][0]["parameters"] = [parameter, copy.deepcopy(parameter)]
    with pytest.raises(ContractValidationError, match="unique"):
        load_feature_config(canonical_feature_json_bytes(config))


@pytest.mark.parametrize(
    "tensor_definitions",
    [
        [],
        [{"dtype": "float32", "name": "mean", "shape": []}],
        [{"dtype": "float64", "name": "mean", "shape": [1]}],
        [{"dtype": "float64", "name": "other", "shape": []}],
        [
            {"dtype": "float64", "name": "mean", "shape": []},
            {"dtype": "float64", "name": "extra", "shape": []},
        ],
    ],
)
def test_fitted_state_rejects_tensor_inventory_drift(
    tensor_definitions: list[dict[str, object]],
) -> None:
    with pytest.raises(ContractValidationError):
        load_feature_fitted_state(
            _fixture_bytes("m2-feature-fitted-state-json-v1-golden.json"),
            tensor_definitions=tensor_definitions,
        )


def test_fitted_state_rejects_empty_duplicate_and_version_drift() -> None:
    definition = [{"dtype": "float64", "name": "mean", "shape": []}]
    state = json.loads(_fixture_bytes("m2-feature-fitted-state-json-v1-golden.json"))

    mutations: tuple[Callable[[dict[str, Any]], None], ...] = (
        lambda value: value["tensors"].clear(),
        lambda value: value["tensors"].append(copy.deepcopy(value["tensors"][0])),
    )
    for mutate in mutations:
        changed = copy.deepcopy(state)
        mutate(changed)
        with pytest.raises(ContractValidationError):
            load_feature_fitted_state(
                canonical_feature_json_bytes(changed),
                tensor_definitions=definition,
            )

    changed = copy.deepcopy(state)
    changed["state_version"] = "2"
    with pytest.raises(ContractValidationError):
        load_feature_fitted_state(
            canonical_feature_json_bytes(changed),
            tensor_definitions=definition,
            expected_state_version="1",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.replace(b"\n", b"\r\n"),
        lambda payload: payload.replace(b",0,", b',"0",'),
        lambda payload: payload.replace(b",0,", b",00,"),
        lambda payload: payload.replace(b"0000000000000000\n", b"7ff0000000000000\n"),
        lambda payload: payload[:-1],
    ],
)
def test_matrix_decoder_rejects_noncanonical_csv_or_numeric_bytes(mutation: object) -> None:
    payload = _fixture_bytes("m2-feature-development-v1-golden.csv")

    with pytest.raises(ContractValidationError):
        load_feature_matrix(
            mutation(payload),  # type: ignore[operator]
            feature_definitions=_golden_descriptor()["feature_definitions"],
        )


def test_matrix_decoder_rejects_row_and_column_order_drift() -> None:
    definitions = _golden_descriptor()["feature_definitions"]
    row_a = "0" * 64 + ",0,0000000000000000\n"
    row_b = "1" * 64 + ",1,3ff0000000000000\n"

    with pytest.raises(ContractValidationError, match="ascending"):
        load_feature_matrix(
            ("candidate_id,count,score\n" + row_b + row_a).encode(),
            feature_definitions=definitions,
        )
    with pytest.raises(ContractValidationError, match="header"):
        load_feature_matrix(
            _fixture_bytes("m2-feature-development-v1-golden.csv").replace(
                b"candidate_id,count,score",
                b"candidate_id,score,count",
            ),
            feature_definitions=definitions,
        )


def test_descriptor_rejects_one_byte_order_version_and_inventory_drift() -> None:
    payload = _fixture_bytes("m2-feature-bundle-descriptor-v1-golden.json")
    with pytest.raises(ContractValidationError):
        load_feature_bundle_descriptor(payload[:-1])

    descriptor = json.loads(payload)
    descriptor["protocol_version"] = "legacy"
    with pytest.raises(ContractValidationError, match="protocol"):
        load_feature_bundle_descriptor(canonical_feature_json_bytes(descriptor))

    descriptor = json.loads(payload)
    descriptor["matrices"].reverse()
    with pytest.raises(ContractValidationError, match="role order"):
        load_feature_bundle_descriptor(canonical_feature_json_bytes(descriptor))

    descriptor = json.loads(payload)
    descriptor["matrices"].pop()
    with pytest.raises(ContractValidationError):
        load_feature_bundle_descriptor(canonical_feature_json_bytes(descriptor))


def test_descriptor_schema_reuses_one_object_key_definition() -> None:
    schema = json.loads(
        (ROOT / "schemas/m2-feature-bundle-descriptor-v1.schema.json").read_text(encoding="utf-8")
    )

    expected_reference = {"$ref": "#/$defs/objectKey"}
    assert schema["$defs"]["artifact"]["properties"]["object_key"] == expected_reference
    assert schema["$defs"]["featureConfig"]["properties"]["object_key"] == expected_reference


@pytest.mark.parametrize(
    ("object_key", "valid"),
    [
        ("features/a/nested.json", True),
        ("/features/a", False),
        ("features//a", False),
        ("features/a/", False),
        ("./a", False),
        ("../a", False),
        ("features/./a", False),
        ("features/a/.", False),
        ("features/../a", False),
        ("features/a/..", False),
    ],
)
def test_descriptor_object_key_schema_and_runtime_acceptance_match(
    object_key: str,
    valid: bool,
) -> None:
    schema = json.loads(
        (ROOT / "schemas/m2-feature-bundle-descriptor-v1.schema.json").read_text(encoding="utf-8")
    )
    descriptor = _golden_descriptor()
    descriptor["feature_config"]["object_key"] = object_key
    schema_accepts = Draft202012Validator(schema).is_valid(descriptor)

    if valid:
        load_feature_bundle_descriptor(canonical_feature_json_bytes(descriptor))
        assert schema_accepts
    else:
        with pytest.raises(ContractValidationError, match="object key"):
            load_feature_bundle_descriptor(canonical_feature_json_bytes(descriptor))
        assert not schema_accepts


def test_run_spec_dispatch_is_prospective_and_preserves_legacy_behavior() -> None:
    descriptor = _fixture_bytes("m2-feature-bundle-descriptor-v1-golden.json")
    legacy = _run_spec(protocol_version="synthetic-v1", feature_sha256="0" * 64)

    validate_run_spec_feature_identity(legacy, None)
    validate_run_spec_feature_identity(
        _run_spec(
            protocol_version="m2-alpha-ired-static-v1",
            feature_sha256=GOLDEN_ROOT,
        ),
        descriptor,
    )

    with pytest.raises(ContractValidationError, match="feature_sha256"):
        validate_run_spec_feature_identity(
            _run_spec(
                protocol_version="m2-alpha-ired-static-v1",
                feature_sha256="0" * 64,
            ),
            descriptor,
        )
    with pytest.raises(ContractValidationError, match="required"):
        validate_run_spec_feature_identity(
            _run_spec(
                protocol_version="m2-alpha-ired-static-v1",
                feature_sha256=GOLDEN_ROOT,
            ),
            None,
        )


def _task_bound_bundle() -> tuple[
    LoadedContract,
    bytes,
    dict[str, bytes],
    dict[str, list[str]],
]:
    logical = json.loads(_fixture_bytes("m2-feature-artifact-v1-valid.json").decode("utf-8"))
    config = {
        "schema_version": "m2-feature-config-v1",
        "feature_config_id": logical["feature_config"]["feature_config_id"],
        "derivations": [
            {
                "feature_id": definition["feature_id"],
                "derivation_id": definition["derivation"]["derivation_id"],
                "derivation_version": definition["derivation"]["derivation_version"],
                "parameters": [],
            }
            for definition in logical["feature_definitions"]
        ],
    }
    config_bytes = encode_feature_config(
        config,
        feature_definitions=logical["feature_definitions"],
    )
    config_key = "features/alpha-amylase/config.json"
    logical["feature_config"]["feature_config_sha256"] = hashlib.sha256(config_bytes).hexdigest()

    tensor_definitions = {
        "sequence-length-standardizer": [{"name": "mean", "dtype": "float64", "shape": []}]
    }
    state_metadata = logical["fitted_states"][0]
    state = {
        "schema_version": "m2-feature-fitted-state-json-v1",
        "fit_state_id": state_metadata["fit_state_id"],
        "state_version": state_metadata["state_version"],
        "derivation_id": state_metadata["derivation_id"],
        "fit_role": state_metadata["fit_role"],
        "input_identity": state_metadata["input_identity"],
        "tensors": [
            {
                "name": "mean",
                "dtype": "float64",
                "shape": [],
                "values": ["0000000000000000"],
            }
        ],
    }
    state_bytes = encode_feature_fitted_state(
        state,
        tensor_definitions=tensor_definitions[state_metadata["fit_state_id"]],
    )
    state_sha256 = hashlib.sha256(state_bytes).hexdigest()
    state_metadata["artifact"]["sha256"] = state_sha256
    state_metadata["artifact"]["size_bytes"] = len(state_bytes)
    logical["feature_definitions"][1]["derivation"]["fit_state"]["sha256"] = state_sha256
    for matrix in logical["matrices"]:
        matrix["fit_state_refs"][0]["sha256"] = state_sha256

    member_bytes = {config_key: config_bytes}
    member_bytes[state_metadata["artifact"]["object_key"]] = state_bytes
    memberships: dict[str, list[str]] = {}
    for role_index, matrix in enumerate(logical["matrices"]):
        candidate_ids = [
            hashlib.sha256(f"{role_index}:{index}".encode()).hexdigest()
            for index in range(matrix["row_count"])
        ]
        rows = [
            {
                "candidate_id": candidate_id,
                "sequence_length": index,
                "sequence_length_zscore": float(index),
            }
            for index, candidate_id in enumerate(candidate_ids)
        ]
        matrix_bytes = encode_feature_matrix(
            rows,
            feature_definitions=logical["feature_definitions"],
        )
        matrix["artifact"]["object_key"] = f"features/alpha-amylase/{matrix['role']}.csv"
        matrix["artifact"]["sha256"] = hashlib.sha256(matrix_bytes).hexdigest()
        matrix["artifact"]["size_bytes"] = len(matrix_bytes)
        member_bytes[matrix["artifact"]["object_key"]] = matrix_bytes
        memberships[matrix["role"]] = candidate_ids

    logical_artifact = load_feature_artifact(canonical_json_bytes(logical))
    descriptor_bytes = build_feature_bundle_descriptor(
        logical_artifact,
        feature_config_object_key=config_key,
        member_bytes=member_bytes,
        tensor_definitions=tensor_definitions,
        expected_role_memberships=memberships,
    )
    return logical_artifact, descriptor_bytes, member_bytes, memberships


def test_full_public_task_bound_fixture_passes_issue_44_and_47() -> None:
    logical, descriptor_bytes, member_bytes, memberships = _task_bound_bundle()
    descriptor = load_feature_bundle_descriptor(descriptor_bytes)
    run_spec = RunSpec(
        schema_version="1",
        protocol_version="m2-alpha-ired-static-v1",
        git_commit=logical.value["generator"]["git_commit"],
        environment_digest=logical.value["generator"]["environment_digest"],
        dataset_manifest_sha256=logical.value["dataset_manifest_sha256"],
        processing_manifest_sha256=logical.value["processing_manifest_sha256"],
        split_sha256=logical.value["split_sha256"],
        feature_sha256=descriptor.sha256,
        experiment_config_sha256="1" * 64,
        model_id="ridge",
        model_config_sha256="2" * 64,
        acquisition_id="greedy",
        acquisition_config_sha256="3" * 64,
        seed=0,
        initial_sample_size=1,
        batch_size=1,
        budget=1,
    )

    validated = validate_feature_bundle_identity(
        logical,
        descriptor_bytes=descriptor_bytes,
        member_bytes=member_bytes,
        expected_role_memberships=memberships,
        run_spec=run_spec,
    )

    assert validated.sha256 == descriptor.sha256


def test_full_bundle_rejects_member_and_inventory_drift() -> None:
    logical, descriptor_bytes, member_bytes, memberships = _task_bound_bundle()
    changed_members = dict(member_bytes)
    config_key = next(key for key in changed_members if key.endswith("/config.json"))
    changed_members[config_key] = changed_members[config_key][:-1] + b" "

    with pytest.raises(ContractValidationError):
        validate_feature_bundle_identity(
            logical,
            descriptor_bytes=descriptor_bytes,
            member_bytes=changed_members,
            expected_role_memberships=memberships,
        )

    extra_members = dict(member_bytes)
    extra_members["features/alpha-amylase/hidden-sidecar.bin"] = b"forbidden"
    with pytest.raises(ContractValidationError, match="inventory"):
        validate_feature_bundle_identity(
            logical,
            descriptor_bytes=descriptor_bytes,
            member_bytes=extra_members,
            expected_role_memberships=memberships,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("feature_config_id", "different-config"),
        ("sha256", "0" * 64),
        ("size_bytes", 1),
    ],
)
def test_full_bundle_rejects_descriptor_config_binding_drift(
    field: str,
    value: object,
) -> None:
    logical, descriptor_bytes, member_bytes, memberships = _task_bound_bundle()
    descriptor = json.loads(descriptor_bytes)
    descriptor["feature_config"][field] = value

    with pytest.raises(ContractValidationError):
        validate_feature_bundle_identity(
            logical,
            descriptor_bytes=canonical_feature_json_bytes(descriptor),
            member_bytes=member_bytes,
            expected_role_memberships=memberships,
        )
