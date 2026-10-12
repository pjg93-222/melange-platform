#!/usr/bin/env bash
# install.sh -- stand up a Melange serve host from this kit (Ubuntu 24.04, run as root).
#
# Brings the apps up from a SIGNED release manifest. Data is not in the kit: it arrives the
# normal way (laptop push -> melange-update.path), and the apps start once it has.
#
#   sudo ./install.sh --manifest manifest.json     # manifest.json.sig must sit beside it
#
# Needs beforehand: docker + compose plugin, jq, openssh-client, and /etc/melange/allowed_signers
# (the release public key, pasted by Paul). cloud-init (ops/vps/cloud-init.yaml) does all of that
# for a fresh server; this script is for an existing host or a second server.
set -euo pipefail

KIT=$(cd "$(dirname "$0")" && pwd)
MANIFEST=""
ALLOWED=${ALLOWED_SIGNERS:-/etc/melange/allowed_signers}
SERVE=/srv/serve
die() { echo "install.sh: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --manifest) MANIFEST=${2:?--manifest needs a path}; shift 2 ;;
    *) die "usage: install.sh --manifest manifest.json" ;;
  esac
done
[ -n "$MANIFEST" ] || die "usage: install.sh --manifest manifest.json"

[ "$(id -u)" = 0 ] || die "run as root"
for c in docker jq ssh-keygen python3; do command -v "$c" >/dev/null || die "missing prerequisite: $c"; done
docker compose version >/dev/null 2>&1 || die "docker compose plugin is missing"
[ -s "$ALLOWED" ] && ! grep -v '^[[:space:]]*#' "$ALLOWED" | grep -q PASTE \
  || die "$ALLOWED must hold the release public key (no PASTE placeholder)"
[ -s "$MANIFEST" ] && [ -s "$MANIFEST.sig" ] || die "$MANIFEST and $MANIFEST.sig are both required"
# Docker's data-root, the release data and the Terrier volumes live on the encrypted volume (ops/VPS.md): never the boot disk.
mountpoint -q /srv/data || die "/srv/data is not a mounted volume; mount the encrypted data volume first"

# Verify and use the SAME bytes: a private copy, so the file cannot change between the check and the read.
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
cp "$MANIFEST" "$WORK/manifest.json"; cp "$MANIFEST.sig" "$WORK/manifest.json.sig"
MANIFEST="$WORK/manifest.json"

echo "== verifying manifest signature"
ssh-keygen -Y verify -f "$ALLOWED" -I release@melange -n melange-release -s "$MANIFEST.sig" < "$MANIFEST" \
  || die "manifest signature is NOT valid; refusing to continue"
[ "$(jq -r .schema "$MANIFEST")" = melange-platform-release-v1 ] || die "unexpected manifest schema"
# The updater's own checks (release id, image digests, subdirs, snapshot block), so a value that would be refused there never
# reaches compose.env.
PYTHONPATH="$KIT/vps" python3 -c '
import json, sys, melange_update as m
try:
    m.check_manifest(json.load(open(sys.argv[1])))
except m.StepError as e:
    sys.exit("install.sh: manifest refused: %s" % e)
' "$MANIFEST"

