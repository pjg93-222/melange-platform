# Melange / Mentat / Terrier: runbook

Incidents first, drills last. Everything here runs as `admin` with `sudo` on the VPS (provider
console, or `ssh.<DOMAIN>` through cloudflared). Setup is in `ops/VPS.md`.

Two rules apply to every section:

- **Never** run `docker compose down`, `docker compose rm`, `docker volume prune` or
  `docker system prune --volumes` on this host. Terrier's field results live in Docker volumes
  (`terrier-co05-state` and the other two); they are the master copy. Use `stop`, `restart`,
  `up -d` and `up -d --force-recreate <service>`.
- If you are unsure whether an action touches field data, stop and take a backup first:
  `sudo /usr/local/lib/melange/melange-ops.sh backup`.

Shortcuts used below:

```bash
dc() { sudo docker compose -p melange -f /srv/serve/compose.yaml --env-file /srv/serve/compose.env "$@"; }
jq . /srv/melange/push/status/status.json        # last release attempt, what is serving, warnings
sudo jq . /var/lib/melange-update/state.json     # deployed release, snapshot id, last forget
sudo journalctl -u melange-update -n 200 --no-pager
dc ps
```

## 1. Compromised user (an invitee's account, laptop or session)

1. **Cut access now.** Cloudflare Zero Trust → Access → Groups: remove the email from every group
   (or add it to an explicit Block policy). Then Settings → Authentication → Users → the user →
   **Revoke** to kill live sessions. The apps re-verify the JWT on every request, so this takes
   effect on the next request.
2. **Terrier user.** Deactivate the account for that district and delete its sessions, with the
   district's Terrier user tooling (confirm the model's field names first):
   ```bash
   dc exec terrier-co05 python manage.py shell -c "from django.contrib.auth import get_user_model as g; \
   u=g().objects.filter(email__iexact='person@example.org'); print(u.update(is_active=False))"
   ```
3. **Find out what they read.** Every request has a JSON audit line (user, method, route, status):
   ```bash
   sudo journalctl CONTAINER_NAME=melange-terrier-co05-1 --since "14 days ago" -o cat | grep '"user":"person@example.org"'
   ```
   Terrier also writes a per-person-read audit event; export that for the same user. Cloudflare
   Access logs on the Free plan are kept 24 hours: **export them first**, before anything else
   expires.
4. If they could have copied voter or donor rows in bulk, go to section 4.
5. Re-invite only after the use agreement is re-signed and the new device is clean.

## 2. Leaked tunnel token or service token

**Tunnel token** (anyone holding it can attach a connector that receives your traffic):

1. Zero Trust → Networks → Tunnels → the tunnel → **Refresh token** (or recreate the tunnel and
   re-add the six public hostnames).
2. On the VPS: `sudo editor /srv/serve/secrets/tunnel.env` (new `TUNNEL_TOKEN=`), then
   `dc up -d --force-recreate cloudflared`.
