"""Canonical review-only Gate B planning artifacts for Issue #58."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pcdb.contracts.experiment import (
    ContractValidationError,
    LoadedContract,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.feature import load_feature_artifact
from pcdb.contracts.feature_identity import load_feature_bundle_descriptor
from pcdb.contracts.prediction import (
    M2_STATIC_ACQUISITION_CONFIG_BYTES,
    load_prediction_artifact,
    load_run_spec_v1,
)
from pcdb.contracts.run import RunSpec
from pcdb.m2.features import FEATURE_CONFIG_ID
from pcdb.m2.gate_a import GATE_A_MODELS, GATE_A_SEED, GATE_A_TASKS
from pcdb.m2.model_identity import MODEL_CONFIG_SHA256

M3_FREEZE_SCHEMA_VERSION = "m3-anti-post-hoc-freeze-v1"
GATE_A_KNOWN_INPUTS_SCHEMA_VERSION = "m2-gate-a-known-input-index-v1"
GATE_B_PACKET_SCHEMA_VERSION = "m2-gate-b-packet-v1"
GATE_B_OPERATOR_PLAN_SCHEMA_VERSION = "m2-gate-b-operator-plan-v1"
M3_MINIMUM_MODELS = (
    "random-forest-sklearn-v1",
    "ridge-sklearn-v1",
    "xgboost-cpu-v1",
)

_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROHIBITED_CHANGES = (
    "acquisition-config",
    "feature-config",
    "model-config",
    "model-set",
    "seed",
)
_AUTHORIZATION_BOUNDARIES = {
    "candidate_truth_consumer": "capability-separated-evaluator-after-sealed-prediction",
    "gate_2_authorized": False,
    "held_out_reads": 0,
    "m3_execution_authorized": False,
}
_EXPERIMENT_PATHS = {
    "alpha-amylase": "configs/experiments/m2-static/alpha-amylase.json",
    "ired": "configs/experiments/m2-static/ired.json",
}
_ENVIRONMENT_PATH = "configs/environments/m2-gate-a-linux-x86_64.json"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_METRIC_OBJECT_NAMES = (
    "metric-spearman-rank-correlation.json",
    "metric-high-value-recall-upper-tail-0.5pct.json",
    "metric-high-value-recall-upper-tail-1pct.json",
    "metric-high-value-recall-upper-tail-2pct.json",
)
_EXTERNAL_MANIFEST_REFERENCES = (
    {
        "bucket": "pcdb-public-data-1408226911",
        "object_key": "manifests/flip2/zenodo-18433203-v3/amylase/dataset-manifest.v2.json",
        "sha256": "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac",
        "size_bytes": 2101,
        "task_id": "alpha-amylase",
        "type": "dataset-manifest",
    },
    {
        "bucket": "pcdb-public-data-1408226911",
        "object_key": (
            "manifests/flip2/zenodo-18433203-v3/amylase/processing/"
            "flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b/"
            "processing-manifest.json"
        ),
        "sha256": "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec",
        "size_bytes": 2429,
        "task_id": "alpha-amylase",
        "type": "processing-manifest",
    },
    {
        "bucket": "pcdb-public-data-1408226911",
        "object_key": (
            "manifests/flip2/zenodo-18433203-v3/amylase/splits/"
            "flip2-amylase-topology-isolation-v1/"
            "244111b3eb4507563602851dfa730e7f5f297eea27447a0e22b59d1dc40fb685/"
            "split-manifest.json"
        ),
        "sha256": "c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a",
        "size_bytes": 2770,
        "task_id": "alpha-amylase",
        "type": "split-manifest",
    },
    {
        "bucket": "pcdb-public-data-1408226911",
        "object_key": "manifests/flip2/zenodo-18433203-v3/ired/dataset-manifest.json",
        "sha256": "37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643",
        "size_bytes": 1599,
        "task_id": "ired",
        "type": "dataset-manifest",
    },
    {
        "bucket": "pcdb-public-data-1408226911",
        "object_key": (
            "manifests/flip2/zenodo-18433203-v3/ired/processing/"
            "flip2-ired-8f32dfa11a52d3ce96da4eb4ebb305210ccf027cf812d277f2c9c25f254c4f5d/"
            "processing-manifest.json"
        ),
        "sha256": "929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba",
        "size_bytes": 2888,
        "task_id": "ired",
        "type": "processing-manifest",
    },
    {
        "bucket": "pcdb-public-data-1408226911",
        "object_key": (
            "manifests/flip2/zenodo-18433203-v3/ired/splits/"
            "flip2-ired-topology-extrapolation-v1/"
            "9dde9ee30c6a30c5a691eb2eeae4782093245e0cc8d93cdfc72c7c69af765995/"
            "split-manifest.json"
        ),
        "sha256": "9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e",
        "size_bytes": 3211,
        "task_id": "ired",
        "type": "split-manifest",
    },
)
_ACCEPTED_INPUT_OBJECT_KEYS = (
    "processed/flip2/zenodo-18433203-v3/amylase/"
    "flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b/"
    "candidates.csv",
    "processed/flip2/zenodo-18433203-v3/amylase/"
    "flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b/"
    "hidden-truth.csv",
    "splits/flip2/zenodo-18433203-v3/amylase/flip2-amylase-topology-isolation-v1/"
    "244111b3eb4507563602851dfa730e7f5f297eea27447a0e22b59d1dc40fb685/"
    "membership.csv",
    "processed/flip2/zenodo-18433203-v3/ired/"
    "flip2-ired-8f32dfa11a52d3ce96da4eb4ebb305210ccf027cf812d277f2c9c25f254c4f5d/"
    "candidates.csv",
    "processed/flip2/zenodo-18433203-v3/ired/"
    "flip2-ired-8f32dfa11a52d3ce96da4eb4ebb305210ccf027cf812d277f2c9c25f254c4f5d/"
    "hidden-truth.csv",
    "splits/flip2/zenodo-18433203-v3/ired/flip2-ired-topology-extrapolation-v1/"
    "9dde9ee30c6a30c5a691eb2eeae4782093245e0cc8d93cdfc72c7c69af765995/"
    "membership.csv",
)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractValidationError(f"duplicate object key is forbidden: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ContractValidationError(f"non-finite JSON constant is forbidden: {value}")


def _parse_canonical_object(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise ContractValidationError("empty JSON artifact is forbidden")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ContractValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError("artifact must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("top-level JSON value must be an object")
    if canonical_json_bytes(value) != payload:
        raise ContractValidationError("artifact bytes are not canonical")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def build_m3_freeze_record(
    *,
    source_git_commit: str,
    task_feature_roots: Mapping[str, str],
    run_ids: Mapping[tuple[str, str], str],
) -> bytes:
    """Freeze the six-run cohort before any candidate-truth evaluator call."""

    if not _GIT_COMMIT.fullmatch(source_git_commit):
        raise ContractValidationError("source Git commit must be full lowercase 40-hex")
    if tuple(task_feature_roots) != GATE_A_TASKS:
        raise ContractValidationError("task feature roots must use the frozen task order")
    expected_run_keys = {
        (task_id, model_id) for task_id in GATE_A_TASKS for model_id in GATE_A_MODELS
    }
    if set(run_ids) != expected_run_keys:
        raise ContractValidationError("run IDs must exact-cover the frozen six-run cohort")

    tasks: list[dict[str, Any]] = []
    for task_id in GATE_A_TASKS:
        feature_root = _require_sha256(task_feature_roots[task_id], f"{task_id} feature root")
        runs = [
            {
                "model_id": model_id,
                "run_id": _require_sha256(run_ids[(task_id, model_id)], "run ID"),
            }
            for model_id in M3_MINIMUM_MODELS
        ]
        tasks.append(
            {
                "feature_root_sha256": feature_root,
                "runs": runs,
                "task_id": task_id,
            }
        )

    value = {
        "anti_post_hoc_policy": {
            "preserve_negative_and_discordant_results": True,
            "same_cohort_prohibited_changes": list(_PROHIBITED_CHANGES),
        },
        "authorization_boundaries": dict(_AUTHORIZATION_BOUNDARIES),
        "cohort": {
            "feature_config_id": FEATURE_CONFIG_ID,
            "model_configs": [
                {
                    "model_config_sha256": MODEL_CONFIG_SHA256[model_id],
                    "model_id": model_id,
                }
                for model_id in M3_MINIMUM_MODELS
            ],
            "seed": GATE_A_SEED,
            "tasks": tasks,
        },
        "frozen_before_candidate_truth_read": True,
        "schema_version": M3_FREEZE_SCHEMA_VERSION,
        "source_git_commit": source_git_commit,
    }
    return canonical_json_bytes(value)


def load_m3_freeze_record(payload: bytes) -> LoadedContract:
    """Load an exact canonical M3 freeze record and reject protocol drift."""

    value = _parse_canonical_object(payload)
    expected_top_level = {
        "anti_post_hoc_policy",
        "authorization_boundaries",
        "cohort",
        "frozen_before_candidate_truth_read",
        "schema_version",
        "source_git_commit",
    }
    if set(value) != expected_top_level:
        raise ContractValidationError("M3 freeze top-level fields differ")
    if value["schema_version"] != M3_FREEZE_SCHEMA_VERSION:
        raise ContractValidationError("M3 freeze schema version drifted")
    source_git_commit = value["source_git_commit"]
    if not isinstance(source_git_commit, str) or not _GIT_COMMIT.fullmatch(source_git_commit):
        raise ContractValidationError("source Git commit must be full lowercase 40-hex")
    if value["frozen_before_candidate_truth_read"] is not True:
        raise ContractValidationError("M3 freeze must precede candidate-truth reads")
    if value["authorization_boundaries"] != _AUTHORIZATION_BOUNDARIES:
        raise ContractValidationError("M3 authorization boundaries drifted")
    if value["anti_post_hoc_policy"] != {
        "preserve_negative_and_discordant_results": True,
        "same_cohort_prohibited_changes": list(_PROHIBITED_CHANGES),
    }:
        raise ContractValidationError("M3 anti-post-hoc policy drifted")

    cohort = value["cohort"]
    if not isinstance(cohort, dict):
        raise ContractValidationError("M3 cohort must be an object")
    if cohort.get("seed") != GATE_A_SEED:
        raise ContractValidationError("M3 cohort seed drifted")
    if cohort.get("feature_config_id") != FEATURE_CONFIG_ID:
        raise ContractValidationError("M3 feature config drifted")
    expected_models = [
        {"model_config_sha256": MODEL_CONFIG_SHA256[model_id], "model_id": model_id}
        for model_id in M3_MINIMUM_MODELS
    ]
    if cohort.get("model_configs") != expected_models:
        raise ContractValidationError("M3 model configs drifted")
    tasks = cohort.get("tasks")
    if not isinstance(tasks, list) or [task.get("task_id") for task in tasks] != list(GATE_A_TASKS):
        raise ContractValidationError("M3 tasks drifted")

    task_feature_roots: dict[str, str] = {}
    run_ids: dict[tuple[str, str], str] = {}
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {
            "feature_root_sha256",
            "runs",
            "task_id",
        }:
            raise ContractValidationError("M3 task fields drifted")
        task_id = task["task_id"]
        feature_root = _require_sha256(task["feature_root_sha256"], "feature root")
        runs = task["runs"]
        if not isinstance(runs, list) or [run.get("model_id") for run in runs] != list(
            M3_MINIMUM_MODELS
        ):
            raise ContractValidationError("M3 task model order drifted")
        task_feature_roots[task_id] = feature_root
        for run in runs:
            if not isinstance(run, dict) or set(run) != {"model_id", "run_id"}:
                raise ContractValidationError("M3 run fields drifted")
            run_ids[(task_id, run["model_id"])] = _require_sha256(run["run_id"], "run ID")

    rebuilt = build_m3_freeze_record(
        source_git_commit=source_git_commit,
        task_feature_roots=task_feature_roots,
        run_ids=run_ids,
    )
    if rebuilt != payload:
        raise ContractValidationError("M3 freeze canonical reconstruction drifted")
    return LoadedContract(
        schema_version=M3_FREEZE_SCHEMA_VERSION,
        value=MappingProxyType(value),
        canonical_bytes=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _artifact_reference(logical_path: str, payload: bytes) -> dict[str, object]:
    return {
        "logical_path": logical_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def build_gate_a_known_inputs_index(gate_a_artifact_root: Path) -> bytes:
    """Strict-read accepted Gate A artifacts into a compact review index.

    The returned index contains identities only. It never copies candidate truth or feature rows.
    """

    tasks: list[dict[str, Any]] = []
    source_commit: str | None = None
    shared_model_configs: dict[str, dict[str, object]] = {}
    for task_id in GATE_A_TASKS:
        logical_path = f"m2-preflight/{task_id}/feature-logical.json"
        descriptor_path = f"m2-preflight/{task_id}/feature-descriptor.json"
        logical_bytes = (gate_a_artifact_root / logical_path).read_bytes()
        descriptor_bytes = (gate_a_artifact_root / descriptor_path).read_bytes()
        load_feature_artifact(logical_bytes)
        descriptor = load_feature_bundle_descriptor(descriptor_bytes)
        descriptor_value = json.loads(descriptor_bytes)

        member_descriptors = [
            ("feature-config", None, descriptor_value["feature_config"]),
            *[
                ("feature-matrix", matrix["role"], matrix["artifact"])
                for matrix in descriptor_value["matrices"]
            ],
        ]
        members: list[dict[str, Any]] = []
        for kind, role, member in member_descriptors:
            object_key = member["object_key"]
            member_bytes = (gate_a_artifact_root / object_key).read_bytes()
            if len(member_bytes) != member["size_bytes"]:
                raise ContractValidationError("feature member size differs from descriptor")
            if hashlib.sha256(member_bytes).hexdigest() != member["sha256"]:
                raise ContractValidationError("feature member SHA-256 differs from descriptor")
            entry: dict[str, Any] = {
                "kind": kind,
                "reference": _artifact_reference(object_key, member_bytes),
            }
            if role is not None:
                entry["role"] = role
            members.append(entry)

        runs: list[dict[str, Any]] = []
        for model_id in GATE_A_MODELS:
            run_spec_path = f"m2-preflight/{task_id}/{model_id}/run-spec.json"
            prediction_path = f"m2-preflight/{task_id}/{model_id}/prediction.json"
            model_config_path = f"m2-preflight/{task_id}/{model_id}/model-config.json"
            run_spec_bytes = (gate_a_artifact_root / run_spec_path).read_bytes()
            prediction_bytes = (gate_a_artifact_root / prediction_path).read_bytes()
            model_config_bytes = (gate_a_artifact_root / model_config_path).read_bytes()
            run_spec = load_run_spec_v1(run_spec_bytes)
            prediction = load_prediction_artifact(prediction_bytes)
            if source_commit is None:
                source_commit = run_spec.git_commit
            if run_spec.git_commit != source_commit:
                raise ContractValidationError("Gate A RunSpec source commits differ")
            if run_spec.feature_sha256 != descriptor.sha256:
                raise ContractValidationError("Gate A RunSpec feature root differs")
            if run_spec.model_id != model_id:
                raise ContractValidationError("Gate A RunSpec model differs")
            if hashlib.sha256(model_config_bytes).hexdigest() != run_spec.model_config_sha256:
                raise ContractValidationError("Gate A model config differs from RunSpec")
            prediction_value = prediction.value
            for field, expected in (
                ("task_id", task_id),
                ("model_id", model_id),
                ("run_id", run_spec.run_id),
                ("feature_sha256", descriptor.sha256),
            ):
                if prediction_value[field] != expected:
                    raise ContractValidationError(f"Gate A prediction {field} differs")

            reference = _artifact_reference(model_config_path, model_config_bytes)
            previous = shared_model_configs.get(model_id)
            if previous is None:
                shared_model_configs[model_id] = reference
            elif (
                previous["sha256"] != reference["sha256"]
                or previous["size_bytes"] != reference["size_bytes"]
            ):
                raise ContractValidationError("task model config bytes differ")
            runs.append(
                {
                    "attempt_id": prediction_value["attempt_id"],
                    "model_id": model_id,
                    "prediction": _artifact_reference(prediction_path, prediction_bytes),
                    "run_id": run_spec.run_id,
                    "run_spec": _artifact_reference(run_spec_path, run_spec_bytes),
                    "run_spec_value": json.loads(run_spec_bytes),
                }
            )
        tasks.append(
            {
                "feature": {
                    "descriptor": _artifact_reference(descriptor_path, descriptor_bytes),
                    "logical": _artifact_reference(logical_path, logical_bytes),
                    "members": members,
                },
                "runs": runs,
                "task_id": task_id,
            }
        )

    if source_commit is None:
        raise ContractValidationError("Gate A known inputs contain no RunSpec")
    value = {
        "model_configs": [
            {"model_id": model_id, "reference": shared_model_configs[model_id]}
            for model_id in GATE_A_MODELS
        ],
        "schema_version": GATE_A_KNOWN_INPUTS_SCHEMA_VERSION,
        "source_git_commit": source_commit,
        "tasks": tasks,
    }
    return canonical_json_bytes(value)


def _load_known_inputs(payload: bytes) -> dict[str, Any]:
    value = _parse_canonical_object(payload)
    if set(value) != {"model_configs", "schema_version", "source_git_commit", "tasks"}:
        raise ContractValidationError("Gate A known-input index fields differ")
    if value["schema_version"] != GATE_A_KNOWN_INPUTS_SCHEMA_VERSION:
        raise ContractValidationError("Gate A known-input index version drifted")
    if not isinstance(value["source_git_commit"], str) or not _GIT_COMMIT.fullmatch(
        value["source_git_commit"]
    ):
        raise ContractValidationError("Gate A source Git commit is invalid")
    tasks = value["tasks"]
    if not isinstance(tasks, list) or [item.get("task_id") for item in tasks] != list(GATE_A_TASKS):
        raise ContractValidationError("Gate A known-input tasks drifted")
    for task in tasks:
        runs = task.get("runs")
        if not isinstance(runs, list) or [run.get("model_id") for run in runs] != list(
            GATE_A_MODELS
        ):
            raise ContractValidationError("Gate A known-input runs drifted")
        for run in runs:
            run_id = _require_sha256(run.get("run_id"), "known-input run ID")
            run_spec_value = run.get("run_spec_value")
            if not isinstance(run_spec_value, dict):
                raise ContractValidationError("known-input RunSpec value is required")
            try:
                run_spec = RunSpec(**run_spec_value)
            except (TypeError, ValueError) as exc:
                raise ContractValidationError("known-input RunSpec value is invalid") from exc
            if run_spec.run_id != run_id:
                raise ContractValidationError("known-input RunSpec run ID differs")
            if run.get("run_spec", {}).get("sha256") != run_id:
                raise ContractValidationError("known-input RunSpec exact-byte SHA differs")
    models = value["model_configs"]
    if not isinstance(models, list) or [item.get("model_id") for item in models] != list(
        GATE_A_MODELS
    ):
        raise ContractValidationError("Gate A known-input model configs drifted")
    return value


def _known_payload(reference: Mapping[str, Any], source_kind: str) -> dict[str, Any]:
    sha256 = _require_sha256(reference.get("sha256"), "known payload SHA-256")
    size_bytes = reference.get("size_bytes")
    logical_path = reference.get("logical_path")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise ContractValidationError("known payload size must be non-negative")
    if not isinstance(logical_path, str) or not logical_path:
        raise ContractValidationError("known payload logical path is required")
    return {
        "sha256": sha256,
        "size_bytes": size_bytes,
        "source": {"kind": source_kind, "logical_path": logical_path},
        "status": "known",
    }


def _runtime_payload(generation_rule: str, validation_rule: str) -> dict[str, Any]:
    return {
        "generation_rule": generation_rule,
        "sha256": None,
        "size_bytes": None,
        "status": "runtime-generated",
        "validation_rule": validation_rule,
    }


def _build_gate_b_operator_plan(
    inventory: list[dict[str, Any]],
    first_run_id: str,
    external_manifest_references: tuple[dict[str, object], ...],
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    def step(operation: str, **identities: Any) -> None:
        steps.append(
            {
                **identities,
                "on_failure": "stop-no-retry-no-next-run",
                "operation": operation,
                "sequence": len(steps) + 1,
            }
        )

    for reference in external_manifest_references:
        step(
            "fresh-readback-validate-external-manifest-reference",
            bucket=reference["bucket"],
            object_key=reference["object_key"],
            sha256=reference["sha256"],
            size_bytes=reference["size_bytes"],
            task_id=reference["task_id"],
            type=reference["type"],
        )

    shared = [item for item in inventory if item["scope"] == "shared"]
    for item in shared:
        step(
            "forbid-overwrite-put-known-shared-payload",
            object_key=item["object_key"],
        )
    m3_item = next(item for item in shared if item["kind"] == "m3-freeze-record")
    step(
        "fresh-readback-validate-m3-freeze-record",
        object_key=m3_item["object_key"],
    )

    run_order: list[str] = []
    for item in inventory:
        if item["scope"] == "per-attempt" and item["run_id"] not in run_order:
            run_order.append(item["run_id"])
    for run_id in run_order:
        run_items = [
            item
            for item in inventory
            if item["scope"] == "per-attempt" and item["run_id"] == run_id
        ]
        prediction = next(item for item in run_items if item["kind"] == "prediction")
        metrics = [item for item in run_items if item["kind"] == "metric-result"]
        run_attempt = next(item for item in run_items if item["kind"] == "run-attempt")
        success = next(item for item in run_items if item["kind"] == "success-marker")
        identity = {
            "attempt_id": prediction["attempt_id"],
            "model_id": prediction["model_id"],
            "run_id": run_id,
            "task_id": prediction["task_id"],
        }
        step(
            "fresh-validate-sealed-prediction",
            **identity,
            object_key=prediction["object_key"],
            validator="pcdb.contracts.prediction.load_prediction_artifact",
        )
        step(
            "forbid-overwrite-put-sealed-prediction",
            **identity,
            object_key=prediction["object_key"],
        )
        step(
            "fresh-readback-validate-sealed-prediction",
            **identity,
            object_key=prediction["object_key"],
        )
        step(
            "evaluator-read-candidate-truth-return-four-aggregate-results",
            **identity,
            output_object_keys=[item["object_key"] for item in metrics],
            persisted_candidate_level_outputs=0,
        )
        for item in metrics:
            step(
                "forbid-overwrite-put-aggregate-metric-result",
                **identity,
                object_key=item["object_key"],
            )
        step(
            "fresh-readback-validate-four-aggregate-results",
            **identity,
            object_keys=[item["object_key"] for item in metrics],
        )
        step(
            "generate-canonical-run-attempt-exact-cover-five-payloads",
            **identity,
            output_object_key=run_attempt["object_key"],
            referenced_object_keys=[
                prediction["object_key"],
                *[item["object_key"] for item in metrics],
            ],
        )
        step(
            "forbid-overwrite-put-run-attempt",
            **identity,
            object_key=run_attempt["object_key"],
        )
        step(
            "fresh-readback-validate-six-payload-objects",
            **identity,
            object_keys=[
                prediction["object_key"],
                *[item["object_key"] for item in metrics],
                run_attempt["object_key"],
            ],
        )
        step(
            "forbid-overwrite-put-empty-success-marker",
            **identity,
            object_key=success["object_key"],
        )
        if run_id == first_run_id:
            step(
                "independent-fresh-readback-exact-cover-alpha-ridge-checkpoint",
                required_exact_keys=[
                    *[item["object_key"] for item in shared],
                    *[item["object_key"] for item in run_items],
                ],
            )

    all_keys = [item["object_key"] for item in inventory]
    step(
        "independent-fresh-readback-exact-cover-final-inventory",
        required_exact_keys=all_keys,
    )
    return {
        "alpha_ridge_checkpoint": {
            "failure_action": "stop-with-other-five-runs-at-zero",
            "first_run_id": first_run_id,
            "required_exact_key_count": 33,
        },
        "final_readback": {
            "external_manifest_reference_count": len(external_manifest_references),
            "reject_missing_extra_duplicate": True,
            "required_exact_key_count": 68,
        },
        "schema_version": GATE_B_OPERATOR_PLAN_SCHEMA_VERSION,
        "steps": steps,
        "truth_boundary": {
            "first_truth_read_operation": (
                "evaluator-read-candidate-truth-return-four-aggregate-results"
            ),
            "held_out_reads": 0,
            "model_side_candidate_truth_reads": 0,
            "persisted_candidate_level_outputs": 0,
        },
    }


def load_gate_b_operator_plan(payload: bytes) -> LoadedContract:
    """Strict-load and order-check the Gate B fail-fast operator plan."""

    value = _parse_canonical_object(payload)
    if set(value) != {
        "alpha_ridge_checkpoint",
        "final_readback",
        "schema_version",
        "steps",
        "truth_boundary",
    }:
        raise ContractValidationError("Gate B operator plan fields differ")
    if value.get("schema_version") != GATE_B_OPERATOR_PLAN_SCHEMA_VERSION:
        raise ContractValidationError("Gate B operator plan version drifted")
    steps = value.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ContractValidationError("Gate B operator plan steps are required")
    if [step.get("sequence") for step in steps] != list(range(1, len(steps) + 1)):
        raise ContractValidationError("Gate B operator plan sequence drifted")
    if any(step.get("on_failure") != "stop-no-retry-no-next-run" for step in steps):
        raise ContractValidationError("Gate B operator plan is not fail-fast")

    first_evaluator_index = next(
        (
            index
            for index, step in enumerate(steps)
            if step.get("operation")
            == "evaluator-read-candidate-truth-return-four-aggregate-results"
        ),
        None,
    )
    if first_evaluator_index is None:
        raise ContractValidationError("Gate B evaluator step is missing")
    m3_index = next(
        (
            index
            for index, step in enumerate(steps)
            if step.get("operation") == "fresh-readback-validate-m3-freeze-record"
        ),
        None,
    )
    if m3_index is None or m3_index >= first_evaluator_index:
        raise ContractValidationError("M3 freeze must validate before candidate truth")
    external_indexes = [
        index
        for index, step in enumerate(steps)
        if step.get("operation") == "fresh-readback-validate-external-manifest-reference"
    ]
    first_write_index = next(
        (
            index
            for index, step in enumerate(steps)
            if str(step.get("operation")).startswith("forbid-")
        ),
        None,
    )
    if len(external_indexes) != 6 or first_write_index is None:
        raise ContractValidationError("six external manifest readbacks are required")
    if any(index >= first_write_index for index in external_indexes):
        raise ContractValidationError("external manifests must fresh-validate before publication")

    evaluator_steps = [
        step
        for step in steps
        if step.get("operation") == "evaluator-read-candidate-truth-return-four-aggregate-results"
    ]
    if len(evaluator_steps) != 6:
        raise ContractValidationError("Gate B operator must contain six evaluator steps")
    for evaluator in evaluator_steps:
        run_id = evaluator.get("run_id")
        run_steps = [step for step in steps if step.get("run_id") == run_id]
        operations = [step["operation"] for step in run_steps]
        try:
            prediction_index = operations.index("fresh-validate-sealed-prediction")
            evaluator_index = operations.index(
                "evaluator-read-candidate-truth-return-four-aggregate-results"
            )
            attempt_index = operations.index("forbid-overwrite-put-run-attempt")
            success_index = operations.index("forbid-overwrite-put-empty-success-marker")
        except ValueError as exc:
            raise ContractValidationError("Gate B run operator steps are incomplete") from exc
        if prediction_index >= evaluator_index:
            raise ContractValidationError("prediction must fresh-validate before candidate truth")
        if attempt_index >= success_index or success_index != len(operations) - 1:
            raise ContractValidationError("RunAttempt must precede the last success marker")
        output_keys = evaluator.get("output_object_keys")
        if not isinstance(output_keys, list) or len(output_keys) != 4:
            raise ContractValidationError("Gate B evaluator must return exactly four results")

    checkpoint = value.get("alpha_ridge_checkpoint")
    if not isinstance(checkpoint, dict) or checkpoint.get("required_exact_key_count") != 33:
        raise ContractValidationError("Alpha Ridge readback checkpoint drifted")
    if checkpoint.get("failure_action") != "stop-with-other-five-runs-at-zero":
        raise ContractValidationError("Alpha Ridge failure action drifted")
    final = value.get("final_readback")
    if not isinstance(final, dict) or final != {
        "external_manifest_reference_count": 6,
        "reject_missing_extra_duplicate": True,
        "required_exact_key_count": 68,
    }:
        raise ContractValidationError("Gate B final readback drifted")
    return LoadedContract(
        schema_version=GATE_B_OPERATOR_PLAN_SCHEMA_VERSION,
        value=MappingProxyType(value),
        canonical_bytes=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def build_gate_b_packet(
    *,
    repository_root: Path,
    gate_a_known_inputs_bytes: bytes,
    evidence_bucket: str,
    evidence_prefix: str,
) -> bytes:
    """Compile a zero-truth, zero-COS-write Gate B review packet."""

    known_inputs = _load_known_inputs(gate_a_known_inputs_bytes)
    if evidence_bucket != "pcdb-benchmark-artifacts-1408226911":
        raise ContractValidationError("Gate B evidence bucket drifted")
    if not evidence_prefix or not evidence_prefix.endswith("/") or evidence_prefix.startswith("/"):
        raise ContractValidationError("Gate B evidence prefix must be relative and end in slash")

    source_commit = known_inputs["source_git_commit"]
    tasks_by_id = {task["task_id"]: task for task in known_inputs["tasks"]}
    run_ids = {
        (task_id, run["model_id"]): run["run_id"]
        for task_id, task in tasks_by_id.items()
        for run in task["runs"]
    }
    feature_roots = {
        task_id: task["feature"]["descriptor"]["sha256"] for task_id, task in tasks_by_id.items()
    }
    m3_bytes = build_m3_freeze_record(
        source_git_commit=source_commit,
        task_feature_roots=feature_roots,
        run_ids=run_ids,
    )

    run_spec_values = [
        run["run_spec_value"] for task in known_inputs["tasks"] for run in task["runs"]
    ]
    for task_id in GATE_A_TASKS:
        experiment_path = _EXPERIMENT_PATHS[task_id]
        experiment_bytes = (repository_root / experiment_path).read_bytes()
        experiment_sha256 = load_experiment_config(experiment_bytes).sha256
        if any(
            item["run_spec_value"]["experiment_config_sha256"] != experiment_sha256
            for item in tasks_by_id[task_id]["runs"]
        ):
            raise ContractValidationError("Experiment Config bytes differ from Gate A RunSpecs")
    environment_bytes = (repository_root / _ENVIRONMENT_PATH).read_bytes()
    environment_value = _parse_canonical_object(environment_bytes)
    if environment_value.get("cpu_only") is not True:
        raise ContractValidationError("Gate B environment must remain CPU-only")
    environment_sha256 = hashlib.sha256(environment_bytes).hexdigest()
    if any(run["environment_digest"] != environment_sha256 for run in run_spec_values):
        raise ContractValidationError("environment manifest differs from Gate A RunSpecs")
    acquisition_sha256 = hashlib.sha256(M2_STATIC_ACQUISITION_CONFIG_BYTES).hexdigest()
    if any(run["acquisition_config_sha256"] != acquisition_sha256 for run in run_spec_values):
        raise ContractValidationError("acquisition config differs from Gate A RunSpecs")
    model_sha_by_id = {
        item["model_id"]: item["reference"]["sha256"] for item in known_inputs["model_configs"]
    }
    if any(
        run["model_config_sha256"] != model_sha_by_id[run["model_id"]] for run in run_spec_values
    ):
        raise ContractValidationError("model config differs from Gate A RunSpecs")
    external_by_task_and_type = {
        (item["task_id"], item["type"]): item for item in _EXTERNAL_MANIFEST_REFERENCES
    }
    for task_id in GATE_A_TASKS:
        run = tasks_by_id[task_id]["runs"][0]["run_spec_value"]
        for field, manifest_type in (
            ("dataset_manifest_sha256", "dataset-manifest"),
            ("processing_manifest_sha256", "processing-manifest"),
            ("split_sha256", "split-manifest"),
        ):
            if run[field] != external_by_task_and_type[(task_id, manifest_type)]["sha256"]:
                raise ContractValidationError("external manifest reference differs from RunSpec")

    inventory: list[dict[str, Any]] = []

    def add(
        *,
        relative_key: str,
        kind: str,
        scope: str,
        payload: Mapping[str, Any],
        identities: Mapping[str, str] | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "kind": kind,
            "object_key": evidence_prefix + relative_key,
            "payload": dict(payload),
            "publication_order": len(inventory) + 1,
            "scope": scope,
        }
        if identities is not None:
            item.update(identities)
        inventory.append(item)

    for task_id in GATE_A_TASKS:
        task = tasks_by_id[task_id]
        feature = task["feature"]
        add(
            relative_key=f"shared/features/{task_id}/logical.json",
            kind="feature-logical",
            scope="shared",
            payload=_known_payload(feature["logical"], "gate-a-scratch"),
            identities={"task_id": task_id},
        )
        add(
            relative_key=f"shared/features/{task_id}/descriptor.json",
            kind="feature-descriptor",
            scope="shared",
            payload=_known_payload(feature["descriptor"], "gate-a-scratch"),
            identities={"task_id": task_id},
        )
        for member in feature["members"]:
            reference = member["reference"]
            member_name = Path(reference["logical_path"]).name
            identities = {"task_id": task_id}
            if "role" in member:
                identities["role"] = member["role"]
            add(
                relative_key=f"shared/features/{task_id}/members/{member_name}",
                kind=member["kind"],
                scope="shared",
                payload=_known_payload(reference, "gate-a-scratch"),
                identities=identities,
            )

    for task_id in GATE_A_TASKS:
        for run in tasks_by_id[task_id]["runs"]:
            add(
                relative_key=f"shared/run-specs/{run['run_id']}.json",
                kind="run-spec",
                scope="shared",
                payload=_known_payload(run["run_spec"], "gate-a-scratch"),
                identities={
                    "model_id": run["model_id"],
                    "run_id": run["run_id"],
                    "task_id": task_id,
                },
            )

    for task_id in GATE_A_TASKS:
        logical_path = _EXPERIMENT_PATHS[task_id]
        payload = (repository_root / logical_path).read_bytes()
        reference = _artifact_reference(logical_path, payload)
        add(
            relative_key=f"shared/configs/experiments/{task_id}.json",
            kind="experiment-config",
            scope="shared",
            payload=_known_payload(reference, "reviewed-git"),
            identities={"task_id": task_id},
        )
    for model in known_inputs["model_configs"]:
        add(
            relative_key=f"shared/configs/models/{model['model_id']}.json",
            kind="model-config",
            scope="shared",
            payload=_known_payload(model["reference"], "gate-a-scratch"),
            identities={"model_id": model["model_id"]},
        )
    add(
        relative_key="shared/configs/acquisitions/m2-static-full-pool-v1.json",
        kind="acquisition-config",
        scope="shared",
        payload=_known_payload(
            _artifact_reference(
                "pcdb.contracts.prediction:M2_STATIC_ACQUISITION_CONFIG_BYTES",
                M2_STATIC_ACQUISITION_CONFIG_BYTES,
            ),
            "reviewed-code-constant",
        ),
    )
    add(
        relative_key="shared/environment/m2-gate-a-linux-x86_64.json",
        kind="environment-manifest",
        scope="shared",
        payload=_known_payload(
            _artifact_reference(_ENVIRONMENT_PATH, environment_bytes), "reviewed-git"
        ),
    )
    add(
        relative_key="shared/governance/m3-anti-post-hoc-freeze-v1.json",
        kind="m3-freeze-record",
        scope="shared",
        payload=_known_payload(
            _artifact_reference("packet:m3_freeze_record", m3_bytes), "packet-derived"
        ),
    )

    for task_id in GATE_A_TASKS:
        for run in tasks_by_id[task_id]["runs"]:
            base = f"attempts/{run['run_id']}/{run['attempt_id']}"
            identities = {
                "attempt_id": run["attempt_id"],
                "model_id": run["model_id"],
                "run_id": run["run_id"],
                "task_id": task_id,
            }
            add(
                relative_key=f"{base}/prediction.json",
                kind="prediction",
                scope="per-attempt",
                payload=_known_payload(run["prediction"], "gate-a-sealed-prediction"),
                identities=identities,
            )
            for metric_name in _METRIC_OBJECT_NAMES:
                add(
                    relative_key=f"{base}/metrics/{metric_name}",
                    kind="metric-result",
                    scope="per-attempt",
                    payload=_runtime_payload(
                        "capability-separated evaluator returns one canonical aggregate result",
                        "strict high-value result loader plus RunSpec/prediction/attempt linkage",
                    ),
                    identities=identities,
                )
            add(
                relative_key=f"{base}/run-attempt.json",
                kind="run-attempt",
                scope="per-attempt",
                payload=_runtime_payload(
                    "canonical RunAttempt after prediction and four metrics validate",
                    "RunAttempt v1 strict loader exact-covers five payload outputs",
                ),
                identities=identities,
            )
            add(
                relative_key=f"{base}/_SUCCESS",
                kind="success-marker",
                scope="per-attempt",
                payload={
                    "sha256": _EMPTY_SHA256,
                    "size_bytes": 0,
                    "source": {"kind": "constant-empty-bytes", "logical_path": "empty-bytes"},
                    "status": "known",
                },
                identities=identities,
            )

    if len(inventory) != 68:
        raise ContractValidationError("Gate B inventory must contain exactly 68 objects")
    first_run_id = run_ids[("alpha-amylase", "ridge-sklearn-v1")]
    operator_plan = _build_gate_b_operator_plan(
        inventory,
        first_run_id,
        _EXTERNAL_MANIFEST_REFERENCES,
    )
    value = {
        "credential_boundary": {
            "accepted_inputs": {
                "bucket": "pcdb-public-data-1408226911",
                "cam_actions": ["name/cos:GetBucket", "name/cos:GetObject"],
                "exact_object_keys": [
                    *[reference["object_key"] for reference in _EXTERNAL_MANIFEST_REFERENCES],
                    *_ACCEPTED_INPUT_OBJECT_KEYS,
                ],
                "operations": ["fresh-get", "head", "restricted-inventory"],
            },
            "explicitly_forbidden": [
                "delete-object",
                "overwrite",
                "other-buckets",
                "other-prefixes",
            ],
            "new_evidence_prefix": {
                "bucket": evidence_bucket,
                "cam_actions": [
                    "name/cos:GetBucket",
                    "name/cos:GetObject",
                    "name/cos:PutObject",
                ],
                "exact_prefix": evidence_prefix,
                "operations": [
                    "forbid-overwrite-put",
                    "fresh-get",
                    "head",
                    "restricted-inventory",
                ],
            },
            "secrets_in_packet": False,
        },
        "evidence_inventory": inventory,
        "evidence_store": {"bucket": evidence_bucket, "prefix": evidence_prefix},
        "execution_caps": {
            "automatic_retry": 0,
            "cos_net_new_bytes": 5 * 1024**3,
            "cos_object_count": 68,
            "formal_attempts": 6,
            "max_cost_cny": 40,
            "max_network_transfer_bytes": 10 * 1024**3,
            "max_ram_bytes": 16 * 1024**3,
            "max_scratch_bytes": 20 * 1024**3,
            "max_vcpu": 8,
            "max_wall_seconds": 4 * 60 * 60,
            "per_run_spec_attempts": 1,
        },
        "external_manifest_references": list(_EXTERNAL_MANIFEST_REFERENCES),
        "gate_a_known_inputs": {
            "sha256": hashlib.sha256(gate_a_known_inputs_bytes).hexdigest(),
            "size_bytes": len(gate_a_known_inputs_bytes),
        },
        "m3_freeze_record": json.loads(m3_bytes),
        "operator_plan": operator_plan,
        "preparation_truth_boundary": {
            "candidate_truth_reads": 0,
            "cos_writes": 0,
            "evaluator_calls": 0,
            "held_out_reads": 0,
            "run_attempts_created": 0,
            "success_markers_created": 0,
        },
        "publication_plan": {
            "first_run_failure_action": "stop-with-other-five-runs-at-zero",
            "first_run_id": first_run_id,
            "first_task_model": {
                "model_id": "ridge-sklearn-v1",
                "task_id": "alpha-amylase",
            },
            "known_prediction_provenance": (
                "reuse Gate A sealed deterministic bytes; attempt identity remains unchanged"
            ),
            "payload_before_run_attempt": True,
            "remaining_runs_start_condition": (
                "independent-fresh-readback-exact-covers-shared-26-and-alpha-ridge-7"
            ),
            "success_marker_last": True,
        },
        "schema_version": GATE_B_PACKET_SCHEMA_VERSION,
        "source_git_commit": source_commit,
    }
    return canonical_json_bytes(value)


def load_gate_b_packet(payload: bytes) -> LoadedContract:
    """Strict-load a canonical Gate B review packet without executing it."""

    value = _parse_canonical_object(payload)
    if set(value) != {
        "credential_boundary",
        "evidence_inventory",
        "evidence_store",
        "execution_caps",
        "external_manifest_references",
        "gate_a_known_inputs",
        "m3_freeze_record",
        "operator_plan",
        "preparation_truth_boundary",
        "publication_plan",
        "schema_version",
        "source_git_commit",
    }:
        raise ContractValidationError("Gate B packet fields differ")
    if value.get("schema_version") != GATE_B_PACKET_SCHEMA_VERSION:
        raise ContractValidationError("Gate B packet schema version drifted")
    inventory = value.get("evidence_inventory")
    if not isinstance(inventory, list) or len(inventory) != 68:
        raise ContractValidationError("Gate B packet must exact-cover 68 objects")
    if [item.get("publication_order") for item in inventory] != list(range(1, 69)):
        raise ContractValidationError("Gate B publication order drifted")
    object_keys = [item.get("object_key") for item in inventory]
    if any(not isinstance(key, str) for key in object_keys) or len(set(object_keys)) != 68:
        raise ContractValidationError("Gate B object keys must be 68 unique strings")
    prefix = value.get("evidence_store", {}).get("prefix")
    if not isinstance(prefix, str) or any(not key.startswith(prefix) for key in object_keys):
        raise ContractValidationError("Gate B object key escaped the evidence prefix")
    if sum(item.get("scope") == "shared" for item in inventory) != 26:
        raise ContractValidationError("Gate B shared inventory must contain 26 objects")
    if sum(item.get("scope") == "per-attempt" for item in inventory) != 42:
        raise ContractValidationError("Gate B per-attempt inventory must contain 42 objects")
    for item in inventory:
        item_payload = item.get("payload")
        if not isinstance(item_payload, dict):
            raise ContractValidationError("Gate B inventory payload must be an object")
        if item_payload.get("status") == "runtime-generated":
            if item_payload.get("sha256") is not None or item_payload.get("size_bytes") is not None:
                raise ContractValidationError("runtime payload must not contain a fake identity")
        elif item_payload.get("status") == "known":
            _require_sha256(item_payload.get("sha256"), "known inventory SHA-256")
            if not isinstance(item_payload.get("size_bytes"), int):
                raise ContractValidationError("known inventory size is required")
        else:
            raise ContractValidationError("Gate B payload status is invalid")
    m3_bytes = canonical_json_bytes(value.get("m3_freeze_record"))
    load_m3_freeze_record(m3_bytes)
    operator_plan_bytes = canonical_json_bytes(value.get("operator_plan"))
    load_gate_b_operator_plan(operator_plan_bytes)
    return LoadedContract(
        schema_version=GATE_B_PACKET_SCHEMA_VERSION,
        value=MappingProxyType(value),
        canonical_bytes=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


__all__ = [
    "GATE_A_KNOWN_INPUTS_SCHEMA_VERSION",
    "GATE_B_PACKET_SCHEMA_VERSION",
    "GATE_B_OPERATOR_PLAN_SCHEMA_VERSION",
    "M3_FREEZE_SCHEMA_VERSION",
    "M3_MINIMUM_MODELS",
    "build_gate_a_known_inputs_index",
    "build_gate_b_packet",
    "build_m3_freeze_record",
    "load_gate_b_packet",
    "load_gate_b_operator_plan",
    "load_m3_freeze_record",
]
