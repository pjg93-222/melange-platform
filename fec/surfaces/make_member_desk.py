# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import os as _oT
def _tpl(n): return open(_oT.path.join(_oT.path.dirname(_oT.path.abspath(__file__)),'templates',n),encoding='utf-8').read()
"""
Builds  cu-member-desk.html  — the integrated, dark-themed individual-member side
of the Moneyball dashboard. Linked from the SNA network dashboard (Moneyball Dashboard.html).

Pipeline:  member_desk_src.json (press/demo/committee/coalitions, extracted from the original
Member Desk) + dashboard_data.json (SNA: rank, stance, electoral, FEC CU $, cosponsor network)
+ category_stance.json (per-category numeric scores) + current-roster bioguides / legislators.csv
+ member_wikipedia_summaries.json (revision-bound extractive biography summaries)
+ electoral_plays.json + archive_leg.json + member_desk_manual.json (folded-in manual entries)
        -> one merged per-member bundle -> cu-member-desk.html

Run:  python fec/surfaces/make_member_desk.py            (writes bundle JSON + HTML)
      python fec/surfaces/make_member_desk.py --data      (writes/validates the bundle only)
"""
import json, os, csv, re, sys, unicodedata, hashlib, sqlite3
from datetime import datetime
from pathlib import Path
import openpyxl
import moneyball_core as MBC
from moneyball_core import dump_atomic, write_text_atomic   # crash-safe generated artifacts
from tier_util import legislative_press_tiers   # Legislative+Press tier (display-only; shared with the dashboard producer)
import bill_rules as BR
import stance.media_rules as MR
from stance.honors_views import load_honors
import config_overlay as CO
import audit.dashboard_integrity as DI
import district.demographic_potential as DP
import district.demographic_normalization as DN
from legislation import dashboard_legislative_workbench as DLW
from legislation.congress_bill_repository import validate_live_repository
import legislation.verify_collector_roster_updates as CRU   # verified collector->ledger sidecar (import-safe)
import district.district_cu_metrics as DCM
from district.cook_pvi import load_optional_reference, attach_reference
from legislation.legislation_culling_status import load_surface_status
from members.hearing_display_contract import load_optional as load_hearing_display
from members.member_identity import Resolver
from members.validate_descriptive_profiles import validate_descriptive_profiles
from speech.descriptive_contract import load_optional as load_speech_descriptive
from surfaces.make_dashboard_html import efficiency_percentiles
from surfaces.honors_publication import compact_honor_cell
import surfaces.desk_press_source as DPS   # DESK_PRESS_SOURCE: the desk's press stream (N1a)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
WB = os.path.join(MB_ROOT, 'Moneyball Main Version - Blended.xlsx')
B = MB_ROOT
def L(p):
    for fp in ([p] if os.path.isabs(p) else [os.path.join(B, p), os.path.join(B, 'fec', p)]):
        if os.path.exists(fp):
            return json.load(open(fp, encoding='utf-8'))
    return None
def _optional_L(p):
    try:
        return L(p)
    except (OSError, TypeError, ValueError):
        print('  !! %s unreadable/malformed — optional desk block disabled' % p)
        return None

