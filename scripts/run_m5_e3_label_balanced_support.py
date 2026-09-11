"""Two bounded controls for same-site support versus allowed-label composition."""

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
import run_m5_e3_site_support as ss  # noqa: E402

rs, hx, e3, rep, gen, cov = ss.rs, ss.hx, ss.e3, ss.rep, ss.gen, ss.cov
PROTOCOL = "m5-e3-label-balanced-support-development-v1"
SOURCE_SS = "b3904c118bc8d9ca1c4a2b20cbdb7a81b944b5ba1db342c6c7dc60cb13513198"
RESULT_SS = "7c8e69fa5b37675b43e12b78418b8d7fefc2cedd4df1804daee12d0a21558126"
DESIGN_SS = "adb22c0eadfb813651fc05d3b0cbd17c81891a8118dee363e195e3089483cf5d"
MODELS = ("xgb", "ridge")
ARMS = ("pair_low", "pair_high", "composition_matched", "original_support", "original_random")
CONTRASTS = (
    ("pair_low", "pair_high", "primary_A"),
    ("original_support", "composition_matched", "primary_B"),
    ("original_random", "composition_matched", "auxiliary"),
    ("original_random", "original_support", "historical_reconstruction"),
)
read = ss.read


def interval(values):
    v = np.asarray(values, dtype=float)
    slots = np.random.Generator(np.random.PCG64(17001)).integers(0, len(v), (20000, len(v)))
    return {
        "estimate": float(v.mean()),
        "interval": np.percentile(v[slots].mean(axis=1), [0.3125, 99.6875]).tolist(),
        "n": len(v),
        "draws": 20000,
        "seed": 17001,
        "coverage": 0.99375,
    }


def score_support(train_positions, target_positions):
    count = np.bincount(
        train_positions, minlength=int(max(train_positions.max(), target_positions.max())) + 1
    )
    return int(np.minimum(count[target_positions], 2).sum())


def pair_selection(ids, labels, positions, target):
    pairs = np.lexsort((ids, labels)).reshape(-1, 2)
    sd = float(labels.std())
    eligible = np.abs(labels[pairs[:, 1]] - labels[pairs[:, 0]]) <= 0.05 * sd
    default = np.min(pairs, axis=1)  # input ordering is the original label-blind hash order
    choices = {}
    for arm, sign in (("pair_high", 1), ("pair_low", -1)):
        selected = default.copy()
        for _ in range(129):
            current = score_support(positions[selected], target)
            best_gain, best = 0, None
            for j in np.flatnonzero(eligible):
                trial = selected.copy()
                trial[j] = int(pairs[j, 0] if selected[j] == pairs[j, 1] else pairs[j, 1])
                gain = sign * (score_support(positions[trial], target) - current)
                if gain > best_gain:
                    best_gain, best = gain, trial
            if best is None:
                break
            selected = best
        else:
            raise ValueError("bounded integer support search did not converge")
        choices[arm] = selected
    a, b = [np.sort(labels[choices[k]]) for k in ("pair_low", "pair_high")]
    gap = float(np.max(np.abs(b - a)))
    if gap > 0.05 * sd + 1e-12:
        raise ValueError("paired label caliper")
    if score_support(positions[choices["pair_high"]], target) < score_support(
        positions[choices["pair_low"]], target
    ):
        raise ValueError("support contrast reversed")
    return choices, {
        "pair_eligible": int(eligible.sum()),
        "pair_label_max_gap_sd": gap / sd if sd else 0.0,
        "pair_label_mean_absolute_gap_sd": float(np.abs(b - a).mean()) / sd if sd else 0.0,
        "pair_label_mean_signed_gap_sd": float((b - a).mean()) / sd if sd else 0.0,
    }


