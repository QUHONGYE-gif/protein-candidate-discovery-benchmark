"""Same observations and fixed frontiers; change only ridge regularization."""

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
import run_m5_e3_hxk4_followup as hx  # noqa: E402

e3, rep, gen, cov = hx.e3, hx.rep, hx.gen, hx.cov
PROTOCOL = "m5-e3-fixed-frontier-ridge-strength-development-v1"
ARMS = {"lambda0p1": 0.1, "lambda1": 1.0, "lambda10": 10.0}
CONTEXTS = ("ridge_pool", "xgb_pool", "remaining")
HX_SOURCE = "945ea08a455c29630ea7bd80262298fe74e2c4ef8f705bb79fec68b1498b095a"
HX_RESULT = "9f507101ccc0453ffb04968e51a48ff6727d6a624b99ff22b8f4a341f913060b"
HX_EXEC = "a09caffe3a72c26e297267c6bf63e5da24beebcd3c1dec9e66a8593f6600ed0a"


def fit_ridge(x, y, strength):
    if strength not in ARMS.values():
        raise ValueError("unlisted strength")
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 2 or y.shape != (len(x),) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("invalid allowed arrays")
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1.0
    z, intercept = (x - center) / scale, float(y.mean())
    alpha = float(len(x)) * strength
    beta = z.T @ np.linalg.solve(z @ z.T + alpha * np.eye(len(x)), y - intercept)
    if not np.isfinite(beta).all():
        raise ValueError("nonfinite ridge")
    return {"center": center, "scale": scale, "beta": beta, "intercept": intercept, "alpha": alpha}


def orders_and_choices(ids, predictions, pools):
    orders = {a: np.lexsort((ids, -v)) for a, v in predictions.items()}
    within = {}
    selected = {}
    for context, indices in pools.items():
        mask = np.zeros(len(ids), dtype=bool)
        mask[indices] = True
        within[context] = {a: order[mask[order]] for a, order in orders.items()}
        selected[context] = {a: indices[:16] for a, indices in within[context].items()}
    return within, selected


