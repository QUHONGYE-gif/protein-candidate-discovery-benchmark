"""Narrow local-scratch CLI for synthetic M2 feature and prediction checks."""

from __future__ import annotations

import json
import math
import time
from argparse import Namespace
from pathlib import Path
from typing import Any, cast

from pcdb.contracts.experiment import (
    ContractValidationError,
    load_experiment_config,
)
from pcdb.contracts.prediction import M2StaticProducerInputs
from pcdb.m2.features import CandidateSequence, build_sequence_feature_bundle
from pcdb.m2.models import DevelopmentObservation, model_config_bytes
from pcdb.m2.producer import (
    SYNTHETIC_ATTEMPT_ID,
    build_synthetic_run_spec,
    produce_static_prediction,
)
from pcdb.storage import LocalArtifactStore

_ROLES = ("development", "validation", "candidate-pool")


def _find_repository_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists() and (candidate / ".gitignore").is_file():
            return candidate
    return None


def _validate_scratch_root(path: Path) -> Path:
    working_directory = Path.cwd().resolve()
    resolved = path.resolve()
    if not path.is_absolute():
        ignored_root = (working_directory / ".scratch").resolve()
        if not resolved.is_relative_to(ignored_root):
            raise ContractValidationError(
                "relative scratch root must stay inside the ignored .scratch namespace"
            )
        return resolved

    repository_root = _find_repository_root(resolved)
    if repository_root is not None and resolved.is_relative_to(repository_root):
        relative_parts = resolved.relative_to(repository_root).parts
        if ".scratch" not in relative_parts:
            raise ContractValidationError(
                "scratch root inside the repository must stay inside an ignored .scratch namespace"
            )
    return resolved


def _reject_constant(value: str) -> None:
    raise ContractValidationError(f"non-finite JSON constant is forbidden: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ContractValidationError(f"duplicate JSON key is forbidden: {key}")
        value[key] = item
    return value


def _read_json_object(path: Path, field: str) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{field} must be a readable strict JSON object") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field} must be a JSON object")
    return value


def _load_roles(
    path: Path,
) -> tuple[str, str, str, dict[str, tuple[CandidateSequence, ...]]]:
    value = _read_json_object(path, "role input")
    if set(value) != {"benchmark_task_id", "generator", "roles"}:
        raise ContractValidationError("role input fields drifted")
    benchmark_task_id = value["benchmark_task_id"]
    generator = value["generator"]
    roles = value["roles"]
    if not isinstance(benchmark_task_id, str):
        raise ContractValidationError("benchmark_task_id must be a string")
    if not isinstance(generator, dict) or set(generator) != {
        "environment_digest",
        "git_commit",
    }:
        raise ContractValidationError("role input generator fields drifted")
    git_commit = generator["git_commit"]
    environment_digest = generator["environment_digest"]
    if not isinstance(git_commit, str) or not isinstance(environment_digest, str):
        raise ContractValidationError("generator identity fields must be strings")
    if not isinstance(roles, dict) or set(roles) != set(_ROLES):
        raise ContractValidationError("role input must contain exactly three roles")
    loaded_roles: dict[str, tuple[CandidateSequence, ...]] = {}
    for role in _ROLES:
        raw_records = roles[role]
        if not isinstance(raw_records, list) or not raw_records:
            raise ContractValidationError(f"{role} records must be a non-empty array")
        records: list[CandidateSequence] = []
        for index, raw_record in enumerate(raw_records):
            if not isinstance(raw_record, dict) or set(raw_record) != {
                "candidate_id",
                "sequence",
            }:
                raise ContractValidationError(f"{role}[{index}] fields drifted")
            candidate_id = raw_record["candidate_id"]
            sequence = raw_record["sequence"]
            if not isinstance(candidate_id, str) or not isinstance(sequence, str):
                raise ContractValidationError(f"{role}[{index}] values must be strings")
            records.append(CandidateSequence(candidate_id, sequence))
        loaded_roles[role] = tuple(records)
    return benchmark_task_id, git_commit, environment_digest, loaded_roles


