# Container path for the host-only refresh steps (design, not implemented)

Status 2026-10-18. Design only: nothing here changes code, jobs, engine flags or images. It closes the gap named in `ops/DOCKER_COVERAGE.md:40` and `:101` ("Not designed; needs a lane design once chain and collect pass").

Rulings respected:
- D-173: lane order. Melange chain and collectors come last, so everything here starts after chain and collect pass.
- D-177: content-level equivalence, with three byte gates.
- D-179: claude CLI login mounted read-only, for the two steps it names.
- D-180: collectors proven by record/replay.
- D-188: each system switches when its gate passes, with a one-flag fallback. The visible-Chrome lane stays a host tool.
- D-201 plus Paul's 2026-10-18 ruling: one Windows run, A2 = A1, fixed volatile list.
- D-212/D-216: `fec/refresh_tick.py` is the single orchestrator; the chain is transactional.

Citations are `path:line` against the `docker-coverage` worktree at `ba1d2e1ba`. One exception: `ops/compute/chain_parity.py` carries another agent's uncommitted `--single-reference` work, so lines cited from it are marked (working copy).

Method: code reading, plus two read-only censuses run for this design. No step was executed and nothing was written.
- **Import census.** `ops/compute/chain_imports.py` `scan()`, seeded with each host-only producer instead of `CHAIN`. Every hard third-party import of the 33 producers is in `ops/locks/compute-melange.linux.txt` (numpy, pandas, openpyxl, scikit-learn, networkx, anthropic, cryptography). `ops/locks/compute-collector.linux.lock` holds only openpyxl, et-xmlfile and pypdf. Subprocess children (Rscript, node, claude) are outside the census and are cited by hand below.
- **Seam census.** The chain's own seam detectors from `ops/tests/test_compute_seams.py` (`windows_path_literals`, `text_writes_without_newline`, `bare_localappdata_reads`, `stored_path_reads`), applied to each producer's import closure. Only modules outside the chain closure, which that suite already lints, are counted.

## 1. The 34 steps

Each row: the `fec/refresh_table.py` line, the step, its producer, and its group (section 3). T marks tick-only rows (`tick_only=True`).

| Line | Step | Producer | Group |
|---|---|---|---|
| 186 | Congress.gov bill repository | `fec/legislation/update_bill_repository.py` | E |
| 214 | interviews: Washington Journal (21d) | `fec/collect/refresh_cspan_wj.py` | H |
| 224 | Bluesky collector (T) | `fec/collect/tweet_collector.py` | D |
| 234 | press delta refresh | `fec/collect/refresh_press_stream.py` | H |
| 237 | press intake audit | `fec/audit_collection.py` | B |
| 242 | press engine feed | `fec/collect/build_press_engine_feed.py` | B |
| 247 | fold promoted press runs | `fec/collect/fold_promoted_press_runs.py` | G |
| 254 | press zero-row review (T) | `fec/collect/press_zero_row_review.py` | B |
| 258 | collection intake audit | `fec/audit_collection.py` | B |
| 262 | accepted audio intake | `fec/members/b5_audio_manifest_adapter.py` | A |
| 265 | collector roster verification | `fec/legislation/verify_collector_roster_updates.py` | A |
| 269 | CU hearings extract | `fec/stance/extract_cu_hearings.py` | C1 |
| 270 | CU interviews extract | `fec/stance/extract_cu_interviews.py` | A |
| 271 | CU bluesky extract | `fec/stance/extract_cu_bluesky.py` | C2 |
| 272 | Big Five traits | `fec/members/big5_traits.py` | C2 |
| 273 | interaction features | `fec/members/interaction_features.py` | C2 |
| 274 | behavior panel | `fec/members/behavior_panel.py` | C2 |
| 275 | behavioral dispositions | `fec/members/behavioral_dispositions.py` | C2 |
| 276 | Big5 v2 (behavior-calibrated) | `fec/members/big5_lexcal_v2.py` | C2 |
| 277 | Big5 v3 (hybrid) | `fec/members/big5_hybrid.py` | C2 |
| 278 | social interaction layer | `fec/network/social_interaction_layer.py` | C2 |
| 279 | press engagement layer | `fec/network/press_engagement_layer.py` | A |
| 280 | press HCoI layer | `fec/network/press_hcoi.py` | A |
| 281 | multilayer network | `fec/network/build_multilayer_network.py` | A |
| 282 | co-appearance layer | `fec/network/co_appearance_layer.py` | C2 |
| 284 | reference layer (classify new evidence) | `fec/members/member_reference_layer.py --score --go` | F |
| 287 | press stance stack | `fec/stance/press_stance_stack.py` | A |
| 294 | prepare dashboard deployment | `fec/surfaces/publish_dashboard.py` | I |
| 300 | signed coalitions (diag) | `fec/network/signed_coalitions.py` | C2 |
| 301 | KB benchmark (diag) | `fec/engine/kb_benchmark.py` | C2 |
| 302 | LEP criterion (diag) | `fec/members/lep_criterion.py` | C2 |
| 303 | say-do gap (diag) | `fec/stance/say_do_gap.py` | C2 |
| 304 | financial_other discovery (review sheet) | `fec/lobbying/financial_other_discovery.py` | C2 |
| 306 | forecast ledger (freeze+score) | `fec/engine/forecast_ledger.py` | C1 |

