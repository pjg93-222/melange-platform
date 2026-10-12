"""Create or verify a consistent SQLite snapshot.

Usage:
  python sqlite_snapshot.py SOURCE_DATABASE TARGET_DATABASE
  python sqlite_snapshot.py --check DATABASE
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path


def readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def quick_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA quick_check(1)").fetchone()
    return "" if row is None else str(row[0])


def verify_database(path: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    connection = readonly_connection(resolved)
    try:
        check = quick_check(connection)
    finally:
        connection.close()
    if check != "ok":
        raise RuntimeError(f"SQLite quick_check failed for {resolved}: {check}")
    return {
        "database": str(resolved),
        "bytes": resolved.stat().st_size,
        "quick_check": check,
    }


def create_snapshot(source: Path, target: Path) -> dict[str, object]:
    source_resolved = source.resolve(strict=True)
    target_resolved = target.resolve(strict=False)
    if source_resolved == target_resolved:
        raise ValueError("Source and target databases must be different files.")
    if target_resolved.exists():
        raise FileExistsError(
            f"Target already exists; refusing to overwrite: {target_resolved}"
        )

    target_resolved.parent.mkdir(parents=True, exist_ok=True)
    pending = target_resolved.with_name(
        f"{target_resolved.name}.pending-{os.getpid()}"
    )
    if pending.exists():
        raise FileExistsError(f"Pending snapshot already exists: {pending}")

    source_connection = readonly_connection(source_resolved)
    target_connection: sqlite3.Connection | None = None
    try:
        source_check = quick_check(source_connection)
        if source_check != "ok":
            raise RuntimeError(
                f"Source SQLite quick_check failed: {source_check}"
            )
        target_connection = sqlite3.connect(pending, timeout=30)
        target_connection.execute("PRAGMA busy_timeout=30000")
        source_connection.backup(target_connection)
        target_connection.commit()
        target_check = quick_check(target_connection)
        if target_check != "ok":
            raise RuntimeError(
                f"Snapshot SQLite quick_check failed: {target_check}"
            )
    except Exception:
        if target_connection is not None:
            target_connection.close()
            target_connection = None
        if pending.exists():
            pending.unlink()
        raise
    finally:
        if target_connection is not None:
            target_connection.close()
        source_connection.close()

    os.replace(pending, target_resolved)
    return {
        "source": str(source_resolved),
        "database": str(target_resolved),
        "bytes": target_resolved.stat().st_size,
        "quick_check": "ok",
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        metavar="DATABASE",
        type=Path,
        help="Verify an existing database without writing it.",
    )
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("target", nargs="?", type=Path)
    arguments = parser.parse_args()
    if arguments.check is not None:
        if arguments.source is not None or arguments.target is not None:
            parser.error("--check cannot be combined with SOURCE/TARGET.")
    elif arguments.source is None or arguments.target is None:
        parser.error("SOURCE and TARGET are required when --check is omitted.")
    return arguments


def main() -> None:
    arguments = parse_arguments()
    result = (
        verify_database(arguments.check)
        if arguments.check is not None
        else create_snapshot(arguments.source, arguments.target)
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
