# -*- coding: utf-8 -*-
"""RENDERED-DESK AUDIT -- assert what the member desk actually RENDERS, not what its bundle holds.

Why this exists
---------------
Twice on 2026-08-26 the member desk shipped a broken view that every existing gate passed.
DESK_SPLIT (775b21af2) defers 14 profile-heavy member fields into an async
``cu-member-desk-profiles.js``.  Two module-scope indexes were built at parse time over deferred
fields and never rebuilt, so they froze empty:

  * ``BILLS`` / ``BILL_BY_KEY`` over ``member.bills``    -> "Bill repository" showed 0 of 464 bills
                                                            (fixed e02c44527)
  * ``STFIDX`` over ``member.staffdir``                  -> 0 of 5,773 staffers indexed, staffer
                                                            filter and "In contact" dead
                                                            (fixed dbf9e61a0)

``fec/audit/dashboard_integrity.py`` validated the BUNDLE throughout and reported
``status: pass, bills: 5899``.  A green audit and an empty page are structurally compatible today,
because a frozen-empty index is textually indistinguishable from a correct one.  This audit closes
that gap by EXECUTING the shipped artifacts and reading the indexes back.

THE ANTI-VACUITY PROPERTY (the load-bearing design rule)
--------------------------------------------------------
Every EXPECTED count in this audit is derived, in this file, from ``member_desk_bundle.json`` --
a DIFFERENT artifact than the two the page ships and a different code path than the one being
measured.  The check must NEVER derive its expectation from the same execution it is measuring;
if it did, an empty page would simply produce an empty expectation and the audit would go green on
nothing.  For the same reason no count is hardcoded: 464 unique bills, 5,773 staffers and 533
members are all re-derived from the bundle on every run, so the audit survives data growth and a
degenerate bundle (zero bills or zero staffdir rows) FAILS the contract instead of passing it.

The observed side is produced by ``fec/audit/rendered_desk_probe.mjs``, which only reports raw
counts and digests and never compares anything.  Python owns every assertion.

What is asserted
----------------
  A1  SPLIT CANARY  -- pre-merge the deferred-derived indexes are EMPTY.  If the artifact does not
                       actually defer, every later assertion is vacuous, so the run SKIPs with a
                       reason rather than reporting a pass it did not earn.
  A2  BILLS         -- post-merge ``BILLS.length`` and ``BILL_BY_KEY`` size equal the unique bill
                       identities derived from the bundle.
  A3  STFIDX        -- post-merge ``STFIDX`` key count equals the bundle's staffdir entry count.
  A4  MERGE HOOK    -- the hook exists in the shipped bytes and drives both rebuilds, established by
                       executing the real profiles file against the real page and observing the
                       calls, never by grepping for a function name.
  A5  REGRESSION    -- the class stays closed: (a) every deferred field the page reads at module
                       scope is also read by the merge hook, and (b) every module-scope binding
                       holds the same value after the split-then-merge as it does in the monolith
                       build.  A NEW index over a deferred field fails one or both without anyone
                       adding it to a list.
  A6  MONOLITH      -- in the DESK_SPLIT=0 shape the indexes are populated at init.

Run:  python fec/audit/audit_rendered_desk.py [--json]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEC = os.path.dirname(_HERE)
if _FEC not in sys.path:
    sys.path.insert(0, _FEC)

from paths import ROOT, p  # noqa: E402  (path bootstrap must precede this import)

PROBE = os.path.join(_HERE, 'rendered_desk_probe.mjs')

PASS, FAIL, SKIP = 'PASS', 'FAIL', 'SKIP'

# DSPLIT is 1 in the split artifact and 0 in the monolith by construction, so it is the one binding
# whose values are REQUIRED to differ between the two runs.  Everything else must match.
DIFF_EXEMPT = ('DSPLIT',)

# Mirrors billIdentity() in the shipped page.  Reimplemented here on purpose: the expectation has to
# come from the bundle through an independent code path, not from the page's own function.
_BILL_RE = re.compile(r'^(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)([0-9]+)$')


class Result(object):
    def __init__(self):
        self.checks = []
        self.info = {}

    def add(self, status, name, detail=''):
        self.checks.append((status, name, detail))
        return status

    def ok(self, cond, name, detail=''):
        return self.add(PASS if cond else FAIL, name, detail)

    @property
    def passed(self):
        return sum(1 for s, _, _ in self.checks if s == PASS)

    @property
    def failed(self):
        return sum(1 for s, _, _ in self.checks if s == FAIL)

    @property
    def skipped(self):
        return sum(1 for s, _, _ in self.checks if s == SKIP)


def _js_number(v):
    """JS Number() semantics for the fields billIdentity() coerces, returning None for NaN."""
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if v is None:
        return 0
    if isinstance(v, str):
        s = v.strip()
        if s == '':
            return 0
        try:
            return float(s)
        except ValueError:
            return None
    return None


def bill_key(row):
    """The page's bill identity, recomputed from a bundle row."""
    raw = re.sub(r'[^A-Z0-9]', '', str(row.get('bill') or '').upper())
    m = _BILL_RE.match(raw)
    if not m:
        return None
    congress = _js_number(row.get('congress'))
    if not congress:
        return None
    return '%d:%s:%d' % (int(congress), m.group(1).lower(), int(m.group(2)))


