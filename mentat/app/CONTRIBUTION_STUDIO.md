# Chart Studio: verified contributions

Open **Chart Studio** in the Mentat console, or navigate to `/#chart-studio` on the same local server. The original plum, ivory and gold interface remains the main workspace: toolbar, Data/Analytics pane, shelves, Marks, Show Me and sheet tabs. Choose **Verified contributions** for contribution analysis or **Report worksheet** for worksheet exploration. The earlier separate `/contribution-studio` prototype remains available as an optional route.

The workspace applies Tableau's field-driven level of detail to political contribution data. Choose an industry and accounting basis, place dimensions and measures on the shelves, then choose a chart. Colour, Detail, Label and Tooltip dimensions define the query grain. Supported interactive marks open their source records; **View source records** is also available. Saved sheets stay in the current browser's local storage.

## Accounting comes before plotting

The default **Published included receipt view** reconciles to the report's included receipts. It is an observed receipt basis, not a certification that each receipt is a different economic gift. The workspace keeps the following bases separate:

| Basis | Meaning |
|---|---|
| Published included receipt view | Included receipts from the pinned workbook export; the normal default. |
| Observed original giving (provisional) | The existing evidence engine's original-giving hypothesis, including unresolved decisions. This can exceed the receipt view because eligibility and money stages differ. |
| Supported original giving (gross subset) | Positive original giving supported by the current evidence rules. This is a gross subset, not a complete net total after refunds. A zero supported subtotal does not establish zero giving. |
| Raw source observations | Retained representations, including exclusions and repetitions. Do not interpret the sum as giving. |
| PAC activity on recipient receipts | A separate recipient-reported activity view, not a complete outgoing Schedule B ledger. |

Unresolved exposure is the sum of absolute raw amounts on unresolved observations. It can overlap amounts already counted and can include repeated representations. It must never be added to a selected subtotal, or treated as a confidence interval or a verified upper bound on giving.

The receipt identifies the industry, workbook, cycles, company filter and source fingerprints. Original-giving records retain decision reasons and evidence references. Raw observations remain preserved. The app does not change old workbook totals in place or treat an ambiguous receipt as proven zero.

## Source contract

The server discovers workbooks only in the configured report directory, resolved through `tools/report_directory.py`. A workbook's source fingerprint pins its export manifest and source payload. The reader verifies the internal source database or its explicitly documented recovery lineage before building a local SQLite analytical index. Client requests select a catalog dataset, never an arbitrary path.

For recovered databases, the server compares the complete financial record universe with the accepted snapshot of the original report source, including excluded observations. Added, missing, changed or conflicting financial identities block that source. Metadata repairs can therefore enrich ZIPs or filing evidence without silently replacing financial facts. Published cycle totals are also read directly from the current workbook and reconciled to the receipt basis. Responses are bound to the workbook and database fingerprints that passed these checks.

The index stores observation identities and separate integer-cent amounts for the accounting bases. Query aggregation happens on those records, not on repeated worksheet summary tables. A contributor ZIP comes from that observation's recorded ZIP; semicolon-separated employee ZIP lists are never exploded into copies of money. Industry and company summaries are not joined back onto the facts.

Index preparation is asynchronous. A large source can take tens of minutes on its first build while financial identities and evidence decisions are checked; progress remains visible. Later views query the prepared index. Missing or mismatched evidence produces a visible error. **Refresh verified data** discovers current pinned reports and invalidates the browser's result and field-member caches.

Queries now use a smaller, verified derivative of the analytical index. Every copied field is compared against the original observation; longer detail fields are read only when needed. The original accounting index remains unchanged. Population summaries and responses are cached with source, query-code and projection-code fingerprints, bounded storage, atomic writes and concurrent-request coordination. Changes to the source or its WAL/journal invalidate results. Display-only changes reuse the current result, and map geometry is cached separately from money queries.

## Filters, totals and exports

Source filters apply before grouping. Grouped measure filters apply after aggregation. The receipt's selected total covers source records before grouped filters; a separate line reports the amount remaining after grouped filters. Paging changes which marks are visible, not the accounting receipt. Distinct donor identity counts are computed from the selected source records rather than added from group counts.

The verified catalog exposes 94 fields: 76 dimensions and 18 measures. Measures include net and positive amount, negative adjustments, observation count, distinct reported identities/committees/companies, average, median, quartiles, population standard deviation, minimum/maximum amount, active days and observed date spans. Monetary values travel over the API as integer cents. Scatter requires two numeric measures; categorical circle views require one. Timelines use actual time spacing and leave missing periods unconnected. Histograms bin individual source amounts with configurable dollar widths, and box plots use source quartiles with explicitly labelled minimum–maximum whiskers. All 24 Show Me choices have verified-source implementations with field requirements. Reference averages and medians describe the displayed marks; source-level statistics are separate measures. Forecasts never enter the source totals.

