# -*- coding: utf-8 -*-
"""Pure rules for incorporating reviewed tweet-image evidence.

Image evidence is a distinct reader with its own confidence and provenance. It may resolve weak
neutral/unrelated text, but it never silently overturns a clear directional text read. Conflicts are
kept for review, and Paul's exact tweet overrides still apply after this layer.
"""
from __future__ import annotations

import json
import os


VALID_STANCES = {'support', 'oppose', 'neutral', 'unrelated'}
DEFAULT_MIN_CONFIDENCE = 0.80


def load_annotations(path):
    """Load approved annotations keyed by exact Quorum ID; missing files are valid."""
    if not os.path.exists(path):
        return {}
    doc = json.load(open(path, encoding='utf-8'))
    rows = doc.get('annotations', doc if isinstance(doc, list) else [])
    return index_annotations(rows)


def index_annotations(rows):
    """Validate already-loaded annotation rows and index them by exact Quorum ID."""
    out = {}
    for row in (rows or []):
        qid = str(row.get('quorum_id') or '').strip()
        stance = str(row.get('cu_alignment') or row.get('stance') or '').strip().lower()
        status = str(row.get('review_status') or 'approved').strip().lower()
        try:
            confidence = float(row.get('confidence'))
        except (TypeError, ValueError):
            confidence = 0.0
        if qid and stance in VALID_STANCES and status == 'approved':
            out[qid] = {**row, 'cu_alignment': stance, 'confidence': confidence}
    return out


def fuse_stance(text_stance, text_tier, image_stance=None, image_confidence=0.0,
                min_confidence=DEFAULT_MIN_CONFIDENCE):
    """Fuse text and image reads while preserving conflicts for audit."""
    text = str(text_stance or 'neutral').strip().lower()
    tier = str(text_tier or 'review').strip().lower()
    image = str(image_stance or '').strip().lower()
    try:
        confidence = float(image_confidence or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    result = {
        'stance': text,
        'image_read': image or None,
        'image_confidence': confidence,
        'image_disposition': 'no-image-evidence',
    }
    if image not in VALID_STANCES:
        return result
    if confidence + 1e-12 < float(min_confidence):
        result['image_disposition'] = 'low-confidence-review'
        return result
    text_directional = text in {'support', 'oppose'}
    image_directional = image in {'support', 'oppose'}
    if text_directional:
        if image_directional and image != text:
            result['image_disposition'] = 'conflict-review'
        elif image == text:
            result['image_disposition'] = 'image-corroborates'
        else:
            result['image_disposition'] = 'image-nondirectional'
        return result
    if image_directional and (text in {'neutral', 'unrelated'} or tier == 'review'):
        result['stance'] = image
        result['image_disposition'] = 'image-resolved'
        return result
    result['image_disposition'] = 'image-nondirectional'
    return result
