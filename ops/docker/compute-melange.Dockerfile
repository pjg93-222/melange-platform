# syntax=docker/dockerfile:1.7
# Melange chain compute image (compute lane MEL: D-172 CPython 3.12.10 exactly, D-173/D-175 built in CI and run on the laptop,
# D-181 Debian 13). It runs `python /melange/fec/run_pipeline.py` with the code and the data MOUNTED: nothing of fec/ or of the
# data is copied in, so the container runs the tree the Windows job runs. Build from the platform repo root:
#   docker build -f ops/docker/compute-melange.Dockerfile \
#     --build-arg DEBIAN_BASE="$(jq -r '.debian.ref + "@" + .debian.digest' ops/compute/melange-base.lock)" \
#     --build-arg CPYTHON_VERSION=... --build-arg PBS_RELEASE=... --build-arg PBS_SHA256=... \
#     --build-arg GIT_SHA=... --build-arg BUILD_TIME=... .
# python:3.12.10-slim-trixie does not exist (see _note in ops/compute/melange-base.lock), so the base is debian:trixie-slim and
# CPython 3.12.10 is the python-build-standalone tarball, verified with sha256sum -c. The ARG defaults below are the values in
# ops/compute/melange-base.lock (a test keeps them equal), so a build that forgot a build arg never floats on a tag.
ARG DEBIAN_BASE=debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
ARG CPYTHON_VERSION=3.12.10
ARG PBS_RELEASE=20250409
ARG PBS_SHA256=8c59b9ac6bff2dc3934181d7bc82594f9f59a613afed8d72c9e89d7194e790ee

# --- CPython 3.12.10 from the pinned release asset. curl and ca-certificates exist only in this stage (never shipped); the
# tarball, not the apt packages, is what the sha256 pins.
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

# --- /opt/engine: the hashed lock, wheels only. A failed install fails the build; nothing is swallowed.
FROM python AS engine
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY ops/locks/compute-melange.linux.txt /tmp/compute-melange.linux.txt
RUN /opt/python/bin/python3 -m venv /opt/engine \
 && /opt/engine/bin/pip install --require-hashes --only-binary=:all: -r /tmp/compute-melange.linux.txt
# Nothing can install at runtime and scanners have nothing to flag: pip leaves the venv and the base interpreter.
RUN /opt/engine/bin/python -m pip uninstall -y pip
RUN find /opt/python/lib/python3.12/site-packages -mindepth 1 -maxdepth 1 \
      \( -name 'pip' -o -name 'pip-*' -o -name 'setuptools*' -o -name 'wheel*' -o -name 'pkg_resources' -o -name '_distutils_hack' \) \
      -exec rm -rf {} + \
 && rm -f /opt/python/bin/pip /opt/python/bin/pip3 /opt/python/bin/pip3.12

FROM ${DEBIAN_BASE}
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
LABEL org.opencontainers.image.title="compute-melange" \
      org.opencontainers.image.description="Melange chain compute image: CPython 3.12.10, hashed wheels, UTF-8 mode; code and data are mounted" \
      org.opencontainers.image.source="https://github.com/pjg93-222/melange-platform" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}"

# debian:trixie-slim normally carries tzdata; install it (the only apt package of this stage) only if the pinned base does not.
RUN test -e /usr/share/zoneinfo/America/Chicago \
 || { apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*; }
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=engine /opt/python /opt/python
COPY --from=engine /opt/engine /opt/engine

# Parity with the Windows job (STATE: Central Standard Time, PYTHONIOENCODING utf-8:surrogateescape, hash seed random) in UTF-8
# mode, as the nightly Windows runner is (daily_refresh.py and refresh_congress_pipeline.py set PYTHONUTF8=1). The 191 chain
# modules' implicit-encoding reads carry no non-ASCII bytes, so the mode changes no bytes, and a CP1252 filesystem split cannot
# exist on Linux CPython 3.12. HOME and the pycache prefix point into the /tmp tmpfs so a read-only root never blocks them and
# no .pyc lands in the mounted tree. Do not set a hash seed here: the Windows run does not either.
ENV PATH=/opt/engine/bin:$PATH \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUTF8=1 TZ=America/Chicago \
    PYTHONIOENCODING=utf-8:surrogateescape PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPYCACHEPREFIX=/tmp/pyc HOME=/tmp/home \
    GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME}

# Build-time assertions: any of these failing fails the build, so a wrong interpreter, locale or zone never ships.
RUN python -c "import locale, sys; assert sys.flags.utf8_mode == 1 and locale.getpreferredencoding(False).lower() == 'utf-8' and sys.getfilesystemencoding().lower() == 'utf-8'"
RUN test -e /usr/share/zoneinfo/America/Chicago && python -c "import time; assert time.tzname == ('CST', 'CDT'), time.tzname"
RUN python -c "import importlib.util, sys; assert sys.version_info[:3] == (3, 12, 10), sys.version; assert importlib.util.find_spec('pip') is None"

# The code arrives as a bind mount at /melange (the chain writes its artifacts there); data at /melange-data, /draft and
# /localappdata/Melange (ops/compute/melange.compose.yaml). Nothing is baked in.
WORKDIR /melange
USER 10001:10001
CMD ["python", "/melange/fec/run_pipeline.py"]
