# Congress bill refresh

For the native first-laptop-use daily process including media, see
[`../DAILY_REFRESH.md`](../DAILY_REFRESH.md). This page describes the bills-only
command and its publication guarantees.

Run from the Melange Systems project directory:

```powershell
& 'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe' fec/legislation/refresh_congress_pipeline.py
```

This command owns one writer lease from collection through publication. It runs
fresh Congress.gov discovery (with official GovInfo fallback), evidence-based
admissions, official votes, the seeded bill repository and BILLSTATUS bulk
refresh. It then runs the existing canonical pipeline from membership
verification through Sentinel Bill Ledger.xlsx, the main Moneyball workbook,
measurements, ranks, the member desk, the combined dashboard and final audits.
The final check binds the collection, workbook and dashboard receipts to the
actual current files. Scoring policy and audit exceptions remain unchanged.

The third step, evidence-based admissions, calls Claude. Per
`data/engine/sentinel_admission_policy.json` (`"transport": "claude-cli"`,
`"model": "sonnet"`, `"tools_enabled": false`) it spawns `claude.exe` once per
measure that needs a fresh verdict; a measure whose official evidence is
unchanged reuses its published verdict and costs no call. Decisions are labelled
automated and are never presented as human ratification. The step's `--no-llm`
flag removes the model entirely: unchanged evidence still reuses its verdict,
changed evidence keeps the published verdict together with its ORIGINAL
`evidence_fingerprint` (flagged `carried_forward_no_llm`, so the next classifying
run re-decides it), and a measure with no published verdict is left unprocessed
and unsealed and counted in `no_llm_deferred`. No hold is invented for it.

The calibration step's budget is 900 seconds. It is a local comparison that
takes well under a second on an idle disk, but was measured at 71.65 s under
load and timed out against the previous 120 s budget on 2026-09-13 while its own
log already read "OK (3 checked)". The budget exists to bound a hang, not to
cost a daily attempt to slow I/O.

The canonical chain removes the prior bound recognition views before the first
main-workbook writer (`build_media_blend.py`), then publishes fresh views after
stance synchronization. Its exact-hash protection against unrelated workbook
edits remains enabled. Do not move preparation after a workbook-writing step.

The Windows task **MoneyballCongressRefresh** now invokes local Python directly
on this user's sign-in, unlock and verified lid-open wake events. A local date
gate permits one successful full refresh per day. **MoneyballDailyRefreshRetry**
retries an observed day hourly and on network reconnection. Neither task needs
Codex or wakes the laptop itself. These replace the earlier fixed 09:00 design.
The earlier Codex heartbeat was removed; the daily loop is Windows-owned.
The existing Windows
**MoneyballWeeklyRefresh** still runs Sundays at 06:00 Eastern and refreshes the
other source streams as well. Its last run before this repair was interrupted
on September 6, before workbook/dashboard production.

Inspect health without modifying data:

```powershell
& 'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe' fec/legislation/refresh_congress_pipeline.py --status
```

Current progress and the last outcome are in
`data/engine/congress_pipeline_status.json`. The last verified successful
generation is in `data/engine/congress_pipeline_receipt.json`. Every attempt has
step logs, before-images and a terminal receipt under `audits/congress_pipeline/`.
A dead owner is reported as interrupted; a running collector is not success.
The wrapper times out and terminates its own failed step tree, retains the last
successful end-to-end receipt, and reports the new failure separately.

