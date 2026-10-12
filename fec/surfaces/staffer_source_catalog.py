"""Sealed, disk-backed source records. Source assertions do not create model ties.

Chunk compression retains full records without millions of Python dictionaries.
The catalog is local only and preserves the normalized corpus's contact redactions.
"""
from pathlib import Path
from collections import Counter
from contextlib import closing
import gzip
import hashlib
import json
import os
import sqlite3
import shutil
import zlib

DATA_ROOT = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Melange/staffer_network'
CORPUS = DATA_ROOT / 'normalized/source-native-corpus/generation-20260830T070500Z-d'
CATALOG = DATA_ROOT / 'live_catalog/source_catalog_v1.sqlite'

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def records(path):
    with (gzip.open if path.suffix == '.gz' else open)(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)

def build_catalog(output=CATALOG, corpus=CORPUS, raw=None):
    """New immutable output only; every admitted input is byte-bound in its receipt."""
    from fec.staffer_network.littlesis_contract import stream_json_array
    from fec.staffer_network.source_native_corpus import _relationship_assertion, _restricted_text, SourceNativeCorpusError
    output, corpus = Path(output), Path(corpus)
    if shutil.disk_usage(output.parent if output.parent.exists() else corpus).free < 2 * 1024**3:
        raise OSError('Source catalog needs at least 2 GiB free before starting')
    receipt_path = corpus / 'source_native_corpus_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('status') != 'PASS' or receipt.get('failures'):
        raise ValueError('Source corpus receipt failed')
    if output.exists():
        raise FileExistsError('Preserve existing catalog: ' + str(output))
    raw = Path(raw) if raw else DATA_ROOT / 'raw/littlesis/snapshot-20260830T062400Z/relationships.json.gz'
    entities = corpus / 'littlesis_entities.ndjson.gz'
    quorum = corpus / 'quorum_legacy_source_native_seed.ndjson'
    inputs = []
    for path, role, specs in ((entities, 'littlesis-entities', receipt['record_files']),
                              (quorum, 'quorum-legacy-source-native-seed', receipt['record_files']),
                              (raw, 'relationships', receipt['input_files'])):
        spec = next(s for s in specs if s['role'] == role)
        if path.stat().st_size != spec['bytes'] or sha(path) != spec['sha256']:
            raise ValueError('Source catalog input mismatch: ' + role)
        inputs.append({**spec, 'path': str(path)})
    output.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output)
    db.executescript('''PRAGMA journal_mode=OFF; PRAGMA temp_store=MEMORY;
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, payload BLOB NOT NULL);
        CREATE TABLE entities(id TEXT PRIMARY KEY, label TEXT, kind TEXT, source TEXT, summary TEXT, chunk INTEGER, ordinal INTEGER);
        CREATE TABLE relationships(id INTEGER PRIMARY KEY, subject TEXT, object TEXT, category TEXT, chunk INTEGER, ordinal INTEGER);
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);''')
    entity_ids, counts, categories = set(), Counter(), Counter()
    chunk, batch, redactions, boss_redactions = 0, [], [0], [0]

    def flush():
        nonlocal chunk, batch
        if batch:
            if shutil.disk_usage(output.parent).free < 256 * 1024**2:
                raise OSError('Stopping unpublished catalog before exhausting the disk reserve')
            db.execute('INSERT INTO chunks VALUES (?,?)', (chunk, zlib.compress(json.dumps(batch, ensure_ascii=False, separators=(',', ':')).encode(), 6)))
            db.commit()
            chunk += 1
            batch = []

    def put_entity(ident, label, kind, source, summary, row):
        db.execute('INSERT INTO entities VALUES (?,?,?,?,?,?,?)', (ident, label, kind, source, summary or '', chunk, len(batch)))
        batch.append(row)
        counts[source + '_' + kind] += 1
        if len(batch) >= 1000:
            flush()

    for row in records(entities):
        entity_ids.add(row['source_numeric_id'])
        put_entity(row['source_entity_id'], row.get('name') or 'Name unavailable',
                   'person' if row['primary_ext'] == 'Person' else 'organization', 'LittleSis', row.get('blurb'), row)
    flush()
    for row in records(quorum):
        put_entity('quorum:' + str(row['source_entity_id']), row['display_name'], 'person', 'Quorum',
                   ' · '.join(filter(None, [row.get('title'), row.get('member_office_source_key')])), row)
    flush()
    for record in stream_json_array(raw):
        try:
            row = _relationship_assertion(record, redactions, entity_ids, boss_redactions)
        except SourceNativeCorpusError as exc:
            if 'date precision' not in str(exc):
                raise
            safe = {**record, 'attributes': {**record['attributes'], 'start_date': None, 'end_date': None}}
            row = _relationship_assertion(safe, redactions, entity_ids, boss_redactions)
            row.update(raw_start_date=record['attributes'].get('start_date'), raw_end_date=record['attributes'].get('end_date'),
                       precision_bucket='invalid_source_date', date_validation_error=str(exc))
            counts['relationships_with_invalid_source_dates'] += 1
        # Retain financial/category facts across all native categories as source assertions.
        attrs = record['attributes']
        row['source_amount'] = attrs.get('amount')
        row['source_currency'] = _restricted_text(attrs.get('currency'), redactions)
        row['endpoint_missing'] = any(int(attrs[k]) not in entity_ids for k in ('entity1_id','entity2_id'))
        row['operator_included'] = False
        db.execute('INSERT INTO relationships VALUES (?,?,?,?,?,?)',
                   (row['source_numeric_id'], row['subject_source_entity_id'], row['object_source_entity_id'], row['category_name'], chunk, len(batch)))
        batch.append(row)
        categories[row['category_name']] += 1
        counts['relationships'] += 1
        counts['relationships_with_missing_endpoint'] += int(row['endpoint_missing'])
        if len(batch) >= 1000:
            flush()
        if counts['relationships'] % 250000 == 0:
            print('Indexed relationships:', counts['relationships'], flush=True)
    flush()
    if len(entity_ids) != receipt['entity_observed'] or counts['relationships'] != receipt['relationship_observed'] or counts['Quorum_person'] != next(s['records'] for s in receipt['record_files'] if s['role'].startswith('quorum')):
        raise ValueError('Source catalog conservation failed')
    db.executescript('CREATE INDEX relationship_subject ON relationships(subject,id); CREATE INDEX relationship_object ON relationships(object,id); CREATE INDEX entity_label ON entities(label COLLATE NOCASE);')
    report = {'schema':'staffer-source-catalog-v1', 'status':'PASS', 'inputs':inputs,
              'corpus_receipt':str(receipt_path), 'corpus_receipt_sha256':sha(receipt_path),
              'counts':dict(counts), 'categories':dict(categories),
              'contact_redactions':redactions[0], 'missing_boss_references':boss_redactions[0],
              'scope':'All held LittleSis entities and relationship categories plus every Quorum directory ID. Source assertions only; no inferred identities, dates or model weights.',
              'missing_endpoint_policy':'Retained and marked; unresolved endpoint is never invented.',
              'identity_policy':'Source IDs stay separate from other sources until an evidenced identity decision.',
              'builder_sha256':sha(Path(__file__))}
    db.execute('INSERT INTO metadata VALUES (?,?)', ('receipt', json.dumps(report)))
    db.commit()
    check = db.execute('PRAGMA integrity_check').fetchone()[0]
    db.close()
    if check != 'ok':
        raise ValueError(check)
    seal = {**report, 'path':str(output), 'bytes':output.stat().st_size, 'sha256':sha(output)}
    output.with_suffix('.receipt.json').write_text(json.dumps(seal, indent=2), encoding='utf-8')
    return seal

