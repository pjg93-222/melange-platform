# The Melange VPS: what to buy, what to paste, in what order

This is Paul's checklist. Nothing here is automatic past the first boot: the server comes up
hardened and empty, and stays closed until each value below is in place. Day-to-day failures are
in `ops/RUNBOOK.md`.

Claude never sees any value marked PASTE. Each one is entered by Paul, on the server's console,
in the Cloudflare/GitHub dashboards, or in a prompt.

## 1. What runs where

```
laptop ──sftp via cloudflared──▶ /srv/melange/push/{inbox,restic}      (chrooted user melange-push)
                                      │ melange-update.path / hourly timer
                                      ▼
                       melange_update.py: verify signature → restore → rehash → flip → compose up
                                      ▼
   Cloudflare ─▶ cloudflared (container) ─▶ melange-desk, mentat-console, terrier-co05/or05/ct01
   (Access)         only service with egress       one internal network per app, no published ports
```

Cloudflare tunnel public hostnames (Zero Trust → Networks → Tunnels → this tunnel → Public
hostnames). Every row gets its own Access application and policy; the apps verify the JWT again.

| Hostname | Service URL | Access groups |
|---|---|---|
| `melange.<DOMAIN>` | `http://melange-desk:8080` | paul-admin, staff, collaborators |
| `mentat.<DOMAIN>` | `http://mentat-console:8802` | paul-admin, staff, collaborators |
| `co05.<DOMAIN>` | `http://terrier-co05:8080` | paul-admin, terrier-co05 (country US) |
| `or05.<DOMAIN>` | `http://terrier-or05:8080` | paul-admin, terrier-or05 (country US) |
| `ct01.<DOMAIN>` | `http://terrier-ct01:8080` | paul-admin, terrier-ct01 (country US) |
| `ssh.<DOMAIN>` | `ssh://172.29.0.1:22` | paul-admin; service token svc-push for sftp |

Files on the server:

| Path | What |
|---|---|
| `/srv/data/<system>/releases/<id>/`, `/srv/data/<system>/current` | verified, read-only data; `current` is the live symlink |
| `/srv/data/docker/` | Docker's data-root (images, Terrier state volumes): on the encrypted volume |
| `/srv/data/.push` (bind-mounted at `/srv/melange/push`) | sftp chroot: `inbox`, `restic`, `outbox` (push user's); `status` (root's, the push user only reads) |
| `/srv/data/.backup-tmp` | root-only staging for the nightly Terrier backup; renamed into `outbox` |
| `/srv/serve/{compose.yaml,compose.env,secrets/}` | the stack, operator values, root-only secrets |
| `/srv/access/jwks.json` | Access public keys, refreshed every 10 minutes |
| `/var/lib/melange-update/state.json` | what is deployed (the downgrade guard reads this) |
| `/etc/melange/` | allowed_signers, restic.pass, age_recipients, healthchecks.url, bootstrap.env |

## 2. Provider requirements

Paul picks the provider; cost is unverified (about $50-100 a month for this size).

- **Region:** United States. **OS image:** Ubuntu 24.04 LTS with cloud-init.
- **Size:** 16 GB RAM and 4-8 vCPU. The container limits add up to 12.8 GB
  (desk 512m, Mentat 6g, three Terrier at 2g, cloudflared 256m); the rest is the OS and page cache.
- **Boot disk:** 40 GB or more (OS, 4 GB journal cap). It holds no data and no Docker state.
- **Data volume:** one separate block volume of about 250 GB, **unformatted**. Peak use at the
  planned 55 GB serve set is about 165 GB (current release + restore staging + restic repo +
  images). Past a 60 GB serve set, buy 350 GB. The updater refuses a restore that would leave under 15 GB free, and
  after every good release runs `docker image prune -af --filter until=168h` (unused images only, never volumes).
