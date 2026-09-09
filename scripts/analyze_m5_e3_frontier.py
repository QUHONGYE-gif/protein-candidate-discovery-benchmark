#!/usr/bin/env python3
"""Saved-prediction frontier diagnostics on the three approved benign C assays."""

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

import numpy as np  # noqa: E402
import run_m5_e3_prediction as e3  # noqa: E402

PROTOCOL = "m5-e3-saved-frontier-v1"
PREVIOUS_SHA = "937febfe04c7f45ff490249fdce1f33b0944d54b7a9a9a9939f3a0776d51eb08"
E3_SHA = "92e384e1972f03144706000b84ef900a909127651478593692baa4207f1be0f1"
RUNTIME_SHA = "75504878c51f03590329b3e5cdb92e47f7c1be8191bec63c5c33af69763dfce0"
DIAGNOSIS_SHA = "4a69e240f0b8615d839195591bcc6b06284a1f1e42d8dba482ab3dfe59b8b376"
KS = (8, 16, 32, 64)


def forbidden_fit(*args, **kwargs):
    raise RuntimeError("new fitting is forbidden in saved-prediction analysis")


e3.rt._fit_model = forbidden_fit


def load_prediction(directory, index):
    path = Path(directory) / f"prediction-{index:03d}.npz"
    meta = json.loads((Path(directory) / f"selection-{index:03d}.json").read_text())
    if e3.digest(path) != meta["prediction_sha256"]:
        raise ValueError("prediction digest mismatch")
    with np.load(path, allow_pickle=False) as data:
        remaining, mean, std = [data[k] for k in ("remaining_indices", "mean", "std")]
    if remaining.shape != mean.shape or mean.shape != std.shape:
        raise ValueError("prediction shape mismatch")
    if not np.isfinite(mean).all() or not np.isfinite(std).all() or (std < 0).any():
        raise ValueError("nonfinite prediction or negative uncertainty")
    return meta, remaining, mean, std


def select_saved(task):
    """Spawn worker receives IDs and saved predictions only; never an evaluator or labels."""
    ids, old_dir, new_dir, index, output = task
    old, rem0, mean0, std0 = load_prediction(old_dir, index)
    new, rem1, mean1, std1 = load_prediction(new_dir, index)
    for key in ("assay_id", "model_id", "initial_ids", "initial_sample_size", "run_seed"):
        if old[key] != new[key]:
            raise ValueError("unpaired saved states")
    expected = [i for i, c in enumerate(ids) if c not in set(old["initial_ids"])]
    if rem0.tolist() != expected or not np.array_equal(rem0, rem1):
        raise ValueError("remaining membership mismatch")
    remain_ids = np.asarray(ids)[rem0]
    orders = {
        "old_order": np.lexsort((remain_ids, -mean0)),
        "new_order": np.lexsort((remain_ids, -mean1)),
    }
    for version, mean, std in ((old, mean0, std0), (new, mean1, std1)):
        score = std if version["model_id"] == e3.MODEL_IDS[0] else mean + std
        for policy, values in (("greedy", mean), ("exploration", score)):
            if list(e3.rt._top_candidate_ids(remain_ids, values, 16)) != version[policy + "_ids"]:
                raise ValueError("historical branch reconstruction mismatch")
    mixed = {}
    if old["model_id"] == e3.MODEL_IDS[1]:
        for key, values in (("10", mean1 + std0), ("01", mean0 + std1)):
            mixed[key] = list(e3.rt._top_candidate_ids(remain_ids, values, 16))
    output = Path(output)
    order_path = output / f"order-{index:03d}.npz"
    with order_path.open("xb") as handle:
        np.savez(handle, **{k: v.astype(np.int32) for k, v in orders.items()})
    e3.write(
        output / f"branches-{index:03d}.json",
        {
            "old_state_id": old["state_id"],
            "new_state_id": new["state_id"],
            "source_prediction_shas": [old["prediction_sha256"], new["prediction_sha256"]],
            "source_selection_shas": [
                e3.digest(Path(d) / f"selection-{index:03d}.json") for d in (old_dir, new_dir)
            ],
            "order_sha256": e3.digest(order_path),
            "mixed": mixed,
        },
    )
    return index


