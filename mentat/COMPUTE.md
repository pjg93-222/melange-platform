# Mentat compute jobs in containers

Design: `audits/deploy_20261011/COMPUTE_CONTAINERS_DESIGN.md` in the Melange repository (section 2.2). Rulings D-173..D-182.
Status 2026-10-12: built and tested without Docker. Nothing here has run in a container, no scheduled task was changed, and the
image has no digest yet (CI has not built it). The Windows jobs are still the reference and still run.

## What is covered

| Job | Replaces | Network | FEC keys | Cap | Service |
|---|---|---|---|---|---|
| `anchors` | task MentatNightlyAnchors (03:30) | none | no | 45 min | `anchors` |
| `refit-detect` | task MentatRefitWatcher (04:00) | none | no | 20 min | `refit-detect` |
| `refit-parity` | `Rscript run_refit.R --mode parity` (manual) | none | no | 240 min | `refit-parity` |
| `refit` | `Rscript run_refit.R --mode refit --publish never` (manual) | none | no | 480 min | `refit` |
| `pocket-build` | the pocket page build of the console publish | none | no | 10 min | `pocket-build` |
| `collect-fanout` | the collect lane of the industry report (`api_company_cycle_fanout.py`) | `collect` | yes | 1440 min | `collect-fanout` |
| `pac-evidence` | `tools/pac_evidence.py` (console) | `collect` | yes | 120 min | `pac-evidence` |

Not covered, on purpose: the industry report build (PowerShell 5.1 orchestrators, the Excel finalizer, `@oai/artifact-tool`; design
tasks M2-c and M2-d), the console prewarm, the figure PNG export and the analytics index (console-side), and the manual ingest and
model-freeze tools. The only PowerShell this lane needs is the Windows wrapper `tools/run_job.ps1`; the image has no PowerShell.

`anchors` inputs: the container requires Melange's `sector_spend.csv` and `fullscope_receipts.json` (`%LOCALAPPDATA%\Melange\lda_rebuild\derived\sector_spend\` and `...\derived\`) to exist, because
they are bind mounts with `create_host_path: false`. A missing file fails the container; the Windows `build_lda` wrote "unavailable" and carried on.

## Files

| File | Role |
|---|---|
| `tools/compute_job.py` | Container entry point: `run <job>`, `verify`, `event`, `jobs`. A job is complete only when its own check passes; the receipt `<LOCALAPPDATA>/Mentat/compute/receipts/<run id>.json` holds the output hashes and key fingerprints, never a key. Runs natively on Windows too. |
| `tools/compute_secrets.py` | Windows side of the key handoff: DPAPI store to one owner-only per-run file (`stage`), overwritten and deleted (`wipe`), stale folders removed (`sweep`). |
| `tools/run_job.ps1` | Windows wrapper (PowerShell 5.1): lock, start Docker, digest-only image, clock check, stage keys, `docker compose run --rm`, verify the receipt, wipe, Glass Box events, logs under `%LOCALAPPDATA%\Mentat\logs\compute`. |
| `compute.compose.yaml` | One hardened service per job: read-only root, uid 10001, `cap_drop ALL`, limits, `/mentat` read-only, a writable mount only where the job writes, network only for the two FEC collectors, keys as a Docker secret file. |
| `compute.images.lock` | The image by digest. `digest` is `null` until CI publishes one; the wrapper refuses a tag or `null`. |
| `tools/mentat_env.py` | The two tool locations with a Windows default (`MENTAT_RSCRIPT`, `MENTAT_R_LIBRARY`). Every other location already had its own variable. |
| `tools/compute_parity.py` | Content-level parity (below). |

Environment seams (a Windows run sets none of them): `MENTAT_RSCRIPT`, `MENTAT_R_LIBRARY`, `LOCALAPPDATA`, `MENTAT_REPORT_DIRECTORY`,
`MENTAT_DATA_ROOT`, `MENTAT_MELANGE_ROOT`, `RF_RUNTIME`, `RF_INPUT_ROOT` / `RF_ROOT_<ALIAS>`, `REFIT_OUT`, `RF_REPORT_DIR`,
`RF_PUBLISH_DIR`, `GLASSBOX_EVENTS_DIR`, `ANCHORS_GLASSBOX_DIR`, `RF_GLASSBOX_DIR`, `FEC_API_KEYS_FILE`. Defaults are today's
values; `tests/test_compute_seams.py`, `tests/Test-MentatPathSeams.ps1`, `tests/test_container_paths.R` and
`forecast/refit/tests/testthat/test-24-container-paths.R` pin them.

