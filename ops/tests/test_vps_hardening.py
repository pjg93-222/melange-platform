"""Regression tests for the Opus review of the VPS side (updater, host scripts, cloud-init, compose, CI).

Most checks run on the laptop (fake runner, temp dirs, static reads of the shipped files). These are POSIX-only (symlinks,
FIFOs, modes, bash) and are skipped on Windows, so they run only in Linux CI, and a revert of what they cover fails nowhere else:
test_write_atomic_never_writes_through_a_planted_symlink, test_inbox_symlink_is_refused_unread_and_removed,
test_inbox_fifo_is_refused_without_blocking, test_force_rmtree_removes_links_without_chmod_through_them,
test_verify_tree_refuses_a_fifo_where_the_manifest_expects_a_file, and test_ping_goes_green_only_when_release_warnings_and_tunnel_are_all_clean
(bash + jq as well; test_ping_checks_warnings_and_cloudflared_health_statically covers its shape on the laptop).
The whole module is skipped where PyYAML is missing."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest

from ops.tests.test_compose_policy import BUILDER, WF_DIR
from ops.tests.test_melange_update import FARM, R1, R2, SNAP_A, SNAP_B, digest, image, mu, rig, keys  # noqa: F401  (fixtures)


class Crash(BaseException):
    """A kill, OOM or reboot: no `except Exception` in the updater may swallow it."""

yaml = pytest.importorskip("yaml", reason="PyYAML not installed; static VPS checks skipped")

OPS = Path(__file__).resolve().parents[1]
posix = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks, FIFOs or modes")
needs_jq = pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")


def text(rel):
    return (OPS / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


CLOUD = text("vps/cloud-init.yaml")
CLOUD_FILES = {"/" + n: d.decode() for n, (d, _) in BUILDER.embedded(CLOUD).items()}      # host-files.toml rides in the payload
BOOT, OPS_SH = text("vps/melange-bootstrap.sh"), text("vps/melange-ops.sh")


@contextlib.contextmanager
def deadline(seconds=10):
    """A regression that opens a FIFO would hang the suite; turn that into a failure."""
    def boom(*_):
        raise TimeoutError("blocked (opening a FIFO?)")
    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


# --- H1: the push-key holder must not steer root's writes -----------------------------------------------------

def test_push_user_sftp_cannot_create_symlinks_or_hardlinks():
    assert "ForceCommand internal-sftp -P symlink,hardlink" in CLOUD_FILES["/etc/ssh/sshd_config.d/10-melange.conf"]


def test_status_is_root_owned_and_backups_stage_in_a_root_only_dir_then_rename_into_the_outbox():
    assert re.search(r"^\s*install -d -o root -g root -m 0755 /srv/melange/push/status\b", BOOT, re.M)
    assert not re.search(r"-o melange-push[^\n]*\bstatus\b", BOOT)         # the push user only reads status/
    assert re.search(r"^\s*install -d -o root -g root -m 0700 /srv/data/\.backup-tmp\b", BOOT, re.M)
    assert "OUTBOX=/srv/data/.push/outbox" in OPS_SH and "STAGE=/srv/data/.backup-tmp" in OPS_SH
    assert 'mv -fT "$tmp" "$out"' in OPS_SH and 'tmp="$STAGE/$1.partial"' in OPS_SH
    assert '$PUSH/outbox/$d' not in OPS_SH and '"$2.partial"' not in OPS_SH   # no age output inside the push-owned directory


@posix
@pytest.mark.parametrize("target_exists", [True, False])
def test_write_atomic_never_writes_through_a_planted_symlink(tmp_path, target_exists):
    victim = tmp_path / "victim"
    if target_exists:
        victim.write_text("precious")
    (tmp_path / ".status.json.tmp").symlink_to(victim)
    mu.write_atomic(tmp_path / "status.json", "new")
    assert (victim.read_text() if target_exists else victim.exists()) == ("precious" if target_exists else False)
    final = tmp_path / "status.json"
    assert final.read_text() == "new" and not final.is_symlink()


# --- M1: the inbox belongs to the push user -----------------------------------------------------------------

@posix
def test_inbox_symlink_is_refused_unread_and_removed(rig, tmp_path):
    rig.release(R1, SNAP_A)
    real = rig.cfg.inbox / "manifest.json"
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(real.read_bytes())
    real.unlink()
    real.symlink_to(elsewhere)
    assert rig.apply() == "FAILED"
    assert "manifest.json refused" in rig.status()["reason"] and rig.calls == []
    assert not os.path.lexists(real) and elsewhere.exists()          # the link went, never its target


@posix
def test_inbox_fifo_is_refused_without_blocking(rig):
    rig.release(R1, SNAP_A)
    fifo = rig.cfg.inbox / "snapshot_manifest.json"
    fifo.unlink()
    os.mkfifo(fifo)
    with deadline():
        assert rig.apply() == "FAILED"
    assert "not a regular file" in rig.status()["reason"] and rig.calls == []
    assert not os.path.lexists(fifo)


def test_oversized_inbox_file_is_refused(rig, monkeypatch):
    rig.release(R1, SNAP_A)
    monkeypatch.setattr(mu, "MAX_INBOX_FILE", 64)
    assert rig.apply() == "FAILED"
    assert "larger than 64 bytes" in rig.status()["reason"] and rig.restores() == 0 and rig.calls == []


# --- M2: restic ids are full ids; hostile restores cannot steer chmod or hang the hasher ---------------------

@posix
def test_force_rmtree_removes_links_without_chmod_through_them(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    outside.chmod(0o644)
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "file_link").symlink_to(outside)
    (tree / "dir_link").symlink_to(tmp_path)
    mu.force_rmtree(tree)
    assert not tree.exists() and outside.exists() and stat.S_IMODE(outside.stat().st_mode) == 0o644
    link = tmp_path / "top_link"
    link.symlink_to(tmp_path)
    mu.force_rmtree(link)                                            # a top-level link is unlinked, not chmodded or followed
    assert not os.path.lexists(link) and tmp_path.exists()


@posix
def test_verify_tree_refuses_a_fifo_where_the_manifest_expects_a_file(rig, tmp_path):
    m = rig.release(R1, SNAP_A)
    by_sys = mu.check_snapshot_manifest(m, (rig.cfg.inbox / "snapshot_manifest.json").read_bytes())
    staging = tmp_path / "stg"
    shutil.copytree(rig.snapshots[SNAP_A][0] / FARM.lstrip("/"), staging)
    mu.verify_tree(staging, m, by_sys)                               # control: the honest tree verifies
    victim = staging / "melange" / "Moneyball.html"
    victim.unlink()
    os.mkfifo(victim)
    with deadline(), pytest.raises(mu.StepError, match="non-regular"):
        mu.verify_tree(staging, m, by_sys)


@pytest.mark.parametrize("short", ["a1" * 8, "A1" * 32, "a1" * 32 + "0", ""])
def test_restic_snapshot_id_must_be_the_full_lowercase_id(rig, short):
    m = rig.release(R1, SNAP_A)
    m["data_snapshot"]["restic_snapshot_id"] = short
    with pytest.raises(mu.StepError, match="data_snapshot"):
        mu.check_manifest(m)


# --- M3: `$` matches before a trailing newline; every validation must be a fullmatch -------------------------

@pytest.mark.parametrize("field", ["release_id", "image", "data_subdir", "restic_snapshot_id", "manifest_sha256", "restic_path"])
def test_a_trailing_newline_is_refused_by_every_manifest_validation(rig, field):
    m = rig.release(R1, SNAP_A)
    mu.check_manifest(json.loads(json.dumps(m)))                     # control: the untouched manifest passes
    holder = m if field == "release_id" else m["systems"]["melange"] if field in ("image", "data_subdir") else m["data_snapshot"]
    holder[field] += "\n"
    with pytest.raises(mu.StepError):
        mu.check_manifest(m)


def test_a_trailing_newline_in_a_snapshot_entry_hash_is_refused(rig):
    m = rig.release(R1, SNAP_A)
    snap = json.loads((rig.cfg.inbox / "snapshot_manifest.json").read_bytes())
    snap["files"][0]["sha256"] += "\n"
    raw = json.dumps(snap).encode()
    m["data_snapshot"]["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(mu.StepError, match="bad or duplicate"):
        mu.check_snapshot_manifest(m, raw)


# --- H2: a crash between the flip and save_state must not strand or destroy the previous release -------------------

def crash_on_up(rig, monkeypatch):
    real, armed = rig._docker, [True]

    def crashing(cmd):
        if "up" in cmd and armed[0]:
            raise Crash()
        return real(cmd)

    monkeypatch.setattr(rig, "_docker", crashing)
    return armed


def test_crash_after_the_flip_is_rolled_back_by_the_next_run_and_the_old_release_survives(rig, monkeypatch):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    env_r1 = rig.env()
    rig.release(R2, SNAP_B, seed="b", tag="2")
    armed = crash_on_up(rig, monkeypatch)
    with pytest.raises(Crash):
        rig.apply()
    assert all(rig.link(s).name == R2 for s in mu.SYSTEMS)                  # half-live: links and env already moved
    assert rig.state()["release_id"] == R1 and rig.state()["pending"]["rid"] == R2
    armed[0] = False
    rig.up_fail.add(digest("melange-desk", "2"))                             # and the new release is bad
    assert rig.apply() == "ROLLED_BACK", rig.status()
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS) and rig.env() == env_r1
    assert "pending" not in rig.state() and rig.state()["release_id"] == R1
    assert (rig.cfg.data / "melange" / "releases" / R1 / "Moneyball.html").read_bytes() == b"<html>a</html>"   # R1 intact
    assert {v["snapshot"] for v in rig.running.values()} == {SNAP_A}
    assert rig.restores() == 3                                               # R1, the crashed R2, and R2 once more


def first_release_crashed(rig, monkeypatch):
    rig.release(R1, SNAP_A)
    armed = crash_on_up(rig, monkeypatch)
    with pytest.raises(Crash):
        rig.apply()
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS)                    # half-live, and there is no earlier release
    pending = rig.state()["pending"]
    assert pending["rid"] == R1 and set(pending["prev"]) == set(mu.SYSTEMS) and not any(pending["prev"].values())
    armed[0] = False


def test_first_release_crash_after_the_flip_is_undone_and_the_same_manifest_deploys_on_rerun(rig, monkeypatch):
    first_release_crashed(rig, monkeypatch)
    assert rig.apply() == "OK", rig.status()                                  # recovery, then the same manifest from the inbox
    assert rig.state()["release_id"] == R1 and "pending" not in rig.state()
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS) and set(rig.running) == {n for v in mu.SYSTEMS.values() for n in v}


def test_first_release_crash_with_an_empty_inbox_leaves_nothing_serving(rig, monkeypatch):
    env0 = rig.env()
    first_release_crashed(rig, monkeypatch)
    for f in rig.cfg.inbox.iterdir():
        f.unlink()
    assert rig.apply() == "ROLLED_BACK" and rig.running == {}
    assert all(rig.link(s) is None for s in mu.SYSTEMS) and rig.env() == env0 and "pending" not in rig.state()
    rig.release(R1, SNAP_A)                                                   # not "already deployed with a different manifest"
    assert rig.apply() == "OK", rig.status()


def test_crash_then_a_good_release_ends_up_serving_it(rig, monkeypatch):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    rig.release(R2, SNAP_B, seed="b", tag="2")
    armed = crash_on_up(rig, monkeypatch)
    with pytest.raises(Crash):
        rig.apply()
    armed[0] = False
    assert rig.apply() == "OK", rig.status()
    assert all(rig.link(s).name == R2 for s in mu.SYSTEMS) and rig.state()["release_id"] == R2
    assert "pending" not in rig.state()


def test_recovery_with_an_empty_inbox_reports_rolled_back_and_leaves_no_pending(rig, monkeypatch):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    rig.release(R2, SNAP_B, seed="b", tag="2")
    armed = crash_on_up(rig, monkeypatch)
    with pytest.raises(Crash):
        rig.apply()
    armed[0] = False
    for f in rig.cfg.inbox.iterdir():
        f.unlink()
    assert rig.apply() == "ROLLED_BACK"
    assert "interrupted" in rig.status()["reason"] and rig.status()["serving_release_id"] == R1
    assert rig.apply() is None and all(rig.link(s).name == R1 for s in mu.SYSTEMS)


def test_a_failed_recovery_keeps_pending_and_leaves_the_inbox_alone(rig, monkeypatch):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    rig.release(R2, SNAP_B, seed="b", tag="2")
    armed = crash_on_up(rig, monkeypatch)
    with pytest.raises(Crash):
        rig.apply()
    armed[0] = False
    rig.up_fail.add(digest("melange-desk", "1"))                             # the old release will not start either
    assert rig.apply() == "FAILED" and "ROLLBACK FAILED" in rig.status()["reason"]
    assert rig.state()["pending"]["rid"] == R2 and (rig.cfg.inbox / "manifest.json").exists()
    rig.up_fail.clear()
    assert rig.apply() in ("OK", "ROLLED_BACK") and "pending" not in rig.state()


def test_stage_data_refuses_to_delete_a_directory_a_current_link_serves(rig):
    m = rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    m = rig.release(R1, SNAP_A)                                              # the same id again: its dir is the live one
    by_sys = mu.check_snapshot_manifest(m, (rig.cfg.inbox / "snapshot_manifest.json").read_bytes())
    with pytest.raises(mu.StepError, match="serves it"):
        mu.stage_data(rig.cfg, m, by_sys)
    assert (rig.cfg.data / "melange" / "releases" / R1 / "Moneyball.html").is_file()


def test_prune_keeps_the_return_point_of_an_interrupted_release(rig):
    from ops.tests.test_melange_update import fake_repoint
    rels = rig.cfg.data / "melange" / "releases"
    for name in (R1, R2):
        (rels / name).mkdir(parents=True)
    fake_repoint(rig.cfg.data / "melange" / "current", rels / R2)
    rig.cfg.state_dir.mkdir(parents=True, exist_ok=True)
    rig.cfg.state_file.write_text(json.dumps({"pending": {"rid": "x", "prev": {"melange": str(rels / R1)}}}))
    mu.prune(rig.cfg)
    assert (rels / R1).is_dir() and (rels / R2).is_dir()
    rig.cfg.state_file.write_text("{}")
    mu.prune(rig.cfg)
    assert not (rels / R1).exists() and (rels / R2).is_dir()


def test_the_update_service_is_never_time_boxed():
    assert re.search(r"^TimeoutStartSec=infinity$", text("vps/systemd/melange-update.service"), re.M)


# --- M4: a system that is new in a failed release has nothing to roll back to ---------------------------------

def test_rollback_takes_a_system_that_is_new_in_the_release_down_instead_of_restarting_it(rig):
    rig.release(R1, SNAP_A, systems={"melange", "mentat"})
    assert rig.apply() == "OK", rig.status()
    env_r1 = rig.env()
    rig.release(R2, SNAP_B, seed="b", tag="2")                               # terrier appears for the first time
    rig.lie.add(digest("terrier-web", "2"))
    n = len(rig.calls)
    assert rig.apply() == "ROLLED_BACK", rig.status()
    assert rig.link("terrier") is None and rig.link("melange").name == R1 and rig.link("mentat").name == R1
    assert rig.env() == env_r1
    after = [c for c in rig.calls[n:] if "compose" in c]
    stop = next(c for c in after if "stop" in c)
    assert {"terrier-co05", "terrier-or05", "terrier-ct01"} <= set(stop) and "melange-desk" not in stop
    last_up = [c for c in after if "up" in c and "--wait" in c][-1]
    assert "melange-desk" in last_up and not any(s.startswith("terrier") for s in last_up)
    assert set(rig.running) == {"melange-desk", "mentat-console"}


# --- M5: "same snapshot" is a per-system fact, not the global one in state.json -------------------------------

def test_code_only_skip_compares_each_systems_own_snapshot_not_the_global_one(rig):
    from ops.tests.test_melange_update import fake_repoint
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    rig.release(R2, SNAP_B, seed="b")
    assert rig.apply() == "OK"
    for s in mu.SYSTEMS:                                                      # RUNBOOK section 3's manual rollback: links back to R1,
        fake_repoint(rig.cfg.data / s / "current", rig.cfg.data / s / "releases" / R1)   # state.json still says snapshot B
    assert rig.state()["restic_snapshot_id"] == SNAP_B
    rig.release("2026.10.14.1", SNAP_B, seed="b", tag="3")                    # a re-cut of R2's data with new code
    assert rig.apply() == "OK", rig.status()
    assert rig.restores() == 3                                                # R1, R2 and this one: the R1 dirs are not B
    assert {v["snapshot"] for v in rig.running.values()} == {SNAP_B}


# --- H3: Terrier may migrate its accounts DB on start; back each district up before the flip -------------------

DISTRICT_SERVICES = ["terrier-co05", "terrier-or05", "terrier-ct01"]
NOTE = "DB schema may be ahead; see RUNBOOK §10"


def deployed_r1(rig):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK", rig.status()
    assert rig.backups == []                                                  # the first Terrier deploy has no DB to protect


def test_a_terrier_image_change_backs_up_every_district_before_anything_moves(rig):
    deployed_r1(rig)
    rig.release(R2, SNAP_B, seed="b", tag="2")
    n = len(rig.calls)
    assert rig.apply() == "OK", rig.status()
    assert rig.backups == [(s, f"/state/pre-{R2}.sqlite3") for s in DISTRICT_SERVICES]
    run = rig.calls[n:]
    first_up = next(i for i, c in enumerate(run) if "up" in c)
    assert sum("manage.py" in c for c in run[:first_up]) == 3                 # all three backups precede the first compose up


def test_a_failed_terrier_backup_refuses_the_release_with_nothing_changed(rig):
    deployed_r1(rig)
    env_before = rig.env()
    rig.release(R2, SNAP_B, seed="b", tag="2")
    rig.backup_fail.add("terrier-or05")
    n = len(rig.calls)
    assert rig.apply() == "FAILED"
    assert "pre-release backup of terrier-or05 failed" in rig.status()["reason"]
    assert not any("up" in c for c in rig.calls[n:])
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS) and rig.env() == env_before
    assert "pending" not in rig.state() and not (rig.cfg.data / "melange" / "releases" / R2).exists()
    assert not (rig.cfg.serve / "compose.env.new").exists()


def test_code_only_for_terrier_takes_no_backup(rig):
    deployed_r1(rig)
    rig.release(R2, SNAP_A, tag="2", tags={"terrier": "1"})                  # melange and mentat move; Terrier's digest does not
    assert rig.apply() == "OK", rig.status()
    assert rig.backups == []


def test_a_district_without_an_accounts_db_yet_does_not_block_the_release(rig):
    deployed_r1(rig)
    rig.release(R2, SNAP_B, seed="b", tag="2")
    rig.backup_missing.update(DISTRICT_SERVICES)                             # `test -f /state/accounts.sqlite3` says absent
    assert rig.apply() == "OK", rig.status()
    assert rig.backups == []                                                  # nothing to copy, so no backup was attempted


def test_a_backup_failure_that_merely_says_does_not_exist_aborts_the_release(rig):
    deployed_r1(rig)
    env_before = rig.env()
    rig.release(R2, SNAP_B, seed="b", tag="2")
    rig.backup_lies.add("terrier-or05")                                       # the DB file is there; the words are not evidence
    assert rig.apply() == "FAILED"
    assert "pre-release backup of terrier-or05 failed" in rig.status()["reason"]
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS) and rig.env() == env_before and "pending" not in rig.state()


def test_a_district_that_cannot_say_whether_it_has_an_accounts_db_aborts_the_release(rig):
    deployed_r1(rig)
    rig.release(R2, SNAP_B, seed="b", tag="2")
    del rig.running["terrier-ct01"]                                           # `exec` cannot answer
    assert rig.apply() == "FAILED"
    assert "terrier-ct01" in rig.status()["reason"] and rig.backups == [(s, f"/state/pre-{R2}.sqlite3") for s in DISTRICT_SERVICES[:2]]
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS) and "pending" not in rig.state()


def test_a_previous_env_without_terrier_image_lines_counts_as_a_changed_image(rig):
    m = rig.release(R1, SNAP_A)
    prev = "DOMAIN=example.test\n"
    assert mu.terrier_image_changed(prev, m) and mu.terrier_image_changed("", m) and mu.terrier_image_changed(None, m)
    same = prev + "".join(f"IMAGE_TERRIER_{d}={m['systems']['terrier']['image']}\n" for d in ("CO05", "OR05", "CT01"))
    assert not mu.terrier_image_changed(same, m)
    assert mu.terrier_image_changed(same.replace("IMAGE_TERRIER_OR05=", "OTHER_OR05="), m)
    deployed_r1(rig)
    rig.cfg.env_file.write_text("".join(ln + "\n" for ln in rig.env().splitlines() if not ln.startswith("IMAGE_TERRIER_")))
    rig.release(R2, SNAP_A, tag="2", tags={"terrier": "1"})                  # Terrier's digest did not change, but nobody can tell
    assert rig.apply() == "OK", rig.status()
    assert rig.backups == [(s, f"/state/pre-{R2}.sqlite3") for s in DISTRICT_SERVICES]


def test_a_system_new_in_this_release_has_no_accounts_db_to_back_up(rig):
    rig.release(R1, SNAP_A, systems=("melange", "mentat"))
    assert rig.apply() == "OK", rig.status()
    rig.release(R2, SNAP_B, seed="b", tag="2")                               # Terrier appears for the first time; nothing runs yet
    assert rig.apply() == "OK", rig.status()
    assert rig.backups == []


def test_rollback_after_a_terrier_image_change_points_at_the_runbook(rig):
    deployed_r1(rig)
    rig.release(R2, SNAP_B, seed="b", tag="2")
    rig.lie.add(digest("terrier-web", "2"))
    assert rig.apply() == "ROLLED_BACK"
    assert NOTE in rig.status()["reason"]
    rig.release("2026.10.14.1", SNAP_B, seed="b", tag="3", tags={"terrier": "1"})   # Terrier's image is the deployed one: no note
    rig.lie.add(digest("melange-desk", "3"))
    assert rig.apply() == "ROLLED_BACK" and NOTE not in rig.status()["reason"]


def test_recovery_after_a_crash_in_a_terrier_release_carries_the_note(rig, monkeypatch):
    deployed_r1(rig)
    rig.release(R2, SNAP_B, seed="b", tag="2")
    armed = crash_on_up(rig, monkeypatch)
    with pytest.raises(Crash):
        rig.apply()
    armed[0] = False
    for f in rig.cfg.inbox.iterdir():
        f.unlink()
    assert rig.apply() == "ROLLED_BACK" and NOTE in rig.status()["reason"]


# --- M6: the heartbeat must go red when the tunnel is down or the last release left warnings -------------------

def run_ping(tmp_path, *, status, health="healthy", cid="abc123"):
    """melange-ops.sh ping against a scratch tree: paths rewritten, docker and curl faked, jq real. Returns the curl URL."""
    t = tmp_path.as_posix()
    script = tmp_path / "ops.sh"
    script.write_text(OPS_SH.replace("PUSH=/srv/melange/push", f"PUSH={t}/push").replace("/etc/melange/healthchecks.url", f"{t}/hc.url")
                      .replace("/srv/access/jwks.json", f"{t}/jwks.json"), newline="\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text('#!/bin/bash\ncase " $* " in\n *" ps -q "*) [ "$FAKE_CID" = none ] || echo "$FAKE_CID" ;;\n'
                                   ' *" inspect "*) echo "$FAKE_HEALTH" ;;\nesac\n', newline="\n")
    (bindir / "curl").write_text('#!/bin/bash\necho "${@: -1}" >> "$CURL_LOG"\n', newline="\n")
    for f in bindir.iterdir():
        f.chmod(0o755)
    (tmp_path / "push" / "status").mkdir(parents=True)
    (tmp_path / "push" / "status" / "status.json").write_text(json.dumps(status))
    (tmp_path / "push" / "outbox").mkdir()
    (tmp_path / "push" / "outbox" / "co05-2026-10-12.age").write_text("x")
    (tmp_path / "jwks.json").write_text("{}")
    (tmp_path / "hc.url").write_text("https://hc.example/ping/abc\n")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "FAKE_CID": cid, "FAKE_HEALTH": health,
           "CURL_LOG": f"{t}/curl.log"}
    p = subprocess.run(["bash", str(script), "ping"], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return (tmp_path / "curl.log").read_text().strip()


@posix
@needs_jq
@pytest.mark.parametrize("status, kw, ok", [
    ({"state": "OK", "warnings": []}, {}, True),
    ({"state": "OK", "warnings": ["cloudflared did not start"]}, {}, False),
    ({"state": "OK", "warnings": []}, {"health": "unhealthy"}, False),
    ({"state": "OK", "warnings": []}, {"health": "starting"}, False),
    ({"state": "OK", "warnings": []}, {"cid": "none"}, False),
    ({"state": "ROLLED_BACK", "warnings": []}, {}, False)])
def test_ping_goes_green_only_when_release_warnings_and_tunnel_are_all_clean(tmp_path, status, kw, ok):
    url = run_ping(tmp_path, status=status, **kw)
    assert url.endswith("/fail") != ok, url


def test_ping_checks_warnings_and_cloudflared_health_statically():
    assert "(.warnings // []) | length" in OPS_SH
    assert "docker inspect -f '{{.State.Health.Status}}'" in OPS_SH and '= healthy ] || why="$why cloudflared not healthy;"' in OPS_SH


# --- M7: disk headroom and image hygiene -----------------------------------------------------------------------

Usage = namedtuple("Usage", "total used free")


def test_a_restore_needs_15_gib_free_beyond_the_snapshot_itself(rig, monkeypatch):
    m = rig.release(R1, SNAP_A)
    need = int(m["data_snapshot"]["bytes"] * 1.05) + 1 + (15 << 30)
    monkeypatch.setattr(mu.shutil, "disk_usage", lambda p: Usage(1 << 40, 0, need - 2))
    assert rig.apply() == "FAILED" and "not enough free space" in rig.status()["reason"] and "15 GiB" in rig.status()["reason"]
    assert rig.restores() == 0
    rig.release(R1, SNAP_A)
    monkeypatch.setattr(mu.shutil, "disk_usage", lambda p: Usage(1 << 40, 0, need))
    assert rig.apply() == "OK", rig.status()


def test_a_good_release_prunes_old_images_but_never_volumes(rig):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    assert ["docker", "image", "prune", "-af", "--filter", "until=168h"] in rig.calls
    assert not any(a in ("volume", "--volumes", "-v", "down", "rm") for c in rig.calls for a in c)
    rig.release(R2, SNAP_B, tag="2")
    rig.lie.add(digest("melange-desk", "2"))
    n = len(rig.calls)
    assert rig.apply() == "ROLLED_BACK"
    assert ["docker", "image", "prune", "-af", "--filter", "until=168h"] not in rig.calls[n:]      # only after a good release


# --- M9: a tag build is the root of trust: no shared cache, and the tag is checked before it reaches buildx --------

WORKFLOW = yaml.safe_load((WF_DIR / "release.yml").read_text(encoding="utf-8"))
TAG_SHAPE = r"^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+$"


@pytest.mark.parametrize("job", ["build", "kit"])
def test_the_tag_is_validated_first_in_every_job_that_uses_it(job):
    steps = WORKFLOW["jobs"][job]["steps"]
    check = steps[0]
    assert check["env"] == {"TAG": "${{ github.ref_name }}"} and "${{" not in check["run"]
    assert TAG_SHAPE in check["run"] and "exit 1" in check["run"]
    for ok in ("v2026.10.12.1", "v2026.10.12.17"):
        assert re.fullmatch(TAG_SHAPE, ok)
    for bad in ("v2026.10.12.1,push=false", "v2026.10.12.1\n", "v2026.10.12", "2026.10.12.1", "v2026.10.12.1 x", "vX.1.2.3",
                "v2026.10.12.1,type=local,dest=/tmp"):
        assert not re.fullmatch(TAG_SHAPE, bad), bad
    used = [i for i, s in enumerate(steps) if "github.ref_name" in str(s.get("with", "")) + str(s.get("run", ""))]
    assert all(i > 0 for i in used)                                           # nothing but the check precedes a use of the tag


def test_the_release_image_build_uses_no_shared_layer_cache():
    push = next(s for s in WORKFLOW["jobs"]["build"]["steps"] if s.get("id") == "push")["with"]
    assert push["no-cache"] is True and not {"cache-from", "cache-to"} & set(push)


# --- Access AUDs: one per host, never shared --------------------------------------------------------------------

def test_the_five_aud_variables_are_distinct_names_and_compose_requires_each():
    services = yaml.safe_load(text("compose.yaml"))["services"]
    names = {}
    for n, s in services.items():
        if n != "cloudflared":
            m = re.fullmatch(r"\$\{(AUD_[A-Z0-9]+):\?[^}]+\}", s["environment"]["ACCESS_AUD"])     # `:?` fails `compose up` when unset
            assert m, (n, s["environment"]["ACCESS_AUD"])
            names[n] = m.group(1)
    assert len(set(names.values())) == len(names) == 5 and names == mu.AUD_VARS
    example = text("compose.env.example")
    assert all(re.search(rf"^{v}=", example, re.M) for v in names.values())


def test_a_shared_or_missing_aud_stops_the_release_before_the_restore(rig):
    env = rig.env()
    rig.cfg.env_file.write_text(env.replace("AUD_OR05=aud4", "AUD_OR05=aud3"))
    rig.release(R1, SNAP_A)
    assert rig.apply() == "FAILED" and "share one AUD" in rig.status()["reason"] and rig.restores() == 0
    rig.cfg.env_file.write_text(env.replace("AUD_CT01=aud5\n", ""))
    rig.release(R1, SNAP_A)
    assert rig.apply() == "FAILED" and "AUD_CT01 not set" in rig.status()["reason"] and rig.restores() == 0
    rig.cfg.env_file.write_text(re.sub(r"AUD_(CO05|OR05|CT01)=\w+\n", "", env))                    # no Terrier in this release
    rig.release(R1, SNAP_B, systems={"melange", "mentat"})
    assert rig.apply() == "OK", rig.status()


# --- LOW: the status file is a report, not a gate ---------------------------------------------------------------

def test_a_status_write_failure_does_not_stop_the_inbox_being_consumed(rig, monkeypatch):
    real = mu.write_atomic

    def flaky(path, *a, **k):
        if Path(path).name == "status.json":
            raise OSError(28, "No space left on device")
        return real(path, *a, **k)

    monkeypatch.setattr(mu, "write_atomic", flaky)
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    assert not any(rig.cfg.inbox.iterdir()) and rig.state()["release_id"] == R1


# --- M10 and the rest of the LOW list: bootstrap and the kit installer ----------------------------------------------

INSTALL = text("kit/install.sh")


def test_terrier_volumes_are_never_created_over_a_directory_already_on_disk():
    for name, script in (("bootstrap", BOOT), ("install.sh", INSTALL)):
        guard = script.index('compgen -G "/srv/data/docker/*/volumes/terrier-$d-state"')
        assert '[ -e "/srv/data/docker/volumes/terrier-$d-state" ]' in script, name   # the no-userns-remap layout
        assert guard < script.index('docker volume create "terrier-$d-state"'), name


def test_bootstrap_takes_a_lock_and_checks_the_luks_key_on_every_run():
    assert BOOT.index("flock -n 9") < BOOT.index("tee -a /var/log/melange-bootstrap.log")
    luks = BOOT[BOOT.index("luks() {"):BOOT.index("docker_stage() {")]
    test = luks.index('cryptsetup open --test-passphrase --key-file "$KEY" "$DATA_DEVICE"')
    assert test < luks.index("mountpoint -q /srv/data")                          # before the already-mounted early return
    assert "pending " in luks[test:test + 400]


def test_installer_verifies_and_uses_a_private_copy_and_runs_the_updaters_checks():
    assert "mountpoint -q /srv/data" in INSTALL
    copy = INSTALL.index('cp "$MANIFEST" "$WORK/manifest.json"')
    assert copy < INSTALL.index("ssh-keygen -Y verify") < INSTALL.index("m.check_manifest") < INSTALL.index("managed=$(jq")
    assert INSTALL.index('MANIFEST="$WORK/manifest.json"') < INSTALL.index("ssh-keygen -Y verify")
    assert "mktemp -d" in INSTALL and "trap 'rm -rf \"$WORK\"' EXIT" in INSTALL


def test_the_installers_embedded_check_is_the_updaters_check_manifest(rig):
    code = re.search(r"python3 -c '\n(.*?)\n' \"\$MANIFEST\"", INSTALL, re.S).group(1)
    env = {**os.environ, "PYTHONPATH": str(OPS / "vps")}

    def run(manifest, tmp=rig.tmp / "m.json"):
        tmp.write_text(json.dumps(manifest))
        return subprocess.run([sys.executable, "-c", code, str(tmp)], env=env, capture_output=True, text=True)

    m = rig.release(R1, SNAP_A)
    assert run(m).returncode == 0
    m["systems"]["melange"]["image"] += "\n"                                     # would have become a second compose.env line
    bad = run(m)
    assert bad.returncode != 0 and "manifest refused" in bad.stderr


# --- deps.yml builds the R bundle from ops/locks/r-packages.txt, which ships in the source-only export -------------------

def test_r_lock_is_name_equals_version_lines_with_no_paths_and_names_its_source():
    raw = text("locks/r-packages.txt")
    head = [ln for ln in raw.splitlines() if ln.startswith("#")]
    body = [ln for ln in raw.splitlines() if ln and not ln.startswith("#")]
    assert "R-packages.lock.tsv" in " ".join(head) and "codex/mentat-crypto" in " ".join(head) and "2026-10-12" in " ".join(head)
    assert len(body) > 100 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9.]*==[0-9][0-9.-]*", ln) for ln in body), body[:3]
    names = [ln.split("==")[0] for ln in body]
    assert len(names) == len(set(names)) and not {"base", "stats", "utils", "tools"} & set(names)       # base packages ship with R


def test_deps_workflow_reads_the_r_lock_and_gives_pip_only_the_python_locks():
    deps = (WF_DIR / "deps.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "R-packages.lock.tsv" not in deps and "ops/locks/r-packages.txt" in deps
    assert "for lock in ops/locks/*.linux.txt;" in deps and "for lock in ops/locks/*.txt" not in deps
    assert "cat ops/locks/*.txt | sha256sum" in deps and 'paths: ["ops/locks/**"]' in deps
    cfg = json.loads(text("release_config.json"))
    assert "r-packages.txt" in cfg["export"]["extra_names"]                      # else the export drops the file CI reads
