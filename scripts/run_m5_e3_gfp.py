#!/usr/bin/env python3
"""GFP extension of E3; candidate rows and predictions stay server-local."""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import importlib.metadata
import json
import multiprocessing as mp
import os
import signal
import threading
import time
from pathlib import Path

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np  # noqa: E402
import run_m5_e3_prediction as e3  # noqa: E402
from run_m5c_gfp_iteration import (  # noqa: E402
    exact_json,
    tree_rss,
    validate_membership,
)

from pcdb.m5 import proteingym_runtime as rt  # noqa: E402
from pcdb.m5.proteingym_confirmation import (  # noqa: E402
    INITIAL_SAMPLE_SIZES,
    MODEL_IDS,
    RUN_SEEDS,
    initial_selection,
)

ASSAY = "GFP_AEQVI_Sarkisyan_2016"
PROTOCOL = "m5-e3-gfp-fixed-policy-proxy-v1"
INPUT_SHA = "130d9f3d9086274c7209158cdcbb3a4054ff3742e3711220542adecae14828fc"
FEATURE_SHA = "c8cbad76b202b7043ef6bdddc295d7f28e28bb5b64aba4e21f24ee03217e5704"
AGGREGATE_SHA = "c2fd3cc0d802612653c65bd064cf3a49c9093deb19a70ceee0fd3737fa98c74d"
SPLIT_SHA = "6dd4d95ae37c367be900746268818f34ee2439c152f0cede6b94538f342eeae3"
HELPER_SHA = "92e384e1972f03144706000b84ef900a909127651478593692baa4207f1be0f1"
SPECS = [(m, n, s) for m in MODEL_IDS for n in INITIAL_SAMPLE_SIZES for s in RUN_SEEDS]
WORKER = None
digest, identity, write, event = e3.digest, e3.identity, e3.write, e3.event


def initialize(ids, feature_path, feature_sha, output, arm):
    global WORKER
    features = np.load(feature_path, mmap_mode="r")
    if features.shape != (len(ids), 420 if arm == "old" else 421):
        raise ValueError("observable feature shape mismatch")
    if digest(feature_path) != feature_sha or not np.isfinite(features).all():
        raise ValueError("observable feature identity mismatch")
    WORKER = (ASSAY, tuple(ids), features, feature_sha, Path(output), arm)


