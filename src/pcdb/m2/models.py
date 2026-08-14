"""Closed Ridge, Random Forest, and XGBoost adapters for synthetic replay."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestRegressor  # type: ignore[import-untyped]
from sklearn.linear_model import Ridge  # type: ignore[import-untyped]
from xgboost import XGBRegressor

from pcdb.contracts.experiment import ContractValidationError
from pcdb.contracts.feature_identity import (
    LoadedFeatureMatrix,
    canonical_feature_json_bytes,
)
from pcdb.m2.features import sequence_feature_definitions
from pcdb.m2.model_identity import (
    MODEL_CONFIG_SHA256 as MODEL_CONFIG_SHA256,
)
from pcdb.m2.model_identity import (
    MODEL_CONFIG_SIZE_BYTES as MODEL_CONFIG_SIZE_BYTES,
)
from pcdb.m2.types import DevelopmentObservation as DevelopmentObservation

MODEL_CONFIG_SCHEMA_VERSION = "m2-model-config-v1"
M4_MODEL_CONFIG_SCHEMA_VERSION = "m4-model-config-v1"
_CANDIDATE_ID = re.compile(r"^[0-9a-f]{64}$")
_MODEL_IDS = tuple(MODEL_CONFIG_SHA256)
_FEATURE_IDS = tuple(
    cast(str, definition["feature_id"]) for definition in sequence_feature_definitions()
)

_RIDGE_PARAMETERS: Mapping[str, object] = MappingProxyType(
    {
        "alpha": 1.0,
        "copy_X": True,
        "fit_intercept": True,
        "max_iter": None,
        "positive": False,
        "random_state": None,
        "solver": "svd",
        "tol": 0.0001,
    }
)
_RANDOM_FOREST_PARAMETERS: Mapping[str, object] = MappingProxyType(
    {
        "bootstrap": True,
        "ccp_alpha": 0.0,
        "criterion": "squared_error",
        "max_depth": 8,
        "max_features": 1.0,
        "max_leaf_nodes": None,
        "max_samples": None,
        "min_impurity_decrease": 0.0,
        "min_samples_leaf": 1,
        "min_samples_split": 2,
        "min_weight_fraction_leaf": 0.0,
        "monotonic_cst": None,
        "n_estimators": 64,
        "n_jobs": 1,
        "oob_score": False,
        "random_state": 1729,
        "verbose": 0,
        "warm_start": False,
    }
)
_XGBOOST_PARAMETERS: Mapping[str, object] = MappingProxyType(
    {
        "base_score": None,
        "booster": "gbtree",
        "callbacks": None,
        "colsample_bylevel": 1.0,
        "colsample_bynode": 1.0,
        "colsample_bytree": 1.0,
        "device": "cpu",
        "early_stopping_rounds": None,
        "enable_categorical": False,
        "eval_metric": None,
        "feature_types": None,
        "feature_weights": None,
        "gamma": 0.0,
        "grow_policy": "depthwise",
        "importance_type": None,
        "interaction_constraints": None,
        "learning_rate": 0.3,
        "max_bin": 256,
        "max_cat_threshold": None,
        "max_cat_to_onehot": None,
        "max_delta_step": 0.0,
        "max_depth": 6,
        "max_leaves": 0,
        "min_child_weight": 1.0,
        "missing": -1.0,
        "monotone_constraints": None,
        "multi_strategy": "one_output_per_tree",
        "n_estimators": 100,
        "n_jobs": 1,
        "num_parallel_tree": 1,
        "objective": "reg:squarederror",
        "random_state": 1729,
        "reg_alpha": 0.0,
        "reg_lambda": 1.0,
        "sampling_method": "uniform",
        "scale_pos_weight": 1.0,
        "seed_per_iteration": False,
        "subsample": 1.0,
        "tree_method": "hist",
        "validate_parameters": True,
        "verbosity": 0,
    }
)
_EFFECTIVE_PARAMETERS: Mapping[str, Mapping[str, object]] = MappingProxyType(
    {
        "ridge-sklearn-v1": _RIDGE_PARAMETERS,
        "random-forest-sklearn-v1": _RANDOM_FOREST_PARAMETERS,
        "xgboost-cpu-v1": _XGBOOST_PARAMETERS,
    }
)
_SEED_POLICIES = {
    "ridge-sklearn-v1": "run-spec-seed-recorded-estimator-no-rng",
    "random-forest-sklearn-v1": "random-state-equals-run-spec-seed",
    "xgboost-cpu-v1": (
        "random-state-equals-run-spec-seed;seed-per-iteration-false;single-thread-cpu-hist"
    ),
}
_M4_SEED_POLICIES = {
    "random-forest-sklearn-v1": (
        "estimator-random-state-fixed-1729;run-spec-seed-drives-replay-acquisition"
    ),
    "xgboost-cpu-v1": (
        "estimator-random-state-fixed-1729;run-spec-seed-drives-replay-acquisition;"
        "seed-per-iteration-false;single-thread-cpu-hist"
    ),
}
M4_MODEL_CONFIG_SHA256: Mapping[str, str] = MappingProxyType(
    {
        "random-forest-sklearn-v1": (
            "418b0fd6189fd9b3987620b05021e5ec5ff3018c074024cd13874cd3b9845312"
        ),
        "xgboost-cpu-v1": ("31eb5bc146f4e810027de8870fa5632e09a5427bd8dddfee9261572c1e3473f2"),
    }
)
M4_MODEL_CONFIG_SIZE_BYTES: Mapping[str, int] = MappingProxyType(
    {
        "random-forest-sklearn-v1": 2_615,
        "xgboost-cpu-v1": 5_733,
    }
)
_LIBRARIES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "ridge-sklearn-v1": MappingProxyType(
            {"name": "scikit-learn", "version_constraint": ">=1.9,<2"}
        ),
        "random-forest-sklearn-v1": MappingProxyType(
            {"name": "scikit-learn", "version_constraint": ">=1.9,<2"}
        ),
        "xgboost-cpu-v1": MappingProxyType(
            {"name": "xgboost-cpu", "version_constraint": ">=3.2,<3.3"}
        ),
    }
)


@dataclass(frozen=True, slots=True)
class LoadedModelConfig:
    """One exact frozen model configuration."""

    model_id: str
    schema_version: str
    seed_policy: str
    parameters: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str


class FittedRegressor(Protocol):
    """The only estimator capabilities retained after in-memory fitting."""

    def fit(
        self,
        features: NDArray[np.float64],
        observations: NDArray[np.float64],
    ) -> object: ...

    def predict(self, features: NDArray[np.float64]) -> NDArray[np.float64]: ...


def _persisted_name(constructor_name: str) -> str:
    return constructor_name.lower().replace("_", "-")


def _typed_parameter(constructor_name: str, value: object) -> dict[str, object]:
    name = _persisted_name(constructor_name)
    if isinstance(value, bool):
        dtype = "bool"
        encoded: object = value
    elif value is None:
        dtype = "utf8"
        encoded = "none"
    elif isinstance(value, str):
        dtype = "utf8"
        encoded = value
    elif isinstance(value, int):
        dtype = "int64"
        encoded = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError("model parameter float must be finite")
        dtype = "float64"
        encoded = struct.pack(">d", value).hex()
    else:
        raise ContractValidationError("model parameter uses an unsupported scalar type")
    return {
        "dtype": dtype,
        "name": name,
        "shape": [],
        "values": [encoded],
    }


def _model_config_value(model_id: str) -> dict[str, object]:
    parameters = _EFFECTIVE_PARAMETERS.get(model_id)
    if parameters is None:
        raise ContractValidationError("model_id is outside the frozen M2 baseline set")
    inventory = [_typed_parameter(name, value) for name, value in parameters.items()]
    inventory.sort(key=lambda item: cast(str, item["name"]).encode("ascii"))
    return {
        "library": dict(_LIBRARIES[model_id]),
        "model_id": model_id,
        "parameters": inventory,
        "schema_version": MODEL_CONFIG_SCHEMA_VERSION,
        "seed_policy": _SEED_POLICIES[model_id],
    }


def _m4_model_config_value(model_id: str) -> dict[str, object]:
    if model_id not in _M4_SEED_POLICIES:
        raise ContractValidationError("model_id is outside the frozen M4 model-config set")
    return {
        **_model_config_value(model_id),
        "schema_version": M4_MODEL_CONFIG_SCHEMA_VERSION,
        "seed_policy": _M4_SEED_POLICIES[model_id],
    }


_MODEL_CONFIG_BYTES = {
    model_id: canonical_feature_json_bytes(_model_config_value(model_id)) for model_id in _MODEL_IDS
}
for _model_id, _payload in _MODEL_CONFIG_BYTES.items():
    if (
        len(_payload) != MODEL_CONFIG_SIZE_BYTES[_model_id]
        or hashlib.sha256(_payload).hexdigest() != MODEL_CONFIG_SHA256[_model_id]
    ):
        raise RuntimeError(f"frozen {_model_id} model config identity drifted")

_M4_MODEL_CONFIG_BYTES = {
    model_id: canonical_feature_json_bytes(_m4_model_config_value(model_id))
    for model_id in _M4_SEED_POLICIES
}
for _model_id, _payload in _M4_MODEL_CONFIG_BYTES.items():
    if (
        len(_payload) != M4_MODEL_CONFIG_SIZE_BYTES[_model_id]
        or hashlib.sha256(_payload).hexdigest() != M4_MODEL_CONFIG_SHA256[_model_id]
    ):
        raise RuntimeError(f"frozen M4 {_model_id} model config identity drifted")


def model_config_bytes(model_id: str) -> bytes:
    """Return exact canonical bytes for one frozen baseline."""

    payload = _MODEL_CONFIG_BYTES.get(model_id)
    if payload is None:
        raise ContractValidationError("model_id is outside the frozen M2 baseline set")
    return payload


def m4_model_config_bytes(model_id: str) -> bytes:
    """Return the exact M4 config, reusing the accepted Ridge bytes."""

    if model_id == "ridge-sklearn-v1":
        return model_config_bytes(model_id)
    payload = _M4_MODEL_CONFIG_BYTES.get(model_id)
    if payload is None:
        raise ContractValidationError("model_id is outside the frozen M4 model-config set")
    return payload


def _reject_constant(value: str) -> None:
    raise ContractValidationError(f"non-finite JSON constant is forbidden: {value}")


def _reject_float(value: str) -> None:
    raise ContractValidationError(f"JSON floating-point numbers are forbidden: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"duplicate model-config key is forbidden: {key}")
        result[key] = value
    return result


def load_model_config(payload: bytes) -> LoadedModelConfig:
    """Strict-decode one exact closed model profile."""

    if not payload or payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise ContractValidationError("model config must be canonical UTF-8 JSON")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("model config must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("model config must be a JSON object")
    if set(value) != {"library", "model_id", "parameters", "schema_version", "seed_policy"}:
        raise ContractValidationError("model config top-level fields drifted")
    model_id = value.get("model_id")
    if not isinstance(model_id, str) or model_id not in _MODEL_CONFIG_BYTES:
        raise ContractValidationError("model config model_id is unknown")
    if canonical_feature_json_bytes(value) != payload:
        raise ContractValidationError("model config bytes are not canonical")
    if payload != _MODEL_CONFIG_BYTES[model_id]:
        raise ContractValidationError("model config differs from the frozen closed profile")
    return LoadedModelConfig(
        model_id=model_id,
        schema_version=MODEL_CONFIG_SCHEMA_VERSION,
        seed_policy=_SEED_POLICIES[model_id],
        parameters=_EFFECTIVE_PARAMETERS[model_id],
        canonical_bytes=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def load_m4_model_config(payload: bytes) -> LoadedModelConfig:
    """Strict-decode the closed M4 profile without widening the M2/M3 loader."""

    if payload == _MODEL_CONFIG_BYTES["ridge-sklearn-v1"]:
        return load_model_config(payload)
    if not payload or payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise ContractValidationError("M4 model config must be canonical UTF-8 JSON")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("M4 model config must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("M4 model config must be a JSON object")
    if set(value) != {"library", "model_id", "parameters", "schema_version", "seed_policy"}:
        raise ContractValidationError("M4 model config top-level fields drifted")
    model_id = value.get("model_id")
    if not isinstance(model_id, str) or model_id not in _M4_MODEL_CONFIG_BYTES:
        raise ContractValidationError("M4 model config model_id is unknown")
    if canonical_feature_json_bytes(value) != payload:
        raise ContractValidationError("M4 model config bytes are not canonical")
    if payload != _M4_MODEL_CONFIG_BYTES[model_id]:
        raise ContractValidationError("M4 model config differs from the frozen closed profile")
    return LoadedModelConfig(
        model_id=model_id,
        schema_version=M4_MODEL_CONFIG_SCHEMA_VERSION,
        seed_policy=_M4_SEED_POLICIES[model_id],
        parameters=_EFFECTIVE_PARAMETERS[model_id],
        canonical_bytes=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def model_effective_parameters(model_id: str) -> Mapping[str, object]:
    """Expose the exact constructor mapping for audit and tests."""

    parameters = _EFFECTIVE_PARAMETERS.get(model_id)
    if parameters is None:
        raise ContractValidationError("model_id is outside the frozen M2 baseline set")
    return parameters


def _matrix_values(matrix: LoadedFeatureMatrix) -> NDArray[np.float64]:
    if matrix.feature_ids != _FEATURE_IDS:
        raise ContractValidationError("feature matrix does not use the frozen 420-feature registry")
    values = np.asarray(
        [[cast(float, row[feature_id]) for feature_id in _FEATURE_IDS] for row in matrix.rows],
        dtype=np.float64,
    )
    if values.shape != (len(matrix.rows), len(_FEATURE_IDS)) or not np.isfinite(values).all():
        raise ContractValidationError("feature matrix must be finite with exact 420-column shape")
    return values


def _observation_values(
    observations: Sequence[DevelopmentObservation],
    candidate_ids: tuple[str, ...],
) -> NDArray[np.float64]:
    if isinstance(observations, (str, bytes, bytearray)) or not observations:
        raise ContractValidationError("development observations must be a non-empty sequence")
    observed: dict[str, float] = {}
    for item in observations:
        if not isinstance(item, DevelopmentObservation):
            raise ContractValidationError(
                "development observations must use DevelopmentObservation"
            )
        if not _CANDIDATE_ID.fullmatch(item.candidate_id):
            raise ContractValidationError("development observation candidate ID is invalid")
        if item.candidate_id in observed:
            raise ContractValidationError("development observations contain duplicate candidates")
        if isinstance(item.observation, bool) or not isinstance(
            item.observation,
            (int, float),
        ):
            raise ContractValidationError("development observation must be numeric")
        value = float(item.observation)
        if not math.isfinite(value):
            raise ContractValidationError("development observation must be finite")
        observed[item.candidate_id] = value
    if set(observed) != set(candidate_ids):
        raise ContractValidationError(
            "development observations must exact-cover development feature membership"
        )
    return np.asarray([observed[candidate_id] for candidate_id in candidate_ids], dtype=np.float64)


def fit_regressor(
    model_config: bytes,
    development_features: LoadedFeatureMatrix,
    development_observations: Sequence[DevelopmentObservation],
    *,
    seed: int,
) -> FittedRegressor:
    """Fit one ephemeral estimator using development features and observations only."""

    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ContractValidationError("RunSpec seed must be a non-negative integer")
    config = load_model_config(model_config)
    features = _matrix_values(development_features)
    observations = _observation_values(
        development_observations,
        development_features.candidate_ids,
    )
    return _fit_validated_regressor(config, features, observations, seed=seed)


def fit_m4_regressor(
    model_config: bytes,
    development_features: LoadedFeatureMatrix,
    development_observations: Sequence[DevelopmentObservation],
    *,
    seed: int,
) -> FittedRegressor:
    """Fit an M4 estimator while keeping estimator RNG fixed by exact config."""

    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ContractValidationError("RunSpec seed must be a non-negative integer")
    config = load_m4_model_config(model_config)
    features = _matrix_values(development_features)
    observations = _observation_values(
        development_observations,
        development_features.candidate_ids,
    )
    return _fit_validated_regressor(
        config,
        features,
        observations,
        seed=seed,
        require_seed_match=False,
    )


def fit_bootstrap_regressor(
    model_config: bytes,
    development_features: LoadedFeatureMatrix,
    development_observations: Sequence[DevelopmentObservation],
    *,
    sampled_indices: Sequence[int],
    seed: int,
) -> FittedRegressor:
    """Fit the frozen estimator on one exact with-replacement row draw."""

    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ContractValidationError("RunSpec seed must be a non-negative integer")
    config = load_model_config(model_config)
    features = _matrix_values(development_features)
    observations = _observation_values(
        development_observations,
        development_features.candidate_ids,
    )
    if (
        isinstance(sampled_indices, (str, bytes, bytearray))
        or len(sampled_indices) != len(development_features.candidate_ids)
        or any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(development_features.candidate_ids)
            for index in sampled_indices
        )
    ):
        raise ContractValidationError("bootstrap indices must exact-cover one in-range size-n draw")
    index_array = np.asarray(sampled_indices, dtype=np.int64)
    return _fit_validated_regressor(
        config,
        features[index_array],
        observations[index_array],
        seed=seed,
    )


def fit_m4_bootstrap_regressor(
    model_config: bytes,
    development_features: LoadedFeatureMatrix,
    development_observations: Sequence[DevelopmentObservation],
    *,
    sampled_indices: Sequence[int],
    seed: int,
) -> FittedRegressor:
    """Fit one M4 estimator on an acquisition-side with-replacement draw."""

    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ContractValidationError("RunSpec seed must be a non-negative integer")
    config = load_m4_model_config(model_config)
    features = _matrix_values(development_features)
    observations = _observation_values(
        development_observations,
        development_features.candidate_ids,
    )
    if (
        isinstance(sampled_indices, (str, bytes, bytearray))
        or len(sampled_indices) != len(development_features.candidate_ids)
        or any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(development_features.candidate_ids)
            for index in sampled_indices
        )
    ):
        raise ContractValidationError("bootstrap indices must exact-cover one in-range size-n draw")
    index_array = np.asarray(sampled_indices, dtype=np.int64)
    return _fit_validated_regressor(
        config,
        features[index_array],
        observations[index_array],
        seed=seed,
        require_seed_match=False,
    )


def _fit_validated_regressor(
    config: LoadedModelConfig,
    features: NDArray[np.float64],
    observations: NDArray[np.float64],
    *,
    seed: int,
    require_seed_match: bool = True,
) -> FittedRegressor:
    if config.model_id == "ridge-sklearn-v1":
        estimator = cast(FittedRegressor, Ridge(**dict(_RIDGE_PARAMETERS)))
    elif config.model_id == "random-forest-sklearn-v1":
        if require_seed_match and seed != _RANDOM_FOREST_PARAMETERS["random_state"]:
            raise ContractValidationError(
                "Random Forest random_state must exact-match the RunSpec seed"
            )
        estimator = cast(
            FittedRegressor,
            RandomForestRegressor(**dict(_RANDOM_FOREST_PARAMETERS)),
        )
    elif config.model_id == "xgboost-cpu-v1":
        if require_seed_match and seed != _XGBOOST_PARAMETERS["random_state"]:
            raise ContractValidationError("XGBoost random_state must exact-match the RunSpec seed")
        estimator = cast(
            FittedRegressor,
            XGBRegressor(**dict(_XGBOOST_PARAMETERS)),
        )
    else:
        raise ContractValidationError("loaded model config dispatch drifted")
    try:
        if config.model_id == "xgboost-cpu-v1":
            cast(Any, estimator).fit(
                features,
                observations,
                sample_weight=None,
                base_margin=None,
                eval_set=None,
                verbose=False,
                xgb_model=None,
                sample_weight_eval_set=None,
                base_margin_eval_set=None,
                feature_weights=None,
            )
        else:
            estimator.fit(features, observations)
    except Exception:
        raise ContractValidationError("synthetic development model fit failed") from None
    return estimator


def predict_regressor(
    fitted_estimator: FittedRegressor,
    candidate_features: LoadedFeatureMatrix,
) -> tuple[tuple[str, float], ...]:
    """Generate finite point predictions for the candidate matrix exact cover."""

    features = _matrix_values(candidate_features)
    try:
        if isinstance(fitted_estimator, XGBRegressor):
            raw_prediction_values = cast(Any, fitted_estimator).predict(
                features,
                output_margin=False,
                validate_features=True,
                base_margin=None,
                iteration_range=None,
            )
        else:
            raw_prediction_values = fitted_estimator.predict(features)
        raw_predictions = np.asarray(raw_prediction_values, dtype=np.float64)
    except Exception:
        raise ContractValidationError("synthetic candidate prediction failed") from None
    if raw_predictions.shape != (len(candidate_features.candidate_ids),):
        raise ContractValidationError("candidate prediction shape differs from membership")
    if not np.isfinite(raw_predictions).all():
        raise ContractValidationError("candidate predictions must be finite")
    return tuple(
        (candidate_id, float(prediction))
        for candidate_id, prediction in zip(
            candidate_features.candidate_ids,
            raw_predictions,
            strict=True,
        )
    )
