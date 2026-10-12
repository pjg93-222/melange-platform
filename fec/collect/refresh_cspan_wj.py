#!/usr/bin/env python3
"""Incrementally collect and load current Washington Journal transcripts.

The browser collector owns discovery/scraping and writes cumulative source files.
This wrapper supplies a bounded date window, requires explicit completion receipts,
and only replaces the C-SPAN slice of ``social_media.db`` when source bytes changed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from members.load_cspan import connect, cspan_stats, prepare, replace  # noqa: E402
from paths import ROOT  # noqa: E402


def find_rscript() -> str:
    found = shutil.which("Rscript") or shutil.which("Rscript.exe")
    if found:
        return found
    candidates = sorted(Path(os.environ.get("ProgramFiles", r"C:\Program Files")).glob(
        "R/R-*/bin/Rscript.exe"
    ))
    if not candidates:
        raise RuntimeError("Rscript executable not found")
    return str(candidates[-1])


def run_checked(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd)
    if completed.returncode:
        raise RuntimeError(
            "%s failed with exit %d" % (Path(command[1]).name, completed.returncode)
        )


def load_receipt(path: Path, schema: str, since: str, until: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"missing or invalid completion receipt: {path}") from error
    if (
        payload.get("schema") != schema
        or payload.get("status") != "COMPLETE"
        or payload.get("since") != since
        or payload.get("until") != until
    ):
        raise RuntimeError(f"completion receipt does not match requested window: {path}")
    return payload


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    yesterday = dt.date.today() - dt.timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=(yesterday - dt.timedelta(days=20)).isoformat())
    parser.add_argument("--until", default=yesterday.isoformat())
    parser.add_argument("--out", type=Path, default=Path(ROOT) / "cspan_out")
    parser.add_argument("--db", type=Path, default=Path(ROOT) / "social_media.db")
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path(ROOT) / "data" / "engine" / "interview_source_receipts" / "washington_journal.json",
    )
    parser.add_argument("--backup-dir", type=Path, default=Path(ROOT) / "backups")
    parser.add_argument("--max", type=int, help="bounded diagnostic cap")
    parser.add_argument("--no-load", action="store_true", help="collect but do not update SQLite")
    args = parser.parse_args(argv)

    since = dt.date.fromisoformat(args.since)
    until = dt.date.fromisoformat(args.until)
    if since > until:
        parser.error("--since must be on or before --until")
    args.out.mkdir(parents=True, exist_ok=True)

    rscript = find_rscript()
    collector = Path(ROOT) / "cspan" / "cspan_scrape.R"
    common = ["--since", since.isoformat(), "--until", until.isoformat(), "--out", str(args.out)]
    if args.max:
        common += ["--max", str(args.max)]

    run_checked([rscript, str(collector), "search", *common, "--strict"], cwd=Path(ROOT))
    search = load_receipt(
        args.out / "search_receipt.json", "cspan-wj-search-v1", since.isoformat(), until.isoformat()
    )
    run_checked(
        [rscript, str(collector), "scrape", *common, "--members-only", "--retry-empty", "--strict"],
        cwd=Path(ROOT),
    )
    scrape = load_receipt(
        args.out / "scrape_receipt.json", "cspan-wj-scrape-v1", since.isoformat(), until.isoformat()
    )

    rows, programs, summary = prepare(str(args.out), source="INTERVIEW", min_words=8)
    accepted_dates = [str(row[1]) for row in rows if row[1]]
    program_dates = [str(program.get("date") or "") for program in programs]
    previous = {}
    if args.receipt.is_file():
        try:
            previous = json.loads(args.receipt.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}

    database_before = database_after = {}
    backup = None
    changed = previous.get("source_digest_sha256") != summary["source_digest_sha256"]
    loaded = False
    if not args.no_load:
        connection = connect(str(args.db))
        try:
            database_before = cspan_stats(connection)
        finally:
            connection.close()
        database_matches = database_before.get("turns") == len(rows)
        if changed or not database_matches:
            database_before, database_after, backup = replace(
                str(args.db), rows, programs, str(args.backup_dir)
            )
            loaded = True
        else:
            database_after = database_before

    payload = {
        "schema": "washington-journal-collection-v1",
        "status": "COMPLETE",
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "collector_horizon": until.isoformat(),
        "search_window_start": since.isoformat(),
        "source_latest_program_date": max(program_dates, default=""),
        "accepted_member_content_latest_date": max(accepted_dates, default=""),
        "source_digest_sha256": summary["source_digest_sha256"],
        "cumulative_manifest_programs": search["cumulative_programs"],
        "cumulative_transcript_files": summary["source_file_count"],
        "accepted_programs": summary["prepared_programs"],
        "accepted_turns": summary["prepared_turns"],
        "accepted_members": summary["prepared_members"],
        "window_member_candidates": scrape["eligible_programs"],
        "window_captured": scrape["captured"],
        "window_zero_transcript": scrape["zero_transcript"],
        "source_changed": bool(changed),
        "database_changed": loaded,
        "database_before": database_before,
        "database_after": database_after,
        "backup": backup,
    }
    atomic_json(args.receipt, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
