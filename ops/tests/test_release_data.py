"""Data capture: serve-set selection, denylist, WAL staging, hash cache, farm layout, restic/sftp (fake shims on PATH),
secret hygiene. No network, no real restic/cloudflared."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from ops.tests.test_release_common import Base, install_shims, read_log
from ops.release_lib import core, data

SECRET_PW, SECRET_TOKEN = "RESTIC-PW-s3cr3t-1", "TUNNEL-TOKEN-s3cr3t-2"


def cred(target):
    return {"melange-restic": ("", core.Secret(SECRET_PW)),
            "melange-svc-push": ("push-id.access", core.Secret(SECRET_TOKEN)),
            "melange-svc-status": ("status-id.access", core.Secret("x"))}[target]


class SelectionTests(Base):
    def setUp(self):
        self.root = self.tmpdir()
        self.cfg = {"melange_live": str(self.root / "m"), "mentat_live": str(self.root / "t"), "terrier_checkout": str(self.root / "r")}

    def put(self, rel, body=b"x", base="src"):
        path = self.root / base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def sets(self, *rules, subdir="mentat"):
        return {"systems": {subdir: {"data_subdir": subdir, "rules": list(rules)}}}

    def rule(self, **kw):
        return {"root": str(self.root / "src"), "include": ["**"], **kw}

    def test_include_exclude_dest_and_sorting(self):
        self.put("b/two.json")
        self.put("a/one.json")
        self.put("a/skip.tmp")
        files = data.resolve_files(self.cfg, self.sets(self.rule(dest="state", exclude=["*.tmp"])))
        self.assertEqual([f["dest"] for f in files], ["mentat/state/a/one.json", "mentat/state/b/two.json"])
        self.assertEqual(data.summarize(files), {"mentat": {"files": 2, "bytes": 2}})

    def test_denylisted_names_are_refused_not_shipped(self):
        for name in ("keys.json", "notes/api_key.txt", "dashboard_password.txt", ".secret", "x/.walk-salt", "store.dpapi",
                     ".env.local", ".demo-key", "tls/server.pem", "tls/server.key", "certs/me.p12", "certs/me.pfx", ".ssh/id_rsa",
                     ".ssh/id_ed25519.pub", "vault.kdbx", ".netrc", "aws_credentials.json", "gh_token.txt", "oauth/Token_Cache.json"):
            root = self.tmpdir()
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("SECRETCONTENT")
            (root / "fine.json").write_text("{}")
            with self.assertRaises(core.ReleaseError, msg=name) as raised:
                data.resolve_files(self.cfg, self.sets({"root": str(root), "include": ["**"]}))
            self.assertEqual(raised.exception.code, "DENYLIST")
            self.assertNotIn("SECRETCONTENT", raised.exception.message)                    # names only, never contents

    def test_a_rule_can_exclude_a_denied_name_explicitly(self):
        self.put("keep.json")
        self.put("keys.json")
        files = data.resolve_files(self.cfg, self.sets(self.rule(exclude=["keys.json"])))
        self.assertEqual([f["dest"] for f in files], ["mentat/keep.json"])

    def test_governed_stores_are_silently_never_shipped(self):
        for name in ("scores.sqlite3", "contacts.sqlite3", "contacts.sqlite3-wal", "accounts.sqlite3", "luna-v1/a.json",
                     "rowfw/b.json", "backups/c.json", "walk-backups/d.json", "old.tar.gz", "email_package/e.xlsx"):
            self.put(name)
        files = data.resolve_files(self.cfg, self.sets(self.rule()))
        self.assertEqual([f["dest"] for f in files], ["mentat/scores.sqlite3"])

    def test_missing_explicit_file_or_dir_fails_closed(self):
        with self.assertRaises(core.ReleaseError) as raised:
            data.resolve_files(self.cfg, self.sets(self.rule(include=["Moneyball.html"])))
        self.assertEqual(raised.exception.code, "SERVE_SET_MISSING")
        with self.assertRaises(core.ReleaseError):
            data.resolve_files(self.cfg, self.sets(self.rule(root=str(self.root / "nope"))))

    def test_two_sources_for_one_destination_is_an_error(self):
        self.put("a.json", base="one")
        self.put("a.json", base="two")
        rules = [{"root": str(self.root / "one"), "include": ["**"]}, {"root": str(self.root / "two"), "include": ["**"]}]
        with self.assertRaises(core.ReleaseError) as raised:
            data.resolve_files(self.cfg, self.sets(*rules))
        self.assertEqual(raised.exception.code, "DEST_COLLISION")

    def test_newest_bundle_with_its_checksum(self):
        old = self.put("co-cd005-aaa.zip")
        self.put("co-cd005-aaa.zip.sha256", b"h")
        new = self.put("co-cd005-bbb.zip")
        self.put("co-cd005-bbb.zip.sha256", b"h")
        self.put("co-cd005-ccc.zip")                                   # newest of all but has no checksum: ignored
        os.utime(old, (1, 1))
        os.utime(new, (2000, 2000))
        files = data.resolve_files(self.cfg, self.sets(self.rule(kind="newest_pair", glob="co-cd005-*.zip", dest="co-05", include=[]),
                                                       subdir="terrier"))
        self.assertEqual([f["dest"] for f in files], ["terrier/co-05/co-cd005-bbb.zip", "terrier/co-05/co-cd005-bbb.zip.sha256"])

    def test_newest_bundle_can_be_served_under_a_fixed_name(self):
        """Terrier reads CU_BUNDLE=/data/bundle.zip and bundle.zip.sha256 beside it, whatever the build's own file name is."""
        self.put("co-cd005-bbb.zip", b"zip")
        self.put("co-cd005-bbb.zip.sha256", b"h")
        rule = self.rule(kind="newest_pair", glob="co-cd005-*.zip", dest="co-05", include=[], **{"as": "bundle.zip"})
        files = data.resolve_files(self.cfg, self.sets(rule, subdir="terrier"))
        self.assertEqual([f["dest"] for f in files], ["terrier/co-05/bundle.zip", "terrier/co-05/bundle.zip.sha256"])
        self.assertEqual(files[0]["src"].name, "co-cd005-bbb.zip")

    def test_cross_reference_runs_pick_latest_newest_per_group_and_extras(self):
        for run, group in (("20260927T000000-aaaa", "banking"), ("20260928T000000-bbbb", "banking"), ("20260927T010000-cccc", "crypto"),
                           ("20260926T000000-dddd", None)):
            self.put(f"{run}/report.json", json.dumps({"industry": group} if group else {}).encode())
            self.put(f"{run}/digest.json")
        self.put("latest.json", json.dumps({"run_id": "20260926T000000-dddd"}).encode())
        self.put("evidence.sqlite")
        self.put("20260913T054905-dc11ad68c0c8.tar.gz")
        files = data.resolve_files(self.cfg, self.sets(self.rule(kind="runs", latest_file="latest.json", group_key="industry",
                                                                 extra=["evidence.sqlite"], include=[], dest="x")))
        runs = {f["dest"].split("/")[2] for f in files if "-" in f["dest"].split("/")[2]}
        self.assertEqual(runs, {"20260928T000000-bbbb", "20260927T010000-cccc", "20260926T000000-dddd"})     # not the superseded banking run
        self.assertIn("mentat/x/evidence.sqlite", [f["dest"] for f in files])
        self.assertIn("mentat/x/latest.json", [f["dest"] for f in files])
        self.assertFalse(any(f["dest"].endswith(".tar.gz") for f in files))

    def test_seed_serve_sets_parse_and_flag_the_census(self):
        sets = json.loads((core.OPS / "serve_sets.json").read_text(encoding="utf-8"))
        self.assertTrue(sets["census_pending"])
        self.assertEqual(set(sets["systems"]), {"melange", "mentat", "terrier"})
        text = json.dumps(sets["systems"])
        for forbidden in ("contacts", "luna", "rowfw", "keys.json", "salt", ".secret", "accounts", "backups", "email"):
            self.assertNotIn(forbidden, text)


