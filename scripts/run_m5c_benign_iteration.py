#!/usr/bin/env python3
"""Fixed same-method one-step comparisons on three approved benign assays."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import multiprocessing
import os
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_variable] = "1"

import numpy as np  # noqa: E402
from m5c_benign_scope import ASSAY_IDS, BASE_COMMIT, COUNTS, STUDY_ID  # noqa: E402
from run_m5c_gfp_iteration import (  # noqa: E402
    exact_json,
    progress,
    tree_rss,
    validate_membership,
    write_json,
)

from pcdb.m5.proteingym_confirmation import (  # noqa: E402
    INITIAL_SAMPLE_SIZES,
    MODEL_IDS,
    RUN_SEEDS,
    initial_selection,
)
from pcdb.m5.proteingym_runtime import (  # noqa: E402
    build_assay_aggregate,
    build_feature_artifact,
    evaluate_state,
    load_candidate_table,
    load_static_proxy,
    load_truth_evaluator,
    select_state,
)

_WORKER = None


def initialize_worker(assay, ids, candidate_sha, feature_path, feature_sha):
    global _WORKER
    features = np.load(feature_path, mmap_mode="r")
    if assay not in ASSAY_IDS or features.shape != (len(ids), 420):
        raise ValueError("iteration worker input identity drifted")
    _WORKER = (assay, ids, candidate_sha, features, feature_sha)


def select_task(task):
    if _WORKER is None:
        raise RuntimeError("worker is not initialized")
    assay, ids, candidate_sha, features, feature_sha = _WORKER
    model, size, seed, observations = task
    return select_state(
        assay_id=assay,
        candidate_ids=ids,
        candidate_pool_sha256=candidate_sha,
        features=features,
        feature_sha256=feature_sha,
        initial_sample_size=size,
        model_id=model,
        observations=observations,
        run_seed=seed,
        assay_ids=ASSAY_IDS,
    )


def run(args, output):
    manifest = exact_json(args.input_root / "input.json", args.input_sha)
    item = next(row for row in manifest["assays"] if row["assay_id"] == args.assay)
    if (
        manifest["study_id"] != STUDY_ID
        or item["candidate_count"] != COUNTS[args.assay]
        or item["label_direction"] != "higher_is_better"
        or item["label_field"] != "DMS_score"
        or item["observable_fields"] != ["mutant", "mutated_sequence"]
        or item["partition"] != "candidate-pool"
        or item["held_out_partitions"]
        or item["sample_sequence_reference_check"] != "PASS"
        or item["id_coverage_check"] != "PASS"
    ):
        raise ValueError("iteration identity/label/visibility input mismatch")
    artifacts = item["artifacts"]
    if args.assay == ASSAY_IDS[2]:
        evidence = exact_json(args.label_evidence, args.label_evidence_sha)
        if (
            evidence["status"] != "PASS"
            or evidence["matched_count"] != COUNTS[args.assay]
            or evidence["proteingym_truth_sha256"] != artifacts["truth_source"]["sha256"]
            or evidence["mismatched_count"] != 0
            or evidence["missing_count"] != 0
        ):
            raise ValueError("GCK measured-label source has not been reconciled")
    candidate = artifacts["candidate"]
    table = load_candidate_table(
        args.input_root / candidate["path"],
        assay_id=args.assay,
        expected_sha256=candidate["sha256"],
        expected_size=candidate["size_bytes"],
    )
    if len(table.candidate_ids) != COUNTS[args.assay]:
        raise ValueError("candidate count mismatch")
    membership = artifacts["membership"]
    validate_membership(args.input_root / membership["path"], membership, table.candidate_ids)
    feature = build_feature_artifact(table.sequences, output / "private/features.npy")
    specifications = [(MODEL_IDS[0], 32, RUN_SEEDS[0])]
    if args.mode == "full":
        specifications = [
            (m, n, s) for m in MODEL_IDS for n in INITIAL_SAMPLE_SIZES for s in RUN_SEEDS
        ]
    states, effects = [], []
    (output / "states").mkdir()
    progress(output, "paired-states", 0)
    truth = artifacts["hidden_truth"]
    # Fresh workers receive only candidate IDs, features, and explicitly revealed observations.
    with multiprocessing.get_context("spawn").Pool(
        1,
        initializer=initialize_worker,
        initargs=(args.assay, table.candidate_ids, table.sha256, str(feature.path), feature.sha256),
    ) as pool:
        evaluator = load_truth_evaluator(
            args.input_root / truth["path"],
            candidate_table=table,
            expected_sha256=truth["sha256"],
            expected_size=truth["size_bytes"],
        )
        for index, (model, size, seed) in enumerate(specifications):
            initial = initial_selection(
                table.candidate_ids,
                assay_id=args.assay,
                run_seed=seed,
                size=size,
                assay_ids=ASSAY_IDS,
            )
            selected = pool.apply(select_task, ((model, size, seed, evaluator.reveal(initial)),))
            state, effect = evaluate_state(selected, evaluator)
            states.append(state)
            effects.append(effect)
            write_json(output / "states" / f"state-{index:03d}.json", state)
            progress(output, "paired-states", index + 1)
    result = {
        "assay_id": args.assay,
        "mode": args.mode,
        "candidate_count": len(table.candidate_ids),
        "candidate_identity_checks": "PASS",
        "truth_alignment_check": "PASS",
        "membership_alignment_check": "PASS",
        "completed_states": len(states),
        "branch_evaluations": len(states) * 66,
        "feature_sha256": feature.sha256,
        "claim_boundary": (
            "revised benign ProteinGym ITERATION; not original four-assay confirmation"
        ),
        "scientific_result": True,
    }
    if args.mode == "full":
        progress(output, "static-proxy", 40)
        reference = artifacts["proxy_source"]
        proxy = load_static_proxy(
            args.input_root / reference["path"],
            candidate_table=table,
            expected_sha256=reference["sha256"],
            expected_size=reference["size_bytes"],
        )
        static = evaluator.evaluate_static_proxy(proxy)
        write_json(
            output / "aggregate-input.json",
            {
                "assay_id": args.assay,
                "candidate_count": len(table.candidate_ids),
                "state_aggregates": states,
                "static_proxy": static,
                "high_value_definitions": {
                    k: dict(v) for k, v in evaluator.definition_summary.items()
                },
            },
        )
        progress(output, "registered-bootstrap", 40)
        report = build_assay_aggregate(
            assay_id=args.assay,
            candidate_count=len(table.candidate_ids),
            evaluator=evaluator,
            state_aggregates=states,
            state_effects=effects,
            static_proxy=static,
            assay_ids=ASSAY_IDS,
        )
        report.update(
            {
                "evidence_stage": "ITERATION / L1 revised benign cohort",
                "confidence_level": 0.99375,
                "cohort_replication": "NOT_EVALUATED",
                "base_commit": BASE_COMMIT,
                "study_id": STUDY_ID,
                "entry_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "feature_sha256": feature.sha256,
            }
        )
        write_json(output / "assay-report.json", report)
        result.update(taxonomy=report["taxonomy"], primary_contrasts=report["primary_contrasts"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--input-sha", required=True)
    parser.add_argument("--assay", choices=ASSAY_IDS, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("one-state", "full"), required=True)
    parser.add_argument("--label-evidence", type=Path)
    parser.add_argument("--label-evidence-sha")
    args = parser.parse_args()
    os.umask(0o077)
    args.attempt_root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    stop = threading.Event()
    usage = {"peak_process_tree_rss_bytes": 0}
    wall_limit = 7200 if args.mode == "full" else 900

    def terminate(_signum, _frame):
        raise RuntimeError("iteration resource limit exceeded")

    signal.signal(signal.SIGTERM, terminate)

    def monitor():
        while not stop.wait(1):
            rss = tree_rss(os.getpid())
            usage["peak_process_tree_rss_bytes"] = max(usage["peak_process_tree_rss_bytes"], rss)
            if rss > 1536 * 1024**2 or time.monotonic() - started > wall_limit:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    source_files = [Path(__file__), Path(__file__).with_name("m5c_benign_scope.py")]
    source_files += [
        Path(__file__).resolve().parents[1] / "src/pcdb/m5" / name
        for name in ("proteingym_confirmation.py", "proteingym_runtime.py")
    ]
    write_json(
        args.attempt_root / "execution.json",
        {
            "study_id": STUDY_ID,
            "base_commit": BASE_COMMIT,
            "assay_id": args.assay,
            "source_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files
            },
            "input_sha256": args.input_sha,
            "label_evidence_sha256": args.label_evidence_sha,
            "mode": args.mode,
            "started_at": datetime.now(UTC).isoformat(),
            "versions": {
                n: importlib.metadata.version(n) for n in ("numpy", "scikit-learn", "xgboost-cpu")
            },
            "limits": {
                "workers": 1,
                "model_threads": 1,
                "rss_bytes": 1536 * 1024**2,
                "wall_seconds": wall_limit,
            },
            "matrix": {
                "models": list(MODEL_IDS),
                "initial_sizes": list(INITIAL_SAMPLE_SIZES),
                "seeds": list(RUN_SEEDS),
                "batch_size": 16,
                "matched_replicates": 64,
            },
            "authorization": "user-approved same-dataset three-assay substitution; results first",
        },
    )
    try:
        write_json(args.attempt_root / "result.json", run(args, args.attempt_root))
        terminal, code = {"status": "succeeded", "failure_class": None}, 0
    except Exception as exc:
        terminal, code = (
            {
                "status": "stopped",
                "failure_class": "validation-or-runtime",
                "error_type": type(exc).__name__,
                "reason": str(exc),
            },
            1,
        )
    finally:
        stop.set()
        thread.join(timeout=2)
    terminal.update(usage)
    terminal.update(
        wall_seconds=time.monotonic() - started, finished_at=datetime.now(UTC).isoformat()
    )
    write_json(args.attempt_root / "terminal.json", terminal)
    print(json.dumps(terminal, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