3. In the tunnel's **Connectors** list there must be exactly one connector, from the VPS's IP.
   Delete any other. Check Access logs for requests you do not recognise. (The alert for "a stray
   tunnel connector" is what normally catches this.)

**Service token** (svc-push or svc-status):

1. Access → Service Auth → delete the token (or rotate it) and create a replacement.
2. Replace the secret in Windows Credential Manager on the laptop.
3. If the Client ID changed, update `ACCESS_SERVICE_CNS` in `/srv/serve/compose.env` and
   `dc up -d` (the apps are recreated with the new value).
4. A stolen svc-push can only reach the chrooted sftp user, and still needs the laptop's push key;
   a stolen svc-status can only read `/version`. Neither can deploy: that needs the release signing
   key (section 5).

## 3. Bad release and rollback

The updater already does the first line of defence. `status.json` says what happened:

| `state` | Meaning | What to do |
|---|---|---|
| `OK` | New release serving, every service answered `/version` with the new snapshot | nothing |
| `ROLLED_BACK` | It flipped, something failed, and the previous release is serving again (on a first release there is none: its links are removed and its services stopped, nothing is served) | read `reason`; fix the release and push a new, higher id |
| `FAILED` | Refused or failed **before** anything was flipped (or the rollback itself failed) | read `reason`, table below |

Common `reason` texts:

| Reason contains | Cause and fix |
|---|---|
| `manifest signature invalid` | wrong key or tampered upload; check `/etc/melange/allowed_signers`; if the laptop key changed, section 5 |
| `downgrade refused` | the pushed release id is lower than the deployed one; release a higher id |
| `already deployed with a different manifest` | an id was reused; cut a new id |
| `snapshot manifest sha256 does not match` | upload corrupted or mixed; push again |
| `file set differs`, `size or sha256 differs` | the restore is not what was signed: restic repo damaged or tampered. Nothing was flipped. Run `restic check` (section 8), push again |
| `inbox/... refused` | the inbox held a symlink, FIFO or a file over 64 MiB (it was removed unread). Nothing is wrong with the release: push again. If you did not do it, the push key is in someone else's hands: section 5 |
| `PASTE placeholders` | fill `compose.env` (VPS.md section 3D) |
| `is not a mounted volume` | the encrypted volume is not mounted: section 7 |
| `AUD_... not set`, `share one AUD` | `compose.env` is missing an Access application's AUD tag, or two services carry the same one (VPS.md section 3D) |
| `not enough free space` | section 7 (volume full) |
| `compose pull failed` | GHCR login expired or the digest does not exist: `sudo docker login ghcr.io`, check the draft release |
| `/version snapshot_id ... != expected` | an app did not report the new snapshot: check `dc logs <service>` and that the app reads `SNAPSHOT_ID_FILE` |
| `was interrupted (crash, kill or reboot)` | the updater died between the flip and the end of a release. The next run put the previous release back first (`state.json` `pending`), then retried the pushed upload; nothing to do unless `state` is `FAILED` |
| `pre-release backup of terrier-... failed` | the release changes the Terrier image and a district's accounts DB could not be copied first (or its presence could not be checked, or the previous `compose.env` names no Terrier image), so nothing was changed. Is that district running (`dc ps`)? Fix it, then push again |
| `DB schema may be ahead` | the rolled-back release had changed the Terrier image, which may have migrated the accounts DB: section 10 |
| `ROLLBACK FAILED` | **services may be down**: `dc ps`, then `dc up -d --wait melange-desk mentat-console terrier-co05 terrier-or05 terrier-ct01`; if the data links are wrong, the manual steps below |

A failed or rolled-back release leaves `current` pointing at the last good data. To retry the same
files: copy them back from `/var/lib/melange-update/processed/<newest>/` into
`/srv/melange/push/inbox/` (manifest.json last), or just push again.

**Manual rollback (emergency only).** The normal way back is to release the old code again under a
new, higher id: downgrade protection stays on by design. If the site is broken *now* and a release
cannot be cut:

```bash
ls /srv/data/melange/releases /srv/data/mentat/releases /srv/data/terrier/releases   # current + previous
# for each system that needs it:
sudo ln -sfn releases/<previous-id> /srv/data/<system>/current
sudo cp /srv/serve/compose.env.prev /srv/serve/compose.env     # the env from before the last flip
dc up -d --wait melange-desk mentat-console terrier-co05 terrier-or05 terrier-ct01
```

Then cut a proper release soon. `state.json` still says the newer id; the next release must be
higher than that.

## 4. PII exposure (voter rows or donor rows seen by someone who should not)

1. **Stop the exposure.** Fastest: Cloudflare Access → that host's application → set the policy to
   block everyone. Or stop the one service: `dc stop terrier-or05` (stop, never `down`).
2. **Preserve evidence.** Export the Access logs now (24-hour retention). Save the journal:
   `sudo journalctl --since "30 days ago" -o json > /srv/data/.incident-$(date +%F).json`
   (permissions 0600). Do not restart services before copying logs you may need.
3. **Scope it.** Which host, which group, which time window, which routes (audit lines), which
   users. For Terrier, the per-person-read audit events list exactly which voter rows a user opened.
4. **Where could the data have gone?** The image (CI scans for data files), the GitHub repo
   (history-free export), GHCR, the Netlify desk, a Terrier backup file, a user's download.
   Remove it at the source: delete the GHCR package version and the release assets, the Netlify
   deploy, and treat any user-held copy per the use agreement.
5. **Notify.** This is Paul's decision, with counsel (state voter-file rules: ORS 247.955, CGS
   9-50d, Colorado terms; FEC donor-use rules). Nothing in this repo is legal advice.
6. **Fix and prove.** Correct the Access policy or governance-matrix row, re-run the negative tests
   (wrong group → denied, other district → denied, no JWT → 403), release, and record it.

## 5. Lost or stolen laptop

The laptop holds the root of trust: the release signing key, the sftp push key, the age private key
(Terrier backups), the restic password, and Credential Manager (service tokens, Netlify, GitHub).

1. **Revoke remote access first.** Cloudflare: delete svc-push and svc-status and any API token.
   GitHub: sign out all sessions, revoke PATs, deploy keys and SSH keys, rotate the GHCR
   `read:packages` token and `sudo docker login ghcr.io` again on the VPS. Netlify: revoke the PAT.