echo "== installing files"
install -d -m 0755 /usr/local/lib/melange /etc/melange "$SERVE" /srv/access /srv/data/melange /srv/data/mentat /srv/data/terrier
install -d -m 0700 "$SERVE/secrets"
install -m 0755 "$KIT/vps/melange_update.py" "$KIT/vps/melange-ops.sh" /usr/local/lib/melange/
install -m 0644 "$KIT/vps/systemd/"* /etc/systemd/system/
install -m 0644 "$KIT/compose.yaml" "$KIT/compose.env.example" "$SERVE/"
[ -e "$SERVE/compose.env" ] || install -m 0600 "$KIT/compose.env.example" "$SERVE/compose.env"
for d in co05 or05 ct01; do
  [ -s "$SERVE/secrets/terrier-$d.env" ] || (umask 077; printf 'CU_SECRET_KEY=%s\n' \
    "$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')" > "$SERVE/secrets/terrier-$d.env")
  if ! docker volume inspect "terrier-$d-state" >/dev/null 2>&1; then
    # on disk but not listed (lost metadata?): `volume create` would adopt or hide the field results behind it
    if compgen -G "/srv/data/docker/*/volumes/terrier-$d-state" >/dev/null || [ -e "/srv/data/docker/volumes/terrier-$d-state" ]; then
      die "terrier-$d-state is on disk under /srv/data/docker but Docker does not list it; not creating a volume (RUNBOOK section 7)"
    fi
    docker volume create "terrier-$d-state" >/dev/null
  fi
done
[ -e "$SERVE/secrets/tunnel.env" ] || (umask 077; echo 'TUNNEL_TOKEN=PASTE-TUNNEL-TOKEN' > "$SERVE/secrets/tunnel.env")
systemctl daemon-reload

echo "== writing compose.env from the manifest (operator values kept)"
! grep -v '^[[:space:]]*#' "$SERVE/compose.env" | grep -q PASTE || die "fill in the PASTE values in $SERVE/compose.env, then re-run"
managed=$(jq -r '
  def img($s): .systems[$s].image // empty;
  [ "RELEASE_ID=" + .release_id,
    (img("melange") | "IMAGE_MELANGE_DESK=" + .),
    (img("mentat")  | "IMAGE_MENTAT_CONSOLE=" + .),
    (img("terrier") | ("IMAGE_TERRIER_CO05=" + ., "IMAGE_TERRIER_OR05=" + ., "IMAGE_TERRIER_CT01=" + .)) ] | .[]' "$MANIFEST" | sort)
tmp=$(mktemp "$SERVE/.compose.env.XXXXXX")
grep -vE '^(IMAGE_(MELANGE_DESK|MENTAT_CONSOLE|TERRIER_CO05|TERRIER_OR05|TERRIER_CT01)|RELEASE_ID)=|^# --- managed by melange_update' \
  "$SERVE/compose.env" > "$tmp" || true
{ echo; echo "# --- managed by melange_update (do not edit below) ---"; echo "$managed"; } >> "$tmp"
chmod 0600 "$tmp"; mv -f "$tmp" "$SERVE/compose.env"

dc() { docker compose -p melange -f "$SERVE/compose.yaml" --env-file "$SERVE/compose.env" "$@"; }

if ! jq -e '(.auths["ghcr.io"] // .credHelpers["ghcr.io"]) != null' "${DOCKER_CONFIG:-$HOME/.docker}/config.json" >/dev/null 2>&1; then
  echo "== docker login ghcr.io (read:packages token; it is prompted, never stored in this script)"
  read -r -p "GitHub user: " ghuser
  docker login ghcr.io -u "$ghuser"
fi

apps=$(jq -r '[.systems | keys[] | {melange:["melange-desk"], mentat:["mentat-console"],
        terrier:["terrier-co05","terrier-or05","terrier-ct01"]}[.][]] | .[]' "$MANIFEST")
echo "== pulling the signed digests"
# shellcheck disable=SC2086
dc pull --quiet $apps

if [ -e /srv/data/melange/current ] && [ -e /srv/data/mentat/current ] && [ -e /srv/data/terrier/current ]; then
  echo "== starting"
  # shellcheck disable=SC2086
  dc up -d --wait $apps
  dc up -d cloudflared
else
  echo "== no data yet: not starting the apps (their data mounts do not exist)."
  echo "   Push a snapshot from the laptop (release run); melange-update.path deploys it and starts them."
fi
systemctl enable --now melange-update.path melange-update.timer access-jwks.timer terrier-backup.timer healthchecks.timer
echo "done. Next: ops/VPS.md, section 'After first boot'."
