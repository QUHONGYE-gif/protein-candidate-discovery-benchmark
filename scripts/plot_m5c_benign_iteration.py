#!/usr/bin/env python3
"""Render assay-separated scientific figures from verified compact results."""

import argparse
import json
from pathlib import Path

from m5c_benign_scope import ASSAY_IDS
from plot_m5c_gfp_iteration import BLUE, INK, LIGHT, MUTED, ORANGE, label
from reportlab.graphics import renderSVG
from reportlab.graphics.shapes import Circle, Drawing, Line, Rect
from reportlab.lib import colors

NAMES = ("PAB1", "Hsp90", "GCK", "GFP (prior exploration)")


def forest_figure(records, out):
    d = Drawing(1100, 585)
    d.add(Rect(0, 0, 1100, 585, fillColor=colors.white, strokeColor=None))
    label(
        d,
        30,
        548,
        "ProteinGym: no established exploration advantage in three new assays",
        21,
        bold=True,
    )
    label(d, 30, 522, "Top 1% new-batch recall | 40 paired states per assay | batch 16", 12, MUTED)
    for panel, (contrast, color) in enumerate((("E-G", BLUE), ("E-M", ORANGE))):
        x, width = 240 + panel * 420, 350

        def tx(value, x=x, width=width):
            return x + (value + 1.6) * width / 3.2

        label(
            d,
            x + width / 2,
            480,
            "Exploration - " + ("Greedy" if panel == 0 else "Matched Random"),
            13,
            bold=True,
            anchor="middle",
        )
        for i, record in enumerate(records):
            y = 425 - i * 80
            v = record["verification"]
            c = v["primary_contrasts"][contrast]
            delta = 100 / v["high_value_definitions"]["0.01"]["nominal_count"]
            d.add(
                Rect(
                    tx(-delta),
                    y - 25,
                    tx(delta) - tx(-delta),
                    50,
                    fillColor=LIGHT,
                    strokeColor=None,
                )
            )
            cc = MUTED if i == 3 else color
            lo, hi, point = (tx(c[k] * 100) for k in ("ci_lower", "ci_upper", "point_estimate"))
            d.add(Line(lo, y, hi, y, strokeColor=cc, strokeWidth=2))
            for xx in (lo, hi):
                d.add(Line(xx, y - 5, xx, y + 5, strokeColor=cc, strokeWidth=1.4))
            d.add(Circle(point, y, 4, fillColor=cc, strokeColor=cc))
            label(d, x + width / 2, y - 39, c["status"], 9, MUTED, anchor="middle")
            if panel == 0:
                label(d, 30, y + 6, NAMES[i], 13, bold=True)
                label(d, 30, y - 13, v["taxonomy"], 9, MUTED)
        d.add(Line(tx(0), 156, tx(0), 453, strokeColor=MUTED, strokeDashArray=[3, 3]))
        for tick in (-1.5, -1, -0.5, 0, 0.5, 1, 1.5):
            label(d, tx(tick), 122, f"{tick:g}", 10, MUTED, anchor="middle")
        label(d, x + width / 2, 100, "Recall difference (percentage points)", 11, anchor="middle")
    d.add(Line(30, 215, 1050, 215, strokeColor=MUTED, strokeDashArray=[3, 3]))
    label(
        d,
        30,
        67,
        "Bars: 99.375% intervals from 20,000 shared-seed bootstrap draws within each assay. "
        "No pooled estimate.",
        11,
        MUTED,
    )
    label(
        d,
        30,
        46,
        "Shading: preset equivalence band (+/-1 high-value hit per batch). "
        "GFP is shown separately as an already-seen reference.",
        11,
        MUTED,
    )
    label(
        d,
        30,
        25,
        "Models: RF + Uncertainty and XGBoost + UCB; "
        "model and exploration rule are interpreted as a combination.",
        11,
        MUTED,
    )
    renderSVG.drawToFile(d, str(out / "primary-effects.svg"))


def cell_figure(records, out):
    d = Drawing(1100, 700)
    d.add(Rect(0, 0, 1100, 700, fillColor=colors.white, strokeColor=None))
    label(d, 30, 665, "New assays: paired effects by model and initial sample size", 21, bold=True)
    label(
        d,
        30,
        639,
        "Small dots = seed effects; large dots = cell means. "
        "Descriptive cells, without separate interval tests.",
        12,
        MUTED,
    )
    for panel, (contrast, color) in enumerate((("E-G", BLUE), ("E-M", ORANGE))):
        x, width = 240 + panel * 420, 350

        def tx(value, x=x, width=width):
            return x + (value + 5) * width / 10

        label(
            d,
            x + width / 2,
            599,
            "Exploration - " + ("Greedy" if panel == 0 else "Matched Random"),
            13,
            bold=True,
            anchor="middle",
        )
        d.add(Line(tx(0), 118, tx(0), 580, strokeColor=MUTED, strokeDashArray=[3, 3]))
        for ai, record in enumerate(records[:3]):
            for ci, cell in enumerate(record["verification"]["cell_summaries"]):
                y = 557 - ai * 155 - ci * 33
                if panel == 0:
                    model = "RF" if cell["model_id"].startswith("random") else "XGB"
                    label(
                        d,
                        30,
                        y - 4,
                        f"{NAMES[ai]} / {model} / {cell['initial_sample_size']}",
                        11,
                        INK,
                    )
                for si, seed in enumerate(cell["seed_effects"]):
                    d.add(
                        Circle(
                            tx(seed[contrast] * 100),
                            y + (si - 4.5) * 1.4,
                            2.2,
                            fillColor=colors.HexColor("#AABAC5"),
                            strokeColor=None,
                        )
                    )
                d.add(
                    Circle(
                        tx(cell["effects"][contrast] * 100),
                        y,
                        4.4,
                        fillColor=color,
                        strokeColor=color,
                    )
                )
        for tick in (-5, -2.5, 0, 2.5, 5):
            label(d, tx(tick), 94, f"{tick:g}", 10, MUTED, anchor="middle")
        label(d, x + width / 2, 72, "Recall difference (percentage points)", 11, anchor="middle")
    label(
        d,
        30,
        41,
        "10 fixed seeds per cell. The same seed slots and nested initial observations "
        "are retained across the four cells.",
        11,
        MUTED,
    )
    label(
        d,
        30,
        20,
        "64 matched-random branches are averaged within each state; "
        "neither seeds nor branches are independent proteins.",
        11,
        MUTED,
    )
    renderSVG.drawToFile(d, str(out / "cell-effects.svg"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--gfp-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = [
        {"verification": json.loads((args.results / assay / "verification.json").read_text())}
        for assay in ASSAY_IDS
    ]
    records.append({"verification": json.loads(args.gfp_reference.read_text())})
    if any(r["verification"]["status"] != "PASS" for r in records):
        raise ValueError("Figures require passing verification records")
    args.output.mkdir(exist_ok=False)
    forest_figure(records, args.output)
    cell_figure(records, args.output)
    print("Two assay-separated SVG figures rendered")


if __name__ == "__main__":
    main()
