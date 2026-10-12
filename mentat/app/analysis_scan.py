"""Disposable narrow copy for Insights; never rewrites the verified source index."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import uuid

import contribution_data as data
import contribution_query_store as store

FIELDS = ('source_id','industry','company','committee_id','recipient','date','cycle',
          'funding_source','party','contributor_key','raw_cents','receipt_cents',
          'original_cents','supported_cents','pac_cents')
CODE_SHA = data._hash(__file__)
CODE_STAMP = data._stamp(__file__)


def view(source_path):
    """Copy only analysis fields once per immutable source; hash-bound on reuse."""
    if data._stamp(__file__) != CODE_STAMP:
        raise ValueError('Insights scan code changed. Restart the console.')
    source_path = Path(source_path)
    version = data._stamp(source_path)
    identity = {'source_stamp':list(version),'insights_projection':CODE_SHA}
    key = store._key(identity)
    path = store.ROOT / 'insights-views' / (key + '.sqlite')
    proof_path = path.with_suffix('.json')
    if not store.source_stable(source_path, version):
        raise ValueError('Insights source has active writes.')
    if store._valid(path, proof_path, identity):
        return path
    with store._lock(path.with_suffix('.lock')):
        if store._valid(path, proof_path, identity):
            return path
        pending = path.with_suffix('.' + uuid.uuid4().hex + '.pending')
        try:
            with closing(sqlite3.connect(pending, uri=True)) as db:
                db.execute('PRAGMA journal_mode=OFF')
                db.execute('PRAGMA synchronous=OFF')
                db.execute('ATTACH DATABASE ? AS original', (source_path.resolve().as_uri()+'?mode=ro',))
                fields = ','.join('"'+field+'"' for field in FIELDS)
                db.execute('CREATE TABLE facts AS SELECT '+fields+' FROM original.facts')
                db.execute('CREATE UNIQUE INDEX facts_identity ON facts(source_id)')
                db.execute('CREATE INDEX facts_company ON facts(company)')
                db.execute('CREATE TABLE metadata AS SELECT * FROM original.metadata')
                count = db.execute('SELECT COUNT(*) FROM facts').fetchone()[0]
                if count != db.execute('SELECT COUNT(*) FROM original.facts').fetchone()[0]:
                    raise ValueError('Insights projection did not preserve every source observation.')
                db.commit()
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Insights projection failed integrity verification.')
            if not store.source_stable(source_path, version):
                raise ValueError('Insights source changed while creating its projection.')
            proof = {'identity':identity,'sha256':data._hash(pending),'fact_count':count}
            pending.replace(path)
            store._atomic(proof_path,json.dumps(proof,sort_keys=True).encode())
            return path
        finally:
            if pending.exists():
                pending.unlink()