def _load_development_observations(path: Path) -> tuple[DevelopmentObservation, ...]:
    value = _read_json_object(path, "development observation input")
    if set(value) != {"development"}:
        raise ContractValidationError(
            "observation input must contain only role-scoped development observations"
        )
    raw_observations = value["development"]
    if not isinstance(raw_observations, list) or not raw_observations:
        raise ContractValidationError("development observations must be a non-empty array")
    observations: list[DevelopmentObservation] = []
    for index, raw_item in enumerate(raw_observations):
        if not isinstance(raw_item, dict) or set(raw_item) != {
            "candidate_id",
            "observation",
        }:
            raise ContractValidationError(f"development[{index}] fields drifted")
        candidate_id = raw_item["candidate_id"]
        observation = raw_item["observation"]
        if not isinstance(candidate_id, str):
            raise ContractValidationError(f"development[{index}].candidate_id must be a string")
        if isinstance(observation, bool) or not isinstance(observation, (int, float)):
            raise ContractValidationError(f"development[{index}].observation must be numeric")
        numeric = float(observation)
        if not math.isfinite(numeric):
            raise ContractValidationError(f"development[{index}].observation must be finite")
        observations.append(DevelopmentObservation(candidate_id, numeric))
    return tuple(observations)


def run_synthetic_cli(arguments: Namespace) -> int:
    """Execute the synthetic-only local-scratch workflow."""

    started = time.perf_counter()
    role_input = cast(Path, arguments.roles)
    observation_input = cast(Path, arguments.development_observations)
    experiment_input = cast(Path, arguments.experiment_config)
    scratch_root = _validate_scratch_root(cast(Path, arguments.scratch_root))
    model_id = cast(str, arguments.model_id)
    attempt_id = cast(str, arguments.attempt_id)

    benchmark_task_id, git_commit, environment_digest, role_candidates = _load_roles(role_input)
    development_observations = _load_development_observations(observation_input)
    experiment_config = load_experiment_config(experiment_input.read_bytes())
    bundle = build_sequence_feature_bundle(
        benchmark_task_id=benchmark_task_id,
        role_candidates=role_candidates,
        git_commit=git_commit,
        environment_digest=environment_digest,
    )
    run_spec = build_synthetic_run_spec(
        bundle=bundle,
        experiment_config=experiment_config,
        model_id=model_id,
        git_commit=git_commit,
        environment_digest=environment_digest,
    )
    config_bytes = model_config_bytes(model_id)
    producer_inputs = M2StaticProducerInputs(
        run_spec=run_spec,
        experiment_config=experiment_config,
        feature_descriptor=bundle.descriptor,
        feature_member_bytes=bundle.member_bytes,
        candidate_ids=tuple(sorted(bundle.memberships["candidate-pool"])),
        attempt_id=attempt_id,
        model_config_bytes=config_bytes,
    )
    prediction_bytes = produce_static_prediction(
        producer_inputs,
        development_observations,
    )

    store = LocalArtifactStore(scratch_root)
    written_size = 0
    logical = store.put_bytes(
        "m2-synthetic/feature-logical.json",
        bundle.logical_artifact.canonical_bytes,
    )
    written_size += logical.size_bytes
    descriptor = store.put_bytes(
        "m2-synthetic/feature-descriptor.json",
        bundle.descriptor_bytes,
    )
    written_size += descriptor.size_bytes
    for object_key, payload in bundle.member_bytes.items():
        written_size += store.put_bytes(object_key, payload).size_bytes
    written_size += store.put_bytes(
        f"m2-synthetic/{model_id}-config.json",
        config_bytes,
    ).size_bytes
    written_size += store.put_bytes(
        "m2-synthetic/run-spec.json",
        run_spec.canonical_json().encode("utf-8"),
    ).size_bytes
    prediction = store.put_bytes(
        "m2-synthetic/prediction.json",
        prediction_bytes,
    )
    written_size += prediction.size_bytes

    summary: dict[str, Any] = {
        "attempt_id": attempt_id,
        "feature_sha256": bundle.descriptor.sha256,
        "model_config_sha256": run_spec.model_config_sha256,
        "model_id": model_id,
        "prediction_sha256": prediction.sha256,
        "role_counts": {role: len(role_candidates[role]) for role in _ROLES},
        "run_id": run_spec.run_id,
        "runtime_seconds": round(time.perf_counter() - started, 6),
        "scratch_size_bytes": written_size,
        "task_id": benchmark_task_id,
    }
    print(json.dumps(summary, allow_nan=False, separators=(",", ":"), sort_keys=True))
    return 0


__all__ = ["SYNTHETIC_ATTEMPT_ID", "run_synthetic_cli"]
