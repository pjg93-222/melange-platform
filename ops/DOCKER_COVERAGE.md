# Docker coverage inventory

Status 2026-10-18. Plan step 2 of the platform installer + admin console plan (Paul, 2026-10-18). Inventory only: nothing here changes a job, a task or an engine flag.

Governing rulings: D-173 (everything containerized: apps now, compute next), D-175 (images build only in GitHub; compute containers run on this laptop), D-188 (each system switches to its container when its parity gate passes, with a one-flag Windows fallback), D-216 (`fec/refresh_tick.py` is the single scheduled orchestrator; reverses D-187), D-241 (Paul, 2026-10-18: Terrier is one app with no per-district access limits).

## How to read this

Citations are `path:line` against these trees, read on 2026-10-18:

| Tag | Tree |
|---|---|
| (none) | Melange, this repo, `main` at `5e7731f1c` |
| `[Mentat]` | branch `codex/mentat-crypto` at `c62ec1231` (the Mentat worktree; same git store as this repo). `main` has no `mentat/app/`. |
| `[Geo]` | Terrier, repo `pjg93-222/Geo`, branch `claude/walk-list` at `a290240`. `origin/main` (`6aafbc5`) carries the desktop owner and auth work and lacks the branch's two hosted-image fixes (`.dockerignore`, `requirements.linux.*`); `Dockerfile`, `compose.yaml`, `compute*.compose.yaml` and `release.yml` are the same in both. |
| `[brain]` | `melange-systems-brain` ledger (STATE.md, D-entries) as mirrored on 2026-10-18 |
| `[host]` | read-only machine state on 2026-10-18: `schtasks` / `Get-ScheduledTask`, local evidence files that git does not track |

D-188 gate column: **PASS** (passing evidence cited), **NOT PASSED** (gate defined, no passing evidence found), **n/a** (no Windows production job to replace; serve images are gated by the release workflow instead), **not checked**.

Owner lanes:

| Lane | Scope |
|---|---|
| Chain compute | `.claude/skills/melange-chain-compute`: Melange chain, collect and office phases (`ops/compute/`) |
| Mentat compute | `.claude/skills/mentat-compute-and-money`: `compute-mentat` jobs and the Mentat parity streak |
| D075 lane | Mentat downstream spending collector (D071-D076) and its opt-in container runtime |
| Terrier compute | `.claude/skills/terrier-compute`: Terrier store builds |
| Self-healing | refresh tick, watchdog and scheduler work (D-212, D-213, D-216) |
| Deployment | serve stack and VPS (`ops/compose.yaml`, `ops/vps/`; [brain] STATE.md:456) |
| Desktop release | `ops/desktop/` Electron client and installers |
| Platform installer | this plan: local compose profiles, admin launcher, admin console Apps page |
| Terrier app | Terrier servers and auth; the desktop owner password belongs to the Terrier release work |
| Terrier unification lane | one Terrier service with no per-district access limits (D-241) |
| Mentat app | the Mentat console and its launchers |
| Staffer-live / Glass box / Press collection | the native surfaces of those names under `fec/` |

## Biggest gaps

1. **Melange chain and collectors never cut over.** Images and compose files exist, but `ops/compute/engines.json:2-3` is `"chain": "windows"` and `"collect": "windows"`, and neither gate file exists (`audits/compute_parity/` holds only the office evidence [host]). 32 of the 49 weekly refresh steps (plus the two tick-only steps) have no container path at all: every step whose script is not in `STEP_SERVICE` stays on the host (`fec/compute_engine.py:15-21`, `:64-74`).
2. **Mentat production is all native.** The nightly anchors and refit watcher run as Windows tasks [host]; the industry report chain (PowerShell 5.1, `mentat.R`, Excel COM, `@oai/artifact-tool`) is left out of the container design on purpose ([Mentat] `mentat/COMPUTE.md:34-36`); the parity streak has not been run on the CI digest (`.claude/skills/mentat-compute-and-money/references/jobs.md:41-44`); the D075 container runtime has never run.
3. **No local app server runs in a container.** Mentat console (8802, 8806/8807), staffer-live (8841), Glass box (8823) and the Terrier districts (8767/8775/8777, to be unified into one service by D-241) are native. The images that exist are hosted-only: `mentat-console` is read-only with no R, Node or Chromium (`ops/docker/mentat.Dockerfile:2`), `terrier-web` runs in hosted mode, and staffer-live and Glass box have no image.
4. **D-216's orchestrator is not scheduled.** `MelangeRefreshTick` and `MelangeRefreshWatchdog` are not registered; the legacy `MoneyballWeeklyRefresh` and `MoneyballCongressRefresh` tasks still drive refreshes natively [host].
5. **Terrier is the only compute lane cut over besides office, and only partly proven.** Parity passed for CO-05 only, on locally built images, and no parity report survives on disk; OR-05 and CT-01 never ran through it ([Geo] `COMPUTE.md:8-9`, `COMPUTE.md:21`, `COMPUTE.md:180`).

