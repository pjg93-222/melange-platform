# Giving Forecast feeds: OpenSecrets and IBISWorld ingest

This folder turns two kinds of outside data into the inputs the Giving Forecast refit needs, one industry at a time:

- **IBISWorld industry reports** (PDF or the Key Statistics Excel export) that you drop in an inbox become the two growth feeds the
  Sector Growth Study workbooks 06 (predicted growth) and 07 (actual growth) hold, with exactly their columns.
- **OpenSecrets** industry pages (totals, House and Senate candidate recipients, presidential recipients) become the same JSON
  "capture envelope" the existing 2026-09-27 captures use, so the refit reads them unchanged.

Everything is R (pdftools 3.9.0, xml2, data.table, jsonlite, readxl; curl only inside the fetcher). The refit's io, vault and Glass Box
emitter are reused from `..\refit\R`; nothing in `..\refit` is edited.

Run everything with `"C:\Program Files\R\R-4.6.0\bin\Rscript.exe" run_ingest.R --feed <feed> [--industry <id>] [--dry-run] [--run-id <id>]`.

| Feed | What it does |
|---|---|
| `ibisworld` | scans `inbox\ibisworld`, parses the reports, writes the 06/07-shaped feeds |
| `opensecrets-fetch` | the permission-gated polite page fetcher (section 3) |
| `opensecrets-pages` | parses OpenSecrets pages you saved by hand into `inbox\opensecrets\pages` |
| `opensecrets-bulk` | bulk-data route: verifies the category file, calibrates, then builds |
| `calibrate` | only the calibration gate of the bulk route |

Exit codes: 0 done (or waiting for a file from you), 2 some files need review, 3 stopped (blocked, gate closed, code not verified,
calibration failed), 1 error.

## 1. Where things live

| Name | Path | Holds |
|---|---|---|
| inbox | `%LOCALAPPDATA%\Mentat\forecast\inbox` (override `MENTAT_FEEDS_INBOX`) | your drops: `ibisworld\`, `opensecrets\pages\`, `opensecrets\bulk\`. Never modified, moved or deleted by the ingest |
| ingest runs | `%LOCALAPPDATA%\Mentat\forecast\ingest` (override `FI_FRT`) | `runs\<run_id>\` (outputs, `receipt.json`, proposals), `os_cache\`, `fetch_state.json`, `current.json` |
| vault | `%LOCALAPPDATA%\Mentat\forecast\refit\raw\<sha256>\<name>` | a byte copy of every file the ingest read, shared with the refit |
| Glass Box | `C:\Users\pgoff\.claude\glassbox\events` (override `RF_GLASSBOX_DIR`) | one event per stage of every run (`feeds.run.start` ... `feeds.run.done` or `feeds.run.fail`) |

All of these are outside git. Licensed content (IBISWorld reports, OpenSecrets pages) never goes in git; the tests use invented
fixtures.

## 2. IBISWorld (inbox only: no network, no login)

Download the report PDF yourself from your own IBISWorld session and drop it in `inbox\ibisworld`. The Key Statistics Excel export
may be dropped too; it is only a cross-check (its layout has not been seen yet, so it stays "review required" until you confirm a
first real export).

For property and casualty insurance (IBISWorld 52412 "Property, Casualty and Direct Insurance in the US") the pipeline waits for an
edition **published in 2025 or later**. One such edition supplies 2024 as a historical year (the actual series and the PAC cycle 2024)
and, published before 2026, the forecast for 2026. Until it arrives, `--feed ibisworld` ends with
`waiting for owner file`, exits 0, and leaves the last accepted output alone.

What the run does with each file: hashes it, reads its type from the bytes, copies it to the vault, identifies the report from its
content (code and edition date from the cover or header; the file name is only a hint and a disagreement is an issue), parses the
Key Statistics or Outlook table, and checks it (years increase, revenue above zero, the printed Annual Change agrees with the
recomputed growth for **every** year, the unit and price basis are found, the publication date is not after the day the file was first
seen). A file that fails a check is marked `review_required` and is never used silently.

Outputs in `runs\<run_id>\outputs`: `ibis_editions.csv`, `ibis_key_statistics_long.csv`, `ibis_predicted_growth.csv` (the 06 columns),
`ibis_actual_growth.csv` (the 07 columns). Rules that matter:

- A forecast for year t uses only an edition published in year t-1 (the latest one). An edition published in year t or later never
  counts as a forecast for t. With no t-1 edition, the latest older edition is shown as a flagged two-year-ahead sensitivity row, never
  as `ready`.
- The actual series is ONE vintage: the latest fully checked edition's historical years. A newer edition replaces the whole history;
  vintages are never spliced. The receipt names the vintage that was replaced.
- An edition with verified numbers but no stated price basis gives a `partial` row with the predicted growth withheld.
- Industries whose actuals are pinned to the workbook pass through unchanged.

The 18 property and casualty editions on this machine (2006-12 to 2024-11) reproduce workbooks 06 and 07 (revenues exactly, growth to 1e-7): see
`tests\testthat\test-02-ibis-real-pc.R` (local-only; it skips when the licensed PDFs are absent).

## 3. OpenSecrets

### 3.1 Property and casualty baseline: F09 (all Insurance), by owner ruling

The OpenSecrets baseline for property and casualty insurance is **F09 (Insurance)**, from its public industry pages, by the owner's ruling of
2026-09-30, knowing that F09 also covers life, health and brokers. Every receipt carries the caveat: *OpenSecrets baseline F09 = all insurance;
Mentat P&C roster and IBISWorld 52412 are P&C only.*

The earlier candidate, category F3400 ("Property & casualty insurance"), is not published as an interest-group page: on 2026-09-30 the
fetcher asked for its totals page and the site answered `302 Found` to `http://opensecrets.org/industries` (the interest-group index, the
site's answer for a page that does not exist), and the public list captured on 2026-09-27 has no F3400 and no insurance code other than F09.
It was never verified and no data for it was used. It is kept in the registry as `superseded_candidate`.

