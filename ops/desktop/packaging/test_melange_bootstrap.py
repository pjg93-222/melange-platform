from pathlib import Path

import pytest

from melange_bootstrap import WELCOME, initialize_workspace


def test_empty_install_has_an_explicit_data_free_first_run_page(tmp_path):
    assert initialize_workspace(tmp_path)
    assert (tmp_path / "Moneyball.html").read_text(encoding="utf-8") == WELCOME
    assert sorted(file.name for file in tmp_path.iterdir()) == ["Moneyball.html"]
    assert "script" not in WELCOME.lower()
    assert not initialize_workspace(tmp_path)


def test_existing_dashboard_and_local_data_are_preserved(tmp_path):
    existing = b"<html>recipient-owned dashboard</html>"
    (tmp_path / "Moneyball.html").write_bytes(existing)
    (tmp_path / "private.sqlite3").write_bytes(b"recipient data")
    assert not initialize_workspace(tmp_path)
    assert (tmp_path / "Moneyball.html").read_bytes() == existing
    assert (tmp_path / "private.sqlite3").read_bytes() == b"recipient data"


def test_invalid_workspace_fails_without_touching_another_location(tmp_path):
    with pytest.raises(ValueError):
        initialize_workspace(tmp_path / "missing")
    (tmp_path / "Moneyball.html").mkdir()
    with pytest.raises(ValueError):
        initialize_workspace(tmp_path)