def frontier_metrics(mean, high, top2, order, denominator):
    """Evaluator-only aggregate function; selection order was saved before scoring."""
    if not np.array_equal(np.sort(order), np.arange(len(mean))) or (high & ~top2).any():
        raise ValueError("invalid ranking or nested high-value membership")
    near, far = top2 & ~high, ~top2
    counts = {k: int(v.sum()) for k, v in (("high", high), ("near", near), ("far", far))}
    out = {"remaining_" + k: v for k, v in counts.items()}
    for key, negative in (("near", near), ("far", far)):
        mask = high | negative
        out["auc_" + key] = e3.auc(mean[mask], high[mask])
    out["auc_all"] = e3.auc(mean, high)
    parts = [(out["auc_" + k], counts[k]) for k in ("near", "far") if counts[k]]
    weighted = (
        sum(a * n for a, n in parts) / sum(n for a, n in parts)
        if parts and all(a is not None for a, n in parts)
        else None
    )
    if weighted is not None and abs(weighted - out["auc_all"]) > 1e-12:
        raise ValueError("AUC mixture reconstruction mismatch")
    out["auc_reconstructed"] = weighted
    ranked = high[order]
    ranks = np.flatnonzero(ranked) + 1
    out["first_high_rank"] = int(ranks[0]) if len(ranks) else None
    out["median_high_rank"] = float(np.median(ranks)) if len(ranks) else None
    for k in KS:
        hits = int(ranked[:k].sum())
        out[f"hits_{k}"] = hits
        out[f"recall_{k}"] = hits / denominator
        out[f"precision_{k}"] = hits / k
    for key, mask in (("near", near), ("far", far)):
        out["top16_" + key] = int(mask[order[:16]].sum())
    if out["hits_16"] + out["top16_near"] + out["top16_far"] != 16:
        raise ValueError("frontier category count mismatch")
    return out


class Evaluator(e3.Evaluator):
    def frontier(self, directory, index, order):
        _, remaining, mean, _ = load_prediction(directory, index)
        memberships = self.truth._M5CTruthEvaluator__memberships
        return frontier_metrics(
            mean,
            memberships[0.01][remaining],
            memberships[0.02][remaining],
            order,
            int(memberships[0.01].sum()),
        )

    def factorial(self, old, new, mixed, prior):
        if old.model_id == e3.MODEL_IDS[0]:
            if mixed:
                raise ValueError("unexpected RF mixed branches")
            return None
        branches = {
            "00": self.truth.evaluate_batch(old.initial_ids, old.exploration_ids),
            "11": self.truth.evaluate_batch(new.initial_ids, new.exploration_ids),
        }
        for key in ("10", "01"):
            branches[key] = self.truth.evaluate_batch(old.initial_ids, mixed[key])
        if branches["00"] != prior["old"]["branches"]["exploration"]:
            raise ValueError("old factorial endpoint mismatch")
        if branches["11"] != prior["new"]["branches"]["exploration"]:
            raise ValueError("new factorial endpoint mismatch")
        return branches


