#!/usr/bin/env python3
"""Paired information ablation using saved predictions on a common remaining pool."""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing as mp
import os
import signal
import threading
import time
from pathlib import Path

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import analyze_m5_e3_frontier as frontier  # noqa: E402
import numpy as np  # noqa: E402
import run_m5_e3_prediction as e3  # noqa: E402

PROTOCOL = "m5-e3-common-pool-information-v1"
FRONTIER_SHA = "67e98445f6c4fe3f6fd46a966b2af321122ea4c1cc8371a7eac81fef164715ba"
FRONTIER_SOURCE_SHA = "5c4b1cc26f70134db894981373cee6c79bf20f8092e72b173a9d204aaceda449"
ARMS = ("old32", "old64", "new32", "new64")


def restrict_positions(source, target):
    positions = np.searchsorted(source, target)
    if (positions >= len(source)).any() or not np.array_equal(source[positions], target):
        raise ValueError("common pool not contained in source predictions")
    return positions


def select_saved(task):
    """Receives only IDs, predictions and expected hashes; no truth path or labels."""
    ids, old_dir, new_dir, index, expected, output = task
    saved = {}
    for version, directory in (("old", old_dir), ("new", new_dir)):
        vi = ("old", "new").index(version)
        for size, offset in ((32, 0), (64, 10)):
            meta, remaining, mean, std = frontier.load_prediction(directory, index + offset)
            prior = expected[str(size)]
            if (
                meta["prediction_sha256"] != prior["source_prediction_shas"][vi]
                or e3.digest(Path(directory) / f"selection-{index + offset:03d}.json")
                != prior["source_selection_shas"][vi]
            ):
                raise ValueError("saved source identity mismatch")
            saved[version + str(size)] = (meta, remaining, mean, std)
    initials = saved["old64"][0]["initial_ids"]
    model, seed = saved["old64"][0]["model_id"], saved["old64"][0]["run_seed"]
    for arm, (meta, remaining, _mean, _std) in saved.items():
        size = int(arm[-2:])
        if (
            meta["model_id"] != model
            or meta["run_seed"] != seed
            or meta["initial_sample_size"] != size
        ):
            raise ValueError("model/seed/size pairing mismatch")
        ref = saved["old" + str(size)][0]
        if meta["initial_ids"] != ref["initial_ids"]:
            raise ValueError("input-arm observations mismatch")
        if len(set(meta["initial_ids"])) != size or not set(meta["initial_ids"]) <= set(initials):
            raise ValueError("initial observations are not nested")
        expected_remaining = [i for i, c in enumerate(ids) if c not in set(meta["initial_ids"])]
        if remaining.tolist() != expected_remaining:
            raise ValueError("source remaining pool mismatch")
    common = saved["old64"][1]
    common_ids = np.asarray(ids)[common]
    selections, orders = {}, {}
    for arm, (meta, remaining, mean, std) in saved.items():
        positions = restrict_positions(remaining, common)
        mean, std = mean[positions], std[positions]
        order = np.lexsort((common_ids, -mean))
        greedy = common_ids[order[:16]].tolist()
        values = std if model == e3.MODEL_IDS[0] else mean + std
        exploration = list(e3.rt._top_candidate_ids(common_ids, values, 16))
        selections[arm] = {"greedy": greedy, "exploration": exploration}
        orders[arm] = order.astype(np.int32)
        if arm.endswith("64") and any(
            selections[arm][p] != meta[p + "_ids"] for p in ("greedy", "exploration")
        ):
            raise ValueError("64-observation endpoint selection mismatch")
    output = Path(output)
    path = output / f"orders-{index:03d}.npz"
    with path.open("xb") as handle:
        np.savez(handle, common_indices=common, **orders)
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "common_initial_ids": initials,
            "selections": selections,
            "order_sha256": e3.digest(path),
            "source_identity": expected,
        },
    )
    return index


def summarize(rows):
    summaries = []
    for assay in e3.ASSAY_IDS:
        for mi, model in enumerate(e3.MODEL_IDS):
            cell = sorted(
                [r for r in rows if r["assay_id"] == assay and r["model_id"] == model],
                key=lambda r: r["run_seed"],
            )
            if [r["run_seed"] for r in cell] != list(e3.RUN_SEEDS):
                raise ValueError("paired seed coverage mismatch")
            effects = {}
            for metric in cell[0]["arms"]["old32"]:
                values = {a: [r["arms"][a][metric] for r in cell] for a in ARMS}
                if any(v is None for arr in values.values() for v in arr):
                    effects[metric] = {
                        "defined_pairs": sum(
                            all(r["arms"][a][metric] is not None for a in ARMS) for r in cell
                        )
                    }
                    continue
                arrays = {a: np.asarray(v, dtype=float) for a, v in values.items()}
                old = arrays["old64"] - arrays["old32"]
                new = arrays["new64"] - arrays["new32"]
                effects[metric] = {
                    "arm_means": {a: float(v.mean()) for a, v in arrays.items()},
                    "information_effect_old": e3.interval(old, 11000 + mi),
                    "information_effect_new": e3.interval(new, 11000 + mi),
                    "effect_interaction": e3.interval(new - old, 11000 + mi),
                }
            summaries.append(
                {
                    "assay_id": assay,
                    "model_id": model,
                    "paired_seed_blocks": 10,
                    "effects": effects,
                    "total_label_cost_all_arms": 80,
                }
            )
    return summaries


