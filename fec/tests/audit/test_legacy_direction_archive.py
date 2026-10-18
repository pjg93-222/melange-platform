"""Synthetic preflight controls; these do not claim recovery of the historical archive."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from audit import audit_legacy_direction_archive as archive


def test_historical_pin_and_path_remain_exact():
    assert archive.HISTORICAL_SHA256 == 'b7a1e0f8d331feda30727a9bd247c22e698d10f051d38b4414a7b5a374a3007e'
    assert archive.ARCHIVE_RELATIVE == Path('backups/bill_directions.PRE-LEGFIX3.csv')


def test_synthetic_matching_bytes_pass_then_mutation_fails(tmp_path):
    path = tmp_path / 'explicitly-synthetic.csv'
    original = b'Bill,Direction\nH.R.1,1\n'
    path.write_bytes(original)
    expected = hashlib.sha256(original).hexdigest()
    assert archive.check_archive(path, expected)['verified'] is True
    path.write_bytes(original.replace(b',1\n', b',0\n'))
    result = archive.check_archive(path, expected)
    assert result['verified'] is False and result['status'] == 'HASH_MISMATCH'
    assert path.read_bytes() != original


def test_missing_archive_fails_and_is_never_created(tmp_path):
    result = archive.historical_preflight(tmp_path)
    assert result['status'] == 'MISSING' and result['verified'] is False
    assert not (tmp_path / archive.ARCHIVE_RELATIVE).exists()


def test_synthetic_archive_cannot_satisfy_historical_pin(tmp_path):
    path = tmp_path / archive.ARCHIVE_RELATIVE
    path.parent.mkdir()
    original = b'Bill,Direction\nH.R.1,1\n'
    path.write_bytes(original)
    result = archive.historical_preflight(tmp_path)
    assert result['status'] == 'HASH_MISMATCH' and result['verified'] is False
    assert result['expected_sha256'] == archive.HISTORICAL_SHA256
    assert path.read_bytes() == original


def test_unreadable_archive_fails_closed(tmp_path):
    path = tmp_path / archive.ARCHIVE_RELATIVE
    path.mkdir(parents=True)
    result = archive.historical_preflight(tmp_path)
    assert result['status'] == 'UNREADABLE' and result['verified'] is False


@pytest.mark.parametrize('present', [False, True])
def test_cli_missing_or_wrong_archive_is_nonzero_and_read_only(tmp_path, present):
    path = tmp_path / archive.ARCHIVE_RELATIVE
    if present:
        path.parent.mkdir()
        path.write_bytes(b'explicitly synthetic, not the historical archive\n')
    before = path.read_bytes() if present else None
    run = subprocess.run([sys.executable, '-B', str(Path(archive.__file__)), '--root', str(tmp_path)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 1, run.stdout + run.stderr
    result = json.loads(run.stdout)
    assert result['verified'] is False
    assert result['expected_sha256'] == archive.HISTORICAL_SHA256
    assert (path.read_bytes() if path.exists() else None) == before


def test_cli_does_not_accept_a_replacement_pin(tmp_path):
    run = subprocess.run([sys.executable, '-B', str(Path(archive.__file__)), '--root', str(tmp_path),
                          '--expected-sha256', '0' * 64], capture_output=True, text=True, check=False)
    assert run.returncode == 2
    assert 'unrecognized arguments' in run.stderr
