# -*- coding: utf-8 -*-
"""Load conservatively attributed C-SPAN turns into ``social_media.db``.

The replacement path is intentionally transactional and C-SPAN-scoped:

1. prepare and validate every row from raw transcript JSON;
2. create a consistent SQLite backup;
3. delete only packages hosted on c-span.org;
4. insert the prepared rows and a per-program attribution audit in one commit.

Use ``--dry-run`` first.  Mutation requires the explicit ``--replace`` flag.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from members.cspan_identity import CSpanIdentity  # noqa: E402
from members.fetch_interviews import scrub  # noqa: E402
from paths import ROOT as MB_ROOT  # noqa: E402


DEFAULT_DIR = os.path.join(MB_ROOT, "cspan_out")
DEFAULT_DB = os.environ.get("MONEYBALL_DB", os.path.join(MB_ROOT, "social_media.db"))
CSPAN_PACKAGE_SQL = "lower(package) LIKE '%c-span.org/%'"


def show_of(url: str) -> str:
    match = re.search(r"/program/([^/]+)/", url or "", re.I)
    return match.group(1).lower() if match else "c-span"


def prepare(
    directory: str,
    source: str = "INTERVIEW",
    min_words: int = 8,
    identity: CSpanIdentity | None = None,
) -> tuple[list[tuple[object, ...]], list[dict[str, object]], dict[str, object]]:
    identity = identity or CSpanIdentity()
    files = sorted(glob.glob(os.path.join(directory, "transcripts", "*.json")))
    rows: list[tuple[object, ...]] = []
    programs: list[dict[str, object]] = []
    status_counts: Counter[str] = Counter()
    member_counts: Counter[str] = Counter()
    member_words: Counter[str] = Counter()
    source_digest = hashlib.sha256()

    for path in files:
        with open(path, "rb") as handle:
            raw = handle.read()
        source_digest.update(os.path.basename(path).encode("utf-8"))
        source_digest.update(hashlib.sha256(raw).digest())
        rec = json.loads(raw.decode("utf-8-sig"))
        program_id = str(rec.get("id") or os.path.splitext(os.path.basename(path))[0])
        date = str(rec.get("date") or "")[:10]
        url = str(rec.get("url") or "")
        title = str(rec.get("title") or rec.get("page_title") or "")
        turns = rec.get("turns") or []
        expected = identity.expected_guest(title, date, [turn.get("name") for turn in turns])
        accepted = []
        seen: set[tuple[str, str]] = set()

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            expected = type(expected)(None, "invalid_date", date)

        for turn in turns:
            result = identity.attribute(turn.get("name"), title, date, expected)
            status_counts[result.status] += 1
            if result.member is None:
                continue
            speech = scrub(" ".join(str(turn.get("text") or "").split()))
            nwords = len(speech.split())
            if nwords < min_words:
                status_counts["below_minimum"] += 1
                continue
            digest = hashlib.md5(speech.encode("utf-8", "replace")).hexdigest()
            key = (result.member, digest)
            if key in seen:
                status_counts["per_program_duplicate"] += 1
                continue
            seen.add(key)
            row = (result.member, date, source, show_of(url), url, nwords, speech)
            rows.append(row)
            accepted.append(row)
            member_counts[result.member] += 1
            member_words[result.member] += nwords

        accepted_members = sorted({str(row[0]) for row in accepted})
        if len(accepted_members) > 1:
            raise RuntimeError(
                f"program {program_id} resolved to multiple members: {accepted_members}"
            )
        programs.append({
            "key": f"cspan:{program_id}",
            "program_id": program_id,
            "package": url,
            "date": date,
            "title": title,
            "expected_member": expected.member,
            "status": "ok" if accepted else expected.status,
            "member_turns": len(accepted),
            "member_words": sum(int(row[5]) for row in accepted),
            "detail": expected.detail,
        })

    duplicate_surplus = len(rows) - len({(row[0], row[4], row[6]) for row in rows})
    if duplicate_surplus:
        raise RuntimeError(f"prepared rows contain {duplicate_surplus} exact duplicate surplus rows")
    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_file_count": len(files),
        "source_digest_sha256": source_digest.hexdigest(),
        "source": source,
        "min_words": min_words,
        "prepared_turns": len(rows),
        "prepared_words": sum(int(row[5]) for row in rows),
        "prepared_members": len(member_counts),
        "prepared_programs": sum(1 for program in programs if program["member_turns"]),
        "all_programs": len(programs),
        "status_counts": dict(sorted(status_counts.items())),
        "member_turn_counts": dict(sorted(member_counts.items())),
        "member_word_counts": dict(sorted(member_words.items())),
    }
    return rows, programs, summary


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=120)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS speeches("
        "member TEXT,date TEXT,source TEXT,chamber TEXT,package TEXT,nwords INTEGER,text TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS cspan_fetch_log("
        "key TEXT PRIMARY KEY,status TEXT,ts TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS cspan_attribution_audit("
        "package TEXT PRIMARY KEY,program_id TEXT,date TEXT,title TEXT,expected_member TEXT,"
        "status TEXT,member_turns INTEGER,member_words INTEGER,detail TEXT,ts TEXT)"
    )


def cspan_stats(con: sqlite3.Connection) -> dict[str, int]:
    row = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT member), COUNT(DISTINCT package), "
        f"COALESCE(SUM(nwords),0) FROM speeches WHERE {CSPAN_PACKAGE_SQL}"
    ).fetchone()
    return {"turns": row[0], "members": row[1], "programs": row[2], "words": row[3]}


def sqlite_backup(con: sqlite3.Connection, db_path: str, backup_dir: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(db_path))[0]
    path = os.path.join(backup_dir, f"{base}_pre_cspan_repair_{stamp}.db")
    if os.path.exists(path):
        raise FileExistsError(path)
    destination = sqlite3.connect(path)
    try:
        con.backup(destination, pages=4096)
    finally:
        destination.close()
    if os.path.getsize(path) == 0:
        raise RuntimeError("SQLite backup is empty")
    return path


def replace(
    db_path: str,
    rows: list[tuple[object, ...]],
    programs: list[dict[str, object]],
    backup_dir: str,
) -> tuple[dict[str, int], dict[str, int], str]:
    con = connect(db_path)
    try:
        ensure_schema(con)
        con.commit()
        before = cspan_stats(con)
        backup_path = sqlite_backup(con, db_path, backup_dir)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        con.execute("BEGIN IMMEDIATE")
        con.execute(f"DELETE FROM speeches WHERE {CSPAN_PACKAGE_SQL}")
        con.execute("DELETE FROM cspan_fetch_log WHERE key LIKE 'cspan:%'")
        con.execute("DELETE FROM cspan_attribution_audit")
        con.executemany("INSERT INTO speeches VALUES (?,?,?,?,?,?,?)", rows)
        con.executemany(
            "INSERT INTO cspan_fetch_log(key,status,ts) VALUES (?,?,?)",
            [(program["key"], program["status"], now) for program in programs],
        )
        con.executemany(
            "INSERT INTO cspan_attribution_audit VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(
                program["package"], program["program_id"], program["date"], program["title"],
                program["expected_member"], program["status"], program["member_turns"],
                program["member_words"], program["detail"], now,
            ) for program in programs],
        )
        con.commit()
        after = cspan_stats(con)
        if after["turns"] != len(rows):
            raise RuntimeError(
                f"post-commit C-SPAN row count {after['turns']} != prepared {len(rows)}"
            )
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
        return before, after, backup_path
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=DEFAULT_DIR)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--source", default="INTERVIEW")
    parser.add_argument("--min-words", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--backup-dir", default=os.path.join(MB_ROOT, "backups"))
    parser.add_argument("status", nargs="?", choices=["status"])
    args = parser.parse_args()

    if args.status:
        con = connect(args.db)
        try:
            ensure_schema(con)
            print(json.dumps(cspan_stats(con), indent=2))
        finally:
            con.close()
        return 0
    if args.replace and args.dry_run:
        parser.error("choose either --dry-run or --replace")
    if not args.replace and not args.dry_run:
        parser.error("mutation requires --replace; use --dry-run to inspect")

    rows, programs, summary = prepare(args.dir, args.source, args.min_words)
    output: dict[str, object] = {"mode": "dry-run" if args.dry_run else "replace", **summary}
    if args.replace:
        before, after, backup_path = replace(args.db, rows, programs, args.backup_dir)
        output.update({"database_before": before, "database_after": after, "backup": backup_path})
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
