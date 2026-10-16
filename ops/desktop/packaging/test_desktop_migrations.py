import json
import sqlite3

import pytest

from desktop_migrations import migrate_sqlite


def seed(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE records(value TEXT)")
        connection.execute("INSERT INTO records VALUES ('recipient-owned record')")


def records(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT value FROM records").fetchall()


def test_success_keeps_verified_prior_state(tmp_path):
    database = tmp_path / "accounts.sqlite3"
    seed(database)
    def change():
        with sqlite3.connect(database) as connection:
            connection.execute("ALTER TABLE records ADD COLUMN flag INTEGER DEFAULT 0")
    backup = migrate_sqlite(database, tmp_path, change, lambda: None)
    assert records(backup) == records(database) == [("recipient-owned record",)]
    assert json.loads((tmp_path / "migration-state.json").read_text())["status"] == "verified"


def test_failed_migration_restores_committed_user_data(tmp_path):
    database = tmp_path / "accounts.sqlite3"
    seed(database)
    def failed_change():
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM records")
            connection.execute("CREATE TABLE partial_schema(value TEXT)")
        raise RuntimeError("synthetic migration failure")
    with pytest.raises(RuntimeError, match="synthetic"):
        migrate_sqlite(database, tmp_path, failed_change, lambda: None)
    assert records(database) == [("recipient-owned record",)]
    with sqlite3.connect(database) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='partial_schema'").fetchall()


def test_interrupted_migration_recovers_before_retry(tmp_path):
    database = tmp_path / "accounts.sqlite3"
    seed(database)
    backup = migrate_sqlite(database, tmp_path, lambda: None, lambda: None)
    receipt = tmp_path / "migration-state.json"
    value = json.loads(receipt.read_text())
    value["status"] = "migrating"
    receipt.write_text(json.dumps(value))
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM records")
    seen = []
    migrate_sqlite(database, tmp_path, lambda: seen.extend(records(database)), lambda: None)
    assert seen == [("recipient-owned record",)]
    assert backup.exists()


def test_failed_validation_also_restores_backup(tmp_path):
    database = tmp_path / "accounts.sqlite3"
    seed(database)
    def validation():
        raise RuntimeError("pending migrations")
    with pytest.raises(RuntimeError, match="pending"):
        migrate_sqlite(database, tmp_path, lambda: None, lambda: None, validation)
    assert records(database) == [("recipient-owned record",)]


def test_database_outside_app_state_is_never_opened(tmp_path):
    external = tmp_path / "other.sqlite3"
    seed(external)
    with pytest.raises(ValueError, match="outside"):
        migrate_sqlite(external, tmp_path / "app", lambda: None, lambda: None)
    assert records(external) == [("recipient-owned record",)]
