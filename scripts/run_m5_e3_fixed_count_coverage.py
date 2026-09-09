#!/usr/bin/env python3
"""Fixed-count, label-blind coverage intervention on approved benign C tasks."""

from __future__ import annotations

import argparse
import gzip
import hashlib
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

PROTOCOL = "m5-e3-fixed-count-coverage-v1"
HELPER_SHA = "92e384e1972f03144706000b84ef900a909127651478593692baa4207f1be0f1"
RUNTIME_SHA = "75504878c51f03590329b3e5cdb92e47f7c1be8191bec63c5c33af69763dfce0"
INPUT_RESULT_SHA = "937febfe04c7f45ff490249fdce1f33b0944d54b7a9a9a9939f3a0776d51eb08"
INFORMATION_RESULT_SHA = "ba0de452db9012fe0b9960168ea5d9b5422eafbd0c2cd7a456170f327b5dcded"
WORKER = None


def initialize(assay, ids, feature_path, feature_sha, output):
    global WORKER
    features = np.load(feature_path, mmap_mode="r", allow_pickle=False)
    if (
        assay not in e3.ASSAY_IDS
        or features.shape != (len(ids), 421)
        or e3.digest(feature_path) != feature_sha
        or not np.isfinite(features).all()
    ):
        raise ValueError("observable feature identity mismatch")
    WORKER = assay, tuple(ids), features, feature_sha, Path(output)


def squared_distances(x, centers):
    distances = np.einsum("ij,ij->i", x, x)[:, None]
    distances = distances + np.einsum("ij,ij->i", centers, centers)[None, :]
    distances -= 2 * (x @ centers.T)
    return np.maximum(distances, 0)


def greedy_centers(distances, center_ids, count):
    """Greedy facility location; labels and predictions are not arguments."""
    if (
        distances.ndim != 2
        or distances.shape[1] != len(center_ids)
        or len(set(center_ids)) != len(center_ids)
        or count < 1
        or count > len(center_ids)
        or not np.isfinite(distances).all()
        or (distances < 0).any()
    ):
        raise ValueError("invalid center-selection inputs")
    nearest = np.full(len(distances), np.inf)
    selected, trace = [], []
    for _ in range(count):
        costs = np.minimum(nearest[:, None], distances).mean(axis=0)
        costs[selected] = np.inf
        index = int(np.lexsort((np.asarray(center_ids), costs))[0])
        selected.append(index)
        nearest = np.minimum(nearest, distances[:, index])
        trace.append(float(nearest.mean()))
    return selected, trace


def prepare(seed):
    """A label-blind worker saves each training subset before any label reveal."""
    assay, ids, features, sha, output = WORKER
    lookup = {c: i for i, c in enumerate(ids)}
    reservoir = e3.initial_selection(
        ids, assay_id=assay, run_seed=seed, size=64, assay_ids=e3.ASSAY_IDS
    )
    random32 = e3.initial_selection(
        ids, assay_id=assay, run_seed=seed, size=32, assay_ids=e3.ASSAY_IDS
    )
    if not set(random32) < set(reservoir) or len(set(reservoir)) != 64:
        raise ValueError("original nested state mismatch")
    common = np.array([i for i, c in enumerate(ids) if c not in set(reservoir)])
    center_ids = tuple(sorted(reservoir))
    center_indices = np.array([lookup[c] for c in center_ids])
    distances = squared_distances(features[common, :420], features[center_indices, :420])
    selected, trace = greedy_centers(distances, center_ids, 32)
    chosen = [center_ids[i] for i in selected]
    baseline_indices = [center_ids.index(c) for c in random32]
    old = distances[:, baseline_indices].min(axis=1)
    new = distances[:, selected].min(axis=1)
    coverage = {
        "random_mean_distance": float(old.mean()),
        "coverage_mean_distance": float(new.mean()),
        "random_p90_distance": float(np.quantile(old, 0.9)),
        "coverage_p90_distance": float(np.quantile(new, 0.9)),
        "training_overlap": len(set(random32) & set(chosen)),
    }
    e3.write(
        output / f"training-{seed}.json",
        {
            "assay_id": assay,
            "run_seed": seed,
            "feature_sha256": sha,
            "common_initial_ids": reservoir,
            "random_training_ids": random32,
            "coverage_training_ids": chosen,
            "coverage": coverage,
            "distance_sha256": hashlib.sha256(distances.tobytes()).hexdigest(),
            "greedy_objective_trace": trace,
        },
    )
    return seed


