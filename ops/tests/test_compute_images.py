"""Policy tests for the Mentat compute image: ops/docker/compute-mentat.Dockerfile, its two lock files and
ops/github/workflows/compute-images.yml. No Docker, no network: the build itself only ever runs in CI.

Skipped with a message when PyYAML is missing."""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
yaml = pytest.importorskip("yaml", reason="PyYAML not installed; compute image policy not checked")

sys.path.insert(0, str(OPS.parent))
from ops.tests.layout import src  # noqa: E402

WF_DIR = src("ops/github/workflows")     # the source-only export (CI) remaps ops/github/ to .github/

DOCKERFILE = (OPS / "docker" / "compute-mentat.Dockerfile").read_text(encoding="utf-8")
WF_TEXT = (WF_DIR / "compute-images.yml").read_text(encoding="utf-8")
WF = yaml.safe_load(WF_TEXT)
IMAGES_LOCK = json.loads((OPS / "images.lock").read_text(encoding="utf-8"))
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def arg(name):
    """The default of `ARG name=default` (the first one, outside any heredoc)."""
    m = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.M)
    assert m, f"ARG {name} has no default"
    return m.group(1)


def run_text(job):
    return "\n".join(s.get("run", "") for s in WF["jobs"][job]["steps"])


# --- the Dockerfile ---------------------------------------------------------------------------------------------------

def test_base_images_are_pinned_by_digest_and_equal_images_lock():
    for key, name in (("debian", "DEBIAN_BASE"), ("node", "NODE_IMAGE")):
        ref, _, digest = arg(name).partition("@")
        assert SHA256.fullmatch(digest) and set(digest[7:]) != {"0"}, name
        assert (ref, digest) == (IMAGES_LOCK[key]["ref"], IMAGES_LOCK[key]["digest"]), f"{name} drifted from ops/images.lock"
        assert "trixie" in ref, f"{name}: D-181 wants Debian 13 bases"
    assert re.fullmatch(r"debian:trixie-\d{8}-slim", IMAGES_LOCK["debian"]["ref"])
    assert IMAGES_LOCK["debian"]["resolved_version"].startswith("13.")
    assert "bookworm" not in DOCKERFILE.lower()


def test_every_from_is_a_pinned_arg_or_an_earlier_stage():
    stages = set()
    for image, stage in re.findall(r"^FROM (\S+)(?: AS (\S+))?$", DOCKERFILE, re.M):
        assert image in ("${DEBIAN_BASE}", "${NODE_IMAGE}") or image in stages, image
        stages.add(stage)


def test_downloads_and_apt_are_pinned():
    for name in ("PBS_SHA256", "R_DEB_SHA256"):
        assert re.fullmatch(r"[0-9a-f]{64}", arg(name)), name
    assert DOCKERFILE.count("sha256sum -c") == 2
    assert re.fullmatch(r"\d{8}", arg("PBS_TAG"))
    assert "cpython-3.12.10%2B${PBS_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz" in DOCKERFILE      # D-172
    assert "https://cdn.posit.co/r/debian-13/pkgs/r-4.6.0_1_amd64.deb" in DOCKERFILE
    assert "R 4.6.0 library" in (OPS / "locks" / "r-packages.txt").read_text(encoding="utf-8")                    # the lock's R
    # Debian packages come from a dated snapshot of both archives, never the moving mirror
    assert re.fullmatch(r"\d{8}T\d{6}Z", arg("DEBIAN_SNAPSHOT"))
    assert DOCKERFILE.count("http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/") == 1
    assert DOCKERFILE.count("http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/") == 1
    assert arg("DEBIAN_SNAPSHOT") >= "20261017T000000Z" and "apt-get upgrade -y" in DOCKERFILE      # D-181: first snapshot holding the fixed libcurl, libexpat and chromium
    assert "deb.debian.org" not in DOCKERFILE
    # Chromium is an exact version, and the apt line uses it
    assert re.fullmatch(r"\d+(\.\d+){3}-\d+~deb13u\d+", arg("CHROMIUM_VERSION"))
    assert "chromium=${CHROMIUM_VERSION}" in DOCKERFILE
    # R packages: a dated PPM binary snapshot for this distro, installed with no compiler to fall back on
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", arg("PPM_SNAPSHOT_DATE"))
    assert "packagemanager.posit.co/cran/__linux__/trixie/" in DOCKERFILE
    assert not re.search(r"\b(build-essential|gcc|g\+\+|gfortran)\b", DOCKERFILE.replace("g++, gcc, gfortran", ""))