def fit_select(task):
    assay, ids, x, feature_sha, output = rep.WORKER
    index, seed, observations, baseline = task
    chosen = hx.initial(ids, seed)
    observed = {o.candidate_id: float(o.label) for o in observations}
    if assay != hx.ASSAY or len(observations) != 64 or set(observed) != set(chosen):
        raise ValueError("allowed observation mismatch")
    ordered = sorted(observed)
    lookup = {c: i for i, c in enumerate(ids)}
    train = np.array([lookup[c] for c in ordered])
    y = np.array([observed[c] for c in ordered])
    observation_sha = e3.identity(list(zip(ordered, y.tolist(), strict=True)))
    if observation_sha != baseline["allowed_observation_sha256"]:
        raise ValueError("old observation changed")
    remaining = np.array([i for i, c in enumerate(ids) if c not in observed])
    candidate_ids = np.asarray(ids)[remaining]
    old_path = Path(baseline["directory"]) / f"prediction-{index:03d}.npz"
    old_model = Path(baseline["directory"]) / f"ridge-{index:03d}.npz"
    if (
        e3.digest(old_path) != baseline["prediction_sha256"]
        or e3.digest(old_model) != baseline["ridge_sha256"]
    ):
        raise ValueError("old artifact identity mismatch")
    with np.load(old_path, allow_pickle=False) as old:
        if not np.array_equal(remaining, old["remaining_indices"]):
            raise ValueError("remaining changed")
        old_predictions = {a: old[a].copy() for a in hx.ARMS}
    old_orders, old_pools, old_choices = hx.crossed(candidate_ids, old_predictions)
    pools = {
        "ridge_pool": old_pools["ridge"],
        "xgb_pool": old_pools["xgb"],
        "remaining": np.arange(len(remaining)),
    }
    predictions = {}
    manipulation = {}
    model_refs = {}
    for a, strength in ARMS.items():
        model = fit_ridge(x[train], y, strength)
        pred = np.empty(len(remaining))
        for start in range(0, len(remaining), 384):
            block = x[remaining[start : start + 384]]
            pred[start : start + len(block)] = gen.ridge_predict(model, block)
        if a == "lambda1":
            if not np.array_equal(pred, old_predictions["ridge"]):
                raise ValueError("reference predictions not exact")
            with np.load(old_model, allow_pickle=False) as saved_model:
                if any(not np.array_equal(saved_model[k], v) for k, v in model.items()):
                    raise ValueError("reference model not exact")
        predictions[a] = pred
        if not np.isfinite(pred).all():
            raise ValueError("nonfinite prediction")
        path = output / f"model-{index:03d}-{a}.npz"
        with path.open("xb") as f:
            np.savez(f, **model)
        model_refs[a] = e3.digest(path)
        z = (x[train] - model["center"]) / model["scale"]
        eigen = np.maximum(np.linalg.eigvalsh(z @ z.T), 0)
        manipulation[a] = {
            "coefficient_l2": float(np.linalg.norm(model["beta"])),
            "training_mse": float(np.mean((gen.ridge_predict(model, x[train]) - y) ** 2)),
            "penalized_effective_df": float(np.sum(eigen / (eigen + model["alpha"]))),
        }
    for metric in ("coefficient_l2", "penalized_effective_df"):
        vals = [manipulation[a][metric] for a in ARMS]
        if any(vals[i] < vals[i + 1] - 1e-10 for i in (0, 1)):
            raise ValueError("shrinkage path not monotone")
    vals = [manipulation[a]["training_mse"] for a in ARMS]
    if any(vals[i] > vals[i + 1] + 1e-10 for i in (0, 1)):
        raise ValueError("training loss path not monotone")
    within, selected = orders_and_choices(candidate_ids, predictions, pools)
    if not np.array_equal(
        selected["ridge_pool"]["lambda1"], old_choices["rr"]
    ) or not np.array_equal(selected["xgb_pool"]["lambda1"], old_choices["xr"]):
        raise ValueError("reference selections not exact")
    diagnostics = {}
    for context, indices in pools.items():
        diagnostics[context] = {
            a: {
                "score_sd": float(v[indices].std()),
                "rank_rho_to_reference": e3.rho(v[indices], predictions["lambda1"][indices]),
                "top16_overlap_reference": int(
                    len(np.intersect1d(selected[context][a], selected[context]["lambda1"]))
                ),
            }
            for a, v in predictions.items()
        }
    path = output / f"prediction-{index:03d}.npz"
    with path.open("xb") as f:
        np.savez(
            f,
            remaining_indices=remaining,
            **predictions,
            **{k: v for k, v in pools.items() if k != "remaining"},
        )
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "run_seed": seed,
            "initial_ids": list(chosen),
            "allowed_observation_sha256": observation_sha,
            "feature_sha256": feature_sha,
            "prediction_sha256": e3.digest(path),
            "model_sha256": model_refs,
            "pool_identities": {
                c: e3.identity(sorted(candidate_ids[v].tolist())) for c, v in pools.items()
            },
            "selected": {
                c: {a: candidate_ids[v].tolist() for a, v in aa.items()}
                for c, aa in selected.items()
            },
            "manipulation": manipulation,
            "diagnostics": diagnostics,
            "reference_reconstruction": "EXACT",
        },
    )
    return index


def interval(values):
    v = np.asarray(values, dtype=float)
    n = len(v)
    slots = np.random.Generator(np.random.PCG64(15001)).integers(0, n, (20000, n))
    return {
        "estimate": float(v.mean()),
        "interval": np.percentile(v[slots].mean(1), [0.3125, 99.6875]).tolist(),
        "n": n,
        "coverage": 0.99375,
        "draws": 20000,
        "seed": 15001,
    }


