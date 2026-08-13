"""Immutable local publication for synthetic M3 replay attempts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.contracts.prediction import load_run_attempt_v1
from pcdb.replay.engine import ReplayTrajectory, load_replay_trajectory
from pcdb.storage.base import ArtifactMetadata, ArtifactStore

_ATTEMPT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayPublication:
    """Keys and identities of one completed immutable attempt publication."""

    attempt_key: str
    attempt_sha256: str
    marker_key: str
    output_keys: tuple[str, ...]


def _validate_runtime_inputs(
    *,
    attempt_id: str,
    started_at: str,
    finished_at: str,
    executed_git_commit: str,
    executed_environment_digest: str,
    runtime_version: str,
) -> None:
    if not _ATTEMPT_ID.fullmatch(attempt_id):
        raise ContractValidationError("attempt_id is invalid")
    if not _GIT_COMMIT.fullmatch(executed_git_commit):
        raise ContractValidationError("executed_git_commit is invalid")
    if not _SHA256.fullmatch(executed_environment_digest):
        raise ContractValidationError("executed_environment_digest is invalid")
    if not runtime_version:
        raise ContractValidationError("runtime_version must not be empty")
    for field, value in (("started_at", started_at), ("finished_at", finished_at)):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ContractValidationError(f"{field} must be RFC 3339") from None
        if parsed.tzinfo is None:
            raise ContractValidationError(f"{field} must include timezone")


def _put_verified(store: ArtifactStore, key: str, payload: bytes) -> ArtifactMetadata:
    metadata = store.put_bytes(key, payload, overwrite=False)
    if (
        metadata.key != key
        or metadata.size_bytes != len(payload)
        or metadata.sha256 != hashlib.sha256(payload).hexdigest()
        or store.get_bytes(key) != payload
    ):
        raise ContractValidationError("artifact fresh readback differs from publication bytes")
    return metadata


def _artifact_value(metadata: ArtifactMetadata) -> dict[str, object]:
    return {
        "object_key": metadata.key,
        "sha256": metadata.sha256,
        "size_bytes": metadata.size_bytes,
    }


def publish_replay_attempt(
    trajectory: ReplayTrajectory,
    *,
    store: ArtifactStore,
    attempt_id: str,
    started_at: str,
    finished_at: str,
    executed_git_commit: str,
    executed_environment_digest: str,
    runtime_version: str,
    key_prefix: str = "",
    executor_type: str = "local",
    region: str | None = None,
) -> ReplayPublication:
    """Publish payloads and RunAttempt immutably, then write ``_SUCCESS`` last."""

    _validate_runtime_inputs(
        attempt_id=attempt_id,
        started_at=started_at,
        finished_at=finished_at,
        executed_git_commit=executed_git_commit,
        executed_environment_digest=executed_environment_digest,
        runtime_version=runtime_version,
    )
    if key_prefix:
        normalized_prefix = key_prefix.rstrip("/")
        prefix_path = PurePosixPath(normalized_prefix)
        if (
            key_prefix.startswith("/")
            or normalized_prefix in {"", "."}
            or ".." in prefix_path.parts
            or str(prefix_path) != normalized_prefix
        ):
            raise ContractValidationError("publication key_prefix is invalid")
        key_prefix = normalized_prefix + "/"
    if executor_type not in {"local", "compute_server"}:
        raise ContractValidationError("executor_type is invalid")
    if region is not None and not region:
        raise ContractValidationError("region must not be empty")
    validated = load_replay_trajectory(trajectory.canonical_bytes)
    if validated.sha256 != trajectory.sha256:
        raise ContractValidationError("trajectory identity drifted before publication")
    base = f"{key_prefix}runs/{trajectory.run_id}/attempts/{attempt_id}"
    outputs: list[ArtifactMetadata] = []
    for round_index, event in enumerate(trajectory.events):
        checkpoint = trajectory.checkpoint_bytes(round_index)
        checkpoint_key = f"{base}/checkpoints/round-{round_index:03d}.json"
        checkpoint_metadata = _put_verified(store, checkpoint_key, checkpoint)
        if checkpoint_metadata.sha256 != event.checkpoint_sha256:
            raise ContractValidationError("checkpoint publication differs from trajectory")
        outputs.append(checkpoint_metadata)
        metrics = trajectory.metric_bytes(round_index)
        if metrics is not None:
            metric_key = f"{base}/metrics/round-{round_index:03d}.json"
            metric_metadata = _put_verified(store, metric_key, metrics)
            if metric_metadata.sha256 != event.metric_sha256:
                raise ContractValidationError("metric publication differs from trajectory")
            outputs.append(metric_metadata)
    outputs.append(_put_verified(store, f"{base}/trajectory.json", trajectory.canonical_bytes))

    marker_key = f"{base}/_SUCCESS"
    marker = {"object_key": marker_key, "sha256": _EMPTY_SHA256, "size_bytes": 0}
    compute = {
        "executor_type": executor_type,
        "runtime_version": runtime_version,
    }
    if region is not None:
        compute["region"] = region
    attempt_value = {
        "attempt_id": attempt_id,
        "completion_marker": marker,
        "compute": compute,
        "executed_environment_digest": executed_environment_digest,
        "executed_git_commit": executed_git_commit,
        "finished_at": finished_at,
        "outputs": [_artifact_value(metadata) for metadata in outputs],
        "run_id": trajectory.run_id,
        "schema_version": "1",
        "started_at": started_at,
        "status": "succeeded",
    }
    attempt_bytes = canonical_json_bytes(attempt_value)
    loaded_attempt = load_run_attempt_v1(attempt_bytes)
    attempt_key = f"{base}/run-attempt.json"
    attempt_metadata = _put_verified(store, attempt_key, attempt_bytes)
    if attempt_metadata.sha256 != loaded_attempt.sha256:
        raise ContractValidationError("RunAttempt publication identity drifted")
    _put_verified(store, marker_key, b"")
    return ReplayPublication(
        attempt_key=attempt_key,
        attempt_sha256=attempt_metadata.sha256,
        marker_key=marker_key,
        output_keys=tuple(metadata.key for metadata in outputs),
    )


def is_replay_attempt_eligible(store: ArtifactStore, attempt_key: str) -> bool:
    """Return whether one attempt has complete fresh-readable immutable evidence."""

    try:
        attempt = load_run_attempt_v1(store.get_bytes(attempt_key))
        if attempt.value["status"] != "succeeded":
            return False
        for output in attempt.value["outputs"]:
            payload = store.get_bytes(output["object_key"])
            if (
                len(payload) != output["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != output["sha256"]
            ):
                return False
        trajectory_refs = [
            output
            for output in attempt.value["outputs"]
            if str(output["object_key"]).endswith("/trajectory.json")
        ]
        if len(trajectory_refs) != 1:
            return False
        trajectory = load_replay_trajectory(store.get_bytes(str(trajectory_refs[0]["object_key"])))
        if trajectory.run_id != attempt.value["run_id"]:
            return False
        trajectory_key = str(trajectory_refs[0]["object_key"])
        base = trajectory_key.removesuffix("/trajectory.json")
        expected_sha256 = {trajectory_key: trajectory.sha256}
        for round_index, event in enumerate(trajectory.events):
            expected_sha256[f"{base}/checkpoints/round-{round_index:03d}.json"] = (
                event.checkpoint_sha256
            )
            if event.metric_sha256 is not None:
                expected_sha256[f"{base}/metrics/round-{round_index:03d}.json"] = (
                    event.metric_sha256
                )
        output_sha256 = {
            str(output["object_key"]): str(output["sha256"]) for output in attempt.value["outputs"]
        }
        if output_sha256 != expected_sha256:
            return False
        if attempt_key != f"{base}/run-attempt.json":
            return False
        marker = attempt.value["completion_marker"]
        return (
            marker["object_key"] == f"{base}/_SUCCESS"
            and store.exists(marker["object_key"])
            and store.get_bytes(marker["object_key"]) == b""
            and marker["sha256"] == _EMPTY_SHA256
            and marker["size_bytes"] == 0
        )
    except (ContractValidationError, KeyError, OSError, TypeError, ValueError):
        return False


__all__ = ["ReplayPublication", "is_replay_attempt_eligible", "publish_replay_attempt"]
