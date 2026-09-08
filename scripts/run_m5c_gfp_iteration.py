#!/usr/bin/env python3
"""Bounded, fixed 40-state GFP M5C exploration; no formal four-assay promotion."""

from __future__ import annotations

import argparse
import csv
import gzip
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

from m5c_proteingym_worker import _init_selection_worker, _select_task  # noqa: E402

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
    write_once,
)

ASSAY = "GFP_AEQVI_Sarkisyan_2016"
BASE = "d563147a1b036066f01bf17bcf6ece14d0246126"
INPUT_SHA = "130d9f3d9086274c7209158cdcbb3a4054ff3742e3711220542adecae14828fc"
SPLIT_SHA = "6dd4d95ae37c367be900746268818f34ee2439c152f0cede6b94538f342eeae3"
SOURCE = Path(__file__).resolve().parents[1]
INPUT = SOURCE / "manifests/experiments/m5c/preflight-result-v5/input-capability-manifest.json"


def progress(output: Path, phase: str, completed: int) -> None:
    with (output / "progress.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "time": datetime.now(UTC).isoformat(),
                    "phase": phase,
                    "completed_states": completed,
                }
            )
            + "\n"
        )


def exact_json(path: Path, expected: str) -> dict:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("input metadata checksum mismatch")
    return json.loads(payload)


