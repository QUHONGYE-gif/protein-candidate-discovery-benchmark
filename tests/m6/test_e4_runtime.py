"""运行日志的新进程回读、泄漏/漂移拒绝与固定矩阵验证。"""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pcdb.m6.policies import Scores
from pcdb.replay._m6_engine import Action, Costs, State, replay

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "scripts/m6_e4_run.py"


def test_event_callback_contains_only_paid_reveals_and_stops_on_failure() -> None:
    events = []

    def persist(event: Any) -> None:
        events.append(event)
        raise OSError("write failed")

    with pytest.raises(OSError, match="write failed"):
        replay(
            {"a": 2.0, "b": 9.0},
            {"a": 1.0, "b": 3.0},
            Costs(1, 4),
            8,
            lambda state: (Action("b", "high"),),
            initial_high=("a",),
            on_event=persist,
        )
    assert len(events) == 1
    assert events[0].action.candidate_id == "a"
    assert events[0].spent == 4


def test_fixed_matrix_retains_five_seeds_and_baseline_references() -> None:
    rt = runpy.run_path(str(RUNTIME))
    op = json.loads((ROOT / "configs/experiments/m6/e4-operation-v3.json").read_bytes())
    keys = rt["matrix"](op["assays"], op["seeds"])
    assert len(keys) == len(set(keys)) == 260
    assert sum(k[3] == "hf_only" for k in keys) == 20
    assert op["logical_comparisons"] == 320
    assert op["direction_consistency_required"] == 4
    assert op["wall_budget_seconds"] == 76 * 3600


@pytest.mark.parametrize("strategy", ["hf_only", "fixed_ratio", "hard_prescreen", "cost_aware"])
def test_saved_scores_replay_in_fresh_process_and_reject_drift(
    tmp_path: Path, strategy: str
) -> None:
    rt = runpy.run_path(str(RUNTIME))
    inputs = tmp_path / "inputs" / "synthetic"
    inputs.mkdir(parents=True)
    (tmp_path / "trajectories").mkdir()
    ids = tuple(sorted(hashlib.sha256(str(i).encode()).hexdigest() for i in range(20)))
    truth = {c: float(i) for i, c in enumerate(ids)}
    rt["write"](inputs / "ids.json", ids)
    rt["write"](inputs / "hf.json", truth)
    rt["write"](inputs / "1729-C0.json", {"values": {c: -v for c, v in truth.items()}})
    np.save(inputs / "features.npy", np.zeros((20, 420)), allow_pickle=False)

    class ObservedOnlyFixture:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __call__(self, state: State) -> Scores:
            assert not hasattr(state, "truth")
            return Scores(dict.fromkeys(ids, 0.0), dict.fromkeys(ids, 1.0), dict.fromkeys(ids, 0.1))

    operation = {
        "member_jobs": 2,
        "wall_budget_seconds": 60,
        "peak_rss_bytes": 2**40,
        "artifact_bytes": 2**30,
    }
    import time

    limits = rt["Limits"](tmp_path, operation, time.monotonic())
    rt["run_trajectory"].__globals__["RFScorer"] = ObservedOnlyFixture
    key = ("synthetic", 1729, "HF" if strategy == "hf_only" else "C0", strategy)
    rt["run_trajectory"](tmp_path, key, limits, operation)
    code = """import runpy,time,json,sys
from pathlib import Path
r=runpy.run_path(sys.argv[1]);out=Path(sys.argv[2]);key=tuple(json.loads(sys.argv[3]))
op=json.loads(sys.argv[4]);r['run_trajectory'](out,key,r['Limits'](out,op,time.monotonic()),op,True)
"""
    command = [
        sys.executable,
        "-c",
        code,
        str(RUNTIME),
        str(tmp_path),
        json.dumps(key),
        json.dumps(operation),
    ]
    subprocess.run(command, check=True, capture_output=True)
    dest = tmp_path / "trajectories" / "-".join(map(str, key))
    events = (dest / "events.jsonl").read_bytes()
    first, *tail = events.splitlines(keepends=True)
    changed = json.loads(first)
    changed["label"] += 1
    (dest / "events.jsonl").write_bytes(rt["canonical"](changed) + b"".join(tail))
    failed = subprocess.run(command, capture_output=True)
    assert failed.returncode != 0
    assert b"event/decision readback mismatch" in failed.stderr
    (dest / "events.jsonl").write_bytes(events)
    scores = (dest / "scores.jsonl").read_bytes()
    if scores:
        first, *tail = scores.splitlines(keepends=True)
        changed = json.loads(first)
        changed["state_sha256"] = "0" * 64
        (dest / "scores.jsonl").write_bytes(rt["canonical"](changed) + b"".join(tail))
        failed = subprocess.run(command, capture_output=True)
        assert failed.returncode != 0
        assert b"score State identity mismatch" in failed.stderr
    with pytest.raises(FileExistsError):
        rt["run_trajectory"](tmp_path, key, limits, operation)


