# syntax=docker/dockerfile:1.7
# Mentat compute image (D-173 lane 2). Built only by CI (ops/github/workflows/compute-images.yml, D-175); the laptop runs it
# by digest under Docker Desktop. A batch image: no HEALTHCHECK, no CMD, and no Mentat code or data inside; both are
# mounted at run time (audits/deploy_20261011/COMPUTE_CONTAINERS_DESIGN.md, section 2.0).
# Contents: CPython 3.12.10 and a hashed venv (D-172), R 4.6.0 with exactly the ops/locks/r-packages.txt versions (a Posit
# Package Manager binary snapshot, then per-package pins from ops/locks/r-package-repos.txt), Node 22 (the binary only; the
# .mjs builders import node: modules and @oai/artifact-tool, which is not here), Chromium for chromote and the figure export.
# No PowerShell (the J3 orchestrators are the later M2-c port), no Excel, no LibreOffice.
# Build from the platform repo root (BuildKit):
#   docker build -f ops/docker/compute-mentat.Dockerfile \
#     --build-arg DEBIAN_BASE="$(jq -r '.debian.ref + "@" + .debian.digest' ops/images.lock)" \
#     --build-arg NODE_IMAGE="$(jq -r '.node.ref + "@" + .node.digest' ops/images.lock)" .
# Every input is pinned: base images by digest (the defaults below are the digests in ops/images.lock, a test keeps them
# equal), Debian packages by a snapshot.debian.org date, CPython and R by sha256, wheels by hash, R packages by exact per-package
# pins (decided 2026-10-12): a base PPM date plus a dated repo for each package the base date does not serve at the lock version.
# Refresh = change one of those pins and say why in the commit. A stale DEBIAN_SNAPSHOT is how a fixable HIGH vulnerability
# (Chromium above all) reaches the images gate: bump it with CHROMIUM_VERSION, the version that snapshot holds.
ARG DEBIAN_BASE=debian:trixie-20261005-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
ARG NODE_IMAGE=node:22-trixie-slim@sha256:154ba2f4d6fec323d28e4f4bb86bba4677f1223391a1979cf521304e03a98dfa

FROM ${NODE_IMAGE} AS node

# One apt configuration for every stage below: the dated snapshot of both archives, never the moving mirror.
FROM ${DEBIAN_BASE} AS base
# 2026-10-13: 20261011T000000Z -> 20261013T000000Z, with CHROMIUM_VERSION below. The 2026.10.13.2 images gate failed this image on
# 260 fixable findings, led by chromium 154.0.8037.92-1~deb13u1 (CVE-2026-106195 and CVE-2026-106237 CRITICAL, 20 or more HIGH). Debian
# fixed them in trixie-security at chromium 155.0.8059.39-1~deb13u1 (amd64 build in the archive since 2026-10-12T15:44Z), which the
# 20261011 snapshot does not hold. The cure for a fixable finding is the fixed package (D-181), never an exception.
# 20261013T000000Z -> 20261017T000000Z, with CHROMIUM_VERSION below (D-181): trixie-security fixed the next chromium findings (CVE-2026-110136
# through CVE-2026-110161) at 155.0.8059.79-1~deb13u1 (DSA-6562-1), libcurl at 8.14.1-2+deb13u7 (DSA-6558-1) and libexpat at 2.9.0-1~deb13u1
# (DSA-6566-1); the 20261013 snapshot holds chromium 155.0.8059.39-1~deb13u1, libcurl 8.14.1-2+deb13u5 and libexpat 2.8.3-1~deb13u1.
ARG DEBIAN_SNAPSHOT=20261017T000000Z
RUN printf '%s\n' \
      'Types: deb' \
      "URIs: http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/" \
      'Suites: trixie trixie-updates' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' \
      '' \
      'Types: deb' \
      "URIs: http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/" \
      'Suites: trixie-security' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' \
      > /etc/apt/sources.list.d/debian.sources \
 && printf '%s\n' 'Acquire::Check-Valid-Until "false";' 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/99snapshot

# Downloads, each checked against a sha256 pinned here. curl never reaches the final image.
FROM base AS fetch
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
ARG PBS_TAG=20250409
ARG PBS_SHA256=8c59b9ac6bff2dc3934181d7bc82594f9f59a613afed8d72c9e89d7194e790ee
ARG R_DEB_SHA256=4b5b7f2a036d15c213c0540b6c3369f54bd65fcd8eeb7a6a267aa7aba98f21c5
# CPython 3.12.10 as python-build-standalone ships it (the Windows jobs run the python.org 3.12.10).
RUN curl -fsSL --retry 5 --retry-all-errors -o /tmp/python.tgz \
      "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-3.12.10%2B${PBS_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz" \
 && echo "${PBS_SHA256}  /tmp/python.tgz" | sha256sum -c - \
 && mkdir /opt/python && tar -xzf /tmp/python.tgz -C /opt/python --strip-components=1
