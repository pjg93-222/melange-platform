# Chart Studio — P1 fixes & field-granularity diagnosis

**Date:** 2026-09-12
**Scope:** `C:\Users\pgoff\Mentat\mentat\app` (chart-studio.js, chart-studio-core.js, tests/chart-studio-core.test.js, one line of index.html)
**Status:** P1 fixes applied and live-verified; **uncommitted** (commit is Paul's). Granularity work is diagnosis only — nothing applied.
**Backups:** pre-edit copies saved beside each file as `<file>.bak-20260911b`.
**Checks:** `node --test tests/chart-studio-core.test.js` = 42 passed / 0 failed (was 37); `node --check` clean on both JS files; both pure CRLF. Live re-verified on a reloaded read-only instance (`server.py --port 8812 --read-only`; the live 8802/8808 consoles were untouched).

---

## Part 1 — the ten P1 fixes

| # | Fix | Live result |
|---|-----|-------------|
| 1 | Cross-series grand total suppressed | Funding Type on Colour -> note "Grand total is not shown: series are independent."; status drops the SUM segment. |
| 2 | Non-sum total suppressed | Mean aggregation + Totals -> "Grand total is only available for SUM and COUNT."; no false AVG figure. |
| 5 | Histogram total & title corrected | Histogram title now "Included Net Receipts · Histogram" (no "· Sum"); no row-count printed as SUM; SVG/PNG/CSV title matches. |
| 3 | Session key is per-worksheet | Switching Funding Cohorts -> Company List now clears the view (fresh Sheet 1, undo disabled); two worksheet-scoped localStorage keys; switching back restores each. Cross-sheet shelf carry-over is gone. Stale `selection` also reset on a source change. |
| 4 | Sort locked on time views | Line over Cycle -> both Sort buttons disabled with title "Time views keep chronological order"; labels stay 2022 / 2024 / 2026. |
| 6 | Sheet rename works | Double-click the active tab opens the editor (focused, text selected); typing + Enter commits. Was: the editor never opened. |
| 7 | Pill onto canvas no longer deletes | Dragging a shelf pill onto the chart is accepted (canvas `dragover` preventDefaults pill drags); the pill is preserved. |
| 8 | Drop onto Filters when a filter exists | Rows Company -> Filters (Company already filtered) -> note "Company already has a filter.", pill preserved. Was: silently deleted. |
| 9 | Same-shelf reorder off-by-one | Columns [Receipts, Funding Type, Receipt Role], drag Receipts onto Receipt Role -> [Funding Type, Receipts, Receipt Role] (correct). |
| 10 | Subject filter for time transforms | `params.subject` added; a Subject `<select>` appears only when the worksheet has a Subject column and auto-selects the first member (no placeholder trap), injecting the filter for time transforms. Core-tested; no Subject-bearing worksheet in the Banking book to drive live. |

**Rulings folded in:** the Filters-shelf reorder is a **silent** no-op (verified on the reloaded bundle — an earlier "already has a filter" note on a reorder was a stale in-memory bundle; the served file carries the guard plus a `data-dragging` fallback). Subject is a derived default that mirrors the classic dialog.

**Verifier false positive:** "`dropped=true` not reset before early returns" — it is set at the top of the drop handler, above every early return, so no no-op / refusal / reorder path can lose a pill. Confirmed live. No change made.

---

## Part 2 — why the draggable fields are coarse (ZIP, Super PAC, etc.)

The cause is **both** the worksheet generation and the chart engine, at two altitudes. The console only ever sees the columns of the published `.xlsx`; it cannot reach the raw FEC rows.

### What already works today (Banking)
"Super PAC" is sliceable now — it is a **value** of the **Recipient Category** column, not its own field. On the **Recipient List** sheet, Total Giving × Recipient Category renders 10 bars: Super PAC, Carey, Leadership PAC, Company PAC, Other PAC, Trade Association, Candidate, Party Committee, JFC, Unclassified (SUM Total Giving 109,525,688 — verified live). The same column appears on PAC & Candidate Map and the yearly Fact sheets. Other draggable dimensions: company, cycle, party, office, state, ownership status, committee relationship.

### What is missing, and why
The source DB `…\.cache\report-repairs\banking-20260910\data\mentat.db` (783,808 contribution rows) HAS the fine fields: contributor_zip (31,992 distinct), committee_id (2,926), contributor_employer (2,458), recipient_committee_type/designation (V/U = Super PAC, etc.), city, state, occupation. None reach the workbook at usable grain:

1. **Worksheet generation (primary cause).** The published workbook is aggregated. The large cycle sheets ("2021-2022" / "2023-2024" / "2025-2026") are written in **compact + formula-facts mode**: the Banking payload is 889 MB, over the 120 MB threshold in `build_industry_report.ps1:545-557`, so `MENTAT_COMPACT_RAW=1` and `MENTAT_FORMULA_FACTS=1`. Compact mode drops zipcode, city, state, employer, occupation and committee type/designation from the raw rows (`crypto_workbook_builder.mjs:807-829` vs `831-867`), and formula-facts collapses to 12 aggregate columns. The granular FEC fields are stripped before the file the console reads is ever written.
2. **Chart engine (secondary cause).**
   - The one raw ZIP field, Employee List -> "ZIPs", is a **semicolon-joined multi-ZIP list** (3,093 distinct; of 4,500 rows, 2,266 single / 2,229 multi / 5 blank) — unusable as a clean dimension.
   - `chart-math.js` throws above **60 groups** (`:168`, `:193`), so a raw ZIP / committee / recipient dimension errors instead of charting.
   - Identifier-named columns (committee_id, candidate_id) are **hidden** (`chart-studio-core.js:38`), inconsistently (e.g. "Candidate ID(s)" escapes the rule that hides "Candidate ID").

### Options (Paul's call — nothing applied)
- **A. Add a fine-grain worksheet to the writer** (e.g. "Recipient Detail" / "Contributions Detail") keeping committee_id, committee type/designation, ZIP, city, state, employer at recipient or receipt grain, row-capped. This is the real fix for ZIP / employer / Super-PAC-by-committee. Touches the R + `crypto_workbook_builder.mjs` pipeline and re-runs the workbook build — Paul's data domain, substantive.
- **B. Chart-side improvements (Claude can do under a gate):** replace the hard 60-group cap with a top-N + "Other" rollup; let a user reveal a hidden identifier column on explicit drag; split semicolon-joined cells into one value per mark. Makes the granular columns that ARE in the workbook usable, but cannot conjure ZIP/employer into the stripped cycle sheets.
- **C. A "detail" data path from the console straight to the source SQLite** — largest change; bypasses the workbook entirely.

**Recommendation: A + B together** — A for the fields that were stripped, B so the console stops hiding and capping the granular columns that do exist.