def _load_responsiveness_optional(path):
    """Fail loudly on every malformed present responsiveness artifact."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError(
            'responsiveness file %s is unreadable or invalid JSON: %s'
            % (path, exc)
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            'responsiveness file %s payload must be an object' % path
        )
    if payload.get('schema') != 'responsiveness-layers-v1':
        raise RuntimeError(
            'responsiveness file %s schema is not responsiveness-layers-v1'
            % path
        )
    receipts = payload.get('receipts')
    if not isinstance(receipts, dict) or receipts.get('display_only') is not True:
        raise RuntimeError(
            'responsiveness file %s requires receipts.display_only == true'
            % path
        )
    rows = payload.get('members')
    if not isinstance(rows, list):
        raise RuntimeError(
            'responsiveness file %s members must be a list' % path
        )
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RuntimeError(
                'responsiveness file %s member row %d must be an object'
                % (path, index)
            )
        member = row.get('member')
        if not isinstance(member, str) or not member.strip():
            raise RuntimeError(
                'responsiveness file %s member row %d has an empty member name'
                % (path, index)
            )
        member_key = member.strip()
        if member_key in seen:
            raise RuntimeError(
                'responsiveness file %s repeats member %r' % (path, member)
            )
        seen.add(member_key)
        take_up = row.get('take_up')
        if not isinstance(take_up, dict):
            raise RuntimeError(
                'responsiveness file %s member %r take_up must be an object'
                % (path, member)
            )
        answered = take_up.get('answered')
        eligible = take_up.get('eligible')
        rate = take_up.get('rate')
        if (type(answered) is not int or answered < 0
                or type(eligible) is not int or eligible < 0
                or answered > eligible
                or (rate is not None and (
                    type(rate) not in (int, float) or not 0 <= rate <= 1
                ))
                or (eligible == 0 and rate is not None)):
            raise RuntimeError(
                'responsiveness file %s member %r take_up counts/rate malformed'
                % (path, member)
            )
        vehicles = take_up.get('vehicles')
        if not isinstance(vehicles, list):
            raise RuntimeError(
                'responsiveness file %s member %r take_up.vehicles must be a list'
                % (path, member)
            )
        for vehicle_index, vehicle in enumerate(vehicles):
            if not isinstance(vehicle, dict):
                raise RuntimeError(
                    'responsiveness file %s member %r vehicle %d must be an object'
                    % (path, member, vehicle_index)
                )
        for field in ('in_group', 'capacity'):
            if field in row and not isinstance(row[field], dict):
                raise RuntimeError(
                    'responsiveness file %s member %r %s must be an object'
                    % (path, member, field)
                )
    return payload

# ---------- sources ----------
MD  = L('data/members/member_desk_src.json')              # {weights, members[], coalitions{}}
SNA = L(os.environ.get('DASHBOARD_DATA_PATH') or 'dashboard_data.json')  # SNA bundle
CS  = L(os.environ.get('CATEGORY_STANCE_PATH') or 'category_stance.json')  # per-category stance
BIO = L('model_bio.json') or {}              # name -> bioguide
_ROSTER_ROWS = L('data/engine/congestion.json') or []
_ROSTER_MONEY = {row['member']:row for row in _ROSTER_ROWS}
_ROSTER_BIO = {row.get('member'): row.get('bioguide') for row in _ROSTER_ROWS
               if row.get('member') and row.get('bioguide')}
_WIKIPEDIA_DOC = _optional_L('data/members/member_wikipedia_summaries.json') or {}
if (_WIKIPEDIA_DOC and
        _WIKIPEDIA_DOC.get('schema') != 'member-wikipedia-biographies-v1'):
    raise RuntimeError('member Wikipedia summary artifact has an unsupported schema')
_WIKIPEDIA = _WIKIPEDIA_DOC.get('members') or {}
EP  = L('data/money/electoral_plays.json') or {}        # name -> {play,value,route,...}
ARCH= L('archive_leg.json') or {}            # name -> {cat: legislative score}
MANUAL = L('member_desk_manual.json') or {}  # name -> {staff, bio, psychometrics, invested_override,...}
PDATE  = L('data/stance/press_date_overrides.json') or {}  # full article url -> {date, method} (recovered for the 244 undated)
PRESSOV = CO.merged_rows('data/stance/press_stance_overrides.json','corrections',
                         ('url','title_contains'),data_root=B)  # live rows + tracked worktree gold
def _press_corr(title, url):
    t = (title or '').lower()
    for c in PRESSOV:
        if (c.get('url') and c['url'] == url) or (c.get('title_contains') and c['title_contains'].lower() in t):
            return c
    return None
OPT    = L(os.environ.get('OPTIMIZE_RESULT_PATH') or 'optimize_result.json') or {}     # joint lazy-greedy optimizer: {alloc:{name:$}, mode, guarantee, base, final,...}
if not OPT: print('  !! optimize_result.json MISSING/empty — desk allocation panels will be blank (run optimize.py)')   # S3-05
OPT_ALLOC = OPT.get('alloc', {})             # the RESEARCH-GROUNDED suggested allocation (vs score-proportional)
EH  = L('data/money/electoral_history.json') or {}      # per-member FEC primary+general vote-share trend + forecast (build_electoral_history.py)
INDUSTRY = L('data/lobbying/district_industry.json')       # OPTIONAL Phase-F LODES/NAICS district + state metrics
INDUSTRY4 = _optional_L('data/lobbying/district_industry_4digit.json')  # OPTIONAL Phase-F CBP four-digit cross-check
CU_METRICS = DCM.CreditUnionMetrics(INDUSTRY4)
EMPLOYERS = _optional_L('data/district/district_employers.json')  # OPTIONAL Phase-G federal employers + CBP large establishments
FINANCIAL = _optional_L('data/district/district_financial.json')  # OPTIONAL Phase-G FDIC/NCUA district footprint
HMDA = _optional_L('data/district/district_hmda.json')            # OPTIONAL Phase-H mortgage lending
COMMUNITY = _optional_L('data/district/district_community.json')  # OPTIONAL 2024 ACS structural context/controls
COOK_PVI = load_optional_reference()  # hash-bound, offline historical source snapshot
DISTRICT_BRIEFS = L('data/district/district_briefs_v1.json')       # REQUIRED ratified three-paragraph district narratives
if os.environ.get('MONEYBALL_ALLOW_MISSING_BILL_REPOSITORY') == '1':
    print('  !! BREAKGLASS: live Congress.gov bill repository gate disabled')
    BILL_LIVE = _optional_L('bill_repository_live.json') or {
        'schema': 'congress-bill-repository-v1', 'updated_at': '',
        'source': 'https://www.congress.gov/', 'bills': {}, 'failures': {},
    }
else:
    BILL_LIVE = validate_live_repository(
        Path(B) / 'member_desk_bundle.json',
        Path(B) / 'bill_repository_live.json',
        Path(B) / 'data' / 'engine' / 'congress_bill_refresh_receipt.json',
    )
BILL_LOBBYING = _optional_L('data/lobbying/bill_lobbying_sidecar.json') or {
    'schema': 'bill-lobbying-sidecar-v2',
    'coverage_note': '',
    'member_basis_note': '',
    'bills': {},
}
CULLING_STATUS = load_surface_status(B)
# ---------- membership-source dial (SCRAPEB-1 / DE-016) ----------
# Which verified-membership artifact the FOURTH membership sidecar merge (~:1720) reads.
#   'delta'      explicit historical comparison: data/members/collector_roster_updates.ndjson,
#                the collector verifier's ACCEPTED delta, merged exactly as before.
#   'cumulative' DEFAULT: the durable history chain step 12 builds, validates and
#                receipt-binds: data/members/sentinel_verified_membership_history.ndjson
#                (fec/legislation/sync_sentinel_bill_ledger.py HISTORY at :39,
#                materialize_verified_history at :785).
# WHY THE DIAL EXISTS. verify_collector_roster_updates.run() REGENERATES THE DELTA SIDECAR WHOLE
# every run -- write_text_atomic(_ndjson(result['accepted']), updates_path) -- and computes it
# against a member_desk_bundle.json that has ALREADY ABSORBED the previous delta, because this
# merge folds the sidecar into `bills`, :2032 puts `bills` on the member record, :2496 writes the
# bundle, and the verifier reads that same bundle back (build_ledger_view). A row the bundle
# already carries is counted `already_present` and DROPPED, not re-emitted. So with base =
# workbook + manual + anchors and D = the live rows absent from base, the sidecar obeys
# S_k = D \ S_(k-1): the same memberships enter and leave the bundle on alternate cycles, and
# fec/engine/primacy_factors.py compute() -- which accumulates numerator/denominator per member
# straight off record['bills'] -- oscillates with PERIOD 2, moving the primacy factor, the tier
# board buckets and allocation priority. DE-016 saw this as a standalone-invocation hazard
# (202 accepted rows collapsing to 3); SCRAPEB-1 shows the chain reproduces it one cycle later,
# so a standalone-only guard would be theatre. The cumulative history never loses a row, so
# merging it is a fixed point and the oscillation stops.
# 2026-09-20 defect repair: verified cumulative history is authoritative so an
# unchanged refresh cannot delete previously accepted memberships. Current
# admissions, official role/withdrawal facts and adjudicated withdrawals still
# run after this merge. 'delta' remains an explicit historical comparison dial.
DESK_MEMBERSHIP_SOURCE = (os.environ.get('DESK_MEMBERSHIP_SOURCE') or 'cumulative').strip().lower()
if DESK_MEMBERSHIP_SOURCE not in ('delta', 'cumulative'):
    raise ValueError(
        "DESK_MEMBERSHIP_SOURCE must be 'delta' or 'cumulative'; got %r"
        % os.environ.get('DESK_MEMBERSHIP_SOURCE'))

# D4 -- CROSSPOST_DEDUP (2026-09-16, packet audits/members_section_20260914 §D4).
# Until now an exact cross-post -- the same normalized text posted by the same
# member on a second platform -- was VISIBLE on the desk but carried
# scored=False.  Measured on the published bundle: 1,028 such rows across 279
# members, of which 895 carry a sign AND scorable text and so regain scored=True
# here; the other 133 are 121 rows whose own stance is neutral (pol==0) and 12
# link-only repeats.  Every one of those rows already carried
# its own pol/label/stance, computed at construction time before `duplicate` was
# known (evidence/q11-score-every-post.md §1), so scoring them borrows no sign
# from a twin.  Default '0' = every signed post is scored evidence.
# CROSSPOST_DEDUP=1 is the revert switch and reproduces the pre-change bundle
# field for field.
#
# LINK-ONLY POSTS ARE A NEW, EXPLICIT RULE, NOT A RESTORATION.  19 posts across
# 7 members (12 of them Warren's) normalize to the EMPTY string because
# MR.normalized_text strips the URL and nothing else remains.  Today they are
# unscored only as a side effect of every one of them colliding into a single
# empty key; under the dial that accident disappears, so the policy is stated
# here instead: a post with no scorable text carries no stance evidence.
CROSSPOST_DEDUP = os.environ.get('CROSSPOST_DEDUP', '0') == '1'

# D5 -- DESK_RESPONSIVENESS (2026-09-16, packet §D5).  The display-only CU ask
# take-up / capacity card is retired from the member desk.  Default '0' = the
# responsiveness artifact is never loaded and the card is never injected;
# DESK_RESPONSIVENESS=1 is the revert switch.  The layer feeds no score, tier,
# rank or allocation (evidence/point2-3-responsiveness-committees.md), so the
# dial cannot move ranks_python.json.
DESK_RESPONSIVENESS = os.environ.get('DESK_RESPONSIVENESS', '0') == '1'


def social_scoring(sign, normalized_text, duplicate, first_post=None, dedup=None):
    """Return (scored, exclusion, also) for one social row.

    ``dedup=True`` reproduces the pre-2026-09-16 behaviour exactly: a duplicate
    is visible and unscored with the 'exact cross-post duplicate' label, and no
    'also' note exists.  ``dedup=False`` (the production default) scores every
    signed post that has scorable text; ``first_post`` is the ``(platform,
    date)`` of the first row seen under the same key and becomes the display
    note on its later cross-posts.
    """
    if dedup is None:
        dedup = CROSSPOST_DEDUP
    if dedup:
        return (
            bool(sign and not duplicate),
            ('exact cross-post duplicate' if duplicate
             else ('' if sign else 'neutral')),
            '',
        )
    if not sign:
        return (False, 'neutral', '')
    if not normalized_text:
        return (False, 'link-only post: no scorable text', '')
    also = ''
    if duplicate and first_post:
        also = ('also posted on %s %s' % (first_post[0] or 'social',
                                          first_post[1] or '')).strip()
    return (True, '', also)


def load_verified_membership_history(root, *, now=None, repository_snapshot=None):
    """Read accepted cumulative history only when its current bindings validate.

    Historical admission dates are durable evidence, not expiry dates. Freshness
    means this history includes the current verified delta and is tied to the
    current repository. Neither missing files nor malformed lines mean zero acts.
    """
    import json
    from pathlib import Path
    from datetime import datetime, timezone
    from legislation import sync_sentinel_bill_ledger as history
    from legislation import verify_collector_roster_updates as verifier
    from legislation import official_membership_movements as movements
    from legislation.congress_bill_repository import parse_bill_identity
    from paths import to_host   # receipts name Windows paths; identity on Windows (compute lane MEL)

    root = Path(root)
    history_path = root / 'data/members/sentinel_verified_membership_history.ndjson'
    receipt_path = root / 'data/engine/sentinel_verified_membership_history_receipt.json'
    updates_path = root / verifier.UPDATES_REL
    verifier_path = root / verifier.RECEIPT_REL
    repository_path = root / verifier.REPOSITORY_REL
    for path in (history_path, receipt_path, updates_path, verifier_path, repository_path):
        if not path.is_file():
            raise RuntimeError('cumulative desk memberships require ' + str(path))
    rows, receipt, metrics = history.validate_existing_history(history_path, receipt_path)
    if not receipt:
        # The shared writer permits a first empty bootstrap without a receipt;
        # this published reader requires proof even for an observed empty set.
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        block = receipt.get('history') or {}
        if (receipt.get('schema') != history.HISTORY_RECEIPT_SCHEMA
                or receipt.get('status') != 'PASS'
                or Path(to_host(str(block.get('path') or ''))).resolve() != history_path.resolve()
                or block.get('sha256') != history.sha256(history_path)
                or block.get('verified_memberships') != 0
                or block.get('verified_interaction_rows') != 0):
            raise RuntimeError('empty verified history lacks a hash-bound zero-count PASS receipt')
        history._validate_history_provenance(rows, receipt)

    updates = history.load_ndjson(updates_path)
    delta = receipt.get('current_delta') or {}
    if (Path(to_host(str(delta.get('path') or ''))).resolve() != updates_path.resolve()
            or delta.get('sha256') != history.sha256(updates_path)
            or delta.get('verified_memberships') != len(updates)):
        raise RuntimeError('verified history is stale or unbound to the current collector delta')
    for row in rows:
        if any(not str(row.get(field) or '').strip() for field in
               ('bill', 'congress', 'member', 'bioguide', 'repository_key')):
            raise RuntimeError('verified history has an incomplete desk projection identity')
        try:
            identity_key = parse_bill_identity(row['bill'], row['congress'])['key']
        except (TypeError, ValueError) as exc:
            raise RuntimeError('verified history has an invalid bill/congress identity') from exc
        if identity_key != str(row['repository_key']).strip().lower():
            raise RuntimeError('verified history bill/congress disagrees with its repository key')
    by_key = {row['membership_key']: row for row in rows}
    for update in updates:
        carried = by_key.get(history.membership_key(update))
        if (carried is None or not {area.casefold() for area in history.normalized_areas(update)}.issubset(
                {area.casefold() for area in carried['areas']})):
            raise RuntimeError('verified history omits a current accepted membership or category')
    import hashlib
    repository_bytes = repository_path.read_bytes()
    repository_hash = hashlib.sha256(repository_bytes).hexdigest()
    if repository_snapshot is not None and json.loads(repository_bytes) != repository_snapshot:
        raise RuntimeError('official sponsor snapshot differs from the current verified repository')
    current = json.loads(verifier_path.read_text(encoding='utf-8'))
    if (current.get('schema') != verifier.RECEIPT_SCHEMA or current.get('status') != 'PASS'
            or Path(to_host(str((current.get('outputs') or {}).get('updates') or ''))).resolve() != updates_path.resolve()
            or Path(to_host(str((current.get('repository') or {}).get('path') or ''))).resolve() != repository_path.resolve()
            or (current.get('repository') or {}).get('sha256') != repository_hash):
        raise RuntimeError('verified history requires the current repository-bound collector PASS receipt')
    identity = lambda row, date_key: tuple(str(row.get(key) or '') for key in
        ('bill', 'congress', 'member', 'bioguide', 'role', date_key))
    accepted = current.get('accepted')
    if (not isinstance(accepted, list)
            or (current.get('counts') or {}).get('accepted') != len(updates)
            or sorted(identity(row, 'date') for row in accepted)
               != sorted(identity(row, 'join_date') for row in updates)):
        raise RuntimeError('collector delta membership identities differ from their PASS receipt')
    try:
        checked = datetime.fromisoformat(current['generated_utc'].replace('Z', '+00:00'))
        completed = datetime.fromisoformat(receipt['completed_at'].replace('Z', '+00:00'))
        current_time = now or datetime.now(timezone.utc)
        age = (current_time - checked).total_seconds()
        completion_age = (current_time - completed).total_seconds()
        ordering = (completed - checked).total_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError('verified membership receipt timestamps are invalid') from exc
    if not -300 <= age <= 8 * 86400 or completion_age < -300 or ordering < -1:
        raise RuntimeError('verified membership history provenance is stale or precedes verification')
    if movements.enabled(root) and not (root / movements.RELATIVE).is_file():
        raise RuntimeError('cumulative memberships require the enabled official movement snapshot')
    return rows, {**metrics, 'history_sha256': receipt['history']['sha256'],
                  'receipt_sha256': history.sha256(receipt_path),
                  'current_delta_sha256': delta['sha256']}


def shape_cumulative_membership_rows(
        records,
        history_schema='sentinel-verified-membership-history-v1',
        update_schema='collector-roster-update-v1',
        roles=('original', 'cosponsor')):
    """Shape verified-history rows into collector-sidecar rows. PURE: no I/O, no globals.

    Input rows are `sentinel-verified-membership-history-v1`
    (sync_sentinel_bill_ledger.normalize_history_record). Every row in that file is an
    ACCEPTED membership by construction -- the history is fed only by the verifier's accepted
    delta and by the hash-bound ledger bootstrap. The validated cumulative caller
    explicitly projects sponsor/original/cosponsor; its merger requires sponsors
    to match the current official repository before crediting an existing act.
    The default remains original/cosponsor for compatibility. The receipt-bound
    reader validates history before projection, so malformed rows cannot disappear
    silently. Admissions and current role/withdrawal filters still run afterward.

    Output rows carry exactly the fields collector_roster_updates.ndjson carries, so
    CRU.merge_collector_roster_updates and everything downstream are unchanged.

    Deduplicated on (bill, congress, member, role): the desk collapses on (bill, congress)
    while the history's own key is (repository_key, bioguide, role), so two history rows can
    name one desk act. Keeping both would double-count the declared source rows that
    dashboard_integrity.postflight reconciles. Order is deterministic.
    """
    shaped, seen = [], set()
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get('schema') or '') != history_schema:
            continue
        role = str(record.get('role') or '').strip().lower()
        if role not in roles or record.get('withdrawn'):
            continue
        bill = str(record.get('bill') or '').strip()
        congress = str(record.get('congress') or '').strip()
        member = str(record.get('member') or '').strip()
        if not bill or not congress or not member:
            continue
        key = (bill, congress, member, role)
        if key in seen:
            continue
        seen.add(key)
        areas = record.get('areas')
        shaped.append({
            'schema': update_schema,
            'bill': bill,
            'congress': congress,
            'repository_key': str(record.get('repository_key') or ''),
            'member': member,
            'bioguide': str(record.get('bioguide') or ''),
            'role': role,
            'join_date': str(record.get('join_date') or ''),
            'withdrawn': False,
            # Areas are inherited verbatim from the ledger and are already canonical on both
            # sides; re-canonicalising here could only perturb the seed fingerprint.
            'areas': ([str(area or '') for area in areas]
                      if isinstance(areas, list) else []),
            'title': str(record.get('title') or ''),
            'sponsor': str(record.get('sponsor') or ''),
            'summary': str(record.get('summary') or ''),
            'action': str(record.get('action') or ''),
            'ncos': record.get('ncos') or 0,
            'url': str(record.get('url') or ''),
            'source': str(record.get('source') or ''),
            'verified_at': str(record.get('verified_at') or ''),
            'repository_sha256': str(record.get('repository_sha256') or ''),
        })
    shaped.sort(key=lambda row: (row['bill'], row['congress'], row['member'], row['role']))
    return shaped


INDUSTRY_SHORT = {
    'CNS01':'Agriculture', 'CNS02':'Mining', 'CNS03':'Utilities', 'CNS04':'Construction',
    'CNS05':'Manufacturing', 'CNS06':'Wholesale Trade', 'CNS07':'Retail Trade',
    'CNS08':'Transportation & Warehousing', 'CNS09':'Information', 'CNS10':'Finance & Insurance',
    'CNS11':'Real Estate', 'CNS12':'Professional & Technical', 'CNS13':'Management',
    'CNS14':'Admin & Waste Mgmt', 'CNS15':'Education', 'CNS16':'Health Care',
    'CNS17':'Arts & Recreation', 'CNS18':'Accommodation & Food', 'CNS19':'Other Services',
    'CNS20':'Public Admin',
}
# BLENDED RANK AUTHORITY (user-directed 2026-07-03 "put in the blended rank"): the desk previously read
# the workbook Blended Model formula cells (rank/score), which go stale to None after any openpyxl save
# and disagreed with the phase-2 authority (Scott-vs-Crapo audit finding R2). ranks_python.json is the
# canonical, always-fresh blended rank — source rank/score from it, workbook only as fallback.
# 2026-07-06 (user: 'make sure the displays are entirely accurate'): the R2 fix corrected rank/score
# but LEFT the pillar percentiles + weights on the stale workbook — they diverged from (and inverted)
# the authority (Crapo influence 1.000 in the authority showed as 0.222 on the desk; weights were the
# old 0.40/0.20/0.35/0.05 vs the then-live 0.25/0.30/0.40/0.05). Now the FOUR pillar bars, their raw
# inputs, AND the displayed weights all come from ranks_python.json — one source of truth, workbook
# only as last-resort fallback. RA_PCTS keys map: leg/press/inf/dem -> desk leg_n/press_n/comm_n/demo_n.
_RA = L('ranks_python.json') or {}
RA_RANK = _RA.get('rank', {}); RA_SCORE = _RA.get('score', {})
RA_PCTS = _RA.get('pcts', {}); RA_PRESSP = _RA.get('press_pillar', {})
RA_INF_BASE = _RA.get('influence_raw_base', {})
RA_ROLE_NOMINAL = _RA.get('formal_role_influence_nominal', {})
RA_ROLE_BONUS = _RA.get('formal_role_influence_bonus', {})
RA_ROLE_DISPLAY = _RA.get('formal_role_display_weight', {})
RA_ROLE_OFFICE = _RA.get('formal_role_influence_office', {})
RA_ROLE_ELIGIBILITY = _RA.get('formal_role_influence_eligibility', {})
RA_INF_ADJ = _RA.get('influence_raw_adjusted', {})
_AB = _RA.get('ab_weights', {})   # {2:leg,3:press,4:inf,5:dem} — the live user dials
RA_W = {'leg': _AB.get('2'), 'press': _AB.get('3'), 'comm': _AB.get('4'), 'demo': _AB.get('5')} if _AB else {}
_SNAP = L('data/engine/pillar_inputs_snapshot.json') or {}   # O=leg raw, E=influence raw (committee_score), F=demo raw
RA_OEF = {'leg': (_RA.get('legislative_pillar') or {}).get('pillar', _SNAP.get('O', {})),
          'comm': RA_INF_ADJ or _SNAP.get('E', {}),
          'demo': _SNAP.get('F', {})}
# ---- signed cross-media reference layer (user-ratified 2026-07-04): who this member references
# (out) / is referenced by (in), Fable-valenced, from member_references.json (directed a->b) ----
_REFS = (L('member_references.json') or {}).get('edges', [])
_PERSONAL = (L('data/members/member_personal_texture.json') or {}).get('members', {})

# The catch-net is deliberately high-recall and its source artifact is immutable at this
# stage.  Repeated transcripts can therefore yield several cards for the same experience.
# Keep every raw item/evidence row in the bundle and attach a display-only suppression
# receipt.  The lexical rule is intentionally strict; the context-assisted rule is narrower
# still and is used only for an unambiguous support/comfort event expressed with synonyms.
_PERSONAL_DEDUPE_STOP = frozenset('''
    a an and are as at be been being but by can could did do does doing for from
    had has have having he her hers him his i if in into is it its me my of on or
    our ours she so than that the their them there they this those to too us was
    we were what when where which while who why will with would you your about
    after again also among because before between both during member more most
    over said says these through under until very
'''.split())
_PERSONAL_DEDUPE_ALIASES = {
    'consoled': 'console', 'consoling': 'console', 'consoles': 'console',
    'comforted': 'console', 'comforting': 'console', 'comforts': 'console',
    'shot': 'shoot', 'shooting': 'shoot', 'came': 'come',
    'families': 'family', 'lives': 'life', 'wives': 'wife',
    'children': 'child',
}
_PERSONAL_DEDUPE_EVENT_TERMS = frozenset({'console'})
_PERSONAL_DEDUPE_NEGATIONS = frozenset({
    'aint', 'cannot', 'cant', 'didnt', 'doesnt', 'dont', 'hardly', 'hasnt',
    'havent', 'isnt', 'neither', 'never', 'no', 'nobody', 'none', 'nor',
    'not', 'nothing', 'rarely', 'wasnt', 'without', 'wont', 'wouldnt',
})
_PERSONAL_DEDUPE_NAME_STOP = frozenset('''
    after and as at but during for from he her his i if in it its look mr mrs ms
    not president senator she speaker that the their there these they this those
    to we well when while who you yes
'''.split())
_PERSONAL_SOURCE_QUALITY = {
    'CREC': 6,
    'HEARING': 5,
    'INTERVIEW': 5,
    'OFFICIAL': 4,
    'PRESS_RELEASE': 3,
    'PRESS': 2,
    'PRESS_TITLE': 1,
}
_PERSONAL_CONFIDENCE_QUALITY = {'high': 3, 'medium': 2, 'low': 1}


def _personal_word_pairs(value, excluded=frozenset()):
    pairs = []
    for raw in re.findall(r'[A-Za-z0-9]+', str(value or '').replace('\u2019', "'")):
        normalized = _PERSONAL_DEDUPE_ALIASES.get(raw.lower(), raw.lower())
        if (len(normalized) >= 3
                and normalized not in _PERSONAL_DEDUPE_STOP
                and normalized not in excluded):
            pairs.append((raw, normalized))
    return pairs


def _personal_terms(value, excluded=frozenset()):
    return {normalized for _, normalized in _personal_word_pairs(value, excluded)}


def _personal_normalized_text(value):
    return ' '.join(normalized for _, normalized in _personal_word_pairs(value))


def _personal_named_anchors(value, excluded=frozenset()):
    anchors = set()
    for raw in re.findall(r'\b[A-Z][a-z]{2,}\b', str(value or '')):
        normalized = _PERSONAL_DEDUPE_ALIASES.get(raw.lower(), raw.lower())
        if (normalized not in excluded
                and normalized not in _PERSONAL_DEDUPE_NAME_STOP):
            anchors.add(normalized)
    return anchors


def _personal_negated_scope_terms(value):
    """Return content terms locally governed by an explicit negation.

    Comparing a document-level yes/no flag is too blunt: "not many know ...
    consoled my family" affirms the consolation event, while "never been a
    fan" reverses the shared *fan* claim.  A short forward scope catches the
    latter without turning unrelated discourse framing into event polarity.
    """
    normalized = str(value or '').lower().replace('\u2019', "'").replace('`', "'")
    tokens = [
        _PERSONAL_DEDUPE_ALIASES.get(token.replace("'", ''), token.replace("'", ''))
        for token in re.findall(r"[a-z]+(?:'[a-z]+)?", normalized)
    ]
    scoped = set()
    for index, token in enumerate(tokens):
        if token not in _PERSONAL_DEDUPE_NEGATIONS:
            continue
        for candidate in tokens[index + 1:index + 7]:
            if (candidate not in _PERSONAL_DEDUPE_STOP
                    and candidate not in _PERSONAL_DEDUPE_NEGATIONS
                    and len(candidate) >= 3):
                scoped.add(candidate)
    return scoped


def _personal_named_anchors_compatible(left_value, right_value, excluded):
    """Reject lexical lookalikes that identify different named people.

    Exact subsets remain compatible ("Trump" vs "Donald Trump").  When both
    sides introduce exclusive names, require near-total agreement; this keeps
    "son John" and "son James" distinct even if the school and activity match.
    """
    left = _personal_named_anchors(left_value, excluded)
    right = _personal_named_anchors(right_value, excluded)
    if not left and not right:
        return True
    if not left or not right:
        return False
    if left <= right or right <= left:
        return True
    return len(left & right) / len(left | right) >= 0.80


def _personal_claims_are_near_duplicates(member_name, left, right):
    """Conservative same-member/same-category display equivalence.

    Exact/near-exact wording is collapsed.  Context may bridge shorter paraphrases only
    for a narrowly enumerated event predicate and a shared named participant.  Requiring
    the predicate in both *claims* prevents a common source passage from merging distinct
    experiences merely because their evidence windows overlap.
    """
    if left.get('category') != right.get('category'):
        return False
    excluded = _personal_terms(member_name) | {
        'senator', 'representative', 'congressman', 'congresswoman',
        'chairman', 'chairwoman',
    }
    left_terms = _personal_terms(left.get('claim'), excluded)
    right_terms = _personal_terms(right.get('claim'), excluded)
    if not left_terms or not right_terms:
        return False
    shared = left_terms & right_terms
    left_negated = _personal_negated_scope_terms(left.get('claim'))
    right_negated = _personal_negated_scope_terms(right.get('claim'))
    if (left_negated ^ right_negated) & shared:
        return False

    left_text = _personal_normalized_text(left.get('claim'))
    right_text = _personal_normalized_text(right.get('claim'))
    if left_text == right_text:
        return True
    anchors_compatible = _personal_named_anchors_compatible(
        left.get('claim'), right.get('claim'), excluded
    )
    if (min(len(left_text), len(right_text)) >= 24
            and (left_text in right_text or right_text in left_text)
            and anchors_compatible):
        return True

    union = left_terms | right_terms
    if (len(shared) >= 4 and len(shared) / len(union) >= 0.72
            and anchors_compatible):
        return True

    if not (shared & _PERSONAL_DEDUPE_EVENT_TERMS):
        return False
    left_context = '%s %s' % (left.get('claim') or '', left.get('evidence') or '')
    right_context = '%s %s' % (right.get('claim') or '', right.get('evidence') or '')
    left_context_terms = _personal_terms(left_context, excluded)
    right_context_terms = _personal_terms(right_context, excluded)
    cross_containment = max(
        len(left_terms & right_context_terms) / len(left_terms),
        len(right_terms & left_context_terms) / len(right_terms),
    )
    shared_named_anchor = (
        _personal_named_anchors(left_context, excluded)
        & _personal_named_anchors(right_context, excluded)
    )
    return cross_containment >= 0.62 and bool(shared_named_anchor)


def _personal_representative_key(item, original_index):
    claim = str(item.get('claim') or '').strip()
    word_count = len(re.findall(r'\b\w+\b', claim))
    provenance = sum(bool(item.get(field)) for field in ('date', 'locator', 'url'))
    named_anchors = len(_personal_named_anchors(claim))
    complete = not (claim.endswith(('...', '\u2026', '-', '--')) or '[INAUDIBLE]' in claim.upper())
    date = str(item.get('date') or '') if re.match(r'^\d{4}-\d{2}-\d{2}$', str(item.get('date') or '')) else ''
    # Quality/provenance dominate.  Explicitly named, complete, recent claims then win;
    # bounded detail favors an informative representative without rewarding transcripts.
    return (
        _PERSONAL_CONFIDENCE_QUALITY.get(str(item.get('confidence') or '').lower(), 0),
        _PERSONAL_SOURCE_QUALITY.get(str(item.get('source') or '').upper(), 0),
        provenance,
        min(named_anchors, 3),
        int(complete),
        date,
        min(word_count, 40),
        -max(0, word_count - 40),
        -original_index,
    )


def _personal_for_display(member_name, personal):
    """Attach a display-dedupe receipt without changing any raw catch-net item."""
    if not isinstance(personal, dict):
        return personal
    items = personal.get('items') or []
    eligible_indices = [
        index for index, item in enumerate(items)
        if isinstance(item, dict) and item.get('display_eligible') is True
    ]
    parents = {index: index for index in eligible_indices}

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left_index, right_index):
        left_root, right_root = find(left_index), find(right_index)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for offset, left_index in enumerate(eligible_indices):
        for right_index in eligible_indices[:offset]:
            if _personal_claims_are_near_duplicates(
                    member_name, items[left_index], items[right_index]):
                union(left_index, right_index)

    grouped = {}
    for index in eligible_indices:
        grouped.setdefault(find(index), []).append(index)
    clusters = []
    suppressed_indices = []
    for indices in grouped.values():
        if len(indices) < 2:
            continue
        representative = max(
            indices,
            key=lambda index: _personal_representative_key(items[index], index),
        )
        suppressed = sorted(index for index in indices if index != representative)
        suppressed_indices.extend(suppressed)
        clusters.append({
            'representative_index': representative,
            'suppressed_indices': suppressed,
        })
    clusters.sort(key=lambda row: row['representative_index'])
    suppressed_indices.sort()
    prepared = dict(personal)
    prepared['display_dedupe'] = {
        'schema': 'personal-display-dedupe-v1',
        'method': 'conservative same-category lexical/event clustering; display-only; raw items retained',
        'suppressed_count': len(suppressed_indices),
        'suppressed_indices': suppressed_indices,
        'clusters': clusters,
    }
    return prepared

_ROUT = {}; _RIN = {}
for _e in _REFS:
    _ROUT.setdefault(_e['a'], []).append(_e); _RIN.setdefault(_e['b'], []).append(_e)
def refs_for(name):
    def pack(e, other, want_quote):
        d = {'who': other, 'net': round(e.get('net', 0), 2), 'n': e.get('n_scored', 0),
             'pos': e.get('pos', 0), 'neg': e.get('neg', 0)}
        if want_quote:
            ex = e.get('examples') or []
            if ex and ex[0].get('w'):
                # run-4q (user: "display the full quotes or to where the name is mentioned"): the layer
                # stores a 200-char window CENTERED ON THE MENTION — the old [:130] head-cut could chop
                # the mentioned name out of its own evidence. Ship the full window + its source.
                d['q'] = re.sub(r'\s+', ' ', ex[0]['w']).strip()
                d['src'] = ex[0].get('src') or ''
        return d
    # 2026-07-04 user directive ("have a toggle where the phrase is mentioned"): evidence quote on
    # EVERY reference row, both directions — the user can verify the person/mention himself.
    out = [pack(e, e['b'], True) for e in sorted(_ROUT.get(name, []), key=lambda e: -e.get('n_scored', 0))[:8]]
    inn = [pack(e, e['a'], True) for e in sorted(_RIN.get(name, []), key=lambda e: -e.get('n_scored', 0))[:8]]
    return {'out': out, 'in': inn} if (out or inn) else None
# ---- computed psychometrics (user-directed 2026-07-03): Big5 radar + B&L persona + susceptibility cross-read ----
B5  = (L('data/members/big5_blue_validated.json') or L('data/members/big5_blue_emb.json') or L('data/members/big5_v2.json') or L('data/members/big5_traits.json') or {}).get('traits', {})
# Preferred Blue supplies source-primary O/C, package-split inverse-antagonism A, the accepted
# text-N criterion, and one half of the externally anchored E estimate.  Public exposure is still
# shown separately and is no longer substituted directly onto the Extraversion axis.
RCPT = (L('member_receptivity.json') or {}).get('members', {})
if not RCPT: print('  !! member_receptivity.json MISSING — psychometrics tab computed section will be empty')
def _pctrank(vals):
    s = sorted(vals); n = max(1, len(s) - 1)
    import bisect
    return lambda v: round(bisect.bisect_left(s, v) / n, 3)
_b5have = {m: t for m, t in B5.items() if t.get('tokens', 0) > 0}
_B5PCT = {tr: _pctrank([t[tr] for t in _b5have.values() if t.get(tr) is not None])
           for tr in ('O', 'C', 'E', 'A', 'ES')} if _b5have else {}
# Archived Pink item-read overlay. It remains display/diagnostic only and never enters an engine.
B5L = (L('data/members/big5_llm_opus.json') or {}).get('traits', {})
_llmhave = {m: t for m, t in B5L.items() if t.get('n_chunks', 0) > 0}
# ES retired 2026-07-12 (user: redundant with N — ES=-N r=-1.00 — + voice-ES gender-confounded).
# Preserve instrument independence on the replacement Neuroticism axis: Blue-N and Pink-N are ranked
# from their OWN text-family scores. The round-7 affective-content score remains a separate,
# text-derived diagnostic candidate; copying it into both polygons would manufacture agreement.
_LLMPCT = {tr: _pctrank([t[tr] for t in _llmhave.values() if t.get(tr) is not None])
           for tr in ('O', 'C', 'E', 'A', 'ES')} if _llmhave else {}
_B5NPCT = _pctrank([t['N'] for t in _b5have.values() if t.get('N') is not None]) if _b5have else None
_LLMNPCT = _pctrank([t['N'] for t in _llmhave.values() if t.get('N') is not None]) if _llmhave else None
_BLUE_FILE = next((os.path.join(B, f) for f in ('data/members/big5_blue_validated.json','data/members/big5_blue_emb.json','data/members/big5_v2.json')
                   if os.path.exists(os.path.join(B, f))), os.path.join(B, 'data', 'members', 'big5_v2.json'))
_BLUE_TS = os.path.getmtime(_BLUE_FILE) if os.path.exists(_BLUE_FILE) else None
_PINK_FILE = os.path.join(B, 'data', 'members', 'big5_llm_opus.json')
_PINK_TS = os.path.getmtime(_PINK_FILE) if os.path.exists(_PINK_FILE) else None
_PINK_OLDER_DAYS = round(max(0.0, (_BLUE_TS - _PINK_TS) / 86400.0), 1) if _BLUE_TS and _PINK_TS else None
_BLUE_DATE = datetime.fromtimestamp(_BLUE_TS).strftime('%Y-%m-%d') if _BLUE_TS else None
_PINK_DATE = datetime.fromtimestamp(_PINK_TS).strftime('%Y-%m-%d') if _PINK_TS else None
_achave = {m: r for m, r in RCPT.items() if r.get('agency') is not None}
_ACPCT = {k: _pctrank([r[k] for r in _achave.values()]) for k in ('agency', 'communion')} if _achave else {}
# ---- external measures (run-4m wiring): CEL/LES effectiveness + DW-NOMINATE (lep_criterion.py),
# Katz-Bonacich centrality (kb_benchmark.py), signed-mention antagonist set (signed_coalitions.py).
# All three are weekly-refresh diagnostics; desk degrades gracefully when any file is absent.
_LEP = (L('data/members/lep_criterion.json') or {}).get('per_member', {})
_KB = (L('data/network/kb_benchmark.json') or {}).get('kb', {})
_ANT = set((L('data/lobbying/signed_coalitions.json') or {}).get('splinter_k2') or [])
_LESVALS = [v['lesclassic'] for v in _LEP.values() if v.get('lesclassic') is not None]
_LESPCT = _pctrank(_LESVALS) if _LESVALS else None
# ---- DISPLAY/ENGINE RECONCILIATION (Paul 2026-08-03: "The display and the engine should be the
# same.")  The desk used to headline classic CEL Legislative Effectiveness (lesclassic) while every
# engine on the page ranks on do_z — CU legislative delivery, z within chamber (say_do_gap.py),
# which reaches flip_model / per_topic_flip / per_topic_coalition / build_dashboard_bundle through
# moneyball_core.delivery_mult. Those two measures disagree systematically because lesclassic is
# PASSAGE-weighted and passage needs the gavel: on the CEL panel 115-118 with contemporaneous
# majority status (n=2,202) majority members average 1.365 vs 0.595 for the minority — 2.29x,
# Cohen d=0.79, rising to 2.87x in the 118th House (see lobbying/coalition_pricing.py for the full working).
# do_z carries no such majority tilt. Gwen Moore is the case Paul named: lesclassic puts her in the
# 7th percentile, the engine's own do_z puts her in the 82nd.
# lesclassic is KEPT, but demoted to a labelled third-party comparison metric — never the headline.
_DOZ = {n: v.get('do_z') for n, v in ((L('say_do_gap.json') or {}).get('members') or {}).items()
        if v.get('do_z') is not None}
_DOZPCT = _pctrank(list(_DOZ.values())) if _DOZ else None
_KBPCT = _pctrank(list(_KB.values())) if _KB else None
def _approach(ap, cp, read):
    """IIT-C octant-informed approach note (Interpersonal Influence Tactics Circumplex: tactics
    arrange on the agency x communion plane; Matz et al. PNAS 2017: matched appeals outperform —
    treated as a tilt, not a lever, per the 2026 meta-analytic caution)."""
    hiA, loA = ap >= 0.60, ap <= 0.40
    hiC, loC = cp >= 0.60, cp <= 0.40
    if hiA and hiC:   note = "Partnership framing: offer a visible leadership role in the cause; collaborative asks land well; let them front the win."
    elif hiA and loC: note = "Lead with outcomes: a short, direct, results-focused brief; let the credit accrue to them; data and wins land better than sentiment."
    elif loA and hiC: note = "Relationship-first: constituent stories and community framing; patient cadence and strong follow-up; open with connection before the ask."
    elif loA and loC: note = "Technical brief: data, evidence, and written material shared in advance; a calm, well-prepared conversation works best."
    elif hiC:         note = "Warmth-forward style: open with shared-community framing, then match their energy in the room."
    elif hiA:         note = "Direct style: be crisp and outcome-led; let the conversation set the tone."
    else:             note = "Mixed interpersonal style: read the room; neither warmth nor directness clearly leads their public persona."
    if read == 'diverge': note += " Note: the behavioral and persona reads differ — best confirmed in person."
    return note

# behavioral dispositions (nomological fix 2026-07-04): the PRIMARY psychometric display —
# measured from behavior (validated by construction), replacing the indicted lexical Big5 radar
# as the headline. Percentile-ranked across covered members per axis.
BDISP = (L('behavioral_dispositions.json') or {}).get('members', {})
# Radar cooperation axis = share-based cooperation_v2 (round-6 engine promotion 2026-07-10,
# matching the engine's A-slot); members without v2 coverage render neutral-with-note, the same
# convention as reactivity — never a silent fallback to the volume-entangled raw-count v1.
_CV2 = {m: (d or {}).get('cooperation_v2_z') for m, d in ((L('coop_v2_candidate.json') or {}).get('members', {})).items()}
# User-promoted display-only conversation roles (2026-07-13). The locked study qualified
# dominance-v2 and expressiveness-v2 as observable roles. They remain barred from every influence/rank
# engine: dominance is also a 10% display-profile E channel, while expressiveness remains speaking style.
_ROLES2_ART = L('data/members/behavioral_roles_v2_candidate.json') or {}
_ROLES2 = _ROLES2_ART.get('members', {})
_COMM_ART = L('communication_style.json') or {}
_COMM = _COMM_ART.get('members', {}) if _COMM_ART.get('schema') == 'communication-style-v1' else {}
_PROFILE_ART = L('data/members/profile_basis_v2.json') or {}
_PROFILE = _PROFILE_ART.get('members', {}) if _PROFILE_ART.get('schema') == 'profile-basis-v2' else {}
_GOLD_ART = L('data/stance/big5_gold_anchored.json') or {}
_GOLD_TRAITS = _GOLD_ART.get('traits') or {}
_GOLD_MEMBERS = {trait: ((row.get('members') or {}) if str(row.get('status') or '').startswith('ACTIVE_') else {})
                 for trait, row in _GOLD_TRAITS.items()}
_E_ART = (_GOLD_TRAITS.get('E') or {})
_E_GOLD = (_E_ART.get('members') or {}) if _E_ART.get('status') == 'ACTIVE_EXTERNAL_GOLD_ANCHORED' else {}
BDR = {m: dict(d) for m, d in BDISP.items()}
for _m, _d in BDR.items():
    if _CV2.get(_m) is not None: _d['cooperation_z'] = _CV2[_m]
    else: _d.pop('cooperation_z', None)
    # Replace the display-only legacy hybrid (interaction + formal leadership) with the qualified
    # conversational measure. Missing interaction evidence remains missing, never neutral/imputed.
    _r2 = _ROLES2.get(_m) or {}
    if _r2.get('dominance_v2') is not None: _d['dominance_z'] = _r2['dominance_v2']
    else: _d.pop('dominance_z', None)
_BD_AX = ['cooperation_z', 'diligence_z', 'dominance_z', 'visibility_z', 'breadth_z']
_BDPCT = {ax: _pctrank([d[ax] for d in BDR.values() if ax in d]) for ax in _BD_AX} if BDR else {}
_EXP2PCT = _pctrank([d['expressiveness_v2'] for d in _ROLES2.values()
                     if d.get('expressiveness_v2') is not None]) if _ROLES2 else None
# Round-9 validated A facet: inverse of controlled, package-split interview antagonism.
# This is display-only and deliberately separate from both the cooperation engine role and
# the two text Big-Five polygons; neither text-A instrument cleared diagonal dominance.
_AVAL_ART = L('data/stance/interview_antagonism.json') or {}
_AVAL = _AVAL_ART.get('members', {})
_AVALPCT = _pctrank([d['agreeableness_z'] for d in _AVAL.values()
                     if d.get('agreeableness_z') is not None]) if _AVAL else None
# Hearing conduct is omitted unless a non-expired, hash-bound feed certifies the
# corrected chamber-scoped attribution contract.  The legacy July artifact and
# V1 hearing_antag table are no longer surface inputs.
_HEARING_DISPLAY = load_hearing_display()
_HANT = (_HEARING_DISPLAY or {}).get('members', {})
_HEARING_APPEARANCES = (_HEARING_DISPLAY or {}).get('appearances', [])
_HANTPCT = _pctrank([d['antagonism_z'] for d in _HANT.values()
                     if d.get('antagonism_z') is not None]) if _HANT else None
# Paul-directed fill (2026-07-17, "fill them" — e.g. Angie Craig): a GUARDED-FALLBACK display layer
# so every member's Big-Five panel renders. Gold-anchored values stay primary; where a slot lacks
# gold coverage the desk shows the full-instrument percentile instead, explicitly captioned as an
# estimate. Sources: text instrument (big5_traits) for E/O/C/A — NEVER the face-invalid broad
# text-N — and the promoted affective-content N artifact for the N slot. Display-only; feeds no
# engine. DESK_B5_FALLBACK=0 is the revert switch (guard-only display, pre-2026-07-17 behavior).
_B5_FALLBACK_ON = os.environ.get('DESK_B5_FALLBACK', '1') != '0'
_AFFN = (L('data/money/affective_n.json') or {}).get('members', {})
_RAWB5 = (L('data/members/big5_traits.json') or {}).get('traits', {})
# Percentile pools for the estimate layer. Blue (the desk's accepted instrument) covers E/O/C for
# all 533, validated package-split A for 322, and the promoted affective-content N for 469. The
# ONLY deeper fallback used is raw text-A (with its mandatory O-halo caveat) — the retired broad
# text-N is NEVER displayed (face-invalid per big5_methods; ledger-binding).
_FB_BLUE_PCT = ({tr: _pctrank([d[tr] for d in B5.values() if d.get(tr) is not None])
                 for tr in ('E', 'O', 'C', 'A', 'N')} if B5 else {})
_AFFNPCT = _pctrank([v for v in _AFFN.values() if v is not None]) if _AFFN else None
_FB_RAW_A_PCT = (_pctrank([d['A'] for d in _RAWB5.values() if d.get('A') is not None])
                 if _RAWB5 else None)
_NARR_PATH = os.path.join(B, 'member_narratives_v2.json')
_NARR_DOC = L('member_narratives_v2.json') or {}
_BASIS_PATH = os.path.join(B, 'data', 'members', 'profile_basis_v2.json')
_BASIS_SHA = hashlib.sha256(open(_BASIS_PATH, 'rb').read()).hexdigest() if os.path.exists(_BASIS_PATH) else None
_NARR_RECEIPT = L('data/members/member_narratives_v2_semantic_receipt.json') or {}
_NARR_DESCRIPTIVE_RECEIPT = L('data/members/member_narratives_v3_descriptive_receipt.json') or {}
_SPEECH_PROFILE_PATH = os.path.join(B, 'data', 'members', 'member_narrative_evidence_v4.json')
_SPEECH_PROFILE_DOC = L('data/members/member_narrative_evidence_v4.json') or {}
_SPEECH_PROFILE_SHA = (hashlib.sha256(open(_SPEECH_PROFILE_PATH, 'rb').read()).hexdigest()
                       if os.path.exists(_SPEECH_PROFILE_PATH) else None)
_SPEECH_PROFILE_ACTIVE = (
    _SPEECH_PROFILE_DOC.get('schema') == 'member-narrative-evidence-v4' and
    _SPEECH_PROFILE_DOC.get('status') == 'ACTIVE_FAIL_CLOSED_INPUT' and
    _SPEECH_PROFILE_DOC.get('basis_sha256') == _BASIS_SHA and
    (_SPEECH_PROFILE_DOC.get('counts') or {}).get('members') == 533 and
    (_SPEECH_PROFILE_DOC.get('counts') or {}).get('speech_pass') == 533 and
    len(_SPEECH_PROFILE_DOC.get('members') or {}) == 533 and
    all(((row.get('speech_profile') or {}).get('status') == 'PASS')
        for row in (_SPEECH_PROFILE_DOC.get('members') or {}).values())
)
_SPEECH_PROFILES = (_SPEECH_PROFILE_DOC.get('members') or {}) if _SPEECH_PROFILE_ACTIVE else {}
if _SPEECH_PROFILE_DOC and not _SPEECH_PROFILE_ACTIVE:
    print('  !! member_narrative_evidence_v4.json failed its 533-member speech-profile contract; '
          'universal speech summaries WITHHELD')
_NARR_PRODUCERS = {
    ('member-narratives-v2-local', 'ACTIVE_LOCAL_ZERO_API'),
    ('member-narratives-v2-api', 'ACTIVE_API_GENERATED_USER_RATIFIED'),
    ('member-narratives-v3-descriptive', 'ACTIVE_GROUNDED_DESCRIPTIVE'),
}


def _district_briefs_for(member_names):
    """Fail closed unless the ratified narrative corpus covers this exact roster."""
    document = DISTRICT_BRIEFS
    if not isinstance(document, dict):
        raise RuntimeError('data/district/district_briefs_v1.json is missing or malformed')
    if document.get('schema') != 'district-demographic-briefs-v1':
        raise RuntimeError('district brief schema is not district-demographic-briefs-v1')
    if document.get('status') != 'RATIFIED_FOR_DASHBOARD':
        raise RuntimeError('district briefs are not ratified for dashboard display')
    briefs = document.get('briefs')
    if not isinstance(briefs, dict):
        raise RuntimeError('district brief artifact has no briefs object')
    expected, actual = set(member_names), set(briefs)
    if actual != expected:
        raise RuntimeError(
            'district brief roster differs from the member desk: missing=%d extras=%d'
            % (len(expected - actual), len(actual - expected)))
    counts = document.get('counts') or {}
    if (counts.get('roster') != len(expected)
            or counts.get('accepted') != len(expected)
            or counts.get('missing') != 0):
        raise RuntimeError('district brief coverage counts are not complete')
    for name in member_names:
        row = briefs[name]
        paragraphs = [row.get('p%d' % index) for index in range(1, 4)] \
            if isinstance(row, dict) else []
        if (not isinstance(row, dict) or row.get('ok') is not True
                or not all(isinstance(value, str) and value.strip()
                           for value in paragraphs)
                or type(row.get('words')) is not int
                or not 200 <= row['words'] <= 250):
            raise RuntimeError('district brief is incomplete or invalid for %s' % name)
    meta = {key: value for key, value in document.items() if key != 'briefs'}
    return briefs, meta
_NARR_LABELLED = (_NARR_DOC.get('schema'), _NARR_DOC.get('status')) in _NARR_PRODUCERS
_NARR_BASIS_OK = _NARR_DOC.get('basis_sha256') == _BASIS_SHA
_NARR_API = _NARR_DOC.get('schema') == 'member-narratives-v2-api'
_NARR_DESCRIPTIVE = _NARR_DOC.get('schema') == 'member-narratives-v3-descriptive'
_NARR_SHA = hashlib.sha256(open(_NARR_PATH, 'rb').read()).hexdigest() if os.path.exists(_NARR_PATH) else None
_NARR_DESCRIPTIVE_ERRORS = (validate_descriptive_profiles(
    _NARR_DOC, _NARR_DESCRIPTIVE_RECEIPT,
    basis_sha256=_BASIS_SHA, evidence_sha256=_SPEECH_PROFILE_SHA,
    active_artifact_sha256=_NARR_SHA,
    member_names=[member['name'] for member in MD['members']],
) if _NARR_DESCRIPTIVE else [])
_NARR_REVIEWED = (not _NARR_API) or (
    _NARR_RECEIPT.get('schema') == 'member-narratives-v2-semantic-receipt-v1' and
    _NARR_RECEIPT.get('status') == 'PASS' and
    _NARR_RECEIPT.get('basis_sha256') == _BASIS_SHA and
    _NARR_RECEIPT.get('evidence_sha256') == _SPEECH_PROFILE_SHA and
    (_NARR_RECEIPT.get('counts') or {}).get('semantic_pass') == 533 and
    _NARR_RECEIPT.get('active_artifact_sha256') == _NARR_SHA)
_NARR_ACTIVE = (_NARR_LABELLED and _NARR_BASIS_OK and _NARR_REVIEWED
                and not _NARR_DESCRIPTIVE_ERRORS)
if _NARR_DOC and not _NARR_ACTIVE:
    # A present-but-rejected artifact must not look like an absent one: otherwise a stale basis
    # or falsely labelled producer can make all 533 briefs disappear without a visible diagnostic.
    if _NARR_LABELLED and not _NARR_BASIS_OK:
        print('  !! member_narratives_v2.json is STALE — basis_sha256=%s but the live '
              'data/members/profile_basis_v2.json hashes to %s; %d briefs WITHHELD' % (
                  _NARR_DOC.get('basis_sha256'), _BASIS_SHA,
                  len(_NARR_DOC.get('members') or {})))
    elif _NARR_DESCRIPTIVE_ERRORS:
        print('  !! Descriptive profiles failed their source, roster, or text contract; '
              '%d briefs WITHHELD: %s' % (len(_NARR_DOC.get('members') or {}),
                                         '; '.join(_NARR_DESCRIPTIVE_ERRORS[:5])))
    elif _NARR_LABELLED and not _NARR_REVIEWED:
        print('  !! API member_narratives_v2.json lacks a matching full-corpus semantic PASS '
              'receipt; %d briefs WITHHELD' % len(_NARR_DOC.get('members') or {}))
    else:
        print('  !! member_narratives_v2.json has an unrecognised provenance pair '
              '(schema=%r status=%r); %d briefs WITHHELD' % (
                  _NARR_DOC.get('schema'), _NARR_DOC.get('status'),
                  len(_NARR_DOC.get('members') or {})))
NARR = (_NARR_DOC.get('members') or {}) if _NARR_ACTIVE else {}
# Voice-ES remains retired. Negative-affect expression is retained below as a literal public-text
# measure and is never presented as holistic Neuroticism or as an independent composure measure.

def _narrative_for_display(nar, descriptive=False, has_audio=False):
    """Keep descriptive profiles free of legacy meeting advice in every surface."""
    out = {key: nar.get(key, '') for key in ('p1', 'p2', 'p3')}
    out['audio'] = bool(has_audio)
    if descriptive:
        out['format'] = 'descriptive-v3'
    else:
        out.update({'meeting_playbook': nar.get('meeting_playbook')})
    return out


def psy_for(name):
    t = B5.get(name) or {}; r = RCPT.get(name) or {}
    out = {}
    bd = BDR.get(name) or {}
    r2 = _ROLES2.get(name) or {}
    comm = _COMM.get(name) or {}
    exposure = comm.get('public_exposure') or {}
    e_public_proxy = exposure.get('role_adjusted_percentile')
    gold = {trait: (_GOLD_MEMBERS.get(trait) or {}).get(name) or {} for trait in ('O','C','E','A','N')}
    e_anchored = gold['E']
    if bd and _BDPCT:
        out['behav_pct'] = [(_BDPCT[ax](bd[ax]) if ax in bd else None) for ax in _BD_AX]
        out['behav_pct'][3] = ((comm.get('facets') or {}).get('activity') or {}).get('percentile')
        out['behavior_scope'] = {
            'dominance_observed': 'dominance_z' in bd,
            'interaction_turns': int(r2.get('qualifying_turns') or 0),
            'roles_v2_display_only': bool(r2),
            'raw_visibility_percentile': exposure.get('observed_visibility_percentile'),
            'role_adjusted_visibility': exposure.get('role_adjusted_percentile'),
        }
        if r2.get('dominance_v2') is not None and r2.get('expressiveness_v2') is not None:
            out['dominance_v2_z'] = r2['dominance_v2']
            out['expressiveness_v2_z'] = r2['expressiveness_v2']
            out['expressiveness_v2_pct'] = _EXP2PCT(r2['expressiveness_v2']) if _EXP2PCT else None
    if comm:
        out['communication_style'] = comm
        out['e_public_proxy_pct'] = e_public_proxy
        out['e_public_proxy_scope'] = ('role-adjusted public exposure context; one 10% anchored-E channel, '
                                       'not the Extraversion score by itself')
    if e_anchored:
        out['e_gold_anchored_pct'] = e_anchored.get('percentile')
        out['e_gold_anchored_scope'] = _E_ART.get('scope')
    out['gold_trait_status'] = {trait: {
        'status': (_GOLD_TRAITS.get(trait) or {}).get('status'),
        'supporting_panels': (_GOLD_TRAITS.get(trait) or {}).get('supporting_panels') or [],
        'strict_discriminant_panels': (_GOLD_TRAITS.get(trait) or {}).get('strict_discriminant_panels') or [],
    } for trait in ('O','C','E','A','N')}
    if name in _AVAL and _AVALPCT and _AVAL[name].get('agreeableness_z') is not None:
        out['a_validated_pct'] = _AVALPCT(_AVAL[name]['agreeableness_z'])
        out['a_antagonism_z'] = _AVAL[name].get('antagonism_z')
        out['a_validated_scope'] = (_AVAL_ART.get('scope') or {}).get('label')
        out['a_coverage_n'] = (_AVAL_ART.get('scope') or {}).get('covered_n')
    if _HANTPCT and (_HANT.get(name) or {}).get('antagonism_z') is not None:
        # separate hearing-based facet (inverse read = low antagonism); percentile of antagonism_z,
        # inverted at display so higher = lower observed hearing antagonism
        out['hearing_antag_pct'] = round(1.0 - _HANTPCT(_HANT[name]['antagonism_z']), 3)
        out['hearing_antag_z'] = _HANT[name]['antagonism_z']
    if any(gold[trait] for trait in gold):
        # Product contract: canonical broad Big-Five order E, O, C, A, N. Every slot now uses the
        # OOF gold artifact; one-panel O/C/N carry their explicit weaker support status.
        out['big5_pct'] = [gold[trait].get('percentile') for trait in ('E','O','C','A','N')]
        out['big5_low'] = bool(t.get('lowconf')); out['tokens'] = t.get('tokens')
    if _B5_FALLBACK_ON:
        # Paul-directed estimate layer (2026-07-17 "fill them"): per-slot fallback percentile +
        # per-slot provenance label; the tpl renders these hatched with an explicit caption and
        # only where the gold-anchored slot is unavailable. Never engine input.
        bl = B5.get(name) or {}
        _fbv = []; _fbs = []
        for tr in ('E', 'O', 'C', 'A', 'N'):
            if tr == 'N' and _AFFNPCT and _AFFN.get(name) is not None:
                # canonical source: the promoted affective_n.json itself (blue's N is its downstream
                # copy and lags until blue rebuilds — e.g. the 2026-07-17 3-member coverage extension)
                _fbv.append(_AFFNPCT(_AFFN[name])); _fbs.append('affective-content N estimate')
            elif bl.get(tr) is not None and _FB_BLUE_PCT:
                _fbv.append(_FB_BLUE_PCT[tr](bl[tr]))
                _fbs.append('affective-content N estimate' if tr == 'N' else
                            ('validated inverse-antagonism A estimate' if tr == 'A' else 'instrument estimate'))
            elif tr == 'A' and _FB_RAW_A_PCT and (_RAWB5.get(name) or {}).get('A') is not None:
                _fbv.append(_FB_RAW_A_PCT(_RAWB5[name]['A']))
                _fbs.append('text-A estimate (O-halo caveat; not discriminant-valid)')
            else:
                _fbv.append(None); _fbs.append(None)
        out['big5_fb_pct'] = _fbv
        out['big5_fb_src'] = _fbs
        out['big5_fb_scope'] = ('estimate layer, display-only (never engine input): shown only where the '
                                'gold-anchored slot is unavailable; the retired broad text-N is never displayed')
    lm = _llmhave.get(name)
    if lm and _LLMPCT:
        out['llm_pct'] = [None] + [_LLMPCT[tr](lm[tr]) for tr in ('O', 'C')] + [
                          _LLMPCT['A'](lm['A']),
                          (_LLMNPCT(lm['N']) if _LLMNPCT and lm.get('N') is not None else None)]
        out['llm_chunks'] = lm.get('n_chunks')
    if out.get('big5_pct') or out.get('llm_pct'):
        out['big5_vintage'] = {'blue': _BLUE_DATE, 'pink': _PINK_DATE,
                               'pink_older_days': _PINK_OLDER_DAYS,
                               'blue_n': len(_b5have), 'pink_n': len(_llmhave),
                               'gold_anchored_n':{trait:len(_GOLD_MEMBERS.get(trait) or {})
                                                  for trait in ('O','C','E','A','N')}}
    if r.get('agency') is not None and _ACPCT:
        out['agency_pct'] = _ACPCT['agency'](r['agency']); out['communion_pct'] = _ACPCT['communion'](r['communion'])
        out['approach'] = _approach(out['agency_pct'], out['communion_pct'], r.get('susc_read'))
    for k in ('pressure_mult', 'susc_z', 'susc_trait_z', 'persona_susc_z', 'susc_read', 'seed_prob', 'basis', 'n_source'):
        v = r.get(MBC.pressure_field()) if k == 'pressure_mult' else r.get(k)   # R1 (Paul ruling 2026-10-07): the value the engines use
        if v is not None: out[k] = v
    lep = _LEP.get(name) or {}
    ext = {}
    if lep.get('followthrough') is not None: ext['ft'] = lep['followthrough']
    if lep.get('lesclassic') is not None and _LESPCT:
        ext['les'] = lep['lesclassic']; ext['les_pct'] = _LESPCT(lep['lesclassic'])
    # the measure the ENGINE actually ranks on — this is the headline effectiveness display
    if name in _DOZ and _DOZPCT:
        ext['do_z'] = round(_DOZ[name], 3); ext['do_pct'] = _DOZPCT(_DOZ[name])
    if lep.get('dwnom1') is not None: ext['dwnom1'] = lep['dwnom1']
    if name in _KB and _KBPCT: ext['kb_pct'] = _KBPCT(_KB[name])
    if name in _ANT: ext['antag'] = 1
    if ext: out['ext'] = ext
    nar = NARR.get(name)
    if nar and nar.get('ok'):
        # Body prose only. Provenance and the construct-separation rule are stated once by the
        # desk template rather than repeated in every member's three paragraphs.
        out['narrative'] = _narrative_for_display(
            nar, descriptive=_NARR_DESCRIPTIVE,
            has_audio=bool((_PROFILE.get(name) or {}).get('verified_audio_v2')))
    else:
        out['narrative_status'] = 'withheld_pending_guarded_local_rebuild_and_user_approval'
    return out or None

CATS = CS['cats']                            # canonical 18 legislative areas
DEFENSIVE = set(CS['defensive'])
SCORES = CS['scores']                        # name -> {cat: 0..1}

# ---------- SNA columnar -> per-name dicts ----------
snames = SNA['name']; sidx = {n:i for i,n in enumerate(snames)}
eng = SNA['engine']
CU_FEC   = eng['cu']                          # actual FEC credit-union PAC $ per member (aggregate.py)
IND_FEC  = eng['ind']                         # bank/finserv $ (the competing money)
PLAY_STR = SNA['play']                        # one char per member: c/g/d/n
EPVAL    = SNA['epval']                       # electoral play value ($)
RANK_SNA = SNA['rank']
PARTY_SNA= SNA['party']                       # one char per member D/R/I/?
ISSUES   = SNA['issues']                      # network issue labels (incl 'Overall CU stance')
STANCE   = SNA['stance']                      # per member, string of tier letters (len == len(ISSUES))
PLAYLBL  = {'c':'Contest · primary','g':'Contest · general','d':'Defend','n':'No active play'}
TIERNAME = {'C':'Champion','F':'Defender','A':'Ally','H':'Highly Persuadable','P':'Persuadable',
            'G':'Lean Persuadable','T':'True Neutral','L':'Lean Skeptic','N':'Neutral','S':'Skeptic',
            'K':'Highly Skeptical','O':'Opponent','-':'—'}
NAME2LETTER = {v: k for k, v in TIERNAME.items()}   # 'Champion'->'C', '—'->'-'  (category_stance matrix uses names)

# cosponsor adjacency (weighted, undirected) from the FULL engine network
def desk_adj(rows):
    """QME 2026-10-01: ONE weight per ordered pair, last row wins -- exactly the browser's `ADJ[u][v]=w;ADJ[v][u]=w`
    and flip_model's name-keyed dict (`adj[a][b]=w; adj[b][a]=w`). The old setdefault(...).append((v, w)) kept BOTH
    rows of a duplicated or reciprocal pair and desk_roi summed them, double-counting that edge's pull."""
    d = {}
    for u, v, w in rows:
        d.setdefault(u, {})[v] = w
        d.setdefault(v, {})[u] = w
    return {u: list(nb.items()) for u, nb in d.items()}