def summarize(rows):
    rows = sorted(rows, key=lambda r: r["run_seed"])
    if [r["run_seed"] for r in rows] != list(hx.NEW_SEEDS):
        raise ValueError("full state matrix")
    contrasts = []
    for context in CONTEXTS:
        for alternative, reference in (
            ("lambda0p1", "lambda1"),
            ("lambda10", "lambda1"),
            ("lambda10", "lambda0p1"),
        ):
            record = {
                "context": context,
                "alternative": alternative,
                "reference": reference,
                "metrics": {},
                "diagnostics": {},
            }
            for section in ("metrics", "diagnostics"):
                for metric in rows[0][section][context][reference]:
                    pairs = [
                        (
                            r[section][context][reference][metric],
                            r[section][context][alternative][metric],
                        )
                        for r in rows
                    ]
                    defined = sum(a is not None and b is not None for a, b in pairs)
                    value = {"defined_pairs": defined}
                    if defined == len(rows):
                        value.update(
                            reference=float(np.mean([a for a, b in pairs])),
                            alternative=float(np.mean([b for a, b in pairs])),
                            change=interval([b - a for a, b in pairs]),
                        )
                    record[section][metric] = value
            record["high_rank_forward"] = interval(
                [
                    r["metrics"][context][reference]["high_rank_sum"]
                    - r["metrics"][context][alternative]["high_rank_sum"]
                    for r in rows
                ]
            )
            d = np.array(
                [
                    r["metrics"][context][alternative]["hits_16"]
                    - r["metrics"][context][reference]["hits_16"]
                    for r in rows
                ]
            )
            record["states"] = {
                "positive": int((d > 0).sum()),
                "zero": int((d == 0).sum()),
                "negative": int((d < 0).sum()),
                "leave_one_out_range": [
                    float((d.sum() - d).min() / 39),
                    float((d.sum() - d).max() / 39),
                ],
            }
            contrasts.append(record)
    availability = []
    for context in CONTEXTS:
        for arm in ARMS:
            m = [r["metrics"][context][arm] for r in rows]
            availability.append(
                {
                    "context": context,
                    "arm": arm,
                    "pool_no_high": sum(v["pool_high"] == 0 for v in m),
                    "high_available_but_zero_hits": sum(
                        v["pool_high"] > 0 and v["hits_16"] == 0 for v in m
                    ),
                    "any_hit": sum(v["hits_16"] > 0 for v in m),
                    "all_zero": sum(v["hits_16"] == 0 for v in m),
                }
            )
    manipulation = {
        a: {
            m: interval([r["manipulation"][a][m] for r in rows]) for m in rows[0]["manipulation"][a]
        }
        for a in ARMS
    }
    return contrasts, availability, manipulation


def selftest():
    hx.selftest()
    x = np.arange(60.0).reshape(12, 5) % 7
    x[:, 0] = 1.0
    y = np.sin(np.arange(12.0))
    for strength in ARMS.values():
        m = fit_ridge(x, y, strength)
        z = (x - m["center"]) / m["scale"]
        primal = np.linalg.solve(
            z.T @ z + len(x) * strength * np.eye(x.shape[1]), z.T @ (y - y.mean())
        )
        assert np.allclose(m["beta"], primal, rtol=1e-12, atol=1e-12)
    assert all(np.array_equal(fit_ridge(x, y, 1.0)[k], v) for k, v in gen.ridge_fit(x, y).items())
    h = np.array([False, True, False, True])
    ranks = np.flatnonzero(h) + 1
    assert int(np.cumsum(h).sum()) == int((len(h) + 1 - ranks).sum())
    assert int((np.flatnonzero(np.zeros(4, dtype=bool)) + 1).sum()) == 0
    assert interval(np.ones(40))["interval"] == [1.0, 1.0]


