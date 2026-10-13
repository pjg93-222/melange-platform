# Melange chain in a container (compute lane MEL)

`fec/run_pipeline.py` runs in the `compute-melange` image (CPython 3.12.10, wheels from the hashed
`ops/locks/compute-melange.linux.txt`, CP1252 locale, `TZ=America/Chicago`). CI builds it
(`.github/workflows/compute-melange.yml`, on the `v*` release tags and by hand: built only when its inputs changed, reported to `release.py` as
`compute-melange-digests`; ops/RELEASE.md "Automatic propagation"); the laptop runs it by digest. The image holds no code and no data.

```
rem optional: the image comes from the verified current release (python ops/release_lib/current.py image compute-melange)
rem unless this names a build no release has yet, e.g. the first parity runs
set IMAGE_COMPUTE_MELANGE=ghcr.io/pjg93-222/compute-melange@sha256:<digest from the workflow's digest artifact>
set MELANGE_TREE_DIR=C:\Users\pgoff\OneDrive\Desktop\Melange Systems
set MELANGE_DRAFT_DIR=C:\Users\pgoff\OneDrive\Desktop\Draft Systems
set MELANGE_DATA_DIR=C:\melange-data
set MELANGE_LOCAL_DIR=%LOCALAPPDATA%\Melange
set MELANGE_GLASSBOX_DIR=C:\Users\pgoff\.claude\glassbox\events
docker compose -f ops/compute/melange.compose.yaml --profile chain run --rm chain
```

| Host (compose variable) | Container | Mode |
|---|---|---|
| `MELANGE_TREE_DIR` | `/melange` (`MONEYBALL_ROOT`; the chain writes its artifacts and the pipeline lock here) | rw |
| `MELANGE_DRAFT_DIR` | `/draft` (`MONEYBALL_DRAFT`) | ro |
| `MELANGE_DATA_DIR` | `/melange-data` (`MELANGE_DATA_ROOT`) | ro |
| `MELANGE_LOCAL_DIR` | `/localappdata/Melange` (`MELANGE_LOCAL_ROOT`; `LOCALAPPDATA=/localappdata`) | rw |
| `MELANGE_GLASSBOX_DIR` | `/glassbox/events` (`GLASSBOX_EVENTS_DIR`) | rw |

`MELANGE_TODAY` (optional ISO date) is passed through for parity runs; unset it for production. Every process whose `today()` returns the
pinned date prints one loud `WARNING` line to stderr, so a pin left in a production environment shows in the log.
The container has `network_mode: none` (the chain is network-free), a read-only root, uid 10001, no capabilities, no secrets.
`IMAGE_COMPUTE_MELANGE` must be a digest reference; the job wrapper refuses tags (design F10). `chain_parity.py command --side container`
takes `--image`, then `IMAGE_COMPUTE_MELANGE`, then `current.py image compute-melange`; with none of the three it exits 2 and prints no command.

## Never run both chains on one tree

