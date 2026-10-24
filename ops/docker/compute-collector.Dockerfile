# syntax=docker/dockerfile:1.7
# Compute-plane collector image (D-173 lane 3b, D-172, D-179, D-180, D-181): the Melange weekly Python collectors, the Playwright
# bill-discovery collector (Node + Chromium) and the claude-CLI classifier steps. No credential is in any layer: the CLI login and
# the API keys arrive at run time as read-only Docker secrets (ops/compute/collectors.compose.yaml) and the entrypoint stages the
# login into a tmpfs. Code and data are mounted, never baked (design principle 1); ops/compute/http_replay.py runs from the mounted tree.
# Build from the platform repo root (CI does: .github/workflows/compute-collector.yml):
#   docker build -f ops/docker/compute-collector.Dockerfile \
#     --build-arg NODE_BASE="$(jq -r '.node.ref + "@" + .node.digest' ops/images.lock)" \
#     --build-arg GIT_SHA=... --build-arg BUILD_TIME=... .
# Every ARG default below is a pin (a test keeps NODE_BASE equal to ops/images.lock), so a build that forgot an arg never floats.
#
# NOT built when this file was written (no Docker on the author's machine, no network): CI's first run is its first build.
#   Python:   CPython 3.12.10 exactly (D-172), the same way as the compute-melange image: python:3.12.10-slim-trixie does not exist
#             (registry 404, resolved 2026-10-12 by the compute-melange lane), so the base is debian:trixie-slim and CPython is the
#             python-build-standalone tarball, verified in the build with sha256sum -c. DEBIAN_BASE, PBS_RELEASE and PBS_SHA256 are the
#             values of ops/compute/melange-base.lock on branch compute/melange-chain; when both lanes are merged, one base lock should
#             own them (a test compares the two when both files exist).
#   Packages: ops/locks/compute-collector.linux.lock (openpyxl, et-xmlfile, pypdf: the third-party closure of the weekly collectors,
#             computed over the 53 modules reachable from the collector entry points). Hash-locked; the hashes are the ones the
#             compute-melange and mentat-console locks already carry for the same three pure-Python wheels. Install with
#             --require-hashes: a wrong hash fails the build.
#   Chromium: Debian's package from a dated snapshot.debian.org archive (DEBIAN_SNAPSHOT, TODO(Paul): ratify the date, like
#             PPM_SNAPSHOT_DATE in deps.yml); the version it lands on is printed by the CI smoke test.
#   claude:   the native linux-x64 binary from the official npm package @anthropic-ai/claude-code-linux-x64, at the exact version
#             the laptop runs (a test compares them). npm verifies the tarball's integrity and `npm audit signatures` checks the
#             registry signature. Pinning the tarball's sha512 would be stronger and needs the registry (not reachable here): after
#             the first CI build, record `npm view <pkg>@<version> dist.integrity` in this file's header.
#   R and audio: not here. The R collectors (press walk, Washington Journal) run in the Mentat/R image with http_replay.py's proxy mode;
#             yt-dlp, ffmpeg and ASR are manual tools and stay out (design 2.4, YAGNI).
ARG DEBIAN_BASE=debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
ARG NODE_BASE=node:22-trixie-slim@sha256:154ba2f4d6fec323d28e4f4bb86bba4677f1223391a1979cf521304e03a98dfa
ARG CPYTHON_VERSION=3.12.10
ARG PBS_RELEASE=20250409
ARG PBS_SHA256=8c59b9ac6bff2dc3934181d7bc82594f9f59a613afed8d72c9e89d7194e790ee

# Node side: the Playwright library (the browser is the system Chromium below) and the claude CLI's native linux-x64 binary,
# both from the npm registry at exact versions. --ignore-scripts: neither needs an install script (the CLI's postinstall only
# copies this same binary into its wrapper package).
FROM ${NODE_BASE} AS node-tools
ARG PLAYWRIGHT_VERSION=1.62.1
ARG CLAUDE_CODE_VERSION=2.1.286
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false
WORKDIR /opt/collector
RUN npm init -y >/dev/null \
 && npm install --save-exact --ignore-scripts --omit=dev "playwright@${PLAYWRIGHT_VERSION}" \
 && npm audit signatures