def run(args):
    selftest()
    identities = (
        (hx.__file__, HX_SOURCE),
        (gen.__file__, hx.GEN_SOURCE),
        (rep.__file__, gen.REPLICATION_SOURCE_SHA),
        (e3.__file__, rep.HELPER_SHA),
        (cov.__file__, gen.COVERAGE_SHA),
        (e3.rt.__file__, rep.RUNTIME_SHA),
    )
    for path, sha in identities:
        if e3.digest(path) != sha:
            raise ValueError("helper source mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("runtime mismatch")
    previous = args.previous / "hxk4-followup-full-001"
    if e3.digest(previous / "result.json.gz") != HX_RESULT:
        raise ValueError("previous result mismatch")
    prior = json.loads(gzip.decompress((previous / "result.json.gz").read_bytes()))
    refs = {r["run_seed"]: r for r in prior["rows"] if r["cohort"] == "new"}
    source = e3.exact_json(previous / "execution.json", HX_EXEC)
    terminal = json.loads((previous / "terminal.json").read_text())
    if terminal["status"] != "succeeded" or any(
        e3.digest(previous / k) != terminal["artifacts"][k]
        for k in ("execution.json", "result.json.gz")
    ):
        raise ValueError("previous terminal mismatch")
    feature_ref = source["input_identity"]
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    item = next(r for r in manifest["assays"] if r["assay_id"] == hx.ASSAY)
    if (
        item["partition"] != "candidate-pool"
        or item["held_out_partitions"]
        or item["label_direction"] != "higher_is_better"
        or item["label_field"] != "DMS_score"
    ):
        raise ValueError("label or data scope")
    ref = item["artifacts"]["candidate"]
    table = e3.rt.load_candidate_table(
        root / ref["path"],
        assay_id=hx.ASSAY,
        expected_sha256=ref["sha256"],
        expected_size=ref["size_bytes"],
    )
    if table.sha256 != feature_ref["candidate_sha256"]:
        raise ValueError("feature row mismatch")
    ref = item["artifacts"]["membership"]
    e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
    feature_path = args.previous / "full-001/private" / hx.ASSAY / "features.npy"
    seeds = (3000,) if args.mode == "smoke" else hx.NEW_SEEDS
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "helper_sha256": {Path(path).name: sha for path, sha in identities},
            "prior_result_sha256": HX_RESULT,
            "prior_execution_sha256": HX_EXEC,
            "input_sha256": e3.INPUT_SHA,
            "input_identity": feature_ref,
            "versions": versions,
            "mode": args.mode,
            "state_seeds": list(seeds),
            "strengths": ARMS,
            "initial_sample_size": 64,
            "label_cost": 80,
            "batch_size": 16,
            "primary_context": "ridge_pool",
            "contexts": list(CONTEXTS),
            "evidence_stage": "ITERATION",
            "application_delta": None,
        },
    )
    private = args.output / "private"
    private.mkdir()
    evaluator = e3.Evaluator(root, item, table)
    with mp.get_context("spawn").Pool(
        1,
        initializer=rep.initialize,
        initargs=(
            hx.ASSAY,
            table.candidate_ids,
            str(feature_path),
            feature_ref["new_feature_sha256"],
            str(private),
        ),
    ) as pool:
        for index, seed in enumerate(seeds):
            old_meta = previous / "private" / f"selection-{index:03d}.json"
            if e3.digest(old_meta) != refs[seed]["selection_sha256"]:
                raise ValueError("old selection hash mismatch")
            meta = json.loads(old_meta.read_text())
            baseline = {
                k: meta[k]
                for k in ("allowed_observation_sha256", "prediction_sha256", "ridge_sha256")
            }
            baseline["directory"] = str(previous / "private")
            task = (
                index,
                seed,
                evaluator.truth.reveal(hx.initial(table.candidate_ids, seed)),
                baseline,
            )
            if pool.apply(fit_select, (task,)) != index:
                raise ValueError("worker result")
            e3.event(args.output, "predictions_saved", index=index, run_seed=seed)
    e3.event(args.output, "workers_exited")
    truth = evaluator.truth._M5CTruthEvaluator__truth
    memberships = evaluator.truth._M5CTruthEvaluator__memberships
    high, top2 = memberships[0.01], memberships[0.02]
    if int(high.sum()) != 86:
        raise ValueError("high denominator changed")
    rows = []
    for index, seed in enumerate(seeds):
        meta_path = private / f"selection-{index:03d}.json"
        meta = json.loads(meta_path.read_text())
        chosen = hx.initial(table.candidate_ids, seed)
        if (
            meta["initial_ids"] != list(chosen)
            or meta["allowed_observation_sha256"] != refs[seed]["allowed_observation_sha256"]
        ):
            raise ValueError("saved initial state")
        path = private / f"prediction-{index:03d}.npz"
        if e3.digest(path) != meta["prediction_sha256"]:
            raise ValueError("prediction hash")
        for arm in ARMS:
            if e3.digest(private / f"model-{index:03d}-{arm}.npz") != meta["model_sha256"][arm]:
                raise ValueError("model hash")
        remaining = np.array([i for i, c in enumerate(table.candidate_ids) if c not in set(chosen)])
        with np.load(path, allow_pickle=False) as saved:
            if not np.array_equal(remaining, saved["remaining_indices"]):
                raise ValueError("remaining identity")
            ids = np.asarray(table.candidate_ids)[remaining]
            pred = {a: saved[a] for a in ARMS}
            pools = {
                c: saved[c] if c != "remaining" else np.arange(len(remaining)) for c in CONTEXTS
            }
            # Reconstruct both fixed pools from identity-bound prior predictions.
            old_path = previous / "private" / f"prediction-{index:03d}.npz"
            if e3.digest(old_path) != refs[seed]["prediction_sha256"]:
                raise ValueError("prior prediction changed")
            with np.load(old_path, allow_pickle=False) as old:
                _, old_pools, _ = hx.crossed(ids, {a: old[a] for a in hx.ARMS})
                if not np.array_equal(pred["lambda1"], old["ridge"]):
                    raise ValueError("reference reconstruction")
            if not np.array_equal(pools["ridge_pool"], old_pools["ridge"]) or not np.array_equal(
                pools["xgb_pool"], old_pools["xgb"]
            ):
                raise ValueError("fixed pool changed")
            within, selected = orders_and_choices(ids, pred, pools)
            if meta["selected"] != {
                c: {a: ids[v].tolist() for a, v in aa.items()} for c, aa in selected.items()
            }:
                raise ValueError("locked selection mismatch")
            metrics = {}
            for context, indices in pools.items():
                if e3.identity(sorted(ids[indices].tolist())) != meta["pool_identities"][context]:
                    raise ValueError("pool identity mismatch")
                metrics[context] = {}
                hh = high[remaining]
                H = int(hh[indices].sum())
                for arm in ARMS:
                    order = within[context][arm]
                    ev = evaluator.truth.evaluate_batch(
                        chosen, ids[selected[context][arm]].tolist()
                    )
                    hits = int(hh[order[:16]].sum())
                    rank_sum = int((np.flatnonzero(hh[order]) + 1).sum())
                    if hits != ev["high_value"]["0.01"]["count"]:
                        raise ValueError("oracle hit mismatch")
                    m = {
                        "hits_16": hits,
                        "recall_16": hits / 86,
                        "best_improvement": ev["best_found_improvement"],
                        "pool_high": H,
                        "pool_size": len(indices),
                        "oracle_bound": min(H, 16),
                        "opportunity_gap": min(H, 16) - hits,
                        "pool_auc": e3.auc(pred[arm][indices], hh[indices]),
                        "high_rank_sum": rank_sum,
                    }
                    if context == "remaining":
                        m.update(
                            cov.prediction_metrics(
                                pred[arm], truth[remaining], hh, top2[remaining], order
                            )
                        )
                        if arm == "lambda1" and any(
                            m[k] != v for k, v in refs[seed]["metrics"]["ridge"].items() if k in m
                        ):
                            raise ValueError("old global metrics")
                    metrics[context][arm] = m
                target = refs[seed]["cells"]["xr" if context == "xgb_pool" else "rr"]
                if metrics[context]["lambda1"]["hits_16"] != target:
                    raise ValueError("old context hits changed")
            rows.append(
                {
                    "assay_id": hx.ASSAY,
                    "run_seed": seed,
                    "initial_sample_size": 64,
                    "label_cost": 80,
                    "high_denominator": 86,
                    "metrics": metrics,
                    "diagnostics": meta["diagnostics"],
                    "manipulation": meta["manipulation"],
                    "pool_identities": meta["pool_identities"],
                    "allowed_observation_sha256": meta["allowed_observation_sha256"],
                    "prediction_sha256": meta["prediction_sha256"],
                    "selection_sha256": e3.digest(meta_path),
                    "reference_reconstruction": "EXACT",
                }
            )
    e3.event(args.output, "scoring_complete", states=len(rows))
    contrasts, availability, manipulation = summarize(rows) if args.mode == "full" else ([], [], {})
    result = {
        "protocol": PROTOCOL,
        "completed_states": len(rows),
        "point_fits": 3 * len(rows),
        "new_states": 0,
        "rows": rows,
        "contrasts": contrasts,
        "availability": availability,
        "manipulation": manipulation,
        "limitations": [
            "development-only",
            "reference-conditioned fixed pools",
            "correlated diagnostic endpoints",
            "no E/UQ",
            "no noise attribution",
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
        print("ridge-strength algebra/reference/fixed-pool checks PASS")
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
