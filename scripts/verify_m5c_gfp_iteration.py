#!/usr/bin/env python3
"""Aggregate-only arithmetic checks and reproduction of the existing C bootstrap."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

from pcdb.m5.proteingym_confirmation import (
    INITIAL_SAMPLE_SIZES,
    MODEL_IDS,
    RUN_SEEDS,
    AssayTaxonomyInput,
    StateEffects,
    bootstrap_assay_effects,
    classify_assay,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--connection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = time.monotonic()
    raw = args.aggregate.read_bytes()
    if args.aggregate.suffix == ".gz":
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    states = data["state_aggregates"]
    definitions = data["high_value_definitions"]
    assert data["assay_id"] == "GFP_AEQVI_Sarkisyan_2016"
    assert data["candidate_count"] == 51714
    assert set(definitions) == {"0.005", "0.01", "0.02"}
    for fraction, definition in definitions.items():
        nominal = math.ceil(float(fraction) * data["candidate_count"])
        assert definition["nominal_count"] == nominal
        assert nominal <= definition["actual_count"] <= data["candidate_count"]
        assert definition["boundary_tie_count"] == definition["actual_count"] - nominal
    expected = {(m, n, s) for m in MODEL_IDS for n in INITIAL_SAMPLE_SIZES for s in RUN_SEEDS}
    observed = {(s["model_id"], s["initial_sample_size"], s["run_seed"]) for s in states}
    assert observed == expected and len(states) == 40
    assert len({s["state_id"] for s in states}) == 40
    connection = args.connection.read_bytes()
    if args.connection.suffix == ".gz":
        connection = gzip.decompress(connection)
    assert json.loads(connection) == states[0]
    checked_metrics = 0
    effects = []
    for state in states:
        assert state["assay_id"] == data["assay_id"]
        assert isinstance(state["divergence_count"], int) and 0 <= state["divergence_count"] <= 16
        b = state["branches"]
        assert len(b["matched_random"]) == 64
        for branch in [b["exploration"], b["greedy"], *b["matched_random"]]:
            assert set(branch["high_value"]) == set(definitions)
            assert branch["best_found_improvement"] == branch["simple_regret_reduction"]
            assert (
                math.isfinite(branch["best_found_improvement"])
                and branch["best_found_improvement"] >= 0
            )
            counts = []
            for fraction in ("0.005", "0.01", "0.02"):
                metric = branch["high_value"][fraction]
                count = metric["count"]
                assert type(count) is int and 0 <= count <= 16
                assert metric["recall"] == count / definitions[fraction]["actual_count"]
                counts.append(count)
                checked_metrics += 1
            assert counts == sorted(counts)
        e = b["exploration"]["high_value"]["0.01"]["recall"]
        g = b["greedy"]["high_value"]["0.01"]["recall"]
        effects.append(
            StateEffects(
                assay_id=data["assay_id"],
                exploration_minus_greedy=e - g,
                exploration_minus_matched=tuple(
                    e - m["high_value"]["0.01"]["recall"] for m in b["matched_random"]
                ),
                initial_sample_size=state["initial_sample_size"],
                model_id=state["model_id"],
                run_seed=state["run_seed"],
                state_id=state["state_id"],
            )
        )
    cell_summaries = []
    sensitivity = {}
    for fraction in ("0.005", "0.01", "0.02"):
        cell_points = []
        for model in MODEL_IDS:
            for size in INITIAL_SAMPLE_SIZES:
                group = [
                    s for s in states if s["model_id"] == model and s["initial_sample_size"] == size
                ]
                assert len(group) == 10
                for key in (
                    "model_config_sha256",
                    "acquisition_id",
                    "acquisition_sha256",
                    "greedy_acquisition_sha256",
                ):
                    assert len({s[key] for s in group}) == 1
                n_high = definitions[fraction]["actual_count"]
                points, means = [], []
                for s in group:
                    b = s["branches"]
                    e = b["exploration"]["high_value"][fraction]["count"] / n_high
                    g = b["greedy"]["high_value"][fraction]["count"] / n_high
                    m = (
                        math.fsum(
                            x["high_value"][fraction]["count"] / n_high for x in b["matched_random"]
                        )
                        / 64
                    )
                    points.append({"seed": s["run_seed"], "E-G": e - g, "E-M": e - m})
                    means.append((e, g, m))
                p = {
                    contrast: math.fsum(s[contrast] for s in points) / 10
                    for contrast in ("E-G", "E-M")
                }
                cell_points.append(p)
                if fraction == "0.01":
                    cell_summaries.append(
                        {
                            "model_id": model,
                            "initial_sample_size": size,
                            "mean_recall": {
                                k: math.fsum(v[i] for v in means) / 10
                                for i, k in enumerate(("E", "G", "M"))
                            },
                            "mean_hits": {
                                k: math.fsum(v[i] * n_high for v in means) / 10
                                for i, k in enumerate(("E", "G", "M"))
                            },
                            "effects": p,
                            "seed_effects": points,
                            "mean_divergence": math.fsum(s["divergence_count"] for s in group) / 10,
                        }
                    )
        sensitivity[fraction] = {
            k: math.fsum(p[k] for p in cell_points) / 4 for k in ("E-G", "E-M")
        }
    print(
        (
            "Arithmetic PASS: 40 states, 2640 branches, 7920 recall entries; "
            "reproducing original 20000-draw bootstrap"
        ),
        flush=True,
    )
    summaries = bootstrap_assay_effects(
        effects, assay_id=data["assay_id"], delta=1 / math.ceil(0.01 * data["candidate_count"])
    )
    primary = {x.contrast: asdict(x) for x in summaries}
    for k in primary:
        assert math.isclose(
            primary[k]["point_estimate"], sensitivity["0.01"][k], rel_tol=0, abs_tol=1e-17
        )
    taxonomy = classify_assay(
        AssayTaxonomyInput(
            actual_primary_high_value_count=definitions["0.01"]["actual_count"],
            assay_id=data["assay_id"],
            exploration_greedy_difference_counts=tuple(s["divergence_count"] for s in states),
            frozen_candidate_count=data["candidate_count"],
            primary_e_g=summaries[0],
            primary_e_m=summaries[1],
            required_defined=True,
            validity_ok=True,
            sensitivity_e_g_point_estimates=tuple(sensitivity[f]["E-G"] for f in ("0.005", "0.02")),
            sensitivity_e_m_point_estimates=tuple(sensitivity[f]["E-M"] for f in ("0.005", "0.02")),
        )
    )
    result = {
        "status": "PASS",
        "evidence_stage": "ITERATION / L1 GFP exploration",
        "aggregate_input_sha256": hashlib.sha256(raw).hexdigest(),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checked_states": 40,
        "checked_branches": 2640,
        "checked_recall_entries": checked_metrics,
        "connection_state_matches": True,
        "high_value_definitions": definitions,
        "primary_contrasts": primary,
        "sensitivity_point_estimates": sensitivity,
        "cell_summaries": cell_summaries,
        "taxonomy": taxonomy,
        "wall_seconds": time.monotonic() - start,
        "review_boundary": (
            "Count-based arithmetic reimplemented; original C bootstrap/classifier reused. "
            "Not an independent reviewer or raw-data re-audit."
        ),
    }
    with args.output.open("x") as out:
        json.dump(result, out, sort_keys=True, indent=2, allow_nan=False)
        out.write("\n")
    print(
        json.dumps(
            {"status": "PASS", "taxonomy": taxonomy, "wall_seconds": result["wall_seconds"]}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