Group sizes: A 7, B 4, C1 2, C2 14, D 1, E 1, F 1, G 1, H 2, I 1 = 34.

## 2. Cross-cutting findings

1. **Chain twins.** Eleven of the 34 rows (ten producers) run producers that are also `run_pipeline.py` CHAIN steps (`fec/run_pipeline.py:56-58`, `:105`, `:114`, `:118-120`, `:122-123`).
   - Seven rows pass exactly the chain's arguments (group A), so the chain container already runs that code.
   - `collection intake audit` adds only `--scope all`, which is the default (`fec/audit_collection.py:32-36`). It still goes to group B, because it shares its basename with `press intake audit`.
   - Three rows pass different arguments, so chain parity does not cover them: `--scope press`, `--intake-receipt` and `--score --go`.
2. **Windows path concatenation.** Fourteen modules these steps reach build paths as `B + r'\data\...'`: the twelve C2 producers, `fec/collect/tweet_collector.py` and `fec/surfaces/lock_dashboard.py`. Examples: `fec/members/big5_traits.py:29`, `:63`, `:257`; `fec/collect/tweet_collector.py:31`.
   - On Linux, `'/melange' + '\\data\\...'` names a file that does not exist. Each module therefore fails at its first open rather than writing somewhere wrong. This is inferred from code order: the first file access in each is a read.
   - The fix is `os.path.join(B, ...)`. It gives the identical string on Windows, because `paths.ROOT` is passed through `abspath` (`fec/paths.py:9`).
3. **The lease handoff keys on phase, not on producer.** `handoff = spec.phase == 'chain'` (`fec/refresh_tick.py:1478`, `fec/weekly_refresh.py:333`) releases the writer lease for the whole step (`fec/refresh_tick.py:816-831`).
   - Mapping a producer that never takes the lease onto the chain service would leave the lease unheld for that step's duration.
   - During that window, `daily_media_refresh` or `refresh_congress_pipeline` could start a second writer; both take the lease (`fec/collect/daily_media_refresh.py:662`, `fec/legislation/refresh_congress_pipeline.py:342`).
   - Only `run_pipeline.py` and `update_bill_repository.py` take the lease themselves (`fec/legislation/update_bill_repository.py:739-741`).
4. **`STEP_SERVICE` is global and keyed by basename** (`fec/compute_engine.py:64-74`, `:201`, `:214`). Four launchers read it:
   - `fec/refresh_tick.py:1474` and `fec/weekly_refresh.py:332` launch with all phases.
   - `fec/collect/daily_media_refresh.py:557` and `fec/legislation/refresh_congress_pipeline.py:370` launch with `phases=('collect',)`. These two daily runners launch 9 of the 34 producers (`daily_media_refresh.py:298-339`, `refresh_congress_pipeline.py:159`) and never hand the lease out.
   - Basename keying also means both `audit_collection.py` rows always move together.
