# -*- coding: utf-8 -*-
"""Gate-2 COR-1 fixtures for build_holistic_base.py (holistic v1; freeze addendum 2026-08-17).
Standalone runner in the repo ok() style; exercises the builder's PURE core (no I/O, no
data plane) so it runs identically in worktree or live tree. Includes the negative-control
mutants the framework's §11 battery requires — a fixture set that cannot fail proves nothing."""
import os, sys, math, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.normpath(os.path.join(HERE, '..', '..', 'network', 'build_holistic_base.py'))
spec = importlib.util.spec_from_file_location('hb', BUILDER)
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)   # import-safe: builder is main()-guarded

P = F = 0
def ok(name, cond, detail=''):
    global P, F
    if cond: P += 1; print('  OK   ' + name)
    else:    F += 1; print('  FAIL ' + name + ('  :: ' + str(detail) if detail else ''))

print('[holistic base: bill_factor (scraper parity :1923-1927)]')
ok('unpassed current-congress single-cosponsor bill = 1.0', hb.bill_factor(False, 0, 1) == 1.0)
ok('SUCCESS multiplies by 3.0', hb.bill_factor(True, 0, 1) == 3.0)
ok('decay 0.8^2 for a 117th bill (age 2)', abs(hb.bill_factor(False, 2, 1) - 0.64) < 1e-12)
ok('sizenorm is a MULTIPLIED 1/sqrt(C) discount (C=4 -> x0.5)', abs(hb.bill_factor(False, 0, 4) - 0.5) < 1e-12)
ok('combined: passed, age 1, C=25 -> 3*0.8/5 = 0.48', abs(hb.bill_factor(True, 1, 25) - 0.48) < 1e-12)
ok('age<0 / None -> full weight (:112 parity)', hb.bill_factor(False, -3, 1) == 1.0 and hb.bill_factor(False, None, 1) == 1.0)
ok('C=0 contributes nothing (defensive 0.0)', hb.bill_factor(False, 0, 0) == 0.0)
_inverted = 3.0 * 1.0 * math.sqrt(4)   # the sqrt-inversion mutant the guide warns about
ok('NC: inverted sizenorm (x sqrt C) FAILS the C=4 fixture', _inverted != hb.bill_factor(True, 0, 4))

print('[holistic base: PASSED_RE (:1786 parity, latest-action semantics)]')
ok('"Passed House" is success', hb.passed_own_chamber('On passage Passed House by voice vote'))
ok('"Agreed to in Senate" is success', hb.passed_own_chamber('Resolution Agreed to in Senate without amendment'))
ok('"Became Public Law" is success', hb.passed_own_chamber('Became Public Law No: 117-58.'))
ok('committee referral is NOT success', not hb.passed_own_chamber('Referred to the Committee on Financial Services'))
ok('None / empty latest action is NOT success', not hb.passed_own_chamber(None) and not hb.passed_own_chamber(''))

print('[holistic base: D6-b high-water SUCCESS (ruled 2026-10-11)]')
def mkxml(latest, actions):
    acts = ''.join('<item><actionCode>%s</actionCode><text>%s</text></item>' % a for a in actions)
    return ('<billStatus><bill><congress>118</congress><latestAction><text>%s</text></latestAction>'
            '<sponsors><item><bioguideId>A000001</bioguideId></item></sponsors>'
            '<actions>%s</actions></bill></billStatus>' % (latest, acts)).encode()
# 118hres5 shape: passed the House, latestAction is a later procedural motion.
HRES5 = mkxml('Motion to reconsider laid on the table Agreed to without objection.',
              [('H38310', 'Motion to reconsider laid on the table Agreed to without objection.'),
               ('8000', 'Passed/agreed to in House: On agreeing to the resolution Agreed to by the Yeas and Nays: 220 - 213.'),
               ('1000', 'Introduced in House')])
# Amendment-agreed-only bill: PASSED_RE would fire on "agreed to in Senate"; it never passed.
AMDT = mkxml('Amendment SA 1234 agreed to in Senate by Voice Vote.',
             [('93000', 'Amendment SA 1234 agreed to in Senate by Voice Vote.'),
              ('10000', 'Introduced in Senate')])
# Rule-passage false positive of the latest-action regex.
RULE = mkxml('Rule H. Res. 973 passed House.', [('H1L220', 'Rule H. Res. 973 passed House.'),
                                                ('1000', 'Introduced in House')])
