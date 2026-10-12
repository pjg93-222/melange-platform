# -*- coding: utf-8 -*-
"""Refined hearing-antagonism builder (2026-07-10, user refinement). Reads the CACHED turn-sequences
(hearings_seq.db) and computes, per member:
  mm_intr  = interrupts a COLLEAGUE (member cuts off another member's dash-terminated turn)
  mm_xparty= ...the colleague is the other party (cleanest antagonism)
  wit_hostile = member turn ADJACENT to a witness that carries HOSTILE markers (aggressive language /
                blunt correction / hostile interrogation) -- NOT routine procedural time-management
                ('reclaiming my time' is EXCLUDED by design; it inflated senior appropriators in v1)
Confound controls (DW-NOMINATE ideology+extremity, party, minority-fraction, chair-role) via residual.
Emits diagnostics: confound checks, split-half reliability, orthogonality to dominance/visibility/
cooperation, face validity, and an AUDIT DUMP of flagged hostile turns so the lexicon can be judged.
Iterate the lexicon here and re-run in seconds -- no re-download (cache is the point)."""
import sys, os, sqlite3, json, collections, re, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from members.dw_control import load_dw

from paths import ROOT
B = ROOT
# The sequence corpus this builder reads. Named so emit_antagonism can bind its artifact to the
# EXACT file by sha256 (2026-09-01): the display feed refuses an antagonism artifact that is not
# provably derived from the corpus whose receipt certifies the attribution contract.
SEQDB = B + r'\hearings_seq.db'
# Haiku substance labels (content-read frames) override the frame_of lexicon when present.
_LABP = os.environ.get('HEARING_SUBSTANCE_LABELS',os.path.join(B,'outputs','research','hearing_substance','substance_labels.json'))
HLAB = json.load(open(_LABP, encoding='utf-8')) if os.path.exists(_LABP) else {}
def exch_id(pkg, m, text):
    return hashlib.md5(('%s|%s|%s' % (pkg, m, text[:80])).encode('utf-8', 'replace')).hexdigest()[:16]

# ---- HOSTILITY LEXICON (pre-specified; member-vs-witness hostility only) ----
INTERROGATE = re.compile(r"\b(yes or no|answer the question|just answer|did you or did you not|"
                         r"answer my question|will you answer|that'?s not what i asked|answer yes or no|"
                         r"i'?m asking you|are you (aware|denying)|isn'?t it true)\b", re.I)
CORRECT = re.compile(r"\b(that'?s (false|not true|incorrect|a lie|not accurate|wrong)|that is false|"
                     r"you'?re (wrong|mistaken|incorrect|lying)|that'?s simply (false|not)|"
                     r"you know that'?s (false|not true))\b", re.I)
AGGRESSIVE = re.compile(r"\b(how dare you|shame on you|you should be ashamed|disgraceful|outrageous|"
                        r"you lied|you failed|reprehensible|you have no business|you don'?t get to|"
                        r"appalling|absolutely unacceptable)\b", re.I)
def is_hostile(t):
    return bool(INTERROGATE.search(t) or CORRECT.search(t) or AGGRESSIVE.search(t))

# SUBSTANCE frame (interim lexicon proxy; the faithful version is a Haiku pass over cached exchanges --
# see hearing_substance_llm.py). The substance of the member's words shapes BOTH the partisan read and
# the antagonism curve: substantive-accountability & partisan-ideological & populist aggression are
# role/context-driven (down-weighted); PERSONAL hostility is the dispositional residual (weighted full).
SUBSTANTIVE = re.compile(r"\b(misled|lied to (congress|us|this committee)|failed to|fraud|wast(e|ed|ing)|"
                         r"abuse|safety|harm(ed|ful)?|illegal|unlawful|violat|cover(ed)? up|coverup|negligen|"
                         r"mismanaged|deceiv|breach|wrongdoing|malfeasance|taxpayer dollars|billion|accountab|"
                         r"under oath|perjur|falsif|misrepresent)\b", re.I)
