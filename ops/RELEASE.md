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
