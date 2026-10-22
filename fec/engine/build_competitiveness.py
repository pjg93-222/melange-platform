# -*- coding: utf-8 -*-
"""COMPETITIVENESS MULTIPLIER c (COMPET_MULT) -- artifact builder (Paul rulings 2026-10-05 and 2026-10-07, brain D-107: Kiley oriented R; 2026 ballot-district
overrides; Sanders/King oriented D). The territory delegates' c = 1 (PR; GU/VI/AS/MP) and DC's D+44 state row are ruled 2026-10-11 (Paul-delegated, CM-3); a district-number join
onto another incumbent's Cook row with no override remains an agent default (D-105/D-107), not a Paul ruling.

c = 1 + AMPLITUDE * exp(-max(lean, 0) / SCALE), range (1, 2], rounded to 6 dp AT THE SOURCE (here).
lean = Cook PVI signed points x (+1 if the member's ORIENTED party is R, -1 if D). Cook signs PVI R positive,
D negative, EVEN = 0, so lean is how many Cook points the seat leans TOWARD the member's own party:
a Democrat in a D+10 seat has lean +10 (c ~ 1.14); a Democrat in an R+7 seat has lean -7 (c = 2.0).

AMPLITUDE=1.0 and SCALE=5.0 are PAUL'S JUDGMENT constants: uncalibrated, not fitted to any outcome, and the
reciprocity they encode (dollars buy more where the seat is competitive) is NOT validated. The formula lives in
moneyball_core (compet_c) so this builder and the kernel accessor MC.cmult cannot drift.

House: Cook 2026 district PVI. Senate: Paul's state PVI table (ALL senators regardless of 2026 class).
Open seats / retirees keep the boost and are flagged. Output is the LIVE artifact data/money/competitiveness_c.json, read by the
kernel's MC.cmult when COMPET_MULT is on (the default since Paul 2026-10-06; COMPET_MULT=0 reverts to exactly 1.0 and reads no file).
Deterministic: no timestamps, so a chain re-run rewrites byte-identical bytes.

BALLOT-DISTRICT OVERRIDES (Paul ruling 2026-10-07, D-107 R3/R4): data/money/cook_pvi_2026/ballot_district_overrides.csv, UTF-8, LF, exact header
  bioguide,name,ballot_district,status,pvi_basis,source_url,source_date,ruling
Precedence: override > Cook incumbent placement > district number. Each row names the Cook district_id (SS-NN; at-large SS-00) whose PVI the member takes:
  status RUNNING_HOUSE      + pvi_basis ballot_2026       -> that district's 2026 PVI (the district the member is on the 2026 ballot for)
  status NOT_RUNNING_HOUSE  + pvi_basis current_seat_2025 -> that district's 2025 PVI (the member's current old-map seat; retirees)
An overridden member never takes the claimed-row c = 1 default. Every violation (bad bioguide, duplicate, not in the roster, name != roster, not a House
member, unknown status/basis, status/basis mismatch, district not in the Cook CSV or in another state, non-https source_url, non-ISO source_date, empty ruling)
raises ValueError naming the CSV line. A header-only file is valid (zero overrides). The file is hashed into the artifact header and re-checked by the kernel
loader (MC.compet_check_inputs, R7). It is deliberately NOT listed in PROVENANCE.json / the Cook README (those are hash-pinned vendor-package records).

Usage: python fec/engine/build_competitiveness.py        (writes data/money/competitiveness_c.json)
"""
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import csv, datetime, difflib, hashlib, json, os, re, sys, unicodedata
import moneyball_core as MC
from paths import p

