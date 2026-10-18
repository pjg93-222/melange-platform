"""Policy tests for the compute-collector image, its compose file, entrypoint, smoke script and workflow.

Nothing here builds or runs an image (no Docker needed): it reads the files, applies the same compose gate CI applies
(security_gate.compose_findings), and runs the entrypoint and the smoke script under `sh` with a fake secrets dir and a fake `docker`.
Skipped with a message when PyYAML or sh is missing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS))
sys.path.insert(0, str(OPS.parent))
import security_gate  # noqa: E402
from ops.tests.layout import src  # noqa: E402

yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
SH = shutil.which("sh")
DOCKERFILE = (OPS / "docker" / "compute-collector.Dockerfile").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((OPS / "compute" / "collectors.compose.yaml").read_text(encoding="utf-8"))
WF_DIR = src("ops/github/workflows")
WORKFLOW_TEXT = (WF_DIR / "compute-collector.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
LOCK = json.loads((OPS / "images.lock").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
SECRETISH = re.compile(r"key|token|secret|password|credential", re.I)


# ---------------------------------------------------------------- Dockerfile

def test_node_base_is_the_digest_in_images_lock_debian_is_pinned_and_never_bookworm():
    assert f"ARG NODE_BASE={LOCK['node']['ref']}@{LOCK['node']['digest']}\n" in DOCKERFILE
    assert re.search(r"^ARG DEBIAN_BASE=debian:trixie-slim@sha256:[0-9a-f]{64}$", DOCKERFILE, re.M)
    code = "\n".join(line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#"))     # comments explain why
    assert "bookworm" not in code.lower() and ":latest" not in code and "FROM python:" not in code


def test_cpython_is_3_12_10_exactly_from_a_sha256_verified_tarball_like_the_melange_chain_image():
    arg = lambda name: re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.M).group(1)      # D-172: not the 3.12.x of a python:* tag
    assert arg("CPYTHON_VERSION") == "3.12.10" and re.fullmatch(r"\d{8}", arg("PBS_RELEASE")) and re.fullmatch(r"[0-9a-f]{64}", arg("PBS_SHA256"))
    assert 'sha256sum -c -' in DOCKERFILE and "(3, 12, 10)" in DOCKERFILE                  # verified at download, asserted at build
    mel = OPS / "compute" / "melange-base.lock"                                           # lands with branch compute/melange-chain
    if not mel.is_file():
        pytest.skip("ops/compute/melange-base.lock is not in this tree (compute/melange-chain not merged)")
    base = json.loads(mel.read_text(encoding="utf-8"))
    assert arg("DEBIAN_BASE") == f"{base['debian']['ref']}@{base['debian']['digest']}"
    assert (arg("CPYTHON_VERSION"), arg("PBS_RELEASE"), arg("PBS_SHA256")) == (base["cpython"]["version"], base["cpython"]["release"], base["cpython"]["sha256"])


def test_image_runs_unprivileged_and_carries_no_credential():
    assert "USER 10001:10001" in DOCKERFILE and 'ENTRYPOINT ["/usr/local/bin/collector-entrypoint"]' in DOCKERFILE
    for line in DOCKERFILE.splitlines():
        if re.match(r"\s*(ENV|ARG)\b", line):
            names = re.findall(r"([A-Z][A-Z0-9_]*)=", line) or re.findall(r"^\s*(?:ENV|ARG)\s+([A-Z][A-Z0-9_]*)", line)
            assert not [n for n in names if SECRETISH.search(n)], line
    assert not re.search(r"COPY[^\n]*(credential|\.claude|keys\.json|_key\.txt|\.env\b)", DOCKERFILE, re.I)
    assert "CLAUDE_CONFIG_DIR=/tmp/claude" in DOCKERFILE and "MELANGE_CLASSIFIER_COMMAND=/usr/local/bin/claude" in DOCKERFILE


def test_installs_are_pinned_hashed_and_signature_checked():
    assert "--require-hashes --only-binary=:all:" in DOCKERFILE and "COPY ops/locks/compute-collector.linux.lock" in DOCKERFILE
    code = "\n".join(line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#"))     # the header talks about both
    assert code.count("npm audit signatures") == 2 and code.count("--ignore-scripts") == 2
    for arg in ("PLAYWRIGHT_VERSION", "CLAUDE_CODE_VERSION"):
        assert re.search(rf"^ARG {arg}=\d+\.\d+\.\d+$", DOCKERFILE, re.M), arg
    assert "snapshot.debian.org/archive/debian-security/" in DOCKERFILE and "grep -q" in DOCKERFILE   # dated archive, guarded
    snapshot = re.search(r"^ARG DEBIAN_SNAPSHOT=(\d{8}T\d{6}Z)$", DOCKERFILE, re.M).group(1)
    assert snapshot >= "20261017T000000Z" and "apt-get upgrade -y" in DOCKERFILE      # D-181: first snapshot holding the fixed libcurl, libexpat and chromium


def test_claude_pin_matches_the_version_installed_on_the_laptop_when_it_can_be_read():
    pinned = re.search(r"^ARG CLAUDE_CODE_VERSION=(.+)$", DOCKERFILE, re.M).group(1)
    appdata = os.environ.get("APPDATA")
    package = Path(appdata or "/nonexistent") / "npm/node_modules/@anthropic-ai/claude-code/package.json"
    if not package.is_file():
        pytest.skip("no laptop npm install of the claude CLI here")
    assert json.loads(package.read_text(encoding="utf-8"))["version"] == pinned    # the Windows run and the container run the same CLI


def test_the_hash_lock_hashes_every_requirement_and_names_the_collector_set():
    text = (OPS / "locks" / "compute-collector.linux.lock").read_text(encoding="utf-8")
    entries = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+) \\\n((?:\s+--hash=sha256:[0-9a-f]{64}(?: \\)?\n?)+)", text, re.M)
    assert {n.lower() for n, _, _ in entries} == {"openpyxl", "pypdf", "et-xmlfile"}
    assert len(entries) == len(re.findall(r"^[A-Za-z0-9_.-]+==", text, re.M))            # none without a hash (deps.yml fails closed on one)
    pinned = dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", (OPS.parent / "requirements.txt").read_text(encoding="utf-8"), re.M))
    assert all(pinned[n.lower()] == v for n, v, _ in entries if n.lower() in pinned)      # the host's versions (requirements.txt)


def test_profile_mount_point_exists_in_the_image_owned_by_the_job_user_so_the_named_volume_is_writable():
    assert re.search(r"install -d -o 10001 -g 10001 [^\n]*/profile", DOCKERFILE)
    assert any(v == "collector-profile:/profile" for s in SERVICES.values() for v in s["volumes"])


# ---------------------------------------------------------------- compose

def test_compose_has_exactly_the_three_profiled_jobs_and_passes_the_ci_compose_gate():
    assert set(SERVICES) == {"collect", "llm", "replay"}
    assert all(s["profiles"] == [name] for name, s in SERVICES.items())            # `docker compose up` starts none of them
    assert security_gate.compose_findings(COMPOSE) == []                           # read-only root, non-root, cap_drop ALL, no-new-privileges, no ports
    for name, s in SERVICES.items():
        for forbidden in ("ports", "privileged", "cap_add", "devices", "pid", "ipc", "userns_mode"):
            assert forbidden not in s, (name, forbidden)
        assert not [v for v in s["volumes"] if "docker.sock" in str(v)]
        assert s["image"].startswith("${IMAGE_COMPUTE_COLLECTOR:?") and s["restart"] == "no"


def test_only_collect_has_api_keys_only_llm_has_the_cli_login_and_replay_has_no_network_and_no_secret():
    assert SERVICES["collect"]["secrets"] == ["govinfo_key", "fec_data_gov"] and SERVICES["collect"]["networks"] == ["egress"]
    assert SERVICES["llm"]["secrets"] == ["claude_credentials"] and SERVICES["llm"]["networks"] == ["egress"]
    replay = SERVICES["replay"]
    assert replay["network_mode"] == "none" and "secrets" not in replay and "networks" not in replay
    cassettes = [v for v in replay["volumes"] if isinstance(v, dict) and v["target"] == "/cassettes"]
    assert len(cassettes) == 1 and cassettes[0]["read_only"] is True
    assert not [s for n, s in SERVICES.items() if n != "replay" and any(isinstance(v, dict) and v["target"] == "/cassettes" for v in s["volumes"])]


def test_secrets_are_file_sources_named_by_variables_and_no_secret_sits_in_an_environment_block():
    assert set(COMPOSE["secrets"]) == {"govinfo_key", "fec_data_gov", "claude_credentials"}
    for name, spec in COMPOSE["secrets"].items():
        assert set(spec) == {"file"} and re.fullmatch(r"\$\{[A-Z_]+_HOST:\?\}", spec["file"]), name       # never a literal host path
    for name, s in SERVICES.items():
        for var, value in s["environment"].items():
            if SECRETISH.search(var):
                assert name == "replay" and value == "replay-placeholder", (name, var)               # replay only: redaction makes any value match
    assert {"MONEYBALL_ROOT", "LOCALAPPDATA", "GLASSBOX_EVENTS_DIR"} <= set(SERVICES["collect"]["environment"])


# ---------------------------------------------------------------- mounts (least privilege, decided 2026-10-12)

FEC = OPS.parent / "fec"
needs_fec = pytest.mark.skipif(not (FEC / "paths.py").is_file(), reason="fec/ is not in this tree (the platform repo carries ops/ only)")
COMPOSE_TEXT = (OPS / "compute" / "collectors.compose.yaml").read_text(encoding="utf-8")
HOST_VARIABLES = {"MELANGE_HOST_ROOT", "GLASSBOX_HOST_EVENTS", "LDA_RAW_HOST_DIR", "CASSETTE_HOST_DIR", "REPLAY_OUT_HOST_DIR"}
LDA_RAW_TARGET = "/localappdata/Melange/lda_rebuild/raw"    # refresh_billstatus_bulk.default_raw_root() under collect's LOCALAPPDATA=/localappdata
KEYISH_NAME = re.compile(r"key|cred|token|secret|passw|dpapi|id_rsa|ed25519|\.(env|pem|pfx|ppk|p12)$", re.I)
LLM_WRITES = ["/melange/audits/sentinel_admissions", "/melange/data/legislation", "/melange/data/members"]


def binds(name):
    """{target: volume} of a service's bind mounts (a named volume is a string, not a bind)."""
    return {v["target"]: v for v in SERVICES[name]["volumes"] if isinstance(v, dict) and v.get("type") == "bind"}


