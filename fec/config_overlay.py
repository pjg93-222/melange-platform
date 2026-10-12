# -*- coding: utf-8 -*-
"""Layer tracked worktree configuration over the live data-root copy.

Moneyball worktrees commonly execute code from ``CODE_ROOT`` while reading/writing artifacts in a
separate ``MONEYBALL_ROOT``. Live-only user rows are preserved; a tracked row with the same stable
identity wins so branch changes are actually exercised during validation and publication.
"""
from __future__ import annotations

import json
import os

from paths import ROOT


CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def candidate_paths(filename, data_root=ROOT, code_root=CODE_ROOT):
    paths = []
    for root in (data_root, code_root):
        path = os.path.abspath(os.path.join(root, filename))
        if path not in paths:
            paths.append(path)
    return paths


def merged_rows(filename, collection, identity_fields, data_root=ROOT, code_root=CODE_ROOT):
    """Merge JSON rows from live root then code root; code rows win by stable identity."""
    keyed = {}
    anonymous = []
    for path in candidate_paths(filename, data_root, code_root):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8') as handle:
                doc = json.load(handle)
        except Exception as exc:
            # FAIL CLOSED (rank-integrity audit 2026-07-17): these files carry Paul's gold
            # overrides and registry polarities. Silently skipping an unreadable copy reverted
            # his corrections with zero signal; a missing file is fine, a corrupt one is not.
            raise RuntimeError('config overlay source unreadable (refusing to silently drop '
                               'user corrections): %s (%s)' % (path, exc))
        rows = doc.get(collection, []) if isinstance(doc, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = None
            for field in identity_fields:
                value = str(row.get(field) or '').strip()
                if value:
                    identity = (field, value.lower())
                    break
            if identity is None:
                anonymous.append(dict(row))
            else:
                keyed[identity] = dict(row)
    return list(keyed.values()) + anonymous