def write_json(path: Path, value: dict) -> None:
    write_once(path, json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n")


def validate_membership(path: Path, reference: dict, candidate_ids: tuple[str, ...]) -> None:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != reference["sha256"] or path.stat().st_size != reference["size_bytes"]:
        raise ValueError("GFP membership checksum mismatch")
    seen = set()
    with gzip.open(path, "rt", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        if next(reader) != ["candidate_id", "partition"]:
            raise ValueError("GFP membership header mismatch")
        for row in reader:
            if len(row) != 2 or row[1] != "candidate-pool" or row[0] in seen:
                raise ValueError("GFP membership partition or uniqueness mismatch")
            seen.add(row[0])
    if seen != set(candidate_ids) or len(seen) != reference["row_count"]:
        raise ValueError("GFP membership does not exactly cover candidates")


def tree_rss(pid: int) -> int:
    """Read only this process and its descendants, in bytes."""
    total = 0
    pending = [pid]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            lines = Path(f"/proc/{current}/status").read_text().splitlines()
            total += sum(int(line.split()[1]) * 1024 for line in lines if line.startswith("VmRSS:"))
            pending.extend(
                int(item)
                for item in Path(f"/proc/{current}/task/{current}/children").read_text().split()
            )
        except FileNotFoundError:
            pass
    return total


def run(args: argparse.Namespace, output: Path) -> dict:
    manifest = exact_json(INPUT, INPUT_SHA)
    item = next(item for item in manifest["assays"] if item["assay_id"] == ASSAY)
    artifacts = item["artifacts"]
    candidate = artifacts["candidate"]
    processing = artifacts["processing_manifest"]
    metadata = exact_json(args.input_root / processing["path"], processing["sha256"])
    split = exact_json(
        args.input_root / Path(processing["path"]).with_name("split-manifest.json"), SPLIT_SHA
    )
    if (
        metadata["label_direction"] != "higher_is_better"
        or metadata["hidden_label_field"] != "DMS_score"
        or metadata["observable_feature_fields"] != ["mutant", "mutated_sequence"]
        or split["partitions"] != ["candidate-pool"]
        or split["held_out_partitions"]
        or split["processing_manifest_sha256"] != processing["sha256"]
    ):
        raise ValueError("GFP label, visibility or pool semantics mismatch")
    for role in ("candidate", "hidden_truth"):
        bound = artifacts[role]
        record = next(
            row for row in metadata["output_files"] if row["object_key"].endswith(bound["path"])
        )
        if (
            any(record[key] != bound[key] for key in ("sha256", "size_bytes"))
            or record["row_count"] != 51714
        ):
            raise ValueError("GFP processing/input identities disagree")
    table = load_candidate_table(
        args.input_root / candidate["path"],
        assay_id=ASSAY,
        expected_sha256=candidate["sha256"],
        expected_size=candidate["size_bytes"],
    )
    if len(table.candidate_ids) != 51714:
        raise ValueError("GFP candidate count mismatch")
    result = {
        "assay_id": ASSAY,
        "candidate_count": len(table.candidate_ids),
        "candidate_identity_checks": "PASS",
        "label_direction_from_manifest": "higher_is_better",
        "processing_manifest_sha256": processing["sha256"],
        "split_manifest_sha256": SPLIT_SHA,
        "hidden_truth_read": False,
        "scientific_result": False,
        "mode": args.mode,
        "evidence_stage": "ITERATION / L1 GFP exploration",
    }
    feature = build_feature_artifact(table.sequences, output / "private/features.npy")
    result.update({"feature_sha256": feature.sha256, "feature_shape": list(feature.shape)})
    if args.mode == "candidate-check":
        result["truth_alignment_check"] = "NOT_RUN"
        result["membership_alignment_check"] = "NOT_RUN"
        return result
    # No protected truth is opened if ordinary-user access is missing. Never use
    # the readable proxy CSV's DMS_score column as an access-control workaround.
    truth = artifacts["hidden_truth"]
    if not os.access(args.input_root / truth["path"], os.R_OK):
        raise PermissionError("GFP processed hidden-truth is not readable by the evaluator account")
    membership = args.input_root / Path(processing["path"]).with_name("membership.csv.gz")
    if not os.access(membership, os.R_OK):
        raise PermissionError("GFP membership is not readable by the evaluator account")
    validate_membership(membership, split["membership_file"], table.candidate_ids)
    state_aggregates = []
    state_effects = []
    specifications = [("random-forest-sklearn-v1", 32, 1729)]
    if args.mode == "full":
        specifications = [
            (model, size, seed)
            for model in MODEL_IDS
            for size in INITIAL_SAMPLE_SIZES
            for seed in RUN_SEEDS
        ]
    progress(output, "paired-states", 0)
    # Spawn starts a fresh interpreter. Only features and revealed observations
    # are sent to model processes; no inherited truth array or evaluator object.
    with multiprocessing.get_context("spawn").Pool(
        1,
        initializer=_init_selection_worker,
        initargs=(ASSAY, table.candidate_ids, table.sha256, str(feature.path), feature.sha256),
    ) as pool:
        evaluator = load_truth_evaluator(
            args.input_root / truth["path"],
            candidate_table=table,
            expected_sha256=truth["sha256"],
            expected_size=truth["size_bytes"],
        )
        (output / "states").mkdir()
        for index, (model, size, seed) in enumerate(specifications):
            initial = initial_selection(
                table.candidate_ids, assay_id=ASSAY, run_seed=seed, size=size
            )
            selection = pool.apply(_select_task, ((model, size, seed, evaluator.reveal(initial)),))
            aggregate, effects = evaluate_state(selection, evaluator)
            state_aggregates.append(aggregate)
            state_effects.append(effects)
            write_json(output / "states" / f"state-{index:03d}.json", aggregate)
            progress(output, "paired-states", index + 1)
    if args.mode == "full":
        # Model workers have exited before the static evaluator opens the proxy source.
        progress(output, "static-proxy", 40)
        proxy_reference = artifacts["proxy_source"]
        proxy = load_static_proxy(
            args.input_root / proxy_reference["path"],
            candidate_table=table,
            expected_sha256=proxy_reference["sha256"],
            expected_size=proxy_reference["size_bytes"],
        )
        static = evaluator.evaluate_static_proxy(proxy)
        # Preserve aggregate-only classifier inputs before the bounded bootstrap.
        write_json(
            output / "aggregate-input.json",
            {
                "assay_id": ASSAY,
                "candidate_count": len(table.candidate_ids),
                "state_aggregates": state_aggregates,
                "static_proxy": static,
                "high_value_definitions": {
                    key: dict(value) for key, value in evaluator.definition_summary.items()
                },
            },
        )
        progress(output, "registered-bootstrap", 40)
        report = build_assay_aggregate(
            assay_id=ASSAY,
            candidate_count=len(table.candidate_ids),
            evaluator=evaluator,
            state_aggregates=state_aggregates,
            state_effects=state_effects,
            static_proxy=static,
        )
        report.update(
            {
                "evidence_stage": "ITERATION / L1 GFP exploration",
                "confidence_level": 0.99375,
                "cohort_replication": "NOT_EVALUATED",
                "base_commit": BASE,
                "entry_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "feature_sha256": feature.sha256,
            }
        )
        write_json(output / "assay-report.json", report)
        result["taxonomy"] = report["taxonomy"]
        result["primary_contrasts"] = report["primary_contrasts"]
        result["static_proxy_read"] = True
    result.update(
        {
            "hidden_truth_read": True,
            "truth_alignment_check": "PASS",
            "scientific_result": True,
            "membership_alignment_check": "PASS",
            "completed_states": len(state_aggregates),
            "branch_evaluations": len(state_aggregates) * 66,
            "claim_boundary": (
                "GFP-only fixed one-step exploration; not original four-assay confirmation"
            ),
            "feature_sha256": feature.sha256,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("candidate-check", "one-state", "full"), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.attempt_root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    wall_limit = 7200 if args.mode == "full" else 900
    stop = threading.Event()
    usage = {"peak_process_tree_rss_bytes": 0}

    def terminate(_signum: int, _frame: object) -> None:
        raise RuntimeError("probe stopped at resource limit")

    signal.signal(signal.SIGTERM, terminate)

    def monitor() -> None:
        while not stop.wait(1):
            rss = tree_rss(os.getpid())
            usage["peak_process_tree_rss_bytes"] = max(usage["peak_process_tree_rss_bytes"], rss)
            if rss > 1536 * 1024**2 or time.monotonic() - started > wall_limit:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    write_json(
        args.attempt_root / "execution.json",
        {
            "base_commit": BASE,
            "entry_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "input_manifest_sha256": INPUT_SHA,
            "mode": args.mode,
            "started_at": datetime.now(UTC).isoformat(),
            "versions": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "scikit-learn", "xgboost-cpu")
            },
            "limits": {
                "workers": 1,
                "model_threads": 1,
                "rss_bytes": 1536 * 1024**2,
                "wall_seconds": wall_limit,
            },
            "matrix": {
                "assay": ASSAY,
                "models": list(MODEL_IDS),
                "initial_sizes": list(INITIAL_SAMPLE_SIZES),
                "seeds": list(RUN_SEEDS),
                "batch_size": 16,
                "matched_replicates": 64,
            },
            "publication": "none",
            "authorization": "user-approved results-first GFP exploration",
        },
    )
    try:
        result = run(args, args.attempt_root)
        write_json(args.attempt_root / "result.json", result)
        terminal = {"status": "succeeded", "failure_class": None}
        code = 0
    except Exception as exc:
        # Error details are kept local, with no candidate rows, labels or scores.
        terminal = {
            "status": "stopped",
            "failure_class": "access"
            if isinstance(exc, PermissionError)
            else "validation-or-runtime",
            "error_type": type(exc).__name__,
            "reason": str(exc),
        }
        code = 1
    finally:
        stop.set()
        thread.join(timeout=2)
    terminal.update(usage)
    terminal.update(
        {"wall_seconds": time.monotonic() - started, "finished_at": datetime.now(UTC).isoformat()}
    )
    write_json(args.attempt_root / "terminal.json", terminal)
    print(json.dumps(terminal, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
