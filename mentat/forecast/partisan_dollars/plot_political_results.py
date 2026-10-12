"""Render the complete comparison, including the matched feature subset."""
import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render(evaluation, output):
    fig, axes = plt.subplots(1, 2, figsize=(11.8, 5.4), sharex=True)
    fig.patch.set_facecolor("#faf8f3")
    variants = ["baseline", "lean_only", "both"]
    labels = ["Existing dollar model", "+ Industry partisan lean", "+ Lean and champion proxy"]
    for ax, population, title in zip(axes, ["all_cells", "fitted_feature_cells"],
                                    ["All forecasts · 320 cells", "Matched feature test · 96 cells"]):
        rows = [next(r for r in evaluation["metrics"] if r["variant"] == variant and
                     r["population"] == population and r["horizon"] == "ALL" and r["chamber"] == "ALL")
                for variant in variants]
        values = [r["certified_within20"] / r["n"] * 100 for r in rows]
        ax.set_facecolor("#faf8f3")
        ax.barh(range(3), values, color=["#62736c", "#568bb5", "#bb754c"], height=.5)
        for index, (value, row) in enumerate(zip(values, rows)):
            rounded = Decimal(str(value)).quantize(Decimal(".1"), rounding=ROUND_HALF_UP)
            ax.text(value + 1.5, index, f"{rounded}%  ({row['certified_within20']}/{row['n']})", va="center", fontsize=10)
        ax.set_yticks(range(3), labels if ax is axes[0] else ["", "", ""])
        ax.invert_yaxis()
        ax.set_xlim(0, 118)
        ax.set_xticks([0, 20, 40, 60, 80, 100], ["0%", "20%", "40%", "60%", "80%", "100%"])
        ax.grid(axis="x", alpha=.15)
        ax.set_axisbelow(True)
        ax.set_title(title, loc="left", fontsize=12, pad=16)
        for side in ["top", "right", "left"]:
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="y", length=0)
    fig.suptitle("Political features did not improve this historical test", x=.03, ha="left", fontsize=17, fontweight="bold")
    fig.text(.03, .86, "Share of dollar forecasts certified within 20% across full classification ranges", fontsize=11, color="#4a4f4d")
    fig.text(.03, .065, "Reconstructed retrospective study. Repeated dates share election outcomes.\n"
             "Matched feature test: 2022 and 2024, banks, credit unions and partial P&C context.", fontsize=10, color="#4a4f4d")
    fig.subplots_adjust(left=.26, right=.96, top=.74, bottom=.22, wspace=.25)
    fig.savefig(output, dpi=170, facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluation")
    parser.add_argument("output")
    args = parser.parse_args()
    render(json.loads(Path(args.evaluation).read_bytes()), args.output)