class SourceCatalog:
    def __init__(self, path=CATALOG):
        self.path = Path(path)
        self.receipt = json.loads(self.path.with_suffix('.receipt.json').read_text(encoding='utf-8'))
        if self.receipt['status'] != 'PASS' or self.path.stat().st_size != self.receipt['bytes'] or sha(self.path) != self.receipt['sha256']:
            raise ValueError('Source catalog seal mismatch')

    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)

    def index_rows(self):
        with closing(self.connect()) as db:
            for ident, label, kind, source, summary in db.execute('SELECT id,label,kind,source,summary FROM entities ORDER BY id'):
                yield [ident, label, summary[:180], '', '', '', source, False, kind]

    def record(self, ident, offset=0, limit=50):
        offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
        with closing(self.connect()) as db:
            row = db.execute('SELECT label,kind,source,summary,chunk,ordinal FROM entities WHERE id=?', (ident,)).fetchone()
            if not row:
                return {'status':'unknown_source_record', 'record':None}
            cache = {}
            def unpack(chunk, ordinal):
                if chunk not in cache:
                    cache[chunk] = json.loads(zlib.decompress(db.execute('SELECT payload FROM chunks WHERE id=?', (chunk,)).fetchone()[0]))
                return cache[chunk][ordinal]
            total = db.execute('SELECT count(*) FROM (SELECT id FROM relationships WHERE subject=? UNION SELECT id FROM relationships WHERE object=?)', (ident, ident)).fetchone()[0]
            entries = db.execute('SELECT id,subject,object,category,chunk,ordinal FROM relationships WHERE id IN (SELECT id FROM relationships WHERE subject=? UNION SELECT id FROM relationships WHERE object=?) ORDER BY id LIMIT ? OFFSET ?', (ident, ident, limit, offset))
            relationships = []
            for rid, subject, obj, category, chunk, ordinal in entries:
                other = obj if subject == ident else subject
                label = db.execute('SELECT label FROM entities WHERE id=?', (other,)).fetchone()
                relationships.append({**unpack(chunk, ordinal), 'other_id':other, 'other_label':label[0] if label else 'Endpoint absent from source inventory'})
            return {'status':'source_evidence', 'node':{'id':ident, 'label':row[0], 'kind':row[1], 'source_system':row[2], 'source_summary':row[3], 'canonical_eligible':False},
                    'record':unpack(row[4], row[5]), 'relationships':relationships, 'total':total, 'offset':offset, 'limit':limit,
                    'has_more':offset+len(relationships)<total, 'provenance':{'catalog_sha256':self.receipt['sha256'], 'inputs':self.receipt['inputs']},
                    'note':'Source-reported assertions; dates and current-status flags describe the source snapshot. These records do not create introduction routes.'}

if __name__ == '__main__':
    print(json.dumps(build_catalog(), indent=2))