def test_python_wheels_are_hash_checked():
    assert "pip install --require-hashes --only-binary=:all: -r /tmp/compute-mentat.linux.txt" in DOCKERFILE
    assert "source=ops/locks/compute-mentat.linux.txt" in DOCKERFILE and "source=ops/locks/r-packages.txt" in DOCKERFILE
    assert "source=ops/locks/r-package-repos.txt" in DOCKERFILE


def test_nothing_is_baked_and_the_image_runs_as_10001():
    assert DOCKERFILE.rstrip().splitlines()[-1] == "USER 10001:10001"
    copies = re.findall(r"^COPY (.*)$", DOCKERFILE, re.M)
    assert copies and all(c.startswith("--from=") for c in copies), copies          # no build-context file is copied in
    assert not re.search(r"^ADD\b", DOCKERFILE, re.M)
    assert not re.search(r"^(ARG|ENV) \S*(KEY|TOKEN|SECRET|PASSWORD)", DOCKERFILE, re.M | re.I)
    code = "\n".join(ln for ln in DOCKERFILE.splitlines() if not ln.lstrip().startswith("#")).lower()   # prose may name what is absent
    assert "healthcheck" not in code                                                 # a batch image
    for banned in ("pwsh", "powershell", "artifact-tool", "npm ", "mentat/", "libreoffice"):
        assert banned not in code, banned


# --- the lock files ---------------------------------------------------------------------------------------------------

def requirement_blocks(path):
    """{name: (version, [hashes])} of a pip requirements file (continuation lines folded)."""
    text = re.sub(r"\\\n\s*", " ", (OPS / "locks" / path).read_text(encoding="utf-8"))
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.!+-]+)((?:\s+--hash=sha256:[0-9a-f]{64})*)", line)
        assert m, f"{path}: not a pinned requirement: {line}"
        out[re.sub(r"[-_.]+", "-", m[1]).lower()] = (m[2], re.findall(r"[0-9a-f]{64}", m[3]))
    return out


def test_python_lock_is_hashed_and_resolves_exactly_the_inputs():
    lock = requirement_blocks("compute-mentat.linux.txt")
    inputs = requirement_blocks("compute-mentat.in")
    assert len(inputs) == 19
    assert all(hashes for _, hashes in lock.values()), "a requirement without a hash"
    assert {n: v for n, (v, _) in lock.items()} == {n: v for n, (v, _) in inputs.items()}, "the lock is not the inputs, resolved"


def test_r_lock_is_a_plain_version_list():
    lines = [ln.strip() for ln in (OPS / "locks" / "r-packages.txt").read_text(encoding="utf-8").splitlines()]
    pkgs = [ln for ln in lines if ln and not ln.startswith("#")]
    assert pkgs and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9.]*==[0-9][0-9.-]*", ln) for ln in pkgs)      # deps.yml's rule
    names = [ln.split("==")[0] for ln in pkgs]
    assert len(names) == len(set(names))


def r_pins():
    """[(name, version, date)] of ops/locks/r-package-repos.txt, in file (install) order."""
    out = []
    for ln in (OPS / "locks" / "r-package-repos.txt").read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            m = re.fullmatch(r"([A-Za-z][A-Za-z0-9.]*)==([0-9][0-9.-]*) (\d{4}-\d{2}-\d{2})", ln)
            assert m, f"r-package-repos.txt: not `name==version date`: {ln}"
            out.append(m.groups())
    return out


