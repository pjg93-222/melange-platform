# Native daily collection and publication

Windows Task Scheduler owns this process. No Codex app or external agent is
needed to trigger, supervise or retry it. Existing collector/classifier programs
retain their configured data services and evidence policies.

Scheduling is agent-free; one collection step is not. The evidence-admissions
step (`fec/legislation/refresh_sentinel_admissions.py`, the third step of the
wrapper) classifies each candidate measure by calling Claude. The transport and
model come from `data/engine/sentinel_admission_policy.json`, which currently
reads `"transport": "claude-cli"` and `"model": "sonnet"`, so the step spawns
`claude.exe` once per measure that needs a fresh verdict, with tools disabled.
A measure whose official evidence is unchanged reuses its published verdict and
costs no call. Every decision is labelled automated and never presented as human
ratification. Passing `--no-llm` to that script removes the model entirely; see
below.

`MoneyballCongressRefresh` launches the installed Python 3.12 `pythonw.exe`
30 seconds after this user's sign-in, unlock, or a verified laptop lid-open
wake event. It runs `fec/daily_refresh.py --trigger first-use` without a visible
console. It does not wake the laptop itself and uses ordinary user permissions.

`MoneyballDailyRefreshRetry` checks hourly and on network reconnection. Retry
mode only finishes a local calendar day already observed by a first-use event.
Opening the laptop repeatedly does not duplicate a successful day's refresh.
Failure, interruption, missing network or an existing writer leave the day
eligible for retry. Separate daily and pipeline locks prevent overlapping jobs.
Successful child receipts can be reconciled after an interrupted parent without
repeating a completed day's work.

The native scheduled action was tested on this laptop: it started Python,
recorded the day, detected the existing publication writer, and returned
75/BUSY without changing that writer or marking the day complete. Proof:
`audits/daily_refresh_scheduler/pythonw_native_20260913/native_execution_verification.json`.

The full flow is:

1. Calibration, fresh Congress.gov discovery with official GovInfo fallback,
   evidence admissions, votes, tracked bill repository, sponsor/cosponsor
   memberships, and BILLSTATUS.
2. Press collection alongside sequential database collectors for floor
   speeches, hearings, interviews, Washington Journal and verified Bluesky
   handles. Item failures block publication even if a collector exits 0.
   Press body retrieval is budgeted per run: see "Partial press coverage".
3. Source acceptance, cumulative press feed, extraction, Social Master,
   behavioral inputs and reference classifications.
4. Complete canonical pipeline: membership verification, Sentinel Bill Ledger,
   main Moneyball workbook, measurements, ranks, member desk, combined dashboard,
   live HTML alias and terminal source-to-dashboard audits.

The full wrapper is `fec/legislation/refresh_congress_pipeline.py --include-media`.
Its media windows bind to the transaction's initial local date. A separate
successful-media receipt widens windows after extended laptop downtime, with
overlap for delayed publication. Initial or accumulated press catch-up can take
hours; existing checkpoints and complete source coverage are retained.

## Partial press coverage

The press collector retrieves article bodies under a per-run budget. When bodies
remain queued for the next pass it reports `status: PARTIAL` with a nonzero
`pending_body_urls` in `outputs/press_refresh_<YYYYMMDD>/press_network_retry_receipt.json`,
and `fec/collect/refresh_press_delta.R` exits 3. That exit code means "budgeted,
resumes next run", not "failed".

`fec/collect/daily_media_refresh.py` therefore treats exit 3 from the `press`
step, and only that step, as `PARTIAL`: the row is not a failure and it does not
increment `error_count`. Any other nonzero exit from press, and exit 3 from any
other step, remain `FAIL`.

Press never backs up (owner ruling 2026-10-15). Every sealed PARTIAL run goes to the
intake gate: its fetched bodies are promoted and its pending URLs (the hash-bound
`press_delta_pending.csv`) are carried forward, so the cumulative feed reports
`coverage.status` `PARTIAL_CARRIED_PENDING`. The next run retries them FIFO (legacy
"" ledger rows first, then oldest first sighting); three real attempts mark a URL
exhausted. A repeated article/member row takes the latest accepted run's values, so
`recency_weight` stays current, and the baseline fold stays gated by `PRESS_BASELINE_FOLD`.
Only a PARTIAL whose pending list the gate refuses (malformed or hash mismatch) parks
`press_intake`, `press_feed`, `press_baseline_fold` and `all_intake` (`SKIPPED_PARTIAL`).