def writable(name):
    return sorted(t for t, v in binds(name).items() if v.get("read_only") is not True)


def test_only_lda_rebuild_raw_of_localappdata_melange_is_mounted_only_on_collect_and_nothing_secret_is_visible_through_a_mount():
    allowed_source = re.compile(r"^\$\{(" + "|".join(sorted(HOST_VARIABLES)) + r"):\?\}(/[a-z_]+)*$")
    assert "LOCALAPPDATA_MELANGE_HOST" not in COMPOSE_TEXT
    lda = [(name, v) for name in SERVICES for v in binds(name).values() if re.search(r"localappdata|lda_rebuild", v["source"] + v["target"], re.I)]
    assert [(name, v["source"], v["target"]) for name, v in lda] == [("collect", "${LDA_RAW_HOST_DIR:?}", LDA_RAW_TARGET)]   # one mount, one service
    assert lda[0][1].get("read_only") is not True and lda[0][1]["bind"] == {"create_host_path": False}   # the BILLSTATUS archives are written there
    for name, s in SERVICES.items():
        for target, v in binds(name).items():
            assert allowed_source.match(v["source"]), (name, v["source"])                 # only the host variables, optionally a subdirectory
            assert not re.search(r"dashboard_password|anthropic_key|lda_api_key", v["source"] + target, re.I), (name, v)
            assert not KEYISH_NAME.search(Path(target).name) and not KEYISH_NAME.search(v["source"].split(":?}")[-1]), (name, v)
            assert not target.startswith("/localappdata") or (name, target) == ("collect", LDA_RAW_TARGET), (name, target)   # no parent, no sibling
        assert s["environment"]["LOCALAPPDATA"] == "/localappdata"                       # names a directory that is not there: fails loudly
    for s in COMPOSE["secrets"].values():
        assert not re.search(r"lda_rebuild|dashboard_password", s["file"], re.I)
    named = {name: [v for v in s["volumes"] if isinstance(v, str)] for name, s in SERVICES.items()}
    assert named == {"collect": ["collector-profile:/profile"], "llm": [], "replay": []}   # the browser profile is the collector's alone


