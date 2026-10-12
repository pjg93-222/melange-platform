"""Bind reference labels to the complete classifier input, preserving a named legacy baseline."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from moneyball_core import dump_atomic


def key(row):
    value = json.dumps(['reference-window-v2', row['a'], row['b'], row['w'][:400]], ensure_ascii=False)
    return 'v2:' + hashlib.sha256(value.encode('utf-8')).hexdigest()


def legacy_key(row):
    return hashlib.md5((row['a']+'|'+row['b']+'|'+row['w'][:200]).encode('utf-8','replace')).hexdigest()


def bind_legacy(cache, refs, path):
    """Freeze existing label associations once; later changed passages cannot inherit them.

    This migration preserves legacy labels, not a claim of reclassification. Ambiguous
    prefixes are separately queued for reclassification by the scored refresh.
    """
    path = Path(path)
    if path.exists():
        baseline = json.loads(path.read_text(encoding='utf-8'))
        if baseline.get('schema') != 'reference-cache-baseline-v2':
            raise ValueError('Unknown reference-cache baseline schema')
    else:
        variants = defaultdict(set)
        for row in refs:
            old = legacy_key(row)
            if old in cache:
                variants[old].add(key(row))
        baseline = {'schema':'reference-cache-baseline-v2',
                    'provenance':'Inherited legacy classifications; no new model judgment.',
                    'bindings':{new:old for old, values in variants.items() for new in values},
                    'ambiguous':[new for values in variants.values() if len(values)>1 for new in values]}
        dump_atomic(baseline, str(path))
    actual = set(cache)
    for new, old in baseline['bindings'].items():
        if new not in cache and old in cache:
            cache[new] = cache[old]
    return set(baseline['ambiguous']) - actual


def validate(value):
    if (not isinstance(value,dict) or set(value)!={'cls','reason'}
            or value['cls'] not in {'positive','negative','neutral-substantive','formality'}
            or not isinstance(value['reason'],str)):
        raise ValueError('Invalid colleague-reference classification')
    return value
