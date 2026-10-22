# -*- coding: utf-8 -*-
"""Keep the main workbook's stance views synchronized with live authorities.

This module deliberately changes cell contents/formulas only. Native Excel owns
the workbook presentation, and preserving its existing styles/formatting avoids
rewriting unrelated OOXML during the automated pipeline sync.
"""
from __future__ import annotations

import roster


GENERAL_STANCE_TIERS = (
    "Champion", "Ally", "Highly Persuadable", "Persuadable",
    "Lean Persuadable", "True Neutral", "Lean Skeptic", "Skeptic",
    "Highly Skeptical", "Opponent", "—",
)

CATEGORY_STANCE_TIERS = (
    "Champion", "Defender", "Ally", "Highly Persuadable", "Persuadable",
    "Lean Persuadable", "True Neutral", "Lean Skeptic", "Skeptic",
    "Highly Skeptical", "Opponent", "—",
)


def sync_category_stance_sheet(wb, category, members, overall_by_member):
    """Write the live roster x 18 matrix and the matching overall stance values."""
    ws = wb["Category Stance"] if "Category Stance" in wb.sheetnames else wb.create_sheet("Category Stance")
    cats = list(category["cats"])
    matrix = category["matrix"]
    headers = ["Member", "General Stance"] + cats
    if len(headers) != 20:
        raise ValueError("Category Stance requires exactly 18 live categories")

    # Members sit on rows 2..last_row; the three legend rows start two rows under the block.  The clear covers the larger of
    # the old sheet and the new extent, so a shrinking roster leaves no stale legend or member rows behind.
    legend = roster.last_row("Category Stance") + 2
    for row in ws.iter_rows(min_row=1, max_row=max(ws.max_row, legend + 2), min_col=1, max_col=20):
        for cell in row:
            cell.value = None
    for col, value in enumerate(headers, start=1):
        ws.cell(1, col).value = value
    for row, member in enumerate(members, start=2):
        ws.cell(row, 1).value = member
        ws.cell(row, 2).value = overall_by_member.get(member, "—")
        for col, cat in enumerate(cats, start=3):
            ws.cell(row, col).value = matrix.get(member, {}).get(cat, "—")

    ws.cell(legend, 1).value = "Legend — stance toward the CU position, by category"
    for col, tier in enumerate(CATEGORY_STANCE_TIERS, start=1):
        ws.cell(legend + 1, col).value = tier
    ws.cell(legend + 2, 1).value = (
        "Source: category_stance.json. General Stance matches Rank & Allocation column G. "
        "Overall Champion is reserved for CU primacy Tier 1 (top 13)."
    )
    return ws


def sync_coalition_profiles_sheet(wb):
    """Replace retired six-tier formulas with the complete current taxonomy."""
    ws = wb["Coalition Profiles"]
    ra_h, ra_g = roster.col_range("H"), roster.col_range("G")
    bm_s, bm_u = roster.col_range("S", "Blended Model"), roster.col_range("U", "Blended Model")
    for row in ws.iter_rows(min_row=3, max_row=13, min_col=10, max_col=21):
        for cell in row:
            cell.value = None
    for col, tier in enumerate(GENERAL_STANCE_TIERS, start=10):
        ws.cell(3, col).value = tier
    ws.cell(3, 21).value = "Dominant Stance"
    for row in range(4, 13):
        for col in range(10, 21):
            header = ws.cell(3, col).coordinate
            ws.cell(row, col).value = (
                f'=COUNTIFS(\'Rank & Allocation\'!{ra_h},$A{row},'
                f'\'Rank & Allocation\'!{ra_g},{header})'
            )
        ws.cell(row, 21).value = f'=INDEX($J$3:$T$3,MATCH(MAX(J{row}:T{row}),J{row}:T{row},0))'
    for col in range(10, 21):
        letter = ws.cell(3, col).column_letter
        ws.cell(13, col).value = f"=SUM({letter}4:{letter}12)"
    ws.cell(13, 21).value = None

    for row in ws.iter_rows(min_row=16, max_row=29, min_col=1, max_col=6):
        for cell in row:
            cell.value = None
    ws.cell(16, 1).value = "BY CURRENT CU-STANCE TIER"
    for col, value in enumerate(("Stance", "Members", "Mean Score", "Allocation", "% of Pot", "Funded"), start=1):
        ws.cell(17, col).value = value
    for row, tier in enumerate(GENERAL_STANCE_TIERS, start=18):
        ws.cell(row, 1).value = tier
        ws.cell(row, 2).value = f'=COUNTIF(\'Rank & Allocation\'!{ra_g},$A{row})'
        ws.cell(row, 3).value = (
            f'=IFERROR(AVERAGEIF(\'Rank & Allocation\'!{ra_g},$A{row},'
            f'\'Blended Model\'!{bm_s}),0)'
        )
        ws.cell(row, 4).value = (
            f'=SUMIFS(\'Blended Model\'!{bm_u},'
            f'\'Rank & Allocation\'!{ra_g},$A{row})'
        )
        ws.cell(row, 5).value = f'=IFERROR(D{row}/\'Blended Model\'!$AB$6,0)'
        ws.cell(row, 6).value = (
            f'=COUNTIFS(\'Rank & Allocation\'!{ra_g},$A{row},'
            f'\'Blended Model\'!{bm_u},">0")'
        )
    ws.cell(29, 1).value = "TOTAL"
    ws.cell(29, 2).value = "=SUM(B18:B28)"
    ws.cell(29, 3).value = f"=AVERAGE('Blended Model'!{bm_s})"
    ws.cell(29, 4).value = "=SUM(D18:D28)"
    ws.cell(29, 5).value = "=SUM(E18:E28)"
    ws.cell(29, 6).value = "=SUM(F18:F28)"
    return ws


def stance_view_contract(category, members, overall_by_member, tier_by_member):
    """Fail closed unless Champion is exactly the tier-wise Tier 1 top 13."""
    if set(members) != set(category["matrix"]):
        raise ValueError("category_stance.json members do not exactly match the workbook roster")
    unknown = sorted({overall_by_member.get(member, "—") for member in members} - set(GENERAL_STANCE_TIERS))
    if unknown:
        raise ValueError("unknown overall stance labels: %s" % unknown)
    champions = {member for member in members if overall_by_member.get(member) == "Champion"}
    tier_one = {member for member in members if tier_by_member.get(member) == 1}
    if champions != tier_one or len(champions) != 13:
        raise ValueError(
            "overall Champion must equal the CU primacy Tier 1 top 13 "
            "(champions=%d tier1=%d symmetric_diff=%s)"
            % (len(champions), len(tier_one), sorted(champions ^ tier_one))
        )
    return {"members": len(members), "categories": len(category["cats"]), "champions": sorted(champions)}
