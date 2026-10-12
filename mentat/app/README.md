# Mentat collection app

**Chart Studio** retains the original interface and now provides verified
contribution charts and maps through its existing field shelves.
See [its accounting and usage guide](CONTRIBUTION_STUDIO.md) for data sources,
accounting bases, field shelves, filters, ZIP overlays and record inspection.

The console uses the palette and typography pairing from
[America's Credit Unions](https://www.americascreditunions.org/), inspected on
September 8, 2026: plum `#342649`, ivory `#F7EDE4`, gold `#ECA91C`, purple
`#57447B`, and teal `#327A7A` / `#9ED4D5`. The theme is in `acu-theme.css`;
`theme.js` shares its colors and font stacks with charts and image exports.
Headings prefer locally installed **Freight Text Pro**, with **Georgia** as the
offline fallback. Controls, tables and chart labels prefer **Articulat CF**, with
**Arial** as the offline fallback. No Adobe Fonts kit, remote font download, logo,
or organization account flow is embedded in the application.

Launch the local console from PowerShell:

```powershell
& .\mentat\app\Start-MentatApp.ps1
```

Open http://127.0.0.1:8802. The **Loading** tab accepts a sector, companies,
and alternative names directly, or CSV, TSV, and XLSX uploads. Enter each alias
on its own line; a comma stays part of a name. Add/remove companies, click
**Review roster**, choose cycles, and click **Start collection**. Reviewing
or uploading alone makes no FEC requests. Aliases group under a canonical
company; they are not subsidiary or ownership relationships.

Manual drafts persist in this browser's local storage, without credentials.
Editing a draft invalidates its previous review and disables collection until
it is reviewed again. Validated rosters persist on the server through the same
readiness checks and isolated runner as uploaded workbooks. In **Collection**,
select a ready roster and use **Continue collection setup** to resume it.
The **PAC evidence** tab defaults to independent saved-snapshot selection. Choose
the company snapshot, election cycle, and a committee available in that cycle.
**Follow Graph selection** optionally restricts evidence to Graph's companies and
cycle; its configuration controls remain available when no snapshot matches.
**Refresh snapshots** discovers newly saved evidence. Company matching normalizes Unicode, whitespace
and case; it does not guess corporate aliases or merge different legal names.

The table format uses `canonical_name,alias,industry,exclusion`; only
`canonical_name` is required. Repeat it for every alias. The included template
contains illustrative names. Existing Mentat roster workbooks are also accepted.
Ambiguous aliases fail the existing roster readiness check and cannot start.
`sector` is accepted as an alternative to `industry`; if both are supplied,
their values must agree. Manual entry rejects duplicate canonical companies.

The console invokes the existing resilient R/PowerShell runner. It snapshots
`R/`, `tools/`, `inputs/`, and `mentat.R` into a private runtime for each upload,
with the uploaded roster installed as that runtime's `Company Name.xlsx`.
FEC bulk snapshots include only the selected election cycles, plus shared metadata
and those cycles' evidence receipts. Report input checks use the same cycle scope.
The snapshot contains independent file copies so workers cannot modify the source files.
This keeps collector roster resolution and readiness validation aligned.
Existing source inputs and collection databases are not replaced.
The app sets `MENTAT_CUSTOM_ROSTER=1` for its workers, allowing the R importer
to read a roster containing only the uploaded industries. Without this flag,
the standard importer's five required industry sheets remain required.

Final workbooks and all report viewing use one authoritative folder:
`C:/Users/pgoff/OneDrive/Desktop/Mentat Complete Files`. Its location is set by
`mentat/config/report-directory.json`, with `MENTAT_REPORT_DIRECTORY` as an explicit
override. There is no fallback to Downloads, cached reports, or historical run
workbooks. The server works from this directory and reports it in `/api/health`.
Graph, Comparison, and the worksheet viewer select the folder through
`/api/report-sources`; their industry names come from the exact workbook filenames.
Banking and Community Banks therefore remain separate choices. The visible Graph
scope names the selected company, employee level, funding view, and cycle beside
the headline total. A Chief subtotal is not labeled as the whole industry total.

Downstream PAC views follow all reachable transfers in the saved PAC snapshots,
once per committee and election cycle, and disclose missing destination snapshots.
For committee/cycles with saved snapshots, the latest coverage replaces the
workbook's independent-spending partition. Explicit candidate gifts replace their
matching workbook gifts; other workbook gifts remain available.
The drawer lists every PAC–candidate relationship, its route, amount, and share of
that PAC's spending in the selected direction. Concentration is observed support
or opposition, not proof that a candidate controls a PAC. Source hashes and filing
coverage dates accompany the API response.

The spending map consolidates the saved network's reported disbursements and
subtracts transfers between its committees to avoid counting those dollars twice.
Unexpanded transfers and unlocated amounts stay explicit. Districts show House
candidate activity; states include all spending kinds by default. Heat colors and
the legend rescale to the positive range of the selected PAC, layer, and filters.
Saved coverage is not a certification of complete FEC filing coverage.

Collection writes its final workbooks to the same folder. Run state, raw exports
and runtime snapshots remain under `%LOCALAPPDATA%/Mentat/console`.
The collection monitor also discovers the latest 15 runs
in `mentat/.cache/resilient-industry-runs`. It refreshes every three seconds;
the source runner updates on its own checkpoint schedule. A stale update is
explicitly labeled and does not prove the worker is still running.
Historical collection metadata remains available for monitoring. Its outside-folder
workbook links are rejected; it cannot supply a hidden alternate report source.
Loading selects the newest saved sector/cycle before opening superseded databases.
A newer failed collection still takes precedence over older verified coverage.
Coverage checks index companies and evidence within each inspection; key status uses
one fresh encrypted-store read per refresh, and the report catalog scans the folder once.

Select a worker to see its latest 50 API checkpoints and 25 stored records in
**Activity Log: Person | Amount | Position | Title | Recipient**. Incoming position
labels use the existing report title rules; final reports can additionally use
executive/entity evidence. Missing titles remain unreported.
These are provisional and may contain overlapping searches or filing versions;
they are not final giving totals. Final workbook links appear only for industries
whose runner status is `succeeded`. Collection, retries, merge, coverage gates,
and reporting retain the existing runner's behavior. Recovery attempts are shown
separately. The interface does not expose manual cancellation or resumption.

FEC keys entered in the interface are saved with user-scoped Windows DPAPI
encryption in `%LOCALAPPDATA%/Mentat/credentials/fec-keys.dpapi`, outside Git.
Workers receive enabled keys in their environment. The app never puts keys on
a command line or returns them to the browser. Rejected keys remain encrypted
but disabled; transient failures do not disable keys.
Alternatively set `FEC_API_KEY` / `FEC_API_KEYS` before launch or pass `-KeysFile`.
R 4.6 and the existing Mentat dependencies remain required. `-PythonPath`,
`-RScriptPath`, `-Port`, and `-NoBrowser` customize the launcher. Without
`-PythonPath` the launcher runs Mentat's own venv, `%LOCALAPPDATA%/Mentat/venv`,
built from `mentat/requirements-console.txt`; when that venv is missing it falls
back to the Codex app's runtime Python, then `python` on PATH, and warns. A venv's
`python.exe` is started as its base interpreter with `__PYVENV_LAUNCHER__` set,
so the server is one process (`tools/mentat_python.psm1`).

Each desktop launch checks the server's loaded code hash, report directory and
read/write mode before reusing it. The hash covers every Python module in
`mentat/app`, not server.py alone. A server of this same project, data root and
report folder that is running older code, is not read-only, and reports
`busy: false` is stopped by process id and replaced on the SAME port, with the
reason printed. A busy server, a read-only preview, a server too old to report
`busy`, and any port whose owning process is not a `server.py` process are all
left running, and the launcher opens another local port with a printed notice.
An open window titled MENTAT is not sufficient evidence that its server is
current; the console's own status bar now states the build, port, start time
and process id it is talking to, and offers Restart when the code on disk has
moved on.

The server binds only to loopback and checks local host, origin, and a session
token for changes. This is a local single-user application, not a remote hosted
service. The one exception is the hosted, read-only console behind Cloudflare
Access: `BIND_HOST`, `ALLOWED_HOSTS`, `ACCESS_AUD`, `ACCESS_ISSUER` and
`ACCESS_JWKS_FILE` (see `access_gate.py`) turn it on, it refuses to start
without `--read-only` or without `MENTAT_STATE_WRITE_ROOT` (exit 2), it serves only an allowlist of read routes
(`HOSTED_GET_ROUTES` and the analytical POSTs in `server.py`; any other route is 403), the two
`/records` routes, which return donor rows, and any query or worksheet that names a person-level field or column
(contributor, street, city, ZIP, employer, occupation, date, source ID, the audit and reported fields; the
`Employee List` and `Cycle Exceptions` kind of sheet) only to an Access email listed in
`MENTAT_DONOR_ROW_EMAILS`, and `MENTAT_STATE_WRITE_ROOT` moves the caches it writes and refuses every other write
under the checkout or the report folder (`requirements-hosted.in` lists its dependencies). That write guard covers the
checkout and the report folder only; in the container the root filesystem and the data mounts are read-only at the OS
level, which covers the rest. At startup `main()` also asks `access_gate.assert_bind_consistent` about the address it
really bound, and exits 2 before serving if the gate refuses it. `/api/flow` drops the
same person-level keys from its records (`FLOW_PERSON_KEYS`, counterparty, the filing pointers and the free-text
`purpose` included) for an email not listed there, and every hosted JSON body is scrubbed of server paths and OSError
text. `/api/comparison` drops its Person subjects (the report's employees: name, company, totals) for an email not
listed, and refuses a query that selects one (`kind=Person` or a Person subject id) with 403. With none of those set
nothing changes.

A worksheet is person-level, and refused to an email not listed, when a header of its own header row (or of the
`header` row asked for) has any of these words: name, person, people, contributor, donor, employee, payee, contact,
email, phone, address, street, city, zip, employer, occupation, title, given, surname (`report_insights.PERSON_HEADER`;
whole words, plurals included, camelCase split, digits ignored). Only the headers in `report_insights.AGGREGATE_HEADERS` are
excepted, each checked against the real report workbooks: the tier label (`Contributor Tier`, `Employee Tier`), the
per-tier giving columns (`Employee`, `Employee %`, `Employee D`, `Employee R %`) and the contributor counts
(`Contributors`, `Distinct Contributors`). `Name / Recipient` is not excepted: on the Top Contributor rows of the
`* Summary` and `<Company> View` sheets it holds the contributor's name (and `Reference Key` the name key); those sheets
are redacted, not refused (next paragraph). Every other sheet with a person-level header is refused.

Decision (Claude's default, pending Paul's governance ruling): a hosted viewer who is not in
`MENTAT_DONOR_ROW_EMAILS` gets the `* Summary` and `<Company> View` sheets with their contributors' names blanked, not
a 403. `report_insights.person_level_sheet` returns `ALLOW`, `REDACT` or `REFUSE` (the most restrictive over the sheet's
own header row and the `header` row asked for): `REDACT` when `Name / Recipient` is the sheet's only person-level header
and it has exactly one `Section` column, `REFUSE` for any other person-level header (`Employer`, `Person Key`, a second
`Section`, no `Section` at all), `ALLOW` for none. `without_contributor_names` in `server.py` (applied to the response only;
`worksheet_data` and its cache are unchanged) sets `Name / Recipient` and `Reference Key` to null on every row whose
`Section` is not `Top Direct Recipient`, `Top Downstream Beneficiary` or `Company Total` (`RECIPIENT_SECTIONS`; an exact
match, so `Top Contributor`, a blank, a differently spelled or a new section is blanked), keeps every other cell and the
row count, and recomputes those two columns' profile (`missing`, `distinct`) from the rows sent. A `REDACT` sheet read at a
`header` row that is not its own has no single `Section` column to go by and is refused. A listed email and local mode get
the sheet as built. Checked against the 11 workbooks in `Mentat Complete Files` (2026-10-12): the Section column holds exactly
those three values and `Top Contributor`; 106 sheets (the `<cycle> Summary` and `<Company> View` sheets of nine workbooks)
are `REDACT`, 52 `REFUSE`, 122 `ALLOW`. What stays on a `Top Contributor` row is its rank, amounts, party split and
`Relationship` label, which carry no name.

Decision (Claude's default, pending Paul's governance ruling): a hosted viewer who is not in
`MENTAT_DONOR_ROW_EMAILS` learns no individual contributor's identity from `/api/flow`
(`without_individual_donors` in `server.py`, applied to the response only; `flow_data.py` is unchanged). Every
donor node (`donor:<NAME KEY>`) that is not explicitly an organization or committee (FEC entity type ORG, CAN, PAC,
CCM, PTY, COM or ORG_PAC; so IND, an untyped one whatever its name looks like, and a type not listed here: where
`flow_data.committee_flow` draws an untyped name without a comma as an organization, the hosted body treats it as a
person) becomes one `donor:individuals` node labelled "Individual contributors (N)" with its amounts summed and no
name as filed or other per-person field; the links into it are summed, so every total is unchanged. Named
organizations, PACs, committees and the unitemized node stay as built. The
"Other" bucket keeps its label and `member_count` but not its member list, and the refunded sink's details are one
generic sentence. A listed email and local mode get the body as built.

Startup self-check (by hand, no code): the portable index tokens name each data input by its relative path, size and
`mtime_ns`. Copy the report folder and the contribution sources to the host in a way that keeps sub-second
modification times (`rsync -a`, `tar`, `cp -p`; not a zip or a transfer that rounds to seconds), then compare one
file's mtime to the nanosecond on both sides (`stat -c %.9Y`). A rounded copy names a different source, every analytical
index then looks stale, and the hosted console, which never builds one, refuses it. Closing the
browser does not stop collection. Keep the app server
running for its process exit receipt; underlying runner receipts remain available
if the app restarts. A failed startup's diagnostic is in the upload's
`launcher.log`. No collection starts until the user presses the start button.

The PAC evidence panel discovers `mentat/.cache/pac-evidence/*/evidence.json`.
It keeps company receipts, exchange conversions, committee transfers, and
support/opposition activity separate. Candidate names remain as filed beside
their registered names; differences go to review rather than automatic merges.
Changing its cycle or committee only filters a saved snapshot. **Configure a new
snapshot** accepts company, aliases, committee IDs and election years; **Use selected
snapshot settings** copies the current configuration for editing. **Collect new
snapshot** explicitly starts the existing collector in a new directory. Progress
survives page reloads, and a completed snapshot becomes selectable automatically.
The dates shown distinguish the evidence
snapshot from the financial summary's reporting period.

Create an evidence snapshot with the bundled Python runtime:

```powershell
python mentat/tools/pac_evidence.py --company 'Harborlight Credit Union' --aliases 'HARBORLIGHT CU' --committees '<FEC_COMMITTEE_ID>' --cycles 2024,2026 --output mentat/.cache/pac-evidence/harborlight-credit-union-20260908
```

Use a new output directory for a fresh collection. Existing directories resume
from hash-verified API responses. The reusable collector verifies exact counts
and pagination, excludes 24/48-hour notices from regular-report IE totals,
checks those totals against financial summaries, and preserves unresolved
coverage. Observed evidence does not assert donor-specific traced dollars. Committee
IDs explicitly set its network boundary; outgoing destinations beyond that
boundary are recorded in `unexpanded_committees`.

The evidence collector also produces `cash-estimates.json` using
`tools/pac_cash_model.py`. This is a separate conditional company cash estimate;
it does not replace the earlier industry allocation in Excel. The sidecar binds
to both the source snapshot and observed evidence by SHA-256. Rebuild it offline:

```powershell
python mentat/tools/pac_cash_model.py --evidence-dir mentat/.cache/pac-evidence/harborlight-credit-union-20260908
```

The dated proportional pool carries funding shares through uniquely paired
transfers, charges operating costs, keeps unspent balances, and uses payment
dates for independent expenditures. Same-day arrivals precede outgoing payments;
this ordering is an explicit modeling assumption. It is not a probability of
influence or a causal estimate. All-source cash conservation and reported
closing balances must pass before numbers are returned. The automatic Form 3X
adapter withholds estimates for missing cash dates, financial gaps, unpaired
transfers, negative adjustments, donor-unresolved opening balances, assets that
need liquidation/offset links, or refunds that need original-donor links.
The normalized core supports identified refunds and donor-labeled opening cash;
the automatic adapter does not guess these labels. Incomplete saved snapshots
are withheld with specific reasons. Unknown is displayed as unavailable,
not zero. Method, current limitations and validation are documented in
`audits/mentat_20260908/PAC_CASH_APPROXIMATION.md`.

**Clear reviewed notices** acknowledges only the visible warning IDs. The
review receipt is stored outside Git in the console data directory. It binds
the warning text to the run and workbook SHA-256. Changed workbooks or warnings
require another review; hard failures cannot be dismissed. Workbook Data
Quality sheets remain intact.

Offline checks:

```powershell
python mentat/tests/test_collection_console.py
```

The tests use temporary inputs/databases, mock collection launch, and exercise
the real runner's plan-only mode and R's offline ingest when R is installed.
They do not call FEC or execute collection.

Loading is the default tab at desktop and narrow widths; each tab has a stable
URL fragment (`#loading`, `#collection`, `#evidence`). A separate quality notice
in Collection reads the saved coverage receipt, inspects
workbook headers for support/opposition columns, and compares the report hash
with the run's receipt. Header presence alone does not validate formulas or
downstream completeness. Source file modification dates are shown separately
from data-through dates.

**Report freshness** in Collection reads each workbook's `Source Freshness`
sheet, its companion `.audit/source_freshness.csv` when both workbook and CSV
hashes match the audit manifest, and hash-bound `source_freshness.csv` references
in `Source Receipts`. Referenced files must stay inside the report directory.
Missing, malformed or changed receipts remain visible as unknown. The panel
separates download/retrieval, last check, local source file modification, server
last-modified, and report file modification. A check is not a download. The run's
pinned source receipts can supply download and local file dates only for sources
whose family, cycle and SHA-256 match the report and the actual source file.
A newly activated source never upgrades an old report's freshness.

A receipt establishes a displayed verified data-through date only with a valid
ISO date in `verified_data_through`, or `data_through` together with an explicit
`data_through_verified: true`. Display headers such as `Verified Data Through`
are also accepted. This displays the receipt's assertion; it does not independently
certify completeness. Check timestamps, file dates, election cycles and latest
transaction dates cannot substitute for it. The panel counts verified source dates
and keeps overall report freshness unknown because source receipts do not prove
complete company/employer search coverage.

**Saved FEC coverage** in Loading and **FEC data-through by sector and cycle**
in Collection use the collection database and its pinned coverage receipt. The
Loading panel filters by the entered/reviewed sectors and selected cycles and
shows the newest saved collection for each pair, including failed collections.
These dates apply to that saved roster; editing names does not verify the new
roster. Coverage warnings are separate from dismissible report-quality notices.

`tools/collection_coverage.py` derives the earliest evidenced cutoff across every
company's employer and corporate searches. The existing
`validate_industry_coverage.py` producer stores a versioned `fec_coverage` record
in `coverage-receipt.json`, with a database SHA-256, evaluation time, per-cycle
cutoffs and their evidence. Reads validate the scope and database binding and
never rewrite the receipt or database. Missing, malformed, changed, incomplete,
or unreadable evidence remains **unverifiable**. Legacy receipts are inspected
read-only with a bounded SQLite query; they cannot acquire a cutoff from task
completion or file timestamps.

Dated source assertions can be supplied as `fec_date_evidence` in the fanout
receipt, or as rows in an optional `fec_date_evidence` SQLite table. Both use
these fields:

```json
{"industry":"Example sector","company":"Example company","cycle":2026,
 "kind":"employer","coverage_start":"2025-01-01",
 "verified_data_through":"2026-08-31",
 "verification_evidence":"Reference to the dated source coverage statement"}
```

An assertion must cover the cycle from its start and contain a real ISO calendar
cutoff and nonempty supporting evidence. Both `employer` and `corporate` need
evidence for every company; zero-result searches also need dated evidence.
Conflicting valid cutoffs use the earliest date. Invalid or missing evidence
prevents verification. These are source assertions, not independent FEC
certification; existing collectors that do not obtain such evidence remain
unverifiable. No cutoff is inferred from a maximum transaction date, API
checkpoint, download, report rebuild, or modification timestamp.

The latest valid stored transaction date is displayed separately, scoped by
sector, company and cycle across employee and corporate records. It is an
observation, never a coverage guarantee. Database and source file modification
times remain labeled file events. Active cycles are stale beyond a **30-day**
lag; past cycles require coverage through **December 31**. This UI policy is in
`STALE_AFTER_DAYS`, is displayed in both panels, and is recalculated on reads.

Offline verification (use the local Python executable if `python` is not on PATH):

```powershell
python -m unittest discover -s mentat/tests -p test_collection_coverage.py
python -m unittest discover -s mentat/tests -p test_validate_industry_coverage.py
python -m unittest discover -s mentat/tests -p test_collection_console.py
node mentat/tests/test_coverage_view.cjs
node mentat/tests/test_console_browser.cjs
```

The browser test uses a temporary local fixture and no FEC requests. Its Python
and Playwright locations use the existing `MENTAT_PYTHON` and
`MENTAT_NODE_MODULES` environment variables. The console suite's R ingestion
test additionally needs the installed Mentat R packages, including `DBI`.

**Graph** is a peer section of the console; Loading remains the default. Choose
an industry, company (or all companies), employee level and election cycle.
The employee labels are **Chief / Middle-Management / Employees**. They map to
the existing report tiers without changing any classification; Employees retains
the existing fallback for unrecognized titles. Corporate giving is a separate
giving scope, not an employee level. Choosing a level selects personal giving.

Graph separates **Scope**, **Analysis**, and **Details & methodology**. The compact
scope applies across **Overview / Recipients / Candidates / Companies**. Overview
shows net giving, direct candidate contributions, giving to recipient organizations,
a recipient-type chart, and an adjustable candidate preview. Recipient organizations
exclude candidate-category receipts; unresolved candidate links stay visible in the
candidate note, so the two summary amounts need not exhaust net giving.

Recipients contains the complete type breakdown and an adjustable organization
preview. JFC stays separate, as do Super PAC, Trade Association, Company PAC,
Leadership PAC, Carey/hybrid, Other PAC, Candidate, Party Committee and Unclassified
receipts. Companies shows company comparisons and the selected scope's personal
giving by Chief, Middle-Management and Employees, retaining unknown levels. A
single-company selection still shows its amounts and level breakdown without an
empty comparison placeholder. Click a type, organization, company or level to
filter; click a candidate for its included receipt groups. These can be aggregates,
not individual transactions. **View all** and **Full table** expose complete values.

The saved-run selector is in Graph's main scope controls and updates as new runs
arrive. Failed finalization is displayed as failed even for legacy run receipts
whose top-level stage said completed. A roster or fetched rows alone cannot become
a Graph source; the finalized report must pass the existing publication checks.

**Sources & freshness** opens a keyboard-accessible drawer containing the selected
reports' source receipts, retrieval timestamps, file modification timestamps,
verified data-through dates, unknown states and SHA-256 fingerprints. Retrieval
never establishes currency. Graph remains
`#graph`; old `#charts` links redirect to it. Reset restores the latest cycle,
all companies/levels/giving, and the Overview direct-contribution view.

The contextual **Export chart** control exports a selected breakdown as PNG or SVG,
or every value as CSV. Images contain the displayed row count, with scope, interpretation notes and
report fingerprints attached. Empty or unavailable views cannot be exported.

Guided graphs read the per-cycle receipt tables, using Recipient facts only in
workbooks that also contain duplicate Master and Employee summaries. Recipient
amounts must reconcile to Master totals by company, level, cycle and category.
Raw layouts use only explicitly included rows. Overlapping report cycles are
rejected. Direct candidate rankings require a unique candidate ID linked to a
principal or authorized committee; JFC, leadership and unauthorized links cannot
be promoted to direct contributions. Unresolved candidate-category amounts remain
visible outside the rankings.

The candidate-view selector separates **Direct contributions**, **Downstream PAC
activity**, and **Conditional industry estimates**. Downstream activity shows
the full reported activity of committees funded within the current selection,
counting each committee/cycle once regardless of how many selected companies
funded it. Support, opposition, and contributions/other activity remain separate.
It is not company-attributed money; company views may overlap. Industry estimates
require all companies, all levels and all giving, and the full allocation CSV
bound to the workbook by its audit manifest. Missing or changed receipts withhold
estimates. Partial downstream worksheet previews cannot produce rankings. These
views make no new source requests or model runs and do not rewrite reports.

**Compare subjects** opens searchable, draggable comparison columns for industries,
companies, people, recipients and candidates. Choose a saved run and industry,
then search and click or drag subjects into the display. Existing columns remain
when the source changes, allowing comparisons across industries and runs. Columns
can be reordered by dragging and removed individually. There is no selected-subject
cap; search renders 100 matches at a time and can reach the complete catalog.
Person totals use report person keys and the Employee fact family, independently
of Recipient/Master totals. Missing person-level breakdowns remain unavailable.
Subjects may overlap and are never added into a combined comparison total.
**Chart these subjects** sends subject-period values to the chart builder;
**Choose report worksheet** returns to a normal report. Comparison CSV includes
source report fingerprints. Existing worksheet limits still apply to charts.

**Create chart** immediately builds a chart from the selected report. Pick a
statistic and click **Bar**, **Line**, **Pie**, **Scatter**, **Histogram** or
**Box plot**; compatible columns are selected automatically. Every change redraws
the chart. **Over time**, **Year over year** and **Same-phase baseline** select the available
date/year field, and comparison time views offer a subject dropdown. **Change
data** contains run/worksheet selection; **More options** contains detailed
settings. The builder scans completed report worksheets locally and supports bar,
horizontal bar, box, pie, doughnut, line, area, scatter, histogram, waterfall,
Pareto and radar charts. Change the detected
header row and use **Rescan columns** for another table within a sheet. Filters
combine with AND; first/last worksheet row controls isolate subsections. Use
the automatic preview as you change options. PNG, SVG and chart-data CSV downloads include
the worksheet, filters, aggregation and report SHA-256 in their provenance.

Missing/non-numeric observations are excluded and counted, never converted to
zero; Count rows counts selected rows regardless of missing measures. Pie charts
reject negative values and zero totals. Box plots use linearly interpolated
quartiles and observed whiskers within 1.5 times the interquartile range. Formula
cells use their last saved cached results; this explorer does not recalculate
Excel formulas. Totals and overlapping summary rows can double count, so choose
the appropriate worksheet subsection. The current local limits are 50,000 rows,
200 columns, 60 groups, and 5,000 scatter points. Oversized requests require a
smaller worksheet or filters rather than silently creating a partial chart.
Time analysis supports calendar-year totals, year-over-year percentages,
absolute period changes and linear trend projections. Election cycles use two-year
steps and cannot be labeled year-over-year. Missing prior periods are not filled
with zero; percentage growth requires a positive prior base. Projections require
three consecutive observed periods and a horizon of 1–10 periods. They are
unvalidated least-squares trend scenarios, with no uncertainty interval; partial
years are not annualized. Filter partial periods and select one comparison subject
before time analysis. The subject dropdown handles this for comparison charts.
Derived and projected values remain labeled in exports.

Graphs do not call Higgsfield, FEC, or any external service; all scripts and
rendering are served locally, with no CDN dependencies.

Collection startup now acknowledges a **preparing** state before copying its
isolated runtime. Enabled keys pass through the process environment, never command
arguments. A concurrent key save finishes before launch. Chart source selection
uses a small report-list endpoint instead of scanning the full collection-monitor
snapshot. Active source folders are
copied once; obsolete local source folders are skipped when an active source root
is selected. Launch failures receive terminal timestamps, and completed runner
receipts restore terminal state after a console restart. Graph catalogs read only
workbook metadata, initial Graph loading requests the latest cycle in one query,
and new report fingerprints refresh the graph source catalog.

The timer separates elapsed time from estimated remaining time. Elapsed time
advances from a server sample using a monotonic browser clock and freezes on
terminal states. Stale checkpoints and exceeded estimates have explicit labels.
The runner no longer converts a missing estimate to zero or invents a three-minute
generator duration; total ETA remains unknown until the required phase timings
are available. These runner changes apply to newly launched runtime snapshots.

Collection workers claim one whole company at a time. When their own list runs
out, they take an untouched company from another worker's waiting list. The
manifest records ownership before dispatch; all cycles and retries stay with
that company's private database. Claimed companies remain pinned after a crash.
Initialization markers distinguish a dispatch that never started from resumable
page checkpoints. Older manifests preserve their existing ownership and resume
behavior. Each company starts a fresh R process, trading startup overhead for
safe handoffs and bounded process lifetime.

Recovery workers now default to automatic capacity sharing (`RecoveryWorkers=0`),
subject to the same overall coordinator limit as collection. A positive value caps
recovery concurrency. Exclusive database leases prevent write collisions;
split retries queue one invocation per database, allowing unrelated databases
to recover concurrently. The persistent per-key request ledger still enforces
API budgets. Coverage, raw-export and deterministic merge gates are unchanged.
The monitor shows waiting companies and handoff counts, and reads each worker's
current status rather than every historical company attempt.

HTTP fetch workers now share a request pool across all unfinished lanes. The
requested fetch capacity is independent of the number of database coordinators;
finishing a lane releases capacity for another lane's requests. Each request
leases an available API key and reserves its durable budget before sending.
Large unfiltered queries are divided into nonoverlapping date windows, including
dates outside the nominal cycle. Their combined IDs must be unique, match the
unsplit query's count, and retain previously checkpointed IDs before acceptance.
Child windows resume from their own checkpoints. One R coordinator still owns
each database's writes, classification and final storage.

The monitor counts actual in-flight HTTP requests and reports database
coordinators separately. Active workers can temporarily fall below the requested
count during checkpoint processing, quota waits, or when fewer independent
requests remain. Completed lanes do not lower the requested capacity.
Collection still advances through industries sequentially; completed-industry
workbook generation overlaps the next industry's collection. End-to-end timing
also includes API latency, R startup and workbook generation.

For inspection while an older console manages active collection, a separate
instance can run with `python mentat/app/server.py --port 8806 --read-only`.
All mutation endpoints are disabled in that instance. Restart the normal console
after active work finishes to load backend changes; refresh its browser afterward.
Do not refresh an old server against new assets before restarting it.

Additional offline regression checks (browser checks require local Playwright
and Chrome; `MENTAT_BROWSER_CHANNEL` can select another installed channel):

```powershell
python mentat/tests/test_report_insights.py
python mentat/tests/test_graph_data.py
node --test mentat/tests/test_chart_math.cjs
node --test mentat/tests/test_ui_analysis.cjs
python mentat/tests/test_comparison_data.py
python mentat/tests/test_worker_sharing.py
python mentat/tests/test_api_request_pool.py
python mentat/tests/test_api_company_cycle_fanout.py
python mentat/tests/test_resilient_orchestrator.py
& 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe' mentat/tests/test_fec_api_fanout.R
& 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe' mentat/tests/test_request_windows.R
node mentat/tests/test_console_browser.cjs
```

The browser suite starts a temporary loopback fixture and checks Graph's default
direct view, company/level/JFC filters, downstream directions, estimate withholding,
dataset switching, all 15 chart-builder types, PNG/SVG/CSV exports, escaped labels, narrow layouts,
keyboard navigation and dialog dismissal/focus restoration, and the
absence of external requests. `MENTAT_NODE_MODULES` and `MENTAT_PYTHON` override
the local test dependencies. Neither test suite reads credentials or collects data.

Rebuilt workbooks include committee cash-transfer paths through three transfers,
coverage, source receipts, and a separate conditional pro-rata estimate. These
estimates are not traced company dollars or lower bounds. Opposition stays
against its named target. ActBlue and WinRed are their own Conduit category
(since 2026-10-09; earlier rebuilds filed them under Carey, which is now only
the ordinary registered hybrid committees); ordinary registered PACs have a
separate Other PAC category. Historical workbooks keep their original evidence
and may predate these corrections.
The September 8 audit and repair evidence are in `audits/mentat_20260908/`.

Graph row counts are editable through **Rows per chart** or **Show all rows**.
The chart builder also exposes a row limit after filters, a worksheet starting
row, and a selectable **Row count** statistic.

**Compare subjects → Who funded whom?** loads saved source-to-recipient
relationships. Select companies/committees and candidates or recipient committees;
search by name or FEC ID, and use receiver type to narrow the picker. Selections
remain when changing the search or receiver-type filter. Side-by-side bars,
horizontal comparisons and separate lines update immediately. Recipients per
view accepts 1–60; Next/Previous retain all choices. Chart and exports cover the
explicitly displayed page. Overall subject totals remain in a collapsed section.

Company-associated personal receipts, corporate receipts, committee support,
opposition and contribution/other activity stay separate. Candidate receipts
require an authorized unique link. PAC activity is counted once per committee
and cycle, without attributing it to funding companies. Missing pairs remain
unavailable; partial downstream worksheets are withheld with an explanation.
The comparison uses stored reports only and does not collect missing activity.
`node --test mentat/tests/test_funding_comparison.cjs` and
`python mentat/tests/test_comparison_data.py` cover the relationship calculations.

The workspace navigation groups Loading and Collection, Graph and Cross-reference,
and PAC evidence. It uses a left sidebar from 1100px and a horizontally scrolling
tab bar below that width. Arrow keys, Home and End follow the visible order; a
skip link moves focus into the current workspace. `workspace.css` contains this
layout and the compact controls. Report-folder selection and explanatory coverage
and cash-estimation text use disclosures; coverage status and reconciliation
warnings stay visible. Compare subjects opens a focused view with Back to graph.

Large workbook reads share an in-progress load for the same file, and Graph and
subject comparisons check that every report still matches its starting file
version before returning. If a report changes during a read, reload the view.
Collection worker paths are resolved before containment checks, so Windows
LocalAppData aliases identify the same run without allowing outside-run files.

Collection now separates In progress, History, and Archived. Archiving changes only
console metadata; it does not move or delete report files or checkpoints. Active
runs cannot be archived, and an archived saved roster must be restored before it
can start. Working folders show the internal collection directory and the configured
completed-report directory.

PAC evidence defaults to **CSV file → PAC → election cycle**. The local importer
accepts up to 5 MB / 50,000 rows, retains original fields and filing duplicates,
and keeps channel totals separate. Raw observations are paginated, while totals
cover every selected row. Imported files live only in the server session. Saved
FEC snapshots remain available through the Evidence source selector. **Split with
PAC evidence / Split with Graph** opens both panes; narrow screens stack them.

Chart types now choose compatible fields and preserve an eligible selected
statistic. Axes include the field name and aggregation. Scatter renders every
loaded valid pair with batched SVG paths; the former 5,000-point rendering cap is
removed. Workbook loading still accepts at most 50,000 worksheet rows and rejects
larger sources explicitly. Dense chart tables preview 200 points, while chart data
CSV exports retain all points.

Over time shows period totals (or the selected aggregation). Year over year shows
percentage change from the prior calendar year; election-cycle data uses period
change instead. Same-phase baseline uses a same-election-phase historical baseline: the latest
completed value from the corresponding phase of the four-year calendar. It needs
eight consecutive closed years or four closed two-year cycles, with at least two
observations per phase. Current, explicitly partial and missing-value periods do
not train it. No fixed presidential uplift, extrapolated growth, or calibrated
prediction interval is implied. Unknown historical coverage stays disclosed.
The benchmark follows the [seasonal naïve method](https://otexts.com/fpp3/simple-methods.html).

**Saved views** live under the console data root at `studio\views\<32 hex>.json`, so a
second console opened on the same data root lists the same views. `GET /api/studio/views`
returns them newest first with a sheet count in place of the sheets; `GET
/api/studio/views?id=` returns one view with its sheets and its active sheet; `POST
/api/studio/views` saves one and `POST /api/studio/views/delete` removes one. A view needs
a name of 1 to 80 characters and 1 to 24 named sheets, and its request body may not exceed
512 KB. Duplicate names are allowed. Deleting a view that is already gone still reports
success, so a repeated click is not an error. A read-only preview console refuses both
writes with "Saved views are read-only in this preview console; save from the original
console." and still answers the reads.

**The briefing tray** is one shared tray per data root: an ordered
`studio\briefing\index.json` beside one `<id>.svg` per item, capped at 40 items. `POST
/api/studio/briefing/add` takes the caption fields and the chart itself as an SVG string of
at most 6 MB; a forty-first item is refused with 409, and a chart that is oversized or does
not begin `<svg` with 400. `POST /api/studio/briefing/remove` and `POST
/api/studio/briefing/order` maintain the tray, and an order has to be a permutation of the
tray it reorders. `GET /api/studio/briefing/item?id=` returns the stored chart as
`text/plain`, never as `image/svg+xml`: an SVG served from this origin under an image type
is script-capable, and these are assembled from report text. The tray is read-only in a
preview console.

**The PowerPoint bundle** `vendor\pptxgen.bundle.js` is PptxGenJS 4.0.1 (MIT), the UMD
browser build from the npm registry tarball, copied unmodified; `vendor/LICENSE-pptxgenjs.md`
carries its licence. SHA-256
`4fb9eac5cfefb213e2d8743c2b7151025f31bfb3f834c73c12062916daa0f3f8`. The offline checks
assert that hash against the bytes the console serves at `/vendor/pptxgen.bundle.js`, not
against the file on disk. Load it with a plain script tag and read `window.PptxGenJS`; it
defines `window.JSZip` as a side effect and is not `require()`-able from Node.

**Publish phone snapshot** writes one self-contained page. `POST /api/publish/pocket` takes
dataset ids from the contribution catalog, runs `tools\pocket\extract_pocket_data.py`
against this console own port and then `tools\pocket\build_page.py`, and writes
`mentat-pocket.html` into a `pocket` folder inside the configured report folder, beside the
`pocket-data.json` and `pocket-integrity.json` it was built from. The repository template
`tools\pocket\mentat-pocket.html` is read, never overwritten. Job state lives at `<data
root>\publish\<id>\job.json` and is read back with `GET /api/publish/pocket?id=`; the
child output is kept beside it in `publish.log`. One publication runs at a time, a second is
refused with 409, and the console reports itself busy while one runs, so a restart will not
interrupt it. Uploading the finished page to a private link remains a manual step.