WORKDIR /opt/claude-src
RUN npm init -y >/dev/null \
 && npm install --save-exact --ignore-scripts --omit=dev "@anthropic-ai/claude-code-linux-x64@${CLAUDE_CODE_VERSION}" \
 && npm audit signatures \
 && install -D -m 0755 node_modules/@anthropic-ai/claude-code-linux-x64/claude /out/claude \
 && /out/claude --version | grep -F "${CLAUDE_CODE_VERSION}"

# CPython from the pinned release asset. curl and ca-certificates exist only in this stage (never shipped); the tarball, not the
# apt packages, is what the sha256 pins.
FROM ${DEBIAN_BASE} AS python
ARG CPYTHON_VERSION
ARG PBS_RELEASE
ARG PBS_SHA256
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    asset="cpython-${CPYTHON_VERSION}%2B${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"; \
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 -o /tmp/cpython.tar.gz \
      "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${asset}"; \
    echo "${PBS_SHA256}  /tmp/cpython.tar.gz" | sha256sum -c -; \
    mkdir /opt/python; \
    tar -xzf /tmp/cpython.tar.gz -C /opt/python --strip-components=1 --no-same-owner; \
    rm /tmp/cpython.tar.gz
RUN /opt/python/bin/python3 -c "import sys; v = '.'.join(map(str, sys.version_info[:3])); assert v == '${CPYTHON_VERSION}', v"

# Hashed wheels only: a failed install fails the build. pip leaves the venv and the base interpreter, so nothing can install at runtime.
FROM python AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY ops/locks/compute-collector.linux.lock /tmp/compute-collector.linux.lock
RUN /opt/python/bin/python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/compute-collector.linux.lock
RUN /opt/venv/bin/python -m pip uninstall -y pip
RUN find /opt/python/lib/python3.12/site-packages -mindepth 1 -maxdepth 1 \
      \( -name 'pip' -o -name 'pip-*' -o -name 'setuptools*' -o -name 'wheel*' -o -name 'pkg_resources' -o -name '_distutils_hack' \) \
      -exec rm -rf {} + \
 && rm -f /opt/python/bin/pip /opt/python/bin/pip3 /opt/python/bin/pip3.12

FROM ${DEBIAN_BASE}
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
# 2026-10-13: 20261011T000000Z -> 20261013T000000Z, the same bump as ops/docker/compute-mentat.Dockerfile: the 20261011 snapshot holds
# chromium 154.0.8037.92-1~deb13u1, which has fixable CRITICAL and HIGH findings at the images gate (D-181); 20261013 holds 155.0.8059.39-1~deb13u1.
# 20261013T000000Z -> 20261017T000000Z, the same bump as ops/docker/compute-mentat.Dockerfile (D-181): the 20261013 snapshot holds chromium
# 155.0.8059.39-1~deb13u1, libcurl 8.14.1-2+deb13u5 and libexpat 2.8.3-1~deb13u1, all with fixable findings at the images gate; 20261017 holds
# chromium 155.0.8059.79-1~deb13u1 (DSA-6562-1), libcurl 8.14.1-2+deb13u7 (DSA-6558-1) and libexpat 2.9.0-1~deb13u1 (DSA-6566-1).
# 20261017T000000Z -> 20261022T170820Z, the same bump as ops/docker/compute-mentat.Dockerfile (D-181): the 2026.10.22.1 images gate failed this
# image on 120 fixable HIGH chromium findings in 155.0.8059.79-1~deb13u1; 20261022T170820Z is the first snapshot holding 156.0.8078.47-1~deb13u1.
# 20261022T170820Z -> 20261023T210712Z, the same bump as ops/docker/compute-mentat.Dockerfile (D-181): the 2026.10.22.3 images gate failed this
# image on CVE-2026-111497 (HIGH) in libbrotli1 1.1.0-2+b7; 20261023T210712Z is the first snapshot holding brotli 1.1.0-2+deb13u1 (DSA-6582-1).
# Chromium stays at 156.0.8078.47-1~deb13u1; of the installed packages only libbrotli1 and tzdata (2026c -> 2026e) move.
ARG DEBIAN_SNAPSHOT=20261023T210712Z
LABEL org.opencontainers.image.title="compute-collector" \
      org.opencontainers.image.description="Melange collectors: CPython 3.12.10, Node/Playwright, Chromium, claude CLI (no credentials)" \
      org.opencontainers.image.source="https://github.com/pjg93-222/melange-platform" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}"

