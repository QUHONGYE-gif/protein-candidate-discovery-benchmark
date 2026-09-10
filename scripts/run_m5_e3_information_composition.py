#!/usr/bin/env python3
"""Select fixed-size training subsets within an already revealed benign reservoir."""

from __future__ import annotations

import argparse
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
import run_m5_e3_fixed_count_coverage as cov  # noqa: E402
import run_m5_e3_prediction as e3  # noqa: E402

PROTOCOL = "m5-e3-information-composition-development-v1"
MODEL = "xgboost-cpu-v1"
ARMS = ("random", "upper", "spread")
CONTRASTS = (("random", "upper"), ("random", "spread"), ("spread", "upper"))
COVERAGE_SHA = "477d9b67b0e2b3985b60e09b356d2585c5d2b1734f06cc9ac1af3e81ec7f131c"


def choose(ids, labels, random_ids):
    """Only 64 already allowed observations are accepted; no other truth input."""
    ids, y = np.asarray(ids), np.asarray(labels, dtype=float)
    if len(ids) != 64 or len(set(ids)) != 64 or y.shape != (64,) or not np.isfinite(y).all():
        raise ValueError("expected exactly64 unique allowed observations")
    if len(random_ids) != 32 or len(set(random_ids)) != 32 or not set(random_ids) < set(ids):
        raise ValueError("invalid original32 subset")
    order = np.lexsort((ids, y))
    ranks = np.rint(np.linspace(0, 63, 32)).astype(int)
    selected = {
        "random": list(random_ids),
        "upper": ids[order[-32:]].tolist(),
        "spread": ids[order[ranks]].tolist(),
    }
    for values in selected.values():
        if len(set(values)) != 32 or not set(values) < set(ids):
            raise ValueError("training selector escaped allowed reservoir")
    return selected


def fit_select(task):
    assay, ids, features, feature_sha, output = cov.WORKER
    index, seed, observations, old_dir, old_identity = task
    reservoir = e3.initial_selection(
        ids, assay_id=assay, run_seed=seed, size=64, assay_ids=e3.ASSAY_IDS
    )
    random_ids = e3.initial_selection(
        ids, assay_id=assay, run_seed=seed, size=32, assay_ids=e3.ASSAY_IDS
    )
    observed = {o.candidate_id: float(o.label) for o in observations}
    if len(observations) != 64 or set(observed) != set(reservoir) or len(observed) != 64:
        raise ValueError("allowed observation capability mismatch")
    y64 = np.array([observed[c] for c in reservoir])
    training = choose(reservoir, y64, random_ids)
    lookup = {c: i for i, c in enumerate(ids)}
    common = np.array([i for i, c in enumerate(ids) if c not in set(reservoir)])
    common_ids = np.asarray(ids)[common]
    estimators, training_y, indices = {}, {}, {}
    for arm, chosen in training.items():
        ordered = sorted(chosen)
        indices[arm] = np.array([lookup[c] for c in ordered])
        training_y[arm] = np.array([observed[c] for c in ordered])
        estimators[arm] = e3.rt._fit_model(MODEL, features[indices[arm]], training_y[arm])
    values = np.empty((3, len(common)))
    for start in range(0, len(common), 384):
        block = features[common[start : start + 384]]
        for j, arm in enumerate(ARMS):
            values[j, start : start + len(block)] = e3.rt._predict(estimators[arm], block)
    old, remaining, old_mean, _ = cov.load_prediction(old_dir, index)
    if (
        old["prediction_sha256"] != old_identity["source_prediction_shas"][1]
        or e3.digest(Path(old_dir) / f"selection-{index:03d}.json")
        != old_identity["source_selection_shas"][1]
        or old["model_id"] != MODEL
        or old["run_seed"] != seed
        or old["initial_ids"] != list(random_ids)
    ):
        raise ValueError("original32 identity mismatch")
    positions = np.searchsorted(remaining, common)
    if (
        (positions >= len(remaining)).any()
        or not np.array_equal(remaining[positions], common)
        or not np.array_equal(old_mean[positions], values[0])
    ):
        raise ValueError("baseline point reconstruction failed")
    if not np.isfinite(values).all():
        raise ValueError("nonfinite predictions")
    orders = {arm: np.lexsort((common_ids, -values[j])) for j, arm in enumerate(ARMS)}
    frontier = np.unique(np.concatenate([orders[a][:64] for a in ARMS]))
    reservoir_indices = np.array([lookup[c] for c in reservoir])
    distances = cov.squared_distances(features[common, :420], features[reservoir_indices, :420])
    label_q75 = float(np.quantile(y64, 0.75))
    info = {}
    for arm in ARMS:
        y = training_y[arm]
        nearest = distances[:, [list(reservoir).index(c) for c in training[arm]]].min(axis=1)
        info[arm] = {
            "label_mean": float(y.mean()),
            "label_std": float(y.std()),
            "label_range": float(np.ptp(y)),
            "label_iqr": float(np.quantile(y, 0.75) - np.quantile(y, 0.25)),
            "unique_labels": len(np.unique(y)),
            "reservoir_upper_quartile_fraction": float((y >= label_q75).mean()),
            "geometry_mean_distance": float(nearest.mean()),
            "geometry_p90_distance": float(np.quantile(nearest, 0.9)),
            "training_spearman": e3.rho(e3.rt._predict(estimators[arm], features[indices[arm]]), y),
        }
    prediction = output / f"prediction-{index:03d}.npz"
    with prediction.open("xb") as f:
        np.savez(
            f,
            remaining_indices=common,
            frontier=frontier,
            **{arm: values[j] for j, arm in enumerate(ARMS)},
            **{arm + "_order": order for arm, order in orders.items()},
        )
    configs = {arm: json.loads(est.get_booster().save_config()) for arm, est in estimators.items()}
    config_path = output / f"model-config-{index:03d}.json"
    e3.write(config_path, configs)
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "assay_id": assay,
            "model_id": MODEL,
            "run_seed": seed,
            "common_initial_ids": list(reservoir),
            "training_ids": training,
            "allowed_observation_sha256": e3.identity(sorted(observed.items())),
            "training_observation_shas": {
                a: e3.identity(sorted((c, observed[c]) for c in training[a])) for a in ARMS
            },
            "feature_sha256": feature_sha,
            "prediction_sha256": e3.digest(prediction),
            "model_config_sha256": e3.digest(config_path),
            "training_info": info,
            "training_overlaps": {
                f"{a}_{b}": len(set(training[a]) & set(training[b])) for a, b in CONTRASTS
            },
            "selected": {a: common_ids[orders[a][:16]].tolist() for a in ARMS},
            "baseline_reconstruction": "EXACT",
            "old_source_identity": old_identity,
        },
    )
    return index