def select(task):
    """No truth/evaluator path or unrevealed label is part of this worker capability."""
    assay, ids, features, feature_sha, output, arm = WORKER
    index, model, size, seed, observations, original_state = task
    initial = initial_selection(ids, assay_id=assay, run_seed=seed, size=size)
    observed = {o.candidate_id: float(o.label) for o in observations}
    if len(observed) != size or len(observations) != size or set(observed) != set(initial):
        raise ValueError("allowed observation prefix mismatch")
    lookup = {cid: i for i, cid in enumerate(ids)}
    ordered = sorted(observed)
    oi = np.asarray([lookup[c] for c in ordered])
    remaining = np.asarray([i for i, c in enumerate(ids) if c not in observed])
    x, y = features[oi], np.asarray([observed[c] for c in ordered])
    draws = tuple(
        rt.bootstrap_draws(
            protocol_id=rt.PROTOCOL_ID,
            run_seed=seed,
            task_id=assay,
            model_id=model,
            round_index=1,
            observation_count=size,
        )
    )
    estimators = [rt._fit_model(model, x, y)]
    for draw in draws:
        sample = np.asarray(draw.indices)
        estimators.append(rt._fit_model(model, x[sample], y[sample]))
    if len(estimators) != 6:
        raise ValueError("bootstrap member count mismatch")
    predictions = np.empty((6, len(remaining)))
    for start in range(0, len(remaining), 384):
        block = features[remaining[start : start + 384]]
        for j, estimator in enumerate(estimators):
            predictions[j, start : start + len(block)] = rt._predict(estimator, block)
    mean, std = predictions[0], predictions[1:].std(axis=0, ddof=0)
    remain_ids = np.asarray([ids[i] for i in remaining], dtype="U64")
    greedy = rt._top_candidate_ids(remain_ids, mean, 16)
    exploration = rt._top_candidate_ids(
        remain_ids, std if model == MODEL_IDS[0] else mean + std, 16
    )
    state_id = (
        original_state
        if arm == "old"
        else identity(
            {
                "protocol": e3.PROTOCOL,
                "original_state_id": original_state,
                "feature_sha256": feature_sha,
            }
        )
    )
    matched = rt.matched_random_batches(
        state_id=state_id,
        greedy_ids=greedy,
        exploration_ids=exploration,
        remaining_pool=tuple(remain_ids),
    )
    overlap = set(greedy) & set(exploration)
    if any(len(set(b)) != 16 or set(b) & set(greedy) != overlap for b in matched):
        raise ValueError("matched overlap mismatch")
    selection = rt.StateSelection(
        assay_id=assay,
        exploration_ids=exploration,
        greedy_ids=greedy,
        initial_ids=initial,
        initial_sample_size=size,
        matched_ids=matched,
        model_id=model,
        run_seed=seed,
        state_id=state_id,
    )
    predpath = output / f"prediction-{index:03d}.npz"
    with predpath.open("xb") as handle:
        np.savez(
            handle,
            remaining_indices=remaining,
            mean=mean,
            std=std,
            members=predictions[1:],
        )
    meta = dataclasses.asdict(selection)
    meta["prediction_sha256"] = digest(predpath)
    write(output / f"selection-{index:03d}.json", meta)
    write(
        output / f"pairing-{index:03d}.json",
        {
            "original_state_id": original_state,
            "new_state_id": state_id,
            "observation_sha256": identity(sorted(observed.items())),
            "bootstrap_sha256": identity([list(d.indices) for d in draws]),
        },
    )
    return index