- **The push area needs its own ceiling.** `/srv/data/.push` (restic repo, inbox, outbox) is writable by whoever holds
  the push key, and it shares the volume with Docker and the release data: a leaked key could fill the disk. Give it a
  quota or its own volume before the first push, e.g. a second block volume mounted at `/srv/data/.push` (size it at 2x the
  restic repo you expect), or an ext4 project quota on that directory (`tune2fs -O project,quota`, `chattr -R +P -p 100
  /srv/data/.push`, `setquota -P 100 <soft> <hard> 0 0 /srv/data`). The 15 GB headroom check keeps a full push area from
  corrupting a deploy; it does not stop it from refusing the next one.
- **Provider firewall:** deny all inbound, allow all outbound. No exceptions: cloudflared dials
  out, nothing dials in. (ufw inside the box denies inbound too; Docker can bypass ufw, so this
  outer layer is the one that counts.)
- **Provider backups and snapshots: off** (ruling 9: the auto-unlock key sits on the boot disk, so
  a provider snapshot of both disks would defeat the encryption).
- **User-data limit:** `cloud-init.yaml` is about 31 KB (a test keeps it under 32 KiB). Providers
  differ (as I recall Hetzner 32 KiB, DigitalOcean 64 KiB, AWS 16 KiB; check yours). If the cap is
  lower, create the server from a plain Ubuntu image and use the kit instead (`ops/kit/install.sh`,
  section 6).
- A console that works without ssh (every provider has one). You will use it for the first hour.

## 3. Paul's checklist, in order

### A. Before pasting `ops/vps/cloud-init.yaml`

Edit the file in a copy; these are the only values in it. Nothing in it is secret (the provider's
metadata service can read user-data).

1. `PASTE-ADMIN-PASSWORD-HASH`: on any Linux box, `mkpasswd -m sha-512`, paste the hash. This is
   the break-glass console login and the sudo password. Write the plain password on paper.
2. `PASTE-ADMIN-PUBKEY`: your ssh public key (`ssh-keygen -t ed25519`; paste the `.pub` line).
3. `PASTE-DATA-DEVICE` (in the `bootstrap.env` block): the data volume's stable device path, for
   example `/dev/disk/by-id/scsi-0HC_Volume_12345678`. It must be the volume, never the boot disk.
   If you cannot know it before the server exists, leave it and fill it in at step C.
4. `PASTE-DOCKER-VERSION`, `PASTE-CONTAINERD-VERSION`, `PASTE-COMPOSE-VERSION`: leave them on the
   first run. The bootstrap adds Docker's apt repo, checks its key fingerprint, prints the
   available versions into `/var/log/melange-bootstrap.log`, and waits for you to pin (section 5).
5. Leave the other PASTE lines (`PASTE-ALLOWED-SIGNERS-LINE`, `PASTE-RELEASE-PUBLIC-KEY`,
   `PASTE-AGE-RECIPIENT`, `PASTE-PUSH-PUBKEY`): you paste those on the box at step D, or here if
   you already have the keys.

### B. Create the server

1. Create the server in a US region from Ubuntu 24.04, with the 250 GB volume attached
   (unformatted) and the provider firewall set to deny all inbound. Provider backups off.
2. Paste the edited `cloud-init.yaml` as user-data. Wait about five minutes.

### C. First look, on the provider console (not ssh: ssh is closed on purpose)

1. Log in as `admin` with the break-glass password.
2. `sudo tail -n 60 /var/log/melange-bootstrap.log`. It ends with a `PENDING:` list. Everything
   pending is one of the rows in D. Fix them, then run `sudo melange-bootstrap` again. It is safe
   to repeat; it never formats a device that already holds something.
3. After the volume line reads `LUKS volume mounted at /srv/data`, back up the LUKS header
   (RUNBOOK section 7) and store the file with the release signing-key backup.

### D. Every value you paste, and where

