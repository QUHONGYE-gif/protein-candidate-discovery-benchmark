"""Canonical review-only Gate B planning artifacts for Issue #58."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

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

_ISSUE_58_SOURCE_COMMIT = "9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5"
_ISSUE_58_EVIDENCE_BUCKET = "pcdb-benchmark-artifacts-1408226911"
_ISSUE_58_EVIDENCE_PREFIX = (
    "m2/issue-58/alpha-ired-static-v1/9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5/"
)
_ISSUE_58_KNOWN_INPUTS_SHA256 = "783e338fd20396ea340d10140592c2752a30ac8fc7115e71b432c0796fe03ae2"
_ISSUE_58_KNOWN_INPUTS_SIZE = 16_873
_ISSUE_58_FEATURE_ROOTS = {
    "alpha-amylase": "fa64315824eccde93e5c4740bbaf638aa54da2d2b8b96053bbae193ec89bbe42",
    "ired": "3d20f04020f1b136d1097d6beee7fcaa7b09729e80babd6d96154f71c510fb1b",
}
_ISSUE_58_RUN_IDS = {
    ("alpha-amylase", "ridge-sklearn-v1"): (
        "835788c12467d4625b2ea482baa3dc69e77fc1c4f9452746aa4ae4d5bad4904c"
    ),
    ("alpha-amylase", "random-forest-sklearn-v1"): (
        "0146cffe9490a9f7c4e9f7909c9cd5225acc06c56d00cd88e9786062ca6e5be2"
    ),
    ("alpha-amylase", "xgboost-cpu-v1"): (
        "60713aeabbcdd2fe6b4077ce8d53ec3d0c37f93b4d45ffeb7d796c7cfa959788"
    ),
    ("ired", "ridge-sklearn-v1"): (
        "0c1426decf706cc086d64e9d2ac94a96915c8a6e515aca7f8d113e750a8a5c09"
    ),
    ("ired", "random-forest-sklearn-v1"): (
        "6250d7e2279ade16252b548d0df5278e0a5d60ccfa41b31789129847ee8d19a6"
    ),
    ("ired", "xgboost-cpu-v1"): (
        "18352e9b9065eaf28ebd42ba92d14f0f0f3b09838b8c674dc7d9bff082da9675"
    ),
}
_ISSUE_58_REVIEWED_FILE_SIZES = {
    "alpha-amylase-experiment": 4_676,
    "ired-experiment": 4_658,
    "environment": 1_656,
}
_EXECUTION_CAPS = {
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
}
_PREPARATION_TRUTH_BOUNDARY = {
    "candidate_truth_reads": 0,
    "cos_writes": 0,
    "evaluator_calls": 0,
    "held_out_reads": 0,
    "run_attempts_created": 0,
    "success_markers_created": 0,
}
_EXPLICITLY_FORBIDDEN = [
    "delete-object",
    "overwrite",
    "other-buckets",
    "other-prefixes",
]
_ACCEPTED_INPUT_CAM_ACTIONS = [
    "name/cos:GetBucket",
    "name/cos:GetObject",
    "name/cos:HeadObject",
]
_NEW_EVIDENCE_CAM_ACTIONS = [
    "name/cos:GetBucket",
    "name/cos:GetObject",
    "name/cos:HeadObject",
    "name/cos:PutObject",
]

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


def _credential_boundary(evidence_bucket: str, evidence_prefix: str) -> dict[str, Any]:
    return {
        "accepted_inputs": {
            "bucket": "pcdb-public-data-1408226911",
            "cam_actions": list(_ACCEPTED_INPUT_CAM_ACTIONS),
            "exact_object_keys": [
                *[reference["object_key"] for reference in _EXTERNAL_MANIFEST_REFERENCES],
                *_ACCEPTED_INPUT_OBJECT_KEYS,
            ],
            "operations": ["fresh-get", "head", "restricted-inventory"],
        },
        "explicitly_forbidden": list(_EXPLICITLY_FORBIDDEN),
        "new_evidence_prefix": {
            "bucket": evidence_bucket,
            "cam_actions": list(_NEW_EVIDENCE_CAM_ACTIONS),
            "exact_prefix": evidence_prefix,
            "operations": [
                "forbid-overwrite-put",
                "fresh-get",
                "head",
                "restricted-inventory",
            ],
        },
        "secrets_in_packet": False,
    }


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


def _expected_known_payloads(
    known_inputs: Mapping[str, Any],
    m3_bytes: bytes,
) -> dict[str, dict[str, Any]]:
    """Rebuild every pre-runtime payload identity from reviewed inputs and contracts."""

    expected: dict[str, dict[str, Any]] = {}
    tasks_by_id = {task["task_id"]: task for task in known_inputs["tasks"]}
    for task_id in GATE_A_TASKS:
        task = tasks_by_id[task_id]
        feature = task["feature"]
        expected[f"shared/features/{task_id}/logical.json"] = _known_payload(
            feature["logical"], "gate-a-scratch"
        )
        expected[f"shared/features/{task_id}/descriptor.json"] = _known_payload(
            feature["descriptor"], "gate-a-scratch"
        )
        for member in feature["members"]:
            name = Path(member["reference"]["logical_path"]).name
            expected[f"shared/features/{task_id}/members/{name}"] = _known_payload(
                member["reference"], "gate-a-scratch"
            )
        for run in task["runs"]:
            expected[f"shared/run-specs/{run['run_id']}.json"] = _known_payload(
                run["run_spec"], "gate-a-scratch"
            )
            base = f"attempts/{run['run_id']}/{run['attempt_id']}"
            expected[f"{base}/prediction.json"] = _known_payload(
                run["prediction"], "gate-a-sealed-prediction"
            )
            expected[f"{base}/_SUCCESS"] = {
                "sha256": _EMPTY_SHA256,
                "size_bytes": 0,
                "source": {"kind": "constant-empty-bytes", "logical_path": "empty-bytes"},
                "status": "known",
            }

        run_spec = task["runs"][0]["run_spec_value"]
        experiment_path = _EXPERIMENT_PATHS[task_id]
        expected[f"shared/configs/experiments/{task_id}.json"] = _known_payload(
            {
                "logical_path": experiment_path,
                "sha256": run_spec["experiment_config_sha256"],
                "size_bytes": _ISSUE_58_REVIEWED_FILE_SIZES[f"{task_id}-experiment"],
            },
            "reviewed-git",
        )

    for model in known_inputs["model_configs"]:
        expected[f"shared/configs/models/{model['model_id']}.json"] = _known_payload(
            model["reference"], "gate-a-scratch"
        )
    expected["shared/configs/acquisitions/m2-static-full-pool-v1.json"] = _known_payload(
        _artifact_reference(
            "pcdb.contracts.prediction:M2_STATIC_ACQUISITION_CONFIG_BYTES",
            M2_STATIC_ACQUISITION_CONFIG_BYTES,
        ),
        "reviewed-code-constant",
    )
    first_run_spec = tasks_by_id[GATE_A_TASKS[0]]["runs"][0]["run_spec_value"]
    expected["shared/environment/m2-gate-a-linux-x86_64.json"] = _known_payload(
        {
            "logical_path": _ENVIRONMENT_PATH,
            "sha256": first_run_spec["environment_digest"],
            "size_bytes": _ISSUE_58_REVIEWED_FILE_SIZES["environment"],
        },
        "reviewed-git",
    )
    expected["shared/governance/m3-anti-post-hoc-freeze-v1.json"] = _known_payload(
        _artifact_reference("packet:m3_freeze_record", m3_bytes), "packet-derived"
    )
    if len(expected) != 38:
        raise ContractValidationError("Gate B known payload identity rebuild is incomplete")
    return expected


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

    external_steps = [steps[index] for index in external_indexes]
    for step, reference in zip(external_steps, _EXTERNAL_MANIFEST_REFERENCES, strict=True):
        expected = {
            **reference,
            "on_failure": "stop-no-retry-no-next-run",
            "operation": "fresh-readback-validate-external-manifest-reference",
            "sequence": step["sequence"],
        }
        if step != expected:
            raise ContractValidationError("external manifest operator reference drifted")

    shared_steps = [
        step
        for step in steps
        if step.get("operation") == "forbid-overwrite-put-known-shared-payload"
    ]
    shared_keys = [step.get("object_key") for step in shared_steps]
    if (
        len(shared_keys) != 26
        or any(not isinstance(key, str) for key in shared_keys)
        or len(set(shared_keys)) != 26
    ):
        raise ContractValidationError("Gate B operator must publish 26 unique shared objects")
    evidence_keys: list[str] = []
    for step in steps:
        if step.get("operation") == "fresh-readback-validate-external-manifest-reference":
            continue
        for field in ("object_key", "output_object_key"):
            item = step.get(field)
            if isinstance(item, str):
                evidence_keys.append(item)
        for field in (
            "object_keys",
            "output_object_keys",
            "referenced_object_keys",
            "required_exact_keys",
        ):
            items = step.get(field)
            if isinstance(items, list):
                evidence_keys.extend(item for item in items if isinstance(item, str))
    if not evidence_keys or any(
        not key.startswith(_ISSUE_58_EVIDENCE_PREFIX) for key in evidence_keys
    ):
        raise ContractValidationError("Gate B operator evidence prefix drifted")

    evaluator_steps = [
        step
        for step in steps
        if step.get("operation") == "evaluator-read-candidate-truth-return-four-aggregate-results"
    ]
    if len(evaluator_steps) != 6:
        raise ContractValidationError("Gate B operator must contain six evaluator steps")
    if len({step.get("run_id") for step in evaluator_steps}) != 6:
        raise ContractValidationError("Gate B evaluator run identities must be unique")
    run_publication_keys: dict[str, list[str]] = {}
    task_models: list[tuple[str, str]] = []
    for evaluator in evaluator_steps:
        run_id = evaluator.get("run_id")
        run_steps = [step for step in steps if step.get("run_id") == run_id]
        operations = [step["operation"] for step in run_steps]
        expected_operations = [
            "fresh-validate-sealed-prediction",
            "forbid-overwrite-put-sealed-prediction",
            "fresh-readback-validate-sealed-prediction",
            "evaluator-read-candidate-truth-return-four-aggregate-results",
            *["forbid-overwrite-put-aggregate-metric-result"] * 4,
            "fresh-readback-validate-four-aggregate-results",
            "generate-canonical-run-attempt-exact-cover-five-payloads",
            "forbid-overwrite-put-run-attempt",
            "fresh-readback-validate-six-payload-objects",
            "forbid-overwrite-put-empty-success-marker",
        ]
        if operations != expected_operations:
            if "fresh-validate-sealed-prediction" in operations and operations.index(
                "fresh-validate-sealed-prediction"
            ) > operations.index("evaluator-read-candidate-truth-return-four-aggregate-results"):
                raise ContractValidationError(
                    "prediction must fresh-validate before candidate truth"
                )
            raise ContractValidationError("Gate B run operator steps are incomplete or reordered")
        identity = {
            field: evaluator.get(field) for field in ("attempt_id", "model_id", "run_id", "task_id")
        }
        if (
            any(not isinstance(item, str) or not item for item in identity.values())
            or not _SHA256.fullmatch(str(run_id))
            or any(
                any(step.get(field) != item for field, item in identity.items())
                for step in run_steps
            )
        ):
            raise ContractValidationError("Gate B run operator identity drifted")
        task_models.append((str(identity["task_id"]), str(identity["model_id"])))
        prediction_key = run_steps[0].get("object_key")
        if (
            not isinstance(prediction_key, str)
            or run_steps[1].get("object_key") != prediction_key
            or run_steps[2].get("object_key") != prediction_key
            or run_steps[0].get("validator") != "pcdb.contracts.prediction.load_prediction_artifact"
        ):
            raise ContractValidationError("sealed prediction operator linkage drifted")
        output_keys = evaluator.get("output_object_keys")
        metric_put_keys = [step.get("object_key") for step in run_steps[4:8]]
        if (
            not isinstance(output_keys, list)
            or len(output_keys) != 4
            or len(set(output_keys)) != 4
            or output_keys != metric_put_keys
            or evaluator.get("persisted_candidate_level_outputs") != 0
            or run_steps[8].get("object_keys") != output_keys
        ):
            raise ContractValidationError("Gate B evaluator must return exactly four results")
        attempt_key = run_steps[9].get("output_object_key")
        if (
            not isinstance(attempt_key, str)
            or run_steps[9].get("referenced_object_keys") != [prediction_key, *output_keys]
            or run_steps[10].get("object_key") != attempt_key
            or run_steps[11].get("object_keys") != [prediction_key, *output_keys, attempt_key]
        ):
            raise ContractValidationError("RunAttempt exact-cover linkage drifted")
        success_key = run_steps[12].get("object_key")
        if not isinstance(success_key, str):
            raise ContractValidationError("success marker object key is required")
        publication_keys = [prediction_key, *output_keys, attempt_key, success_key]
        if len(set(publication_keys)) != 7:
            raise ContractValidationError("Gate B run seven-object closure contains duplicates")
        run_publication_keys[str(run_id)] = publication_keys

    expected_task_models = [
        (task_id, model_id) for task_id in GATE_A_TASKS for model_id in GATE_A_MODELS
    ]
    if task_models != expected_task_models:
        raise ContractValidationError("Gate B task/model run order drifted")
    actual_cohort = [
        (str(step["task_id"]), str(step["model_id"]), str(step["run_id"]))
        for step in evaluator_steps
    ]
    expected_cohort = [
        (task_id, model_id, _ISSUE_58_RUN_IDS[(task_id, model_id)])
        for task_id, model_id in expected_task_models
    ]
    if actual_cohort != expected_cohort:
        raise ContractValidationError("Gate B exact six-run cohort drifted")

    checkpoint = value.get("alpha_ridge_checkpoint")
    if not isinstance(checkpoint, dict) or checkpoint.get("required_exact_key_count") != 33:
        raise ContractValidationError("Alpha Ridge readback checkpoint drifted")
    if checkpoint.get("failure_action") != "stop-with-other-five-runs-at-zero":
        raise ContractValidationError("Alpha Ridge failure action drifted")
    first_run_id = checkpoint.get("first_run_id")
    if first_run_id != evaluator_steps[0].get("run_id") or task_models[0] != (
        "alpha-amylase",
        "ridge-sklearn-v1",
    ):
        raise ContractValidationError("Alpha Ridge first-run identity drifted")
    checkpoint_steps = [
        step
        for step in steps
        if step.get("operation") == "independent-fresh-readback-exact-cover-alpha-ridge-checkpoint"
    ]
    if len(checkpoint_steps) != 1:
        raise ContractValidationError("Alpha Ridge checkpoint step drifted")
    checkpoint_step = checkpoint_steps[0]
    checkpoint_index = steps.index(checkpoint_step)
    if any(
        step.get("run_id") != first_run_id
        for step in steps[:checkpoint_index]
        if step.get("run_id") is not None
    ) or any(
        step.get("run_id") == first_run_id
        for step in steps[checkpoint_index + 1 :]
        if step.get("run_id") is not None
    ):
        raise ContractValidationError("non-first run crossed the Alpha Ridge checkpoint")
    expected_checkpoint_keys = [*shared_keys, *run_publication_keys[str(first_run_id)]]
    if checkpoint_step.get("required_exact_keys") != expected_checkpoint_keys:
        raise ContractValidationError("Alpha Ridge checkpoint exact keys drifted")
    final = value.get("final_readback")
    if not isinstance(final, dict) or final != {
        "external_manifest_reference_count": 6,
        "reject_missing_extra_duplicate": True,
        "required_exact_key_count": 68,
    }:
        raise ContractValidationError("Gate B final readback drifted")
    final_steps = [
        step
        for step in steps
        if step.get("operation") == "independent-fresh-readback-exact-cover-final-inventory"
    ]
    expected_final_keys = [
        *shared_keys,
        *[
            key
            for evaluator in evaluator_steps
            for key in run_publication_keys[str(evaluator["run_id"])]
        ],
    ]
    if (
        len(final_steps) != 1
        or final_steps[0] is not steps[-1]
        or final_steps[0].get("required_exact_keys") != expected_final_keys
    ):
        raise ContractValidationError("Gate B final readback exact keys drifted")
    return LoadedContract(
        schema_version=GATE_B_OPERATOR_PLAN_SCHEMA_VERSION,
        value=MappingProxyType(value),
        canonical_bytes=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


class GateBOperatorRuntime(Protocol):
    """Narrow capabilities required by the validated Issue #58 operator."""

    def bind_reviewed_packet(self, packet: Mapping[str, Any]) -> None: ...

    def validate_external_manifest_reference(self, step: Mapping[str, Any]) -> None: ...

    def publish_known_shared(self, step: Mapping[str, Any]) -> None: ...

    def validate_m3_freeze(self, step: Mapping[str, Any]) -> None: ...

    def validate_sealed_prediction(self, step: Mapping[str, Any]) -> None: ...

    def publish_sealed_prediction(self, step: Mapping[str, Any]) -> None: ...

    def readback_sealed_prediction(self, step: Mapping[str, Any]) -> None: ...

    def evaluate_four_aggregate_results(self, step: Mapping[str, Any]) -> None: ...

    def publish_aggregate_metric_result(self, step: Mapping[str, Any]) -> None: ...

    def readback_four_aggregate_results(self, step: Mapping[str, Any]) -> None: ...

    def generate_run_attempt(self, step: Mapping[str, Any]) -> None: ...

    def publish_run_attempt(self, step: Mapping[str, Any]) -> None: ...

    def readback_six_payload_objects(self, step: Mapping[str, Any]) -> None: ...

    def publish_empty_success_marker(self, step: Mapping[str, Any]) -> None: ...

    def readback_exact_keys(self, step: Mapping[str, Any]) -> None: ...