## Watching a run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File fec/legislation/open_congress_pipeline_monitor.ps1
```

That opens a separate console titled "CONGRESS PIPELINE - LIVE" running the
read-only watcher `fec/legislation/watch_congress_pipeline.ps1`, which refreshes
every five seconds and writes nothing. It shows pipeline status, mode, active
step, elapsed time, owner pid and the step table; today's daily-gate attempts
and completion; the media receipt including any partial press backlog; the tail
of the progress log; and the tail of the active step's log. Every read is
guarded, so a missing or half-written document prints `(not yet)`. Pass `-Root`
to point either script at a different tree.

The status document and both receipts are published by an atomic rename that
retries through the brief Windows lock window (`lane_file_io.replace_atomic`,
~40 attempts with 50-250 ms jitter, on WinError 5/32/33), because on
2026-09-13 22:42Z a reader holding `congress_pipeline_status.json` made the
rename fail with "Access is denied" and killed the whole scheduled run; the
watcher opens these files with `FileShare ReadWrite,Delete` so that a poll can
never block a writer again.

Progress is also emitted while a run is in flight:

- `data/engine/congress_pipeline_live.log` gets one human-readable line per
  event (UTC timestamp, step, status, elapsed, note). It is trimmed to the last
  400 lines at the start of each run and is never held open.
- Glass Box receives `refresh.snapshot` (before-image start/progress/completion),
  `refresh.step` (running/completed/skipped) and a `refresh.heartbeat` every
  60 seconds while a step runs. The step and snapshot events now carry
  `current`/`total`, so a viewer can show position without guessing.
- The before-image records `sha256`, `size` and `copied` for each source
  artifact in `before.json`. The two ~98 MB dashboards are hashed rather than
  copied: that copy cost 287 MB and about eleven silent minutes per run and
  nothing read it. `rank_changes` reads `before/ranks_python.json`, which is
  still copied byte-for-byte, as is every other non-HTML source.

## Deferred discovery

Discovery drives a real browser and its launch is occasionally flaky: on
2026-09-13 a scheduler run failed after 1003 s with Playwright reporting
`launchPersistentContext: Timeout 180000ms exceeded`, which cost the whole day
and had the hourly retry restarting a four-hour chain. Discovery is therefore
the one step that is retried. On a nonzero exit the wrapper waits
`DISCOVERY_RETRY_DELAY_SECONDS` (300) and runs it once more into
`discovery.retry.log` in the same run directory. If the retry succeeds the step
is an ordinary success carrying `retried: true` and the original `first_returncode`.

If it fails twice, the step is recorded with status `DEFERRED`, the note
`discovery collector failed twice (rc=N, rc=M); new-bill discovery deferred to
the next run; see <log>`, a Glass Box `refresh.step` event with status
`deferred`, and a progress-log line - and the run continues. Repository and
BILLSTATUS still refresh every already-known bill, so the day publishes a
complete generation of the existing universe; only newly introduced measures
wait for the next run. The terminal status stays `PASS` if everything else
passes, `deferred_steps: ["discovery"]` appears in the status document, the
run-directory receipt and the root receipt, and the Glass Box `refresh.run`
summary reads `PASS with deferred: discovery`. `daily_refresh.verified_completion`
checks schema, status, mode, ownership and the media binding only, so such a day
closes normally.

Every other step keeps strict stop-on-first-failure. Nothing else is retried.

## Partial press coverage

The press collector budgets body retrieval per run and exits 3 when bodies are
still queued. `collect/daily_media_refresh.py` records that as `PARTIAL`, not
`FAIL`, and parks every step that would hand the intake gate the incomplete
generation by path: `press_intake`, `press_feed`, `press_baseline_fold` and the
derived `all_intake`, each with status `SKIPPED_PARTIAL` and a note naming the
outstanding `pending_body_urls`. The gate itself is untouched. This wrapper's
`media` step row and its Glass Box completion event carry `partial_sources`,
`press_partial` and `parked_steps`, so a PASS never reads as complete press
coverage. The canonical chain's own `audit_collection.py` step discovers a press
run rather than being handed one, so it audits the last complete generation and
is unaffected. Full detail is in [`../DAILY_REFRESH.md`](../DAILY_REFRESH.md).

## Smoke mode

`--smoke` exercises the whole shape of the job cheaply. It skips discovery
(recorded as `SKIPPED_SMOKE`), runs admissions with `--limit 1 --no-llm
--no-install`, leaves votes, repository and BILLSTATUS unchanged, runs
`collect/daily_media_refresh.py --smoke`, and runs the canonical pipeline in
full - there is no shortened canonical chain. It cannot be combined with
`--include-media`. A smoke run records `"mode": "smoke"` in the status document
and its run-directory receipt, writes no
`data/engine/congress_pipeline_receipt.json`, and is refused by
`daily_refresh.verified_completion`, so it can never complete a day.

Discovery checkpoints resume only the current interrupted cycle. A completed
cycle starts new browser queries on the next pass; prior query/page evidence is
preserved under `.refresh_cycles`. The canonical discovery ledgers and
version-bound GovInfo cache are retained. New candidates remain non-scoring
until the existing evidence-admission rules accept them; the historical
adjudication backlog is not represented as complete.

An active, evidence-bound Congress.gov verification cooldown routes directly
to GovInfo without contacting Congress.gov or modifying the cooldown. Admission
cache keys ignore only the U.S. Code website's recognized publication-date
banner. Unchanged substantive evidence retains its published decision; actual
bill/statutory changes still trigger evidence review. Full raw source bytes,
hashes and exact quotation checks are preserved.

The legacy `update_bill_repository.py --rebuild-dashboard` option now also
updates Excel and runs the canonical downstream pipeline, after writing a
complete collection receipt. For recurring open-universe collection, use the
full command above. Never run membership verification by itself after a
completed pipeline: its output must travel with the Excel/history consumers
and downstream rebuild (Project Brain DE-016).
