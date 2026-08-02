"""Strict logical contract for M2 feature artifact bundles."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Collection, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from pcdb.contracts.experiment import ContractValidationError, LoadedContract

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_SCHEMA_VERSION = "m2-feature-artifact-v1"
_ROLES = ("development", "validation", "candidate-pool")
_ROLE_SET = frozenset(_ROLES)
_DTYPES = frozenset({"float32", "float64", "int32", "int64"})
_FORBIDDEN_NAME_PARTS = (
    "target",
    "label",
    "truth",
    "held_out",
    "holdout",
    "high_value",
    "highvalue",
    "boundary_score",
    "boundary_tie",
    "tie_count",
    "external_validation",
    "publisher",
    "model_output",
    "metric_result",
    "scientific_verdict",
    "membership",
    "role",
    "observation",
)

M2_FEATURE_TASK_BINDINGS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "alpha-amylase": MappingProxyType(
            {
                "dataset_task_id": "amylase",
                "dataset_manifest_sha256": (
                    "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac"
                ),
                "processing_manifest_sha256": (
                    "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec"
                ),
                "split_sha256": (
                    "c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a"
                ),
                "role_counts": MappingProxyType(
                    {
                        "development": 722,
                        "validation": 361,
                        "candidate-pool": 2262,
                    }
                ),
            }
        ),
        "ired": MappingProxyType(
            {
                "dataset_task_id": "ired",
                "dataset_manifest_sha256": (
                    "37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643"
                ),
                "processing_manifest_sha256": (
                    "929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba"
                ),
                "split_sha256": (
                    "9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e"
                ),
                "role_counts": MappingProxyType(
                    {
                        "development": 4448,
                        "validation": 1034,
                        "candidate-pool": 2070,
                    }
                ),
            }
        ),
    }
)

_TOP_LEVEL_FIELDS = {
    "benchmark_task_id",
    "contract_status",
    "dataset_manifest_sha256",
    "dataset_task_id",
    "feature_config",
    "feature_definitions",
    "fitted_states",
    "generator",
    "matrices",
    "observable_sources",
    "processing_manifest_sha256",
    "schema_version",
    "split_sha256",
}
_FEATURE_FIELDS = {"allowed_values", "derivation", "dtype", "feature_id", "shape"}
_ALLOWED_VALUE_FIELDS = {"boolean", "finite", "missing"}
_DERIVATION_FIELDS = {
    "derivation_id",
    "derivation_version",
    "fit_state",
    "source_fields",
}
_FIT_STATE_REF_FIELDS = {"fit_state_id", "sha256"}
_MATRIX_FIELDS = {
    "artifact",
    "candidate_id_field",
    "column_count",
    "feature_ids",
    "fit_state_refs",
    "role",
    "row_count",
}
_ARTIFACT_FIELDS = {"object_key", "sha256", "size_bytes"}
_FITTED_STATE_FIELDS = {
    "artifact",
    "derivation_id",
    "fit_role",
    "fit_state_id",
    "input_identity",
    "state_version",
}
_INPUT_IDENTITY_FIELDS = {
    "dataset_manifest_sha256",
    "processing_manifest_sha256",
    "role",
    "split_sha256",
}


def _reject_constant(value: str) -> None:
    raise ContractValidationError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractValidationError(f"duplicate object key is forbidden: {key}")
        value[key] = item
    return value


def _parse_json_bytes(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise ContractValidationError("empty feature artifact is forbidden")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ContractValidationError("UTF-8 BOM is forbidden")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractValidationError("feature artifact must be valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ContractValidationError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError("feature artifact must be strict JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("feature artifact must be a JSON object")
    return value


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _require_exact_keys(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ContractValidationError(f"{field} keys must be strings")
    actual = set(value)
    if actual != expected:
        raise ContractValidationError(
            f"{field} fields differ: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    return value


def _require_array(value: Any, field: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "a non-empty" if nonempty else "an"
        raise ContractValidationError(f"{field} must be {qualifier} array")
    return value


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ContractValidationError(f"{field} must be a canonical identifier")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"{field} must be a non-negative integer")
    return value


def _require_unique_strings(value: Any, field: str) -> list[str]:
    items = _require_array(value, field, nonempty=True)
    if any(not isinstance(item, str) or not item for item in items) or len(set(items)) != len(
        items
    ):
        raise ContractValidationError(f"{field} must contain unique non-empty strings")
    return items


def _reject_leakage_name(value: str, field: str) -> None:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold())
    if any(part in normalized for part in _FORBIDDEN_NAME_PARTS):
        raise ContractValidationError(f"{field} contains a forbidden target/truth-derived name")


def _validate_artifact(value: Any, field: str) -> dict[str, Any]:
    artifact = _require_exact_keys(value, _ARTIFACT_FIELDS, field)
    object_key = artifact["object_key"]
    if (
        not isinstance(object_key, str)
        or not object_key
        or object_key.startswith("/")
        or ".." in object_key.split("/")
    ):
        raise ContractValidationError(f"{field}.object_key must be a safe relative object key")
    _reject_leakage_name(object_key, f"{field}.object_key")
    _require_sha256(artifact["sha256"], f"{field}.sha256")
    if _require_nonnegative_int(artifact["size_bytes"], f"{field}.size_bytes") < 1:
        raise ContractValidationError(f"{field}.size_bytes must be positive")
    return artifact


def _validate_fit_state_ref(value: Any, field: str) -> tuple[str, str]:
    reference = _require_exact_keys(value, _FIT_STATE_REF_FIELDS, field)
    state_id = _require_identifier(reference["fit_state_id"], f"{field}.fit_state_id")
    _reject_leakage_name(state_id, f"{field}.fit_state_id")
    state_sha256 = _require_sha256(reference["sha256"], f"{field}.sha256")
    return state_id, state_sha256


def _validate_feature_definitions(value: Any) -> tuple[list[str], dict[str, str]]:
    definitions = _require_array(value, "feature_definitions", nonempty=True)
    feature_ids: list[str] = []
    required_states: dict[str, str] = {}
    for index, raw_definition in enumerate(definitions):
        field = f"feature_definitions[{index}]"
        definition = _require_exact_keys(raw_definition, _FEATURE_FIELDS, field)
        feature_id = _require_identifier(definition["feature_id"], f"{field}.feature_id")
        _reject_leakage_name(feature_id, f"{field}.feature_id")
        feature_ids.append(feature_id)
        if definition["dtype"] not in _DTYPES:
            raise ContractValidationError(f"{field}.dtype is not an allowed numeric dtype")
        if definition["shape"] != []:
            raise ContractValidationError(f"{field}.shape must describe a scalar")

        allowed_values = _require_exact_keys(
            definition["allowed_values"],
            _ALLOWED_VALUE_FIELDS,
            f"{field}.allowed_values",
        )
        if allowed_values != {"boolean": "forbid", "finite": True, "missing": "forbid"}:
            raise ContractValidationError(
                f"{field}.allowed_values must require finite, non-boolean, non-missing values"
            )

        derivation = _require_exact_keys(
            definition["derivation"],
            _DERIVATION_FIELDS,
            f"{field}.derivation",
        )
        derivation_id = _require_identifier(
            derivation["derivation_id"],
            f"{field}.derivation.derivation_id",
        )
        _reject_leakage_name(derivation_id, f"{field}.derivation.derivation_id")
        _require_identifier(
            derivation["derivation_version"],
            f"{field}.derivation.derivation_version",
        )
        source_fields = _require_unique_strings(
            derivation["source_fields"],
            f"{field}.derivation.source_fields",
        )
        if source_fields != ["sequence"]:
            raise ContractValidationError(
                f"{field}.derivation.source_fields must use only the observable sequence source"
            )
        fit_state = derivation["fit_state"]
        if fit_state is not None:
            state_id, state_sha256 = _validate_fit_state_ref(
                fit_state,
                f"{field}.derivation.fit_state",
            )
            if state_id in required_states and required_states[state_id] != state_sha256:
                raise ContractValidationError("fit-state references disagree on SHA-256")
            required_states[state_id] = state_sha256
    if len(set(feature_ids)) != len(feature_ids):
        raise ContractValidationError("feature definitions contain duplicate feature IDs")
    return feature_ids, required_states


def _validate_fitted_states(
    value: Any,
    *,
    artifact: dict[str, Any],
    required_states: Mapping[str, str],
) -> dict[str, str]:
    fitted_states = _require_array(value, "fitted_states")
    seen: dict[str, str] = {}
    for index, raw_state in enumerate(fitted_states):
        field = f"fitted_states[{index}]"
        state = _require_exact_keys(raw_state, _FITTED_STATE_FIELDS, field)
        state_id = _require_identifier(state["fit_state_id"], f"{field}.fit_state_id")
        if state_id in seen:
            raise ContractValidationError("fitted_states contains duplicate fit_state_id values")
        _require_identifier(state["state_version"], f"{field}.state_version")
        derivation_id = _require_identifier(state["derivation_id"], f"{field}.derivation_id")
        _reject_leakage_name(derivation_id, f"{field}.derivation_id")
        if state["fit_role"] != "development":
            raise ContractValidationError("fitted state may fit only the development role")

        input_identity = _require_exact_keys(
            state["input_identity"],
            _INPUT_IDENTITY_FIELDS,
            f"{field}.input_identity",
        )
        if input_identity["role"] != "development":
            raise ContractValidationError("fitted-state input identity must use development")
        for identity_field in (
            "dataset_manifest_sha256",
            "processing_manifest_sha256",
            "split_sha256",
        ):
            _require_sha256(
                input_identity[identity_field],
                f"{field}.input_identity.{identity_field}",
            )
            if input_identity[identity_field] != artifact[identity_field]:
                raise ContractValidationError(
                    f"{field}.input_identity.{identity_field} does not match the bundle"
                )

        state_artifact = _validate_artifact(state["artifact"], f"{field}.artifact")
        state_sha256 = state_artifact["sha256"]
        if required_states.get(state_id) != state_sha256:
            raise ContractValidationError(
                "fitted state does not resolve an exact feature reference"
            )
        seen[state_id] = state_sha256

    if seen != dict(required_states):
        raise ContractValidationError(
            "fitted_states must exact-cover all and only referenced learned state"
        )
    return seen


def _validate_matrices(
    value: Any,
    *,
    binding: Mapping[str, Any],
    feature_ids: Sequence[str],
    required_states: Mapping[str, str],
) -> None:
    matrices = _require_array(value, "matrices", nonempty=True)
    roles: list[str] = []
    expected_refs = dict(required_states)
    object_keys: set[str] = set()
    artifact_hashes: set[str] = set()
    common_feature_ids: list[str] | None = None
    for index, raw_matrix in enumerate(matrices):
        field = f"matrices[{index}]"
        matrix = _require_exact_keys(raw_matrix, _MATRIX_FIELDS, field)
        role = matrix["role"]
        if role not in _ROLE_SET:
            raise ContractValidationError(f"{field}.role is forbidden or unknown")
        roles.append(role)
        if matrix["candidate_id_field"] != "candidate_id":
            raise ContractValidationError(f"{field}.candidate_id_field must be candidate_id")
        row_count = _require_nonnegative_int(matrix["row_count"], f"{field}.row_count")
        column_count = _require_nonnegative_int(
            matrix["column_count"],
            f"{field}.column_count",
        )
        if row_count < 1 or row_count != binding["role_counts"][role]:
            raise ContractValidationError(f"{field}.row_count does not match the frozen role count")
        if column_count < 1 or column_count != len(feature_ids):
            raise ContractValidationError(
                f"{field}.column_count does not match feature definitions"
            )
        matrix_feature_ids = _require_unique_strings(
            matrix["feature_ids"],
            f"{field}.feature_ids",
        )
        if set(matrix_feature_ids) != set(feature_ids):
            raise ContractValidationError(
                f"{field}.feature_ids must exact-cover the feature definitions"
            )
        if common_feature_ids is None:
            common_feature_ids = matrix_feature_ids
        elif matrix_feature_ids != common_feature_ids:
            raise ContractValidationError(
                "all role matrices must declare one shared feature column order"
            )
        matrix_artifact = _validate_artifact(matrix["artifact"], f"{field}.artifact")
        object_key = matrix_artifact["object_key"]
        artifact_sha256 = matrix_artifact["sha256"]
        if object_key in object_keys or artifact_sha256 in artifact_hashes:
            raise ContractValidationError("role matrices must be separate artifact members")
        object_keys.add(object_key)
        artifact_hashes.add(artifact_sha256)

        matrix_refs: dict[str, str] = {}
        for ref_index, raw_reference in enumerate(
            _require_array(matrix["fit_state_refs"], f"{field}.fit_state_refs")
        ):
            state_id, state_sha256 = _validate_fit_state_ref(
                raw_reference,
                f"{field}.fit_state_refs[{ref_index}]",
            )
            if state_id in matrix_refs:
                raise ContractValidationError(f"{field}.fit_state_refs contains duplicates")
            matrix_refs[state_id] = state_sha256
        if matrix_refs != expected_refs:
            raise ContractValidationError(
                f"{field}.fit_state_refs must exact-bind every feature fitted state"
            )

    if len(roles) != len(_ROLES) or set(roles) != _ROLE_SET:
        raise ContractValidationError(
            "matrices must contain each required model-visible role exactly once"
        )


def _validate_v1(value: dict[str, Any]) -> None:
    _require_exact_keys(value, _TOP_LEVEL_FIELDS, "feature artifact")
    if value["schema_version"] != _SCHEMA_VERSION:
        raise ContractValidationError("feature artifact schema_version is invalid")
    if value["contract_status"] != "production":
        raise ContractValidationError("feature artifact contract_status must be production")

    benchmark_task_id = value["benchmark_task_id"]
    if benchmark_task_id not in M2_FEATURE_TASK_BINDINGS:
        raise ContractValidationError("feature artifact benchmark task is unknown")
    binding = M2_FEATURE_TASK_BINDINGS[benchmark_task_id]
    if value["dataset_task_id"] != binding["dataset_task_id"]:
        raise ContractValidationError("dataset and benchmark task mapping does not match")
    for field in (
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ):
        _require_sha256(value[field], field)
        if value[field] != binding[field]:
            raise ContractValidationError(f"{field} does not match the frozen task binding")

    feature_config = _require_exact_keys(
        value["feature_config"],
        {"feature_config_id", "feature_config_sha256"},
        "feature_config",
    )
    feature_config_id = _require_identifier(
        feature_config["feature_config_id"],
        "feature_config.feature_config_id",
    )
    _reject_leakage_name(feature_config_id, "feature_config.feature_config_id")
    _require_sha256(
        feature_config["feature_config_sha256"],
        "feature_config.feature_config_sha256",
    )

    generator = _require_exact_keys(
        value["generator"],
        {"environment_digest", "git_commit"},
        "generator",
    )
    if not isinstance(generator["git_commit"], str) or not _GIT_COMMIT.fullmatch(
        generator["git_commit"]
    ):
        raise ContractValidationError("generator.git_commit must be a full lowercase Git SHA")
    _require_sha256(generator["environment_digest"], "generator.environment_digest")

    if value["observable_sources"] != ["sequence"]:
        raise ContractValidationError(
            "observable_sources must contain only the accepted sequence source"
        )
    feature_ids, required_states = _validate_feature_definitions(value["feature_definitions"])
    resolved_states = _validate_fitted_states(
        value["fitted_states"],
        artifact=value,
        required_states=required_states,
    )
    _validate_matrices(
        value["matrices"],
        binding=binding,
        feature_ids=feature_ids,
        required_states=resolved_states,
    )


def load_feature_artifact(
    payload: bytes,
    *,
    expected_sha256: str | None = None,
) -> LoadedContract:
    """Strict-parse and domain-validate one logical feature artifact envelope.

    Issue #44 deliberately accepts semantically equivalent JSON formatting. Exact
    canonical member bytes and the final feature root identity belong to Issue #47.
    """

    value = _parse_json_bytes(payload)
    version = value.get("schema_version")
    if version != _SCHEMA_VERSION:
        raise ContractValidationError(f"unknown feature artifact schema_version: {version!r}")
    _validate_v1(value)
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractValidationError("feature artifact SHA-256 does not match")
    return LoadedContract(
        schema_version=version,
        value=_deep_freeze(value),
        canonical_bytes=payload,
        sha256=digest,
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_compatible(item) for item in value]
    return value


def _contract_value(artifact: LoadedContract | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(artifact, LoadedContract):
        if artifact.schema_version != _SCHEMA_VERSION:
            raise ContractValidationError("membership artifact must use feature artifact v1")
        raw_value: Any = artifact.value
    else:
        raw_value = artifact
    value = _json_compatible(raw_value)
    if not isinstance(value, dict):
        raise ContractValidationError("feature artifact value must be an object")
    _validate_v1(value)
    return value


def _candidate_id_set(values: Collection[str], field: str) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise ContractValidationError(f"{field} must be a candidate-ID collection")
    items = list(values)
    if any(not isinstance(item, str) or not item for item in items):
        raise ContractValidationError(f"{field} contains an invalid candidate ID")
    if len(items) != len(set(items)):
        raise ContractValidationError(f"{field} contains duplicate candidate IDs")
    return set(items)


def validate_feature_memberships(
    artifact: LoadedContract | Mapping[str, Any],
    *,
    matrix_candidate_ids: Mapping[str, Sequence[str]],
    expected_role_memberships: Mapping[str, Collection[str]],
) -> None:
    """Validate per-role exact coverage, uniqueness, counts, and cross-role isolation."""

    value = _contract_value(artifact)
    if set(matrix_candidate_ids) != _ROLE_SET or set(expected_role_memberships) != _ROLE_SET:
        raise ContractValidationError(
            "membership inputs must contain exactly the three model-visible roles"
        )
    matrices = value.get("matrices")
    if not isinstance(matrices, Sequence) or isinstance(matrices, (str, bytes)):
        raise ContractValidationError("feature artifact matrices are invalid")
    declared_counts: dict[str, int] = {}
    for matrix in matrices:
        if not isinstance(matrix, Mapping):
            raise ContractValidationError("feature artifact matrix must be an object")
        role = matrix.get("role")
        row_count = matrix.get("row_count")
        if role not in _ROLE_SET or role in declared_counts:
            raise ContractValidationError("feature artifact matrix roles are invalid")
        if isinstance(row_count, bool) or not isinstance(row_count, int):
            raise ContractValidationError("feature artifact matrix row_count is invalid")
        declared_counts[role] = row_count
    if set(declared_counts) != _ROLE_SET:
        raise ContractValidationError("feature artifact matrix roles are incomplete")
    benchmark_task_id = value.get("benchmark_task_id")
    if not isinstance(benchmark_task_id, str):
        raise ContractValidationError("feature artifact benchmark task is invalid")
    binding = M2_FEATURE_TASK_BINDINGS.get(benchmark_task_id)
    if binding is None:
        raise ContractValidationError("feature artifact benchmark task is unknown")
    if declared_counts != dict(binding["role_counts"]):
        raise ContractValidationError("feature artifact matrix counts drift from task binding")

    seen_by_role: dict[str, set[str]] = {}
    for role in _ROLES:
        actual = _candidate_id_set(
            matrix_candidate_ids[role],
            f"matrix_candidate_ids[{role}]",
        )
        expected = _candidate_id_set(
            expected_role_memberships[role],
            f"expected_role_memberships[{role}]",
        )
        if len(actual) != declared_counts[role]:
            raise ContractValidationError(
                f"{role} matrix candidate count does not match declared row_count"
            )
        if actual != expected:
            raise ContractValidationError(
                f"{role} matrix candidate IDs do not exact-cover Split membership"
            )
        seen_by_role[role] = actual

    for index, role in enumerate(_ROLES):
        for other_role in _ROLES[index + 1 :]:
            if seen_by_role[role] & seen_by_role[other_role]:
                raise ContractValidationError(
                    f"candidate IDs overlap across {role} and {other_role}"
                )


def _validate_feature_cell(value: Any, dtype: str, field: str) -> None:
    if value is None:
        raise ContractValidationError(f"{field} is missing")
    if isinstance(value, bool):
        raise ContractValidationError(f"{field} must not be boolean")
    if dtype in {"int32", "int64"}:
        if not isinstance(value, int):
            raise ContractValidationError(f"{field} must be an integer")
        minimum, maximum = (-(2**31), 2**31 - 1) if dtype == "int32" else (-(2**63), 2**63 - 1)
        if not minimum <= value <= maximum:
            raise ContractValidationError(f"{field} is outside the {dtype} range")
        return
    if not isinstance(value, (int, float)):
        raise ContractValidationError(f"{field} must be numeric")
    try:
        numeric_value = float(value)
        finite = math.isfinite(numeric_value)
        if dtype == "float32":
            as_float32 = struct.unpack("!f", struct.pack("!f", numeric_value))[0]
            finite = finite and math.isfinite(as_float32)
    except (OverflowError, struct.error):
        finite = False
    if not finite:
        raise ContractValidationError(f"{field} must be finite")


def validate_feature_matrix_rows(
    artifact: LoadedContract | Mapping[str, Any],
    *,
    role: str,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Validate decoded matrix headers, scalar cell domains, and row inventory.

    This validates logical decoded values only. It intentionally does not define
    member serialization, row ordering, or the final feature root hash.
    """

    value = _contract_value(artifact)
    if role not in _ROLE_SET:
        raise ContractValidationError("feature matrix role is forbidden or unknown")
    if isinstance(rows, (str, bytes)):
        raise ContractValidationError("feature matrix rows must be an array")

    matrix = next(item for item in value["matrices"] if item["role"] == role)
    if len(rows) != matrix["row_count"]:
        raise ContractValidationError("feature matrix row count does not match its inventory")
    definitions = {
        definition["feature_id"]: definition for definition in value["feature_definitions"]
    }
    expected_fields = {"candidate_id", *matrix["feature_ids"]}
    candidate_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ContractValidationError(f"feature matrix row {index} must be an object")
        if set(row) != expected_fields:
            raise ContractValidationError(
                f"feature matrix row {index} fields do not match the declared columns"
            )
        candidate_id = row["candidate_id"]
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ContractValidationError(f"feature matrix row {index} candidate_id is invalid")
        if candidate_id in candidate_ids:
            raise ContractValidationError("feature matrix rows contain duplicate candidate IDs")
        candidate_ids.add(candidate_id)
        for feature_id in matrix["feature_ids"]:
            definition = definitions[feature_id]
            _validate_feature_cell(
                row[feature_id],
                definition["dtype"],
                f"feature matrix row {index}.{feature_id}",
            )