def load_prediction(directory, index):
    path = Path(directory) / f"prediction-{index:03d}.npz"
    meta = json.loads((Path(directory) / f"selection-{index:03d}.json").read_text())
    if e3.digest(path) != meta["prediction_sha256"]:
        raise ValueError("prediction digest mismatch")
    with np.load(path, allow_pickle=False) as data:
        remaining, mean, std = [data[k] for k in ("remaining_indices", "mean", "std")]
        if "members" in data and not np.array_equal(data["members"].std(axis=0), std):
            raise ValueError("uncertainty reconstruction mismatch")
    if (
        remaining.shape != mean.shape
        or mean.shape != std.shape
        or not np.isfinite(mean).all()
        or not np.isfinite(std).all()
        or (std < 0).any()
    ):
        raise ValueError("invalid saved prediction")
    return meta, remaining, mean, std


def choices(ids, mean, std, model):
    order = np.lexsort((ids, -mean))
    score = std if model == e3.MODEL_IDS[0] else mean + std
    return order, {
        "greedy": ids[order[:16]].tolist(),
        "exploration": list(e3.rt._top_candidate_ids(ids, score, 16)),
    }


def fit_and_select(task):
    """Receives exactly the selected32 observations; no hidden labels or evaluator."""
    assay, ids, features, sha, output = WORKER
    index, model, seed, observations, old_dir, old_identity = task
    training_path = output / f"training-{seed}.json"
    training = json.loads(training_path.read_text())
    if training["feature_sha256"] != sha:
        raise ValueError("training input identity mismatch")
    allowed = set(training["coverage_training_ids"])
    observed = {o.candidate_id: float(o.label) for o in observations}
    if len(observations) != 32 or len(observed) != 32 or set(observed) != allowed:
        raise ValueError("training-label capability mismatch")
    lookup = {c: i for i, c in enumerate(ids)}
    ordered = sorted(observed)
    oi = np.array([lookup[c] for c in ordered])
    common = np.array(
        [i for i, c in enumerate(ids) if c not in set(training["common_initial_ids"])]
    )
    x, y = features[oi], np.array([observed[c] for c in ordered])
    draws = tuple(
        e3.rt.bootstrap_draws(
            protocol_id=e3.rt.PROTOCOL_ID,
            run_seed=seed,
            task_id=assay,
            model_id=model,
            round_index=1,
            observation_count=32,
        )
    )
    estimators = [e3.rt._fit_model(model, x, y)]
    for draw in draws:
        sample = np.array(draw.indices)
        estimators.append(e3.rt._fit_model(model, x[sample], y[sample]))
    if len(estimators) != 6:
        raise ValueError("bootstrap member count mismatch")
    predictions = np.empty((6, len(common)))
    for start in range(0, len(common), 384):
        block = features[common[start : start + 384]]
        for j, estimator in enumerate(estimators):
            predictions[j, start : start + len(block)] = e3.rt._predict(estimator, block)
    mean, std = predictions[0], predictions[1:].std(axis=0, ddof=0)
    common_ids = np.asarray(ids)[common]
    order, selected = choices(common_ids, mean, std, model)
    path = output / f"prediction-{index:03d}.npz"
    with path.open("xb") as handle:
        np.savez(handle, remaining_indices=common, mean=mean, std=std, members=predictions[1:])
    baseline, remaining, before_mean, before_std = load_prediction(old_dir, index)
    if (
        baseline["prediction_sha256"] != old_identity["source_prediction_shas"][1]
        or e3.digest(Path(old_dir) / f"selection-{index:03d}.json")
        != old_identity["source_selection_shas"][1]
        or baseline["model_id"] != model
        or baseline["run_seed"] != seed
        or list(baseline["initial_ids"]) != training["random_training_ids"]
    ):
        raise ValueError("baseline identity mismatch")
    positions = np.searchsorted(remaining, common)
    if (positions >= len(remaining)).any() or not np.array_equal(remaining[positions], common):
        raise ValueError("baseline common-pool mismatch")
    old_order, old_selected = choices(
        common_ids, before_mean[positions], before_std[positions], model
    )
    order_path = output / f"orders-{index:03d}.npz"
    with order_path.open("xb") as handle:
        np.savez(handle, random=old_order, coverage=order)
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "assay_id": assay,
            "model_id": model,
            "run_seed": seed,
            "prediction_sha256": e3.digest(path),
            "order_sha256": e3.digest(order_path),
            "training_sha256": e3.digest(training_path),
            "observation_sha256": e3.identity(sorted(observed.items())),
            "bootstrap_sha256": e3.identity([list(d.indices) for d in draws]),
            "random": old_selected,
            "coverage": selected,
            "old_source_identity": old_identity,
        },
    )
    return index


