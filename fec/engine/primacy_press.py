"""Aggregate the adopted, immutable classified media vintage.

This input is a historical labeled corpus, not an automatically refreshed media
feed. Updating it requires a complete classified generation plus its receipt.
The producer records this limitation; it never treats new/unclassified speech
as neutral, silently discards an admitted crosspost, or falls back on old counts.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

LEDGER = 'fec/config/primacy_press_v1.ndjson'
RECEIPT = 'fec/config/primacy_press_v1.json'
CLASS_W = {'CU_DIRECT': 1.0, 'SHARED': 0.5, 'OTHER_INDUSTRY': 0.0, 'OFF_TOPIC': 0.0}


def load(roster, root):
    raw = (Path(root) / LEDGER).read_bytes()
    receipt = json.loads((Path(root) / RECEIPT).read_text(encoding='utf-8'))
    if (receipt.get('schema') != 'primacy-press-corpus-v1'
            or receipt.get('sha256') != hashlib.sha256(raw).hexdigest()
            or receipt.get('class_weights') != CLASS_W
            or receipt.get('crossposts') != 'all'
            or receipt.get('third_party') != 'included'
            or receipt.get('decay') != 'none'):
        raise ValueError('classified primacy press receipt/hash/policy mismatch')
    groups = defaultdict(list)
    name_by_bioguide = {row['bioguide']: member for member, row in roster.items()}
    seen = set()
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        occurrence = row['occurrence']
        if occurrence in seen:
            raise ValueError('duplicate classified press occurrence')
        seen.add(occurrence)
        member = row['member']
        if member not in roster and row['bioguide'] in name_by_bioguide:
            raise ValueError('classified press roster name changed; reconcile identity: ' + member)
        if member in roster and row['bioguide'] != roster[member]['bioguide']:
            raise ValueError('classified press member/bioguide mismatch: ' + member)
        direction = int(row['direction'])
        weight = 0.10 * float(row['intensity_scaled'] or 1.0)
        if direction not in (-1, 1) or not math.isfinite(weight) or weight <= 0:
            raise ValueError('invalid classified press direction/intensity')
        klass = row['effective_class']
        if klass not in CLASS_W:
            raise ValueError('unclassified primacy press item: ' + row['id'])
        groups[member].append((weight * CLASS_W[klass] * direction, weight))
    if len(seen) != receipt['items'] or len(groups) != receipt['members']:
        raise ValueError('classified press corpus counts mismatch')
    proportions, items = {}, {}
    for member in roster:
        values = groups.get(member, [])
        numerator = round(sum(value[0] for value in values), 9)
        denominator = round(sum(value[1] for value in values), 9)
        proportions[member] = numerator / denominator if denominator else 0.0
        items[member] = len(values)
    return proportions, items, Counter(classified_items=len(seen)), receipt