What the 2026-09-27 captures already hold for F09 is the totals page (all cycles 1990-2024) and the summary page. The House and Senate
candidate recipients and the presidential recipients for 2012-2024 (21 pages) are the pages the fetcher adds.

### Frozen captures

"Frozen" means the existing captures are never re-fetched or overwritten. `registry\frozen_os_pages.json` lists every captured page (code,
cycle, kind, file, sha256; no content; regenerate with `tools\make_frozen_inventory.R`). The fetcher plans only pages the inventory does not
hold, refuses any page it does hold, and closes the fully captured codes (F03, F05, T2100) altogether. A registry that names an inventory
that is missing stops the run rather than fetching blind.

### 3.2 The page fetcher (`--feed opensecrets-fetch`)

It runs only when `registry\feeds.json` carries the owner's attestation of OpenSecrets' permission (who, date, basis), and it is
deliberately polite:

- a descriptive User-Agent naming the tool and its operator (no e-mail address; set `MENTAT_OS_CONTACT` to add a contact of your choice)
- one request per 4 seconds at most (never faster than 3 s), one connection, no cookies kept, no proxy
- `robots.txt` first, obeyed; a content-addressed cache so each URL is fetched once (`--refresh` to fetch again)
- only the URLs the registry lists for the industry and the frozen inventory does not hold (see "Frozen captures")
- the page is checked first (canonical code, heading, breadcrumb) and the run stops if it is not the industry the registry expects

**It never tries to get past a block.** A Cloudflare challenge (403 "Just a moment", a `cf-mitigated` header, a captcha body),
401/407/429/503, a redirect off the site or to a login page, a non-HTML answer or a page that is not OpenSecrets stops the run at once.
The stop is written to `fetch_state.json` (with the status, headers and the start of the body as evidence), to the receipt, and to the
Glass Box, and the run says: ask OpenSecrets for an allowlisted access method, or drop the bulk files in the inbox. The next run stays
closed until you set `"stopped": false` in `fetch_state.json`. A redirect to OpenSecrets' own apex host (a page that does not exist) also
ends the run, but is not a block and sets no flag. There is no browser, script execution, proxy, cookie or header-spoofing code
anywhere in this folder, and a test enforces it.

### 3.3 Bulk data (`--feed opensecrets-bulk`), the route that can deliver F3400

Download with your own OpenSecrets account and drop unchanged (zip or extracted `.txt`) into `inbox\opensecrets\bulk`:

1. `CRP_Categories.txt` (the run stops unless `F3400` is there with a name matching "property & casualty" and order code `F09`)
2. `cands`, `pacs`, `indivs` for cycles 2012, 2014, 2016, 2018, 2020, 2022, 2024 (2008 and 2010 welcome); `cmtes` and `pac_other` only if you want party or outside money rebuilt

The bulk recipe is used only after the **calibration gate** passes: for F03, F05, T2100 and F09 and every cycle 2012-2024 it must
reproduce the existing page captures (PAC total within 0.5%, each chamber's Democratic and Republican sums within 1%, at least 98% of
recipients matched by name key within $1). If no recipe passes, the run stops with the residual table and no bulk number is written.

### 3.4 Pages saved by hand (`--feed opensecrets-pages`)

Name each saved file like `F3400_2012_candidates_H.html` (`totals`, `candidates_H`, `candidates_S`, `presidential`); a save that holds only
the rendered 25 rows of a long table is rejected.

## 4. Receipts and proposals

`receipt.json` (written last) lists the run id, code hashes, package versions, every input (name, sha256, size, first seen, vault path),
every output (path, sha256, rows), every check, decisions, issues, the network log of the fetcher and the Glass Box event ids.
`proposed_inputs.json` and `proposed_registry_patch.json` sit beside it: the ingest **never** edits `registry\*.json` or the refit's
`inputs.json`; you apply proposals (APPLY = Paul). `current.json` maps each output role to the latest accepted output.
`fi_load_feed("ibis_predicted_growth")` is the only supported way to read a feed back: it checks the output hash, the vault copies of
every raw input and the ingest code hash, and answers `use`, `rederive` (IBISWorld feed, code changed) or `rerun_ingest`.

## 5. Tests

`"C:\Program Files\R\R-4.6.0\bin\Rscript.exe" tests\testthat.R` (filter with `FI_TEST_FILTER=08-fetch`). No test touches the network; the
fetcher is tested with an injected fake transport and a fake clock. Tests that need licensed files look them up by pinned sha256 or path
and skip with a reason when absent.