def test_prepare_pins_inputs_and_rejects_truth_hash_drift(tmp_path: Path) -> None:
    import csv
    import gzip
    import time

    rt = runpy.run_path(str(RUNTIME))
    audit_source = ROOT / "docs/results/m6-e4-proteingym-inputs-20260913/audit.py"
    audit = runpy.run_path(str(audit_source))
    data = tmp_path / "data"
    receipts = []
    for task in audit["ASSAYS"]:
        folder = data / "processed" / task / "v1"
        folder.mkdir(parents=True)
        records = [hashlib.sha256(f"{task}:{i}".encode()).hexdigest() for i in range(256)]
        for name, columns, rows in (
            (
                "candidates.csv.gz",
                ["candidate_id", "mutant", "mutated_sequence"],
                [[c, f"A{i}C", "ACDEFGHIKLMNPQRSTVWY"] for i, c in enumerate(records)],
            ),
            (
                "membership.csv.gz",
                ["candidate_id", "partition"],
                [[c, "candidate-pool"] for c in records],
            ),
            (
                "hidden-truth.csv.gz",
                ["candidate_id", "DMS_score"],
                [[c, str(i)] for i, c in enumerate(records)],
            ),
        ):
            with gzip.open(folder / name, "wt", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows(rows)

        def entry(name: str, folder: Path = folder) -> dict[str, Any]:
            path = folder / name
            return dict(
                object_key=str(path.relative_to(data)),
                sha256=rt["sha"](path),
                size_bytes=path.stat().st_size,
                row_count=256,
            )

        dataset = data / "manifests" / task / "dataset-manifest-v1.json"
        dataset.parent.mkdir(parents=True)
        rt["write"](dataset, {"fixture": task})
        processing = dict(
            dataset_manifest_sha256=rt["sha"](dataset),
            label_direction="higher_is_better",
            observable_feature_fields=["mutant", "mutated_sequence"],
            output_files=[entry("candidates.csv.gz"), entry("hidden-truth.csv.gz")],
        )
        rt["write"](folder / "processing-manifest.json", processing)
        split = dict(
            processing_manifest_sha256=rt["sha"](folder / "processing-manifest.json"),
            partitions=["candidate-pool"],
            held_out_partitions=[],
            membership_file=entry("membership.csv.gz"),
        )
        rt["write"](folder / "split-manifest.json", split)
        receipts.append(
            dict(
                assay_id=task,
                candidate_count=256,
                dataset_manifest_sha256=rt["sha"](dataset),
                processing_manifest_sha256=rt["sha"](folder / "processing-manifest.json"),
                split_manifest_sha256=rt["sha"](folder / "split-manifest.json"),
            )
        )
    (data / "evidence").mkdir()
    rt["write"](data / "evidence/unified-attempt.json", {"assays": receipts})
    frozen = tmp_path / "audit"
    frozen.mkdir()
    (frozen / "audit.py").write_bytes(audit_source.read_bytes())
    rt["write"](frozen / "report.json", audit["audit"](data))
    rt["prepare"].__globals__["AUDIT"] = frozen
    operation = {
        "seeds": [1729],
        "wall_budget_seconds": 120,
        "peak_rss_bytes": 2**40,
        "artifact_bytes": 2**30,
    }
    output = tmp_path / "study"
    output.mkdir()
    rt["prepare"](data, output, operation, rt["Limits"](output, operation, time.monotonic()))
    rt["validate_inputs"](output)
    task = audit["ASSAYS"][0]
    assert np.load(output / "inputs" / task / "features.npy").shape == (256, 420)
    assert rt["read"](output / "inputs" / task / "1729-C0.json") == rt["read"](
        output / "inputs" / task / "1729-C3.json"
    )
    truth = data / "processed" / task / "v1/hidden-truth.csv.gz"
    truth.write_bytes(truth.read_bytes() + b"drift")
    failed = tmp_path / "failed"
    failed.mkdir()
    with pytest.raises(ValueError, match="HF bytes mismatch"):
        rt["prepare"](data, failed, operation, rt["Limits"](failed, operation, time.monotonic()))
    assert not list(failed.rglob("hf.json"))
    feature = output / "inputs" / task / "features.npy"
    feature.write_bytes(feature.read_bytes() + b"drift")
    with pytest.raises(ValueError, match="input bytes drifted"):
        rt["validate_inputs"](output)


def test_explicit_logical_index_and_four_of_five_direction() -> None:
    rt = runpy.run_path(str(RUNTIME))
    tasks, seeds = ["t1", "t2", "t3", "t4"], list(range(1729, 1734))
    index = rt["logical_index"](tasks, seeds)
    assert len(index) == 320
    assert len({r["trajectory"] for r in index}) == 260
    assert len({r["baseline"] for r in index}) == 20
    evaluations: dict[tuple[str, int, str, str], Any] = {}
    for key in rt["matrix"](tasks, seeds):
        _, seed, condition, strategy = key
        recall = 0.5
        if strategy == "cost_aware" and seed < 1733:
            recall = 0.8 if condition == "C0" else 0.2 if condition == "C2" else 0.5
        evaluations[key] = {
            "0.01": {"checkpoints": {"6400": {"recall": recall}}, "cost_to_target": None}
        }
    result = rt["summary"](evaluations, tasks, seeds)
    assert result["verdict"] == "SUPPORTS_BOUNDED_HYPOTHESIS"
    assert all(r["supports"] for r in result["mechanisms"])
    # 3/5同向不满足用户选择的4/5，不能因mean跨门槛而宣称支持。
    evaluations[("t1", 1732, "C0", "cost_aware")]["0.01"]["checkpoints"]["6400"]["recall"] = 0.5
    result = rt["summary"](evaluations, tasks, seeds)
    assert result["verdict"] == "UNSTABLE / REVISE"
    assert not result["mechanisms"][0]["supports"]