def test_r_pins_restate_the_lock_exactly():
    """Exact per-package pins (decided 2026-10-12): a pin never overrides the lock, it says which dated repo carries the lock's version."""
    lock = dict(ln.strip().split("==") for ln in (OPS / "locks" / "r-packages.txt").read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#"))
    pins = r_pins()
    assert pins and len({n for n, _, _ in pins}) == len(pins)
    for name, version, date in pins:
        assert lock.get(name) == version, f"{name}: pin {version} is not the lock's {lock.get(name)}"
        assert "2026-04-24" <= date <= "2026-10-12", (name, date)                    # a past snapshot, after R 4.6.0's first binaries
    raw = (OPS / "locks" / "r-package-repos.txt").read_bytes()
    assert raw.isascii() and b"\r" not in raw


def test_r_packages_are_installed_at_exactly_the_lock_and_the_build_fails_on_any_difference():
    r = re.search(r"Rscript - <<'EOF'\n(.*?)\nEOF\n", DOCKERFILE, re.S).group(1)
    assert "exact per-package pins (decided 2026-10-12)" in DOCKERFILE and "Paul ratifies" not in DOCKERFILE
    # base install first, then each pin from its own dated repo with no dependency resolution, then the comparison
    assert r.index("install.packages(pkgs, lib = .Library)") < r.index("dependencies = FALSE") < r.index("installed.packages(")
    assert 'paste0("https://packagemanager.posit.co/cran/__linux__/trixie/", date)' in r and "repos = ppm(pin[2])" in r
    assert "packageDescription(name, lib.loc = .Library)$Version" in r                # raw "1.2-6", not packageVersion's "1.2.6"
    for failure in ('stop("pin "', 'stop("R packages: missing "', 'stop("R packages differ from the lock: "'):
        assert failure in r, failure
    assert 'have[pkgs, "Version"] != want' in r and "noCache = TRUE" in r
    assert "want[[name]] != version" in r and "is not a version of the lock" in r      # a pin the lock does not state stops the build


def test_workflow_accepts_no_r_difference_and_the_smoke_test_stays_strict():
    assert WF["env"]["R_LOCK_MISMATCHES_ACCEPTED"] == "0"
    assert 'if (length(off) != as.integer(Sys.getenv("ACCEPTED"))) stop(' in run_text("build")
    assert "exact per-package pins (decided 2026-10-12)" in WF_TEXT
    # the Windows bundle (deps.yml, job r-bundle) takes the same base date and the same pins as the image
    deps = yaml.safe_load((WF_DIR / "deps.yml").read_text(encoding="utf-8"))
    assert deps["env"]["PPM_SNAPSHOT_DATE"] == arg("PPM_SNAPSHOT_DATE")
    assert "ops/locks/r-package-repos.txt" in json.dumps(deps["jobs"]["r-bundle"])


def test_files_the_workflow_and_dockerfile_read_survive_the_source_only_export():
    sys.path.insert(0, str(OPS.parent))
    from ops.release_lib import export
    cfg = json.loads((OPS / "release_config.json").read_text(encoding="utf-8"))["export"]
    needed = {p for p in re.findall(r"\bops/[A-Za-z0-9_./-]+", WF_TEXT + DOCKERFILE) if (OPS.parent / p).is_file()}
    needed |= {"ops/locks/compute-mentat.in", "ops/docker/compute-mentat.Dockerfile", "ops/github/workflows/compute-images.yml"}
    assert {"ops/locks/compute-mentat.linux.txt", "ops/locks/r-packages.txt", "ops/images.lock", "ops/security_gate.py"} <= needed
    dropped = sorted(p for p in needed if not export.allowed_name(export.remap(p), cfg))
    assert not dropped, f"the export would drop {dropped}: add the names to release_config.json export.extra_names"


# --- the workflow -----------------------------------------------------------------------------------------------------

def test_workflow_runs_on_tags_and_by_hand():
    on = WF[True] if True in WF else WF["on"]                      # YAML 1.1 reads the bare key `on` as true
    assert on["push"] == {"tags": ["v*"]} and "workflow_dispatch" in on


def test_every_action_is_pinned_to_the_sha_release_yml_uses():
    release = (WF_DIR / "release.yml").read_text(encoding="utf-8")
    pins = {}
    for n, line in enumerate(WF_TEXT.splitlines(), 1):
        if re.match(r"\s*(-\s+)?uses:", line):
            m = re.search(r"uses:\s*([\w./-]+)@([0-9a-f]{40})\s+# v\d+\s*$", line)
            assert m, f"compute-images.yml:{n}: {line.strip()}"
            pins[m[1]] = m[2]
    assert pins
    for name, sha in pins.items():
        assert f"uses: {name}@{sha} " in release, f"{name} is pinned to a SHA release.yml does not use"


def test_permissions_are_minimal():
    assert WF["permissions"] == {}
    for name, job in WF["jobs"].items():
        perms = job.get("permissions")
        assert perms, name
        assert (perms.get("packages") == "write") == (name == "build"), name
        assert perms.get("contents") == "read", name                     # nothing here writes the repo or a release
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), f"{name}: expression inside run (pass it via env)"


