from __future__ import annotations

import hashlib
import json
import operator
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from pcdb.contracts.experiment import ContractValidationError
from pcdb.contracts.feature import (
    M2_FEATURE_TASK_BINDINGS,
    load_feature_artifact,
    validate_feature_matrix_rows,
    validate_feature_memberships,
)

ROOT = Path(__file__).parents[2]
SCHEMA_PATH = ROOT / "schemas/m2-feature-artifact-v1.schema.json"
FIXTURE_PATH = ROOT / "tests/fixtures/contracts/m2-feature-artifact-v1-valid.json"

ROLES = ("development", "validation", "candidate-pool")
ALPHA_COUNTS = {
    "development": 722,
    "validation": 361,
    "candidate-pool": 2262,
}
IRED_COUNTS = {
    "development": 4448,
    "validation": 1034,
    "candidate-pool": 2070,
}


def _fixture_value() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _payload(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load(value: Mapping[str, Any] | None = None) -> Any:
    return load_feature_artifact(FIXTURE_PATH.read_bytes() if value is None else _payload(value))


def _candidate_ids(role: str, count: int) -> list[str]:
    return [
        hashlib.sha256(f"synthetic:{role}:{index}".encode()).hexdigest() for index in range(count)
    ]


@pytest.fixture(scope="module")
def alpha_memberships() -> dict[str, frozenset[str]]:
    return {role: frozenset(_candidate_ids(role, count)) for role, count in ALPHA_COUNTS.items()}


def _matrix_candidate_ids(
    memberships: Mapping[str, Collection[str]],
) -> dict[str, list[str]]:
    return {role: sorted(candidate_ids) for role, candidate_ids in memberships.items()}


def _development_rows() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": candidate_id,
            "sequence_length": index + 1,
            "sequence_length_zscore": float(index) / 10,
        }
        for index, candidate_id in enumerate(
            _candidate_ids("development", ALPHA_COUNTS["development"])
        )
    ]


def _ired_value() -> dict[str, Any]:
    value = _fixture_value()
    binding = M2_FEATURE_TASK_BINDINGS["ired"]
    value["dataset_task_id"] = binding["dataset_task_id"]
    value["benchmark_task_id"] = "ired"
    for field in (
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ):
        value[field] = binding[field]
        value["fitted_states"][0]["input_identity"][field] = binding[field]
    for matrix in value["matrices"]:
        matrix["row_count"] = binding["role_counts"][matrix["role"]]
    return value


def test_production_task_bindings_freeze_exact_mapping_and_role_counts() -> None:
    assert M2_FEATURE_TASK_BINDINGS["alpha-amylase"]["dataset_task_id"] == "amylase"
    assert M2_FEATURE_TASK_BINDINGS["alpha-amylase"]["role_counts"] == ALPHA_COUNTS
    assert M2_FEATURE_TASK_BINDINGS["ired"]["dataset_task_id"] == "ired"
    assert M2_FEATURE_TASK_BINDINGS["ired"]["role_counts"] == IRED_COUNTS


def test_both_explicit_task_mappings_load() -> None:
    assert _load().value["benchmark_task_id"] == "alpha-amylase"
    assert _load(_ired_value()).value["benchmark_task_id"] == "ired"


def test_valid_fixture_matches_closed_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(_fixture_value())


def test_feature_schema_contains_no_duplicate_object_keys() -> None:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate schema key: {key}")
            value[key] = item
        return value

    json.loads(
        SCHEMA_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )


def test_valid_mixed_stateless_and_stateful_artifact_binds_all_required_roles(
    alpha_memberships: Mapping[str, Collection[str]],
) -> None:
    loaded = _load()

    assert loaded.schema_version == "m2-feature-artifact-v1"
    assert loaded.sha256 == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert tuple(matrix["role"] for matrix in loaded.value["matrices"]) == ROLES
    assert loaded.value["feature_definitions"][0]["derivation"]["fit_state"] is None
    assert loaded.value["fitted_states"][0]["fit_role"] == "development"

    validate_feature_memberships(
        loaded,
        matrix_candidate_ids=_matrix_candidate_ids(alpha_memberships),
        expected_role_memberships=alpha_memberships,
    )