SCHEMA = MC.COMPET_SCHEMA
INGEST_DIR = 'data/money/cook_pvi_2026'
_INPUTS = dict(MC.COMPET_INPUTS)                     # the kernel owns the paths so builder and loader cannot drift
HOUSE_CSV = _INPUTS['cook_house_csv']
STATE_CSV = _INPUTS['cook_state_csv_supplied_by_paul']
BALLOT_CSV = _INPUTS['ballot_district_overrides_csv']   # R3/R4 (Paul ruling 2026-10-07, D-107)
BALLOT_HEADER = ['bioguide', 'name', 'ballot_district', 'status', 'pvi_basis', 'source_url', 'source_date', 'ruling']
BALLOT_STATUS_BASIS = {'RUNNING_HOUSE': 'ballot_2026', 'NOT_RUNNING_HOUSE': 'current_seat_2025'}
PROVENANCE = INGEST_DIR + '/PROVENANCE.json'
VOTEVIEW = 'votes/HS119_members.csv'
OUT_REL = 'data/money/competitiveness_c.json'
FORMULA = 'c = round(1 + AMPLITUDE*exp(-max(lean,0)/SCALE), 6); lean = pvi_signed_points*(+1 if oriented party R else -1)'
# Mid-decade redistricting states (Paul 2026-10-05): FLAGGED, never altered.
REDISTRICTED = frozenset('AL CA FL LA MO NC OH TN TX UT'.split())
PVI_SHIFT_FLAG = 5                                   # |pvi2026 - pvi2025| >= 5 points -> flag
CAUCUS = {'S000033': 'D', 'K000383': 'D'}            # Sanders, King: independents who caucus with Democrats, oriented D (Paul ruling 2026-10-07, D-107 R5, ratified; flagged)
# R2 (Paul ruling 2026-10-07, brain D-107): explicit orientation overrides keyed by bioguide; they win over the roster party.
ORIENTATION_OVERRIDES = {'K000401': ('R', 'Paul ruling 2026-10-07 (D-107 R2)')}   # Kevin Kiley: oriented by his prior party (Voteview R, then I; Cook labels him I)
# CM-3 (ruled 2026-10-11, Paul-delegated): provenance for the two delegate flags, written into the artifact header (no c value depends on it).
DELEGATE_RULINGS = {'territory_delegate_c1': 'ruled 2026-10-11 (Paul-delegated, CM-3): every territory delegate (PR, GU, VI, AS, MP) has c = 1; PR Resident Commissioner: no Cook PVI and a four-year term, so no 2026 race; GU/VI/AS/MP keep the same c = 1 path (no Cook PVI)',
                    'dc_delegate_state_row': "ruled 2026-10-11 (Paul-delegated, CM-3): DC keeps Paul's supplied D+44 state row"}
FUZZY_MIN = 0.85                                     # surname similarity for Cook's misspellings (Petterson, Haridopolis)


def norm(s):
    """Lower-case ASCII letters and single spaces: accents, punctuation and hyphens dropped."""
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return ' '.join(re.sub(r'[^a-z\s]', ' ', s.replace("'", '').replace('-', ' ')).split())


def party_letter(party_code):
    return {'100': 'D', '200': 'R'}.get(str(party_code), 'I')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def parse_incumbent(text):
    """Cook's incumbent cell -> (name_text, kind): 'OPEN (Moore)' -> ('Moore', 'open'); 'Jared Golden' -> (.., 'seated')."""
    m = re.match(r'^\s*(OPEN|VACANT)\s*\((.*)\)\s*$', text or '')
    if m:
        return m.group(2).strip(), m.group(1).lower()
    return (text or '').strip(), 'seated'


def surname(name_norm):
    return name_norm.split()[-1] if name_norm else ''


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def place_house_members(members, cook_rows):
    """Cook-incumbent placement: member name -> (cook row, 'cook_incumbent_exact'|'cook_incumbent_fuzzy').
    `members`: list of dicts {name, state, party}. A Cook row is claimed by at most one member. Pass 1 takes full-name
    equality; pass 2 takes full-name rows on first initial + surname similarity, and surname-only OPEN/VACANT rows on
    surname similarity + party agreement, each only when it leaves exactly one best candidate."""
    by_state = {}
    for r in cook_rows:
        by_state.setdefault(r['state'], []).append(r)
    claimed, placed = set(), {}
    for m in members:                                                    # pass 1: exact normalised full name
        nn = norm(m['name'])
        hits = [r for r in by_state.get(m['state'], []) if r['district_id'] not in claimed
                and parse_incumbent(r['incumbent_source_text'])[1] == 'seated'
                and norm(parse_incumbent(r['incumbent_source_text'])[0]) == nn]
        if len(hits) == 1:
            placed[m['name']] = (hits[0], 'cook_incumbent_exact'); claimed.add(hits[0]['district_id'])
    for m in members:                                                    # pass 2: fuzzy
        if m['name'] in placed: continue
        nn = norm(m['name']); first, last = nn.split()[0], surname(nn)
        scored = []
        for r in by_state.get(m['state'], []):
            if r['district_id'] in claimed: continue
            text, kind = parse_incumbent(r['incumbent_source_text']); tn = norm(text)
            if not tn: continue
            if kind == 'seated':
                if tn.split()[0][:1] != first[:1]: continue
                s = _sim(surname(tn), last)
            else:                                                        # surname only: party must agree
                if r['incumbent_party_source'] != m['party']: continue
                s = _sim(tn.split()[-1], last)
            if s >= FUZZY_MIN: scored.append((s, r['district_id'], r))
        scored.sort(key=lambda t: (-t[0], t[1]))
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            placed[m['name']] = (scored[0][2], 'cook_incumbent_fuzzy'); claimed.add(scored[0][1])
    return placed