_OPERATOR_METHODS = {
    "fresh-readback-validate-external-manifest-reference": ("validate_external_manifest_reference"),
    "forbid-overwrite-put-known-shared-payload": "publish_known_shared",
    "fresh-readback-validate-m3-freeze-record": "validate_m3_freeze",
    "fresh-validate-sealed-prediction": "validate_sealed_prediction",
    "forbid-overwrite-put-sealed-prediction": "publish_sealed_prediction",
    "fresh-readback-validate-sealed-prediction": "readback_sealed_prediction",
    "evaluator-read-candidate-truth-return-four-aggregate-results": (
        "evaluate_four_aggregate_results"
    ),
    "forbid-overwrite-put-aggregate-metric-result": "publish_aggregate_metric_result",
    "fresh-readback-validate-four-aggregate-results": "readback_four_aggregate_results",
    "generate-canonical-run-attempt-exact-cover-five-payloads": "generate_run_attempt",
    "forbid-overwrite-put-run-attempt": "publish_run_attempt",
    "fresh-readback-validate-six-payload-objects": "readback_six_payload_objects",
    "forbid-overwrite-put-empty-success-marker": "publish_empty_success_marker",
    "independent-fresh-readback-exact-cover-alpha-ridge-checkpoint": "readback_exact_keys",
    "independent-fresh-readback-exact-cover-final-inventory": "readback_exact_keys",
}


