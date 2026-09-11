"""Locked-position support diagnosis, then same-size training-subset intervention."""

from __future__ import annotations

import argparse
import gzip
import importlib.metadata
import json
import multiprocessing as mp
import os
import re
import signal
import threading
import time
from pathlib import Path

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"
import numpy as np  # noqa: E402
import run_m5_e3_ridge_strength as rs  # noqa: E402

hx, e3, rep, gen, cov = rs.hx, rs.e3, rs.rep, rs.gen, rs.cov
PROTOCOL = "m5-e3-same-site-support-development-v1"
RS_SHA = "552110cc332e26446ce8728451f3d265177e21c1cd3697059a091889ea2ba0c8"
DESIGN_SOURCE_SHA = "eda5692a80086606f105d5506d76adda50aa2a0fd182ceec88d96ba3e99a3c57"
DESIGN_RESULT_SHA = "adb22c0eadfb813651fc05d3b0cbd17c81891a8118dee363e195e3089483cf5d"
SUBSETS = ("random32", "support32", "all64")
MODELS = ("xgb", "ridge")
POSITIONS = None


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith(".gz") else raw)


def interval(values):
    v = np.asarray(values, dtype=float)
    n = len(v)
    slots = np.random.Generator(np.random.PCG64(16001)).integers(0, n, (20000, n))
    return {
        "estimate": float(v.mean()),
        "interval": np.percentile(v[slots].mean(axis=1), [0.3125, 99.6875]).tolist(),
        "n": n,
        "draws": 20000,
        "seed": 16001,
        "coverage": 0.99375,
    }


def parse_positions(mutants):
    positions = []
    for mutant in mutants:
        m = re.fullmatch(r"([A-Z])(\d+)([A-Z])", mutant)
        if m is None or m[1] == m[3]:
            raise ValueError("requires genuine single substitutions")
        positions.append(int(m[2]))
    return np.asarray(positions, dtype=int)


def support_subset(train_positions, target_positions, size=32):
    """Only observable positions; ties use coverage then original hash order."""
    weights = np.bincount(
        target_positions, minlength=max(train_positions.max(), target_positions.max()) + 1
    )
    selected, used, counts = [], set(), {}
    for _ in range(size):
        best = max(
            (i for i in range(len(train_positions)) if i not in used),
            key=lambda i: (
                int(weights[train_positions[i]])
                if counts.get(int(train_positions[i]), 0) < 2
                else 0,
                counts.get(int(train_positions[i]), 0) == 0,
                -i,
            ),
        )
        # When no target benefit remains, preserve the original hash order.
        if weights[train_positions[best]] == 0 or counts.get(int(train_positions[best]), 0) >= 2:
            best = next(i for i in range(len(train_positions)) if i not in used)
        selected.append(best)
        used.add(best)
        site = int(train_positions[best])
        counts[site] = counts.get(site, 0) + 1
    return np.asarray(selected)


def support_arrays(positions, train_positions, labels):
    count = np.zeros(int(max(positions.max(), train_positions.max())) + 1, dtype=int)
    low, high = np.full(len(count), np.inf), np.full(len(count), -np.inf)
    for site, label in zip(train_positions, labels, strict=True):
        count[site] += 1
        low[site] = min(low[site], label)
        high[site] = max(high[site], label)
    ranges = np.zeros(len(count))
    paired = count >= 2
    ranges[paired] = high[paired] - low[paired]
    return count[positions], ranges[positions]


def support_stats(count, ranges, target):
    out = {}
    for prefix, indices in (("remaining", np.arange(len(count))), ("reference64", target)):
        c, r = count[indices], ranges[indices]
        out.update(
            {
                prefix + "_supported_fraction": float((c > 0).mean()),
                prefix + "_paired_fraction": float((c >= 2).mean()),
                prefix + "_support_score": int(np.minimum(c, 2).sum()),
                prefix + "_support_count_sum": int(c.sum()),
                prefix + "_range_sum": float(r.sum()),
                prefix + "_range_defined": int((c >= 2).sum()),
            }
        )
    return out


