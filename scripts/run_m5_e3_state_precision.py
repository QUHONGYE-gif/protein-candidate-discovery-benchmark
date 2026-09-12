"""Fixed-input new-state batch for the sparse-hit and state-precision diagnosis."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import heapq
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

PROTOCOL = "m5-e3-state-precision-development-v1"
SEEDS = tuple(range(4000, 4060))
MODEL = "xgboost-cpu-v1"
KS = (8, 16, 32, 64)
HELPER_SHA = "92e384e1972f03144706000b84ef900a909127651478593692baa4207f1be0f1"
RUNTIME_SHA = "75504878c51f03590329b3e5cdb92e47f7c1be8191bec63c5c33af69763dfce0"
INPUT_RESULT_SHA = "937febfe04c7f45ff490249fdce1f33b0944d54b7a9a9a9939f3a0776d51eb08"
FRONTIER_SHA = "67e98445f6c4fe3f6fd46a966b2af321122ea4c1cc8371a7eac81fef164715ba"
WORKER = None


def initial(ids, assay, seed, size):
    """Keep the original hash-ranking rule; do not change frozen seed validators."""
    if assay not in e3.ASSAY_IDS or seed not in (1729, *SEEDS) or size not in (32, 64):
        raise ValueError("unlisted development state")
    if len(set(ids)) != len(ids) or len(ids) < size:
        raise ValueError("candidate identity mismatch")
    ranked = heapq.nsmallest(
        size,
        (
            (
                hashlib.sha256(
                    json.dumps(
                        [e3.rt.PROTOCOL_ID, assay, seed, cid],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).digest(),
                cid,
            )
            for cid in ids
        ),
    )
    return tuple(cid for _, cid in ranked)


def initialize(assay, ids, feature_path, feature_sha, output):
    global WORKER
    x = np.load(feature_path, mmap_mode="r", allow_pickle=False)
    if (
        assay not in e3.ASSAY_IDS
        or x.shape != (len(ids), 421)
        or e3.digest(feature_path) != feature_sha
        or not np.isfinite(x).all()
    ):
        raise ValueError("observable feature identity mismatch")
    WORKER = assay, tuple(ids), x, feature_sha, Path(output)


def fit_select(task):
    assay, ids, x, feature_sha, output = WORKER
    index, size, seed, observations, snapshots = task
    observed = {o.candidate_id: float(o.label) for o in observations}
    chosen = initial(ids, assay, seed, size)
    if len(observed) != len(observations) or len(observed) != size or set(observed) != set(chosen):
        raise ValueError("allowed-label binding mismatch")
    lookup = {cid: i for i, cid in enumerate(ids)}
    ordered = sorted(observed)
    train = np.array([lookup[c] for c in ordered])
    y = np.array([observed[c] for c in ordered])
    remaining = np.array([i for i, cid in enumerate(ids) if cid not in observed])
    remaining_ids = np.array([ids[i] for i in remaining])
    predictions, selections = {}, {}
    for arm, width in (("old", 420), ("new", 421)):
        estimator = e3.rt._fit_model(MODEL, x[train, :width], y)
        mean = np.empty(len(remaining))
        for start in range(0, len(remaining), 384):
            block = remaining[start : start + 384]
            mean[start : start + len(block)] = e3.rt._predict(estimator, x[block, :width])
        if not np.isfinite(mean).all():
            raise ValueError("nonfinite prediction")
        if snapshots:
            path, sha = snapshots[arm]
            if e3.digest(path) != sha:
                raise ValueError("historical prediction hash mismatch")
            with np.load(path, allow_pickle=False) as old:
                if not np.array_equal(remaining, old["remaining_indices"]) or not np.array_equal(
                    mean, old["mean"]
                ):
                    raise ValueError("point prediction does not exactly reconstruct")
        predictions[arm] = mean
        selections[arm] = list(e3.rt._top_candidate_ids(remaining_ids, mean, 64))
    path = output / f"prediction-{index:03d}.npz"
    with path.open("xb") as handle:
        np.savez(handle, remaining_indices=remaining, **predictions)
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "assay_id": assay,
            "initial_sample_size": size,
            "run_seed": seed,
            "initial_ids": list(chosen),
            "allowed_observation_sha256": e3.identity(list(zip(ordered, y.tolist(), strict=True))),
            "feature_sha256": feature_sha,
            "prediction_sha256": e3.digest(path),
            "selections": selections,
            "historical_point_reconstruction": "PASS" if snapshots else "not_applicable",
        },
    )
    return index


def interval(values, count):
    cells = np.asarray(values, dtype=float).reshape(-1, count)
    by_seed = cells.mean(axis=0)
    slots = np.random.Generator(np.random.PCG64(20001)).integers(0, count, (20000, count))
    draws = by_seed[slots].mean(axis=1)
    return {
        "estimate": float(by_seed.mean()),
        "interval": np.percentile(draws, [0.3125, 99.6875]).tolist(),
        "seed": 20001,
        "draws": 20000,
        "coverage": 0.99375,
    }


def summarize(rows):
    output = []
    for assay in e3.ASSAY_IDS:
        for size in (32, 64, "combined"):
            selected = sorted(
                [
                    r
                    for r in rows
                    if r["assay_id"] == assay
                    and (size == "combined" or r["initial_sample_size"] == size)
                ],
                key=lambda r: (r["initial_sample_size"], r["run_seed"]),
            )
            wanted = [(n, s) for n in ((32, 64) if size == "combined" else (size,)) for s in SEEDS]
            if [(r["initial_sample_size"], r["run_seed"]) for r in selected] != wanted:
                raise ValueError("new-seed matrix mismatch")
            metrics = {}
            for metric in selected[0]["old"]:
                pairs = [(r["old"][metric], r["new"][metric]) for r in selected]
                defined = sum(a is not None and b is not None for a, b in pairs)
                metrics[metric] = {
                    "defined_pairs": defined,
                    "old": None,
                    "new": None,
                    "change": None,
                }
                if defined == len(selected):
                    a, b = np.asarray(pairs).T
                    metrics[metric].update(
                        old=float(a.mean()), new=float(b.mean()), change=interval(b - a, len(SEEDS))
                    )
            output.append(
                {
                    "assay_id": assay,
                    "model_id": MODEL,
                    "initial_sample_size": size,
                    "states": len(selected),
                    "seed_slots": len(SEEDS),
                    "metrics": metrics,
                }
            )
    return output


def selftest():
    ids = tuple(f"{i:064x}" for i in range(96))
    assay = e3.ASSAY_IDS[0]
    for n in (32, 64):
        assert initial(ids, assay, 1729, n) == e3.initial_selection(
            ids, assay_id=assay, run_seed=1729, size=n, assay_ids=e3.ASSAY_IDS
        )
    assert initial(ids, assay, 4000, 32) == initial(ids, assay, 4000, 64)[:32]
    assert initial(ids, assay, 4000, 64) == initial(ids[::-1], assay, 4000, 64)
    assert interval([0.0] * 20 + [2.0] * 20, 20)["interval"] == [1, 1]
    assert not set(SEEDS) & set(e3.RUN_SEEDS)


def run(args):
    selftest()
    for path, expected in (
        (e3.__file__, HELPER_SHA),
        (e3.rt.__file__, RUNTIME_SHA),
        (args.previous / "full-001/result.json.gz", INPUT_RESULT_SHA),
        (args.previous / "frontier-full-001/result.json.gz", FRONTIER_SHA),
    ):
        if e3.digest(path) != expected:
            raise ValueError("historical source/input identity mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("environment mismatch")
    source = json.loads(gzip.decompress((args.previous / "full-001/result.json.gz").read_bytes()))
    prior = json.loads(
        gzip.decompress((args.previous / "frontier-full-001/result.json.gz").read_bytes())
    )
    feature_refs = {r["assay_id"]: r for r in source["inputs"]}
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    if (
        tuple(x["assay_id"] for x in manifest["assays"]) != e3.ASSAY_IDS
        or manifest["study_id"] != e3.STUDY_ID
    ):
        raise ValueError("assay scope mismatch")
    seeds = (1729,) if args.mode == "smoke" else SEEDS
    specs = [(n, s) for n in (32, 64) for s in seeds]
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
            "input_result_sha256": INPUT_RESULT_SHA,
            "frontier_sha256": FRONTIER_SHA,
            "versions": versions,
            "parameters": dict(e3.rt.model_effective_parameters(MODEL)),
            "state_specs": specs,
            "selection_sizes": list(KS),
            "input_identities": source["inputs"],
            "truth_boundary": (
                "candidate-pool evaluator reveals fixed allowed observations; spawn workers save "
                "all selections and exit before scoring; not OS isolation"
            ),
            "evidence_stage": "ITERATION",
            "application_delta": None,
        },
    )
    rows = []
    for item in manifest["assays"]:
        assay, artifacts = item["assay_id"], item["artifacts"]
        if (
            item["partition"] != "candidate-pool"
            or item["held_out_partitions"]
            or item["candidate_count"] != e3.COUNTS[assay]
            or item["label_direction"] != "higher_is_better"
            or item["label_field"] != "DMS_score"
            or item["sample_sequence_reference_check"] != "PASS"
            or item["id_coverage_check"] != "PASS"
        ):
            raise ValueError("sample/label scope mismatch")
        ref = artifacts["candidate"]
        table = e3.rt.load_candidate_table(
            root / ref["path"],
            assay_id=assay,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        if table.sha256 != feature_refs[assay]["candidate_sha256"]:
            raise ValueError("feature row binding mismatch")
        ref = artifacts["membership"]
        e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
        featurepath = args.previous / "full-001/private" / assay / "features.npy"
        original = (
            args.study
            / ("runs-001" if assay == e3.ASSAY_IDS[0] else "remaining-runs-001")
            / assay
            / "full-001/private/features.npy"
        )
        if (
            e3.digest(original) != e3.FEATURE_SHAS[assay]
            or e3.digest(featurepath) != feature_refs[assay]["new_feature_sha256"]
        ):
            raise ValueError("feature identity mismatch")
        if not np.array_equal(
            np.load(original, mmap_mode="r"), np.load(featurepath, mmap_mode="r")[:, :420]
        ):
            raise ValueError("baseline feature columns changed")
        private = args.output / "private" / assay
        private.mkdir(parents=True)
        evaluator = e3.Evaluator(root, item, table)
        initargs = (
            assay,
            table.candidate_ids,
            str(featurepath),
            feature_refs[assay]["new_feature_sha256"],
            str(private),
        )
        with mp.get_context("spawn").Pool(1, initializer=initialize, initargs=initargs) as pool:
            for index, (size, seed) in enumerate(specs):
                chosen = initial(table.candidate_ids, assay, seed, size)
                if chosen != initial(table.candidate_ids, assay, seed, 64)[:size]:
                    raise ValueError("initial nesting mismatch")
                snapshots = None
                if args.mode == "smoke":
                    oi = e3.SPECS.index((MODEL, size, seed))
                    historical = next(
                        r
                        for r in prior["rows"]
                        if (r["assay_id"], r["model_id"], r["initial_sample_size"], r["run_seed"])
                        == (assay, MODEL, size, seed)
                    )
                    hashes = historical["source_identity"]["source_prediction_shas"]
                    snapshots = {
                        "old": (
                            str(
                                args.study
                                / "model-diagnosis-001/private"
                                / assay
                                / f"prediction-{oi:03d}.npz"
                            ),
                            hashes[0],
                        ),
                        "new": (
                            str(
                                args.previous
                                / "full-001/private"
                                / assay
                                / f"prediction-{oi:03d}.npz"
                            ),
                            hashes[1],
                        ),
                    }
                if (
                    pool.apply(
                        fit_select,
                        ((index, size, seed, evaluator.truth.reveal(chosen), snapshots),),
                    )
                    != index
                ):
                    raise ValueError("worker return mismatch")
                e3.event(args.output, "predictions_saved", assay_id=assay, index=index)
        e3.event(args.output, "workers_exited", assay_id=assay)
        truth = evaluator.truth._M5CTruthEvaluator__truth
        high = evaluator.truth._M5CTruthEvaluator__memberships[0.01]
        for index, (size, seed) in enumerate(specs):
            meta = json.loads((private / f"selection-{index:03d}.json").read_text())
            path = private / f"prediction-{index:03d}.npz"
            if e3.digest(path) != meta["prediction_sha256"] or meta["initial_ids"] != list(
                initial(table.candidate_ids, assay, seed, size)
            ):
                raise ValueError("saved state mismatch")
            with np.load(path, allow_pickle=False) as saved:
                remaining = saved["remaining_indices"]
                expected = [
                    i
                    for i, cid in enumerate(table.candidate_ids)
                    if cid not in set(meta["initial_ids"])
                ]
                if remaining.tolist() != expected:
                    raise ValueError("evaluation pool mismatch")
                ids = np.array([table.candidate_ids[i] for i in remaining])
                lookup = {cid: i for i, cid in enumerate(table.candidate_ids)}
                record = {
                    "assay_id": assay,
                    "model_id": MODEL,
                    "initial_sample_size": size,
                    "run_seed": seed,
                    "remaining_count": len(remaining),
                    "remaining_high": int(high[remaining].sum()),
                    "high_denominator": int(high.sum()),
                    "prediction_sha256": meta["prediction_sha256"],
                    "allowed_observation_sha256": meta["allowed_observation_sha256"],
                    "historical_point_reconstruction": meta["historical_point_reconstruction"],
                }
                for arm in ("old", "new"):
                    mean = saved[arm]
                    top = list(e3.rt._top_candidate_ids(ids, mean, 64))
                    if top != meta["selections"][arm] or len(set(top)) != 64:
                        raise ValueError("selection reconstruction mismatch")
                    metric = {
                        "spearman": e3.rho(mean, truth[remaining]),
                        "top1_auc": e3.auc(mean, high[remaining]),
                        "mae": float(np.abs(mean - truth[remaining]).mean()),
                    }
                    for k in KS:
                        if top[:k] != list(e3.rt._top_candidate_ids(ids, mean, k)):
                            raise ValueError("cutoff nesting mismatch")
                        hits = int(sum(high[lookup[c]] for c in top[:k]))
                        metric.update(
                            {
                                f"hits_{k}": hits,
                                f"recall_{k}": hits / int(high.sum()),
                                f"precision_{k}": hits / k,
                            }
                        )
                    record[arm] = metric
                rows.append(record)
        e3.event(args.output, "assay_scored", assay_id=assay, states=len(rows))
    result = {
        "protocol": PROTOCOL,
        "mode": args.mode,
        "completed_states": len(rows),
        "point_fits": len(rows) * 2,
        "new_measurements": 0,
        "new_seed_states": args.mode == "full",
        "rows": rows,
        "summaries": summarize(rows) if args.mode == "full" else [],
        "limits": [
            "existing development assays",
            "not independent data validation",
            "no mechanism attribution",
            "no E/UQ claim",
            "no pooled task CI",
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
    parser.add_argument("--wall-seconds", type=int, default=800)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        print("replication pairing/initial-prefix self-test PASS")
        return 0
    if (
        any(getattr(args, k) is None for k in ("study", "previous", "output", "plan"))
        or not 0 < args.wall_seconds <= 900
    ):
        parser.error("all paths and a wall cap in (0,900] required")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    started, stop, breach = time.monotonic(), threading.Event(), []
    usage = {"peak_rss_bytes": 0, "peak_disk_bytes": 0}

    def terminate(_signum, _frame):
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