def test_semantically_equivalent_json_formatting_is_accepted_by_issue_44() -> None:
    value = _fixture_value()
    compact = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    loaded = load_feature_artifact(compact)

    assert loaded.value["benchmark_task_id"] == "alpha-amylase"
    assert loaded.sha256 == hashlib.sha256(compact).hexdigest()


def test_issue_44_does_not_freeze_bundle_enumeration_or_feature_column_order() -> None:
    value = _fixture_value()
    value["matrices"].reverse()
    value["feature_definitions"].reverse()
    for matrix in value["matrices"]:
        matrix["feature_ids"].reverse()

    loaded = _load(value)

    assert {matrix["role"] for matrix in loaded.value["matrices"]} == set(ROLES)
    assert {definition["feature_id"] for definition in loaded.value["feature_definitions"]} == {
        "sequence_length",
        "sequence_length_zscore",
    }


def test_role_matrices_must_share_one_declared_feature_column_order() -> None:
    value = _fixture_value()
    value["matrices"][0]["feature_ids"].reverse()

    with pytest.raises(ContractValidationError, match="shared feature column order"):
        _load(value)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xef\xbb\xbf" + FIXTURE_PATH.read_bytes(), "BOM"),
        (b'{"schema_version":"m2-feature-artifact-v1","bad":"\\xff"}', "UTF-8"),
        (
            b'{"schema_version":"m2-feature-artifact-v1",'
            b'"schema_version":"m2-feature-artifact-v1"}',
            "duplicate",
        ),
    ],
)
def test_loader_rejects_bom_invalid_utf8_and_duplicate_keys(
    payload: bytes,
    message: str,
) -> None:
    if message == "UTF-8":
        payload = payload.replace(b"\\xff", b"\xff")
    with pytest.raises(ContractValidationError, match=message):
        load_feature_artifact(payload)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_loader_rejects_nonfinite_json_constants(constant: str) -> None:
    payload = FIXTURE_PATH.read_text(encoding="utf-8").replace(
        '"size_bytes": 128',
        f'"size_bytes": {constant}',
        1,
    )

    with pytest.raises(ContractValidationError, match="non-finite"):
        load_feature_artifact(payload.encode("utf-8"))


def test_loader_rejects_unknown_fields() -> None:
    value = _fixture_value()
    value["unexpected"] = "must-fail-closed"

    with pytest.raises(ContractValidationError):
        _load(value)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("matrices", 0, "artifact", "object_key"), "/absolute/features.json"),
        (("matrices", 0, "artifact", "object_key"), "features/../truth.json"),
        (("matrices", 0, "artifact", "size_bytes"), 0),
    ],
)
def test_artifact_references_require_safe_nonempty_exact_members(
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    value: Any = _fixture_value()
    target: Any = value
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = replacement
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert not Draft202012Validator(schema).is_valid(value)
    with pytest.raises(ContractValidationError, match="object key|size_bytes|positive"):
        _load(value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("boolean", "allow"),
        ("finite", False),
        ("missing", "allow"),
    ],
)
def test_numeric_features_freeze_boolean_nonfinite_and_missing_value_policy(
    field: str,
    replacement: object,
) -> None:
    value = _fixture_value()
    value["feature_definitions"][0]["allowed_values"][field] = replacement

    with pytest.raises(ContractValidationError, match="boolean|finite|missing|allowed"):
        _load(value)


def test_boolean_cannot_satisfy_numeric_inventory_fields() -> None:
    value = _fixture_value()
    value["matrices"][0]["row_count"] = True

    with pytest.raises(ContractValidationError):
        _load(value)


@pytest.mark.parametrize(
    ("dataset_task_id", "benchmark_task_id"),
    [
        ("ired", "alpha-amylase"),
        ("amylase", "ired"),
        ("alpha-amylase", "alpha-amylase"),
    ],
)
def test_task_mapping_is_explicit_and_fail_closed(
    dataset_task_id: str,
    benchmark_task_id: str,
) -> None:
    value = _fixture_value()
    value["dataset_task_id"] = dataset_task_id
    value["benchmark_task_id"] = benchmark_task_id

    with pytest.raises(ContractValidationError, match="task"):
        _load(value)


@pytest.mark.parametrize(
    "field",
    [
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ],
)
def test_task_manifest_bindings_fail_closed_on_hash_drift(field: str) -> None:
    value = _fixture_value()
    value[field] = "f" * 64

    with pytest.raises(ContractValidationError, match="manifest|task"):
        _load(value)


