#!/bin/bash
# melange-bootstrap: idempotent first-boot setup. Safe to re-run after editing
# /etc/melange/bootstrap.env. Stages: harden -> luks -> docker -> payload. A stage that lacks
# a value Paul must set is skipped and listed under PENDING at the end. Nothing is formatted,
# overwritten or started on the unencrypted boot disk: Docker's data-root is on the LUKS
# volume, so no volume means no Docker.
set -uo pipefail
# One run at a time: cloud-init's runcmd and an admin's `sudo melange-bootstrap` must not race over the same volume.
exec 9>/run/melange-bootstrap.lock
flock -n 9 || { echo "melange-bootstrap: another run is in progress" >&2; exit 1; }
exec > >(tee -a /var/log/melange-bootstrap.log) 2>&1
# shellcheck disable=SC1091
. /etc/melange/bootstrap.env
PENDING=()
KEY=/etc/cryptsetup-keys.d/melange-data.key
say() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
pending() { PENDING+=("$*"); say "PENDING: $*"; }
unset_var() { case "${!1:-}" in ""|*PASTE*) return 0 ;; *) return 1 ;; esac; }

harden() {
  say "stage harden"
  install -d -m 0755 /etc/melange /srv/serve /srv/access /usr/local/lib/melange /opt/melange
  sysctl --system >/dev/null
  # Ubuntu 24.04 starts sshd from ssh.socket, which ignores ListenAddress: use the service.
  systemctl disable --now ssh.socket >/dev/null 2>&1 || true
  if sshd -t; then
    systemctl enable ssh.service >/dev/null 2>&1
    systemctl restart ssh.service
  else
    pending "sshd -t failed; ssh left as it was"
  fi
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow in on br-egress to 172.29.0.1 port 22 proto tcp comment 'cloudflared to sshd' >/dev/null
  ufw --force enable >/dev/null
  augenrules --load >/dev/null 2>&1 || pending "auditd rules did not load"
  systemctl restart systemd-journald
}

luks() {
  say "stage luks"
  # Every run, mounted or not: a key file that no longer opens the volume means the data is gone at the next reboot.
  if ! unset_var DATA_DEVICE && [ -b "$DATA_DEVICE" ] && cryptsetup isLuks "$DATA_DEVICE" 2>/dev/null \
     && ! cryptsetup open --test-passphrase --key-file "$KEY" "$DATA_DEVICE" 2>/dev/null; then
    pending "$KEY does not unlock $DATA_DEVICE: the volume will not open after a reboot (RUNBOOK section 7)"
  fi
  if mountpoint -q /srv/data; then say "/srv/data is already mounted"; return; fi
  if unset_var DATA_DEVICE; then pending "set DATA_DEVICE in /etc/melange/bootstrap.env (no data volume means no Docker)"; return; fi
  [ -b "$DATA_DEVICE" ] || { pending "DATA_DEVICE $DATA_DEVICE is not a block device"; return; }
  # Guard rails: never format anything that already holds something.
  if cryptsetup isLuks "$DATA_DEVICE" 2>/dev/null; then
    pending "$DATA_DEVICE already has a LUKS header; not touching it (open it by hand if it is yours)"; return
  fi
  if [ -n "$(wipefs --no-act "$DATA_DEVICE" 2>/dev/null)" ]; then
    pending "$DATA_DEVICE already carries a signature (wipefs -n); not formatting"; return
  fi
  if lsblk -nro MOUNTPOINT "$DATA_DEVICE" | grep -q .; then
    pending "$DATA_DEVICE or a partition of it is mounted; not formatting"; return
  fi
  say "formatting $DATA_DEVICE as LUKS2 (it was empty)"
  install -d -m 0755 /etc/cryptsetup-keys.d
  if [ ! -e "$KEY" ]; then (umask 077; head -c 4096 /dev/urandom > "$KEY"); fi
  chown root:root "$KEY"; chmod 0400 "$KEY"
  cryptsetup luksFormat --type luks2 --batch-mode --key-file "$KEY" "$DATA_DEVICE" || { pending "luksFormat failed"; return; }
  cryptsetup open --key-file "$KEY" "$DATA_DEVICE" melange-data || { pending "cryptsetup open failed"; return; }
  mkfs.ext4 -q -L melange-data /dev/mapper/melange-data || { pending "mkfs failed"; return; }
  local uuid; uuid=$(blkid -s UUID -o value "$DATA_DEVICE")
  grep -q '^melange-data ' /etc/crypttab 2>/dev/null || echo "melange-data UUID=$uuid $KEY luks,nofail" >> /etc/crypttab
  grep -q ' /srv/data ' /etc/fstab || echo '/dev/mapper/melange-data /srv/data ext4 defaults,nofail,noatime 0 2' >> /etc/fstab
  install -d -m 0755 /srv/data
  mount /srv/data || { pending "mount /srv/data failed"; return; }
  chmod 0755 /srv/data
  say "LUKS volume mounted at /srv/data; back up its header (ops/VPS.md)"
}