5. **Two transports have no replay.**
   - Browser sessions (Playwright) and chromote: `ops/compute/http_replay.py:33-34` excludes both.
   - The claude CLI as the classifier calls it is covered. `classifier_runtime.classify_json` spawns `subprocess.Popen` over temp files (`fec/classifier_runtime.py:31`); besides `subprocess.run` (`http_replay.py:288`), the hook replaces `Popen` with a subclass (`:289`) that records and replays a `claude` argv whose stdout is a file (`_cli_popen`, `_cli_spawn`, `:515-568`). This was a gap before that hook.
   - R `httr`/`rvest` is covered, by proxy mode (`http_replay.py:19-21`). That needs `cryptography` in the image that runs the proxy (`http_replay.py:26`).
6. **Containers see only the forwarded environment.** Host children get the tick's whole environment (`fec/refresh_tick.py:1473`); a container gets `FORWARD` only (`fec/compute_engine.py:50`, `:231-233`).
   - The derived layers read many dials, for example `BIG5_SOURCES`, `REF_DYAD_CAP`, `SOC_VOLFLOOR`, `NET_CONFIG` and `MEDIA_ADMIT` (`fec/members/big5_traits.py:28-41`, `fec/network/build_multilayer_network.py:26-82`). `PRESS_BASELINE_FOLD` gates the fold (`fec/collect/fold_promoted_press_runs.py:34-36`).
   - Any dial set on the host would silently take a different value in a container.
   - Chain parity's Windows side inherits only `ENV_ALLOW` (`ops/compute/chain_parity.py:68`, working copy), so parity proves only the configuration with no dials set. The chain has the same exposure today.
7. **`social_media.db` is a WAL database.**
   - Two of these steps write it: `tweet_collector.py:34`, and `refresh_cspan_wj.py:84` via `members/load_cspan.py:139`. Container writers must stage the database (`ops/compute/collectors.compose.yaml:27-28`, `ops/compute/README.md:89-91`).
   - Ten read it. Six do so through read-write connections: `big5_traits.py:63`, `interaction_features.py:55`, `behavior_panel.py:29`, `social_interaction_layer.py:28`, `co_appearance_layer.py:36` and `member_reference_layer.py:137`. The rest open it `mode=ro`, for example `fec/stance/extract_cu_hearings.py:60` and `fec/audit_collection.py:468`.
   - Whether these reads work over the VM file share is what chain parity will show: the chain itself reads the database `mode=ro` (`fec/stance/extract_cu_interviews.py:50`).

## 3. Groups

Images and services:
- `compute-melange` (`ops/docker/compute-melange.Dockerfile`) runs in the chain service (`ops/compute/melange.compose.yaml:30`): network none (`:34`), tree rw (`:56`), Draft ro (`:57`), data ro (`:58`), `%LOCALAPPDATA%\Melange` rw (`:59`).
- `compute-collector` (`ops/docker/compute-collector.Dockerfile`) carries Node, Playwright, Chromium at `/usr/bin/google-chrome` (`:113`) and the claude CLI. It runs in two services: `collect` (`collectors.compose.yaml:84`, tree rw) and `llm` (`:95`, tree ro at `:100`).
- `compute-mentat` carries R 4.6.0 and Chromium (`ops/docker/compute-mentat.Dockerfile:5-8`).

No new `engines.json` key is needed; `load()` accepts only `collect`, `chain` and `office` (`fec/compute_engine.py:42`, `:98-99`). Each group rides an existing key and gets its own evidence gate (P3).

