# -*- coding: utf-8 -*-
"""Canonical readers for the production press corpus plus the audited refresh stream.

The refresh is supplemental: the external Draft Systems tone file remains the historical
corpus, while ``data/stance/press_refresh_tone.csv`` contributes newly collected, CU-scored
host-article/member turns.  ``OFFICIAL_MENTION`` relationships live in a separate typed feed
and are deliberately never returned by :func:`load_tone_rows`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit

from paths import DRAFT, ROOT


BASE_TONE_PATH = os.path.join(DRAFT, "cu_press_stance_tone.csv")
REFRESH_TONE_PATH = os.path.join(ROOT, "data", "stance", "press_refresh_tone.csv")
REFRESH_FULLTEXT_PATH = os.path.join(ROOT, "data", "stance", "press_refresh_fulltext.jsonl")
REFRESH_MENTIONS_PATH = os.path.join(ROOT, "data", "press", "official_mentions.jsonl")
REFRESH_RECEIPT_PATH = os.path.join(ROOT, "data", "stance", "press_refresh_feed_receipt.json")


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_refresh_receipt(tone_path=REFRESH_TONE_PATH,
                             fulltext_path=REFRESH_FULLTEXT_PATH,
                             mentions_path=REFRESH_MENTIONS_PATH,
                             receipt_path=REFRESH_RECEIPT_PATH):
    """Fail closed unless the complete refresh file set matches its last committed receipt."""
    if not os.path.isfile(receipt_path):
        raise RuntimeError("press refresh receipt is missing")
    try:
        with io.open(receipt_path, encoding="utf-8") as handle:
            receipt = json.load(handle)
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError("press refresh receipt is unreadable") from exc
    if (receipt.get("schema") != "press-refresh-engine-feed-v1" or
            receipt.get("status") != "ACTIVE_TYPED_SUPPLEMENT"):
        raise RuntimeError("press refresh receipt is not active")
    if (receipt.get("promotion_mode") != "CUMULATIVE_ACCEPTED_RUN_LEDGER"
            or not receipt.get("accepted_runs")):
        raise RuntimeError("press refresh receipt is not a cumulative accepted-run ledger")
    expected = {
        "data/stance/press_refresh_tone.csv": tone_path,
        "data/stance/press_refresh_fulltext.jsonl": fulltext_path,
        "data/press/official_mentions.jsonl": mentions_path,
    }
    hashes = receipt.get("outputs_sha256") or {}
    if set(hashes) != set(expected):
        raise RuntimeError("press refresh receipt does not cover the complete output set")
    for relative, path in expected.items():
        if not os.path.isfile(path) or _sha256_file(path) != hashes.get(relative):
            raise RuntimeError("press refresh output set is mixed or uncommitted: %s" % relative)
    return receipt


def canonical_url(value, keep_fragment=True):
    """Normalize a URL while retaining member fragments unless explicitly requested otherwise."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        host = parts.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = parts.path.rstrip("/") or "/"
        fragment = parts.fragment if keep_fragment else ""
        return urlunsplit((parts.scheme.lower() or "https", host, path, parts.query, fragment))
    except ValueError:
        return value.rstrip("/").lower()