def match_labels(ids, labels, candidates, mandatory, target):
    """Exact sorted-label squared-distance optimum with mandatory real observations."""
    ordered = np.asarray(sorted(candidates, key=lambda i: (labels[i], ids[i])), dtype=int)
    target = np.sort(target)
    n, k = len(ordered), len(target)
    dp = np.full((n + 1, k + 1), np.inf)
    take = np.zeros((n + 1, k + 1), dtype=bool)
    dp[0, 0] = 0
    for i, candidate in enumerate(ordered, 1):
        if int(candidate) not in mandatory:
            dp[i] = dp[i - 1]
        for j in range(1, min(k, i) + 1):
            cost = dp[i - 1, j - 1] + float((labels[candidate] - target[j - 1]) ** 2)
            if cost < dp[i, j]:
                dp[i, j], take[i, j] = cost, True
    if not np.isfinite(dp[n, k]):
        raise ValueError("no feasible label-matching subset")
    selected, j = [], k
    for i in range(n, 0, -1):
        if take[i, j]:
            selected.append(int(ordered[i - 1]))
            j -= 1
    if j or len(selected) != k or not mandatory <= set(selected):
        raise ValueError("matching backtrace")
    return np.asarray(selected), float(dp[n, k])


def choose(ids, labels, positions, target, original_support, original_random):
    paired, info = pair_selection(ids, labels, positions, target)
    target_sites = set(target.tolist())
    anchors = {int(i) for i in original_support if positions[i] in target_sites}
    candidates = anchors | {i for i, p in enumerate(positions) if p not in target_sites}
    matched, distance = match_labels(ids, labels, candidates, anchors, labels[original_random])
    old_distance = float(
        np.square(np.sort(labels[original_support]) - np.sort(labels[original_random])).sum()
    )
    if distance > old_distance + 1e-10:
        raise ValueError("matching objective got worse than feasible original")
    original_counts = np.array([(positions[original_support] == p).sum() for p in target])
    matched_counts = np.array([(positions[matched] == p).sum() for p in target])
    if not np.array_equal(original_counts, matched_counts):
        raise ValueError("fixed reference support counts differ")
    selected = {
        **paired,
        "composition_matched": matched,
        "original_support": original_support,
        "original_random": original_random,
    }
    if any(
        len(set(ii.tolist())) != 32 or not set(ii.tolist()) <= set(range(64))
        for ii in selected.values()
    ):
        raise ValueError("training identity/count")
    sd = float(labels.std())
    scale = 32 * sd**2 if sd else 1.0
    info.update(
        anchors=len(anchors),
        composition_old_squared_distance_sd=old_distance / scale,
        composition_matched_squared_distance_sd=distance / scale,
        composition_distance_reduction_sd=(old_distance - distance) / scale,
        composition_support_count_max_difference=int(
            np.abs(original_counts - matched_counts).max()
        ),
        pair_changed=int(
            not np.array_equal(np.sort(paired["pair_high"]), np.sort(paired["pair_low"]))
        ),
        composition_changed=int(set(matched) != set(original_support)),
    )
    return selected, info


def prepare(task):
    assay, ids, x, feature_sha, output = rep.WORKER
    index, seed, observations, previous, reference = task
    previous = Path(previous)
    chosen, observed, initial, remaining, sha = ss.allowed(observations, ids, seed, reference)
    old = read(previous / "site-support-diagnostic-001/private" / f"design-{index:03d}.json")
    diagnostic_row = next(
        r
        for r in read(previous / "site-support-diagnostic-001/result.json.gz")["rows"]
        if r["run_seed"] == seed
    )
    if (
        e3.digest(previous / "site-support-diagnostic-001/private" / f"design-{index:03d}.json")
        != diagnostic_row["design_sha256"]
    ):
        raise ValueError("original locked design identity")
    pred_path = previous / "site-support-full-001/private" / f"prediction-{index:03d}.npz"
    if e3.digest(pred_path) != reference["prediction_sha256"]:
        raise ValueError("old predictions identity")
    with np.load(pred_path, allow_pickle=False) as old_prediction:
        if not np.array_equal(old_prediction["remaining_indices"], remaining):
            raise ValueError("remaining identity")
        reference64 = old_prediction["reference64"].copy()
        arrays = {
            m + "_" + a: old_prediction[m + "_" + b].copy()
            for m in MODELS
            for a, b in [("original_support", "support32"), ("original_random", "random32")]
        }
    lookup = {c: i for i, c in enumerate(chosen)}
    original_s, original_r = [
        np.array([lookup[c] for c in old["training_ids"][a]]) for a in ("support32", "random32")
    ]
    labels = np.asarray([observed[c] for c in chosen])
    selected, checks = choose(
        np.asarray(chosen),
        labels,
        ss.POSITIONS[initial],
        ss.POSITIONS[remaining[reference64]],
        original_s,
        original_r,
    )
    manipulation = {}
    for arm, ii in selected.items():
        yy = labels[ii]
        counts, ranges = ss.support_arrays(ss.POSITIONS[remaining], ss.POSITIONS[initial[ii]], yy)
        manipulation[arm] = ss.support_stats(counts, ranges, reference64)
        manipulation[arm].update(
            train_count=32,
            train_sites=len(set(ss.POSITIONS[initial[ii]].tolist())),
            train_label_mean=float(yy.mean()),
            train_label_sd=float(yy.std()),
            train_label_range=float(np.ptp(yy)),
        )
    path = output / f"reference-{index:03d}.npz"
    with path.open("xb") as f:
        np.savez(f, remaining_indices=remaining, reference64=reference64, **arrays)
    e3.write(
        output / f"design-{index:03d}.json",
        {
            "run_seed": seed,
            "allowed_observation_sha256": sha,
            "training_ids": {a: np.asarray(chosen)[ii].tolist() for a, ii in selected.items()},
            "label_balance": checks,
            "manipulation": manipulation,
            "reference_sha256": e3.digest(path),
            "prior_prediction_sha256": reference["prediction_sha256"],
        },
    )
    return index


