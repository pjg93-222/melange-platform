"""Complete, sealed LDA source records and assertions, paged independently of routes."""
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import zlib

try:
    from .staffer_source_catalog import DATA_ROOT, SourceCatalog, records, sha
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_source_catalog import DATA_ROOT, SourceCatalog, records, sha

LDA_CORPUS = DATA_ROOT / 'normalized/lda-source-native/generation-20260830T231200Z'
LDA_CATALOG = DATA_ROOT / 'live_catalog/lda_source_catalog_v1.sqlite'
ROLES = ('lobbyist_assertions', 'organization_assertions', 'activity_assertions',
         'industry_edges', 'covered_position_assertions')


def build_catalog(output=LDA_CATALOG, corpus=LDA_CORPUS):
    output, corpus = Path(output), Path(corpus)
    receipt_path = corpus / 'lda_source_native_corpus_receipt.json'
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    if receipt.get('status') != 'PASS' or receipt.get('failures'):
        raise ValueError('LDA source receipt is not PASS')
    if output.exists() or output.with_suffix('.receipt.json').exists():
        raise FileExistsError('Preserve immutable LDA catalog: ' + str(output))
    specs = {s['role']: s for s in receipt['files']}
    inputs = []
    for role in ROLES:
        spec = specs[role]
        path = corpus / spec['path']
        if path.stat().st_size != spec['bytes'] or sha(path) != spec['sha256']:
            raise ValueError('LDA input hash/size mismatch: ' + role)
        inputs.append({**spec, 'path': str(path)})
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < 8 * 1024**3:
        raise OSError('LDA catalog requires an 8 GiB free-space reserve')
    temporary = output.with_suffix('.building.sqlite')
    if temporary.exists():
        raise FileExistsError('Preserve unfinished LDA build: ' + str(temporary))
    counts, categories = Counter(), Counter()
    chunk, batch, rid = 0, [], 0
    with closing(sqlite3.connect(temporary)) as db:
        db.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
          PRAGMA temp_store=FILE; PRAGMA cache_size=-32768;
          CREATE TABLE chunks(id INTEGER PRIMARY KEY, payload BLOB NOT NULL);
          CREATE TABLE entities(id TEXT PRIMARY KEY, label TEXT, kind TEXT, source TEXT, summary TEXT, chunk INTEGER, ordinal INTEGER);
          CREATE TABLE relationships(id INTEGER PRIMARY KEY, subject TEXT, object TEXT, category TEXT, chunk INTEGER, ordinal INTEGER);
          CREATE TABLE source_ids(id TEXT PRIMARY KEY);
          CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);''')

        def flush():
            nonlocal chunk, batch
            if not batch:
                return
            if shutil.disk_usage(output.parent).free < 512 * 1024**2:
                raise OSError('LDA build stopped before disk reserve exhausted')
            data = json.dumps(batch, ensure_ascii=False, separators=(',', ':')).encode()
            db.execute('INSERT INTO chunks VALUES (?,?)', (chunk, zlib.compress(data, 3)))
            db.commit()
            chunk += 1
            batch = []

        for role in ROLES:
            observed = 0
            for row in records(corpus / specs[role]['path']):
                observed += 1
                if role in ('lobbyist_assertions', 'organization_assertions', 'activity_assertions'):
                    if role == 'lobbyist_assertions':
                        ident, kind, label = row['source_person_id'], 'person', row['display_name']
                    elif role == 'organization_assertions':
                        ident, kind, label = row['source_org_id'], 'organization', row['display_name']
                    else:
                        ident, kind = row['source_activity_id'], 'activity'
                        label = ' · '.join(str(v) for v in (row.get('general_issue_code_display') or row.get('general_issue_code') or 'Lobbying activity', row.get('filing_year'), row.get('filing_period')) if v)
                    summary = row.get('description') or row.get('organization_kind') or 'Source-native LDA record'
                    db.execute('INSERT INTO entities VALUES (?,?,?,?,?,?,?)', (ident, label, kind, 'Lobbying', summary, chunk, len(batch)))
                    counts[kind] += 1
                else:
                    if role == 'industry_edges':
                        source_id, subject, obj, category = row['edge_id'], row['subject_id'], row['object_id'], row['predicate']
                    else:
                        source_id, subject, obj = row['assertion_id'], row['source_person_id'], row['source_activity_id']
                        category = 'COVERED_POSITION_ASSERTION'
                    db.execute('INSERT INTO source_ids VALUES (?)', (source_id,))
                    db.execute('INSERT INTO relationships VALUES (?,?,?,?,?,?)', (rid, subject, obj, category, chunk, len(batch)))
                    rid += 1
                    categories[category] += 1
                    row = {**row, 'source_relationship_id': source_id, 'category_name': category,
                           'operator_included': False, 'canonical_eligible': False}
                batch.append(row)
                if len(batch) >= 1000:
                    flush()
                if observed % 250000 == 0:
                    print(role, observed, flush=True)
            flush()
            if observed != specs[role]['records']:
                raise ValueError('LDA record conservation failed: ' + role)
            counts[role] = observed
            print(role, 'complete', observed, flush=True)
        db.executescript('CREATE INDEX relationship_subject ON relationships(subject,id); CREATE INDEX relationship_object ON relationships(object,id);')
        counts['relationships'] = rid
        counts['missing_endpoint_assertions'] = db.execute('''SELECT count(*) FROM relationships r
          WHERE NOT EXISTS(SELECT 1 FROM entities e WHERE e.id=r.subject)
          OR NOT EXISTS(SELECT 1 FROM entities e WHERE e.id=r.object)''').fetchone()[0]
        if receipt_path.read_bytes() != receipt_bytes:
            raise ValueError('LDA receipt changed while building')
        for item in inputs:
            if sha(item['path']) != item['sha256']:
                raise ValueError('LDA source changed while building: ' + item['role'])
        report = {'schema': 'staffer-lda-source-catalog-v1', 'status': 'PASS',
                  'inputs': inputs, 'corpus_receipt': str(receipt_path),
                  'corpus_receipt_sha256': sha(receipt_path), 'builder_sha256': sha(__file__),
                  'counts': dict(counts), 'categories': dict(categories),
                  'scope': 'Every held normalized LDA lobbyist, organization, activity, industry edge and covered-position assertion. Source evidence only; no added model weights or identity decisions.'}
        db.execute('INSERT INTO metadata VALUES (?,?)', ('receipt', json.dumps(report)))
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('LDA catalog integrity check failed')
    temporary.replace(output)
    report.update(path=str(output), bytes=output.stat().st_size, sha256=sha(output))
    output.with_suffix('.receipt.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


class LdaCatalog(SourceCatalog):
    def __init__(self, path=LDA_CATALOG):
        super().__init__(path)
        if self.receipt.get('schema') != 'staffer-lda-source-catalog-v1':
            raise ValueError('Unsupported LDA catalog')

    def index_rows(self):
        # Existing exact-ID inventory already includes all lobbyists. Activities
        # remain accessible from evidence pages without crowding the people map.
        with closing(self.connect()) as db:
            for ident, label, summary in db.execute("SELECT id,label,summary FROM entities WHERE kind='organization' ORDER BY id"):
                yield ['organization:' + ident, label, summary[:180], '', '', '', 'Lobbying', False, 'organization']

    def record(self, ident, offset=0, limit=50):
        if ident.startswith(('organization:lda:', 'person:lda:')):
            ident = ident.split(':', 1)[1]
        result = super().record(ident, offset, limit)
        if result.get('record'):
            result['relationship_label'] = 'LDA source assertions'
            result['note'] = 'Complete held LDA source evidence. Filing reports and covered-position text do not establish continuous employment, identity matches or personal familiarity.'
            for row in result['relationships']:
                row['endpoint_missing'] = row['other_label'] == 'Endpoint absent from source inventory'
                if row.get('covered_position'):
                    row['description'] = row['covered_position']
        return result


if __name__ == '__main__':
    print(json.dumps(build_catalog(), indent=2), flush=True)
