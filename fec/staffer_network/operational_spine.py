"""Directory-led candidate spine, separate from payroll population and live state.

The spine contains people, source bindings, roles, population decisions and review
queues. It does not promote new identity matches or change relationship mathematics.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import contextlib
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unicodedata

from fec.surfaces.staffer_connection_facts import employment_dates

VERSION = 'directory-led-operational-spine-v1'
ALLOWED_IDENTITY_STATUSES = {'USER_CONFIRMED_SAME_PERSON', 'REFERENCE_RESOLVED_SAME_PERSON'}


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def normalized(value):
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode()
    return ' '.join(re.findall(r'[a-z0-9]+', text.casefold()))


def role_state(title):
    """Role-level exclusions; ambiguous mixed roles never supply scored tenure."""
    text = normalized(title)
    if not text:
        return 'UNKNOWN_ROLE'
    if not re.search(r'\bintern(?:s|ship)?\b', text):
        return 'SUBSTANTIVE'
    # A coordinating role is not an internship. A mixed role is not a supervisor
    # merely because one half contains "director".
    if re.search(r'[/;&]|\band\b', str(title), re.I) and re.search(
            r'\b(?:staff assistant|legislative assistant|aide|scheduler|counsel|fellow|director)\b', text):
        return 'MIXED_ROLE_REVIEW'
    if re.search(r'\b(?:coordinator|director|manager|supervisor|administrator)\b', text):
        return 'SUBSTANTIVE'
    return 'INTERN'


def clean_office(value):
    return re.sub(r'(Democratic|Republican|Independent)(?:[A-Z]{2}.*)?$', '', str(value or '')).strip()


def profile_current_roles(profile):
    """Accept role/office pairs, including split headers; never page chrome as jobs."""
    fragments = profile.get('current_header_positions', [])
    roles, used = [], set()
    for index, raw in enumerate(fragments):
        title, sep, employer = raw.partition(' for ')
        basis = 'current_header'
        extra = None
        if not sep and raw.endswith(' for') and index + 1 < len(fragments):
            title, employer, sep = raw[:-4], fragments[index + 1], ' for '
            basis, extra = 'current_header_reassembled', index + 1
        # Source staff includes technical and administrative roles too. Do not
        # silently narrow the user's population with a job-title vocabulary.
        if not sep or not normalized(title) or not employer.strip():
            continue
        if any(x in raw.lower() for x in ('/url:', 'this button', 'colleagues', 'redirect')):
            continue
        if any(x in employer for x in ('@', '/url:', 'http:', 'https:')):
            continue
        roles.append({'title': title.strip(), 'employer': employer.strip(), 'kind': basis})
        used.add(index)
        if extra is not None:
            used.add(extra)
    if not roles:
        for entry in profile.get('employment_history', []):
            dates = employment_dates(entry.get('date_text'), profile.get('observed_at'))
            org = container(entry.get('employer'))
            if dates.get('listed_current') and org and org['kind'] in ('member_office', 'committee'):
                roles.append({'title': entry.get('title', ''), 'employer': entry['employer'], 'kind': 'current_history'})
    # Original fragments are retained in source_records. Only counts/indices appear
    # in review metadata so unstructured source contact fragments are not echoed.
    return roles, sorted(set(range(len(fragments))) - used)


def container(value):
    """Exact normalized source label only: this is not fuzzy employer resolution."""
    label = clean_office(value)
    key = normalized(label)
    if not key:
        return None
    if re.match(r'^(rep\.?|representative|sen\.?|senator)\s', label, re.I):
        kind = 'member_office'
    elif re.match(r'^(house|senate)\b.*\b(?:committee|subcommittee)\b', label, re.I):
        kind = 'committee'
    else:
        kind = 'organization_or_unresolved_office'
    return {'id': 'source-container:' + sha_bytes(key.encode())[:32], 'label': label,
            'kind': kind, 'normalization': 'EXACT_NORMALIZED_SOURCE_LABEL',
            'canonical_container_accepted': False}


def identity_bindings(groups, keep_separate=()):
    """Preserve existing decisions and roots; reject cycles or contradictory roots."""
    aliases, decisions = {}, {}
    for group in groups:
        if group.get('status') not in ALLOWED_IDENTITY_STATUSES:
            continue
        primary = group['primary_id']
        for alias in group.get('alias_ids', []):
            if alias == primary:
                raise ValueError('Identity group contains its own root as an alias')
            if alias in aliases and aliases[alias] != primary:
                raise ValueError('Conflicting existing identity roots: ' + alias)
            aliases[alias] = primary
        decisions[sha_bytes(canonical_json(group).encode())] = group

    def resolve(ident):
        seen = set()
        while ident in aliases:
            if ident in seen:
                raise ValueError('Identity cycle')
            seen.add(ident)
            ident = aliases[ident]
        return ident

    result = {a: resolve(a) for a in aliases}
    for left, right in keep_separate:
        if resolve(left) == resolve(right):
            raise ValueError('Existing identity group contradicts keep-separate decision')
    return result, decisions


def source_disposition(source, role_states, available=True, has_history=False, candidate_count=0):
    if not available:
        return 'PROFILE_UNAVAILABLE', 'review'
    if role_states and all(s == 'INTERN' for s in role_states):
        return 'INTERN_ONLY_CURRENT_EXCLUDED', 'excluded'
    if source == 'politico':
        if 'SUBSTANTIVE' in role_states:
            return 'POLITICO_CURRENT_AS_REPORTED', 'staffer_current_reported'
        if has_history:
            return 'POLITICO_HISTORICAL_ROLE_ONLY', 'staffer_historical'
        return 'ROLE_UNRESOLVED', 'review'
    if source == 'quorum':
        if 'SUBSTANTIVE' not in role_states:
            return 'ROLE_UNRESOLVED', 'review'
        if candidate_count:
            return 'QUORUM_CROSS_SOURCE_IDENTITY_REVIEW', 'review'
        return 'QUORUM_ONLY_UNDATED_ROLE', 'staffer_undated'
    return 'LDA_SOURCE_LOBBYIST', 'ecosystem_lobbyist'


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE sources(id TEXT PRIMARY KEY, path TEXT, sha256 TEXT, records INTEGER, provenance TEXT);
CREATE TABLE people(id TEXT PRIMARY KEY, label TEXT, identity_status TEXT);
CREATE TABLE source_records(id TEXT PRIMARY KEY, source TEXT REFERENCES sources(id), person_id TEXT REFERENCES people(id),
 label TEXT, disposition TEXT, payload TEXT, payload_sha256 TEXT);
CREATE TABLE identity_decisions(id TEXT PRIMARY KEY, status TEXT, payload TEXT);
CREATE TABLE aliases(alias_id TEXT PRIMARY KEY, person_id TEXT, decision_status TEXT);
CREATE TABLE containers(id TEXT PRIMARY KEY, label TEXT, kind TEXT, resolution_status TEXT);
CREATE TABLE roles(id TEXT PRIMARY KEY, source_record_id TEXT REFERENCES source_records(id), person_id TEXT REFERENCES people(id),
 container_id TEXT REFERENCES containers(id), title TEXT, role_state TEXT, role_kind TEXT, observed_at TEXT,
 source_date_text TEXT, start TEXT, end_exclusive TEXT, date_precision TEXT, temporal_candidate INTEGER,
 canonical_eligible INTEGER DEFAULT 0, payload TEXT);
CREATE TABLE memberships(person_id TEXT REFERENCES people(id), population TEXT, source_record_id TEXT REFERENCES source_records(id),
 reason TEXT, PRIMARY KEY(person_id,population,source_record_id));
CREATE TABLE preferred_roles(person_id TEXT, role_id TEXT REFERENCES roles(id), preference_reason TEXT, PRIMARY KEY(person_id,role_id));
CREATE TABLE review_queue(id TEXT PRIMARY KEY, kind TEXT, source_record_id TEXT, other_source_id TEXT, status TEXT, payload TEXT);
CREATE TABLE payroll_requests(id TEXT PRIMARY KEY, status TEXT, payload TEXT);
CREATE TABLE payroll_evidence(id TEXT PRIMARY KEY, request_id TEXT REFERENCES payroll_requests(id), source_person_id TEXT,
 source_row_id TEXT, role_state TEXT, payload TEXT);
CREATE TABLE ecosystem_references(id TEXT PRIMARY KEY, source TEXT, path TEXT, sha256 TEXT, payload TEXT);
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX role_person ON roles(person_id);
CREATE INDEX role_container_dates ON roles(container_id,start,end_exclusive);
CREATE INDEX source_person ON source_records(person_id);
CREATE INDEX member_population ON memberships(population,person_id);
CREATE INDEX name_search ON people(label COLLATE NOCASE);
CREATE INDEX review_source ON review_queue(source_record_id);
'''


class SpineWriter:
    def __init__(self, db, aliases):
        self.db, self.aliases = db, aliases
        self.identity_roots = set(aliases.values())
        self.db.executescript(SCHEMA)

    def record(self, ident, source, label, disposition, population, payload):
        person = self.aliases.get(ident, ident)
        status = 'EXISTING_IDENTITY_DECISION' if person != ident or person in self.identity_roots else 'SOURCE_LOCAL_UNRESOLVED_CROSS_SOURCE'
        self.db.execute('INSERT OR IGNORE INTO people VALUES (?,?,?)', (person, label, status))
        # Politico rows are loaded first, so preferred names also come from Politico.
        raw = canonical_json(payload)
        self.db.execute('INSERT INTO source_records VALUES (?,?,?,?,?,?,?)',
                        (ident, source, person, label, disposition, raw, sha_bytes(raw.encode())))
        self.db.execute('INSERT INTO memberships VALUES (?,?,?,?)', (person, population, ident, disposition))
        return person

    def role(self, source_id, person, title, employer, kind, observed, date_text='', as_of=None):
        state = role_state(title)
        org = container(employer)
        if org:
            self.db.execute('INSERT OR IGNORE INTO containers VALUES (?,?,?,?)',
                            (org['id'], org['label'], org['kind'], org['normalization']))
        dates = employment_dates(date_text, observed) if date_text else {}
        # Never extend a profile's "Present" beyond the source observation date.
        start, end = dates.get('start'), dates.get('end_exclusive')
        valid = bool(start and end and start < end)
        if valid and as_of:
            stop = min(end, (date.fromisoformat(as_of) + timedelta(days=1)).isoformat())
            valid = start < stop
            end = stop if valid else end
        candidate = bool(valid and org and state == 'SUBSTANTIVE' and kind == 'history')
        payload = {'title': title, 'employer': employer, 'date_text': date_text, 'dates': dates,
                   'source_observed_at': observed, 'canonical_eligible': False,
                   'temporal_exclusion_reason': None if candidate else ('INTERN' if state == 'INTERN' else
                       'MIXED_OR_UNKNOWN_ROLE' if state != 'SUBSTANTIVE' else 'UNBOUNDED_OR_NONHISTORICAL_ROLE'),
                   'duration_status': 'CANDIDATE_SOURCE_INTERVAL_NOT_CANONICAL_EMPLOYMENT'}
        rid = 'role:' + sha_bytes(canonical_json([source_id, kind, title, employer, date_text]).encode())
        self.db.execute('INSERT OR IGNORE INTO roles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (rid, source_id, person, org['id'] if org else None, title, state, kind, observed,
                         date_text, start, end, dates.get('date_precision', 'unknown'), int(candidate), 0,
                         canonical_json(payload)))
        return rid


def build(root, output, as_of='2026-09-08'):
    root, output = Path(root).resolve(), Path(output).resolve()
    local = (Path(os.environ['LOCALAPPDATA']) / 'Melange/staffer_network').resolve()
    if not output.is_relative_to(local) or output == local:
        raise ValueError('Detailed spine output must be under the machine-local staffer directory')
    if output.exists():
        raise FileExistsError('Preserve existing generation: ' + str(output))
    # Build in a sibling staging directory; the final generation appears only once complete.
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.spine-staging-', dir=output.parent))
    try:
        with contextlib.ExitStack() as stack:
            receipt = _build_staged(root, output, stage, local, as_of, stack)
        os.replace(stage, output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return receipt


def _build_staged(root, output, stage, local, as_of, stack):
    frozen = stage / 'inputs'
    frozen.mkdir()
    manifest, captured = [], {}

    def capture(key, path, prefix=None):
        path = Path(path)
        with path.open('rb') as stream:
            raw = stream.read(prefix) if prefix is not None else stream.read()
        dest = frozen / (key + ''.join(path.suffixes))
        dest.write_bytes(raw)
        entry = {'id': key, 'path': str(path), 'snapshot': str(output / 'inputs' / dest.name), 'sha256': sha_bytes(raw), 'bytes': len(raw),
                 'prefix_bytes': prefix}
        manifest.append(entry)
        captured[key] = raw
        return raw

    base = root / 'audits/staffer_network/staffer_social_network_20260908_v8_math_primary'
    baseline = json.loads(capture('baseline_receipt', base / 'staffer_social_network_receipt.json'))
    capture('builder_code', Path(__file__))
    capture('date_parser_code', root / 'fec/surfaces/staffer_connection_facts.py')
    pin = capture('live_graph_pin', root / 'fec/surfaces/staffer_graph_snapshot.py')
    if b'GENERATION = "staffer_social_network_20260908_v8_math_primary"' not in pin:
        raise ValueError('Baseline generation changed; capture and review the actual pin')
    identity = json.loads(capture('existing_identity_decisions', root / 'audits/staffer_live_runtime/identity_resolution_receipt.json'))
    assertions = json.loads(capture('user_decisions', root / 'fec/surfaces/staffer_user_assertions.json'))
    groups = identity.get('identity_groups', []) + assertions.get('identity_groups', [])
    aliases, decisions = identity_bindings(groups, assertions.get('keep_separate_pairs', []))

    corpus = local / 'normalized/source-native-corpus/generation-20260830T070500Z-d'
    qr = json.loads(capture('quorum_receipt', corpus / 'source_native_corpus_receipt.json'))
    qb = capture('quorum_records', corpus / 'quorum_legacy_source_native_seed.ndjson')
    qs = next(s for s in qr['record_files'] if s['role'] == 'quorum-legacy-source-native-seed')
    if qr['status'] != 'PASS' or qr['failures'] or sha_bytes(qb) != qs['sha256'] or len(qb) != qs['bytes']:
        raise ValueError('Quorum receipt binding failed')
    qrows = [json.loads(l) for l in qb.splitlines()]
    proot = root / 'audits/staffer_network/politico_profile_crawl_20260903_raw'
    pr = json.loads(capture('politico_receipt', proot / 'crawl_verification.json'))
    pb = capture('politico_records', proot / 'profiles.ndjson', pr['profile_prefix_bytes'])
    if pr['status'] != 'PASS' or pr['integrity_status'] != 'PASS' or pr['verification_failure_count'] or sha_bytes(pb) != pr['profile_prefix_sha256']:
        raise ValueError('Politico verified prefix binding failed')
    prows = [json.loads(l) for l in pb.splitlines()]

    lda_root = local / 'normalized/lda-source-native/generation-20260830T231200Z'
    lr = json.loads(capture('lda_receipt', lda_root / 'lda_source_native_corpus_receipt.json'))
    ls = next(s for s in lr['files'] if s['role'] == 'lobbyist_assertions')
    lb = capture('lda_records', lda_root / ls['path'])
    if lr['status'] != 'PASS' or lr['failures'] or sha_bytes(lb) != ls['sha256']:
        raise ValueError('LDA source binding failed')
    lrows = [json.loads(l) for l in gzip.decompress(lb).splitlines()]
    if len(qrows) != qs['records'] or len(prows) != pr['counts']['profile_rows'] or len(lrows) != ls['records']:
        raise ValueError('Input source counts do not reconcile')

    recon = local / 'reconciliation/quorum-politico-20260908-design-v1'
    rr = json.loads(capture('reconciliation_receipt', recon / 'reconciliation_receipt.json'))
    if rr['inputs'][0]['sha256'] != sha_bytes(qb) or rr['inputs'][1]['sha256'] != sha_bytes(pb):
        raise ValueError('Crosswalk belongs to different source snapshots')
    crosswalk_bytes = capture('candidate_crosswalk', recon / 'candidate_crosswalk.ndjson')
    cs = next(a for a in rr['artifacts'] if Path(a['path']).name == 'candidate_crosswalk.ndjson')
    if sha_bytes(crosswalk_bytes) != cs['sha256']:
        raise ValueError('Crosswalk binding failed')
    crosswalk = [json.loads(l) for l in crosswalk_bytes.splitlines()]
    candidates = defaultdict(list)
    for row in crosswalk:
        candidates[row['quorum_id']].append(row)

    dbpath = stage / 'spine.sqlite'
    db = stack.enter_context(contextlib.closing(sqlite3.connect(dbpath)))
    writer = SpineWriter(db, aliases)
    for key, path, raw, count in [('politico', proot / 'profiles.ndjson', pb, len(prows)),
                                   ('quorum', corpus / 'quorum_legacy_source_native_seed.ndjson', qb, len(qrows)),
                                   ('lda', lda_root / ls['path'], lb, len(lrows))]:
        db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (key, str(path), sha_bytes(raw), count,
                   canonical_json({'source_date_semantics': 'unknown' if key == 'quorum' else 'per_record', 'snapshot_as_of': as_of})))
    for did, group in sorted(decisions.items()):
        db.execute('INSERT INTO identity_decisions VALUES (?,?,?)', (did, group['status'], canonical_json(group)))
    for alias, person in sorted(aliases.items()):
        db.execute('INSERT INTO aliases VALUES (?,?,?)', (alias, person, 'PRESERVED_EXISTING_DECISION'))

    for p in sorted(prows, key=lambda p: p['profile_uuid']):
        sid = 'politico:' + p['profile_uuid']
        available = bool(p.get('verified_name')) and p.get('identity_status') != 'PROFILE_UNAVAILABLE_404'
        current, unused_fragments = profile_current_roles(p)
        states = [role_state(role['title']) for role in current]
        substantive_history = any(role_state(r.get('title')) == 'SUBSTANTIVE' and
            (container(r.get('employer')) or {}).get('kind') in ('member_office', 'committee')
            for r in p.get('employment_history', []))
        disposition, population = source_disposition('politico', states, available, substantive_history)
        person = writer.record(sid, 'politico', p.get('verified_name') or p['expected_name'], disposition, population, p)
        if available:
            for role in current:
                rid = writer.role(sid, person, role['title'], role['employer'], role['kind'], p.get('observed_at'), as_of=as_of)
                if role_state(role['title']) == 'SUBSTANTIVE':
                    db.execute('INSERT OR IGNORE INTO preferred_roles VALUES (?,?,?)', (person, rid, 'POLITICO_PRO_PREFERRED_SOURCE'))
            for role in p.get('employment_history', []):
                writer.role(sid, person, role.get('title', ''), role.get('employer', ''), 'history', p.get('observed_at'), role.get('date_text', ''), as_of)
            if unused_fragments:
                db.execute('INSERT INTO review_queue VALUES (?,?,?,?,?,?)', ('header:' + sid, 'UNPARSED_HEADER_FRAGMENTS', sid, None,
                           'RETAINED_OUTSIDE_ROLES', canonical_json({'fragment_indices': unused_fragments, 'count': len(unused_fragments),
                               'reconstructed_pairs': sum(r['kind']=='current_header_reassembled' for r in current)})))
        if population in ('review', 'excluded'):
            db.execute('INSERT INTO review_queue VALUES (?,?,?,?,?,?)', ('source:' + sid, disposition, sid, None,
                       'EXCLUDED_INTERN' if population == 'excluded' else 'REVIEW_REQUIRED', canonical_json({'population': population})))

    for q in sorted(qrows, key=lambda q: q['source_entity_id']):
        sid = 'quorum:' + q['source_entity_id']
        plausible = [r for r in candidates[sid] if not r.get('name_conflict') and r['agreement'] != 'UNAVAILABLE_PROFILE_NAME_LEAD']
        disposition, population = source_disposition('quorum', [role_state(q.get('title'))], candidate_count=len(plausible))
        person = writer.record(sid, 'quorum', q['display_name'], disposition, population, q)
        rid = writer.role(sid, person, q.get('title', ''), q.get('member_office_source_key', ''), 'undated_directory', None, as_of=as_of)
        if population == 'staffer_undated':
            db.execute('INSERT INTO preferred_roles VALUES (?,?,?)', (person, rid, 'QUORUM_ONLY_DATE_UNKNOWN'))
        for proposal in candidates[sid]:
            did = 'candidate:' + sha_bytes(canonical_json([sid, proposal['politico_id']]).encode())
            db.execute('INSERT INTO review_queue VALUES (?,?,?,?,?,?)', (did, proposal['agreement'], sid, proposal['politico_id'],
                       'REVIEW_REQUIRED', canonical_json({**proposal, 'preferred_role_source_after_identity_acceptance': 'politico', 'new_identity_merge': False})))
    for l in sorted(lrows, key=lambda l: l['source_person_id']):
        sid = 'person:' + l['source_person_id']
        writer.record(sid, 'lda', l['display_name'], 'LDA_SOURCE_LOBBYIST', 'ecosystem_lobbyist', l)

    # Ecosystem incidences remain in their existing source indexes; no all-pairs clique.
    for entry in lr['files']:
        db.execute('INSERT INTO ecosystem_references VALUES (?,?,?,?,?)', ('lda:' + entry['role'], 'lda', str(lda_root / entry['path']), entry['sha256'], canonical_json(entry)))
    for entry in qr['record_files']:
        if entry['role'].startswith('littlesis'):
            filename = 'littlesis_entities.ndjson.gz' if entry['role'] == 'littlesis-entities' else 'littlesis_relationship_assertions.ndjson.gz'
            db.execute('INSERT INTO ecosystem_references VALUES (?,?,?,?,?)', (entry['role'], 'littlesis', str(corpus / filename), entry['sha256'], canonical_json(entry)))

    pay_receipt_path = recon / 'payroll_ambiguity_cached_review_receipt.json'
    if pay_receipt_path.exists():
        pay_receipt = json.loads(capture('payroll_review_receipt', pay_receipt_path))
        pay_bytes = capture('targeted_payroll_review', Path(pay_receipt['output']['path']))
        if sha_bytes(pay_bytes) != pay_receipt['output']['sha256'] or pay_receipt['proof_row_binding_failures']:
            raise ValueError('Targeted payroll review binding failed')
        for row in map(json.loads, pay_bytes.splitlines()):
            db.execute('INSERT INTO payroll_requests VALUES (?,?,?)', (row['request_id'], row['status'], canonical_json(row)))
            for pid, proofs in row['candidate_support'].items():
                for proof in proofs:
                    ep = proof['payroll_proof']
                    if not proof['matches_pinned_bundle_rows']:
                        continue
                    for source_row in ep['provenance']['source_row_ids']:
                        ident = sha_bytes(canonical_json([row['request_id'], pid, source_row]).encode())
                        db.execute('INSERT OR IGNORE INTO payroll_evidence VALUES (?,?,?,?,?,?)',
                                   (ident, row['request_id'], pid, source_row, role_state(ep['title']), canonical_json(proof)))

    policy = {'version': VERSION, 'as_of': as_of, 'preferred_role_source': 'politico', 'payroll_mode': 'TARGETED_REQUESTS_ONLY',
              'new_identity_merges': 0, 'internship_scored_days': 0, 'core_sources': ['politico', 'quorum'],
              'quorum_unresolved_match_policy': 'RETAIN_SEPARATE_IN_REVIEW_NOT_DOUBLE_COUNTED_AS_CURRENT',
              'math_changed': False, 'canonical_employment_promotions': 0, 'production_consumer_enabled': False,
              'source_local_people_not_globally_deduplicated': True,
              'registry_boundary': 'Directory and LDA source entities only; external existing aliases retained as redirects, not admitted people.'}
    for key, value in [('policy', policy), ('baseline', baseline), ('input_manifest', manifest),
                        ('keep_separate_pairs', assertions.get('keep_separate_pairs', [])),
                        ('relationship_assertions', assertions.get('coworker_assertions', []))]:
        db.execute('INSERT INTO metadata VALUES (?,?)', (key, canonical_json(value)))
    db.commit()
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Candidate database integrity failed')
    counts = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in
              ['people', 'source_records', 'identity_decisions', 'aliases', 'roles', 'containers', 'review_queue', 'payroll_requests', 'payroll_evidence']}
    populations = dict(db.execute('SELECT population,count(DISTINCT person_id) FROM memberships GROUP BY population'))
    dispositions = dict(db.execute('SELECT disposition,count(*) FROM source_records GROUP BY disposition'))
    role_counts = dict(db.execute('SELECT role_state,count(*) FROM roles GROUP BY role_state'))
    temporal = db.execute('SELECT count(*) FROM roles WHERE temporal_candidate=1').fetchone()[0]
    db.close()
    # Static source bytes must not change during capture. A live identity receipt is
    # intentionally a frozen observation, not a demand that its worker stop updating.
    for entry in manifest:
        if entry['id'] in ('existing_identity_decisions',):
            continue
        with Path(entry['path']).open('rb') as stream:
            raw = stream.read(entry['prefix_bytes']) if entry['prefix_bytes'] is not None else stream.read()
        if sha_bytes(raw) != entry['sha256']:
            raise ValueError('Input changed during candidate build: ' + entry['id'])
    receipt = {'schema': VERSION, 'status': 'BUILT_AWAITING_INDEPENDENT_VERIFICATION', 'policy': policy,
               'counts': counts, 'population_distinct_ids': populations, 'source_dispositions': dispositions,
               'role_states': role_counts, 'bounded_nonintern_source_interval_candidates': temporal,
               'baseline_pinned_person_candidates': baseline['node_universe']['person'],
               'database': {'path': str(output / 'spine.sqlite'), 'sha256': sha_file(dbpath)}, 'inputs': manifest,
               'identity_quality_status': 'EXISTING_DECISIONS_PRESERVED_NEW_MATCHES_REVIEW_REQUIRED',
               'relationship_quality_status': 'SPINE_ONLY_NO_NEW_RELATIONSHIPS_PROMOTED',
               'build_code_sha256': sha_file(__file__)}
    (stage / 'spine_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def summary(directory):
    return json.loads((Path(directory) / 'spine_receipt.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--as-of', default='2026-09-08')
    args = parser.parse_args()
    report = build(args.root, args.output, args.as_of)
    print(json.dumps({k: v for k, v in report.items() if k != 'inputs'}, indent=2))