# R 4.6.0 as Posit's r-builds package it for Debian 13 (the R the PPM binaries are built with). Unpacked with dpkg-deb:
# the package's Depends line (compilers, OpenBLAS, -dev libraries) is not wanted.
RUN curl -fsSL --retry 5 --retry-all-errors -o /tmp/r.deb https://cdn.posit.co/r/debian-13/pkgs/r-4.6.0_1_amd64.deb \
 && echo "${R_DEB_SHA256}  /tmp/r.deb" | sha256sum -c - \
 && dpkg-deb -x /tmp/r.deb /r-root

# The hashed venv. Wheels only; a missing hash or a source distribution fails the build.
FROM base AS pydeps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY --from=fetch /opt/python /opt/python
RUN --mount=type=bind,source=ops/locks/compute-mentat.linux.txt,target=/tmp/compute-mentat.linux.txt \
    /opt/python/bin/python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/compute-mentat.linux.txt
# Separate step on purpose: a failed install above must fail the build, never be swallowed.
RUN /opt/venv/bin/pip uninstall -y pip && /opt/python/bin/python3 -m pip uninstall -y pip setuptools wheel

FROM base
ARG DEBIAN_FRONTEND=noninteractive
ARG CHROMIUM_VERSION=155.0.8059.79-1~deb13u1
ARG PPM_SNAPSHOT_DATE=2026-06-19
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
LABEL org.opencontainers.image.title="compute-mentat" \
      org.opencontainers.image.description="Mentat compute runtime (Python 3.12.10, R 4.6.0, Node 22, Chromium); code and data are mounted at run time" \
      org.opencontainers.image.source="https://github.com/pjg93-222/melange-platform" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}"

# The package list was derived from the ELF NEEDED entries of R and of the 117 compiled PPM binaries (2026-10-12), not guessed.
# libopenblas0-pthread is there because float and rsparse link libopenblas.so.0 directly; the alternatives are then pinned
# back to the reference BLAS and LAPACK, which is what Windows R uses (the CONSTRAINTS cross-BLAS note).
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends \
      ca-certificates locales \
      # R itself (no X11 or Tcl/Tk: there is no display, and tcltk is not in the lock)
      libblas3 liblapack3 libbz2-1.0 libcairo2 libcurl4t64 libdeflate0 libglib2.0-0t64 libgomp1 libicu76 libjpeg62-turbo \
      liblzma5 libpango-1.0-0 libpangocairo-1.0-0 libpcre2-8-0 libpng16-16t64 libreadline8t64 libtiff6 libtirpc3t64 \
      libzstd1 zlib1g \
      # what the PPM binaries link
      libabsl20240722 libfontconfig1 libfreetype6 libfribidi0 libgdal36 libgeos-c1t64 libgit2-1.9 libglpk40 libharfbuzz0b \
      libmariadb3 libopenblas0-pthread libpoppler-cpp2 libproj25 libprotobuf32t64 libssl3t64 libudunits2-0 libuv1t64 \
      libwebp7 libwebpmux3 libxml2 \
      # headless browser (chromote, figure PNG export) with fonts to draw text
      chromium=${CHROMIUM_VERSION} fonts-dejavu-core fonts-liberation \
 && sed -i 's/^# \(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen && locale-gen \
 && update-alternatives --set libblas.so.3-x86_64-linux-gnu /usr/lib/x86_64-linux-gnu/blas/libblas.so.3 \
 && update-alternatives --set liblapack.so.3-x86_64-linux-gnu /usr/lib/x86_64-linux-gnu/lapack/liblapack.so.3 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=fetch /r-root/opt/R /opt/R
RUN ln -s /opt/R/4.6.0/bin/R /usr/local/bin/R && ln -s /opt/R/4.6.0/bin/Rscript /usr/local/bin/Rscript

