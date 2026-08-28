#!/usr/bin/env python3
"""Reconstruct every historical decision state for one selected M5A trajectory."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any


def _compact(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _git(source: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(source), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _hex(value: float) -> str:
    if not math.isfinite(float(value)):
        raise ValueError("non-finite formal reconstruction value")
    return struct.pack(">d", float(value)).hex()


def _rank(scores: tuple[tuple[str, float], ...]) -> tuple[str, ...]:
    return tuple(
        candidate_id
        for candidate_id, _ in sorted(
            scores,
            key=lambda item: (-item[1], item[0].encode("ascii")),
        )
    )


def _spearman(first: tuple[str, ...], second: tuple[str, ...]) -> float:
    if len(first) != len(second) or set(first) != set(second) or len(first) < 2:
        raise ValueError("ranking exact-cover drifted")
    positions = {candidate_id: index + 1 for index, candidate_id in enumerate(second)}
    squared = sum(
        (index + 1 - positions[candidate_id]) ** 2
        for index, candidate_id in enumerate(first)
    )
    count = len(first)
    return 1.0 - (6.0 * squared) / (count * (count * count - 1))


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _metric(metric: Any) -> dict[str, object]:
    if metric.status == "undefined":
        return {"status": "undefined", "undefined_reason": metric.undefined_reason}
    value = (
        1.0
        if metric.value is True
        else 0.0
        if metric.value is False
        else float(metric.value)
    )
    return {"status": "defined", "value_binary64": _hex(value)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-source", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--trajectory-sha256", required=True)
    parser.add_argument("--candidate-features", type=Path, required=True)
    parser.add_argument("--feature-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if (
        _git(args.historical_source, "rev-parse", "HEAD") != args.source_commit
        or _git(args.historical_source, "rev-parse", "HEAD^{tree}") != args.source_tree
        or _git(args.historical_source, "status", "--porcelain", "--untracked-files=no")
    ):
        raise ValueError("historical source commit/tree/clean identity drifted")
    trajectory_payload = args.trajectory.read_bytes()
    feature_payload = args.candidate_features.read_bytes()
    if hashlib.sha256(trajectory_payload).hexdigest() != args.trajectory_sha256:
        raise ValueError("trajectory SHA-256 drifted")
    if hashlib.sha256(feature_payload).hexdigest() != args.feature_sha256:
        raise ValueError("feature SHA-256 drifted")

    sys.path.insert(0, str(args.historical_source / "src"))
    from pcdb.contracts.feature_identity import encode_feature_matrix, load_feature_matrix
    from pcdb.m2.features import sequence_feature_definitions
    from pcdb.m2.models import (
        DevelopmentObservation,
        fit_m4_regressor,
        m4_model_config_bytes,
        predict_regressor,
    )
    from pcdb.replay.engine import bootstrap_prediction_std, load_replay_trajectory
    from pcdb.replay.oracle import Observation

    trajectory = load_replay_trajectory(trajectory_payload)
    if not trajectory.complete or trajectory.run_spec.git_commit != args.source_commit:
        raise ValueError("trajectory completion/source identity drifted")
    features = load_feature_matrix(
        feature_payload,
        feature_definitions=sequence_feature_definitions(),
        expected_sha256=args.feature_sha256,
    )
    run_spec = trajectory.run_spec
    model_config = m4_model_config_bytes(run_spec.model_id)

    def subset(candidate_ids: tuple[str, ...]):
        selected = set(candidate_ids)
        rows = [row for row in features.rows if row["candidate_id"] in selected]
        return load_feature_matrix(
            encode_feature_matrix(rows, feature_definitions=sequence_feature_definitions()),
            feature_definitions=sequence_feature_definitions(),
        )

    rounds: list[dict[str, object]] = []
    for round_index in range(1, len(trajectory.events)):
        prefix = tuple(
            observation
            for event in trajectory.events[:round_index]
            for observation in event.observations
        )
        observed = {observation.candidate_id: observation.label for observation in prefix}
        remaining = tuple(
            candidate_id
            for candidate_id in features.candidate_ids
            if candidate_id not in observed
        )
        ordered_observed = tuple(sorted(observed))
        fitted = fit_m4_regressor(
            model_config,
            subset(ordered_observed),
            [
                DevelopmentObservation(
                    candidate_id=candidate_id,
                    observation=observed[candidate_id],
                )
                for candidate_id in ordered_observed
            ],
            seed=run_spec.seed,
        )
        means = predict_regressor(fitted, subset(remaining))
        uncertainty = bootstrap_prediction_std(
            run_spec=run_spec,
            task_id=trajectory.task_id,
            candidate_features=features,
            model_config=model_config,
            observations=[
                Observation(candidate_id=item.candidate_id, label=item.label)
                for item in prefix
            ],
            candidate_ids=remaining,
            round_index=round_index,
        )
        if tuple(candidate_id for candidate_id, _ in means) != tuple(
            candidate_id for candidate_id, _ in uncertainty
        ):
            raise ValueError("mean/uncertainty candidate exact-cover drifted")
        if run_spec.acquisition_id == "uncertainty-bootstrap-std-v1":
            acquisition = uncertainty
            policy = "exploration"
        elif run_spec.acquisition_id == "ucb-bootstrap-std-beta1-v1":
            acquisition = tuple(
                (candidate_id, mean + std)
                for (candidate_id, mean), (_, std) in zip(
                    means,
                    uncertainty,
                    strict=True,
                )
            )
            policy = "exploration"
        elif run_spec.acquisition_id == "greedy-predictive-mean-v1":
            acquisition = means
            policy = "greedy"
        else:
            raise ValueError("trajectory acquisition is outside formal M5A")
        mean_ranking = _rank(means)
        uncertainty_ranking = _rank(uncertainty)
        acquisition_ranking = _rank(acquisition)
        k = run_spec.batch_size
        actual = trajectory.events[round_index].selected_candidate_ids
        mean_top = set(mean_ranking[:k])
        acquisition_top = set(acquisition_ranking[:k])
        overlap = len(mean_top & acquisition_top)
        union = len(mean_top | acquisition_top)
        metrics = trajectory.events[round_index].metrics
        if metrics is None:
            raise ValueError("formal trajectory lacks checkpoint metrics")
        mean_positions = {
            candidate_id: index + 1
            for index, candidate_id in enumerate(mean_ranking)
        }
        uncertainty_positions = {
            candidate_id: index + 1
            for index, candidate_id in enumerate(uncertainty_ranking)
        }
        rounds.append(
            {
                "actual_policy_exact_match": tuple(acquisition_ranking[:k]) == actual,
                "checkpoint_metrics": {
                    "best_found_value": _metric(metrics.best_found_value),
                    "high_value_discovery_recall": _metric(metrics.high_value_discovery_recall),
                    "simple_regret": _metric(metrics.simple_regret),
                    "success_at_budget": _metric(metrics.success_at_budget),
                },
                "divergence_binary64": _hex(1.0 - overlap / union),
                "mean_acquisition_spearman_binary64": _hex(
                    _spearman(mean_ranking, acquisition_ranking)
                ),
                "remaining_count": len(remaining),
                "revealed_before": len(observed),
                "round": round_index,
                "selected_mean_ranks": [mean_positions[candidate_id] for candidate_id in actual],
                "selected_uncertainty_ranks": [
                    uncertainty_positions[candidate_id] for candidate_id in actual
                ],
                "top_k_jaccard_binary64": _hex(overlap / union),
                "top_k_overlap": overlap,
            }
        )
    summary = {
        "initial_sample_size": run_spec.initial_sample_size,
        "model_id": run_spec.model_id,
        "policy": policy,
        "rounds": rounds,
        "schema_version": "m5a-formal-trajectory-summary-v1",
        "seed": run_spec.seed,
        "task_id": trajectory.task_id,
    }
    output = _compact(summary)
    if b"candidate_id" in output or b'"value":' in output:
        raise ValueError("formal trajectory summary leaks IDs or labels")
    _write_exclusive(args.output, output)
    print(
        json.dumps(
            {"rounds": len(rounds), "sha256": hashlib.sha256(output).hexdigest()},
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