**Never run the Windows chain and the container chain on the same tree** (design F9). The pipeline lock is judged by
process id, and a container cannot see Windows ids (nor Windows the container's). Parity runs (design section 4) start the
two platforms from byte-identical copies of the read set, one after the other.

## Refresh engines: host or container, per phase (R-6 Option A)

`fec/weekly_refresh.py`, the daily Congress pipeline (`fec/legislation/refresh_congress_pipeline.py`) and `fec/collect/daily_media_refresh.py`
run each step either on this host (engine `windows`: `python <script>` exactly as before) or in its compute container (`container`:
`docker compose run` on the compose files here). The engine is chosen per phase by `fec/compute_engine.py`, from `ops/compute/engines.json`
(`{"chain": "windows", "collect": "windows", "office": "windows"}` is what is committed, so nothing changes until a phase is switched):

| Phase (engine key) | Moves | Compose service | Switch needs (`cutover.py`) |
|---|---|---|---|
| collect (`collect`) | the network collectors in `compute_engine.STEP_SERVICE`: CREC/CHRG, bill discovery, FEC money, rollcall, BILLSTATUS bulk, the eight interview legs, Wikipedia | `collectors.compose.yaml`: `collect`; admissions on `llm` | `report`: a collectors replay report, `result` PASS |
| chain (`chain`) | `fec/run_pipeline.py` (weekly only) | `melange.compose.yaml`: `chain` | `report`: what `chain_parity.py compare --report` writes, `result` PASS, `exit_code` 0, no failed gates |
| publish (`office`) | after `prepare dashboard deployment`: a LibreOffice view copy of the workbook in `outputs/workbook_view/`, never gating the refresh | `office-calc.compose.yaml` | `equivalence`: what `xl_equivalence.py compare --report` writes, `equivalent` true; `rank_check`: `result` PASS with `members` 533 and `rank_exact` 533 |

Switching a phase on, and back:

```
python ops/compute/cutover.py show
python ops/compute/cutover.py set chain container     # refused unless the evidence says PASS; --evidence SLOT=PATH or --evidence-dir to point at it
python ops/compute/cutover.py set chain windows       # the one-flag fallback; never refused
set MELANGE_ENGINE_CHAIN=windows                      # per-process override of the file, for one run
```

The evidence files default to `audits/compute_parity/` (`chain_parity.json`, `collectors_replay.json`, `office_equivalence.json`, `office_rank_check.json`;
`MELANGE_PARITY_DIR` moves the directory). No tool writes the collectors replay report yet; its shape is defined in `cutover.py`. The office pair is written by one run
of `xl_equivalence.py compare REF CAND --expect-formulas 98269 --rank-range 'Blended Model!T3:T535' --rank-range 'Blended Model!Y3:Y535' --report office_equivalence.json
--rank-check-report office_rank_check.json`: the rank-gated rule of D-177 as amended 2026-10-13 (Paul: tiny rounding differences are allowed in number cells only
when every rank, percentile and threshold matches exactly in the same run). In that run the 533 member ranks and every discrete cell (non-numeric values; formulas
with RANK, PERCENTRANK, TRUNC or ROUND; comparisons and IF/IFS/IFERROR/IFNA that give a flag or a label; any column holding a RANK or PERCENTRANK formula) are checked
exactly, and only if all of them match may a continuous number differ, by at most 1024 ULP (`--continuous-ulp`; 256 is the most seen, SUMPRODUCT summation order).
Otherwise the tolerance is refused, the old tier rules judge the rest, and the report says why. Both reports state the rank check and the tolerance.
Every switch, and every refusal, is a Glass Box event (`compute.cutover`) carrying the sha256 of the evidence it read. A refresh reads the file once when it
starts, so a running refresh keeps its engines; a malformed file is a pre-step failure, not a surprise at the first step.

Rules the code keeps:

* The image is the env override (`IMAGE_COMPUTE_COLLECTOR`, `IMAGE_COMPUTE_MELANGE`, `IMAGE_OFFICE_CALC`; a digest reference, never a tag), else
  `python ops/release_lib/current.py image <name>` (the verified current manifest), else the step fails closed with a message and nothing starts.
* Every producer not listed in `STEP_SERVICE` stays on the host whatever the engines say: the R collectors (no `r-compute` service yet), the steps that take the
  pipeline writer lease themselves (`update_bill_repository`, `daily_media_refresh`), `fold_promoted_press_runs`, `member_reference_layer`, the derived layers,
  `publish_dashboard`. The visible-Chrome congress.gov cookie lane is not a refresh step and stays a host tool (decided 2026-10-12). The daily Congress pipeline
  moves its collector steps only; its `run_pipeline` step stays on the host, where that process holds the lease.
* Inside a container every phase resolves to `windows` (the `-e MELANGE_ENGINE_*=windows` flags), so a container never starts a sibling container.
* `chain` = container hands the writer lease to the container: `weekly_refresh.py` removes its own lease just before `docker compose run` and takes it back when the
  container exits (a container cannot adopt a Windows-held lease: design F9). The weekly lock stays held, and failing to take the lease back is a hard abort.
* Compose variables the code can derive (the tree, Draft Systems, the data root, the Melange local folder, the Glass Box folder) are set unless already in the
  environment. Secrets are never derived: `GOVINFO_KEY_FILE_HOST` and `FEC_DATA_GOV_FILE_HOST` (collect), `CLAUDE_CREDENTIALS_FILE_HOST` (admissions) must name existing
  files, set by the job wrapper (`run_job.ps1`, lane C1, not written yet); a variable a service does not use points at an empty scratch file.
* A timed-out or interrupted step removes its container (`docker rm -f <name>`); `--rm` removes one that ended.

Not provided by this code, so check before switching `collect`: staging of `social_media.db` (507 MB, WAL: design 2.4 says every container writer stages it), starting
Docker Desktop, and the secret files above. `fec/refresh_tick.py` is not switched (unscheduled; Option A was ruled, not the tick).

## Parity run (design section 4; needs Docker and a free hour)

`chain_parity.py` proves a container run equals the Windows run from frozen, byte-identical inputs: A1 and A2 are two Windows runs
(their difference is the noise floor), B is the container run. The byte gates are `ranks_python.json`, `dashboard_data.json` and
`bipartisan.json`; every other changed path is judged by content (D-177) and rank delta must be 0 over 533 members.

```
python ops/compute/chain_parity.py freeze --src <tree> --dest <frozen> --list <read-set.txt>     # then copy <frozen> to A1, A2, B
python ops/compute/chain_parity.py verify --root <A1> --manifest <frozen>.manifest.json           # before every run
python ops/compute/chain_parity.py command --side windows   --root <A1> --today 2026-10-12 --execute
python ops/compute/chain_parity.py command --side container --root <B>  --today 2026-10-12 --execute
python ops/compute/chain_parity.py compare --frozen <frozen>.manifest.json --a1 <A1> --a2 <A2> --b <B> \
    --map C:\melange-data=/melange-data --expect-members 533 --report parity.json
```

Both sides take the same `--today` (it sets `MELANGE_TODAY`: `build_media_blend` decays by age, so two days apart differ for a
reason that is not the container's). Exit 0 is a pass, 1 a failed gate, 3 a pass that still needs Paul to accept PATH_ONLY,
EOL_ONLY or ZIP_CONTENT paths.

## Base image and interpreter

`python:3.12.10-slim-trixie` does not exist (the registry answers 404; only `3.12.10-slim-bookworm` does, and D-181 forbids bookworm
bases). D-172 needs CPython 3.12.10 exactly, so the base is `debian:trixie-slim` pinned by digest and CPython is the
python-build-standalone 3.12.10 tarball, verified with `sha256sum -c` before it is unpacked. Every pin is in `melange-base.lock`;
the Dockerfile's `ARG` defaults repeat it and `ops/tests/test_compute_melange.py` fails if they drift.

## Static checks (no Docker needed)

```
python -m pytest -q -p no:cacheprovider ops/tests                                      # the whole ops suite; the next two lines are subsets of it
python -m pytest -q ops/tests/test_compute_melange.py ops/tests/test_chain_parity.py   # lock, Dockerfile, compose, workflow, parity harness
python -m pytest -q ops/tests/test_compute_seams.py ops/tests/test_pipeline_lock_container.py   # path/text-I/O seams, host-aware lease
python ops/compute/chain_imports.py            # third-party packages the 78 chain steps reach
python ops/compute/chain_imports.py --compile  # every reached module compiles under this interpreter (CI runs it on 3.12.10 in the image)
```

## What the code does differently inside the container

* `fec/paths.py` is the single seam: `MONEYBALL_ROOT`, `MONEYBALL_DRAFT`, `MELANGE_LOCAL_ROOT` (`%LOCALAPPDATA%\Melange`),
  `MELANGE_DATA_ROOT` (`C:\melange-data`) and `MELANGE_TODAY` (one calendar day for both sides of a parity run). Every default is
  today's Windows value.
* Absolute Windows paths stored inside data (clip manifests, receipts, press delta roots) are translated where the chain reads them:
  `paths.to_host()` maps the four known roots onto their mounts and is the identity on Windows; `paths.stored_name()` takes the last
  component under either separator. An AST lint (`ops/tests/test_compute_seams.py`) fails if a chain module reads a stored-path
  key without one of them. Outputs a container run writes carry container paths, so `chain_parity.py compare --map` classes those
  files PATH_ONLY (Paul accepts them), never silently equal.
* Text files the Windows chain writes with CRLF carry an explicit `newline='\r\n'`, so Linux writes the same bytes; text reads without
  `encoding=` decode as Windows-1252 because the image runs under a generated `en_US.CP1252` locale (the Dockerfile asserts it).
* `fec/pipeline_lock.py` records a host identity (boot id, pid namespace, container id). A lease from another host or container is
  HELD unless its own heartbeat file (`.pipeline_run.lock` -> `.pipeline_run.heartbeat.lock`, a `.lock` name so the system-manifest walk
  skips it) is older than `MELANGE_LOCK_STALE_SECONDS` (600 s); Windows behaviour is unchanged.
* `paths.LOCAL_MELANGE` fails closed: with neither `MELANGE_LOCAL_ROOT` nor `LOCALAPPDATA` set, importing `paths` (and every module that
  imports the name) still works, and any USE of the value raises `paths.LocalRootUnset` -- the behaviour `champions_v2`, `contest_screen`,
  `org_mentions`, `org_stance_priors`, `entity_identity`, `sector_spend` and both `make_lobby_*_html` had when `LOCALAPPDATA` was unset.
  `STAFFER_NETWORK_LOCAL` keeps the home-directory fallback it always had. `efficiency_layers` and `coalition_pricing` build a path from it
  at import, so in such an environment they now stop at import instead of silently using a relative `Melange\...` path.
* `audit_full.py`'s `[SYS]` section maps the manifest's Windows roots onto the mounts; its A6 host-topology checks (volume letters,
  disk ids, OneDrive replica) are SKIPPED off Windows with a printed reason, never passed.

## Landing on the Windows tree

Before the landing step rebuilds anything, `python ops/compute/check_longform_inputs.py --root <live tree>` (read-only; one line of counts,
no path or byte of any input) confirms that every input the turn-features receipt records, other than the producer, still exists and
hashes to its recorded sha256. It is NOT run from this branch; the landing step runs it, and exit 0 is the gate.

Every edit to a chain module changes the sha256 receipts may pin. A search of the live tree's receipts, manifests and sources
(2026-10-12) found exactly three: `data/speech/member_longform_turn_features_v1.receipt.json` pins
`build_member_longform_turn_features.py` (its `--if-current` step sees a changed producer and rebuilds once, before the narrative
ledger reads it), `data/legislation/vote_refresh_receipt.json` lists `build_vote_category.py` (rewritten by that step on every run), and
the retired `compet_reciprocity_prereg.md` / `compet_reciprocity_look.py` restate `primacy_factors.py`'s hash as text (never compared
with the file; the look was retired without being taken). Nothing else pins a touched module.

## Not proven until Docker runs it

Nothing here was built or run in a container. The first CI build, the smoke job and a parity run (`chain_parity.py`: Windows twice,
container once, from byte-identical frozen inputs) are the first real checks. Open items the parity run will answer:

* the artifact-tool steps (`sync_stance_g`, `honors_workbook`, `sync_sentinel_bill_ledger`) belong to the D-178 lane; the honors
  binding file is keyed by the Windows workbook path, so a container lookup misses until that lane keys it root-relative
  (`paths.to_host()` is the read-side helper);
* the image carries no `git`: `system_manifest_check` SKIPS its generator-commit check and the git-commit sentinel pins when there is
  no git binary (Windows unchanged); a loose git object that was repacked since the manifest was written cannot be re-verified without
  git and reads as a changed sentinel;
* CPython 3.12.10 here bundles SQLite 3.47.1 (the host has 3.49.1) and zlib 1.3.1 (same as the host): design task C2 measures whether
  that moves any gated byte;
* bind-mount behaviour of Docker Desktop on the OneDrive tree (file ownership for uid 10001, mtimes, placeholders).

## Part B: the `fec/run_pipeline.py` hunks (landed, D-189)

Part B is in `main` as 70f3e5b0c (D-189; Paul, in chat 2026-10-12). The container-chain branch had left `fec/run_pipeline.py` at main's bytes
because the live tree held another lane's uncommitted work in it (a step-failure diagnostics feature, a CRLF working copy); the D-189
ruling landed the four hunks the container chain needs alongside that work. They went in as a commit written with plumbing
(`commit-tree`/`update-ref`), so the live index and working copy of the other lane's file were not rewritten:

1. `from paths import LOCAL_MELANGE`;
2. `newline='\r\n'` on the two text-mode writes in `_record_tolerated` and `_prune_tolerated` (`data/engine/chain_tolerated_failures.json`,
   an indented JSON list the runner touches only when that file already exists or a step is tolerated: without the hunk a Linux run
   writes LF where the Windows run writes CRLF, an EOL_ONLY file in any parity comparison that records or prunes a tolerated failure);
3. `_DEMO_DATA_ROOT` defaults through `os.path.join(LOCAL_MELANGE, 'demographics')`;
4. the staffer-network `OPTIONAL_INPUTS` generation path goes through `LOCAL_MELANGE` the same way.

Hunks 1, 3 and 4 move no path on Windows (`LOCAL_MELANGE` is today's `%LOCALAPPDATA%\Melange`); in the container the compose file sets both
`MELANGE_LOCAL_ROOT` and `LOCALAPPDATA`, so they resolve the same without Part B and only hunk 2 can change bytes. The same commit removed
`PART_B_PENDING` from `ops/tests/test_compute_seams.py` (the exemption, its two lint unions and its ratchet test): `run_pipeline.py` now
answers to the Windows-literal and implicit-EOL lints like every other chain module. A parity comparison of the container chain needs a
tree at or after 70f3e5b0c, which has Part B.

## Reviewed and left alone

* MINOR 1: the pre-existing `\r\r\n` risk in `fec/surfaces/build_clean_workspace.py` (around line 372, where text read with its CRLFs
  intact meets writes that translate `\n`). A text-mode write on Windows translates `\n` the same way `newline='\r\n'` does on Linux, so
  the two platforms agree byte for byte and parity holds; changing it would change output bytes, so it is its own change, not this lane's.
* MINOR 5: the effectiveness-shadow manual tool (`run_pipeline.py --effectiveness-shadow`, i.e. `fec/legislation/run_effectiveness_shadow.py`
  and the shadow writers in `fec/engine/effectiveness_integration.py`). It is not a chain step; its writes take `newline='\r\n'` like the
  rest, and nothing else about it is changed here.