def fit(task):
    assay, ids, x, feature_sha, output = rep.WORKER
    index, seed, observations, design_dir, design_sha, reference = task
    chosen, observed, initial, remaining, sha = ss.allowed(observations, ids, seed, reference)
    design_dir = Path(design_dir)
    path = design_dir / f"design-{index:03d}.json"
    if e3.digest(path) != design_sha:
        raise ValueError("locked design changed")
    meta = read(path)
    old_path = design_dir / f"reference-{index:03d}.npz"
    if e3.digest(old_path) != meta["reference_sha256"] or sha != meta["allowed_observation_sha256"]:
        raise ValueError("locked reference/state changed")
    with np.load(old_path, allow_pickle=False) as old:
        if not np.array_equal(old["remaining_indices"], remaining):
            raise ValueError("remaining changed")
        fixed = old["reference64"].copy()
        original = {
            m + "_" + a: old[m + "_" + a].copy()
            for m in MODELS
            for a in ("original_support", "original_random")
        }
    lookup = {c: i for i, c in enumerate(ids)}
    predictions, model_hashes = {}, {}
    for arm in ARMS:
        selected = sorted(meta["training_ids"][arm])
        if len(set(selected)) != 32 or not set(selected) <= set(chosen):
            raise ValueError("training subset identity")
        ti, labels = (
            np.array([lookup[c] for c in selected]),
            np.array([observed[c] for c in selected]),
        )
        for model in MODELS:
            key = model + "_" + arm
            if arm == "original_random":
                predictions[key] = original[key]
                continue
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
            values = np.empty(len(remaining))
            for start in range(0, len(remaining), 384):
                block = x[remaining[start : start + 384]]
                values[start : start + len(block)] = predict(block)
            if not np.isfinite(values).all():
                raise ValueError("nonfinite predictions")
            if arm == "original_support" and not np.array_equal(values, original[key]):
                raise ValueError("original support point reconstruction")
            model_path = output / (
                f"model-{index:03d}-{key}.npz"
                if model == "ridge"
                else f"config-{index:03d}-{key}.json"
            )
            if model == "ridge":
                with model_path.open("xb") as f:
                    np.savez(f, **estimator)
            else:
                e3.write(model_path, json.loads(estimator.get_booster().save_config()))
            model_hashes[model_path.name] = e3.digest(model_path)
            predictions[key] = values
    cid = np.asarray(ids)[remaining]
    orders = {k: np.lexsort((cid, -v)) for k, v in predictions.items()}
    mask = np.zeros(len(remaining), dtype=bool)
    mask[fixed] = True
    path = output / f"prediction-{index:03d}.npz"
    with path.open("xb") as f:
        np.savez(f, remaining_indices=remaining, reference64=fixed, **predictions)
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "run_seed": seed,
            "allowed_observation_sha256": sha,
            "design_sha256": design_sha,
            "prediction_sha256": e3.digest(path),
            "model_sha256": model_hashes,
            "selected64": {k: cid[o[:64]].tolist() for k, o in orders.items()},
            "fixed_selected16": {k: cid[o[mask[o]][:16]].tolist() for k, o in orders.items()},
            "reference_reconstruction": "EXACT",
        },
    )
    return index