# PRESS_URL_CANON (A5, 2026-09-19; packet audits/laundry_list_closeout_20260918 FINDINGS.md A5).
# One release ingested under two URL forms was counted twice: 12 (member, title, date) groups
# survived ``load_tone_rows``, 6 of them with more than one 'used' row.  Eleven of the 12
# differ by PATH ALIAS alone (the twelfth is a truncated stored url and is left alone) -- a
# CMS section prefix ('/news/press-releases/X' beside
# '/press-releases/X'), an inserted date path ('/news/videos/watch/2018/03/06/X' beside
# '/media/videos/watch/X'), a section rename ('/newsroom/op-eds/X' beside
# '/newsroom/press-releases/X'), or a Drupal duplicate-slug marker ('...-election-0' beside
# '...-election').  No scheme, www., trailing-slash, case, query or fragment rule reaches any of
# them, so the extra rules are PATH rules and they live in ``release_url_key``.
#
# ``canonical_url`` is deliberately left byte-identical.  That is measured, not cautious:
# ``data/stance/api_press_stance.csv`` is keyed on the RAW url, so folding the path rules into
# ``canonical_url`` itself drops the press router's PRIMARY basis from 627 api-target hits to 93
# (receipt audits/laundry_list_closeout_20260918/fixM/m2_canon_candidates.out.txt).  The
# full-text body map, the stack-label map, the collector's own turn key and
# ``press_baseline_fold.index_key``'s pinned divergence all key on ``canonical_url`` too.
# ``release_url_key`` has exactly one caller, ``tone_turn_key``, so the change is confined to
# which rows are ingested; every surviving row's routing is unchanged.
#
# Default '2' = release identity.  PRESS_URL_CANON=1 restores the legacy key byte for byte.
PRESS_URL_CANON = (os.environ.get("PRESS_URL_CANON", "2") or "2").strip()
if PRESS_URL_CANON not in ("1", "2"):
    # Fail at import, before any I/O: a dial that silently changes what COUNTS must never
    # fall through to a default because it was misspelled (the DESK_MEMBERSHIP_SOURCE
    # pattern, make_member_desk.py ~232).
    raise ValueError("PRESS_URL_CANON must be '1' (legacy url key) or '2' (release key); got %r"
                     % os.environ.get("PRESS_URL_CANON"))
TONE_DEDUP_LEDGER_SCHEMA = "press-tone-ingest-dedup-v1"
# A Drupal collision marker, never a numbered edition: '-0' only, and only on a slug that is
# itself hyphenated.  Scalise's 'heat-report-22'/'-26'/'-28' are editions and must stay apart.
_DUPLICATE_SLUG = re.compile(r"^(?P<slug>[^/]*[A-Za-z][^/]*-[^/]*)-0$")
# A release slug: three or more hyphen-separated parts and at least one letter.  A one-hyphen
# tail ('press-releases', 'news-media') is a SECTION and is never used as an identity, which is
# what keeps Boozman's 18 query-string articles and every '*.aspx' endpoint apart.
_RELEASE_SLUG = re.compile(r"^(?=[^/]*[A-Za-z])[^/]+(?:-[^/]+){2,}$")
# Filled on every load_tone_rows() call so an existing caller can read the accounting without a
# signature change.
LAST_TONE_DEDUP_LEDGER = {}


def release_url_key(value, canon=None):
    """Identity of the RELEASE a press URL points at.  Ingest de-duplication only.

    Strengthens :func:`canonical_url` with the two measured path-alias rules and nothing
    else.  ``canon='1'`` returns ``canonical_url`` unchanged.
    """
    canon = PRESS_URL_CANON if canon is None else str(canon).strip()
    if canon not in ("1", "2"):
        raise ValueError("PRESS_URL_CANON must be '1' or '2'; got %r" % (canon,))
    url = canonical_url(value, keep_fragment=True)
    if canon == "1" or not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        return url
    marker = _DUPLICATE_SLUG.match(segments[-1])
    if marker:
        segments[-1] = marker.group("slug")
    if _RELEASE_SLUG.match(segments[-1]):
        segments = [segments[-1]]
    return urlunsplit((parts.scheme, parts.netloc, "/" + "/".join(segments),
                       parts.query, parts.fragment))


def tone_turn_key(row, canon=None):
    url = release_url_key(row.get("url"), canon=canon)
    identity = (row.get("bioguide") or row.get("member") or "").strip().upper()
    return url, identity


def _read_csv(path):
    if not path or not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _same_release(kept, row):
    """True when two rows that share a release key are provably ONE release.

    The member is already part of the key; the date and the title (case and spacing aside)
    must agree too.  Anything less is a different release that happens to reuse a slug.
    """
    def plain(value):
        return " ".join(str(value or "").split()).casefold()
    return (plain(kept.get("date")) == plain(row.get("date"))
            and plain(kept.get("title")) == plain(row.get("title")))