@needs_fec
def test_the_lda_raw_mount_target_is_the_default_raw_root_of_refresh_billstatus_bulk_under_collects_environment():
    """default_raw_root() run on collect's own environment (LOCALAPPDATA=/localappdata, POSIX paths) names the mount target."""
    import ast
    import posixpath
    import types
    source = (FEC / "legislation" / "refresh_billstatus_bulk.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "default_raw_root")
    namespace = {"os": types.SimpleNamespace(environ=dict(SERVICES["collect"]["environment"]), path=posixpath)}
    exec(compile(ast.Module([fn], []), "default_raw_root", "exec"), namespace)
    assert namespace["default_raw_root"]() == LDA_RAW_TARGET


def test_the_host_folder_behind_the_lda_raw_mount_holds_no_key_credential_or_token_file_when_this_machine_has_it():
    """Names only, never contents: lda_rebuild itself holds the lobbying API keys, its raw subfolder must not."""
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) / "Melange" / "lda_rebuild" / "raw" if local else None
    if root is None or not root.is_dir():
        pytest.skip("no %LOCALAPPDATA%\\Melange\\lda_rebuild\\raw on this machine")
    keyish = [str(p.relative_to(root)) for p in root.rglob("*") if KEYISH_NAME.search(p.name)]
    assert not keyish, keyish[:10]


