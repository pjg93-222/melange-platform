# Mentat Sequencing — v2.1 (reviewed + decided, 2026-07-18)

**Status: SPEC FINAL-DRAFT. Nothing executed, no code written yet — build starts on Paul's go.**

v1 ("Mentat Squencing.docx") was reviewed with (a) live verification of every CSS selector on fec.gov
(2026-07-18), (b) a cited research pass on FEC data mechanics, and (c) Paul's decisions of 2026-07-18.
Changes from v1 are marked **[CHANGED]**, additions **[NEW]**. Defaults I adopted under "fix all of
those" are collected in the **Defaults adopted** section at the bottom — veto any of them.

## Decisions recorded (Paul, 2026-07-18)

| Decision | Ruling |
|---|---|
| Implementation | **R pipeline** |
| Collection engine | **Headless Chrome** (browser scrape of fec.gov, not bulk files / not the API) |
| Cycles | **2021 onward**: 2021-22, 2023-24, 2025-26-to-date |
| 527s and 501c's | **Excluded** from the recipient taxonomy and all Phase 5 math |
| Category workbook | Received: `C:\Users\pgoff\OneDrive\Documents\Super PAC, 527, Carey, 501c.xlsx` (sheets 2021-2022, 2023-2024) |
| Output template | Received: `C:\Users\pgoff\OneDrive\Documents\Spending Report template.xlsx` (see Output contract section) |
| ABS issuers | **Out of scope** (in the template's Sheet1 asset list but no roster; Paul ruled out 2026-07-18) — its Sheet1 row is ignored |

**Objective (unchanged):** an R pipeline that navigates and extracts FEC campaign-finance data for
designated industries (Crypto, Credit Union, Community Banks, Property & Casualty Insurance, Credit
Arms), then aggregates and analyzes it for statistical trends, partisan divides, and outliers.

---

## Engine & operational contract **[NEW]**

- **Stack:** R + `chromote` (headless Chrome over the DevTools protocol — `rvest` alone cannot see
  the FEC tables, they are client-rendered), `data.table` for wrangling, `RSQLite` as the working
  store, `writexl` plus `zip` for formula-preserving OOXML output.
- **Extraction method [UPGRADED 2026-07-18, verified live]:** the FEC results table (a DataTables
  client) holds the **complete 81-field API record for every row in JS memory** —
  `jQuery('#results').DataTable().rows().data()` returns, per row: contributor name/first/last/
  suffix/street/city/state/zip, employer, occupation, `contributor_aggregate_ytd`, amount, date,
  `committee_id` + full committee object, **`sub_id` (true unique FEC record id)**,
  `transaction_id`, `memo_code`/`memo_text`/`memoed_subtotal`, `receipt_type_full` (e.g. EARMARK),
  `conduit_committee_id/name`, `recipient_committee_type`/`designation`, `election_type`,
  `amendment_indicator`, `report_year`, `two_year_transaction_period`, `pdf_url`, `load_date`.
  So the scraper drives the page filters in headless Chrome and reads whole pages via **one JS
  evaluation per page — no per-row expand clicks** (~10-40× faster than v1's click-per-row plan;
  crypto's biggest employer-cycle is ~40 pages at 100/page). The v1 CSS selectors remain as the
  **spot-check fallback harness** (all verified working).
- **Volume reality (measured live):** STATE FARM = 45,721 rows in the 2023-24 cycle alone; NAVY
  FEDERAL = 3,886. With ~40 companies × 3 cycles and an expand-click per row, this is a
  multi-day-to-week continuous scrape. The pipeline is therefore engineered as a long-running
  campaign, not a script:
  - **Checkpoint/resume:** progress recorded per (company, cycle, page); a killed run resumes at the
    last completed page, never re-collecting or losing rows. All writes atomic.
  - **Politeness:** fixed delay + jitter between page loads; one browser session; back-off on errors.
  - **Selector canaries:** on every page, assert the expected sort order (see `.sorting_1` note) and
    the presence/label-text of expected fields; on the committee page, assert row labels before
    reading values. Any canary failure halts the run loudly rather than collecting garbage.
  - **Results per page = 100** (verified available) — cuts pagination 3×.
  - **OneDrive hazard [NEW]:** the RSQLite working store and all in-flight scrape outputs live
    **outside** OneDrive-synced directories (OneDrive file locks are a known failure class in this
    project); only finished Excel deliverables land in OneDrive.
  - **Incremental re-runs [NEW]:** the checkpoint schema supports delta pulls — for the live
    2025-26 cycle, later runs re-scrape only from `min_date = last completed scrape date`, with each
    row stamped `scraped_at`. Processed data lags filings, so the most recent weeks of a live cycle
    are structurally incomplete; every 2025-26 output states its data-through date.
- Bulk-data note for the record: the FEC bulk `indiv[YY].zip` files exist (3.9-5.5 GB/cycle
  compressed) with a true unique row id, but they generally omit under-$200 conduit itemizations that
  the website *does* show — so the headless-Chrome choice actually buys better small-dollar coverage
  in exchange for time. Documented so nobody "optimizes" this later without knowing the trade.

---

## Phase 0: Quartermaster **[NEW]**

Normalize all inputs into machine-readable form before any collection.

1. **Company roster** — parse `Company Name.xlsx` → `mentat/config/companies.json`:
   - One record per blank-row-separated block; first entry = canonical name.
   - Normalize the structural split: Crypto sheet stores variants in *columns*, the other four in
     *rows*. **[CHANGED]**
   - Split slash-packed cells ("Ford Motor Credit Company / Ford Motor Credit Company LLC").
   - Convert annotations to structured fields, never search terms: "(distinctive… safe)" →
     `confidence: safe`; "(⚠️ spot-check)" → `confidence: spot_check` (mandatory sample review);
     "NOT SEATTLE KRAKEN" / "NOT TZERO TECHNOLOGIES" → `exclusions`; "CIK 2055592" → metadata; prose
     rows dropped with their subsidiary lists kept as variants of the parent.
   - **Roll-ups (default adopted):** subsidiaries aggregate to the parent, with per-subsidiary
     breakdown preserved (GEICO/Gen Re/National Indemnity/BH GUARD → Berkshire; Kia/Genesis → Hyundai
     Capital; PacLease/Kenworth/Peterbilt → PACCAR).
   - Output printed as a one-page-per-industry summary for Paul's eyeball **before Phase 1 runs**.
2. **Category workbook ingest** — parse `Super PAC, 527, Carey, 501c.xlsx` → category register:
   - Per sheet (cycle): `Group` (strip trailing NBSP), `View` → lean (**C → R-side, L → D-side,
     X → no lean**), category = x-marked column.
   - **527s / 501c columns ignored (EXCLUDED per decision).** A row marked only 527/501c drops out;
     a multi-marked row (12 exist in 2023-24) keeps its FEC-visible mark (Super PAC or Carey).
   - Rows with **no** category mark (19 + 10, e.g. DCCC/NRCC) are party committees → absorbed by the
     Party Committee bucket in Phase 4, not errors.
   - Groups are listed by colloquial name; each must resolve to an **FEC committee ID** (via the FEC
     committee search, scriptable in the same headless session). Ambiguous resolutions → review queue.
   - **Precedence rule:** Paul's workbook labels are gold and win over FEC committee-type codes;
     type codes (O/U = Super PAC, V/W = hybrid/Carey) fill gaps and *flag* disagreements into a
     review queue — the pipeline never overrides a Paul label. (Example the pipeline will surface:
     groups marked Carey whose FEC type says O.)
3. **Leadership-PAC register** — from the two CSVs (2021-22: 747 rows, 2023-24: 792). `Lean`
   "Solidly Republican/Conservative" → R, "Solidly Democrat/Liberal" → D; any other value surfaces
   for ruling. **2025-26 gap (default adopted):** carry the 2023-24 register forward as provisional
   AND detect new leadership PACs live (FEC committee designation "D" on the committee page); both
   feed a lean-labeling queue for Paul. Names resolve to committee IDs like the category workbook.
4. **Trade-association register** — the Trade Associations doc lists association *names*; their PACs
   file under different names (NAR → RPAC). Resolve each association → its PAC's committee ID
   (connected-organization/sponsor search); associations without a findable federal PAC are recorded
   `no_federal_pac` (they simply won't match receipts). Cleanup: strip the wiki artifact
   "[National Automatic Merchandising Association (NAMA)]][1]"; dedupe American Bar Association and
   Free Speech Coalition (each listed twice); note American Bankers Assn vs American Bar Assn share
   the acronym ABA — matching is by resolved committee ID, never by acronym. **[CHANGED]**
5. **Cycle windows:** 2021-22, 2023-24, 2025-26 (through collection date; partial-cycle flagged in
   all Phase 5 outputs). **[DECIDED]**
6. **Storage schema** — RSQLite working store; Excel is a Phase 5 *output*, not the store.
   Contributions table: contributor name, date, amount, recipient name, **recipient committee ID**,
   city, state, zip, employer (verbatim), occupation, **year-to-date aggregate**, **memo text**,
   form/line, plus provenance (industry, company, matched variant, phase found, match confidence,
   flags). Review queues are tables, exported to CSV for Paul.
7. **Dedup key [UPGRADED]** — primary key = **`sub_id`** (unique FEC record id, available via the
   DataTables in-memory records; the *rendered* UI shows no id, which is what the earlier
   verification measured). The tuple key `(contributor name, date, amount, recipient committee ID,
   memo text)` remains as the fallback for spot-check-harness rows only. Amendment handling via
   `amendment_indicator` + `original_sub_id`, flagged `possible_amendment`, never dropped.
8. **Template workbook ingest [NEW]** — from `Credit Union Cross Industry Spending.xlsx`:
   - **Known-executives register** (Sheet2's CEO column, e.g. Brian Armstrong → Coinbase): a
     name→company→Chief-tier override for Phase 4, since executives' occupation strings are often
     vague. Spelling normalization applied via mapping (Krakken→Kraken, Winkelvoss→Winklevoss),
     originals preserved.
   - **Industry asset denominators** (Sheet1: assets in $T with source links) → Phase 5
     cross-industry normalization.
   - The workbook's "Company Names" sheet is a **mirror** of `Company Name.xlsx`; the standalone
     file is canonical (one source of truth, no dual maintenance — same rule as the stance-label
     template precedent).

---

## Phase 1: Lay of The Land

Itemized individual contributions by employer, per company, per cycle.

- Filter `#contributor_employer` with the company's **minimal covering variant set**.
  **[CHANGED] Variant subsumption (verified):** the employer filter is a keyword match — "NAVY
  FEDERAL" already returns every "NAVY FEDERAL CREDIT UNION" row. Phase 0 computes the minimal set;
  non-subsumed variants (NFCU, BECU, …) run in Phase 2. Deselect via
  `#contributor_employer-field .dropdown__selected label` (verified).
- Cycle via the page's two-year transaction period control; `#min_date`/`#max_date` only for
  sub-slices. **[CHANGED]**
- Per row (selectors **verified live 2026-07-18**):
  - `.row--has-details .hide-panel-tablet` → Contributor name
  - `.sorting_1` → Receipt date — *sort-marker class, works only while the table is date-sorted;
    the per-page canary asserts sort order* **[CHANGED]**
  - `#results a` → Recipient **+ capture href → committee ID** (the Phase 4 join key) **[NEW]**
  - `.row--has-details .dt-type-numeric` → Amount
  - Expand `.button--dt-details`; panel section 1: `tr:nth-child(2) td` City/State/Zip (parse into
    three fields), `tr:nth-child(3) td` Occupation, `tr:nth-child(4) td` Employer (verbatim),
    `tr:nth-child(5) td` **Year-to-date aggregate [NEW]**; panel section 3: **Memo text [NEW]**,
    form/line, **Report year and Election type [NEW]** (both already on the panel — free capture;
    election type enables primary-vs-general splits later).
- **Refunds [NEW]:** negative/refund rows keep their sign, are flagged `refund`, and net against
  totals in Phase 5 — never dropped, never counted as positive giving.
- Paginate `.next` (100/page) until exhausted; apply Phase 0 exclusion post-filters.

### Conduit / double-counting rule **[NEW — materially changes the money math]**
Research-confirmed anatomy: one gift through ActBlue (C00401224) or WinRed (C00694323) produces up to
three itemized rows — (1) the conduit's own receipt (donor name+employer, memo names the true
destination; first row of live verification was exactly this: USAA employee, $10, recipient ACTBLUE,
memo "EARMARKED FOR HARRIS VICTORY FUND"), (2) the recipient's primary row (donor name+employer,
counts toward totals), (3) the recipient's MEMO_CD='X' pairing row (contributor = the conduit org —
carries no employer, so it does not enter an employer-filtered scrape).
Rule for our scrape **[UPGRADED — now exact, via the in-memory record fields]**:
- `memo_code == 'X'` / `memoed_subtotal == true` rows → `memo_leg`, excluded from totals (this is
  the FEC's own "not in committee totals" flag, no text-pattern guessing needed).
- Rows whose recipient committee is a conduit (ActBlue C00401224 / WinRed C00694323) →
  `conduit_leg`, excluded from totals; final recipient resolvable from `memo_text` +
  `receipt_type_full == 'EARMARK'` + `conduit_committee_id` on the recipient-side row.
- JFC pass-through attribution rows → `passthrough_leg` via memo fields, excluded; the
  terminal-recipient row counts.
- Before the rule freezes: a **50-row reconciled hand-check sample goes to Paul** (default adopted).

### Coverage caveat (for interpretation, stated in every Phase 5 output)
Itemized data covers donors over $200 aggregate (plus conduit gifts of any size). Rank-and-file
payroll giving to the company PAC below that is visible only in the PAC's **Unitemized individual
contributions** line — captured in Phase 3 so Phase 5 can report the size of the blind spot.

---

## Phase 2: No Quarter

1. **Variant pass** — remaining Phase 0 variants through the Phase 1 procedure; dedup via the key.
2. **Contributor-name pass** — deselect employer; for each distinct Phase 1 contributor, search
   `#contributor_name`; collect rows missing from Phase 1 (gifts filed with blank/other employer).
   **[CHANGED] Match ladder** (v1's rules formalized; default adopted):
   - Name normalization first: FEC "LAST, FIRST" all-caps format; suffixes (JR/SR/II/III/IV)
     stripped for matching but retained (a JR/SR pair at the same address is two people — suffix
     mismatch blocks AUTO and routes to REVIEW). **[NEW]**
   - **AUTO:** exact name + same zip
   - **HIGH:** exact name + same city, or nickname-map name (standard table: Tim↔Timothy,
     Dick↔Richard, …) + same zip
   - **REVIEW QUEUE:** nickname + same city; exact name + same state + similar occupation
     (normalized: "SVP" ≈ "SENIOR VICE PRESIDENT"; "EXECUTIVE" ≈ "CEO" only at REVIEW); any
     employer contradiction ("IF there is a Contradiction in company, flag it")
   - Weaker: discard. Review rows land in `review_queue_identity.csv` for Paul; the pipeline never
     silently resolves an identity.
3. **Company-as-contributor pass** — each company variant into `#contributor_name`, catching
   **corporate treasury giving** (how corporate money to Super PACs appears) and name/employer field
   swaps. Stored in a separate `corporate_giving` table — never blended into employee-tier math
   (default adopted).

Loop (Phase 1 → Phase 2) per company per cycle; then next company until the sheet is covered.

---

## Phase 3: Digging Deeper

Company-PAC financial summaries per covered cycle.

- **PAC identification [CHANGED]:** primary key = the FEC committee record (a corporate PAC lists its
  **connected organization**; verified USAA's PAC files under the legal name "United Services
  Automobile Association") + concentration of Phase 1 employee money. v1's rule of thumb stays as
  sanity check. Confirmed list → `company_pac_register.csv` → **Paul approves before Phase 3 runs**.
  (v1 referenced Phase 4's gold labels here — a phase-ordering contradiction, now removed; each
  phase depends only on earlier ones.)
- **Extraction [CHANGED — v1's selectors verified broken]:** the nth-child selectors collide
  (`.level--1 .t-mono` returns both Total receipts AND Total disbursements; the mapped Operating
  Expenditures selector actually grabs Total contributions; two metrics were mapped to the same
  nth-child(7); positions shift by committee type). Fix: select by **row label text** on
  `.simple-table__row` (label + `.t-mono` value). Extracted lines: Total receipts · Total
  contributions · Total individual contributions · **Itemized individual contributions · Unitemized
  individual contributions [NEW]** · Total federal receipts · Total disbursements · Operating
  expenditures · Contributions to other committees · Beginning cash on hand · Ending cash on hand.
- Cycle via `#summary-cycle` (verified), per covered cycle.

---

## Phase 4: Rally the Troops

### Contributor tiers (from occupation string; uppercase-normalized keyword table; default adopted)
- **Chief Officers:** CEO, CFO, COO, CIO, CTO, CLO, CMO, CHRO, PRESIDENT, CHAIRMAN, CHIEF *,
  FOUNDER, OWNER, EVP, EXECUTIVE VICE PRESIDENT, GENERAL COUNSEL
- **Middle Management:** SVP, VP, VICE PRESIDENT, DIRECTOR, MANAGER, SUPERVISOR, HEAD OF, LEAD,
  PRINCIPAL, PARTNER
- **Employees:** everything else recognizably a job (AGENT, ANALYST, TELLER, ADJUSTER, UNDERWRITER…)
- `UNCLASSIFIED` bucket for the rest — never guessed into a tier. RETIRED/HOMEMAKER/NOT EMPLOYED →
  `NON_EMPLOYEE` flag (usually spouses/noise; sample to Paul).
- **Known-executives override [NEW]:** a Phase 0 register (seeded from the template workbook's CEO
  column) pins named individuals to Chief Officers regardless of their occupation string.
- **Banking quirk (default adopted — flip if wrong):** in Community Banks and Credit Unions, bare
  VP/AVP/VICE PRESIDENT → **Employees** (rank-and-file titles there); SVP/EVP and true C-suite stay
  management. Config switch `vp_rank_and_file_industries` makes this reversible in one line, and
  Phase 5 reports the tier splits both ways once so the effect is visible.
- Tier is assigned per row (people change roles across cycles); Phase 5 uses tier at donation time.

### Recipient categories (assigned by committee ID, never name-matching; registers from Phase 0)
Company PAC · Trade Association PAC · Leadership PAC (R/D via Lean) · Super PAC (R/D/none via View) ·
Carey (R/D/none via View) · **Candidate committee** (needed for "% directly to candidates"; party
from the FEC candidate record — ground truth, not a lean label) · **Party committee** ·
**Joint fundraising committee** · **Conduit** (surviving tagged legs) · **Other/Unknown** **[NEW
buckets]**. ~~527s · 501c~~ **[EXCLUDED per decision 2026-07-18]**.
Registers are cycle-aware `(committee_id, name, category, lean, cycle, source)`.

### Party lean assignment
Candidate committees → candidate's party. Leadership PACs → CSV Lean. Super PAC/Carey → workbook View
(C→R, L→D, X→none). Party committees → their party. Company PACs & trade associations → **no lean**
(they are the vehicles under analysis; their partisanship is *measured* in Phase 3/5, not labeled).

---

## Phase 5: Taking a Step Back

All computation on the deduped, conduit-resolved RSQLite tables; output = `Mentat.xlsx` with
pivot-ready sheets + a methods sheet stating every formula and denominator in plain terms. Quarter =
calendar quarter of receipt date. 2025-26 marked partial in every view.

Per company → aggregated per industry, per quarter and per cycle:
- **Top recipients** (final recipients; conduit legs collapsed)
- **Top donors** per industry (identity from the Phase 2 ladder)
- **Partisan split & drift** — share of categorizable dollars to R-side vs D-side recipients,
  quarter over quarter; drift = change vs trailing baseline. Uncategorized dollars are their own
  reported line, never silently dropped.
- **Allocation percentages** (denominator = the group's total FEC-visible itemized dollars in the
  window, conduit-deduped; stated on every table): % company PAC · % Super PACs · % directly to
  candidates · % Careys · % party committees **[NEW]** · % leadership PACs **[NEW]** · %
  trade-association PACs **[NEW]** · % other. ~~% 527s · % 501c~~ **[EXCLUDED]**
  - Computed separately for Chief Officers / Middle Management / Employees, then aggregated;
    corporate-treasury giving reported alongside, never blended.
- **Partisan differences within each category** (e.g. employee Super PAC money: R vs D share).
- **Company-PAC health panel** (Phase 3): receipts, itemized/unitemized split, contributions to
  other committees, cash position, per cycle.
- **Cross-industry comparison [NEW]** — the template workbook's endgame: total political spending
  per industry normalized by industry size (dollars per $T of assets, denominators from the
  template's Sheet1 with its source links preserved on the methods sheet), so a $2.4T industry and
  a $3.8T industry compare on footing.

Math note: formula design delegated to the builder ("you have domain over the mathematical
equations"); every formula appears in the methods sheet.

---

## Raw-extraction output contract & QA gates **[NEW, 2026-07-18 — from the template workbook]**

Template: `C:\Users\pgoff\OneDrive\Documents\Spending Report template.xlsx`.

- **Shape:** the generated `spending_report.xlsx` preserves the template's four exact sheet names:
  `Stats (XXX Company)`, `Employees (XXX Company)`, `Raw Inputs (XXX Company)`, and `Sheet4`.
  The Stats sheet preserves the six year blocks (2026 through 2021), ten company/summary rows per
  block, and all 83 metric columns in their original order.
- **Employees sheet:** header row
  `Employee Name | Employee Title | Company | Zipcode | State`.
- **Raw Inputs sheet:** NO header row, with columns
  `Name | Recipient | State | Employer | Date | Amount`.
  Recipient shows the display name for readability; the committee ID remains in the DB and drives
  categorization.
- **Attribution gate:** only countable rows with `company_match_status='accepted'` enter the
  Employees and Raw Inputs sheets. Exact verified aliases are accepted; ambiguous short employers
  require an identity cross-reference; unrelated substring collisions are rejected and audited.
- **Calculation contract:** `Sheet4` contains the normalized fact rows, exact Raw Input/Employee row
  pointers, classification provenance, and formula definitions. All 5,822 Stats calculation cells
  reference one exact, bounded Sheet4 fact range. Total-industry share = company / ten-company
  annual total; category share = category / relevant company or employee-tier total; Partisan Split
  = R / (R + D), blank at zero denominator; Average = arithmetic mean of ten company rows; Change =
  current average minus prior-year average in the same units. Unsupported Phase 3 Company-PAC facts
  stay blank.
- **Acceptance fixture:** the template's Crypto sheet already contains **1,189 hand-extracted rows
  (through 2026-05-31)**. QA gate: the pipeline's crypto extract must reconcile against Paul's
  manual rows — every manual row matched (or explained), every pipeline-only row sampled for
  spot-verification. The scraper does not graduate until this reconciliation is clean.
- **Internal reconciliation gate:** per company-cycle, Phase 1's summed gifts *to the company PAC*
  must not exceed the PAC's own "Itemized individual contributions" line from Phase 3 (the PAC also
  takes non-employee money, so ≤ + tolerance, not equality). Gross violations halt loudly.
- **Selector-drift canaries** (engine section) run on every page; any failure stops the run.

## Build & graduation order **[NEW]**

1. Build Phase 0 → Paul approves the printed rosters/registers.
2. Build the Phase 1-2 collector → run **Crypto first** (smallest industry AND the one with the
   acceptance fixture) → reconcile against Paul's 1,189 hand-extracted rows → scraper graduates.
3. Fan out to the remaining four industries in tranches (checkpointed; runs survive sleep/shutdown
   and resume where they left off — but elapsed time depends on how often the machine is on).
4. Phase 3 after Paul approves the company-PAC register; Phases 4-5 close out, with the conduit-rule
   50-row hand-check and lean-label queues along the way.

Operational notes: the scrape data directory is **gitignored** (code + config committed, data not);
Phase 5's workbook write requires **Excel to be closed** (standing environment quirk).

## Defaults adopted under "fix all of those" (2026-07-18) — veto any

1. Berkshire/Hyundai/PACCAR subsidiary **roll-ups to parent** with per-subsidiary breakdown kept.
2. **Identity match ladder** AUTO/HIGH/REVIEW as specified; nothing below HIGH auto-merges.
3. **Company-as-contributor** read as corporate-treasury giving; separate table.
4. **Conduit rule** = final-recipient attribution (drop conduit/pass-through legs); 50-row
   hand-check to Paul before freeze.
5. **Recipient taxonomy** gains Candidate/Party/JFC/Conduit/Other buckets.
6. **Banking VP quirk**: VP/AVP → Employees in Community Banks & Credit Unions only (config-
   reversible; both-ways report shown once).
7. **View "X"** in the category workbook = no lean assigned.
8. **2025-26 leadership PACs**: 2023-24 register carried forward provisional + live designation-"D"
   detection; lean-labeling queue to Paul.
9. **Home**: `mentat/` module inside Melange Systems (own data dir + config; not wired into the
   Moneyball chain or audit_full). Working store RSQLite; Excel as output.
10. **Workbook-label precedence**: Paul's labels beat FEC type codes; disagreements queued, never
    auto-resolved.
11. **Output workbook contract:** exact four-sheet `Spending Report template.xlsx` layout, with
    headerless Raw Inputs ordered Name | Recipient | State | Employer | Date | Amount.
12. **Known-executives register** seeded from the template's CEO column, spelling-normalized via
    mapping (originals preserved).
13. **Refund rows** kept signed, flagged, netted in totals.
14. **Suffix rule**: JR/SR/III mismatches block auto identity merges.