def paired_fields(rows, getter, reference, alternative):
    result = {}
    for key in getter(rows[0], reference):
        pairs = [(getter(r, reference)[key], getter(r, alternative)[key]) for r in rows]
        item = {"defined_pairs": sum(a is not None and b is not None for a, b in pairs)}
        if item["defined_pairs"] == len(rows):
            item.update(
                reference=float(np.mean([a for a, b in pairs])),
                alternative=float(np.mean([b for a, b in pairs])),
                change=interval([b - a for a, b in pairs]),
            )
        result[key] = item
    return result


def summarize(rows, design=False):
    balance = {
        k: {
            "mean": float(np.mean([r["label_balance"][k] for r in rows])),
            "min": float(min(r["label_balance"][k] for r in rows)),
            "max": float(max(r["label_balance"][k] for r in rows)),
        }
        for k in rows[0]["label_balance"]
    }
    manip = [
        {
            "reference": a,
            "alternative": b,
            "role": role,
            "fields": paired_fields(rows, lambda r, arm: r["manipulation"][arm], a, b),
        }
        for a, b, role in CONTRASTS
    ]
    output = {"label_balance_summary": balance, "manipulation_comparisons": manip}
    if design:
        return output
    comparisons = []
    for model in MODELS:
        for a, b, role in CONTRASTS:
            x, y = [np.array([r["metrics"][model][arm]["hits_16"] for r in rows]) for arm in (a, b)]
            d = y - x
            comparisons.append(
                {
                    "model": model,
                    "reference": a,
                    "alternative": b,
                    "role": role,
                    "metrics": paired_fields(
                        rows, lambda r, arm, model=model: r["metrics"][model][arm], a, b
                    ),
                    "states": {
                        "positive": int((d > 0).sum()),
                        "negative": int((d < 0).sum()),
                        "zero": int((d == 0).sum()),
                        "both_zero": int(((x == 0) & (y == 0)).sum()),
                        "reference_zero": int((x == 0).sum()),
                        "alternative_zero": int((y == 0).sum()),
                        "leave_one_out_range": [
                            float((d.sum() - d).min() / (len(d) - 1)),
                            float((d.sum() - d).max() / (len(d) - 1)),
                        ]
                        if len(d) > 1
                        else None,
                    },
                }
            )
    output["comparisons"] = comparisons
    return output


def selftest():
    import itertools

    ids = np.asarray(list("abcdef"))
    y = np.array([0.0, 0.01, 1.0, 1.01, 2.0, 2.01])
    p = np.array([1, 2, 3, 4, 1, 5])
    target = np.array([1, 1, 3])
    pairs, info = pair_selection(ids, y, p, target)
    assert info["pair_label_max_gap_sd"] <= 0.05
    assert score_support(p[pairs["pair_high"]], target) >= score_support(
        p[pairs["pair_low"]], target
    )
    selected, cost = match_labels(ids, y, set(range(6)), {1}, np.array([0.2, 1.1, 1.8]))
    expected = min(
        sum((np.sort(y[list(ii)]) - np.array([0.2, 1.1, 1.8])) ** 2)
        for ii in itertools.combinations(range(6), 3)
        if 1 in ii
    )
    assert abs(cost - expected) < 1e-12 and 1 in selected
    assert interval([1] * 40)["interval"] == [1.0, 1.0]