ADJ = desk_adj(eng['adj'])                    # {u: [(v, w), ...]} one entry per neighbour

# ---------- legislators.csv bio enrichment (bioguide -> fields) ----------
LEG = {}
legpath = os.path.join(B, 'fec', 'legislators.csv')
if os.path.exists(legpath):
    with open(legpath, encoding='utf-8') as f:
        r = csv.DictReader(f)
        for row in r:
            bg = row.get('bioguide_id')
            if bg:
                LEG[bg] = row
def nrm(s):  # NFC-normalize so accented names match across model_bio / workbook / member list
    return unicodedata.normalize('NFC', str(s)) if s else ''
BIO_NFC = {nrm(k): v for k, v in BIO.items()}   # NFC(name) -> bioguide
def bio_for(name):
    # ``model_bio.json`` is an older 492-member intermediary.  The current
    # congestion roster already carries the authoritative bioguide for all 533
    # desk members, so use it as a no-guess fallback before joining the richer
    # legislators.csv fields.  This is what restores Steve Scalise and the
    # other sitting members omitted from model_bio without fuzzy name matching.
    bg = BIO.get(name) or BIO_NFC.get(nrm(name)) or _ROSTER_BIO.get(name)
    row = LEG.get(bg, {}) if bg else {}
    out = {'bioguide': bg}
    for k in ('birthday','gender','party','state','type','url','twitter','facebook',
              'youtube','mastodon','contact_form','ballotpedia_id','wikipedia_id',
              'first_name','middle_name','last_name','suffix'):
        v = row.get(k)
        if v: out[k] = v
    wiki = _WIKIPEDIA.get(name) or {}
    if (wiki.get('status') == 'ok' and
            (not bg or not wiki.get('bioguide') or wiki.get('bioguide') == bg)):
        out['wikipedia'] = {
            key: wiki[key] for key in ('url','revision_id','revision_timestamp','summary')
            if wiki.get(key) not in (None, '', {})
        }
        if not out.get('wikipedia_id') and wiki.get('wikipedia_id'):
            out['wikipedia_id'] = wiki['wikipedia_id']
    return out

# ---------- Phase-F industry geo resolution + compact payload ----------
_STATE_USPS = {
    'alabama':'AL','alaska':'AK','arizona':'AZ','arkansas':'AR','california':'CA','colorado':'CO',
    'connecticut':'CT','delaware':'DE','district of columbia':'DC','florida':'FL','georgia':'GA',
    'hawaii':'HI','idaho':'ID','illinois':'IL','indiana':'IN','iowa':'IA','kansas':'KS',
    'kentucky':'KY','louisiana':'LA','maine':'ME','maryland':'MD','massachusetts':'MA',
    'michigan':'MI','minnesota':'MN','mississippi':'MS','missouri':'MO','montana':'MT',
    'nebraska':'NE','nevada':'NV','new hampshire':'NH','new jersey':'NJ','new mexico':'NM',
    'new york':'NY','north carolina':'NC','north dakota':'ND','ohio':'OH','oklahoma':'OK',
    'oregon':'OR','pennsylvania':'PA','rhode island':'RI','south carolina':'SC',
    'south dakota':'SD','tennessee':'TN','texas':'TX','utah':'UT','vermont':'VT',
    'virginia':'VA','washington':'WA','west virginia':'WV','wisconsin':'WI','wyoming':'WY',
    'american samoa':'AS','guam':'GU','northern mariana islands':'MP','puerto rico':'PR',
    'u.s. virgin islands':'VI','us virgin islands':'VI','virgin islands':'VI',
}
def _industry_geo(name, member):
    bg = BIO.get(name) or BIO_NFC.get(nrm(name))
    row = LEG.get(bg) if bg else None
    if row is not None:
        state = str(row.get('state') or '').strip().upper()
        typ = str(row.get('type') or '').strip().lower()
        if not re.fullmatch(r'[A-Z]{2}', state):
            return None
        if typ == 'sen':
            return state
        if typ == 'rep':
            if state == 'DC':
                return state
            try:
                district = int(row.get('district'))
            except (TypeError, ValueError):
                return None
            return '%s-%02d' % (state, district)
        return None

    # Quorum fallback is used only when the legislators.csv bio join lacks this member.
    district_text = str(member.get('district') or '').strip()
    district_match = re.search(r'\bdistrict\s+(\d+)\b', district_text, re.I)
    state_text = district_text.split(',')[-1].strip() if ',' in district_text else district_text
    state = _STATE_USPS.get(state_text.lower())
    if not state:
        return None
    fallback_rep = (str(member.get('type') or '').strip().lower() == 'rep' or
                    str(member.get('chamber') or '').strip().lower() == 'house' or
                    bool(district_match) or
                    bool(re.search(r'\b(delegate|representative|congressional district)\b',
                                   district_text, re.I)))
    if state == 'DC' and fallback_rep:
        return state
    if district_match:
        return '%s-%02d' % (state, int(district_match.group(1)))
    if re.search(r'\bcongressional district\b', district_text, re.I) and re.search(r'\bat[- ]large\b', district_text, re.I):
        return '%s-00' % state
    return state