def test_build_is_uncached_pinned_and_pushed_by_digest():
    push = next(s for s in WF["jobs"]["build"]["steps"] if s.get("id") == "push")
    w = push["with"]
    assert w["file"] == "ops/docker/compute-mentat.Dockerfile" and w["no-cache"] is True and w["provenance"] is False
    assert not {"cache-from", "cache-to"} & set(w)
    assert "DEBIAN_BASE=${{ steps.base.outputs.debian }}" in w["build-args"] and "SOURCE_DATE_EPOCH" in w["build-args"]
    assert WF["env"]["IMAGE_NAME"] == "ghcr.io/pjg93-222/compute-mentat"
    assert WF["jobs"]["build"]["outputs"]["digest"] == "${{ steps.image.outputs.digest }}"     # the reused digest or the one just pushed
    for job in ("sbom", "scan", "digests"):                              # everything downstream uses the digest, never the tag
        assert "needs.build.outputs.digest" in json.dumps(WF["jobs"][job])


def test_smoke_test_covers_what_the_image_is_for():
    smoke = run_text("build")
    assert "--network none --read-only --cap-drop ALL --security-opt no-new-privileges" in smoke
    assert 'loadNamespace(p)' in smoke and 'readLines("/tmp/pkgs")' in smoke      # every package of the lock (library() of all 214 fails on the Windows library too)
    assert "R_LOCK_MISMATCHES_ACCEPTED" in smoke and WF["env"]["R_LOCK_MISMATCHES_ACCEPTED"] == "0"
    assert "sys.version_info[:3] == (3, 12, 10)" in smoke and "md.version" in smoke               # python and the locked wheels
    assert "node --version" in smoke and "chromium --version" in smoke
    assert "/blas/libblas.so.3" in smoke and "/lapack/liblapack.so.3" in smoke                    # reference BLAS and LAPACK
    assert "baked path" in smoke and "data file in the image" in smoke                           # the no-data check
    py = re.search(r"<<'PY'\n(.*?)\n\s*PY\n", smoke, re.S).group(1)
    ast.parse(re.sub(r"(?m)^ {10}", "", py))                              # the embedded python is at least valid python


def test_smoke_script_is_valid_bash():
    bash = shutil.which("bash")
    if not bash or subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True).stdout.strip() != "ok":
        pytest.skip("no working bash")
    r = subprocess.run([bash, "-n"], input=run_text("build").encode(), capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


def test_images_gate_is_called_with_its_real_cli_and_release_yml_trivy_pins():
    help_text = subprocess.run([sys.executable, str(OPS / "security_gate.py"), "--help"], capture_output=True, text=True).stdout
    joined = re.sub(r"\\\n\s*", " ", run_text("scan"))
    call = re.search(r"ops/security_gate\.py ([^\n]*)", joined).group(1).split()
    assert call[0] == "images" and {"--ci", "--image", "--compose-json"} <= set(call)
    assert all(f in help_text for f in call if f.startswith("--"))
    release = (WF_DIR / "release.yml").read_text(encoding="utf-8")
    for key in ("TRIVY_VERSION", "TRIVY_SHA256"):
        assert re.search(rf"{key}: (\S+)", WF_TEXT)[1] == re.search(rf"{key}: (\S+)", release)[1]


def test_digests_json_is_uploaded_only_after_every_gate():
    job = WF["jobs"]["digests"]
    assert set(job["needs"]) == {"build", "sbom", "scan"}
    assert "digests.json" in run_text("digests")
    up = next(s for s in job["steps"] if "upload-artifact" in s.get("uses", ""))
    assert up["with"] == {"name": "compute-images-digests", "path": "digests.json"}