def summarize(rows):
    summaries = []
    for assay in e3.ASSAY_IDS:
        cell = sorted([r for r in rows if r["assay_id"] == assay], key=lambda r: r["run_seed"])
        if [r["run_seed"] for r in cell] != list(e3.RUN_SEEDS):
            raise ValueError("paired seed coverage mismatch")
        for before, after in CONTRASTS:
            sections = {}
            for section in ("metrics", "training_info"):
                effects = {}
                for metric in cell[0][section][before]:
                    pairs = [(r[section][before][metric], r[section][after][metric]) for r in cell]
                    defined = sum(x is not None and y is not None for x, y in pairs)
                    record = {"defined_pairs": defined}
                    if defined == 10:
                        record.update(
                            {
                                "before": float(np.mean([x for x, y in pairs])),
                                "after": float(np.mean([y for x, y in pairs])),
                                "change": e3.interval([y - x for x, y in pairs], 12301),
                            }
                        )
                    effects[metric] = record
                sections[section] = effects
            summaries.append(
                {"assay_id": assay, "before": before, "after": after, "states": 10, **sections}
            )
    return summaries


def selftest():
    ids = [f"id{i:03d}" for i in range(64)]
    y = np.arange(64.0)
    selected = choose(ids, y, ids[::2])
    assert selected["upper"] == ids[32:]
    assert selected["spread"][0] == ids[0] and selected["spread"][-1] == ids[-1]
    assert selected == choose(ids[::-1], y[::-1], ids[::2])
    assert selected == choose(ids, y * 10 + 7, ids[::2])
    tied = choose(ids[::-1], np.zeros(64), ids[::2])
    assert tied["upper"] == ids[32:] and len(set(tied["spread"])) == 32
    try:
        choose(ids, np.ones(63), ids[::2])
    except ValueError:
        pass
    else:
        raise AssertionError("non-capability labels accepted")
    # Existing paired estimator must not treat opposing within-seed cells as independent.
    check = e3.interval(list(range(10)) + list(range(9, -1, -1)), 12301)
    assert check["estimate"] == 4.5 and check["interval"] == [4.5, 4.5]


