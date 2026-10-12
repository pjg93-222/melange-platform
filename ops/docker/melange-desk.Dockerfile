# syntax=docker/dockerfile:1.7
# Melange desk server: stdlib Python, two source files, no data baked in (data arrives as a
# read-only bind mount at /data/melange). Build from the platform repo root:
#   docker build -f ops/docker/melange-desk.Dockerfile \
#     --build-arg PYTHON_BASE="$(jq -r '.python.ref + "@" + .python.digest' ops/images.lock)" \
#     --build-arg GIT_SHA=... --build-arg BUILD_TIME=... .
# The default below is the digest in ops/images.lock (a test keeps them equal), so a build that
# forgot the build arg still never floats on a tag.
ARG PYTHON_BASE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_BASE}

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
LABEL org.opencontainers.image.title="melange-desk" \
      org.opencontainers.image.description="Melange invited-build desk (read-only static server behind Cloudflare Access)" \
      org.opencontainers.image.source="https://github.com/pjg93-222/melange-platform" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME}

# Stdlib only: drop pip so scanners have nothing to flag and nothing can install at runtime.
RUN python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true \
 && groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
# `fec` has no __init__.py in the repo (namespace package), so no stubs are needed.
# If desk_server.py starts importing anything else from fec/, add that file here.
COPY fec/access_gate.py /app/fec/access_gate.py
COPY fec/surfaces/desk_server.py /app/fec/surfaces/desk_server.py

USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
# -m runs desk_server's `sys.exit(main())`, so a bad DESK_ROOT / ACCESS_* setting exits 2 (python -c "...main()" would exit 0).
# DESK_ROOT is set by compose: /data/melange, the release's melange/ tree (Moneyball.html, lobby-*.html, downloads/).
CMD ["python", "-m", "fec.surfaces.desk_server"]
