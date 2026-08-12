"""Deterministic, zero-real-input Gate A review packet for Issue #68."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.run import RunSpec
from pcdb.m2.models import model_config_bytes

_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MATRIX_PATH = Path("manifests/experiments/m3/issue-66/run-spec-matrix.v1.json")
_FREEZE_PATH = Path("manifests/experiments/m3/issue-66/protocol-freeze.v1.json")
_M2_PACKET_PATH = Path("manifests/experiments/m2/issue-58/gate-b-packet.v1.json")
_ENVIRONMENT_PATH = Path("configs/environments/m2-gate-a-linux-x86_64.json")
_PUBLIC_BUCKET = "pcdb-public-data-1408226911"
_ARTIFACT_BUCKET = "pcdb-benchmark-artifacts-1408226911"
_M2_EVIDENCE_PREFIX = "m2/issue-58/alpha-ired-static-v1/9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5/"

_HIDDEN_TRUTH = {
    "alpha-amylase": {
        "object_key": (
            "processed/flip2/zenodo-18433203-v3/amylase/"
            "flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b/"
            "hidden-truth.csv"
        ),
        "sha256": "1d7b4deb06af08649867cbdeec6d0ef13dd70e2997687bbc290493a63fb7b8f8",
        "size_bytes": 301480,
    },
    "ired": {
        "object_key": (
            "processed/flip2/zenodo-18433203-v3/ired/"
            "flip2-ired-8f32dfa11a52d3ce96da4eb4ebb305210ccf027cf812d277f2c9c25f254c4f5d/"
            "hidden-truth.csv"
        ),
        "sha256": "cb97fd8a4b0256a8e3a5ab6476cecfcbdb732e21eaa3af5104170693cabdf3b8",
        "size_bytes": 725742,
    },
}


def _load_canonical_object(path: Path, field: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{field} must be readable canonical JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
        raise ContractValidationError(f"{field} must be canonical JSON")
    return cast(dict[str, Any], value), payload


def _artifact(path: Path, payload: bytes) -> dict[str, object]:
    return {
        "path": path.as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _source_state(
    repository_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], bytes, list[dict[str, object]]]:
    matrix, matrix_bytes = _load_canonical_object(repository_root / _MATRIX_PATH, "M3 matrix")
    freeze, freeze_bytes = _load_canonical_object(repository_root / _FREEZE_PATH, "M3 freeze")
    m2_packet, m2_packet_bytes = _load_canonical_object(
        repository_root / _M2_PACKET_PATH,
        "accepted M2 Gate B packet",
    )
    environment, environment_bytes = _load_canonical_object(
        repository_root / _ENVIRONMENT_PATH,
        "environment candidate",
    )
    if matrix.get("schema_version") != "m3-run-spec-matrix-v1":
        raise ContractValidationError("M3 matrix schema version drifted")
    if freeze.get("schema_version") != "m3-protocol-freeze-v1":
        raise ContractValidationError("M3 protocol freeze schema version drifted")
    if m2_packet.get("schema_version") != "m2-gate-b-packet-v1":
        raise ContractValidationError("accepted M2 Gate B packet schema version drifted")
    if environment.get("cpu_only") is not True:
        raise ContractValidationError("Gate A environment must remain CPU-only")
    frozen_matrix = [
        item
        for item in cast(list[dict[str, object]], freeze.get("artifact_inventory"))
        if item.get("path") == _MATRIX_PATH.as_posix()
    ]
    rebuilt_matrix = _artifact(_MATRIX_PATH, matrix_bytes)
    if len(frozen_matrix) != 1 or any(
        frozen_matrix[0].get(field) != rebuilt_matrix[field]
        for field in ("path", "sha256", "size_bytes")
    ):
        raise ContractValidationError("M3 matrix differs from the accepted protocol freeze")
    artifacts = [
        _artifact(_MATRIX_PATH, matrix_bytes),
        _artifact(_FREEZE_PATH, freeze_bytes),
        _artifact(_M2_PACKET_PATH, m2_packet_bytes),
        _artifact(_ENVIRONMENT_PATH, environment_bytes),
    ]
    return matrix, m2_packet, environment_bytes, artifacts


def _matrix_run_specs(
    repository_root: Path,
    matrix: dict[str, Any],
    *,
    reviewed_git_commit: str,
    environment_digest: str,
    source_artifacts: list[dict[str, object]],
) -> list[dict[str, object]]:
    tasks = cast(list[str], matrix.get("tasks"))
    models = cast(list[dict[str, str]], matrix.get("models"))
    acquisitions = cast(list[dict[str, object]], matrix.get("acquisition_configs"))
    chains = cast(list[dict[str, object]], matrix.get("manifest_chains"))
    seeds = cast(list[int], matrix.get("seeds"))
    profile = cast(dict[str, int | str], matrix.get("run_spec_profile"))
    if (
        tasks != ["alpha-amylase", "ired"]
        or len(models) != 3
        or len(acquisitions) != 3
        or seeds != [1729]
        or matrix.get("expected_run_spec_count") != 18
        or matrix.get("late_bound_run_spec_fields") != ["environment_digest", "git_commit"]
    ):
        raise ContractValidationError("M3 matrix identity drifted")
    chain_by_task = {str(item["task_id"]): item for item in chains}
    experiment_by_task: dict[str, tuple[str, bytes]] = {}
    for reference in cast(list[dict[str, object]], matrix.get("experiment_configs")):
        path = Path(str(reference["path"]))
        payload = (repository_root / path).read_bytes()
        loaded = load_experiment_config(payload)
        task_id = str(reference["task_id"])
        if (
            loaded.value["task_id"] != task_id
            or loaded.sha256 != reference["sha256"]
            or len(payload) != reference["size_bytes"]
        ):
            raise ContractValidationError("M3 Experiment Config identity drifted")
        experiment_by_task[task_id] = (loaded.sha256, payload)
        source_artifacts.append(_artifact(path, payload))
    acquisition_by_id: dict[str, str] = {}
    for reference in acquisitions:
        path = Path(str(reference["path"]))
        value, payload = _load_canonical_object(repository_root / path, "acquisition config")
        acquisition_id = str(reference["acquisition_id"])
        digest = hashlib.sha256(payload).hexdigest()
        if (
            value.get("acquisition_id") != acquisition_id
            or digest != reference["sha256"]
            or len(payload) != reference["size_bytes"]
        ):
            raise ContractValidationError("M3 acquisition config identity drifted")
        acquisition_by_id[acquisition_id] = digest
        source_artifacts.append(_artifact(path, payload))
    model_by_id: dict[str, str] = {}
    for model_reference in models:
        model_id = model_reference["model_id"]
        payload = model_config_bytes(model_id)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != model_reference["model_config_sha256"]:
            raise ContractValidationError("M3 model config identity drifted")
        model_by_id[model_id] = digest

    run_specs: list[dict[str, object]] = []
    for task_id in tasks:
        chain = chain_by_task[task_id]
        experiment_sha256, _ = experiment_by_task[task_id]
        for model_id in [item["model_id"] for item in models]:
            for acquisition_id in [str(item["acquisition_id"]) for item in acquisitions]:
                run_spec = RunSpec(
                    schema_version=str(profile["schema_version"]),
                    protocol_version=str(matrix["protocol_version"]),
                    git_commit=reviewed_git_commit,
                    environment_digest=environment_digest,
                    dataset_manifest_sha256=str(chain["dataset_manifest_sha256"]),
                    processing_manifest_sha256=str(chain["processing_manifest_sha256"]),
                    split_sha256=str(chain["split_sha256"]),
                    feature_sha256=str(chain["feature_sha256"]),
                    experiment_config_sha256=experiment_sha256,
                    model_id=model_id,
                    model_config_sha256=model_by_id[model_id],
                    acquisition_id=acquisition_id,
                    acquisition_config_sha256=acquisition_by_id[acquisition_id],
                    seed=seeds[0],
                    initial_sample_size=int(profile["initial_sample_size"]),
                    batch_size=int(profile["batch_size"]),
                    budget=int(profile["budget"]),
                )
                run_specs.append(
                    {
                        "run_id": run_spec.run_id,
                        "run_spec": asdict(run_spec),
                        "task_id": task_id,
                    }
                )
    if len(run_specs) != matrix["expected_run_spec_count"]:
        raise ContractValidationError("M3 RunSpec materialization is incomplete")
    return run_specs


def _input_inventory(m2_packet: dict[str, Any]) -> list[dict[str, object]]:
    manifest_refs = cast(list[dict[str, object]], m2_packet.get("external_manifest_references"))
    if len(manifest_refs) != 6:
        raise ContractValidationError("accepted manifest-chain inventory drifted")
    manifests: list[dict[str, object]] = []
    for reference in manifest_refs:
        manifests.append(
            {
                "bucket": reference["bucket"],
                "object_key": reference["object_key"],
                "purpose": reference["type"],
                "sha256": reference["sha256"],
                "size_bytes": reference["size_bytes"],
                "task_id": reference["task_id"],
                "truth_bearing": False,
            }
        )
    feature_items = []
    for item in cast(list[dict[str, Any]], m2_packet.get("evidence_inventory")):
        if item.get("kind") == "feature-descriptor" or (
            item.get("kind") == "feature-matrix" and item.get("role") == "candidate-pool"
        ):
            payload = cast(dict[str, object], item["payload"])
            feature_items.append(
                {
                    "bucket": _ARTIFACT_BUCKET,
                    "object_key": item["object_key"],
                    "purpose": item["kind"]
                    if item["kind"] == "feature-descriptor"
                    else "candidate-feature-matrix",
                    "sha256": payload["sha256"],
                    "size_bytes": payload["size_bytes"],
                    "task_id": item["task_id"],
                    "truth_bearing": False,
                }
            )
    if len(feature_items) != 4 or any(
        not str(item["object_key"]).startswith(_M2_EVIDENCE_PREFIX) for item in feature_items
    ):
        raise ContractValidationError("accepted feature inventory drifted")
    truths = [
        {
            "bucket": _PUBLIC_BUCKET,
            "object_key": reference["object_key"],
            "purpose": "hidden-truth",
            "sha256": reference["sha256"],
            "size_bytes": reference["size_bytes"],
            "task_id": task_id,
            "truth_bearing": True,
        }
        for task_id, reference in _HIDDEN_TRUTH.items()
    ]
    credential_boundary = cast(dict[str, Any], m2_packet.get("credential_boundary"))
    accepted_inputs = cast(dict[str, Any], credential_boundary.get("accepted_inputs"))
    accepted_keys = set(cast(list[str], accepted_inputs.get("exact_object_keys")))
    required_public_keys = {str(item["object_key"]) for item in manifests + truths}
    if accepted_inputs.get("bucket") != _PUBLIC_BUCKET or not required_public_keys <= accepted_keys:
        raise ContractValidationError("Gate A public inputs exceed the accepted M2 read boundary")
    inventory = sorted(
        manifests + feature_items, key=lambda item: (str(item["task_id"]), str(item["purpose"]))
    )
    inventory.extend(truths)
    for item in inventory:
        if (
            item["bucket"] not in {_PUBLIC_BUCKET, _ARTIFACT_BUCKET}
            or not isinstance(item["object_key"], str)
            or not _SHA256.fullmatch(str(item["sha256"]))
            or not isinstance(item["size_bytes"], int)
            or item["size_bytes"] < 1
        ):
            raise ContractValidationError("Gate A input identity is invalid")
    if len({(item["bucket"], item["object_key"]) for item in inventory}) != 12:
        raise ContractValidationError("Gate A input inventory is not an exact 12-object set")
    return inventory


def build_gate_a_packet(
    repository_root: Path,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
) -> bytes:
    """Build canonical review bytes without server, COS, or real-input access."""

    if not _GIT_OBJECT.fullmatch(reviewed_git_commit) or not _GIT_OBJECT.fullmatch(
        reviewed_git_tree
    ):
        raise ContractValidationError("reviewed Git commit and tree must be lowercase 40-hex")
    root = repository_root.resolve()
    matrix, m2_packet, environment_bytes, source_artifacts = _source_state(root)
    environment_digest = hashlib.sha256(environment_bytes).hexdigest()
    run_specs = _matrix_run_specs(
        root,
        matrix,
        reviewed_git_commit=reviewed_git_commit,
        environment_digest=environment_digest,
        source_artifacts=source_artifacts,
    )
    sentinel = next(
        item
        for item in run_specs
        if item["task_id"] == "ired"
        and cast(dict[str, object], item["run_spec"])["model_id"] == "ridge-sklearn-v1"
        and cast(dict[str, object], item["run_spec"])["acquisition_id"] == "random-seeded-sha256-v1"
    )
    caps = cast(dict[str, object], matrix["resource_caps"])
    packet = {
        "authorization_boundaries": {
            "gate_a_execution_authorized": False,
            "gate_b_execution_authorized": False,
            "packet_review_authorizes_candidate_truth_read": False,
            "packet_review_authorizes_cos_access": False,
            "packet_review_authorizes_server_access": False,
        },
        "cam_policy": {
            "allowed_actions": [
                "name/cos:GetBucket",
                "name/cos:GetObject",
                "name/cos:HeadObject",
            ],
            "delete_object": 0,
            "exact_object_only": True,
            "put_object": 0,
        },
        "environment_candidate": {
            **_artifact(_ENVIRONMENT_PATH, environment_bytes),
            "reuse_basis": "accepted M2 CPU environment; Issue #67 added no production dependency",
        },
        "execution_policy": {
            "automatic_retry": 0,
            "cos_puts": 0,
            "formal_attempts": 0,
            "manual_entry_starts": 1,
            "max_cost_cny": caps["max_cost_cny"],
            "max_network_transfer_bytes": caps["max_network_transfer_bytes"],
            "max_ram_bytes": caps["max_ram_bytes"],
            "max_scratch_bytes": caps["max_scratch_bytes"],
            "max_vcpu": caps["max_vcpu"],
            "max_wall_seconds": caps["max_wall_seconds"],
            "overwrite": 0,
            "replacement_attempts": 0,
            "required_checks": [
                "same-runspec-double-run",
                "interrupted-then-recovered",
                "no-repeat-purchase",
                "budget-monotonic",
                "four-decision-metrics",
                "resource-caps",
            ],
        },
        "input_inventory": _input_inventory(m2_packet),
        "issue_number": 68,
        "reviewed_source": {
            "git_commit": reviewed_git_commit,
            "git_tree": reviewed_git_tree,
        },
        "run_specs": run_specs,
        "schema_version": "m3-gate-a-review-packet-v1",
        "sentinel": {
            "acquisition_id": "random-seeded-sha256-v1",
            "cost_basis": (
                "smallest frozen candidate pool; hash acquisition performs no "
                "acquisition-side fit; "
                "smallest deterministic model config"
            ),
            "model_id": "ridge-sklearn-v1",
            "run_id": sentinel["run_id"],
            "task_id": "ired",
        },
        "source_artifacts": source_artifacts,
        "truth_boundary": {
            "allowed_path": "Gate A entry -> ReplayEngine -> HiddenOracle",
            "held_out_reads": 0,
            "packet_build_truth_reads": 0,
            "packet_build_real_input_reads": 0,
        },
    }
    return canonical_json_bytes(packet)


def packet_identity(packet_bytes: bytes) -> tuple[int, str]:
    """Return exact packet size and SHA-256."""

    return len(packet_bytes), hashlib.sha256(packet_bytes).hexdigest()


def load_gate_a_packet(
    packet_bytes: bytes,
    *,
    expected_sha256: str,
    repository_root: Path,
    expected_git_commit: str,
    expected_git_tree: str,
) -> dict[str, Any]:
    """Strict-load and reproduce one exact Gate A review packet."""

    if (
        not _SHA256.fullmatch(expected_sha256)
        or hashlib.sha256(packet_bytes).hexdigest() != expected_sha256
    ):
        raise ContractValidationError("Gate A packet identity differs from reviewed bytes")
    try:
        value = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("Gate A packet must be canonical JSON") from None
    if not isinstance(value, dict) or canonical_json_bytes(value) != packet_bytes:
        raise ContractValidationError("Gate A packet must be canonical JSON")
    expected = build_gate_a_packet(repository_root, expected_git_commit, expected_git_tree)
    if expected != packet_bytes:
        raise ContractValidationError("Gate A packet differs from exact reviewed repository bytes")
    return cast(dict[str, Any], value)


def _verify_runtime_source(
    repository_root: Path,
    reviewed_git_commit: str,
    reviewed_git_tree: str,
    module_files: Mapping[str, Path],
) -> None:
    """Bind imported M3 modules to one exact clean reviewed checkout."""

    root = repository_root.resolve()
    expected_files = {
        "gate_a": root / "src/pcdb/m3/gate_a.py",
        "replay_engine": root / "src/pcdb/replay/engine.py",
        "hidden_oracle": root / "src/pcdb/replay/oracle.py",
    }
    if set(module_files) != set(expected_files) or any(
        module_files[name].resolve() != expected.resolve()
        for name, expected in expected_files.items()
    ):
        raise ContractValidationError("Gate A executing source does not match repository root")
    try:
        checked_out_commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        checked_out_tree = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        worktree_status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractValidationError("Gate A runtime source could not be verified") from exc
    if checked_out_commit != reviewed_git_commit:
        raise ContractValidationError("Gate A checkout commit differs from reviewed source")
    if checked_out_tree != reviewed_git_tree:
        raise ContractValidationError("Gate A checkout tree differs from reviewed source")
    if worktree_status:
        raise ContractValidationError("Gate A runtime source checkout must be clean")


def _verified_runtime_environment_digest(repository_root: Path) -> str:
    """Rebuild the environment digest only after the running process matches its manifest."""

    path = repository_root.resolve() / _ENVIRONMENT_PATH
    try:
        manifest, payload = _load_canonical_object(path, "Gate A environment manifest")
        os_release = platform.freedesktop_os_release()
    except (OSError, ValueError) as exc:
        raise ContractValidationError("Gate A runtime environment could not be verified") from exc
    if (
        manifest.get("architecture") != platform.machine()
        or cast(dict[str, object], manifest.get("python")).get("implementation")
        != platform.python_implementation()
        or cast(dict[str, object], manifest.get("python")).get("version")
        != platform.python_version()
        or manifest.get("operating_system") != os_release.get("PRETTY_NAME")
    ):
        raise ContractValidationError("Gate A runtime platform drifted")
    for item in cast(list[dict[str, object]], manifest.get("packages")):
        try:
            actual_version = importlib.metadata.version(str(item["distribution"]))
        except importlib.metadata.PackageNotFoundError as exc:
            raise ContractValidationError("Gate A runtime package is absent") from exc
        if actual_version != item["version"]:
            raise ContractValidationError("Gate A runtime package version drifted")
    thread_policy = cast(dict[str, object], manifest.get("thread_policy"))
    thread_environment = cast(dict[str, str], thread_policy.get("environment"))
    for name, expected in thread_environment.items():
        if os.environ.get(name) != expected:
            raise ContractValidationError("Gate A runtime thread policy drifted")
    return hashlib.sha256(payload).hexdigest()


def validate_gate_a_entry(
    *,
    packet_bytes: bytes,
    packet_sha256: str,
    repository_root: Path,
    scratch_root: Path,
) -> Path:
    """Validate all non-input identities before any truth-bearing read."""

    if (
        not _SHA256.fullmatch(packet_sha256)
        or hashlib.sha256(packet_bytes).hexdigest() != packet_sha256
    ):
        raise ContractValidationError("Gate A packet identity differs from reviewed bytes")
    try:
        untrusted = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError("Gate A packet must be canonical JSON") from None
    if not isinstance(untrusted, dict) or canonical_json_bytes(untrusted) != packet_bytes:
        raise ContractValidationError("Gate A packet must be canonical JSON")
    reviewed = cast(dict[str, str], untrusted.get("reviewed_source"))
    if (
        not isinstance(reviewed, dict)
        or not _GIT_OBJECT.fullmatch(str(reviewed.get("git_commit")))
        or not _GIT_OBJECT.fullmatch(str(reviewed.get("git_tree")))
    ):
        raise ContractValidationError("Gate A packet reviewed source is invalid")
    from pcdb.replay import engine as replay_engine_module
    from pcdb.replay import oracle as hidden_oracle_module

    _verify_runtime_source(
        repository_root,
        reviewed["git_commit"],
        reviewed["git_tree"],
        {
            "gate_a": Path(__file__),
            "replay_engine": Path(str(replay_engine_module.__file__)),
            "hidden_oracle": Path(str(hidden_oracle_module.__file__)),
        },
    )
    packet = load_gate_a_packet(
        packet_bytes,
        expected_sha256=packet_sha256,
        repository_root=repository_root,
        expected_git_commit=reviewed["git_commit"],
        expected_git_tree=reviewed["git_tree"],
    )
    environment = cast(dict[str, object], packet["environment_candidate"])
    if _verified_runtime_environment_digest(repository_root) != environment["sha256"]:
        raise ContractValidationError("runtime environment differs from reviewed candidate")
    resolved_scratch = scratch_root.resolve()
    resolved_repository = repository_root.resolve()
    if resolved_scratch.is_relative_to(resolved_repository):
        relative = resolved_scratch.relative_to(resolved_repository)
        if not relative.parts or relative.parts[0] != ".scratch":
            raise ContractValidationError(
                "repository-local Gate A scratch must stay inside the ignored .scratch namespace"
            )
    if resolved_scratch.exists() and (
        not resolved_scratch.is_dir() or any(resolved_scratch.iterdir())
    ):
        raise ContractValidationError("Gate A scratch root must start empty")
    return resolved_scratch


__all__ = [
    "build_gate_a_packet",
    "load_gate_a_packet",
    "packet_identity",
    "validate_gate_a_entry",
]