docker_stage() {
  say "stage docker"
  mountpoint -q /srv/data || { pending "docker: /srv/data is not mounted (luks stage incomplete)"; return; }
  systemctl daemon-reload
  install -d -m 0711 /srv/data/docker
  if ! dpkg -s docker-ce >/dev/null 2>&1; then
    # Repo first, so the log can list the versions to pin before anything is installed.
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc || { pending "could not fetch Docker's apt key"; return; }
    local fpr; fpr=$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc | awk -F: '/^fpr:/ {print $10; exit}')
    # Docker's published apt key fingerprint; verify against docs.docker.com (ops/VPS.md).
    if [ "$fpr" != 9DC858229FC7DD38854AE2D88D81803C0EBFCD88 ]; then
      pending "Docker apt key fingerprint mismatch ($fpr); NOT installing"; rm -f /etc/apt/keyrings/docker.asc; return
    fi
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release; echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    local v
    for v in DOCKER_VERSION CONTAINERD_VERSION COMPOSE_VERSION; do
      if unset_var "$v"; then
        apt-cache madison docker-ce containerd.io docker-compose-plugin | head -n 12
        pending "set $v in /etc/melange/bootstrap.env (versions listed above)"; return
      fi
    done
    apt-get install -y -qq --no-install-recommends "docker-ce=$DOCKER_VERSION" "docker-ce-cli=$DOCKER_VERSION" \
      "containerd.io=$CONTAINERD_VERSION" "docker-compose-plugin=$COMPOSE_VERSION" || { pending "docker install failed (check the pinned versions)"; return; }
    apt-mark hold docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
  fi
  systemctl enable --now docker || { pending "docker did not start"; return; }
  # External volumes: `compose down -v` cannot remove Terrier's field results.
  local d
  for d in co05 or05 ct01; do
    docker volume inspect "terrier-$d-state" >/dev/null 2>&1 && continue
    # Docker does not list it, but a directory of that name is on disk (lost metadata?): `volume create` would adopt or hide
    # the field results behind it. Look first.
    if compgen -G "/srv/data/docker/*/volumes/terrier-$d-state" >/dev/null || [ -e "/srv/data/docker/volumes/terrier-$d-state" ]; then
      pending "terrier-$d-state is on disk under /srv/data/docker but Docker does not list it; NOT creating a volume (RUNBOOK section 7)"
      continue
    fi
    docker volume create "terrier-$d-state" >/dev/null
  done
}

payload_stage() {
  say "stage payload"
  mountpoint -q /srv/data || { pending "payload: /srv/data is not mounted"; return; }
  [ -s /usr/local/lib/melange/melange_update.py ] || { pending "payload not extracted (tar -C / -xJf /opt/melange/payload.tar.xz)"; return; }
  install -d -m 0700 /srv/serve/secrets
  [ -e /srv/serve/compose.env ] || install -m 0600 /srv/serve/compose.env.example /srv/serve/compose.env
  local d s
  for d in co05 or05 ct01; do
    if [ ! -s "/srv/serve/secrets/terrier-$d.env" ]; then
      (umask 077; printf 'CU_SECRET_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')" > "/srv/serve/secrets/terrier-$d.env")
    fi
  done
  [ -e /srv/serve/secrets/tunnel.env ] || (umask 077; echo 'TUNNEL_TOKEN=PASTE-TUNNEL-TOKEN' > /srv/serve/secrets/tunnel.env)
  for s in melange mentat terrier; do install -d -m 0755 "/srv/data/$s"; done
  # The sftp chroot lives on the encrypted volume (bind mount), root-owned all the way down.
  install -d -m 0755 -o root -g root /srv/data/.push /srv/melange /srv/melange/push
  grep -q ' /srv/melange/push ' /etc/fstab || echo '/srv/data/.push /srv/melange/push none bind,nofail,x-systemd.requires-mounts-for=/srv/data 0 0' >> /etc/fstab
  systemctl daemon-reload
  mountpoint -q /srv/melange/push || mount /srv/melange/push
  for s in inbox restic; do install -d -o melange-push -g melange-push -m 0750 "/srv/melange/push/$s"; done
  install -d -o melange-push -g melange-push -m 0755 /srv/melange/push/outbox   # the laptop pulls and deletes here
  # root writes status.json and the backups' staging: neither may sit in a directory the push user can plant links in
  install -d -o root -g root -m 0755 /srv/melange/push/status
  install -d -o root -g root -m 0700 /srv/data/.backup-tmp
  systemctl enable --now melange-update.path melange-update.timer access-jwks.timer terrier-backup.timer healthchecks.timer
}

summary() {
  local f
  # Comments may mention PASTE; only an uncommented value that still says PASTE is unfinished.
  for f in /etc/melange/bootstrap.env /etc/melange/allowed_signers /srv/serve/compose.env /srv/serve/secrets/tunnel.env; do
    if [ -e "$f" ] && grep -v '^[[:space:]]*#' "$f" | grep -q PASTE; then pending "fill the PASTE values in $f"; fi
  done
  grep -qE '^age1' /etc/melange/age_recipients 2>/dev/null || pending "paste an age1... recipient into /etc/melange/age_recipients"
  grep -qE '^ssh-' /etc/ssh/authorized_keys/melange-push 2>/dev/null || pending "paste the laptop push key into /etc/ssh/authorized_keys/melange-push"
  getent shadow admin | cut -d: -f2 | grep -qE '^\$[0-9a-z]+\$' || pending "admin has no valid password hash; set one for the console login (passwd admin)"
  [ -s /etc/melange/restic.pass ] || pending "create /etc/melange/restic.pass (root, 0400): the restic repository password"
  [ -s /etc/melange/healthchecks.url ] || pending "create /etc/melange/healthchecks.url (root, 0600): the Healthchecks.io ping URL"
  jq -e '(.auths["ghcr.io"] // .credHelpers["ghcr.io"]) != null' /root/.docker/config.json >/dev/null 2>&1 \
    || pending "docker login ghcr.io (as root, with the read:packages token)"
  if [ "${#PENDING[@]}" -eq 0 ]; then say "bootstrap complete"; else say "bootstrap finished with ${#PENDING[@]} pending item(s); see ops/VPS.md"; fi
}

harden
luks
docker_stage
payload_stage
summary