| Group | Steps | What blocks it today (cited) | Image | Service / profile | Key | Parity under D-201 single reference | Effort |
|---|---|---|---|---|---|---|---|
| **A** chain twins | 262, 265, 270, 279, 280, 281, 287 | Nothing in the producers. Each has the same producer and argv as its CHAIN step, sits inside the linted chain closure, and its imports are in the lock. Only P1-P3 are needed. The press readers open Draft read-only (`press_hcoi.py:21`, `press_stance_stack.py:51`), which `:57` provides. | compute-melange | `melange.compose.yaml` `chain` | chain | Inherited. Chain parity's B run executes these producers with these argv on the frozen inputs, and their outputs appear in that report's G4. Gate: `chain_parity.json` PASS (`ops/compute/cutover.py:17`). | S |
| **B** press transaction, Python half | 237, 242, 254, 258 | The argv differs from the chain's (`--scope press`, `--intake-receipt`), so chain parity does not cover these rows. Zero-row review is lint-clean and outside the chain. All are network-free. They read `outputs/press_refresh_*`, `MELANGE_DATA` (`audit_collection.py:38-61`) and the Draft baseline (`press_baseline_fold.py:63`), all read-only. The tick runs press rows in its collect phase (`refresh_table.py:90`), but the engine follows the service's phase (`compute_engine.py:204`), so these rows follow the chain flag. | compute-melange | `chain` | chain | One step-parity run (P4) of the four rows in table order, on one frozen copy holding the newest sealed `outputs/press_refresh_*`, the `data/engine` receipts and the MELANGE_DATA manifests. Receipt clocks go on the volatile list, each cited at its writer line. | S |
| **C1** lint-clean, not in the chain | 269, 306 | `extract_cu_hearings` reads the database `mode=ro` (`:60`) and writes `data/stance/hearings_cu.csv` (`:42`). `forecast_ledger` writes `%LOCALAPPDATA%\Melange\lda_rebuild\derived` (`:32-36`), which `:59` mounts rw. | compute-melange | `chain` | chain | Step parity. The ledger runs on scratch local directories on both sides (the `require_scratch_dirs` rule). Volatile: `frozen_utc` (`forecast_ledger.py:321`, `:356`). | S |
| **C2** seam fixes | 271-278, 282, 300-304 | Windows path literals, by detector count: big5_traits 6, interaction_features 2, behavior_panel 11, behavioral_dispositions 7, big5_lexcal_v2 3 (plus an implicit-EOL write at `:88`), big5_hybrid 5, social_interaction_layer 4, co_appearance_layer 4, signed_coalitions 3, kb_benchmark 3, lep_criterion 6, say_do_gap 2. extract_cu_bluesky imports `collect/tweet_collector.py`, which has 7. financial_other_discovery has a bare `LOCALAPPDATA` read (`:311`) and an implicit-EOL write (`:293`). Five open the WAL database read-write (finding 7). The diag rows read chain outputs (`lep_criterion.py:112`, `kb_benchmark.py:61`). | compute-melange | `chain` | chain | Fix first. The fixes are byte-neutral on Windows: `os.path.join`, `newline='\r\n'`, `paths.LOCAL_MELANGE`. Confirm that by hashing the outputs of one Windows run before and after the edit. Then one step-parity run of the DERIVE and DIAG rows in table order, on a frozen copy that includes the chain outputs the diag rows read. | M |
| **D** Bluesky | 224 | The tweet_collector literals (above). It writes the WAL database (`:34`), so it needs staging (P7). Keyless urllib egress. The daily runner also launches it (`daily_media_refresh.py:299`). | compute-collector | `collectors.compose.yaml` `collect` | collect | D-180: record on Windows, then replay in the `replay` profile through the in-process urllib hook (`http_replay.py:8-12`), over a short fixed window. Gates as in `collectors_replay`: `social_media.db` table rows plus the receipt. | M |
| **E** bill repository | 186 | It takes the lease itself and adopts the tick's (`update_bill_repository.py:739-741`; `refresh_tick.py:61-66`). A container cannot adopt a Windows lease (`README.md:36-37`), so it needs the P1 handoff. It drives headless Playwright (`update_bill_repository.py:310-395`); the image has Playwright, and the `.mjs` Linux default path matches (`refresh_bill_repository_headless.mjs:22-25`). The browser profile defaults to `os.tmpdir()` (`:26-29`), which is the tmpfs `/tmp`, wiped every run. `tick_argv` applies to both engines, so the container profile path needs an environment-variable default in the code. It writes `bill_repository_live.json` at the tree root, which `collect` allows. `refresh_congress_pipeline.py:159` also launches it. | compute-collector | `collect`, with the `/profile` volume (`collectors.compose.yaml:92`) | collect | **No replay transport** for browser sessions (`http_replay.py:33-34`), so D-180 cannot be met as written. Paul's decision 1. | L |
| **F** reference layer | 284 | It writes `member_references.json` at the tree root by temp file and replace (`member_reference_layer.py:384`; `moneyball_core.py:326-332`), but `llm` mounts the tree ro (`collectors.compose.yaml:100`). It opens the WAL database read-write (`:137`, `:172`) under that read-only root, which is inferred to fail. The transport comes from the policy file, default `api` (`:234-250`). The API transport reads its key from the environment or `fec/anthropic_key.txt` (`:242-244`); the compose file defines no API secret (`:93`, `:105`), and `compute-collector` lacks `anthropic`. The CLI transport calls `classify_json` (`:301-303`), whose `Popen` the replay hook now records and replays (finding 5). D-179 names two other steps, not this one. The daily runner also launches it (`daily_media_refresh.py:339`). | compute-collector (CLI) | `llm` | collect (`compute_engine.py:57-58`), although the tick runs it in its compute phase | D-180 record/replay of the classifier responses, never a second live call (`audits/deploy_20261011/COMPUTE_CONTAINERS_DESIGN.md:378-386`). The Popen hook it needed exists (finding 5). | L |
| **G** press baseline fold | 247 | When enabled (`fold_promoted_press_runs.py:34-36`, `:215-217`), it rewrites the Draft Systems baseline (`press_baseline_fold.py:63`). No service mounts Draft rw (`melange.compose.yaml:57`). `PRESS_BASELINE_FOLD` is not in `FORWARD` (`compute_engine.py:50`). Three stored-path reads lack `to_host` (`fold_promoted_press_runs.py:88`, `:99`, `:122`). The daily runner also launches it (`daily_media_refresh.py:317`). | compute-melange | New `chain-draft` profile: `chain` with `/draft` rw. The mount must be the whole directory, because replacing a file onto a single-file bind fails. | chain | Step parity on scratch Draft copies (`--draft-dir`), with the fold enabled on both sides. | M |
| **H** R collectors | 214, 234 | Both shell out to Rscript (`refresh_press_stream.py:114-123`, `refresh_cspan_wj.py:30-37`), and no Melange image has R (`compute-collector.Dockerfile:28`). `compute-mentat` has R 4.6.0, Chromium, and every package the walk and scraper load, by name (`ops/locks/r-packages.txt`; versions not compared with `outputs/r-library`). `MELANGE_R_LIBRARY` defaults into the tree (`refresh_press_delta.R:64`); in a container it must point at an empty directory, or the Windows binaries there load first. Draft is opened read-only (`:46-50`), and environment overrides for the roots exist (`refresh_press_delta.R:46-47`, `cspan/cspan_scrape.R:90`). WJ also writes the database (P7) and uses chromote (`cspan_scrape.R:41`). It imports `members.fetch_interviews` (`load_cspan.py:28`), whose module-level literal (`fetch_interviews.py:124`) fails at import on Linux. The press walk sets `CU_PR_NO_CHROMOTE=1` (`refresh_press_delta.R:55`). On Linux the timeout kill reaches only the direct Rscript child (`refresh_press_stream.py:136-139`); stopping the container ends the workers. The daily runner launches both. | compute-mentat plus `cryptography`, or a new R collector image (Paul's decision 4) | New `r-collect` profile in `collectors.compose.yaml`: egress, tree rw, Draft ro | collect | Press walk: proxy replay of a bounded walk (`--max-delta` small, `--workers 1`); the gates are the run root's files by content plus the receipt. **WJ: no replay transport for chromote.** Paul's decision 1. | L |
| **I** dashboard deployment | 294 | Paul ruled "Don't need to do that" (`refresh_tick.py:409-411`), and the step is SKIPPED_NOT_CONFIGURED without `DASHBOARD_PASSWORD_FILE` (`:413-418`). Its phase key would be `office` (`compute_engine.py:43`), whose gate proves LibreOffice, so a new key would be needed. The output never repeats (random salt and IV, `lock_dashboard.py:77`), so parity would need decrypt-and-compare. Windows literals sit at `lock_dashboard.py:12-13`. | none | none | none | Not proposed. | M if ever |

## 4. Shared prerequisites (before any group moves)

| # | Change | Why (cited) | Effort |
|---|---|---|---|
| P1 | Hand the lease out only for producers that take it themselves (`{run_pipeline.py, update_bill_repository.py}`), not for every step where `phase == 'chain'`. | Finding 3 (`refresh_tick.py:1478`, `weekly_refresh.py:333`). | S |
| P2 | Put the new mappings in a second, tick-scoped map that only `refresh_tick.py:1474` consults. The legacy wrappers keep these producers on the host. | Finding 4: the daily runners launch 9 of these producers and hand no lease out. D-216 makes the wrappers manual. | S |
| P3 | Per-group gate. Each new entry names `audits/compute_parity/steps/<group>.json`. `engine_of` returns container only when the phase key is container and that file says PASS; it reads the file once, in `snapshot()` (`compute_engine.py:114-119`). `cutover.py show` lists the groups. The phase key stays the one-flag fallback. | D-188 requires a gate per system; `engines.json` cannot take new keys (`compute_engine.py:98-99`). | M |
| P4 | Step parity. Reuse `freeze`, `verify` and `compare --single-reference` with the fixed volatile list (`chain_parity.py:27-31`, `:216-243`, `:763-766`, working copy). Add `command --script/--args`; today `build_command` runs only `run_pipeline.py` (`chain_parity.py:700-716`, working copy). Add a switch that drops the chain-only G2/G3 when the step writes no byte artifact. The volatile list masks JSON value paths only (`is_volatile`, `:238-243`), so a clock column in a CSV would need a list extension. On the collector side, generalize `collectors_replay.py` beyond `crec` (`README.md:95-111`). | D-201 plus today's ruling; D-180. | M |
| P5 | Add the extension producers as seeds to `chain_imports.py`, which scans only `CHAIN` (`:53-65`), and to the seams lint (`test_compute_seams.py:51-65`, `:95`), so CI enforces the lock and the seams for them. | The census in the header holds only for this commit. | S |
| P6 | Before taking the lease, stop any leftover `melange-*` container (`compute_engine.py:195-196`). | The exit guard kills the docker client, not the container (`refresh_tick.py:2469-2474`; `compute_engine.py:238-241`). A hard-killed tick can leave a container writing after the lease is gone (inferred; not observed). | S |
| P7 | Stage `social_media.db` for container writers. | `collectors.compose.yaml:27-28`; owned by the collect lane. | M |
| P8 | Environment parity: for each group, refuse container mode, or forward an allowlist, when a dial its producers read is set in the host environment. | Finding 6. | S |

## 5. Risks checked

| Failure mode | Where | Exposure | Status |
|---|---|---|---|
| Lease released during a step that does not take it; a second writer starts | A, B, C1, C2, G, if mapped naively | Real (finding 3) | Closed by P1 (design) |
| Handoff gap: another refresh takes the lease between hand-out and the container taking it | E | Inherited from the chain. Fails closed but loses the run (`.claude/skills/melange-chain-compute/references/traps.md:16`) | Open; accepted for the chain |
| Orphan container still writing after the lease is released | all | Inferred | P6 |
| A legacy wrapper launches a container that cannot adopt its lease | D, E, F, G, H, via the daily runners | Real (finding 4) | Closed by P2 |
| Silent behaviour change from unforwarded dials | C2, F, G | Real (finding 6); the chain has it too | P8 |
| Torn or undetected writes: the tick diffs `(size, mtime_ns)` (`fec/refresh_state.py:327-357`) across the Docker Desktop share | all | Unproven (`README.md:213`) | Shared with the chain; chain parity is the first evidence |
| WAL corruption or read failure over the VM share | C2, D, F, H | Real for writers; unknown for readers | P7 for writers. Readers follow chain parity, else a staged read copy |
| Secret visible to a container | F: `llm` mounts the whole tree ro (`:100`), so an in-tree `fec/anthropic_key.txt` would be readable. All chain-service groups: `%LOCALAPPDATA%\Melange` is mounted rw, and it includes `lda_rebuild`'s key files, which `collect` deliberately excludes | Whether the files exist was not checked | The chain service has network none. F needs a Docker secret, never the in-tree file |
| Widened write privilege | G (Draft rw) | By design | Paul's decision 3 |
| Fallback leaves mixed engines | all | `cutover.py set chain windows` returns every chain-service step; `set collect windows` returns every collect, llm and r-collect step | Intended as the one-flag fallback (Paul's decision 7) |

## 6. Rollout, smallest risk first

1. P1, P2, P3, P5, P6 and P8, none of which changes Windows behaviour. P4 once the chain lane commits `--single-reference`.
2. **A**, as soon as `chain_parity.json` passes and `chain` is container. No producer change.
3. **B** and **C1**: new step proofs, no producer change.
4. **C2**: seam fixes, the Windows hash check, then the derive-block proof.
5. **D**, once `collect` is container, P7 exists and replay is generalized.
6. **G**, after Paul's decision 3.
7. **E**, after Paul's decision 1. It carries the known handoff gap.
8. **F**, after Paul's decision 2. The replay Popen hook it waited on exists (finding 5).
9. **H**: the press walk first, after Paul's decision 4. WJ only if decision 1 gives it a proof.
10. **I**: no container path.

## 7. Paul's decisions

1. **Collectors that replay cannot prove**: the bill repository (Playwright) and Washington Journal (chromote).
   - The already-mapped bill discovery collector is the same case, and the collect gate does not cover it either (`README.md:110-111`).
   - D-180 as written requires replay.
   - Options: keep them on the host; accept a split proof (record the browser checkpoint on Windows, replay only the deterministic Python half); or set another standard.
2. **Reference layer**, two questions:
   - (a) Container transport: extend D-179 to this third CLI step, add an API key as a new secret (this changes billing; design §2.4, `:378-386`), or keep the step on the host.
   - (b) Output location: move `member_references.json` under `data/members/`, or add a score-only mode and leave aggregation to the chain's `--rebuild` (`run_pipeline.py:123`). Moving the file touches about ten readers: `audit_full`, `dashboard_integrity`, `build_dashboard_bundle`, `make_member_desk`, `signed_coalitions`, two `run_legislation_*` scripts and three `research/member_personal_relationships` modules.
3. **Draft Systems read-write**: mount it into one `chain-draft` service for the baseline fold, or keep the fold on the host. Also, should `PRESS_BASELINE_FOLD` join `FORWARD`?
4. **R image**: extend `compute-mentat` (shared with Mentat; adds `cryptography`), or build a new R collector image in CI (D-175)?
5. **Dashboard deployment**: confirm that it gets no container path.
6. **Volatile list**: may the lane add host-step entries to the list ruled today, each citing its writer line, or does each addition need Paul's review?
7. **Gate shape**: per-group evidence under the existing `chain` and `collect` flags, with no new key.
   - Confirm that one `cutover.py set chain windows`, returning every chain-service step to the host at once, is the D-188 fallback he wants.
   - Confirm that each group may switch automatically on its own evidence.
8. **Legacy wrappers**: confirm that the new container paths apply only to `refresh_tick.py`, and that `weekly_refresh`, `daily_media_refresh` and `refresh_congress_pipeline` keep these producers on the host (P2).

## 8. Found outside the 34 (for the owning lanes)

- **Interview legs would fail at import.** `fec/members/fetch_interviews.py:124` runs `open(B + r'\data\engine\congestion.json')` at module level, and builds the DB path the same way at `:48`. The eight interview legs already mapped to the collect container (`compute_engine.py:70`) would therefore fail at import on Linux. The crec-only replay gate would not catch this (`README.md:110-111`). Owner: the chain-compute lane.
- **More detector hits, not inspected by hand.** The same detectors flag `fec/legislation/refresh_billstatus_bulk.py` (one Windows literal, two stored-path reads) and `fec/legislation/refresh_legislation_discovery.py` (one implicit-EOL write).
- **Unused browser profile volume.** No collector passes `--profile`; the discovery wrapper's command (`refresh_legislation_discovery.py:245`) has none. The `collect` service's `collector-profile` volume (`collectors.compose.yaml:92`) therefore goes unused, and the discovery `.mjs` starts a fresh `/tmp` profile every run (`collect_congress_advanced.mjs:66`).
