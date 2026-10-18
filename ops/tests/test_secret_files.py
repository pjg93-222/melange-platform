"""Tests for the secret-file staging in fec/compute_engine.py (Paul 2026-10-18): launch() writes the API keys a container needs into
one-line files in a per-run private folder, on the container path only, and the folder is deleted when the run ends.

Fake keys under tmp_path only: no real keys.json, credentials file or key is opened; no Docker, no network, no live tree.  Every key
below carries a CANARY marker so a leak into an error, a log line or an argument is a plain string search.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
FEC = REPO / "fec"
sys.path.insert(0, str(FEC))
import compute_engine as CE  # noqa: E402

COLLECT_IMG = "ghcr.io/pjg93-222/compute-collector@sha256:" + "a" * 64
CHAIN_IMG = "ghcr.io/pjg93-222/compute-melange@sha256:" + "b" * 64
GOV, FECK, LDA = "CANARY-gov-1b8e5d0a93c7f642", "CANARY-fec-7f3a91c2d4e8b605", "CANARY-lda-must-never-be-staged"
ENGINE_VARS = tuple(f"MELANGE_ENGINE_{k}" for k in ("COLLECT", "CHAIN", "OFFICE"))
CREC = "fec/collect/fetch_congressional_record.py"
ADMISSIONS = "fec/legislation/refresh_sentinel_admissions.py"


@pytest.fixture(autouse=True)
def world(monkeypatch, tmp_path):
    for var in (*ENGINE_VARS, *CE.ALL_SECRET_VARS, *CE.FORWARD, "GOVINFO_KEY", "FEC_API_KEY", "GLASSBOX_EVENTS_DIR", "IMAGE_OFFICE_CALC"):
        monkeypatch.delenv(var, raising=False)
    home, temp, root = tmp_path / "home", tmp_path / "tmp", tmp_path / "tree"
    for folder in (home, temp, root / "fec", root / "ops" / "compute"):
        folder.mkdir(parents=True)
    for name in ("collectors", "melange", "office-calc"):
        (root / "ops" / "compute" / f"{name}.compose.yaml").write_text("name: x\n", encoding="utf-8")
    for var in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(var, str(home))                      # the CLI login and keys.json are looked up under the home dir
    monkeypatch.setattr(tempfile, "tempdir", str(temp))         # the secret root and the empty scratch file go under tmp_path
    monkeypatch.setattr(CE.paths, "ROOT", str(root))
    monkeypatch.setattr(CE.paths, "LOCAL_MELANGE", str(tmp_path / "local"))
    monkeypatch.setattr(CE.paths, "DRAFT", str(tmp_path / "draft"))
    monkeypatch.setattr(CE.paths, "MELANGE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MELANGE_ENGINE_COLLECT", "container")
    monkeypatch.setenv("IMAGE_COMPUTE_COLLECTOR", COLLECT_IMG)
    monkeypatch.setenv("IMAGE_COMPUTE_MELANGE", CHAIN_IMG)
    CE.reset()
    yield SimpleNamespace(home=home, temp=temp, root=root, secrets=temp / "melange-secrets")
    CE.reset()


def keys_json(w, doc=None, raw=None):
    path = w.home.joinpath(*CE.KEYS_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(doc or {"data_gov": {"keys": [FECK, "second"]}, "lda": {"keys": [LDA]}}),
                    encoding="utf-8")


def govinfo_file(w, data=b"\xef\xbb\xbf" + GOV.encode() + b"\r\n"):    # as a Notepad-saved file reads: BOM and CRLF
    (w.root / "fec" / "govinfo_key.txt").write_bytes(data)


def credentials(w):
    path = w.home / ".claude" / ".credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"canary": "%s"}' % GOV, encoding="utf-8")
    return path


def collect(w, **kwargs):
    return CE.launch(str(w.root / CREC), ["crec"], root=w.root, cwd=str(w.root / "fec"), **kwargs)


def admissions(w, **kwargs):
    return CE.launch(str(w.root / ADMISSIONS), [], root=w.root, cwd=str(w.root), **kwargs)


def fake_runs(monkeypatch, fake):
    """run_bounded replaced for everything but icacls, which stays real: it is how the folder is made private."""
    real = CE.run_bounded
    monkeypatch.setattr(CE, "run_bounded", lambda argv, **kw: real(argv, **kw) if argv[0] == "icacls" else fake(argv, **kw))


def folders(w):
    return sorted(p.name for p in w.secrets.iterdir()) if w.secrets.exists() else []


def leaked(text):
    return any(c in text for c in (GOV, FECK, LDA))


# ------------------------------------------------------------------------------------------------------- staging: what, where

def test_collect_stages_one_line_files_from_where_the_windows_collectors_look(world):
    keys_json(world)
    govinfo_file(world)
    got = collect(world)
    folder = world.secrets / got.container                         # named for the container, so release() and reap() find it
    assert folders(world) == [got.container] and got.container.startswith("melange-collect-")
    assert sorted(p.name for p in folder.iterdir()) == ["fec_data_gov", "govinfo_key"]
    assert (folder / "govinfo_key").read_bytes() == GOV.encode() + b"\n"       # BOM and CRLF gone: one clean line
    assert (folder / "fec_data_gov").read_bytes() == FECK.encode() + b"\n"     # data_gov.keys[0] only
    assert not any(LDA.encode() in p.read_bytes() or b"second" in p.read_bytes() for p in folder.iterdir())
    assert got.env["GOVINFO_KEY_FILE_HOST"] == str(folder / "govinfo_key") and got.env["FEC_DATA_GOV_FILE_HOST"] == str(folder / "fec_data_gov")
    assert os.path.getsize(got.env["CLAUDE_CREDENTIALS_FILE_HOST"]) == 0       # not this service's secret: the empty scratch file
    assert not leaked(" ".join(got.argv) + " " + " ".join(map(str, got.env.values())) + repr(got))   # paths only


def test_the_environment_wins_over_the_files_as_in_the_windows_collectors(world, monkeypatch):
    keys_json(world)
    govinfo_file(world)
    monkeypatch.setenv("GOVINFO_KEY", "CANARY-env-gov")
    monkeypatch.setenv("FEC_API_KEY", "CANARY-env-fec")
    folder = world.secrets / collect(world).container
    assert (folder / "govinfo_key").read_bytes() == b"CANARY-env-gov\n" and (folder / "fec_data_gov").read_bytes() == b"CANARY-env-fec\n"
    given = collect(world, environ={**os.environ, "GOVINFO_KEY": "CANARY-given-gov"})      # the environ a caller passes is the one read
    assert (world.secrets / given.container / "govinfo_key").read_bytes() == b"CANARY-given-gov\n"


def test_a_variable_the_caller_already_set_is_respected_and_only_the_other_is_staged(world, monkeypatch):
    keys_json(world)
    govinfo_file(world)
    mine = world.temp / "mine.txt"
    mine.write_text("CANARY-callers-own", encoding="utf-8")
    monkeypatch.setenv("GOVINFO_KEY_FILE_HOST", str(mine))
    got = collect(world)
    assert got.env["GOVINFO_KEY_FILE_HOST"] == str(mine) and mine.read_text(encoding="utf-8") == "CANARY-callers-own"
    assert sorted(p.name for p in (world.secrets / got.container).iterdir()) == ["fec_data_gov"]
    monkeypatch.setenv("FEC_DATA_GOV_FILE_HOST", str(mine))
    CE.release(got)
    assert collect(world).env["FEC_DATA_GOV_FILE_HOST"] == str(mine) and folders(world) == []     # both given: nothing staged at all


def test_llm_points_at_the_cli_login_itself_and_copies_nothing(world):
    login = credentials(world)
    got = admissions(world)
    assert got.argv[4:6] == ["--profile", "llm"]
    assert got.env["CLAUDE_CREDENTIALS_FILE_HOST"] == str(login) and login.read_text(encoding="utf-8") == '{"canary": "%s"}' % GOV
    assert folders(world) == [] and not world.secrets.exists()                 # D-179: Compose mounts the login read-only; no copy
    assert not leaked(" ".join(got.argv) + repr(got))


def test_llm_without_a_login_file_stops_with_the_variable_named(world):
    with pytest.raises(CE.EngineError, match="CLAUDE_CREDENTIALS_FILE_HOST"):
        admissions(world)


def test_the_chain_and_the_host_never_stage(world, monkeypatch):
    keys_json(world)
    govinfo_file(world)
    monkeypatch.setenv("MELANGE_ENGINE_CHAIN", "container")
    CE.reset()
    chain = CE.launch(str(world.root / "fec" / "run_pipeline.py"), [], root=world.root, cwd=str(world.root / "fec"))
    assert chain.container.startswith("melange-chain-") and not world.secrets.exists()
    monkeypatch.setenv("MELANGE_ENGINE_COLLECT", "windows")
    CE.reset()
    monkeypatch.setattr(CE, "_stage_secrets", lambda *a, **k: pytest.fail("the host path staged a secret"))
    script = str(world.root / CREC)
    assert CE.launch(script, ["crec"], root=world.root) == CE.Launch([sys.executable, script, "crec"], None, None, None)
    assert not world.secrets.exists()


# --------------------------------------------------------------------------------------------- a key that cannot be used: fail closed

def test_no_key_anywhere_is_a_named_error_and_nothing_is_left(world):
    with pytest.raises(CE.EngineError) as error:
        collect(world)
    assert "GOVINFO_KEY_FILE_HOST" in str(error.value) and "GOVINFO_KEY" in str(error.value) and "govinfo_key.txt" in str(error.value)
    govinfo_file(world)
    with pytest.raises(CE.EngineError) as error:       # the first file was written before this one failed: it must not stay
        collect(world)
    assert "FEC_DATA_GOV_FILE_HOST" in str(error.value) and "FEC_API_KEY" in str(error.value) and "keys.json" in str(error.value)
    assert folders(world) == []


@pytest.mark.parametrize("raw", ["not json", "[]", '{"data_gov": {}}', '{"data_gov": {"keys": []}}', '{"data_gov": {"keys": [""]}}',
                                 '{"data_gov": {"keys": ["  "]}}', '{"data_gov": {"keys": [7]}}',
                                 json.dumps({"data_gov": {"keys": [FECK + "\n" + LDA]}})])
def test_an_unusable_keys_json_is_refused_without_quoting_it(world, raw):
    govinfo_file(world)
    keys_json(world, raw=raw)
    with pytest.raises(CE.EngineError, match="FEC_DATA_GOV_FILE_HOST") as error:
        collect(world)
    assert not leaked(str(error.value) + repr(error.value)) and folders(world) == []


def test_a_multi_line_govinfo_file_is_refused(world):
    keys_json(world)
    govinfo_file(world, GOV.encode() + b"\n" + LDA.encode() + b"\n")
    with pytest.raises(CE.EngineError, match="GOVINFO_KEY_FILE_HOST") as error:
        collect(world)
    assert not leaked(str(error.value)) and folders(world) == []


def test_a_failure_after_staging_takes_the_files_back(world):
    keys_json(world)
    govinfo_file(world)
    with pytest.raises(CE.EngineError, match="outside the tree"):
        CE.launch(str(world.root / CREC), [], root=world.root, cwd=str(world.root.parent))
    assert folders(world) == []


def test_an_image_that_cannot_be_verified_stages_nothing(world, monkeypatch):
    keys_json(world)
    govinfo_file(world)
    monkeypatch.delenv("IMAGE_COMPUTE_COLLECTOR")
    fake_runs(monkeypatch, lambda argv, **kw: SimpleNamespace(stdout="", stderr="no manifest", returncode=1))
    monkeypatch.setattr(CE, "_stage_secrets", lambda *a, **k: pytest.fail("a key was staged for a run that cannot start"))
    with pytest.raises(CE.EngineError, match="no verified image"):
        collect(world)
    assert not world.secrets.exists()


def test_a_planted_link_or_junction_as_the_secret_root_is_refused(world):
    keys_json(world)
    govinfo_file(world)
    elsewhere = world.temp / "elsewhere"
    elsewhere.mkdir()
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(world.secrets), str(elsewhere)], check=True, capture_output=True)
        else:
            os.symlink(elsewhere, world.secrets, target_is_directory=True)
    except (OSError, subprocess.CalledProcessError) as error:
        pytest.skip("cannot create a link here: %s" % type(error).__name__)
    try:
        with pytest.raises(CE.EngineError, match="symbolic link or junction") as error:
            collect(world)
        assert str(world.secrets) in str(error.value) and not leaked(str(error.value))
        assert list(elsewhere.iterdir()) == []                                 # nothing was written through it
    finally:
        os.rmdir(world.secrets)                                                # removes the link, not its target


# ------------------------------------------------------------------------------------------------ deletion: release, reap, sweep

def test_release_deletes_the_folder_and_is_safe_to_repeat(world, capsys, caplog):
    caplog.set_level(0)
    keys_json(world)
    govinfo_file(world)
    got = collect(world)
    assert folders(world) == [got.container]
    CE.release(got)
    assert folders(world) == []
    CE.release(got)                                                            # a second call, or a host Launch: nothing happens
    CE.release(CE.Launch(["x"], None, None, None))
    captured = capsys.readouterr()
    assert not leaked(captured.out + captured.err + caplog.text)               # the success path prints and logs no key


def test_reap_removes_the_container_first_and_then_the_files_even_if_docker_fails(world, monkeypatch):
    keys_json(world)
    govinfo_file(world)
    got = collect(world)
    seen = []

    def run(argv, **kw):
        seen.append((argv[1], (world.secrets / got.container).exists()))
        if argv[1] == "stop":
            raise OSError("docker stop failed")

    monkeypatch.setattr(CE, "run_bounded", run)
    CE.reap(got)
    assert seen == [("stop", True), ("rm", True)]                              # the files were still there while the container could be
    assert folders(world) == []                                                # ... and are gone after it is removed


def test_release_ignores_a_name_that_is_not_a_plain_folder_under_the_secret_root(world):
    victim = world.temp / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep", encoding="utf-8")
    world.secrets.mkdir()
    for name in ("..", ".", "../victim", str(victim), "", "a/b"):
        CE.release(SimpleNamespace(container=name))
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "keep" and world.secrets.is_dir()


def test_the_next_staging_sweeps_a_crash_leftover_but_not_a_fresh_sibling(world):
    keys_json(world)
    govinfo_file(world)
    world.secrets.mkdir()
    old, fresh = world.secrets / "melange-collect-0ld0ld0ld0", world.secrets / "melange-collect-fresh00000"
    for folder in (old, fresh):
        folder.mkdir()
        (folder / "govinfo_key").write_text("CANARY-left-behind\n", encoding="utf-8")
    past = time.time() - CE.SECRET_TTL - 60
    os.utime(old, (past, past))
    got = collect(world)
    assert folders(world) == sorted([fresh.name, got.container])


# ------------------------------------------------------------------------------------------------- no key value anywhere else

@pytest.mark.parametrize("case", ["icacls", "write", "junk", "multi"])
def test_no_key_value_reaches_an_error_its_chain_or_its_traceback(world, monkeypatch, capsys, caplog, case):
    if case == "icacls" and os.name != "nt":
        pytest.skip("POSIX uses the mode bits, not icacls")
    caplog.set_level(0)
    keys_json(world)
    govinfo_file(world)
    real_open = os.open

    def refuse(path, *args, **kwargs):                                         # the secret file cannot be written
        if str(world.secrets) in str(path):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    if case == "icacls":                                                       # icacls fails and says whatever it likes
        monkeypatch.setattr(CE, "run_bounded", lambda argv, **kw: SimpleNamespace(returncode=5, stdout=FECK, stderr=GOV))
    elif case == "write":
        monkeypatch.setattr(os, "open", refuse)
    elif case == "junk":
        keys_json(world, raw='{"data_gov": {"keys": ["%s"' % FECK)
    else:
        keys_json(world, raw=json.dumps({"data_gov": {"keys": [FECK + "\n" + GOV]}}))
    with pytest.raises(CE.EngineError) as error:
        collect(world)
    chain, link = [], error.value
    while link is not None and link not in chain:
        chain.append(link)
        link = link.__cause__ or link.__context__
    text = "".join(traceback.format_exception(error.value)) + " ".join(repr(e) + str(e) for e in chain)
    captured = capsys.readouterr()
    assert not leaked(text + captured.out + captured.err + caplog.text) and folders(world) == []


# --------------------------------------------------------------------------------------------------------- the folder is private

@pytest.mark.skipif(os.name != "nt", reason="icacls is Windows")
def test_on_windows_only_this_user_can_open_the_folder_and_the_file_inside_it(world):
    keys_json(world)
    govinfo_file(world)
    got = collect(world)
    folder = world.secrets / got.container
    me = os.environ["USERNAME"].lower()
    for target in (folder, folder / "govinfo_key"):
        out = subprocess.run(["icacls", str(target)], capture_output=True, text=True, check=True).stdout
        entries = re.findall(r"(\S+):\((?:[A-Z]+\))+", out.replace(str(target), ""))
        assert len(entries) == 1 and entries[0].lower().endswith(me), (target, out)       # no SYSTEM, Administrators, Users, Everyone
        assert "(F)" in out
    assert not [line for line in (folder / "govinfo_key").read_bytes().splitlines() if line.endswith(b"\r")]    # one line, LF, no CR


@pytest.mark.skipif(os.name == "nt", reason="mode bits are the POSIX guard")
def test_on_posix_the_folder_and_file_are_owner_only(world):
    keys_json(world)
    govinfo_file(world)
    folder = world.secrets / collect(world).container
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700 and stat.S_IMODE((folder / "govinfo_key").stat().st_mode) == 0o600


# ------------------------------------------------------------------------ the two callers that do not reap on a normal exit

class Child:
    pid = 4242

    def __init__(self, on_wait=None, code=0, interrupt=False):
        self.on_wait, self.code, self.interrupt = on_wait, code, interrupt

    def wait(self, timeout=None):
        if self.on_wait:
            self.on_wait()
        if self.interrupt:
            raise KeyboardInterrupt
        return self.code

    def poll(self):
        return self.code if not self.interrupt else None


@pytest.mark.parametrize("how", ["exit", "popen fails", "log fails", "interrupt"])
def test_daily_media_refresh_leaves_no_secret_file_on_any_path(world, monkeypatch, tmp_path, how):
    from collect import daily_media_refresh as media
    fake_runs(monkeypatch, lambda argv, **kw: None)                            # reap's docker stop / rm
    monkeypatch.setattr(media, "terminate_tree", lambda process: None)
    during = []

    real_popen = subprocess.Popen

    def popen(argv, **kwargs):
        if argv[0] == "icacls":                                                # the folder's ACL is applied for real
            return real_popen(argv, **kwargs)
        if how == "popen fails":
            raise FileNotFoundError("docker")
        return Child(lambda: during.append(folders(world)), interrupt=how == "interrupt")

    monkeypatch.setattr(media.subprocess, "Popen", popen)
    crec = next(step for step in media.source_steps(datetime.date(2026, 10, 12)) if step.name == "crec")
    environment = {**os.environ, "GOVINFO_KEY": GOV, "FEC_API_KEY": FECK, "MONEYBALL_ROOT": str(tmp_path)}   # keys from the environment: no file is read
    log = tmp_path / ("no-such-folder" if how == "log fails" else ".") / "crec.log"      # "log fails": the log cannot be opened after launch()
    if how == "exit":
        assert media.run_process(crec, REPO, log, environment) == 0
    else:
        with pytest.raises(FileNotFoundError if how in ("popen fails", "log fails") else KeyboardInterrupt):
            media.run_process(crec, REPO, log, environment)
    assert not during or (len(during) == 1 and len(during[0]) == 1)             # the folder existed while the child ran
    assert (how in ("popen fails", "log fails")) == (not during)
    assert folders(world) == []


@pytest.mark.parametrize("how", ["exit", "popen fails"])
def test_the_congress_pipeline_leaves_no_secret_file_on_any_path(world, monkeypatch, how):
    from legislation import refresh_congress_pipeline as pipeline
    keys_json(world)
    govinfo_file(world)
    credentials(world)
    monkeypatch.setenv("MELANGE_ENGINE_CHAIN", "container")
    monkeypatch.delenv("MELANGE_PIPELINE_LOCK_TOKEN", raising=False)
    monkeypatch.delenv("MELANGE_PIPELINE_LOCK_OWNER_PID", raising=False)
    fake_runs(monkeypatch, lambda argv, **kw: None)
    monkeypatch.setattr(pipeline, "CODE", world.root / "fec")
    monkeypatch.setattr(pipeline, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "DISCOVERY_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setitem(sys.modules, "run_pipeline", SimpleNamespace(preflight_dependencies=lambda: None))
    monkeypatch.setitem(sys.modules, "legislation.sync_sentinel_bill_ledger", SimpleNamespace(runtime=lambda: ("fixture", "node", "modules")))
    during = []

    real_popen = subprocess.Popen

    def popen(command, **kwargs):
        if command[0] == "icacls":                                             # the folder's ACL is applied for real
            return real_popen(command, **kwargs)
        if "docker" not in command[0]:
            return SimpleNamespace(pid=1, poll=lambda: 0, returncode=0)        # the host calibration child
        during.append(folders(world))
        if how == "popen fails":
            raise FileNotFoundError("docker")
        return SimpleNamespace(pid=2, poll=lambda: 4, wait=lambda timeout=None: 4, returncode=4)

    monkeypatch.setattr(pipeline.subprocess, "Popen", popen)
    try:
        pipeline.run(world.root)
    except Exception:
        pass
    assert during and all(len(seen) <= 1 for seen in during) and any(len(seen) == 1 for seen in during)    # a collect step had its folder
    assert folders(world) == []