| Value | Where it comes from | Where it goes | How |
|---|---|---|---|
| Release public key (`PASTE-ALLOWED-SIGNERS-LINE`, `PASTE-RELEASE-PUBLIC-KEY`) | the laptop's release signing key `.pub` | `/etc/melange/allowed_signers` | one line: `release@melange namespaces="melange-release" ssh-ed25519 AAAA...` (replace both placeholder lines) |
| Push public key (`PASTE-PUSH-PUBKEY`) | an ed25519 key made on the laptop for sftp | `/etc/ssh/authorized_keys/melange-push` | one line, no options |
| age recipient (`PASTE-AGE-RECIPIENT`) | `age-keygen` on the laptop (keep the private key off the server) | `/etc/melange/age_recipients` | one `age1...` line |
| restic password | you choose; also store it in Windows Credential Manager for the laptop | `/etc/melange/restic.pass` | `sudo sh -c 'umask 277; cat > /etc/melange/restic.pass'`, paste, Ctrl-D. Never type it on a command line |
| Healthchecks ping URL | healthchecks.io check "melange-vps", period 5 min, grace 10 min, phone alert | `/etc/melange/healthchecks.url` | `sudo sh -c 'umask 177; cat > /etc/melange/healthchecks.url'`, paste, Ctrl-D |
| GHCR token | GitHub classic PAT, `read:packages` only | root's docker config | `sudo docker login ghcr.io -u <github-user>`; the token is typed at the prompt, never in a file or history |
| Tunnel token (`PASTE-TUNNEL-TOKEN`) | Cloudflare tunnel → Configure → the token inside the install command | `/srv/serve/secrets/tunnel.env` | `sudo editor /srv/serve/secrets/tunnel.env`, one line: `TUNNEL_TOKEN=...` |
| Domain (`PASTE-YOUR-DOMAIN`) | the domain moved to Cloudflare | `/srv/serve/compose.env` `DOMAIN=` | edit with `sudo editor` |
| Team name (`PASTE-TEAM-NAME`) | Zero Trust → Settings → the `<team>.cloudflareaccess.com` prefix | `compose.env` `TEAM=` | edit |
| Access AUDs (`PASTE-AUD-MELANGE`, `PASTE-AUD-MENTAT`, `PASTE-AUD-CO05`, `PASTE-AUD-OR05`, `PASTE-AUD-CT01`) | each Access application's Overview → Application Audience (AUD) Tag | `compose.env` `AUD_*=` | edit; one per host, never shared (the updater refuses a release while two are equal or one is unset) |
| Service-token names | Access → Service Auth: the svc-status token's Client ID (`<id>.access`) | `compose.env` `ACCESS_SERVICE_CNS=` | comma-separated; empty accepts none |
| cloudflared digest | `ops/images.lock` after section 4 | `compose.env` `IMAGE_CLOUDFLARED=cloudflare/cloudflared@sha256:...` | edit |

The updater refuses to deploy while any PASTE placeholder is left in `compose.env`, and the
bootstrap lists every file that still has one.

When the table is done: `sudo systemctl start access-jwks.service` (check
`/srv/access/jwks.json` exists), then `sudo melange-bootstrap` once more. It should end with
`bootstrap complete`.

### E. Cloudflare side (dashboard, once)

1. Tunnel with the six public hostnames from section 1, each "Protect with Access".
2. One Access application per hostname with its own policy and its own AUD (that is how the apps
   tell hosts apart). Terrier policies require the district group and `country = US`.
3. Service tokens svc-push (sftp via `ssh.<DOMAIN>`) and svc-status (`/version` checks); store
   them in Windows Credential Manager on the laptop only.
4. Cache bypass on all hostnames, HSTS, tunnel health notifications.

### F. First release

From the laptop: `release plan` (dry run), then Paul says yes, then `release run`. Watch
`jq . /srv/melange/push/status/status.json` on the server. The first data push is about 55 GB; run
it overnight. `OK` means every service answered `/version` with the new snapshot.

## 4. Base image digests