def summarize(rows):
    summaries = []
    for assay in e3.ASSAY_IDS:
        for mi, model in enumerate(e3.MODEL_IDS):
            cell = sorted(
                [r for r in rows if r["assay_id"] == assay and r["model_id"] == model],
                key=lambda r: (r["initial_sample_size"], r["run_seed"]),
            )
            if len(cell) != 20:
                raise ValueError("full state coverage mismatch")
            summary = {"assay_id": assay, "model_id": model, "states": 20, "frontier": {}}
            for metric in cell[0]["old_frontier"]:
                pairs = [(r["old_frontier"][metric], r["new_frontier"][metric]) for r in cell]
                defined = [(a, b) for a, b in pairs if a is not None and b is not None]
                record = {"defined_pairs": len(defined), "old": None, "new": None, "change": None}
                if len(defined) == 20:
                    record.update(
                        old=float(np.mean([a for a, b in pairs])),
                        new=float(np.mean([b for a, b in pairs])),
                        change=e3.interval([b - a for a, b in pairs], 10800 + mi),
                    )
                summary["frontier"][metric] = record
            summary["factorial"] = None
            if mi == 1:
                summary["factorial"] = {}
                for metric in ("hits", "recall", "best_found_improvement"):
                    values = {}
                    for branch in ("00", "10", "01", "11"):
                        vals = [r["factorial"][branch] for r in cell]
                        values[branch] = np.array(
                            [
                                b["best_found_improvement"]
                                if metric == "best_found_improvement"
                                else b["high_value"]["0.01"][
                                    "count" if metric == "hits" else "recall"
                                ]
                                for b in vals
                            ]
                        )
                    v = values
                    effects = {
                        "mean_given_old_uq": v["10"] - v["00"],
                        "mean_given_new_uq": v["11"] - v["01"],
                        "uq_given_old_mean": v["01"] - v["00"],
                        "uq_given_new_mean": v["11"] - v["10"],
                        "total": v["11"] - v["00"],
                        "interaction": v["11"] - v["10"] - v["01"] + v["00"],
                    }
                    summary["factorial"][metric] = {
                        "branch_means": {k: float(a.mean()) for k, a in values.items()},
                        "effects": {k: e3.interval(a, 10901) for k, a in effects.items()},
                    }
            summaries.append(summary)
    return summaries


def selftest():
    high = np.zeros(80, dtype=bool)
    high[[2, 30]] = True
    top2 = high.copy()
    top2[[8, 15]] = True
    mean = np.repeat(np.arange(40), 2).astype(float)
    ids = np.array([f"c{i:03d}" for i in range(80)])
    order = np.lexsort((ids, -mean))
    result = frontier_metrics(mean, high, top2, order, 2)

    def direct_auc(negative):
        diff = mean[high, None] - mean[negative][None, :]
        return float(((diff > 0) + 0.5 * (diff == 0)).mean())

    for key, negative in (("near", top2 & ~high), ("far", ~top2), ("all", ~high)):
        assert abs(result["auc_" + key] - direct_auc(negative)) < 1e-14
    assert result["hits_16"] == int(high[order[:16]].sum())
    assert list(e3.rt._top_candidate_ids(ids, mean, 16)) == ids[order[:16]].tolist()
    assert e3.interval(np.stack([np.arange(10), -np.arange(10)]), 1)["interval"] == [0.0, 0.0]
    empty = frontier_metrics(mean, np.zeros(80, dtype=bool), top2, order, 2)
    assert empty["auc_all"] is None and empty["first_high_rank"] is None
    try:
        e3.rt._fit_model()
    except RuntimeError:
        pass
    else:
        raise AssertionError("fit guard failed")


