"""Explicit read-only preflight for the historical LEGDIR_V1 rollback archive.

The general kernel tests use synthetic legacy-lookup fixtures. They do not
establish historical byte parity. Before requesting LEGDIR_V1, run this command
against the intended data root; missing or changed historical bytes fail closed.
The pin comes from audits/legfix3/RUNBOOK_W2.md, not a reconstructed fixture.
This command never creates an archive, changes the loader, or enables the dial.
"""
import argparse
import hashlib
import json
from pathlib import Path


HISTORICAL_SHA256 = 'b7a1e0f8d331feda30727a9bd247c22e698d10f051d38b4414a7b5a374a3007e'
ARCHIVE_RELATIVE = Path('backups') / 'bill_directions.PRE-LEGFIX3.csv'


def check_archive(path, expected_sha256):
    """Compare actual bytes with an explicit pin; synthetic callers use their own pin."""
    path = Path(path)
    result = {'path': str(path), 'expected_sha256': expected_sha256,
              'actual_sha256': None, 'status': 'MISSING', 'verified': False}
    try:
        with path.open('rb') as handle:
            actual = hashlib.file_digest(handle, 'sha256').hexdigest()
    except FileNotFoundError:
        return result
    except OSError as error:
        result.update(status='UNREADABLE', error=str(error))
        return result
    result.update(actual_sha256=actual, verified=actual == expected_sha256,
                  status='VERIFIED' if actual == expected_sha256 else 'HASH_MISMATCH')
    return result


def historical_preflight(root):
    result = check_archive(Path(root) / ARCHIVE_RELATIVE, HISTORICAL_SHA256)
    result['scope'] = 'historical LEGDIR_V1 archive byte identity'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    result = historical_preflight(args.root)
    print(json.dumps(result, indent=2))
    return 0 if result['verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
