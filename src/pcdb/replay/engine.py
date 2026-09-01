"""Deterministic synthetic replay for the frozen M3 protocol."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, cast

import numpy as np

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.feature_identity import (
    LoadedFeatureMatrix,
    encode_feature_matrix,
    load_feature_bundle_descriptor,
    load_feature_matrix,
)
from pcdb.contracts.run import RunSpec
from pcdb.evaluation import HighValueEvaluator, MetricValue, SequentialMetricSet
from pcdb.m2.features import sequence_feature_definitions
from pcdb.m2.models import (
    DevelopmentObservation,
    fit_bootstrap_regressor,
    fit_m4_bootstrap_regressor,
    fit_m4_regressor,
    fit_regressor,
    load_m4_model_config,
    load_model_config,
    predict_regressor,
)
from pcdb.replay.oracle import HiddenOracle, Observation

_CANDIDATE_ID = re.compile(r"^[0-9a-f]{64}$")
_M3_PROTOCOL_ID = "m3-single-fidelity-replay-v1"
_M4_PROTOCOL_ID = "m4-single-fidelity-screening-v1"
_M5_TRACER_PROTOCOL_ID = "m5-seed-source-tracer-v1"
_RANDOM_ID = "random-seeded-sha256-v1"
_RANDOM_CONFIG_SHA256 = "24f80499cb48ad6716f86a9c47bf098d47acdb78841bcb7b8926504619983e0f"
_GREEDY_ID = "greedy-predictive-mean-v1"
_GREEDY_CONFIG_SHA256 = "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"
_UNCERTAINTY_ID = "uncertainty-bootstrap-std-v1"
_UNCERTAINTY_CONFIG_SHA256 = "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6"
_UCB_ID = "ucb-bootstrap-std-beta1-v1"
_UCB_CONFIG_SHA256 = "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d"
_M4_SEEDS = tuple(range(1729, 1739))
_M4_INITIAL_SAMPLE_SIZES = (32, 64)
_M4_BATCH_SIZES = (16,)
_M4_BUDGETS = (128,)
_M4_HIGH_VALUE_DEFINITIONS = (
    ("upper-tail-1pct", 0.01),
    ("upper-tail-0.5pct", 0.005),
    ("upper-tail-2pct", 0.02),
)


def _resolved_experiment_context(run_spec: RunSpec, payload: bytes) -> tuple[str, float]:
    loaded = load_experiment_config(payload)
    if loaded.sha256 != run_spec.experiment_config_sha256:
        raise ContractValidationError("Experiment Config identity differs from RunSpec")
    value = loaded.value
    if loaded.schema_version == "3" and value["protocol_version"] != run_spec.protocol_version:
        raise ContractValidationError("Experiment Config protocol differs from RunSpec")
    for field in ("dataset_manifest_sha256", "processing_manifest_sha256", "split_sha256"):
        if value[field] != getattr(run_spec, field):
            raise ContractValidationError(f"Experiment Config {field} differs from RunSpec")
    if run_spec.protocol_version == _M4_PROTOCOL_ID and loaded.schema_version == "2":
        registered_profile = {
            "seeds": _M4_SEEDS,
            "initial_sample_sizes": _M4_INITIAL_SAMPLE_SIZES,
            "batch_sizes": _M4_BATCH_SIZES,
            "budgets": _M4_BUDGETS,
        }
        run_profile = {
            "seeds": run_spec.seed,
            "initial_sample_sizes": run_spec.initial_sample_size,
            "batch_sizes": run_spec.batch_size,
            "budgets": run_spec.budget,
        }
        for field, expected in registered_profile.items():
            if value[field] != expected or run_profile[field] not in expected:
                raise ContractValidationError(
                    f"M4 Experiment Config {field} differs from the frozen profile"
                )
    else:
        registered_profile = {
            "seeds": (run_spec.seed,),
            "initial_sample_sizes": (run_spec.initial_sample_size,),
            "batch_sizes": (run_spec.batch_size,),
            "budgets": (run_spec.budget,),
        }
        for field, expected in registered_profile.items():
            if value[field] != expected:
                raise ContractValidationError(f"Experiment Config {field} differs from RunSpec")
    if run_spec.model_id not in value["models"]:
        raise ContractValidationError("RunSpec model is not registered by Experiment Config")
    if run_spec.acquisition_id not in value["acquisitions"]:
        raise ContractValidationError("RunSpec acquisition is not registered by Experiment Config")
    if loaded.schema_version in {"2", "3"}:
        if (
            run_spec.protocol_version
            in {
                _M4_PROTOCOL_ID,
                _M5_TRACER_PROTOCOL_ID,
            }
            and value["phase"] != "development"
        ):
            raise ContractValidationError("development Experiment Config phase must be development")
        primary_id = value["evaluation"]["primary_high_value_definition_id"]
        primary = next(
            (
                definition
                for definition in value["evaluation"]["high_value_definitions"]
                if definition["definition_id"] == primary_id
            ),
            None,
        )
        if primary is None or primary["purpose"] != "primary":
            raise ContractValidationError("primary high-value definition is unresolved")
        task_id = value["task_id"]
        fraction = primary["value"]
    else:
        if canonical_json_bytes(value) != payload or value.get("phase") != "smoke":
            raise ContractValidationError("synthetic Experiment Config must be canonical smoke v1")
        evaluation = value.get("evaluation")
        task_id = value.get("task_id")
        if not isinstance(evaluation, Mapping) or set(evaluation) != {
            "primary_high_value_fraction"
        }:
            raise ContractValidationError("synthetic primary high-value definition is unresolved")
        fraction = evaluation["primary_high_value_fraction"]
    if not isinstance(task_id, str) or not task_id:
        raise ContractValidationError("Experiment Config task identity is invalid")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
        raise ContractValidationError("primary high-value fraction is invalid")
    if not math.isfinite(float(fraction)) or not 0 < float(fraction) <= 1:
        raise ContractValidationError("primary high-value fraction is invalid")
    return task_id, float(fraction)


@dataclass(frozen=True, slots=True)
class ReplaySeedAxes:
    """Random-source identities consumed by one replay."""

    initial_seed: int
    internal_seed: int | None
    estimator_seed: int


@dataclass(frozen=True, slots=True)
class ImmediateBatchMetrics:
    """Aggregate-only truth result for one branch-local immediate batch."""

    actual_high_value_count: int
    best_found_improvement: float
    high_value_recall_increment: float
    simple_regret_reduction: float


class ImmediateBatchEvaluator:
    """Truth-owning capability with immutable definitions and branch-local oracle state."""

    def __init__(self, truth: Mapping[str, float], fractions: Sequence[float]) -> None:
        try:
            validated = HiddenOracle(truth)
        except ValueError:
            raise ContractValidationError("immediate batch truth boundary is invalid") from None
        self.__truth = dict(truth)
        if set(validated.candidate_ids) != set(self.__truth) or not fractions:
            raise ContractValidationError("immediate batch truth boundary is invalid")
        memberships: dict[float, frozenset[str]] = {}
        ranked = sorted(self.__truth.items(), key=lambda item: (-item[1], item[0]))
        for raw_fraction in fractions:
            fraction = float(raw_fraction)
            if not math.isfinite(fraction) or not 0 < fraction <= 1 or fraction in memberships:
                raise ContractValidationError("immediate batch definition is invalid")
            nominal = math.ceil(fraction * len(ranked))
            boundary = ranked[nominal - 1][1]
            memberships[fraction] = frozenset(
                candidate_id for candidate_id, value in ranked if value >= boundary
            )
        self.__memberships = memberships

    @property
    def definition_counts(self) -> MappingProxyType[float, int]:
        """Expose tie-inclusive counts without membership or truth values."""

        return MappingProxyType(
            {fraction: len(membership) for fraction, membership in self.__memberships.items()}
        )

    def evaluate(
        self,
        *,
        observation_prefix_ids: Sequence[str],
        selected_ids: Sequence[str],
    ) -> MappingProxyType[float, ImmediateBatchMetrics]:
        """Reveal one isolated branch and return no truth-bearing detail."""

        prefix = tuple(observation_prefix_ids)
        selected = tuple(selected_ids)
        if (
            not prefix
            or len(set(prefix)) != len(prefix)
            or len(selected) != 16
            or len(set(selected)) != 16
            or not set(prefix).isdisjoint(selected)
        ):
            raise ContractValidationError("immediate batch boundary is invalid")
        if not set((*prefix, *selected)).issubset(self.__truth):
            raise ContractValidationError(
                "immediate batch candidate is outside the bound truth universe"
            )
        oracle = HiddenOracle(self.__truth)
        oracle.reveal(prefix)
        before_best = max(item.label for item in oracle.observations)
        oracle.reveal(selected)
        after_best = max(item.label for item in oracle.observations)
        improvement = after_best - before_best
        selected_set = frozenset(selected)
        return MappingProxyType(
            {
                fraction: ImmediateBatchMetrics(
                    actual_high_value_count=len(selected_set & membership),
                    best_found_improvement=improvement,
                    high_value_recall_increment=(len(selected_set & membership) / len(membership)),
                    simple_regret_reduction=improvement,
                )
                for fraction, membership in self.__memberships.items()
            }
        )


def _resolved_seed_axes(run_spec: RunSpec, payload: bytes) -> ReplaySeedAxes:
    """Resolve random sources from the hash-bound Experiment Config."""

    _resolved_experiment_context(run_spec, payload)
    if run_spec.protocol_version != _M5_TRACER_PROTOCOL_ID:
        return ReplaySeedAxes(
            initial_seed=run_spec.seed,
            internal_seed=run_spec.seed,
            estimator_seed=run_spec.seed,
        )
    loaded = load_experiment_config(payload)
    if loaded.schema_version != "3":
        raise ContractValidationError("M5 tracer requires Experiment Config v3")
    axes = loaded.value["seed_axes"]
    initial_seed = axes["initial_seed"]
    internal_seed = axes["internal_seed"]
    estimator_seed = axes["estimator_seed"]
    if initial_seed != run_spec.seed:
        raise ContractValidationError("M5 initial seed differs from RunSpec seed")
    if run_spec.acquisition_id == _UNCERTAINTY_ID:
        if isinstance(internal_seed, bool) or not isinstance(internal_seed, int):
            raise ContractValidationError("M5 uncertainty internal seed is unresolved")
    elif run_spec.acquisition_id == _GREEDY_ID:
        if internal_seed is not None:
            raise ContractValidationError("M5 Greedy must not consume an internal seed")
    else:
        raise ContractValidationError("M5 acquisition is outside the tracer protocol")
    if estimator_seed != 1729:
        raise ContractValidationError("M5 estimator seed differs from frozen model config")
    return ReplaySeedAxes(
        initial_seed=initial_seed,
        internal_seed=internal_seed,
        estimator_seed=estimator_seed,
    )


def _strict_json_object(payload: bytes, field: str) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ContractValidationError(f"{field} contains a non-finite JSON constant")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractValidationError(f"{field} contains a duplicate JSON key")
            result[key] = value
        return result

    if not payload or payload.startswith(b"\xef\xbb\xbf"):
        raise ContractValidationError(f"{field} must be strict UTF-8 JSON")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except ContractValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError(f"{field} must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be a JSON object")
    return cast(dict[str, Any], value)


def _validated_candidate_features(
    *,
    run_spec: RunSpec,
    task_id: str,
    descriptor_bytes: bytes,
    candidate_features: LoadedFeatureMatrix,
) -> LoadedFeatureMatrix:
    descriptor = load_feature_bundle_descriptor(
        descriptor_bytes,
        expected_sha256=run_spec.feature_sha256,
    )
    value = descriptor.value
    if value["benchmark_task_id"] != task_id:
        raise ContractValidationError("feature descriptor task differs from Experiment Config")
    for field in ("dataset_manifest_sha256", "processing_manifest_sha256", "split_sha256"):
        if value[field] != getattr(run_spec, field):
            raise ContractValidationError(f"feature descriptor {field} differs from RunSpec")
    candidate_members = [
        member for member in value["matrices"] if member["role"] == "candidate-pool"
    ]
    if len(candidate_members) != 1:
        raise ContractValidationError("feature descriptor candidate-pool member is unresolved")
    reference = candidate_members[0]["artifact"]
    payload = candidate_features.canonical_bytes
    if (
        hashlib.sha256(payload).hexdigest() != reference["sha256"]
        or len(payload) != reference["size_bytes"]
    ):
        raise ContractValidationError("candidate feature member differs from descriptor")
    validated = load_feature_matrix(
        payload,
        feature_definitions=cast(Sequence[Mapping[str, Any]], value["feature_definitions"]),
        expected_sha256=cast(str, reference["sha256"]),
    )
    if candidate_features != validated:
        raise ContractValidationError("decoded candidate features differ from their member bytes")
    if len(validated.candidate_ids) != candidate_members[0]["row_count"]:
        raise ContractValidationError("candidate feature member row count differs from descriptor")
    return validated


def _manifest_bound_truth_sha256(
    *,
    run_spec: RunSpec,
    processing_manifest_bytes: bytes,
    hidden_truth_bytes: bytes,
    candidate_ids: tuple[str, ...],
) -> str:
    if hashlib.sha256(processing_manifest_bytes).hexdigest() != (
        run_spec.processing_manifest_sha256
    ):
        raise ContractValidationError("processing manifest identity differs from RunSpec")
    manifest = _strict_json_object(processing_manifest_bytes, "processing manifest")
    if (
        manifest.get("schema_version") != "1"
        or manifest.get("dataset_manifest_sha256") != run_spec.dataset_manifest_sha256
        or manifest.get("candidate_id_field") != "candidate_id"
        or manifest.get("candidate_id_unique") is not True
        or manifest.get("hidden_label_field") != "target"
        or manifest.get("label_direction") != "higher_is_better"
    ):
        raise ContractValidationError("processing manifest domain identity drifted")
    outputs = manifest.get("output_files")
    if not isinstance(outputs, list):
        raise ContractValidationError("processing manifest outputs are invalid")
    truth_references = [
        item
        for item in outputs
        if isinstance(item, dict)
        and isinstance(item.get("object_key"), str)
        and PurePosixPath(item["object_key"]).name == "hidden-truth.csv"
    ]
    if len(truth_references) != 1:
        raise ContractValidationError(
            "processing manifest must reference exactly one hidden-truth artifact"
        )
    reference = truth_references[0]
    required = {"object_key", "sha256", "size_bytes"}
    if not required <= set(reference) or not set(reference) <= required | {"row_count"}:
        raise ContractValidationError("hidden-truth manifest reference fields drifted")
    if reference["sha256"] != hashlib.sha256(hidden_truth_bytes).hexdigest() or reference[
        "size_bytes"
    ] != len(hidden_truth_bytes):
        raise ContractValidationError("hidden-truth exact bytes do not match manifest")
    truth = _parse_hidden_truth(hidden_truth_bytes)
    if "row_count" in reference and reference["row_count"] != len(truth):
        raise ContractValidationError("hidden-truth row count does not match manifest")
    if not set(candidate_ids) <= set(truth):
        raise ContractValidationError("hidden-truth does not cover the candidate pool")
    pool_truth = sorted((candidate_id, truth[candidate_id]) for candidate_id in candidate_ids)
    payload = json.dumps(
        pool_truth,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parse_hidden_truth(payload: bytes) -> dict[str, float]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractValidationError("hidden-truth must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    if reader.fieldnames != ["candidate_id", "target"]:
        raise ContractValidationError("hidden-truth fields drifted")
    truth: dict[str, float] = {}
    try:
        for row in reader:
            candidate_id = row.get("candidate_id")
            raw_target = row.get("target")
            if (
                set(row) != {"candidate_id", "target"}
                or None in row
                or not isinstance(candidate_id, str)
                or not _CANDIDATE_ID.fullmatch(candidate_id)
                or candidate_id in truth
                or not isinstance(raw_target, str)
                or not raw_target
            ):
                raise ContractValidationError("hidden-truth row is invalid")
            try:
                target = float(raw_target)
            except ValueError as exc:
                raise ContractValidationError("hidden-truth target must be numeric") from exc
            if not math.isfinite(target):
                raise ContractValidationError("hidden-truth target must be finite")
            truth[candidate_id] = target
    except csv.Error as exc:
        raise ContractValidationError("hidden-truth must be valid CSV") from exc
    return truth


def _tuple_sha256(values: list[object]) -> str:
    payload = json.dumps(
        values,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class BootstrapDraw:
    """One frozen acquisition-side bootstrap member draw."""

    member_index: int
    member_seed_uint64: int
    indices: tuple[int, ...]


def bootstrap_draws(
    *,
    protocol_id: str,
    run_seed: int,
    task_id: str,
    model_id: str,
    round_index: int,
    observation_count: int,
) -> tuple[BootstrapDraw, ...]:
    """Rebuild the five exact PCG64 draws frozen by the M3 protocol."""

    if not protocol_id or not task_id or not model_id:
        raise ContractValidationError("bootstrap identities must not be empty")
    for name, value, minimum in (
        ("run_seed", run_seed, 0),
        ("round_index", round_index, 1),
        ("observation_count", observation_count, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ContractValidationError(f"bootstrap {name} is invalid")
    draws: list[BootstrapDraw] = []
    for member_index in range(5):
        digest = _tuple_sha256(
            [
                protocol_id,
                run_seed,
                task_id,
                model_id,
                round_index,
                member_index,
            ]
        )
        member_seed = int.from_bytes(bytes.fromhex(digest)[:8], "big")
        generator = np.random.Generator(np.random.PCG64(member_seed))
        indices = generator.integers(
            0,
            observation_count,
            size=observation_count,
            dtype=np.int64,
            endpoint=False,
        )
        draws.append(
            BootstrapDraw(
                member_index=member_index,
                member_seed_uint64=member_seed,
                indices=tuple(int(index) for index in indices),
            )
        )
    return tuple(draws)


def _strict_feature_subset(
    candidate_features: LoadedFeatureMatrix,
    candidate_ids: tuple[str, ...],
) -> LoadedFeatureMatrix:
    selected = set(candidate_ids)
    rows = [row for row in candidate_features.rows if row["candidate_id"] in selected]
    definitions = sequence_feature_definitions()
    return load_feature_matrix(
        encode_feature_matrix(rows, feature_definitions=definitions),
        feature_definitions=definitions,
    )


def bootstrap_prediction_std(
    *,
    run_spec: RunSpec,
    task_id: str,
    candidate_features: LoadedFeatureMatrix,
    model_config: bytes,
    observations: Sequence[Observation],
    candidate_ids: tuple[str, ...],
    round_index: int,
    experiment_config: bytes | None = None,
) -> tuple[tuple[str, float], ...]:
    """Return exact five-member population prediction std for unrevealed candidates."""

    if run_spec.protocol_version == _M3_PROTOCOL_ID:
        loaded_config = load_model_config(model_config)
        fit_bootstrap = fit_bootstrap_regressor
    elif run_spec.protocol_version in {_M4_PROTOCOL_ID, _M5_TRACER_PROTOCOL_ID}:
        loaded_config = load_m4_model_config(model_config)
        fit_bootstrap = fit_m4_bootstrap_regressor
    else:
        raise ContractValidationError("RunSpec protocol is outside the implemented replay set")
    if (
        loaded_config.model_id != run_spec.model_id
        or loaded_config.sha256 != run_spec.model_config_sha256
    ):
        raise ContractValidationError("model config identity differs from RunSpec")
    if not observations:
        raise ContractValidationError("uncertainty requires revealed observations")
    observed: dict[str, float] = {}
    for observation in observations:
        if not isinstance(observation, Observation):
            raise ContractValidationError("uncertainty observations use an invalid type")
        if observation.candidate_id in observed:
            raise ContractValidationError("uncertainty observations contain duplicate candidates")
        if not _CANDIDATE_ID.fullmatch(observation.candidate_id) or not math.isfinite(
            observation.label
        ):
            raise ContractValidationError("uncertainty observation is invalid")
        observed[observation.candidate_id] = observation.label
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ContractValidationError("uncertainty candidate IDs must be non-empty and distinct")
    if set(candidate_ids) & set(observed):
        raise ContractValidationError("uncertainty candidates overlap revealed observations")
    visible_ids = set(candidate_features.candidate_ids)
    if not set(candidate_ids) | set(observed) <= visible_ids:
        raise ContractValidationError(
            "uncertainty inputs are outside observable feature membership"
        )

    ordered_observed_ids = tuple(sorted(observed))
    observed_matrix = _strict_feature_subset(candidate_features, ordered_observed_ids)
    ordered_observations = [
        DevelopmentObservation(candidate_id=candidate_id, observation=observed[candidate_id])
        for candidate_id in ordered_observed_ids
    ]
    candidate_matrix = _strict_feature_subset(candidate_features, candidate_ids)
    member_predictions: list[tuple[tuple[str, float], ...]] = []
    bootstrap_seed = run_spec.seed
    estimator_seed = run_spec.seed
    if run_spec.protocol_version == _M5_TRACER_PROTOCOL_ID:
        if experiment_config is None:
            raise ContractValidationError(
                "M5 uncertainty requires its hash-bound Experiment Config"
            )
        seed_axes = _resolved_seed_axes(run_spec, experiment_config)
        if seed_axes.internal_seed is None:
            raise ContractValidationError("M5 uncertainty internal seed is unresolved")
        bootstrap_seed = seed_axes.internal_seed
        estimator_seed = seed_axes.estimator_seed
    draws = bootstrap_draws(
        protocol_id=run_spec.protocol_version,
        run_seed=bootstrap_seed,
        task_id=task_id,
        model_id=run_spec.model_id,
        round_index=round_index,
        observation_count=len(ordered_observed_ids),
    )
    for draw in draws:
        fitted = fit_bootstrap(
            model_config,
            observed_matrix,
            ordered_observations,
            sampled_indices=draw.indices,
            seed=estimator_seed,
        )
        member_predictions.append(predict_regressor(fitted, candidate_matrix))
    expected_ids = candidate_matrix.candidate_ids
    if any(
        tuple(candidate_id for candidate_id, _ in member) != expected_ids
        for member in member_predictions
    ):
        raise ContractValidationError("bootstrap predictions do not exact-cover candidates")
    values = np.asarray(
        [[prediction for _, prediction in member] for member in member_predictions],
        dtype=np.float64,
    )
    scores = np.std(values, axis=0, ddof=0)
    if scores.shape != (len(expected_ids),) or not np.isfinite(scores).all():
        raise ContractValidationError("bootstrap prediction std must be finite exact-cover")
    return tuple(
        (candidate_id, float(score))
        for candidate_id, score in zip(expected_ids, scores, strict=True)
    )


def ucb_prediction_scores(
    *,
    run_spec: RunSpec,
    task_id: str,
    candidate_features: LoadedFeatureMatrix,
    model_config: bytes,
    observations: Sequence[Observation],
    candidate_ids: tuple[str, ...],
    round_index: int,
) -> tuple[tuple[str, float], ...]:
    """Return M4 mean + population-bootstrap-std scores in candidate order."""

    if run_spec.protocol_version != _M4_PROTOCOL_ID:
        raise ContractValidationError("UCB requires the frozen M4 replay protocol")
    loaded_config = load_m4_model_config(model_config)
    if (
        loaded_config.model_id != run_spec.model_id
        or loaded_config.sha256 != run_spec.model_config_sha256
    ):
        raise ContractValidationError("model config identity differs from RunSpec")
    observed: dict[str, float] = {}
    for observation in observations:
        if not isinstance(observation, Observation):
            raise ContractValidationError("UCB observations use an invalid type")
        if observation.candidate_id in observed:
            raise ContractValidationError("UCB observations contain duplicate candidates")
        if not _CANDIDATE_ID.fullmatch(observation.candidate_id) or not math.isfinite(
            observation.label
        ):
            raise ContractValidationError("UCB observation is invalid")
        observed[observation.candidate_id] = observation.label
    if not observed:
        raise ContractValidationError("UCB requires revealed observations")
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ContractValidationError("UCB candidate IDs must be non-empty and distinct")
    if set(candidate_ids) & set(observed):
        raise ContractValidationError("UCB candidates overlap revealed observations")
    visible_ids = set(candidate_features.candidate_ids)
    if not set(candidate_ids) | set(observed) <= visible_ids:
        raise ContractValidationError("UCB inputs are outside observable feature membership")

    observed_ids = tuple(sorted(observed))
    fitted = fit_m4_regressor(
        model_config,
        _strict_feature_subset(candidate_features, observed_ids),
        [
            DevelopmentObservation(candidate_id=candidate_id, observation=observed[candidate_id])
            for candidate_id in observed_ids
        ],
        seed=run_spec.seed,
    )
    means = predict_regressor(
        fitted,
        _strict_feature_subset(candidate_features, candidate_ids),
    )
    standard_deviations = bootstrap_prediction_std(
        run_spec=run_spec,
        task_id=task_id,
        candidate_features=candidate_features,
        model_config=model_config,
        observations=observations,
        candidate_ids=candidate_ids,
        round_index=round_index,
    )
    if tuple(item[0] for item in means) != tuple(item[0] for item in standard_deviations):
        raise ContractValidationError("UCB mean and uncertainty membership differ")
    scores = tuple(
        (candidate_id, mean + standard_deviation)
        for (candidate_id, mean), (_, standard_deviation) in zip(
            means,
            standard_deviations,
            strict=True,
        )
    )
    if any(not math.isfinite(score) for _, score in scores):
        raise ContractValidationError("UCB scores must be finite")
    return scores


def _replay_schema_version(protocol_version: str, artifact: str) -> str:
    prefix = {
        _M3_PROTOCOL_ID: "m3",
        _M4_PROTOCOL_ID: "m4",
        _M5_TRACER_PROTOCOL_ID: "m4",
    }.get(protocol_version)
    if prefix is None or artifact not in {"checkpoint", "trajectory"}:
        raise ContractValidationError("replay schema dispatch drifted")
    return f"{prefix}-replay-{artifact}-v1"


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """One complete atomic reveal boundary."""

    round: int
    selected_candidate_ids: tuple[str, ...]
    observations: tuple[Observation, ...]
    distinct_revealed: int
    checkpoint_sha256: str
    metrics: SequentialMetricSet | None = None
    metric_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayTrajectory:
    """Canonical result of one complete deterministic replay."""

    run_spec: RunSpec
    task_id: str
    events: tuple[ReplayEvent, ...]

    @property
    def run_id(self) -> str:
        return self.run_spec.run_id

    @property
    def distinct_revealed(self) -> int:
        return self.events[-1].distinct_revealed

    @property
    def complete(self) -> bool:
        return self.distinct_revealed == self.run_spec.budget

    @staticmethod
    def _checkpoint_event_value(event: ReplayEvent) -> dict[str, object]:
        value: dict[str, object] = {
            "distinct_revealed": event.distinct_revealed,
            "observations": [
                {
                    "candidate_id": observation.candidate_id,
                    "value": observation.label,
                }
                for observation in event.observations
            ],
            "round": event.round,
            "selected_candidate_ids": list(event.selected_candidate_ids),
        }
        if event.metrics is not None:
            value["metrics"] = asdict(event.metrics)
            value["metric_sha256"] = event.metric_sha256
        return value

    @classmethod
    def _event_value(cls, event: ReplayEvent) -> dict[str, object]:
        value = cls._checkpoint_event_value(event)
        value["checkpoint_sha256"] = event.checkpoint_sha256
        return value

    def checkpoint_bytes(self, round_index: int) -> bytes:
        if (
            isinstance(round_index, bool)
            or not isinstance(round_index, int)
            or not 0 <= round_index < len(self.events)
        ):
            raise ContractValidationError("checkpoint round is outside trajectory")
        value = {
            "events": [
                self._checkpoint_event_value(event) for event in self.events[: round_index + 1]
            ],
            "run_id": self.run_id,
            "run_spec": asdict(self.run_spec),
            "schema_version": _replay_schema_version(
                self.run_spec.protocol_version,
                "checkpoint",
            ),
            "task_id": self.task_id,
        }
        return canonical_json_bytes(value)

    def metric_bytes(self, round_index: int) -> bytes | None:
        if (
            isinstance(round_index, bool)
            or not isinstance(round_index, int)
            or not 0 <= round_index < len(self.events)
        ):
            raise ContractValidationError("metric round is outside trajectory")
        metrics = self.events[round_index].metrics
        return canonical_json_bytes(asdict(metrics)) if metrics is not None else None

    @property
    def latest_checkpoint_bytes(self) -> bytes:
        return self.checkpoint_bytes(len(self.events) - 1)

    @property
    def canonical_bytes(self) -> bytes:
        value = {
            "events": [self._event_value(event) for event in self.events],
            "run_id": self.run_id,
            "run_spec": asdict(self.run_spec),
            "schema_version": _replay_schema_version(
                self.run_spec.protocol_version,
                "trajectory",
            ),
            "task_id": self.task_id,
        }
        return canonical_json_bytes(value)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


def _load_metric_value(value: object, *, field: str) -> MetricValue:
    if not isinstance(value, dict) or set(value) != {"status", "undefined_reason", "value"}:
        raise ContractValidationError(f"{field} fields drifted")
    if value["status"] == "undefined":
        if (
            value["value"] is not None
            or not isinstance(value["undefined_reason"], str)
            or not value["undefined_reason"]
        ):
            raise ContractValidationError(f"{field} undefined state is invalid")
        return MetricValue.undefined(value["undefined_reason"])
    if value["status"] != "defined" or value["undefined_reason"] is not None:
        raise ContractValidationError(f"{field} status is invalid")
    raw_metric = value["value"]
    if isinstance(raw_metric, bool):
        return MetricValue.defined(raw_metric)
    if not isinstance(raw_metric, (int, float)) or not math.isfinite(float(raw_metric)):
        raise ContractValidationError(f"{field} defined value is invalid")
    return MetricValue.defined(float(raw_metric))


def _load_metrics(value: object) -> SequentialMetricSet:
    fields = {
        "best_found_value",
        "simple_regret",
        "high_value_discovery_recall",
        "success_at_budget",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractValidationError("trajectory metric fields drifted")
    return SequentialMetricSet(
        best_found_value=_load_metric_value(
            value["best_found_value"],
            field="best_found_value",
        ),
        simple_regret=_load_metric_value(value["simple_regret"], field="simple_regret"),
        high_value_discovery_recall=_load_metric_value(
            value["high_value_discovery_recall"],
            field="high_value_discovery_recall",
        ),
        success_at_budget=_load_metric_value(
            value["success_at_budget"],
            field="success_at_budget",
        ),
    )


def load_replay_trajectory(payload: bytes) -> ReplayTrajectory:
    """Strict-load one complete canonical M3 or M4 replay trajectory."""

    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("trajectory must be canonical UTF-8 JSON") from None
    if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
        raise ContractValidationError("trajectory bytes are not canonical")
    if set(value) != {"events", "run_id", "run_spec", "schema_version", "task_id"}:
        raise ContractValidationError("trajectory fields drifted")
    raw_run_spec = value["run_spec"]
    if not isinstance(raw_run_spec, dict):
        raise ContractValidationError("trajectory RunSpec must be an object")
    for field in ("seed", "initial_sample_size", "batch_size", "budget"):
        if isinstance(raw_run_spec.get(field), bool) or not isinstance(
            raw_run_spec.get(field), int
        ):
            raise ContractValidationError(f"trajectory RunSpec {field} must be an integer")
    try:
        run_spec = RunSpec(**raw_run_spec)
    except (TypeError, ValueError):
        raise ContractValidationError("trajectory RunSpec is invalid") from None
    if value["schema_version"] != _replay_schema_version(
        run_spec.protocol_version,
        "trajectory",
    ):
        raise ContractValidationError("trajectory schema version drifted")
    if value["run_id"] != run_spec.run_id:
        raise ContractValidationError("trajectory run_id differs from RunSpec")
    task_id = value["task_id"]
    if not isinstance(task_id, str) or not task_id:
        raise ContractValidationError("trajectory task_id is invalid")
    raw_events = value["events"]
    if not isinstance(raw_events, list) or not raw_events:
        raise ContractValidationError("trajectory events must be non-empty")
    events: list[ReplayEvent] = []
    revealed: set[str] = set()
    metrics_present: bool | None = None
    for round_index, raw_event in enumerate(raw_events):
        base_fields = {
            "checkpoint_sha256",
            "distinct_revealed",
            "observations",
            "round",
            "selected_candidate_ids",
        }
        if (
            not isinstance(raw_event, dict)
            or not base_fields <= set(raw_event)
            or not set(raw_event) <= base_fields | {"metric_sha256", "metrics"}
        ):
            raise ContractValidationError("trajectory event fields drifted")
        this_has_metrics = "metrics" in raw_event or "metric_sha256" in raw_event
        if this_has_metrics != ({"metrics", "metric_sha256"} <= set(raw_event)):
            raise ContractValidationError("trajectory metric identity is incomplete")
        if metrics_present is None:
            metrics_present = this_has_metrics
        elif metrics_present != this_has_metrics:
            raise ContractValidationError("trajectory metric boundaries are inconsistent")
        if raw_event["round"] != round_index:
            raise ContractValidationError("trajectory rounds must be contiguous")
        checkpoint_sha256 = raw_event["checkpoint_sha256"]
        if not isinstance(checkpoint_sha256, str) or not _CANDIDATE_ID.fullmatch(checkpoint_sha256):
            raise ContractValidationError("trajectory checkpoint SHA-256 is invalid")
        raw_selected = raw_event["selected_candidate_ids"]
        expected_size = run_spec.initial_sample_size if round_index == 0 else run_spec.batch_size
        if (
            not isinstance(raw_selected, list)
            or len(raw_selected) != expected_size
            or any(
                not isinstance(candidate_id, str) or not _CANDIDATE_ID.fullmatch(candidate_id)
                for candidate_id in raw_selected
            )
        ):
            raise ContractValidationError("trajectory selection size or identity is invalid")
        selected = tuple(raw_selected)
        if len(selected) != len(set(selected)) or set(selected) & revealed:
            raise ContractValidationError("trajectory contains a repeat purchase")
        raw_observations = raw_event["observations"]
        if not isinstance(raw_observations, list) or len(raw_observations) != len(selected):
            raise ContractValidationError("trajectory observations differ from selection")
        observations: list[Observation] = []
        for candidate_id, raw_observation in zip(selected, raw_observations, strict=True):
            if (
                not isinstance(raw_observation, dict)
                or set(raw_observation) != {"candidate_id", "value"}
                or raw_observation["candidate_id"] != candidate_id
                or isinstance(raw_observation["value"], bool)
                or not isinstance(raw_observation["value"], (int, float))
                or not math.isfinite(float(raw_observation["value"]))
            ):
                raise ContractValidationError("trajectory observation payload is invalid")
            observations.append(
                Observation(candidate_id=candidate_id, label=float(raw_observation["value"]))
            )
        revealed.update(selected)
        if raw_event["distinct_revealed"] != len(revealed):
            raise ContractValidationError("trajectory reveal count is not monotonic")
        metrics = _load_metrics(raw_event["metrics"]) if this_has_metrics else None
        metric_sha256 = raw_event.get("metric_sha256")
        if metrics is not None and (
            not isinstance(metric_sha256, str)
            or not _CANDIDATE_ID.fullmatch(metric_sha256)
            or hashlib.sha256(canonical_json_bytes(asdict(metrics))).hexdigest() != metric_sha256
        ):
            raise ContractValidationError("trajectory metric SHA-256 differs")
        event = ReplayEvent(
            round=round_index,
            selected_candidate_ids=selected,
            observations=tuple(observations),
            distinct_revealed=len(revealed),
            checkpoint_sha256=checkpoint_sha256,
            metrics=metrics,
            metric_sha256=metric_sha256 if isinstance(metric_sha256, str) else None,
        )
        events.append(event)
        prefix = ReplayTrajectory(run_spec=run_spec, task_id=task_id, events=tuple(events))
        if hashlib.sha256(prefix.checkpoint_bytes(round_index)).hexdigest() != checkpoint_sha256:
            raise ContractValidationError("trajectory checkpoint SHA-256 differs")
    if len(revealed) != run_spec.budget:
        raise ContractValidationError("trajectory does not end at exact budget")
    trajectory = ReplayTrajectory(run_spec=run_spec, task_id=task_id, events=tuple(events))
    if trajectory.canonical_bytes != payload:
        raise ContractValidationError("trajectory canonical reconstruction drifted")
    return trajectory


class ReplayEngine:
    """Own selection-to-oracle flow for one supported replay RunSpec."""

    @classmethod
    def from_reviewed_inputs(
        cls,
        *,
        run_spec: RunSpec,
        experiment_config: bytes,
        feature_descriptor: bytes,
        processing_manifest: bytes,
        hidden_truth: bytes,
        task_id: str,
        candidate_features: LoadedFeatureMatrix,
        model_config: bytes,
    ) -> ReplayEngine:
        """Construct the truth-owning engine without exposing labels to its caller."""

        truth = _parse_hidden_truth(hidden_truth)
        try:
            pool_truth = {
                candidate_id: truth[candidate_id]
                for candidate_id in candidate_features.candidate_ids
            }
        except KeyError:
            raise ContractValidationError(
                "hidden-truth does not cover the candidate pool"
            ) from None
        return cls(
            run_spec=run_spec,
            experiment_config=experiment_config,
            feature_descriptor=feature_descriptor,
            processing_manifest=processing_manifest,
            hidden_truth=hidden_truth,
            task_id=task_id,
            candidate_ids=candidate_features.candidate_ids,
            oracle=HiddenOracle(pool_truth),
            candidate_features=candidate_features,
            model_config=model_config,
            evaluator=HighValueEvaluator(tuple(pool_truth.items())),
        )

    def __init__(
        self,
        *,
        run_spec: RunSpec,
        experiment_config: bytes,
        feature_descriptor: bytes,
        processing_manifest: bytes,
        hidden_truth: bytes,
        task_id: str,
        candidate_ids: tuple[str, ...],
        oracle: HiddenOracle,
        candidate_features: LoadedFeatureMatrix,
        model_config: bytes,
        evaluator: HighValueEvaluator,
        high_value_fraction: float | None = None,
    ) -> None:
        acquisition_profiles = {
            _M3_PROTOCOL_ID: {
                _RANDOM_ID: _RANDOM_CONFIG_SHA256,
                _GREEDY_ID: _GREEDY_CONFIG_SHA256,
                _UNCERTAINTY_ID: _UNCERTAINTY_CONFIG_SHA256,
            },
            _M4_PROTOCOL_ID: {
                _RANDOM_ID: _RANDOM_CONFIG_SHA256,
                _GREEDY_ID: _GREEDY_CONFIG_SHA256,
                _UNCERTAINTY_ID: _UNCERTAINTY_CONFIG_SHA256,
                _UCB_ID: _UCB_CONFIG_SHA256,
            },
            _M5_TRACER_PROTOCOL_ID: {
                _GREEDY_ID: _GREEDY_CONFIG_SHA256,
                _UNCERTAINTY_ID: _UNCERTAINTY_CONFIG_SHA256,
            },
        }
        protocol_acquisitions = acquisition_profiles.get(run_spec.protocol_version)
        if protocol_acquisitions is None:
            raise ContractValidationError("RunSpec protocol is outside the implemented replay set")
        expected_acquisition_sha = protocol_acquisitions.get(run_spec.acquisition_id)
        if expected_acquisition_sha is None:
            raise ContractValidationError("acquisition is outside the implemented protocol subset")
        if run_spec.acquisition_config_sha256 != expected_acquisition_sha:
            raise ContractValidationError("acquisition config identity drifted")
        resolved_task_id, resolved_fraction = _resolved_experiment_context(
            run_spec, experiment_config
        )
        seed_axes = _resolved_seed_axes(run_spec, experiment_config)
        if task_id != resolved_task_id:
            raise ContractValidationError("task identity differs from Experiment Config")
        if high_value_fraction is not None and high_value_fraction != resolved_fraction:
            raise ContractValidationError(
                "primary high-value fraction differs from Experiment Config"
            )
        if not candidate_ids or any(not _CANDIDATE_ID.fullmatch(item) for item in candidate_ids):
            raise ContractValidationError("candidate IDs must be lower-case 64-hex")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ContractValidationError("candidate IDs must be distinct")
        if set(candidate_ids) != set(oracle.candidate_ids):
            raise ContractValidationError(
                "candidate IDs must exact-match the hidden oracle universe"
            )
        validated_features = _validated_candidate_features(
            run_spec=run_spec,
            task_id=resolved_task_id,
            descriptor_bytes=feature_descriptor,
            candidate_features=candidate_features,
        )
        if set(validated_features.candidate_ids) != set(candidate_ids):
            raise ContractValidationError("candidate features must exact-cover the pool")
        loaded_config = (
            load_model_config(model_config)
            if run_spec.protocol_version == _M3_PROTOCOL_ID
            else load_m4_model_config(model_config)
        )
        if (
            loaded_config.model_id != run_spec.model_id
            or loaded_config.sha256 != run_spec.model_config_sha256
        ):
            raise ContractValidationError("model config identity differs from RunSpec")
        if set(evaluator.candidate_ids) != set(candidate_ids):
            raise ContractValidationError("evaluator must exact-match the candidate pool")
        bound_truth_sha256 = _manifest_bound_truth_sha256(
            run_spec=run_spec,
            processing_manifest_bytes=processing_manifest,
            hidden_truth_bytes=hidden_truth,
            candidate_ids=validated_features.candidate_ids,
        )
        if oracle.truth_sha256 != bound_truth_sha256:
            raise ContractValidationError("hidden oracle truth identity differs from manifest")
        if evaluator.truth_sha256 != bound_truth_sha256:
            raise ContractValidationError("evaluator truth identity differs from manifest")
        evaluator.definition_summary(fraction=resolved_fraction)
        if run_spec.initial_sample_size > run_spec.budget:
            raise ContractValidationError("initial sample size exceeds budget")
        if run_spec.budget > len(candidate_ids):
            raise ContractValidationError("budget exceeds candidate pool")
        if (run_spec.budget - run_spec.initial_sample_size) % run_spec.batch_size:
            raise ContractValidationError("budget is not reachable by complete batches")
        self._run_spec = run_spec
        self._experiment_config = experiment_config
        self._seed_axes = seed_axes
        self._task_id = resolved_task_id
        self._candidate_ids = validated_features.candidate_ids
        self._oracle = oracle
        self._candidate_features = validated_features
        self._model_config = model_config
        self._evaluator = evaluator
        self._high_value_fraction = resolved_fraction

    def _initial_selection(self) -> tuple[str, ...]:
        ranked = sorted(
            (
                _tuple_sha256(
                    [
                        self._run_spec.protocol_version,
                        self._task_id,
                        self._seed_axes.initial_seed,
                        candidate_id,
                    ]
                ),
                candidate_id,
            )
            for candidate_id in self._candidate_ids
        )
        return tuple(
            candidate_id for _, candidate_id in ranked[: self._run_spec.initial_sample_size]
        )

    def _random_selection(self, *, round_index: int) -> tuple[str, ...]:
        ranked = sorted(
            (
                _tuple_sha256(
                    [
                        self._run_spec.protocol_version,
                        self._task_id,
                        self._run_spec.seed,
                        round_index,
                        candidate_id,
                    ]
                ),
                candidate_id,
            )
            for candidate_id in self._candidate_ids
            if candidate_id not in self._oracle.revealed_ids
        )
        return tuple(candidate_id for _, candidate_id in ranked[: self._run_spec.batch_size])

    def _feature_subset(self, candidate_ids: tuple[str, ...]) -> LoadedFeatureMatrix:
        if self._candidate_features is None:
            raise ContractValidationError("model acquisition requires candidate features")
        return _strict_feature_subset(self._candidate_features, candidate_ids)

    def _greedy_selection(self) -> tuple[str, ...]:
        if self._model_config is None:
            raise ContractValidationError("Greedy acquisition requires frozen model config")
        observations = tuple(sorted(self._oracle.observations, key=lambda item: item.candidate_id))
        observed_ids = tuple(item.candidate_id for item in observations)
        remaining_ids = tuple(
            sorted(
                candidate_id
                for candidate_id in self._candidate_ids
                if candidate_id not in set(observed_ids)
            )
        )
        fit = (
            fit_regressor
            if self._run_spec.protocol_version == _M3_PROTOCOL_ID
            else fit_m4_regressor
        )
        fitted = fit(
            self._model_config,
            self._feature_subset(observed_ids),
            [
                DevelopmentObservation(
                    candidate_id=observation.candidate_id,
                    observation=observation.label,
                )
                for observation in observations
            ],
            seed=self._seed_axes.estimator_seed,
        )
        predictions = predict_regressor(fitted, self._feature_subset(remaining_ids))
        ranked = sorted(predictions, key=lambda item: (-item[1], item[0]))
        return tuple(candidate_id for candidate_id, _ in ranked[: self._run_spec.batch_size])

    def _uncertainty_selection(self, *, round_index: int) -> tuple[str, ...]:
        if self._candidate_features is None or self._model_config is None:
            raise ContractValidationError("Uncertainty acquisition requires model inputs")
        remaining_ids = tuple(
            sorted(
                candidate_id
                for candidate_id in self._candidate_ids
                if candidate_id not in self._oracle.revealed_ids
            )
        )
        scores = bootstrap_prediction_std(
            run_spec=self._run_spec,
            task_id=self._task_id,
            candidate_features=self._candidate_features,
            model_config=self._model_config,
            observations=self._oracle.observations,
            candidate_ids=remaining_ids,
            round_index=round_index,
            experiment_config=self._experiment_config,
        )
        ranked = sorted(scores, key=lambda item: (-item[1], item[0]))
        return tuple(candidate_id for candidate_id, _ in ranked[: self._run_spec.batch_size])

    def _ucb_selection(self, *, round_index: int) -> tuple[str, ...]:
        if self._candidate_features is None or self._model_config is None:
            raise ContractValidationError("UCB acquisition requires model inputs")
        remaining_ids = tuple(
            sorted(
                candidate_id
                for candidate_id in self._candidate_ids
                if candidate_id not in self._oracle.revealed_ids
            )
        )
        scores = ucb_prediction_scores(
            run_spec=self._run_spec,
            task_id=self._task_id,
            candidate_features=self._candidate_features,
            model_config=self._model_config,
            observations=self._oracle.observations,
            candidate_ids=remaining_ids,
            round_index=round_index,
        )
        ranked = sorted(scores, key=lambda item: (-item[1], item[0]))
        return tuple(candidate_id for candidate_id, _ in ranked[: self._run_spec.batch_size])

    def _expected_selection(self, round_index: int) -> tuple[str, ...]:
        if round_index == 0:
            return self._initial_selection()
        if self._run_spec.acquisition_id == _RANDOM_ID:
            return self._random_selection(round_index=round_index)
        if self._run_spec.acquisition_id == _GREEDY_ID:
            return self._greedy_selection()
        if self._run_spec.acquisition_id == _UNCERTAINTY_ID:
            return self._uncertainty_selection(round_index=round_index)
        if self._run_spec.acquisition_id == _UCB_ID:
            return self._ucb_selection(round_index=round_index)
        raise ContractValidationError("acquisition dispatch drifted")

    def _metrics(self) -> SequentialMetricSet | None:
        return self._evaluator.sequential_metrics(
            [observation.candidate_id for observation in self._oracle.observations],
            fraction=self._high_value_fraction,
            success_budget=self._run_spec.budget,
        )

    def final_m4_definition_metrics(self) -> tuple[tuple[str, SequentialMetricSet], ...]:
        """Return the three frozen definition metrics after a complete M4-compatible run."""

        if self._run_spec.protocol_version not in {
            _M4_PROTOCOL_ID,
            _M5_TRACER_PROTOCOL_ID,
        }:
            raise ContractValidationError(
                "definition sensitivity metrics require an M4-compatible protocol"
            )
        if len(self._oracle.revealed_ids) != self._run_spec.budget:
            raise ContractValidationError("definition sensitivity metrics require a complete run")
        revealed = [observation.candidate_id for observation in self._oracle.observations]
        return tuple(
            (
                definition_id,
                self._evaluator.sequential_metrics(
                    revealed,
                    fraction=fraction,
                    success_budget=self._run_spec.budget,
                ),
            )
            for definition_id, fraction in _M4_HIGH_VALUE_DEFINITIONS
        )

    def _make_event(
        self,
        existing_events: Sequence[ReplayEvent],
        *,
        round_index: int,
        selected: tuple[str, ...],
        observations: tuple[Observation, ...],
    ) -> ReplayEvent:
        metrics = self._metrics()
        metric_sha256 = (
            hashlib.sha256(canonical_json_bytes(asdict(metrics))).hexdigest()
            if metrics is not None
            else None
        )
        provisional = ReplayEvent(
            round=round_index,
            selected_candidate_ids=selected,
            observations=observations,
            distinct_revealed=len(self._oracle.revealed_ids),
            checkpoint_sha256="0" * 64,
            metrics=metrics,
            metric_sha256=metric_sha256,
        )
        prefix = ReplayTrajectory(
            run_spec=self._run_spec,
            task_id=self._task_id,
            events=(*existing_events, provisional),
        )
        checkpoint_sha256 = hashlib.sha256(prefix.checkpoint_bytes(round_index)).hexdigest()
        return ReplayEvent(
            round=round_index,
            selected_candidate_ids=selected,
            observations=observations,
            distinct_revealed=len(self._oracle.revealed_ids),
            checkpoint_sha256=checkpoint_sha256,
            metrics=metrics,
            metric_sha256=metric_sha256,
        )

    def _restore_checkpoint(self, payload: bytes) -> list[ReplayEvent]:
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ContractValidationError("checkpoint must be canonical UTF-8 JSON") from None
        if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
            raise ContractValidationError("checkpoint bytes are not canonical")
        if set(value) != {"events", "run_id", "run_spec", "schema_version", "task_id"}:
            raise ContractValidationError("checkpoint fields drifted")
        if value["schema_version"] != _replay_schema_version(
            self._run_spec.protocol_version,
            "checkpoint",
        ):
            raise ContractValidationError("checkpoint schema version drifted")
        if value["run_id"] != self._run_spec.run_id or value["run_spec"] != asdict(self._run_spec):
            raise ContractValidationError("checkpoint RunSpec identity drifted")
        if value["task_id"] != self._task_id:
            raise ContractValidationError("checkpoint task identity drifted")
        raw_events = value["events"]
        if not isinstance(raw_events, list) or not raw_events:
            raise ContractValidationError("checkpoint requires a complete reveal boundary")
        restored: list[ReplayEvent] = []
        for round_index, raw_event in enumerate(raw_events):
            expected_event_fields = {
                "distinct_revealed",
                "observations",
                "round",
                "selected_candidate_ids",
            }
            if self._evaluator is not None:
                expected_event_fields.add("metrics")
                expected_event_fields.add("metric_sha256")
            if not isinstance(raw_event, dict) or set(raw_event) != expected_event_fields:
                raise ContractValidationError("checkpoint event fields drifted")
            if raw_event["round"] != round_index:
                raise ContractValidationError("checkpoint rounds must be contiguous")
            raw_selected = raw_event["selected_candidate_ids"]
            if not isinstance(raw_selected, list) or any(
                not isinstance(candidate_id, str) for candidate_id in raw_selected
            ):
                raise ContractValidationError("checkpoint selection is invalid")
            selected = tuple(raw_selected)
            expected = self._expected_selection(round_index)
            if selected != expected:
                raise ContractValidationError("checkpoint selection differs from frozen protocol")
            revealed = self._oracle.reveal(selected)
            raw_observations = raw_event["observations"]
            if not isinstance(raw_observations, list) or len(raw_observations) != len(revealed):
                raise ContractValidationError("checkpoint observations differ from selection")
            for raw_observation, observation in zip(
                raw_observations,
                revealed,
                strict=True,
            ):
                if (
                    not isinstance(raw_observation, dict)
                    or set(raw_observation) != {"candidate_id", "value"}
                    or raw_observation["candidate_id"] != observation.candidate_id
                    or isinstance(raw_observation["value"], bool)
                    or not isinstance(raw_observation["value"], (int, float))
                    or not math.isfinite(float(raw_observation["value"]))
                    or float(raw_observation["value"]) != observation.label
                ):
                    raise ContractValidationError("checkpoint observation payload drifted")
            distinct_revealed = len(self._oracle.revealed_ids)
            if raw_event["distinct_revealed"] != distinct_revealed:
                raise ContractValidationError("checkpoint reveal count drifted")
            event = self._make_event(
                restored,
                round_index=round_index,
                selected=selected,
                observations=revealed,
            )
            if raw_event != ReplayTrajectory._checkpoint_event_value(event):
                raise ContractValidationError("checkpoint event payload drifted")
            restored.append(event)
        if len(self._oracle.revealed_ids) > self._run_spec.budget:
            raise ContractValidationError("checkpoint exceeds RunSpec budget")
        return restored

    def run(
        self,
        *,
        checkpoint: bytes | None = None,
        stop_after_round: int | None = None,
    ) -> ReplayTrajectory:
        """Run through the exact budget and return canonical trajectory bytes."""

        if self._oracle.observations:
            raise ContractValidationError("fresh replay requires an unrevealed hidden oracle")
        if stop_after_round is not None and (
            isinstance(stop_after_round, bool)
            or not isinstance(stop_after_round, int)
            or stop_after_round < 0
        ):
            raise ContractValidationError("stop_after_round must be a non-negative integer")
        events = self._restore_checkpoint(checkpoint) if checkpoint is not None else []
        round_index = len(events)
        while len(self._oracle.revealed_ids) < self._run_spec.budget:
            selected = self._expected_selection(round_index)
            observations = self._oracle.reveal(selected)
            events.append(
                self._make_event(
                    events,
                    round_index=round_index,
                    selected=selected,
                    observations=observations,
                )
            )
            if stop_after_round == round_index:
                break
            round_index += 1
        return ReplayTrajectory(
            run_spec=self._run_spec,
            task_id=self._task_id,
            events=tuple(events),
        )