def run(args):
    selftest()
    for path, expected in (
        (e3.__file__, cov.HELPER_SHA),
        (e3.rt.__file__, cov.RUNTIME_SHA),
        (cov.__file__, COVERAGE_SHA),
        (args.previous / "full-001/result.json.gz", cov.INPUT_RESULT_SHA),
        (args.previous / "information-full-001/result.json.gz", cov.INFORMATION_RESULT_SHA),
    ):
        if e3.digest(path) != expected:
            raise ValueError("source/prior identity mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("runtime mismatch")
    prior = json.loads(
        gzip.decompress((args.previous / "information-full-001/result.json.gz").read_bytes())
    )
    keyed = {(r["assay_id"], r["run_seed"]): r for r in prior["rows"] if r["model_id"] == MODEL}
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
            "helper_sha256": cov.HELPER_SHA,
            "coverage_helper_sha256": COVERAGE_SHA,
            "runtime_sha256": cov.RUNTIME_SHA,
            "input_sha256": e3.INPUT_SHA,
            "prior_result_sha256": cov.INFORMATION_RESULT_SHA,
            "input_result_sha256": cov.INPUT_RESULT_SHA,
            "versions": versions,
            "model_parameters": dict(e3.rt.model_effective_parameters(MODEL)),
            "arms": list(ARMS),
            "training_count": 32,
            "allowed_observation_count": 64,
            "main_label_cost": 80,
            "batch_size": 16,
            "features": 421,
            "application_delta": None,
            "evidence_stage": "ITERATION",
            "truth_boundary": "allowed64 only in worker; save then score",
        },
    )
    seeds = (1729,) if args.mode == "smoke" else e3.RUN_SEEDS
    indices = [i for i, (m, n, s) in enumerate(e3.SPECS) if m == MODEL and n == 32 and s in seeds]
    rows = []
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
        old_dir = args.previous / "full-001/private" / assay
        private = args.output / "private" / assay
        private.mkdir(parents=True)
        evaluator = e3.Evaluator(root, item, table)
        initargs = (
            assay,
            table.candidate_ids,
            str(old_dir / "features.npy"),
            feature_shas[assay],
            str(private),
        )
        with mp.get_context("spawn").Pool(1, initializer=cov.initialize, initargs=initargs) as pool:
            for index in indices:
                _, _, seed = e3.SPECS[index]
                reservoir = e3.initial_selection(
                    table.candidate_ids,
                    assay_id=assay,
                    run_seed=seed,
                    size=64,
                    assay_ids=e3.ASSAY_IDS,
                )
                task = (
                    index,
                    seed,
                    evaluator.truth.reveal(reservoir),
                    str(old_dir),
                    keyed[assay, seed]["source_identity"]["32"],
                )
                if pool.apply(fit_select, (task,)) != index:
                    raise ValueError("worker identity mismatch")
                e3.event(
                    args.output, "predictions_and_selection_saved", assay_id=assay, index=index
                )
        e3.event(args.output, "fit_workers_exited", assay_id=assay)
        truth = evaluator.truth._M5CTruthEvaluator__truth
        memberships = evaluator.truth._M5CTruthEvaluator__memberships
        for index in indices:
            _, _, seed = e3.SPECS[index]
            meta_path = private / f"selection-{index:03d}.json"
            meta = json.loads(meta_path.read_text())
            path = private / f"prediction-{index:03d}.npz"
            if (
                e3.digest(path) != meta["prediction_sha256"]
                or e3.digest(private / f"model-config-{index:03d}.json")
                != meta["model_config_sha256"]
            ):
                raise ValueError("saved artifact identity mismatch")
            metrics = {}
            with np.load(path, allow_pickle=False) as data:
                common, frontier = data["remaining_indices"], data["frontier"]
                expected = [
                    i
                    for i, c in enumerate(table.candidate_ids)
                    if c not in set(meta["common_initial_ids"])
                ]
                if common.tolist() != expected:
                    raise ValueError("common candidate pool mismatch")
                ids = np.asarray(table.candidate_ids)[common]
                orders = {a: np.lexsort((ids, -data[a])) for a in ARMS}
                if not np.array_equal(
                    frontier, np.unique(np.concatenate([orders[a][:64] for a in ARMS]))
                ):
                    raise ValueError("prediction-only frontier mismatch")
                for arm in ARMS:
                    order, score = orders[arm], data[arm]
                    if (
                        not np.array_equal(order, data[arm + "_order"])
                        or ids[order[:16]].tolist() != meta["selected"][arm]
                    ):
                        raise ValueError("locked selection mismatch")
                    out = cov.prediction_metrics(
                        score,
                        truth[common],
                        memberships[0.01][common],
                        memberships[0.02][common],
                        order,
                    )
                    out["common_frontier_auc"] = e3.auc(
                        score[frontier], memberships[0.01][common][frontier]
                    )
                    for k in (8, 16, 32, 64):
                        out[f"hits_{k}"] = int(memberships[0.01][common][order[:k]].sum())
                    evaluated = evaluator.truth.evaluate_batch(
                        meta["common_initial_ids"], meta["selected"][arm]
                    )
                    out["greedy_recall"] = evaluated["high_value"]["0.01"]["recall"]
                    out["greedy_hits"] = evaluated["high_value"]["0.01"]["count"]
                    out["greedy_best"] = evaluated["best_found_improvement"]
                    if not out["greedy_hits"] == out["hits_16"] == out["top16_high"]:
                        raise ValueError("batch and rank counts disagree")
                    if arm == "random":
                        old_metrics = keyed[assay, seed]["arms"]["new32"]
                        if any(out[k] != v for k, v in old_metrics.items() if k in out):
                            raise ValueError("baseline aggregate reconstruction mismatch")
                    metrics[arm] = out
                frontier_high = int(memberships[0.01][common][frontier].sum())
            rows.append(
                {
                    "assay_id": assay,
                    "model_id": MODEL,
                    "run_seed": seed,
                    "training_count": 32,
                    "allowed_observation_count": 64,
                    "label_cost": 80,
                    "remaining_count": len(common),
                    "high_denominator": int(memberships[0.01].sum()),
                    "frontier_count": len(frontier),
                    "frontier_high_count": frontier_high,
                    "training_info": meta["training_info"],
                    "training_overlaps": meta["training_overlaps"],
                    "metrics": metrics,
                    "baseline_reconstruction": meta["baseline_reconstruction"],
                    "prediction_sha256": meta["prediction_sha256"],
                    "selection_sha256": e3.digest(meta_path),
                    "allowed_observation_sha256": meta["allowed_observation_sha256"],
                    "training_observation_shas": meta["training_observation_shas"],
                    "model_config_sha256": meta["model_config_sha256"],
                }
            )
        e3.event(args.output, "assay_scored", assay_id=assay, completed_states=len(rows))
        del evaluator, truth, memberships
    result = {
        "protocol": PROTOCOL,
        "completed_states": len(rows),
        "point_fits": 3 * len(rows),
        "new_measurements": 0,
        "allowed_labels_per_state": 64,
        "trained_labels_per_arm": 32,
        "rows": rows,
        "summaries": summarize(rows) if args.mode == "full" else [],
        "limitations": [
            "development data",
            "label-aware subset selection from paid64",
            "not a32-label acquisition experiment",
            "no newE orUQ",
            "no causal mediation",
            "no complete-case CI",
            "no application delta",
            "jointM5 incomplete",
        ],
    }
    with (args.output / "result.json.gz").open("xb") as f:
        f.write(
            gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0)
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "previous", "output", "plan"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--wall-seconds", type=int, default=90)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        print("information-composition selection and pairing checks PASS")
        return 0
    if any(getattr(args, k) is None for k in ("study", "previous", "output", "plan")):
        parser.error("study, previous, output and plan required")
    if not 0 < args.wall_seconds <= (90 if args.mode == "smoke" else 800):
        parser.error("invalid mode wall cap")
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
    e3.event(args.output, "attempt_started", mode=args.mode, source_sha256=e3.digest(__file__))
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
        e3.event(args.output, "attempt_finished", status=status, failure_class=failure)
        terminal = {
            "status": status,
            "failure_class": failure,
            "mode": args.mode,
            "wall_seconds": time.monotonic() - started,
            **usage,
            "source_sha256": e3.digest(__file__),
            "artifacts": {p.name: e3.digest(p) for p in args.output.iterdir() if p.is_file()},
        }
        e3.write(args.output / "terminal.json", terminal)
        print(json.dumps(terminal), flush=True)
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