def score_locked(evaluator, table, private, seeds, designs, references):
    """Evaluator-only phase, called after every selection is saved and all workers exit."""
    truth = evaluator.truth._M5CTruthEvaluator__truth
    membership = evaluator.truth._M5CTruthEvaluator__memberships
    high, top2 = membership[0.01], membership[0.02]
    if int(high.sum()) != 86:
        raise ValueError("high denominator")
    rows = []
    for index, seed in enumerate(seeds):
        chosen = hx.initial(table.candidate_ids, seed)
        remaining = np.array([i for i, c in enumerate(table.candidate_ids) if c not in set(chosen)])
        ids = np.asarray(table.candidate_ids)[remaining]
        meta_path = private / f"selection-{index:03d}.json"
        meta = read(meta_path)
        path = private / f"prediction-{index:03d}.npz"
        if e3.digest(path) != meta["prediction_sha256"] or any(
            e3.digest(private / k) != v for k, v in meta["model_sha256"].items()
        ):
            raise ValueError("fit artifact identity")
        with np.load(path, allow_pickle=False) as saved:
            if not np.array_equal(saved["remaining_indices"], remaining):
                raise ValueError("score remaining identity")
            predictions = {m + "_" + a: saved[m + "_" + a] for m in MODELS for a in ARMS}
            orders = {k: np.lexsort((ids, -v)) for k, v in predictions.items()}
            fixed = saved["reference64"]
            mask = np.zeros(len(remaining), dtype=bool)
            mask[fixed] = True
            if meta["selected64"] != {k: ids[o[:64]].tolist() for k, o in orders.items()} or meta[
                "fixed_selected16"
            ] != {k: ids[o[mask[o]][:16]].tolist() for k, o in orders.items()}:
                raise ValueError("selection identity")
            hh = high[remaining]
            metrics = {}
            for model in MODELS:
                union = np.unique(np.concatenate([orders[model + "_" + a][:64] for a in ARMS]))
                metrics[model] = {}
                for arm in ARMS:
                    key = model + "_" + arm
                    order, values = orders[key], predictions[key]
                    fo = order[mask[order]]
                    ev = evaluator.truth.evaluate_batch(chosen, ids[order[:16]].tolist())
                    m = cov.prediction_metrics(values, truth[remaining], hh, top2[remaining], order)
                    m.update(
                        hits_16=int(hh[order[:16]].sum()),
                        recall_16=float(hh[order[:16]].sum() / 86),
                        hits_64=int(hh[order[:64]].sum()),
                        best_improvement=ev["best_found_improvement"],
                        fixed_hits_16=int(hh[fo[:16]].sum()),
                        fixed_pool_high=int(hh[fixed].sum()),
                        fixed_high_rank_sum=int((np.flatnonzero(hh[fo]) + 1).sum()),
                        fixed_auc=e3.auc(values[fixed], hh[fixed]),
                        balanced_frontier_auc=e3.auc(values[union], hh[union]),
                        balanced_frontier_high=int(hh[union].sum()),
                        balanced_frontier_size=len(union),
                    )
                    if m["hits_16"] != ev["high_value"]["0.01"]["count"]:
                        raise ValueError("oracle hits")
                    if arm in ("original_support", "original_random"):
                        old_arm = "support32" if arm == "original_support" else "random32"
                        prior = references[seed]["metrics"][model][old_arm]
                        if any(value != m[k] for k, value in prior.items() if k in m):
                            raise ValueError("original metrics mismatch")
                    metrics[model][arm] = m
        design = designs[seed]
        rows.append(
            {
                "run_seed": seed,
                "allowed_observation_sha256": meta["allowed_observation_sha256"],
                "prediction_sha256": meta["prediction_sha256"],
                "selection_sha256": e3.digest(meta_path),
                "design_sha256": meta["design_sha256"],
                "reference_reconstruction": meta["reference_reconstruction"],
                "label_balance": design["label_balance"],
                "manipulation": design["manipulation"],
                "metrics": metrics,
                "label_cost": 80,
                "high_denominator": 86,
            }
        )
    return rows