The canonical chain is unaffected. Its own `audit_collection.py` step
(`fec/run_pipeline.py:52`) passes no `--press-delta-root`, so it discovers a run
instead, and `discover_run` (`build_press_engine_feed.py:300-307`) skips a
refused one and audits the last complete press generation.

When `error_count` is zero but any source row is `PARTIAL` (excused or not), the
terminal status is `PARTIAL`, not `PASS`; a skipped smoke row alone does not make it
partial. `PARTIAL` is a non-fatal success: the helper exits 0, the chain continues
and the catch-up anchor `daily_media_refresh_success.json` is written (it anchors
`source_steps` windows only; press uses a fixed `--date-floor` and its own tombstone
ledger). The receipt carries the gap explicitly so a clean `PASS` can never be read
as complete press coverage:

- `partial`: true when any source returned partial coverage.
- `partial_sources`: the names of those sources.
- `press_partial`: `pending_body_urls`, `failed_bodies`, `status` and `run_root`
  copied from the press retry receipt (or `{"note": "retry receipt absent"}`).
- `parked_steps`: every step that did not run, parked or skipped.

The per-source console line reads `press: PARTIAL (11591.32s) pending_body_urls=2200`.
The receipt `schema` is unchanged at `daily-media-refresh-receipt-v1`. The
wrapper copies `partial_sources`, `press_partial` and `parked_steps` onto its own
`media` step row in `data/engine/congress_pipeline_status.json` and says
`PARTIAL: press; parked: ...` in its Glass Box completion event.

`daily_refresh.verified_completion` requires the media receipt's `schema` and
`status: PASS` or `PARTIAL`, its hash binding and its freshness; it does not inspect
`error_count` and does not require `daily_media_refresh_success.json`. A PARTIAL
day therefore closes normally and the hourly retry stops, while the deferred
press bodies are collected by the next run's budget. The completion record carries
`media_status`; when it is `PARTIAL` the attempt and the day are recorded as
`PARTIAL` (invocation result `PARTIAL`, exit 0) with `success` still set, so a later
`--trigger retry` returns `ALREADY_COMPLETE` and launches nothing (DE-020).

## Deferred discovery

Discovery drives a real browser, and a flaky Playwright launch used to cost the
whole day (2026-09-13: `launchPersistentContext: Timeout 180000ms exceeded`
after 1003 s, then an hourly retry restarting a four-hour chain). Discovery is
now the only retried step: on a nonzero exit the wrapper waits
`DISCOVERY_RETRY_DELAY_SECONDS` (300) and runs it once more into
`discovery.retry.log`. A successful retry is an ordinary success carrying
`retried: true` and `first_returncode`.

Two failures defer rather than fail. The step is recorded `DEFERRED` with the
note `discovery collector failed twice (rc=N, rc=M); new-bill discovery deferred
to the next run; see <log>`, the run continues, and the terminal status is still
`PASS` if everything else passes. `deferred_steps: ["discovery"]` is written to
`data/engine/congress_pipeline_status.json`, the run-directory receipt and
`data/engine/congress_pipeline_receipt.json`, and the Glass Box run summary says
`PASS with deferred: discovery`. `verified_completion` inspects schema, status,
mode, ownership and the media binding only, so the day closes and the hourly
retry stops. Repository and BILLSTATUS still refresh every already-known bill;
only newly introduced measures wait for the next run. Every other step keeps
strict stop-on-first-failure.

## Smoke mode

`--smoke` on the wrapper runs the whole shape of the job cheaply, for checking
wiring rather than producing a generation:

```powershell
& 'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe' fec/legislation/refresh_congress_pipeline.py --smoke
```

- discovery is skipped and recorded as `SKIPPED_SMOKE`;
- admissions runs `--limit 1 --no-llm --no-install`, so it makes no model call
  and does not replace the published admissions generation;
- votes, repository and BILLSTATUS run unchanged;
- media runs `--smoke`: sources are limited to `crec`, `chrg` and `bluesky`, the
  whole press lane is recorded `SKIPPED_SMOKE`, all derived producers still run,
  and no success anchor is written;
- the canonical pipeline runs in full. There is no shortened version of it.

`--smoke` cannot be combined with `--include-media` (argparse error). A smoke run
records `"mode": "smoke"`, writes no `data/engine/congress_pipeline_receipt.json`,
and is rejected by `daily_refresh.verified_completion`, so it can never complete
a day.