def test_replay_has_the_tree_read_only_a_separate_writable_output_dir_and_nothing_else_writable():
    replay, b = SERVICES["replay"], binds("replay")
    assert b["/melange"]["read_only"] is True and b["/melange"]["source"] == "${MELANGE_HOST_ROOT:?}"
    assert b["/cassettes"]["read_only"] is True and b["/cassettes"]["source"] == "${CASSETTE_HOST_DIR:?}"
    out = b["/replay-out"]
    assert out.get("read_only") is not True and out["source"] == "${REPLAY_OUT_HOST_DIR:?}"
    assert replay["environment"]["MONEYBALL_ROOT"] == "/replay-out"                      # a replayed collector writes where the output dir is
    assert writable("replay") == ["/glassbox/events", "/replay-out"]
    assert len(replay["volumes"]) == len(b) == 4                                         # no named volume, no extra bind
    assert replay["network_mode"] == "none" and "secrets" not in replay and "networks" not in replay


def test_llm_has_the_tree_read_only_and_writes_only_the_directories_the_claude_steps_write():
    b = binds("llm")
    assert b["/melange"]["read_only"] is True and b["/melange"]["source"] == "${MELANGE_HOST_ROOT:?}"
    assert [t for t in writable("llm") if t != "/glassbox/events"] == LLM_WRITES
    for target in LLM_WRITES:
        assert b[target]["source"] == "${MELANGE_HOST_ROOT:?}" + target[len("/melange"):]   # the same directory of the same tree
    assert len(SERVICES["llm"]["volumes"]) == len(b) == 5                                # no named volume


def test_collect_alone_has_the_whole_tree_writable_and_the_header_says_why():
    for name in SERVICES:
        assert (binds(name)["/melange"].get("read_only") is not True) == (name == "collect"), name
    assert writable("collect") == ["/glassbox/events", LDA_RAW_TARGET, "/melange"]
    for reason in ("-wal, -shm and -journal", "os.replace", "bill_repository_live.json", "Neither fits a read-only tree"):
        assert reason in COMPOSE_TEXT, reason