def run(args):
    selftest()
    for path, expected in (
        (e3.__file__, E3_SHA),
        (e3.rt.__file__, RUNTIME_SHA),
        (args.previous / "full-001/result.json.gz", PREVIOUS_SHA),
    ):
        if e3.digest(path) != expected:
            raise ValueError("source or prior result mismatch")
    previous = json.loads(gzip.decompress((args.previous / "full-001/result.json.gz").read_bytes()))

    def key(r):
        return (r["assay_id"], r["model_id"], r["initial_sample_size"], r["run_seed"])

    prior = {key(r): r for r in previous["rows"]}
    if len(prior) != len(previous["rows"]) or len(prior) != 120:
        raise ValueError("previous state coverage mismatch")
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    if tuple(x["assay_id"] for x in manifest["assays"]) != e3.ASSAY_IDS:
        raise ValueError("assay scope mismatch")
    diagnosis = e3.exact_json(args.study / "model-diagnosis-001/diagnosis.json", DIAGNOSIS_SHA)
    diagnosis = {r["state_id"]: r for r in diagnosis["states"]}
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "previous_result_sha256": PREVIOUS_SHA,
            "previous_source_sha256": E3_SHA,
            "runtime_sha256": RUNTIME_SHA,
            "input_sha256": e3.INPUT_SHA,
            "state_specs": e3.SPECS,
            "new_fits": 0,
            "truth_boundary": (
                "prediction-only spawn worker exits before evaluator; not OS isolation"
            ),
            "application_delta": None,
            "evidence_stage": "ITERATION",
            "cutoffs": KS,
            "new_branch_rule": "XGB only: new mean + old sigma; old mean + new sigma; beta=1",
        },
    )
    indices = [0, 20] if args.mode == "smoke" else list(range(40))
    rows = []
    for item in manifest["assays"]:
        assay, ref = item["assay_id"], item["artifacts"]["candidate"]
        if item["label_direction"] != "higher_is_better" or item["held_out_partitions"]:
            raise ValueError("label direction or held-out mismatch")
        table = e3.rt.load_candidate_table(
            root / ref["path"],
            assay_id=assay,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        old_dir = args.study / "model-diagnosis-001/private" / assay
        new_dir = args.previous / "full-001/private" / assay
        private = args.output / "private" / assay
        private.mkdir(parents=True)
        with mp.get_context("spawn").Pool(1) as pool:
            for index in indices:
                task = (table.candidate_ids, str(old_dir), str(new_dir), index, str(private))
                if pool.apply(select_saved, (task,)) != index:
                    raise ValueError("worker state mismatch")
        e3.event(args.output, "all_assay_orders_saved_worker_exited", assay_id=assay)
        evaluator = Evaluator(root, item, table)
        for index in indices:
            model, size, seed = e3.SPECS[index]
            expected = prior[(assay, model, size, seed)]
            old, baseline, before = evaluator.score(old_dir, index)
            new, revised, after = evaluator.score(new_dir, index)
            if baseline != expected["old"] or revised != expected["new"]:
                raise ValueError("previous aggregate exact reconstruction mismatch")
            if before != expected["old_diagnostics"] or after != expected["new_diagnostics"]:
                raise ValueError("previous prediction diagnostic mismatch")
            d = diagnosis[old.state_id]
            for label in ("prediction", "selection"):
                suffix = ".npz" if label == "prediction" else ".json"
                if (
                    e3.digest(old_dir / f"{label}-{index:03d}{suffix}")
                    != d[f"private_{label}_sha256"]
                ):
                    raise ValueError("baseline private identity mismatch")
            meta = json.loads((private / f"branches-{index:03d}.json").read_text())
            if meta["old_state_id"] != old.state_id or meta["new_state_id"] != new.state_id:
                raise ValueError("private state identity mismatch")
            orderpath = private / f"order-{index:03d}.npz"
            if e3.digest(orderpath) != meta["order_sha256"]:
                raise ValueError("saved order identity mismatch")
            row = {
                "assay_id": assay,
                "model_id": model,
                "initial_sample_size": size,
                "run_seed": seed,
                "source_identity": {k: v for k, v in meta.items() if k != "mixed"},
            }
            with np.load(orderpath, allow_pickle=False) as data:
                for version, directory in (("old", old_dir), ("new", new_dir)):
                    f = evaluator.frontier(directory, index, data[version + "_order"])
                    if (
                        f["hits_16"]
                        != expected[version]["branches"]["greedy"]["high_value"]["0.01"]["count"]
                    ):
                        raise ValueError("main budget hit reconstruction mismatch")
                    if f["auc_all"] != expected[version + "_diagnostics"]["top1_auc"]:
                        raise ValueError("global AUC reconstruction mismatch")
                    row[version + "_frontier"] = f
            row["factorial"] = evaluator.factorial(old, new, meta["mixed"], expected)
            rows.append(row)
        del evaluator
        e3.event(args.output, "assay_scored", assay_id=assay, completed_states=len(rows))
    result = {
        "protocol": PROTOCOL,
        "completed_states": len(rows),
        "new_fits": 0,
        "new_mixed_branches": sum(r["factorial"] is not None for r in rows) * 2,
        "previous_reconstruction": "PASS",
        "paired_initials": "PASS",
        "rows": rows,
        "summaries": summarize(rows) if args.mode == "full" else [],
        "limits": [
            "posthoc development diagnostic",
            "no population CI",
            "no application delta",
            "hybrid branches are computational counterfactuals",
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
        print("frontier self-test PASS")
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
