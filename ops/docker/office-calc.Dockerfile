# syntax=docker/dockerfile:1.7
# office-calc (D-174): LibreOffice headless used only as a CALCULATOR. A job container: no server, no network
# (network_mode none in ops/compute/office-calc.compose.yaml), nothing baked but the one script. Build from the
# platform repo root:
#   docker build -f ops/docker/office-calc.Dockerfile \
#     --build-arg DEBIAN_BASE="$(jq -r '.debian.ref + "@" + .debian.digest' ops/images.lock)" \
#     --build-arg GIT_SHA=... --build-arg BUILD_TIME=... .
# Base: plain Debian 13, not the python image, because LibreOffice's UNO bridge (python3-uno) is built for Debian's own
# python3 (3.13) and cannot be imported from a /usr/local 3.12. The default below is the digest in ops/images.lock (a test
# keeps them equal).
# LibreOffice: Debian's package, 4:25.2.3-2+deb13u8 (>= 24.8, which LET and XLOOKUP need), from a dated snapshot.debian.org
# source so the build does not drift when the archive moves; the build fails unless exactly that version installs. The
# -nogui variants are the headless build (no GTK, X11 or Qt). Python stays stdlib: openpyxl cannot write a cached value next
# to a formula, so office_recalc.py edits the cell XML itself.
ARG DEBIAN_BASE=debian:trixie-20261005-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
FROM ${DEBIAN_BASE}

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
# 20261012T000000Z -> 20261017T000000Z (D-181): the images gate failed this image on fixable findings in libcurl4t64 and libcurl3t64-gnutls
# 8.14.1-2+deb13u5 (CVE-2026-8286, CVE-2026-8927, CVE-2026-92392) and libexpat1 2.8.3-1~deb13u1 (CVE-2026-66046, CVE-2026-76956,
# CVE-2026-76957, CVE-2026-77214, CVE-2026-93990). trixie-security fixed them at libcurl 8.14.1-2+deb13u7 (DSA-6558-1) and libexpat
# 2.9.0-1~deb13u1 (DSA-6566-1); the 20261012 snapshot holds neither. The cure for a fixable finding is the fixed package, never an
# exception. The upgrade step and the dpkg assertions below make the build fail if the snapshot ever stops holding them.
ARG SNAPSHOT=20261017T000000Z
ARG LIBREOFFICE_VERSION=4:25.2.3-2+deb13u8
LABEL org.opencontainers.image.title="office-calc" \
      org.opencontainers.image.description="LibreOffice headless recalculation of a workbook copy (view copy with injected cached values)" \
      org.opencontainers.image.source="https://github.com/pjg93-222/melange-platform" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}"

ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME} \
    HOME=/tmp/home TMPDIR=/tmp LANG=C.UTF-8 LC_ALL=C.UTF-8 SAL_USE_VCLPLUGIN=svp

# http on purpose: the slim base has no ca-certificates, and apt checks the signed Release files either way.
# Docs and man pages are not installed (copyright files are kept: the licence scan reads them). Fonts: DejaVu only.
RUN set -eux; \
    test -s /usr/share/keyrings/debian-archive-keyring.pgp; \
    printf '%s\n' \
      'Types: deb' "URIs: http://snapshot.debian.org/archive/debian/${SNAPSHOT}/" 'Suites: trixie trixie-updates' \
      'Components: main' 'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' '' \
      'Types: deb' "URIs: http://snapshot.debian.org/archive/debian-security/${SNAPSHOT}/" 'Suites: trixie-security' \
      'Components: main' 'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' \
      > /etc/apt/sources.list.d/debian.sources; \
    printf '%s\n' 'path-exclude=/usr/share/doc/*' 'path-include=/usr/share/doc/*/copyright' 'path-exclude=/usr/share/man/*' \
      'path-exclude=/usr/share/info/*' > /etc/dpkg/dpkg.cfg.d/01-minimal; \
    apt-get -o Acquire::Check-Valid-Until=false update; \
    apt-get upgrade -y; \
    apt-get install -y --no-install-recommends \
      "libreoffice-calc-nogui=${LIBREOFFICE_VERSION}" "libreoffice-core-nogui=${LIBREOFFICE_VERSION}" \
      "libreoffice-common=${LIBREOFFICE_VERSION}" "python3-uno=${LIBREOFFICE_VERSION}" fonts-dejavu-core; \
    test "$(dpkg-query -W -f='${Version}' libreoffice-calc-nogui)" = "${LIBREOFFICE_VERSION}"; \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libcurl4t64)" ge 8.14.1-2+deb13u7; \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libcurl3t64-gnutls)" ge 8.14.1-2+deb13u7; \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libexpat1)" ge 2.9.0-1~deb13u1; \
    /usr/bin/python3 -I -c "import uno"; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/*; \
    groupadd --gid 10001 app; \
    useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY ops/compute/office_recalc.py /opt/office-calc/office_recalc.py

USER 10001:10001
WORKDIR /tmp
# A job container: it exits when the recalculation does, so there is nothing for a health check to ask.
HEALTHCHECK NONE
# -I: the workbook is untrusted data, so no PYTHON* variables and no user site; -B: the root is read-only anyway.
ENTRYPOINT ["/usr/bin/python3", "-I", "-B", "/opt/office-calc/office_recalc.py"]
CMD ["--help"]