def derive_expectations(bundle_path):
    """EXPECTED side. Sourced from member_desk_bundle.json only -- never from the page."""
    t0 = time.time()
    with open(bundle_path, encoding='utf-8') as fh:
        bundle = json.load(fh)
    members = bundle.get('members') or []
    keys = set()
    bill_rows = 0
    staff_rows = 0
    staff_ids = set()
    for m in members:
        for row in (m.get('bills') or []):
            bill_rows += 1
            k = bill_key(row)
            if k:
                keys.add(k)
        for sf in (m.get('staffdir') or []):
            staff_rows += 1
            staff_ids.add(sf.get('id'))
    return {
        'source': bundle_path,
        'members': len(members),
        'names': [m.get('name') for m in members],
        'bill_rows': bill_rows,
        'bill_keys': len(keys),
        'staff_rows': staff_rows,
        'staff_ids': len(staff_ids),
        'has_bill_lobbying': bool(bundle.get('bill_lobbying')),
        'load_seconds': round(time.time() - t0, 2),
    }


def run_probe(html, profiles, node, timeout=600):
    """OBSERVED side. Runs the node harness; returns (payload, stderr, seconds)."""
    fd, job = tempfile.mkstemp(suffix='.json', prefix='rendered_desk_job_')
    os.close(fd)
    try:
        with open(job, 'w', encoding='utf-8') as fh:
            json.dump({'html': html, 'profiles': profiles}, fh)
        t0 = time.time()
        proc = subprocess.run([node, PROBE, job], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout)
        elapsed = time.time() - t0
        marker = '__PROBE_JSON__'
        idx = proc.stdout.find(marker)
        if idx < 0:
            return None, (proc.stdout + '\n' + proc.stderr).strip()[-2000:], elapsed
        return json.loads(proc.stdout[idx + len(marker):].strip()), proc.stderr.strip()[-2000:], elapsed
    finally:
        try:
            os.remove(job)
        except OSError:
            pass


def _src(expected_from, observed_from):
    return 'expected-source=%s | observed-source=%s' % (expected_from, observed_from)