The **Data Source** tab exposes all eight report choices, seven logical table groups, relationship rules and the complete typed field dictionary. **Fields from** narrows the Data pane by table. Reported metadata is related using source ID, industry and original source table; duplicate metadata variants remain lists on one row, preserving money and unmatched observations. Reported cumulative year-to-date amounts remain descriptive dimensions because they cannot safely be summed across receipts. Every report worksheet also remains available in worksheet mode at its own grain. This is a fixed, audited relationship model rather than a general-purpose database join editor.

CSV export fetches every matching group, including groups beyond the current page. Currency columns are labelled `_usd`; the CSV includes the dataset, accounting basis, workbook, query-engine and field-semantics fingerprints, analytical index, filters and histogram bin width. Source or query-version changes during export stop the download. ZIP values retain their leading zeros in the file; import ZIP columns as text in spreadsheet applications. SVG and PNG include the visible page range and source context. Exports are generated locally. Ongoing calendar cycles and years are identified when selected or displayed; this does not assert that collection is complete through today.

## Geography

Maps use Census ZCTA representative points for recorded ZIPs, with optional state, congressional-district and ZCTA outlines. Congress 119 and 120 are explicit choices. These are donor-origin maps; they are not recipient district maps.

Map queries include every selected ZIP/state group, independent of the ordinary chart page size. Missing locations retain their amounts in the unmapped subtotal. Alaska, Hawaii and territories appear in labelled insets. Layer visibility never changes contribution amounts. ZIP polygons load for selected areas or the viewport; deferred polygons are disclosed.

Recorded state validates eligibility for U.S. ZIP geography. Foreign, unrecognized and missing states remain unmapped, even when a five-digit postal code coincides with a U.S. ZIP. Eligible ZIP/state groups combine into one ZIP mark, whose drill-through retains the exact eligible state selection. Grouped filters apply to ZIP/state groups before that combination. Their dollars are never dropped from the accounting receipt.

ZIP areas approximate postal geography. A ZIP can intersect multiple districts or states. This workspace does not allocate a ZIP's full dollars to every intersecting district, infer a donor address, or manufacture a district spending total from polygon overlap. Geography requests contain only public ZIP identifiers and layer/viewport choices; contribution amounts stay in Mentat.

## Implementation

- `contribution_data.py`: source verification, indexing, accounting queries and drill-through.
- `contribution_query.py`: read-only query planning and bounded result caching; population totals remain independent of paging.
- `contribution_semantics.py`: typed fields, observation statistics and cardinality-preserving reported metadata relationships.
- `contribution_query_store.py`: verified compact query views, stable donor-identity encoding and concurrent preparation.
- `contribution_source_guard.py`: complete recovered-source financial identity checks and cached proofs.
- `contribution_workbook_guard.py`: actual published workbook totals and source proof enforcement.
- `contribution_geo.py`: official geometry, coverage and validated local cache.
- `index.html`, `chart-studio.js`, `chart-studio.css`: the original Chart Studio interface and verified-source integration.
- `chart-studio-contributions.js`: contribution field metadata, shelf-to-query translation and exact drill filters.
- `contribution-studio.html`, `.css`, `.js`: the optional earlier prototype.
- `contribution-plot.js`: native SVG charts, typed measures and accessible marks.
- `contribution-charts.js`: distribution, matrix, area, categorical circle, date-span and dual-axis chart families.
- `contribution-map.js`: native SVG geography and independent layers.
- `server.py`: same-origin local API with existing Host, Origin and session-token checks.

The earlier prototype's design brief used Higgsfield with a generic political-contribution workspace prompt; no private contribution data was supplied. Following the user's clarification, the main application retains its original UI and local fonts. The requested data and geography features are integrated into that interface.

Startup uses a light local session handshake; collection monitoring and saved-evidence scans start when their views need them. Static assets revalidate with content identities; larger text and geometry responses support gzip. The guided Graph creates detailed table rows when opened, preserving its charts and exports. The desktop launcher and managed workers avoid measured redundant probes and idle waits while retaining identity, budget and process-exit checks.

Measured results and validation for the restored interface are recorded in `outputs/chart-studio-original-ui-20260912/IMPLEMENTATION_AND_PERFORMANCE.md` at the repository root. First-time source verification and preparation still take longer than repeated charts.

The expanded field catalog, all four user-supplied Tableau references, primary-documentation corrections and source reconciliation results are recorded in `outputs/chart-studio-tableau-expansion-20260912/TABLEAU_REVIEW_AND_CHANGES.md` at the repository root.

The supporting Tableau/FEC/Census/HUD research and original bug reproductions are recorded in `outputs/chart-studio-review-20260912/MENTAT_CHART_STUDIO_REVIEW.md` at the repository root. That report describes the pre-fix review; the implementation above supersedes its affected chart-reader and renderer findings.