`ops/images.lock` holds the real multi-arch index digests of the python, node and cloudflared images
(resolved 2026-10-12, patch levels in `resolved_version`); `release.yml` still refuses to build if one
is ever a placeholder. Both Dockerfiles default `PYTHON_BASE` to the locked digest, `compose.env.example`
carries the cloudflared one, and Terrier's Dockerfile pins the same python and node digests;
`test_compose_policy.py` fails if any of them drifts from the lock. To refresh (no Docker needed):

```bash
# token: https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull
# digest: GET https://registry-1.docker.io/v2/library/python/manifests/3.12-slim-bookworm  (Accept: the OCI index and
# Docker manifest-list types) -> the Docker-Content-Digest header; the python/node patch level is PYTHON_VERSION /
# NODE_VERSION in the amd64 image config. Same for library/node:22-bookworm-slim and cloudflare/cloudflared:<newest dated tag>.
```

Update the lock, then every place the test names. Refresh quarterly or on a base-image CVE; the
security gate's image scan is what flags one.

## 5. Pin Docker

After the bootstrap has added the repo, `/var/log/melange-bootstrap.log` lists the candidates. Or:

```bash
apt-cache madison docker-ce containerd.io docker-compose-plugin | head
```

Put the exact version strings (for example `5:27.5.1-1~ubuntu.24.04~noble`; the three packages
use different version schemes) into `/etc/melange/bootstrap.env`, then `sudo melange-bootstrap`.
Docker is `apt-mark hold`-ed afterwards and is outside unattended-upgrades; upgrade it by hand
(`apt-mark unhold`, install the new pin, `hold`) during a quiet hour.

**Docker 29 and later** default to the containerd image store, which keeps image layers under `/var/lib/containerd`
on the boot disk, outside Docker's `data-root`. If the pinned `docker-ce` is 29 or newer, put containerd's root on the
volume before the first pull: `root = "/srv/data/containerd"` in `/etc/containerd/config.toml` (and a `containerd.service`
drop-in with `RequiresMountsFor=/srv/data`), then `sudo systemctl restart containerd docker`. Check with
`docker info | grep -i 'driver-type\|storage driver'` and that `/var/lib/containerd` stays small after a pull.

The bootstrap checks Docker's repository key against fingerprint
`9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88`. Confirm that fingerprint once against
<https://docs.docker.com/engine/install/ubuntu/>; if Docker ever rotates it, the bootstrap fails
closed and says so.

## 6. Stand up a second server (kit path)

For a host that is not created from cloud-init: install Docker and `jq`, put the release public key
in `/etc/melange/allowed_signers`, unpack the kit from the draft GitHub release (check it against
`SHA256SUMS`), put the release's `manifest.json` and `manifest.json.sig` beside it, and run
`sudo ./install.sh --manifest manifest.json`. It refuses to continue unless the signature verifies.
It installs the same updater, units and compose file, writes `compose.env` from the signed digests,
and pulls them. Data still arrives by the normal push.

## 7. Pinned actions and CI tools

Every third-party action in `ops/github/workflows/*.yml` and Geo's `.github/workflows/release.yml` is pinned
to a full commit SHA with the tag it came from as a trailing comment (`test_compose_policy.py` checks the
form). To bump one: `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`.

| Action | Used in |
|---|---|
| `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4` | release, deps |
| `actions/upload-artifact@v4`, `actions/download-artifact@v4` | release, deps, geo |
| `docker/setup-buildx-action@v3`, `docker/login-action@v3`, `docker/build-push-action@v6` | release, geo |
| `anchore/sbom-action@v0` | release, geo |
| `r-lib/actions/setup-r@v2` | deps |

CI and the gate (`ops/security_gate.py`, whose CLI the workflow now uses as is):

- `security` runs `export --ci --target . --report ...` after installing gitleaks 8.30.1 by version and sha256
  (the digest GitHub publishes for the asset); the gate fetches no tools and fails closed on a missing one.
  `scan` installs trivy 0.75.0 the same way and runs `images --ci --compose-json compose.config.json --image <desk>
  <mentat> --report ...`. The `all` stage also needs the Geo checkout (`--terrier-dir`), so it stays the laptop's
  pre-release run.