@pytest.mark.parametrize(
    ("role", "wrong_count"), [("development", 721), ("validation", 362), ("candidate-pool", 2261)]
)
def test_matrix_row_counts_exact_match_frozen_task_role_counts(
    role: str,
    wrong_count: int,
) -> None:
    value = _fixture_value()
    matrix = next(item for item in value["matrices"] if item["role"] == role)
    matrix["row_count"] = wrong_count

    with pytest.raises(ContractValidationError, match="count|row"):
        _load(value)


@pytest.mark.parametrize("role", ["held-out", "WT-holdout", "external-validation"])
def test_forbidden_roles_fail_both_schema_and_domain_validation(role: str) -> None:
    value = _fixture_value()
    value["matrices"][2]["role"] = role
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert not Draft202012Validator(schema).is_valid(value)
    with pytest.raises(ContractValidationError, match="role"):
        _load(value)


def test_all_three_roles_are_required_exactly_once() -> None:
    missing = _fixture_value()
    missing["matrices"].pop()
    with pytest.raises(ContractValidationError, match="role"):
        _load(missing)

    duplicate = _fixture_value()
    duplicate["matrices"][2]["role"] = "validation"
    with pytest.raises(ContractValidationError, match="role"):
        _load(duplicate)


def test_matrix_columns_exact_bind_declared_feature_definitions() -> None:
    unknown_feature = _fixture_value()
    unknown_feature["matrices"][0]["feature_ids"][1] = "undeclared_feature"
    with pytest.raises(ContractValidationError, match="feature"):
        _load(unknown_feature)

    wrong_column_count = _fixture_value()
    wrong_column_count["matrices"][0]["column_count"] = 1
    with pytest.raises(ContractValidationError, match="column"):
        _load(wrong_column_count)

    duplicate_definition = _fixture_value()
    duplicate_definition["feature_definitions"][1]["feature_id"] = "sequence_length"
    with pytest.raises(ContractValidationError, match="duplicate|feature"):
        _load(duplicate_definition)


@pytest.mark.parametrize("role", ROLES)
def test_each_role_must_exact_cover_split_membership(
    alpha_memberships: Mapping[str, Collection[str]],
    role: str,
) -> None:
    matrix_ids = _matrix_candidate_ids(alpha_memberships)
    matrix_ids[role][-1] = hashlib.sha256(f"synthetic:unknown:{role}".encode()).hexdigest()

    with pytest.raises(ContractValidationError, match="exact-cover"):
        validate_feature_memberships(
            _load(),
            matrix_candidate_ids=matrix_ids,
            expected_role_memberships=alpha_memberships,
        )


def test_each_role_rejects_duplicate_candidate_ids(
    alpha_memberships: Mapping[str, Collection[str]],
) -> None:
    matrix_ids = _matrix_candidate_ids(alpha_memberships)
    matrix_ids["development"][-1] = matrix_ids["development"][0]

    with pytest.raises(ContractValidationError, match="duplicate"):
        validate_feature_memberships(
            _load(),
            matrix_candidate_ids=matrix_ids,
            expected_role_memberships=alpha_memberships,
        )


def test_candidate_ids_cannot_overlap_across_roles(
    alpha_memberships: Mapping[str, Collection[str]],
) -> None:
    matrix_ids = _matrix_candidate_ids(alpha_memberships)
    overlap = matrix_ids["development"][0]
    original_validation_id = matrix_ids["validation"][0]
    matrix_ids["validation"][0] = overlap
    expected = {role: set(ids) for role, ids in alpha_memberships.items()}
    expected["validation"].remove(original_validation_id)
    expected["validation"].add(overlap)

    with pytest.raises(ContractValidationError, match="overlap"):
        validate_feature_memberships(
            _load(),
            matrix_candidate_ids=matrix_ids,
            expected_role_memberships=expected,
        )


