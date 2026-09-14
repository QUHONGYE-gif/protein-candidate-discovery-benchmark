#!/usr/bin/env python3
"""Render static scientific charts from verified aggregate-only GFP results."""

import argparse
import json
import math
from pathlib import Path

from reportlab.graphics import renderSVG
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors

INK = colors.HexColor("#192F40")
BLUE = colors.HexColor("#156AA6")
ORANGE = colors.HexColor("#BD581C")
MUTED = colors.HexColor("#536878")
LIGHT = colors.HexColor("#E9F0F4")


def label(d, x, y, text, size=11, color=INK, bold=False, anchor="start"):
    d.add(
        String(
            x,
            y,
            text,
            fontName="Helvetica-Bold" if bold else "Helvetica",
            fontSize=size,
            fillColor=color,
            textAnchor=anchor,
        )
    )


def plot(x, y, width, height, bound, ymax, series):
    chart = LinePlot()
    chart.x, chart.y, chart.width, chart.height = x, y, width, height
    chart.data = [s[0] for s in series]
    chart.joinedLines = 1
    chart.xValueAxis.valueMin = -bound
    chart.xValueAxis.valueMax = bound
    chart.xValueAxis.valueStep = bound / 2
    chart.xValueAxis.labelTextFormat = "%.2f"
    chart.xValueAxis.labels.fontSize = 10
    chart.xValueAxis.strokeColor = MUTED
    chart.yValueAxis.valueMin = 0
    chart.yValueAxis.valueMax = ymax
    chart.yValueAxis.visible = False
    for i, (_points, color, size, line_width) in enumerate(series):
        chart.lines[i].strokeColor = color
        chart.lines[i].strokeWidth = line_width
        if size:
            marker = makeMarker("FilledCircle")
            marker.size = size
            marker.fillColor = color
            marker.strokeColor = color
            chart.lines[i].symbol = marker
    return chart


def primary_figure(v, out):
    d = Drawing(880, 385)
    d.add(Rect(0, 0, 880, 385, fillColor=colors.white, strokeColor=None))
    label(d, 32, 350, "GFP: exploration has no established discovery advantage", 20, bold=True)
    label(d, 32, 327, "Top 1% discovery recall | 40 paired states | batch size 16", 12, MUTED)
    bound = max(
        0.25,
        math.ceil(
            max(
                abs(v["primary_contrasts"][k][edge]) * 100
                for k in ("E-G", "E-M")
                for edge in ("ci_lower", "ci_upper")
            )
            * 4
        )
        / 4,
    )
    x, y, w, h = 178, 125, 615, 147
    delta = 100 / v["high_value_definitions"]["0.01"]["nominal_count"]

    def transform(value):
        return x + (value + bound) * w / (2 * bound)

    d.add(
        Rect(
            transform(-delta),
            y,
            transform(delta) - transform(-delta),
            h,
            fillColor=LIGHT,
            strokeColor=None,
        )
    )
    d.add(Line(transform(0), y, transform(0), y + h, strokeColor=MUTED, strokeDashArray=[3, 3]))
    series = []
    for row, contrast, color in ((2, "E-G", BLUE), (1, "E-M", ORANGE)):
        c = v["primary_contrasts"][contrast]
        series += [
            ([(c["ci_lower"] * 100, row), (c["ci_upper"] * 100, row)], color, 0, 2),
            ([(c["point_estimate"] * 100, row)], color, 9, 1),
        ]
        yy = y + row * h / 3
        label(
            d,
            32,
            yy + 6,
            "Exploration - " + ("Greedy" if contrast == "E-G" else "Matched Random"),
            10,
            bold=True,
        )
        label(d, 32, yy - 10, c["status"], 9, MUTED)
    d.add(plot(x, y, w, h, bound, 3, series))
    label(d, x + w / 2, 85, "Difference in recall (percentage points)", 12, anchor="middle")
    label(
        d,
        32,
        55,
        (
            "Bars: 99.375% intervals, 20,000 shared-seed bootstrap draws. "
            f"Shaded band: +/-{delta:.3f} pp."
        ),
        11,
        MUTED,
    )
    label(
        d,
        32,
        34,
        "Intervals describe variation within one GFP assay. Overall taxonomy: "
        + v["taxonomy"]
        + ".",
        11,
        MUTED,
    )
    renderSVG.drawToFile(d, str(out / "primary-effects.svg"))


def cell_figure(v, out):
    d = Drawing(1000, 420)
    d.add(Rect(0, 0, 1000, 420, fillColor=colors.white, strokeColor=None))
    label(d, 30, 385, "GFP: paired seed effects by model and initial sample size", 20, bold=True)
    label(
        d,
        30,
        360,
        "Small dots = seed effects; large dots = cell means. These are not independent assays.",
        12,
        MUTED,
    )
    cells = v["cell_summaries"]
    extremes = [abs(s[k]) * 100 for c in cells for s in c["seed_effects"] for k in ("E-G", "E-M")]
    bound = max(0.25, math.ceil(max(extremes) * 4) / 4)
    y, h = 108, 205
    for i, c in enumerate(cells):
        row = 4 - i
        label(
            d,
            30,
            y + row * h / 5 - 4,
            ("RF" if c["model_id"].startswith("random") else "XGBoost")
            + f" / initial {c['initial_sample_size']}",
            11,
            bold=True,
        )
    for panel, (contrast, color) in enumerate((("E-G", BLUE), ("E-M", ORANGE))):
        x, w = 195 + panel * 395, 345
        label(
            d,
            x + w / 2,
            331,
            "Exploration - " + ("Greedy" if contrast == "E-G" else "Matched Random"),
            12,
            bold=True,
            anchor="middle",
        )
        d.add(Line(x + w / 2, y, x + w / 2, y + h, strokeColor=MUTED, strokeDashArray=[3, 3]))
        series = []
        for i, c in enumerate(cells):
            row = 4 - i
            pale = colors.HexColor("#87B6D5" if panel == 0 else "#D6A182")
            for slot, s in enumerate(c["seed_effects"]):
                jitter = (slot - 4.5) * 0.033
                series.append(([(s[contrast] * 100, row + jitter)], pale, 4, 1))
            series.append(([(c["effects"][contrast] * 100, row)], color, 10, 1))
        d.add(plot(x, y, w, h, bound, 5, series))
        label(d, x + w / 2, 65, "Recall difference (percentage points)", 11, anchor="middle")
    label(
        d,
        30,
        34,
        (
            "10 fixed seeds per cell; Matched Random replicates are averaged within each state. "
            "No cell-specific interval is shown."
        ),
        11,
        MUTED,
    )
    renderSVG.drawToFile(d, str(out / "cell-effects.svg"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    v = json.loads(args.verification.read_text())
    assert v["status"] == "PASS"
    args.output.mkdir(exist_ok=False)
    primary_figure(v, args.output)
    cell_figure(v, args.output)
    print("Two SVG figures rendered with ReportLab charts")


if __name__ == "__main__":
    main()
