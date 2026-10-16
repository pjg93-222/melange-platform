"""Verified backups and crash recovery for installation-owned SQLite migrations."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid


def _integrity(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RuntimeError("Database integrity verification failed")


def _readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def _copy_database(source, destination):
    """SQLite's backup transaction preserves committed data and handles WAL safely."""
    with closing(_readonly(source)) as original, closing(sqlite3.connect(destination, timeout=10)) as copied:
        _integrity(original)
        original.backup(copied)
        _integrity(copied)


def _write_receipt(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def _migration_lock(root):
    # An OS-held lock is released even after a crash; a stale marker cannot deadlock startup.
    with (root / "migration.lock").open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def migrate_sqlite(database, state_root, migrate, close_connections, validate=lambda: None):
    """Migrate only this installation's database, restoring its verified backup on failure.

    Call before starting request handlers. Callbacks must not migrate other databases
    or mutate external data files; those require a separate versioned migration plan.
    """
    root = Path(state_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = Path(database)
    if database.is_symlink() or not database.resolve().is_relative_to(root):
        raise ValueError("Migration database is outside this installation's state directory")
    database = database.resolve()
    backups = root / "migration-backups"
    backups.mkdir(exist_ok=True)
    if backups.is_symlink() or backups.resolve().parent != root:
        raise ValueError("Migration backup directory is not installation-owned")
    receipt_path = root / "migration-state.json"
    with _migration_lock(root):
        close_connections()
        previous = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
        if previous.get("status") == "migrating":
            recovery_name = previous.get("backup")
            if not isinstance(recovery_name, str) or Path(recovery_name).name != recovery_name:
                raise RuntimeError("Interrupted migration has no valid recovery backup")
            recovery = backups / recovery_name
            if recovery.is_symlink() or recovery.resolve().parent != backups.resolve():
                raise RuntimeError("Migration recovery path is invalid")
            _copy_database(recovery, database)
            _write_receipt(receipt_path, {**previous, "status": "recovered-after-interruption"})

        # Even a new installation has an empty, valid recovery database.
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database, timeout=10)) as original:
            _integrity(original)
        backup = backups / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex + ".sqlite3")
        _copy_database(database, backup)
        record = {"schemaVersion": 1, "status": "migrating", "backup": backup.name,
                  "startedAt": datetime.now(timezone.utc).isoformat()}
        _write_receipt(receipt_path, record)
        try:
            migrate()
            close_connections()
            with closing(_readonly(database)) as updated:
                _integrity(updated)
            validate()
            close_connections()
        except BaseException:
            close_connections()
            _copy_database(backup, database)
            _write_receipt(receipt_path, {**record, "status": "restored-after-failure"})
            raise
        _write_receipt(receipt_path, {**record, "status": "verified",
                                      "finishedAt": datetime.now(timezone.utc).isoformat()})
        return backup


def migrate_django(state_root):
    """Terrier's explicit single-database migration boundary."""
    from django.core.management import call_command
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    if set(connections) != {"default"}:
        raise RuntimeError("Additional databases require a reviewed desktop migration plan")
    connection = connections["default"]
    if connection.vendor != "sqlite":
        raise RuntimeError("Desktop migration requires its local SQLite database")
    state = Path(state_root)
    database = Path(connection.settings_dict["NAME"])
    if database.is_symlink() or not database.resolve().is_relative_to(state.resolve()):
        raise ValueError("Migration database is outside this installation's state directory")
    receipt = state / "migration-state.json"
    interrupted = receipt.exists() and json.loads(receipt.read_text(encoding="utf-8")).get("status") == "migrating"
    if not interrupted:
        executor = MigrationExecutor(connection)
        if not executor.migration_plan(executor.loader.graph.leaf_nodes()):
            return None

    def verify_plan():
        executor = MigrationExecutor(connections["default"])
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            raise RuntimeError("Database migration did not complete")

    return migrate_sqlite(connection.settings_dict["NAME"], state_root,
                          lambda: call_command("migrate", interactive=False, verbosity=0),
                          connections.close_all, verify_plan)
