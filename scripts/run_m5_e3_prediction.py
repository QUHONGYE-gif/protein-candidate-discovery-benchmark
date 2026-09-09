#!/usr/bin/env python3
"""Bounded benign-assay E3 intervention; private rows remain on the execution host."""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import importlib.metadata
import json
import math
import multiprocessing as mp
import os
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np  # noqa: E402
from m5c_benign_scope import ASSAY_IDS, COUNTS, STUDY_ID  # noqa: E402
from pcdb.m5 import proteingym_runtime as rt  # noqa: E402
from pcdb.m5.proteingym_confirmation import (  # noqa: E402
    INITIAL_SAMPLE_SIZES,
    MODEL_IDS,
    RUN_SEEDS,
    initial_selection,
)
from run_m5c_gfp_iteration import (  # noqa: E402
    exact_json,
    tree_rss,
    validate_membership,
)

PROTOCOL = "m5-e3-fixed-policy-proxy-v1"
INPUT_SHA = "cfe2e613ed20969ea8a6072e703de6cead9affb745b66a5be4b8d047a6f13d10"
FEATURE_SHAS = dict(
    zip(
        ASSAY_IDS,
        (
            "6b4f427466b9ef1c3f38e0538aa89b076cbb0d3d37405d04f56b12269d786c06",
            "f55aa51622ca4f1230e956f9ec510e09526cbed13eade1841bf11320cd7be2dc",
            "1a7213fce0447662e2b9a2b54f5939d363e2883883da41fe2aad49f2b15ff58c",
        ),
        strict=True,
    )
)
AGGREGATE_SHAS = dict(
    zip(
        ASSAY_IDS,
        (
            "7514c6e4f106d0325a98ae923830eaca3267386c5a39babf4113cb524b782c62",
            "d591f0391da27179814c100ba0b99c7157615de1bba407062964dafe87b52c79",
            "86a08f6bda71047ad65b11c52d7b73cdfe7efaf6b0e8be731246b66d5789518e",
        ),
        strict=True,
    )
)
SPECS = [(m, n, s) for m in MODEL_IDS for n in INITIAL_SAMPLE_SIZES for s in RUN_SEEDS]
WORKER = None


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def write(path, value):
    with Path(path).open("x") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


def event(root, kind, **values):
    with (root / "progress.jsonl").open("a") as handle:
        handle.write(
            json.dumps({"time": datetime.now(UTC).isoformat(), "event": kind, **values}) + "\n"
        )


def rho(a, b):
    x, y = rt._average_ranks(a), rt._average_ranks(b)
    x -= x.mean()
    y -= y.mean()
    denominator = math.sqrt(float(x @ x) * float(y @ y))
    return None if denominator == 0 else float(x @ y) / denominator


def auc(values, positive):
    count = int(positive.sum())
    rest = len(positive) - count
    if count == 0 or rest == 0:
        return None
    return float(
        (rt._average_ranks(values)[positive].sum() - count * (count + 1) / 2) / (count * rest)
    )


def initialize(assay, ids, feature_path, feature_sha, output):
    global WORKER
    features = np.load(feature_path, mmap_mode="r")
    if assay not in ASSAY_IDS or features.shape != (len(ids), 421):
        raise ValueError("observable feature shape mismatch")
    if digest(feature_path) != feature_sha or not np.isfinite(features).all():
        raise ValueError("observable feature identity mismatch")
    WORKER = (assay, tuple(ids), features, feature_sha, Path(output))