def run(args):
    e3.selftest()
    if digest(e3.__file__) != HELPER_SHA:
        raise ValueError("frozen E3 helper identity mismatch")
    if digest(rt.__file__) != "75504878c51f03590329b3e5cdb92e47f7c1be8191bec63c5c33af69763dfce0":
        raise ValueError("baseline runtime identity mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("runtime versions mismatch")
    manifest = exact_json(args.manifest, INPUT_SHA)
    # No paths for any other assay are opened.
    items = [item for item in manifest["assays"] if item["assay_id"] == ASSAY]
    if len(items) != 1:
        raise ValueError("GFP manifest identity mismatch")
    item = items[0]
    refs, root, output = item["artifacts"], args.input_root, args.output
    processing = refs["processing_manifest"]
    metadata = exact_json(root / processing["path"], processing["sha256"])
    split = exact_json(root / Path(processing["path"]).with_name("split-manifest.json"), SPLIT_SHA)
    if (
        metadata["label_direction"] != "higher_is_better"
        or metadata["hidden_label_field"] != "DMS_score"
        or metadata["observable_feature_fields"] != ["mutant", "mutated_sequence"]
        or split["partitions"] != ["candidate-pool"]
        or split["held_out_partitions"]
        or split["processing_manifest_sha256"] != processing["sha256"]
    ):
        raise ValueError("label/visibility/pool semantics mismatch")
    for role in ("candidate", "hidden_truth"):
        bound = refs[role]
        record = next(
            r for r in metadata["output_files"] if r["object_key"].endswith(bound["path"])
        )
        if (
            any(record[k] != bound[k] for k in ("sha256", "size_bytes"))
            or record["row_count"] != 51714
        ):
            raise ValueError("processing/input identities mismatch")
    ref = refs["candidate"]
    table = rt.load_candidate_table(
        root / ref["path"],
        assay_id=ASSAY,
        expected_sha256=ref["sha256"],
        expected_size=ref["size_bytes"],
    )
    if len(table.candidate_ids) != 51714:
        raise ValueError("candidate count mismatch")
    validate_membership(
        root / Path(processing["path"]).with_name("membership.csv.gz"),
        split["membership_file"],
        table.candidate_ids,
    )
    original = args.study / "full-001"
    old_aggregate = exact_json(original / "aggregate-input.json", AGGREGATE_SHA)
    old_states = {
        (r["model_id"], r["initial_sample_size"], r["run_seed"]): r
        for r in old_aggregate["state_aggregates"]
    }
    if len(old_states) != 40 or set(old_states) != set(SPECS):
        raise ValueError("baseline state coverage mismatch")
    basepath = original / "private/features.npy"
    if digest(basepath) != FEATURE_SHA:
        raise ValueError("baseline feature identity mismatch")
    private = output / "private"
    private.mkdir()
    for arm in ("old", "new"):
        (private / arm).mkdir()
    ref = refs["proxy_source"]
    proxy = rt.load_static_proxy(
        root / ref["path"],
        candidate_table=table,
        expected_sha256=ref["sha256"],
        expected_size=ref["size_bytes"],
    )
    features = np.column_stack([np.load(basepath, mmap_mode="r"), proxy])
    featurepath = private / "features.npy"
    with featurepath.open("xb") as handle:
        np.save(handle, features, allow_pickle=False)
    feature_sha = digest(featurepath)
    inputs = [
        {
            "assay_id": ASSAY,
            "candidate_sha256": table.sha256,
            "baseline_feature_sha256": FEATURE_SHA,
            "new_feature_sha256": feature_sha,
            "proxy_source_sha256": ref["sha256"],
            "column": "ESM1v_single",
            "dimension": 421,
        }
    ]
    del features, proxy
    write(
        output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": digest(__file__),
            "helper_sha256": HELPER_SHA,
            "runtime_source_sha256": digest(rt.__file__),
            "plan_sha256": digest(args.plan),
            "input_sha256": INPUT_SHA,
            "original_aggregate_sha256": AGGREGATE_SHA,
            "versions": versions,
            "state_specs": SPECS,
            "application_delta": None,
            "evidence_stage": "ITERATION",
            "truth_boundary": (
                "evaluator-only labels; observable/allowed-observation spawn workers; "
                "not OS isolation"
            ),
            "intervention": "same 420 features plus fixed ESM1v_single; no new labels",
        },
    )
    event(output, "observable_input_verified", assay_id=ASSAY)
    indices = [0, 20] if args.mode == "smoke" else list(range(40))
    evaluator = e3.Evaluator(root, item, table)
    for arm, path, sha in (("old", basepath, FEATURE_SHA), ("new", featurepath, feature_sha)):
        with mp.get_context("spawn").Pool(
            1,
            initializer=initialize,
            initargs=(table.candidate_ids, str(path), sha, str(private / arm), arm),
        ) as pool:
            for index in indices:
                model, size, seed = SPECS[index]
                initial = initial_selection(
                    table.candidate_ids,
                    assay_id=ASSAY,
                    run_seed=seed,
                    size=size,
                )
                task = (
                    index,
                    model,
                    size,
                    seed,
                    evaluator.truth.reveal(initial),
                    old_states[SPECS[index]]["state_id"],
                )
                if pool.apply(select, (task,)) != index:
                    raise ValueError("worker state identity mismatch")
                event(output, "prediction_saved", arm=arm, index=index)
    # Both workers exited; complete-truth diagnostics cannot affect saved choices.
    rows = []
    for index in indices:
        model, size, seed = SPECS[index]
        old_selection, baseline, before = evaluator.score(private / "old", index)
        if baseline != old_states[SPECS[index]]:
            raise ValueError("baseline exact branch reconstruction mismatch")
        selection, new, after = evaluator.score(private / "new", index)
        if list(selection.initial_ids) != list(old_selection.initial_ids):
            raise ValueError("paired initial observations mismatch")
        pairs = [
            json.loads((private / arm / f"pairing-{index:03d}.json").read_text())
            for arm in ("old", "new")
        ]
        for key in ("original_state_id", "observation_sha256", "bootstrap_sha256"):
            if pairs[0][key] != pairs[1][key]:
                raise ValueError("paired observations/bootstrap identity mismatch")
        decomposition = evaluator.compare(old_selection, selection)
        for policy in ("greedy", "exploration"):
            actual = (
                new["branches"][policy]["high_value"]["0.01"]["count"]
                - baseline["branches"][policy]["high_value"]["0.01"]["count"]
            )
            if decomposition[policy]["net_hits"] != actual:
                raise ValueError("discovery decomposition mismatch")
        rows.append(
            {
                "assay_id": ASSAY,
                "model_id": model,
                "initial_sample_size": size,
                "run_seed": seed,
                "old": baseline,
                "new": new,
                "old_diagnostics": before,
                "new_diagnostics": after,
                "decomposition": decomposition,
                "paired_initials": "PASS",
            }
        )
    result = {
        "protocol": PROTOCOL,
        "evidence_stage": "ITERATION",
        "completed_states": len(rows),
        "evaluated_new_branches": len(rows) * 66,
        "reconstructed_baseline_branches": len(rows) * 66,
        "inputs": inputs,
        "summaries": e3.summarize(rows, ASSAY) if args.mode == "full" else [],
        "rows": rows,
        "baseline_reconstruction": "PASS",
        "paired_initials": "PASS",
        "paired_bootstrap_draws": "PASS",
        "limits": [
            "development data",
            "not complete joint M5",
            "no population CI",
            "no application delta",
        ],
    }
    with (output / "result.json.gz").open("xb") as handle:
        handle.write(
            gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0)
        )
    event(output, "assay_scored", completed_states=len(rows))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--wall-seconds", type=int, default=2300)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        e3.selftest()
        print("self-test PASS")
        return 0
    if (
        not args.study
        or not args.output
        or not args.plan
        or not args.manifest
        or not args.input_root
    ):
        parser.error("study, output, plan, manifest and input-root are required")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    started, stop, breach = time.monotonic(), threading.Event(), []
    usage = {"peak_rss_bytes": 0, "peak_disk_bytes": 0}

    def terminate(signum, frame):
        raise RuntimeError("interrupted-or-resource-cap")

    def monitor():
        while not stop.wait(2):
            rss = tree_rss(os.getpid())
            size = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
            usage["peak_rss_bytes"] = max(usage["peak_rss_bytes"], rss)
            usage["peak_disk_bytes"] = max(usage["peak_disk_bytes"], size)
            if (
                rss > 1610612736
                or size > 1073741824
                or time.monotonic() - started > args.wall_seconds
            ):
                breach.append("budget")
                os.kill(os.getpid(), signal.SIGTERM)
                return

    signal.signal(signal.SIGTERM, terminate)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    status, failure = "failed", None
    try:
        run(args)
        status = "succeeded"
    except BaseException as exc:
        import traceback

        failure = "budget" if breach else type(exc).__name__
        write(
            args.output / "failure.json",
            {
                "failure_class": failure,
                "frames": [
                    {
                        "file": Path(f.filename).name,
                        "line": f.lineno,
                        "function": f.name,
                    }
                    for f in traceback.extract_tb(exc.__traceback__)
                ],
            },
        )
    finally:
        stop.set()
        thread.join(timeout=3)
        terminal = {
            "status": status,
            "failure_class": failure,
            "wall_seconds": time.monotonic() - started,
            "mode": args.mode,
            **usage,
            "source_sha256": digest(__file__),
            "artifacts": {p.name: digest(p) for p in args.output.iterdir() if p.is_file()},
        }
        write(args.output / "terminal.json", terminal)
        print(json.dumps(terminal), flush=True)
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