@needs_fec
def test_the_collector_code_bears_out_the_mounts():
    """The reasons the header gives, read from the code: the root-level writes that keep the tree writable for collect, and the
    directories llm's two claude steps write."""
    text = lambda rel: (FEC.parent / rel).read_text(encoding="utf-8")
    crec = text("fec/collect/fetch_congressional_record.py")
    assert "DB=os.path.join(B,'social_media.db')" in crec and "PRAGMA journal_mode=WAL" in crec
    repo = text("fec/legislation/update_bill_repository.py")
    assert 'default=Path(ROOT) / "bill_repository_live.json"' in repo and "dir=Path(ROOT)" in repo and "write_repository_atomic" in repo
    adm = text("fec/legislation/refresh_sentinel_admissions.py")
    for written in ("root/'data/legislation/sentinel_admission_checkpoint.json'", "root/'data/legislation/sentinel_evidence'",
                    "root/'audits/sentinel_admissions'", "RELATIVE"):
        assert written in adm, written
    assert "data/legislation/sentinel_admissions.json" in text("fec/legislation/sentinel_admissions.py")
    ref = text("fec/members/member_reference_layer.py")
    for written in ("os.path.join(B, 'data', 'members', 'member_reference_cache_fable.jsonl')", "data/members/member_reference_classification_receipt.json"):
        assert written in ref, written
    assert "member_references.json" in ref and "dump_atomic" in ref                      # the root-level output no bind mount can allow