## Overlaps with lanes already in flight

These rows are marked **owned by that lane**. This document does not plan around them.

- **D075 container runtime (Mentat).** Opt-in via `run_job.ps1 -Runtime container` ([Mentat] `mentat/tools/run_job.ps1:62`, `:157-158`); host-native stays the default ([Mentat] `mentat/COMPUTE.md:22-23`). Commit `9391fd381` says: "No container has run yet, so the S0-S2 smoke steps are Paul's". There is no container-runtime receipt in `%LOCALAPPDATA%\Mentat\compute\receipts`, no export folder and no `frontier.sqlite.MOVED.json` [host]; the production ledger and its receipts are host files.
- **Self-healing build P0-P4 (Melange).** The brief and [brain] STATE.md:492 describe it as uncommitted. Git on 2026-10-18 shows P0 `c28ebe1ed`, P1 `3cb213fd6`, P2 `75aa077fa` and P4 `c6b9494d6` / `dcaa3b083` on `main` (2026-10-15/16); no P3 commit was found, and the live tree shows no modified tracked files (about 325 untracked paths, none under `ops/`). Either way the tick, watchdog and task registration rows belong to that lane.

## Servers

| Piece | App | Path | Image | Compose | D-188 gate | Gap | Owner | Plan to close |
|---|---|---|---|---|---|---|---|---|
| Melange desk (8080) | Melange | `fec/surfaces/desk_server.py:209` | `melange-desk` (`ops/docker/melange-desk.Dockerfile:39`) | `ops/compose.yaml:66` `melange-desk` | n/a (release-gated, `ops/github/workflows/release.yml:90-92`) | Container-only; no native launcher, only a manual command (`ops/DESK.md:39`). No local profile. | Deployment | Local profile on 18xxx (plan step 3). |
| Mentat console, hosted (8802) | Mentat | [Mentat] `mentat/app/server.py:3919` | `mentat-console` (`ops/docker/mentat.Dockerfile:54`) | `ops/compose.yaml:85` `mentat-console` | n/a (release-gated, `release.yml:93-95`) | Read-only by design ([Mentat] `server.py:3926-3932`); `/healthz` and `/version` answer only when Access is enforced ([Mentat] `server.py:3313-3316`). | Deployment | Expose `/healthz` + `/version` in local mode (plan step 3). |
| Mentat console, local (8802 default; Paul's live 8806/8807) | Mentat | [Mentat] `mentat/app/server.py:3919`; [Mentat] `mentat/app/prewarm.py:131` (8806); `.claude/skills/ops-shared/host.md:80` (8806, 8807) | none | none | n/a | Native and writable. The image cannot replace it: no R, Node, Chromium or Excel (`ops/docker/mentat.Dockerfile:2`), and collection, report builds and figure export run from it ([Mentat] `server.py:1763-1764`, `:3709-3713`). No launcher in either tree sets 8806 or 8807. | Mentat app | Recipients: read-only image via the local profile (step 3). Paul's writable console: needs a writable console image design; no ruling or lane owns that today. |
| Staffer-live (8841) | Melange | `fec/surfaces/staffer_live_server.py:838` | none | none | n/a | No Dockerfile or compose service. | Staffer-live | Paul-gated: "hosting the live staffer console (32 GB server plus 8841 read-only work)" ([brain] STATE.md:528). |
| Glass box (8823) | Melange | `fec/surfaces/glassbox_app_server.py:68` | none | none | n/a | No image. The 2026-08-25 containerization proposal listed Glass box as host-side ([brain] STATE.md:1535-1539); that was a proposal, not a ruling. | Glass box | Paul's call: native host monitor or image. |
| Press recovery monitor (8876) | Melange | `fec/collect/press_recovery_monitor.py:133` | none | none | n/a | Native; its launcher `fec/collect/launch_collection_monitor.pyw` is untracked in the live tree. | Press collection | Follows the press collectors; no separate work. |
| LAN update publisher (38443) | Platform | `ops/desktop/server/desktop_admin.py:737-738` | none | none | n/a | Native; starts from a Run key (`ops/desktop/server/Register-LanPublisherStartup.ps1:19-20`), not Task Scheduler. | Desktop release | Optional feed; GitHub Releases is the default update channel (`ops/desktop/client/README.md:11`). No plan. |
| Terrier CO-05 (8767) | Terrier | [Geo] `Start-TerrierServer.ps1:155`; [Geo] `server/research/management/commands/serve.py:144` | none on the laptop (`terrier-web` on the VPS) | none (`ops/compose.yaml:121` on the VPS) | n/a (app server; the Terrier D-188 gate covers store builds only) | to be unified into one Terrier service (D-241). Today: native Waitress; `terrier-web` runs hosted mode, which refuses the local owner (`CU_LOCAL_ADMIN=1`, [Geo] `server/settings.py:81-82`). | Terrier unification lane | One Terrier service (D-241), then its local profile running `terrier-web` in a non-hosted local mode (step 3); auth follows the Terrier release work's owner password (`server/research/desktop_owner.py` on `origin/main`). |
| Terrier OR-05 (8775), CT-01 (8777) | Terrier | [Geo] `Start-DistrictServer.ps1:150` | none on the laptop | none (`ops/compose.yaml:144`, `:167` on the VPS) | n/a | to be unified into one Terrier service (D-241). The ports are launch arguments ([Geo] `Start-DistrictServer.ps1:4`; [Geo] `tools/districts.py:124`, `:157`); the commands that start them are recorded only in the untracked `_build/FINISH.md:34-35`. | Terrier unification lane | As CO-05. |
| VPS serve stack: `cloudflared`, desk, Mentat console | Platform | `ops/compose.yaml:66`, `:85`, `:193` | all images (`cloudflare/cloudflared:2026.10.0`, `ops/images.lock:14`) | `ops/compose.yaml` | n/a | Covered. Whether the VPS stack is live was not checked. | Deployment | None. |
| VPS Terrier services `terrier-co05`, `terrier-or05`, `terrier-ct01` | Terrier | `ops/compose.yaml:121`, `:144`, `:167`; [Geo] `compose.yaml:36`, `:49`, `:62` | `terrier-web` ([Geo] `Dockerfile:25`) | `ops/compose.yaml` | n/a (release-gated) | to be unified into one Terrier service (D-241) | Terrier unification lane | One Terrier service in `ops/compose.yaml` and [Geo] `compose.yaml`. |
| Per-district Terrier state volumes | Terrier | `ops/compose.yaml:139`, `:162`, `:185`, `:220-222` | n/a | `ops/compose.yaml` | n/a | to be unified into one Terrier service (D-241) | Terrier unification lane | One state volume; the three account stores need a migration plan. |

## Launchers

The launcher is native by nature (it opens the window; see the native section). The gap is the server it starts.

| Piece | App | Path | Image | Compose | D-188 gate | Gap | Owner | Plan to close |
|---|---|---|---|---|---|---|---|---|
| `Start.cmd` -> `Start-Terrier.ps1` (Edge/Chrome `--app` on 8767) | Terrier | [Geo] `Start.cmd:2`; [Geo] `Start-Terrier.ps1:19` | none | none | n/a | Starts the native CO-05 server. | Terrier app | Repoint to the unified service (D-241) through the local profile. |
| `Install-TerrierDesktop.ps1` (frontend build + `Terrier.lnk`) | Terrier | [Geo] `Install-TerrierDesktop.ps1:5`, `:58-59` | none | none | n/a | Runs `npm` on the host. | Terrier app | Superseded by the Electron installer (R1); no change. |
| `Start-DistrictServer.ps1`, `Watch-*Server.ps1`, `Stop-*Server.ps1` | Terrier | [Geo] `Start-DistrictServer.ps1:4` | none | none | n/a | to be unified into one Terrier service (D-241). Native per-district supervisors. | Terrier unification lane | Retire with the per-district servers; compose `restart:` for the unified service. |
| Electron Terrier district-transition pool (one backend per district) | Terrier | `ops/desktop/client/district-transition.cjs:18`, `:28` | none (native Electron) | none | n/a | to be unified into one Terrier service (D-241) | Terrier unification lane | One backend for the unified service; the pool goes away. |
| `Start-MentatApp.ps1` (`--app` window, ports 8802-8811) | Mentat | [Mentat] `mentat/app/Start-MentatApp.ps1:207`, `:348`, `:391` | none | none | n/a | Starts the native console. | Mentat app | Repoint for recipients via the local profile (step 3). |
| `MENTAT.exe` and `Install MENTAT.cmd` | Mentat | [Mentat] `mentat/app/desktop/Launcher.cs:17-19`; [Mentat] `mentat/Install MENTAT.cmd:2` | none | none | n/a | csc-built shim around `Start-MentatApp.ps1`. | Mentat app | Superseded by the Electron installer (R1). |
| Remote pod console | Mentat | [Mentat] `mentat/app/remote-console.psm1:107`, `:141` | none | none | n/a | SSH to a pod; out of laptop scope. | Mentat app | None. |
| Moneyball Edge/WebView2 launchers | Melange | `fec/desktop/launch_moneyball_edge.ps1:37`; `fec/desktop/launch_moneyball_edge.py:96`; `fec/desktop/moneyball_app.py:1` | none | none | n/a | Start native staffer-live (8841). | Staffer-live | Follows staffer-live. |
| `fec/glassbox_app.vbs` -> `open_glassbox_app.ps1` | Melange | `fec/glassbox_app.vbs:8`; `fec/open_glassbox_app.ps1:36`, `:229` | none | none | n/a | Starts native Glass box. | Glass box | Follows the Glass box ruling. |
| `Start-AccessDashboard.ps1`, `Start-AccessNotifier.ps1` | Platform | `ops/desktop/server/Start-AccessDashboard.ps1:21` | none | none | n/a | None: native by plan R2. | Platform installer | Desktop "Melange Admin" launcher (plan step 4). |
| `Start-LanPublisher*.ps1`, `Enable-TrustedLan.ps1` | Platform | `ops/desktop/server/Start-LanPublisher.ps1:4`, `:73` | none | none | n/a | See LAN publisher row. | Desktop release | None. |
| `ops/kit/Install-MelangeApps.ps1` (Edge shortcuts to hosted apps) | Platform | `ops/kit/Install-MelangeApps.ps1:6`, `:68` | n/a (remote apps) | n/a | n/a | None. | Deployment | None. |
| Operator consoles (`open_congress_pipeline_monitor.ps1`, `start_legislation_acceptance_service.ps1`, `bill_audit_terminal.ps1`) | Melange | `fec/legislation/open_congress_pipeline_monitor.ps1:32`; `fec/start_legislation_acceptance_service.ps1:21`; `bill_audit_terminal.ps1:5` | none | none | n/a | Monitor consoles and a service launcher for host pipelines. | Chain compute | Follow the jobs they serve. |

## Batch jobs

### Melange

Every refresh step launches through `compute_engine.launch` (`fec/refresh_tick.py:1474`): host form at `fec/compute_engine.py:211`, `docker compose run` form at `fec/compute_engine.py:227`, chosen per phase by `ops/compute/engines.json`. Fallback: `python ops/compute/cutover.py set <phase> windows` (`ops/compute/cutover.py:8`).

| Piece | App | Path | Image | Compose | D-188 gate | Gap | Owner | Plan to close |
|---|---|---|---|---|---|---|---|---|
| Chain (`run_pipeline.py`) | Melange | `fec/compute_engine.py:64-74` (`STEP_SERVICE`) | `compute-melange` (`ops/docker/compute-melange.Dockerfile:87`) | `ops/compute/melange.compose.yaml:30` `chain`; parity overlay `melange.parity.compose.yaml` | NOT PASSED: gate file `chain_parity.json` (`ops/compute/cutover.py:17`) absent [host]; last attempts failed, side B never ran (`.claude/skills/melange-chain-compute/references/parity.md:44-50`) | `engines.json:2` = windows. Blocked on relocatability ([brain] STATE.md:479); open question on one Windows run vs the A1/A2 compare ([brain] STATE.md:487). | Chain compute | Owned by that lane: D-201 proof, then `cutover.py set chain container`. |
| Network collectors (7 scripts) and the `llm` step (`refresh_sentinel_admissions.py`) | Melange | `fec/compute_engine.py:64-74`; `llm` is in the collect phase (`fec/compute_engine.py:57`) | `compute-collector` (`ops/docker/compute-collector.Dockerfile:2-3`) | `ops/compute/collectors.compose.yaml:84` `collect`, `:95` `llm`, `:107` `replay` | NOT PASSED: `collectors_replay.json` (`cutover.py:18`) absent [host] | `engines.json:3` = windows. Even on PASS the cutover is held on FEC key wiring, secret-file vars and a SIGTERM hunk (`.claude/skills/melange-chain-compute/SKILL.md:61-65`). | Chain compute | Owned by that lane: D-180 replay, then the three held items (FEC key wiring is Paul's). |
| Office view (LibreOffice recalculation) | Melange | `fec/compute_engine.py:250`, `:284`; called from `fec/refresh_tick.py:2131` | `office-calc` (`ops/docker/office-calc.Dockerfile:60`) | `ops/compute/office-calc.compose.yaml:10` `office-calc` | PASS: `office_equivalence.json` equivalent, `office_rank_check.json` 533/533 [host]; cut over in `958cc51af` ([brain] STATE.md:467) | Covered (`engines.json:4` = container). The evidence files are gitignored and live only on this laptop. | Chain compute | None. |
| Host-only refresh steps (bill repository, Washington Journal, press transaction, gates, derived layers, reference layer, press stance stack, `publish_dashboard`, diagnostics: 32 of the 49 in `fec/refresh_table.py:2`, plus the two tick-only steps) | Melange | `fec/compute_engine.py:15-21` | none | none | n/a: no container path exists | The largest uncovered block. D-173 puts the Melange chain and collectors last in the lane order. | Chain compute | Not designed; needs a lane design once chain and collect pass. |
| Playwright bill repository refresh (`update_bill_repository.py` -> `refresh_bill_repository_headless.mjs`) | Melange | `fec/legislation/update_bill_repository.py:52` | none (not in `STEP_SERVICE`) | none | n/a | Host-only, although `compute-collector` carries Node and Chromium. | Chain compute | Same as the row above. |
| Visible-Chrome congress.gov cookie lane | Melange | not a refresh step (`fec/compute_engine.py:15-21`) | none | none | n/a | Stays a host tool by D-188. | Chain compute | None (see native section). |
| Chain / collector replay harness (`chain_parity.py`, `collectors_replay.py`, `ops/compute/http_replay.py`) | Melange | `ops/compute/http_replay.py:686-690` | `compute-collector` (`replay` service) | `ops/compute/collectors.compose.yaml:107` | n/a (it is the gate) | Run by hand. | Chain compute | None. |

### Mentat

All ten jobs share one image, `ghcr.io/pjg93-222/compute-mentat`, pinned at CI digest `sha256:906fa995...` ([Mentat] `mentat/compute.images.lock:3-4`; built by `ops/github/workflows/compute-images.yml:24`), run through [Mentat] `mentat/tools/run_job.ps1`, compose [Mentat] `mentat/compute.compose.yaml` (entrypoint `compute_job.py`, `:18-19`). The D-188 gate for Mentat jobs is three consecutive passing parity runs on three snapshots on that digest ([Mentat] `mentat/COMPUTE.md:73-78`). The only container run on record is `pocket-build` on a local image (receipt `pocket-wrapper-2`, 2026-10-13 04:37 UTC [host]); local-image passes "do not count for the CI digest" (`.claude/skills/mentat-compute-and-money/references/jobs.md:41-44`).

| Piece | App | Path | Image | Compose | D-188 gate | Gap | Owner | Plan to close |
|---|---|---|---|---|---|---|---|---|
| `anchors` | Mentat | [Mentat] `mentat/compute.compose.yaml:58` | `compute-mentat` | `mentat/compute.compose.yaml` `anchors` | NOT PASSED on the CI digest (local-image passes only, [brain] STATE.md:469) | Production is the native task `MentatNightlyAnchors` (ran 2026-10-18 03:30, exit 0 [host]). | Mentat compute | Owned by that lane: streak on the CI digest with fresh inputs; the task swap ([Mentat] `mentat/COMPUTE.md:85-97`) needs Paul's yes (`.claude/skills/ops-shared/chain-of-command.md:38-41`). |
| `refit-detect` | Mentat | [Mentat] `mentat/compute.compose.yaml:77` | `compute-mentat` | `refit-detect` | NOT PASSED on the CI digest | Production is the native task `MentatRefitWatcher` (`Rscript.exe run_refit.R --mode detect` [host]). | Mentat compute | As `anchors`. |
| `refit-parity`, `refit` | Mentat | [Mentat] `mentat/compute.compose.yaml:95`, `:124` | `compute-mentat` | `refit-parity`, `refit` | NOT PASSED on the CI digest | Manual jobs; native R is the reference. | Mentat compute | Owned by that lane. |
| `pocket-build` | Mentat | [Mentat] `mentat/compute.compose.yaml:130` | `compute-mentat` | `pocket-build` | NOT PASSED on the CI digest | The console publish still runs `build_page.py` natively ([Mentat] `mentat/app/server.py:2356`). | Mentat compute | Owned by that lane; then point the console publish at the job. |
| `collect-fanout`, `pac-evidence` | Mentat | [Mentat] `mentat/compute.compose.yaml:144`, `:162` | `compute-mentat` | `collect-fanout`, `pac-evidence` | NOT PASSED: replay snapshot S1 judged R0/A1/A2 only, `complete: false`, no container side B [host] | Native equivalents in use: `Start-MentatApiCompanyCycleFanout.ps1` and the console's `pac_evidence.py` ([Mentat] `mentat/app/server.py:2049`). | Mentat compute | Owned by that lane (D-180 replay with side B). |
| `collect-downstream`, `-recover`, `-import` (D075) | Mentat | [Mentat] `mentat/compute.compose.yaml:178`, `:202`, `:224`; [Mentat] `mentat/tools/run_job.ps1:163`, `:167` | `compute-mentat` | the three services above | not checked: never run in a container; S0-S2 smoke open (commit `9391fd381`) | Host-native default; the container path refuses until the cutover record exists ([Mentat] `run_job.ps1:396`). | D075 lane | Owned by that lane. |
| D076 bulk activity index builder | Mentat | [Mentat] `mentat/tools/build_bulk_activity_index.py` | none | none | n/a | Native; no job. | D075 lane | Owned by that lane. |
| D-074 bulk operating expenditures | Mentat | reader [Mentat] `mentat/app/bulk_oppexp.py`, called at [Mentat] `mentat/app/candidate_spending.py:667-671` | none | none | n/a | The reader is part of the native console; the ingest writer was not found in the tree. | D075 lane | Owned by that lane. |
| Industry report chain (`mentat.R`, PowerShell 5.1 orchestrators, R collectors, Node workbook builders, Excel COM finalizer) | Mentat | [Mentat] `mentat/tools/parallel/Start-MentatResilientIndustryReports.ps1:95`; [Mentat] `mentat/tools/build_industry_report.ps1:967`, `:988`; [Mentat] `mentat/tools/finalize_crypto_excel.ps1:259` | none | none | n/a | Left out on purpose ([Mentat] `mentat/COMPUTE.md:34-36`): PowerShell 5.1, Excel COM and `@oai/artifact-tool` (absent from the image, `ops/docker/compute-mentat.Dockerfile:7`). The D-178 openpyxl writer exists behind `MENTAT_XLSX_WRITER` ([Mentat] `build_industry_report.ps1:169-172`). | Mentat compute | Design tasks M2-c and M2-d ([Mentat] `mentat/COMPUTE.md:34-36`): prove the openpyxl writer, then port the orchestrators. |
| Scheduled native jobs (`MentatNightlyAnchors`, `MentatRefitWatcher`) | Mentat | `%LOCALAPPDATA%\Mentat\scheduled\run_logged.ps1` (outside git) [host] | none | none | see `anchors` / `refit-detect` | Native until the streak passes. | Mentat compute | See `anchors`. |

### Terrier

| Piece | App | Path | Image | Compose | D-188 gate | Gap | Owner | Plan to close |
|---|---|---|---|---|---|---|---|---|
| Store build, 10 steps (roads, street, contacts, shown, scores, walk, ingest, extend, bundle, density) | Terrier | [Geo] `tools/build_step.py:58-88`; [Geo] `tools/compute_build.py:3-9` | `terrier-build`, `terrier-build-score` ([Geo] `Dockerfile.build:33`, `:53`) | [Geo] `compute.compose.yaml:45` `terrier-build`, `:98` `terrier-score`, `:104` `terrier-export` | PASS for CO-05: "PARITY PASS, 17 of 17" ([Geo] `COMPUTE.md:281`), bundle byte-identical ([Geo] `COMPUTE.md:333`); cut over with `TERRIER_COMPUTE=windows` fallback ([Geo] `COMPUTE.md:207`) | Only CO-05 was run ([Geo] `COMPUTE.md:21`, `:180`); parity used local builds, CI digests not compared ([Geo] `COMPUTE.md:8-9`); no production build has run through it yet ([Geo] `COMPUTE.md:15-16`); the cited `parity_report.json` files are no longer on disk [host]. | Terrier compute | Owned by that lane: OR-05 and CT-01 parity, then compare the CI digests. |
| Host-only build steps (import, `prepare_geography`, R6/R7, pincheck, smoke, gate, stage, Luna phase 3) | Terrier | [Geo] `COMPUTE.md:302-304`; `tools/luna_district` uses `ctypes.windll` ([Geo] `COMPUTE.md:183`) | none | none | n/a | Census downloads and spare-port server steps stay on the host. | Terrier compute | None planned. |
| Management commands (`invite`, `backup`, `import_summary`) | Terrier | [Geo] `server/research/management/commands/` | `terrier-web` when hosted: `import_summary` at container start ([Geo] `tools/container_start.py:54`), `invite` and `backup` by `manage.py` inside the container ([Geo] `HOSTED.md:82`, `:91`) | VPS: `ops/vps/melange-ops.sh` runs `manage.py backup` inside the container | n/a | Native on the laptop. | Terrier app | Follows the local profile (step 3). |
| `_build` runners (`run_*.ps1`, `launch_run_*.ps1`, `swap_co_live.ps1`) | Terrier | [Geo] `_build/` (untracked, `.gitignore:11`) | none | none | n/a | Native wrappers that call `tools/compute_build.py`. | Terrier compute | None. |

## Scheduled tasks and orchestrators

State read on 2026-10-18 [host].

| Piece | App | Path | Image | Compose | D-188 gate | Gap | Owner | Plan to close |
|---|---|---|---|---|---|---|---|---|
| `fec/refresh_tick.py` (task `MelangeRefreshTick`) | Melange | `ops/scheduler/register_refresh_tick_task.ps1:41`, `:80` | none (dispatches steps to containers by phase) | none | n/a (dispatcher; each phase has its own gate) | Task NOT registered, so D-216's single orchestrator is not running. | Self-healing | Owned by that lane; registration is Paul's yes. |
| `fec/refresh_watchdog.py` (task `MelangeRefreshWatchdog`) | Melange | `ops/scheduler/register_refresh_watchdog_task.ps1:19`, `:38` | none | none | n/a | Task NOT registered. | Self-healing | Owned by that lane. |
| `MoneyballWeeklyRefresh` (`weekly_refresh.py`), `MoneyballCongressRefresh` (`daily_refresh.py`) | Melange | `fec/weekly_refresh.py:4-6`; `fec/register_daily_refresh_task.ps1:27`, `:50` | none | none | n/a | Ready and still scheduled, although D-216 makes them manual wrappers. | Self-healing | Owned by that lane: retire when the tick is registered. |
| `MoneyballDailyRefreshRetry`, `MoneyballCollectorSupervisor` | Melange | `fec/register_daily_refresh_task.ps1:28`; `fec/collect/collector_supervisor.py` | none | none | n/a | Disabled. | Self-healing | None. |
| `MelangeStafferLiveSidecar` | Melange | `fec/surfaces/staffer_live_watchdog.ps1:11` | none | none | n/a | Disabled. | Staffer-live | Follows staffer-live. |
| `MelangeAutoRelease` (`ops/release.py auto`), `MelangeSourceSync` | Platform | `ops/auto/Register-AutoRelease.ps1:21`, `:34`; `ops/auto/Register-SourceSync.ps1:15`, `:20` | none | none | n/a | Not registered, so D-183 auto-release is not running. | Deployment | Paul's yes to register. Stays native (see native section). |
| `MentatNightlyAnchors`, `MentatRefitWatcher` | Mentat | see the Mentat batch table | none | none | NOT PASSED | Ready, native. | Mentat compute | See `anchors`. |
| VPS timers (`melange-update`, `access-jwks`, `healthchecks`, `terrier-backup`) | Platform | `ops/vps/systemd/melange-update.timer:5`, `terrier-backup.timer:5` | n/a (they drive `docker compose`) | `ops/compose.yaml` | n/a | None. | Deployment | None (see native section). |

## Report tools

| Piece | App | Path | Image | Compose | D-188 gate | Gap | Owner | Plan to close |
|---|---|---|---|---|---|---|---|---|
| Node Playwright collectors (`.mjs` congress scrapers) | Melange | `fec/legislation/refresh_legislation_discovery.py:30`; `fec/collect/collect_congress_advanced.mjs:18` | `compute-collector` (Node + Chromium, `ops/docker/compute-collector.Dockerfile:2-3`) | `collectors.compose.yaml` `collect` | NOT PASSED (collect phase) | Run natively while `collect` = windows. | Chain compute | Follows the collect cutover. |
| `build_audit_workbook.mjs` | Melange | `fec/surfaces/build_audit_workbook.py:9` | none | none | n/a | Dead path: the Python port replaces it. | Chain compute | None. |
| Mentat Node workbook builders (`crypto_workbook_builder.mjs` and others) + `@oai/artifact-tool` | Mentat | [Mentat] `mentat/tools/build_industry_report.ps1:967` | none (`compute-mentat` has the Node binary only, `ops/docker/compute-mentat.Dockerfile:7`) | none | n/a | Native only. | Mentat compute | D-178 openpyxl switch (see the industry report row). |
| Chromium/Edge headless figure export | Mentat | [Mentat] `mentat/app/figure_pdf.py:428` (route [Mentat] `server.py:3709-3713`) | none (`compute-mentat` carries Chromium but no job runs it) | none | n/a | Native headless Edge, called from the native console. | Mentat compute | Not planned; the jobs reference lists figure export as not containerized on purpose. |
| Chromium PDF in Melange | Melange | none found (`--print-to-pdf`, `puppeteer` searched); desktop figures use Electron (`ops/desktop/packaging/desktop_figure_renderer.py:1`) | n/a | n/a | n/a | None. | Desktop release | None. |
| Terrier walk-list PDF | Terrier | [Geo] `frontend/src/v3/lib/walkPdf.ts:3-4` (jsPDF in the browser; [Geo] `HOSTED.md:74`) | n/a (client-side) | n/a | n/a | None. | Terrier app | None. |
| LibreOffice office-calc | Melange | `ops/compute/office_recalc.py:394` | `office-calc` (`ops/docker/office-calc.Dockerfile:2`, `:45-46`) | `ops/compute/office-calc.compose.yaml:10` | PASS (office row above) | Covered. Mentat has no office-calc path; the design's task 2d is not implemented. | Chain compute | Mentat: none planned. |
| Excel COM recalculation (Melange) | Melange | `fec/recalculate_formula_cache_copy.ps1:357` | none | none | n/a | Reference side of the office gate (D-174). | Chain compute | None (see native section). |
| Excel COM finalizer (Mentat) | Mentat | [Mentat] `mentat/tools/finalize_crypto_excel.ps1:259` | none | none | n/a | Production, native only. | Mentat compute | See the industry report row. |
| Terrier image scan (`tools/image_gate.py`, trivy) | Terrier | [Geo] `.github/workflows/release.yml:293` | n/a (runs on the CI runner) | n/a | n/a | None. | Terrier app | None. |

## Pieces that legitimately stay native

| Piece | Path | Why native |
|---|---|---|
| Electron desktop shell (one client, three apps) | `ops/desktop/client/package.json:5`, `:6`, `:8`, `:10`, `:18`; NSIS config `ops/desktop/client/builder.cjs:25-26` | It is the native window and installer (ruling R1). It spawns its bundled backend on a free loopback port (`ops/desktop/client/runtime.cjs:11`, `:54`), and "End users do not need Docker" (`ops/desktop/client/README.md:3`). |
| Browser app windows (Edge/Chrome `--app`, WebView2) | [Geo] `Start-Terrier.ps1:19`; [Mentat] `mentat/app/Start-MentatApp.ps1:391`; `fec/desktop/launch_moneyball_edge.ps1:37`; `fec/desktop/moneyball_app.py:1` | A window on the desktop cannot live in a Linux container. Only the server behind it moves. |
| Windows Task Scheduler and the registration scripts | `ops/scheduler/*.ps1`, `ops/auto/Register-*.ps1`, `fec/register_daily_refresh_task.ps1` | D-216 keeps `refresh_tick.py` as a scheduled host orchestrator that dispatches each phase to a container. |
| Native admin dashboard | `ops/desktop/server/admin_dashboard.py:92-94`, `:220`, `:235` | Ruling R2: extend the native dashboard; no Docker socket in any container. Loopback only, ephemeral port. |
| Visible-Chrome congress.gov cookie lane | (manual host tool) | D-188: "The visible-Chrome congress.gov lane stays a host tool." |
| Excel COM recalculation as the reference | `fec/recalculate_formula_cache_copy.ps1:357` | D-174: Excel is the reference the LibreOffice view is proven against. |
| Release signing and source sync | `ops/release.py`, `ops/sync_sources.py`, `ops/auto/` | Digests are signed on the laptop into one platform manifest (D-183, [brain] STATE.md:458); D-175 keeps the signing key on the laptop. |
| VPS systemd timers | `ops/vps/systemd/` | They are the host side that runs `docker compose` on the VPS. |
| CI-only tools | `ops/github/workflows/*.yml`; [Geo] `tools/image_gate.py` | They run on GitHub runners, not on the laptop (D-175). |
| Developer and test servers | [Geo] `Start.ps1` (8765), `Start-v3.ps1` (8766), `Start-test-demo.ps1` (Node fixture); `.claude/launch.json:7`, `:13` (static preview on 8802) | Development tools, not production. |

## Stale statements found (not fixed here)

- `fec/compute_engine.py:6` shows `"office":"windows"` as the example; `ops/compute/engines.json:4` is `"container"`.
- `ops/compute/README.md:200-202` still says nothing was built or run in a container.
- `ops/compute/cutover.py:20-21` says nothing writes the rank-check evidence yet; `xl_equivalence.py --rank-check-report` writes it.
- [Mentat] `mentat/COMPUTE.md:4-6` and `:181-182` say Docker is not installed and the image has no digest; the lock holds a CI digest and Docker runs on this laptop ([brain] STATE.md:493).
- [brain] STATE.md:492 says the self-healing build is uncommitted; git shows P0, P1, P2 and P4 on `main` (see Overlaps).

## Not inventoried

Test suites, one-off scripts under `audits/` and `tmp/`, quarantined copies under `fec/_quarantine_paulcomputer_20260727/`, `fec/speech/*.ps1` and the other single-purpose `fec/*.ps1` pipeline wrappers (they run on the host and follow their pipeline's phase), and the optional Rust scanner build in [Mentat] `mentat/tools/build_native.ps1`.