PARTISAN_FRAME = re.compile(r"\b(radical|socialist|marxist|woke|left-wing|right-wing|extremist|your party|"
                            r"the (left|right)|partisan|ideolog|agenda|weaponiz|witch hunt|hoax|sham)\b", re.I)
PERSONAL = re.compile(r"\b(disgrace|how dare you|shame on you|you should be ashamed|reprehensible|"
                      r"you have no business|despicable|you people|pathetic|coward|liar)\b", re.I)
def frame_of(t):
    """Interim frame classification of a hostile turn (Haiku pass overrides). Priority: personal >
    substantive > partisan > (bare interrogation = procedural-populist)."""
    if PERSONAL.search(t): return 'personal'        # dispositional antagonism
    if SUBSTANTIVE.search(t): return 'substantive'  # accountability (grounded) -> not disposition
    if PARTISAN_FRAME.search(t): return 'partisan'  # ideological attack -> partisan axis
    return 'procedural'                              # bare interrogation/correction w/o substance or ideology

# GRANDSTAND / procedural-dominance markers (axis 1, KEPT per user: senior members command the floor).
# 'reclaiming my time' lives HERE (procedural, not antagonism) -- the phrase that inflated Collins is
# the RIGHT signal for a dominance/status axis, wrong for the disagreeableness axis.
PROC = re.compile(r"reclaim\w+ my time|regular order|point of order|regain my time|"
                  r"i control the time|reclaiming the balance|will the gentlem[ae]n yield", re.I)

# Administration party by date (for the hostile-witness POLITICAL control: opposing-admin grilling =
# constituent-grievance performance, not disposition).
ADMIN = [('2017-01-20', '2021-01-20', 'R'), ('2021-01-20', '2025-01-20', 'D')]
def admin_party(date):
    for a, b, p in ADMIN:
        if a <= date < b: return p
    return '?'

def load_seniority():
    """member -> years in office (from voteview first-congress). Senior members grandstand more."""
    import csv
    first = {}
    for r in csv.DictReader(open(B + r'\votes\HSall_members.csv', encoding='utf-8')):
        b = r.get('bioguide_id')
        try: cong = int(r['congress'])
        except (ValueError, TypeError): continue
        if b and (b not in first or cong < first[b]): first[b] = cong
    Cj = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
    out = {}
    for m in Cj:
        fc = first.get(Cj[m].get('bioguide'))
        if fc: out[m] = 2026 - (1789 + 2 * (fc - 1))   # years since first election
    return out

def load_party():
    import csv
    P = {}; Cj = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
    bio = {m: Cj[m].get('bioguide') for m in Cj}
    for r in csv.DictReader(open(B + r'\fec\legislators.csv', encoding='utf-8')):
        if r.get('bioguide_id'): P[r['bioguide_id']] = (r.get('party') or '')[:1].upper()
    return {m: P.get(bio.get(m), '?') for m in Cj}