## Running one job (after Docker and an image digest exist)

```powershell
# a console; the digest comes from compute.images.lock
& C:\Users\pgoff\Mentat\mentat\tools\run_job.ps1 -Job anchors
& C:\Users\pgoff\Mentat\mentat\tools\run_job.ps1 -Job pac-evidence -JobArgs '--output','C:\...\evidence','--committees','C00...','--company','X'
& C:\Users\pgoff\Mentat\mentat\tools\run_job.ps1 -Job anchors -PlanOnly      # prints the compose command and host variables, runs nothing
```

Exit codes: 0 verified; 1 the job failed or its receipt did not verify; 2 refused before running (unknown job, no image digest,
bad run id); 3 Docker or its clock; 75 another run holds the lock (or the Windows task is running); 124 timed out.

## Replacing MentatNightlyAnchors and MentatRefitWatcher (documented, not performed)

Preconditions: Docker Desktop installed (design P0); a CI-built `compute-mentat` digest recorded in `compute.images.lock` in a
reviewed commit; three consecutive passing parity runs on three different input snapshots; Paul's explicit go. The two Windows tasks
are the reference until then and stay exactly as they are (anchors: `run_logged.ps1 -Name MentatNightlyAnchors -TimeoutMinutes 45`,
daily 03:30, 1 h limit; refit watcher: `-TimeoutMinutes 20`, daily 04:00, 30 min limit).

1. Disable the Windows tasks first (one writer per tree; the pipeline lock is PID-based and blind across the VM):
   `Disable-ScheduledTask -TaskName MentatNightlyAnchors; Disable-ScheduledTask -TaskName MentatRefitWatcher`
2. Register the container tasks with the same triggers and run-when-missed behaviour. Limits are the wrapper's backstop (cap + 5 min)
   plus Docker start (up to 4 min), the image pull and the clock check, so 90 and 45 minutes:

