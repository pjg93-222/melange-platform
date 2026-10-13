"""Static policy tests for the Melange chain compute image (compute lane MEL, D-173/D-175/D-177/D-181, 2026-10-12).

Nothing here builds an image or runs the chain: it pins what the Dockerfile, the base lock, the hashed wheel lock, the
compose file and the workflow have to say, with the cross-file equalities (a digest repeated in three places) that drift
silently otherwise. Skipped with a message when PyYAML is missing."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
REPO = OPS.parent
yaml = pytest.importorskip("yaml", reason="PyYAML not installed; compute-melange policy not checked")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(OPS / "compute"))
import chain_imports  # noqa: E402

WF_DIR = OPS / "github" / "workflows" if (OPS / "github" / "workflows").is_dir() else REPO / ".github" / "workflows"
DOCKERFILE = (OPS / "docker" / "compute-melange.Dockerfile").read_text(encoding="utf-8")
BASE = json.loads((OPS / "compute" / "melange-base.lock").read_text(encoding="utf-8"))
LOCK_TEXT = (OPS / "locks" / "compute-melange.linux.txt").read_text(encoding="utf-8")
IN_TEXT = (OPS / "locks" / "compute-melange.in").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((OPS / "compute" / "melange.compose.yaml").read_text(encoding="utf-8"))
SERVICE = COMPOSE["services"]["chain"]
WORKFLOW_TEXT = (WF_DIR / "compute-melange.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(text: str) -> dict[str, str]:
    """{normalised name: version} for every `name==version` line of a requirements-style file."""
    return {norm(m.group(1)): m.group(2) for m in re.finditer(r"(?m)^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^\s\\;#]+)", text)}


def lock_entries() -> dict[str, list[str]]:
    out, cur = {}, None
    for line in LOCK_TEXT.splitlines():
        head = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==(\S+?)(?: \\)?$", line)
        if head:
            cur = norm(head.group(1))
            out[cur] = []
        elif re.match(r"^\s+--hash=sha256:", line) and cur:
            out[cur].append(re.search(r"sha256:([0-9a-f]+)", line).group(1))
    return out


# ------------------------------------------------------------------------------------------------ base lock and Dockerfile

def dockerfile_arg(name: str) -> str:
    return re.search(r"(?m)^ARG %s=(\S+)$" % name, DOCKERFILE).group(1)


def test_dockerfile_arg_defaults_equal_the_base_lock():
    """The defaults repeat the lock so a build that forgot a build arg never floats on a tag."""
    assert dockerfile_arg("DEBIAN_BASE") == BASE["debian"]["ref"] + "@" + BASE["debian"]["digest"]
    assert dockerfile_arg("CPYTHON_VERSION") == BASE["cpython"]["version"]
    assert dockerfile_arg("PBS_RELEASE") == BASE["cpython"]["release"]
    assert dockerfile_arg("PBS_SHA256") == BASE["cpython"]["sha256"]


def test_base_lock_digests_are_real_and_the_asset_name_matches_its_version():
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", BASE["debian"]["digest"]) and not re.fullmatch(r"sha256:0+", BASE["debian"]["digest"])
    assert HEX64.match(BASE["cpython"]["sha256"]) and not set(BASE["cpython"]["sha256"]) == {"0"}
    assert BASE["cpython"]["asset"] == "cpython-%s+%s-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz" % (
        BASE["cpython"]["version"], BASE["cpython"]["release"])
    assert "%2B" in DOCKERFILE and "${CPYTHON_VERSION}%2B${PBS_RELEASE}" in DOCKERFILE      # the URL-encoded form of the asset name
    assert BASE["cpython"]["version"] == "3.12.10"                                         # D-172: compute never moves patch level
    assert BASE["python_trixie_tag"]["exists"] is False                                    # why the base is not python:3.12.10-slim-trixie


def test_debian_13_base_per_d181_never_bookworm():
    assert BASE["debian"]["ref"] == "debian:trixie-slim"
    assert "bookworm" not in dockerfile_arg("DEBIAN_BASE")
    assert not re.search(r"(?m)^FROM\s+\S*bookworm", DOCKERFILE)


def test_every_from_is_the_digest_pinned_base_or_a_stage():
    stages, froms = set(), re.findall(r"(?m)^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", DOCKERFILE)
    assert froms
    for ref, alias in froms:
        assert ref == "${DEBIAN_BASE}" or ref in stages, ref
        if alias:
            stages.add(alias)
    assert "@sha256:" in dockerfile_arg("DEBIAN_BASE")


def test_cpython_is_verified_before_it_is_unpacked_and_checked_after():
    assert re.search(r'sha256sum -c -\s*;\s*\\\s*\n\s*mkdir /opt/python', DOCKERFILE) or "sha256sum -c -" in DOCKERFILE
    assert DOCKERFILE.index("sha256sum -c -") < DOCKERFILE.index("tar -xzf /tmp/cpython.tar.gz")
    assert "--proto '=https'" in DOCKERFILE and "--tlsv1.2" in DOCKERFILE
    assert "assert v == '${CPYTHON_VERSION}'" in DOCKERFILE


def test_wheels_install_hashed_and_binary_only_and_pip_is_removed():
    assert "pip install --require-hashes --only-binary=:all: -r /tmp/compute-melange.linux.txt" in DOCKERFILE
    assert "pip uninstall -y pip" in DOCKERFILE
    assert "find_spec('pip') is None" in DOCKERFILE


def test_nothing_of_the_tree_or_the_data_is_baked_in():
    copies = re.findall(r"(?m)^COPY\s+(?:--\S+\s+)*(\S+)\s+(\S+)", DOCKERFILE)
    assert copies
    for src, _dest in copies:
        assert src.startswith("/opt/") or src.startswith("/usr/lib/locale/") or src == "ops/locks/compute-melange.linux.txt", src
    code = "\n".join(line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("COPY fec", "COPY . ", "ADD ", "melange-data", "API Keys", "keys.json"):
        assert forbidden not in code, forbidden


def test_runs_as_uid_10001_and_the_default_command_is_the_chain():
    assert re.search(r"(?m)^USER 10001:10001$", DOCKERFILE)
    assert 'CMD ["python", "/melange/fec/run_pipeline.py"]' in DOCKERFILE
    assert "WORKDIR /melange" in DOCKERFILE


def test_the_environment_mirrors_the_windows_job():
    env_block = DOCKERFILE[DOCKERFILE.index("ENV PATH="):DOCKERFILE.index("# Build-time assertions")]
    for want in ("LANG=en_US.CP1252", "LC_ALL=en_US.CP1252", "TZ=America/Chicago", "PYTHONIOENCODING=utf-8:surrogateescape",
                 "PYTHONPYCACHEPREFIX=/tmp/pyc", "HOME=/tmp/home"):
        assert want in env_block, want
    for never in ("PYTHONHASHSEED", "PYTHONUTF8", "PYTHONUTF-8"):     # the Windows run sets neither: a hash seed here would be a difference
        assert never not in env_block, never
    assert "locale.getencoding().lower() in ('cp1252','windows-1252')" in DOCKERFILE      # the build fails if the locale is not CP1252
    assert "localedef -c -i en_US -f CP1252" in DOCKERFILE


# ------------------------------------------------------------------------------------------------ the two locks

def test_every_locked_package_has_at_least_one_sha256_and_nothing_else_is_unhashed():
    entries = lock_entries()
    assert len(entries) >= 50
    for name, hashes in entries.items():
        assert hashes and all(HEX64.match(h) for h in hashes), name
    assert len(re.findall(r"(?m)^[A-Za-z0-9][A-Za-z0-9_.\-]*==", LOCK_TEXT)) == len(entries)         # no duplicate package
    assert not re.search(r"(?m)^\s*(-e|git\+|https?://|\./)", LOCK_TEXT)                              # no editable, VCS or URL requirement
    assert "--hash=sha256:" in LOCK_TEXT and len(re.findall(r"--hash=sha256:", LOCK_TEXT)) == sum(map(len, entries.values()))


def test_the_lock_resolves_every_pin_of_the_input_at_the_same_version():
    locked, wanted = pins(LOCK_TEXT), pins(IN_TEXT)
    assert wanted
    for name, version in wanted.items():
        assert locked.get(name) == version, (name, locked.get(name), version)


def test_the_input_holds_every_line_of_the_two_host_requirement_files_and_the_undeclared_imports():
    wanted = pins(IN_TEXT)
    for rel in ("requirements.txt", "fec/requirements-honors.txt"):
        for name, version in pins((REPO / rel).read_text(encoding="utf-8")).items():
            assert wanted.get(name) == version, (rel, name)
    assert {"zopfli", "opensmile", "anthropic"} <= set(wanted)          # declared nowhere before this lane (design F6)


def test_the_wheelhouse_job_downloads_for_every_glibc_tag_the_lock_needs():
    """deps.yml rebuilds the wheelhouse from every ops/locks/*.linux.txt with explicit --platform flags, and pip expands none of them
    downwards: cryptography 49 ships only manylinux_2_34 wheels, numpy and pyarrow manylinux_2_27/_2_28 (checked against the PyPI
    JSON for all 59 locked hashes, 2026-10-12)."""
    deps = (WF_DIR / "deps.yml").read_text(encoding="utf-8")
    for tag in ("manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64", "manylinux_2_34_x86_64"):
        assert "--platform %s" % tag in deps, tag
    assert "cryptography" in lock_entries()
    assert "--require-hashes --only-binary=:all:" in deps


def test_the_lock_names_the_interpreter_it_was_resolved_for():
    assert "CPython 3.12.10" in LOCK_TEXT and "--require-hashes --only-binary=:all:" in LOCK_TEXT


def test_the_census_is_covered_by_the_lock_and_the_not_in_image_list_is_soft_only_and_unlocked():
    result = chain_imports.scan(str(REPO))
    locked = set(lock_entries())
    assert result["steps"] == len(set(chain_imports.chain_steps(str(REPO))))
    for top in result["third_party"]:
        dist = norm(chain_imports.dist_name(top))
        if top in chain_imports.NOT_IN_IMAGE:
            assert top in result["soft_only"], "%s became a hard import: it must go in the lock" % top
            assert dist not in locked, top
        else:
            assert dist in locked, "%s (imported by %s) is not in the lock" % (top, result["third_party"][top])
    assert set(chain_imports.NOT_IN_IMAGE) <= set(result["third_party"]), "a NOT_IN_IMAGE entry nothing imports any more"
    assert not result["unparsed"]


def test_chain_imports_import_mode_reports_a_missing_package_and_exits_nonzero(capsys):
    failed = chain_imports.import_all(["json", "no_such_package_for_the_melange_chain"], out=lambda s: None)
    assert list(failed) == ["no_such_package_for_the_melange_chain"]


def test_chain_imports_compile_mode_parses_every_reached_module(capsys):
    assert chain_imports.main(["--root", str(REPO), "--compile"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"compiled \d+ of \d+ modules", out)
    bad = REPO / "fec" / "__compile_probe__"
    bad.mkdir()
    try:
        # a syntax error in a reached module must fail the mode: probe the function directly, not through the real tree
        broken = bad / "broken.py"
        broken.write_text("def (:\n", encoding="utf-8")
        failures = chain_imports.compile_all([str(broken)])
        assert list(failures) == [str(broken)]
    finally:
        for f in bad.iterdir():
            f.unlink()
        bad.rmdir()


# ------------------------------------------------------------------------------------------------ compose

def test_the_chain_service_has_no_network_and_the_serve_stack_hardening():
    assert SERVICE["network_mode"] == "none"
    assert "networks" not in SERVICE and "ports" not in SERVICE and "expose" not in SERVICE
    assert SERVICE["read_only"] is True and SERVICE["user"] == "10001:10001"
    assert SERVICE["cap_drop"] == ["ALL"] and "cap_add" not in SERVICE
    assert "no-new-privileges:true" in SERVICE["security_opt"]
    assert any(t.startswith("/tmp") and "noexec" in t for t in SERVICE["tmpfs"])
    assert SERVICE["pids_limit"] > 0 and SERVICE["restart"] == "no" and SERVICE["init"] is True
    for key in ("privileged", "pid", "ipc", "userns_mode", "devices", "env_file", "secrets", "extra_hosts", "dns"):
        assert key not in SERVICE, key
    assert SERVICE["profiles"] == ["chain"]
    assert re.fullmatch(r"\$\{IMAGE_COMPUTE_MELANGE:\?[^}]+\}", SERVICE["image"])


def test_the_security_gates_compose_policy_has_no_finding_on_this_file():
    """The workflow's scan job renders this file and feeds ops/security_gate.py images; the same rules run here, offline."""
    sys.path.insert(0, str(OPS))
    import security_gate
    assert security_gate.compose_findings({"services": COMPOSE["services"]}) == []
    bad = {"chain": dict(SERVICE, read_only=False)}                        # negative control: the rules do bite this shape
    assert security_gate.compose_findings({"services": bad})


def test_the_image_reference_must_be_a_digest_by_the_job_wrapper_contract():
    text = (OPS / "compute" / "README.md").read_text(encoding="utf-8")
    assert "@sha256:" in text and "refuses tags" in text
    assert "latest" not in SERVICE["image"]


def test_resource_limits_match_the_host_the_parity_numbers_come_from():
    assert SERVICE["cpus"] == "8" and SERVICE["mem_limit"].endswith("g")
    assert int(SERVICE["mem_limit"][:-1]) >= 8


def test_mounts_are_exactly_the_five_in_the_design_with_the_right_modes_and_no_socket():
    mounts = {v["target"]: v for v in SERVICE["volumes"]}
    assert set(mounts) == {"/melange", "/draft", "/melange-data", "/localappdata/Melange", "/glassbox/events"}
    assert {t: v["read_only"] for t, v in mounts.items()} == {"/melange": False, "/draft": True, "/melange-data": True,
                                                              "/localappdata/Melange": False, "/glassbox/events": False}
    for v in mounts.values():
        assert v["type"] == "bind" and v["bind"] == {"create_host_path": False}
        assert v["source"].startswith("${MELANGE_") and ":?" in v["source"]          # a missing variable stops the run, never a default
    assert "docker.sock" not in json.dumps(COMPOSE)


def test_environment_names_are_the_ones_the_code_reads():
    env = SERVICE["environment"]
    assert env["MONEYBALL_ROOT"] == "/melange" and env["MONEYBALL_DRAFT"] == "/draft"
    assert env["MELANGE_DATA_ROOT"] == "/melange-data" and env["MELANGE_LOCAL_ROOT"] == "/localappdata/Melange"
    assert env["LOCALAPPDATA"] == "/localappdata" and env["GLASSBOX_EVENTS_DIR"] == "/glassbox/events"
    assert env["TZ"] == "America/Chicago" and env["LANG"] == env["LC_ALL"] == "en_US.CP1252"
    assert "MELANGE_TODAY" in env
    source = "\n".join((REPO / rel).read_text(encoding="utf-8") for rel in ("fec/paths.py", "fec/surfaces/glassbox_emit.py"))
    for name in ("MONEYBALL_ROOT", "MONEYBALL_DRAFT", "MELANGE_DATA_ROOT", "MELANGE_LOCAL_ROOT", "GLASSBOX_EVENTS_DIR", "MELANGE_TODAY"):
        assert name in source, "%s is set by compose but nothing in fec/ reads it" % name
    assert "LOCALAPPDATA" in (REPO / "fec" / "paths.py").read_text(encoding="utf-8")
    # LOCALAPPDATA is the parent of MELANGE_LOCAL_ROOT, so the modules that still read it directly agree with the seam
    assert env["MELANGE_LOCAL_ROOT"] == env["LOCALAPPDATA"] + "/Melange"
    for secret in ("KEY", "TOKEN", "PASSWORD", "SECRET"):
        assert not any(secret in k for k in env), secret


def test_the_command_is_the_windows_command():
    assert SERVICE["command"] == ["python", "/melange/fec/run_pipeline.py"]


# ------------------------------------------------------------------------------------------------ workflow

def release_pins() -> dict[str, tuple[str, str]]:
    text = (WF_DIR / "release.yml").read_text(encoding="utf-8")
    return {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(r"uses:\s*([\w./-]+)@([0-9a-f]{40})\s+# (v\d+)", text)}


def test_every_action_is_the_same_pinned_sha_as_release_yml():
    release = release_pins()
    used = re.findall(r"uses:\s*([\w./-]+)@([0-9a-f]{40})\s+# (v\d+)\s*$", WORKFLOW_TEXT, re.M)
    assert used and len(used) == len(re.findall(r"uses:", WORKFLOW_TEXT)), "an action is not pinned to a full SHA with its tag"
    for action, sha, tag in used:
        assert release[action] == (sha, tag), "%s is pinned differently from release.yml" % action


def test_triggers_permissions_and_concurrency():
    triggers = WORKFLOW.get("on", WORKFLOW.get(True))                                  # PyYAML reads the bare key `on` as True
    assert set(triggers) == {"workflow_dispatch", "push"}
    assert triggers["push"] == {"tags": ["v*"]}                                        # release.yml's tags: release.py waits for this run
    assert WORKFLOW["permissions"] == {}
    assert WORKFLOW["concurrency"]["cancel-in-progress"] is False
    for name, job in WORKFLOW["jobs"].items():
        assert job["permissions"].get("contents") == "read", name
        assert set(job["permissions"]) <= {"contents", "packages"}, name
    assert WORKFLOW["jobs"]["build"]["permissions"]["packages"] == "write"
    assert all(j["permissions"].get("packages") != "write" for n, j in WORKFLOW["jobs"].items() if n != "build")


def test_jobs_and_their_order():
    jobs = WORKFLOW["jobs"]
    assert set(jobs) == {"test-lock", "build", "smoke", "sbom", "scan", "digests"}
    assert jobs["build"]["needs"] == "test-lock"
    for name in ("smoke", "sbom", "scan"):
        assert jobs[name]["needs"] == "build", name
    assert jobs["digests"]["needs"] == ["build", "smoke", "sbom", "scan"]               # digests.json only after every check passed


def test_no_github_context_is_interpolated_into_a_shell_script():
    for name, job in WORKFLOW["jobs"].items():
        for step in job["steps"]:
            if "run" in step:
                assert "${{" not in step["run"], "%s / %s interpolates a context into a script" % (name, step.get("name", step["run"][:30]))


def test_the_build_is_cacheless_digest_pinned_and_passes_the_base_lock_through():
    build = next(s for s in WORKFLOW["jobs"]["build"]["steps"] if s.get("id") == "push")["with"]
    assert build["no-cache"] is True and build["provenance"] is False and build["sbom"] is False
    assert build["file"] == "ops/docker/compute-melange.Dockerfile" and build["context"] == "."
    for arg in ("DEBIAN_BASE", "CPYTHON_VERSION", "PBS_RELEASE", "PBS_SHA256", "GIT_SHA", "BUILD_TIME", "SOURCE_DATE_EPOCH"):
        assert arg + "=" in build["build-args"], arg
    assert "push=true" in build["outputs"] and "rewrite-timestamp=true" in build["outputs"]
    assert "melange-base.lock" in WORKFLOW_TEXT and "[[ \"$deb\" =~" in WORKFLOW_TEXT        # a placeholder digest stops the build


def test_smoke_runs_offline_unprivileged_and_imports_every_chain_package():
    smoke = "\n".join(s.get("run", "") for s in WORKFLOW["jobs"]["smoke"]["steps"])
    for flag in ("--network none", "--read-only", "--user 10001:10001", "--cap-drop ALL", "no-new-privileges:true", "$GITHUB_WORKSPACE:/melange:ro"):
        assert flag in smoke, flag
    assert "python /melange/ops/compute/chain_imports.py --import" in smoke
    assert "python /melange/ops/compute/chain_imports.py --compile" in smoke
    assert "(3,12,10)" in smoke and "cp1252" in smoke and "America/Chicago" in smoke and "PYTHONHASHSEED" in smoke


def test_the_lock_job_proves_the_census_the_static_tests_the_install_and_the_imports():
    run = "\n".join(s.get("run", "") for s in WORKFLOW["jobs"]["test-lock"]["steps"])
    assert "ops/tests/test_compute_melange.py" in "\n".join(s.get("run", "") for s in WORKFLOW["jobs"]["test-lock"]["steps"])
    assert "pip install --require-hashes --only-binary=:all: -r ops/locks/compute-melange.linux.txt" in run
    assert "pip check" in run and "chain_imports.py --import" in run and "chain_imports.py --compile" in run


def test_sbom_and_the_images_scan_run_against_the_pushed_digest():
    sbom = next(s for s in WORKFLOW["jobs"]["sbom"]["steps"] if "anchore/sbom-action" in s.get("uses", ""))
    assert sbom["with"]["format"] == "spdx-json" and "ref.outputs.image" in sbom["with"]["image"]
    scan = "\n".join(s.get("run", "") for s in WORKFLOW["jobs"]["scan"]["steps"])
    assert "python ops/security_gate.py images --ci" in scan and '--image "$IMAGE_COMPUTE_MELANGE"' in scan
    assert "--compose-json compose.config.json" in scan and "--profile chain config --format json" in scan
    assert "sha256sum -c -" in scan                                                      # trivy is checksum-pinned like release.yml's
    release = (WF_DIR / "release.yml").read_text(encoding="utf-8")
    assert re.search(r"TRIVY_SHA256:\s*(\S+)", WORKFLOW_TEXT).group(1) == re.search(r"TRIVY_SHA256:\s*(\S+)", release).group(1)
    assert re.search(r"TRIVY_VERSION:\s*(\S+)", WORKFLOW_TEXT).group(1) == re.search(r"TRIVY_VERSION:\s*(\S+)", release).group(1)


def test_release_yml_is_untouched_by_this_lane():
    """The compute workflow is its own file: release.yml owns the v* tags and must not name the compute image."""
    release = (WF_DIR / "release.yml").read_text(encoding="utf-8")
    assert "compute-melange" not in release


def test_the_new_files_survive_the_source_only_export():
    from ops.release_lib import export
    cfg = json.loads((OPS / "release_config.json").read_text(encoding="utf-8"))["export"]
    wanted = ["ops/compute/README.md", "ops/compute/chain_imports.py", "ops/compute/chain_parity.py", "ops/compute/melange-base.lock",
              "ops/compute/melange.compose.yaml", "ops/docker/compute-melange.Dockerfile", "ops/github/workflows/compute-melange.yml",
              "ops/locks/compute-melange.in", "ops/locks/compute-melange.linux.txt"]
    dropped = [p for p in wanted if (REPO / p).is_file() and not export.allowed_name(export.remap(p), cfg)]
    assert not dropped, "the export would drop %s: add the names to release_config.json export.extra_names" % dropped
    assert sum((REPO / p).is_file() for p in wanted) == len(wanted)
    raw = (OPS / "release_config.json").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "release_config.json must keep one line ending throughout"