def _industry_for(name, member):
    if not INDUSTRY:
        return None
    geo = _industry_geo(name, member)
    src = ((INDUSTRY.get('geos') or {}).get(geo) if geo else None)
    if not src:
        return None
    kind = src.get('kind')
    employment = {'total': (src.get('employment') or {}).get('C000')}
    if kind == 'state' and geo != 'DC':
        per_district = src.get('per_district_equiv')
        if per_district and per_district.get('C000') is not None:
            employment['per_district'] = per_district.get('C000')
    out = {
        'geo': geo,
        'kind': kind,
        'latest_year': src.get('latest_year'),
        'window': src.get('window'),
        'employment': employment,
        'makeup': src.get('makeup_pct') or {},
        'lq': src.get('lq_national') or {},
        'gq': src.get('growth_quotient') or {},
        'ss_total': src.get('shift_share_total'),
        'shift_share': src.get('shift_share') or {},
        'cu_metrics': CU_METRICS.for_geo(
            geo, src, INDUSTRY.get('sector_meta') or {}),
        'potential': DP.evaluate(
            geo, src, INDUSTRY4, INDUSTRY.get('sector_meta') or {}),
        'flags': {
            'small': ((src.get('flags') or {}).get('small_sectors') or []),
            'small_base': (
                (src.get('flags') or {}).get('small_base_sectors') or []),
        },
    }
    if kind == 'district':
        out['redrawn'] = bool(src.get('redrawn_since_118'))
    elif kind == 'state' and geo != 'DC':
        out['n_districts'] = src.get('n_voting_districts')
    return out


def _community_for_geo(geo):
    if not isinstance(COMMUNITY, dict) or not geo:
        return None
    collection = (
        'states' if re.fullmatch(r'[A-Z]{2}', str(geo))
        else 'districts'
    )
    source = (COMMUNITY.get(collection) or {}).get(geo)
    if not isinstance(source, dict):
        return None
    return {
        'geo': source.get('geo') or geo,
        'kind': source.get('kind'),
        'name': source.get('name'),
        'state': source.get('state'),
        'region': source.get('region'),
        'vintage': COMMUNITY.get('vintage'),
        'survey': COMMUNITY.get('survey'),
        'congress': source.get('congress'),
        'estimates': dict(source.get('estimates') or {}),
        'moe_90': dict(source.get('moe_90') or {}),
        'derived': dict(source.get('derived') or {}),
        'area': dict(source.get('area') or {}),
        'complete': source.get('complete') is True,
    }


# ---------- Phases G/H district artifacts + senator state aggregates ----------
def _gh_num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float('inf'), float('-inf')):
        return None
    return int(number) if number.is_integer() else number

def _gh_rows(artifact):
    if not isinstance(artifact, dict):
        return {}
    rows = artifact.get('districts')
    return rows if isinstance(rows, dict) else {}

def _gh_state_groups(rows):
    groups = {}
    for geo, row in rows.items():
        match = re.fullmatch(r'([A-Z]{2})-\d{2}', str(geo))
        if match and isinstance(row, dict):
            groups.setdefault(match.group(1), []).append(row)
    return groups