@pytest.mark.parametrize("mapping_name", ["matrix", "expected"])
def test_membership_inputs_require_exactly_the_three_model_visible_roles(
    alpha_memberships: Mapping[str, Collection[str]],
    mapping_name: str,
) -> None:
    matrix_ids = _matrix_candidate_ids(alpha_memberships)
    expected: dict[str, Collection[str]] = dict(alpha_memberships)
    target = matrix_ids if mapping_name == "matrix" else expected
    target.pop("validation")

    with pytest.raises(ContractValidationError, match="three model-visible roles"):
        validate_feature_memberships(
            _load(),
            matrix_candidate_ids=matrix_ids,
            expected_role_memberships=expected,
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("feature_definitions", 0, "feature_id"), "target_scaled"),
        (("feature_definitions", 0, "feature_id"), "high_value_score"),
        (("feature_definitions", 0, "feature_id"), "high.value.score"),
        (("feature_definitions", 0, "feature_id"), "publisher.role"),
        (("feature_definitions", 0, "derivation", "source_fields"), ["target"]),
        (("feature_definitions", 0, "derivation", "source_fields"), ["validation_target"]),
        (("feature_definitions", 0, "derivation", "source_fields"), ["boundary_score"]),
        (("feature_definitions", 0, "derivation", "source_fields"), ["hidden_label"]),
        (("feature_definitions", 0, "derivation", "source_fields"), ["structure"]),
        (("observable_sources",), ["sequence", "high_value_membership"]),
    ],
)
def test_target_and_truth_derived_feature_inputs_fail_closed(
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    value: Any = _fixture_value()
    target: Any = value
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = replacement

    with pytest.raises(ContractValidationError, match="feature|source|observable|truth|target"):
        _load(value)


def test_stateless_derivations_require_explicit_null_fit_state() -> None:
    value = _fixture_value()
    value["feature_definitions"][0]["derivation"].pop("fit_state")
    with pytest.raises(ContractValidationError, match="fit_state|missing"):
        _load(value)

    hidden_state = _fixture_value()
    hidden_state["fitted_states"] = []
    with pytest.raises(ContractValidationError, match="fit|state"):
        _load(hidden_state)


def test_stateful_derivations_require_a_non_null_explicit_state_reference() -> None:
    value = _fixture_value()
    value["feature_definitions"][1]["derivation"]["fit_state"] = None

    with pytest.raises(ContractValidationError, match="fit|state"):
        _load(value)


def test_learned_state_is_explicit_and_development_only() -> None:
    _load()

    wrong_role = _fixture_value()
    wrong_role["fitted_states"][0]["fit_role"] = "validation"
    wrong_role["fitted_states"][0]["input_identity"]["role"] = "validation"
    with pytest.raises(ContractValidationError, match="development"):
        _load(wrong_role)

    missing_state = _fixture_value()
    missing_state["fitted_states"] = []
    with pytest.raises(ContractValidationError, match="fit|state"):
        _load(missing_state)


def test_fitted_state_derivation_identity_rejects_truth_derived_names() -> None:
    value = _fixture_value()
    value["fitted_states"][0]["derivation_id"] = "target-standardizer"

    with pytest.raises(ContractValidationError, match="target|truth|derivation"):
        _load(value)


def test_fitted_state_derivation_identity_exact_matches_feature_reference() -> None:
    value = _fixture_value()
    value["fitted_states"][0]["derivation_id"] = "unrelated-sequence-standardizer"

    with pytest.raises(ContractValidationError, match="derivation"):
        _load(value)


def test_one_fitted_state_cannot_be_claimed_by_conflicting_derivations() -> None:
    value = _fixture_value()
    conflicting_definition = cast(
        dict[str, Any],
        json.loads(json.dumps(value["feature_definitions"][1])),
    )
    conflicting_definition["feature_id"] = "sequence_length_zscore_copy"
    conflicting_definition["derivation"]["derivation_id"] = "different-sequence-standardizer"
    value["feature_definitions"].append(conflicting_definition)
    for matrix in value["matrices"]:
        matrix["feature_ids"].append("sequence_length_zscore_copy")
        matrix["column_count"] += 1

    with pytest.raises(ContractValidationError, match="derivation"):
        _load(value)


@pytest.mark.parametrize(
    "field",
    [
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ],
)
def test_fitted_state_input_identity_exact_matches_bundle(field: str) -> None:
    value = _fixture_value()
    value["fitted_states"][0]["input_identity"][field] = "9" * 64

    with pytest.raises(ContractValidationError, match="identity|manifest|state"):
        _load(value)


@pytest.mark.parametrize("role", ["validation", "candidate-pool"])
def test_non_development_matrices_can_only_reference_development_fitted_state(role: str) -> None:
    value = _fixture_value()
    matrix = next(item for item in value["matrices"] if item["role"] == role)
    matrix["fit_state_refs"] = []

    with pytest.raises(ContractValidationError, match="fit|state"):
        _load(value)


def test_all_transform_members_exact_bind_the_explicit_fitted_state() -> None:
    value = _fixture_value()
    value["matrices"][2]["fit_state_refs"][0]["sha256"] = "6" * 64

    with pytest.raises(ContractValidationError, match="fit|state"):
        _load(value)


def test_loaded_feature_artifact_is_deeply_immutable() -> None:
    loaded = _load()
    identity = (loaded.sha256, loaded.canonical_bytes)

    with pytest.raises(TypeError):
        operator.setitem(cast(Any, loaded.value), "benchmark_task_id", "ired")
    with pytest.raises(TypeError):
        operator.setitem(cast(Any, loaded.value["matrices"][0]), "row_count", 0)
    with pytest.raises(TypeError):
        operator.setitem(cast(Any, loaded.value["feature_definitions"]), 0, {})

    assert (loaded.sha256, loaded.canonical_bytes) == identity


def test_expected_sha256_is_checked_against_exact_input_bytes() -> None:
    payload = FIXTURE_PATH.read_bytes()
    load_feature_artifact(payload, expected_sha256=hashlib.sha256(payload).hexdigest())

    with pytest.raises(ContractValidationError, match="SHA-256"):
        load_feature_artifact(payload, expected_sha256="0" * 64)


def test_decoded_feature_matrix_rows_validate_numeric_policy_and_inventory() -> None:
    validate_feature_matrix_rows(
        _load(),
        role="development",
        rows=_development_rows(),
    )


@pytest.mark.parametrize(
    ("feature_id", "bad_value", "message"),
    [
        ("sequence_length", True, "boolean"),
        ("sequence_length", None, "missing"),
        ("sequence_length", 1.5, "integer"),
        ("sequence_length", 2**63, "range"),
        ("sequence_length_zscore", True, "boolean"),
        ("sequence_length_zscore", None, "missing"),
        ("sequence_length_zscore", float("nan"), "finite"),
        ("sequence_length_zscore", float("inf"), "finite"),
        ("sequence_length_zscore", "1.0", "numeric"),
    ],
)
def test_decoded_feature_matrix_rows_reject_invalid_cells(
    feature_id: str,
    bad_value: object,
    message: str,
) -> None:
    rows = _development_rows()
    rows[0][feature_id] = bad_value

    with pytest.raises(ContractValidationError, match=message):
        validate_feature_matrix_rows(_load(), role="development", rows=rows)


def test_decoded_feature_matrix_rows_reject_float32_overflow() -> None:
    value = _fixture_value()
    value["feature_definitions"][1]["dtype"] = "float32"
    rows = _development_rows()
    rows[0]["sequence_length_zscore"] = 1e39

    with pytest.raises(ContractValidationError, match="finite"):
        validate_feature_matrix_rows(_load(value), role="development", rows=rows)


def test_decoded_feature_matrix_rows_reject_missing_extra_and_target_columns() -> None:
    missing = _development_rows()
    missing[0].pop("sequence_length")
    with pytest.raises(ContractValidationError, match="fields"):
        validate_feature_matrix_rows(_load(), role="development", rows=missing)

    extra = _development_rows()
    extra[0]["target"] = 1.0
    with pytest.raises(ContractValidationError, match="fields"):
        validate_feature_matrix_rows(_load(), role="development", rows=extra)


def test_decoded_feature_matrix_rows_reject_count_duplicate_and_forbidden_role() -> None:
    short = _development_rows()[:-1]
    with pytest.raises(ContractValidationError, match="row count"):
        validate_feature_matrix_rows(_load(), role="development", rows=short)

    duplicate = _development_rows()
    duplicate[-1]["candidate_id"] = duplicate[0]["candidate_id"]
    with pytest.raises(ContractValidationError, match="duplicate"):
        validate_feature_matrix_rows(_load(), role="development", rows=duplicate)

    with pytest.raises(ContractValidationError, match="role"):
        validate_feature_matrix_rows(_load(), role="held-out", rows=[])
