"""Runtime-boundary tests for the formal M5C slice."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from pcdb.contracts import ContractValidationError
from pcdb.m5.proteingym_confirmation import ASSAY_IDS, HIGH_VALUE_FRACTIONS
from pcdb.m5.proteingym_runtime import (
    M5CTruthEvaluator,
    StateSelection,
    build_assay_aggregate,
    build_cohort_report,
    build_feature_artifact,
    build_run_identity,
    evaluate_state,
    load_candidate_table,
    load_static_proxy,
    select_state,
    verify_assay_aggregate,
    write_once,
)

ROOT = Path(__file__).resolve().parents[2]


def _candidate_id(assay_id: str, mutant: str) -> str:
    return hashlib.sha256(f"proteingym:v1.3:{assay_id}\0{mutant}".encode()).hexdigest()


def _gzip_csv(fieldnames: tuple[str, ...], rows: list[dict[str, object]]) -> bytes:
    stream = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed:
        text = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
        writer = csv.DictWriter(text, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        text.flush()
    return stream.getvalue()


def test_candidate_and_proxy_readers_exact_bind_without_using_truth_columns(tmp_path: Path) -> None:
    assay_id = ASSAY_IDS[0]
    rows: list[dict[str, object]] = [
        {
            "candidate_id": _candidate_id(assay_id, f"A{index}V"),
            "mutant": f"A{index}V",
            "mutated_sequence": "ACDE",
        }
        for index in range(1, 4)
    ]
    candidate_payload = _gzip_csv(
        ("candidate_id", "mutant", "mutated_sequence"),
        rows,
    )
    candidate_path = tmp_path / "candidates.csv.gz"
    candidate_path.write_bytes(candidate_payload)
    table = load_candidate_table(
        candidate_path,
        assay_id=assay_id,
        expected_sha256=hashlib.sha256(candidate_payload).hexdigest(),
        expected_size=len(candidate_payload),
    )
    assert table.candidate_ids == tuple(row["candidate_id"] for row in rows)

    proxy_path = tmp_path / "scores.csv"
    proxy_path.write_text(
        "mutant,DMS_score,DMS_score_bin,ESM1v_single\n"
        "A1V,999,1,0.1\nA2V,-999,0,0.2\nA3V,123,1,0.3\n",
        encoding="utf-8",
    )
    proxy = load_static_proxy(
        proxy_path,
        candidate_table=table,
        expected_sha256=hashlib.sha256(proxy_path.read_bytes()).hexdigest(),
        expected_size=proxy_path.stat().st_size,
    )
    assert proxy.tolist() == [0.1, 0.2, 0.3]


def test_truth_capability_returns_only_aggregate_branch_and_proxy_results() -> None:
    candidate_ids = tuple(
        hashlib.sha256(f"candidate-{index}".encode()).hexdigest() for index in range(100)
    )
    truth = np.asarray(range(100), dtype=np.float64)
    evaluator = M5CTruthEvaluator(candidate_ids, truth)
    prefix = candidate_ids[:32]
    selected = candidate_ids[-16:]

    revealed = evaluator.reveal(prefix)
    assert tuple(item.candidate_id for item in revealed) == prefix
    branch = evaluator.evaluate_batch(prefix, selected)
    branch_high_value = cast(dict[str, object], branch["high_value"])
    assert set(branch_high_value) == {"0.005", "0.01", "0.02"}
    assert "candidate_id" not in json.dumps(branch)

    proxy = evaluator.evaluate_static_proxy(np.asarray(range(100), dtype=np.float64))
    assert proxy["spearman"] == 1.0
    proxy_high_value = cast(dict[str, object], proxy["high_value_recall"])
    assert set(proxy_high_value) == {"0.005", "0.01", "0.02"}
    assert evaluator.definition_summary["0.01"]["nominal_count"] == 1


def test_run_identity_is_content_derived_and_attempt_root_is_server_first() -> None:
    payload = build_run_identity(
        attempt_id="attempt-001",
        contract_digest="1" * 64,
        input_manifest_digest="2" * 64,
        git_commit="3" * 40,
        git_tree="4" * 40,
        environment_digest="5" * 64,
    )
    value = json.loads(payload)
    assert value["attempt_root"] == "/srv/pcdb/runs/M5C/attempt-001"
    assert value["cos_write_count"] == 0
    assert value["observed_usage"] == {
        "cost": 0,
        "cpu_seconds": 0,
        "network_bytes": 0,
        "peak_rss_bytes": 0,
        "scratch_bytes": 0,
        "wall_seconds": 0,
    }
    identity = dict(value)
    run_id = identity.pop("run_id")
    expected = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    assert run_id == expected

    with pytest.raises(ContractValidationError, match="attempt"):
        build_run_identity(
            attempt_id="../escape",
            contract_digest="1" * 64,
            input_manifest_digest="2" * 64,
            git_commit="3" * 40,
            git_tree="4" * 40,
            environment_digest="5" * 64,
        )


def test_write_once_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    write_once(path, b"first")
    with pytest.raises(FileExistsError):
        write_once(path, b"second")
    assert path.read_bytes() == b"first"


def test_truth_fractions_remain_the_frozen_set() -> None:
    assert HIGH_VALUE_FRACTIONS == (0.005, 0.01, 0.02)


def test_feature_artifact_and_state_selection_are_content_bound(tmp_path: Path) -> None:
    assay_id = ASSAY_IDS[0]
    candidate_ids = tuple(
        hashlib.sha256(f"selection-{index}".encode()).hexdigest() for index in range(96)
    )
    sequences = tuple("ACDEFGHIKLMNPQRSTVWY" for _ in candidate_ids)
    artifact = build_feature_artifact(sequences, tmp_path / "features.npy")
    assert artifact.shape == (96, 420)
    assert artifact.size_bytes == artifact.path.stat().st_size

    truth = np.linspace(-1, 1, len(candidate_ids), dtype=np.float64)
    evaluator = M5CTruthEvaluator(candidate_ids, truth)
    selection = select_state(
        assay_id=assay_id,
        candidate_ids=candidate_ids,
        candidate_pool_sha256="a" * 64,
        features=np.load(artifact.path, mmap_mode="r"),
        feature_sha256=artifact.sha256,
        initial_sample_size=32,
        model_id="random-forest-sklearn-v1",
        observations=evaluator.reveal(
            __import__(
                "pcdb.m5.proteingym_confirmation", fromlist=["initial_selection"]
            ).initial_selection(candidate_ids, assay_id=assay_id, run_seed=1729, size=32)
        ),
        run_seed=1729,
    )
    assert len(selection.initial_ids) == 32
    assert len(selection.greedy_ids) == len(selection.exploration_ids) == 16
    assert len(selection.matched_ids) == 64
    assert all(len(batch) == 16 for batch in selection.matched_ids)
    assert set(selection.initial_ids).isdisjoint(selection.greedy_ids)


def test_state_evaluation_and_assay_report_are_aggregate_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_ids = tuple(
        hashlib.sha256(f"report-{index}".encode()).hexdigest() for index in range(80)
    )
    evaluator = M5CTruthEvaluator(candidate_ids, np.arange(80, dtype=np.float64))
    states: list[dict[str, object]] = []
    state_effects = []
    for model_id in ("random-forest-sklearn-v1", "xgboost-cpu-v1"):
        for initial_size in (32, 64):
            for seed in range(1729, 1739):
                prefix = candidate_ids[:initial_size]
                remaining = candidate_ids[initial_size:]
                greedy = remaining[:16]
                exploration = remaining[-16:]
                selection = StateSelection(
                    assay_id=ASSAY_IDS[0],
                    exploration_ids=exploration,
                    greedy_ids=greedy,
                    initial_ids=prefix,
                    initial_sample_size=initial_size,
                    matched_ids=tuple(greedy for _ in range(64)),
                    model_id=model_id,
                    run_seed=seed,
                    state_id=hashlib.sha256(
                        f"{model_id}-{initial_size}-{seed}".encode()
                    ).hexdigest(),
                )
                aggregate, effect = evaluate_state(selection, evaluator)
                states.append(aggregate)
                state_effects.append(effect)
    import pcdb.m5.proteingym_confirmation as confirmation

    monkeypatch.setattr(confirmation, "BOOTSTRAP_DRAWS", 128)
    report = build_assay_aggregate(
        assay_id=ASSAY_IDS[0],
        candidate_count=80,
        evaluator=evaluator,
        state_aggregates=states,
        state_effects=state_effects,
        static_proxy={"defined": False, "high_value_recall": {}, "spearman": None},
    )
    encoded = json.dumps(report, allow_nan=False, sort_keys=True)
    assert report["complete_state_count"] == 40
    assert report["taxonomy"] in {
        "BEHAVIOR_REDUNDANCY",
        "CONDITIONAL_POSITIVE",
        "FRAGILE_INCONCLUSIVE",
        "INCONCLUSIVE",
        "OPPORTUNITY_COST",
        "OTHER_NEGATIVE",
        "UNINFORMATIVE_DEVIATION",
        "UNSTABLE",
    }
    assert report["taxonomy"] != "UNDEFINED_INCONCLUSIVE"
    assert "candidate_id" not in encoded
    assert "initial_ids" not in encoded
    verify_assay_aggregate(report)
    report["taxonomy"] = "VALIDITY_STOP"
    with pytest.raises(ContractValidationError, match="reproduction"):
        verify_assay_aggregate(report)


def test_cohort_report_preserves_assay_first_precedence() -> None:
    reports = [
        {
            "assay_id": assay_id,
            "primary_contrasts": {
                "E-G": {"status": "EQUIVALENT", "point_estimate": 0.0},
                "E-M": {"status": "SUPERIOR", "point_estimate": 0.1},
            },
            "taxonomy": "OPPORTUNITY_COST",
        }
        for assay_id in ASSAY_IDS
    ]
    report = build_cohort_report(reports, attempt_id="attempt-001")
    assert report["cohort_status"] == "REPLICATED_OPPORTUNITY_COST"
    assert report["attempt_selection"] == "first-complete-valid-terminal"
    assert report["label_counts"] == {"OPPORTUNITY_COST": 4}


def test_formal_entry_points_fail_closed_off_target_host(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    main = subprocess.run(
        (
            sys.executable,
            str(ROOT / "scripts" / "run_m5c_proteingym.py"),
            "--attempt-id",
            "attempt-001",
            "--contract-manifest",
            str(tmp_path / "contract.json"),
            "--expected-git-commit",
            "a" * 40,
            "--expected-git-tree",
            "b" * 40,
            "--gate-a",
            str(tmp_path / "gate.json"),
            "--governance-config",
            str(tmp_path / "governance.json"),
            "--input-manifest",
            str(tmp_path / "input.json"),
            "--input-root",
            str(tmp_path),
            "--python",
            sys.executable,
            "--source-root",
            str(ROOT),
        ),
        check=False,
        capture_output=True,
        env=environment,
    )
    assert main.returncode == 2
    assert json.loads(main.stdout)["status"] == "NOT_STARTED"

    review_root = tmp_path / "review-attempt-001"
    review = subprocess.run(
        (
            sys.executable,
            str(ROOT / "scripts" / "reproduce_m5c_proteingym.py"),
            "--aggregate-input",
            str(tmp_path / "aggregate.json"),
            "--aggregate-input-sha256",
            "c" * 64,
            "--expected-git-commit",
            "a" * 40,
            "--expected-git-tree",
            "b" * 40,
            "--output-root",
            str(review_root),
            "--source-root",
            str(ROOT),
        ),
        check=False,
        capture_output=True,
        env=environment,
    )
    assert review.returncode == 2
    assert json.loads(review.stderr)["status"] == "STOPPED"
    assert not review_root.exists()