def build():
    DW = load_dw(); PTY = load_party()
    sc = sqlite3.connect(SEQDB, timeout=60); sc.execute('PRAGMA query_only=1')
    mc = sqlite3.connect(B + r'\social_media.db', timeout=60); mc.execute('PRAGMA query_only=1')
    chair_of = {p: cm for p, cm in mc.execute("select package, chair_member from hearing_meta")}
    chair_party = {p: cp for p, cp in mc.execute("select package, chair_party from hearing_meta")}

    agg = collections.defaultdict(lambda: collections.Counter())
    byhear = collections.defaultdict(list)          # member -> per-hearing (turns, mm, wit_hostile, grand)
    minority = collections.defaultdict(lambda: [0, 0])
    opp = collections.defaultdict(lambda: [0, 0])   # member -> [opp-admin hearings, total] (political control)
    audit = collections.defaultdict(list)
    nh = 0
    for pkg, date, seqj in sc.execute("select package, date, seq from hearing_seq"):
        seq = json.loads(seqj); nh += 1
        cp = chair_party.get(pkg, '?'); ap = admin_party(date)
        per = collections.defaultdict(lambda: collections.Counter())
        for i, turn in enumerate(seq):
            kind = turn[0]
            if kind in ('W', 'C', 'O'): continue           # only member turns are scored
            m = kind; pty = turn[1]; text = turn[3] if len(turn) > 3 else ''
            if chair_of.get(pkg) == m: continue            # skip hearings the member PRESIDED
            per[m]['turns'] += 1
            prev = seq[i - 1] if i > 0 else None
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            # --- GRANDSTAND axis (procedural dominance; interrupting ANYONE + floor-command phrases) ---
            if prev and prev[2] == 1 and prev[0] != m:
                per[m]['all_intr'] += 1
            if PROC.search(text): per[m]['proc'] += 1
            # --- ANTAGONISM axis: member-vs-member interrupt (colleague cut off) ---
            if prev and prev[0] not in ('W', 'C', 'O') and prev[2] == 1 and prev[0] != m:
                per[m]['mm_intr'] += 1
                if prev[1] in ('D', 'R') and pty in ('D', 'R') and prev[1] != pty:
                    per[m]['mm_xparty'] += 1
            # --- ANTAGONISM axis: member-vs-witness HOSTILE (adjacent to witness + hostile language) ---
            wtype = (prev[1] if (prev and prev[0] == 'W') else nxt[1] if (nxt and nxt[0] == 'W') else None)
            if wtype is not None and is_hostile(text):
                per[m]['wit_hostile'] += 1
                grp = 'off' if wtype in ('G', 'S', 'M', 'J') else 'biz' if wtype == 'B' else 'exp' if wtype == 'E' else 'priv'
                per[m]['wh_' + grp] += 1                      # by witness type (control for business/officials)
                fr = frame_of(text)
                per[m]['fr_' + fr] += 1                       # lexicon frame (interim)
                # HAIKU content-read label (overrides lexicon when present): dispositional = genuinely
                # hostile AND personal/aggressive-interrogation AND not partisan-framed
                hl = HLAB.get(exch_id(pkg, m, text))
                if hl:
                    per[m]['h_labeled'] += 1
                    per[m]['h_' + hl.get('frame', 'na')] += 1
                    if hl.get('hostile') and hl.get('frame') in ('personal_attack', 'aggressive_interrogation') and not hl.get('partisan'):
                        per[m]['h_disp'] += 1
                if ap != '?' and pty in ('D', 'R'):
                    per[m]['wh_opp' if pty != ap else 'wh_aligned'] += 1
                if len(audit[m]) < 3: audit[m].append('[%s/%s] %s' % (grp, fr, text[:150]))
        for m, c in per.items():
            for k, v in c.items(): agg[m][k] += v
            agg[m]['hearings'] += 1
            byhear[m].append((c['turns'], c['mm_intr'] + c['mm_xparty'] + c['wit_hostile'], c['all_intr'] + c['proc']))
            if cp in ('D', 'R') and PTY.get(m) in ('D', 'R'):
                minority[m][1] += 1; minority[m][0] += (cp != PTY[m])
            if ap in ('D', 'R') and PTY.get(m) in ('D', 'R'):
                opp[m][1] += 1; opp[m][0] += (ap != PTY[m])
    sc.close(); mc.close()
    return DW, PTY, agg, byhear, minority, opp, audit, nh

def z(v):
    v = np.array(v, float); s = v.std(); return (v - v.mean()) / s if s > 0 else v * 0
def corr(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float); m = ~(np.isnan(a) | np.isnan(b))
    return float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 5 else float('nan')
def resid(y, X):
    X = np.column_stack([np.ones(len(y))] + X); beta, *_ = np.linalg.lstsq(X, y, rcond=None); return y - X @ beta