class FarmTests(Base):
    def setUp(self):
        self.root = self.tmpdir()
        self.cache = data.HashCache(self.root / "hash_cache.sqlite")
        self.addCleanup(self.cache.close)

    def file(self, name, body=b"hello", mtime=None):
        path = self.root / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        if mtime:
            os.utime(path, ns=(mtime, mtime))
        info = path.stat()
        return {"system": "mentat", "dest": "mentat/" + name, "src": path, "size": info.st_size, "mtime_ns": info.st_mtime_ns}

    def wal_db(self, name="live.sqlite"):
        path = self.root / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t(x)")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(50)])      # these rows live only in the -wal
        self.addCleanup(connection.close)
        info = path.stat()
        return {"system": "mentat", "dest": "mentat/state/" + name, "src": path, "size": info.st_size, "mtime_ns": info.st_mtime_ns}

    def test_plain_files_are_hard_linked_hashed_and_sorted(self):
        files = [self.file("b.txt", b"two"), self.file("a.txt", b"one")]
        rows = data.build_farm(files, self.root / "farm", self.cache)
        self.assertEqual([r["path"] for r in rows], ["mentat/a.txt", "mentat/b.txt"])
        self.assertEqual(rows[0]["sha256"], core.sha256_bytes(b"one"))
        farm = self.root / "farm" / "mentat" / "a.txt"
        self.assertEqual(farm.read_bytes(), b"one")
        self.assertEqual(os.stat(farm).st_ino, os.stat(files[1]["src"]).st_ino)          # a link, not a copy: no extra disk
        self.assertEqual(rows[0]["mtime_ns"], files[1]["mtime_ns"])

    def test_wal_database_is_staged_through_backup_with_its_mtime(self):
        entry = self.wal_db()
        self.assertTrue(data.needs_staging(entry["src"]))
        entry["mtime_ns"] = 1_700_000_000_123_456_700
        os.utime(entry["src"], ns=(entry["mtime_ns"], entry["mtime_ns"]))
        rows = data.build_farm([entry], self.root / "farm", self.cache)
        staged = self.root / "farm" / "mentat" / "state" / "live.sqlite"
        self.assertNotEqual(os.stat(staged).st_ino, os.stat(entry["src"]).st_ino)       # a real copy
        copy = sqlite3.connect(staged)
        try:
            self.assertEqual(copy.execute("SELECT count(*) FROM t").fetchone()[0], 50)    # WAL-only rows are in the copy
        finally:
            copy.close()
        self.assertEqual(rows[0]["mtime_ns"], 1_700_000_000_123_456_700)
        self.assertEqual(os.stat(staged).st_mtime_ns, 1_700_000_000_123_456_700)
        self.assertEqual(rows[0]["sha256"], core.sha256_file(staged))

    def test_quiet_sqlite_is_read_in_place_and_noise_is_not_sqlite(self):
        quiet = self.root / "src" / "quiet.sqlite3"
        quiet.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(quiet)
        connection.execute("CREATE TABLE t(x)")
        connection.commit()
        connection.close()
        self.assertFalse(data.needs_staging(quiet))
        (self.root / "src" / "quiet.sqlite3-shm").write_bytes(b"\0" * 32)
        self.assertTrue(data.needs_staging(quiet))                                      # a -shm beside it: someone may have it open
        fake = self.root / "src" / "notes.sqlite"
        fake.write_bytes(b"not a database")
        self.assertFalse(data.needs_staging(fake))
        self.assertFalse(data.needs_staging(self.root / "src" / "x.json"))

    def test_hash_cache_is_reused_until_size_or_mtime_changes(self):
        files = [self.file(f"f{i}.bin", bytes([i]) * 100) for i in range(5)]
        data.build_farm(files, self.root / "farm1", self.cache)
        self.assertEqual((self.cache.hits, self.cache.misses), (0, 5))
        data.build_farm(files, self.root / "farm2", self.cache)
        self.assertEqual((self.cache.hits, self.cache.misses), (5, 5))
        changed = files[0]["src"]
        changed.write_bytes(b"different and longer")
        info = changed.stat()
        files[0].update(size=info.st_size, mtime_ns=info.st_mtime_ns)
        rows = data.build_farm(files, self.root / "farm3", self.cache)
        self.assertEqual((self.cache.hits, self.cache.misses), (9, 6))
        self.assertEqual(rows[0]["sha256"], core.sha256_bytes(b"different and longer"))

    def test_a_source_that_changes_during_capture_is_caught(self):
        files = [self.file("a.txt"), self.file("b.txt")]
        data.build_farm(files, self.root / "farm", self.cache)
        data.verify_unchanged(files)
        files[0]["src"].write_bytes(b"hello, edited")
        with self.assertRaises(core.ReleaseError) as raised:
            data.verify_unchanged(files)
        self.assertEqual(raised.exception.code, "SOURCE_CHANGED")
        files[1]["src"].unlink()
        with self.assertRaises(core.ReleaseError):
            data.verify_unchanged([files[1]])

    def test_snapshot_manifest_is_canonical_and_matches_the_contract(self):
        files = [self.file("b.txt", b"two"), self.file("a.txt", b"one")]
        rows = data.build_farm(files, self.root / "farm", self.cache)
        body = data.snapshot_manifest("2026.10.12.1", rows, "ruled", "d" * 64)
        doc = json.loads(body)
        self.assertEqual(list(doc), ["files", "governance", "profile_sha256", "release_id", "schema"])        # sorted keys
        self.assertEqual((doc["schema"], doc["governance"], doc["profile_sha256"], doc["release_id"]),
                         ("melange-snapshot-v1", "ruled", "d" * 64, "2026.10.12.1"))        # the invited build's own state, not a constant
        self.assertEqual([sorted(r) for r in doc["files"]][0], ["mtime_ns", "path", "sha256", "size"])
        self.assertEqual([r["path"] for r in doc["files"]], ["mentat/a.txt", "mentat/b.txt"])
        self.assertEqual(body, core.canonical_json(doc))
        self.assertNotIn(b" ", body.replace(b"mentat", b"").replace(b"melange", b""))


