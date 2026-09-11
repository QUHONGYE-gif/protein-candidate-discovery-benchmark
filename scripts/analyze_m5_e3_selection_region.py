#!/usr/bin/env python3
"""Compare saved-prediction improvements inside and outside the G16 competition region."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import signal
import threading
import time
from pathlib import Path

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_var] = "1"

import numpy as np  # noqa: E402

PROTOCOL = "m5-e3-selection-region-v1"
INPUT_RESULT_SHA = "937febfe04c7f45ff490249fdce1f33b0944d54b7a9a9a9939f3a0776d51eb08"
FRONTIER_SHA = "67e98445f6c4fe3f6fd46a966b2af321122ea4c1cc8371a7eac81fef164715ba"
REPLICATION_SHA = "4f8063f48e5671f9d3008751a05fde7072f26feafe932a7722f6c766209481e8"
HELPER_SHA = "92e384e1972f03144706000b84ef900a909127651478593692baa4207f1be0f1"
RUNTIME_SHA = "75504878c51f03590329b3e5cdb92e47f7c1be8191bec63c5c33af69763dfce0"
K = 16
IDS = None
RESOURCES = {"peak_rss_bytes": 0}
STOP_MONITOR = threading.Event()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
        handle.write("\n")


def event(root, name, **details):
    with (root / "progress.jsonl").open("a") as handle:
        handle.write(json.dumps({"unix_time": time.time(), "event": name, **details}) + "\n")


def exact_gzip(path, expected):
    if digest(path) != expected:
        raise ValueError("source aggregate identity mismatch")
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def rowkey(row):
    return tuple(row[k] for k in ("assay_id", "model_id", "initial_sample_size", "run_seed"))


def paired_auc(scores, high, negative):
    """Pairwise probability, with half credit for tied scores; no model fitting."""
    positives = scores[high]
    negatives = np.sort(scores[negative])
    if not len(positives) or not len(negatives):
        return None
    wins2 = np.searchsorted(negatives, positives, side="left")
    wins2 += np.searchsorted(negatives, positives, side="right")
    return float(wins2.sum() / (2 * len(positives) * len(negatives)))


def region_metrics(old, new, high, order0, order1):
    n = len(high)
    if n < K or not high.any():
        raise ValueError("insufficient remaining candidates or positives")
    if not all(np.array_equal(np.sort(o), np.arange(n)) for o in (order0, order1)):
        raise ValueError("invalid order permutation")
    head0, head1 = np.zeros(n, bool), np.zeros(n, bool)
    head0[order0[:K]], head1[order1[:K]] = True, True
    regions = {"union": head0 | head1, "old_reference": head0, "new_reference": head1}
    output = {
        "remaining_count": n,
        "remaining_high": int(high.sum()),
        "old": {},
        "new": {},
        "regions": {},
    }
    for label, head in regions.items():
        negative_head, negative_other = head & ~high, ~head & ~high
        output["regions"][label] = {
            "candidate_count": int(head.sum()),
            "high_count": int((head & high).sum()),
            "head_negative": int(negative_head.sum()),
            "other_negative": int(negative_other.sum()),
        }
        for version, scores in (("old", old), ("new", new)):
            a = paired_auc(scores, high, negative_head)
            b = paired_auc(scores, high, negative_other)
            output[version][label + "_head_auc"] = a
            output[version][label + "_other_auc"] = b
            if a is not None and b is not None:
                weights = output["regions"][label]
                mixed = (a * weights["head_negative"] + b * weights["other_negative"]) / (
                    weights["head_negative"] + weights["other_negative"]
                )
                overall = paired_auc(scores, high, ~high)
                if abs(mixed - overall) > 1e-12:
                    raise ValueError("AUC mixture does not reconstruct")
    ranks = []
    for version, scores, order in (("old", old, order0), ("new", new, order1)):
        r = np.empty(n, np.int32)
        r[order] = np.arange(1, n + 1)
        ranks.append(r)
        output[version].update(
            all_auc=paired_auc(scores, high, ~high),
            hits16=int(high[order[:K]].sum()),
            first_high_rank=int(r[high].min()),
            median_high_rank=float(np.median(r[high])),
        )
    r0, r1 = ranks
    up, down, same = (high & mask for mask in (r1 < r0, r1 > r0, r1 == r0))
    both_out = (r0 > K) & (r1 > K)
    output["movement"] = {
        "high_up": int(up.sum()),
        "high_down": int(down.sum()),
        "high_same": int(same.sum()),
        "up_outside_both": int((up & both_out).sum()),
        "up_inside_either": int((up & ~both_out).sum()),
        "down_outside_both": int((down & both_out).sum()),
        "entered16": int((high & ~head0 & head1).sum()),
        "exited16": int((high & head0 & ~head1).sum()),
        "retained16": int((high & head0 & head1).sum()),
        "outside_both": int((high & both_out).sum()),
    }
    m = output["movement"]
    if m["entered16"] - m["exited16"] != output["new"]["hits16"] - output["old"]["hits16"]:
        raise ValueError("entry/exit identity mismatch")
    if m["high_up"] + m["high_down"] + m["high_same"] != int(high.sum()):
        raise ValueError("rank movement identity mismatch")
    output["effects"] = {}
    for label in regions:
        values = [
            output[v][label + "_" + r + "_auc"] for v in ("old", "new") for r in ("head", "other")
        ]
        output["effects"][label + "_other_minus_head"] = (
            None
            if any(x is None for x in values)
            else (values[3] - values[1]) - (values[2] - values[0])
        )
    return output


def initialize(ids):
    global IDS
    IDS = np.asarray(ids)


def load_pair(task):
    """Only IDs, saved cache paths, hashes and state identifiers reach this worker."""
    arrays, metadata = [], []
    for index, source in enumerate(task["sources"]):
        path, meta_path = Path(source["prediction"]), Path(source["selection"])
        if digest(path) != source["prediction_sha256"]:
            raise ValueError("saved prediction identity changed")
        if source.get("selection_sha256") and digest(meta_path) != source["selection_sha256"]:
            raise ValueError("saved selection identity changed")
        meta = json.loads(meta_path.read_text())
        if meta["prediction_sha256"] != source["prediction_sha256"]:
            raise ValueError("selection/prediction binding changed")
        for k in ("assay_id", "initial_sample_size", "run_seed"):
            if meta[k] != task[k]:
                raise ValueError("source state mismatch")
        if task["cohort"] == "original" and meta["model_id"] != task["model_id"]:
            raise ValueError("source model mismatch")
        with np.load(path, allow_pickle=False) as d:
            rem = d["remaining_indices"]
            if task["cohort"] == "original":
                scores = d["mean"]
            else:
                scores = d["old" if index == 0 else "new"]
        if rem.ndim != 1 or scores.shape != rem.shape or not np.isfinite(scores).all():
            raise ValueError("bad saved prediction shape/value")
        if (
            not np.array_equal(rem, np.sort(np.unique(rem)))
            or rem.min() < 0
            or rem.max() >= len(IDS)
        ):
            raise ValueError("invalid remaining indices")
        expected = np.flatnonzero(~np.isin(IDS, meta["initial_ids"]))
        if len(set(meta["initial_ids"])) != task["initial_sample_size"] or not np.array_equal(
            rem, expected
        ):
            raise ValueError("initial/remaining identity mismatch")
        order = np.lexsort((IDS[rem], -scores))
        stored = (
            meta["greedy_ids"]
            if task["cohort"] == "original"
            else meta["selections"]["old" if index == 0 else "new"][:K]
        )
        if IDS[rem][order[:K]].tolist() != stored:
            raise ValueError("G16 reconstruction mismatch")
        arrays.append((rem, scores, order))
        metadata.append(meta)
    if metadata[0]["initial_ids"] != metadata[1]["initial_ids"] or not np.array_equal(
        arrays[0][0], arrays[1][0]
    ):
        raise ValueError("unpaired observations or remaining pool")
    return arrays


def prepare(task):
    pair = load_pair(task)
    path = Path(task["prepared"])
    with path.open("xb") as handle:
        np.savez(
            handle, old_order=pair[0][2].astype(np.int32), new_order=pair[1][2].astype(np.int32)
        )
    return {
        "prepared_sha256": digest(path),
        "prediction_shas": [s["prediction_sha256"] for s in task["sources"]],
    }


def forbidden(*_args, **_kwargs):
    raise RuntimeError("fitting/reveal is forbidden for saved-prediction analysis")


def selftest():
    rng = np.random.default_rng(391)
    for n in (40, 80, 120):
        for _ in range(8):
            old, new = rng.integers(0, 20, (2, n)).astype(float)
            high = np.zeros(n, bool)
            high[rng.choice(n, 5, replace=False)] = True
            order0, order1 = [np.lexsort((np.arange(n), -x)) for x in (old, new)]
            out = region_metrics(old, new, high, order0, order1)
            for version, scores in (("old", old), ("new", new)):
                head = np.zeros(n, bool)
                head[np.union1d(order0[:K], order1[:K])] = True
                for label, mask in (("head", head & ~high), ("other", ~head & ~high)):
                    differences = scores[high, None] - scores[mask][None, :]
                    brute = float(((differences > 0) + 0.5 * (differences == 0)).mean())
                    assert abs(brute - out[version]["union_" + label + "_auc"]) < 1e-12
            unchanged = region_metrics(old, old, high, order0, order0)
            assert unchanged["effects"]["union_other_minus_head"] == 0
            assert unchanged["movement"]["high_same"] == 5
    high = np.zeros(40, bool)
    high[:K] = True
    same = np.arange(40, 0, -1, dtype=float)
    result = region_metrics(same, same, high, np.arange(40), np.arange(40))
    assert result["old"]["union_head_auc"] is None
    assert result["effects"]["union_other_minus_head"] is None
    print("PASS: paired AUC/brute force/ties/rank movement/no-op/undefined synthetic checks")


def run(args):
    import run_m5_e3_prediction as e3

    if digest(e3.__file__) != HELPER_SHA or digest(e3.rt.__file__) != RUNTIME_SHA:
        raise ValueError("historical helper/runtime mismatch")

    def monitor():
        while not STOP_MONITOR.wait(0.25):
            rss = e3.tree_rss(os.getpid())
            RESOURCES["peak_rss_bytes"] = max(RESOURCES["peak_rss_bytes"], rss)
            if rss > 1536 * 1024**2:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=monitor, daemon=True).start()
    e3.rt._fit_model = forbidden
    e3.rt.M5CTruthEvaluator.reveal = forbidden
    old = exact_gzip(args.previous / "full-001/result.json.gz", INPUT_RESULT_SHA)
    frontier = exact_gzip(args.previous / "frontier-full-001/result.json.gz", FRONTIER_SHA)
    new = exact_gzip(args.previous / "replication-full-001/result.json.gz", REPLICATION_SHA)
    if len(old["rows"]) != 120 or len(new["rows"]) != 120 or len(frontier["rows"]) != 120:
        raise ValueError("historical cohort size changed")
    references = {
        "original": {rowkey(r): r for r in old["rows"]},
        "replication": {rowkey(r): r for r in new["rows"]},
    }
    identities = {rowkey(r): r["source_identity"] for r in frontier["rows"]}
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    if tuple(x["assay_id"] for x in manifest["assays"]) != e3.ASSAY_IDS:
        raise ValueError("assay scope changed")
    write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": digest(__file__),
            "plan_sha256": digest(args.plan),
            "helper_sha256": HELPER_SHA,
            "runtime_sha256": RUNTIME_SHA,
            "input_sha256": e3.INPUT_SHA,
            "original_result_sha256": INPUT_RESULT_SHA,
            "frontier_result_sha256": FRONTIER_SHA,
            "replication_result_sha256": REPLICATION_SHA,
            "new_fits": 0,
            "new_labels": 0,
            "new_states": 0,
            "k": K,
            "data_boundary": (
                "all prediction-only workers exit before evaluator reads existing membership; "
                "no reveal; aggregate-only export"
            ),
        },
    )
    all_tasks, tables = [], {}
    for item in manifest["assays"]:
        assay, ref = item["assay_id"], item["artifacts"]["candidate"]
        if item["label_direction"] != "higher_is_better" or item["held_out_partitions"]:
            raise ValueError("label/held-out mismatch")
        table = e3.rt.load_candidate_table(
            root / ref["path"],
            assay_id=assay,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        if len(table.candidate_ids) != e3.COUNTS[assay]:
            raise ValueError("candidate count mismatch")
        tables[assay] = (item, table)
        tasks = []
        for cohort in references:
            for key, prior in sorted(references[cohort].items()):
                if key[0] != assay:
                    continue
                if args.mode == "smoke" and (
                    key[2] != 32 or key[3] != (e3.RUN_SEEDS[0] if cohort == "original" else 2000)
                ):
                    continue
                if cohort == "original":
                    index = e3.SPECS.index(key[1:])
                    dirs = [
                        args.study / "model-diagnosis-001/private" / assay,
                        args.previous / "full-001/private" / assay,
                    ]
                    ident = identities[key]
                    sources = [
                        {
                            "prediction": str(d / f"prediction-{index:03d}.npz"),
                            "selection": str(d / f"selection-{index:03d}.json"),
                            "prediction_sha256": ident["source_prediction_shas"][i],
                            "selection_sha256": ident["source_selection_shas"][i],
                        }
                        for i, d in enumerate(dirs)
                    ]
                else:
                    if key[1] != e3.MODEL_IDS[1] or key[3] not in range(2000, 2020):
                        raise ValueError("replication scope mismatch")
                    index = (key[2] == 64) * 20 + key[3] - 2000
                    d = args.previous / "replication-full-001/private" / assay
                    sources = [
                        {
                            "prediction": str(d / f"prediction-{index:03d}.npz"),
                            "selection": str(d / f"selection-{index:03d}.json"),
                            "prediction_sha256": prior["prediction_sha256"],
                        }
                    ] * 2
                directory = args.output / "private" / assay / cohort
                directory.mkdir(parents=True, exist_ok=True)
                task = dict(
                    zip(
                        ("assay_id", "model_id", "initial_sample_size", "run_seed"),
                        key,
                        strict=True,
                    )
                )
                task.update(
                    cohort=cohort,
                    index=index,
                    sources=sources,
                    prepared=str(directory / f"orders-{index:03d}.npz"),
                )
                tasks.append(task)
        with mp.get_context("spawn").Pool(
            1, initializer=initialize, initargs=(table.candidate_ids,)
        ) as pool:
            for task in tasks:
                locked = pool.apply(prepare, (task,))
                task["locked"] = locked
        all_tasks.extend(tasks)
        event(args.output, "assay_worker_exited", assay_id=assay, states=len(tasks))
    expected_count = 9 if args.mode == "smoke" else 240
    if len(all_tasks) != expected_count:
        raise ValueError("prepared state coverage mismatch")
    event(args.output, "all_orders_locked_all_workers_exited", states=len(all_tasks))
    rows = []
    for assay, (item, table) in tables.items():
        event(args.output, "evaluator_constructed", assay_id=assay)
        evaluator = e3.Evaluator(root, item, table)
        # Existing identity-bound memberships stay entirely inside this evaluator phase.
        high_all = evaluator.truth._M5CTruthEvaluator__memberships[0.01]
        initialize(table.candidate_ids)
        for task in (t for t in all_tasks if t["assay_id"] == assay):
            key = rowkey(task)
            pair = load_pair(task)
            if digest(task["prepared"]) != task["locked"]["prepared_sha256"]:
                raise ValueError("locked order hash mismatch")
            with np.load(task["prepared"], allow_pickle=False) as d:
                orders = d["old_order"], d["new_order"]
            if not all(np.array_equal(orders[i], pair[i][2]) for i in (0, 1)):
                raise ValueError("locked order mismatch")
            metrics = region_metrics(pair[0][1], pair[1][1], high_all[pair[0][0]], *orders)
            prior = references[task["cohort"]][key]
            for version in ("old", "new"):
                if task["cohort"] == "original":
                    auc_expected = prior[version + "_diagnostics"]["top1_auc"]
                    hits_expected = prior[version]["branches"]["greedy"]["high_value"]["0.01"][
                        "count"
                    ]
                else:
                    auc_expected, hits_expected = (
                        prior[version]["top1_auc"],
                        prior[version]["hits_16"],
                    )
                    if (
                        len(pair[0][0]) != prior["remaining_count"]
                        or int(high_all[pair[0][0]].sum()) != prior["remaining_high"]
                    ):
                        raise ValueError("replication membership count mismatch")
                if (
                    abs(metrics[version]["all_auc"] - auc_expected) > 1e-12
                    or metrics[version]["hits16"] != hits_expected
                ):
                    raise ValueError("historical primary endpoint mismatch")
            row = {
                k: task[k]
                for k in (
                    "assay_id",
                    "model_id",
                    "initial_sample_size",
                    "run_seed",
                    "cohort",
                    "index",
                )
            }
            row.update(metrics)
            row["high_denominator"] = int(high_all.sum())
            row["source_identity"] = task["locked"]
            rows.append(row)
        del evaluator, high_all
        event(args.output, "assay_scored", assay_id=assay, completed_states=len(rows))
    result = {
        "protocol": PROTOCOL,
        "mode": args.mode,
        "rows": rows,
        "completed_states": len(rows),
        "new_fits": 0,
        "new_labels": 0,
        "new_states": 0,
        "historical_endpoints": "PASS",
        "limitations": [
            "development landscapes",
            "prediction-defined conditional regions",
            "not upstream causal identification",
            "no equivalence claim",
        ],
    }
    with (args.output / "result.json.gz").open("xb") as handle:
        handle.write(
            gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0)
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "previous", "output", "plan"):
        p.add_argument("--" + name, type=Path)
    p.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    p.add_argument("--wall-seconds", type=int, default=120)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        selftest()
        return
    if any(getattr(args, k) is None for k in ("study", "previous", "output", "plan")):
        p.error("study, previous, output and plan required")
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    signal.signal(
        signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("wall cap exceeded"))
    )
    signal.alarm(args.wall_seconds)
    signal.signal(
        signal.SIGTERM,
        lambda *_: (_ for _ in ()).throw(RuntimeError("resource cap or termination")),
    )
    event(args.output, "started", mode=args.mode)
    try:
        run(args)
    except BaseException as error:
        write(
            args.output / "terminal.json",
            {
                "status": "failed",
                "failure_type": type(error).__name__,
                "reason": str(error),
                "wall_seconds": time.monotonic() - started,
                **RESOURCES,
            },
        )
        raise
    else:
        output_bytes = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
        write(
            args.output / "terminal.json",
            {
                "status": "succeeded" if output_bytes <= 256 * 1024**2 else "stopped",
                "wall_seconds": time.monotonic() - started,
                "output_bytes": output_bytes,
                **RESOURCES,
                "result_sha256": digest(args.output / "result.json.gz"),
            },
        )
    finally:
        signal.alarm(0)
        STOP_MONITOR.set()


if __name__ == "__main__":
    main()