DEEMED = mkxml('Received in the Senate.', [('8000', 'Pursuant to the provisions of H. Res. 5, the bill is considered passed.')])
_env0 = os.environ.pop('HOLISTIC_SUCCESS', None)
ok('default HOLISTIC_SUCCESS is highwater', hb.success_mode() == 'highwater')
ok('118hres5 shape is SUCCESS under the default parse', hb.parse_billstatus_xml(HRES5)['passed'] is True)
ok('NC: the old latest-action parse FAILS the 118hres5 fixture', hb.parse_billstatus_xml(HRES5, 'latest')['passed'] is False)
ok('amendment-agreed-only bill stays NOT success (high-water)', hb.parse_billstatus_xml(AMDT)['passed'] is False)
ok('NC: PASSED_RE over all actions would wrongly pass the amendment-only bill',
   any(hb.PASSED_RE.search(t) for t in ['Amendment SA 1234 agreed to in Senate by Voice Vote.']))
ok('rule-passage latestAction is NOT success under high-water', hb.parse_billstatus_xml(RULE)['passed'] is False)
ok('deemed passage (LoC code 8000, non-standard text) is SUCCESS', hb.parse_billstatus_xml(DEEMED)['passed'] is True)
os.environ['HOLISTIC_SUCCESS'] = 'latest'
ok('HOLISTIC_SUCCESS=latest restores the 08-17 parse', hb.parse_billstatus_xml(HRES5)['passed'] is False
   and hb.parse_billstatus_xml(RULE)['passed'] is True)
ok('latest semantics keep the original cache dir; highwater gets its own',
   hb.success_cache_dir('C') == 'C' and hb.success_cache_dir('C', 'highwater') == os.path.join('C', 'hw'))
os.environ['HOLISTIC_SUCCESS'] = 'High-Water'
try:
    hb.success_mode(); _got = 'ACCEPTED'
except ValueError:
    _got = 'raised'
ok('HOLISTIC_SUCCESS rejects unknown values (fail-closed)', _got == 'raised', _got)
os.environ.pop('HOLISTIC_SUCCESS', None)
if _env0 is not None: os.environ['HOLISTIC_SUCCESS'] = _env0

print('[holistic base: assemble — orientation, roles, roster, identity]')
ROSTER = {'A000001', 'B000002', 'C000003'}
def mkbill(sponsor='A000001', cos=(('B000002', True, False), ('C000003', False, False)),
           congress=119, passed=False):
    return {'congress': congress, 'passed': passed, 'sponsor': sponsor, 'cos': list(cos)}

edges, stats = hb.assemble([mkbill()], ROSTER, ow=1.0, rw=0.2)
f = 1.0 / math.sqrt(2)
ok('edges run sponsor->cosponsor only (A->B, A->C; no B->A)',
   set(edges) == {('A000001', 'B000002'), ('A000001', 'C000003')})
ok('original tie lands in WOriginal at full f', abs(edges[('A000001', 'B000002')]['WOriginal'] - f) < 1e-12)
ok('regular tie lands in WRegular at full f (role dial factored OUT)',
   abs(edges[('A000001', 'C000003')]['WRegular'] - f) < 1e-12)
ok('Weight identity: ow*WOriginal + rw*WRegular (D1 defaults)',
   abs(edges[('A000001', 'C000003')]['Weight'] - 0.2 * f) < 1e-12 and
   abs(edges[('A000001', 'B000002')]['Weight'] - 1.0 * f) < 1e-12)
_swapped = {(t, s) for (s, t) in edges}
ok('NC: swapped-orientation mutant disagrees with the edge set', _swapped != set(edges))

edges0, _ = hb.assemble([mkbill()], ROSTER, ow=1.0, rw=0.0)
ok('zero regular dial: row STILL EMITTED with Weight 0 and WRegular intact (COR-2 basis)',
   ('A000001', 'C000003') in edges0 and edges0[('A000001', 'C000003')]['Weight'] == 0.0
   and abs(edges0[('A000001', 'C000003')]['WRegular'] - f) < 1e-12)

edges5, _ = hb.assemble([mkbill()], ROSTER, ow=1.0, rw=0.5)
ok('parity mode rw=0.5 reprices from the SAME subtotals',
   abs(edges5[('A000001', 'C000003')]['Weight'] - 0.5 * f) < 1e-12)

