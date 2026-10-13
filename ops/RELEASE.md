# Releasing Melange, Mentat and Terrier

One command on the laptop turns the current state of all three systems into a release and pushes it to GitHub,
the VPS, the Netlify desk and Mentat Pocket. The laptop keeps all compute and the signing key; the VPS only
verifies and serves.

```
python ops/release.py plan   [--data-only] [--no-netlify] [--skip-kernel] [--governance-override] [--census-override]
python ops/release.py run    [--data-only] [--no-netlify] [--skip-kernel] [--governance-override] [--census-override]
                             [--confirm-outward git,data,manifest,netlify,undraft]
python ops/release.py resume <id> [same flags]
python ops/release.py images [--id <CalVer>] [--confirm-outward git,geo]    # code only: CI builds the three images, nothing else
python ops/release.py images --init-remote --confirm-outward repo         # create the private platform repo, set the bare origin
python ops/release.py auto   [--plan] [--force-retry] [--pause | --unpause]   # the registered task runs this; see "Automatic propagation"
                             [--confirm-outward git,geo,data,manifest,netlify,undraft]
python ops/release.py current [--use <id>]                                 # the manifest compute jobs resolve their images through
python ops/release.py status [<id>]
python ops/release.py verify <manifest.json> [--allowed-signers P] [--current ID] [--snapshot P]
python ops/release.py init-signing          # prints the key commands; generates nothing
python ops/release.py store-netlify-token    # prompts (hidden) for the Netlify token, stores it DPAPI-encrypted
```

Python 3.12, standard library only. Settings live in `ops/release_config.json` (non-secret); what ships lives in
`ops/serve_sets.json`.

## plan