class TransportTests(Base):
    def setUp(self):
        self.root = self.tmpdir()
        self.log, self.remote = self.root / "log.jsonl", self.root / "remote"
        self.remote.mkdir()
        self.cfg = core.load_config(overrides={"domain": "example.test"})
        self.env = install_shims(self.root / "bin", ["restic", "sftp", "cloudflared"], self.log, self.remote)
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def stage(self):
        stage = self.root / "farm"
        (stage / "mentat").mkdir(parents=True, exist_ok=True)
        (stage / "mentat" / "a.txt").write_text("a")
        return stage

    def test_restic_backup_uses_the_contract_flags_and_keeps_secrets_out_of_argv(self):
        snap = data.restic_backup(self.cfg, "2026.10.12.1", self.stage(), self.root, cred)
        self.assertEqual(snap["snapshot_id"], "ab12cd34" * 8)
        farm = str(self.root / "farm")
        self.assertEqual(snap["restic_path"], "/" + farm[0] + farm[2:].replace("\\", "/"))         # C:\a\farm is /C/a/farm in the restic tree
        calls = read_log(self.log)
        listed = [c["argv"] for c in calls if "snapshots" in c["argv"] and "--tag" not in c["argv"]]
        self.assertEqual(len(listed), 1)                                               # the path comes from restic's own record
        self.assertEqual(listed[0][listed[0].index("snapshots") + 1:], ["ab12cd34" * 8, "--json"])
        backup = next(c for c in calls if "backup" in c["argv"])
        argv = backup["argv"]
        for flag in ("--tag", "release=2026.10.12.1", "--ignore-inode", "--ignore-ctime", "--files-from-verbatim", "--json"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[:2], ["-r", "sftp:melange-push@ssh.example.test:/restic"])
        self.assertTrue(any(a.startswith("sftp.command=ssh") and "cloudflared access ssh --hostname" in a and "-s sftp" in a for a in argv))
        listing = Path(argv[argv.index("--files-from-verbatim") + 1])
        self.assertEqual(listing.read_text().strip(), str(self.root / "farm"))
        for call in calls:                                                              # secrets: env yes, argv never
            joined = json.dumps(call["argv"])
            self.assertNotIn(SECRET_PW, joined)
            self.assertNotIn(SECRET_TOKEN, joined)
            self.assertNotIn("push-id", joined)
        self.assertEqual(backup["env"]["RESTIC_PASSWORD"], SECRET_PW)
        self.assertEqual(backup["env"]["TUNNEL_SERVICE_TOKEN_SECRET"], SECRET_TOKEN)
        self.assertEqual(backup["env"]["TUNNEL_SERVICE_TOKEN_ID"], "push-id.access")

    def test_the_recorded_snapshot_id_is_restics_full_id_never_the_short_one_from_backup(self):
        full = "ab12cd34" + "ef" * 28
        with mock.patch.dict(os.environ, {"FAKE_SNAPSHOT": "ab12cd34", "FAKE_RESTIC_FULL_ID": full}):       # backup --json says short
            snap = data.restic_backup(self.cfg, "2026.10.12.1", self.stage(), self.root, cred)
        self.assertEqual(snap["snapshot_id"], full)
        self.log.unlink()
        with mock.patch.dict(os.environ, {"FAKE_SNAPSHOT": "ab12cd34"}):                                     # restic never expands it
            with self.assertRaises(core.ReleaseError) as raised:
                data.restic_backup(self.cfg, "2026.10.12.2", self.stage(), self.root, cred)
        self.assertEqual(raised.exception.code, "RESTIC_SNAPSHOT_ID")
        with mock.patch.dict(os.environ, {"FAKE_RESTIC_SNAPSHOTS": json.dumps([{"id": "f" * 8, "paths": ["/a"]}])}):
            with self.assertRaises(core.ReleaseError):                                                      # a reused snapshot too
                data.restic_backup(self.cfg, "2026.10.12.1", self.stage(), self.root, cred)

    def test_existing_release_snapshot_is_reused_and_a_missing_repo_is_initialised(self):
        earlier = [{"id": "f" * 64, "tags": ["release=2026.10.12.1"], "paths": ["D:\\old releases\\staging\\x\\farm"]}]
        with mock.patch.dict(os.environ, {"FAKE_RESTIC_SNAPSHOTS": json.dumps(earlier)}):
            snap = data.restic_backup(self.cfg, "2026.10.12.1", self.stage(), self.root, cred)
        self.assertEqual(snap, {"snapshot_id": "f" * 64, "reused": True, "restic_path": "/D/old releases/staging/x/farm"})  # as that snapshot recorded it
        self.assertFalse(any("backup" in c["argv"] for c in read_log(self.log)))
        self.log.unlink()
        with mock.patch.dict(os.environ, {"FAKE_RESTIC_NO_REPO": "1"}):
            data.restic_backup(self.cfg, "2026.10.12.2", self.stage(), self.root, cred)
        self.assertTrue(any("init" in c["argv"] for c in read_log(self.log)))

    def test_missing_tools_credentials_or_domain_mean_not_configured(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.root / "empty")}):
            with self.assertRaises(core.NotConfigured):
                data.restic_backup(self.cfg, "2026.10.12.1", self.stage(), self.root, cred)
        with self.assertRaises(core.NotConfigured):
            data.restic_backup(dict(self.cfg, domain=None), "2026.10.12.1", self.stage(), self.root, cred)
        def missing(_target):
            raise core.NotConfigured("no such credential")
        with self.assertRaises(core.NotConfigured):
            data.restic_backup(self.cfg, "2026.10.12.1", self.stage(), self.root, missing)
        with mock.patch.dict(os.environ, {"PATH": str(self.root / "bin")}):                # restic present, cloudflared removed
            (self.root / "bin" / "cloudflared.cmd").rename(self.root / "cloudflared.off")
            try:
                with self.assertRaises(core.NotConfigured):
                    data.restic_backup(self.cfg, "2026.10.12.1", self.stage(), self.root, cred)
            finally:
                (self.root / "cloudflared.off").rename(self.root / "bin" / "cloudflared.cmd")

    def test_inbox_upload_order_is_snapshot_then_signature_then_manifest(self):
        names = ["snapshot_manifest.json", "manifest.json.sig", "manifest.json"]
        files = {}
        for name in names:
            files[name] = self.root / name
            files[name].write_text("body of " + name)
        data.push_release_files(self.cfg, files, self.root, cred)
        order = (self.remote / "order.log").read_text().split("\n")
        self.assertEqual([line for line in order if line.startswith("rename")],
                         ["rename snapshot_manifest.json", "rename manifest.json.sig", "rename manifest.json"])
        self.assertEqual(order[-2], "rename manifest.json")                             # manifest.json is LAST
        for name in names:
            self.assertEqual((self.remote / "inbox" / name).read_text(), "body of " + name)
        self.assertFalse(list((self.remote / "inbox").glob("*.part")))
        call = read_log(self.log)[-1]
        self.assertNotIn(SECRET_TOKEN, json.dumps(call["argv"]))
        self.assertEqual(call["env"]["TUNNEL_SERVICE_TOKEN_SECRET"], SECRET_TOKEN)
        self.assertIn("ProxyCommand=cloudflared access ssh --hostname %h", call["argv"])
        with self.assertRaises(core.ReleaseError):
            data.push_release_files(self.cfg, {"manifest.json": files["manifest.json"]}, self.root, cred)

    def test_a_leftover_from_an_interrupted_push_does_not_block_the_next_one(self):
        """sftp rename never overwrites: the batch removes the target first (-rm ignores a missing file), so a resumed push lands."""
        names = ["snapshot_manifest.json", "manifest.json.sig", "manifest.json"]
        files = {name: self.root / name for name in names}
        for name in names:
            files[name].write_text("first " + name)
        data.push_release_files(self.cfg, files, self.root, cred)
        for name in names:
            files[name].write_text("second " + name)
        data.push_release_files(self.cfg, files, self.root, cred)
        for name in names:
            self.assertEqual((self.remote / "inbox" / name).read_text(), "second " + name)
        batch = (self.root / "sftp-push.batch").read_text().splitlines()
        self.assertEqual([line.split()[0] for line in batch], ["put", "-rm", "rename"] * 3)

    def test_restic_tree_path_is_what_restic_stores_on_windows_and_refuses_anything_odd(self):
        for native, tree in ((r"C:\Users\pgoff\AppData\Local\Melange\releases\staging\2026.10.12.1\farm",
                              "/C/Users/pgoff/AppData/Local/Melange/releases/staging/2026.10.12.1/farm"),
                             ("d:/data/farm/", "/d/data/farm"), ("/srv/farm", "/srv/farm"), (r"C:\Users\Paul_Goff\farm", "/C/Users/Paul_Goff/farm")):
            self.assertEqual(data.restic_tree_path(native), tree)
        for bad in (r"\\server\share\farm", "relative/farm", r"C:\a\..\b", r"C:\a (b)\farm", r"C:\a;b", "C:"):
            with self.assertRaises(core.ReleaseError, msg=bad) as raised:
                data.restic_tree_path(bad)
            self.assertEqual(raised.exception.code, "RESTIC_PATH")

    def test_restic_tree_path_and_snapshot_id_refuse_a_trailing_newline(self):
        for bad in ("/srv/farm\n", "C:\\farm\\x\n"):
            with self.assertRaises(core.ReleaseError, msg=repr(bad)) as raised:
                data.restic_tree_path(bad)
            self.assertEqual(raised.exception.code, "RESTIC_PATH")
        self.assertEqual(data.full_snapshot_id({"id": "f" * 64}), "f" * 64)
        with self.assertRaises(core.ReleaseError) as raised:
            data.full_snapshot_id({"id": "f" * 64 + "\n"})
        self.assertEqual(raised.exception.code, "RESTIC_SNAPSHOT_ID")

    def test_a_snapshot_that_holds_more_than_the_farm_is_refused(self):
        with mock.patch.dict(os.environ, {"FAKE_RESTIC_SNAPSHOTS": json.dumps([{"id": "f" * 64, "paths": ["/a", "/b"]}])}):
            with self.assertRaises(core.ReleaseError) as raised:
                data.restic_backup(self.cfg, "2026.10.12.1", self.stage(), self.root, cred)
        self.assertEqual(raised.exception.code, "RESTIC_PATH")

    def test_vps_status_is_fetched_over_sftp(self):
        (self.remote / "status").mkdir()
        (self.remote / "status" / "status.json").write_text(json.dumps({"release_id": "2026.10.12.1"}))
        self.assertEqual(data.fetch_status(self.cfg, self.root, cred)["release_id"], "2026.10.12.1")
        (self.remote / "status" / "status.json").unlink()
        with self.assertRaises(core.ReleaseError):
            data.fetch_status(self.cfg, self.root, cred)


