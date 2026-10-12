# Handoff prompt — continue Mentat Chart Studio (implement A + B)

Paste everything below the line into a fresh Claude Code session opened on the Mentat repo.

---

Continue work on the Mentat console's **Chart Studio** (a Tableau-style chart builder in the console). First run the Project Brain boot in CLAUDE.md and follow the BOOT contract, then read the auto-memory note `mentat-chart-studio-review-2026-09-11` for full context. Work through visible PowerShell where practical.

## Where things stand (verify before trusting this)
- The Chart Studio code lives in `C:\Users\pgoff\Mentat\mentat\app`: `chart-studio.js`, `chart-studio-core.js`, `chart-studio.css`, `tests/chart-studio-core.test.js`, wired into `app.js`, `charts.js`, `chart-math.js`, `grouped-chart.js`, `index.html`, `server.py`.
- Ten P1 bugs were fixed on 2026-09-12 and are **uncommitted**; pre-edit copies are `<file>.bak-20260911b` beside each file. `node --test tests/chart-studio-core.test.js` = 42/42. Do not regress these.
- The console reads only the columns of the published workbook `C:\Users\pgoff\OneDrive\Desktop\Mentat Complete Files\<Industry> Industry Spending Overview.xlsx`. It never reads raw FEC rows.

## The goal
The user wants to drag **fine-grained fields** into Rows/Columns like in Tableau — contributor ZIP codes, individual committees / Super PACs by name, employer — not just the pre-aggregated dollar columns. Root cause is two-sided, so implement both fixes.

## B — chart-side (do FIRST; reversible; no pipeline run)
In `C:\Users\pgoff\Mentat\mentat\app`. Back up each file as `<file>.bak-20260912` before editing; keep `node --test` green and add core tests.
1. Replace the hard 60-group cap in `chart-math.js` (~lines 168 and 193, "Filter to 60 groups or fewer…") with a **top-N + "Other" rollup**: keep the largest N groups (N configurable, default ~40), fold the remainder into a single "Other" mark, and note the rollup under the chart. Do not silently drop groups.
2. Let a user **reveal a hidden identifier column**: `chart-studio-core.js` (~line 38) hides identifier-named columns (committee_id, candidate_id, ZIP-in-name). Add a control in the Data pane ("Show identifier fields") or allow an explicit drag of a hidden field, so committee/candidate IDs and codes can be charted on demand. Keep them hidden by default.
3. **Split semicolon-joined cells** into one value per mark for multi-value columns (Employee List "ZIPs" and "Cities / States" are `;`-joined). Add an opt-in "split multi-value" toggle on the field or shelf, so a raw ZIP dimension becomes usable.

## A — data-side (do SECOND; the real fix for ZIP/employer/committee; GATED)
The large cycle sheets are written in compact + formula-facts mode (payload > 120 MB threshold in `build_industry_report.ps1:545-557` -> `MENTAT_COMPACT_RAW=1`, `MENTAT_FORMULA_FACTS=1`), which strips zipcode/city/state/employer/occupation/committee type before writing (`crypto_workbook_builder.mjs:807-829` compact vs `831-867` full). The source DB has the fields: `…\.cache\report-repairs\banking-20260910\data\mentat.db`, `contributions` table, 783,808 rows, 31,992 distinct ZIPs, 2,926 committee_id, 2,458 employers, `recipient_committee_type`/`recipient_committee_designation` (V/U = Super PAC).
- Add a **dedicated fine-grain worksheet** to the workbook writer (`crypto_workbook_builder.mjs`, plus the R exporter that builds the payload) — e.g. "Recipient Detail" and/or "Contributions Detail" — keeping committee_id, committee type/designation (labelled), ZIP, city, state, employer at recipient or receipt grain, **row-capped** to something the console's 50,000-row scan (`report_insights.py`, MAX_ROWS) can serve.
- Design it so the new sheet survives compact/formula-facts mode (those flags must not strip the new sheet's columns).
- **STOP and show the user the plan before re-running the industry build** — regenerating the workbook overwrites the file the user distributes and is heavy. Build + unit-test the writer change, dry-run on a copy, present, then run the real build only on the user's go.

## Constraints (do not violate)
- **No git.** Committing is the user's call; do not run any git command. (This lane's memory records a standing "no git" rule.)
- **Ask first before applying substantive changes** and before any irreversible action (the workbook rebuild especially). Present findings + plan, then wait.
- **Verify live** on a read-only second instance: add a `.claude/launch.json` entry `python C:\Users\pgoff\Mentat\mentat\app\server.py --port 8812 --read-only` (the live consoles on 8802/8808 are the user's — never touch them). `launch.json` is tracked — byte-copy it first and restore it after. **Reload the browser tab after every edit** before trusting behavior: the server serves files per-request but the loaded page caches its JS bundle (this bit us once — a stale bundle showed old behavior).
- Keep `.bak` backups; keep `node --test tests/chart-studio-core.test.js` green; both JS files are pure CRLF — preserve line endings.
- Note: the Claude Code Write/Edit tool refuses writes into `C:\Users\pgoff\Mentat\mentat\app` when the session runs from a Melange worktree (it treats it as the base checkout). Run this secondary session **with `C:\Users\pgoff\Mentat` as the working directory** so edits land normally, or apply edits via a Node/PowerShell patcher that asserts each find-string occurs exactly once.

## Already works today — tell the user, don't rebuild it
"Super PAC" is already sliceable: it is a **value** of the **Recipient Category** column (present on Recipient List, PAC & Candidate Map, and the yearly Fact sheets). On Recipient List, Total Giving × Recipient Category draws all ten categories. B makes the *raw* committees/ZIPs usable; A brings the stripped fields into the workbook.

Deliver: B implemented + live-verified; A built, tested, and presented for the user's go before the workbook rebuild. Report what was changed, the test results, and what remains gated.