`--no-llm` on `fec/legislation/refresh_sentinel_admissions.py` is also usable on
its own. It never calls the classifier. A measure whose evidence is unchanged
reuses its published verdict as usual. A measure whose evidence moved keeps its
published verdict together with its original `evidence_fingerprint`, flagged
`carried_forward_no_llm`, so the next classifying run still sees changed evidence
and re-decides it. A measure with no published verdict is left unprocessed and
unsealed - no hold is invented - and is counted in the generation's
`no_llm_deferred` / `no_llm_deferred_keys`.

Daily admissions run `--limit 80 --max-classifier-calls 48` (2026-10-15 ruling, about
$108/month); the cap counts real classifier calls only and the generation records
`classifier_calls` / `max_classifier_calls`. `aborted_reason` `CLASSIFIER_CALL_BUDGET`
is a normal bounded run (PASS); `CLASSIFIER_QUOTA_EXHAUSTED` is FAIL, checkpoint
reused on rerun. The classifier child runs with `DISABLE_PROMPT_CACHING=1`.

## Watching a run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File fec/legislation/open_congress_pipeline_monitor.ps1
```

That opens a separate console titled "CONGRESS PIPELINE - LIVE" running
`fec/legislation/watch_congress_pipeline.ps1`, which refreshes every five
seconds and writes nothing. It shows pipeline status/mode/active step/elapsed
and the step table, today's daily-gate attempts, the media receipt including any
partial press backlog, the tail of `data/engine/congress_pipeline_live.log`, and
the tail of the active step's own log. Run the watcher directly with `-Root` to
point it at another tree. Every read is guarded, so a missing or half-written
file prints `(not yet)` instead of throwing.

Direct X/Truth API collection is explicitly not integrated into the daily
workbook path. Existing licensed Quorum exports remain inputs; no unattended
licensed export downloader is configured. Historical discovery candidates are
not all admitted/scoring bills. Evidence review is bounded at 80 new candidates
and 48 paid classifier calls per pass; admitted/tracked bills are refreshed each
pass.

Run manually from the project directory:

```powershell
& 'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe' fec/daily_refresh.py --trigger manual
```

Local health and evidence:

- `data/engine/daily_refresh_state.json`: day gate, attempts and completion.
- `data/engine/congress_pipeline_status.json`: active pipeline progress.
- `data/engine/daily_media_refresh_receipt.json`: individual source outcomes.
- `data/engine/daily_media_refresh_success.json`: preserved catch-up anchor.
- `data/engine/congress_pipeline_receipt.json`: last verified pipeline success.
  A smoke run never writes this file.
- `data/engine/congress_pipeline_live.log`: one human-readable line per step
  start, completion, 60-second heartbeat and failure. Trimmed to the last 400
  lines at the start of each run.
- `audits/daily_refresh`, `audits/congress_pipeline`, `audits/daily_media_refresh`:
  attempt logs, immutable receipts and before-images.

Every one of these documents is published by an atomic rename that retries
through the brief Windows lock window (`lane_file_io.replace_atomic`, ~40
attempts with 50-250 ms jitter, on WinError 5/32/33), because a reader holding a
status file used to fail the rename and kill the whole run; the watcher in turn
opens them with `FileShare ReadWrite,Delete` so it can never cause that.

Every BUSY result exits 75 and names its own cause in `reason`: "Another daily
refresh owns the gate" (the daily lock is held), "Previous daily child is still
running" (an earlier attempt's child still owns the job), and "pipeline lease
busy (child exited 75)" (our own child found the pipeline writer lease held).

A bills-only PASS cannot complete a day that requires media. A started process
does not count as success. Final completion requires owned, fresh, immutable
media and pipeline receipts bound to actual files. Known ratified audit
exceptions remain explicit; new failures block completion. No scoring weights,
admission rubric or audit waiver was added.

The canonical chain removes prior bound recognition views before the first
main-workbook writer, then publishes fresh views after stance synchronization.
Exact hash protection against unrelated workbook edits remains enabled. Never
run membership verification alone after publication: its output must travel
with Excel/history consumers and the downstream chain (Project Brain DE-016).

The existing Sunday `MoneyballWeeklyRefresh` shares the writer lock. The earlier
fixed 09:00 Congress trigger was replaced. The earlier Codex heartbeat was
removed; its automation configuration is absent. The daily loop is Windows-owned.

Regenerate/validate task XML without registration:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File fec/register_daily_refresh_task.ps1
```

Add `-Register` to update both tasks. Existing task XML is backed up first. The
manual PowerShell launcher remains available, but registered tasks invoke
`pythonw.exe` directly because the native hidden PowerShell host stalled during
this laptop's live test.