def audit(html=None, profiles=None, bundle=None, node=None, timeout=600):
    r = Result()
    html = html or p('cu-member-desk.html')
    profiles = profiles or p('cu-member-desk-profiles.js')
    bundle = bundle or p('member_desk_bundle.json')
    node = node or os.environ.get('RENDERED_DESK_NODE') or shutil.which('node')
    r.info.update({'html': html, 'profiles': profiles, 'bundle': bundle, 'node': node})

    missing = [q for q in (html, bundle, PROBE) if not os.path.exists(q)]
    if missing:
        r.add(SKIP, 'rendered-desk audit', 'shipped artifacts absent: %s' % ', '.join(missing))
        return r
    if not node:
        r.add(SKIP, 'rendered-desk audit', 'node executable not found; set RENDERED_DESK_NODE to run it')
        return r

    exp = derive_expectations(bundle)
    r.info['expected'] = {k: v for k, v in exp.items() if k != 'names'}

    # CONTRACT -- a degenerate bundle must fail here, not sail through as "0 expected, 0 observed".
    degenerate = []
    if exp['members'] <= 0:
        degenerate.append('bundle has no members')
    if exp['bill_keys'] <= 0:
        degenerate.append('bundle yields 0 unique bill identities from %d rows' % exp['bill_rows'])
    if exp['staff_ids'] <= 0:
        degenerate.append('bundle yields 0 staffdir ids from %d rows' % exp['staff_rows'])
    if degenerate:
        r.add(FAIL, 'CONTRACT expectations are non-degenerate',
              'degenerate expectation, refusing to certify the page against nothing: %s | %s'
              % ('; '.join(degenerate), _src(bundle, 'not executed')))
        return r
    r.add(PASS, 'CONTRACT expectations are non-degenerate',
          '%d members, %d unique bills from %d rows, %d staffdir ids from %d rows'
          % (exp['members'], exp['bill_keys'], exp['bill_rows'], exp['staff_ids'], exp['staff_rows']))

    if not os.path.exists(profiles):
        # Only meaningful once we know the artifact is split; the probe decides that.
        profiles = None

    payload, stderr, elapsed = run_probe(html, profiles, node, timeout=timeout)
    r.info['probe_seconds'] = round(elapsed, 2)
    if payload is None:
        r.add(FAIL, 'PROBE the shipped artifacts execute',
              'node is present but the harness produced no result in %.1fs: %s' % (elapsed, stderr))
        return r
    r.info['probe'] = {k: payload.get(k) for k in
                       ('script_bytes', 'profiles_bytes', 'timings', 'deferred_fields',
                        'heavy_rows', 'var_rewritten', 'digest_nodes', 'notes')}
    if not payload.get('ok'):
        r.add(FAIL, 'PROBE the shipped artifacts execute',
              'harness error: %s' % payload.get('error'))
        return r
    r.add(PASS, 'PROBE the shipped artifacts execute',
          '%s bytes of page script + %s bytes of profiles, %.1fs'
          % (payload.get('script_bytes'), payload.get('profiles_bytes'), elapsed))
    # A deferred callback that threw would leave the page half-initialised while every count below
    # still read cleanly, so stub health is asserted rather than logged.
    r.ok(not (payload.get('notes') or []), 'PROBE no deferred callback threw during init',
         'harness notes: %s' % (payload.get('notes') or []))

    # ---- A1 SPLIT CANARY ---------------------------------------------------------------------
    if not payload.get('split'):
        r.add(SKIP, 'A1 SPLIT CANARY deferred indexes start empty',
              'artifact is not split, so every deferred-field assertion below would be vacuous: %s'
              % payload.get('split_reason', 'no reason reported'))
        r.add(SKIP, 'A2 BILLS index matches the bundle', 'no split to merge')
        r.add(SKIP, 'A3 STFIDX index matches the bundle', 'no split to merge')
        r.add(SKIP, 'A4 MERGE HOOK drives both rebuilds', 'no split to merge')
        r.add(SKIP, 'A5 REGRESSION GUARD no unrebuilt deferred structure', 'no split to merge')
        r.add(SKIP, 'A5d rendered ranked board equals the monolith\'s', 'no split to merge')
        r.add(SKIP, 'A6 MONOLITH indexes populated at init', 'input is already a monolith')
        return r

    pre = payload['shipped']['pre']
    post = payload['shipped']['post']
    observed_from = '%s + %s executed' % (os.path.basename(html), os.path.basename(profiles or '?'))
    empty_pre = [n for n, v in (('BILLS', pre['bills']), ('BILL_BY_KEY', pre['billByKey']),
                                ('STFIDX', pre['stfidx'])) if v]
    if empty_pre:
        r.add(SKIP, 'A1 SPLIT CANARY deferred indexes start empty',
              'DSPLIT=1 but %s already populated before the merge -- the artifact is not really '
              'deferring these fields, so the post-merge assertions prove nothing: %s'
              % (', '.join(empty_pre), observed_from))
        for nm in ('A2 BILLS index matches the bundle', 'A3 STFIDX index matches the bundle',
                   'A4 MERGE HOOK drives both rebuilds',
                   'A5 REGRESSION GUARD no unrebuilt deferred structure',
                   'A5d rendered ranked board equals the monolith\'s',
                   'A6 MONOLITH indexes populated at init'):
            r.add(SKIP, nm, 'split canary did not hold')
        return r
    r.ok(True, 'A1 SPLIT CANARY deferred indexes start empty',
         'BILLS=0 BILL_BY_KEY=0 STFIDX=0 pre-merge; %d/%d members carry bills, %d/%d carry staffdir'
         % (pre['membersWithBills'], pre['members'], pre['membersWithStaffdir'], pre['members']))

    # ---- A2 BILLS ----------------------------------------------------------------------------
    r.ok(post['bills'] == exp['bill_keys'] and post['billByKey'] == exp['bill_keys'],
         'A2 BILLS index matches the bundle',
         'expected %d unique bill identities (from %d bundle rows), observed BILLS=%s '
         'BILL_BY_KEY=%s; page saw %s bill rows | %s'
         % (exp['bill_keys'], exp['bill_rows'], post['bills'], post['billByKey'],
            post['billRows'], _src(bundle, observed_from)))
    r.ok(post['billRows'] == exp['bill_rows'],
         'A2b page received every bill row the bundle holds',
         'expected %d rows, page holds %s | %s' % (exp['bill_rows'], post['billRows'],
                                                   _src(bundle, observed_from)))

    # ---- A3 STFIDX ---------------------------------------------------------------------------
    r.ok(exp['staff_ids'] == exp['staff_rows'],
         'A3a staffdir ids are unique in the bundle',
         'expected the index to be able to hold every entry: %d rows collapse to %d ids | %s'
         % (exp['staff_rows'], exp['staff_ids'], _src(bundle, 'not executed')))
    r.ok(post['stfidx'] == exp['staff_ids'],
         'A3 STFIDX index matches the bundle',
         'expected %d staffdir ids (from %d bundle entries), observed STFIDX=%s; page saw %s '
         'staffdir rows | %s'
         % (exp['staff_ids'], exp['staff_rows'], post['stfidx'], post['staffRows'],
            _src(bundle, observed_from)))

    # ---- A4 MERGE HOOK -----------------------------------------------------------------------
    calls = payload['shipped'].get('calls') or []
    patched = payload['shipped'].get('patched') or []
    need = ('__melangeProfileMerge', 'rebuildBillRepository', 'rebuildStaffIndex')
    r.ok(post['hookType'] == 'function' and all(c in calls for c in need)
         and 'rebuildBillRepository' in patched and 'rebuildStaffIndex' in patched,
         'A4 MERGE HOOK drives both rebuilds',
         'expected the shipped profiles file to invoke the hook and both rebuilds; observed hook '
         'typeof=%s, instrumented=%s, call order=%s | %s'
         % (post['hookType'], patched, calls, _src('shipped bytes, executed', observed_from)))
    names_ok = payload['shipped']['post'].get('names') == exp['names']
    r.ok(names_ok and post['members'] == exp['members'] and post['profilesReady'] is True,
         'A4b merged roster aligns positionally with the bundle',
         'expected the bundle\'s %d names in order, observed %s members, roster match=%s, '
         '__profilesReady=%s | %s'
         % (exp['members'], post['members'], names_ok, post['profilesReady'],
            _src(bundle, observed_from)))

    # ---- instrumentation cross-validation ----------------------------------------------------
    inst = payload.get('instrumented')
    if not inst:
        r.add(FAIL, 'A5 REGRESSION GUARD no unrebuilt deferred structure',
              'instrumented run unavailable: %s' % payload.get('instrumented_error'))
        return r
    same = (inst['pre']['bills'] == pre['bills'] and inst['pre']['stfidx'] == pre['stfidx']
            and inst['post']['bills'] == post['bills'] and inst['post']['stfidx'] == post['stfidx']
            and inst['post']['billByKey'] == post['billByKey'])
    r.ok(same and not inst.get('merge_error'),
         'A5a instrumented run reproduces the verbatim run',
         'the enumeration rewrites %s column-0 declarations to var so the page\'s module-scope '
         'bindings become reflectable; it must not change behaviour. verbatim pre/post '
         '(%s,%s)/(%s,%s,%s) vs instrumented (%s,%s)/(%s,%s,%s), merge_error=%s'
         % (payload.get('var_rewritten'), pre['bills'], pre['stfidx'], post['bills'],
            post['billByKey'], post['stfidx'], inst['pre']['bills'], inst['pre']['stfidx'],
            inst['post']['bills'], inst['post']['billByKey'], inst['post']['stfidx'],
            inst.get('merge_error')))

    # ---- A5 REGRESSION GUARD -----------------------------------------------------------------
    deferred = set(payload.get('deferred_fields') or [])
    reads_init = set(inst.get('reads_at_init') or [])
    reads_merge = set(inst.get('reads_at_merge') or [])
    unrebuilt_fields = sorted(reads_init - reads_merge)
    r.ok(not unrebuilt_fields,
         'A5b every deferred field read at init is re-read by the merge hook',
         'deferred fields (from the shipped profiles payload): %s; read at module-scope init: %s; '
         'read during the merge hook: %s; READ AT INIT BUT NEVER REBUILT: %s | %s'
         % (sorted(deferred), sorted(reads_init), sorted(reads_merge), unrebuilt_fields,
            _src('shipped profiles payload keys', observed_from)))

    mono = payload.get('monolith')
    if not mono:
        r.add(SKIP, 'A5c split-then-merge equals the monolith build',
              'monolith comparison unavailable: %s' % payload.get('monolith_error'))
        r.add(SKIP, 'A6 MONOLITH indexes populated at init',
              'monolith comparison unavailable: %s' % payload.get('monolith_error'))
    else:
        ib, mb = inst.get('bindings') or {}, mono.get('bindings') or {}
        only_split = sorted(set(ib) - set(mb))
        only_mono = sorted(set(mb) - set(ib))
        differing = sorted(n for n in set(ib) & set(mb)
                           if n not in DIFF_EXEMPT and ib[n].get('digest') != mb[n].get('digest'))
        exempt_seen = sorted(n for n in DIFF_EXEMPT if n in ib and n in mb
                             and ib[n].get('digest') != mb[n].get('digest'))
        truncated = bool(payload.get('budget_truncated'))
        r.ok(not differing and not only_split and not only_mono and not truncated,
             'A5c split-then-merge equals the monolith build',
             'compared %d module-scope bindings; STALE AFTER MERGE: %s; only in split: %s; only in '
             'monolith: %s; exempt-by-construction and differing as expected: %s; digest budget '
             'truncated: %s | %s'
             % (len(set(ib) & set(mb)), differing or 'none', only_split or 'none',
                only_mono or 'none', exempt_seen, truncated,
                _src('monolith (DESK_SPLIT=0) init', 'split init + profiles merge')))

        # ---- A5d the RENDERED board, not just the indexes behind it ------------------------
        # renderRanked() runs once at module scope and the merge hook never re-runs it, so any
        # dependence of the board's markup on a deferred field is permanent staleness that no
        # count would reveal.  Comparing the markup against the monolith's is what turns that from
        # an assumption into an observation.  The non-empty guard keeps the comparison from being
        # satisfied by two equally blank boards.
        split_board = inst['post'].get('rowsHtmlSha')
        mono_board = mono['init'].get('rowsHtmlSha')
        board_len = inst['post'].get('rowsHtmlLen') or 0
        r.ok(board_len > 0 and split_board is not None and split_board == mono_board,
             'A5d rendered ranked board equals the monolith\'s',
             'expected the board rendered after the split+merge to match the monolith build byte '
             'for byte; observed %s bytes sha=%s vs monolith %s bytes sha=%s (merge re-rendered '
             'the board: %s) | %s'
             % (board_len, (split_board or '')[:16], mono['init'].get('rowsHtmlLen'),
                (mono_board or '')[:16],
                pre.get('rowsHtmlSha') != post.get('rowsHtmlSha'),
                _src('monolith (DESK_SPLIT=0) init', 'split init + profiles merge')))

        # ---- A6 MONOLITH -------------------------------------------------------------------
        mi = mono['init']
        r.ok(mi['bills'] == exp['bill_keys'] and mi['billByKey'] == exp['bill_keys']
             and mi['stfidx'] == exp['staff_ids'] and mi['dsplit'] == 0,
             'A6 MONOLITH indexes populated at init',
             'monolith source=%s (DSPLIT patched=%s); expected BILLS=%d BILL_BY_KEY=%d STFIDX=%d '
             'at init, observed %s/%s/%s with DSPLIT=%s | %s'
             % (mono.get('source'), mono.get('dsplit_patched'), exp['bill_keys'],
                exp['bill_keys'], exp['staff_ids'], mi['bills'], mi['billByKey'], mi['stfidx'],
                mi['dsplit'], _src(bundle, 'reconstructed monolith artifact, executed')))

    decls = payload.get('module_scope_decls') or []
    r.info['module_scope'] = {
        'declarations': len(decls),
        'reading_members': [d['names'] for d in decls if d.get('reads_members')],
        'bindings_compared': len(inst.get('bindings') or {}),
    }
    return r


