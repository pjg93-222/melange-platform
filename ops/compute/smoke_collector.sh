#!/bin/sh
# Smoke tests for the compute-collector image. CI runs this against the pushed digest (.github/workflows/compute-collector.yml); the
# laptop wrapper runs it once after pulling a digest. Every container is hardened like the compose jobs and has NO network.
#   usage: smoke_collector.sh IMAGE@sha256:<digest> EXPECTED_PYTHON_VERSION EXPECTED_CLAUDE_VERSION
# Checks: Python and imports, the CP1252 default encoding, Chromium and Playwright launching headless, the claude CLI at the pinned
# version, a fake login staged from a secret into a private config dir, and NO credential anywhere in the image (files, env, secrets).
set -eu
image=${1:?usage: smoke_collector.sh IMAGE@sha256:DIGEST PYTHON_VERSION CLAUDE_VERSION}
want_python=${2:?}
want_claude=${3:?}
case "$image" in *@sha256:*) ;; *) echo "FAIL: image must be pinned by digest: $image" >&2; exit 2 ;; esac

hardened="--rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 --tmpfs /tmp:size=1g,mode=1777,nosuid --shm-size 1g"
run() { docker run $hardened "$image" "$@"; }
ok() { echo "ok: $1"; }
fail() { echo "FAIL: $1" >&2; exit 1; }

[ "$(run python -c 'import platform; print(platform.python_version())')" = "$want_python" ] || fail "python is not $want_python"
ok "python $want_python"
run python -c 'import json, sqlite3, ssl, openpyxl, pypdf' || fail "python imports (openpyxl, pypdf, sqlite3, ssl)"
ok "python imports"
[ "$(run python -c 'import locale; print(locale.getpreferredencoding(False).lower())')" = cp1252 ] || fail "default text encoding is not cp1252"
ok "cp1252 default encoding"
[ "$(run python -c 'import time; print(time.strftime("%Z"))')" != UTC ] || fail "TZ is UTC, expected America/Chicago"
ok "America/Chicago"

run chromium --version | grep -q '^Chromium ' || fail "chromium --version"
ok "$(run chromium --version)"
run chromium --headless=new --no-sandbox --disable-gpu --dump-dom about:blank | grep -q '<html' || fail "chromium headless dump-dom"
ok "chromium headless"
run node --version | grep -q '^v22\.' || fail "node is not 22.x"
run node -e "(async () => { const { chromium } = require('playwright'); const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true }); const p = await b.newPage(); await p.setContent('<p>pw-ok</p>'); console.log(await p.textContent('p')); await b.close(); })()" \
    | grep -qx 'pw-ok' || fail "playwright cannot drive the system chromium"
ok "playwright + chromium"

run claude --version | grep -qF "$want_claude" || fail "claude --version is not $want_claude"
ok "claude $want_claude"

# The entrypoint stages a login secret into a private config dir (and only there).
fake=$(mktemp)
trap 'rm -f "$fake"' EXIT
printf '{}' > "$fake"
chmod 0644 "$fake"
docker run $hardened -v "$fake:/run/secrets/claude_credentials:ro" "$image" \
    sh -c 'test "$(stat -c %a "$CLAUDE_CONFIG_DIR/.credentials.json")" = 600 && test "$(stat -c %a "$CLAUDE_CONFIG_DIR")" = 700' \
    || fail "login secret was not staged into a private CLAUDE_CONFIG_DIR"
ok "login staged privately"

# No credential baked in: files, environment variable names, and /run/secrets.
found=$(run find / -xdev \( -name '.credentials.json' -o -name '.claude.json' -o -name 'keys.json' -o -name '*_key.txt' -o -name '.env' \) 2>/dev/null || true)
[ -z "$found" ] || fail "credential-like files in the image: $found"
leaked=$(run sh -c 'env | cut -d= -f1' | grep -Ei 'key|token|secret|password|credential' | grep -vx 'GPG_KEY' || true)
[ -z "$leaked" ] || fail "credential-like environment variables in the image: $leaked"
[ -z "$(run sh -c 'ls -A /run/secrets 2>/dev/null' || true)" ] || fail "/run/secrets is not empty by default"
[ "$(run id -u)" = 10001 ] || fail "not running as uid 10001"
ok "no credentials in the image; uid 10001"
echo "smoke passed: $image"