# Chromium, CA roots, tzdata and a CP1252 locale from the dated archive. The sed is guarded: if the base's sources file ever stops
# naming deb.debian.org the grep fails the build instead of installing from a moving mirror. http on purpose, as in the other compute
# Dockerfiles: the slim base has no ca-certificates until this step installs it, so https fails certificate verification (found by the
# first build, 2026-10-13), and apt checks the signed Release files either way. `localedef -c` exits 1 on charmap
# warnings (as locale-gen does); anything else fails the build, and the assertions below fail it if the locale is not CP1252. The
# output is named by its path: glibc 2.41-12+deb13u4 writes a bare `en_US.CP1252` name to /usr/lib/locale/en_US.cp1252 (codeset lower-cased).
RUN set -eux; \
    sed -i -e "s#https\?://deb.debian.org/debian-security#http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}#" \
           -e "s#https\?://deb.debian.org/debian#http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}#" \
           /etc/apt/sources.list.d/debian.sources; \
    grep -q "snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}" /etc/apt/sources.list.d/debian.sources; \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates tzdata locales chromium fonts-liberation; \
    localedef -c -i en_US -f CP1252 --no-archive /usr/lib/locale/en_US.CP1252 || [ "$?" -eq 1 ]; \
    test -s /usr/lib/locale/en_US.CP1252/LC_CTYPE; \
    ln -s /usr/bin/chromium /usr/bin/google-chrome; \
    rm -rf /var/lib/apt/lists/*
# ^ /usr/bin/google-chrome is where fec/collect/collect_congress_advanced_pages.mjs looks on Linux (DEFAULT_CHROME).

# /profile is the mount point of the compose file's collector-profile volume: it exists here, owned by the job user, so the
# volume (and the read-only root) leaves it writable by uid 10001.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && install -d -o 10001 -g 10001 -m 0700 /profile

COPY --from=deps /opt/python /opt/python
COPY --from=deps /opt/venv /opt/venv
COPY --from=node-tools /usr/local/bin/node /usr/local/bin/node
COPY --from=node-tools /opt/collector/node_modules /opt/collector/node_modules
COPY --from=node-tools /out/claude /usr/local/bin/claude
COPY --chmod=0755 ops/compute/collector-entrypoint.sh /usr/local/bin/collector-entrypoint

# Parity levers of the Windows weekly task (design 2.0): Central time, CP1252 default text encoding, the host's PYTHONIOENCODING.
# The claude CLI: no self-update and no non-essential traffic; its login directory is CLAUDE_CONFIG_DIR, staged by the entrypoint.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX=/tmp/pyc PATH=/opt/venv/bin:$PATH \
    GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME} \
    TZ=America/Chicago LANG=en_US.CP1252 LC_ALL=en_US.CP1252 PYTHONIOENCODING=utf-8:surrogateescape \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    NODE_PATH=/opt/collector/node_modules HOME=/tmp/home \
    MELANGE_CLASSIFIER_COMMAND=/usr/local/bin/claude CLAUDE_CONFIG_DIR=/tmp/claude \
    DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

# Build-time assertions: a wrong interpreter, locale or zone fails the build instead of shipping.
RUN python -c "import sys, locale, importlib.util; assert sys.version_info[:3] == (3, 12, 10), sys.version; assert sys.flags.utf8_mode == 0; assert locale.getpreferredencoding(False).lower() in ('cp1252', 'windows-1252'); assert importlib.util.find_spec('pip') is None; import openpyxl, pypdf, ssl"
RUN python -c "import time; assert time.tzname == ('CST', 'CDT'), time.tzname"

USER 10001:10001
ENTRYPOINT ["/usr/local/bin/collector-entrypoint"]
CMD ["python", "--version"]
