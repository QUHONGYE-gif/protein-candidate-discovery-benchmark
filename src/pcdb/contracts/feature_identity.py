"""Canonical byte identity for complete M2 feature bundles.

The logical, task-bound feature contract remains owned by ``feature.py``.  This
module adds the prospective Issue #47 serialization profile and composes its
exact member bytes into one descriptor root.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from pcdb.contracts.experiment import ContractValidationError, LoadedContract
from pcdb.contracts.feature import (
    load_feature_artifact,
    validate_feature_matrix_rows,
    validate_feature_memberships,
)
from pcdb.contracts.run import RunSpec

PROTOCOL_VERSION = "m2-alpha-ired-static-v1"
DESCRIPTOR_SCHEMA_VERSION = "m2-feature-bundle-descriptor-v1"
DESCRIPTOR_FORMAT_VERSION = "pcdb-canonical-json-v1"
MATRIX_FORMAT_VERSION = "m2-feature-matrix-csv-v1"
FITTED_STATE_SCHEMA_VERSION = "m2-feature-fitted-state-json-v1"
FEATURE_CONFIG_SCHEMA_VERSION = "m2-feature-config-v1"
FEATURE_REGISTRY_VERSION = "m2-feature-registry-v1"
FEATURE_ARTIFACT_SCHEMA_VERSION = "m2-feature-artifact-v1"

_ROLES = ("development", "validation", "candidate-pool")
_ROLE_SET = frozenset(_ROLES)
_TYPED_DTYPES = frozenset({"bool", "int32", "int64", "float32", "float64", "utf8"})
_MATRIX_DTYPES = frozenset({"int32", "int64", "float32", "float64"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_INTEGER = re.compile(r"^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$")
_CANDIDATE_ID = _SHA256

_DESCRIPTOR_FIELDS = {
    "benchmark_task_id",
    "dataset_manifest_sha256",
    "dataset_task_id",
    "descriptor_format_version",
    "feature_artifact_contract_status",
    "feature_artifact_schema_version",
    "feature_config",
    "feature_definitions",
    "feature_registry_version",
    "fitted_state_format_version",
    "fitted_states",
    "generator",
    "matrices",
    "matrix_format_version",
    "observable_sources",
    "processing_manifest_sha256",
    "protocol_version",
    "schema_version",
    "split_sha256",
}
_FEATURE_CONFIG_FIELDS = {
    "feature_config_id",
    "object_key",
    "schema_version",
    "sha256",
    "size_bytes",
}
_FEATURE_DEFINITION_FIELDS = {
    "allowed_values",
    "derivation",
    "dtype",
    "feature_id",
    "shape",
}
_DERIVATION_FIELDS = {
    "derivation_id",
    "derivation_version",
    "fit_state",
    "source_fields",
}
_STATE_DESCRIPTOR_FIELDS = {
    "artifact",
    "derivation_id",
    "fit_role",
    "fit_state_id",
    "input_identity",
    "state_version",
    "tensor_definitions",
}
_MATRIX_DESCRIPTOR_FIELDS = {
    "artifact",
    "candidate_id_field",
    "column_count",
    "feature_ids",
    "fit_state_refs",
    "role",
    "row_count",
}
_ARTIFACT_FIELDS = {"object_key", "sha256", "size_bytes"}
_INPUT_IDENTITY_FIELDS = {
    "dataset_manifest_sha256",
    "processing_manifest_sha256",
    "role",
    "split_sha256",
}


@dataclass(frozen=True, slots=True)
class LoadedFeatureMatrix:
    """One strict-decoded canonical matrix member."""

    feature_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]
    canonical_bytes: bytes
    sha256: str


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_compatible(item) for item in value]
    return value


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _require_nfc_string(value: str, field: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ContractValidationError(f"{field} contains an unpaired surrogate")
    if unicodedata.normalize("NFC", value) != value:
        raise ContractValidationError(f"{field} must be Unicode NFC")


def _validate_canonical_json_domain(value: Any, field: str = "$") -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if value < 0:
            raise ContractValidationError(f"{field} contains a negative structural integer")
        return
    if isinstance(value, float):
        raise ContractValidationError(f"{field} contains a JSON floating-point number")
    if isinstance(value, str):
        _require_nfc_string(value, field)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise ContractValidationError(f"{field} object keys must be ASCII strings")
            _require_nfc_string(key, f"{field} key")
            _validate_canonical_json_domain(item, f"{field}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_canonical_json_domain(item, f"{field}[{index}]")
        return
    raise ContractValidationError(f"{field} is not a closed canonical JSON value")


def canonical_feature_json_bytes(value: Any) -> bytes:
    """Encode ``pcdb-canonical-json-v1`` bytes.

    Array order remains semantic; callers for versioned members normalize their
    explicitly sortable inventories before invoking this encoder.
    """

    compatible = _json_compatible(value)
    _validate_canonical_json_domain(compatible)
    try:
        text = json.dumps(
            compatible,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        return (text + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ContractValidationError("value cannot be encoded as canonical JSON") from exc


def _reject_constant(value: str) -> None:
    raise ContractValidationError(f"non-finite JSON constant is forbidden: {value}")


def _reject_float(value: str) -> None:
    raise ContractValidationError(f"JSON floating-point numbers are forbidden: {value}")


def _parse_nonnegative_int(value: str) -> int:
    if value.startswith("-"):
        raise ContractValidationError("negative structural integers are forbidden")
    return int(value)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"duplicate object key is forbidden: {key}")
        result[key] = value
    return result


def _parse_canonical_json_bytes(payload: bytes, field: str) -> dict[str, Any]:
    if not payload:
        raise ContractValidationError(f"{field} must not be empty")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ContractValidationError(f"{field} must not contain a UTF-8 BOM")
    if b"\r" in payload:
        raise ContractValidationError(f"{field} must use LF line endings")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractValidationError(f"{field} must be valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
            parse_int=_parse_nonnegative_int,
        )
    except ContractValidationError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError(f"{field} must be strict JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be a JSON object")
    _validate_canonical_json_domain(value)
    if canonical_feature_json_bytes(value) != payload:
        raise ContractValidationError(f"{field} bytes are not canonical")
    return value


def _require_exact_keys(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be an object")
    actual = set(value)
    if actual != expected:
        raise ContractValidationError(
            f"{field} fields differ: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    return value


def _require_array(value: Any, field: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise ContractValidationError(f"{field} must be a {qualifier}array")
    return value


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ContractValidationError(f"{field} must be a stable ASCII identifier")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractValidationError(f"{field} must be a positive integer")
    return value


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"{field} must be a non-negative integer")
    return value


def _require_safe_object_key(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ContractValidationError(f"{field} must be an immutable relative object key")
    return value


def _require_ascii_sorted_unique(
    values: Sequence[str],
    field: str,
    *,
    nonempty: bool = True,
) -> None:
    if (nonempty and not values) or len(values) != len(set(values)):
        raise ContractValidationError(f"{field} must contain unique values")
    if list(values) != sorted(values, key=lambda item: item.encode("ascii")):
        raise ContractValidationError(f"{field} must be ordered by ascending ASCII")


def _shape_product(shape: list[int]) -> int:
    product = 1
    for dimension in shape:
        product *= dimension
    return product


def _validate_shape(value: Any, field: str) -> list[int]:
    shape = _require_array(value, field)
    for index, dimension in enumerate(shape):
        _require_nonnegative_int(dimension, f"{field}[{index}]")
    return shape


def _validate_float_lexeme(value: Any, dtype: str, field: str) -> None:
    length = 8 if dtype == "float32" else 16
    if not isinstance(value, str) or len(value) != length or not re.fullmatch(r"[0-9a-f]+", value):
        raise ContractValidationError(f"{field} must be exact lower-case big-endian {dtype} hex")
    raw = bytes.fromhex(value)
    number = struct.unpack("!f" if dtype == "float32" else "!d", raw)[0]
    if not math.isfinite(number):
        raise ContractValidationError(f"{field} must encode a finite {dtype}")


def _validate_typed_values(
    *,
    dtype: Any,
    shape: Any,
    values: Any,
    field: str,
) -> None:
    if dtype not in _TYPED_DTYPES:
        raise ContractValidationError(f"{field}.dtype is unknown")
    parsed_shape = _validate_shape(shape, f"{field}.shape")
    parsed_values = _require_array(values, f"{field}.values")
    if len(parsed_values) != _shape_product(parsed_shape):
        raise ContractValidationError(f"{field}.values length does not match row-major shape")
    for index, value in enumerate(parsed_values):
        value_field = f"{field}.values[{index}]"
        if dtype == "bool":
            if not isinstance(value, bool):
                raise ContractValidationError(f"{value_field} must be boolean")
        elif dtype == "utf8":
            if not isinstance(value, str):
                raise ContractValidationError(f"{value_field} must be a string")
            _require_nfc_string(value, value_field)
        elif dtype in {"int32", "int64"}:
            if not isinstance(value, str) or not _INTEGER.fullmatch(value):
                raise ContractValidationError(
                    f"{value_field} must be a canonical base-10 integer string"
                )
            number = int(value)
            bits = 32 if dtype == "int32" else 64
            if not -(2 ** (bits - 1)) <= number <= 2 ** (bits - 1) - 1:
                raise ContractValidationError(f"{value_field} is outside {dtype} range")
        else:
            _validate_float_lexeme(value, dtype, value_field)


def _validate_named_typed_values(
    value: Any,
    field: str,
    *,
    require_nonempty: bool,
) -> list[dict[str, Any]]:
    items = _require_array(value, field, nonempty=require_nonempty)
    names: list[str] = []
    for index, raw_item in enumerate(items):
        item_field = f"{field}[{index}]"
        item = _require_exact_keys(
            raw_item,
            {"dtype", "name", "shape", "values"},
            item_field,
        )
        names.append(_require_identifier(item["name"], f"{item_field}.name"))
        _validate_typed_values(
            dtype=item["dtype"],
            shape=item["shape"],
            values=item["values"],
            field=item_field,
        )
    _require_ascii_sorted_unique(names, field, nonempty=require_nonempty)
    return items


def _feature_definition_map(
    feature_definitions: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    definitions = [_json_compatible(item) for item in feature_definitions]
    result: dict[str, dict[str, Any]] = {}
    for index, raw_definition in enumerate(definitions):
        field = f"feature_definitions[{index}]"
        definition = _require_exact_keys(
            raw_definition,
            _FEATURE_DEFINITION_FIELDS,
            field,
        )
        feature_id = _require_identifier(definition["feature_id"], f"{field}.feature_id")
        if feature_id in result:
            raise ContractValidationError("feature definitions contain duplicate feature IDs")
        if definition["dtype"] not in _MATRIX_DTYPES:
            raise ContractValidationError(f"{field}.dtype is not a supported matrix dtype")
        if definition["shape"] != []:
            raise ContractValidationError(f"{field}.shape must be scalar")
        if definition["allowed_values"] != {
            "boolean": "forbid",
            "finite": True,
            "missing": "forbid",
        }:
            raise ContractValidationError(f"{field}.allowed_values is not frozen")
        derivation = _require_exact_keys(
            definition["derivation"],
            _DERIVATION_FIELDS,
            f"{field}.derivation",
        )
        _require_identifier(
            derivation["derivation_id"],
            f"{field}.derivation.derivation_id",
        )
        _require_identifier(
            derivation["derivation_version"],
            f"{field}.derivation.derivation_version",
        )
        source_fields = _require_array(
            derivation["source_fields"],
            f"{field}.derivation.source_fields",
            nonempty=True,
        )
        if any(not isinstance(source, str) or not source.isascii() for source in source_fields):
            raise ContractValidationError(f"{field}.derivation.source_fields is invalid")
        _require_ascii_sorted_unique(
            source_fields,
            f"{field}.derivation.source_fields",
        )
        fit_state = derivation["fit_state"]
        if fit_state is not None:
            reference = _require_exact_keys(
                fit_state,
                {"fit_state_id", "sha256"},
                f"{field}.derivation.fit_state",
            )
            _require_identifier(
                reference["fit_state_id"],
                f"{field}.derivation.fit_state.fit_state_id",
            )
            _require_sha256(
                reference["sha256"],
                f"{field}.derivation.fit_state.sha256",
            )
        result[feature_id] = definition
    if not result:
        raise ContractValidationError("feature definitions must be non-empty")
    return result


def _mapping_ascii_sort_key(value: Any, field: str) -> bytes:
    if not isinstance(value, Mapping):
        return b""
    item = value.get(field)
    if not isinstance(item, str) or not item.isascii():
        return b""
    return item.encode("ascii")


def _normalize_config(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _json_compatible(value)
    if not isinstance(normalized, dict):
        raise ContractValidationError("feature config must be an object")
    derivations = normalized.get("derivations")
    if isinstance(derivations, list):
        for derivation in derivations:
            if isinstance(derivation, dict) and isinstance(derivation.get("parameters"), list):
                derivation["parameters"].sort(
                    key=lambda item: _mapping_ascii_sort_key(item, "name")
                )
        derivations.sort(key=lambda item: _mapping_ascii_sort_key(item, "feature_id"))
    return normalized


def _validate_feature_config(
    value: dict[str, Any],
    *,
    feature_definitions: Sequence[Mapping[str, Any]] | None,
) -> None:
    _require_exact_keys(
        value,
        {"derivations", "feature_config_id", "schema_version"},
        "feature config",
    )
    if value["schema_version"] != FEATURE_CONFIG_SCHEMA_VERSION:
        raise ContractValidationError("feature config schema_version is invalid")
    _require_identifier(value["feature_config_id"], "feature_config.feature_config_id")
    derivations = _require_array(
        value["derivations"],
        "feature_config.derivations",
        nonempty=True,
    )
    feature_ids: list[str] = []
    definition_map = (
        _feature_definition_map(feature_definitions) if feature_definitions is not None else None
    )
    for index, raw_derivation in enumerate(derivations):
        field = f"feature_config.derivations[{index}]"
        derivation = _require_exact_keys(
            raw_derivation,
            {
                "derivation_id",
                "derivation_version",
                "feature_id",
                "parameters",
            },
            field,
        )
        feature_id = _require_identifier(derivation["feature_id"], f"{field}.feature_id")
        feature_ids.append(feature_id)
        derivation_id = _require_identifier(
            derivation["derivation_id"],
            f"{field}.derivation_id",
        )
        derivation_version = _require_identifier(
            derivation["derivation_version"],
            f"{field}.derivation_version",
        )
        _validate_named_typed_values(
            derivation["parameters"],
            f"{field}.parameters",
            require_nonempty=False,
        )
        if definition_map is not None and feature_id in definition_map:
            expected = definition_map[feature_id]["derivation"]
            if (
                derivation_id != expected["derivation_id"]
                or derivation_version != expected["derivation_version"]
            ):
                raise ContractValidationError(f"{field} does not match its feature definition")
    _require_ascii_sorted_unique(feature_ids, "feature_config.derivations")
    if definition_map is not None and set(feature_ids) != set(definition_map):
        raise ContractValidationError(
            "feature config derivations must exact-cover feature definitions"
        )


def encode_feature_config(
    value: Mapping[str, Any],
    *,
    feature_definitions: Sequence[Mapping[str, Any]] | None = None,
) -> bytes:
    """Validate, normalize ordered inventories, and encode a feature config."""

    normalized = _normalize_config(value)
    _validate_feature_config(normalized, feature_definitions=feature_definitions)
    return canonical_feature_json_bytes(normalized)


def load_feature_config(
    payload: bytes,
    *,
    feature_definitions: Sequence[Mapping[str, Any]] | None = None,
    expected_sha256: str | None = None,
) -> LoadedContract:
    """Strict-decode one persisted canonical feature-config member."""

    value = _parse_canonical_json_bytes(payload, "feature config")
    _validate_feature_config(value, feature_definitions=feature_definitions)
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractValidationError("feature config SHA-256 does not match")
    return LoadedContract(
        schema_version=FEATURE_CONFIG_SCHEMA_VERSION,
        value=_deep_freeze(value),
        canonical_bytes=payload,
        sha256=digest,
    )


def _normalize_state(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _json_compatible(value)
    if not isinstance(normalized, dict):
        raise ContractValidationError("fitted state must be an object")
    tensors = normalized.get("tensors")
    if isinstance(tensors, list):
        tensors.sort(key=lambda item: _mapping_ascii_sort_key(item, "name"))
    return normalized


def _validate_tensor_definitions(value: Any) -> list[dict[str, Any]]:
    definitions = _require_array(value, "tensor_definitions", nonempty=True)
    names: list[str] = []
    for index, raw_definition in enumerate(definitions):
        field = f"tensor_definitions[{index}]"
        definition = _require_exact_keys(
            raw_definition,
            {"dtype", "name", "shape"},
            field,
        )
        names.append(_require_identifier(definition["name"], f"{field}.name"))
        if definition["dtype"] not in _TYPED_DTYPES:
            raise ContractValidationError(f"{field}.dtype is unknown")
        _validate_shape(definition["shape"], f"{field}.shape")
    _require_ascii_sorted_unique(names, "tensor_definitions")
    return definitions


def _validate_fitted_state(
    value: dict[str, Any],
    *,
    tensor_definitions: Sequence[Mapping[str, Any]],
    expected_fit_state_id: str | None,
    expected_derivation_id: str | None,
    expected_state_version: str | None,
    expected_input_identity: Mapping[str, Any] | None,
) -> None:
    _require_exact_keys(
        value,
        {
            "derivation_id",
            "fit_role",
            "fit_state_id",
            "input_identity",
            "schema_version",
            "state_version",
            "tensors",
        },
        "fitted state",
    )
    if value["schema_version"] != FITTED_STATE_SCHEMA_VERSION:
        raise ContractValidationError("fitted state schema_version is invalid")
    state_id = _require_identifier(value["fit_state_id"], "fitted_state.fit_state_id")
    derivation_id = _require_identifier(
        value["derivation_id"],
        "fitted_state.derivation_id",
    )
    state_version = _require_identifier(
        value["state_version"],
        "fitted_state.state_version",
    )
    if value["fit_role"] != "development":
        raise ContractValidationError("fitted state fit_role must be development")
    input_identity = _require_exact_keys(
        value["input_identity"],
        _INPUT_IDENTITY_FIELDS,
        "fitted_state.input_identity",
    )
    if input_identity["role"] != "development":
        raise ContractValidationError("fitted state input identity role must be development")
    for name in (
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ):
        _require_sha256(input_identity[name], f"fitted_state.input_identity.{name}")
    tensors = _validate_named_typed_values(
        value["tensors"],
        "fitted_state.tensors",
        require_nonempty=True,
    )
    definitions = _validate_tensor_definitions(
        [_json_compatible(item) for item in tensor_definitions]
    )
    tensor_specs = {item["name"]: (item["dtype"], item["shape"]) for item in tensors}
    definition_specs = {item["name"]: (item["dtype"], item["shape"]) for item in definitions}
    if tensor_specs != definition_specs:
        raise ContractValidationError(
            "fitted state tensors must exact-cover root-bound tensor_definitions"
        )
    expected_values = (
        (state_id, expected_fit_state_id, "fit_state_id"),
        (derivation_id, expected_derivation_id, "derivation_id"),
        (state_version, expected_state_version, "state_version"),
    )
    for actual, expected, field in expected_values:
        if expected is not None and actual != expected:
            raise ContractValidationError(f"fitted state {field} does not match")
    if expected_input_identity is not None and input_identity != _json_compatible(
        expected_input_identity
    ):
        raise ContractValidationError("fitted state input identity does not match")


def encode_feature_fitted_state(
    value: Mapping[str, Any],
    *,
    tensor_definitions: Sequence[Mapping[str, Any]],
) -> bytes:
    """Validate, normalize tensor order, and encode one fitted state."""

    normalized = _normalize_state(value)
    _validate_fitted_state(
        normalized,
        tensor_definitions=tensor_definitions,
        expected_fit_state_id=None,
        expected_derivation_id=None,
        expected_state_version=None,
        expected_input_identity=None,
    )
    return canonical_feature_json_bytes(normalized)


def load_feature_fitted_state(
    payload: bytes,
    *,
    tensor_definitions: Sequence[Mapping[str, Any]],
    expected_fit_state_id: str | None = None,
    expected_derivation_id: str | None = None,
    expected_state_version: str | None = None,
    expected_input_identity: Mapping[str, Any] | None = None,
    expected_sha256: str | None = None,
) -> LoadedContract:
    """Strict-decode one fitted-state member against its root-bound inventory."""

    value = _parse_canonical_json_bytes(payload, "fitted state")
    _validate_fitted_state(
        value,
        tensor_definitions=tensor_definitions,
        expected_fit_state_id=expected_fit_state_id,
        expected_derivation_id=expected_derivation_id,
        expected_state_version=expected_state_version,
        expected_input_identity=expected_input_identity,
    )
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractValidationError("fitted state SHA-256 does not match")
    return LoadedContract(
        schema_version=FITTED_STATE_SCHEMA_VERSION,
        value=_deep_freeze(value),
        canonical_bytes=payload,
        sha256=digest,
    )


def _decode_matrix_cell(value: str, dtype: str, field: str) -> int | float:
    if dtype in {"int32", "int64"}:
        if not _INTEGER.fullmatch(value) or value == "-0":
            raise ContractValidationError(f"{field} is not a canonical integer cell")
        number = int(value)
        bits = 32 if dtype == "int32" else 64
        if not -(2 ** (bits - 1)) <= number <= 2 ** (bits - 1) - 1:
            raise ContractValidationError(f"{field} is outside {dtype} range")
        return number
    _validate_float_lexeme(value, dtype, field)
    return cast(
        float,
        struct.unpack(
            "!f" if dtype == "float32" else "!d",
            bytes.fromhex(value),
        )[0],
    )


def _encode_matrix_cell(value: Any, dtype: str, field: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ContractValidationError(f"{field} must be a finite non-boolean value")
    if dtype in {"int32", "int64"}:
        if not isinstance(value, int):
            raise ContractValidationError(f"{field} must be an integer")
        bits = 32 if dtype == "int32" else 64
        if not -(2 ** (bits - 1)) <= value <= 2 ** (bits - 1) - 1:
            raise ContractValidationError(f"{field} is outside {dtype} range")
        return str(value)
    if not isinstance(value, (int, float)):
        raise ContractValidationError(f"{field} must be numeric")
    try:
        numeric = float(value)
        raw = struct.pack("!f" if dtype == "float32" else "!d", numeric)
        decoded = struct.unpack("!f" if dtype == "float32" else "!d", raw)[0]
    except (OverflowError, struct.error) as exc:
        raise ContractValidationError(f"{field} cannot be encoded as {dtype}") from exc
    if not math.isfinite(numeric) or not math.isfinite(decoded):
        raise ContractValidationError(f"{field} must be finite")
    return raw.hex()


def encode_feature_matrix(
    rows: Sequence[Mapping[str, Any]],
    *,
    feature_definitions: Sequence[Mapping[str, Any]],
) -> bytes:
    """Encode rows as deterministic ``m2-feature-matrix-csv-v1`` bytes."""

    if isinstance(rows, (str, bytes, bytearray)):
        raise ContractValidationError("matrix rows must be a sequence of mappings")
    definitions = _feature_definition_map(feature_definitions)
    feature_ids = sorted(definitions, key=lambda item: item.encode("ascii"))
    expected_fields = {"candidate_id", *feature_ids}
    normalized_rows: list[Mapping[str, Any]] = []
    candidate_ids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ContractValidationError(
                f"matrix row {index} fields do not exact-cover the header"
            )
        candidate_id = row["candidate_id"]
        if not isinstance(candidate_id, str) or not _CANDIDATE_ID.fullmatch(candidate_id):
            raise ContractValidationError(
                f"matrix row {index}.candidate_id must be lower-case 64-hex"
            )
        candidate_ids.append(candidate_id)
        normalized_rows.append(row)
    if not normalized_rows:
        raise ContractValidationError("feature matrix must contain at least one data row")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ContractValidationError("matrix rows contain duplicate candidate IDs")
    normalized_rows.sort(key=lambda row: row["candidate_id"].encode("ascii"))
    lines = [",".join(("candidate_id", *feature_ids))]
    for row_index, row in enumerate(normalized_rows):
        cells = [row["candidate_id"]]
        for feature_id in feature_ids:
            cells.append(
                _encode_matrix_cell(
                    row[feature_id],
                    definitions[feature_id]["dtype"],
                    f"matrix row {row_index}.{feature_id}",
                )
            )
        lines.append(",".join(cells))
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_feature_matrix(
    payload: bytes,
    *,
    feature_definitions: Sequence[Mapping[str, Any]],
    expected_sha256: str | None = None,
) -> LoadedFeatureMatrix:
    """Strict-decode one canonical feature matrix member."""

    if not payload:
        raise ContractValidationError("feature matrix must not be empty")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ContractValidationError("feature matrix must not contain a UTF-8 BOM")
    if b"\r" in payload or not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise ContractValidationError("feature matrix must use exactly one LF after every row")
    if b'"' in payload or b"\\" in payload:
        raise ContractValidationError("feature matrix quoting and escapes are forbidden")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractValidationError("feature matrix must be valid UTF-8") from exc
    lines = text[:-1].split("\n")
    if len(lines) < 2 or any(not line for line in lines):
        raise ContractValidationError(
            "feature matrix must contain a header and non-empty data rows"
        )
    definitions = _feature_definition_map(feature_definitions)
    feature_ids = sorted(definitions, key=lambda item: item.encode("ascii"))
    expected_header = ",".join(("candidate_id", *feature_ids))
    if lines[0] != expected_header:
        raise ContractValidationError(
            "feature matrix header must exact-cover features in ascending ASCII order"
        )
    rows: list[Mapping[str, Any]] = []
    candidate_ids: list[str] = []
    for index, line in enumerate(lines[1:]):
        cells = line.split(",")
        if len(cells) != len(feature_ids) + 1:
            raise ContractValidationError(f"feature matrix row {index} field count differs")
        candidate_id = cells[0]
        if not _CANDIDATE_ID.fullmatch(candidate_id):
            raise ContractValidationError(
                f"feature matrix row {index}.candidate_id must be lower-case 64-hex"
            )
        candidate_ids.append(candidate_id)
        row: dict[str, Any] = {"candidate_id": candidate_id}
        for feature_id, cell in zip(feature_ids, cells[1:], strict=True):
            row[feature_id] = _decode_matrix_cell(
                cell,
                definitions[feature_id]["dtype"],
                f"feature matrix row {index}.{feature_id}",
            )
        rows.append(MappingProxyType(row))
    _require_ascii_sorted_unique(candidate_ids, "feature matrix candidate IDs")
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractValidationError("feature matrix SHA-256 does not match")
    return LoadedFeatureMatrix(
        feature_ids=tuple(feature_ids),
        candidate_ids=tuple(candidate_ids),
        rows=tuple(rows),
        canonical_bytes=payload,
        sha256=digest,
    )


def _validate_artifact_entry(value: Any, field: str) -> dict[str, Any]:
    artifact = _require_exact_keys(value, _ARTIFACT_FIELDS, field)
    _require_safe_object_key(artifact["object_key"], f"{field}.object_key")
    _require_sha256(artifact["sha256"], f"{field}.sha256")
    _require_positive_int(artifact["size_bytes"], f"{field}.size_bytes")
    return artifact


def _validate_input_identity(
    value: Any,
    *,
    descriptor: Mapping[str, Any],
    field: str,
) -> dict[str, Any]:
    identity = _require_exact_keys(value, _INPUT_IDENTITY_FIELDS, field)
    if identity["role"] != "development":
        raise ContractValidationError(f"{field}.role must be development")
    for name in (
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ):
        _require_sha256(identity[name], f"{field}.{name}")
        if identity[name] != descriptor[name]:
            raise ContractValidationError(f"{field}.{name} does not match descriptor")
    return identity


def _validate_descriptor(value: dict[str, Any]) -> None:
    _require_exact_keys(value, _DESCRIPTOR_FIELDS, "feature bundle descriptor")
    exact_versions = {
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "descriptor_format_version": DESCRIPTOR_FORMAT_VERSION,
        "matrix_format_version": MATRIX_FORMAT_VERSION,
        "fitted_state_format_version": FITTED_STATE_SCHEMA_VERSION,
        "feature_artifact_schema_version": FEATURE_ARTIFACT_SCHEMA_VERSION,
        "feature_artifact_contract_status": "production",
        "feature_registry_version": FEATURE_REGISTRY_VERSION,
        "protocol_version": PROTOCOL_VERSION,
    }
    for field, expected in exact_versions.items():
        if value[field] != expected:
            raise ContractValidationError(f"descriptor {field} is invalid")
    _require_identifier(value["dataset_task_id"], "descriptor.dataset_task_id")
    _require_identifier(value["benchmark_task_id"], "descriptor.benchmark_task_id")
    for field in (
        "dataset_manifest_sha256",
        "processing_manifest_sha256",
        "split_sha256",
    ):
        _require_sha256(value[field], f"descriptor.{field}")
    if value["observable_sources"] != ["sequence"]:
        raise ContractValidationError("descriptor observable_sources must be exactly sequence")
    generator = _require_exact_keys(
        value["generator"],
        {"environment_digest", "git_commit"},
        "descriptor.generator",
    )
    if not isinstance(generator["git_commit"], str) or not _GIT_COMMIT.fullmatch(
        generator["git_commit"]
    ):
        raise ContractValidationError("descriptor generator.git_commit is invalid")
    _require_sha256(
        generator["environment_digest"],
        "descriptor.generator.environment_digest",
    )
    config = _require_exact_keys(
        value["feature_config"],
        _FEATURE_CONFIG_FIELDS,
        "descriptor.feature_config",
    )
    if config["schema_version"] != FEATURE_CONFIG_SCHEMA_VERSION:
        raise ContractValidationError("descriptor feature config schema_version is invalid")
    _require_identifier(
        config["feature_config_id"],
        "descriptor.feature_config.feature_config_id",
    )
    _require_safe_object_key(
        config["object_key"],
        "descriptor.feature_config.object_key",
    )
    _require_sha256(config["sha256"], "descriptor.feature_config.sha256")
    _require_positive_int(config["size_bytes"], "descriptor.feature_config.size_bytes")

    definitions = _feature_definition_map(value["feature_definitions"])
    feature_ids = list(definitions)
    _require_ascii_sorted_unique(feature_ids, "descriptor.feature_definitions")

    fitted_states = _require_array(
        value["fitted_states"],
        "descriptor.fitted_states",
    )
    state_ids: list[str] = []
    states_by_id: dict[str, dict[str, Any]] = {}
    object_keys = {config["object_key"]}
    for index, raw_state in enumerate(fitted_states):
        field = f"descriptor.fitted_states[{index}]"
        state = _require_exact_keys(
            raw_state,
            _STATE_DESCRIPTOR_FIELDS,
            field,
        )
        state_id = _require_identifier(state["fit_state_id"], f"{field}.fit_state_id")
        state_ids.append(state_id)
        _require_identifier(state["derivation_id"], f"{field}.derivation_id")
        _require_identifier(state["state_version"], f"{field}.state_version")
        if state["fit_role"] != "development":
            raise ContractValidationError(f"{field}.fit_role must be development")
        _validate_input_identity(
            state["input_identity"],
            descriptor=value,
            field=f"{field}.input_identity",
        )
        definitions_for_state = _validate_tensor_definitions(state["tensor_definitions"])
        if definitions_for_state != state["tensor_definitions"]:
            raise ContractValidationError(f"{field}.tensor_definitions is invalid")
        artifact = _validate_artifact_entry(state["artifact"], f"{field}.artifact")
        if artifact["object_key"] in object_keys:
            raise ContractValidationError("descriptor member object keys must be unique")
        object_keys.add(artifact["object_key"])
        states_by_id[state_id] = state
    _require_ascii_sorted_unique(
        state_ids,
        "descriptor.fitted_states",
        nonempty=False,
    )

    required_states: dict[str, tuple[str, str]] = {}
    for definition in definitions.values():
        reference = definition["derivation"]["fit_state"]
        if reference is None:
            continue
        state_id = reference["fit_state_id"]
        requirement = (reference["sha256"], definition["derivation"]["derivation_id"])
        if state_id in required_states and required_states[state_id] != requirement:
            raise ContractValidationError("descriptor fitted-state references conflict")
        required_states[state_id] = requirement
    if set(states_by_id) != set(required_states):
        raise ContractValidationError(
            "descriptor fitted states must exact-cover feature references"
        )
    for state_id, state in states_by_id.items():
        sha256, derivation_id = required_states[state_id]
        if state["artifact"]["sha256"] != sha256 or state["derivation_id"] != derivation_id:
            raise ContractValidationError("descriptor fitted-state identity conflicts")

    matrices = _require_array(
        value["matrices"],
        "descriptor.matrices",
        nonempty=True,
    )
    roles: list[str] = []
    for index, raw_matrix in enumerate(matrices):
        field = f"descriptor.matrices[{index}]"
        matrix = _require_exact_keys(
            raw_matrix,
            _MATRIX_DESCRIPTOR_FIELDS,
            field,
        )
        role = matrix["role"]
        if role not in _ROLE_SET:
            raise ContractValidationError(f"{field}.role is invalid")
        roles.append(role)
        if matrix["candidate_id_field"] != "candidate_id":
            raise ContractValidationError(f"{field}.candidate_id_field is invalid")
        _require_positive_int(matrix["row_count"], f"{field}.row_count")
        if _require_positive_int(
            matrix["column_count"],
            f"{field}.column_count",
        ) != len(feature_ids):
            raise ContractValidationError(f"{field}.column_count differs")
        if matrix["feature_ids"] != feature_ids:
            raise ContractValidationError(
                f"{field}.feature_ids must use ascending descriptor feature order"
            )
        references = _require_array(matrix["fit_state_refs"], f"{field}.fit_state_refs")
        reference_ids: list[str] = []
        reference_map: dict[str, str] = {}
        for ref_index, raw_reference in enumerate(references):
            reference = _require_exact_keys(
                raw_reference,
                {"fit_state_id", "sha256"},
                f"{field}.fit_state_refs[{ref_index}]",
            )
            state_id = _require_identifier(
                reference["fit_state_id"],
                f"{field}.fit_state_refs[{ref_index}].fit_state_id",
            )
            reference_ids.append(state_id)
            reference_map[state_id] = _require_sha256(
                reference["sha256"],
                f"{field}.fit_state_refs[{ref_index}].sha256",
            )
        _require_ascii_sorted_unique(
            reference_ids,
            f"{field}.fit_state_refs",
            nonempty=False,
        )
        if reference_map != {
            state_id: state["artifact"]["sha256"] for state_id, state in states_by_id.items()
        }:
            raise ContractValidationError(f"{field}.fit_state_refs must exact-cover fitted states")
        artifact = _validate_artifact_entry(matrix["artifact"], f"{field}.artifact")
        if artifact["object_key"] in object_keys:
            raise ContractValidationError("descriptor member object keys must be unique")
        object_keys.add(artifact["object_key"])
    if roles != list(_ROLES):
        raise ContractValidationError("descriptor matrices must use the fixed role order")


def load_feature_bundle_descriptor(
    payload: bytes,
    *,
    expected_sha256: str | None = None,
) -> LoadedContract:
    """Strict-decode a canonical complete feature-bundle descriptor."""

    value = _parse_canonical_json_bytes(payload, "feature bundle descriptor")
    _validate_descriptor(value)
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractValidationError("feature bundle descriptor SHA-256 does not match")
    return LoadedContract(
        schema_version=DESCRIPTOR_SCHEMA_VERSION,
        value=_deep_freeze(value),
        canonical_bytes=payload,
        sha256=digest,
    )


def _logical_artifact_value(
    artifact: LoadedContract | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(artifact, LoadedContract):
        value = _json_compatible(artifact.value)
    else:
        value = _json_compatible(artifact)
    if not isinstance(value, dict):
        raise ContractValidationError("logical feature artifact must be an object")
    load_feature_artifact(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return value


def _member_artifact(
    *,
    object_key: str,
    payload: bytes,
) -> dict[str, Any]:
    _require_safe_object_key(object_key, "member object_key")
    return {
        "object_key": object_key,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _prepare_bundle_descriptor(
    artifact: LoadedContract | Mapping[str, Any],
    *,
    feature_config_object_key: str,
    member_bytes: Mapping[str, bytes],
    tensor_definitions: Mapping[str, Sequence[Mapping[str, Any]]],
    expected_role_memberships: Mapping[str, Collection[str]],
) -> dict[str, Any]:
    logical = _logical_artifact_value(artifact)
    feature_definitions = logical["feature_definitions"]
    definition_map = _feature_definition_map(feature_definitions)
    ordered_feature_ids = sorted(definition_map, key=lambda item: item.encode("ascii"))
    ordered_definitions = [definition_map[feature_id] for feature_id in ordered_feature_ids]

    state_metadata = {state["fit_state_id"]: state for state in logical["fitted_states"]}
    matrix_metadata = {matrix["role"]: matrix for matrix in logical["matrices"]}
    expected_object_keys = {
        feature_config_object_key,
        *(state["artifact"]["object_key"] for state in state_metadata.values()),
        *(matrix["artifact"]["object_key"] for matrix in matrix_metadata.values()),
    }
    if set(member_bytes) != expected_object_keys:
        raise ContractValidationError(
            "member inventory must exact-cover config, all states, and all role matrices"
        )
    if set(tensor_definitions) != set(state_metadata):
        raise ContractValidationError("tensor_definitions must exact-cover all fitted states")

    config_payload = member_bytes[feature_config_object_key]
    config = load_feature_config(
        config_payload,
        feature_definitions=ordered_definitions,
        expected_sha256=logical["feature_config"]["feature_config_sha256"],
    )
    if config.value["feature_config_id"] != logical["feature_config"]["feature_config_id"]:
        raise ContractValidationError("feature config ID does not match logical artifact")

    descriptor_states: list[dict[str, Any]] = []
    loaded_state_hashes: dict[str, str] = {}
    for state_id in sorted(state_metadata, key=lambda item: item.encode("ascii")):
        metadata = state_metadata[state_id]
        object_key = metadata["artifact"]["object_key"]
        payload = member_bytes[object_key]
        definitions_for_state = sorted(
            [_json_compatible(item) for item in tensor_definitions[state_id]],
            key=lambda item: _mapping_ascii_sort_key(item, "name"),
        )
        loaded_state = load_feature_fitted_state(
            payload,
            tensor_definitions=definitions_for_state,
            expected_fit_state_id=state_id,
            expected_derivation_id=metadata["derivation_id"],
            expected_state_version=metadata["state_version"],
            expected_input_identity=metadata["input_identity"],
            expected_sha256=metadata["artifact"]["sha256"],
        )
        if len(payload) != metadata["artifact"]["size_bytes"]:
            raise ContractValidationError("fitted state size does not match logical artifact")
        loaded_state_hashes[state_id] = loaded_state.sha256
        descriptor_states.append(
            {
                "artifact": _member_artifact(object_key=object_key, payload=payload),
                "derivation_id": metadata["derivation_id"],
                "fit_role": metadata["fit_role"],
                "fit_state_id": state_id,
                "input_identity": metadata["input_identity"],
                "state_version": metadata["state_version"],
                "tensor_definitions": definitions_for_state,
            }
        )

    loaded_matrices: dict[str, LoadedFeatureMatrix] = {}
    descriptor_matrices: list[dict[str, Any]] = []
    for role in _ROLES:
        metadata = matrix_metadata[role]
        if metadata["feature_ids"] != ordered_feature_ids:
            raise ContractValidationError(
                "logical matrix feature order must be ascending for this profile"
            )
        object_key = metadata["artifact"]["object_key"]
        payload = member_bytes[object_key]
        loaded_matrix = load_feature_matrix(
            payload,
            feature_definitions=ordered_definitions,
            expected_sha256=metadata["artifact"]["sha256"],
        )
        if len(payload) != metadata["artifact"]["size_bytes"]:
            raise ContractValidationError("matrix size does not match logical artifact")
        if len(loaded_matrix.rows) != metadata["row_count"]:
            raise ContractValidationError("matrix row count does not match logical artifact")
        validate_feature_matrix_rows(artifact, role=role, rows=loaded_matrix.rows)
        loaded_matrices[role] = loaded_matrix
        references = sorted(
            [_json_compatible(item) for item in metadata["fit_state_refs"]],
            key=lambda item: item["fit_state_id"].encode("ascii"),
        )
        if {
            reference["fit_state_id"]: reference["sha256"] for reference in references
        } != loaded_state_hashes:
            raise ContractValidationError(
                "matrix fitted-state references do not match loaded states"
            )
        descriptor_matrices.append(
            {
                "artifact": _member_artifact(object_key=object_key, payload=payload),
                "candidate_id_field": metadata["candidate_id_field"],
                "column_count": metadata["column_count"],
                "feature_ids": ordered_feature_ids,
                "fit_state_refs": references,
                "role": role,
                "row_count": metadata["row_count"],
            }
        )
    validate_feature_memberships(
        artifact,
        matrix_candidate_ids={role: loaded_matrices[role].candidate_ids for role in _ROLES},
        expected_role_memberships=expected_role_memberships,
    )

    descriptor = {
        "benchmark_task_id": logical["benchmark_task_id"],
        "dataset_manifest_sha256": logical["dataset_manifest_sha256"],
        "dataset_task_id": logical["dataset_task_id"],
        "descriptor_format_version": DESCRIPTOR_FORMAT_VERSION,
        "feature_artifact_contract_status": logical["contract_status"],
        "feature_artifact_schema_version": logical["schema_version"],
        "feature_config": {
            "feature_config_id": config.value["feature_config_id"],
            "object_key": feature_config_object_key,
            "schema_version": config.schema_version,
            "sha256": config.sha256,
            "size_bytes": len(config_payload),
        },
        "feature_definitions": ordered_definitions,
        "feature_registry_version": FEATURE_REGISTRY_VERSION,
        "fitted_state_format_version": FITTED_STATE_SCHEMA_VERSION,
        "fitted_states": descriptor_states,
        "generator": logical["generator"],
        "matrices": descriptor_matrices,
        "matrix_format_version": MATRIX_FORMAT_VERSION,
        "observable_sources": sorted(
            logical["observable_sources"],
            key=lambda item: item.encode("ascii"),
        ),
        "processing_manifest_sha256": logical["processing_manifest_sha256"],
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "split_sha256": logical["split_sha256"],
    }
    _validate_descriptor(descriptor)
    return descriptor


def build_feature_bundle_descriptor(
    artifact: LoadedContract | Mapping[str, Any],
    *,
    feature_config_object_key: str,
    member_bytes: Mapping[str, bytes],
    tensor_definitions: Mapping[str, Sequence[Mapping[str, Any]]],
    expected_role_memberships: Mapping[str, Collection[str]],
) -> bytes:
    """Build a complete canonical descriptor after validating every member."""

    descriptor = _prepare_bundle_descriptor(
        artifact,
        feature_config_object_key=feature_config_object_key,
        member_bytes=member_bytes,
        tensor_definitions=tensor_definitions,
        expected_role_memberships=expected_role_memberships,
    )
    payload = canonical_feature_json_bytes(descriptor)
    load_feature_bundle_descriptor(payload)
    return payload


def validate_run_spec_feature_identity(
    run_spec: RunSpec,
    descriptor: LoadedContract | bytes | None,
) -> None:
    """Apply descriptor-root semantics only to the exact prospective profile."""

    if run_spec.protocol_version != PROTOCOL_VERSION:
        return
    if descriptor is None:
        raise ContractValidationError(
            "feature bundle descriptor is required for the prospective protocol"
        )
    if isinstance(descriptor, bytes):
        loaded = load_feature_bundle_descriptor(descriptor)
    else:
        loaded = load_feature_bundle_descriptor(
            descriptor.canonical_bytes,
            expected_sha256=descriptor.sha256,
        )
    value = loaded.value
    if run_spec.feature_sha256 != loaded.sha256:
        raise ContractValidationError(
            "RunSpec.feature_sha256 does not match descriptor exact bytes"
        )
    bindings = {
        "protocol_version": run_spec.protocol_version,
        "dataset_manifest_sha256": run_spec.dataset_manifest_sha256,
        "processing_manifest_sha256": run_spec.processing_manifest_sha256,
        "split_sha256": run_spec.split_sha256,
    }
    for field, expected in bindings.items():
        if value[field] != expected:
            raise ContractValidationError(f"RunSpec {field} does not match descriptor")
    if (
        value["generator"]["git_commit"] != run_spec.git_commit
        or value["generator"]["environment_digest"] != run_spec.environment_digest
    ):
        raise ContractValidationError("RunSpec generator identity does not match descriptor")


def validate_feature_bundle_identity(
    artifact: LoadedContract | Mapping[str, Any],
    *,
    descriptor_bytes: bytes,
    member_bytes: Mapping[str, bytes],
    expected_role_memberships: Mapping[str, Collection[str]],
    run_spec: RunSpec | None = None,
) -> LoadedContract:
    """Fail closed over a logical artifact, every member, and the descriptor root."""

    descriptor = load_feature_bundle_descriptor(descriptor_bytes)
    value = descriptor.value
    tensor_definitions = {
        state["fit_state_id"]: state["tensor_definitions"] for state in value["fitted_states"]
    }
    reconstructed = build_feature_bundle_descriptor(
        artifact,
        feature_config_object_key=value["feature_config"]["object_key"],
        member_bytes=member_bytes,
        tensor_definitions=tensor_definitions,
        expected_role_memberships=expected_role_memberships,
    )
    if reconstructed != descriptor_bytes:
        raise ContractValidationError(
            "descriptor bytes do not match independent complete reconstruction"
        )
    if run_spec is not None:
        validate_run_spec_feature_identity(run_spec, descriptor)
    return descriptor
