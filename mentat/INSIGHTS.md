# Insights

Open the **Insights** workspace in Mentat. Industry favorites, comparable trends,
and change explanations are enabled initially. The other eight analyses are
optional. Switches persist in this browser; **Core three** restores the starting
selection. Lobbying disclosures and inflation adjustments are not included.

The panel design uses the synthetic [Higgsfield design reference](https://d8j0ntlcm91z4.cloudfront.net/user_3J1aVESwhbXLraSW8PtRFWWgpV2/hf_20260920_210727_c67f4e78-17a0-436d-abb3-87f21ae1e450.png)
generated for this workspace. The implemented controls and charts are local,
accessible HTML/SVG, with responsive cards, tables and source inspection.

The cleanup pass uses a second synthetic [Higgsfield layout reference](https://d8j0ntlcm91z4.cloudfront.net/user_3J1aVESwhbXLraSW8PtRFWWgpV2/hf_20260920_215056_7a99a3a2-2751-4214-9094-41ebba2a6408.png).
Comparison options and **Choose analyses** start collapsed. The three core cards
share aligned headings and source footers; **Table** opens exact values and
record links. **What changed** shows one selected funding channel at a time,
while its table and exports retain every returned channel. Method and coverage
notes remain expandable below each chart.

## What each analysis means

| Panel | Interpretation |
| --- | --- |
| Industry favorites | Observed recipient net amounts, positive-dollar shares, company breadth and persistence. An explicitly chosen comparison industry enables relative share comparisons. Employee-associated giving does not establish an employer's institutional preference. |
| Comparable trends | Full observed totals and totals at matching election-stage cutoffs are separate. No collection completeness is inferred from a calendar year ending. |
| What changed | Continuing, newly observed and no-longer-observed companies reconcile to the matched-period change within each funding channel. Unattributed companies remain separate. These describe records, not proven changes in intent. |
| Concentration & breadth | Positive-gross shares and concentration, with signed adjustments retained separately. |
| Relationships | Observed company–recipient continuity between the selected cycles. Missing coverage cannot prove a relationship ended. |
| Company portfolio overlap | Similarity of covered companies' recipient distributions within each channel. |
| Timing | Event-date monthly and quarterly patterns with same-phase historical context. Invalid or conflicting dates stay out of temporal measures. |
| Recipient profiles | Explicit, cycle-specific FEC committee–candidate links; no name-based identity guesses or allocation of receipts among linked candidates. |
| Outgoing spending | Saved Schedule B payments, vendor patterns and dated campaign financial totals. These have their own sources and accounting scope. Campaign finances are not selected-industry giving and must not be added to receipt totals. |
| Legislative context | Dated sponsorship/cosponsorship/action context through exact FEC-to-Bioguide links. Association does not establish influence or causation. |
| Forecasts | Conservative historical baselines, with rolling-origin errors where evaluable. Four consecutive explicitly complete historical cycles are required; source-vintage limitations and unavailable intervals remain visible. |

The historical-baseline Forecasts card is hidden by default; enable "Show the historical-baseline forecast card" under Insights More options to reveal it. This setting is saved in the current browser.

## Scope, evidence and exports

Choose the published industry, accounting basis, company and contribution-date
cutoff. Comparison controls set funding channel, baseline/current cycles and an
optional different tracked industry. The comparator includes its covered
companies, without borrowing the focal company's filter. It is not a market-wide
baseline. Banking and Community Banks remain separate datasets.

Amounts are calculated in signed integer cents. Different funding channels and
money stages remain separate. Tables are bounded for presentation; displayed
counts and notices disclose truncation. Metrics use every selected observation
unless an explicit capacity limit prevents the analysis, in which case no partial
totals are published. Source records are paginated and bound to the analytical
snapshot. Comparison rows expose separate baseline/current evidence.

CSV and JSON export the displayed analysis, its scope, accounting context,
source metadata and limitations. CSV money columns are explicitly cents; ratios
are labeled. JSON preserves the full returned envelope. CSV text is protected
against spreadsheet formula interpretation. Exports are not unbounded raw-source
dumps.

The local journal in `state/insights-history` records source snapshots and hashed
observation versions. It stores first/last inspection times, not donor names.
**First inspected here is not a filing or publication date.** A newly inspected
version may reflect an import, an identity correction, an accounting change or a
changed source record; it is not automatically a new gift. Failed or unstable
analyses do not commit a snapshot. An event-date cutoff cannot recreate evidence
that was publicly available at that date.

## Data integrations and operational limits

The giving facts come from the same verified published-report source used by
Chart Studio. Queries use the authoritative configured report directory and
refuse stale or mismatched source proofs. They never modify the published
workbooks, source observations, accounting classifications or Melange artifacts.

Recipient profiles use Mentat's saved FEC `cn`, `cm` and `ccl` masters. The other
context panels read Melange's `fec/legislators.csv`, `bill_repository_live.json`
and SHA-verified saved OpenFEC pages referenced by
`data/money/fec_refresh_current.json`. The default Melange root is
`~/OneDrive/Desktop/Melange Systems`; `MENTAT_MELANGE_ROOT` can override it.
These integrations do not automatically call remote APIs. Missing or limited
saved coverage is shown explicitly. The currently saved disbursement selection
may cover only specified transaction types, not a complete vendor ledger.

Additional immutable OpenFEC snapshots can be placed under
`inputs/analysis_integrations/<snapshot>/manifest.json` using the schema in
`app/analysis_integrations.py`. Source hashes, safe paths, exact identities and
date bounds are required. The adapter never treats a source manifest as proof
of complete historical collection.

The published contribution index presently supplies no trustworthy full-cycle
coverage contract for forecasting. Accordingly, the UI withholds forecasts even
when years contain some observations. The pure analytics engine supports
explicit completeness evidence for future verified integrations; a browser
request cannot assert that evidence. This also means current data does not
support historical point-in-time forecast validation.

Preparation runs in a bounded background worker. Verified results are cached
locally across application restarts; source, integration and code changes
invalidate them. Changing a selection clears
the prior view immediately and ignores obsolete replies. If source files change
during preparation, refresh after collection settles. A backend code change
requires a Mentat restart; reloading the page alone does not reload Python.

## Verification

Run the focused Python checks with:

```powershell
python -m unittest discover -s mentat/tests -p 'test_analysis*.py' -q
node mentat/tests/test_insights_browser.cjs
node mentat/tests/test_insights_controls_browser.cjs
python mentat/tools/render_console.py --check
```

The checks cover exact signed-cent conservation, matched cutoffs, source-record
reconciliation, invalid dates, missing identities, source changes, HTTP security,
saved-evidence integrity, forecasts, history rollback, UI races, exports, direct
navigation and responsive layout. Browser tests use clearly synthetic fixtures.
