#!/usr/bin/env python3
"""Fresh-process worker for one prefix-only M5A acquisition reconstruction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _compact_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _load_input(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict) or _compact_json_bytes(value) != payload:
        raise ValueError("reconstruction input is not compact canonical JSON")
    expected = {
        "batch_size",
        "input_identity",
        "observations",
        "remaining_candidate_ids",
        "round",
        "run_spec",
        "schema_version",
        "task_id",
        "tie_break",
    }
    if set(value) != expected or value["schema_version"] != "m5a-reconstruction-input-v1":
        raise ValueError("reconstruction input fields or schema drifted")
    forbidden = {"actual_selected_ids", "events", "hidden_truth", "metrics", "oracle"}
    if forbidden & set(value):
        raise ValueError("reconstruction input crosses the target/truth boundary")
    return value


def _git_value(source: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(source), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-source", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidate-features", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    value = _load_input(args.input)
    identity = value["input_identity"]
    if not isinstance(identity, dict):
        raise ValueError("input_identity must be an object")
    expected_commit = identity.get("source_git_commit")
    expected_tree = identity.get("source_git_tree")
    if (
        _git_value(args.historical_source, "rev-parse", "HEAD") != expected_commit
        or _git_value(args.historical_source, "rev-parse", "HEAD^{tree}") != expected_tree
        or _git_value(
            args.historical_source,
            "status",
            "--porcelain",
            "--untracked-files=no",
        )
    ):
        raise ValueError("historical source commit/tree/clean identity drifted")

    source_path = str(args.historical_source / "src")
    sys.path.insert(0, source_path)
    from pcdb.contracts.feature_identity import load_feature_matrix
    from pcdb.contracts.run import RunSpec
    from pcdb.m2.features import sequence_feature_definitions
    from pcdb.m2.models import m4_model_config_bytes
    from pcdb.replay.engine import bootstrap_prediction_std, ucb_prediction_scores
    from pcdb.replay.oracle import Observation

    feature_payload = args.candidate_features.read_bytes()
    expected_feature_sha = identity.get("candidate_feature_sha256")
    if hashlib.sha256(feature_payload).hexdigest() != expected_feature_sha:
        raise ValueError("candidate feature member SHA-256 drifted")
    features = load_feature_matrix(
        feature_payload,
        feature_definitions=sequence_feature_definitions(),
        expected_sha256=expected_feature_sha,
    )
    run_spec = RunSpec(**value["run_spec"])
    if run_spec.run_id != identity.get("run_id") or run_spec.git_commit != expected_commit:
        raise ValueError("RunSpec or source identity drifted")
    observations = tuple(
        Observation(candidate_id=item["candidate_id"], label=float(item["value"]))
        for item in value["observations"]
    )
    remaining_ids = tuple(value["remaining_candidate_ids"])
    model_config = m4_model_config_bytes(run_spec.model_id)
    scoring_arguments = {
        "run_spec": run_spec,
        "task_id": value["task_id"],
        "candidate_features": features,
        "model_config": model_config,
        "observations": observations,
        "candidate_ids": remaining_ids,
        "round_index": value["round"],
    }
    if run_spec.acquisition_id == "uncertainty-bootstrap-std-v1":
        scores = bootstrap_prediction_std(**scoring_arguments)
    elif run_spec.acquisition_id == "ucb-bootstrap-std-beta1-v1":
        scores = ucb_prediction_scores(**scoring_arguments)
    else:
        raise ValueError("acquisition is outside the accepted smoke strata")

    # Import the artifact encoder from the execution implementation only after scoring has
    # loaded from the exact historical source. Its format is the accepted M5A smoke contract.
    sys.path.pop(0)
    for module_name in tuple(sys.modules):
        if module_name == "pcdb" or module_name.startswith("pcdb."):
            del sys.modules[module_name]
    execution_src = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(execution_src))
    from pcdb.m5.reconstruction_smoke import ranking_artifact_bytes

    output = ranking_artifact_bytes(input_value=value, scores=scores)
    _write_exclusive(args.output, output)
    print(
        json.dumps(
            {
                "artifact_sha256": hashlib.sha256(output).hexdigest(),
                "ranking_size": len(scores),
                "state_id": identity.get("state_id"),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