# R packages: exact per-package pins (decided 2026-10-12), so the library is the Windows machine's, version for version.
# Posit serves a prebuilt binary for this R and distro to a request with R's user agent. There is no compiler in the image, so a
# package without a binary fails the build instead of being compiled (none needed a source build). The lock is that machine's
# library, installed on different days, and no one PPM date serves all 214 of its versions. So: (1) all 214 are installed from
# the base snapshot PPM_SNAPSHOT_DATE, which serves 199 of them at the lock version (of every day 2026-06-01..2026-07-20 and
# every week 2026-03-01..2026-10-11, 2026-06-19 differs from the lock least); (2) the other 15 are then installed one by one,
# with dependencies=FALSE, from the dated repo that ops/locks/r-package-repos.txt names for each, in that file's dependency
# order; (3) every installed version is compared with the lock, and a difference, a missing package or an extra one fails the
# build. A package a pin does not serve at the lock version fails (2) or (3), never installs quietly.
RUN --mount=type=bind,source=ops/locks/r-packages.txt,target=/tmp/r-packages.txt --mount=type=bind,source=ops/locks/r-package-repos.txt,target=/tmp/r-package-repos.txt Rscript - <<'EOF'
lock <- trimws(readLines("/tmp/r-packages.txt"))
lock <- lock[nzchar(lock) & !startsWith(lock, "#")]
pkgs <- sub("==.*$", "", lock)
want <- structure(sub("^.*==", "", lock), names = pkgs)
pins <- trimws(readLines("/tmp/r-package-repos.txt"))
pins <- strsplit(pins[nzchar(pins) & !startsWith(pins, "#")], "[[:space:]]+")
snap <- Sys.getenv("PPM_SNAPSHOT_DATE")
ppm <- function(date) c(CRAN = paste0("https://packagemanager.posit.co/cran/__linux__/trixie/", date))
ua <- sprintf("R/%s R (%s)", getRversion(), paste(getRversion(), R.version["platform"], R.version["arch"], R.version["os"]))
options(repos = ppm(snap), HTTPUserAgent = ua, timeout = 1200, warn = 1)
install.packages(pkgs, lib = .Library)
for (pin in pins) {
  name <- sub("==.*$", "", pin[1])
  version <- sub("^.*==", "", pin[1])
  if (!name %in% pkgs || want[[name]] != version) stop("pin ", pin[1], " is not a version of the lock")
  install.packages(name, lib = .Library, repos = ppm(pin[2]), dependencies = FALSE)
  got <- packageDescription(name, lib.loc = .Library)$Version
  if (!identical(got, version)) stop("pin ", pin[1], " from ", pin[2], ": installed ", got)
  cat(sprintf("R packages: %s %s from %s\n", name, got, pin[2]))
}
have <- installed.packages(lib.loc = .Library, noCache = TRUE)
base <- rownames(have)[have[, "Priority"] %in% "base"]
missing <- setdiff(pkgs, rownames(have))
extra <- setdiff(rownames(have), c(base, pkgs))
if (length(missing) || length(extra))
  stop("R packages: missing ", paste(missing, collapse = " "), "; not in the lock ", paste(extra, collapse = " "))
off <- pkgs[have[pkgs, "Version"] != want]
if (length(off))
  stop("R packages differ from the lock: ", paste(sprintf("%s lock %s, installed %s", off, want[off], have[off, "Version"]), collapse = "; "))
cat(sprintf("R packages: all %d at the lock version (base snapshot %s, %d pinned)\n", length(pkgs), snap, length(pins)))
# Files some packages ship that nothing here runs and the images gate reads as findings (D-181: removed, never excepted): example
# Dockerfiles (sf/docker/*, bslib), a fake GCP service-account key (gargle test fixture) and the example private key in the openssl
# keys vignette. Removed in this layer, so no layer of the image holds them.
junk <- c(list.files(.Library, "^Dockerfile$|\\.dockerfile$", recursive = TRUE, full.names = TRUE, all.files = TRUE, ignore.case = TRUE),
          file.path(.Library, "gargle", "extdata", "fake_service_account.json"), file.path(.Library, "openssl", "doc", "keys.html"))
junk <- junk[file.exists(junk)]
unlink(junk)
cat(sprintf("R packages: removed %d example files the images gate reads as findings\n", length(junk)))
EOF

COPY --from=pydeps /opt/python /opt/python
COPY --from=pydeps /opt/venv /opt/venv

# uid 10001 as the other images; no home directory. The wrapper mounts a tmpfs on /tmp for HOME and R's tempdir.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

# LANG: R sorts strings by the process locale, and Windows R sorts by en-US, so C.UTF-8 would reorder every sort().
ENV PATH=/opt/venv/bin:$PATH \
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOME=/tmp/home
WORKDIR /tmp
USER 10001:10001
