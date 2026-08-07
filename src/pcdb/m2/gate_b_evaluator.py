"""Capability-separated aggregate evaluator for the frozen Issue #58 cohort."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_high_value_result,
)
from pcdb.contracts.prediction import load_prediction_artifact
from pcdb.evaluation.high_value import HighValueEvaluator, MetricValue
from pcdb.m2.gate_b_runtime import GateBEvaluationRequest, ObjectMetadata

_PUBLIC_BUCKET = "pcdb-public-data-1408226911"
_TASK_OBJECTS = {
    "alpha-amylase": {
        "processing": (
            "manifests/flip2/zenodo-18433203-v3/amylase/processing/"
            "flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b/"
            "processing-manifest.json"
        ),
        "split": (
            "manifests/flip2/zenodo-18433203-v3/amylase/splits/"
            "flip2-amylase-topology-isolation-v1/"
            "244111b3eb4507563602851dfa730e7f5f297eea27447a0e22b59d1dc40fb685/"
            "split-manifest.json"
        ),
        "truth": (
            "processed/flip2/zenodo-18433203-v3/amylase/"
            "flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b/"
            "hidden-truth.csv"
        ),
        "membership": (
            "splits/flip2/zenodo-18433203-v3/amylase/"
            "flip2-amylase-topology-isolation-v1/"
            "244111b3eb4507563602851dfa730e7f5f297eea27447a0e22b59d1dc40fb685/"
            "membership.csv"
        ),
    },
    "ired": {
        "processing": (
            "manifests/flip2/zenodo-18433203-v3/ired/processing/"
            "flip2-ired-8f32dfa11a52d3ce96da4eb4ebb305210ccf027cf812d277f2c9c25f254c4f5d/"
            "processing-manifest.json"
        ),
        "split": (
            "manifests/flip2/zenodo-18433203-v3/ired/splits/"
            "flip2-ired-topology-extrapolation-v1/"
            "9dde9ee30c6a30c5a691eb2eeae4782093245e0cc8d93cdfc72c7c69af765995/"
            "split-manifest.json"
        ),
        "truth": (
            "processed/flip2/zenodo-18433203-v3/ired/"
            "flip2-ired-8f32dfa11a52d3ce96da4eb4ebb305210ccf027cf812d277f2c9c25f254c4f5d/"
            "hidden-truth.csv"
        ),
        "membership": (
            "splits/flip2/zenodo-18433203-v3/ired/"
            "flip2-ired-topology-extrapolation-v1/"
            "9dde9ee30c6a30c5a691eb2eeae4782093245e0cc8d93cdfc72c7c69af765995/"
            "membership.csv"
        ),
    },
}


class ReadOnlyObjects(Protocol):
    """The evaluator has no listing, PUT, overwrite, or delete capability."""

    def head(self, bucket: str, key: str) -> ObjectMetadata: ...

    def get(self, bucket: str, key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class TruthBundle:
    """Ephemeral evaluator-only truth; this type is never serialised or published."""

    candidate_ids: frozenset[str]
    truth_rows: tuple[tuple[str, float], ...]


class TruthBundleReader(Protocol):
    def read(self, task_id: str, run_spec: Mapping[str, Any]) -> TruthBundle: ...


def _fresh(objects: ReadOnlyObjects, key: str, reference: Mapping[str, Any]) -> bytes:
    if reference.get("object_key") != key:
        raise ContractValidationError("evaluator manifest object key drifted")
    metadata = objects.head(_PUBLIC_BUCKET, key)
    payload = objects.get(_PUBLIC_BUCKET, key)
    if (
        metadata.size_bytes != reference.get("size_bytes")
        or len(payload) != reference.get("size_bytes")
        or hashlib.sha256(payload).hexdigest() != reference.get("sha256")
    ):
        raise ContractValidationError("evaluator fresh object identity drifted")
    return payload


def _json_object(payload: bytes, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{field} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be an object")
    return value


def _membership(payload: bytes) -> tuple[set[str], set[str]]:
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True)
    except UnicodeDecodeError as exc:
        raise ContractValidationError("membership must be UTF-8") from exc
    if reader.fieldnames != ["candidate_id", "role", "group_id"]:
        raise ContractValidationError("membership fields drifted")
    all_ids: set[str] = set()
    candidates: set[str] = set()
    try:
        for row in reader:
            candidate = row.get("candidate_id")
            role = row.get("role")
            if not candidate or candidate in all_ids or not row.get("group_id"):
                raise ContractValidationError("membership row drifted")
            if role not in {"development", "validation", "candidate-pool", "held-out"}:
                raise ContractValidationError("membership role drifted")
            all_ids.add(candidate)
            if role == "candidate-pool":
                candidates.add(candidate)
    except csv.Error as exc:
        raise ContractValidationError("membership CSV is invalid") from exc
    if not candidates:
        raise ContractValidationError("candidate pool is empty")
    return all_ids, candidates


def _truth(payload: bytes, expected: set[str]) -> dict[str, float]:
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True)
    except UnicodeDecodeError as exc:
        raise ContractValidationError("hidden truth must be UTF-8") from exc
    if reader.fieldnames != ["candidate_id", "target"]:
        raise ContractValidationError("hidden truth fields drifted")
    values: dict[str, float] = {}
    try:
        for row in reader:
            candidate = row.get("candidate_id")
            if not candidate or candidate in values:
                raise ContractValidationError("hidden truth row drifted")
            try:
                target = float(str(row.get("target")))
            except ValueError as exc:
                raise ContractValidationError("hidden truth target drifted") from exc
            if not math.isfinite(target):
                raise ContractValidationError("hidden truth target must be finite")
            values[candidate] = target
    except csv.Error as exc:
        raise ContractValidationError("hidden truth CSV is invalid") from exc
    if set(values) != expected:
        raise ContractValidationError("hidden truth does not exact-cover split membership")
    return values


def _metric_state(metric: MetricValue) -> tuple[str, float | bool | None, str | None]:
    return metric.status, metric.value, metric.undefined_reason


class CosTruthBundleReader:
    """Fresh-read the exact four truth-chain objects using read-only capability."""

    def __init__(self, objects: ReadOnlyObjects) -> None:
        self._objects = objects

    def read(self, task_id: str, run_spec: Mapping[str, Any]) -> TruthBundle:
        paths = _TASK_OBJECTS.get(task_id)
        if paths is None:
            raise ContractValidationError("Gate B evaluator task drifted")
        processing_ref = {
            "object_key": paths["processing"],
            "sha256": run_spec["processing_manifest_sha256"],
        }
        split_ref = {
            "object_key": paths["split"],
            "sha256": run_spec["split_sha256"],
        }
        processing_head = self._objects.head(_PUBLIC_BUCKET, paths["processing"])
        processing_ref["size_bytes"] = processing_head.size_bytes
        processing_bytes = _fresh(self._objects, paths["processing"], processing_ref)
        split_head = self._objects.head(_PUBLIC_BUCKET, paths["split"])
        split_ref["size_bytes"] = split_head.size_bytes
        split_bytes = _fresh(self._objects, paths["split"], split_ref)
        processing = _json_object(processing_bytes, "processing manifest")
        split = _json_object(split_bytes, "split manifest")
        truth_refs = [
            item
            for item in processing.get("output_files", [])
            if isinstance(item, dict) and item.get("object_key") == paths["truth"]
        ]
        if (
            len(truth_refs) != 1
            or split.get("membership_file", {}).get("object_key") != paths["membership"]
        ):
            raise ContractValidationError("evaluator manifest linkage drifted")
        membership_bytes = _fresh(self._objects, paths["membership"], split["membership_file"])
        truth_bytes = _fresh(self._objects, paths["truth"], truth_refs[0])
        all_ids, candidate_ids = _membership(membership_bytes)
        truth = _truth(truth_bytes, all_ids)
        return TruthBundle(
            candidate_ids=frozenset(candidate_ids),
            truth_rows=tuple((candidate, truth[candidate]) for candidate in sorted(candidate_ids)),
        )


class FrozenM2AggregateEvaluator:
    """Read truth only here and return four canonical aggregate-only artifacts."""

    def __init__(self, truth_reader: TruthBundleReader) -> None:
        self._truth_reader = truth_reader

    def evaluate(self, request: object) -> Mapping[str, bytes]:
        if not isinstance(request, GateBEvaluationRequest):
            raise ContractValidationError("Gate B evaluator request type drifted")
        run_spec = request.run_spec
        truth = self._truth_reader.read(request.task_id, run_spec)

        prediction = load_prediction_artifact(request.prediction_bytes)
        if (
            prediction.value["run_id"] != request.run_id
            or prediction.value["attempt_id"] != request.attempt_id
            or prediction.value["task_id"] != request.task_id
            or prediction.value["model_id"] != request.model_id
            or set(prediction.candidate_ids) != truth.candidate_ids
        ):
            raise ContractValidationError("evaluator sealed prediction binding drifted")
        prediction_rows = tuple(zip(prediction.candidate_ids, prediction.predictions, strict=True))
        evaluator = HighValueEvaluator(truth.truth_rows)
        experiment = _json_object(request.experiment_config_bytes, "experiment config")
        if (
            hashlib.sha256(request.experiment_config_bytes).hexdigest()
            != run_spec["experiment_config_sha256"]
        ):
            raise ContractValidationError("evaluator experiment config identity drifted")

        results: dict[str, bytes] = {}
        for key in request.output_object_keys:
            definition_id: str | None = None
            definition: Mapping[str, Any] | None = None
            if key.endswith("metric-spearman-rank-correlation.json"):
                metric_id = "spearman_rank_correlation"
                metric = evaluator.spearman_rank_correlation(prediction_rows)
                support: dict[str, Any] = {"evaluated_candidate_count": len(truth.candidate_ids)}
            else:
                metric_id = "high_value_recall_at_nominal_k"
                matches = [
                    item
                    for item in experiment["evaluation"]["high_value_definitions"]
                    if f"metric-high-value-recall-{item['definition_id']}.json" in key
                ]
                if len(matches) != 1:
                    raise ContractValidationError("evaluator metric output key drifted")
                definition = matches[0]
                definition_id = str(definition["definition_id"])
                fraction = float(definition["derivation"]["fraction"])
                summary = evaluator.definition_summary(fraction=fraction)
                metric = evaluator.high_value_recall_at_nominal_k(
                    prediction_rows, fraction=fraction
                )
                support = {
                    "evaluated_candidate_count": len(truth.candidate_ids),
                    "actual_high_value_count": summary.actual_high_value_count,
                    "boundary_score": summary.boundary_score,
                    "boundary_tie_count": summary.boundary_tie_count,
                    "frozen_candidate_count": definition["frozen_candidate_count"],
                    "nominal_k": definition["derivation"]["nominal_k"],
                }
            status, value, reason = _metric_state(metric)
            payload = canonical_json_bytes(
                {
                    "schema_version": "high-value-metric-result-v1",
                    "experiment_config_sha256": run_spec["experiment_config_sha256"],
                    "dataset_manifest_sha256": run_spec["dataset_manifest_sha256"],
                    "processing_manifest_sha256": run_spec["processing_manifest_sha256"],
                    "split_sha256": run_spec["split_sha256"],
                    "run_id": request.run_id,
                    "attempt_id": request.attempt_id,
                    "input_artifact_sha256": prediction.sha256,
                    "task_id": request.task_id,
                    "model_id": request.model_id,
                    "model_config_sha256": run_spec["model_config_sha256"],
                    "acquisition_id": run_spec["acquisition_id"],
                    "acquisition_config_sha256": run_spec["acquisition_config_sha256"],
                    "seed": run_spec["seed"],
                    "candidate_role": "candidate-pool",
                    "definition_id": definition_id,
                    "metric_id": metric_id,
                    "evaluation_scope": "m2-static",
                    "round": None,
                    "budget": None,
                    "status": status,
                    "value": value,
                    "undefined_reason": reason,
                    "support": support,
                }
            )
            load_high_value_result(payload, expected_schema_version="high-value-metric-result-v1")
            results[key] = payload
        if len(results) != 4:
            raise ContractValidationError("evaluator did not exact-produce four results")
        return results