def _gh_ranked(rows, list_key, value_key, limit):
    merged = {}
    labels = {}
    for row in rows:
        values = row.get(list_key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            value = _gh_num(item.get(value_key))
            if not name or value is None:
                continue
            key = name.casefold()
            labels.setdefault(key, name)
            merged[key] = merged.get(key, 0) + value
    ranked = sorted(merged, key=lambda key: (-merged[key], labels[key].casefold()))[:limit]
    return [{'name': labels[key], value_key: _gh_num(merged[key])} for key in ranked]

def _employer_multiple_recipient_count(rows):
    count = 0
    for row in rows.values():
        values = row.get('top_federal') if isinstance(row, dict) else None
        if not isinstance(values, list):
            continue
        count += sum(
            1 for item in values
            if isinstance(item, dict) and
            str(item.get('name') or '').strip().casefold() == 'multiple recipients'
        )
    return count

def _employer_ranked(rows, limit):
    merged = {}
    labels = {}
    for row in rows:
        values = row.get('top_federal')
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            amount = _gh_num(item.get('amount'))
            if not name or name.casefold() == 'multiple recipients' or amount is None:
                continue
            recipient_id = str(item.get('recipient_id') or '').strip()
            key = ('recipient_id', recipient_id.casefold()) if recipient_id else ('name', name.casefold())
            labels.setdefault(key, name)
            merged[key] = merged.get(key, 0) + amount
    ranked = sorted(merged, key=lambda key: (-merged[key], labels[key].casefold()))[:limit]
    return [{'name': labels[key], 'amount': _gh_num(merged[key])} for key in ranked]

def _employers_pack(row):
    if not isinstance(row, dict):
        return None
    top = []
    values = row.get('top_federal')
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            amount = _gh_num(item.get('amount'))
            if name and name.casefold() != 'multiple recipients' and amount is not None:
                top.append({'name': name, 'amount': amount})
    total = ((row.get('large_establishments') or {}).get('total') or {})
    large = {
        'est_500plus': _gh_num(total.get('est_500plus')),
        'est_1000plus': _gh_num(total.get('est_1000plus')),
        'complete': bool(total.get('complete')),
    }
    if not top and large['est_500plus'] is None and large['est_1000plus'] is None:
        return None
    return {'top_federal': top[:5], 'large': large}

def _employer_state_aggregates(rows):
    out = {}
    for state, district_rows in _gh_state_groups(rows).items():
        ranked = _employer_ranked(district_rows, 10)
        est_500plus = est_1000plus = 0
        have_500plus = have_1000plus = False
        complete = True
        for row in district_rows:
            total = ((row.get('large_establishments') or {}).get('total') or {})
            n500 = _gh_num(total.get('est_500plus'))
            n1000 = _gh_num(total.get('est_1000plus'))
            if n500 is not None:
                est_500plus += n500
                have_500plus = True
            if n1000 is not None:
                est_1000plus += n1000
                have_1000plus = True
            complete = complete and total.get('complete') is True
        packed = {
            'top_federal': [{'name': item['name'], 'amount': item['amount']} for item in ranked[:5]],
            'large': {
                'est_500plus': _gh_num(est_500plus) if have_500plus else None,
                'est_1000plus': _gh_num(est_1000plus) if have_1000plus else None,
                'complete': complete,
            },
        }
        if packed['top_federal'] or have_500plus or have_1000plus:
            out[state] = packed
    return out

def _financial_pack(row, state_aggregate=False):
    if not isinstance(row, dict):
        return None
    banks = row.get('banks') if isinstance(row.get('banks'), dict) else {}
    cus = row.get('cus') if isinstance(row.get('cus'), dict) else {}
    ratio = row.get('ratio') if isinstance(row.get('ratio'), dict) else {}
    bank_top = banks.get('top5') if isinstance(banks.get('top5'), list) else []
    cu_top = cus.get('top5') if isinstance(cus.get('top5'), list) else []
    packed = {
        'banks': {
            'n_branches': _gh_num(banks.get('n_branches')),
            'deposits_kusd': _gh_num(banks.get('deposits_kusd')),
            'hhi': None if state_aggregate else _gh_num(banks.get('deposit_hhi')),
            'top1_name': str((bank_top[0] or {}).get('name') or '') if bank_top and isinstance(bank_top[0], dict) else '',
        },
        'cus': {
            'n_branches': _gh_num(cus.get('n_branches')),
            'est_shares_kusd': _gh_num(cus.get('est_shares_kusd')),
            'top1_name': str((cu_top[0] or {}).get('name') or '') if cu_top and isinstance(cu_top[0], dict) else '',
        },
        'cu_branch_share': _gh_num(ratio.get('cu_branch_share')),
    }
    numeric = (packed['banks']['n_branches'], packed['banks']['deposits_kusd'],
               packed['cus']['n_branches'], packed['cus']['est_shares_kusd'],
               packed['cu_branch_share'])
    return packed if any(value is not None for value in numeric) else None

def _financial_state_aggregates(rows):
    out = {}
    for state, district_rows in _gh_state_groups(rows).items():
        bank_branches = bank_deposits = cu_branches = cu_shares = 0
        have_bank_branches = have_bank_deposits = have_cu_branches = have_cu_shares = False
        for row in district_rows:
            banks = row.get('banks') if isinstance(row.get('banks'), dict) else {}
            cus = row.get('cus') if isinstance(row.get('cus'), dict) else {}
            for value, flag in (
                (_gh_num(banks.get('n_branches')), 'bank_branches'),
                (_gh_num(banks.get('deposits_kusd')), 'bank_deposits'),
                (_gh_num(cus.get('n_branches')), 'cu_branches'),
                (_gh_num(cus.get('est_shares_kusd')), 'cu_shares'),
            ):
                if value is None:
                    continue
                if flag == 'bank_branches':
                    bank_branches += value
                    have_bank_branches = True
                elif flag == 'bank_deposits':
                    bank_deposits += value
                    have_bank_deposits = True
                elif flag == 'cu_branches':
                    cu_branches += value
                    have_cu_branches = True
                else:
                    cu_shares += value
                    have_cu_shares = True
        bank_top = _gh_ranked(
            [row.get('banks') or {} for row in district_rows], 'top5', 'deposits_kusd', 5)
        cu_top = _gh_ranked(
            [row.get('cus') or {} for row in district_rows], 'top5', 'est_shares_kusd', 5)
        denominator = bank_branches + cu_branches
        source = {
            'banks': {
                'n_branches': _gh_num(bank_branches) if have_bank_branches else None,
                'deposits_kusd': _gh_num(bank_deposits) if have_bank_deposits else None,
                'deposit_hhi': None,
                'top5': [{'name': item['name'], 'deposits_kusd': item['deposits_kusd']} for item in bank_top],
            },
            'cus': {
                'n_branches': _gh_num(cu_branches) if have_cu_branches else None,
                'est_shares_kusd': _gh_num(cu_shares) if have_cu_shares else None,
                'top5': [{'name': item['name'], 'est_shares_kusd': item['est_shares_kusd']} for item in cu_top],
            },
            'ratio': {
                'cu_branch_share': round(cu_branches / denominator, 6) if denominator else None,
            },
        }
        packed = _financial_pack(source, state_aggregate=True)
        if packed:
            out[state] = packed
    return out

def _hmda_pack(row):
    if not isinstance(row, dict):
        return None
    n_originations = _gh_num(row.get('n_originations'))
    home_purchase_share = _gh_num(row.get('home_purchase_share'))
    lmi_tract_share = _gh_num(row.get('lmi_tract_share'))
    if n_originations is None or home_purchase_share is None or lmi_tract_share is None:
        return None
    return {
        'n_originations': n_originations,
        'home_purchase_share': home_purchase_share,
        'lmi_tract_share': lmi_tract_share,
    }

def _hmda_districts(artifact):
    rows = _gh_rows(artifact)
    if not rows:
        return {}
    packed = {}
    for geo, row in rows.items():
        if not re.fullmatch(r'[A-Z]{2}-\d{2}', str(geo)):
            continue
        value = _hmda_pack(row)
        if value is None:
            return {}
        packed[str(geo)] = value
    return packed

def _hmda_state_aggregates(rows):
    out = {}
    for state, district_rows in _gh_state_groups(rows).items():
        total = sum(row['n_originations'] for row in district_rows)
        if total <= 0:
            continue
        out[state] = {
            'n_originations': _gh_num(total),
            'home_purchase_share': sum(row['n_originations'] * row['home_purchase_share']
                                       for row in district_rows) / total,
            'lmi_tract_share': sum(row['n_originations'] * row['lmi_tract_share']
                                   for row in district_rows) / total,
        }
    return out

def _gh_source_year(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if 'year' in str(key).lower():
                match = re.search(r'(?:19|20)\d{2}', str(child))
                if match:
                    return match.group(0)
        for child in value.values():
            year = _gh_source_year(child)
            if year:
                return year
    elif isinstance(value, list):
        for child in value:
            year = _gh_source_year(child)
            if year:
                return year
    return None

def _gh_metadata(employer_rows, financial_rows, hmda_rows):
    meta = {}
    if employer_rows:
        sources = EMPLOYERS.get('sources') if isinstance(EMPLOYERS.get('sources'), dict) else {}
        usaspending = sources.get('usaspending') if isinstance(sources.get('usaspending'), dict) else {}
        cbp = sources.get('cbp') if isinstance(sources.get('cbp'), dict) else {}
        vintages = []
        if usaspending.get('fy') is not None:
            meta['usaspending_fy'] = str(usaspending.get('fy'))
            vintages.append('USASpending FY%s' % usaspending.get('fy'))
        if cbp.get('year') is not None:
            vintages.append('CBP %s' % cbp.get('year'))
        if vintages:
            meta['employers_vintage'] = '; '.join(vintages)
        method_notes = EMPLOYERS.get('method_notes') if isinstance(EMPLOYERS.get('method_notes'), dict) else {}
        if method_notes.get('usaspending'):
            meta['employer_method'] = str(method_notes['usaspending'])
        if method_notes.get('cbp_large'):
            meta['large_incomplete'] = str(method_notes['cbp_large'])
    if financial_rows:
        sources = FINANCIAL.get('sources') if isinstance(FINANCIAL.get('sources'), dict) else {}
        vintages_raw = FINANCIAL.get('vintages') if isinstance(FINANCIAL.get('vintages'), dict) else {}
        fdic = sources.get('fdic_sod') if isinstance(sources.get('fdic_sod'), dict) else {}
        ncua = sources.get('ncua_call_report') if isinstance(sources.get('ncua_call_report'), dict) else {}
        fdic_year = fdic.get('year', vintages_raw.get('fdic_sod_year'))
        ncua_quarter = ncua.get('quarter', vintages_raw.get('ncua_quarter'))
        vintages = []
        if fdic_year is not None:
            vintages.append('FDIC SOD %s' % fdic_year)
        if ncua_quarter:
            vintages.append('NCUA %s' % ncua_quarter)
        if vintages:
            meta['fin_vintages'] = '; '.join(vintages)
        method_notes = FINANCIAL.get('method_notes') if isinstance(FINANCIAL.get('method_notes'), dict) else {}
        if method_notes.get('sod_hq_deposits'):
            meta['sod_hq_deposits'] = str(method_notes['sod_hq_deposits'])
    if hmda_rows:
        year = _gh_source_year(HMDA.get('sources'))
        if year:
            meta['hmda_year'] = year
    return meta

def _gh_is_dc_pr(geo):
    return bool(geo and (str(geo) in ('DC', 'PR') or str(geo)[:2] in ('DC', 'PR')))

# ---------- state parse (for same-state relational) ----------
def state_of(m):
    d = (m.get('district') or '').strip()
    if not d: return ''
    if ',' in d:        # "Congressional District 4 (119th Congress), Florida"
        return d.split(',')[-1].strip()
    return d            # senators: "California"

# ---------- legacy analytical rank; never used by the active earned-honors display ----------
def category_ranks():
    ranks = {}   # cat -> {name: rank}
    for cat in CATS:
        ordered = sorted(SCORES.keys(), key=lambda n: -(SCORES.get(n, {}).get(cat, 0) or 0))
        ranks[cat] = {n: i+1 for i, n in enumerate(ordered)}
    return ranks
CATRANK = category_ranks()

# ---------- full label -> canonical-area normalization (used for BOTH press articles and bills) ----------
LBL2CAT = {
    'CFPB Issues & Structural Reform':'CFPB','Charter Modernization & Governance':'Charter Modernization',
    'Credit Union Capital Requirements':'CU Capital Requirements','Credit Card & Consumer-Fee Mandates':'Credit Card Mandates',
    'Data Privacy & Cybersecurity':'Data Privacy & Cyber','Defense Credit Union Issues':'Defense CU Issues',
    'Mortgage Lending & Housing':'Mortgage & Housing','NCUA Budget & Share Insurance Fund':'NCUA Budget & Insurance',
    'Technology & Emerging Issues':'Technology & Emerging','Telephone Consumer Protection Act':'TCPA',
    'Telephone Consumer Protection Act Issues':'TCPA',
    # press-only extras (no canonical equivalent — kept as their own bucket):
    'Credit Union Tax Status':'CU Tax Status','CU-Bank Competitive Dynamics':'CU-Bank Competitive Dynamics',
    'General Credit Union':'General Credit Union',
}
def norm_cats(s):
    out = []
    for c in (s or '').split(';'):
        c = c.strip()
        if not c: continue
        c = LBL2CAT.get(c, c)
        if c not in out: out.append(c)
    return out

# ---------- short axis labels for the radars (full labels clip the SVG) ----------
CATS_SHORT = {'Examination & Supervision':'Exam','CU Capital Requirements':'Capital','NCUA Budget & Insurance':'NCUA',
    'Interchange':'Interch.','Payments':'Payments','Technology & Emerging':'Tech','Fraud Prevention':'Fraud',
    'Data Privacy & Cyber':'Privacy','Charter Modernization':'Charter','Defense CU Issues':'Defense',
    'Field of Membership':'FOM','CDFI Fund':'CDFI','CFPB':'CFPB','Credit Card Mandates':'Cards','TCPA':'TCPA',
    'Member Business Lending':'MBL','Mortgage & Housing':'Mortgage'}

# ---------- congress.gov URL for a bill (e.g. 'H.R.2155' @ 119 -> .../119th-congress/house-bill/2155) ----------
_BILLTYPE = {'H.R':'house-bill','S':'senate-bill','H.RES':'house-resolution','S.RES':'senate-resolution',
    'H.J.RES':'house-joint-resolution','S.J.RES':'senate-joint-resolution',
    'H.CON.RES':'house-concurrent-resolution','S.CON.RES':'senate-concurrent-resolution'}
def bill_url(name, congress):
    if not name or not congress: return ''
    m = re.match(r'^([HS][.A-Z]*?)(\d+)$', str(name).upper().replace(' ', ''))
    if not m: return ''
    t = _BILLTYPE.get(m.group(1).rstrip('.'))
    if not t: return ''
    try: c = int(congress)
    except (TypeError, ValueError): return ''
    return 'https://www.congress.gov/bill/%dth-congress/%s/%s' % (c, t, m.group(2))

# ---------- graded sentiment label from a -1..1 score (0.2 steps) ----------
_GRADED = {-1.0:'Very strongly negative',-0.8:'Strongly negative',-0.6:'Negative',-0.4:'Weakly negative',
           -0.2:'Very weakly negative',0.0:'Neutral',0.2:'Very weakly positive',0.4:'Weakly positive',
           0.6:'Positive',0.8:'Strongly positive',1.0:'Very strongly positive'}
def graded_label(g):
    return _GRADED[max(-1.0, min(1.0, round(round(g/0.2)*0.2, 1)))]
def _fsign(x):
    try: x = float(x)
    except (TypeError, ValueError): return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)

# ---------- press articles (by bioguide) + bills with roles (by name), from the Blended workbook ----------
def load_press_and_bills(member_names):
    wb = openpyxl.load_workbook(WB, read_only=True, data_only=True)
    try:
        return _read_press_and_bills(member_names, wb)
    finally:
        wb.close()

def _read_press_and_bills(member_names, wb):
    resolver = Resolver(member_names, BIO)
    def canon(name, bioguide):
        return resolver.resolve(name, bioguide)
    def fdate(d):
        if d is None: return ''
        s = str(d)
        return s[:10] if s[:4].isdigit() else ''                    # datetime/'YYYY-…' -> date; 'NA'/other -> blank
    # --- press ---
    pw = wb['Press Release Articles']
    ph = {h: i for i, h in enumerate(next(pw.iter_rows(min_row=1, max_row=1, values_only=True)))}
    def pv(r, k): return r[ph[k]] if k in ph else None
    arts = {}
    press_source_rows=press_unknown_member=press_billstatus_excluded=0
    exclusion_samples={'press_unknown_member':[],'social_unknown_member':[],
                       'bill_unknown_member':[],'bill_manual_excluded':[]}
    # N1a (2026-09-19, packet audits/laundry_list_closeout_20260918 FINDINGS.md A1): the rows
    # walked below are the UNION of this sheet and the press stream the RANKING ENGINE reads
    # (fec/press_stream.py). No script in the chain writes 'Press Release Articles' — it comes
    # from the external R pipeline — so the desk showed a frozen corpus: 291 engine rows had no
    # sheet row, 221 of them SCORING (receipt laneA/receipts/r9_engine_only_press.out.txt).
    # The helper only SELECTS rows and hands them back in this sheet's own tuple shape, so
    # every rule below — billstatus routing, the stance_src=='bill' sign branch, the
    # press_stance_overrides correction, PDATE recovery, norm_cats, graded_label — runs over an
    # added row exactly as over a sheet row, and the press_source_rows census that
    # dashboard_integrity reconciles still counts every row walked.
    # DESK_PRESS_SOURCE=sheet restores this loop's pre-2026-09-19 input byte for byte.
    _press_rows, _press_source_ledger = DPS.desk_press_rows(
        pw.iter_rows(min_row=2, values_only=True), ph)
    for r in _press_rows:
        press_source_rows += 1
        nm = canon(pv(r, 'member'), pv(r, 'bioguide'))
        if not nm:
            if len(exclusion_samples['press_unknown_member'])<20:
                exclusion_samples['press_unknown_member'].append({'member':pv(r,'member'),'bioguide':pv(r,'bioguide')})
            press_unknown_member += 1; continue
        # SKIP billstatus bill-cosponsorship rows: these are legislation (shown in Legislative actions, from the
        # role sheets), not press — folding them in here gave fake "articles" with dead billstatus: links.
        if str(pv(r, 'url') or '').startswith('billstatus:'):
            press_billstatus_excluded += 1; continue
        # REDESIGNED direction = run_stage5 final_sign: tracked-bill CU-direction if bill-sourced, else DEBATE stance.
        # (The legacy score_label was an orphaned rule-based column; final_sign is what the model's press_value uses.)
        src = pv(r, 'stance_src')
        fsign = _fsign(pv(r, 'score')) if src == 'bill' else _fsign(pv(r, 'stance_sign'))
        mag = abs(float(pv(r, 'score_graded') or 0))
        grd = 0.0 if fsign == 0 else fsign * (mag if mag > 0 else 0.2)
        title = pv(r, 'title') or ''; stance_disp = pv(r, 'stance') or ''
        _c = _press_corr(title, str(pv(r, 'url') or ''))   # manual mislabel correction (press_stance_overrides.json)
        if _c:
            fsign = {'support': 1, 'oppose': -1, 'neutral': 0}.get(_c.get('stance'), fsign)
            grd = 0.0 if fsign == 0 else fsign * (mag if mag > 0 else 0.2)
            stance_disp = _c.get('stance', stance_disp)
        try: sconf = round(float(pv(r, 'stance_conf') or 0), 2)
        except (TypeError, ValueError): sconf = 0
        url = pv(r, 'url') or ''
        date = fdate(pv(r, 'date')); dest = ''
        if not date and url in PDATE:                              # recovered date for an undated item
            ov = PDATE[url]; date = ov.get('date', '')
            dest = 'est' if ov.get('method') == 'wayback-capture' else 'rec'
        arts.setdefault(nm, []).append({
            'date': date, 'dest': dest, 'title': title, 'url': url,
            'cats': norm_cats(pv(r, 'categories')), 'pol': fsign, 'label': graded_label(grd),
            'stance': stance_disp, 'sconf': sconf, 'ssrc': ('corrected' if _c else (src or '')),
            'corr': bool(_c), 'rel': pv(r, 'relevance') or '', 'source': 'press',
            'scored': True, 'exclusion': ''})                     # basis fields for the card
    # --- social (the old desk said "press & social" but loaded press only) ---
    media_audit = {'press_source_rows':press_source_rows,
                   'press_displayed': sum(len(v) for v in arts.values()),
                   'press_unknown_member_excluded':press_unknown_member,
                   'press_billstatus_rows_routed_to_legislation':press_billstatus_excluded,
                   # N1a: which source each walked press row came from, plus the A5 ingest
                   # ledger, so the press census chains from the tone files to this payload.
                   'press_source_ledger':_press_source_ledger,
                   'social_source_rows': 0, 'social_displayed': 0,
                   'social_scored_unique': 0, 'social_duplicate_visible': 0,
                   'social_unverified_handle_excluded': 0,
                   'social_unrelated_excluded': 0, 'social_offtopic_excluded': 0,
                   'social_unknown_member_excluded': 0,
                   'social_exclusions_by_member': {},
                   'bill_source_rows': 0, 'bill_unknown_member_excluded': 0,
                   'bill_rules_excluded': 0, 'bill_override_excluded': 0,
                   'bill_manual_rows': 0, 'bill_manual_excluded': 0,
                   'bill_primacy_membership_rows': 0,
                   'bill_duplicate_rows_collapsed': 0,
                   # adjudicated per-member withdrawals (2026-08-29). These stay 0
                   # when data/legislation/membership_withdrawals.csv is absent, so
                   # the legislation reconciliation term they feed is a no-op then.
                   'bill_withdrawal_rules': 0, 'bill_withdrawn_rows_removed': 0,
                   'bill_withdrawal_rules_already_absent': 0,
                   'exclusion_samples':exclusion_samples}
    seen = {}      # (member, normalized text) -> (platform, date) of the FIRST such item;
                   # the bluesky footprint dedups against these keys, and D4's display note reads the value
    social_path = os.path.join(B, 'Social Master.xlsx')
    stack_path = os.path.join(B, 'data', 'stance', 'tweet_stance_final.csv')
    stack = {}
    if os.path.exists(stack_path):
        for sr in csv.DictReader(open(stack_path, encoding='utf-8-sig', newline='')):
            stack[(sr.get('member') or '', MR.normalized_text(sr.get('text')))] = sr
    if os.path.exists(social_path):
        swb = openpyxl.load_workbook(social_path, read_only=True, data_only=True)
        try:
            sw = swb['Corpus']
            sh = {h: i for i, h in enumerate(next(sw.iter_rows(min_row=1, max_row=1, values_only=True)))}
            tierw = {'unanimous': 1.0, 'majority': 0.7, 'review': 0.35}
            def sv(row, key): return row[sh[key]] if key in sh else None
            for row in sw.iter_rows(min_row=2, values_only=True):
                media_audit['social_source_rows'] += 1
                if str(sv(row, 'verified_handle') or '').strip() != '1':
                    media_audit['social_unverified_handle_excluded'] += 1; continue
                nm = canon(sv(row, 'member'), None)
                if not nm:
                    if len(exclusion_samples['social_unknown_member'])<20:
                        exclusion_samples['social_unknown_member'].append(str(sv(row,'member') or ''))
                    media_audit['social_unknown_member_excluded'] += 1; continue
                text = str(sv(row, 'text') or '').strip()
                hit = stack.get((str(sv(row, 'member') or ''), MR.normalized_text(text)))
                stance = str((hit or {}).get('stack_stance') or sv(row, 'stance') or 'neutral').lower()
                if stance == 'unrelated':
                    media_audit['social_unrelated_excluded'] += 1
                    by_member = media_audit['social_exclusions_by_member'].setdefault(nm, {'unrelated': 0, 'offtopic': 0})
                    by_member['unrelated'] += 1
                    continue
                raw_cats = MR.route_raw_categories(text)
                if not raw_cats:
                    media_audit['social_offtopic_excluded'] += 1
                    by_member = media_audit['social_exclusions_by_member'].setdefault(nm, {'unrelated': 0, 'offtopic': 0})
                    by_member['offtopic'] += 1
                    continue
                # `seen` maps key -> (platform, date) of the FIRST row under that key, so a
                # later cross-post can name where the same words were first published.  The
                # key/seen machinery is unchanged under either dial: it is what the bluesky
                # footprint dedups against below, and what produces the display note here.
                # N2 (2026-09-19): the key is MR.crosspost_key, the WHOLE normalised statement,
                # not MR.normalized_text's first 120 characters -- 445 of 1,434 groups were false
                # collisions on a shared prefix.  Measured effect on this payload: 298 of the 895
                # 'also' notes change (289 of them should never have existed).
                # MR.normalized_text stays the tweet_stance_final join key above, untouched.
                # CROSSPOST_KEY_CHARS=120 restores the old key; CROSSPOST_DEDUP is unchanged.
                key = (nm, MR.crosspost_key(text)); duplicate = key in seen
                plat = str(sv(row, 'platform') or 'social'); pdate = fdate(sv(row, 'date'))
                if not duplicate: seen[key] = (plat, pdate)
                weight = tierw.get(str((hit or {}).get('tier') or '').lower(), 0.5)
                sign = {'support': 1, 'oppose': -1, 'neutral': 0}.get(stance, 0)
                # Both counters stay censuses of the SAME events they always counted, so the
                # media audit reads identically under either dial; `scored` is the gate.
                if duplicate: media_audit['social_duplicate_visible'] += 1
                elif sign: media_audit['social_scored_unique'] += 1
                scored, exclusion, also = social_scoring(sign, key[1], duplicate, seen.get(key))
                art = {
                    'date': pdate, 'dest': '', 'title': text,
                    'url': str(sv(row, 'url') or ''), 'cats': norm_cats(';'.join(raw_cats)),
                    'pol': sign, 'label': graded_label(sign * weight), 'stance': stance,
                    'sconf': weight, 'ssrc': 'stack-social' if hit else 'social-source',
                    'corr': False, 'rel': plat,
                    'source': 'social', 'scored': scored,
                    'exclusion': exclusion}
                # Emitted only when non-empty, so CROSSPOST_DEDUP=1 writes the original keys.
                if also: art['also'] = also
                arts.setdefault(nm, []).append(art)
                media_audit['social_displayed'] += 1
        finally:
            swb.close()
    # --- media footprint: interviews + hearings + bluesky (user-directed 2026-07-16: include them in
    # the per-member Press & social media section). DISPLAY-ONLY appearance/post records read from
    # social_media.db; no CU stance is manufactured for them (stance labels are the user's domain) —
    # items ship scored=False with an explicit reason, and nothing downstream reads them back into the
    # press pillar.  All accepted rows are retained by default; the browser renders them in
    # 50-row pages so a prolific member does not create thousands of DOM nodes at once.
    # Completeness is an invariant: there is no producer-side row cap. The browser paginates in
    # 50-row increments so compact rendering never means omitted evidence. DESK_FOOTPRINT=0 remains
    # the full feature revert switch for emergency diagnosis, but normal production includes all.
    media_totals = {}
    FOOTPRINT_ON = os.environ.get('DESK_FOOTPRINT', '1') != '0'
    MEDIA_CAP = 0
    media_audit.update({
        'footprint_enabled': int(FOOTPRINT_ON), 'footprint_display_cap': MEDIA_CAP,
        'interview_episode_rows': 0, 'interview_appearances_displayed': 0,
        'interview_beyond_display_cap': 0, 'interview_unknown_member_excluded': 0,
        'hearing_appearance_rows': 0, 'hearing_appearances_displayed': 0,
        'hearing_beyond_display_cap': 0, 'hearing_unknown_member_excluded': 0,
        'bluesky_post_rows': 0, 'bluesky_footprint_displayed': 0, 'bluesky_beyond_display_cap': 0,
        'bluesky_unverified_handle_posts_excluded': 0, 'bluesky_unknown_member_excluded': 0,
        'bluesky_empty_text_excluded': 0, 'bluesky_scored_rows_routed_to_social': 0})
    if FOOTPRINT_ON and os.path.exists(os.path.join(B, 'social_media.db')):
        NET = {'transcripts.cnn.com': 'CNN', 'www.cnn.com': 'CNN', 'www.foxnews.com': 'Fox News',
               'foxnews.com': 'Fox News', 'www.ms.now': 'MSNBC / MS NOW', 'ms.now': 'MSNBC / MS NOW',
               'www.msnbc.com': 'MSNBC / MS NOW', 'msnbc.com': 'MSNBC / MS NOW',
               'www.npr.org': 'NPR', 'www.pbs.org': 'PBS',
               'www.c-span.org': 'C-SPAN', 'www.nbcnews.com': 'NBC News', 'www.cbsnews.com': 'CBS News',
               'abcnews.go.com': 'ABC News', 'hughhewitt.com': 'Hugh Hewitt Show', 'www.csis.org': 'CSIS',
               'www.cfr.org': 'CFR', 'www.aspensecurityforum.org': 'Aspen Security Forum'}
        def _net(pkg):
            if '/washington-journal/' in str(pkg or '').lower():
                return 'Washington Journal'
            mm = re.match(r'https?://([^/]+)/', str(pkg or ''))
            d = mm.group(1).lower() if mm else ''
            return NET.get(d, d or 'broadcast')
        def _fp_add(by, key, tag=None):
            # No producer-side cap: all accepted records enter the bundle. Client-side 50-row
            # pagination bounds DOM work without discarding or preferentially sampling evidence.
            shown_total = 0
            for nm, lst in by.items():
                lst.sort(key=lambda a: a['date'], reverse=True)
                shown = lst
                arts.setdefault(nm, []).extend(shown)
                media_totals.setdefault(nm, {})[key] = [len(shown), len(lst)]
                if key == 'iv':
                    # Preserve the outlet census even when a caller deliberately enables the
                    # emergency payload cap.  The UI uses this to expose Fox, MSNBC/MS NOW,
                    # Washington Journal, and every other collected interview source directly.
                    _source_counts = {}
                    for _item in lst:
                        _label = _item.get('network') or _item.get('rel') or 'Other interview source'
                        _source_counts[_label] = _source_counts.get(_label, 0) + 1
                    media_totals[nm]['iv_sources'] = dict(sorted(
                        _source_counts.items(), key=lambda pair: (-pair[1], pair[0].casefold())))
                shown_total += len(shown)
                if tag and STANCE_ON:
                    # tally AFTER the cap, so matched can never exceed displayed
                    media_audit[tag + '_rows_stance_matched'] += sum(1 for a in shown if a.get('gstance'))
                    media_audit[tag + '_rows_snippet_matched'] += sum(1 for a in shown if a.get('snip'))
            return shown_total
        # --- graded CU stance + transcript passage on footprint rows (user-directed 2026-07-19).
        # Interview and hearing appearances are ALREADY stance-graded (interview_stance_final.csv /
        # hearing_stance_final.csv) and already carry a sentence-snapped CU passage from
        # cu_passages.py (interviews_cu.csv / hearings_cu.csv); both channels feed the press pillar
        # via build_media_blend.py. This DISPLAYS those existing labels beside the appearance record —
        # it never manufactures one (stance labels are the user's domain). A row with no graded
        # counterpart keeps its prior scored=False + reason verbatim. Coverage is genuinely partial
        # (interviews 25 members, hearings 72), and hearing footprint rows come from hearing_antag
        # while hearing stance/text come from speeches source='CHRG' — so most hearing rows will not
        # match. The match counters below disclose that rather than letting a sparse tab read as
        # "no stance". DESK_MEDIA_STANCE=0 reverts to the pre-2026-07-19 rendering exactly.
        STANCE_ON = os.environ.get('DESK_MEDIA_STANCE', '1') != '0'
        def _grade_index(stance_csv, text_csv, src, con):
            """(canon member, YYYY-MM-DD, package) -> {stance, tier, snip, n}.

            The stance CSV and the passage CSV are row-aligned 1:1 (verified 2026-07-19:
            bioguide+member+date+categories agree on every row of both pairs), so each passage is
            paired with ITS OWN grade. Nothing is aggregated across a date and no label outside the
            source vocabulary is ever synthesised - the quote and the stance beside it always
            describe the same words.

            The CU artifacts carry no package column, but a member can have several packages on one
            date (one interview date carries 40), so a date-level join would paste a passage onto
            every sibling appearance. The passage text disambiguates: it is a window over one
            package's transcript, so probing a distinctive slice against each candidate pins the
            right one (measured 2026-07-19: 775/775 hearing and 36/37 interview passages resolve to
            exactly one package). The one ambiguous passage attaches to both rather than guessing.

            Returns (index, drops) - drops counts every passage the index refused, by reason.
            """
            def rows(fn):
                fp = os.path.join(B, fn)
                if not os.path.exists(fp):
                    print('  desk media stance: %s absent - that channel stays ungraded' % fn)
                    return []
                with open(fp, encoding='utf-8-sig', newline='') as fh:
                    return list(csv.DictReader(fh))
            def _norm(s): return ' '.join(str(s or '').split()).lower()
            stance_rows, text_rows = rows(stance_csv), rows(text_csv)
            drops = {'unaligned': 0, 'ungraded': 0, 'unpinned': 0}
            if len(stance_rows) != len(text_rows):
                print('  desk media stance: %s/%s row counts differ (%d vs %d) - channel skipped'
                      % (stance_csv, text_csv, len(stance_rows), len(text_rows)))
                return {}, drops
            out = {}
            for srow, trow in zip(stance_rows, text_rows):
                # the pairing is positional, so verify it row by row rather than trusting it
                if ((srow.get('bioguide') or '') != (trow.get('bioguide') or '')
                        or fdate(srow.get('date')) != fdate(trow.get('date'))):
                    drops['unaligned'] += 1
                    continue
                stance = (srow.get('stance') or '').strip()
                text = (trow.get('text') or '').strip()
                nm = canon(trow.get('member'), trow.get('bioguide'))
                if not nm or not text or not stance:
                    drops['ungraded'] += 1
                    continue
                day = fdate(trow.get('date'))
                cands = list(con.execute(
                    "SELECT package, GROUP_CONCAT(text,' ') FROM speeches "
                    "WHERE source=? AND member=? AND substr(date,1,10)=? GROUP BY package",
                    (src, trow.get('member'), day)))
                norm = _norm(text)
                probe = norm[len(norm) // 3:len(norm) // 3 + 60]
                pkgs = [p for p, t in cands if probe and probe in _norm(t)]
                if not pkgs:
                    drops['unpinned'] += 1
                    continue
                for pkg in pkgs:
                    slot = out.setdefault((nm, day, pkg),
                                          {'stance': '', 'tier': '', 'snip': '', 'n': 0})
                    slot['n'] += 1
                    # Keep the FIRST passage pinned to this package, with ITS OWN grade, so the
                    # quote and the label always describe the same words. First, not longest:
                    # cu_passages emits ~500-char windows (half=250, median 543) and merges
                    # overlapping ones, so the longest passage is a merged blob -- for a committee
                    # chair it is the transcript appendix absorbed into their final turn
                    # ("This hearing is adjourned. [Whereupon...] [Prepared statements...]").
                    # Since the template clips to 400 chars, longest-wins displayed the opening of
                    # the least representative passage. First = the earliest CU mention, which is a
                    # natural single window of the member actually speaking.
                    if not slot['snip']:
                        slot['snip'] = text
                        slot['stance'] = stance
                        slot['tier'] = (srow.get('tier') or '').strip()
            if any(drops.values()):
                print('  desk media stance: %s passages dropped - %r' % (src, drops))
            return out, drops
        # counters are added ONLY when the feature is on, so DESK_MEDIA_STANCE=0 leaves the
        # serialized media_audit dict byte-identical to the pre-2026-07-19 bundle
        if STANCE_ON:
            media_audit.update({
                'media_stance_enabled': 1,
                'interview_rows_stance_matched': 0, 'interview_rows_snippet_matched': 0,
                'hearing_rows_stance_matched': 0, 'hearing_rows_snippet_matched': 0})
        _grade_consumed = set()   # (id(index), nm, date, package) actually attached to a display row
        def _apply_grade(item, index, nm, pkg):
            """Attach an EXISTING grade/passage to a display row; no-op when none exists.
            Counting happens in _fp_add, after the per-member display cap."""
            grade = index.get((nm, item['date'], pkg))
            if not grade:
                return item
            _grade_consumed.add((id(index), nm, item['date'], pkg))
            item['gstance'], item['gtier'], item['gn'] = grade['stance'], grade['tier'], grade['n']
            if grade['snip']:
                item['snip'] = grade['snip']
            return item
        fcon = sqlite3.connect(os.path.join(B, 'social_media.db'), timeout=60)
        try:
            # built here (not above) because pinning a passage to its package reads the speeches table
            IV_GRADE, HR_GRADE = {}, {}
            if STANCE_ON:
                IV_GRADE, _ivdrop = _grade_index('data/stance/interview_stance_final.csv', 'data/stance/interviews_cu.csv',
                                                 'INTERVIEW', fcon)
                HR_GRADE, _hrdrop = _grade_index('data/stance/hearing_stance_final.csv', 'data/stance/hearings_cu.csv',
                                                 'CHRG', fcon)
                # every drop path is persisted, not just printed - a regression in the pairing or the
                # package probe must leave a trace in the integrity report, not only in scrollback
                media_audit.update({'interview_graded_keys': len(IV_GRADE),
                                    'hearing_graded_keys': len(HR_GRADE)})
                for _tag, _d in (('interview', _ivdrop), ('hearing', _hrdrop)):
                    for _why, _n in _d.items():
                        media_audit['%s_passages_dropped_%s' % (_tag, _why)] = _n
            # interviews: one item per broadcast episode (member x date x transcript page)
            iv_by = {}
            for mem, dt, pkg, turns, words in fcon.execute(
                    "SELECT member, date, package, COUNT(*), COALESCE(SUM(nwords),0) FROM speeches "
                    "WHERE source='INTERVIEW' GROUP BY member, date, package"):
                media_audit['interview_episode_rows'] += 1
                nm = canon(mem, None)
                if not nm:
                    media_audit['interview_unknown_member_excluded'] += 1; continue
                net = _net(pkg)
                iv_by.setdefault(nm, []).append(_apply_grade({
                    'date': fdate(dt),
                    'title': '%s appearance — %d segment%s · %s words' % (
                        net, turns, '' if turns == 1 else 's', format(int(words or 0), ',')),
                    'url': pkg if str(pkg or '').startswith('http') else '',
                    'rel': net, 'network': net, 'source': 'interview', 'scored': False,
                    'exclusion': 'appearance record'},
                    IV_GRADE, nm, pkg))
            media_audit['interview_appearances_displayed'] = _fp_add(iv_by, 'iv', 'interview')
            media_audit['interview_beyond_display_cap'] = (media_audit['interview_episode_rows']
                - media_audit['interview_unknown_member_excluded'] - media_audit['interview_appearances_displayed'])
            # hearings: one item per accepted, hash-bound hearing appearance.  Never
            # query the stale legacy hearing_antag table at display time.
            hr_by = {}
            for _appearance in _HEARING_APPEARANCES:
                mem = _appearance.get('member')
                dt = _appearance.get('date')
                pkg = _appearance.get('package')
                title = _appearance.get('title') or ''
                turns = int(_appearance.get('turns') or 0)
                media_audit['hearing_appearance_rows'] += 1
                nm = canon(mem, None)
                if not nm:
                    media_audit['hearing_unknown_member_excluded'] += 1; continue
                ch = 'Senate' if 'shrg' in str(pkg or '') else ('House' if 'hhrg' in str(pkg or '') else 'Joint')
                hr_by.setdefault(nm, []).append(_apply_grade({
                    'date': fdate(dt), 'title': (title or '').strip() or str(pkg or ''),
                    'url': ('https://www.govinfo.gov/app/details/' + pkg) if pkg else '',
                    'rel': '%s hearing · %d speaking turn%s' % (ch, turns or 0, '' if (turns or 0) == 1 else 's'),
                    'source': 'hearing', 'scored': False, 'exclusion': 'appearance record'},
                    HR_GRADE, nm, pkg))
            # A graded hearing passage can have NO hearing_antag row to hang on: hearing_antag records
            # the antagonism pass, not every speaker, so a committee chair (Mike Crapo at Senate Banking
            # 2019, e.g.) speaks and is graded while owning no antag row. Measured 2026-07-19: 191 of 372
            # graded hearing keys land this way. Emitting them from the CU side is the only way the
            # passage reaches the desk; there is no duplicate risk because these packages are precisely
            # the ones hearing_antag has no row for.
            if STANCE_ON:
                media_audit['hearing_graded_rows_added'] = 0
                _hmeta = dict(fcon.execute("SELECT package, COALESCE(title,'') FROM hearing_meta"))
                for (_nm, _day, _pkg), _g in HR_GRADE.items():
                    if (id(HR_GRADE), _nm, _day, _pkg) in _grade_consumed:
                        continue
                    _ch = ('Senate' if 'shrg' in str(_pkg or '') else
                           'House' if 'hhrg' in str(_pkg or '') else 'Joint')
                    hr_by.setdefault(_nm, []).append({
                        'date': _day, 'title': (_hmeta.get(_pkg) or '').strip() or str(_pkg or ''),
                        'url': ('https://www.govinfo.gov/app/details/' + _pkg) if _pkg else '',
                        'rel': '%s hearing · graded transcript' % _ch,
                        'source': 'hearing', 'scored': False, 'exclusion': 'appearance record',
                        'gstance': _g['stance'], 'gtier': _g['tier'], 'gn': _g['n'], 'snip': _g['snip']})
                    _grade_consumed.add((id(HR_GRADE), _nm, _day, _pkg))
                    media_audit['hearing_graded_rows_added'] += 1
            media_audit['hearing_appearances_displayed'] = _fp_add(hr_by, 'hr', 'hearing')
            media_audit['hearing_beyond_display_cap'] = (media_audit['hearing_appearance_rows']
                + media_audit.get('hearing_graded_rows_added', 0)
                - media_audit['hearing_unknown_member_excluded'] - media_audit['hearing_appearances_displayed'])
            # bluesky: the member's own posts from USER-VERIFIED handles only (the look-alike rule from
            # build_social_master stands — unverified-handle posts stay withheld until the user confirms).
            # CU-relevant scored posts already arrive via Social Master above (deduped here); the
            # remainder is unscored footprint.
            _bio2name = {m['bioguide']: m['member'] for m in (L('data/engine/congestion.json') or []) if m.get('bioguide')}
            handles = {bg: (h or '') for bg, h in fcon.execute('SELECT bioguide, bluesky FROM members')}
            bs_by = {}
            for bg, dt, tx, pid in fcon.execute(
                    "SELECT bioguide, created_at, text, post_id FROM posts WHERE platform='bluesky'"):
                media_audit['bluesky_post_rows'] += 1
                h = handles.get(bg, '')
                if not h or h == 'none-found' or 'UNVERIFIED' in h:
                    media_audit['bluesky_unverified_handle_posts_excluded'] += 1; continue
                nm = canon(_bio2name.get(bg) or '', bg)
                if not nm:
                    media_audit['bluesky_unknown_member_excluded'] += 1; continue
                tx = (tx or '').strip()
                if not tx:
                    media_audit['bluesky_empty_text_excluded'] += 1; continue
                # `seen` is keyed by MR.crosspost_key (N2), so its only other reader keys the
                # same way or this lookup silently stops matching.
                if (nm, MR.crosspost_key(tx)) in seen:
                    media_audit['bluesky_scored_rows_routed_to_social'] += 1; continue
                bs_by.setdefault(nm, []).append({
                    'date': fdate(dt), 'title': tx,
                    'url': 'https://bsky.app/profile/%s/post/%s' % (h.split()[0], (pid or '').split('/')[-1]),
                    'rel': 'bluesky', 'source': 'bluesky', 'scored': False,
                    'exclusion': 'no CU relevance detected — footprint only'})
            media_audit['bluesky_footprint_displayed'] = _fp_add(bs_by, 'bs')
            media_audit['bluesky_beyond_display_cap'] = (media_audit['bluesky_post_rows']
                - media_audit['bluesky_unverified_handle_posts_excluded'] - media_audit['bluesky_unknown_member_excluded']
                - media_audit['bluesky_empty_text_excluded'] - media_audit['bluesky_scored_rows_routed_to_social']
                - media_audit['bluesky_footprint_displayed'])
            # a pending (unconfirmed) bluesky handle is itself intel — disclose it on the member's section
            for bg, h in handles.items():
                nm = _bio2name.get(bg)
                if nm and 'UNVERIFIED' in (h or ''):
                    media_totals.setdefault(nm, {})['bs_pending'] = 1
        finally:
            fcon.close()
        print('media footprint: %d interview / %d hearing / %d bluesky items displayed (cap %d/member/source); '
              'beyond-cap %d/%d/%d; %d bluesky posts excluded pending handle verification' % (
              media_audit['interview_appearances_displayed'], media_audit['hearing_appearances_displayed'],
              media_audit['bluesky_footprint_displayed'], MEDIA_CAP,
              media_audit['interview_beyond_display_cap'], media_audit['hearing_beyond_display_cap'],
              media_audit['bluesky_beyond_display_cap'], media_audit['bluesky_unverified_handle_posts_excluded']))
        if STANCE_ON:
            # coverage is partial by construction - print it so a sparse tab is never mistaken for "no stance"
            for _tag, _idx in (('interview', IV_GRADE), ('hearing', HR_GRADE)):
                # a graded passage that reaches no display row would be silently invisible; both
                # channels currently place every key, and this counter is the tripwire if that changes
                media_audit[_tag + '_graded_keys_unplaced'] = sum(
                    1 for k in _idx if (id(_idx),) + k not in _grade_consumed)
            print('desk media stance: interview %d/%d rows carry a graded stance (%d with passage) '
                  'from %d graded keys, %d unplaced; hearing %d/%d rows (%d with passage) '
                  'from %d graded keys, %d unplaced' % (
                  media_audit['interview_rows_stance_matched'], media_audit['interview_appearances_displayed'],
                  media_audit['interview_rows_snippet_matched'], media_audit['interview_graded_keys'],
                  media_audit['interview_graded_keys_unplaced'],
                  media_audit['hearing_rows_stance_matched'], media_audit['hearing_appearances_displayed'],
                  media_audit['hearing_rows_snippet_matched'], media_audit['hearing_graded_keys'],
                  media_audit['hearing_graded_keys_unplaced']))
    for nm in arts: arts[nm].sort(key=lambda a: a['date'], reverse=True)
    # --- bills (sponsor / original-cosponsor / cosponsor) ---
    # DROP non-CU bills the keyword scraper miscategorised (audit 2026-06-29). Curated by SUBJECT, not acronym:
    # the financial "SMART Act" (Supervisory Modifications…) and CLARITY/crypto bills are KEPT; the auto-repair
    # "SMART Act" (Save Money on Auto Repair…) and policing/broadband/general-tech false positives are dropped.
    # NDAA bills are intentionally restored; ambiguous SMART rows are pinned by bill + Congress in bill_rules.py.
    DROP_BILLS = BR.EXCLUDED_TITLE_PHRASES
    ROLEMAP = {'Sponsor Interactions': 'sponsor', 'Original Co-Sponsor Interaction': 'original',
               'Co-Sponsor Interactions': 'cosponsor'}
    BILL_OVERRIDES = BR.load_overrides(os.path.join(B, 'data', 'legislation', 'bill_overrides.csv'))
    bills = {}
    for sheet, role in ROLEMAP.items():
        for r in wb[sheet].iter_rows(min_row=2, values_only=True):
            media_audit['bill_source_rows'] += 1
            nm = canon(r[2], None)
            if not nm:
                if len(exclusion_samples['bill_unknown_member'])<20:
                    exclusion_samples['bill_unknown_member'].append(str(r[2] or ''))
                media_audit['bill_unknown_member_excluded'] += 1; continue
            title = str(r[4] or '')
            sm = str(r[12] or '')
            # match title AND summary: e.g. the auto-repair bill's short title is just "SMART Act" (collides with
            # the financial SMART Act) — only its summary "Save Money on Auto Repair…" disambiguates it.
            if BR.excluded_bill(r[3],r[9],title,sm):
                media_audit['bill_rules_excluded'] += 1; continue
            ov = BR.override_for(BILL_OVERRIDES,r[3],r[9])
            if ov and not BR.override_included(ov):
                media_audit['bill_override_excluded'] += 1; continue
            area = (LBL2CAT.get(r[0], r[0]) if r[0] else '')
            if ov and ov.get('Category'): area = BR.canonical_category(ov['Category']) or area
            bills.setdefault(nm, []).append({
                'role': role, 'area': area,
                'bill': r[3] or '', 'title': r[4] or '', 'sponsor': r[5] or '',
                'ncos': r[6] or 0, 'congress': r[9] or '', 'action': r[8] or '',
                'summary': sm[:220] + ('…' if len(sm) > 220 else ''),
                'url': bill_url(r[3], r[9])})
    manual_bills = os.path.join(B, 'manual_bill_evidence.csv')
    if os.path.exists(manual_bills):
        for r in csv.DictReader(open(manual_bills, encoding='utf-8-sig')):
            media_audit['bill_manual_rows'] += 1
            nm = canon(r.get('Member'), None)
            if not nm or BR.excluded_bill(r.get('Bill'),r.get('Congress'),r.get('Title'),r.get('Summary')):
                media_audit['bill_manual_excluded'] += 1
                if len(exclusion_samples['bill_manual_excluded'])<20:
                    exclusion_samples['bill_manual_excluded'].append({'member':r.get('Member'),'bill':r.get('Bill')})
                continue
            raw_role = str(r.get('Role') or '').lower()
            role = 'sponsor' if raw_role == 'sponsor' else ('original' if 'original' in raw_role else 'cosponsor')
            area = BR.canonical_category(r.get('Category')) or str(r.get('Category') or '')
            sm = str(r.get('Summary') or '')
            bills.setdefault(nm, []).append({
                'role': role, 'area': area, 'bill': r.get('Bill') or '', 'title': r.get('Title') or '',
                'sponsor': r.get('Sponsor') or '', 'ncos': 0, 'congress': r.get('Congress') or '',
                'action': r.get('LastAction') or '', 'summary': sm[:220] + ('…' if len(sm) > 220 else ''),
                'url': r.get('DetailLink') or bill_url(r.get('Bill'), r.get('Congress'))})
    # The adjudicated primacy universe contains amendment sponsors that do not
    # have a sponsor/cosponsor worksheet row. Preserve those evidence-bearing
    # memberships in a dedicated sidecar instead of fabricating workbook rows.
    # The normal collapse below makes this additive and idempotent when an
    # amendment later arrives through the workbook or manual evidence file.
    primacy_membership_files = (
        'data/members/primacy_act_memberships.ndjson',
        'data/members/registry_anchor_memberships.ndjson',
        'data/members/bank_anchor_memberships.ndjson',
    )
    for primacy_membership_name in primacy_membership_files:
      primacy_memberships = os.path.join(B, primacy_membership_name)
      if os.path.exists(primacy_memberships):
        with open(primacy_memberships, encoding='utf-8') as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                r = json.loads(line)
                media_audit['bill_primacy_membership_rows'] += 1
                nm = canon(r.get('member'), None)
                if not nm:
                    raise ValueError(
                        'primacy membership line %d has an unknown member: %r'
                        % (line_no, r.get('member'))
                    )
                bill = str(r.get('bill') or '').strip()
                congress = str(r.get('congress') or '').strip()
                role = str(r.get('role') or '').strip().lower()
                if not bill or not congress or role not in (
                        'sponsor', 'original', 'cosponsor'):
                    raise ValueError(
                        'primacy membership line %d has an invalid act/role'
                        % line_no
                    )
                areas = r.get('areas') or ['']
                if not isinstance(areas, list):
                    raise ValueError(
                        'primacy membership line %d areas must be a list'
                        % line_no
                    )
                sm = str(r.get('summary') or '')
                for area in areas or ['']:
                    bills.setdefault(nm, []).append({
                        'role': role,
                        'area': (
                            BR.canonical_category(area)
                            or str(area or '')
                        ),
                        'bill': bill,
                        'title': r.get('title') or '',
                        'sponsor': r.get('sponsor') or nm,
                        'ncos': r.get('ncos') or 0,
                        'congress': congress,
                        'action': r.get('action') or '',
                        'summary': (
                            sm[:220] + ('…' if len(sm) > 220 else '')
                        ),
                        'url': r.get('url') or bill_url(bill, congress),
                    })
    # FOURTH membership sidecar (2026-08-29, Paul: "Wire the cosponsor joins into the ledger, it
    # needs to self-update ... anything the collectors ingest needs to be wired directly to the
    # ledger, it will go through a verification process first"). The weekly Congress.gov/GovInfo
    # BILLSTATUS refresh was DISPLAY-ONLY (bundle['bill_live']); a member who joined a tracked bill
    # after the workbook was last hand-maintained never earned ledger credit and the primacy factor
    # never moved. verify_collector_roster_updates.py is that verification gate: fail-closed on the
    # refresh receipt and closed to acts THIS ledger already carries. The validated cumulative
    # path projects every accepted role, including sponsors that still match the current official
    # repository; the legacy delta path remains original/cosponsor-only. Neither path widens the
    # act universe past bill_beneficiary_tags.ndjson, which primacy_factors.main:466 asserts on.
    # Read receipt-bound cumulative accepted history by default. The verifier's
    # per-refresh delta alone loses joins as soon as the desk absorbs them.
    # Current official identity, admissions, exclusions, role corrections and
    # withdrawals govern historical evidence before the final role collapse.
    _membership_rel = 'data/members/collector_roster_updates.ndjson'
    _membership_path = os.path.join(B, 'data', 'members', 'collector_roster_updates.ndjson')
    _membership_note = ''
    _membership_temp = None
    _verified_sponsors = None
    if DESK_MEMBERSHIP_SOURCE == 'cumulative':
        import tempfile
        _membership_rel = 'data/members/sentinel_verified_membership_history.ndjson'
        _history_path = os.path.join(
            B, 'data', 'members', 'sentinel_verified_membership_history.ndjson')
        _history_rows, _history_binding = load_verified_membership_history(
            B, repository_snapshot=BILL_LIVE)
        media_audit['bill_membership_history'] = _history_binding
        _shaped = shape_cumulative_membership_rows(
            _history_rows, roles=('sponsor', 'original', 'cosponsor'))
        # BILL_LIVE has already passed the repository/receipt gate. Historical
        # sponsor identity alone cannot restore someone the current source changed.
        _verified_sponsors = {
            (key, sponsor['bioguide'])
            for key, bill in BILL_LIVE.get('bills', {}).items()
            for sponsor in [bill.get('sponsor') or {}]
            if sponsor.get('bioguide') and not sponsor.get('withdrawn_date')
        }
        _membership_note = (' [%d verified history membership(s), %d shaped]'
                            % (len(_history_rows), len(_shaped)))
        _fd, _membership_temp = tempfile.mkstemp(
            suffix='.ndjson', prefix='desk_membership_cumulative_')
        with os.fdopen(_fd, 'w', encoding='utf-8', newline='\n') as _handle:
            for _row in _shaped:
                _handle.write(json.dumps(_row, sort_keys=True, ensure_ascii=False) + '\n')
        _membership_path = _membership_temp
    try:
        _collector_audit = CRU.merge_collector_roster_updates(
            _membership_path,
            bills, canon, verified_sponsors=_verified_sponsors,
            exclude=lambda _b, _c, _t, _s: bool(BR.excluded_bill(_b, _c, _t, _s)) or bool(
                (lambda _ov: _ov and not BR.override_included(_ov))(
                    BR.override_for(BILL_OVERRIDES, _b, _c))))
    finally:
        if _membership_temp:
            try:
                os.remove(_membership_temp)
            except OSError:
                pass
    print('  desk membership source: %s <- %s: %d row(s) read, %d merged, %d ledger row(s) '
          'appended, %d skipped%s'
          % (DESK_MEMBERSHIP_SOURCE, _membership_rel,
             _collector_audit['collector_roster_lines'],
             _collector_audit['collector_roster_merged'],
             _collector_audit['collector_roster_appended'],
             _collector_audit['collector_roster_skipped'], _membership_note))
    from legislation import sentinel_admissions as SA
    _admissions=SA.load(B)
    _admission_replaced=SA.remove_managed_desk_rows(bills,_admissions)
    _admission_rows=SA.append_to_desk(bills,_admissions)
    from legislation import official_membership_movements as OM
    _movement_removed=OM.apply_to_desk(bills,OM.load(B))+_admission_replaced
    media_audit['bill_withdrawn_rows_removed']=_movement_removed
    # Declared source rows for the dashboard_integrity legislation reconciliation: appended LEDGER
    # rows (what the collapse sees, one per area) plus lines that carried an explicit skip reason.
    media_audit['bill_collector_roster_rows'] = (
        _collector_audit['collector_roster_appended']
        + _collector_audit['collector_roster_skipped'] + _admission_rows)
    media_audit['bill_collector_roster_excluded'] = _collector_audit['collector_roster_skipped']
    media_audit['bill_collector_roster_detail'] = _collector_audit
    # --- ADJUDICATED PER-MEMBER WITHDRAWALS (2026-08-29) ---
    # BILLSTATUS reports a cosponsor who withdrew via sponsorshipWithdrawnDate; the
    # ledger must not carry that membership. No existing mechanism could say so:
    # bill_overrides.csv is keyed (bill, congress) only (bill_rules.load_overrides),
    # so Include=0 would drop the act for EVERY member. See bill_rules for why this
    # is a separate artifact rather than a column on manual_bill_evidence.csv.
    # This pass runs LAST -- after the workbook sheets, manual evidence, the three
    # adjudicated primacy sidecars AND the collector merge -- so no source can
    # re-introduce a membership the adjudication removed. With the file absent the
    # loop below is skipped entirely and the ledger is byte-identical to before.
    _withdrawals = BR.load_membership_withdrawals(
        os.path.join(B, 'data', 'legislation', 'membership_withdrawals.csv'))
    media_audit['bill_withdrawal_rules'] = len(_withdrawals)
    if _withdrawals:
        _suppress = {}
        for _w in _withdrawals:
            _nm = canon(_w['member'], _w['bioguide'] or None)
            if not _nm:
                # fail CLOSED: an adjudication naming nobody on the roster is a
                # typo, and silently ignoring it would leave the withdrawn row in.
                raise ValueError(
                    'membership withdrawal row %d names an unresolvable member: %r'
                    % (_w['row'], _w['member']))
            _suppress.setdefault((_nm, _w['key']), 0)
        _removed = 0
        for _nm in list(bills):
            _kept = []
            for _row in bills[_nm]:
                _k = (_nm, BR.withdrawal_act_key(_row['bill'], _row['congress']))
                if _k in _suppress:
                    _suppress[_k] += 1
                    _removed += 1
                    continue
                _kept.append(_row)
            bills[_nm] = _kept
        media_audit['bill_withdrawn_rows_removed'] = _removed + _movement_removed
        # A rule matching nothing is the CORRECT steady state once applied (and is
        # the only state for a withdrawn join the ledger never carried), so it is
        # counted and printed rather than raised.
        media_audit['bill_withdrawal_rules_already_absent'] = sum(
            1 for _count in _suppress.values() if not _count)
        print('  bills: %d adjudicated withdrawal(s) removed %d pre-collapse ledger '
              'row(s); %d rule(s) already absent from the ledger'
              % (len(_withdrawals), _removed,
                 media_audit['bill_withdrawal_rules_already_absent']))
    # collapse duplicate rows: an original cosponsor is ALSO listed as a plain cosponsor (1890 cases), and a bill
    # can map to several CU topics (1455) -> one row per (bill, congress) with the most specific role
    # (sponsor>original>cosponsor) and all its topic areas merged into a tag list. Distinct bill numbers (incl.
    # genuine same-session re-introductions, e.g. S.2545/S.3575) stay separate.
    order = {'sponsor': 0, 'original': 1, 'cosponsor': 2}
    before_collapse = sum(len(v) for v in bills.values())
    for nm in list(bills):
        groups = {}
        for b in bills[nm]:
            k = (b['bill'], str(b['congress']))
            g = groups.get(k)
            if g is None:
                g = {kk: vv for kk, vv in b.items() if kk != 'area'}; g['areas'] = []; groups[k] = g
            if order.get(b['role'], 9) < order.get(g['role'], 9): g['role'] = b['role']
            if b['area'] and b['area'] not in g['areas']: g['areas'].append(b['area'])
        for g in groups.values(): g['areas'].sort()
        bills[nm] = sorted(groups.values(),
            key=lambda b: (order.get(b['role'], 9), str(b['congress']), b['areas'][0] if b['areas'] else ''))
    media_audit['bill_duplicate_rows_collapsed'] = before_collapse - sum(len(v) for v in bills.values())
    # --- committee positions (FULL set incl Ways & Means / Finance / JCT, from the Committee Data sheet;
    #     the old member-desk committee field only had Financial Services/Small Business/Banking) ---
    cmts = {}
    cds = wb['Committee Data']
    for r in cds.iter_rows(min_row=2, values_only=True):
        nm = canon(r[0], None)
        if not nm: continue
        seats = []
        for part in str(r[4] or '').split('|'):
            part = part.strip()
            m = re.match(r'^(.*?)\s*\[([^\]]+)\]$', part)
            if not m: continue
            tag = m.group(2).strip().split('/'); nme = m.group(1).strip(); rel = tag[0]
            # 2026-07-04 user directive: Small Business IS a highly relevant committee (SBA lending,
            # MBL) — display the sheet's own [Highly] tier verbatim; the old 'Moderately' unify-override is retired.
            seats.append({'name': nme, 'rel': rel, 'role': (tag[1] if len(tag) > 1 else '')})
        cmts[nm] = {'score': r[1], 'seats': seats}
    # --- workbook Blended Model rank/score/pillars — kept ONLY as the last-resort fallback (2026-07-06):
    #     the desk now sources rank/score/pillars/weights from ranks_python.json (the authority). These
    #     workbook cells go stale to None after any openpyxl save and used a different normalization than
    #     the Python authority, which is exactly what made Crapo's #1 look unjustified on the desk. The
    #     live weighting is 0.45*Leg% + 0.10*Press% + 0.40*Influence% + 0.05*Demo% (ab_weights).
    #     Press sentiment is active at a deliberately diminished weight. ---
    wbm = {}
    bmsh = wb['Blended Model']
    for r in bmsh.iter_rows(min_row=3, max_row=535, values_only=True):
        nm = canon(r[0], None)
        if not nm: continue
        wbm[nm] = {'rank': r[19], 'score': r[18],                       # col20 Rank, col19 Score
                   'press_n': r[8], 'leg_n': r[15], 'comm_n': r[17], 'demo_n': r[12],   # Press%, Legislative%, Influence%, Demo%
                   'press_raw': r[2], 'leg_raw': r[14], 'comm_raw': r[4], 'demo_raw': r[5]}  # raw inputs
    media_audit['bills_displayed_unique'] = sum(len(v) for v in bills.values())
    return arts, bills, cmts, wbm, media_audit, media_totals

# ---------- marginal winnability ROI: expected WEIGHTED flips gained from the NEXT $10k (one PAC cycle-max) ----------
# Faithful port of the in-browser flip engine. A member's winnability contribution on issue i is tg_i[u]*pf_i[u],
# pf = a/(a+b) with a's money term = WM*(cu+1)/((cu+1)+HALFF*rcp). We use a FINITE DIFFERENCE over a realistic
# $10k increment rather than the analytic derivative: dpf -> pf(cu+10k) - pf(cu). The derivative is singular for
# no-signal members (no network pressure AND ~no money -> a+b->0), which spuriously dominates; the finite
# difference is bounded by tg (the member's engagement x persuadability x influence weight), so it naturally
# downweights members the model knows nothing about and rewards genuinely movable, under-funded targets.
# 2026-09-30 (Paul: "F1. Yes", "F4. Proceed"): the unit is weighted expected flips (sum over issues of target
# value x delta pflip), never co-sponsors. The camp letters match flip_model's PRO/ANTI (Highly Skeptical 'K'
# is anti, as in flip_model and the JS CAMP 'OSK'; this port used to count it neutral), and a member behind
# the system-level stance gate that keeps optimize.py from funding them gets ROI 0 here too. Every input is a
# parameter so fec/tests/surfaces/test_desk_efficiency_metrics.py can lift this function with ast and check
# it against flip_model's own code on a fixture.
CHUNK = 10000.0
def desk_roi(eng, stance, tiername, adj, party, gated=frozenset(), chunk=10000.0):
    K = eng['const']; PERS = eng['pers']
    cu, ind, rcp, tierp, bpf, ENG = eng['cu'], eng['ind'], eng['rcp'], eng['tierp'], eng['bpf'], eng['eng']
    PRIORITY = eng.get('priority') or [tierp for _ in ENG]
    pmult=eng.get('pmult') or [1.0]*len(cu); dmult=eng.get('dmult') or [1.0]*len(cu)
    cmult=eng.get('cmult') or [1.0]*len(cu)                    # COMPET_MULT (absent/dial OFF -> exactly 1.0 = the old arithmetic)
    WM, WP, HALFF, BASE = K['WM'], K['WP'], K['HALFF'], K['BASE']
    SRCPRO, SRCANTI = K['SRCPRO'], K['SRCANTI']
    Nn = len(stance)
    # QME 2026-10-01: set membership, not `t in 'CFA'` (a substring test: '' and 'CF' matched). Same for single-char tokens.
    DR, PRO, ANTI = frozenset('DR'), frozenset('CFA'), frozenset('OSK')
    def align(u, v):
        pu, pv = party[u], party[v]
        if pu == pv and pu in DR: return SRCPRO
        if pu != pv and pu in DR and pv in DR: return SRCANTI
        return 1.0
    def camp(t): return 'pro' if t in PRO else ('anti' if t in ANTI else 'neu')   # = flip_model PRO / ANTI
    def pf(cuA, indA, nAu, mA, nBu, mB, c, pm, cm=1.0):
        au = WM * pm * (cm * cuA + 1) / ((cm * cuA + 1) + c) + WP * nAu / mA
        bu = WM * pm * (cm * indA + 1) / ((cm * indA + 1) + c) + WP * nBu / mB
        return au / (au + bu) if (au + bu) > 0 else 0.0
    roi = [0.0] * Nn
    for i in range(len(ENG) - 1):                              # the real issues (exclude the appended 'Overall')
        tiers = [stance[u][i] for u in range(Nn)]
        nA = [0.0] * Nn; nB = [0.0] * Nn
        for u in range(Nn):
            a = b = 0.0
            for v, w in adj.get(u, []):
                c = camp(tiers[v])
                if c == 'neu': continue
                wc = w * align(u, v)
                if c == 'pro': a += wc
                else: b += wc
            nA[u] = a; nB[u] = b
        # 2026-10-01 (Paul: "Match backend"): normalise by the REAL peer maximum, with 1 only when it is 0
        # (an empty camp), exactly flip_model.topic_setup's `max(nA.values()) or 1`. The old max(1.0, ...)
        # floor shrank the peer term wherever a camp's strongest pull was below 1.
        mA = max(nA) or 1.0; mB = max(nB) or 1.0
        engi = ENG[i]
        priorityi = PRIORITY[i] if i < len(PRIORITY) else tierp
        for u in range(Nn):
            if u in gated: continue                            # F1: optimize.py never funds a stance-gated member
            c = HALFF * rcp[u]
            pers = PERS.get(tiername.get(tiers[u], '—'), 0) or 0
            if pers <= 0: continue                             # no persuadability weight -> no winnability to move
            tg = max(0.0, engi[u] + BASE * priorityi[u]) * pers * (1 + 0.3 * bpf[u]) * dmult[u]
            d = pf(cu[u] + chunk, ind[u], nA[u], mA, nB[u], mB, c, pmult[u], cmult[u]) - pf(cu[u], ind[u], nA[u], mA, nB[u], mB, c, pmult[u], cmult[u])
            roi[u] += tg * d
    return roi                                                 # expected weighted flips gained from the next $10k
def desk_eff_values(scores, invested, percentiles):
    """eff_value = pct(score) - pct(invested), 3 dp, with tie-aware MIDRANK percentiles (None -> 0).
    Members tied on a value share one percentile, so the result depends on the values alone, never on row
    order. `percentiles` is district.demographic_normalization.midrank_percentiles (the demo radar's rule)."""
    sp = percentiles([(i, v or 0) for i, v in enumerate(scores)])
    ip = percentiles([(i, v or 0) for i, v in enumerate(invested)])
    return [round(sp[i] - ip[i], 3) for i in range(len(scores))]
def desk_plan_fields(planned):
    """(opt_alloc, gap) for one member. optimize.py measures its PMAX headroom ABOVE existing CU money, so its
    alloc is already the increment the plan adds; gap is that increment, never negative. It used to be
    opt_alloc - invested, which subtracted all existing CU money from an increment (2026-09-20 data: Sherman
    read -$25,500 while the plan adds $10,000; 72 of 389 funded members read gap <= 0)."""
    oa = round(planned or 0)
    return oa, max(0, oa)
def desk_plan_for(opt_alloc, name):
    """(opt_alloc, gap) for a desk row, looked up BY NAME in optimize_result's alloc (QME 2026-10-01: test seam)."""
    return desk_plan_fields(opt_alloc.get(name, 0))
def desk_roi_for(roi, si):
    """rec['roi']: ROI[sna_idx] to 4 dp, 0 for a desk row with no SNA index (QME 2026-10-01: test seam)."""
    return round(roi[si], 4) if si is not None else 0
def desk_opt_reconcile(opt_alloc, desk_names):
    """QME 2026-10-01: optimizer dollars the desk cannot place. OPT_ALLOC.get(rec['name'], 0) silently zeroed any
    optimizer name that matched no desk row. Returns (matched$, total$, {name: $ unmatched}); the caller prints and
    raises, because the desk must show every planned dollar."""
    names = set(desk_names)
    total = sum(round(v or 0) for v in opt_alloc.values())
    unmatched = {n: round(v or 0) for n, v in opt_alloc.items() if n not in names and round(v or 0)}
    return total - sum(unmatched.values()), total, unmatched
# F1: the gate optimize.py applies -- same kernel helper, over the category_stance.json this build reads.
_GATE_TIERS = MBC.stance_gate_tiers(CS, snames)
ROI_GATED = frozenset(i for i, n in enumerate(snames) if _GATE_TIERS.get(n) in MBC.STANCE_GATE_BLOCKED)
ROI = desk_roi(eng, STANCE, TIERNAME, ADJ, PARTY_SNA, gated=ROI_GATED, chunk=CHUNK)
print('  ROI stance gate: %d members (overall %s) get ROI 0, as optimize.py funds none of them' %
      (len(ROI_GATED), '/'.join(sorted(MBC.STANCE_GATE_BLOCKED))))

# ---------- build merged per-member records ----------
SDIR=L('data/members/staff_directory.json') or {}   # Quorum staffer directory (build_staff_directory.py)
# run-4r (user: "have the interactions been logged... industry-relevant highlighted... I am not
# seeing anything"): (1) DURABLE interaction state — the desk's "Export staff state" button downloads
# staff_state.json; drop it in the Melange Systems folder and every build bakes it in (gold/contacted,
# meeting, notes, star toggles survive browsers, regenerations, machines; localStorage still overlays
# for live edits). (2) Committee-aware relevance: the title-regex star covered only 1% of staffers, so
# policy-role staff (LD/LA/counsel/policy) in offices holding a TRACKED-COMMITTEE seat now star too.
STFSTATE = L('staff_state.json') or L('staff_interactions.json') or {}
if STFSTATE: print('  staff interaction state: %d staffers baked from staff_state.json' % len(STFSTATE))
_POLICY_RE = re.compile(r'legislative (director|assistant|counsel|aide)|deputy legislative|senior policy'
                        r'|policy (advisor|adviser|director|counsel|analyst)|chief counsel|general counsel|tax counsel', re.I)
def attach_honors_display(bundle, honors):
    """Attach one validated recognition generation without changing model inputs.

    None is returned by load_honors only for the explicit legacy rollback mode.
    The browser uses these shared records for badges, order, and rank intervals;
    radar/adjrank/stances remain analytical compatibility data.
    """
    if honors is None:
        return bundle
    if honors.get('overall_policy', {}).get('id') != 'overall_primacy_standing_v2':
        raise RuntimeError('member desk requires the overall behavioral standing policy')
    overall_label = os.environ.get('OVERALL_LABEL', 'analytic').strip().lower()
    if overall_label not in ('analytic', 'legacy'):
        raise RuntimeError('Unknown OVERALL_LABEL: ' + overall_label)
    expected_names = {member['name'] for member in bundle['members']}
    if set(honors['members']) != expected_names:
        raise RuntimeError('member desk honors roster differs from the displayed roster')
    if set(honors['cats']) != set(bundle['cats']):
        raise RuntimeError('member desk honors categories differ from displayed categories')
    for member in bundle['members']:
        row = honors['members'][member['name']]
        if set(row['fields']) != set(bundle['cats']):
            raise RuntimeError('member desk honors fields incomplete for ' + member['name'])
        if (type(row['overall'].get('priority_tier')) is not int
                or row['overall']['priority_tier'] not in (0, 1, 2, 3)
                or not row['overall'].get('stance')):
            raise RuntimeError('member desk overall priority/standing incomplete for ' + member['name'])
        member['honors'] = {
            'member_id': row['member_id'],
            'overall': compact_honor_cell(row['overall']),
            'fields': {cat: compact_honor_cell(cell)
                       for cat, cell in row['fields'].items()},
        }
        if overall_label != 'legacy':
            # One overall label on every surface. The primacy-rank standing the
            # honors generation computes stays in the immutable artifact for
            # audit; what the desk shows is the analytic band already published
            # as overall_stance, so the two overall renderers cannot disagree.
            # overall_stance is a tier LETTER here; TIERNAME is the same map the
            # page uses. An absent analytic band reads as insufficient evidence
            # rather than an em dash, which is not in the standing vocabulary.
            label = TIERNAME.get(member['overall_stance'], '—')
            member['honors']['overall']['stance'] = (
                'Insufficient evidence' if label == '—' else label)
            member['honors']['overall']['stance_reason'] = 'analytic_overall_band'
    bundle['rank_honors'] = {
        key: honors[key] for key in (
            'schema', 'generation_id', 'policy', 'as_of', 'evidence_window',
            'overall_board', 'overall_policy',
        )
    }
    if honors.get('measure_titles') is not None:
        # Titles are shared once per generation, never copied onto every action.
        bundle['rank_honors']['measure_titles'] = honors['measure_titles']
    return bundle


def honors_generation_meta(bundle):
    """A small page marker lets combined/export builders reject mixed releases."""
    from html import escape
    honors = bundle.get('rank_honors')
    if not honors:
        return ''
    return '<meta name="melange-honors-generation" content="%s">' % escape(
        honors['generation_id'], quote=True,
    )


def build_bundle():
    analytic_inputs_only = '--analytic-inputs-only' in sys.argv
    if analytic_inputs_only and '--data' not in sys.argv:
        raise RuntimeError('--analytic-inputs-only requires --data; it cannot publish a public page')
    # Primacy needs this analytical bundle before it can produce the board that
    # honors validate. The dedicated internal stage does not render a page.
    honors = None if analytic_inputs_only else load_honors(B)
    member_names = [m['name'] for m in MD['members']]
    district_briefs, district_briefs_meta = _district_briefs_for(member_names)
    _tier, _trank, _tier_detail = legislative_press_tiers(
        member_names, with_detail=True
    )
    preflight = DI.preflight(member_names, CS, SNA, ARCH)
    ARTS, BILLS, CMTS, WBM, LOAD_AUDIT, MTOT = load_press_and_bills(member_names)
    employer_rows = _gh_rows(EMPLOYERS)
    financial_rows = _gh_rows(FINANCIAL)
    hmda_rows = _hmda_districts(HMDA)
    employer_states = _employer_state_aggregates(employer_rows)
    financial_states = _financial_state_aggregates(financial_rows)
    hmda_states = _hmda_state_aggregates(hmda_rows)
    employer_multiple_recipient_skipped = _employer_multiple_recipient_count(employer_rows)
    gh_meta = _gh_metadata(employer_rows, financial_rows, hmda_rows)
    gh_counts = {
        key: {'attached': 0, 'absent': 0, 'dc_pr': 0}
        for key, rows in (('employers', employer_rows), ('financial', financial_rows), ('hmda', hmda_rows))
        if rows
    }
    def staff_pack(name):
        oncmte = bool((CMTS.get(name) or {}).get('seats'))
        lst = []
        for sf in SDIR.get(name, []):
            # Legacy Quorum exports once carried direct email/phone fields. The
            # member desk is a public surface, so contact fields are stripped at
            # this final boundary even when an old local directory is supplied.
            sf = {k: v for k, v in dict(sf).items() if k not in {'e', 'p', 'email', 'phone'}}
            st = STFSTATE.get(str(sf.get('id')))
            if st: sf['st'] = st
            if sf.get('star'):
                sf['why'] = 'portfolio title (banking/financial/tax/housing/econ)'
            elif oncmte and _POLICY_RE.search(sf.get('t') or ''):
                sf['star'] = 1; sf['why'] = 'policy staff in a tracked-committee office'
            lst.append(sf)
        # industry-relevant first, then contacted, then the rest
        lst.sort(key=lambda s: (not s.get('star'), not (s.get('st') or {}).get('gold'), s.get('n') or ''))
        return lst
    members = []
    industry_attached = industry_unresolved = 0
    community_attached = community_unresolved = 0
    potential_qualified = 0
    for m in MD['members']:
        name = m['name']
        i = sidx.get(name)
        sc = SCORES.get(name, {})
        # legislative radar values (0..1) in canonical category order
        radar = [round(sc.get(c, 0) or 0, 3) for c in CATS]
        # adjusted rank per category
        adjr = {c: CATRANK[c].get(name) for c in CATS}
        # cosponsor partners (top by weight) — names + weights
        partners = []
        if i is not None and i in ADJ:
            for v, w in sorted(ADJ[i], key=lambda t: -t[1])[:12]:
                partners.append({'name': snames[v], 'w': round(w, 2),
                                 'party': PARTY_SNA[v], 'rank': RA_RANK.get(snames[v]) or WBM.get(snames[v], {}).get('rank')})
        arts = ARTS.get(name, [])            # full press DB, grouped by member (every article they're named in)
        bills = BILLS.get(name, [])          # sponsor / original-cosponsor / cosponsor, with subject area
        # chamber/party are often blank in the source -> derive
        dist = m.get('district','')
        chamber = m.get('chamber') or ('House' if re.search(r'district|\d', dist, re.I) else 'Senate')
        party = m.get('party') or ({'D':'Democrat','R':'Republican','I':'Independent'}
                                   .get(PARTY_SNA[i] if i is not None else '?', ''))
        industry = _industry_for(name, m)
        if INDUSTRY:
            if industry is None: industry_unresolved += 1
            else: industry_attached += 1
        demographic_potential = (
            industry.get('potential') if industry else {
                'qualified': False,
                'label': DP.LABEL,
                'signals': [],
                'reason': 'industry_unavailable',
            })
        if demographic_potential.get('qualified'):
            potential_qualified += 1
        gh_geo = _industry_geo(name, m)
        community = _community_for_geo(gh_geo)
        if COMMUNITY:
            if community is None:
                community_unresolved += 1
            else:
                community_attached += 1
        senator_aggregate = chamber == 'Senate' and bool(re.fullmatch(r'[A-Z]{2}', str(gh_geo or '')))
        house_district = (chamber == 'House' and
                          bool(re.fullmatch(r'[A-Z]{2}-\d{2}', str(gh_geo or ''))) and
                          not _gh_is_dc_pr(gh_geo))
        if senator_aggregate:
            employers = employer_states.get(gh_geo)
            financial = financial_states.get(gh_geo)
            hmda = hmda_states.get(gh_geo)
        elif house_district:
            employers = _employers_pack(employer_rows.get(gh_geo))
            financial = _financial_pack(financial_rows.get(gh_geo))
            hmda = hmda_rows.get(gh_geo)
        else:
            employers = financial = hmda = None
        for key, value in (('employers', employers), ('financial', financial), ('hmda', hmda)):
            if key not in gh_counts:
                continue
            if value is None:
                gh_counts[key]['absent'] += 1
            else:
                gh_counts[key]['attached'] += 1
            if _gh_is_dc_pr(gh_geo):
                gh_counts[key]['dc_pr'] += 1
        wbr = WBM.get(name, {})   # authoritative Blended Model rank/score/pillars (counts Ways & Means)
        population_normalization = DN.senate_population_basis(
            (m.get('demo') or {}).get('constituents'),
            chamber,
            gh_geo,
        )
        rec = {
            'name': name,
            'district': dist,
            'chamber': chamber,
            'party': party,
            # general page  (rank/score from ranks_python.json, the phase-2 BLENDED rank authority; workbook fallback)
            'rank': RA_RANK.get(name) or wbr.get('rank', m.get('rank')),
            'score': (RA_SCORE.get(name) if RA_SCORE.get(name) is not None else wbr.get('score', m.get('score'))),
            'tier': _tier.get(name, 0),          # primacy tier (1/2/3, 0=untiered); display-only
            'tierrank': _trank.get(name, 0),     # 1-based primacy-board rank
            'tier_detail': _tier_detail.get(name),
            'alloc': m.get('alloc', 0),            # model-suggested allocation
            'invested': CU_FEC[i] if i is not None else 0,   # ACTUAL FEC credit-union $
            'ind_money': round((_ROSTER_MONEY.get(name,{}).get('banks') or 0)+(_ROSTER_MONEY.get(name,{}).get('finserv') or 0)),
            'money_source_status': _ROSTER_MONEY.get(name,{}).get('money_source_status','LEGACY_SOURCE'),
            'ev_play': PLAYLBL.get(PLAY_STR[i],'') if i is not None else '',
            'ev_play_code': PLAY_STR[i] if i is not None else 'n',
            'ev_value': EPVAL[i] if i is not None else 0,    # electoral-vulnerability suggested $
            'overall_stance': (STANCE[i][-1] if i is not None else '-'),
            'radar': radar,
            'adjrank': adjr,
            'sna_idx': i,
            # pillars — FROM THE RANK AUTHORITY (ranks_python.json pcts), the exact percentiles that
            # compute the score; comm_n = Influence% (E/max, committee_score, W&M-aware). Workbook
            # value only if the authority is missing this member (should never happen post-chain).
            'press_n': (RA_PCTS.get(name) or {}).get('press', wbr.get('press_n', m.get('press_n'))),
            'leg_n':   (RA_PCTS.get(name) or {}).get('leg',   wbr.get('leg_n',   m.get('leg_n'))),
            'comm_n':  (RA_PCTS.get(name) or {}).get('inf',   wbr.get('comm_n',  m.get('comm_n'))),
            'demo_n':  (RA_PCTS.get(name) or {}).get('dem',   wbr.get('demo_n',  m.get('demo_n'))),
            # raw inputs behind each percentile (press pillar value; O/E/F snapshot for leg/inf/demo)
            'press_raw': RA_PRESSP.get(name, wbr.get('press_raw', m.get('press_raw'))),
            'leg_raw':   RA_OEF['leg'].get(name,  wbr.get('leg_raw',  m.get('leg_raw'))),
            'comm_raw':  RA_OEF['comm'].get(name, wbr.get('comm_raw', m.get('comm_raw'))),
            'comm_base_raw': RA_INF_BASE.get(name, RA_OEF['comm'].get(name, wbr.get('comm_raw', m.get('comm_raw')))),
            'formal_role_influence_nominal': RA_ROLE_NOMINAL.get(name, 0.0),
            'formal_role_influence_bonus': RA_ROLE_BONUS.get(name, 0.0),
            'formal_role_display_weight': RA_ROLE_DISPLAY.get(name, 0.0),
            'formal_role_influence_office': RA_ROLE_OFFICE.get(name, ''),
            'formal_role_influence_eligibility': RA_ROLE_ELIGIBILITY.get(name, 'not_listed'),
            'demo_raw':  RA_OEF['demo'].get(name, wbr.get('demo_raw', m.get('demo_raw'))),
            'coalition': m.get('coalition'),
            # per-legislative-area stance tiers (Champion..Opponent), aligned to CATS; '-' = no position
            'stances': ''.join(NAME2LETTER.get((CS['matrix'].get(name, {}) or {}).get(cat, '—'), '-') for cat in CATS),
            # say-do gate (2026-07-07): CATS indices with a 'Proven' badge (sponsored bill in the
            # category advanced past introduction) and indices capped Champion/Defender->Ally for
            # lacking a sponsored bill (raw tier preserved in category_stance.json's gate block)
            'proven': [i for i, cat in enumerate(CATS) if cat in ((CS.get('gate') or {}).get('proven', {}).get(name) or [])],
            'gcap':   [i for i, cat in enumerate(CATS) if cat in ((CS.get('gate') or {}).get('capped', {}).get(name) or {})],
            'overall_say_do': ((CS.get('gate') or {}).get('overall_capped', {}).get(name) or None),
            'committee': m.get('committee'),
            # full committee positions (incl Ways & Means / Finance / JCT) from the workbook Committee Data sheet
            'committees': CMTS.get(name, {}).get('seats', []),
            'committee_score': CMTS.get(name, {}).get('score'),
            # 7 sections
            'staffdir': staff_pack(name),                          # Quorum directory + baked interaction state + committee-aware stars (run-4r)
            'staff': MANUAL.get(name, {}).get('staff'),            # manual
            'bio': bio_for(name),                                  # model_bio + legislators.csv
            'bio_manual': MANUAL.get(name, {}).get('bio'),         # manual addendum
            # Raw catch-net items remain byte-for-byte values; display_dedupe is an
            # auditable list of repeated-card indices consumed only by personalHTML.
            'personal': _personal_for_display(name, _PERSONAL.get(name)),
            'state': state_of(m),
            'partners': partners,                                  # relational: cosponsor ties
            'refs': refs_for(name),                                # signed cross-media references (out/in, Fable-valenced)
            'demo': m.get('demo'),                                 # demographics
            'demo_population_normalization': population_normalization,
            'district_brief': {
                key: district_briefs[name][key]
                for key in ('p1', 'p2', 'p3', 'words', 'figures')
            },                                                       # ratified district narrative
            'community': community,                                # 2024 ACS structural context and controls
            'industry': industry,                                  # optional compact Phase-F LODES/NAICS metrics
            'demographic_potential': demographic_potential,        # cross-validated LQ + shift-share signal
            'psychometrics': MANUAL.get(name, {}).get('psychometrics'),  # manual
            'psy': psy_for(name),                                  # computed: Big5 pct radar + persona + susceptibility cross-read
            'bills': bills,                                        # legislative actions (role + area)
            'articles': arts,                                      # press & social (full DB, cats normalized)
            'media_exclusions': LOAD_AUDIT.get('social_exclusions_by_member', {}).get(
                name, {'unrelated': 0, 'offtopic': 0}),
            'media_totals': MTOT.get(name) or {},                  # footprint (shown, total) per source + pending-handle flag

            'electoral': EH.get(name),                             # FEC primary+general vote-share trend + Ballotpedia forecast
        }
        if employers is not None:
            rec['employers'] = employers
        if financial is not None:
            rec['financial'] = financial
        if hmda is not None:
            rec['hmda'] = hmda
        members.append(rec)
    potential_stats = DP.score_members(members, INDUSTRY4)
    potential_qualified = potential_stats['qualified_members']
    if INDUSTRY:
        print('  industry: %d members attached  |  !! unresolved geo: %d' %
              (industry_attached, industry_unresolved))
        potential_scores = [
            m['demographic_potential']['score'] for m in members
            if (m.get('demographic_potential') or {}).get('score') is not None
        ]
        score_range = (
            '  |  score %.1f-%.1f' %
            (min(potential_scores), max(potential_scores))
            if potential_scores else ''
        )
        print(('  demographic potential: %d members / %d markets qualified%s') %
              (potential_qualified, potential_stats['qualified_markets'],
               score_range))
    if COMMUNITY:
        print(
            '  ACS community: %d members attached  |  unresolved geo: %d' %
            (community_attached, community_unresolved)
        )
    print('  district briefs: %d/%d ratified narratives attached' %
          (len(district_briefs), len(member_names)))
    for key in ('employers', 'financial', 'hmda'):
        if key in gh_counts:
            count = gh_counts[key]
            skipped = ('  |  MULTIPLE RECIPIENTS skipped: %d' % employer_multiple_recipient_skipped
                       if key == 'employers' else '')
            print(('  %s: %d members attached  |  absent: %d  |  DC/PR counted absent: %d%s') %
                  (key, count['attached'], count['absent'], count['dc_pr'], skipped))
    # ----- demographics radar: percentile-rank each metric across all members.
    #       Population is placed on a comparable constituency basis first:
    #       senators use state population / apportioned House seats; House
    #       members retain their district population. Identical inputs share a
    #       tie-aware midrank rather than receiving arbitrary adjacent ranks. -----
    DEMO_AXES = [('income','Median income'),('pct_finance','Finance / RE workforce'),
                 ('pct_ownerocc_mort','Owner-occ. mortgage'),('pct_costburden','Cost-burdened HHs'),
                 ('establishments','Business density'),('constituents','Population')]
    for key,_ in DEMO_AXES:
        values = []
        for i, member in enumerate(members):
            demo = member.get('demo') or {}
            value = demo.get(key)
            if key == 'constituents':
                senate_basis = (
                    member.get('demo_population_normalization') or {}
                )
                value = senate_basis.get(
                    'normalization_population', value)
            if value is not None:
                values.append((i, value))
        for i, percentile in DN.midrank_percentiles(values).items():
            members[i].setdefault('_demopct', {})[key] = round(
                percentile, 3)
    for rec in members:
        pm = rec.pop('_demopct', {})
        rec['demo_radar'] = [pm.get(key, 0) for key,_ in DEMO_AXES]

    # ----- investment efficiency: percentile(score) - percentile(invested) = "undervalued" signal -----
    # F4b (2026-09-30): tie-aware MIDRANK percentiles, the demo radar's rule above. The old stable sort broke
    # ties by row order: 521 of 533 members share an invested value (188 at $0) and a row shuffle moved 497
    # eff_values. Midrank gives tied members one value, so eff_value no longer depends on file order.
    eff = desk_eff_values([rec.get('score') for rec in members],
                          [rec.get('invested') for rec in members], DN.midrank_percentiles)
    for i, rec in enumerate(members):
        rec['eff_value'] = eff[i]                                       # +1 = high influence, low spend (undervalued)
        # Golovin-Krause optimizer suggestion ($): the plan's increment above existing CU money; gap = that
        # increment, never negative (F4a, 2026-09-30).
        rec['opt_alloc'], rec['gap'] = desk_plan_for(OPT_ALLOC, rec['name'])
        si = rec.get('sna_idx')
        rec['roi'] = desk_roi_for(ROI, si)                                  # expected weighted flips from the next $10k
    _m, _t, _um = desk_opt_reconcile(OPT_ALLOC, [rec['name'] for rec in members])
    print('  optimizer dollars on desk rows: $%s of $%s planned (%d unmatched names)' % (format(_m, ',.0f'), format(_t, ',.0f'), len(_um)))
    if _um:
        print('  !! optimizer names with no desk row (planned $ the desk would not show):')
        for _n, _v in sorted(_um.items(), key=lambda kv: -kv[1]): print('     %r  $%s' % (_n, format(_v, ',.0f')))
        raise ValueError('make_member_desk: %d optimizer alloc name(s), $%s planned, match no desk row' % (len(_um), format(sum(_um.values()), ',.0f')))

    # ----- self-contained cosponsor graph (member-array indices) for the coalition builder -----
    midx = {m['name']: k for k, m in enumerate(members)}
    adj_out = []
    dcross=float((eng.get('const') or {}).get('DCROSS',0.4)); emult=eng.get('emult') or [1.0]*len(snames)
    bpf=eng.get('bpf') or [0.24]*len(snames)
    for u, v, w in eng['adj']:
        a = midx.get(snames[u]); b = midx.get(snames[v])
        if a is not None and b is not None and a != b:
            cross=(PARTY_SNA[u]!=PARTY_SNA[v] and PARTY_SNA[u] in frozenset('DR') and PARTY_SNA[v] in frozenset('DR'))
            cpf=dcross+(1-dcross)*(bpf[u]*bpf[v])**0.5 if cross else 1.0
            wc=w*cpf*emult[v]; probability=wc/(wc+6.0)
            adj_out.append([a, b, round(w, 2), round(probability, 6)])

    # ----- DIRECTED cosponsor graph + cosponsor propensity (2026-08-03) -----
    # THE PATHFINDER'S ETHOS (Paul): "we already found a sponsor, it is finding the best path
    # around them to build the most effective co-sponsor coalition." The sponsor is GIVEN, so the
    # asset we are shopping for is willingness to SIGN ONTO someone else's bill -- the IN-side of a
    # directed sponsor->cosponsor edge. `adj_out` above is symmetrised, and measured on this graph
    # the top-20 symmetrised-degree hubs draw 92.8% of their degree from OUT-edges (people who
    # sponsor bills others sign). Ranking candidate COSPONSORS by that is ranking them on the one
    # capability the task does not need. See moneyball_core.load_network_directed().
    dadj_out = []
    try:
        _DIR = MBC.load_network_directed()
        for _s, _row in _DIR.items():
            a = midx.get(_s)
            if a is None: continue
            for _c2, _w in _row.items():
                b = midx.get(_c2)
                if b is None or a == b: continue
                # same cross-party kernel the symmetric edges use, so the two are on one scale
                ui, vi = sidx.get(_s), sidx.get(_c2)
                if ui is None or vi is None: continue
                cross = (PARTY_SNA[ui] != PARTY_SNA[vi] and PARTY_SNA[ui] in 'DR' and PARTY_SNA[vi] in 'DR')
                cpf = dcross + (1 - dcross) * (bpf[ui] * bpf[vi]) ** 0.5 if cross else 1.0
                wc = _w * cpf * emult[vi]
                dadj_out.append([a, b, round(_w, 2), round(wc / (wc + 6.0), 6)])
    except Exception as exc:                      # missing/unreadable base scrape must not kill the desk
        print('  !! directed cosponsor graph unavailable (%s) — builder falls back to symmetrised' % exc)
        dadj_out = []

    # Per-member cosponsor propensity from the SAME bills ledger the page already shows, so the
    # number under a member's name and the number the recommender ranks on cannot disagree.
    #   cos   = bills this member signed onto as (original) cosponsor  -> the recruitable behaviour
    #   spons = bills this member sponsored                            -> deliberately NOT scored
    #   area  = the same cosponsor count split by subject area, for issue-conditioned propensity
    #           and for the selectivity (lift) readout.
    _CIDX = {c: i for i, c in enumerate(CATS)}
    cos_n = [0] * len(members); spo_n = [0] * len(members)
    cos_area = [[0] * len(CATS) for _ in members]
    for i, rec in enumerate(members):
        for b in (rec.get('bills') or []):
            if b.get('role') == 'sponsor':
                spo_n[i] += 1
                continue
            cos_n[i] += 1
            for a in (b.get('areas') or []):
                j = _CIDX.get(a)
                if j is not None: cos_area[i][j] += 1
    _tot_area = [sum(cos_area[i][j] for i in range(len(members))) for j in range(len(CATS))]
    _tot_all = sum(_tot_area) or 1
    cospon = {
        'cos': cos_n, 'spons': spo_n, 'area': cos_area,
        'area_share': [round(t / _tot_all, 6) for t in _tot_area],   # global area mix = the lift null
        'source': 'member desk bills ledger (role sponsor/original/cosponsor, adjudicated)',
    }

    # SELECTIVITY (2026-08-03, Paul: "Yeah we gotta account for selectivity"). The block above is
    # the member's OWN ledger, so its numerator and denominator move together and it cannot tell a
    # member who signs EVERYTHING from one who signs THIS ISSUE. cosponsor_selectivity.py supplies
    # the missing opportunity denominator from the BILLSTATUS per-bill cosponsor rosters: of the
    # bills in an area that the member could have signed (own chamber, Congress they served in, not
    # their own bill), what share did they sign. `opp` travels with `sel` so the template can tell
    # a real zero from no-opportunity. Optional: absent the artifact the basis is unchanged.
    _SELJ = L('data/network/cosponsor_selectivity.json') or {}
    _SEL = _SELJ.get('members') or {}
    _nz = len(CATS)
    selectivity = {
        'sel': [(_SEL.get(m['name']) or {}).get('sel') or [0.0] * _nz for m in members],
        'opp': [(_SEL.get(m['name']) or {}).get('opp') or [0] * _nz for m in members],
        'sig': [(_SEL.get(m['name']) or {}).get('sig') or [0] * _nz for m in members],
        'area_base_rate': _SELJ.get('area_base_rate') or [0.0] * _nz,
        'source': _SELJ.get('roster_source') or 'cosponsor_selectivity.json absent',
        'shrinkage': _SELJ.get('shrinkage') or {},
    }
    print('selectivity: %d/%d members carry an opportunity denominator' % (
        sum(1 for r in selectivity['opp'] if any(r)), len(members)))

    influence_policy = DLW.policy_bundle(CATS, members)
    # Coalition pricing (2026-08-03): per-member access price + majority-neutral capability, for
    # the builder's "Underpriced" basis. Produced by fec/lobbying/coalition_pricing.py; optional, and the
    # template falls back to neutral values if it is absent, so a missing artifact degrades the
    # basis rather than breaking the page.
    _PRICING = L('data/lobbying/coalition_pricing.json') or {}
    _PM = _PRICING.get('members') or {}
    pricing = {
        'cap':   [round((_PM.get(m['name']) or {}).get('capability_pct', 0.5), 4) for m in members],
        'attn':  [round((_PM.get(m['name']) or {}).get('attention_pct', 0.5), 4) for m in members],
        'price': [round((_PM.get(m['name']) or {}).get('price', 1.0), 4) for m in members],
        'ign':   [round((_PM.get(m['name']) or {}).get('ignored', 0.0), 4) for m in members],
        'jur':   [1 if (_PM.get(m['name']) or {}).get('in_jurisdiction') else 0 for m in members],
        'schema': _PRICING.get('schema'),
        'ratified': _PRICING.get('ratified'),
    } if _PM else {}
    DEMO_SHORT = {'Median income':'Income','Finance / RE workforce':'Finance','Owner-occ. mortgage':'Mortgages',
                  'Cost-burdened HHs':'Cost-burd.','Business density':'Business','Population':'Population'}
    _automatic_source=L('data/legislation/sentinel_admissions.json') or {}
    _money_source=L('data/money/fec_refresh_current.json') or {}
    bundle = {
        'source_updates': {
            'bills_checked_at':_automatic_source.get('checked_at'),
            'automatic_bill_status':_automatic_source.get('status','NOT_RUN'),
            'automatically_classified_bills':len(_automatic_source.get('bills',{})),
            'bill_review_backlog':len(_automatic_source.get('deferred_keys',[])),
            'bill_holds':sum(d.get('disposition')=='hold' for d in _automatic_source.get('decisions',{}).values()),
            'money_checked_at':_money_source.get('checked_at'),
            'money_cycle':_money_source.get('cycle'),
            'money_evidence_through':_money_source.get('latest_attributed_transaction_date'),
            'money_identity_gaps':_money_source.get('unresolved_members',[]),
        },
        'cats': CATS,
        'cats_short': [CATS_SHORT.get(c, c) for c in CATS],
        'defensive': sorted(DEFENSIVE),
        'stance_curves': {
            key: value for key, value in (CS.get('curve') or {}).items()
            if key != 'label_scores'
        },
        'art_extra_cats': ['CU-Bank Competitive Dynamics','General Credit Union'],
        'demo_axes': [lbl for _,lbl in DEMO_AXES],
        'demo_short': [DEMO_SHORT.get(lbl, lbl) for _,lbl in DEMO_AXES],
        'district_briefs_meta': district_briefs_meta,
        'industry_meta': ({
            'sector_meta': INDUSTRY.get('sector_meta') or {},
            'groups': INDUSTRY.get('groups') or {},
            'lodes': INDUSTRY.get('lodes') or {},
            'growth_window_years': INDUSTRY.get('growth_window_years'),
            'short_names': INDUSTRY_SHORT,
        } if INDUSTRY else None),
        'community_meta': ({
            'schema': COMMUNITY.get('schema'),
            'vintage': COMMUNITY.get('vintage'),
            'survey': COMMUNITY.get('survey'),
            'congress': COMMUNITY.get('congress'),
            'coverage': COMMUNITY.get('coverage') or {},
            'profile_families': COMMUNITY.get('profile_families') or {},
            'variable_catalog': COMMUNITY.get('variable_catalog') or {},
            'method': COMMUNITY.get('method'),
            'official_urls': {
                'DP05': (
                    'https://api.census.gov/data/2024/acs/acs1/'
                    'profile/groups/DP05.html'
                ),
                'DP03': (
                    'https://api.census.gov/data/2024/acs/acs1/'
                    'profile/groups/DP03.html'
                ),
                'DP04': (
                    'https://api.census.gov/data/2024/acs/acs1/'
                    'profile/groups/DP04.html'
                ),
                'geography': (
                    'https://api.census.gov/data/2024/acs/acs1/'
                    'geography.html'
                ),
                'gazetteer': (
                    'https://www2.census.gov/geo/docs/maps-data/data/'
                    'gazetteer/2024_Gazetteer/'
                ),
            },
        } if COMMUNITY else None),
        'demographic_potential_meta': DP.metadata(
            INDUSTRY4,
            (INDUSTRY.get('sector_meta') or {}) if INDUSTRY else {},
            qualified_members=potential_qualified,
            qualified_markets=potential_stats['qualified_markets'],
            small_sector_min=(
                INDUSTRY.get('small_sector_min', 500)
                if INDUSTRY else 500
            ),
            sensitivity_audit=potential_stats.get(
                'sensitivity_audit'
            ),
            structural_adjustment=potential_stats.get(
                'structural_adjustment'
            ),
        ),
        **({'gh_meta': gh_meta} if gh_meta else {}),
        # pillar weights FROM THE AUTHORITY (ranks_python.json ab_weights = live user dials AB2:AB5);
        # hardcoded 0.40/0.20/0.35/0.05 removed 2026-07-06. The 2026-09-03 policy keeps Press
        # sentiment active at a diminished 0.10 and reallocates the other 0.15 to legislation.
        # Fallback matches that ratified policy if the authority artifact is absent.
        'weights': {**MD['weights'], 'press': RA_W.get('press', 0.10), 'leg': RA_W.get('leg', 0.45),
                    'comm': RA_W.get('comm', 0.40), 'demo': RA_W.get('demo', 0.05)},
        'coalitions': MD['coalitions'],
        'tiername': TIERNAME,
        'adj': adj_out,
        'bill_live': BILL_LIVE,
        'bill_lobbying': BILL_LOBBYING,
        'legislation_culling': {
            'status': CULLING_STATUS.get('status'),
            'phase': CULLING_STATUS.get('phase'),
            'promotion_performed': CULLING_STATUS.get(
                'promotion_performed'
            ),
            'counts': CULLING_STATUS.get('counts') or {},
            'generation_id': CULLING_STATUS.get('generation_id'),
            'provenance': {
                'staged_receipt': CULLING_STATUS.get('staged_receipt'),
                'promotion_receipt': CULLING_STATUS.get(
                    'promotion_receipt'
                ),
            },
        },
        'influence_policy': influence_policy,
        'pricing': pricing,                     # coalition access price + capability (Underpriced basis)
        'dadj': dadj_out,                       # DIRECTED sponsor -> cosponsor edges (recruitment basis)
        'cospon': cospon,                       # per-member cosponsor propensity + area split
        'selectivity': selectivity,             # signatures / OPPORTUNITIES per area (BILLSTATUS rosters)
        'integrity': {'source_display': LOAD_AUDIT},
        'freshness': {
            'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'sources': {rel:datetime.fromtimestamp(os.path.getmtime(os.path.join(B,rel))).astimezone().isoformat(timespec='seconds')
                        for rel in ('ranks_python.json','category_stance.json','dashboard_data.json',
                                    'communication_style.json','social_media.db',
                                    'data/lobbying/district_industry.json','data/lobbying/district_industry_4digit.json',
                                    'data/district/district_community.json',
                                    'data/district/district_briefs_v1.json',
                                    'data/district/district_employers.json',
                                    'data/district/district_financial.json','data/district/district_hmda.json',
                                    'data/members/member_wikipedia_summaries.json',
                                    'bill_repository_live.json','data/lobbying/bill_lobbying_sidecar.json',
                                    'data/members/collector_roster_updates.ndjson',
                                    'data/engine/collector_roster_updates_receipt.json')
                        if os.path.exists(os.path.join(B,rel))},
        },
        'members': members,
    }
    # A legislation shadow run (run_legislation_dashboard_member_desk.py) pins the volatile freshness block.
    if os.environ.get('DESK_FRESHNESS_PIN'):
        with open(os.environ['DESK_FRESHNESS_PIN'], encoding='utf-8') as fh:
            bundle['freshness'] = json.load(fh)
    bundle['integrity']['validation'] = DI.postflight(bundle, preflight)
    # The observed-speech layer is attached only after the score/rank postflight.
    # This ordering is the enforceable D-013 isolation boundary: no engine,
# profile, rank, allocation, or Big-Five computation above can consume it.
    _speech = load_speech_descriptive(B)
    if _speech:
        _speech_members = _speech['members']
        for _member in members:
            if _member['name'] in _speech_members:
                _member['speech_observed'] = _speech_members[_member['name']]
        bundle['speech_descriptive'] = {
            'schema': _speech['schema'],
            'status': _speech['status'],
            'contract': _speech['contract'],
            'corpus': _speech['corpus'],
            'measurement_counts': _speech['measurement_counts'],
            'setting_effects': _speech['setting_effects'],
            'subject_effects': _speech['subject_effects'],
        }
    # Universal speech profiles are attached after postflight for the same strict
    # display-only isolation. Every roster member has a word/interaction baseline;
    # accepted audio is an additional detail layer whose thinness limits claim
    # strength rather than changing PASS/FAIL.
    if _SPEECH_PROFILES:
        for _member in members:
            _profile = ((_SPEECH_PROFILES.get(_member['name']) or {}).get('speech_profile') or {})
            _member['speech_profile'] = {
                'status': _profile.get('status'),
                'coverage_rule': _profile.get('coverage_rule'),
                'general_word_habits': _profile.get('general_word_habits'),
                'interaction_habits': _profile.get('interaction_habits'),
                # Two complementary audio layers (2026-09-07).  Both are forwarded;
                # each is None when the member does not carry it, so speechHTML()
                # can render an honest empty state instead of a 0-clip table.
                'general_audio_habits': _profile.get('general_audio_habits'),
                'verified_voiceprint_baseline': _profile.get('verified_voiceprint_baseline'),
                # Long-form remains a third, separately labelled complement. Candidate-only
                # turns are forwarded with their authority state so the desk can disclose
                # coverage without promoting them into baseline claims or meeting guidance.
                'longform_interview_evidence': _profile.get('longform_interview_evidence'),
                'longform_measured_delivery': _profile.get('longform_measured_delivery'),
                'by_setting': _profile.get('by_setting') or {},
                'setting_contrasts': _profile.get('setting_contrasts') or [],
                'repeated_defensive_reactions': _profile.get('repeated_defensive_reactions'),
                'supported_topic_associations': _profile.get('supported_topic_associations') or [],
            }
        bundle['speech_profile_method'] = {
            'schema': _SPEECH_PROFILE_DOC['schema'],
            'status': 'PASS_ALL_MEMBERS',
            'basis_sha256': _SPEECH_PROFILE_DOC['basis_sha256'],
            'counts': _SPEECH_PROFILE_DOC['counts'],
            'method': _SPEECH_PROFILE_DOC['method'],
        }
    # Optional display-only CU responsiveness layer.  It is attached only after
    # the score/rank postflight so no desk computation can consume it.  When the
    # artifact is absent, the bundle and rendered template follow the old path.
    # D5 -- DESK_RESPONSIVENESS (2026-09-16, packet §D5) retires the desk section
    # by default.  At '0' the loader never runs, so `responsiveness_receipts` is
    # never set, the :3170 injection conditional is already false, and the card
    # disappears with NO template edit and no anchor risk.  The producer, the
    # loader, the JS helpers and the injector all stay in place, dead under the
    # default and live again under DESK_RESPONSIVENESS=1.
    _resp_path = os.environ.get('RESPONSIVENESS_LAYERS_PATH') or os.path.join(
        B, 'data', 'engine', 'responsiveness_layers.json'
    )
    _resp = _load_responsiveness_optional(_resp_path) if DESK_RESPONSIVENESS else None
    if _resp:
        _resp_receipts = _resp.get('receipts')
        _resp_rows = _resp.get('members')
        _resp_by_name = {}
        for _resp_row in _resp_rows:
            _resp_by_name[_resp_row['member']] = _resp_row
        for _member in members:
            if _member['name'] in _resp_by_name:
                # Preserve the producer row, including every vehicle field,
                # without reinterpreting or recomputing it on the surface.
                _member['responsiveness_cu'] = _resp_by_name[_member['name']]
        bundle['responsiveness_receipts'] = _resp_receipts
    attach_reference(bundle, COOK_PVI, LEG)  # descriptive fields only, after desk calculations
    return attach_honors_display(bundle, honors)

HTML = _tpl('make_member_desk_00.tpl')

_RESPONSIVENESS_MEMBER_HELPERS = r'''
function responsivenessPercent(value){
  if(value==null)return 'n/a';
  return (100*Number(value)).toFixed(1).replace(/\.0$/,'')+'%';
}
function responsivenessOrdinal(value){
  if(value==null)return 'n/a';
  const n=Math.round(100*Number(value)),mod100=n%100;
  const suffix=(mod100>=11&&mod100<=13)?'th':n%10===1?'st':n%10===2?'nd':n%10===3?'rd':'th';
  return n+suffix;
}
function responsivenessCard(m){
  const row=m.responsiveness_cu;
  if(!row)return '';
  const take=row.take_up||{},group=row.in_group||{},capacity=row.capacity||{};
  const receipts=D.responsiveness_receipts||{};
  const unmatched=(receipts.service_unmatched_members||[]).includes(m.name);
  const answered=unmatched?'service data unavailable (Voteview lag)':take.rate==null?'n/a':`${esc(String(take.answered))}/${esc(String(take.eligible))}`;
  const vehicles=(take.vehicles||[]).map(vehicle=>`<tr>
    <td>${esc(vehicle.vehicle)}</td><td>${esc(vehicle.congress)}</td><td>${esc(vehicle.ask_type)}</td>
    <td><code>${esc(JSON.stringify(vehicle.scope||{}))}</code></td>
    <td>${esc(String(vehicle.eligible))}</td><td>${esc(String(vehicle.answered))}</td>
    <td>${vehicle.role==null?'null':esc(vehicle.role)}</td></tr>`).join('');
  return `<div class=card style=margin-bottom:14px>
    <div class=eyebrow>Responsiveness (CU) — display-only</div>
    <div style="font-weight:700;margin-bottom:7px">Asks answered ${answered} · in-group mean ${responsivenessPercent(group.mean_rate)}</div>
    <div class=mrow><span>Capacity-adjusted CU delivery:</span><b>${responsivenessOrdinal(capacity.pctile_linear)} pctile (linear, roster-wide; log spec in artifact receipts)</b></div>
    <div style="overflow-x:auto;margin-top:9px"><table><thead><tr><th>vehicle</th><th>congress</th><th>ask_type</th><th>scope tag</th><th>eligible</th><th>answered</th><th>role</th></tr></thead><tbody>${vehicles}</tbody></table></div>
    <div style="font-size:10px;color:var(--faint);margin-top:8px">Display-only: no desk score, board tier, or rank uses this layer. Denominators are member-specific (chamber, party, and service scoped); rates are not comparable across members.</div>
  </div>`;
}
'''

def _html_with_responsiveness(template):
    helper_anchor = 'function openMember(i){'
    panel_anchor = '  ${conductCard(m)}\n  <div class=gen>'
    if template.count(helper_anchor) != 1 or template.count(panel_anchor) != 1:
        raise RuntimeError('member desk responsiveness insertion anchor drifted')
    template = template.replace(
        helper_anchor,
        _RESPONSIVENESS_MEMBER_HELPERS + '\n' + helper_anchor,
    )
    return template.replace(
        panel_anchor,
        '  ${conductCard(m)}\n  ${responsivenessCard(m)}\n  <div class=gen>',
    )


_PERSONAL_DISPLAY_REPLACEMENTS = (
    (
        "  const p=m.personal||{},allItems=p.items||[],eligible=allItems.filter(x=>x.display_eligible===true);",
        "  const p=m.personal||{},allItems=p.items||[],dedupe=p.display_dedupe||{},dedupeSuppressed=new Set(dedupe.suppressed_indices||[]),sourceEligible=allItems.filter(x=>x.display_eligible===true),eligible=allItems.filter((x,i)=>x.display_eligible===true&&!dedupeSuppressed.has(i));",
    ),
    (
        "  const hidden=allItems.length-eligible.length,dupes=duplicates.length;",
        "  const hidden=allItems.length-sourceEligible.length,nearDupes=dedupeSuppressed.size,dupes=duplicates.length;",
    ),
    (
        "  const suppressed=`${hidden?` ${hidden} automated catch-net excerpt${hidden===1?' was':'s were'} hidden by the personal-detail precision gate.`:''}${dupes?` ${dupes} detail${dupes===1?' was':'s were'} already covered in the biography and suppressed here.`:''}`;",
        "  const suppressed=`${hidden?` ${hidden} automated catch-net excerpt${hidden===1?' was':'s were'} hidden by the personal-detail precision gate.`:''}${nearDupes?` ${nearDupes} semantically repeated personal detail${nearDupes===1?' was':'s were'} collapsed; every raw excerpt remains in the data bundle.`:''}${dupes?` ${dupes} detail${dupes===1?' was':'s were'} already covered in the biography and suppressed here.`:''}`;",
    ),
    (
        "${hidden?` · ${hidden} catch-net excerpt${hidden===1?'':'s'} hidden by the precision gate`:''}${dupes?` · ${dupes} biography duplicate${dupes===1?'':'s'} suppressed`:''}</div>${cards}`;",
        "${hidden?` · ${hidden} catch-net excerpt${hidden===1?'':'s'} hidden by the precision gate`:''}${nearDupes?` · ${nearDupes} repeated personal card${nearDupes===1?'':'s'} collapsed (raw evidence retained)`:''}${dupes?` · ${dupes} biography duplicate${dupes===1?'':'s'} suppressed`:''}</div>${cards}`;",
    ),
)


def _html_with_personal_dedupe(template):
    for old, new in _PERSONAL_DISPLAY_REPLACEMENTS:
        if template.count(old) != 1:
            raise RuntimeError('member desk personal-dedupe insertion anchor drifted')
        template = template.replace(old, new)
    return template

if __name__ == '__main__':
    bundle = build_bundle()
    # validation
    ms = bundle['members']
    print('members: %d' % len(ms))
    inv = [m['invested'] for m in ms if m['invested']]
    print('with FEC CU $: %d  | total $%s  | max $%s (%s)' % (
        len(inv), f"{sum(inv):,.0f}", f"{max(inv, default=0):,.0f}",
        max(ms, key=lambda m: m['invested'], default={'name': 'none'})['name']))
    print('with electoral play (not none): %d' % sum(1 for m in ms if m['ev_play_code'] != 'n'))
    print('with >=1 cosponsor partner: %d' % sum(1 for m in ms if m['partners']))
    print('total articles: %d  | members with >=1 article: %d  | max: %d' % (
        sum(len(m['articles']) for m in ms), sum(1 for m in ms if m['articles']),
        max((len(m['articles']) for m in ms), default=0)))
    _sd = bundle['integrity']['source_display']
    print('footprint displayed: %d interview / %d hearing / %d bluesky (cap=%s, enabled=%s)' % (
        _sd.get('interview_appearances_displayed', 0), _sd.get('hearing_appearances_displayed', 0),
        _sd.get('bluesky_footprint_displayed', 0), _sd.get('footprint_display_cap'), _sd.get('footprint_enabled')))
    print('total bills: %d  | members with >=1 bill: %d' % (
        sum(len(m['bills']) for m in ms), sum(1 for m in ms if m['bills'])))
    print('with bioguide: %d  | with birthday: %d' % (
        sum(1 for m in ms if m['bio'].get('bioguide')),
        sum(1 for m in ms if m['bio'].get('birthday'))))
    # sample: a known CU champion
    s = next((m for m in ms if m['name'] == 'Adam Schiff'), None)
    if s is not None:
        print('\nSAMPLE — Adam Schiff: rank #%s, invested $%s, ev_play=%s ($%s), top adj-rank cats:' % (
            s['rank'], f"{s['invested']:,.0f}", s['ev_play'], f"{s['ev_value']:,.0f}"))
        tops = sorted(bundle['cats'], key=lambda c: s['adjrank'].get(c) or 9999)[:3]
        for c in tops:
            print('   %-30s score %.3f  adj-rank #%s' % (c, s['radar'][bundle['cats'].index(c)], s['adjrank'][c]))
        print('   partners:', ', '.join(p['name'] for p in s['partners'][:5]))
    import io
    _dir = os.environ.get('DESK_OUT') or B   # no-clobber redirect (a directory) for preview/test runs; prod = unset -> live tree
    if '--data' not in sys.argv:
        eff_path = os.environ.get('EFFICIENCY_LAYERS_PATH') or os.path.join(MB_ROOT, 'data', 'engine', 'efficiency_layers.json')
        with open(eff_path,encoding='utf-8') as fh: efficiency=json.load(fh)
        if efficiency.get('schema')!='efficiency-layers-v2':
            raise RuntimeError('efficiency_layers.json schema is not efficiency-layers-v2')
    out = os.path.join(_dir, 'member_desk_bundle.json')
    from engine.effectiveness_integration import optional_shadow_explanations
    bundle = optional_shadow_explanations(bundle, out)
    dump_atomic(bundle, out, separators=(',', ':'), ensure_ascii=False)
    print('\nwrote %s (%.0f KB)' % (out, os.path.getsize(out)/1024))
    if '--data' not in sys.argv:
        html_out = os.path.join(_dir, 'cu-member-desk.html')
        bundle['efficiency_layers']=efficiency
        bundle['efficiency_layers_display']=efficiency_percentiles(efficiency)
        # DESK_SPLIT (default ON, ratified 2026-08-24; =0 restores the single-file build):
        # profile-only member fields plus bill_lobbying move to a sibling script the page
        # loads async, so the ranked board parses ~7% of the bytes and is interactive in
        # under half a second. Reunion was proven sha256-identical to the monolith payload
        # and the board render path touches none of the deferred fields (field-by-field
        # audit incl. renderRanked's helper functions). member_desk_bundle.json above stays
        # the FULL bundle either way - its consumers see no change.
        HEAVY_MEMBER_FIELDS = ['articles','personal','industry','district_brief','cook_pvi','responsiveness_cu','psy',
                               'bills','refs','staffdir','community','electoral','employers',
                               'partners','bio','financial']
        split = os.environ.get('DESK_SPLIT', '1') != '0'
        if split:
            heavy_rows, light_members = [], []
            for m in bundle['members']:
                heavy_rows.append({k: m[k] for k in HEAVY_MEMBER_FIELDS if k in m})
                light_members.append({k: v for k, v in m.items() if k not in HEAVY_MEMBER_FIELDS})
            light = {k: v for k, v in bundle.items() if k != 'bill_lobbying'}
            light['members'] = light_members
            prof = MBC.json_for_script(
                {'members_heavy': heavy_rows, 'bill_lobbying': bundle.get('bill_lobbying')},
                separators=(',', ':'), ensure_ascii=False)
            prof_path = os.path.join(_dir, 'cu-member-desk-profiles.js')
            write_text_atomic('window.__melangeProfileMerge(' + prof + ');', prof_path)
            print('wrote %s (%.0f KB)' % (prof_path, os.path.getsize(prof_path)/1024))
            payload = MBC.json_for_script(light, separators=(',', ':'), ensure_ascii=False)
            profiles_tag = '<script src="cu-member-desk-profiles.js" async></script>'
        else:
            payload = MBC.json_for_script(bundle, separators=(',', ':'), ensure_ascii=False)
            profiles_tag = ''
        page_template = _html_with_personal_dedupe(
            _html_with_responsiveness(HTML)
            if bundle.get('responsiveness_receipts') else HTML
        )
        write_text_atomic(page_template.replace('__BUNDLE__', payload)
                                       .replace('__HONORS_GENERATION_META__', honors_generation_meta(bundle))
                                       .replace('__DSPLIT__', '1' if split else '0')
                                       .replace('__PROFILES_TAG__', profiles_tag), html_out)
        print('wrote %s (%.0f KB)' % (html_out, os.path.getsize(html_out)/1024))