2. **On the VPS:**
   - remove the push key from `/etc/ssh/authorized_keys/melange-push`, add the new laptop's key;
   - replace the line in `/etc/melange/allowed_signers` with the **new** release public key. From
     then on only manifests signed by the new key deploy; the old key is dead. Downgrade
     protection continues across the change (`state.json` is untouched);
   - restic: from the **new laptop** (bootstrapped with the old password, read from
     `/etc/melange/restic.pass` on the console), `restic key add` a new password and
     `restic key remove` the old key id over sftp, then put the new password in
     `/etc/melange/restic.pass`. Do not run repo-writing restic commands as root on the VPS: the
     files it creates would be root-owned and the laptop could no longer write the repository;
   - age: add the new recipient to `/etc/melange/age_recipients`. Old `.age` files stay readable by
     whoever holds the old private key; take a fresh backup (`melange-ops.sh backup`).
3. **Rebuild the laptop** from Carbonite (the C:\melange-data, LOCALAPPDATA and Terrier data sets,
   and the signing-key backup), generate the new keys, and run a `release plan`.
4. If the disk was not encrypted and was unlocked, assume the key was copied: also treat section 1
   as in effect for anyone whose data or credentials were on it.

## 6. Stale JWKS (everyone gets 401 or 403 on every app)

`/srv/access/jwks.json` is refreshed every 10 minutes. Healthchecks goes red when it is over an
hour old. Cloudflare rotates signing keys about every six weeks and publishes the next key in
advance, so a file stale for more than a week breaks logins.

```bash
ls -l /srv/access/jwks.json
sudo systemctl start access-jwks.service && sudo journalctl -u access-jwks -n 30 --no-pager
curl -fsS "https://$(grep ^TEAM= /srv/serve/compose.env | cut -d= -f2).cloudflareaccess.com/cdn-cgi/access/certs" | jq '.keys | length'
```

Typical causes: `TEAM` wrong in `compose.env`, no outbound HTTPS (provider firewall), a Cloudflare
outage. The refresh script validates the JSON before replacing, so a bad response never overwrites
good keys. No restart is needed: the directory is mounted into the containers.

## 7. LUKS and the data volume

Symptoms: updater says `is not a mounted volume`; Docker not running after a reboot.

```bash
lsblk -f; findmnt /srv/data; sudo cryptsetup status melange-data
sudo systemctl status systemd-cryptsetup@melange-data --no-pager
# manual unlock (the key is on the boot disk by design, ruling 9):
sudo cryptsetup open --key-file /etc/cryptsetup-keys.d/melange-data.key <device> melange-data
sudo mount /srv/data && sudo mount /srv/melange/push && sudo systemctl restart docker
dc up -d
```

Docker will not start without `/srv/data` mounted: that is the design, so nothing is ever written
to the unencrypted boot disk.

- **Header backup (do once, after first boot):**
  `sudo cryptsetup luksHeaderBackup <device> --header-backup-file /root/luks-header.img`, encrypt
  it with `age -R /etc/melange/age_recipients` and keep it with the signing-key backup. A damaged
  header is otherwise unrecoverable.
- **Key file lost:** the data is unrecoverable by design. Provision a new volume, run
  `melange-bootstrap`, push a full snapshot, and restore Terrier from the latest `.age` backup
  (section 10). This is why the nightly Terrier backup and the laptop pull matter most.
- **A Terrier state volume that Docker does not list:** the bootstrap and `install.sh` refuse to `docker volume create
  terrier-<d>-state` when `/srv/data/docker/*/volumes/terrier-<d>-state` already exists (a new volume would adopt or hide the field
  results in it). Look first: `sudo ls -l /srv/data/docker/*/volumes/terrier-<d>-state/_data`. If that is the district's data,
  `sudo docker volume create terrier-<d>-state` re-attaches it; take a `melange-ops.sh backup` straight after.
- **Volume full:** `df -h /srv/data`. The updater already keeps only current + previous releases.
  Check `sudo du -xh --max-depth=2 /srv/data | sort -h | tail`. Grow the provider volume, then
  `sudo cryptsetup resize melange-data && sudo resize2fs /dev/mapper/melange-data`.
- **Monthly:** `sudo reboot` once a month for kernel updates (unattended-upgrades does not reboot).
  Afterwards check `findmnt /srv/data`, `dc ps` and that Healthchecks is green.

## 8. Quarterly restore drill (with a negative control)

Do this on the first of each quarter. It proves the backups restore **and** that a corrupted byte
is caught. Record the result in the brain.

**Part A: the data snapshot restore.**