def prediction_metrics(mean, truth, high, top2, order):
    near, far = top2 & ~high, ~top2
    high_ranks = np.flatnonzero(high[order]) + 1
    out = {
        "spearman": e3.rho(mean, truth),
        "mae": float(np.abs(mean - truth).mean()),
        "top1_auc": e3.auc(mean, high),
        "near_auc": e3.auc(mean[high | near], high[high | near]),
        "far_auc": e3.auc(mean[high | far], high[high | far]),
        "first_high_rank": int(high_ranks[0]) if len(high_ranks) else None,
        "median_high_rank": float(np.median(high_ranks)) if len(high_ranks) else None,
        "top16_high": int(high[order[:16]].sum()),
        "top16_near": int(near[order[:16]].sum()),
        "top16_far": int(far[order[:16]].sum()),
    }
    if out["top16_high"] + out["top16_near"] + out["top16_far"] != 16:
        raise ValueError("top16 category mismatch")
    return out


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
            for metric in cell[0]["random"]:
                pairs = [(r["random"][metric], r["coverage"][metric]) for r in cell]
                if any(a is None or b is None for a, b in pairs):
                    effects[metric] = {
                        "defined_pairs": sum(a is not None and b is not None for a, b in pairs)
                    }
                else:
                    effects[metric] = {
                        "random": float(np.mean([a for a, b in pairs])),
                        "coverage": float(np.mean([b for a, b in pairs])),
                        "change": e3.interval([b - a for a, b in pairs], 11100 + mi),
                    }
            summaries.append(
                {"assay_id": assay, "model_id": model, "paired_states": 10, "effects": effects}
            )
    return summaries


