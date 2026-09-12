"""Fixed-observation ridge versus historical XGB on benign development states."""

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
import run_m5_e3_replication as rep  # noqa: E402

PROTOCOL = "m5-e3-learning-generalization-development-v1"
REPLICATION_SOURCE_SHA = "4d2a595e00a349851e833b366dca19b2d34572715a8765f4789fd5a4b16c3382"
REPLICATION_RESULT_SHA = "4f8063f48e5671f9d3008751a05fde7072f26feafe932a7722f6c766209481e8"
COVERAGE_SHA = "477d9b67b0e2b3985b60e09b356d2585c5d2b1734f06cc9ac1af3e81ec7f131c"
ARMS = ("xgb", "ridge")


def ridge_fit(x, y):
    """Training-only standardization; mean MSE + ||beta||^2, free intercept."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 2 or y.shape != (len(x),) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("invalid allowed training arrays")
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1.0
    z, intercept = (x - center) / scale, float(y.mean())
    alpha = float(len(x))
    beta = z.T @ np.linalg.solve(z @ z.T + alpha * np.eye(len(x)), y - intercept)
    if not np.isfinite(beta).all():
        raise ValueError("nonfinite ridge solution")
    return {"center": center, "scale": scale, "beta": beta, "intercept": intercept, "alpha": alpha}


def ridge_predict(model, x):
    return ((np.asarray(x, dtype=float) - model["center"]) / model["scale"]) @ model[
        "beta"
    ] + model["intercept"]


def fit_select(task):
    assay, ids, x, feature_sha, output = rep.WORKER
    index, size, seed, observations, baseline_dir, baseline_ref = task
    chosen = rep.initial(ids, assay, seed, size)
    observed = {o.candidate_id: float(o.label) for o in observations}
    if len(observations) != size or len(observed) != size or set(observed) != set(chosen):
        raise ValueError("allowed observation identity mismatch")
    ordered = sorted(observed)
    lookup = {cid: i for i, cid in enumerate(ids)}
    train = np.array([lookup[c] for c in ordered])
    y = np.array([observed[c] for c in ordered])
    observation_sha = e3.identity(list(zip(ordered, y.tolist(), strict=True)))
    if observation_sha != baseline_ref["allowed_observation_sha256"]:
        raise ValueError("historical observation mismatch")
    remaining = np.array([i for i, cid in enumerate(ids) if cid not in observed])
    candidate_ids = np.asarray(ids)[remaining]
    model = ridge_fit(x[train], y)
    estimator = e3.rt._fit_model(rep.MODEL, x[train], y)
    predictions = {a: np.empty(len(remaining)) for a in ARMS}
    for start in range(0, len(remaining), 384):
        block = x[remaining[start : start + 384]]
        predictions["xgb"][start : start + len(block)] = e3.rt._predict(estimator, block)
        predictions["ridge"][start : start + len(block)] = ridge_predict(model, block)
    baseline_path = Path(baseline_dir) / f"prediction-{index:03d}.npz"
    if e3.digest(baseline_path) != baseline_ref["prediction_sha256"]:
        raise ValueError("historical prediction identity mismatch")
    with np.load(baseline_path, allow_pickle=False) as old:
        if not np.array_equal(remaining, old["remaining_indices"]) or not np.array_equal(
            predictions["xgb"], old["new"]
        ):
            raise ValueError("XGB point reconstruction failed")
    for a in ARMS:
        if not np.isfinite(predictions[a]).all():
            raise ValueError("nonfinite prediction")
    orders = {a: np.lexsort((candidate_ids, -predictions[a])) for a in ARMS}
    frontier = np.unique(np.concatenate([orders[a][:64] for a in ARMS]))
    prediction_path = output / f"prediction-{index:03d}.npz"
    with prediction_path.open("xb") as f:
        np.savez(f, remaining_indices=remaining, frontier=frontier, **predictions)
    ridge_path = output / f"ridge-{index:03d}.npz"
    with ridge_path.open("xb") as f:
        np.savez(f, **model)
    config_path = output / f"xgb-config-{index:03d}.json"
    e3.write(config_path, json.loads(estimator.get_booster().save_config()))
    train_scores = {
        "xgb": e3.rt._predict(estimator, x[train]),
        "ridge": ridge_predict(model, x[train]),
    }
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "assay_id": assay,
            "initial_sample_size": size,
            "run_seed": seed,
            "initial_ids": list(chosen),
            "allowed_observation_sha256": observation_sha,
            "feature_sha256": feature_sha,
            "prediction_sha256": e3.digest(prediction_path),
            "ridge_sha256": e3.digest(ridge_path),
            "xgb_config_sha256": e3.digest(config_path),
            "baseline_prediction_sha256": baseline_ref["prediction_sha256"],
            "baseline_reconstruction": "EXACT",
            "selected": {a: candidate_ids[orders[a][:64]].tolist() for a in ARMS},
            "training": {
                a: {"spearman": e3.rho(v, y), "mae": float(np.abs(v - y).mean())}
                for a, v in train_scores.items()
            },
        },
    )
    return index


def paired(values):
    a = np.asarray(values, dtype=float).reshape(-1, 20).mean(axis=0)
    slots = np.random.Generator(np.random.PCG64(12901)).integers(0, 20, (20000, 20))
    return {
        "estimate": float(a.mean()),
        "interval": np.percentile(a[slots].mean(axis=1), [0.3125, 99.6875]).tolist(),
        "draws": 20000,
        "seed": 12901,
        "coverage": 0.99375,
    }


def summarize(rows):
    summaries = []
    for assay in e3.ASSAY_IDS:
        for size in (32, 64, "combined"):
            cell = sorted(
                [
                    r
                    for r in rows
                    if r["assay_id"] == assay
                    and (size == "combined" or r["initial_sample_size"] == size)
                ],
                key=lambda r: (r["initial_sample_size"], r["run_seed"]),
            )
            expected = [
                (n, s) for n in ((32, 64) if size == "combined" else (size,)) for s in rep.SEEDS
            ]
            if [(r["initial_sample_size"], r["run_seed"]) for r in cell] != expected:
                raise ValueError("paired matrix mismatch")
            sections = {}
            for section in ("metrics", "training"):
                values = {}
                for metric in cell[0][section]["xgb"]:
                    pairs = [(r[section]["xgb"][metric], r[section]["ridge"][metric]) for r in cell]
                    defined = sum(a is not None and b is not None for a, b in pairs)
                    record = {"defined_pairs": defined}
                    if defined == len(cell):
                        record.update(
                            before=float(np.mean([a for a, b in pairs])),
                            after=float(np.mean([b for a, b in pairs])),
                            change=paired([b - a for a, b in pairs]),
                        )
                    values[metric] = record
                sections[section] = values
            summaries.append(
                {
                    "assay_id": assay,
                    "initial_sample_size": size,
                    "states": len(cell),
                    "seed_slots": 20,
                    **sections,
                }
            )
    return summaries


def selftest():
    x = np.arange(60.0).reshape(12, 5) % 7
    x[:, 0] = 1.0
    y = np.sin(np.arange(12.0))
    m = ridge_fit(x, y)
    z = (x - m["center"]) / m["scale"]
    primal = np.linalg.solve(z.T @ z + len(x) * np.eye(x.shape[1]), z.T @ (y - y.mean()))
    assert np.allclose(m["beta"], primal, atol=1e-12, rtol=1e-12)
    assert m["scale"][0] == 1 and abs(m["beta"][0]) < 1e-12
    assert np.allclose(ridge_predict(ridge_fit(x, y * 3 + 7), x), ridge_predict(m, x) * 3 + 7)
    assert np.allclose(ridge_predict(ridge_fit(x[::-1], y[::-1]), x), ridge_predict(m, x))
    assert paired(list(range(20)) + list(range(19, -1, -1)))["interval"] == [9.5, 9.5]
    rep.selftest()


def run(args):
    selftest()
    for path, sha in (
        (e3.__file__, rep.HELPER_SHA),
        (e3.rt.__file__, rep.RUNTIME_SHA),
        (rep.__file__, REPLICATION_SOURCE_SHA),
        (cov.__file__, COVERAGE_SHA),
        (args.previous / "replication-full-001/result.json.gz", REPLICATION_RESULT_SHA),
    ):
        if e3.digest(path) != sha:
            raise ValueError("helper or baseline identity mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("runtime identity mismatch")
    prior = json.loads(
        gzip.decompress((args.previous / "replication-full-001/result.json.gz").read_bytes())
    )
    refs = {(r["assay_id"], r["initial_sample_size"], r["run_seed"]): r for r in prior["rows"]}
    source = e3.exact_json(
        args.previous / "replication-full-001/execution.json",
        "53ff04921f03867a3766ee21abcf4616340d2903618fcbcd188f0c48669edba1",
    )
    # Bind this metadata to the successful historical terminal before using its feature references.
    terminal = json.loads((args.previous / "replication-full-001/terminal.json").read_text())
    if (
        terminal["status"] != "succeeded"
        or e3.digest(args.previous / "replication-full-001/execution.json")
        != terminal["artifacts"]["execution.json"]
    ):
        raise ValueError("historical execution identity mismatch")
    feature_refs = {r["assay_id"]: r for r in source["input_identities"]}
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    if tuple(r["assay_id"] for r in manifest["assays"]) != e3.ASSAY_IDS:
        raise ValueError("assay scope mismatch")
    all_specs = [(n, s) for n in (32, 64) for s in rep.SEEDS]
    specs = [(i, n, s) for i, (n, s) in enumerate(all_specs) if args.mode == "full" or s == 2000]
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "replication_source_sha256": REPLICATION_SOURCE_SHA,
            "replication_result_sha256": REPLICATION_RESULT_SHA,
            "helper_sha256": rep.HELPER_SHA,
            "coverage_helper_sha256": COVERAGE_SHA,
            "runtime_sha256": rep.RUNTIME_SHA,
            "input_sha256": e3.INPUT_SHA,
            "input_identities": source["input_identities"],
            "versions": versions,
            "xgb_parameters": dict(e3.rt.model_effective_parameters(rep.MODEL)),
            "ridge_parameters": {
                "mean_loss_lambda": 1.0,
                "sum_loss_alpha": "n_train",
                "intercept": "unpenalized",
                "standardization": "training means and population std; zero std to1",
                "solver": "numpy dual solve",
            },
            "state_specs": [[n, s] for i, n, s in specs],
            "features": 421,
            "batch_size": 16,
            "evidence_stage": "ITERATION",
            "application_delta": None,
            "truth_boundary": "allowed32/64 only in worker; lock and exit before score",
        },
    )
    rows = []
    for item in manifest["assays"]:
        assay = item["assay_id"]
        if (
            item["partition"] != "candidate-pool"
            or item["held_out_partitions"]
            or item["label_direction"] != "higher_is_better"
            or item["label_field"] != "DMS_score"
        ):
            raise ValueError("sample/label scope mismatch")
        ref = item["artifacts"]["candidate"]
        table = e3.rt.load_candidate_table(
            root / ref["path"],
            assay_id=assay,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        if table.sha256 != feature_refs[assay]["candidate_sha256"]:
            raise ValueError("feature row identity mismatch")
        ref = item["artifacts"]["membership"]
        e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
        feature_path = args.previous / "full-001/private" / assay / "features.npy"
        private = args.output / "private" / assay
        private.mkdir(parents=True)
        evaluator = e3.Evaluator(root, item, table)
        initargs = (
            assay,
            table.candidate_ids,
            str(feature_path),
            feature_refs[assay]["new_feature_sha256"],
            str(private),
        )
        with mp.get_context("spawn").Pool(1, initializer=rep.initialize, initargs=initargs) as pool:
            for index, size, seed in specs:
                chosen = rep.initial(table.candidate_ids, assay, seed, size)
                task = (
                    index,
                    size,
                    seed,
                    evaluator.truth.reveal(chosen),
                    str(args.previous / "replication-full-001/private" / assay),
                    {
                        k: refs[assay, size, seed][k]
                        for k in ("prediction_sha256", "allowed_observation_sha256")
                    },
                )
                if pool.apply(fit_select, (task,)) != index:
                    raise ValueError("worker return mismatch")
                e3.event(args.output, "predictions_saved", assay_id=assay, index=index)
        e3.event(args.output, "workers_exited", assay_id=assay)
        truth = evaluator.truth._M5CTruthEvaluator__truth
        memberships = evaluator.truth._M5CTruthEvaluator__memberships
        high, top2 = memberships[0.01], memberships[0.02]
        for index, size, seed in specs:
            meta_path = private / f"selection-{index:03d}.json"
            meta = json.loads(meta_path.read_text())
            for name, field in (
                (f"prediction-{index:03d}.npz", "prediction_sha256"),
                (f"ridge-{index:03d}.npz", "ridge_sha256"),
                (f"xgb-config-{index:03d}.json", "xgb_config_sha256"),
            ):
                if e3.digest(private / name) != meta[field]:
                    raise ValueError("saved artifact hash mismatch")
            if meta["initial_ids"] != list(rep.initial(table.candidate_ids, assay, seed, size)):
                raise ValueError("initial state mismatch")
            with np.load(private / f"prediction-{index:03d}.npz", allow_pickle=False) as saved:
                remaining, frontier = saved["remaining_indices"], saved["frontier"]
                expected = [
                    i
                    for i, c in enumerate(table.candidate_ids)
                    if c not in set(meta["initial_ids"])
                ]
                if remaining.tolist() != expected:
                    raise ValueError("common remaining pool mismatch")
                ids = np.asarray(table.candidate_ids)[remaining]
                orders = {a: np.lexsort((ids, -saved[a])) for a in ARMS}
                if not np.array_equal(
                    frontier, np.unique(np.concatenate([orders[a][:64] for a in ARMS]))
                ):
                    raise ValueError("prediction-only frontier mismatch")
                metrics = {}
                for a in ARMS:
                    order = orders[a]
                    if ids[order[:64]].tolist() != meta["selected"][a]:
                        raise ValueError("locked selection mismatch")
                    m = cov.prediction_metrics(
                        saved[a], truth[remaining], high[remaining], top2[remaining], order
                    )
                    m["common_frontier_auc"] = e3.auc(saved[a][frontier], high[remaining][frontier])
                    for k in (8, 16, 32, 64):
                        hits = int(high[remaining][order[:k]].sum())
                        m.update(
                            {
                                f"hits_{k}": hits,
                                f"recall_{k}": hits / int(high.sum()),
                                f"precision_{k}": hits / k,
                            }
                        )
                    ev = evaluator.truth.evaluate_batch(
                        meta["initial_ids"], meta["selected"][a][:16]
                    )
                    m["greedy_recall"] = ev["high_value"]["0.01"]["recall"]
                    m["greedy_best"] = ev["best_found_improvement"]
                    if (
                        m["hits_16"] != ev["high_value"]["0.01"]["count"]
                        or m["greedy_recall"] != m["recall_16"]
                    ):
                        raise ValueError("batch/ranking mismatch")
                    if a == "xgb" and any(
                        m[k] != v for k, v in refs[assay, size, seed]["new"].items()
                    ):
                        raise ValueError("historical aggregate reconstruction failed")
                    metrics[a] = m
                frontier_high = int(high[remaining][frontier].sum())
            rows.append(
                {
                    "assay_id": assay,
                    "initial_sample_size": size,
                    "run_seed": seed,
                    "label_cost": size + 16,
                    "remaining_count": len(remaining),
                    "remaining_high": int(high[remaining].sum()),
                    "high_denominator": int(high.sum()),
                    "frontier_count": len(frontier),
                    "frontier_high_count": frontier_high,
                    "metrics": metrics,
                    "training": meta["training"],
                    "baseline_reconstruction": "EXACT",
                    "allowed_observation_sha256": meta["allowed_observation_sha256"],
                    "prediction_sha256": meta["prediction_sha256"],
                    "selection_sha256": e3.digest(meta_path),
                    "ridge_sha256": meta["ridge_sha256"],
                    "baseline_prediction_sha256": meta["baseline_prediction_sha256"],
                }
            )
        e3.event(args.output, "assay_scored", assay_id=assay, states=len(rows))
        del evaluator, truth, memberships, high, top2
    result = {
        "protocol": PROTOCOL,
        "completed_states": len(rows),
        "point_fits": 2 * len(rows),
        "new_measurements": 0,
        "new_seed_states": 0,
        "rows": rows,
        "summaries": summarize(rows) if args.mode == "full" else [],
        "limitations": [
            "development-only",
            "learning package, not isolated regularization",
            "no E/UQ",
            "no complete-case CI",
            "no population equivalence",
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
        print("ridge algebra and shared-state checks PASS")
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
