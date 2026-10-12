# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
"""Shared CU-PASSAGE extractor (Paul 2026-07-15: "you should be getting multiple entries, or sentences,
from the same interview, not just one").  Given a long interview/hearing transcript, return the
~500-char, sentence-snapped passages AROUND each CU keyword mention (windows that overlap are merged).
The interview + hearing channels emit one entry PER passage instead of one per whole transcript, so the
labeled snippet is actually the credit-union content — not the unrelated opening of a rambling interview.

NOT wired into the pipeline; pure text helper, no I/O, no network."""
import re

# sentence terminator: . ! ? optionally followed by a closing quote/paren/bracket, then whitespace.
_SENT_END = re.compile(r'[.!?]["\'’\)\]]?\s+')

def _find_hits(text_low, keywords):
    """All start offsets of any keyword (deduped, sorted)."""
    hits = []
    for k in keywords:
        start = 0
        while True:
            i = text_low.find(k, start)
            if i < 0:
                break
            hits.append(i)
            start = i + 1
    return sorted(set(hits))

def _snap_start(text, pos, max_back=260):
    """Move the window start BACK to just after the previous sentence end (within max_back chars), so a
    passage begins at a clean sentence boundary. If none is found (very long sentence) keep pos."""
    lo = max(0, pos - max_back)
    seg = text[lo:pos]
    last = None
    for m in _SENT_END.finditer(seg):
        last = m
    if last is not None:
        return lo + last.end()
    return lo if pos - max_back <= 0 else pos

def _snap_end(text, pos, max_fwd=260):
    """Move the window end FORWARD to the next sentence end (within max_fwd chars)."""
    hi = min(len(text), pos + max_fwd)
    m = _SENT_END.search(text[pos:hi])
    if m is not None:
        return pos + m.end()
    return hi

def passages(text, keywords, half=250):
    """Return the list of ~2*half-char, sentence-snapped passages around each CU keyword mention in
    `text`. Mentions whose raw windows overlap are merged into one passage (a dense CU discussion stays
    together); mentions far apart become separate passages (the whole transcript never collapses to one
    entry unless it is wall-to-wall CU)."""
    text = text or ''
    tl = text.lower()
    hits = _find_hits(tl, keywords)
    if not hits:
        return []
    wins = [[max(0, i - half), min(len(text), i + half)] for i in hits]
    merged = [wins[0][:]]
    for s, e in wins[1:]:
        if s <= merged[-1][1]:                      # overlaps/touches the previous window -> merge
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out = []
    for s, e in merged:
        p = text[_snap_start(text, s):_snap_end(text, e)].strip()
        if p:
            out.append(p)
    return out