- A tag build is the root of trust, so `build` and `kit` first refuse any tag that is not `v<YYYY>.<MM>.<DD>.<n>` (a ref name can hold
  commas, which buildx splits `outputs:` on), and the image builds use no layer cache (`no-cache: true`): a GitHub Actions cache entry can
  be written by any other workflow run in the repo, so a cached layer would ride, unseen, into an image the release signs. Geo's workflow
  does the same. The cost is a few minutes per release. (Hash-pinning the `test` job's dependencies would not help here: they never
  reach an image.)
- `test` installs `requirements.txt`, `pyyaml` and `cryptography==49.0.0`, pinned but not hashed; swap to a hashed
  `ops/locks/ci-test.linux.txt` when one exists. It runs `ops/ci/no_data_tests.txt` and `ops/ci/mentat_tests.txt`.
  The Mentat step writes an empty `mentat/inputs/fec_receipt_rules.json` (the export drops `mentat/inputs`; the console
  stats it at import) and deselects the five tests that read the real rules. To run them in CI, let the export carry
  that file and `fec_transaction_types.json` (about 13 KB of FEC rules, no data): an export-policy call, Paul's.
  `test_hosted_mode.py::test_one_audit_line_per_request_without_query_or_address` reads the server's stderr right after the
  response and has failed once in about eight runs on a loaded machine; re-run before suspecting the change.
- Geo's workflow is its own (`.github/workflows/release.yml` in Geo); `ops/terrier/geo-release.yml` is gone. It passes
  `GIT_SHA`/`BUILD_TIME`, refuses an image holding data files, and writes `digests.json` as
  `{tag, commit, images: {"terrier-web": ref@digest}}`, which `release.py` parses.
- `deps.yml` needs a repository secret `GEO_READ_TOKEN` (fine-grained PAT, read-only contents on
  `pjg93-222/Geo`) and a ratified `PPM_SNAPSHOT_DATE`. It builds the R bundle from `ops/locks/r-packages.txt`
  (`name==version`, CRAN only; the export no longer carries `mentat/R-packages.lock.tsv`) and fails when the dated
  snapshot serves a different version of any package. `R_VERSION` is the minor that lock was made with (4.6). To refresh
  the lock after Mentat's library changes, regenerate it from `mentat/R-packages.lock.tsv` (drop the paths and the
  packages that ship inside R) and keep the header comment naming the source and date.

## 8. Design calls to veto

These are places where the brief left a gap and I chose; each is a small change to reverse.

1. **sshd also listens on 172.29.0.1** (the `egress` bridge gateway), not only 127.0.0.1. The
   cloudflared container cannot reach the host's loopback, and the ssh route needs a target.
   `ufw` allows only the `br-egress` interface to that address and only on port 22; nothing else can
   reach it. Revert: drop the second `ListenAddress` and the ufw rule, and run a host-side
   cloudflared for ssh instead.
2. **Docker's data-root is on the LUKS volume** (`/srv/data/docker`). Otherwise Terrier's field
   results (the master copy, ruling 8) would sit unencrypted on the boot disk. Consequence: if the
   volume does not unlock, Docker does not start and the site is down until you fix it. That is the
   intended fail-safe.
3. **The sftp chroot is on the volume** (bind mount of `/srv/data/.push`), so the restic repo does
   not need space on the boot disk.
4. **Release data is world-readable on the host** (files 0444, directories 0555). With
   `userns-remap` the containers read host files as "other". The host has two login users (admin,
   and melange-push confined to its chroot), and admin is root-equivalent anyway.
5. **`up --wait` covers the app services only**, not cloudflared. A Cloudflare incident must not
   roll back a good release. cloudflared is started with a plain `up -d` and a failure is reported
   under `warnings` in `status.json`.
6. **Terrier state volumes are `external`**, created once by the bootstrap. `compose down -v`
   cannot delete them, and the updater never issues `down`, `rm` or `volume` (it refuses to).
