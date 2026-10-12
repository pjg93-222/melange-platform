#!/bin/bash
# melange-ops.sh jwks|backup|ping  --  small host jobs run by systemd timers (as root).
#   jwks    refresh /srv/access/jwks.json from Cloudflare Access (atomic, validated)
#   backup  nightly: Terrier accounts DB -> age-encrypted file in the sftp outbox (laptop pulls it)
#   ping    Healthchecks.io dead-man switch; /fail when the VPS is not actually healthy (release, warnings, tunnel, JWKS, backups)
set -euo pipefail
umask 022
PUSH=/srv/melange/push
# Root writes into the outbox, which the push user owns: stage in a root-only directory on the same filesystem, then
# rename into place. The /srv/data path, not the chroot bind mount at $PUSH: a rename across mounts is EXDEV.
OUTBOX=/srv/data/.push/outbox
STAGE=/srv/data/.backup-tmp
dc() { docker compose -p melange -f /srv/serve/compose.yaml --env-file /srv/serve/compose.env "$@"; }

jwks() {
  : "${TEAM:?TEAM is missing from /srv/serve/compose.env}"
  # Global on purpose: an EXIT trap runs after the function returns, when a `local` is gone.
  JWKS_TMP=$(mktemp /srv/access/.jwks.XXXXXX)
  trap 'rm -f "$JWKS_TMP"' EXIT
  curl --fail --silent --show-error --max-time 30 --proto '=https' --tlsv1.2 \
    "https://${TEAM}.cloudflareaccess.com/cdn-cgi/access/certs" -o "$JWKS_TMP"
  jq -e '(.keys | type == "array") and (.keys | length > 0)' "$JWKS_TMP" >/dev/null   # never replace good keys with junk
  chmod 0644 "$JWKS_TMP"                                                              # apps run as a remapped uid
  mv -f "$JWKS_TMP" /srv/access/jwks.json                                             # atomic: same directory
}

# Terrier's own `manage.py backup` (SQLite backup API after integrity_check) into the container's
# tmpfs, streamed out through age, plaintext removed from the container whatever happens.
# The state volume is only read. Output appears under its final name only if every step worked.
backup_one() {
  local svc="terrier-$1" out="$2" tmp="$STAGE/$1.partial" rc=0
  dc exec -T "$svc" python manage.py backup /tmp/accounts-backup.sqlite3 >/dev/null || rc=1
  if [ "$rc" = 0 ]; then
    { dc exec -T "$svc" cat /tmp/accounts-backup.sqlite3 | age -R /etc/melange/age_recipients -o "$tmp"; } || rc=1
  fi
  dc exec -T "$svc" rm -f /tmp/accounts-backup.sqlite3 >/dev/null 2>&1 || true
  if [ "$rc" = 0 ] && [ -s "$tmp" ]; then
    chmod 0644 "$tmp"   # ciphertext only; the chrooted push user must be able to read and delete it
    mv -fT "$tmp" "$out"   # rename(2) replaces a link the push user planted at $out; it never follows it
  else
    rm -f "$tmp"
    return 1
  fi
}

backup() {
  local day d rc=0
  day=$(date -u +%F)
  grep -qE '^age1' /etc/melange/age_recipients 2>/dev/null \
    || { echo "melange-ops: no age1... recipient in /etc/melange/age_recipients" >&2; exit 1; }
  install -d -m 0700 -o root -g root "$STAGE"
  for d in co05 or05 ct01; do
    backup_one "$d" "$OUTBOX/$d-$day.age" || { echo "melange-ops: backup of $d FAILED" >&2; rc=1; }
  done
  # the laptop pulls and deletes; this only caps a laptop that has been away for two weeks
  find "$OUTBOX" -name '*.age' -mtime +14 -delete
  exit "$rc"
}

ping() {
  local f=/etc/melange/healthchecks.url url why="" state
  [ -s "$f" ] || { echo "melange-ops: $f not set; heartbeat disabled"; exit 0; }
  url=$(head -n1 "$f")
  state=$(jq -r '.state // "missing"' "$PUSH/status/status.json" 2>/dev/null || echo missing)
  [ "$state" = OK ] || why="$why release state is $state;"
  # warnings (cloudflared did not start...) stay in status.json until the next release rewrites it
  [ "$(jq -r '(.warnings // []) | length' "$PUSH/status/status.json" 2>/dev/null || echo 0)" = 0 ] || why="$why status.json has warnings;"
  # the tunnel is the only way in: a dead or unhealthy cloudflared is an outage even when every app is fine
  cid=$(dc ps -q cloudflared 2>/dev/null | head -n1 || true)
  [ -n "$cid" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)" = healthy ] || why="$why cloudflared not healthy;"
  [ -n "$(find /srv/access/jwks.json -mmin -60 2>/dev/null)" ] || why="$why JWKS older than 1 h or missing;"
  # Terrier field data: an older newest backup means the nightly job is failing
  [ -n "$(find "$PUSH/outbox" -name '*.age' -mmin -2160 2>/dev/null | head -n1)" ] || why="$why no Terrier backup in 36 h;"
  if [ -z "$why" ]; then
    curl --fail --silent --max-time 10 --retry 3 --data-raw "ok" "$url" >/dev/null
  else
    curl --fail --silent --max-time 10 --retry 3 --data-raw "$why" "$url/fail" >/dev/null
  fi
}

case "${1:-}" in
  jwks) jwks ;;
  backup) backup ;;
  ping) ping ;;
  *) echo "usage: melange-ops.sh jwks|backup|ping" >&2; exit 2 ;;
esac