class SecretHygieneTests(Base):
    def test_secret_never_shows_in_repr_str_format_or_pickle(self):
        secret = core.Secret("hunter2")
        for text in (repr(secret), str(secret), f"{secret}", "%s" % secret, "%r" % secret, repr([secret]), repr({"k": secret})):
            self.assertNotIn("hunter2", text)
        import pickle
        with self.assertRaises(TypeError):
            pickle.dumps(secret)
        self.assertEqual(secret.reveal(), "hunter2")

    def test_run_refuses_a_secret_in_argv_and_scrubs_it_from_errors(self):
        with self.assertRaises(core.ReleaseError) as raised:
            core.run([sys.executable, "-c", "pass", "hunter2"], env={"TOKEN": core.Secret("hunter2")})
        self.assertEqual(raised.exception.code, "SECRET_IN_ARGV")
        with self.assertRaises(core.ReleaseError) as raised:
            core.run([sys.executable, "-c", "import os,sys; sys.stderr.write(os.environ['TOKEN']); sys.exit(3)"],
                     env={"TOKEN": core.Secret("hunter2")})
        self.assertNotIn("hunter2", str(raised.exception))
        self.assertIn("****", str(raised.exception))
        ok = core.run([sys.executable, "-c", "import os; print(os.environ['TOKEN'])"], env={"TOKEN": core.Secret("hunter2")})
        self.assertEqual(ok.stdout.strip(), b"hunter2")                                  # it does reach the child, via env

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_credential_manager_miss_is_not_configured_and_dpapi_round_trips(self):
        with self.assertRaises(core.NotConfigured):
            core.read_credential("melange-release-test-target-that-does-not-exist-9f3a")
        path = self.tmpdir() / "s" / "netlify.dpapi"
        core.store_dpapi_secret(path, "nfp_example_token")
        self.assertNotIn(b"nfp_example_token", path.read_bytes())
        self.assertEqual(core.load_dpapi_secret(path).reveal(), "nfp_example_token")
        with self.assertRaises(core.NotConfigured):
            core.load_dpapi_secret(path.with_name("absent.dpapi"))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_credential_manager_hit_path_reads_username_and_utf16_secret(self):
        """Writes one throwaway SESSION-persisted credential, reads it back through read_credential(), deletes it."""
        import ctypes
        import uuid
        target = "melange-release-selftest-" + uuid.uuid4().hex
        secret = "pw-ünï-" + uuid.uuid4().hex
        blob = secret.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = core._CRED(Flags=0, Type=1, TargetName=target, Comment=None, LastWritten=0, CredentialBlobSize=len(blob),
                          CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)), Persist=1, AttributeCount=0,
                          Attributes=None, TargetAlias=None, UserName="id.access")
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.CredWriteW.argtypes = [ctypes.POINTER(core._CRED), ctypes.c_uint32]
        advapi.CredWriteW.restype = ctypes.c_int
        advapi.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        advapi.CredDeleteW.restype = ctypes.c_int
        self.assertTrue(advapi.CredWriteW(ctypes.byref(cred), 0))
        self.addCleanup(advapi.CredDeleteW, target, 1, 0)
        user, value = core.read_credential(target)
        self.assertEqual(user, "id.access")
        self.assertEqual(value.reveal(), secret)
        self.assertNotIn(secret, repr(value))

    def test_credential_blob_decoding(self):
        self.assertEqual(core._decode_blob("pass-w0rd".encode("utf-16-le")), "pass-w0rd")
        self.assertEqual(core._decode_blob(b"plain-utf8"), "plain-utf8")


if __name__ == "__main__":
    unittest.main()