def main():
    DW, PTY, agg, byhear, minority, opp, audit, nh = build()
    MIN = 20
    mem = [m for m in agg if agg[m]['turns'] >= MIN and m in DW]
    SEN = load_seniority()
    BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
    print('=== TWO-AXIS hearing build | %d hearings cached | %d members (>=%d turns) ===' % (nh, len(mem), MIN))
    tr = np.array([agg[m]['turns'] for m in mem], float)
    def rate(k): return np.array([agg[m][k] for m in mem]) / tr
    ext = np.array([DW[m]['extremity'] for m in mem]); d1 = np.array([DW[m]['dim1'] for m in mem])
    pty = np.array([1.0 if PTY.get(m) == 'R' else 0.0 for m in mem])
    minf = np.array([minority[m][0] / minority[m][1] if minority[m][1] else 0.5 for m in mem])
    oppf = np.array([opp[m][0] / opp[m][1] if opp[m][1] else 0.5 for m in mem])
    sen = np.array([SEN.get(m, np.nan) for m in mem]); sen = np.where(np.isnan(sen), np.nanmean(sen), sen)
    def bdv(k): return np.array([BD.get(m, {}).get(k, np.nan) if isinstance(BD.get(m), dict) else np.nan for m in mem])

    # ---------- AXIS 1: GRANDSTAND (procedural dominance; KEPT, seniority-correlated by design) ----------
    grand = z(rate('all_intr')) + z(rate('proc'))
    print('\n### AXIS 1 -- GRANDSTAND / procedural dominance (the original signal, kept) ###')
    print('  vs seniority(yrs): %+.3f (EXPECT +; senior members command the floor)' % corr(grand, sen))
    print('  vs dominance_z: %+.3f | vs visibility_z: %+.3f' % (corr(grand, bdv('dominance_z')), corr(grand, bdv('visibility_z'))))
    go = sorted(range(len(mem)), key=lambda i: -grand[i])
    print('  top grandstanders: %s' % ', '.join('%s(%s,%dy)' % (mem[i], PTY.get(mem[i]), sen[i]) for i in go[:8]))

    # ---------- AXIS 2: ANTAGONISM (disagreeableness; substance-shaped + witness-type + politically controlled) ----------
    tot_wh = sum(agg[m]['wit_hostile'] for m in mem)
    fr = {k: sum(agg[m]['fr_' + k] for m in mem) for k in ('personal', 'substantive', 'partisan', 'procedural')}
    wg = {k: sum(agg[m]['wh_' + k] for m in mem) for k in ('off', 'biz', 'exp', 'priv')}
    print('\n### AXIS 2 -- ANTAGONISM (substance-shaped) ###')
    print('  hostile-witness by TARGET: officials %d | business %d | expert %d | private %d' % (wg['off'], wg['biz'], wg['exp'], wg['priv']))
    print('  hostile-witness by SUBSTANCE frame: %s' % {k: '%d(%.0f%%)' % (v, 100 * v / max(tot_wh, 1)) for k, v in fr.items()})
    # substance shapes the curve: PERSONAL = dispositional; substantive/partisan down-weighted; populist(biz) discounted
    personal = rate('fr_personal')
    antag_raw = z(rate('mm_intr')) + z(rate('mm_xparty')) + z(personal)   # dispositional hostile signal only
    antag = resid(antag_raw, [sen, ext, d1, pty, minf, oppf])
    print('  antag(personal-hostility) raw confounds: senior %+.3f | extremity %+.3f | opp-admin %+.3f'
          % (corr(antag_raw, sen), corr(antag_raw, ext), corr(antag_raw, oppf)))
    print('  --- your point: SUBSTANCE bends the curve ---')
    for k, v in fr.items():
        vec = rate('fr_' + k)
        print('    %-11s hostility vs opp-admin %+.3f | vs extremity %+.3f' % (k, corr(vec, oppf), corr(vec, ext)))
    print('  --- witness-type: is officials-hostility more political than business-hostility? ---')
    print('    officials-hostility vs opp-admin %+.3f (partisan) | business-hostility vs opp-admin %+.3f (populist, should be ~0)'
          % (corr(rate('wh_off'), oppf), corr(rate('wh_biz'), oppf)))
    print('  orthogonality: dominance %+.3f | cooperation %+.3f | reactivity %+.3f'
          % (corr(antag, bdv('dominance_z')), corr(antag, bdv('cooperation_z')), corr(antag, bdv('reactivity_z'))))
    ao = sorted(range(len(mem)), key=lambda i: -antag[i])
    print('  top antagonists (lexicon personal-hostility, controlled): %s' % ', '.join('%s(%s)' % (mem[i], PTY.get(mem[i])) for i in ao[:8]))

    # ---------- HAIKU content-read antagonism (activates once substance_labels.json exists) ----------
    tot_lab = sum(agg[m]['h_labeled'] for m in mem)
    if tot_lab > 100:
        hframes = {k: sum(agg[m].get('h_' + k, 0) for m in mem) for k in
                   ('personal_attack', 'aggressive_interrogation', 'substantive_accountability', 'partisan_ideological', 'populist_grilling', 'neutral_questioning')}
        print('\n### AXIS 2b -- HAIKU content-read antagonism (%d exchanges labeled) ###' % tot_lab)
        print('  Haiku frames: %s' % {k: '%d(%.0f%%)' % (v, 100 * v / max(tot_lab, 1)) for k, v in hframes.items()})
        hd = rate('h_disp')                        # dispositional = hostile & personal/aggressive & not partisan
        antag_h = z(rate('mm_intr')) + z(rate('mm_xparty')) + z(hd)
        antag_hc = resid(antag_h, [sen, ext, d1, pty, minf, oppf])
        print('  Haiku-dispositional vs opp-admin %+.3f | extremity %+.3f (should be low = not political)' % (corr(hd, oppf), corr(hd, ext)))
        print('  orthogonality: dominance %+.3f | cooperation %+.3f (want negative) | reactivity %+.3f'
              % (corr(antag_hc, bdv('dominance_z')), corr(antag_hc, bdv('cooperation_z')), corr(antag_hc, bdv('reactivity_z'))))
        ah = sorted(range(len(mem)), key=lambda i: -antag_hc[i])
        print('  TOP ANTAGONISTS (Haiku content-read, controlled): %s'
              % ', '.join('%s(%s)' % (mem[i], PTY.get(mem[i])) for i in ah[:10]))
        print('  vs lexicon ranking corr: %+.3f' % corr(antag, antag_hc))
    else:
        print('\n  (Haiku labels: %d exchanges -- run hearing_substance_prep + classify workflow to activate AXIS 2b)' % tot_lab)

    print('\n### the two axes are distinct: corr(grandstand, antagonism_controlled) = %+.3f ###' % corr(grand, antag))
    print('\n--- reliability (split-half >=4 hearings) ---')
    memR = [m for m in mem if len(byhear[m]) >= 4]
    def half(which, idx):
        out = []
        for m in memR:
            h = byhear[m][which::2]; t = max(sum(x[0] for x in h), 1); out.append(sum(x[idx] for x in h) / t)
        return z(out)
    for nm, idx in [('antagonism', 1), ('grandstand', 2)]:
        r = corr(half(0, idx), half(1, idx)); print('  %-12s n=%d SB=%+.3f' % (nm, len(memR), 2 * r / (1 + r) if r > -1 else float('nan')))
    print('\n--- AUDIT: flagged HOSTILE turns (judge the lexicon) ---')
    for m in sorted(mem, key=lambda m: -agg[m]['wit_hostile'])[:6]:
        print('  %s (%d):' % (m, agg[m]['wit_hostile']))
        for s in audit.get(m, [])[:2]: print('     "%s"' % s)

if __name__ == '__main__':
    main()