```bash
export RESTIC_REPOSITORY=/srv/melange/push/restic RESTIC_PASSWORD_FILE=/etc/melange/restic.pass
sudo -E restic check --read-data-subset=5% --no-lock             # repository integrity (read-only)
sudo -E restic snapshots                                         # pick the deployed snapshot id
N=/var/lib/melange-update/processed; M=$(sudo ls -1 $N | tail -n1)
sudo mkdir -p /srv/data/.drill && cd /srv/data/.drill
P=$(sudo jq -r .data_snapshot.restic_path $N/$M/manifest.json)   # where the farm sits inside the snapshot, e.g. /C/Users/.../farm
sudo -E restic restore <snapshot-id>:$P --target . --no-lock --include '/terrier/co-05'   # one small subset
sudo jq -r '.files[] | select(.path|startswith("terrier/co-05/")) | "\(.sha256)  \(.path)"' $N/$M/snapshot_manifest.json \
  | sudo sha256sum -c -                                          # expect every line: OK
```

(`restic_path` is in the signed `manifest.json` next to the snapshot manifest in `processed/`; the farm is not the snapshot root, restic keeps its absolute path.)

**Part B: negative control.** Corrupt one byte of one restored file and confirm the same check fails.

```bash
F=$(sudo find /srv/data/.drill/terrier/co-05 -type f | head -n1)
sudo cp "$F" /root/.drill-orig && printf '\x00' | sudo dd of="$F" bs=1 seek=100 conv=notrunc
# re-run the sha256sum -c pipeline above: it MUST report FAILED for that file
sudo cp /root/.drill-orig "$F"                                   # then clean up
sudo rm -rf /srv/data/.drill /root/.drill-orig
```

If Part B does **not** fail, the check is blind: stop and investigate before trusting any restore.
The same property is covered in code by `ops/tests/test_melange_update.py::test_corrupted_byte_fails_restore_before_flip`;
the drill proves it with the real repository and real tools.

**Part C: the Terrier backup.** Pull the newest `.age` from `outbox` to the laptop (the normal
nightly pull), decrypt with the age private key, and check it:

```powershell
age -d -i $env:LOCALAPPDATA\Melange\secrets\age.key co05-2026-10-12.age > co05.sqlite3
sqlite3 co05.sqlite3 "PRAGMA integrity_check; select count(*) from auth_user;"   # ok, then a plausible count
```

Restore it into a **scratch** Terrier copy with `python manage.py backup co05.sqlite3 --restore`
(app stopped) and compare a few walk lists to the server. Never test-restore on the live server.

## 9. Routine checks

- **Weekly:** `release status` on the laptop (GitHub, VPS `/version` for all five services, Netlify,
  Pocket, launcher drift); Cloudflare Access config diff; `systemctl --failed` on the VPS.
- **Alerts that should page you:** Healthchecks missing/failed ping; a new email in Access logs; a
  non-US Terrier login; repeated denials; a stray tunnel connector.
- **After any reboot or provider maintenance:** `findmnt /srv/data`, `dc ps`, the tunnel shows one
  connector, `jq .state /srv/melange/push/status/status.json` is `OK`.

## 10. Restoring Terrier field results after data loss

Use only if `accounts.sqlite3` is lost or corrupt. It **overwrites** the district's live database
with the backup, so field results recorded since that backup are lost: first take a fresh backup of
whatever is there if the container still runs.

```bash
sudo /usr/local/lib/melange/melange-ops.sh backup                 # if the app is still readable
# decrypt the chosen .age on the laptop, copy the .sqlite3 to the server (0644):
sudo install -d -m 0755 /srv/data/.restore && sudo install -m 0644 co05.sqlite3 /srv/data/.restore/
dc stop terrier-co05
dc run --rm --no-deps -v /srv/data/.restore:/restore:ro terrier-co05 \
  python manage.py backup /restore/co05.sqlite3 --restore
dc up -d --wait terrier-co05
sudo rm -rf /srv/data/.restore
```

Then confirm the district lead can see their walk lists, and note the gap in recorded results.

### After a rolled-back release that changed the Terrier image

The new image may have migrated `accounts.sqlite3` when it started, and the previous image may not read the newer schema
(the status reason says `DB schema may be ahead`). Before every release that changes the Terrier image the updater copies each
running district's DB to `/state/pre-<release-id>.sqlite3` in its state volume (Terrier's own `manage.py backup`); if that copy
fails the release is refused with nothing changed. If a district misbehaves after the rollback, put the copy back (it discards
only what was recorded in the minutes since the flip):

```bash
dc stop terrier-co05
dc run --rm --no-deps terrier-co05 python manage.py backup /state/pre-<release-id>.sqlite3 --restore
dc up -d --wait terrier-co05
```

Once a release has settled, delete the copies: `dc exec -T terrier-co05 sh -c 'rm -f /state/pre-*.sqlite3'` (each district).
