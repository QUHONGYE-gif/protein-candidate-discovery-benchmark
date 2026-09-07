#!/usr/bin/env python3
"""Execute the sealed M5C four-assay comparison inside one attempt root."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import numpy as np

from pcdb.contracts import ContractValidationError
from pcdb.m2.features import FEATURE_CONFIG_ID, FEATURE_CONFIG_SHA256
from pcdb.m5.proteingym_confirmation import (
    ASSAY_IDS,
    FROZEN_CANDIDATE_COUNTS,
    INITIAL_SAMPLE_SIZES,
    MODEL_IDS,
    RUN_SEEDS,
    StateEffects,
    compact_json_bytes,
    initial_selection,
)
from pcdb.m5.proteingym_runtime import (
    StateSelection,
    build_assay_aggregate,
    build_cohort_report,
    build_feature_artifact,
    evaluate_state,
    load_candidate_table,
    load_static_proxy,
    load_truth_evaluator,
    select_state,
    write_once,
)
from pcdb.replay.oracle import Observation

_WORKER_ASSAY = ""
_WORKER_CANDIDATE_IDS: tuple[str, ...] = ()
_WORKER_CANDIDATE_SHA = ""
_WORKER_FEATURES: np.ndarray | None = None
_WORKER_FEATURE_SHA = ""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_json_exact(path: Path, expected_sha256: str) -> tuple[dict[str, Any], bytes]:
    payload = path.read_bytes()
    if _sha256(payload) != expected_sha256:
        raise ContractValidationError(f"M5C exact JSON identity drifted: {path}")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractValidationError(f"M5C JSON is invalid: {path}") from None
    if not isinstance(value, dict):
        raise ContractValidationError(f"M5C JSON root is invalid: {path}")
    return cast(dict[str, Any], value), payload


def _artifact(item: Mapping[str, object], role: str) -> tuple[str, str, int]:
    try:
        value = cast(Mapping[str, object], item["artifacts"])[role]
        artifact = cast(Mapping[str, object], value)
        path = artifact["path"]
        sha256 = artifact["sha256"]
        size = artifact["size_bytes"]
    except (KeyError, TypeError):
        raise ContractValidationError(f"M5C {role} manifest entry is invalid") from None
    if not isinstance(path, str) or not isinstance(sha256, str) or type(size) is not int:
        raise ContractValidationError(f"M5C {role} manifest identity is invalid")
    return path, sha256, size


def _init_selection_worker(
    assay_id: str,
    candidate_ids: tuple[str, ...],
    candidate_sha256: str,
    feature_path: str,
    feature_sha256: str,
) -> None:
    global _WORKER_ASSAY
    global _WORKER_CANDIDATE_IDS
    global _WORKER_CANDIDATE_SHA
    global _WORKER_FEATURES
    global _WORKER_FEATURE_SHA
    _WORKER_ASSAY = assay_id
    _WORKER_CANDIDATE_IDS = candidate_ids
    _WORKER_CANDIDATE_SHA = candidate_sha256
    _WORKER_FEATURES = np.load(feature_path, mmap_mode="r")
    _WORKER_FEATURE_SHA = feature_sha256
    if _WORKER_FEATURES.shape != (len(candidate_ids), 420):
        raise RuntimeError("M5C feature worker shape drifted")


def _select_task(
    task: tuple[str, int, int, tuple[Observation, ...]],
) -> StateSelection:
    model_id, initial_size, seed, observations = task
    if _WORKER_FEATURES is None:
        raise RuntimeError("M5C selection worker is not initialized")
    return select_state(
        assay_id=_WORKER_ASSAY,
        candidate_ids=_WORKER_CANDIDATE_IDS,
        candidate_pool_sha256=_WORKER_CANDIDATE_SHA,
        features=_WORKER_FEATURES,
        feature_sha256=_WORKER_FEATURE_SHA,
        initial_sample_size=initial_size,
        model_id=model_id,
        observations=observations,
        run_seed=seed,
    )


def _run_assay(
    item: Mapping[str, object],
    *,
    input_root: Path,
    private_root: Path,
    workers: int,
) -> dict[str, object]:
    assay_id = item.get("assay_id")
    if assay_id not in ASSAY_IDS or item.get("eligibility") != "eligible":
        raise ContractValidationError("M5C assay eligibility or identity drifted")
    assay_id = cast(str, assay_id)
    candidate_relative, candidate_sha, candidate_size = _artifact(item, "candidate")
    candidate_table = load_candidate_table(
        input_root / candidate_relative,
        assay_id=assay_id,
        expected_sha256=candidate_sha,
        expected_size=candidate_size,
    )
    raw_coverage = item.get("finite_coverage")
    if not isinstance(raw_coverage, dict):
        raise ContractValidationError("M5C finite-coverage record is invalid")
    coverage = cast(Mapping[str, object], raw_coverage)
    if (
        len(candidate_table.candidate_ids) != FROZEN_CANDIDATE_COUNTS[assay_id]
        or coverage.get("finite") != FROZEN_CANDIDATE_COUNTS[assay_id]
        or coverage.get("truth_candidate_count") != FROZEN_CANDIDATE_COUNTS[assay_id]
        or coverage.get("score_row_count") != FROZEN_CANDIDATE_COUNTS[assay_id]
        or any(
            coverage.get(field) != 0
            for field in ("missing", "non_finite", "truth_candidates_without_score_row")
        )
    ):
        raise ContractValidationError("M5C frozen assay count or finite coverage drifted")
    feature = build_feature_artifact(
        candidate_table.sequences,
        private_root / f"{assay_id}.features.npy",
    )

    context = multiprocessing.get_context("fork")
    with context.Pool(
        processes=workers,
        initializer=_init_selection_worker,
        initargs=(
            assay_id,
            candidate_table.candidate_ids,
            candidate_table.sha256,
            str(feature.path),
            feature.sha256,
        ),
    ) as pool:
        # Pool construction forks every surrogate worker before hidden truth is opened.
        truth_relative, truth_sha, truth_size = _artifact(item, "hidden_truth")
        evaluator = load_truth_evaluator(
            input_root / truth_relative,
            candidate_table=candidate_table,
            expected_sha256=truth_sha,
            expected_size=truth_size,
        )
        tasks = []
        for model_id in MODEL_IDS:
            for initial_size in INITIAL_SAMPLE_SIZES:
                for seed in RUN_SEEDS:
                    initial_ids = initial_selection(
                        candidate_table.candidate_ids,
                        assay_id=assay_id,
                        run_seed=seed,
                        size=initial_size,
                    )
                    tasks.append((model_id, initial_size, seed, evaluator.reveal(initial_ids)))
        selections = pool.map(_select_task, tasks)

    state_aggregates: list[dict[str, object]] = []
    state_effects: list[StateEffects] = []
    for selection in selections:
        aggregate, effects = evaluate_state(selection, evaluator)
        state_aggregates.append(aggregate)
        state_effects.append(effects)
    proxy_relative, proxy_sha, proxy_size = _artifact(item, "proxy_source")
    proxy = load_static_proxy(
        input_root / proxy_relative,
        candidate_table=candidate_table,
        expected_sha256=proxy_sha,
        expected_size=proxy_size,
    )
    report = build_assay_aggregate(
        assay_id=assay_id,
        candidate_count=len(candidate_table.candidate_ids),
        evaluator=evaluator,
        state_aggregates=state_aggregates,
        state_effects=state_effects,
        static_proxy=evaluator.evaluate_static_proxy(proxy),
    )
    report["feature_artifact"] = {
        "feature_config_id": FEATURE_CONFIG_ID,
        "feature_config_sha256": FEATURE_CONFIG_SHA256,
        "sha256": feature.sha256,
        "shape": list(feature.shape),
        "size_bytes": feature.size_bytes,
    }
    return report


def _run(args: argparse.Namespace) -> dict[str, object]:
    manifest, manifest_bytes = _load_json_exact(
        args.input_manifest,
        args.input_manifest_sha256,
    )
    if (
        manifest.get("schema_version") != "pcdb-m5c-input-capability-manifest-v1"
        or manifest.get("manifest_status") != "passing"
        or manifest.get("contract_id") != "m5c-proteingym-prospective-v1"
        or manifest.get("contract_version") != 6
    ):
        raise ContractValidationError("M5C passing input-capability manifest drifted")
    raw_assays = manifest.get("assays")
    if (
        not isinstance(raw_assays, list)
        or tuple(item.get("assay_id") if isinstance(item, dict) else None for item in raw_assays)
        != ASSAY_IDS
    ):
        raise ContractValidationError("M5C input manifest assay cohort drifted")
    reports = [
        _run_assay(
            cast(Mapping[str, object], item),
            input_root=args.input_root,
            private_root=args.attempt_root / "private",
            workers=args.workers,
        )
        for item in raw_assays
    ]
    cohort = build_cohort_report(reports, attempt_id=args.attempt_root.name)
    aggregate = {
        "assays": reports,
        "attempt_id": args.attempt_root.name,
        "contract_digest": "abc16ed38664d63efede535de3ecd8b8552031e52b156c37023e7b7e717d5278",
        "input_capability_manifest_sha256": _sha256(manifest_bytes),
        "schema_version": "m5c-proteingym-sealed-aggregate-input-v1",
    }
    aggregate_bytes = compact_json_bytes(aggregate)
    report_bytes = compact_json_bytes(cohort)
    audit = {
        "candidate_level_outputs_persisted": 0,
        "cos_writes": 0,
        "hidden_truth_file_reads": 4,
        "input_capability_manifest_sha256": _sha256(manifest_bytes),
        "network_bytes": 0,
        "proxy_source_reads": 4,
        "schema_version": "m5c-proteingym-capability-audit-v1",
        "static_proxy_consumers": ["static_proxy_evaluator"],
        "surrogate_inputs": [
            "sequence-composition-420-v1",
            "branch-revealed-observations",
        ],
        "surrogate_workers_forked_before_truth_open": True,
    }
    audit_bytes = compact_json_bytes(audit)
    aggregate_root = args.attempt_root / "aggregate"
    write_once(aggregate_root / "aggregate-input.json", aggregate_bytes)
    write_once(aggregate_root / "audit.json", audit_bytes)
    write_once(aggregate_root / "report.json", report_bytes)
    inventory = {
        "artifacts": [
            {
                "path": "aggregate/aggregate-input.json",
                "sha256": _sha256(aggregate_bytes),
                "size_bytes": len(aggregate_bytes),
            },
            {
                "path": "aggregate/audit.json",
                "sha256": _sha256(audit_bytes),
                "size_bytes": len(audit_bytes),
            },
            {
                "path": "aggregate/report.json",
                "sha256": _sha256(report_bytes),
                "size_bytes": len(report_bytes),
            },
        ],
        "schema_version": "m5c-proteingym-terminal-inventory-v1",
    }
    inventory_bytes = compact_json_bytes(inventory)
    write_once(aggregate_root / "inventory.json", inventory_bytes)
    return {
        "aggregate_input_sha256": _sha256(aggregate_bytes),
        "assay_count": len(reports),
        "audit_sha256": _sha256(audit_bytes),
        "cohort_status": cohort["cohort_status"],
        "inventory_sha256": _sha256(inventory_bytes),
        "report_sha256": _sha256(report_bytes),
        "schema_version": "m5c-proteingym-worker-success-v1",
        "state_count": sum(int(report["complete_state_count"]) for report in reports),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-root", required=True, type=Path)
    parser.add_argument("--input-manifest", required=True, type=Path)
    parser.add_argument("--input-manifest-sha256", required=True)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be in [1,16]")
    try:
        result = _run(args)
    except (ContractValidationError, FileExistsError, OSError, ValueError) as exc:
        sys.stderr.buffer.write(
            compact_json_bytes(
                {
                    "failure_class": "validity"
                    if isinstance(exc, ContractValidationError)
                    else "implementation",
                    "reason": str(exc),
                    "schema_version": "m5c-proteingym-worker-failure-v1",
                }
            )
        )
        return 2
    sys.stdout.buffer.write(compact_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