def import_closure(entry):
    """The fec/ modules a collector script imports, transitively, by module name (an over-approximation: same-named modules all count)."""
    import ast
    modules = {}
    for path in FEC.rglob("*.py"):
        if not {"tests", "attic", "docs", "research", "audit"} & set(path.relative_to(FEC).parts):
            modules.setdefault(path.stem, []).append(path)
    start = (FEC / entry).resolve()
    seen, todo = {start}, [start]
    while todo:
        tree = ast.parse(todo.pop().read_text(encoding="utf-8", errors="replace"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[-1] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names |= ({node.module.split(".")[-1]} if node.module else set()) | {a.name for a in node.names}
        for name in names:
            for path in modules.get(name, []):
                if path.resolve() not in seen:
                    seen.add(path.resolve())
                    todo.append(path.resolve())
    return seen


COLLECTOR_ENTRIES = [   # the scheduled collectors and claude steps of fec/weekly_refresh.py (design 1.4)
    "collect/fetch_congressional_record.py", "legislation/refresh_legislation_discovery.py", "legislation/refresh_sentinel_admissions.py",
    "collect/refresh_fec_money.py", "legislation/refresh_vote_sources.py", "legislation/update_bill_repository.py",
    "legislation/refresh_billstatus_bulk.py", "members/fetch_interviews.py", "collect/refresh_cspan_wj.py",
    "members/wikipedia_biographies.py", "collect/refresh_press_stream.py", "audit_collection.py", "collect/build_press_engine_feed.py",
    "collect/fold_promoted_press_runs.py", "members/member_reference_layer.py",
]


@needs_fec
def test_no_scheduled_collector_reads_or_writes_under_localappdata_melange_except_the_billstatus_default():
    """The trace behind 'no LOCALAPPDATA mount': the only files in the collectors' import closures that name LOCALAPPDATA are
    paths.py (constants), run_pipeline.py (string joins at import; its preflight is all update_bill_repository imports) and
    refresh_billstatus_bulk.py (a default that --raw-root overrides).
    A new hit means a mount decision: add the subfolder to the compose file, read-only unless the collector writes there."""
    allowed = {"paths.py", "run_pipeline.py", "refresh_billstatus_bulk.py"}
    hits = {}
    for entry in COLLECTOR_ENTRIES:
        assert (FEC / entry).is_file(), entry
        for path in import_closure(entry):
            if re.search(r"LOCALAPPDATA|lda_rebuild", path.read_text(encoding="utf-8", errors="replace")):
                hits.setdefault(path.name, set()).add(entry)
    assert set(hits) <= allowed, {k: sorted(v) for k, v in hits.items() if k not in allowed}
    assert "refresh_billstatus_bulk.py" in hits and "--raw-root" in (FEC / "legislation/refresh_billstatus_bulk.py").read_text(encoding="utf-8")


def test_the_gate_workflow_exports_every_host_variable_the_compose_file_needs():
    needed = set(re.findall(r"\$\{([A-Z_]+):\?", COMPOSE_TEXT)) - {"IMAGE_COMPUTE_COLLECTOR"}
    exported = {v for loop in re.findall(r"for v in ([A-Z_ ]+); do export", WORKFLOW_TEXT) for v in loop.split()}
    assert exported == needed, (sorted(needed - exported), sorted(exported - needed))   # an unset ${VAR:?} breaks `docker compose config`
    assert HOST_VARIABLES <= needed


# ---------------------------------------------------------------- workflow

def test_workflow_jobs_gate_smoke_scan_and_sbom_and_build_without_cache_or_floating_pins():
    jobs = WORKFLOW["jobs"]
    assert set(jobs) == {"test", "build", "sbom", "scan", "digests"} and WORKFLOW["permissions"] == {}
    build = {s.get("id") or s.get("name"): s for s in jobs["build"]["steps"]}
    push = build["push"]["with"]
    assert push["no-cache"] is True and push["provenance"] is False and push["file"] == "ops/docker/compute-collector.Dockerfile"
    assert "push=true" in push["outputs"] and "rewrite-timestamp=true" in push["outputs"]
    assert any("smoke_collector.sh" in s.get("run", "") for s in jobs["build"]["steps"])
    assert "security_gate.py images --ci --compose-json" in jobs["scan"]["steps"][-1]["run"]
    assert any(str(s.get("uses", "")).startswith("anchore/sbom-action@") for s in jobs["sbom"]["steps"])
    assert jobs["build"]["permissions"] == {"contents": "read", "packages": "write"} and jobs["scan"]["permissions"]["packages"] == "read"
    assert ":latest" not in WORKFLOW_TEXT and "pull_request_target" not in WORKFLOW_TEXT
    trigger = WORKFLOW.get("on") or WORKFLOW.get(True)
    assert trigger["push"] == {"tags": ["v*"]} and "workflow_dispatch" in trigger                      # release.yml's tags: release.py waits for this run


def test_workflow_uses_the_same_action_pins_as_release_yml():
    release = (WF_DIR / "release.yml").read_text(encoding="utf-8")
    pins = lambda text: dict(re.findall(r"uses:\s*([\w./-]+)@([0-9a-f]{40})\s+# v\d+", text))
    ours, theirs = pins(WORKFLOW_TEXT), pins(release)
    assert ours and all(theirs.get(action) == sha for action, sha in ours.items()), {a: s for a, s in ours.items() if theirs.get(a) != s}


# ---------------------------------------------------------------- entrypoint and smoke script under sh

needs_sh = pytest.mark.skipif(SH is None, reason="no sh on PATH")


def entrypoint(tmp_path, secrets: dict, *command):
    sdir = tmp_path / "secrets"
    sdir.mkdir(exist_ok=True)
    for name, data in secrets.items():
        (sdir / name).write_bytes(data)
    env = {**os.environ, "SECRETS_DIR": str(sdir), "HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
    for var in ("GOVINFO_KEY", "FEC_API_KEY"):
        env.pop(var, None)
    return subprocess.run([SH, str(OPS / "compute" / "collector-entrypoint.sh"), *command], env=env, capture_output=True, text=True, timeout=60)


@needs_sh
def test_entrypoint_stages_the_login_privately_and_exports_key_files_without_touching_the_secrets(tmp_path):
    login = b'{"fake": "login"}'
    done = entrypoint(tmp_path, {"claude_credentials": login, "govinfo_key": b"gov-key-value\n", "fec_data_gov": b"fec-key-value"},
                      sys.executable, "-c", "import os; print(os.environ['GOVINFO_KEY'], os.environ['FEC_API_KEY'], os.environ['CLAUDE_CONFIG_DIR'], oct(os.umask(0)))")
    assert done.returncode == 0, done.stderr
    assert done.stdout.split()[:2] == ["gov-key-value", "fec-key-value"]            # command substitution strips the newline
    assert done.stdout.split()[-1] != "0o77"                                      # the owner-only umask stayed inside the login copy
    staged = tmp_path / "claude" / ".credentials.json"
    assert staged.read_bytes() == login and (tmp_path / "secrets" / "claude_credentials").read_bytes() == login
    if os.name != "nt":
        assert stat.S_IMODE(staged.stat().st_mode) == 0o600 and stat.S_IMODE(staged.parent.stat().st_mode) == 0o700


@needs_sh
def test_entrypoint_without_secrets_still_runs_the_command_and_creates_no_login_dir(tmp_path):
    done = entrypoint(tmp_path, {}, sys.executable, "-c", "import os; print('GOVINFO_KEY' in os.environ)")
    assert done.returncode == 0 and done.stdout.strip() == "False" and not (tmp_path / "claude").exists()
    assert entrypoint(tmp_path, {}, sys.executable, "-c", "raise SystemExit(5)").returncode == 5      # exec: the job's exit code is the container's


FAKE_DOCKER = r'''#!/bin/sh
all="$*"
case "$all" in
  *platform.python_version*) echo "${FAKE_PY:-3.12.10}" ;;
  *"import json, sqlite3"*) ;;
  *getpreferredencoding*) echo cp1252 ;;
  *time.strftime*) echo CST ;;
  *"chromium --version"*) echo "Chromium 141.0.7390.65 built on Debian GNU/Linux 13" ;;
  *"chromium --headless"*) echo "<html><head></head><body></body></html>" ;;
  *"node --version"*) echo v22.23.3 ;;
  *"node -e"*) echo pw-ok ;;
  *"claude --version"*) echo "${FAKE_CLAUDE:-2.1.286} (Claude Code)" ;;
  *claude_credentials*) exit "${FAKE_LOGIN_EXIT:-0}" ;;
  *"find /"*) printf '%s' "${FAKE_FOUND:-}" ;;
  *"env | cut"*) printf 'PATH\nGPG_KEY\nLANG\n%s' "${FAKE_ENV:-}" ;;
  *"ls -A /run/secrets"*) printf '%s' "${FAKE_SECRETS:-}" ;;
  *"id -u"*) echo 10001 ;;
  *) echo "unexpected docker call: $all" >&2; exit 99 ;;
esac
'''
IMAGE = "ghcr.io/pjg93-222/compute-collector@sha256:" + "ab" * 32


def smoke(tmp_path, image=IMAGE, py="3.12.10", claude="2.1.286", **fake_env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
    (bin_dir / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", **fake_env}
    return subprocess.run([SH, str(OPS / "compute" / "smoke_collector.sh"), image, py, claude], env=env, capture_output=True, text=True, timeout=60)


@needs_sh
def test_smoke_script_passes_a_healthy_image_and_names_every_check(tmp_path):
    done = smoke(tmp_path)
    assert done.returncode == 0, done.stderr + done.stdout
    for check in ("python 3.12.10", "python imports", "cp1252", "chromium headless", "playwright + chromium", "claude 2.1.286",
                  "login staged privately", "no credentials in the image"):
        assert check in done.stdout, check
    assert "smoke passed" in done.stdout


@needs_sh
@pytest.mark.parametrize("fake_env, message", [
    ({"FAKE_FOUND": "/root/.credentials.json"}, "credential-like files"),
    ({"FAKE_ENV": "\nANTHROPIC_API_KEY"}, "credential-like environment variables"),
    ({"FAKE_SECRETS": "claude_credentials"}, "/run/secrets is not empty"),
    ({"FAKE_LOGIN_EXIT": "1"}, "login secret was not staged"),
    ({"FAKE_CLAUDE": "2.0.0"}, "claude --version"),
])
def test_smoke_script_fails_on_a_baked_credential_a_wrong_cli_or_a_broken_login_staging(tmp_path, fake_env, message):
    done = smoke(tmp_path, **fake_env)
    assert done.returncode == 1 and message in done.stderr, done.stderr


@needs_sh
def test_smoke_script_refuses_a_tag_and_a_wrong_python(tmp_path):
    assert smoke(tmp_path, image="ghcr.io/pjg93-222/compute-collector:latest").returncode == 2
    done = smoke(tmp_path, py="3.12.15")
    assert done.returncode == 1 and "python is not 3.12.15" in done.stderr
