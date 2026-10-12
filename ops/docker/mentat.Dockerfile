# syntax=docker/dockerfile:1.7
# Mentat console, read-only. Hashed wheels only; no R, Node or Chromium (those routes return 403
# under --read-only). Build from the platform repo root:
#   docker build -f ops/docker/mentat.Dockerfile \
#     --build-arg PYTHON_BASE="$(jq -r '.python.ref + "@" + .python.digest' ops/images.lock)" \
#     --build-arg GIT_SHA=... --build-arg BUILD_TIME=... .
# Layer order is base -> deps -> code, so a code-only release pulls a few MB.
# The default PYTHON_BASE below is the digest in ops/images.lock (a test keeps them equal).
ARG PYTHON_BASE=python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

FROM ${PYTHON_BASE} AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY ops/locks/mentat-console.linux.txt /tmp/mentat-console.linux.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/mentat-console.linux.txt
# Separate step on purpose: a failed install above must fail the build, never be swallowed by `|| true`.
RUN /opt/venv/bin/python -m pip uninstall -y pip || true

# Strip what must not ship before it can land in any layer of the final image.
FROM ${PYTHON_BASE} AS src
COPY mentat/app /stage/app
COPY mentat/tools /stage/tools
RUN find /stage -type d \( -name tests -o -name test -o -name __pycache__ \) -prune -exec rm -rf {} + \
 && find /stage -type f \( -name '*.ps1' -o -name '*.psm1' -o -name '*.bak*' -o -name '*.pyc' \) -delete

FROM ${PYTHON_BASE}
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
LABEL org.opencontainers.image.title="mentat-console" \
      org.opencontainers.image.description="Mentat console (read-only hosted mode behind Cloudflare Access)" \
      org.opencontainers.image.source="https://github.com/pjg93-222/melange-platform" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME} \
    PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/mentat/app:/app/mentat/tools

# The named volumes mounted here are created from these directories on first use, so uid 10001
# must own them (userns-remap makes root-owned volumes unwritable otherwise).
RUN /usr/local/bin/python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && install -d -o 10001 -g 10001 /var/lib/mentat /var/cache/mentat

COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY --from=src /stage/app /app/mentat/app
COPY --from=src /stage/tools /app/mentat/tools

USER 10001:10001
EXPOSE 8802
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8802/healthz', timeout=3)"]
CMD ["python", "/app/mentat/app/server.py", "--port", "8802", "--read-only", "--data-root", "/var/lib/mentat"]