def initialize(assay, ids, feature_path, feature_sha, output, positions):
    global POSITIONS
    rep.initialize(assay, ids, feature_path, feature_sha, output)
    POSITIONS = np.asarray(positions)
    if POSITIONS.shape != (len(ids),):
        raise ValueError("position row identity")


def allowed(observations, ids, seed, reference):
    chosen = hx.initial(ids, seed)
    observed = {o.candidate_id: float(o.label) for o in observations}
    if len(observations) != 64 or set(observed) != set(chosen):
        raise ValueError("allowed observation mismatch")
    sha = e3.identity(sorted(observed.items()))
    if sha != reference["allowed_observation_sha256"]:
        raise ValueError("allowed observation identity")
    lookup = {c: i for i, c in enumerate(ids)}
    initial = np.asarray([lookup[c] for c in chosen])
    remaining = np.asarray([i for i, c in enumerate(ids) if c not in observed])
    return chosen, observed, initial, remaining, sha


def prepare(task):
    assay, ids, x, feature_sha, output = rep.WORKER
    index, seed, observations, prior_path, reference = task
    chosen, observed, initial, remaining, sha = allowed(observations, ids, seed, reference)
    old_path = Path(prior_path) / f"prediction-{index:03d}.npz"
    if e3.digest(old_path) != reference["prediction_sha256"]:
        raise ValueError("prior predictions identity")
    with np.load(old_path, allow_pickle=False) as old:
        if not np.array_equal(old["remaining_indices"], remaining):
            raise ValueError("prior remaining identity")
        predictions = {m: old[m].copy() for m in MODELS}
    ridges = np.lexsort((np.asarray(ids)[remaining], -predictions["ridge"]))[:64]
    selected = support_subset(POSITIONS[initial], POSITIONS[remaining[ridges]])
    subsets = {"random32": initial[:32], "support32": initial[selected], "all64": initial}
    diagnostics, arrays = {}, {}
    for name, train in subsets.items():
        labels = np.array([observed[ids[i]] for i in train])
        counts, ranges = support_arrays(POSITIONS[remaining], POSITIONS[train], labels)
        arrays[name + "_counts"] = counts
        arrays[name + "_ranges"] = ranges
        diagnostics[name] = support_stats(counts, ranges, ridges)
        diagnostics[name].update(
            train_count=len(train),
            train_sites=len(np.unique(POSITIONS[train])),
            train_label_mean=float(labels.mean()),
            train_label_range=float(np.ptp(labels)),
            train_label_sd=float(labels.std()),
        )
    if (
        diagnostics["support32"]["reference64_support_score"]
        < diagnostics["random32"]["reference64_support_score"]
    ):
        raise ValueError("support objective regressed")
    path = output / f"support-{index:03d}.npz"
    with path.open("xb") as f:
        np.savez(f, remaining_indices=remaining, reference64=ridges, **predictions, **arrays)
    e3.write(
        output / f"design-{index:03d}.json",
        {
            "run_seed": seed,
            "allowed_observation_sha256": sha,
            "training_ids": {k: np.asarray(ids)[v].tolist() for k, v in subsets.items()},
            "support_sha256": e3.digest(path),
            "prior_prediction_sha256": reference["prediction_sha256"],
            "remaining_identity": e3.identity(np.asarray(ids)[remaining].tolist()),
            "reference64_identity": e3.identity(
                sorted(np.asarray(ids)[remaining[ridges]].tolist())
            ),
            "manipulation": diagnostics,
            "subset_overlap": len(set(subsets["random32"]) & set(subsets["support32"])),
        },
    )
    return index