def select(task):
    """No truth/evaluator path or unrevealed label is part of this worker capability."""
    assay, ids, features, feature_sha, output = WORKER
    index, model, size, seed, observations, original_state = task
    initial = initial_selection(ids, assay_id=assay, run_seed=seed, size=size, assay_ids=ASSAY_IDS)
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
    state_id = identity(
        {
            "protocol": PROTOCOL,
            "original_state_id": original_state,
            "feature_sha256": feature_sha,
        }
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


class Evaluator:
    """Full labels remain here; only aggregate diagnostics leave the evaluator."""

    def __init__(self, root, item, table):
        ref = item["artifacts"]["hidden_truth"]
        self.truth = rt.load_truth_evaluator(
            root / ref["path"],
            candidate_table=table,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        self.ids = table.candidate_ids
        self.lookup = {cid: i for i, cid in enumerate(self.ids)}

    def score(self, private, index):
        meta = json.loads((private / f"selection-{index:03d}.json").read_text())
        path = private / f"prediction-{index:03d}.npz"
        if digest(path) != meta.pop("prediction_sha256"):
            raise ValueError("saved prediction identity mismatch")
        selection = rt.StateSelection(**meta)
        aggregate, _ = rt.evaluate_state(selection, self.truth)
        with np.load(path) as data:
            remaining, mean, std = [data[k] for k in ("remaining_indices", "mean", "std")]
            if "members" in data and not np.array_equal(data["members"].std(axis=0), std):
                raise ValueError("uncertainty member reconstruction mismatch")
        expected = [i for i, c in enumerate(self.ids) if c not in set(selection.initial_ids)]
        if (
            remaining.tolist() != expected
            or mean.shape != std.shape
            or mean.shape != remaining.shape
        ):
            raise ValueError("prediction membership mismatch")
        if not np.isfinite(mean).all() or not np.isfinite(std).all() or (std < 0).any():
            raise ValueError("prediction value mismatch")
        remain_ids = np.asarray([self.ids[i] for i in remaining])
        for label, values in (
            ("greedy", mean),
            ("exploration", std if selection.model_id == MODEL_IDS[0] else mean + std),
        ):
            if list(rt._top_candidate_ids(remain_ids, values, 16)) != list(
                getattr(selection, label + "_ids")
            ):
                raise ValueError("selection reconstruction mismatch")
        y = self.truth._M5CTruthEvaluator__truth[remaining]
        high = self.truth._M5CTruthEvaluator__memberships[0.01][remaining]
        metrics = {
            "spearman": rho(mean, y),
            "mae": float(np.abs(mean - y).mean()),
            "top1_auc": auc(mean, high),
            "uq_error_rho": rho(std, np.abs(mean - y)),
            "uq_top1_auc": auc(std, high),
        }
        return selection, aggregate, metrics

    def compare(self, old_selection, new_selection):
        result = {}
        membership = self.truth._M5CTruthEvaluator__memberships[0.01]
        for name in ("greedy", "exploration"):
            before, after = [set(getattr(s, name + "_ids")) for s in (old_selection, new_selection)]
            entered, exited = after - before, before - after
            incoming = int(sum(membership[self.lookup[c]] for c in entered))
            outgoing = int(sum(membership[self.lookup[c]] for c in exited))
            result[name] = {
                "overlap": len(before & after),
                "entered_count": len(entered),
                "entered_hits": incoming,
                "exited_hits": outgoing,
                "net_hits": incoming - outgoing,
            }
        return result


def interval(values, seed):
    """Resample shared seed slots, preserving nested initial sizes and model dependence."""
    a = np.asarray(values, dtype=float)
    if (
        a.ndim not in (1, 2)
        or a.size < 10
        or a.size % 10
        or (a.ndim == 2 and a.shape[1] != 10)
        or not np.isfinite(a).all()
    ):
        raise ValueError("paired bootstrap shape/definedness mismatch")
    by_seed = a.reshape(-1, 10).mean(axis=0)
    slots = np.random.Generator(np.random.PCG64(seed)).integers(0, 10, (20000, 10))
    draws = by_seed[slots].mean(axis=1)
    return {
        "estimate": float(by_seed.mean()),
        "interval": np.quantile(draws, [0.003125, 0.996875]).tolist(),
        "coverage": 0.99375,
        "seed": seed,
        "draws": 20000,
    }


def summarize(rows, assay):
    out = []
    for mi, model in enumerate(MODEL_IDS):
        selected = sorted(
            [r for r in rows if r["model_id"] == model],
            key=lambda r: (r["initial_sample_size"], r["run_seed"]),
        )
        if len(selected) != 20:
            raise ValueError("full cell cover mismatch")
        metrics = {}
        for name in ("spearman", "mae", "top1_auc", "uq_error_rho", "uq_top1_auc"):
            pairs = [(r["old_diagnostics"][name], r["new_diagnostics"][name]) for r in selected]
            if any(a is None or b is None for a, b in pairs):
                metrics[name] = {
                    "defined_pairs": sum(a is not None and b is not None for a, b in pairs)
                }
            else:
                metrics[name] = {
                    "old": float(np.mean([a for a, b in pairs])),
                    "new": float(np.mean([b for a, b in pairs])),
                    "change": interval([b - a for a, b in pairs], 10600 + mi),
                }
        effects = {}
        for policy in ("greedy", "exploration"):
            for fraction in ("0.005", "0.01", "0.02"):
                values = [
                    r["new"]["branches"][policy]["high_value"][fraction]["recall"]
                    - r["old"]["branches"][policy]["high_value"][fraction]["recall"]
                    for r in selected
                ]
                effects[policy + "_recall_" + fraction] = interval(values, 10600 + mi)
            values = [
                r["new"]["branches"][policy]["best_found_improvement"]
                - r["old"]["branches"][policy]["best_found_improvement"]
                for r in selected
            ]
            effects[policy + "_best"] = interval(values, 10600 + mi)
        out.append(
            {
                "assay_id": assay,
                "model_id": model,
                "prediction": metrics,
                "fixed_policy_effects": effects,
                "unit": "within-assay shared seed slots",
            }
        )
    return out


def selftest():
    import tempfile
    from types import SimpleNamespace

    from scipy.stats import spearmanr
    from sklearn.metrics import roc_auc_score

    x, y = (
        np.array([1.0, 1.0, 3.0, 2.0, 7.0, 2.0]),
        np.array([9.0, 5.0, 1.0, 2.0, 2.0, 4.0]),
    )
    positive = np.array([True, False, False, True, True, False])
    assert abs(rho(x, y) - spearmanr(x, y).statistic) < 1e-14
    assert abs(auc(x, positive) - roc_auc_score(positive, x)) < 1e-14
    assert rho(np.ones(6), y) is None and auc(x, np.zeros(6, dtype=bool)) is None
    assert auc(np.ones(6), positive) == 0.5
    assert interval(np.zeros((2, 10)), 106)["interval"] == [0.0, 0.0]
    paired = np.stack([np.arange(10), -np.arange(10)])
    assert interval(paired, 106)["interval"] == [0.0, 0.0]
    assert interval(np.ones((2, 10)), 106)["estimate"] == 1.0
    assert interval(np.ones(20), 106)["interval"] == [1.0, 1.0]
    # Permuting rows and changing the hidden-label column cannot change extracted inputs.
    with tempfile.TemporaryDirectory() as temp:
        table = SimpleNamespace(candidate_ids=("a", "b"), mutant_to_index={"A1C": 0, "A1D": 1})
        paths = [Path(temp) / name for name in ("one.csv", "two.csv")]
        paths[0].write_text("mutant,ESM1v_single,DMS_score\nA1D,-3,99\nA1C,2,-99\n")
        paths[1].write_text("mutant,ESM1v_single,DMS_score\nA1C,2,456\nA1D,-3,-456\n")
        extracted = [
            rt.load_static_proxy(
                p,
                candidate_table=table,
                expected_sha256=digest(p),
                expected_size=p.stat().st_size,
            )
            for p in paths
        ]
        assert np.array_equal(extracted[0], extracted[1])
        assert extracted[0].tolist() == [2.0, -3.0]


def run(args):
    selftest()
    if digest(rt.__file__) != "75504878c51f03590329b3e5cdb92e47f7c1be8191bec63c5c33af69763dfce0":
        raise ValueError("baseline runtime source identity mismatch")
    root, output = args.study / "data-001", args.output
    manifest = exact_json(root / "input.json", INPUT_SHA)
    if (
        manifest["study_id"] != STUDY_ID
        or tuple(x["assay_id"] for x in manifest["assays"]) != ASSAY_IDS
    ):
        raise ValueError("study identity mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("runtime identity mismatch")
    old_diagnosis = exact_json(
        args.study / "model-diagnosis-001/diagnosis.json",
        "4a69e240f0b8615d839195591bcc6b06284a1f1e42d8dba482ab3dfe59b8b376",
    )
    diagnosis_by_state = {r["state_id"]: r for r in old_diagnosis["states"]}
    write(
        output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": digest(__file__),
            "runtime_source_sha256": digest(rt.__file__),
            "plan_sha256": digest(args.plan),
            "input_sha256": INPUT_SHA,
            "versions": versions,
            "state_specs": SPECS,
            "application_delta": None,
            "evidence_stage": "ITERATION",
            "authorization": (
                "User requested PR purpose and stepwise joint E3 implementation, 2026-09-09"
            ),
            "truth_boundary": (
                "evaluator-only complete labels; observable/allowed-observation spawn workers; "
                "not OS isolation"
            ),
        },
    )
    all_rows, summaries, inputs = [], [], []
    indices = (
        [0, 20] if args.mode == "smoke" else [0, 20] + [i for i in range(40) if i not in (0, 20)]
    )
    for item in manifest["assays"]:
        assay, artifacts = item["assay_id"], item["artifacts"]
        if (
            item["candidate_count"] != COUNTS[assay]
            or item["partition"] != "candidate-pool"
            or item["held_out_partitions"]
        ):
            raise ValueError("candidate/partition mismatch")
        if (
            item["label_direction"] != "higher_is_better"
            or item["label_field"] != "DMS_score"
            or item["observable_fields"] != ["mutant", "mutated_sequence"]
        ):
            raise ValueError("label/input semantics mismatch")
        if item["sample_sequence_reference_check"] != "PASS" or item["id_coverage_check"] != "PASS":
            raise ValueError("sample/sequence identity mismatch")
        if assay == ASSAY_IDS[2]:
            ev = exact_json(
                args.study / "provenance-001/gck-label-reconciliation.json",
                "c09a7f65e6f64cecc05d7812e4cb92d1f32160a379d1163f2b4c2f5a2e69ba57",
            )
            if (
                ev["status"] != "PASS"
                or ev["matched_count"] != COUNTS[assay]
                or ev["mismatched_count"]
                or ev["missing_count"]
                or ev["proteingym_truth_sha256"] != artifacts["truth_source"]["sha256"]
            ):
                raise ValueError("measured-label reconciliation mismatch")
        ref = artifacts["candidate"]
        table = rt.load_candidate_table(
            root / ref["path"],
            assay_id=assay,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        ref = artifacts["membership"]
        validate_membership(root / ref["path"], ref, table.candidate_ids)
        original = (
            args.study
            / ("runs-001" if assay == ASSAY_IDS[0] else "remaining-runs-001")
            / assay
            / "full-001"
        )
        old_aggregate = exact_json(original / "aggregate-input.json", AGGREGATE_SHAS[assay])
        old_states = {
            (r["model_id"], r["initial_sample_size"], r["run_seed"]): r
            for r in old_aggregate["state_aggregates"]
        }
        if set(old_states) != set(SPECS):
            raise ValueError("baseline state cover mismatch")
        basepath = original / "private/features.npy"
        if digest(basepath) != FEATURE_SHAS[assay]:
            raise ValueError("baseline feature identity mismatch")
        private = output / "private" / assay
        private.mkdir(parents=True)
        ref = artifacts["proxy_source"]
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
        inputs.append(
            {
                "assay_id": assay,
                "candidate_sha256": table.sha256,
                "baseline_feature_sha256": FEATURE_SHAS[assay],
                "proxy_source_sha256": ref["sha256"],
                "new_feature_sha256": feature_sha,
                "column": "ESM1v_single",
                "dimension": 421,
            }
        )
        del features, proxy
        event(output, "observable_input_verified", assay_id=assay)
        # The raw score table stays outside workers, which receive only the extracted matrix.
        with mp.get_context("spawn").Pool(
            1,
            initializer=initialize,
            initargs=(
                assay,
                table.candidate_ids,
                str(featurepath),
                feature_sha,
                str(private),
            ),
        ) as pool:
            evaluator = Evaluator(root, item, table)
            for index in indices:
                model, size, seed = SPECS[index]
                initial = initial_selection(
                    table.candidate_ids,
                    assay_id=assay,
                    run_seed=seed,
                    size=size,
                    assay_ids=ASSAY_IDS,
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
                event(output, "prediction_saved", assay_id=assay, index=index)
        # Workers have exited before complete-truth prediction diagnostics and branch scoring.
        rows = []
        for index in indices:
            model, size, seed = SPECS[index]
            old_private = args.study / "model-diagnosis-001/private" / assay
            old_selection, baseline, before = evaluator.score(old_private, index)
            if baseline != old_states[SPECS[index]]:
                raise ValueError("baseline exact branch reconstruction mismatch")
            expected_diagnosis = diagnosis_by_state[old_selection.state_id]
            if (
                digest(old_private / f"prediction-{index:03d}.npz")
                != expected_diagnosis["private_prediction_sha256"]
            ):
                raise ValueError("accepted baseline prediction hash mismatch")
            if (
                digest(old_private / f"selection-{index:03d}.json")
                != expected_diagnosis["private_selection_sha256"]
            ):
                raise ValueError("accepted baseline selection hash mismatch")
            selection, new, after = evaluator.score(private, index)
            if list(selection.initial_ids) != list(old_selection.initial_ids):
                raise ValueError("paired initial observations mismatch")
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
                    "assay_id": assay,
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
        all_rows.extend(rows)
        if args.mode == "full":
            summaries.extend(summarize(rows, assay))
        event(output, "assay_scored", assay_id=assay, completed_states=len(all_rows))
    result = {
        "protocol": PROTOCOL,
        "evidence_stage": "ITERATION",
        "completed_states": len(all_rows),
        "evaluated_new_branches": len(all_rows) * 66,
        "inputs": inputs,
        "summaries": summaries,
        "rows": all_rows,
        "baseline_reconstruction": "PASS",
        "paired_initials": "PASS",
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--wall-seconds", type=int, default=2300)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        print("self-test PASS")
        return 0
    if not args.study or not args.output or not args.plan:
        parser.error("study, output and plan are required")
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