7. **The updater consumes the inbox**: processed files move to
   `/var/lib/melange-update/processed/` (last five kept) whatever the outcome, so a mid-upload
   timer run cannot misread a half-written set, and a rejected upload does not retry hourly. To
   retry, push again or copy the three files back into the inbox.
8. **Code-only releases skip the restore**: when `data_snapshot.restic_snapshot_id` equals the one
   already deployed, the existing data directories are reused (no second 55 GB copy).
9. **The snapshot id file**: the updater writes the restic snapshot id to `.snapshot_id` in each
   system's release directory; compose mounts it at `/run/snapshot_id` (`SNAPSHOT_ID_FILE`), and the
   updater requires `/version.snapshot_id` to equal it. Apps must report exactly the file's content.
10. **One internal network per app** (`desk`, `mentat`, `co05`, `or05`, `ct01`). cloudflared joins all of them plus `egress`;
    the apps share nothing, so a compromised district container cannot open a connection to another district's.
11. **The push-key holder cannot steer root.** sftp is `internal-sftp -P symlink,hardlink` (no links can be planted), `status/` is
    root's, root's temp files are created `O_EXCL|O_NOFOLLOW`, inbox files are read `O_NOFOLLOW` as regular files of at most
    64 MiB, and the nightly backup is encrypted into `/srv/data/.backup-tmp` (root, 0700) and renamed into the outbox.
12. **A crash mid-release is recovered, not repeated.** Before the first link moves, `state.json` records `pending` (the previous
    links and env). If the updater dies after that (kill, OOM, reboot), the next run puts the previous release back first, then
    handles the inbox. The service has no start timeout, so systemd never causes that crash itself.
13. **Terrier is backed up before it can migrate.** A release that changes the Terrier image digest first runs Terrier's own
    `manage.py backup` for every district into `/state/pre-<release-id>.sqlite3`; if that fails nothing is changed. A rollback after
    such a release says so in `status.json` (RUNBOOK section 10).

## 9. Assumptions about other lanes

- Release lane: `data_snapshot.restic_snapshot_id` is a hex restic id and `data_snapshot.restic_path` is where the
  release's farm directory sits inside that snapshot, as restic records it (a Windows `C:\...\farm` is `/C/.../farm`;
  read from `restic snapshots --json`). The updater restores `<id>:<path>`, so the restored root holds `melange/`,
  `mentat/`, `terrier/` directly. A manifest without it fails closed (`unexpected top-level entries`).
- `systems.<name>.data_subdir` defaults to the system name; `services` is optional and defaults to
  every service of the system.
- Terrier data per district sits at `terrier/co-05|or-05|ct-01/` in the snapshot (the stores, `bundle.zip` and
  `bundle.zip.sha256`), mounted read-only at `/data` (`CU_DATA_DIR`, `CU_BUNDLE=/data/bundle.zip`); the external
  `terrier-<d>-state` volume is mounted at `/state` (`CU_STATE_DIR`). Services and hostnames stay `co05|or05|ct01`.
- Melange's `melange/` tree is the desk root (`DESK_ROOT=/data/melange`): pages with their `.gz` siblings,
  `INVITED_BUILD.json` and `downloads/<id>/` plus `downloads/latest/` (real files; a snapshot holds no symlinks).
- `CU_DISTRICT_URLS` is a comma list of `co-05=https://co05.<domain>,...`.
- Access lane: `fec/surfaces/desk_server.py` exposes `main()` and imports only `fec/access_gate.py`
  (the desk image copies exactly those two files and runs `python -m fec.surfaces.desk_server`). Mentat's modules
  resolve with `PYTHONPATH=/app/mentat/app:/app/mentat/tools`; its read-only data is mounted where `server.py`'s
  `PROJECT` (`/app/mentat`) looks: `state`, `.cache`, `inputs`, `forecast`, `app/data`, plus `reports` and `melange`
  through `MENTAT_REPORT_DIRECTORY` and `MENTAT_MELANGE_ROOT`.
