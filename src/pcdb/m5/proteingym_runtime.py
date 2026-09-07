"""Runtime boundaries for the sealed M5C ProteinGym confirmation slice."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestRegressor  # type: ignore[import-untyped]
from xgboost import XGBRegressor

from pcdb.contracts import ContractValidationError
from pcdb.m2.features import ALPHABET
from pcdb.m2.models import m4_model_config_bytes, model_effective_parameters
from pcdb.m5.proteingym_confirmation import (
    ASSAY_IDS,
    BATCH_SIZE,
    HIGH_VALUE_FRACTIONS,
    INITIAL_SAMPLE_SIZES,
    MATCHED_REPLICATES,
    MODEL_IDS,
    PROTOCOL_ID,
    RUN_SEEDS,
    AssayTaxonomyInput,
    StateEffects,
    bootstrap_assay_effects,
    build_sequence_composition_matrix,
    classify_assay,
    classify_cohort,
    compact_json_bytes,
    initial_selection,
    matched_random_batches,
    state_identity,
)
from pcdb.replay.engine import bootstrap_draws
from pcdb.replay.oracle import Observation

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_ATTEMPT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_GREEDY_CONFIG_SHA256 = "3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15"
_EXPLORATION_IDENTITIES = MappingProxyType(
    {
        "random-forest-sklearn-v1": (
            "uncertainty-bootstrap-std-v1",
            "7748917bf19048002b784be8c95dfe76ca138dac421e511a4f79e33aa82d16f6",
        ),
        "xgboost-cpu-v1": (
            "ucb-bootstrap-std-beta1-v1",
            "9b132ec77e71a8c6966e3eae712c56da148aa92c4d5c54001fb1c1a954b75e4d",
        ),
    }
)
_MAIN_CAPS: Mapping[str, int | float] = MappingProxyType(
    {
        "cost": 100,
        "cpu_seconds": 1_382_400,
        "network_bytes": 4_294_967_296,
        "peak_rss_bytes": 48_000_000_000,
        "scratch_bytes": 32_212_254_720,
        "wall_seconds": 86_400,
    }
)


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise ContractValidationError(f"cannot read exact M5C input: {path}") from exc
    return digest.hexdigest(), size


def _require_exact_file(path: Path, expected_sha256: str, expected_size: int) -> None:
    if (
        not _SHA256.fullmatch(expected_sha256)
        or type(expected_size) is not int
        or expected_size < 0
    ):
        raise ContractValidationError("M5C expected file identity is invalid")
    sha256, size = _file_identity(path)
    if sha256 != expected_sha256 or size != expected_size:
        raise ContractValidationError(f"M5C input exact-byte identity drifted: {path}")


@dataclass(frozen=True, slots=True)
class CandidateTable:
    """Observable candidate identity, mutant and sequence table."""

    assay_id: str
    candidate_ids: tuple[str, ...]
    mutants: tuple[str, ...]
    sequences: tuple[str, ...]
    sha256: str
    size_bytes: int

    @property
    def mutant_to_index(self) -> Mapping[str, int]:
        return MappingProxyType({mutant: index for index, mutant in enumerate(self.mutants)})


@dataclass(frozen=True, slots=True)
class FeatureArtifact:
    """Exact on-disk float64 feature artifact used by state identities."""

    path: Path
    sha256: str
    shape: tuple[int, int]
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StateSelection:
    """Private candidate-level selection passed only to the evaluator."""

    assay_id: str
    exploration_ids: tuple[str, ...]
    greedy_ids: tuple[str, ...]
    initial_ids: tuple[str, ...]
    initial_sample_size: int
    matched_ids: tuple[tuple[str, ...], ...]
    model_id: str
    run_seed: int
    state_id: str


def load_candidate_table(
    path: Path,
    *,
    assay_id: str,
    expected_sha256: str,
    expected_size: int,
) -> CandidateTable:
    """Load an exact-bound observable ProteinGym candidate artifact."""

    if not assay_id:
        raise ContractValidationError("M5C candidate assay identity is empty")
    _require_exact_file(path, expected_sha256, expected_size)
    candidate_ids: list[str] = []
    mutants: list[str] = []
    sequences: list[str] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ContractValidationError("M5C candidate table is empty") from exc
            if header != ["candidate_id", "mutant", "mutated_sequence"]:
                raise ContractValidationError("M5C candidate fields drifted")
            for row in reader:
                if len(row) != 3:
                    raise ContractValidationError("M5C candidate row shape drifted")
                candidate_id, mutant, sequence = row
                expected_id = hashlib.sha256(
                    f"proteingym:v1.3:{assay_id}\0{mutant}".encode()
                ).hexdigest()
                if (
                    candidate_id != expected_id
                    or not mutant
                    or len(sequence) < 2
                    or sequence != sequence.upper()
                    or any(residue not in ALPHABET for residue in sequence)
                ):
                    raise ContractValidationError("M5C candidate identity or sequence is invalid")
                candidate_ids.append(candidate_id)
                mutants.append(mutant)
                sequences.append(sequence)
    except (OSError, gzip.BadGzipFile, csv.Error, UnicodeError) as exc:
        if isinstance(exc, ContractValidationError):
            raise
        raise ContractValidationError("M5C candidate table is unreadable") from exc
    if (
        not candidate_ids
        or len(candidate_ids) != len(set(candidate_ids))
        or len(mutants) != len(set(mutants))
    ):
        raise ContractValidationError("M5C candidate table is empty or duplicated")
    return CandidateTable(
        assay_id=assay_id,
        candidate_ids=tuple(candidate_ids),
        mutants=tuple(mutants),
        sequences=tuple(sequences),
        sha256=expected_sha256,
        size_bytes=expected_size,
    )


def build_feature_artifact(sequences: Sequence[str], path: Path) -> FeatureArtifact:
    """Create the frozen 420-column matrix as an append-only NumPy artifact."""

    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    matrix = np.lib.format.open_memmap(
        path,
        mode="w+",
        dtype=np.float64,
        shape=(len(sequences), 420),
    )
    try:
        for start in range(0, len(sequences), 4096):
            stop = min(start + 4096, len(sequences))
            matrix[start:stop] = build_sequence_composition_matrix(sequences[start:stop])
        matrix.flush()
    except Exception:
        del matrix
        raise
    del matrix
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    sha256, size = _file_identity(path)
    return FeatureArtifact(path=path, sha256=sha256, shape=(len(sequences), 420), size_bytes=size)


def _fit_model(
    model_id: str,
    features: NDArray[np.float64],
    labels: NDArray[np.float64],
) -> Any:
    parameters = dict(model_effective_parameters(model_id))
    try:
        if model_id == "random-forest-sklearn-v1":
            estimator = RandomForestRegressor(**parameters)
            estimator.fit(features, labels)
        elif model_id == "xgboost-cpu-v1":
            estimator = XGBRegressor(**parameters)
            estimator.fit(
                features,
                labels,
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
            raise ContractValidationError("M5C model identity drifted")
    except ContractValidationError:
        raise
    except Exception:
        raise ContractValidationError("M5C surrogate fit failed") from None
    return estimator


def _predict(estimator: Any, features: NDArray[np.float64]) -> NDArray[np.float64]:
    try:
        if isinstance(estimator, XGBRegressor):
            raw = estimator.predict(
                features,
                output_margin=False,
                validate_features=True,
                base_margin=None,
                iteration_range=None,
            )
        else:
            raw = estimator.predict(features)
    except Exception:
        raise ContractValidationError("M5C surrogate prediction failed") from None
    values = np.asarray(raw, dtype=np.float64)
    if values.shape != (len(features),) or not np.isfinite(values).all():
        raise ContractValidationError("M5C surrogate prediction shape or values drifted")
    return values


def _top_candidate_ids(
    candidate_ids: NDArray[np.str_], scores: NDArray[np.float64], count: int
) -> tuple[str, ...]:
    order = np.lexsort((candidate_ids, -scores))
    return tuple(str(candidate_ids[index]) for index in order[:count])


def select_state(
    *,
    assay_id: str,
    candidate_ids: Sequence[str],
    candidate_pool_sha256: str,
    features: NDArray[np.float64],
    feature_sha256: str,
    initial_sample_size: int,
    model_id: str,
    observations: Sequence[Observation],
    run_seed: int,
) -> StateSelection:
    """Fit one frozen state from revealed labels and select G/E/M branches."""

    ids = tuple(candidate_ids)
    values = np.asarray(features, dtype=np.float64)
    if (
        assay_id not in ASSAY_IDS
        or model_id not in MODEL_IDS
        or initial_sample_size not in INITIAL_SAMPLE_SIZES
        or run_seed not in RUN_SEEDS
        or values.shape != (len(ids), 420)
        or not np.isfinite(values).all()
    ):
        raise ContractValidationError("M5C state matrix or identity drifted")
    expected_initial = initial_selection(
        ids,
        assay_id=assay_id,
        run_seed=run_seed,
        size=initial_sample_size,
    )
    observed = {item.candidate_id: float(item.label) for item in observations}
    if len(observed) != len(observations) or set(observed) != set(expected_initial):
        raise ContractValidationError("M5C revealed prefix drifted")
    id_to_index = {candidate_id: index for index, candidate_id in enumerate(ids)}
    ordered_observed = tuple(sorted(observed))
    observed_indices = np.asarray([id_to_index[item] for item in ordered_observed], dtype=np.int64)
    observed_features = values[observed_indices]
    observed_labels = np.asarray([observed[item] for item in ordered_observed], dtype=np.float64)
    remaining_indices = np.asarray(
        [index for index, candidate_id in enumerate(ids) if candidate_id not in observed],
        dtype=np.int64,
    )
    if len(remaining_indices) < BATCH_SIZE:
        raise ContractValidationError("M5C remaining candidate pool is too small")
    remaining_features = values[remaining_indices]
    remaining_ids = np.asarray([ids[index] for index in remaining_indices], dtype="U64")
    means = _predict(_fit_model(model_id, observed_features, observed_labels), remaining_features)
    member_predictions: list[NDArray[np.float64]] = []
    for draw in bootstrap_draws(
        protocol_id=PROTOCOL_ID,
        run_seed=run_seed,
        task_id=assay_id,
        model_id=model_id,
        round_index=1,
        observation_count=len(ordered_observed),
    ):
        sampled = np.asarray(draw.indices, dtype=np.int64)
        member_predictions.append(
            _predict(
                _fit_model(model_id, observed_features[sampled], observed_labels[sampled]),
                remaining_features,
            )
        )
    uncertainty = np.std(np.stack(member_predictions), axis=0, ddof=0)
    acquisition = uncertainty if model_id == MODEL_IDS[0] else means + uncertainty
    greedy = _top_candidate_ids(remaining_ids, means, BATCH_SIZE)
    exploration = _top_candidate_ids(remaining_ids, acquisition, BATCH_SIZE)
    model_sha256 = hashlib.sha256(m4_model_config_bytes(model_id)).hexdigest()
    state_id = state_identity(
        assay_id=assay_id,
        model_id=model_id,
        model_config_sha256=model_sha256,
        initial_sample_size=initial_sample_size,
        run_seed=run_seed,
        candidate_pool_sha256=candidate_pool_sha256,
        feature_sha256=feature_sha256,
    )
    remaining = tuple(str(item) for item in remaining_ids)
    return StateSelection(
        assay_id=assay_id,
        exploration_ids=exploration,
        greedy_ids=greedy,
        initial_ids=expected_initial,
        initial_sample_size=initial_sample_size,
        matched_ids=matched_random_batches(
            state_id=state_id,
            greedy_ids=greedy,
            exploration_ids=exploration,
            remaining_pool=remaining,
        ),
        model_id=model_id,
        run_seed=run_seed,
        state_id=state_id,
    )


def load_static_proxy(
    path: Path,
    *,
    candidate_table: CandidateTable,
    expected_sha256: str,
    expected_size: int,
) -> NDArray[np.float64]:
    """Read only mutant and ESM1v_single inside the static-evaluator capability."""

    _require_exact_file(path, expected_sha256, expected_size)
    values = np.empty(len(candidate_table.candidate_ids), dtype=np.float64)
    seen = np.zeros(len(candidate_table.candidate_ids), dtype=np.bool_)
    mutant_to_index = candidate_table.mutant_to_index
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ContractValidationError("M5C static proxy source is empty") from exc
            if (
                len(header) != len(set(header))
                or "mutant" not in header
                or "ESM1v_single" not in header
            ):
                raise ContractValidationError("M5C static proxy fields drifted")
            mutant_column = header.index("mutant")
            proxy_column = header.index("ESM1v_single")
            for row in reader:
                if len(row) != len(header):
                    raise ContractValidationError("M5C static proxy row shape drifted")
                mutant = row[mutant_column].strip()
                index = mutant_to_index.get(mutant)
                if index is None or seen[index]:
                    raise ContractValidationError("M5C static proxy membership drifted")
                try:
                    value = float(row[proxy_column])
                except ValueError as exc:
                    raise ContractValidationError("M5C static proxy value is non-numeric") from exc
                if not math.isfinite(value):
                    raise ContractValidationError("M5C static proxy value is non-finite")
                values[index] = value
                seen[index] = True
    except (OSError, csv.Error, UnicodeError) as exc:
        if isinstance(exc, ContractValidationError):
            raise
        raise ContractValidationError("M5C static proxy source is unreadable") from exc
    if not bool(seen.all()):
        raise ContractValidationError("M5C static proxy does not exact-cover candidates")
    return values


def _fraction_key(fraction: float) -> str:
    return {0.005: "0.005", 0.01: "0.01", 0.02: "0.02"}[fraction]


def _average_ranks(values: NDArray[np.float64]) -> NDArray[np.float64]:
    order = np.argsort(values, kind="mergesort")
    ranked = values[order]
    starts = np.concatenate(([0], np.flatnonzero(ranked[1:] != ranked[:-1]) + 1))
    ends = np.concatenate((starts[1:], [len(values)]))
    ranks = np.empty(len(values), dtype=np.float64)
    for start, end in zip(starts, ends, strict=True):
        ranks[order[start:end]] = (start + end - 1) / 2 + 1
    return ranks


class M5CTruthEvaluator:
    """Truth-owning capability that emits only revealed labels or aggregates."""

    def __init__(self, candidate_ids: Sequence[str], truth: NDArray[np.float64]) -> None:
        ids = tuple(candidate_ids)
        values = np.asarray(truth, dtype=np.float64)
        if (
            not ids
            or len(ids) != len(set(ids))
            or any(not _SHA256.fullmatch(candidate_id) for candidate_id in ids)
            or values.shape != (len(ids),)
            or not np.isfinite(values).all()
        ):
            raise ContractValidationError("M5C truth capability identity is invalid")
        self.__candidate_ids = ids
        self.__index = {candidate_id: index for index, candidate_id in enumerate(ids)}
        self.__truth = values.copy()
        self.__memberships: dict[float, NDArray[np.bool_]] = {}
        self.__definitions: dict[str, dict[str, int]] = {}
        for fraction in HIGH_VALUE_FRACTIONS:
            nominal = math.ceil(fraction * len(values))
            boundary = float(np.partition(values, len(values) - nominal)[len(values) - nominal])
            membership = values >= boundary
            actual = int(membership.sum())
            self.__memberships[fraction] = membership
            self.__definitions[_fraction_key(fraction)] = {
                "actual_count": actual,
                "boundary_tie_count": actual - nominal,
                "nominal_count": nominal,
            }

    @property
    def definition_summary(self) -> Mapping[str, Mapping[str, int]]:
        return MappingProxyType(
            {key: MappingProxyType(dict(value)) for key, value in self.__definitions.items()}
        )

    def _indices(self, candidate_ids: Sequence[str]) -> NDArray[np.int64]:
        ids = tuple(candidate_ids)
        if len(ids) != len(set(ids)):
            raise ContractValidationError("M5C evaluator selection contains duplicates")
        try:
            return np.asarray([self.__index[candidate_id] for candidate_id in ids], dtype=np.int64)
        except KeyError as exc:
            raise ContractValidationError("M5C evaluator selection is outside the pool") from exc

    def reveal(self, candidate_ids: Sequence[str]) -> tuple[Observation, ...]:
        """Reveal only the prospectively selected observation prefix."""

        ids = tuple(candidate_ids)
        indices = self._indices(ids)
        return tuple(
            Observation(candidate_id=candidate_id, label=float(self.__truth[index]))
            for candidate_id, index in zip(ids, indices, strict=True)
        )

    def evaluate_batch(
        self, observation_prefix_ids: Sequence[str], selected_ids: Sequence[str]
    ) -> dict[str, object]:
        """Evaluate one branch without returning truth or high-value membership."""

        prefix = tuple(observation_prefix_ids)
        selected = tuple(selected_ids)
        if not prefix or len(selected) != 16 or set(prefix) & set(selected):
            raise ContractValidationError("M5C evaluator branch boundary is invalid")
        prefix_indices = self._indices(prefix)
        selected_indices = self._indices(selected)
        before_best = float(np.max(self.__truth[prefix_indices]))
        after_best = max(before_best, float(np.max(self.__truth[selected_indices])))
        improvement = after_best - before_best
        high_value: dict[str, object] = {}
        for fraction in HIGH_VALUE_FRACTIONS:
            membership = self.__memberships[fraction]
            count = int(membership[selected_indices].sum())
            actual = self.__definitions[_fraction_key(fraction)]["actual_count"]
            high_value[_fraction_key(fraction)] = {
                "count": count,
                "recall": count / actual,
            }
        return {
            "best_found_improvement": improvement,
            "high_value": high_value,
            "simple_regret_reduction": improvement,
        }

    def evaluate_static_proxy(self, proxy: NDArray[np.float64]) -> dict[str, object]:
        """Evaluate the isolated ESM1v baseline and return aggregate-only metrics."""

        values = np.asarray(proxy, dtype=np.float64)
        if values.shape != self.__truth.shape or not np.isfinite(values).all():
            raise ContractValidationError("M5C static proxy does not exact-cover finite candidates")
        truth_ranks = _average_ranks(self.__truth)
        proxy_ranks = _average_ranks(values)
        truth_centered = truth_ranks - float(np.mean(truth_ranks))
        proxy_centered = proxy_ranks - float(np.mean(proxy_ranks))
        denominator = math.sqrt(
            float(np.dot(truth_centered, truth_centered))
            * float(np.dot(proxy_centered, proxy_centered))
        )
        spearman = (
            None
            if denominator == 0
            else float(np.dot(truth_centered, proxy_centered)) / denominator
        )
        high_value: dict[str, object] = {}
        ids = np.asarray(self.__candidate_ids, dtype="U64")
        order = np.lexsort((ids, -values))
        for fraction in HIGH_VALUE_FRACTIONS:
            key = _fraction_key(fraction)
            nominal = self.__definitions[key]["nominal_count"]
            selected = order[:nominal]
            count = int(self.__memberships[fraction][selected].sum())
            high_value[key] = {
                "discovery_count": count,
                "recall": count / self.__definitions[key]["actual_count"],
                "selected_count": nominal,
            }
        return {
            "defined": spearman is not None,
            "high_value_recall": high_value,
            "spearman": spearman,
        }


def load_truth_evaluator(
    path: Path,
    *,
    candidate_table: CandidateTable,
    expected_sha256: str,
    expected_size: int,
) -> M5CTruthEvaluator:
    """Open exact-bound hidden truth only inside the evaluator capability."""

    _require_exact_file(path, expected_sha256, expected_size)
    values = np.empty(len(candidate_table.candidate_ids), dtype=np.float64)
    seen = np.zeros(len(candidate_table.candidate_ids), dtype=np.bool_)
    id_to_index = {
        candidate_id: index for index, candidate_id in enumerate(candidate_table.candidate_ids)
    }
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ContractValidationError("M5C hidden truth is empty") from exc
            if header != ["candidate_id", "DMS_score"]:
                raise ContractValidationError("M5C hidden-truth fields drifted")
            for row in reader:
                if len(row) != 2:
                    raise ContractValidationError("M5C hidden-truth row shape drifted")
                index = id_to_index.get(row[0])
                if index is None or seen[index]:
                    raise ContractValidationError("M5C hidden-truth membership drifted")
                try:
                    value = float(row[1])
                except ValueError as exc:
                    raise ContractValidationError("M5C hidden-truth value is non-numeric") from exc
                if not math.isfinite(value):
                    raise ContractValidationError("M5C hidden-truth value is non-finite")
                values[index] = value
                seen[index] = True
    except (OSError, gzip.BadGzipFile, csv.Error, UnicodeError) as exc:
        if isinstance(exc, ContractValidationError):
            raise
        raise ContractValidationError("M5C hidden truth is unreadable") from exc
    if not bool(seen.all()):
        raise ContractValidationError("M5C hidden truth does not exact-cover candidates")
    return M5CTruthEvaluator(candidate_table.candidate_ids, values)


def evaluate_state(
    selection: StateSelection,
    evaluator: M5CTruthEvaluator,
) -> tuple[dict[str, object], StateEffects]:
    """Turn one private selection into the frozen aggregate-only state record."""

    greedy = evaluator.evaluate_batch(selection.initial_ids, selection.greedy_ids)
    exploration = evaluator.evaluate_batch(selection.initial_ids, selection.exploration_ids)
    matched = tuple(
        evaluator.evaluate_batch(selection.initial_ids, batch) for batch in selection.matched_ids
    )
    if len(matched) != MATCHED_REPLICATES:
        raise ContractValidationError("M5C matched evaluation count drifted")
    primary = "0.01"
    e_recall = float(cast(dict[str, object], exploration["high_value"])[primary]["recall"])
    g_recall = float(cast(dict[str, object], greedy["high_value"])[primary]["recall"])
    matched_recall = tuple(
        float(cast(dict[str, object], item["high_value"])[primary]["recall"]) for item in matched
    )
    effects = StateEffects(
        assay_id=selection.assay_id,
        exploration_minus_greedy=e_recall - g_recall,
        exploration_minus_matched=tuple(e_recall - value for value in matched_recall),
        initial_sample_size=selection.initial_sample_size,
        model_id=selection.model_id,
        run_seed=selection.run_seed,
        state_id=selection.state_id,
    )
    aggregate = {
        "acquisition_id": _EXPLORATION_IDENTITIES[selection.model_id][0],
        "acquisition_sha256": _EXPLORATION_IDENTITIES[selection.model_id][1],
        "assay_id": selection.assay_id,
        "branches": {
            "exploration": exploration,
            "greedy": greedy,
            "matched_random": list(matched),
        },
        "divergence_count": BATCH_SIZE
        - len(set(selection.exploration_ids) & set(selection.greedy_ids)),
        "initial_sample_size": selection.initial_sample_size,
        "greedy_acquisition_id": "greedy-predictive-mean-v1",
        "greedy_acquisition_sha256": _GREEDY_CONFIG_SHA256,
        "model_config_sha256": hashlib.sha256(
            m4_model_config_bytes(selection.model_id)
        ).hexdigest(),
        "model_id": selection.model_id,
        "run_seed": selection.run_seed,
        "state_id": selection.state_id,
    }
    if "candidate_id" in json.dumps(aggregate, allow_nan=False):
        raise ContractValidationError("M5C state aggregate leaks candidate identity")
    return aggregate, effects


def _mean_state_contrasts(
    states: Sequence[Mapping[str, object]], fraction: str
) -> tuple[float, float]:
    e_g: list[float] = []
    e_m: list[float] = []
    for state in states:
        branches = cast(Mapping[str, object], state["branches"])
        exploration = cast(Mapping[str, object], branches["exploration"])
        greedy = cast(Mapping[str, object], branches["greedy"])
        matched = cast(Sequence[Mapping[str, object]], branches["matched_random"])
        e = float(cast(Mapping[str, object], exploration["high_value"])[fraction]["recall"])
        g = float(cast(Mapping[str, object], greedy["high_value"])[fraction]["recall"])
        m = (
            math.fsum(
                float(cast(Mapping[str, object], item["high_value"])[fraction]["recall"])
                for item in matched
            )
            / MATCHED_REPLICATES
        )
        e_g.append(e - g)
        e_m.append(e - m)
    return math.fsum(e_g) / len(e_g), math.fsum(e_m) / len(e_m)


def build_assay_aggregate(
    *,
    assay_id: str,
    candidate_count: int,
    evaluator: M5CTruthEvaluator,
    state_aggregates: Sequence[Mapping[str, object]],
    state_effects: Sequence[StateEffects],
    static_proxy: Mapping[str, object],
) -> dict[str, object]:
    """Build one complete, candidate-free assay report and taxonomy."""

    if len(state_aggregates) != 40 or len(state_effects) != 40:
        raise ContractValidationError("M5C assay report does not exact-cover 40 states")
    summaries = bootstrap_assay_effects(
        state_effects,
        assay_id=assay_id,
        delta=1 / math.ceil(0.01 * candidate_count),
    )
    by_contrast = {item.contrast: item for item in summaries}
    sensitivity = {
        fraction: _mean_state_contrasts(state_aggregates, fraction)
        for fraction in ("0.005", "0.02")
    }
    definitions = {key: dict(value) for key, value in evaluator.definition_summary.items()}
    taxonomy_input = AssayTaxonomyInput(
        actual_primary_high_value_count=definitions["0.01"]["actual_count"],
        assay_id=assay_id,
        exploration_greedy_difference_counts=tuple(
            int(state["divergence_count"]) for state in state_aggregates
        ),
        frozen_candidate_count=candidate_count,
        primary_e_g=by_contrast["E-G"],
        primary_e_m=by_contrast["E-M"],
        # The static proxy is descriptive only and cannot change the taxonomy.
        required_defined=True,
        sensitivity_e_g_point_estimates=tuple(
            sensitivity[fraction][0] for fraction in ("0.005", "0.02")
        ),
        sensitivity_e_m_point_estimates=tuple(
            sensitivity[fraction][1] for fraction in ("0.005", "0.02")
        ),
        validity_ok=True,
    )
    report: dict[str, object] = {
        "assay_id": assay_id,
        "candidate_count": candidate_count,
        "complete_state_count": len(state_aggregates),
        "divergence_counts": list(taxonomy_input.exploration_greedy_difference_counts),
        "high_value_definitions": definitions,
        "primary_contrasts": {item.contrast: asdict(item) for item in summaries},
        "schema_version": "m5c-proteingym-assay-aggregate-v1",
        "sensitivity_point_estimates": {
            fraction: {"E-G": values[0], "E-M": values[1]}
            for fraction, values in sensitivity.items()
        },
        "state_aggregates": list(state_aggregates),
        "static_proxy": dict(static_proxy),
        "taxonomy": classify_assay(taxonomy_input),
        "validity_ok": True,
    }
    encoded = json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True)
    if "candidate_id" in encoded or "initial_ids" in encoded:
        raise ContractValidationError("M5C assay aggregate leaks candidate-level data")
    return report


def _effect_from_aggregate(state: Mapping[str, object]) -> StateEffects:
    try:
        branches = cast(Mapping[str, object], state["branches"])
        exploration = cast(Mapping[str, object], branches["exploration"])
        greedy = cast(Mapping[str, object], branches["greedy"])
        matched = cast(Sequence[Mapping[str, object]], branches["matched_random"])
        e = float(cast(Mapping[str, object], exploration["high_value"])["0.01"]["recall"])
        g = float(cast(Mapping[str, object], greedy["high_value"])["0.01"]["recall"])
        m = tuple(
            float(cast(Mapping[str, object], item["high_value"])["0.01"]["recall"])
            for item in matched
        )
        return StateEffects(
            assay_id=cast(str, state["assay_id"]),
            exploration_minus_greedy=e - g,
            exploration_minus_matched=tuple(e - value for value in m),
            initial_sample_size=cast(int, state["initial_sample_size"]),
            model_id=cast(str, state["model_id"]),
            run_seed=cast(int, state["run_seed"]),
            state_id=cast(str, state["state_id"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ContractValidationError("M5C aggregate state is invalid") from None


def verify_assay_aggregate(report: Mapping[str, object]) -> None:
    """Recompute every registered classifier input from sealed aggregates only."""

    try:
        assay_id = cast(str, report["assay_id"])
        candidate_count = cast(int, report["candidate_count"])
        states = cast(Sequence[Mapping[str, object]], report["state_aggregates"])
        definitions = cast(Mapping[str, Mapping[str, int]], report["high_value_definitions"])
        divergences = tuple(int(item["divergence_count"]) for item in states)
        effects = tuple(_effect_from_aggregate(item) for item in states)
    except (KeyError, TypeError, ValueError):
        raise ContractValidationError("M5C aggregate reproduction input is invalid") from None
    if assay_id not in ASSAY_IDS or type(candidate_count) is not int or candidate_count <= 0:
        raise ContractValidationError("M5C aggregate reproduction identity is invalid")
    summaries = bootstrap_assay_effects(
        effects,
        assay_id=assay_id,
        delta=1 / math.ceil(0.01 * candidate_count),
    )
    by_contrast = {item.contrast: item for item in summaries}
    sensitivity = {
        fraction: _mean_state_contrasts(states, fraction) for fraction in ("0.005", "0.02")
    }
    try:
        actual_primary_count = definitions["0.01"]["actual_count"]
    except (KeyError, TypeError):
        raise ContractValidationError("M5C aggregate high-value definition is invalid") from None
    taxonomy_input = AssayTaxonomyInput(
        actual_primary_high_value_count=actual_primary_count,
        assay_id=assay_id,
        exploration_greedy_difference_counts=divergences,
        frozen_candidate_count=candidate_count,
        primary_e_g=by_contrast["E-G"],
        primary_e_m=by_contrast["E-M"],
        required_defined=True,
        sensitivity_e_g_point_estimates=tuple(
            sensitivity[fraction][0] for fraction in ("0.005", "0.02")
        ),
        sensitivity_e_m_point_estimates=tuple(
            sensitivity[fraction][1] for fraction in ("0.005", "0.02")
        ),
        validity_ok=True,
    )
    expected_primary = {item.contrast: asdict(item) for item in summaries}
    expected_sensitivity = {
        fraction: {"E-G": values[0], "E-M": values[1]} for fraction, values in sensitivity.items()
    }
    if (
        report.get("complete_state_count") != 40
        or report.get("divergence_counts") not in (divergences, list(divergences))
        or compact_json_bytes(report.get("primary_contrasts"))
        != compact_json_bytes(expected_primary)
        or report.get("sensitivity_point_estimates") != expected_sensitivity
        or report.get("taxonomy") != classify_assay(taxonomy_input)
        or report.get("validity_ok") is not True
    ):
        raise ContractValidationError("M5C aggregate reproduction mismatch")


def build_cohort_report(
    assay_reports: Sequence[Mapping[str, object]], *, attempt_id: str
) -> dict[str, object]:
    """Apply the registered assay-first cohort roll-up without pooling seeds."""

    if not _ATTEMPT.fullmatch(attempt_id):
        raise ContractValidationError("M5C cohort attempt identity is invalid")
    labels: dict[str, str] = {}
    statuses: dict[str, dict[str, str]] = {}
    effects: dict[str, list[float]] = {"E-G": [], "E-M": []}
    for report in assay_reports:
        assay_id = report.get("assay_id")
        taxonomy = report.get("taxonomy")
        if (
            not isinstance(assay_id, str)
            or assay_id not in ASSAY_IDS
            or assay_id in labels
            or not isinstance(taxonomy, str)
        ):
            raise ContractValidationError("M5C cohort assay report drifted")
        contrasts = cast(Mapping[str, Mapping[str, object]], report.get("primary_contrasts"))
        if set(contrasts) != {"E-G", "E-M"}:
            raise ContractValidationError("M5C cohort primary contrasts drifted")
        labels[assay_id] = taxonomy
        statuses[assay_id] = {}
        for contrast in ("E-G", "E-M"):
            status = contrasts[contrast].get("status")
            point = contrasts[contrast].get("point_estimate")
            if not isinstance(status, str) or not isinstance(point, (int, float)):
                raise ContractValidationError("M5C cohort contrast result drifted")
            statuses[assay_id][contrast] = status
            effects[contrast].append(float(point))
    classification = classify_cohort(labels, primary_statuses=statuses)
    label_counts = dict(sorted(Counter(labels.values()).items()))
    return {
        "assay_reports": list(assay_reports),
        "attempt_id": attempt_id,
        "attempt_selection": "first-complete-valid-terminal",
        "cohort_status": classification.status,
        "descriptive_effects": {
            contrast: {
                "maximum": max(values) if values else None,
                "mean": math.fsum(values) / len(values) if values else None,
                "minimum": min(values) if values else None,
            }
            for contrast, values in effects.items()
        },
        "eligible_assay_count": len(labels),
        "heterogeneity_reason": classification.heterogeneity_reason,
        "label_counts": label_counts,
        "schema_version": "m5c-proteingym-cohort-report-v1",
    }


def build_run_identity(
    *,
    attempt_id: str,
    contract_digest: str,
    input_manifest_digest: str,
    git_commit: str,
    git_tree: str,
    environment_digest: str,
) -> bytes:
    """Build the pre-side-effect Governance v2 Run-pinned identity."""

    if (
        not _ATTEMPT.fullmatch(attempt_id)
        or any(
            not _SHA256.fullmatch(value)
            for value in (contract_digest, input_manifest_digest, environment_digest)
        )
        or any(not _GIT.fullmatch(value) for value in (git_commit, git_tree))
    ):
        raise ContractValidationError("M5C run identity or attempt field is invalid")
    value: dict[str, object] = {
        "attempt_id": attempt_id,
        "attempt_root": f"/srv/pcdb/runs/M5C/{attempt_id}",
        "contract_digest": contract_digest,
        "cos_write_count": 0,
        "environment_digest": environment_digest,
        "git_commit": git_commit,
        "git_tree": git_tree,
        "input_manifest_digest": input_manifest_digest,
        "observed_usage": {
            "cost": 0,
            "cpu_seconds": 0,
            "network_bytes": 0,
            "peak_rss_bytes": 0,
            "scratch_bytes": 0,
            "wall_seconds": 0,
        },
        "resource_caps": dict(_MAIN_CAPS),
        "schema_version": "pcdb-governance-run-identity-v1",
        "source_clean": True,
    }
    value["run_id"] = hashlib.sha256(compact_json_bytes(dict(sorted(value.items())))).hexdigest()
    return compact_json_bytes(dict(sorted(value.items())))


def write_once(path: Path, payload: bytes) -> None:
    """Create and fsync one append-only artifact without overwrite fallback."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


__all__ = [
    "CandidateTable",
    "FeatureArtifact",
    "M5CTruthEvaluator",
    "StateSelection",
    "build_assay_aggregate",
    "build_cohort_report",
    "build_feature_artifact",
    "build_run_identity",
    "evaluate_state",
    "load_candidate_table",
    "load_static_proxy",
    "load_truth_evaluator",
    "select_state",
    "verify_assay_aggregate",
    "write_once",
]
