"""Incrementally reconcile sealed historical windows against the held ledger.

Source IDs and exact texts are the grouping keys. Filing observations are not
employment and the resulting queue never activates a live identity or work tie.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time

from .adjudicate_covered_date_scope import career_context
from .lda_composite_repair import _atomic_json
from .operational_spine import role_state
from .reconcile_career_evidence import text_interval
from .reconcile_held_evidence import covered_state, digest, norm, packed, sha
from .refine_covered_dates import assess


def assess_clause(text, as_of):
    role = role_state(text)
    if role == 'INTERN':
        return {'status': 'EXCLUDED_INTERN', 'employment_promoted': False}
    if role != 'SUBSTANTIVE':
        return {'status': 'ROLE_REVIEW_REQUIRED', 'employment_promoted': False}
    dates = text_interval(text, as_of)
    written = assess(text, as_of)
    if written:
        if dates['status'] != 'MISSING_DATE_BOUND':
            return {'status': 'MULTIPLE_INTERVALS_REVIEW', 'employment_promoted': False}
        dates = written
    if dates['status'] == 'BOUNDED_SOURCE_INTERVAL':
        _, scope = career_context(text, dates.get('source_date_text') or dates['matched_date_text'])
        if scope != 'SINGLE_CLAUSE_DATE_SCOPE_CONFIRMED':
            return {'status': scope, 'source_date_assessment': dates, 'employment_promoted': False}
    return {**dates, 'date_scope': 'SOURCE_CLAUSE_ONLY',
            'structured_employer_title': 'REQUIRES_ADJUDICATION', 'employment_promoted': False}


def connect_queue(path):
    con = sqlite3.connect(path)
    con.executescript('''
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE IF NOT EXISTS window(id TEXT PRIMARY KEY,receipt TEXT);
      CREATE TABLE IF NOT EXISTS claim(id TEXT PRIMARY KEY,person TEXT,raw_text TEXT,
        ledger_claim_id TEXT,evidence_status TEXT,classification TEXT);
      CREATE TABLE IF NOT EXISTS clause(id TEXT PRIMARY KEY,claim_id TEXT,raw_text TEXT,status TEXT,assessment TEXT);
      CREATE TABLE IF NOT EXISTS support(window_id TEXT,id TEXT,claim_id TEXT,filing_uuid TEXT,activity TEXT,
        PRIMARY KEY(window_id,id));
      CREATE TABLE IF NOT EXISTS source_person(window_id TEXT,person TEXT,labels TEXT,status TEXT,
        PRIMARY KEY(window_id,person));
      CREATE INDEX IF NOT EXISTS support_claim ON support(claim_id);
      CREATE INDEX IF NOT EXISTS clause_status ON clause(status);
    ''')
    return con


def evaluate(source, ledger, queue, window, as_of):
    """Account for every assertion; parse only previously unseen exact texts."""
    existing = {}
    for ident, person, payload in ledger.execute(
            "SELECT id,person,payload FROM claim WHERE source='lda_covered_position'"):
        existing.setdefault((person, norm(json.loads(payload)['raw_text'])), []).append(
            (ident, json.loads(payload)['raw_text']))
    ledger_people = {row[0] for row in ledger.execute("SELECT id FROM person WHERE source='lda'")}
    seen = {row[0] for row in queue.execute('SELECT id FROM claim')}
    counts = Counter()
    for person, raw in source.execute('SELECT person,raw_text FROM covered_position GROUP BY person,raw_text'):
        claim_id = digest(['historical-exact-text-v1', person, raw])
        counts['distinct_person_texts_in_window'] += 1
        if claim_id in seen:
            counts['previously_processed_exact_texts'] += 1
            continue
        seen.add(claim_id)
        candidates = existing.get((person, norm(raw)), [])
        exact = [ident for ident, text in candidates if text == raw]
        status = ('EXACT_EXISTING_LEDGER_TEXT' if len(exact) == 1 else
                  'NORMALIZED_TEXT_VARIANT_REVIEW' if candidates else 'NEW_SOURCE_CAREER_TEXT')
        classification = covered_state(raw)
        queue.execute('INSERT INTO claim VALUES(?,?,?,?,?,?)',
                      (claim_id, person, raw, exact[0] if len(exact) == 1 else None, status, classification))
        counts[status] += 1
        if classification != 'SUBSTANTIVE_CLAIM_REQUIRES_RECONCILIATION':
            counts[classification] += 1
            continue
        # Existing exact claims have already been parsed by the ledger pipeline.
        if status == 'EXACT_EXISTING_LEDGER_TEXT':
            counts['existing_claim_reparses_avoided'] += 1
            continue
        for ordinal, text in enumerate(re.split(r'[;\n]+', raw)):
            if not norm(text):
                continue
            assessment = assess_clause(text, as_of)
            queue.execute('INSERT INTO clause VALUES(?,?,?,?,?)',
                          (digest([claim_id, ordinal]), claim_id, text, assessment['status'], packed(assessment)))
            counts['clause_' + assessment['status']] += 1
    queue.executemany('INSERT INTO support VALUES(?,?,?,?,?)', (
        (window, ident, digest(['historical-exact-text-v1', person, text]), uuid, activity)
        for ident, person, text, uuid, activity in source.execute(
            'SELECT id,person,raw_text,filing_uuid,activity FROM covered_position')))
    counts['source_assertions_accounted'] = queue.execute(
        'SELECT count(*) FROM support WHERE window_id=?', (window,)).fetchone()[0]
    for person, in source.execute('SELECT DISTINCT person FROM person_name'):
        labels = [row[0] for row in source.execute('SELECT label FROM person_name WHERE person=? ORDER BY label', (person,))]
        status = 'EXISTING_SOURCE_ID' if 'person:' + person in ledger_people else 'NEW_SOURCE_ID_REVIEW'
        queue.execute('INSERT INTO source_person VALUES(?,?,?,?)', (window, person, packed(labels), status))
        counts['person_' + status] += 1
    counts['repeated_assertions_grouped'] = counts['source_assertions_accounted'] - counts['distinct_person_texts_in_window']
    return dict(counts)


def run_window(root, ledger_path, output, window, as_of):
    root, ledger_path, output = map(lambda x: Path(x).resolve(), (root, ledger_path, output))
    output.mkdir(parents=True, exist_ok=True)
    queue_path = output / 'historical-review.sqlite'
    with connect_queue(queue_path) as queue:
        if queue.execute('SELECT 1 FROM window WHERE id=?', (window,)).fetchone():
            return None
        receipt_path = root / 'normalized' / window / 'activity.sqlite.receipt.json'
        receipt_hash = sha(receipt_path)
        receipt = json.loads(receipt_path.read_bytes())
        completed = json.loads((root / 'backfill_status.json').read_bytes())['completed'].get(window)
        if (receipt.get('status') != 'PASS' or receipt.get('scope') != 'SOURCE_ONLY_HISTORICAL_WINDOW'
                or receipt.get('window_id') != window or not completed
                or not completed['comparison']['pass'] or not receipt['acquisition_comparison']['pass']
                or completed['records'] != receipt['counts'].get('filings', 0)):
            raise ValueError('Window is not sealed and independently reconciled')
        database = receipt_path.parent / receipt['database']
        if database.parent.resolve() != receipt_path.parent or sha(database) != receipt['sha256']:
            raise ValueError('Normalized database binding failed')
        for proof in receipt['sources']:
            if sha(proof['path']) != proof['sha256']:
                raise ValueError('Acquisition manifest changed')
        ledger_hash = sha(ledger_path)
        binding = packed({'ledger_sha256': ledger_hash, 'as_of': as_of, 'builder_sha256': sha(Path(__file__))})
        prior = queue.execute("SELECT value FROM metadata WHERE key='inputs'").fetchone()
        if prior and prior[0] != binding:
            raise ValueError('Choose a new output generation: ledger, date or evaluator changed')
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source, \
                sqlite3.connect(ledger_path.as_uri() + '?mode=ro', uri=True) as ledger:
            queue.execute('BEGIN')
            queue.execute("INSERT OR IGNORE INTO metadata VALUES('inputs',?)", (binding,))
            counts = evaluate(source, ledger, queue, window, as_of)
            if counts['source_assertions_accounted'] != receipt['counts'].get('covered_position_assertions', 0):
                raise ValueError('Historical assertion accounting mismatch')
            if sha(ledger_path) != ledger_hash or sha(database) != receipt['sha256'] or sha(receipt_path) != receipt_hash:
                raise ValueError('Input changed during evaluation')
            result = {'status': 'PASS', 'window': window, 'counts': counts, 'ledger_sha256': ledger_hash,
                      'normalization_receipt_sha256': receipt_hash, 'database_sha256': receipt['sha256'],
                      'builder_sha256': sha(Path(__file__)), 'production_activated': False,
                      'employment_promoted': False, 'cross_source_identities_merged': 0,
                      'completed_at': datetime.now(timezone.utc).isoformat()}
            queue.execute('INSERT INTO window VALUES(?,?)', (window, packed(result)))
            if queue.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Queue integrity failed')
            queue.commit()
        _atomic_json(output / (window + '.json'), result)
        queue.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return result


def watch(root, ledger, output, as_of, once=False):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    status = {'status': 'RUNNING', 'production_activated': False}
    try:
        while True:
            normalized = json.loads((root / 'normalization_status.json').read_bytes())
            for window in sorted(normalized.get('windows', {})):
                result = run_window(root, ledger, output, window, as_of)
                if result:
                    status['last_completed'] = result
                    print(packed(result), flush=True)
            status['updated_at'] = datetime.now(timezone.utc).isoformat()
            _atomic_json(output / 'status.json', status)
            if once or normalized['status'] == 'NORMALIZATION_PASS':
                return
            time.sleep(20)
    except Exception as exc:
        status.update(status='ATTENTION_REQUIRED', error=type(exc).__name__ + ': ' + str(exc))
        _atomic_json(output / 'status.json', status)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--as-of', default='2026-09-08')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    watch(args.root, args.ledger, args.output, args.as_of, args.once)