def load_tone_rows(base_path=BASE_TONE_PATH, refresh_path=REFRESH_TONE_PATH,
                   ledger=None, canon=None):
    """Return the historical and refresh tone rows, de-duplicated by article/member turn.

    Historical rows win exact collisions.  This makes the adapter additive and prevents a
    refresh from silently rewriting a previously published observation.  The same precedence
    decides which URL form of one release survives: the earlier row, which is the historical
    one.  The later form is recorded in the dedup ledger, never removed from its input file.

    The release key only NOMINATES a pair.  Drupal appends '-0' whenever a new node reuses an
    existing title, so a recurring headline republished on another day shares its release key
    with the original.  Two rows are one release only when the date and the title agree as
    well (all 12 measured pairs do); a row that shares a key but not the date and title is a
    different release, is KEPT, and is counted in ``release_key_conflict_kept``.

    ``ledger``, when a mapping is supplied, receives the full ingest accounting so the press
    path's own census still closes: rows_read == rows_returned + official_mention_skipped +
    no_url_skipped + exact_url_turn_collapsed + duplicate_url_form_collapsed.
    """
    if (os.path.abspath(refresh_path) == os.path.abspath(REFRESH_TONE_PATH)
            and os.path.exists(refresh_path)):
        validate_refresh_receipt()
    rows = []
    seen = {}             # release key -> every row KEPT under it, in ingest order
    legacy_seen = set()   # the legacy (canonical url, identity) key of every kept row
    counts = {"rows_read": 0, "official_mention_skipped": 0, "no_url_skipped": 0,
              "exact_url_turn_collapsed": 0, "duplicate_url_form_collapsed": 0,
              "release_key_conflict_kept": 0}
    merged = []
    for source_path in (base_path, refresh_path):
        for row in _read_csv(source_path):
            counts["rows_read"] += 1
            if (row.get("source") or "").strip().upper() == "OFFICIAL_MENTION":
                counts["official_mention_skipped"] += 1
                continue
            key = tone_turn_key(row, canon=canon)
            if not key[0]:
                counts["no_url_skipped"] += 1
                continue
            # The exact (url, identity) collision collapses first, exactly as it always did --
            # under PRESS_URL_CANON=1 the two keys are the same key, so this is the whole rule.
            legacy_key = tone_turn_key(row, canon="1")
            if legacy_key in legacy_seen:
                counts["exact_url_turn_collapsed"] += 1
                continue
            candidates = seen.setdefault(key, [])
            kept = next((k for k in candidates if _same_release(k, row)), None)
            if kept is not None:
                counts["duplicate_url_form_collapsed"] += 1
                merged.append({
                    "member": kept.get("member"), "bioguide": kept.get("bioguide"),
                    "date": kept.get("date"), "title": kept.get("title"),
                    "kept_url": (kept.get("url") or "").strip(),
                    "dropped_url": (row.get("url") or "").strip(),
                    "release_key": key[0],
                    "reason": "duplicate-url-form: same release, aliased path"})
                continue
            if candidates:
                counts["release_key_conflict_kept"] += 1
            candidates.append(row)
            legacy_seen.add(legacy_key)
            rows.append(row)
    counts["rows_returned"] = len(rows)
    ledger_out = dict(counts)
    ledger_out["schema"] = TONE_DEDUP_LEDGER_SCHEMA
    ledger_out["canon"] = PRESS_URL_CANON if canon is None else str(canon).strip()
    ledger_out["duplicate_url_form_rows"] = merged
    LAST_TONE_DEDUP_LEDGER.clear()
    LAST_TONE_DEDUP_LEDGER.update(ledger_out)
    if ledger is not None:
        ledger.update(ledger_out)
    return rows


def fulltext_paths():
    """Full-text sources in precedence order; later duplicates replace earlier text."""
    return (
        os.path.join(ROOT, "press_text_cache.jsonl"),
        os.path.join(ROOT, "data", "stance", "press_text_cache_priority.jsonl"),
        REFRESH_FULLTEXT_PATH,
    )


def load_fulltext():
    if os.path.exists(REFRESH_FULLTEXT_PATH):
        validate_refresh_receipt()
    out = {}
    for path in fulltext_paths():
        if not os.path.exists(path):
            continue
        with io.open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                url = canonical_url(row.get("url"), keep_fragment=True)
                text = row.get("text") or row.get("body") or ""
                if url and text:
                    out[url] = text
    return out


def source_paths(base_path=BASE_TONE_PATH, refresh_path=REFRESH_TONE_PATH):
    if (os.path.abspath(refresh_path) == os.path.abspath(REFRESH_TONE_PATH)
            and os.path.exists(refresh_path)):
        validate_refresh_receipt()
    return [path for path in (base_path, refresh_path) if path and os.path.exists(path)]