def fit(task):
    assay, ids, x, feature_sha, output = rep.WORKER
    index, seed, observations, design_path, design_sha, reference = task
    chosen, observed, initial, remaining, sha = allowed(observations, ids, seed, reference)
    meta_path = Path(design_path) / f"design-{index:03d}.json"
    if e3.digest(meta_path) != design_sha:
        raise ValueError("locked design changed")
    meta = read(meta_path)
    old_path = Path(design_path) / f"support-{index:03d}.npz"
    if e3.digest(old_path) != meta["support_sha256"] or meta["allowed_observation_sha256"] != sha:
        raise ValueError("locked support changed")
    with np.load(old_path, allow_pickle=False) as old:
        if not np.array_equal(old["remaining_indices"], remaining):
            raise ValueError("locked remaining changed")
        reference64 = old["reference64"].copy()
        old_predictions = {m: old[m].copy() for m in MODELS}
    lookup = {c: i for i, c in enumerate(ids)}
    predictions, model_hashes, training = {}, {}, {}
    for subset in SUBSETS:
        selected = sorted(meta["training_ids"][subset])
        expected = 64 if subset == "all64" else 32
        if len(set(selected)) != expected or not set(selected) <= set(chosen):
            raise ValueError("training subset identity")
        ti = np.array([lookup[c] for c in selected])
        labels = np.array([observed[c] for c in selected])
        for model in MODELS:
            key = model + "_" + subset
            estimator = (
                gen.ridge_fit(x[ti], labels)
                if model == "ridge"
                else e3.rt._fit_model(rep.MODEL, x[ti], labels)
            )
            predict = (
                (lambda block, estimator=estimator: gen.ridge_predict(estimator, block))
                if model == "ridge"
                else (lambda block, estimator=estimator: e3.rt._predict(estimator, block))
            )
            prediction = np.empty(len(remaining))
            for start in range(0, len(remaining), 384):
                block = x[remaining[start : start + 384]]
                prediction[start : start + len(block)] = predict(block)
            if not np.isfinite(prediction).all():
                raise ValueError("nonfinite prediction")
            if subset == "all64" and not np.array_equal(prediction, old_predictions[model]):
                raise ValueError("all64 predictions not exact")
            if model == "ridge":
                model_path = output / f"model-{index:03d}-{key}.npz"
                with model_path.open("xb") as f:
                    np.savez(f, **estimator)
            else:
                model_path = output / f"config-{index:03d}-{key}.json"
                e3.write(model_path, json.loads(estimator.get_booster().save_config()))
            model_hashes[model_path.name] = e3.digest(model_path)
            predictions[key] = prediction
            train_prediction = predict(x[ti])
            training[key] = {
                "rho": e3.rho(train_prediction, labels),
                "mse": float(np.mean((train_prediction - labels) ** 2)),
            }
    candidate_ids = np.asarray(ids)[remaining]
    order = {k: np.lexsort((candidate_ids, -v)) for k, v in predictions.items()}
    mask = np.zeros(len(remaining), dtype=bool)
    mask[reference64] = True
    path = output / f"prediction-{index:03d}.npz"
    with path.open("xb") as f:
        np.savez(f, remaining_indices=remaining, reference64=reference64, **predictions)
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "run_seed": seed,
            "allowed_observation_sha256": sha,
            "design_sha256": design_sha,
            "prediction_sha256": e3.digest(path),
            "model_sha256": model_hashes,
            "selected64": {k: candidate_ids[v[:64]].tolist() for k, v in order.items()},
            "fixed_selected16": {
                k: candidate_ids[v[mask[v]][:16]].tolist() for k, v in order.items()
            },
            "training": training,
            "reference_reconstruction": "EXACT",
        },
    )
    return index


