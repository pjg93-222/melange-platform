"""One explicitly configured directory for published reports and report viewing.

Collection databases, evidence and intermediate build files remain separate.
There is deliberately no fallback to Downloads, caches, or historical reports.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def report_directory(project_root=None):
    """Resolve the configured existing report directory, without creating it."""
    value = os.environ.get('MENTAT_REPORT_DIRECTORY', '').strip()
    if not value:
        config = Path(project_root or PROJECT) / 'config' / 'report-directory.json'
        try:
            setting = json.loads(config.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as error:
            raise ValueError('The authoritative report directory is not configured.') from error
        if setting.get('schema_version') != 1:
            raise ValueError('Unsupported report-directory configuration.')
        value = setting.get('directory')
    if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
        raise ValueError('The report directory must be an explicit absolute path.')
    directory = Path(value).resolve()
    if not directory.is_dir():
        raise ValueError('The configured report directory is unavailable; no alternate source will be used.')
    return directory


def require_report_path(path, directory=None):
    """Reject stale metadata or a symlink that points outside the report folder."""
    directory = Path(directory).resolve() if directory is not None else report_directory()
    path = Path(path).resolve()
    if not path.is_relative_to(directory):
        raise ValueError('Report is outside the authoritative report directory.')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path)
    parser.add_argument('--check-path', type=Path)
    args = parser.parse_args()
    directory = report_directory(args.project_root)
    print(require_report_path(args.check_path, directory) if args.check_path else directory)


if __name__ == '__main__':
    main()