def main(argv):
    r = audit()
    if '--json' in argv:
        print(json.dumps({'checks': [{'status': s, 'name': n, 'detail': d} for s, n, d in r.checks],
                          'info': r.info}, indent=2, default=str))
    else:
        for status, name, detail in r.checks:
            line = {'PASS': '  OK   ', 'FAIL': 'FAIL  ', 'SKIP': ' SKIP  '}[status] + name
            if detail:
                line += '\n         :: ' + detail
            print(line)
        ms = r.info.get('module_scope') or {}
        if ms:
            print('\nmodule scope: %d column-0 declarations, %d read the roster directly, '
                  '%d bindings compared against the monolith'
                  % (ms['declarations'], len(ms['reading_members']), ms['bindings_compared']))
        print('\nrendered-desk audit: %d passed, %d failed, %d skipped (probe %.1fs)'
              % (r.passed, r.failed, r.skipped, r.info.get('probe_seconds') or 0.0))
    try:
        from surfaces import glassbox_emit as GB
        GB.emit('audit', 'fail' if r.failed else ('skip' if r.skipped and not r.passed else 'ok'),
                'rendered-desk audit: %d passed, %d failed, %d skipped' % (r.passed, r.failed, r.skipped),
                source='moneyball-audit', stage='audit_rendered_desk',
                metadata={'expected': r.info.get('expected'), 'probe_seconds': r.info.get('probe_seconds')})
    except Exception:
        pass
    return 1 if r.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