def run(args):
    selftest()
    identities = [
        (ss.__file__, SOURCE_SS),
        (rs.__file__, ss.RS_SHA),
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
    prior = args.previous / "site-support-full-001"
    if (
        e3.digest(prior / "result.json.gz") != RESULT_SS
        or e3.digest(args.previous / "site-support-diagnostic-001/result.json.gz") != DESIGN_SS
    ):
        raise ValueError("original results identity")
    references = {r["run_seed"]: r for r in read(prior / "result.json.gz")["rows"]}
    original_execution = read(prior / "execution.json")
    if (
        e3.digest(prior / "execution.json")
        != read(prior / "terminal.json")["artifacts"]["execution.json"]
    ):
        raise ValueError("prior execution identity")
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
    identity = original_execution["input_identity"]
    if table.sha256 != identity["candidate_sha256"]:
        raise ValueError("candidate identity")
    positions = ss.parse_positions(table.mutants)
    if len(positions) != 8570 or len(set(positions)) != 464:
        raise ValueError("position scope")
    ref = item["artifacts"]["membership"]
    e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
    seeds = (3000,) if args.mode == "smoke" else hx.NEW_SEEDS
    designs = None
    if args.mode != "design":
        if (
            args.design is None
            or args.design_sha is None
            or e3.digest(args.design / "result.json.gz") != args.design_sha
        ):
            raise ValueError("design required/identity")
        terminal = read(args.design / "terminal.json")
        de = read(args.design / "execution.json")
        if terminal["status"] != "succeeded" or any(
            e3.digest(args.design / k) != v for k, v in terminal["artifacts"].items()
        ):
            raise ValueError("design terminal identity")
        if de["source_sha256"] != e3.digest(__file__) or de["plan_sha256"] != e3.digest(args.plan):
            raise ValueError("design recipe changed")
        designs = {r["run_seed"]: r for r in read(args.design / "result.json.gz")["rows"]}
        if not any(
            r["label_balance"]["pair_changed"] or r["label_balance"]["composition_changed"]
            for r in designs.values()
        ):
            raise ValueError("no distinct intervention; stop")
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "helper_sha256": {Path(p).name: sha for p, sha in identities},
            "versions": versions,
            "prior_result_sha256": RESULT_SS,
            "prior_design_result_sha256": DESIGN_SS,
            "input_sha256": e3.INPUT_SHA,
            "input_identity": identity,
            "state_seeds": list(seeds),
            "initial_sample_size": 64,
            "training_size": 32,
            "label_cost": 80,
            "batch_size": 16,
            "arms": list(ARMS),
            "fitted_arms": list(ARMS[:-1]),
            "contrasts": [list(c) for c in CONTRASTS],
            "label_caliper_sd": 0.05,
            "design_result_sha256": args.design_sha,
            "new_labels": 0,
            "new_states": 0,
            "application_delta": None,
            "evidence_stage": "ITERATION",
        },
    )
    private = args.output / "private"
    private.mkdir()
    evaluator = e3.Evaluator(root, item, table)
    feature_path = args.previous / "full-001/private" / hx.ASSAY / "features.npy"
    with mp.get_context("spawn").Pool(
        1,
        initializer=ss.initialize,
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
            task = (
                (index, seed, observations, str(args.previous), references[seed])
                if args.mode == "design"
                else (
                    index,
                    seed,
                    observations,
                    str(args.design / "private"),
                    designs[seed]["design_sha256"],
                    references[seed],
                )
            )
            returned = pool.apply(prepare if args.mode == "design" else fit, (task,))
            if returned != index:
                raise ValueError("worker index")
            e3.event(
                args.output,
                "design_saved" if args.mode == "design" else "predictions_saved",
                index=index,
                run_seed=seed,
            )
    e3.event(args.output, "workers_exited")
    if args.mode == "design":
        rows = []
        for index, seed in enumerate(seeds):
            path = private / f"design-{index:03d}.json"
            meta = read(path)
            if meta["run_seed"] != seed:
                raise ValueError("design state identity")
            rows.append(
                {
                    k: meta[k]
                    for k in (
                        "run_seed",
                        "allowed_observation_sha256",
                        "manipulation",
                        "label_balance",
                    )
                }
                | {"design_sha256": e3.digest(path), "reference_sha256": meta["reference_sha256"]}
            )
        e3.event(args.output, "design_complete_without_scoring", states=len(rows))
    else:
        rows = score_locked(evaluator, table, private, seeds, designs, references)
        e3.event(args.output, "scoring_complete", states=len(rows))
    result = {
        "protocol": PROTOCOL,
        "mode": args.mode,
        "completed_states": len(rows),
        "point_fits": 0 if args.mode == "design" else 8 * len(rows),
        "rows": rows,
        **(summarize(rows, design=args.mode == "design") if args.mode != "smoke" else {}),
        "limitations": [
            "seen development landscape",
            "reference frontier indirectly uses allowed64 labels",
            "A labels close, not equal; input identities change",
            "B support fixed only in reference64; other input composition changes",
            "no unique coverage/label cause or independent mediation/confirmation",
        ],
    }
    with (
        (args.output / "result.json.gz").open("xb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out,
    ):
        out.write(json.dumps(result, sort_keys=True, allow_nan=False).encode())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "previous", "output", "plan"):
        p.add_argument("--" + name, type=Path)
    p.add_argument("--mode", choices=("design", "smoke", "full"), default="design")
    p.add_argument("--wall-seconds", type=int, default=90)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--design", type=Path)
    p.add_argument("--design-sha")
    args = p.parse_args()
    if args.self_test:
        selftest()
        print("label-balanced selector and mandatory-matching checks PASS")
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