```powershell
$run = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\pgoff\Mentat\mentat\tools\run_job.ps1" -Job '
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$tasks = @(
  @{ Name = 'MentatNightlyAnchorsContainer'; Job = 'anchors';      At = '3:30am'; Minutes = 90; OnBattery = $false },
  @{ Name = 'MentatRefitWatcherContainer';   Job = 'refit-detect'; At = '4:00am'; Minutes = 45; OnBattery = $true })
foreach ($t in $tasks) {
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes $t.Minutes)
  if ($t.OnBattery) { $settings.DisallowStartIfOnBatteries = $false }       # as the refit watcher is today
  Register-ScheduledTask -TaskName $t.Name -Principal $principal -Settings $settings -Trigger (New-ScheduledTaskTrigger -Daily -At $t.At) `
    -Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ($run + $t.Job) -WorkingDirectory 'C:\Users\pgoff\Mentat')
}
```

3. Run each once by hand (`Start-ScheduledTask`) and read `%LOCALAPPDATA%\Mentat\logs\compute\<job>.log` and the Glass Box events.
4. Rollback at any time: `Disable-ScheduledTask` the two container tasks and `Enable-ScheduledTask` the originals. The originals are
   deleted only after one reverse check (the next container run repeated on Windows from its snapshot) and Paul's word.

## Parity (D-177: content, not bytes)

Bytes are the wrong test for workbooks, SQLite and anything with a stamp, so `tools/compute_parity.py` compares what a file holds:
JSON by leaf, CSV by cell, SQLite per table (schema, rows, a digest per column in primary-key order), workbooks per cell (formula
and cached value), zips per decompressed member, text with line endings normalised. Classes: `BYTE_EQUAL`, `<KIND>_CANONICAL`,
`EOL_ONLY`, `PATH_ONLY` (differs only by a declared mount prefix), `DIFFERENT`. The run fails on any `DIFFERENT` file or a file on one side only.
Two writers state the Windows bytes explicitly, so their files are `BYTE_EQUAL` across platforms and not `EOL_ONLY`: `rf_write_csv` (refit-detect's
`detect.csv`, the refit's review and panel CSVs) and `tools/pocket/build_page.py` (the pocket page) write CRLF on every platform.

```powershell
# 1. noise floor: the Windows job twice on the same frozen inputs; only what differs between the two is masked
python tools\compute_parity.py variance A1 A2 --out mask.json --ignore "*.log"
# 2. the container run to scratch (the wrapper takes the output folder from the caller), same inputs, never in parallel with A
$env:ANCHORS_OUT_HOST = 'D:\parity\B'; .\tools\run_job.ps1 -Job anchors
# 3. A against B; the mask is refused unless it came from this A
python tools\compute_parity.py compare A1 D:\parity\B --mask mask.json --map C:/Users/pgoff/Mentat=/mentat --report report.json
```

Frozen inputs, one heavy job at a time, and the cut-over criterion (three passing runs, Paul accepts the explanation report) are the
design's section 4. Anchors and the refit `detect` mode are the first candidates; `refit parity` already ran once on a Linux pod.

## Collector parity (D-180)

The two collectors (`collect-fanout`, `pac-evidence`) read the live FEC API, which answers differently at different moments (F7), so two
live runs prove nothing. The layer records the web traffic of one Windows run (R0) and feeds the same answers to every later run of the
unchanged job. It is wrapper, environment and proxy only: `R/collect.R`, `R/schema.R`, `inputs/fec_receipt_rules.json`,
`tools/pac_evidence.py`, `tools/cross_reference_engine.py`, the money logic in `app/flow_data.py` and `METHODOLOGY.md` are not touched, and
neither are production `run_job.ps1` and `compute.compose.yaml` (a test pins that they never mention the replay file or a proxy variable).

How: the clients already honour `HTTPS_PROXY` / `NO_PROXY` / `SSL_CERT_FILE` (R on Windows also needs `CURL_SSL_BACKEND=openssl` +
`CURL_CA_BUNDLE`; `http_replay.py` sets all of them), so a loopback forward proxy serves the cassette. Melange's `ops/compute/http_replay.py`
is used byte for byte (its sha256 is in every replay report). Only `api.open.fec.gov` is fetched; the key travels in `X-Api-Key`, which the
cassette stores as `REDACTED`, so a replay with a dummy key matches the same cassette entry.

| File | Role |
|---|---|
| `tools/replay_proxy.py` | `mint` (host: CA + leaf for api.open.fec.gov, CA key never written), `record` (stock `http_replay` classes, proxy timeout 150 s because the request pool waits 120 s, `--secret-file`), `replay` (stdlib stand-in for the CA class, because the image has no `cryptography`; `--allow-unused`). Writes `REPLAY_REPORT` JSON: http_replay sha256, cassette digest, served, misses, unused, served per request key, refused TLS hosts. Exit: the child's if nonzero, else 3 on a miss. |
| `tools/replay_parity.py` | `snapshot`, `record`, `replay --side` A1, A2 or B, `judge`, `status`, `--plan` (prints the commands and variable names, runs nothing). |
| `compute.replay.yaml` | The second `-f` file, container side B only: network none, networks reset, cassette / tls / `http_replay.py` read-only and never created by Docker, the dummy keys secret (`REPLAY_KEYS_FILE`), `http_replay.py` mounted at `/melange-replay/ops/compute/` (three levels deep: it takes `parents[2]` of its own path), `collect-fanout` gets a scratch `.cache`. |

Root `%LOCALAPPDATA%\Mentat\replay\<job>\<snapshot>\` (`MENTAT_REPLAY_ROOT` overrides; owner-only like the key folders): `snapshot.json`,
`cassette\`, `tls\`, `inputs\`, `keys\` (the dummy keys), `reports\`, and `runs\R0|A1|A2|B` (each run's work folder, moved there after it ends; every
run uses the same `<snapshot>\work` path so path-valued fields agree). Outputs and receipts go only to scratch, never the live tree.

```powershell
$env:MELANGE_HTTP_REPLAY = '<path to Melange ops\compute\http_replay.py>'      # default: the live Melange tree, or /melange-replay/... in the container
python tools\replay_parity.py snapshot --job pac-evidence --snapshot S1 -- --committees C00000059 --cycles 2024 --company "Hallmark Cards" --max-units 1 --output {work}/evidence
python tools\replay_parity.py record  --job pac-evidence --snapshot S1       # R0, live: real key quota
python tools\replay_parity.py replay  --job pac-evidence --snapshot S1 --side A1
python tools\replay_parity.py replay  --job pac-evidence --snapshot S1 --side A2
python tools\replay_parity.py replay  --job pac-evidence --snapshot S1 --side B     # the container; needs a digest in compute.images.lock
python tools\replay_parity.py judge   --job pac-evidence --snapshot S1
python tools\replay_parity.py status  --job pac-evidence
```

Record safety: `record` refuses while an `api_company_cycle_fanout.py` is running (shared key quota), stages the keys with `compute_secrets.py`
and wipes them in `finally`, and afterwards rescans the cassette for every key in the store, disabled ones included; a cassette holding any is
marked dirty and no replay will use it. Replays use dummy keys (`replay-dummy-0001`, ... as many as the recording had). `collect-fanout`
replays pin `--budget-database` (in scratch) and `--initial-workers / --max-parallel / --max-lane-ceiling / --pool-width / --recovery-workers W
--memory-per-lane-mb 640 --concurrency-per-key 8` so two runs schedule alike.

The judge (`reports\judge.json`, one gate per line): A1 against A2 builds the noise mask (`compute_parity.variance`). A masked place whose
column or leaf name says money (`amount|total|receipt|disburs|expend|cash|count|sum|cents|rank|percentil|threshold`) is allowed only if A1 and A2
hold the same values there, as a multiset. Never masked: per SQLite / CSV table the row count, the exact Decimal sum of each protected column and
an order-independent multiset digest of the protected projection; per JSON protected leaf the same, by index-free path. They must be equal across
A1, A2 and B, and R0 against A1. Also required: zero misses and refused hosts everywhere, equal served counts per request across A1, A2 and B,
the cassette and `http_replay.py` unchanged, every run on the same Mentat commit and image digest, `compute_job.py verify` passing on every
receipt, and (with B) `compute_parity compare` of A1 against B with the work folder mapped to the container mount. `.csv.gz` / `.jsonl.gz` outputs
are read for the protected groups; any other opaque file that differs between A1 and A2 fails the guard.

The streak (`status`) counts consecutive passes on distinct snapshots with the same image digest and Mentat commit and a B run; a failure or a
code change resets it. Three are the cut-over criterion above. Limits: a client that ignores the proxy variables would go live during a Windows
replay (the container has no network, so B cannot); the cassette digest trusts body file names (content-addressed); a chunked request body and
HTTP/2 are not supported by `http_replay.py`; on Python 3.13 hosts record needs a CA with key identifiers (`mint` adds them; the stock throwaway
CA used by `record` does not).

## Blocked

- Docker Desktop and WSL2 are not installed (design P0, an admin action); no compose service has started. The compose file is
  validated with `docker compose config` and the wrapper is exercised against a fake `docker`.
- `compute-mentat` is not built: `compute-images.yml` and `ops/docker/compute-mentat.Dockerfile` are on the Melange
  repository's `compute/mentat-image` branch (exported to the platform repository at release). Until a digest is in `compute.images.lock` the wrapper stops at exit 2.
- Unverified until Docker exists: the `hwclock -s` resync of the VM clock, bind mounts of OneDrive folders (they must be "Always keep
  on this device"), SQLite reads over the VM share (the anchors job reads the analytics indexes read-only), and the end-to-end R
  refit (`MENTAT_COMPUTE_REFIT_RLIB` enables that test against a native Rscript).
- The 15 R packages whose versions the PPM snapshot cannot match to the lock (listed in the Dockerfile) are Paul's to ratify; the
  refit's `sandwich` is one of them, and the refit's own parity mode is how a difference would show.