# full-bill C: one cosponsor off roster -> C stays 3, tie dropped, factor uses sqrt(3)
b3 = mkbill(cos=(('B000002', True, False), ('C000003', False, False), ('Z999999', False, False)))
edges3, stats3 = hb.assemble([b3], ROSTER, 1.0, 0.2)
ok('C is FULL-BILL (D6-c): off-roster cosponsor still discounts via sqrt(3)',
   abs(edges3[('A000001', 'B000002')]['WOriginal'] - 1.0 / math.sqrt(3)) < 1e-12)
ok('off-roster cosponsor tie dropped and COUNTED',
   ('A000001', 'Z999999') not in edges3 and stats3['counters']['ties_cosponsor_off_roster'] == 1)

edgesD, statsD = hb.assemble([mkbill(sponsor='Z999999')], ROSTER, 1.0, 0.2)
ok('departed sponsor (D6-d accept-the-drop): bill contributes ZERO ties, counted',
   len(edgesD) == 0 and statsD['counters']['bills_sponsor_off_roster'] == 1)

edgesS, statsS = hb.assemble([mkbill(cos=(('A000001', True, False),))], ROSTER, 1.0, 0.2)
ok('self-loop dropped (:1931 parity)', len(edgesS) == 0 and statsS['counters']['ties_self_loop_dropped'] == 1)

print('[holistic base: PRO-1 adversarial-round fixes 2026-08-17]')
# Duplicate cosponsor items on one bill (real case: BILLSTATUS-118hr6116.xml lists G000578,
# L000596, S001214 each twice, as (original, withdrawn) then (regular, live)).
bdup = mkbill(cos=(('B000002', True, True), ('B000002', False, False), ('C000003', False, False)))
edup, sdup = hb.assemble([bdup], ROSTER, 1.0, 0.2)
ok('duplicate cosponsor items collapse to ONE tie of ONE role',
   edup[('A000001', 'B000002')]['NBills'] == 1 and
   edup[('A000001', 'B000002')]['WOriginal'] == 0.0 and
   edup[('A000001', 'B000002')]['WRegular'] > 0,
   edup[('A000001', 'B000002')])
ok('duplicate collapse is COUNTED, not silent', sdup['counters'].get('cosponsor_duplicate_items_collapsed') == 1)
ok('C uses DISTINCT cosponsors (2, not 3) so other ties are not over-discounted',
   abs(edup[('A000001', 'C000003')]['WRegular'] - 1.0 / math.sqrt(2)) < 1e-12,
   edup[('A000001', 'C000003')]['WRegular'])
_dupmutant = 1.0 / math.sqrt(3)   # what the pre-fix code produced
ok('NC: the pre-fix (un-deduped) value would FAIL this fixture',
   abs(edup[('A000001', 'C000003')]['WRegular'] - _dupmutant) > 1e-9)

# COR-5 share must divide by TIE-CAPABLE bills, not all bills.
bills_cor5 = [mkbill(sponsor='Z999999'),                       # tie-capable, dropped
              mkbill(),                                        # tie-capable, kept
              mkbill(sponsor='Z999999', cos=())]               # NOT tie-capable (no cosponsors)
_, s5 = hb.assemble(bills_cor5, ROSTER, 1.0, 0.2)
ok('COR-5 denominator excludes zero-cosponsor bills (1/2 = 50%, not 1/3)',
   abs(s5['sponsor_dropped_share_of_tie_capable'][119] - 0.5) < 1e-12,
   s5['sponsor_dropped_share_of_tie_capable'])
ok('all-bills denominator still reported for reference', s5['bills_by_congress'][119] == 3)

print('[holistic base: COR-9 name assert]')
try:
    hb.assert_output_name(r'X:\anywhere\congress_cosponsor_network_HOLISTIC_20260817.xlsx'); ok('NC: production-family name refused', False)
except ValueError: ok('NC: production-family name refused', True)
try:
    hb.assert_output_name(r'X:\anywhere\holistic_network_BASE_20260817.xlsx'); ok('holistic BASE name accepted', True)
except ValueError: ok('holistic BASE name accepted', False)
try:
    hb.assert_output_name(r'X:\anywhere\some_other_name.xlsx'); ok('NC: non-family name refused', False)
except ValueError: ok('NC: non-family name refused', True)

try:
    hb.assert_output_name(os.path.join('X:', 'anywhere', 'holistic_network_BASE_20260817.manifest.json'))
    ok('NC: right prefix but wrong extension refused', False)
except ValueError: ok('NC: right prefix but wrong extension refused', True)

print('HOLISTIC BASE FIXTURES — %d passed, %d FAILED' % (P, F))
sys.exit(1 if F else 0)