def diagnose_groups(pred, counts, ranges, high, ids):
    order = np.lexsort((ids, -pred))
    result = {}
    for band, rr in (("1_16", order[:16]), ("17_64", order[16:64]), ("beyond64", order[64:])):
        for cls, ii in (("high", rr[high[rr]]), ("nonhigh", rr[~high[rr]])):
            c, rg = counts[ii], ranges[ii]
            result[band + "/" + cls] = {
                "n": len(ii),
                "supported": int((c > 0).sum()),
                "paired": int((c >= 2).sum()),
                "support_count_sum": int(c.sum()),
                "range_sum": float(rg.sum()),
                "nonzero_range": int(((c >= 2) & (rg > 0)).sum()),
            }
    return result


def paired_fields(rows, getter, reference, alternative):
    result = {}
    for key in getter(rows[0], reference):
        pairs = [(getter(r, reference)[key], getter(r, alternative)[key]) for r in rows]
        defined = sum(a is not None and b is not None for a, b in pairs)
        item = {"defined_pairs": defined}
        if defined == len(rows):
            item.update(
                reference=float(np.mean([a for a, b in pairs])),
                alternative=float(np.mean([b for a, b in pairs])),
                change=interval([b - a for a, b in pairs]),
            )
        result[key] = item
    return result


def summarize(rows):
    if [r["run_seed"] for r in rows] != list(hx.NEW_SEEDS):
        raise ValueError("full matrix mismatch")
    comparisons = []
    for model in MODELS:
        for alternative, reference in (
            ("support32", "random32"),
            ("all64", "random32"),
            ("all64", "support32"),
        ):
            metrics = paired_fields(
                rows, lambda r, a, model=model: r["metrics"][model][a], reference, alternative
            )
            x, y = [
                np.array([r["metrics"][model][a]["hits_16"] for r in rows])
                for a in (reference, alternative)
            ]
            delta = y - x
            comparisons.append(
                {
                    "model": model,
                    "alternative": alternative,
                    "reference": reference,
                    "metrics": metrics,
                    "states": {
                        "positive": int((delta > 0).sum()),
                        "negative": int((delta < 0).sum()),
                        "zero": int((delta == 0).sum()),
                        "both_zero": int(((x == 0) & (y == 0)).sum()),
                        "reference_zero": int((x == 0).sum()),
                        "alternative_zero": int((y == 0).sum()),
                        "leave_one_out_range": [
                            float((delta.sum() - delta).min() / 39),
                            float((delta.sum() - delta).max() / 39),
                        ],
                    },
                }
            )
    manipulation = paired_fields(rows, lambda r, a: r["manipulation"][a], "random32", "support32")
    interaction = interval(
        [
            (
                r["metrics"]["ridge"]["support32"]["hits_16"]
                - r["metrics"]["ridge"]["random32"]["hits_16"]
            )
            - (
                r["metrics"]["xgb"]["support32"]["hits_16"]
                - r["metrics"]["xgb"]["random32"]["hits_16"]
            )
            for r in rows
        ]
    )
    return {
        "comparisons": comparisons,
        "manipulation": manipulation,
        "model_interaction": interaction,
    }


def selftest():
    rs.selftest()
    import itertools

    positions = np.array([1, 2, 1, 3, 4, 2])
    target = np.array([1, 1, 2, 3])
    chosen = support_subset(positions, target, 3)

    def score(ii):
        return sum(min(int((positions[list(ii)] == s).sum()), 2) for s in target)

    assert score(chosen) == max(score(v) for v in itertools.combinations(range(6), 3))
    c, r = support_arrays(np.array([1, 2, 3]), np.array([1, 1, 2]), np.array([0.0, 2.0, 9.0]))
    assert c.tolist() == [2, 1, 0] and r.tolist() == [2.0, 0.0, 0.0]
    assert support_subset(np.array([1, 1, 2, 3]), np.array([9]), 2).tolist() == [0, 1]
    assert interval(np.ones(40))["interval"] == [1.0, 1.0]