A dry run. It evaluates every gate, lists what the export would contain versus the current platform `main`
(added, changed, removed) and everything it would drop and why, summarises the serve sets, and reports which
outward steps are configured. The only thing it writes is `plan-<utc>.json` in
`%LOCALAPPDATA%\Melange\releases\`. It does not build the export, hash data, or touch the network. The security
gate runs against the exported tree during `run` (it needs the tree materialised).

## run

Release ids are CalVer `YYYY.MM.DD.N`; the git tag is `v<id>`; ordering is numeric on the four parts.
Order of work:

1. **Gates** (all must pass, none is skipped silently):
   no `.pipeline_run.lock` holder alive; `.weekly_refresh.lock` free (probed, never held); no Mentat lane writer;
   the allowlisted paths clean in all three repos and each HEAD equal to its released ref; `fec/tests/run_tests.py`
   ends in `N passed, 0 FAILED` (`--skip-kernel` skips it and the manifest records that); the last weekly refresh
   is COMPLETE (the gate's detail lists any audit failures it tolerated, and they are recorded in the manifest);
   with Netlify in the release, `dashboard_deployment_receipt.json` matches `deploy/index.html`; free disk >= need + 10 GB;
   **the invited build** (D-164, D-169): `outputs/invited_build/INVITED_BUILD.json` says `governance: "ruled"`
   (`--governance-override` is for Paul-only testing and is recorded as `governance_override`), every shipped
   `Moneyball.html` / `lobby-*.html` and `.gz` hashes to what it records with no unlisted page beside them, and each
   recorded input is still the live page (rebuild it otherwise); **the census** (`serve_sets.json` `census_pending`
   is false, or `--census-override`, recorded as `census_override`).
2. **Export** (full releases): the platform tree is built from git objects only, never a checkout, so autocrlf and
   eol attributes cannot touch a byte. Allowed: `fec/` (minus `_quarantine_*`, `attic`, `research`,
   `pas224.zip`), `requirements.txt`, `ops/` (`ops/github/**` becomes `.github/**`, `ops/export.gitattributes`
   becomes `.gitattributes`), and Mentat `mentat/` from `codex/mentat-crypto` (minus `inputs` except `fec_receipt_rules.json` and
   `fec_transaction_types.json`, `state`, `audits`, `.cache`, and the memo-redisclosure fixture); `fec/docs/` is also left
   out; code extensions only (no `.tsv`), each file <= 5 MB. The new commit's tree is proved equal to the expected
   (mode, blob, path) set, then materialised and scanned by `python ops/security_gate.py export --target <dir>`
   (absent or failing = stop). Only then do `refs/heads/main` and the lightweight tag move in the bare repo
   `C:\Users\pgoff\PlatformRelease\melange-platform.git`. Every git call runs with `core.hooksPath=` (no repo hook executes). The commit date is fixed per release, so a retry
   reproduces the same commit id.
3. **Pocket and reader downloads** (local): Mentat's own builder is gated by its `selfcheck.py` and locked with
   `lock_dashboard()` using the dashboard password file (`DASHBOARD_PASSWORD_FILE`; never printed). Third parties get
   only the **invited** desk (D-171, R3): `deploy/index.html` is pinned into the release dir only if it is a lock
   envelope, `dashboard_deployment_receipt.json` matches it, and the receipt's source is
   `outputs/invited_build/Moneyball.html` as `INVITED_BUILD.json` describes it (lock that file with
   `publish_dashboard.py --source outputs/invited_build/Moneyball.html`). Otherwise the desk is left out of the
   downloads, Netlify is skipped with a message, and nothing falls back to the full desk. The pinned copy, the Pocket and
   `version.json` are staged under `releases\<id>\netlify\deploy\`. Downloads
   `downloads/<id>/` hold that locked desk, the locked Pocket, optional
   workbooks (`downloads.workbooks`, default none) and `SHA256SUMS`; the same files go again under `downloads/latest/`
   without the id in their names (`melange-desk.html`), because `desk_server` serves `/downloads/<id|latest>/<name>`
   from the desk root (`melange/` in the snapshot) and a snapshot holds no symlinks. The email package is never an input.
   The lock check is `manifest.is_lock_envelope`, which also requires the security gate's own `is_locked_envelope`
   (AES-GCM page, wrapper under 64 KiB, incompressible ciphertext). Before anything is captured,
   `security_gate.py packages` scans the package dir, the Netlify staging dir and the downloads dir.
   The lock check is `manifest.is_lock_envelope`, which also requires the security gate's own `is_locked_envelope`
   (AES-GCM page, wrapper under 64 KiB, incompressible ciphertext). Before anything is captured,
   `security_gate.py packages` scans the package dir, the Netlify staging dir and the downloads dir.
4. **Capture** (local, under the pipeline writer lease): every file in the serve sets gets a sha256 (cached by
   path, size, mtime in `releases\hash_cache.sqlite`). The files are exposed to restic through a tree of NTFS hard
   links at `releases\staging\<id>\farm\<system>\<rel>`, so no space is used and the layout equals the manifest
   paths. SQLite files in WAL mode (or with `-wal`/`-shm`/`-journal` beside them) are the only copies, made with
   `sqlite3.backup()` with the source mtime preserved. Output: `snapshot_manifest.json`.
   Anything matching the secret denylist (`keys.json`, `*key*.txt`, `dashboard_password*`, `.secret`, `*salt*`,
   `.demo-key`, `*.dpapi`, `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `*.kdbx`, `.netrc`,
   `*credential*`, `*token*`) makes the release refuse; contacts and account stores, `luna-v1`, `rowfw`,
   `backups` and `*.tar.gz` are never shipped. The farm then passes `security_gate.py farm` (exact secret values and
   secret-named files; not `data_leak`, since data files belong there) before the first git push. `snapshot_manifest.json`
   carries INVITED_BUILD.json's own `governance` and the profile's sha256 (the VPS updater accepts `ruled`, and `pending` only when the signed manifest's `invited_build` gate records `governance_override`).
5. **Outward steps** (each needs its config AND `--confirm-outward`):
   `git push origin main refs/tags/v<id>` for the platform repo and `git push` + tag for Geo (never `--force`; a tag that
   already points elsewhere, locally or on the remote, is a hard stop); `restic backup`; wait for both repos' CI
   (`gh run watch`) and read `digests.json` and asset hashes from the draft releases; build and sign
   `manifest.json`; upload to the VPS inbox in this order: `snapshot_manifest.json`, `manifest.json.sig`,
   `manifest.json` last (each appears atomically via `.part` + rename); Netlify draft, download-and-compare,
   publish, re-verify production (retried with backoff; no redirect is ever followed with a credential); publish the draft
   GitHub releases; clean the staging farm. Signing needs `signing.allowed_signers` to exist: the fresh signature is
   verified against it before the manifest is pushed.
   **What the digests are trusted for.** Before it signs, the command asks `gh api` that each repo's `digests.json`
   names the exported commit and the tag, that a successful push run of that tag has `head_sha` equal to that commit
   (the platform repo's export commit; Geo's released commit), and that the draft release was made by `github-actions[bot]` and
   every one of its assets was uploaded by it inside that run's time window (the assets' `created_at`: a release's own
   `created_at` is the date of the tagged commit, which is always before the run). A hand-made release, a swapped asset, a run for another
   commit, or anything `gh` cannot show is a `PROVENANCE` stop. This does **not** cover a compromised CI run: the runner,
   a third-party action, or a workflow change that reached the tagged commit would still produce a release that passes.
   Mitigations are the pinned action SHAs, the tagged workflow being the one in the exported (reviewed) tree, and the
   security gate over the images; treat CI as trusted, not as proven.
6. Without `--confirm-outward` naming the step (`--confirm-outward git,data,manifest,netlify,undraft`; every step the run
   reaches must be listed), or with config missing, the run stops cleanly at that outward step. Exit
   codes: 0 done, 1 failed, 2 usage, 3 pending confirmation, 4 not configured.

`--data-only` skips the export, git and CI: it carries the previous release's images and platform commit and
ships fresh data plus the desk and Pocket.

## resume

`resume <id>` is `run` continuing that id. There is no step machine: every step checks its external fact first
(tag on the remote, restic snapshot tagged `release=<id>`, VPS `status.json`, signed manifest on disk) and skips
if it is already true. State in `releases\<id>\state.json` holds only fixed inputs (commit date, previous commit,
ids) so a retry reproduces identical results.

## images

`images` builds the three app images (melange-desk, mentat-console, terrier-web) on GitHub and nothing else: no data
snapshot, no VPS, no Netlify, no manifest, no signing, and the draft releases stay drafts. Use it to get image digests
before a data release is possible. It is code only: images are built by CI from committed objects, so no data leaves the
laptop, and the gates that guard data (clean live tree, kernel tests, weekly refresh, invited build, census, locks, disk,
package and farm scans) are **not evaluated**. `state.json` records that under `code_only`, with the list of gates it
skipped and why. What does apply: the same byte-faithful export as `run` (main + `codex/mentat-crypto` + `ops`), which must
pass `security_gate.py export` (fail closed; gitleaks is required), and the tag rule: `v<id>` on another commit, locally
or on either remote, is a hard stop. A failed gate moves no ref and pushes nothing.

Without `--confirm-outward` the command exports and gates locally, then stops (exit 3). Each outward step is named:

- `git`: push the bare platform repo's `main` and `v<id>` to `platform_remote_url`.
- `geo`: push `terrier_images_ref` (default `deploy/terrier-hosted`) in `terrier_checkout` to Geo's `origin` under the
  same branch name, plus `v<id>` on that commit, so Geo's own workflow builds terrier-web. `origin` must be
  `terrier_remote_url`; the branch can never be `terrier_ref` (`claude/walk-list`). Never `--force`.

Then it waits on `gh run watch` for the tag's run in both repos (the first run and the draft release are polled once a
minute, up to 15 minutes; one watch is capped at 45 minutes), reads `digests.json` from each draft release (same
provenance check as `run`), and prints `ghcr.io/...@sha256:...` for each image plus the run URLs. A red run exits 1 and
prints the last 40 lines of `gh run view --log-failed` with token-shaped text masked. `--id` reuses or fixes the CalVer id;
rerunning an id skips whatever is already done.

`images --init-remote --confirm-outward repo` is the one-time setup: it runs `gh repo view` and, only if the repo is
missing, `gh repo create pjg93-222/melange-platform --private --disable-wiki`, then sets the bare repo's `origin` to
`platform_remote_url` (it refuses a bare repo whose origin is somewhere else). Safe to repeat.

## status

`status [<id>]` prints one row per surface: GitHub release (tag, assets, not a draft), VPS `status.json` over
sftp, `/version` of melange, mentat, co05, or05 and ct01 (CF-Access service-token headers from the `svc-status`
credential), the Netlify desk (version and served sha256), Pocket, and launcher drift. A row with missing
configuration says `not configured`. It exits non-zero unless every row is `ok`.

## verify

`verify` checks the SSH signature (`ssh-keygen -Y verify`, namespace `melange-release`) against
`ops/vps/allowed_signers`, validates the structure (every image pinned by digest, previous older than the id),
optionally binds `snapshot_manifest.json` to the signed hash, and refuses a downgrade; an equal id is a no-op.

## Automatic propagation

D-183: any change that is made updates everywhere else, without anyone running a release. `python ops/release.py auto` is one
idempotent pass over what changed; a Task Scheduler task runs it every 15 minutes and at logon. It reuses the steps above
(export, security gate, push, CI wait, digests, manifest signing, VPS push, Netlify, status) and adds no new way out of the laptop.

### What a pass does

1. **Pause, lock.** `auto.pause` present: say so and stop. One pass at a time (`auto.lock`); a second one exits quietly.
2. **Heads.** Melange `melange_ref` (main), Mentat `mentat_ref`, Terrier `terrier_images_ref` (today `deploy/terrier-hosted`).
   They are compared with the heads last propagated (`auto_state.json`; before the first pass, the commits the newest signed
   manifest was built from). Nothing differs: no code release (the cost is three `git rev-parse`), and the pass goes on to the data check.
3. **Decision**, printed as `auto: <action>: <why>`: `idle` (no change), `wait` (the newest changed commit is younger than
   `auto.quiet_minutes`, default 20: nobody gets a release per commit while work is going on), `capped` (`auto.max_per_day`,
   default 6 releases in 24 h), `failed` (these exact heads already failed; a new commit or `--force-retry` is needed), `go`.
4. **Code release** (`go`). Before an id, a tag or a push is spent, the pass checks that the manifest can be signed unattended:
   the release key must be loaded in the Windows OpenSSH agent (`ssh-add`). If not, it alerts and tries again next pass.
   Then: export + security gate. If the exported tree equals what was last propagated and Geo did not move, the commits only
   touched things that are not released: they are marked propagated with no release at all. Otherwise a `code-only` release
   (the manifest `kind`): push the platform repo (and Geo when its branch moved), wait for CI, collect every image digest, sign,
   publish. Data is carried from the previous release unchanged (its snapshot, its `snapshot_manifest.json`, its invited-build gate).
5. **Only images whose inputs changed are built.** `ops/image_inputs.json` + `ops/release_lib/inputs.py` give each image an
   input hash (its Dockerfile, every path its `COPY`/`ADD` lines name, `ops/images.lock`, and any `extra` paths, by git blob id).
   CI looks for the tag `inputs-<hash>` in GHCR (`ops/ci/build_or_reuse.sh check`): found = reuse that digest, nothing is rebuilt
   (tests, SBOM and the security scan still run on it); missing = build, then tag the new digest `inputs-<hash>`
   (`build_or_reuse.sh tag`). Any registry answer other than "found" or "not found" fails the step. A reused image keeps the
   commit it was built from in the manifest (`image_commits`), so `status` does not call it stale. Geo unchanged: its images stand as signed.
6. **One manifest for every image.** `melange-platform-release-v1` gains an `images` map `{name: repo@sha256:...}` (all three app
   images, plus Terrier's build images and the compute images) and `image_commits`. The `systems` block is unchanged, so the VPS
   updater, which reads only `systems`, takes a `code-only` manifest as a code-only release (same snapshot, no data copy).
   Compute images come from workflows other than `release.yml` (see "Compute workflows adopt it" below).
7. **Publish**, in this order, each step verified before the next: (i) the laptop's `current\` manifest + signature
   (`%LOCALAPPDATA%\Melange\releases\current\`, written atomically after the signature checks out against `ops/vps/allowed_signers`);
   (ii) the VPS inbox when `domain`, `hosts.ssh`, `vps.ssh_user` and `credentials.svc_push` are set (else skipped with a note);
   (iii) the draft GitHub releases are un-drafted. Every step is a Glass Box event (`release.step`, `release.run`, `release.auto`).
8. **Data.** A weekly refresh that finished `COMPLETE`, or a changed invited build, since the last data release starts the
   existing `--data-only` path with all its gates (governance ruled, census, kernel tests, locks, disk). A refusal is a note and the
   next pass looks again. The Netlify desk is part of it only when the invited build changed, governance is ruled and Netlify is
   configured (D-171); otherwise the pass says why it skipped. The first pass records what exists as the baseline and releases nothing.
9. **Local consoles.** After a code release that changed Mentat, the pass asks Mentat's own launcher (`Start-MentatApp.ps1
   -NoBrowser -Port N -DataRoot <the console's>`) to replace an **idle** console that is running older code (D-029): a busy console is
   left alone and asked again next pass; a read-only preview or a console of another checkout is never touched.
   `auto.mentat_restart` switches this off. For Melange (8841) and the Terrier local servers it only emits a Glass Box
   `release.restart_needed` event.
10. **Failure.** Nothing is propagated beyond what was already verified. The pass records the heads that failed (no retry on them
    until a new commit or `--force-retry`), emits one Glass Box `release.auto` `failed` event and, when `auto.healthchecks_url` is
    set, pings `<url>/fail` once per distinct problem. A clean pass pings `<url>`; while a failure is unresolved it does not, so the
    check goes late and stays red. A pass that lacks something that is not there yet (the signing key in the agent, the VPS settings)
    is "blocked", not failed: it tries again next time.

`--confirm-outward` is the same list `images` and `run` take (`git,geo,data,manifest,netlify,undraft`); the registered task passes
all six. A step left out stops the release there (exit 3) and it resumes on the next pass that allows it.
`auto --plan` prints the decision, whether signing is ready and whether data changed, and writes nothing.

### Paul's controls

* **Look first, then run:** `python ops/release.py auto --plan`, then `python ops/release.py auto --confirm-outward git,geo,data,manifest,netlify,undraft`.
* **Pause / resume:** `python ops/release.py auto --pause` writes `auto.pause`; `--unpause` removes it. The task keeps running and does nothing.
* **Register / remove the task:** `ops\auto\Register-AutoRelease.ps1` (`-WhatIf` first; nothing runs it for you) and `ops\auto\Unregister-AutoRelease.ps1`.
  The task runs as you, run level Limited, one instance at a time, logging to `%LOCALAPPDATA%\Melange\releases\auto.log`.
* **Config** (`ops/release_config.json`, block `auto`): `quiet_minutes`, `max_per_day`, `mentat_restart`, `mentat_port` (first of ten
  ports scanned), `healthchecks_url` (null = no pings).
* **Roll back:** (1) `python ops/release.py auto --pause`, so the next change does not replace what you are about to do;
  (2) the VPS: release the old code again under a new id, or the manual steps in `ops/RUNBOOK.md` section 3 (the updater also rolls
  back by itself when a release fails its health check); (3) the laptop: `python ops/release.py current --use <id>` verifies that
  release's signature and makes its manifest `current` again, so compute jobs take its images. `release.py current` alone prints the
  current release and its images.

### Compute jobs follow the manifest

`ops/release_lib/current.py` is the only place a compute job should learn which image to run:

```
python ops/release_lib/current.py image compute-mentat     # prints ghcr.io/pjg93-222/compute-mentat@sha256:... (else exit 1, reason on stderr)
python ops/release_lib/current.py show                     # release id, kind and every image
```

It verifies `current\manifest.json` against `ops/vps/allowed_signers` (SSH signature, namespace `melange-release`) and checks the
name is in the manifest and pinned by digest. Anything else (no manifest, a bad or missing signature, an edited file, an unknown name,
no trust file) is a non-zero exit and **no output**, so the wrapper must stop. In Python: `current.image_for(name)`.
A wrapper adopts it by replacing its hand-edited lock with the call (the compute branches own their files; this is what they change):

```powershell
$image = (& python "$RepoRoot\ops\release_lib\current.py" image compute-mentat).Trim()
if ($LASTEXITCODE -ne 0 -or -not $image) { throw 'no verified image for compute-mentat: not running' }
docker run --rm ... $image ...
```

The wrappers to point at it: Mentat `run_job.ps1` (branch `compute/mentat-jobs`; `compute-mentat`, `office-calc`), the Melange chain
runner (`compute/melange-chain`; `compute-melange`), the collectors (`compute/collectors`; `compute-collector`) and the Terrier build
runner (`terrier-build`, `terrier-build-score`; Geo's workflow builds them and they arrive in the same manifest).
Done on the Melange side: `ops/compute/chain_parity.py command --side container` takes `--image`, then the caller's
`IMAGE_COMPUTE_MELANGE` (an explicit choice, for a build no release names yet: the first parity runs), and otherwise asks
`current.py image compute-melange`; with none of the three it prints nothing and exits 2. The collector and office compose files take
their image from a variable the (not yet written) job wrapper sets: that wrapper resolves it the same way. Mentat's and Terrier's
wrappers live in their own repos and are not edited from here.

### Compute workflows adopt it

A `compute-*.yml` workflow (exported to `.github/workflows/` of the platform repo) takes part in the release in three small steps:

1. **Trigger** on the same tag push as `release.yml` (`on: push: tags: ["v*"]`). `release.py` waits for every run on the tagged commit
   and refuses the release if any is red.
2. **Reuse or build**, per image, in the build job (the same pattern as `release.yml`). When its lane lands, add the image to
   `ops/image_inputs.json` as `"compute-mentat": {"dockerfile": "ops/docker/compute-mentat.Dockerfile", "extra": [...]}` (done for the
   four compute images, see "Adopted" below).

   ```yaml
   - id: inputs
     run: echo "hash=$(python3 ops/release_lib/inputs.py hash "$NAME")" >> "$GITHUB_OUTPUT"
   - id: reuse
     run: bash ops/ci/build_or_reuse.sh check "ghcr.io/pjg93-222/$NAME" "$HASH"
   - id: push
     if: steps.reuse.outputs.reused != 'true'
     uses: docker/build-push-action@...        # as in release.yml
   - if: steps.reuse.outputs.reused != 'true'
     run: bash ops/ci/build_or_reuse.sh tag "ghcr.io/pjg93-222/$NAME" "$HASH" "$DIGEST"
   ```

   The digest to record is `steps.reuse.outputs.digest` when reused, else `steps.push.outputs.digest`.
3. **Report the digest**: upload a workflow artifact whose name ends in `-digests` holding `digests.json` =
   `{"tag": "<tag>", "commit": "<sha>", "images": {"<name>": "ghcr.io/pjg93-222/<name>@sha256:<64 hex>"}}`. `release.py` reads only
   artifacts of successful `push` runs of this tag at this commit, requires the file to name the same commit and tag, and refuses any
   image outside `ghcr.io/pjg93-222/`, whose name differs from its repository name, or that is not pinned by digest
   (`DIGESTS_BAD`); two runs naming one image differently are `DIGEST_CONFLICT`. Geo's `digests.json` can name extra Terrier
   images the same way, in its release asset.

An image whose Dockerfile is not in the tree yet is absent from the hashes and from the manifest until its lane lands.

**Adopted** (integration-2, 2026-10-12): `compute-images.yml` (compute-mentat, artifact `compute-images-digests`), `compute-office.yml`
(office-calc, `compute-office-digests`), `compute-melange.yml` (compute-melange, `compute-melange-digests`) and `compute-collector.yml`
(compute-collector, `compute-collector-digests`). All four run on the `v*` tags (the compute-specific tags are gone; `workflow_dispatch`
stays for a build by hand), build only on an `inputs-<hash>` miss, give a new image that tag after its smoke test, and write
`digests.json` in a `digests` job that needs the smoke test, SBOM and images gate. A red compute run therefore stops the release, as
documented above. `ops/tests/test_compute_workflows_adopt.py` holds them to this shape. Two rules for the `ops/image_inputs.json` rows:
a file the build reads without a `COPY` (`RUN --mount=type=bind,source=...`, or a lock the workflow turns into build args) goes under
`extra`; the Melange or Mentat tree an image runs on does not, because those images hold no code and any commit would force a rebuild.

### How each platform receives an update

| Platform | What triggers it | How it receives the change |
|---|---|---|
| Laptop compute jobs (Mentat jobs, Melange chain, Terrier build, collectors) | any code release | `current\manifest.json` is replaced; the next job start resolves its image through `current.py` |
| Mentat console (laptop, 8802...) | a release that changed Mentat | the launcher replaces an idle console running older code; busy ones are retried each pass |
| Melange console (8841), Terrier local servers | a release that changed them | Glass Box `release.restart_needed`; restart when idle (nothing is restarted for you) |
| VPS (melange-desk, mentat-console, terrier-co05/or05/ct01) | any signed manifest | manifest, signature and `snapshot_manifest.json` reach the inbox; the updater verifies, pulls the digests in `systems`, flips (code-only = same snapshot, no data copy), health-checks and rolls back by itself on failure |
| GitHub (platform repo + Geo) | any code release | tag pushed, CI builds only changed images, draft releases un-drafted after the manifest is verified |
| Netlify desk, Mentat Pocket | a data release (desk: only when the invited build changed) | the existing `--data-only` path and its gates; skipped with a note when governance is not ruled or Netlify is not configured |

## Secrets

Read from Windows Credential Manager (generic credentials) or DPAPI; held in a wrapper whose repr/str hide the
value; passed to child processes through environment variables or HTTP headers only; refused if they would land in
argv; scrubbed from error text; never written to logs, Glassbox events or files (the DPAPI store excepted).

| Credential (Credential Manager) | Username | Secret |
|---|---|---|
| `melange-restic` | (unused) | restic repository password (`RESTIC_PASSWORD`) |
| `melange-svc-push` | Cloudflare service-token client id | client secret (`TUNNEL_SERVICE_TOKEN_ID/SECRET` for cloudflared) |
| `melange-svc-status` | Cloudflare service-token client id | client secret (`CF-Access-Client-Id/Secret` headers) |

## Paul's one-time setup

1. Install `restic` and `cloudflared` (official releases) so they are on PATH. (`gitleaks` is the security gate's.)
2. Signing key: `python ops/release.py init-signing`, run the printed `ssh-keygen` command yourself, `ssh-add` it,
   commit the `allowed_signers` line, and include the key in the Carbonite backup.
3. Create the three credentials above in Credential Manager (`cmdkey /generic:melange-restic /user:x /pass:...` or
   the Control Panel), entering the values yourself.
4. `python ops/release.py store-netlify-token`, then set `netlify.site_id` in `ops/release_config.json`.
5. Set `domain` in `ops/release_config.json` once the Cloudflare zone exists; pin the VPS host key once with a
   manual `ssh melange-push@ssh.<domain>` (the release command uses `BatchMode`, so an unknown host key fails
   closed rather than prompting).
6. Create the empty private `pjg93-222/melange-platform`; `gh auth login` for the CLI.
7. Set `DASHBOARD_PASSWORD_FILE` (or `dashboard_password_file`) for Pocket locking.
8. Optional Pocket rebuild from a running console: set `pocket.console_port` and `pocket.datasets`.
9. Review `ops/serve_sets.json` (`census_pending` is true: every set is an upper bound until the audit-hook census)
   and run `python ops/release.py plan`.
10. Automatic propagation: make sure the Windows "OpenSSH Authentication Agent" service is running and the release key is loaded
    (`ssh-add %USERPROFILE%\.ssh\melange_release_ed25519`, once per boot; the task cannot answer a passphrase prompt), optionally
    create a Healthchecks check and put its ping URL in `auto.healthchecks_url`, run `python ops/release.py auto --plan`, and, when it
    reads right, run `ops\auto\Register-AutoRelease.ps1 -WhatIf` and then without `-WhatIf`.

## Notes for the VPS side

* Restic snapshots one directory (the farm), so `restic snapshots --json` shows exactly one path. Restic stores that
  absolute path in the tree (on Windows `C:\...\farm` is `/C/.../farm`), with `melange/`, `mentat/`, `terrier/` under it
  and the same relative paths as `snapshot_manifest.json`. The release reads the path back from
  `restic snapshots <id> --json` and signs it into `manifest.data_snapshot.restic_path`; the VPS restores
  `<id>:<restic_path>`, so the restored root is the farm.
* `manifest.json` and `snapshot_manifest.json` are canonical JSON (sorted keys, `(",", ":")`, UTF-8 not escaped);
  the signature covers the file bytes exactly. `snapshot_manifest.json`'s sha256 is
  `manifest.data_snapshot.manifest_sha256`.
* `.part` files in the inbox are in-progress uploads and must be ignored (the updater reads only the three contract
  names and starts only when `manifest.json` exists). `sftp rename` never overwrites, so the push removes any leftover of the
  same name first (`-rm`, errors ignored) and then renames.