def execute_gate_b_operator(
    gate_b_packet_bytes: bytes,
    runtime: GateBOperatorRuntime,
) -> None:
    """Execute the plan from one strict full packet; stop on the first exception."""

    packet = load_gate_b_packet(gate_b_packet_bytes).value
    runtime.bind_reviewed_packet(packet)
    plan = load_gate_b_operator_plan(canonical_json_bytes(packet["operator_plan"])).value
    for raw_step in plan["steps"]:
        step = MappingProxyType(dict(raw_step))
        method_name = _OPERATOR_METHODS.get(str(step["operation"]))
        if method_name is None:
            raise ContractValidationError("Gate B operator contains an unsupported operation")
        method = getattr(runtime, method_name, None)
        if not callable(method):
            raise ContractValidationError(
                f"Gate B runtime is missing required capability: {method_name}"
            )
        method(step)


def build_gate_b_packet(
    *,
    repository_root: Path,
    gate_a_known_inputs_bytes: bytes,
    evidence_bucket: str,
    evidence_prefix: str,
) -> bytes:
    """Compile a zero-truth, zero-COS-write Gate B review packet."""

    known_inputs = _load_known_inputs(gate_a_known_inputs_bytes)
    if (
        len(gate_a_known_inputs_bytes) != _ISSUE_58_KNOWN_INPUTS_SIZE
        or hashlib.sha256(gate_a_known_inputs_bytes).hexdigest() != _ISSUE_58_KNOWN_INPUTS_SHA256
    ):
        raise ContractValidationError("Gate A known-input index identity drifted")
    if evidence_bucket != _ISSUE_58_EVIDENCE_BUCKET:
        raise ContractValidationError("Gate B evidence bucket drifted")
    if evidence_prefix != _ISSUE_58_EVIDENCE_PREFIX:
        raise ContractValidationError("Gate B evidence prefix drifted")

    source_commit = known_inputs["source_git_commit"]
    if source_commit != _ISSUE_58_SOURCE_COMMIT:
        raise ContractValidationError("Gate B source commit drifted")
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
        "credential_boundary": _credential_boundary(evidence_bucket, evidence_prefix),
        "evidence_inventory": inventory,
        "evidence_store": {"bucket": evidence_bucket, "prefix": evidence_prefix},
        "execution_caps": dict(_EXECUTION_CAPS),
        "external_manifest_references": list(_EXTERNAL_MANIFEST_REFERENCES),
        "gate_a_known_inputs": {
            "artifact": {
                "sha256": hashlib.sha256(gate_a_known_inputs_bytes).hexdigest(),
                "size_bytes": len(gate_a_known_inputs_bytes),
            },
            "value": known_inputs,
        },
        "m3_freeze_record": json.loads(m3_bytes),
        "operator_plan": operator_plan,
        "preparation_truth_boundary": dict(_PREPARATION_TRUTH_BOUNDARY),
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
    if value.get("source_git_commit") != _ISSUE_58_SOURCE_COMMIT:
        raise ContractValidationError("Gate B source commit drifted")
    if value.get("evidence_store") != {
        "bucket": _ISSUE_58_EVIDENCE_BUCKET,
        "prefix": _ISSUE_58_EVIDENCE_PREFIX,
    }:
        raise ContractValidationError("Gate B evidence store drifted")
    if value.get("execution_caps") != _EXECUTION_CAPS:
        raise ContractValidationError("Gate B execution caps drifted")
    if value.get("preparation_truth_boundary") != _PREPARATION_TRUTH_BOUNDARY:
        raise ContractValidationError("Gate B preparation truth boundary drifted")
    if value.get("credential_boundary") != _credential_boundary(
        _ISSUE_58_EVIDENCE_BUCKET,
        _ISSUE_58_EVIDENCE_PREFIX,
    ):
        raise ContractValidationError("Gate B credential boundary drifted")
    if value.get("external_manifest_references") != list(_EXTERNAL_MANIFEST_REFERENCES):
        raise ContractValidationError("Gate B external manifest references drifted")
    embedded_known_inputs = value.get("gate_a_known_inputs")
    if (
        not isinstance(embedded_known_inputs, dict)
        or set(embedded_known_inputs) != {"artifact", "value"}
        or not isinstance(embedded_known_inputs.get("artifact"), dict)
    ):
        raise ContractValidationError("Gate A known-input index identity drifted")
    known_inputs_bytes = canonical_json_bytes(embedded_known_inputs.get("value"))
    known_inputs_artifact = embedded_known_inputs["artifact"]
    if known_inputs_artifact != {
        "sha256": _ISSUE_58_KNOWN_INPUTS_SHA256,
        "size_bytes": _ISSUE_58_KNOWN_INPUTS_SIZE,
    } or (
        len(known_inputs_bytes) != _ISSUE_58_KNOWN_INPUTS_SIZE
        or hashlib.sha256(known_inputs_bytes).hexdigest() != _ISSUE_58_KNOWN_INPUTS_SHA256
    ):
        raise ContractValidationError("Gate A known-input index identity drifted")
    known_inputs = _load_known_inputs(known_inputs_bytes)
    if known_inputs["source_git_commit"] != _ISSUE_58_SOURCE_COMMIT:
        raise ContractValidationError("Gate A known-input source commit drifted")
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
    m3 = load_m3_freeze_record(m3_bytes).value
    if m3.get("source_git_commit") != _ISSUE_58_SOURCE_COMMIT:
        raise ContractValidationError("M3 source commit differs from Gate B packet")
    known_tasks = {task["task_id"]: task for task in known_inputs["tasks"]}
    expected_feature_roots = {
        task_id: known_tasks[task_id]["feature"]["descriptor"]["sha256"] for task_id in GATE_A_TASKS
    }
    expected_run_ids = {
        (task_id, run["model_id"]): run["run_id"]
        for task_id in GATE_A_TASKS
        for run in known_tasks[task_id]["runs"]
    }
    expected_m3_bytes = build_m3_freeze_record(
        source_git_commit=_ISSUE_58_SOURCE_COMMIT,
        task_feature_roots=expected_feature_roots,
        run_ids=expected_run_ids,
    )
    if m3_bytes != expected_m3_bytes or expected_feature_roots != _ISSUE_58_FEATURE_ROOTS:
        raise ContractValidationError("Gate B M3 feature roots or run cohort drifted")

    shared = inventory[:26]
    expected_shared_layout: list[tuple[str, str, str | None, str | None, str | None]] = []
    for task_id in GATE_A_TASKS:
        expected_shared_layout.extend(
            [
                (f"shared/features/{task_id}/logical.json", "feature-logical", task_id, None, None),
                (
                    f"shared/features/{task_id}/descriptor.json",
                    "feature-descriptor",
                    task_id,
                    None,
                    None,
                ),
                (
                    f"shared/features/{task_id}/members/config.json",
                    "feature-config",
                    task_id,
                    None,
                    None,
                ),
                (
                    f"shared/features/{task_id}/members/development.csv",
                    "feature-matrix",
                    task_id,
                    None,
                    "development",
                ),
                (
                    f"shared/features/{task_id}/members/validation.csv",
                    "feature-matrix",
                    task_id,
                    None,
                    "validation",
                ),
                (
                    f"shared/features/{task_id}/members/candidate-pool.csv",
                    "feature-matrix",
                    task_id,
                    None,
                    "candidate-pool",
                ),
            ]
        )
    m3_runs = {
        (task["task_id"], run["model_id"]): run["run_id"]
        for task in m3["cohort"]["tasks"]
        for run in task["runs"]
    }
    for task_id in GATE_A_TASKS:
        for model_id in GATE_A_MODELS:
            run_id = m3_runs[(task_id, model_id)]
            expected_shared_layout.append(
                (f"shared/run-specs/{run_id}.json", "run-spec", task_id, model_id, None)
            )
    expected_shared_layout.extend(
        [
            (
                "shared/configs/experiments/alpha-amylase.json",
                "experiment-config",
                "alpha-amylase",
                None,
                None,
            ),
            ("shared/configs/experiments/ired.json", "experiment-config", "ired", None, None),
            *[
                (
                    f"shared/configs/models/{model_id}.json",
                    "model-config",
                    None,
                    model_id,
                    None,
                )
                for model_id in GATE_A_MODELS
            ],
            (
                "shared/configs/acquisitions/m2-static-full-pool-v1.json",
                "acquisition-config",
                None,
                None,
                None,
            ),
            (
                "shared/environment/m2-gate-a-linux-x86_64.json",
                "environment-manifest",
                None,
                None,
                None,
            ),
            (
                "shared/governance/m3-anti-post-hoc-freeze-v1.json",
                "m3-freeze-record",
                None,
                None,
                None,
            ),
        ]
    )
    actual_shared_layout = [
        (
            item["object_key"][len(_ISSUE_58_EVIDENCE_PREFIX) :],
            item.get("kind"),
            item.get("task_id"),
            item.get("model_id"),
            item.get("role"),
        )
        for item in shared
    ]
    if actual_shared_layout != expected_shared_layout:
        raise ContractValidationError("Gate B shared inventory layout drifted")

    expected_run_order = [
        (task_id, model_id, m3_runs[(task_id, model_id)])
        for task_id in GATE_A_TASKS
        for model_id in GATE_A_MODELS
    ]
    expected_attempt_kinds = [
        "prediction",
        *["metric-result"] * 4,
        "run-attempt",
        "success-marker",
    ]
    for run_index, (task_id, model_id, run_id) in enumerate(expected_run_order):
        run_items = inventory[26 + run_index * 7 : 26 + (run_index + 1) * 7]
        if (
            len(run_items) != 7
            or [item.get("kind") for item in run_items] != expected_attempt_kinds
        ):
            raise ContractValidationError("Gate B per-run seven-object closure drifted")
        attempt_id = run_items[0].get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ContractValidationError("Gate B attempt identity is required")
        if any(
            item.get("scope") != "per-attempt"
            or item.get("task_id") != task_id
            or item.get("model_id") != model_id
            or item.get("run_id") != run_id
            or item.get("attempt_id") != attempt_id
            for item in run_items
        ):
            raise ContractValidationError("Gate B per-run identity linkage drifted")
        base = f"attempts/{run_id}/{attempt_id}"
        expected_relative_keys = [
            f"{base}/prediction.json",
            *[f"{base}/metrics/{name}" for name in _METRIC_OBJECT_NAMES],
            f"{base}/run-attempt.json",
            f"{base}/_SUCCESS",
        ]
        if [
            item["object_key"][len(_ISSUE_58_EVIDENCE_PREFIX) :] for item in run_items
        ] != expected_relative_keys:
            raise ContractValidationError("Gate B per-run object key layout drifted")
        if (
            run_items[0]["payload"].get("status") != "known"
            or any(item["payload"].get("status") != "runtime-generated" for item in run_items[1:6])
            or run_items[6]["payload"]
            != {
                "sha256": _EMPTY_SHA256,
                "size_bytes": 0,
                "source": {"kind": "constant-empty-bytes", "logical_path": "empty-bytes"},
                "status": "known",
            }
        ):
            raise ContractValidationError("Gate B per-run payload lifecycle drifted")

    expected_known_payloads = _expected_known_payloads(known_inputs, m3_bytes)
    actual_known_payloads = {
        item["object_key"][len(_ISSUE_58_EVIDENCE_PREFIX) :]: item["payload"]
        for item in inventory
        if item["payload"].get("status") == "known"
    }
    if actual_known_payloads != expected_known_payloads:
        raise ContractValidationError("Gate B known payload identities drifted")

    first_run_id = m3_runs[("alpha-amylase", "ridge-sklearn-v1")]
    expected_publication_plan = {
        "first_run_failure_action": "stop-with-other-five-runs-at-zero",
        "first_run_id": first_run_id,
        "first_task_model": {"model_id": "ridge-sklearn-v1", "task_id": "alpha-amylase"},
        "known_prediction_provenance": (
            "reuse Gate A sealed deterministic bytes; attempt identity remains unchanged"
        ),
        "payload_before_run_attempt": True,
        "remaining_runs_start_condition": (
            "independent-fresh-readback-exact-covers-shared-26-and-alpha-ridge-7"
        ),
        "success_marker_last": True,
    }
    if value.get("publication_plan") != expected_publication_plan:
        raise ContractValidationError("Gate B publication plan drifted")
    operator_plan_bytes = canonical_json_bytes(value.get("operator_plan"))
    load_gate_b_operator_plan(operator_plan_bytes)
    expected_operator_plan = _build_gate_b_operator_plan(
        inventory,
        first_run_id,
        _EXTERNAL_MANIFEST_REFERENCES,
    )
    if value.get("operator_plan") != expected_operator_plan:
        raise ContractValidationError("Gate B operator plan differs from inventory rebuild")
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
    "GateBOperatorRuntime",
    "M3_FREEZE_SCHEMA_VERSION",
    "M3_MINIMUM_MODELS",
    "build_gate_a_known_inputs_index",
    "build_gate_b_packet",
    "build_m3_freeze_record",
    "execute_gate_b_operator",
    "load_gate_b_packet",
    "load_gate_b_operator_plan",
    "load_m3_freeze_record",
]
