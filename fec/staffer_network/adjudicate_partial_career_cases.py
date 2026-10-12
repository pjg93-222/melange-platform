"""Apply held office/title evidence only to newly parsed bounded partial claims."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

from .complete_existing_career_histories import classify_historical_clauses, write_gzip
from .compact_existing_review import packed, sha


def rows(path):
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        for line in stream:
            yield json.loads(line)


def run(receipt_path, ledger_path, output):
    receipt_path, ledger_path, output = map(Path, (receipt_path, ledger_path, output))
    if output.exists():
        raise ValueError('Immutable output exists')
    receipt = json.loads(receipt_path.read_bytes())
    for item in (receipt['source_file'], receipt['unseen_clauses']):
        if sha(item['path']) != item['sha256']:
            raise ValueError('Source bytes changed')
    source = list(rows(receipt['source_file']['path']))
    db = sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE claim(id TEXT,person TEXT); CREATE TABLE clause(id TEXT,claim_id TEXT,raw_text TEXT,status TEXT,assessment TEXT); CREATE TABLE support(window_id TEXT,id TEXT,claim_id TEXT,filing_uuid TEXT,activity TEXT);')
    seen = set()
    for item in rows(receipt['unseen_clauses']['path']):
        if item['assessment']['status'] != 'BOUNDED_SOURCE_INTERVAL':
            continue
        parent = hashlib.sha256(packed([item['person'],item['parent_raw_text']]).encode()).hexdigest()
        if parent not in seen:
            seen.add(parent)
            db.execute('INSERT INTO claim VALUES(?,?)',(parent,item['person']))
            for lo,hi in item['source_rows']:
                for index in range(lo,hi+1):
                    original=source[index-1]
                    if original['person'] != item['person'] or original['covered_position'] != item['parent_raw_text']:
                        raise ValueError('Source reference disagrees with claim')
                    db.execute('INSERT INTO support VALUES(?,?,?,?,?)',('partial-2018',str(index),parent,
                        original['filing_uuid'],str(original['activity_ordinal'])))
        clause_id=hashlib.sha256(packed([parent,item['clause_ordinal']]).encode()).hexdigest()
        db.execute('INSERT INTO clause VALUES(?,?,?,?,?)',(clause_id,parent,item['raw_text'],
                   item['assessment']['status'],packed(item['assessment'])))
    with sqlite3.connect(ledger_path.resolve().as_uri()+'?mode=ro',uri=True) as ledger:
        accepted,held=classify_historical_clauses(ledger,db)
    expected=receipt['clause_counts']['BOUNDED_SOURCE_INTERVAL']
    if len(accepted)+len(held)!=expected:
        raise ValueError('Adjudication partition incomplete')
    output.mkdir(parents=True)
    files=[write_gzip(output/'supported_clauses.ndjson.gz',accepted),write_gzip(output/'held_clauses.ndjson.gz',held)]
    result={'status':'PASS','bounded_clauses':expected,'counts':dict(Counter(r['status'] for r in accepted+held)),
            'inputs':{'receipt':str(receipt_path.resolve()),'receipt_sha256':sha(receipt_path),
                      'ledger':str(ledger_path.resolve()),'ledger_sha256':sha(ledger_path)},
            'files':files,'production_eligible':False,'window_complete':False,
            'policy':'Partial-window source claims; exact phrase normalization does not establish employment or identity.'}
    (output/'receipt.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('receipt','ledger','output'):p.add_argument('--'+key,required=True,type=Path)
    a=p.parse_args();print(json.dumps(run(a.receipt,a.ledger,a.output),indent=2))