def selftest():
    e3.selftest()
    x = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [7.0, 0.0], [9.0, 0.0]])
    centers = np.array([[0.0, 0.0], [2.0, 0.0], [8.0, 0.0]])
    distances = squared_distances(x, centers)
    assert np.allclose(
        distances, ((x[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), atol=1e-12
    )
    chosen, trace = greedy_centers(distances, ("a", "b", "c"), 2)
    assert len(set(chosen)) == 2 and trace[1] <= trace[0]
    assert greedy_centers(np.zeros((4, 3)), ("c", "a", "b"), 2)[0] == [1, 2]
    # Reordering the center table preserves ID-level tie handling and selected IDs.
    perm = np.array([2, 0, 1])
    second, _ = greedy_centers(distances[:, perm], np.array(["a", "b", "c"])[perm], 2)
    assert [("a", "b", "c")[i] for i in chosen] == [("a", "b", "c")[perm[i]] for i in second]
    assert e3.interval(np.zeros(10), 11100)["interval"] == [0.0, 0.0]


def run(args):
    selftest()
    for path, expected in (
        (e3.__file__, HELPER_SHA),
        (e3.rt.__file__, RUNTIME_SHA),
        (args.previous / "full-001/result.json.gz", INPUT_RESULT_SHA),
        (args.previous / "information-full-001/result.json.gz", INFORMATION_RESULT_SHA),
    ):
        if e3.digest(path) != expected:
            raise ValueError("source/prior identity mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("runtime mismatch")
    prior = json.loads(
        gzip.decompress((args.previous / "information-full-001/result.json.gz").read_bytes())
    )
    keyed = {(r["assay_id"], r["model_id"], r["run_seed"]): r for r in prior["rows"]}
    source = json.loads(gzip.decompress((args.previous / "full-001/result.json.gz").read_bytes()))
    feature_shas = {r["assay_id"]: r["new_feature_sha256"] for r in source["inputs"]}
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
            "helper_sha256": HELPER_SHA,
            "runtime_sha256": RUNTIME_SHA,
            "input_sha256": e3.INPUT_SHA,
            "prior_result_sha256": INFORMATION_RESULT_SHA,
            "input_result_sha256": INPUT_RESULT_SHA,
            "versions": versions,
            "training_count": 32,
            "available_observations": 64,
            "label_cost": 80,
            "batch_size": 16,
            "geometry_columns": 420,
            "model_columns": 421,
            "application_delta": None,
            "truth_boundary": "label-blind subsets; 32-observation workers; evaluator scoring",
            "evidence_stage": "ITERATION",
            "geometry": "greedy mean nearest squared Euclidean; ID ties",
        },
    )
    seeds = (1729,) if args.mode == "smoke" else e3.RUN_SEEDS
    rows, coverages = [], []
    for item in manifest["assays"]:
        assay, ref = item["assay_id"], item["artifacts"]["candidate"]
        if (
            item["partition"] != "candidate-pool"
            or item["held_out_partitions"]
            or item["label_direction"] != "higher_is_better"
        ):
            raise ValueError("candidate/label scope mismatch")
        table = e3.rt.load_candidate_table(
            root / ref["path"],
            assay_id=assay,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        ref = item["artifacts"]["membership"]
        e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
        private = args.output / "private" / assay
        private.mkdir(parents=True)
        old_dir = args.previous / "full-001/private" / assay
        feature_path = old_dir / "features.npy"
        initargs = (
            assay,
            table.candidate_ids,
            str(feature_path),
            feature_shas[assay],
            str(private),
        )
        with mp.get_context("spawn").Pool(1, initializer=initialize, initargs=initargs) as pool:
            for seed in seeds:
                if pool.apply(prepare, (seed,)) != seed:
                    raise ValueError("coverage preparation mismatch")
        e3.event(args.output, "all_training_subsets_saved", assay_id=assay)
        evaluator = e3.Evaluator(root, item, table)
        indices = [i for i, (m, n, s) in enumerate(e3.SPECS) if n == 32 and s in seeds]
        with mp.get_context("spawn").Pool(1, initializer=initialize, initargs=initargs) as pool:
            for index in indices:
                model, _, seed = e3.SPECS[index]
                training = json.loads((private / f"training-{seed}.json").read_text())
                original64 = e3.initial_selection(
                    table.candidate_ids,
                    assay_id=assay,
                    run_seed=seed,
                    size=64,
                    assay_ids=e3.ASSAY_IDS,
                )
                if list(original64) != training["common_initial_ids"] or not set(
                    training["coverage_training_ids"]
                ) <= set(original64):
                    raise ValueError("allowed-observation scope mismatch")
                observations = evaluator.truth.reveal(training["coverage_training_ids"])
                expected = keyed[(assay, model, seed)]["source_identity"]["32"]
                task = (index, model, seed, observations, str(old_dir), expected)
                if pool.apply(fit_and_select, (task,)) != index:
                    raise ValueError("fit state mismatch")
                e3.event(args.output, "prediction_saved", assay_id=assay, index=index)
        e3.event(args.output, "fit_workers_exited", assay_id=assay)
        truth = evaluator.truth._M5CTruthEvaluator__truth
        membership = evaluator.truth._M5CTruthEvaluator__memberships
        for seed in seeds:
            training = json.loads((private / f"training-{seed}.json").read_text())
            coverages.append({"assay_id": assay, "run_seed": seed, **training["coverage"]})
        for index in indices:
            model, _, seed = e3.SPECS[index]
            meta, common, newmean, newstd = load_prediction(private, index)
            training_path = private / f"training-{seed}.json"
            if e3.digest(training_path) != meta["training_sha256"]:
                raise ValueError("training selection changed after fitting")
            training = json.loads(training_path.read_text())
            _, remaining, oldmean, oldstd = load_prediction(old_dir, index)
            positions = np.searchsorted(remaining, common)
            oldmean, oldstd = oldmean[positions], oldstd[positions]
            common_ids = np.asarray(table.candidate_ids)[common]
            orderpath = private / f"orders-{index:03d}.npz"
            if e3.digest(orderpath) != meta["order_sha256"]:
                raise ValueError("saved order identity mismatch")
            arms = {}
            with np.load(orderpath, allow_pickle=False) as orders:
                for arm, mean, std in (("random", oldmean, oldstd), ("coverage", newmean, newstd)):
                    order, selected = choices(common_ids, mean, std, model)
                    if not np.array_equal(order, orders[arm]) or selected != meta[arm]:
                        raise ValueError("saved selection reconstruction mismatch")
                    metrics = prediction_metrics(
                        mean,
                        truth[common],
                        membership[0.01][common],
                        membership[0.02][common],
                        orders[arm],
                    )
                    for policy in ("greedy", "exploration"):
                        outcome = evaluator.truth.evaluate_batch(
                            training["common_initial_ids"], meta[arm][policy]
                        )
                        metrics[policy + "_hits"] = outcome["high_value"]["0.01"]["count"]
                        metrics[policy + "_recall"] = outcome["high_value"]["0.01"]["recall"]
                        metrics[policy + "_best"] = outcome["best_found_improvement"]
                    if metrics["greedy_hits"] != metrics["top16_high"]:
                        raise ValueError("top16 and branch mismatch")
                    if arm == "random":
                        expected = keyed[(assay, model, seed)]["arms"]["new32"]
                        if any(metrics[k] != v for k, v in expected.items()):
                            raise ValueError("previous random32 exact reconstruction mismatch")
                    arms[arm] = metrics
            train_coverage = {}
            for arm in ("random", "coverage"):
                ti = [evaluator.lookup[c] for c in training[arm + "_training_ids"]]
                train_coverage[arm + "_training_hits"] = int(membership[0.01][ti].sum())
                train_coverage[arm + "_training_mean_label"] = float(truth[ti].mean())
            rows.append(
                {
                    "assay_id": assay,
                    "model_id": model,
                    "run_seed": seed,
                    "remaining_count": len(common),
                    "high_value_denominator": int(membership[0.01].sum()),
                    "label_cost": 80,
                    "training_count": 32,
                    **arms,
                    "training_label_description": train_coverage,
                    "prediction_sha256": meta["prediction_sha256"],
                    "selection_sha256": e3.digest(private / f"selection-{index:03d}.json"),
                    "training_sha256": meta["training_sha256"],
                }
            )
        del evaluator, truth, membership
        e3.event(args.output, "assay_scored", assay_id=assay, completed_states=len(rows))
    coverage_summaries = []
    if args.mode == "full":
        for assay in e3.ASSAY_IDS:
            rr = sorted(
                [r for r in coverages if r["assay_id"] == assay], key=lambda r: r["run_seed"]
            )
            coverage_summaries.append(
                {
                    "assay_id": assay,
                    "seed_count": len(rr),
                    "mean_distance_change": e3.interval(
                        [r["coverage_mean_distance"] - r["random_mean_distance"] for r in rr], 11150
                    ),
                    "p90_distance_change": e3.interval(
                        [r["coverage_p90_distance"] - r["random_p90_distance"] for r in rr], 11150
                    ),
                    "mean_training_overlap": float(np.mean([r["training_overlap"] for r in rr])),
                }
            )
    result = {
        "protocol": PROTOCOL,
        "completed_states": len(rows),
        "new_fits": len(rows) * 6,
        "new_measurements": 0,
        "new_branches": len(rows) * 2,
        "baseline_reconstruction": "PASS",
        "fixed_training_count": "PASS",
        "common_pool": "PASS",
        "rows": rows,
        "coverage_rows": coverages,
        "summaries": summarize(rows) if args.mode == "full" else [],
        "coverage_summaries": coverage_summaries,
        "limits": [
            "development",
            "geometric coverage is not functional coverage",
            "training label composition also changes",
            "not measurement efficiency",
            "no application delta",
            "no population CI",
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
    parser.add_argument("--wall-seconds", type=int, default=2300)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        print("coverage self-test PASS")
        return 0
    if any(getattr(args, k) is None for k in ("study", "previous", "output", "plan")):
        parser.error("study, previous, output and plan required")
    if args.wall_seconds <= 0 or args.wall_seconds > 2400:
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