def run(args):
    selftest()
    identities = [
        (rs.__file__, RS_SHA),
        (hx.__file__, rs.HX_SOURCE),
        (gen.__file__, hx.GEN_SOURCE),
        (rep.__file__, gen.REPLICATION_SOURCE_SHA),
        (cov.__file__, gen.COVERAGE_SHA),
        (e3.__file__, rep.HELPER_SHA),
        (e3.rt.__file__, rep.RUNTIME_SHA),
    ]
    if any(e3.digest(p) != sha for p, sha in identities):
        raise ValueError("helper source identity")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("environment identity")
    prior = args.previous / "hxk4-followup-full-001"
    if (
        e3.digest(prior / "result.json.gz") != rs.HX_RESULT
        or e3.digest(prior / "execution.json") != rs.HX_EXEC
    ):
        raise ValueError("prior result identity")
    refs = {
        r["run_seed"]: r for r in read(prior / "result.json.gz")["rows"] if r["cohort"] == "new"
    }
    source = read(prior / "execution.json")
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    item = next(r for r in manifest["assays"] if r["assay_id"] == hx.ASSAY)
    if (
        item["held_out_partitions"]
        or item["partition"] != "candidate-pool"
        or item["label_field"] != "DMS_score"
        or item["label_direction"] != "higher_is_better"
    ):
        raise ValueError("label/data boundary")
    ref = item["artifacts"]["candidate"]
    table = e3.rt.load_candidate_table(
        root / ref["path"],
        assay_id=hx.ASSAY,
        expected_sha256=ref["sha256"],
        expected_size=ref["size_bytes"],
    )
    identity = source["input_identity"]
    if table.sha256 != identity["candidate_sha256"]:
        raise ValueError("candidate feature identity")
    positions = parse_positions(table.mutants)
    if len(positions) != 8570 or len(set(positions)) != 464:
        raise ValueError("position scope changed")
    ref = item["artifacts"]["membership"]
    e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
    feature_path = args.previous / "full-001/private" / hx.ASSAY / "features.npy"
    seeds = (3000,) if args.mode == "smoke" else hx.NEW_SEEDS
    diagnostic = args.previous / "site-support-diagnostic-001"
    design_refs = None
    if args.mode != "diagnostic":
        terminal = read(diagnostic / "terminal.json")
        if terminal["status"] != "succeeded" or any(
            e3.digest(diagnostic / k) != terminal["artifacts"][k]
            for k in ("execution.json", "result.json.gz")
        ):
            raise ValueError("diagnostic terminal mismatch")
        de = read(diagnostic / "execution.json")
        if (
            de["source_sha256"] != DESIGN_SOURCE_SHA
            or e3.digest(diagnostic / "result.json.gz") != DESIGN_RESULT_SHA
            or de["plan_sha256"] != e3.digest(args.plan)
        ):
            raise ValueError("diagnostic design source changed")
        design_refs = {r["run_seed"]: r for r in read(diagnostic / "result.json.gz")["rows"]}
        if not any(r["subset_overlap"] < 32 for r in design_refs.values()):
            raise ValueError("no distinct intervention; stop")
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "helper_sha256": {Path(p).name: sha for p, sha in identities},
            "prior_result_sha256": rs.HX_RESULT,
            "input_sha256": e3.INPUT_SHA,
            "input_identity": identity,
            "versions": versions,
            "state_seeds": list(seeds),
            "initial_sample_size": 64,
            "subset_sizes": {"random32": 32, "support32": 32, "all64": 64},
            "label_cost": 80,
            "batch_size": 16,
            "application_delta": None,
            "diagnostic_result_sha256": e3.digest(diagnostic / "result.json.gz")
            if design_refs
            else None,
            "evidence_stage": "ITERATION",
            "new_labels": 0,
            "new_states": 0,
        },
    )
    private = args.output / "private"
    private.mkdir()
    evaluator = e3.Evaluator(root, item, table)
    with mp.get_context("spawn").Pool(
        1,
        initializer=initialize,
        initargs=(
            hx.ASSAY,
            table.candidate_ids,
            str(feature_path),
            identity["new_feature_sha256"],
            str(private),
            positions,
        ),
    ) as pool:
        for index, seed in enumerate(seeds):
            observations = evaluator.truth.reveal(hx.initial(table.candidate_ids, seed))
            if args.mode == "diagnostic":
                task = index, seed, observations, str(prior / "private"), refs[seed]
                returned = pool.apply(prepare, (task,))
            else:
                task = (
                    index,
                    seed,
                    observations,
                    str(diagnostic / "private"),
                    design_refs[seed]["design_sha256"],
                    refs[seed],
                )
                returned = pool.apply(fit, (task,))
            if returned != index:
                raise ValueError("worker index")
            e3.event(
                args.output,
                "design_saved" if args.mode == "diagnostic" else "predictions_saved",
                index=index,
                run_seed=seed,
            )
    e3.event(args.output, "workers_exited")
    truth = evaluator.truth._M5CTruthEvaluator__truth
    memberships = evaluator.truth._M5CTruthEvaluator__memberships
    high, top2 = memberships[0.01], memberships[0.02]
    if int(high.sum()) != 86:
        raise ValueError("high denominator")
    rows = []
    for index, seed in enumerate(seeds):
        chosen = hx.initial(table.candidate_ids, seed)
        remaining = np.array([i for i, c in enumerate(table.candidate_ids) if c not in set(chosen)])
        ids = np.asarray(table.candidate_ids)[remaining]
        if args.mode == "diagnostic":
            path = private / f"design-{index:03d}.json"
            meta = read(path)
            support_path = private / f"support-{index:03d}.npz"
            if e3.digest(support_path) != meta["support_sha256"]:
                raise ValueError("locked support hash")
            with np.load(support_path, allow_pickle=False) as saved:
                if not np.array_equal(saved["remaining_indices"], remaining):
                    raise ValueError("diagnostic remaining")
                groups = {
                    m: diagnose_groups(
                        saved[m], saved["all64_counts"], saved["all64_ranges"], high[remaining], ids
                    )
                    for m in MODELS
                }
                for m in MODELS:
                    if groups[m]["1_16/high"]["n"] != refs[seed]["metrics"][m]["hits_16"]:
                        raise ValueError("diagnostic original hits")
            rows.append(
                {
                    "run_seed": seed,
                    "groups": groups,
                    "manipulation": meta["manipulation"],
                    "subset_overlap": meta["subset_overlap"],
                    "design_sha256": e3.digest(path),
                    "support_sha256": meta["support_sha256"],
                    "allowed_observation_sha256": meta["allowed_observation_sha256"],
                    "remaining_count": len(remaining),
                    "remaining_high": int(high[remaining].sum()),
                }
            )
            continue
        meta_path = private / f"selection-{index:03d}.json"
        meta = read(meta_path)
        path = private / f"prediction-{index:03d}.npz"
        if e3.digest(path) != meta["prediction_sha256"] or any(
            e3.digest(private / p) != sha for p, sha in meta["model_sha256"].items()
        ):
            raise ValueError("fit artifact identity")
        with np.load(path, allow_pickle=False) as saved:
            if not np.array_equal(saved["remaining_indices"], remaining):
                raise ValueError("fit remaining")
            pred = {m + "_" + a: saved[m + "_" + a] for m in MODELS for a in SUBSETS}
            orders = {k: np.lexsort((ids, -v)) for k, v in pred.items()}
            mask = np.zeros(len(remaining), dtype=bool)
            fixed = saved["reference64"]
            mask[fixed] = True
            if meta["selected64"] != {k: ids[v[:64]].tolist() for k, v in orders.items()} or meta[
                "fixed_selected16"
            ] != {k: ids[v[mask[v]][:16]].tolist() for k, v in orders.items()}:
                raise ValueError("selection lock mismatch")
            metrics = {}
            for model in MODELS:
                union = np.unique(np.concatenate([orders[model + "_" + a][:64] for a in SUBSETS]))
                metrics[model] = {}
                for subset in SUBSETS:
                    key = model + "_" + subset
                    order, values = orders[key], pred[key]
                    hh = high[remaining]
                    fixed_order = order[mask[order]]
                    ev = evaluator.truth.evaluate_batch(chosen, ids[order[:16]].tolist())
                    m = cov.prediction_metrics(values, truth[remaining], hh, top2[remaining], order)
                    m.update(
                        hits_16=int(hh[order[:16]].sum()),
                        recall_16=float(hh[order[:16]].sum() / 86),
                        hits_64=int(hh[order[:64]].sum()),
                        best_improvement=ev["best_found_improvement"],
                        fixed_hits_16=int(hh[fixed_order[:16]].sum()),
                        fixed_pool_high=int(hh[fixed].sum()),
                        fixed_high_rank_sum=int((np.flatnonzero(hh[fixed_order]) + 1).sum()),
                        fixed_auc=e3.auc(values[fixed], hh[fixed]),
                        subset_frontier_auc=e3.auc(values[union], hh[union]),
                        subset_frontier_high=int(hh[union].sum()),
                        subset_frontier_size=len(union),
                    )
                    if m["hits_16"] != ev["high_value"]["0.01"]["count"]:
                        raise ValueError("oracle hits")
                    if subset == "all64" and any(
                        m[k] != v for k, v in refs[seed]["metrics"][model].items() if k in m
                    ):
                        raise ValueError("all64 metrics mismatch")
                    metrics[model][subset] = m
        design = design_refs[seed]
        rows.append(
            {
                "run_seed": seed,
                "metrics": metrics,
                "manipulation": design["manipulation"],
                "subset_overlap": design["subset_overlap"],
                "allowed_observation_sha256": meta["allowed_observation_sha256"],
                "prediction_sha256": meta["prediction_sha256"],
                "selection_sha256": e3.digest(meta_path),
                "design_sha256": meta["design_sha256"],
                "reference_reconstruction": meta["reference_reconstruction"],
                "training": meta["training"],
                "label_cost": 80,
                "high_denominator": 86,
            }
        )
    e3.event(args.output, "scoring_complete", states=len(rows))
    summary = summarize(rows) if args.mode == "full" else {}
    if args.mode == "diagnostic":
        summary = {
            "group_totals": {
                m: {
                    g: {k: sum(r["groups"][m][g][k] for r in rows) for k in rows[0]["groups"][m][g]}
                    for g in rows[0]["groups"][m]
                }
                for m in MODELS
            },
            "manipulation": paired_fields(
                rows, lambda r, a: r["manipulation"][a], "random32", "support32"
            ),
            "changed_subsets": sum(r["subset_overlap"] < 32 for r in rows),
        }
    result = {
        "protocol": PROTOCOL,
        "mode": args.mode,
        "completed_states": len(rows),
        "point_fits": 0 if args.mode == "diagnostic" else len(rows) * 6,
        "rows": rows,
        **summary,
        "limitations": [
            "seen development landscape",
            "reference frontier uses allowed64 labels indirectly",
            "site support is a proxy, not sufficient functional information",
            "training subset identities/label distributions change together",
            "no independent biology/noise/mediation claim",
        ],
    }
    with (args.output / "result.json.gz").open("xb") as f:
        f.write(
            gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0)
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "previous", "output", "plan"):
        p.add_argument("--" + name, type=Path)
    p.add_argument("--mode", choices=("diagnostic", "smoke", "full"), default="diagnostic")
    p.add_argument("--wall-seconds", type=int, default=90)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        selftest()
        print("site-support selector/allowed support checks PASS")
        return 0
    if any(
        getattr(args, k) is None for k in ("study", "previous", "output", "plan")
    ) or not 0 < args.wall_seconds <= (700 if args.mode == "full" else 90):
        p.error("required paths or invalid wall cap")
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
                "message": str(exc),
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
