# -*- coding: utf-8 -*-
"""Rebuild the canonical flat C-SPAN turn file from raw transcript JSON.

The raw JSON remains the source of truth.  Unlike the original R-side export,
``is_member`` is based on the shared C-SPAN identity gate, not the usually blank
role field.  The role-derived diagnostic is retained separately so regressions
are visible without confusing it for identity.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from members.cspan_identity import CSpanIdentity  # noqa: E402
from paths import ROOT as MB_ROOT  # noqa: E402


FIELDS = [
    "id", "url", "title", "date", "seq", "time", "offset", "speaker",
    "role", "role_suggests_member", "resolved_member", "is_member",
    "attribution_status", "nwords", "text",
]


def role_suggests_member(role: str | None) -> bool:
    value = (role or "").lower()
    return any(token in value for token in (
        "representative", "senator", "congress", "delegate", "speaker",
    ))


def _atomic_csv(path: str, rows: list[dict[str, object]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".turns-", suffix=".csv", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cspan-audit-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build(out_dir: str, identity: CSpanIdentity) -> tuple[list[dict[str, object]], dict[str, object]]:
    files = sorted(glob.glob(os.path.join(out_dir, "transcripts", "*.json")))
    rows: list[dict[str, object]] = []
    statuses: Counter[str] = Counter()
    program_statuses: Counter[str] = Counter()
    members: Counter[str] = Counter()
    member_words: Counter[str] = Counter()
    program_audit: list[dict[str, object]] = []
    source_digest = hashlib.sha256()

    for path in files:
        with open(path, "rb") as handle:
            raw = handle.read()
        source_digest.update(os.path.basename(path).encode("utf-8"))
        source_digest.update(hashlib.sha256(raw).digest())
        rec = json.loads(raw.decode("utf-8-sig"))
        date = str(rec.get("date") or "")[:10]
        title = str(rec.get("title") or rec.get("page_title") or "")
        turns = rec.get("turns") or []
        expected = identity.expected_guest(title, date, [turn.get("name") for turn in turns])
        program_statuses[expected.status] += 1
        accepted = 0
        accepted_words = 0

        for seq, turn in enumerate(turns, start=1):
            label = str(turn.get("name") or "")
            result = identity.attribute(label, title, date, expected=expected)
            text = " ".join(str(turn.get("text") or "").split())
            nwords = len(text.split())
            statuses[result.status] += 1
            if result.member:
                accepted += 1
                accepted_words += nwords
                members[result.member] += 1
                member_words[result.member] += nwords
            rows.append({
                "id": rec.get("id") or os.path.splitext(os.path.basename(path))[0],
                "url": rec.get("url") or "",
                "title": title,
                "date": date,
                "seq": seq,
                "time": turn.get("time") or "",
                "offset": turn.get("offset") if turn.get("offset") is not None else "",
                "speaker": label,
                "role": turn.get("role") or "",
                "role_suggests_member": "TRUE" if role_suggests_member(turn.get("role")) else "FALSE",
                "resolved_member": result.member or "",
                "is_member": "TRUE" if result.member else "FALSE",
                "attribution_status": result.status,
                "nwords": nwords,
                "text": text,
            })

        program_audit.append({
            "id": str(rec.get("id") or os.path.splitext(os.path.basename(path))[0]),
            "url": rec.get("url") or "",
            "date": date,
            "title": title,
            "expected_member": expected.member,
            "program_status": expected.status,
            "member_turns_before_minimum": accepted,
            "member_words_before_minimum": accepted_words,
        })

    audit = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "cspan_out/transcripts/*.json",
        "source_file_count": len(files),
        "source_digest_sha256": source_digest.hexdigest(),
        "turn_count": len(rows),
        "member_turn_count_before_minimum": sum(members.values()),
        "member_word_count_before_minimum": sum(member_words.values()),
        "member_count_before_minimum": len(members),
        "program_count": len(program_audit),
        "attribution_status_counts": dict(sorted(statuses.items())),
        "program_status_counts": dict(sorted(program_statuses.items())),
        "member_turn_counts_before_minimum": dict(sorted(members.items())),
        "member_word_counts_before_minimum": dict(sorted(member_words.items())),
        "programs": program_audit,
    }
    return rows, audit


def backup_flat_files(out_dir: str, backup_root: str) -> list[str]:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(backup_root, f"cspan_flat_pre_repair_{stamp}")
    os.makedirs(target, exist_ok=False)
    copied = []
    for path in sorted(glob.glob(os.path.join(out_dir, "turns*.csv"))):
        destination = os.path.join(target, os.path.basename(path))
        shutil.copy2(path, destination)
        copied.append(destination)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(MB_ROOT, "cspan_out"))
    parser.add_argument("--audit", default=None)
    parser.add_argument("--check", action="store_true", help="compute and print only; do not write")
    parser.add_argument("--backup-old", action="store_true")
    parser.add_argument("--allow-empty", action="store_true",
                        help="permit writing when no transcripts/turns were found (default: refuse)")
    parser.add_argument("--replace-shards", action="store_true",
                        help="remove stale turns_N.csv files after a successful canonical write")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out)
    audit_path = os.path.abspath(args.audit or os.path.join(out_dir, "attribution_audit.json"))
    identity = CSpanIdentity()
    rows, audit = build(out_dir, identity)

    if args.check:
        print(json.dumps({key: value for key, value in audit.items() if key != "programs"}, indent=2))
        return 0

    if not args.allow_empty and (audit["source_file_count"] == 0 or audit["turn_count"] == 0):
        raise SystemExit(f"refusing to write: empty corpus under {out_dir} "
                         f"(source_file_count={audit['source_file_count']}, turn_count={audit['turn_count']}); "
                         "check --out or pass --allow-empty")

    copied: list[str] = []
    if args.backup_old:
        copied = backup_flat_files(out_dir, os.path.join(MB_ROOT, "backups"))
    _atomic_csv(os.path.join(out_dir, "turns.csv"), rows)
    _atomic_json(audit_path, audit)
    if args.replace_shards:
        for path in glob.glob(os.path.join(out_dir, "turns_*.csv")):
            os.unlink(path)

    print(json.dumps({
        "turns_csv": os.path.join(out_dir, "turns.csv"),
        "audit_json": audit_path,
        "backed_up_files": copied,
        "turn_count": audit["turn_count"],
        "member_turn_count_before_minimum": audit["member_turn_count_before_minimum"],
        "member_count_before_minimum": audit["member_count_before_minimum"],
        "program_status_counts": audit["program_status_counts"],
        "attribution_status_counts": audit["attribution_status_counts"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