def load_house(path):
    with open(path, encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r['pvi_2026_signed_points'] = int(r['pvi_2026_signed_points'])
        r['pvi_2025_signed_points'] = int(r['pvi_2025_signed_points'])
        r['district_number'] = int(r['district_number'])
    return rows


def load_states(path):
    with open(path, encoding='utf-8', newline='') as fh:
        return {r['state_abbr']: {'label': r['pvi_label'], 'pts': int(r['pvi_signed_points'])} for r in csv.DictReader(fh)}


def load_ballot_overrides(path, roster, cook_rows):
    """R3/R4: bioguide -> override row. Fails closed on any malformed row (format in the module docstring and MATHEMATICAL_SPECIFICATION.md COMPET_MULT)."""
    with open(path, encoding='utf-8', newline='') as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != BALLOT_HEADER:
            raise ValueError('ballot overrides: header %r != %r' % (rd.fieldnames, BALLOT_HEADER))
        rows = list(rd)
    by_bio = {r['bioguide']: r for r in roster if r['bioguide']}
    cook_by_id = {r['district_id']: r for r in cook_rows}
    out = {}
    for line, raw in enumerate(rows, start=2):
        if None in raw or any(v is None for v in raw.values()):
            raise ValueError('%s line %d: wrong number of columns' % (BALLOT_CSV, line))
        r = {k: (v or '').strip() for k, v in raw.items()}
        b = r['bioguide']; where = '%s line %d' % (BALLOT_CSV, line)
        if not re.fullmatch(r'[A-Z]\d{6}', b): raise ValueError('%s: bad bioguide %r' % (where, b))
        if b in out: raise ValueError('%s: duplicate bioguide %s' % (where, b))
        m = by_bio.get(b)
        if m is None: raise ValueError('%s: bioguide %s not in the roster' % (where, b))
        if norm(r['name']) != norm(m['name']): raise ValueError('%s: name %r != roster %r' % (where, r['name'], m['name']))
        if m['chamber'] != 'House' or m['vv_chamber'] != 'House': raise ValueError('%s: %s is not a House member' % (where, m['name']))
        if r['status'] not in BALLOT_STATUS_BASIS: raise ValueError('%s: status %r not in %s' % (where, r['status'], sorted(BALLOT_STATUS_BASIS)))
        if r['pvi_basis'] != BALLOT_STATUS_BASIS[r['status']]:
            raise ValueError('%s: status %s requires pvi_basis %s (got %r)' % (where, r['status'], BALLOT_STATUS_BASIS[r['status']], r['pvi_basis']))
        d = r['ballot_district']
        if d not in cook_by_id: raise ValueError('%s: ballot_district %r is not a Cook district_id' % (where, d))
        if cook_by_id[d]['state'] != m['state']: raise ValueError('%s: ballot_district %s is outside %s' % (where, d, m['state']))
        if not r['source_url'].startswith('https://'): raise ValueError('%s: source_url must be https' % where)
        try: datetime.date.fromisoformat(r['source_date'])
        except ValueError: raise ValueError('%s: source_date %r is not YYYY-MM-DD' % (where, r['source_date']))
        if not r['ruling']: raise ValueError('%s: ruling is empty' % where)
        out[b] = r
    return out


def voteview_rows(path):
    """bioguide -> the HIGHEST-ICPSR HS119 row (Voteview's 9-prefixed post-switch identity wins, as MC.party())."""
    best = {}
    with open(path, encoding='utf-8', newline='') as fh:
        for row in csv.DictReader(fh):
            b = row.get('bioguide_id')
            if not b: continue
            if b not in best or int(row['icpsr']) > int(best[b]['icpsr']):
                best[b] = row
    return best


def roster_records(congestion, vv):
    """One dict per congestion member: name, bioguide, chamber (the roster's), state, district_code, party."""
    out = []
    for m in congestion:
        row = vv.get(m.get('bioguide') or '')
        out.append({'name': m['member'], 'bioguide': m.get('bioguide'), 'chamber': m['chamber'],
                    'state': row['state_abbrev'] if row else None,
                    'district_code': int(row['district_code']) if row else None,
                    'party': party_letter(row['party_code']) if row else None,
                    'vv_chamber': row['chamber'] if row else None})
    return out


def oriented_party(rec):
    """(party, flags): an explicit override (R2), else the roster party, else the caucus map for the two independents; None when unoriented."""
    flags = []
    ov = ORIENTATION_OVERRIDES.get(rec['bioguide'])
    if ov:
        flags.append('orientation_override'); return ov[0], flags
    if rec['party'] in ('D', 'R'):
        return rec['party'], flags
    if rec['bioguide'] in CAUCUS:
        flags.append('independent_caucus_oriented'); return CAUCUS[rec['bioguide']], flags
    flags.append('unoriented_independent'); return None, flags


def build_records(roster, cook_rows, states, overrides=None):
    """Pure join + formula over already-loaded inputs. Returns {member name: record} in roster order."""
    overrides = overrides or {}
    house = [r for r in roster if r['chamber'] == 'House' and r['state'] and r['state'] != 'DC' and r['state'] != 'PR'
             and r['vv_chamber'] == 'House']
    placed = place_house_members(house, cook_rows)
    cook_by_dist = {(r['state'], r['district_number']): r for r in cook_rows}
    cook_by_id = {r['district_id']: r for r in cook_rows}
    at_large = {r['state'] for r in cook_rows if r['district_number'] == 0}
    placed_to = {r['district_id']: n for n, (r, _m) in placed.items()}
    out = {}
    for rec in roster:
        name, flags = rec['name'], []
        pvi26 = pvi25 = None; method = 'unmatched'; key = None; source = 'none'; status = 'UNMATCHED'; party_o = None
        if rec['state'] is None:
            flags.append('not_in_voteview_hs119')
        elif rec['state'] == 'PR' or (rec['chamber'] == 'House' and rec['state'] in ('GU', 'VI', 'AS', 'MP')):
            method = 'territory_delegate'; status = 'NO_PVI_TERRITORY'; flags.append('territory_delegate_c1')
        elif rec['chamber'] == 'Senate' or rec['state'] == 'DC':
            st = states.get(rec['state'])
            if st:
                pvi26 = st['pts']; key = rec['state']; method = 'state_table'; source = 'cook_state_pvi_supplied_by_paul'
                status = 'MATCHED'
                if rec['state'] == 'DC': flags.append('dc_delegate_state_row')
            else:
                flags.append('state_not_in_table')
        else:
            ov = overrides.get(rec['bioguide'])
            if ov:
                # R3/R4 (Paul ruling 2026-10-07, D-107): the district named in the override CSV beats Cook's placement and the district number. No claimed-row
                # c = 1 rule here: two incumbents on one ballot (member vs member) both take that PVI.
                row = cook_by_id[ov['ballot_district']]; method = 'ballot_district_override'
                flags.append('ballot_district_override')
                if name in placed: cur = placed[name][0]['district_id']
                else:
                    cur_row = cook_by_dist.get((rec['state'], 0)) if rec['state'] in at_large else cook_by_dist.get((rec['state'], rec['district_code']))
                    cur = cur_row['district_id'] if cur_row else None
                if row['district_id'] != cur: flags.append('ballot_district_differs_from_cook_row')
                if ov['status'] == 'RUNNING_HOUSE' and placed_to.get(row['district_id']) not in (None, name): flags.append('ballot_district_shared_with_other_incumbent')
                if ov['status'] == 'NOT_RUNNING_HOUSE': flags.append('ballot_status_not_running_house')
                if ov['pvi_basis'] == 'current_seat_2025': flags.append('pvi_basis_current_seat_2025')
            elif name in placed:
                row, method = placed[name]
            else:
                if rec['state'] in at_large: row = cook_by_dist.get((rec['state'], 0))
                else: row = cook_by_dist.get((rec['state'], rec['district_code']))
                method = 'district_number' if row else 'unmatched'
                if row and rec['state'] in at_large and rec['district_code'] != 1:
                    flags.append('at_large_district_code_not_1')
                if row and placed_to.get(row['district_id']) not in (None, name):
                    flags.append('district_number_row_claimed_by_other_incumbent')
            if row:
                pvi26, pvi25 = row['pvi_2026_signed_points'], row['pvi_2025_signed_points']
                key = row['district_id']; source = 'cook_district_pvi_2026'; status = 'MATCHED'
                if parse_incumbent(row['incumbent_source_text'])[1] == 'open': flags.append('open_seat')
                if parse_incumbent(row['incumbent_source_text'])[1] == 'vacant': flags.append('vacant_seat_incumbent_named')
                if method == 'cook_incumbent_fuzzy': flags.append('cook_name_fuzzy_match')
                if method in ('cook_incumbent_exact', 'cook_incumbent_fuzzy') and rec['district_code'] is not None and row['district_number'] != rec['district_code'] \
                        and not (rec['state'] in at_large and rec['district_code'] == 1):
                    flags.append('cook_placement_differs_from_voteview_district')
                if rec['state'] in REDISTRICTED: flags.append('mid_decade_redistricted_state')
                if abs(pvi26 - pvi25) >= PVI_SHIFT_FLAG: flags.append('pvi_shift_ge_5')
                # House boundaries VERIFIED by Paul 2026-10-06: no boundary flag (the Cook CSV's own UNVERIFIED-HERE column is the vendor's, superseded).
            else:
                flags.append('no_cook_row')
        lean = c = None
        if status == 'MATCHED':
            party_o, f2 = oriented_party(rec); flags += f2
            if 'district_number_row_claimed_by_other_incumbent' in flags:
                # Agent default (brain D-105, never ruled): a member who fell back to a district-number row Cook assigns to ANOTHER incumbent has no PVI of their own in
                # this table: c = 1.0 exactly, flag kept, status says why; pvi fields stay recorded. Calvert and Doggett are NO LONGER in this case: Paul ruled 2026-10-07
                # (D-107 R3/R4) and each has a row in ballot_district_overrides.csv. The territory delegates' c = 1 (PR; GU/VI/AS/MP) and DC's D+44 state row are ruled 2026-10-11 (Paul-delegated, CM-3).
                status = 'MATCHED_DISTRICT_ROW_CLAIMED_C1'
            elif party_o is not None:
                pvi_lean = pvi25 if (overrides.get(rec['bioguide']) or {}).get('pvi_basis') == 'current_seat_2025' else pvi26
                lean = pvi_lean * (1 if party_o == 'R' else -1)
        c = MC.compet_c(lean)
        if lean is None and status == 'MATCHED': status = 'MATCHED_UNORIENTED'
        out[name] = {'bioguide': rec['bioguide'], 'chamber': rec['chamber'], 'state': rec['state'],
                     'join_key': key, 'join_method': method, 'roster_party': rec['party'], 'oriented_party': party_o,
                     'pvi2026': pvi26, 'pvi2025': pvi25, 'lean': lean, 'c': c, 'source': source,
                     'join_status': status, 'flags': flags}
        ov = overrides.get(rec['bioguide'])
        if ov and method == 'ballot_district_override':
            out[name]['ballot_override'] = {k: ov[k] for k in ('status', 'ballot_district', 'pvi_basis', 'source_url', 'source_date', 'ruling')}
    return out


def summarize(members):
    import collections
    cs = [r['c'] for r in members.values()]
    return {'members': len(cs), 'c_ge_1_5': sum(1 for x in cs if x >= 1.5), 'c_eq_2': sum(1 for x in cs if x == 2.0),
            'c_eq_1': sum(1 for x in cs if x == 1.0), 'c_mean': round(sum(cs) / len(cs), 6) if cs else None,
            'join_method': dict(sorted(collections.Counter(r['join_method'] for r in members.values()).items())),
            'join_status': dict(sorted(collections.Counter(r['join_status'] for r in members.values()).items())),
            'flags': dict(sorted(collections.Counter(f for r in members.values() for f in r['flags']).items()))}


def check_provenance(prov):
    """The ingested files must still be the bytes PROVENANCE.json recorded (fail closed on drift)."""
    for rel, expect in sorted((prov.get('sha256') or {}).items()):
        got = sha256_file(p(INGEST_DIR, rel))
        if got != expect:
            raise ValueError('competitiveness: %s sha256 %s != provenance %s' % (rel, got, expect))


def build_artifact():
    congestion = json.load(open(p('data', 'engine', 'congestion.json'), encoding='utf-8'))
    prov = json.load(open(p(PROVENANCE), encoding='utf-8'))
    check_provenance(prov)
    vv = voteview_rows(p(VOTEVIEW))
    roster = roster_records(congestion, vv)
    house_rows = load_house(p(HOUSE_CSV))
    overrides = load_ballot_overrides(p(BALLOT_CSV), roster, house_rows)
    members = build_records(roster, house_rows, load_states(p(STATE_CSV)), overrides)
    header = {'schema': SCHEMA, 'formula': FORMULA, 'amplitude': MC.COMPET_AMPLITUDE, 'scale': MC.COMPET_SCALE,
              'rounding_dp': 6, 'roster_sha256': MC.compet_roster_hash(congestion),
              'constants_status': "Paul's judgment, 2026-10-05; uncalibrated; reciprocity (money buys more where the seat is competitive) NOT validated",
              'input_sha256': {'cook_house_csv': sha256_file(p(HOUSE_CSV)), 'cook_state_csv_supplied_by_paul': sha256_file(p(STATE_CSV)),
                               'ballot_district_overrides_csv': sha256_file(p(BALLOT_CSV)),
                               'provenance_json': sha256_file(p(PROVENANCE)), 'voteview_hs119_members': sha256_file(p(VOTEVIEW)),
                               'congestion_json': sha256_file(p('data', 'engine', 'congestion.json'))},
              'input_paths': dict(MC.COMPET_INPUTS),
              'orientation_overrides': {b: {'party': pt, 'provenance': pv} for b, (pt, pv) in sorted(ORIENTATION_OVERRIDES.items())},
              'caucus_orientation': {'members': dict(sorted(CAUCUS.items())), 'provenance': 'Paul ruling 2026-10-07 (D-107 R5; ratified)'},
              'ballot_overrides': {'csv': BALLOT_CSV, 'running_house': sorted(b for b, o in overrides.items() if o['status'] == 'RUNNING_HOUSE'),
                                   'not_running_house': sorted(b for b, o in overrides.items() if o['status'] == 'NOT_RUNNING_HOUSE')},
              'delegate_rulings': dict(DELEGATE_RULINGS),
              'boundary_currency_status': 'VERIFIED (every House row; Paul 2026-10-06; supersedes the Cook package column UNVERIFIED-HERE)',
              'summary': summarize(members)}
    return {'header': header, 'members': members}


def main():
    doc = build_artifact()
    MC.compet_validate(doc, MC.congestion())      # the kernel's own gate: never write what MC.cmult would refuse
    MC.compet_check_inputs(doc)                   # R7: the hashes just recorded still match the files on disk
    MC.dump_atomic(doc, p(OUT_REL), indent=1, sort_keys=False)
    s = doc['header']['summary']
    print('competitiveness_c: %d members | c>=1.5 %d | c==2.0 %d | c==1.0 %d | mean %.4f | joins %s'
          % (s['members'], s['c_ge_1_5'], s['c_eq_2'], s['c_eq_1'], s['c_mean'], s['join_method']))
    print('wrote %s' % OUT_REL)


def guarded_main(build=None, env=None):
    """Chain-step entry. The artifact is consumed unless COMPET_MULT='0' (default ON since Paul 2026-10-06), so a builder failure is fatal
    by default; only with the dial explicitly off does it print a loud line and exit 0."""
    env = os.environ if env is None else env
    on = (env.get('COMPET_MULT', '1').strip() or '1') != '0'
    try:
        (build or main)()
        return 0
    except Exception as exc:
        if on:
            raise
        print('COMPETITIVENESS BUILD FAILED (dial off, non-fatal): %s: %s' % (type(exc).__name__, exc))
        return 0


if __name__ == '__main__':
    sys.exit(guarded_main())