def selftest():
    frontier.selftest()
    src, target = np.arange(32, 100), np.arange(64, 100)
    positions = restrict_positions(src, target)
    assert np.array_equal(src[positions], target)
    # Identical predictions remain identical after removing the extra initial candidates.
    assert np.array_equal((-src)[positions], -target)
    try:
        restrict_positions(src, np.array([31, 99]))
    except ValueError:
        pass
    else:
        raise AssertionError("noncontained pool accepted")
    assert e3.interval(np.arange(10) - np.arange(10), 11000)["interval"] == [0.0, 0.0]


def run(args):
    selftest()
    for path, expected in (
        (e3.__file__, frontier.E3_SHA),
        (e3.rt.__file__, frontier.RUNTIME_SHA),
        (frontier.__file__, FRONTIER_SOURCE_SHA),
        (args.previous / "full-001/result.json.gz", frontier.PREVIOUS_SHA),
        (args.previous / "frontier-full-001/result.json.gz", FRONTIER_SHA),
    ):
        if e3.digest(path) != expected:
            raise ValueError("source/prior result identity mismatch")
    previous = json.loads(gzip.decompress((args.previous / "full-001/result.json.gz").read_bytes()))
    prior_frontier = json.loads(
        gzip.decompress((args.previous / "frontier-full-001/result.json.gz").read_bytes())
    )

    def key(r):
        return r["assay_id"], r["model_id"], r["initial_sample_size"], r["run_seed"]

    prior = {key(r): r for r in previous["rows"]}
    identities = {key(r): r["source_identity"] for r in prior_frontier["rows"]}
    if len(prior) != 120 or set(prior) != set(identities):
        raise ValueError("prior state coverage mismatch")
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    if tuple(x["assay_id"] for x in manifest["assays"]) != e3.ASSAY_IDS:
        raise ValueError("assay scope mismatch")
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "input_sha256": e3.INPUT_SHA,
            "prior_result_sha256": frontier.PREVIOUS_SHA,
            "frontier_sha256": FRONTIER_SHA,
            "helper_sha256": frontier.E3_SHA,
            "frontier_source_sha256": FRONTIER_SOURCE_SHA,
            "runtime_sha256": frontier.RUNTIME_SHA,
            "new_fits": 0,
            "new_measurements": 0,
            "arms": ARMS,
            "label_cost_all_arms": 80,
            "common_initial_count": 64,
            "batch_size": 16,
            "evidence_stage": "ITERATION",
            "application_delta": None,
            "truth_boundary": "prediction-only worker exits before evaluator; not OS isolation",
        },
    )
    indices = [0, 20] if args.mode == "smoke" else [*range(10), *range(20, 30)]
    rows = []
    for item in manifest["assays"]:
        assay, ref = item["assay_id"], item["artifacts"]["candidate"]
        if (
            item["held_out_partitions"]
            or item["partition"] != "candidate-pool"
            or item["label_direction"] != "higher_is_better"
        ):
            raise ValueError("pool/label mismatch")
        table = e3.rt.load_candidate_table(
            root / ref["path"],
            assay_id=assay,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        ref = item["artifacts"]["membership"]
        e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
        old_dir = args.study / "model-diagnosis-001/private" / assay
        new_dir = args.previous / "full-001/private" / assay
        private = args.output / "private" / assay
        private.mkdir(parents=True)
        with mp.get_context("spawn").Pool(1) as pool:
            for index in indices:
                model, _, seed = e3.SPECS[index]
                expected = {str(n): identities[(assay, model, n, seed)] for n in (32, 64)}
                if (
                    pool.apply(
                        select_saved,
                        (
                            (
                                table.candidate_ids,
                                str(old_dir),
                                str(new_dir),
                                index,
                                expected,
                                str(private),
                            ),
                        ),
                    )
                    != index
                ):
                    raise ValueError("worker state mismatch")
        e3.event(args.output, "common_pool_choices_saved_worker_exited", assay_id=assay)
        evaluator = e3.Evaluator(root, item, table)
        truth = evaluator.truth._M5CTruthEvaluator__truth
        memberships = evaluator.truth._M5CTruthEvaluator__memberships
        denominator = int(memberships[0.01].sum())
        lookup = evaluator.lookup
        for index in indices:
            model, _, seed = e3.SPECS[index]
            meta = json.loads((private / f"selection-{index:03d}.json").read_text())
            path = private / f"orders-{index:03d}.npz"
            if e3.digest(path) != meta["order_sha256"]:
                raise ValueError("saved order identity mismatch")
            arms = {}
            with np.load(path, allow_pickle=False) as orders:
                common = orders["common_indices"]
                initial = meta["common_initial_ids"]
                if set(common.tolist()) != {
                    i for i, c in enumerate(table.candidate_ids) if c not in set(initial)
                }:
                    raise ValueError("evaluation common pool mismatch")
                for arm in ARMS:
                    version, size = arm[:-2], int(arm[-2:])
                    directory = old_dir if version == "old" else new_dir
                    offset = 0 if size == 32 else 10
                    source, remaining, mean, _ = frontier.load_prediction(directory, index + offset)
                    mean = mean[restrict_positions(remaining, common)]
                    f = frontier.frontier_metrics(
                        mean,
                        memberships[0.01][common],
                        memberships[0.02][common],
                        orders[arm],
                        denominator,
                    )
                    metrics = {
                        "spearman": e3.rho(mean, truth[common]),
                        "mae": float(np.abs(mean - truth[common]).mean()),
                        "top1_auc": f["auc_all"],
                        "near_auc": f["auc_near"],
                        "far_auc": f["auc_far"],
                    }
                    for policy in ("greedy", "exploration"):
                        branch = evaluator.truth.evaluate_batch(
                            initial, meta["selections"][arm][policy]
                        )
                        if (
                            size == 64
                            and branch
                            != prior[(assay, model, 64, seed)][version]["branches"][policy]
                        ):
                            raise ValueError("64-observation endpoint outcome mismatch")
                        metrics[policy + "_hits"] = branch["high_value"]["0.01"]["count"]
                        metrics[policy + "_recall"] = branch["high_value"]["0.01"]["recall"]
                        metrics[policy + "_best"] = branch["best_found_improvement"]
                    if metrics["greedy_hits"] != f["hits_16"]:
                        raise ValueError("ranking/selection hit mismatch")
                    if size == 64:
                        expected = prior[(assay, model, 64, seed)][version + "_diagnostics"]
                        for name in ("spearman", "mae", "top1_auc"):
                            if metrics[name] != expected[name]:
                                raise ValueError("64-observation prediction endpoint mismatch")
                    arms[arm] = metrics
            init32 = frontier.load_prediction(old_dir, index)[0]["initial_ids"]
            initial_coverage = {
                "observed_high_32": int(sum(memberships[0.01][lookup[c]] for c in init32)),
                "observed_high_64": int(sum(memberships[0.01][lookup[c]] for c in initial)),
            }
            rows.append(
                {
                    "assay_id": assay,
                    "model_id": model,
                    "run_seed": seed,
                    "remaining_count": len(common),
                    "high_value_denominator": denominator,
                    "label_cost_all_arms": 80,
                    "initial_coverage": initial_coverage,
                    "arms": arms,
                    "source_identity": meta["source_identity"],
                }
            )
        del evaluator, truth, memberships
        e3.event(args.output, "assay_scored", assay_id=assay, completed_blocks=len(rows))
    result = {
        "protocol": PROTOCOL,
        "paired_seed_blocks": len(rows),
        "scored_branches": len(rows) * 8,
        "new_fits": 0,
        "new_measurements": 0,
        "endpoint_reconstruction": "PASS",
        "common_pool": "PASS",
        "rows": rows,
        "summaries": summarize(rows) if args.mode == "full" else [],
        "limits": [
            "development information ablation",
            "not a measurement-efficiency comparison",
            "sample amount and coverage not separated",
            "no population CI",
            "no application delta",
            "joint M5 incomplete",
        ],
    }
    with (args.output / "result.json.gz").open("xb") as handle:
        handle.write(
            gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0)
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "previous", "output", "plan"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--wall-seconds", type=int, default=450)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        print("information self-test PASS")
        return 0
    if any(getattr(args, k) is None for k in ("study", "previous", "output", "plan")):
        parser.error("study, previous, output and plan required")
    if args.wall_seconds <= 0 or args.wall_seconds > 900:
        parser.error("invalid wall cap")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    started, stop, breach = time.monotonic(), threading.Event(), []
    usage = {"peak_rss_bytes": 0, "peak_disk_bytes": 0}

    def terminate(signum, frame):
        raise RuntimeError("interrupted or resource cap")

    def monitor():
        while not stop.wait(1):
            rss = e3.tree_rss(os.getpid())
            size = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
            usage["peak_rss_bytes"] = max(usage["peak_rss_bytes"], rss)
            usage["peak_disk_bytes"] = max(usage["peak_disk_bytes"], size)
            if (
                rss > 1610612736
                or size > 268435456
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
        e3.write(
            args.output / "failure.json",
            {
                "failure_class": failure,
                "frames": [
                    {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                    for f in traceback.extract_tb(exc.__traceback__)
                ],
            },
        )
    finally:
        stop.set()
        thread.join(timeout=3)
        usage["peak_disk_bytes"] = max(
            usage["peak_disk_bytes"],
            sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file()),
        )
        terminal = {
            "status": status,
            "failure_class": failure,
            "wall_seconds": time.monotonic() - started,
            "mode": args.mode,
            **usage,
            "source_sha256": e3.digest(__file__),
            "artifacts": {p.name: e3.digest(p) for p in args.output.iterdir() if p.is_file()},
        }
        e3.write(args.output / "terminal.json", terminal)
        print(json.dumps(terminal), flush=True)
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
