# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""
Combines the SNA network dashboard (Moneyball Dashboard.html), the member desk
(cu-member-desk.html), the industry surface (lobby-network.html) and the staffer
network (staffer-network.html) into ONE self-contained file: Moneyball.html.

TWO documents come out of ONE extraction (2026-09-05):
  * Moneyball.html       -- all FOUR surfaces.  The file-open / live deliverable; unchanged.
  * Moneyball_Email.html -- the THREE-surface variant (network + member desk + industry),
    i.e. exactly what Moneyball.html was before the staffer surface landed on 2026-09-04.
    fec/surfaces/make_email_package.py zips THIS file into the all-in-one attachment (under
    the arcname Moneyball.html, so recipients still open the filename they know).  Reason:
    the staffer panel carries a ~4.12 MB JSON payload that costs ~640 KB compressed, and the
    all-in-one zip's 14 MiB attachment budget had ~510 KB of headroom.  With the fourth
    surface in it the level-9 zip measured 14,808,817 bytes against a 14,680,064 budget, and
    make_email_package.py refused the package, which halted the chain before the audit.
    Raising the attachment budget is the owner's call, not this builder's; trimming the
    section to fit would leave a ~1% margin that the next data refresh breaks.  So the two
    deliverables were split instead.  Both come from assemble() below, over the SAME
    extracted pieces -- there is no second parse and no second staleness contract.

    2026-10-07, second trim of the same kind.  The telecom sector put the all-in-one zip back
    over the budget: 14,990,864 bytes against 14,680,064 after the zopfli fallback, 310,800 over,
    with 14,591,006 the last good build.  The growth was 426 KB compressed, split between the
    industry page-data (+200 KB: the fifth industry and 338 more companies) and the desk's
    bill_lobbying sidecar (+221 KB), so no trim of the growth alone reaches the 13.8 MiB target.
    The emailed copy therefore ships the desk profiles payload with its bill_lobbying sidecar
    set to null (email_profiles below): the Bills view keeps every bill and loses only the
    per-bill "Bill-citing lobbying" block, which the desk already omits when it has no sidecar
    for a bill, and the family zip already emails cu-member-desk.html without the
    cu-member-desk-profiles.js it points at.  The four-surface Moneyball.html and
    cu-member-desk-profiles.js are untouched.

Design for a clean merge + minimal antivirus false-positives:
- Single file, NO external loads (no CDN/fonts/script-src), no eval/new Function/document.write.
- Each app's JS is wrapped in its own IIFE (so the four scripts can't collide on globals),
  exposing only a few cross-app hooks on window.
- The SNA stylesheet is scoped under #app-network, the industry stylesheet under
  #app-industry and the staffer stylesheet under #app-staffer, so their bare-element rules
  (body/select/header/table) don't bleed into the member desk.  The member stylesheet stays
  global BUT is wrapped in a `@layer desk` so it can no longer out-cascade the two surfaces
  it now shares a document with (see the CASCADE note below).
- ID collisions are renamed in the incoming chunk: desk `backbtn` -> `mdback`,
  industry `main` -> `indmain`.  The staffer surface needs no rename: every id it emits is
  prefixed `stf-`, and the collision guard below proves that against the other three.
- Cross-surface links switch views in-page (no navigation) and push a history entry, so
  browser Back/Forward walks the four surfaces.  The drill-down surface
  (lobby-interactions.html) is deliberately NOT merged and stays a real navigation.
- The staffer surface is OPTIONAL: it renders a receipt-gated generation that a fresh or
  data-isolated tree may not have.  When staffer-network.html is absent the panel is simply
  not merged (clean skip, mirroring run_pipeline.py's OPTIONAL_INPUTS contract); when its
  section data is present it must be current, or this script refuses.  A document with no
  staffer panel still ships the whole controller, so gotoStaffer degrades to gotoNetwork
  rather than hiding every panel and leaving a blank page -- that is the ONE behavioural
  difference the email split introduced, and it applies to the absent-surface build too.

CASCADE note (2026-08-03).  Before the industry merge, only two surfaces shared the
document: every SNA rule carries the `#app-network` id, so it already out-ranked every
global desk rule that could match an SNA element, and no desk id-rule matches inside
#app-network (the one shared id, backbtn, is renamed).  The desk stylesheet is therefore
cascade-neutral for the SNA surface, and moving it into a layer changes nothing there.
It is NOT neutral for the industry surface: 44 global desk rules match industry elements
and win every property the industry stylesheet does not itself set (table{min-width:680px},
header{flex-wrap:wrap}, body{font:13px/1.45}, a{text-decoration:none} ...).  Layer order
`desk, industry` fixes that by construction instead of by enumeration.  Unlayered rules
(SNA + shell) still beat both layers, which is the pre-merge behaviour.

Run:  python fec/surfaces/make_lobby_network_html.py     (industry surface)
      python fec/surfaces/make_staffer_network_html.py  (staffer surface, optional)
      python fec/surfaces/make_combined.py              (after make_dashboard_html.py + make_member_desk.py)

Writes: Moneyball.html (four surfaces) and Moneyball_Email.html (three), both atomically.
"""
import re, io, os, sys, time
B = MB_ROOT
_STALE_OK = os.environ.get('COMBINED_STALE_OK','0')=='1'
if _STALE_OK:
    print('WARNING: COMBINED_STALE_OK=1; stale-input guards bypassed')

# 2026-07-19 incident: desk postflight raised IntegrityError after its reconciliation write, and
# make_combined rebuilt Moneyball.html from stale desk HTML with no warning.
_recon_p = os.path.join(B, 'dashboard_exclusion_reconciliation.json')
_desk_p = os.path.join(B, 'cu-member-desk.html')
if not os.path.exists(_desk_p):
    print('ERROR: cu-member-desk.html is missing; refusing to combine')
    sys.exit(1)
if _STALE_OK:
    pass
elif os.path.exists(_recon_p):
    _recon_mtime = os.path.getmtime(_recon_p)
    _desk_mtime = os.path.getmtime(_desk_p)
    if _recon_mtime > _desk_mtime + 5:
        print('ERROR: dashboard_exclusion_reconciliation.json mtime: %s' % time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_recon_mtime)))
        print('ERROR: cu-member-desk.html mtime: %s' % time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_desk_mtime)))
        print('ERROR: the last make_member_desk.py run failed after its reconciliation write;')
        print('ERROR: cu-member-desk.html is STALE; refusing to combine')
        sys.exit(1)

# 2026-08-03: the same failure mode, one surface over.  Both lobby-network.html and
# lobby-interactions.html are built by chain steps as of 2026-08-04
# (make_lobby_network_html.py and make_lobby_interactions_html.py), but this staleness guard is
# retained as defence-in-depth for the case where make_combined.py is invoked directly rather
# than through run_pipeline.py -- exactly how the original failure happened.  It still catches
# when its inputs — or its own builder — move past it.  That is how the industry surface shipped
# without its bank-tier chart and year filter for a day.  The builder publishes its own input
# manifest; ask it rather than re-deriving the paths here.
_ind_p = os.path.join(B, 'lobby-network.html')
if not os.path.exists(_ind_p):
    print('ERROR: lobby-network.html is missing; run fec/surfaces/make_lobby_network_html.py first')
    sys.exit(1)
if not _STALE_OK:
    from surfaces import make_lobby_network_html as _indbuild
    _ind_mtime = os.path.getmtime(_ind_p)
    _ind_inputs = list(_indbuild._input_paths(_indbuild._default_data_root()))
    # the builder itself counts as an input: a code change is as stale-making as a data change
    _ind_inputs.append(('builder', os.path.abspath(_indbuild.__file__)))
    _stale = [(n, p, os.path.getmtime(p)) for n, p in _ind_inputs
              if os.path.exists(p) and os.path.getmtime(p) > _ind_mtime + 5]
    _missing = [(n, p) for n, p in _ind_inputs if not os.path.exists(p)]
    if _missing:
        for n, p in _missing:
            print('ERROR: industry input missing: %s -> %s' % (n, p))
        print('ERROR: the industry surface cannot be shown to be current; refusing to combine')
        sys.exit(1)
    if _stale:
        print('ERROR: lobby-network.html mtime: %s' % time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_ind_mtime)))
        for n, p, mt in sorted(_stale, key=lambda r: -r[2]):
            print('ERROR:   newer input %-22s %s  (%s)' % (n, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mt)), p))
        print('ERROR: lobby-network.html is STALE; run fec/surfaces/make_lobby_network_html.py, then combine')
        sys.exit(1)
    # mtime alone is not enough, and this is not hypothetical: on 2026-08-03 the deployed
    # lobby-network.html was NEWER than its builder and still carried none of the builder's
    # bank-tier or year-filter markup.  So also require, by content, that every honesty string
    # the builder currently emits is actually on the page.  Self-maintaining: it reads the
    # builder's own `html.escape(<CONST>)` call sites rather than a hand-kept list.
    import html as _htmlmod
    _bsrc = io.open(_indbuild.__file__, encoding='utf-8').read()
    # 2026-08-06 family split: builder honesty strings now render across the
    # five-page industry family, so the coverage check reads the whole family.
    _indraw = io.open(_ind_p, encoding='utf-8').read()
    for _fam_name in ('lobby-champions.html', 'lobby-forecast.html',
                      'lobby-bills.html', 'lobby-screen.html'):
        _fam_p = os.path.join(B, _fam_name)
        if os.path.exists(_fam_p):
            _indraw += io.open(_fam_p, encoding='utf-8').read()
    _absent = []
    for _nm in sorted(set(re.findall(r'html\.escape\(([A-Za-z_][\w.]*)\)', _bsrc))):
        _obj = _indbuild
        try:
            for _part in _nm.split('.'):
                _obj = getattr(_obj, _part)
        except AttributeError:
            continue                                   # a local, not a module constant
        if isinstance(_obj, str) and len(_obj) >= 40 and _htmlmod.escape(_obj) not in _indraw:
            _absent.append(_nm)
    if _absent:
        for _nm in _absent:
            print('ERROR: builder honesty string not on the page: %s' % _nm)
        print('ERROR: lobby-network.html does not match fec/surfaces/make_lobby_network_html.py;')
        print('ERROR: rebuild the industry surface before combining')
        sys.exit(1)

# 2026-09-04: the same guard, one surface further out.  The staffer surface renders a
# receipt-gated generation (audits/staffer_network/<generation>) that a fresh or data-isolated
# tree legitimately does not have, so ABSENCE is a clean skip -- exactly the contract
# run_pipeline.py's OPTIONAL_INPUTS gives the two staffer chain steps.  What absence must not
# do is hide a stale merge, so the two failure modes that ARE errors are named here: a section
# artifact with no page built from it, and a page older than the section, its builder or its
# template.  COMBINED_STALE_OK bypasses both, like the guards above.
_stf_p = os.path.join(B, 'staffer-network.html')
_stf_data_p = os.path.join(B, 'data', 'engine', 'staffer_network_section.json')
_stf_dir = os.path.dirname(os.path.abspath(__file__))
STF_PRESENT = os.path.exists(_stf_p)
if not _STALE_OK and not STF_PRESENT and os.path.exists(_stf_data_p):
    print('ERROR: data/engine/staffer_network_section.json exists but staffer-network.html does not;')
    print('ERROR: run fec/surfaces/make_staffer_network_html.py, then combine')
    sys.exit(1)
if not _STALE_OK and STF_PRESENT:
    _stf_mtime = os.path.getmtime(_stf_p)
    # 2026-09-07: track BOTH shipped templates, not just _00.  make_staffer_network_html.py's
    # DEFAULT_TEMPLATE flipped to _01 today (owner ruling D-022) and --template can select
    # either one explicitly, so a page can legitimately be built from either file -- see the
    # TWO TEMPLATES, TWO EXACT ANCHORS note below, which already has to tell the two apart by
    # content for the same reason.  Naming only the one that happened to be the default left the
    # OTHER one unwatched: editing make_staffer_network_html_01.tpl alone did not touch
    # make_staffer_network_html_00.tpl's mtime, so this guard passed while shipping a page built
    # from a template that had since changed.  Tracking both by name (rather than resolving
    # dynamically which one produced the page and checking only that one) matches this file's own
    # precedent for this exact pair -- _STF_LINK_ANCHORS below also enumerates both explicitly and
    # treats any third shape as a loud failure rather than something to infer silently.
    # 2026-09-13: the THREE Groups artifacts too.  make_staffer_network_html_01.tpl --
    # the DEFAULT template since 2026-09-07 -- fills __OFFICES__/__ORGS__/__CLIENTS__
    # from data/engine/staffer_{offices,organisations,clients}_section.json, which is
    # ~12 MB of the ~12.1 MB page.  Their producer
    # (fec/surfaces/build_staffer_groups_section.py) is deliberately NOT a chain step,
    # so the ONLY thing standing between a groups rebuild and a stale merge is this
    # list; without them, running that producer and then make_combined.py shipped the
    # previous build's offices, organisations and client ties with no error at all.
    # Absent files are skipped by the os.path.exists filter below, exactly as an
    # absent section already is.
    _stf_inputs = [('section', _stf_data_p),
                   ('builder', os.path.join(_stf_dir, 'make_staffer_network_html.py')),
                   ('template_00', os.path.join(_stf_dir, 'templates', 'make_staffer_network_html_00.tpl')),
                   ('template_01', os.path.join(_stf_dir, 'templates', 'make_staffer_network_html_01.tpl')),
                   ('offices', os.path.join(B, 'data', 'engine', 'staffer_offices_section.json')),
                   ('organisations', os.path.join(B, 'data', 'engine', 'staffer_organisations_section.json')),
                   ('clients', os.path.join(B, 'data', 'engine', 'staffer_clients_section.json'))]
    _stf_stale = [(n, p, os.path.getmtime(p)) for n, p in _stf_inputs
                  if os.path.exists(p) and os.path.getmtime(p) > _stf_mtime + 5]
    if _stf_stale:
        print('ERROR: staffer-network.html mtime: %s' % time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_stf_mtime)))
        for n, p, mt in sorted(_stf_stale, key=lambda r: -r[2]):
            print('ERROR:   newer input %-22s %s  (%s)' % (n, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mt)), p))
        print('ERROR: staffer-network.html is STALE; run fec/surfaces/make_staffer_network_html.py, then combine')
        sys.exit(1)
if not STF_PRESENT:
    print('note: staffer-network.html absent; the staffer panel is not merged')

def read(p): return io.open(os.path.join(B, p), encoding='utf-8').read()

sna = read('Moneyball Dashboard.html')
md  = read('cu-member-desk.html')
from surfaces.honors_publication import validate_pages, mark_page
_honors_generation = validate_pages(B, ('Moneyball Dashboard.html', 'cu-member-desk.html'),
    {'Moneyball Dashboard.html': sna, 'cu-member-desk.html': md})
ind = read('lobby-network.html')
stf = read('staffer-network.html') if STF_PRESENT else ''

def extract(t):
    css  = re.search(r'<style>(.*?)</style>', t, re.S).group(1)
    body = re.search(r'<body[^>]*>(.*?)<script>', t, re.S).group(1)
    js   = re.search(r'<script>(.*?)</script>', t, re.S).group(1)
    return css, body, js

def extract_multi(t):
    """Same contract as extract(), for a page with more than one <style> and a JSON
    data island.  The app script is the LAST bare <script>...</script>; everything
    between <body> and it — including <script id=page-data type=application/json> —
    is body."""
    css = '\n'.join(re.findall(r'<style>(.*?)</style>', t, re.S))
    hits = list(re.finditer(r'<script>(.*?)</script>', t, re.S))
    if not hits:
        raise SystemExit('ERROR: no application <script> found in the industry surface')
    js = hits[-1].group(1)
    body = t[re.search(r'<body[^>]*>', t).end():hits[-1].start()]
    return css, body, js

snacss, snabody, snajs = extract(sna)
# DESK_SPLIT: the standalone desk defers profile-heavy data to a sibling file whose <script src>
# tag sits AFTER the app script, so extract() never sees it (by design). Moneyball.html stays
# ONE file by contract: inline the profiles content right after the desk IIFE - the merge hook
# is a window global and survives the wrapping. Refuse a half-split input loudly.
mdcss,  mdbody,  mdjs  = extract(md)
indcss, indbody, indjs = extract_multi(ind)
# The staffer template is written to extract()'s contract: exactly one <style>, then body,
# then exactly one bare <script> carrying `const SDATA=...` and the app.
stfcss, stfbody, stfjs = extract(stf) if STF_PRESENT else ('', '', '')

# ---- ID collision guard (scan body AND js, since some ids are built in script templates) ----
def ids(t):
    return set(re.findall(r'\bid=([A-Za-z0-9_-]+)', t)) | set(re.findall(r'\bid="([^"]+)"', t))
_sna_ids, _md_ids, _ind_ids = ids(snabody+snajs), ids(mdbody+mdjs), ids(indbody+indjs)
_stf_ids = ids(stfbody+stfjs)
# The staffer surface renames nothing: it prefixes every id `stf-`, so all three of its pairs
# are expected EMPTY.  Keeping them in the same table means a future staffer id that drops the
# prefix trips the same assertion as any other collision instead of silently shadowing an id.
_known = {('sna','desk'): {'backbtn'}, ('sna','ind'): {'main'}, ('desk','ind'): set(),
          ('sna','stf'): set(), ('desk','stf'): set(), ('ind','stf'): set()}
for (a, b), allowed in _known.items():
    got = {'sna': _sna_ids, 'desk': _md_ids, 'ind': _ind_ids, 'stf': _stf_ids}
    collide = got[a] & got[b]
    print('id collisions %s x %s: %s' % (a, b, sorted(collide)))
    assert collide <= allowed, 'Unexpected id collision(s) %s x %s: %s - handle before merging.' % (a, b, sorted(collide - allowed))
# rename the member-desk backbtn everywhere in the member chunks (ID attr is emitted from JS;
# the .backbtn class is member-only and stays). SNA keeps its own #backbtn.
mdbody = mdbody.replace('id=backbtn', 'id=mdback')
mdjs   = mdjs.replace('id=backbtn', 'id=mdback').replace("'#backbtn'", "'#mdback'")
# rename the industry <main id=main> (the SNA layout column owns #main).  Only the industry
# skiplink addresses it; nothing in the industry JS reads it.
assert len(re.findall(r'\bid=main\b', indbody)) == 1, 'industry id=main is no longer unique'
assert len(re.findall(r'href=#main\b', indbody)) == 1, 'industry skiplink target moved'
indbody = re.sub(r'\bid=main\b', 'id=indmain', indbody)
indbody = re.sub(r'href=#main\b', 'href=#indmain', indbody)

# =============================================================================================
# NAVIGATION SHELL -- option A, "one shell header" (2026-09-07, owner's pick off the mockups)
# =============================================================================================
# Before this, the merged document carried FOUR product headers stacked one per panel, each with
# its own copy of the brand glyph and its own set of cross-surface links, and the industry panel
# additionally carried a "Melange surfaces" tabstrip that duplicated those links a second time
# (with the result that `aria-current=page` was announced twice for Industry).  Option A replaces
# all of that with ONE bar above the four panels: brand once, a five-tab surface strip once, the
# promoted "Skip to content" link, and the desk's freshness bar.
#
# WHY THE SURGERY IS HERE AND NOT IN THE FOUR PRODUCERS.  The four templates do not only feed
# this merge -- they also build the STANDALONE pages (Moneyball Dashboard.html,
# cu-member-desk.html, the five-page lobby-* family, staffer-network.html), which are opened on
# their own and have no shell to fall back on.  Deleting the panel chrome from the templates
# would strand every one of those.  Three measured consequences, any one of which is decisive:
#   1. lobby-interactions.html is deliberately NOT merged (see the controller's click intercept
#      below) and is built by the SAME _product_header() as the merged industry page.  Removing
#      the "Melange surfaces" strip there deletes the only route off that page, permanently.
#   2. The kernel suite pins this chrome IN THE TEMPLATES, not in the merged document:
#      fec/tests/infra/test_bug_report_regressions.py (deskhome in the desk header; the industry
#      product-header's dashboard + desk hrefs), fec/tests/lobbying/test_lda_rebuild.py
#      (aria-label="Melange surfaces" and exactly one brand-glyph per family page),
#      fec/tests/lobbying/test_lobbyview_network.py and
#      fec/tests/surfaces/test_combined_staffer_section.py [f]/[g] (the dashboard and staffer
#      header anchors).  Editing the templates turns those red by design -- they exist precisely
#      to stop a surface becoming a one-way trap.
#   3. The member desk cannot be rebuilt cheaply (make_member_desk.py rewrites a ~69 MB bundle),
#      and this script reads the BUILT cu-member-desk.html.  A desk-template edit would be inert
#      until that rebuild, so the desk half of the shell could not ship from the templates today
#      in any case.
# So the templates and every standalone page are left EXACTLY as they were, and the duplicated
# chrome is removed from the extracted per-surface bodies here, where "duplicated" is actually
# true.  This file already does precisely this kind of per-surface body surgery a few lines up
# (backbtn -> mdback, id=main -> id=indmain) and in assemble() (the email variant's stflink), and
# it uses the same discipline: an exact-count assertion per anchor, so a producer that changes
# its markup fails LOUDLY here instead of silently shipping a doubled navigation bar.
#
# NOTHING IS DELETED, only relocated: every visible string the shell drops from a panel is
# restated verbatim in the shell (Main dashboard / Member desk / Industry / Staffer network /
# Filings / Skip to content / Melange), the desk's four file-IO controls and #povhint are folded
# behind a disclosure rather than removed, the industry panel keeps its own "Industry pages"
# family strip, the staffer panel keeps its probe-gated "Live staffer view" link, and
# #integritybar moves with its id intact so the desk's own JS
# (document.getElementById('integritybar').innerHTML=...) still fills it.
#
# CASCADE.  The shell CSS is UNLAYERED (appended to shell_css below, after the @layer blocks):
# the three surface panels sit under `all:revert` resets and the desk stylesheet is global inside
# @layer desk, so the shell inherits nothing it can rely on and states every property it cares
# about; unlayered normal declarations out-rank every layered one regardless of specificity.
# The shell is a <div>, NOT a <header>: the desk print sheet hides a bare `header` with
# !important from inside @layer desk, and for !important declarations layer order REVERSES, so a
# layered !important beats an unlayered one and a <header> shell could not be un-hidden from
# here.  @media print{#melchrome{display:none!important}} is kept so the shipped one-pager still
# prints the member view and no chrome.

_MEL_GLYPH = ('<svg class=brand-glyph width="19" height="31" viewBox="0 0 128 208" aria-hidden="true">'
              '<path d="M108 20 L64 64 L28 104 L64 144 L20 188 M64 64 L100 104 L64 144" fill="none" '
              'stroke="#EECD5C" stroke-width="19" stroke-linejoin="miter" stroke-miterlimit="6"/></svg>')
_MEL_STF_GLYPH   = _MEL_GLYPH.replace('class=brand-glyph', 'class=stf-brand')
_MEL_SHELL_GLYPH = _MEL_GLYPH.replace('class=brand-glyph', 'class=melglyph')
# the desk freshness bar, moved bodily into the shell.  Same id, same inline style, so the desk's
# own innerHTML write still lands and --line/--muted still resolve (both are declared on the
# desk stylesheet's global :root, and a custom property inside @layer desk still inherits).
_MEL_INTEGRITY = ('<div id=integritybar style="padding:7px 18px;background:#282113;'
                  'border-bottom:1px solid var(--line);font-size:11px;color:var(--muted)"></div>')


def _mel_once(text, old, new, label):
    n = text.count(old)
    assert n == 1, (
        'navigation shell: expected exactly 1 occurrence of the %s anchor, found %d.\n'
        'The producer that emits it has changed.  Update the anchor in make_combined.py to the '
        'new markup -- do NOT relax this to a tolerant replace, or the merged page silently '
        'ships two navigation bars.' % (label, n))
    return text.replace(old, new, 1)


def _mel_cut(text, old, label):
    return _mel_once(text, old, '', label)


# ---- 1. the network panel: brand glyph + the three cross-surface links out of its <header> ----
# The <h1> text stays; only the duplicated glyph in front of it goes.  Neither the SNA script nor
# its stylesheet references #desklink / #lobnetlink / #stflink (verified by grep over the built
# page's IIFE), so removing the anchors costs no behaviour.  #stflink is not lost: it is
# RELOCATED onto the shell's Staffer network tab below, which is what assemble() strips for the
# three-surface email variant.
snabody = _mel_cut(snabody, _MEL_GLYPH, 'network brand glyph')
snabody = _mel_cut(snabody,
    '<a id=desklink class=desklink href="cu-member-desk.html">Member desk ▸</a>'
    '<a id=lobnetlink class=desklink href="lobby-network.html">Industry ▸</a>'
    '<a id=stflink class=desklink href="staffer-network.html">Staffer network ▸</a>',
    'network cross-surface links')

# ---- 2. the member desk: brand glyph, two cross-surface links, folded file-IO, freshness bar ---
# The desk keeps a SECOND "Main dashboard" route inside the member profile view, emitted from its
# own JS as <a class="netlink deskhome" ...> with no id; that one is untouched, so a member
# profile is still not a dead end.
mdbody = _mel_cut(mdbody, _MEL_GLYPH, 'desk brand glyph')
mdbody = _mel_cut(mdbody,
    '<a class="netlink deskhome" id=deskhome href="Moneyball Dashboard.html">◀ Main dashboard</a>',
    'desk Main dashboard link')
mdbody = _mel_cut(mdbody,
    '<a class=deskhome id=deskindustry href="lobby-network.html">Industry ▸</a>',
    'desk Industry link')
# The four file-IO controls plus #povhint fold into one disclosure.  They are all addressed by id
# from the desk script ($('#exportbtn'), $('#importfile'), $('#povexport'), $('#povimport'),
# $('#povhint')), so wrapping them changes no wiring; the <label for=...> pairs keep working
# because the inputs move inside the same <details> as their labels.  "Notes and overrides" is
# the one genuinely NEW visible string option A introduces -- a control that did not exist before
# cannot have an existing label.
_mel_fold_re = re.compile(r'<button class=ghostbtn id=exportbtn>.*?'
                          r'<span id=povhint style="color:var\(--faint\);font-size:11px"></span>', re.S)
_mel_fold = _mel_fold_re.search(mdbody)
assert _mel_fold, ('navigation shell: the desk file-IO control run (#exportbtn ... #povhint) was '
                   'not found in cu-member-desk.html; update the anchor in make_combined.py')
mdbody = _mel_once(mdbody, _mel_fold.group(0),
                   '<details class=melfold><summary>Notes and overrides ▾</summary>'
                   '<div class=melfoldbody>' + _mel_fold.group(0) + '</div></details>',
                   'desk file-IO run')
mdbody = _mel_cut(mdbody, _MEL_INTEGRITY, 'desk freshness bar')

# ---- 3. the industry panel: brand glyph, the "Melange surfaces" strip, the skip link ----
# The strip IS the shell strip now; removing it also ends the doubled aria-current on Industry
# (the panel marked itself current while the shell marks the active surface).  The panel's own
# "Industry pages" family strip and its "Page shortcuts" sitenav STAY -- they are within-family
# navigation that the shell does not carry.  The skiplink is promoted, not dropped; note this
# runs AFTER the id=main -> id=indmain rename above, so the anchor matches the renamed href.
indbody = _mel_cut(indbody, _MEL_GLYPH, 'industry brand glyph')
indbody = _mel_cut(indbody,
    '<nav class=tabstrip aria-label="Melange surfaces">'
    '<a class=tab href="Moneyball Dashboard.html"><span class=tabicon aria-hidden=true>◧</span>Main dashboard</a>'
    '<a class=tab href="cu-member-desk.html"><span class=tabicon aria-hidden=true>▤</span>Member desk</a>'
    '<a class=tab aria-current=page href="lobby-network.html"><span class=tabicon aria-hidden=true>◈</span>Industry</a>'
    '<a class=tab href="lobby-interactions.html"><span class=tabicon aria-hidden=true>▦</span>Filings</a>'
    '</nav>',
    'industry Melange surfaces strip')
indbody = _mel_cut(indbody, '<a class=skiplink href=#indmain>Skip to content</a>', 'industry skip link')

# ---- 4. the staffer panel: brand glyph + the cross-surface links ----
# #stf-livelink ("Live staffer view") STAYS: it is revealed only by the panel's own no-cors probe
# of the optional local 8841 service, and its href is written by that probe's callback.
#
# TWO TEMPLATES, TWO EXACT ANCHORS (2026-09-07).  make_staffer_network_html_00.tpl (the network
# surface) emits three cross-surface links; make_staffer_network_html_01.tpl (the D-022 groups
# surface) emits only "Main dashboard", because the shell below already carries Member desk and
# Industry.  This is NOT the tolerant replace _mel_once warns about:
#   * each candidate is still required to match EXACTLY once,
#   * the LONGER run is tried first -- the _01 anchor is a prefix of the _00 run, so that order
#     is the whole reason a _00 page is not cut short and left with two dangling links, and
#   * if neither candidate matches exactly once the build STOPS and names both counts.
# So the merged page still cannot ship two navigation bars, and a third template shape is a
# loud failure rather than a silent one.
_STF_LINK_MAIN = ('<a id=stf-netlink class=stf-desklink href="Moneyball Dashboard.html">'
                  'Main dashboard ▸</a>')
_STF_LINKS_00 = (_STF_LINK_MAIN
                 + '<a id=stf-desk class=stf-desklink href="cu-member-desk.html">Member desk ▸</a>'
                 + '<a id=stf-indlink class=stf-desklink href="lobby-network.html">Industry ▸</a>')
_STF_LINK_ANCHORS = ((_STF_LINKS_00, 'make_staffer_network_html_00.tpl'),
                     (_STF_LINK_MAIN, 'make_staffer_network_html_01.tpl'))
if STF_PRESENT:
    stfbody = _mel_cut(stfbody, _MEL_STF_GLYPH, 'staffer brand glyph')
    _stf_anchor = next((a for a, _n in _STF_LINK_ANCHORS if stfbody.count(a) == 1), None)
    assert _stf_anchor is not None, (
        'navigation shell: the staffer cross-surface links anchor did not match any known '
        'staffer template exactly once (%s).\nThe producer that emits it has changed.  Update '
        'the anchors in make_combined.py to the new markup -- do NOT relax this to a tolerant '
        'replace, or the merged page silently ships two navigation bars.'
        % ', '.join('%s found %d' % (_n, stfbody.count(a)) for a, _n in _STF_LINK_ANCHORS))
    stfbody = _mel_cut(stfbody, _stf_anchor, 'staffer cross-surface links')

# ---- the shell itself ------------------------------------------------------------------------
# Every label is copied verbatim out of the chrome it replaces.  The four surface glyphs
# (◧ ▤ ◈ ▦) are the industry strip's own; ▧ for Staffer network is the only new one, because the
# strip it came from predates the staffer surface.  The Main dashboard tab carries `netlink` so
# the controller's existing a.netlink handler keeps its behaviour (leaving the desk re-selects
# the member you were reading and drops ?m=).  The Staffer network tab carries id=stflink: that
# id is the document's ONE staffer route, relocated out of the network header, and assemble()
# strips exactly it for the three-surface email variant.
MELSHELL_HTML = (
 '<div id=melchrome>'
 '<a class=melskip id=melskip href=#main>Skip to content</a>'
 '<div id=melshell>'
 '<div id=melrow1>'
 '<span id=melbrand>' + _MEL_SHELL_GLYPH + 'Melange</span>'
 '<nav id=meltabs aria-label="Melange surfaces">'
 '<a class="meltab netlink" data-surface=network aria-current=page href="Moneyball Dashboard.html">'
 '<span class=meltabicon aria-hidden=true>◧</span>Main dashboard</a>'
 '<a class=meltab data-surface=member href="cu-member-desk.html">'
 '<span class=meltabicon aria-hidden=true>▤</span>Member desk</a>'
 '<a class=meltab data-surface=industry href="lobby-network.html">'
 '<span class=meltabicon aria-hidden=true>◈</span>Industry</a>'
 '<a id=stflink class=meltab data-surface=staffer href="staffer-network.html">'
 '<span class=meltabicon aria-hidden=true>▧</span>Staffer network</a>'
 '<a class=meltab href="lobby-interactions.html">'
 '<span class=meltabicon aria-hidden=true>▦</span>Filings</a>'
 '</nav>'
 '</div>'
 '<div id=melfresh>' + _MEL_INTEGRITY + '</div>'
 '</div></div>'
)

MELSHELL_JS = """<script>
/* ---- navigation shell script.  Emitted AFTER the four surface IIFEs and the route controller,
   so every window hook it uses is already published.  Three jobs:
     1. measure the shell and hand its height back to the two boxes that are pinned to the
        viewport (see HAZARD 1 in the shell CSS) -- the map canvas is sized by the SNA's resize()
        from #netwrap, so re-measure and re-call it through window.__netResize;
     2. keep aria-current on the active tab.  The CSS ALSO marks the active tab off the body mode
        class the controller already toggles, so the mark stays correct even if this script never
        runs (file:// with scripting restricted, an exception in an earlier IIFE, ...);
     3. point the promoted "Skip to content" link at the visible surface's own main region. ---- */
(function(){
 var chrome=document.getElementById('melchrome');
 function mode(){var c=document.body.classList;
   return c.contains('deskmode')?'member':c.contains('indmode')?'industry':
          c.contains('stfmode')?'staffer':'network';}
 function measure(){
   if(!chrome)return;
   var h=chrome.getBoundingClientRect().height;
   if(h<=0)return;
   /* --melshell-now: the shell height on the surface showing RIGHT NOW.  Drives the sticky
      offsets of the three headers that pin to top:0. */
   document.documentElement.style.setProperty('--melshell-now',Math.round(h)+'px');
   /* --melshell-h: the netmode height ONLY.  #app-network and its inner #app are sized against
      it, so it must never be overwritten with a height measured on another surface. */
   if(!document.body.classList.contains('netmode'))return;
   document.documentElement.style.setProperty('--melshell-h',Math.round(h)+'px');
   if(window.__netResize){try{window.__netResize();}catch(e){}}
 }
 var SKIP={network:'#main',member:'#view-ranked',industry:'#indmain',staffer:'#stf-main'};
 function sync(){
   var m=mode();
   var tabs=document.querySelectorAll('#meltabs .meltab');
   for(var i=0;i<tabs.length;i++){
     if(tabs[i].getAttribute('data-surface')===m)tabs[i].setAttribute('aria-current','page');
     else tabs[i].removeAttribute('aria-current');
   }
   var sk=document.getElementById('melskip');
   if(sk)sk.setAttribute('href',SKIP[m]||'#main');
   measure();
 }
 try{new MutationObserver(sync).observe(document.body,{attributes:true,attributeFilter:['class']});}
 catch(e){}
 window.addEventListener('resize',measure);
 if(document.readyState==='complete')sync();else window.addEventListener('load',sync);
 sync();
})();
</script>"""

# ---- scope a (flat) stylesheet under one root selector ----
_SCOPABLE_AT = ('media', 'supports', 'container')   # at-rules whose body is ordinary rules
_VERBATIM_AT = ('keyframes', '-webkit-keyframes', 'font-face', 'page', 'property', 'counter-style')
def scope_css(css, root):
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    # A statement at-rule (@charset/@import/@layer, terminated by ';' not '{') would make the
    # block walker below swallow the rule that follows it.  None of the four surfaces emit one;
    # fail loudly rather than silently drop a stylesheet if that ever changes.
    seen = set(m.group(1).lower() for m in re.finditer(r'@([a-zA-Z-]+)', css))
    unknown = seen - set(_SCOPABLE_AT) - set(_VERBATIM_AT)
    assert not unknown, 'unhandled at-rule(s) %s in the %s stylesheet - extend scope_css' % (sorted(unknown), root)
    def scope_flat(block):                                  # scope selectors in a block with no at-rule
        out = []
        for chunk in block.split('}'):
            chunk = chunk.strip()
            if not chunk or '{' not in chunk:
                continue
            sel, decl = chunk.split('{', 1)
            sels = []
            for s in sel.split(','):
                s = s.strip()
                if not s: continue
                if s == '*':                     sels.append(root + ' *')
                elif s in ('body','html',':root'):sels.append(root)
                else:                            sels.append(root + ' ' + s)
            out.append(','.join(sels) + '{' + decl + '}')
        return '\n'.join(out)
    # walk top-level; preserve at-rule wrappers.  @media/@supports/@container get their INNER
    # selectors scoped and the wrapper kept (the old split-on-'}' destroyed @media blocks);
    # every other at-rule (@keyframes, @font-face) is passed through VERBATIM, because its body
    # is not a selector list - scoping `from`/`to` would silently kill the animation.
    out = []; i = 0; n = len(css)
    while i < n:
        m = re.match(r'\s*@([a-zA-Z-]+)[^{]*\{', css[i:])
        if m:
            at = m.group(1).lower()
            start = i + m.end(); depth = 1; j = start
            while j < n and depth > 0:
                if css[j] == '{': depth += 1
                elif css[j] == '}': depth -= 1
                j += 1
            if at in _SCOPABLE_AT:
                out.append(css[i:start] + scope_flat(css[start:j-1]) + '}')
            else:
                out.append(css[i:j])                          # verbatim: @keyframes, @font-face, ...
            i = j
        else:
            nxt = n
            for mm in re.finditer(r'@[a-zA-Z-]+', css[i:]):
                nxt = i + mm.start(); break
            out.append(scope_flat(css[i:nxt]))
            i = nxt
    return '\n'.join(out)
snacss_scoped = scope_css(snacss, '#app-network')
indcss_scoped = scope_css(indcss, '#app-industry')
stfcss_scoped = scope_css(stfcss, '#app-staffer') if STF_PRESENT else ''

# The member-desk stylesheet is global, and layer order alone does NOT make the industry surface
# render as authored: layers only decide WHO WINS where both stylesheets speak, and the damage was
# in the gaps - properties the desk sets and the industry never mentions.  Measured against the
# standalone page (computed-style walk of all 45,810 elements, 200 distinct tag/class signatures):
# line-height 1.45 inheriting from desk body{font:13px/1.45} onto 182 elements, cursor:pointer on
# every td, h1/h2/p margins zeroed, table{min-width:680px}, .eyebrow/.toggle/.verdict/.num class
# collisions, a{text-decoration:none} killing the skiplink underline, header{flex-wrap:wrap}.
# `all:revert` first in the industry layer discards the desk's author declarations inside
# #app-industry wholesale, and every industry rule (>= 1 id + 1 more) out-specifies it, so the
# surface computes exactly as it does standalone.  Custom properties, `direction` and
# `unicode-bidi` are outside `all`, so the industry palette survives.  Two corrections the
# measurement forced:
#  - `revert` restores INHERITANCE, it cannot stop it, so desk body{font:13px/1.45} still reached
#    in.  line-height is pinned back to the standalone root value explicitly.
#  - SVG presentation attributes (fill/width/opacity on the hatch <pattern> children) live in the
#    author origin, so a blanket revert ERASED them: the bank-tier hatch went black and 0x0.
#    SVG subtrees are excluded via :where(), which contributes no specificity, so the reset stays
#    at (1,0,0) and cannot outrank an industry rule.  No desk selector matches an SVG element
#    (its bare-tag rules are a/b/body/button/h1/h2/header/html/input/select/summary/table/td/th
#    and none of its class rules land inside an <svg>), so excluding them leaks nothing.
_ind_reset = ("#app-industry{all:revert;line-height:normal;-webkit-font-smoothing:auto}\n"
              "#app-industry *:not(:where(svg,svg *)){all:revert}")
# Same reset, same reasoning, for the staffer panel: the global desk stylesheet matches its
# bare table/th/td/header/h1/h2/a/button/input elements and would otherwise win every property
# the staffer sheet does not itself set.  `all:revert` in the staffer layer discards those
# author declarations inside #app-staffer; every staffer rule carries the #app-staffer id plus
# at least one more component, so it out-specifies the (1,0,0) reset.  line-height is pinned
# because `revert` restores inheritance rather than stopping it, and SVG subtrees (the brand
# glyph's presentation attributes) are excluded through :where(), which adds no specificity.
_stf_reset = ("#app-staffer{all:revert;line-height:normal;-webkit-font-smoothing:auto}\n"
              "#app-staffer *:not(:where(svg,svg *)){all:revert}")

shell_css = (
 "html,body{margin:0;background:#1D1912;color:#F3F3E6}"
 "[hidden]{display:none!important}"
 "#app-network{height:100vh;overflow:hidden}"
 "#app-industry{min-height:100vh}"
 "#app-staffer{min-height:100vh}"
 "body.deskmode{overflow:auto}body.netmode{overflow:hidden}body.indmode{overflow:auto}"
 "body.stfmode{overflow:auto}"
 "canvas{touch-action:none}"
 "@media(max-width:640px){#app-network{height:auto;overflow:visible}body.netmode{overflow:auto}}"
)

# ---- the navigation shell's own stylesheet (option A) ----------------------------------------
# Appended to shell_css, so it lands UNLAYERED and LAST inside the single <style> assemble()
# emits.  Unlayered wins over @layer desk / industry / staffer whatever their specificity, and
# being last settles ties against the unlayered SNA sheet.  Every colour here is an existing
# dashboard palette token: #1D1912 bg, #282113 bar, #2E2514 panel, #3B2E16 panel-hover,
# #433317 rule, #564019 border, #9D9784 faint, #A49D8A muted, #D2CDBC text-soft, #D2A63C
# gold-fill, #EECD5C gold, #F3F3E6 text.  No new colours, no light theme.
MELSHELL_CSS = """
:root{--melshell-h:78px}
#melchrome{position:sticky;top:0;z-index:120;background:#1D1912;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
#melchrome *{box-sizing:border-box}
.melskip{position:absolute;left:16px;top:-72px;z-index:200;background:#D2A63C;color:#1D1912;
  padding:8px 16px;border-radius:6px;font-size:12px;font-weight:700;text-decoration:none}
.melskip:focus{top:6px;outline:2px solid #EECD5C;outline-offset:2px}
#melshell{background:#282113;border-bottom:1px solid #433317}
#melrow1{display:flex;align-items:center;gap:14px;padding:0 14px;height:46px}
#melbrand{flex:0 0 auto;display:flex;align-items:center;gap:7px;
  font-family:var(--ui-display,"Century Gothic","Tw Cen MT",Futura,"Avenir Next","Segoe UI",sans-serif);
  font-size:11px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:#D2A63C}
#melbrand .melglyph{flex:0 0 auto;display:block}
#meltabs{display:flex;align-items:center;gap:2px;flex:1 1 auto;min-width:0;flex-wrap:wrap}
.meltab{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid transparent;
  border-radius:5px;
  font-family:var(--ui-display,"Century Gothic","Tw Cen MT",Futura,"Avenir Next","Segoe UI",sans-serif);
  font-size:12px;font-weight:600;letter-spacing:.03em;
  color:#A49D8A;text-decoration:none;white-space:nowrap;background:transparent;cursor:pointer}
.meltab:hover{background:#3B2E16;color:#F3F3E6;text-decoration:none}
.meltab .meltabicon{font-size:11px;opacity:.85}
/* Active surface, marked BOTH ways on purpose: aria-current is kept in sync by the shell script
   and is already correct in the static markup for the surface the document opens on, and the
   body mode class the controller toggles marks it with no script of ours at all. */
.meltab[aria-current=page],
body.netmode .meltab[data-surface=network],
body.deskmode .meltab[data-surface=member],
body.indmode  .meltab[data-surface=industry],
body.stfmode  .meltab[data-surface=staffer]{background:#2E2514;border-color:#564019;color:#F3F3E6;
  box-shadow:inset 0 2px 0 #EECD5C}
#melfresh{border-top:1px solid #433317}
#melfresh #integritybar{border-bottom:0}

/* HAZARD 1: TWO boxes are pinned to the viewport, not one, and fixing only the outer one leaves
   the map CROPPED rather than blank -- measured on the mockup: with the shell at 105px the panel
   correctly became 771px while the canvas stayed 788px and the bottom of the map was clipped by
   #app-network{overflow:hidden}.  The inner box is the dashboard's own
   #app{grid-template-rows:auto auto 1fr;height:100vh;height:100dvh}
   (fec/surfaces/templates/make_dashboard_html_00.tpl:9), and the SNA's resize() sizes the canvas
   from cv.parentElement (#netwrap), which is that grid's 1fr row -- so the canvas follows #app,
   not #app-network.  Both are given back exactly what the shell takes.  The 100dvh line is
   repeated after the 100vh line because the source does the same and dvh must stay the winner
   where it is supported.  --melshell-h is measured at runtime by the shell script and
   re-measured on resize; the 78px seed only has to keep first paint close.  The 640px rules are
   restated here because they must stay AFTER these overrides in source order. */
#app-network{height:calc(100vh - var(--melshell-h));height:calc(100dvh - var(--melshell-h));overflow:hidden}
#app-network #app{height:calc(100vh - var(--melshell-h));height:calc(100dvh - var(--melshell-h))}
/* these two are NOT the map: they are sized against the shell height on the surface actually
   showing, because --melshell-h is deliberately measured in netmode only and goes stale for them
   after a window resize on another surface (measured: shell 78px, --melshell-h left at 94px from
   an earlier netmode width, industry min-height 16px short). */
#app-industry,#app-staffer{min-height:calc(100vh - var(--melshell-now,var(--melshell-h)))}
@media(max-width:640px){#app-network{height:auto;overflow:visible}body.netmode{overflow:auto}
  #app-network #app{height:auto;min-height:calc(100vh - var(--melshell-h));min-height:calc(100dvh - var(--melshell-h))}}

/* HAZARD 3: anything the four surfaces stick to the TOP OF THE VIEWPORT must now stick below the
   shell rather than under it.  Three elements pin to top:0 -- the industry product-header, the
   industry table heads and the desk table heads -- but only ONE of them actually sticks to the
   viewport, and offsetting the other two is not merely useless, it is harmful.  Measured in the
   merged document (Chrome, 1280x720):
     * #app-member thead th   -> nearest scroll container: the VIEWPORT.  Needs the offset, and
                                 gets it.  Without it the desk's table heads slide under the bar.
     * #app-industry .product-header -> nearest scroll container: #app-industry ITSELF.  The
       industry sheet's body{overflow:auto} is rewritten to #app-industry{overflow:auto} by
       scope_css, so the panel is its own scrollport -- and one that never scrolls, because the
       panel is only ever min-height-constrained (scrollHeight == clientHeight, measured).  Its
       sticky is therefore inert here, its scrollport already starts BELOW the shell, and a
       top offset only pushes the header down the page: measured 78px of dead space above the
       industry header (panel-relative top 78 instead of 0) for no protection at all.
     * #app-industry thead th -> nearest scroll container: .tablewrap{overflow:auto}.  Same
       story, plus a live hazard: any table head whose natural position is within the offset of
       its own wrapper's top would be shoved down inside the table.
   So the offset is applied to the desk heads ONLY.  --melshell-now rather than --melshell-h
   because the latter is measured in netmode only (it feeds the map's height calc) and is stale
   on every other surface. */
#app-member thead th{top:var(--melshell-now,var(--melshell-h))}

/* HAZARD 2: the desk print sheet hides a bare `header` with !important from inside @layer desk,
   and for IMPORTANT declarations layer order is REVERSED -- a layered !important beats an
   unlayered one -- so a <header> shell could not be un-hidden from here.  The shell is a <div>,
   which that selector cannot match, and print behaviour is preserved deliberately: the shipped
   one-pager prints the member view and no chrome. */
@media print{#melchrome{display:none!important}}

/* the desk's folded file-IO disclosure */
details.melfold{position:relative;display:inline-block}
details.melfold>summary{list-style:none;cursor:pointer;background:#1D1912;color:#A49D8A;
  border:1px solid #564019;border-radius:6px;padding:6px 11px;font-size:12px;display:inline-block}
details.melfold>summary::-webkit-details-marker{display:none}
details.melfold>summary:hover{border-color:#D2A63C;color:#F3F3E6}
details.melfold[open]>summary{border-color:#D2A63C;color:#F3F3E6}
details.melfold>.melfoldbody{position:absolute;left:0;top:calc(100% + 6px);z-index:90;
  display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding:12px;min-width:250px;
  background:#282113;border:1px solid #564019;border-radius:8px;
  box-shadow:0 10px 30px rgba(18,14,7,.55)}
"""
shell_css = shell_css + MELSHELL_CSS

# ---- wrap each script in an IIFE + export cross-app hooks ----
snajs_wrapped = ("(function(){\n" + snajs +
  "\n/* APPROVED network BRIDGE */\n" + io.open(os.path.join(os.path.dirname(__file__), 'approved', 'network-bridge.js'), encoding='utf-8').read() + "\n/* END APPROVED network BRIDGE */\n" +
  "\nwindow.__netResize=resize;"
  "window.__netSelect=function(n){var i=DATA.name.indexOf(n);if(i>=0)selectMember(i);};\n})();")
# window.__netCoalition is now defined inside the SNA dashboard template itself (loadCoalition): it highlights the
# coalition members (white ring), frames the camera, and colors the field by who's likely to join. gotoNetworkCoalition
# below calls it. (The old inline version here predated the route/cascade rewrite and is intentionally removed.)
mdprof = ''
if '__melangeProfileMerge' in mdjs:
    _pp = os.path.join(B, 'cu-member-desk-profiles.js')
    if not os.path.exists(_pp):
        raise SystemExit('ERROR: desk is split (__melangeProfileMerge present) but '
                         'cu-member-desk-profiles.js is missing - rebuild the desk first')
    mdprof = "<script>\n" + io.open(_pp, encoding='utf-8').read() + "\n</script>"
mdjs_wrapped = ("(function(){\n" + mdjs +
  "\n/* APPROVED desk BRIDGE */\n" + io.open(os.path.join(os.path.dirname(__file__), 'approved', 'desk-bridge.js'), encoding='utf-8').read() + "\n/* END APPROVED desk BRIDGE */\n" +
  "\nwindow.__deskOpen=function(n){var i=byName[n];if(i!=null)openMember(i);};"
  "window.__deskShow=function(v){setView(v||'ranked');};\n})();")
# the industry script opens with its own "use strict" directive; a leading newline keeps it in the
# function's directive prologue, so the IIFE stays strict exactly as the standalone page is.
indjs_wrapped = "(function(){\n" + indjs + "\n})();"
# The staffer app already publishes __stafferOpen/__stafferShow from inside its own IIFE (it
# needs its private select()/drawStation()).  The outer wrapper keeps `const SDATA` off the
# global scope and re-declares the two hooks defensively, so the controller's contract is
# visible here beside the other three surfaces and a panel that failed to initialise degrades
# to a no-op switch rather than a TypeError.
stfjs_wrapped = ("(function(){\n" + stfjs +
  "\nwindow.__stafferOpen=window.__stafferOpen||function(name){};"
  "window.__stafferShow=window.__stafferShow||function(){};\n})();")

controller = """
(function(){
 function showApp(w){
  document.getElementById('app-network').hidden=(w!=='network');
  document.getElementById('app-member').hidden=(w!=='member');
  document.getElementById('app-industry').hidden=(w!=='industry');
  // the staffer panel is optional (absent when no verified generation is in the tree)
  var stf=document.getElementById('app-staffer');if(stf)stf.hidden=(w!=='staffer');
  document.body.classList.toggle('netmode',w==='network');
  document.body.classList.toggle('deskmode',w==='member');
  document.body.classList.toggle('indmode',w==='industry');
  document.body.classList.toggle('stfmode',w==='staffer');
  // the industry stylesheet asks for html{scroll-behavior:smooth}; scoping moved that onto the
  // panel, which is not the scroll container here, so drive it from the controller instead.
  try{document.documentElement.style.scrollBehavior=(w==='industry')?'smooth':'';}catch(x){}
  if(w==='network'&&window.__netResize)window.__netResize();
 }
 // history: every cross-surface switch is a real entry, so Back walks the four surfaces.
 // file:// has an opaque origin and throws on pushState — degrade to a plain view switch.
 function push(u){try{history.pushState(null,'',u);}catch(x){}}
 window.gotoMember=function(name,nopush){if(!nopush)push('?m='+encodeURIComponent(name));showApp('member');if(window.__deskOpen)window.__deskOpen(name);window.scrollTo(0,0);};
 window.gotoNetwork=function(name,nopush){if(!nopush)push(location.pathname);showApp('network');if(name&&window.__netSelect)window.__netSelect(name);};
 window.gotoNetworkCoalition=function(names){showApp('network');if(window.__netCoalition)window.__netCoalition(names);window.scrollTo(0,0);};
 window.gotoMemberDesk=function(view,nopush){if(!nopush)push(location.pathname+'?v=desk');showApp('member');if(window.__deskShow)window.__deskShow(view||'ranked');window.scrollTo(0,0);};
 window.gotoIndustry=function(nopush){if(!nopush)push(location.pathname+'?v=industry');showApp('industry');window.scrollTo(0,0);};
 // gotoStaffer is defined in EVERY build, including the ones with no staffer panel: the
 // three-surface email variant (Moneyball_Email.html) and any tree with no verified staffer
 // generation.  Without the guard it would push ?v=staffer and then hide all of the panels
 // that DO exist, leaving a blank document; fall back to the network instead, passing nopush
 // through so a Back/Forward-driven call still does not push a duplicate entry.
 window.gotoStaffer=function(name,nopush){var stf=document.getElementById('app-staffer');if(!stf){window.gotoNetwork(null,nopush);return;}if(!nopush)push(location.pathname+'?v=staffer'+(name?'&s='+encodeURIComponent(name):''));showApp('staffer');if(window.__stafferOpen)window.__stafferOpen(name||null);window.scrollTo(0,0);};
 document.addEventListener('click',function(e){
  if(!e.target.closest)return;
  if(e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;   // let ctrl/cmd/middle-click open a real tab
  // the desk's "Main dashboard" link keeps its special case: it re-selects the member you were
  // reading and drops ?m= so a refresh lands on the network again.
  var nl=e.target.closest('a.netlink');
  if(nl){e.preventDefault();var open=!document.getElementById('app-member').hidden;
    var h2=document.querySelector('#app-member .mhead h2');
    var nm=open&&h2?h2.textContent.trim():null;
    window.gotoNetwork(nm);return;}
  // ANY anchor addressing another merged surface switches views in-page instead of re-fetching
  // the ~37MB document: the network's #mdlink chip and #lobnetlink, every member name on the
  // network side (a.mlink / a.mprof) and on the industry side (a.memberlink), the desk's
  // #deskindustry, and the industry tabstrip.  Anything else — notably the interaction ledger
  // at lobby-interactions.html, which is NOT merged — falls through to a real navigation.
  var an=e.target.closest('a[href]');
  if(!an)return;
  var h=an.getAttribute('href')||'';
  if(h.indexOf('cu-member-desk.html')===0){
    e.preventDefault();var m=null;
    try{m=new URL(an.href,location.href).searchParams.get('m');}catch(x){}
    if(m)window.gotoMember(m);else window.gotoMemberDesk('ranked');return;}
  if(h.indexOf('lobby-network.html')===0){e.preventDefault();window.gotoIndustry();return;}
  if(h.indexOf('staffer-network.html')===0&&document.getElementById('app-staffer')){
    e.preventDefault();var sn=null;
    try{sn=new URL(an.href,location.href).searchParams.get('s');}catch(x){}
    window.gotoStaffer(sn);return;}
  if(h.indexOf('Moneyball Dashboard.html')===0){e.preventDefault();window.gotoNetwork();return;}
 });
 // Back/Forward.  Read the surface off the URL, not off history.state: the desk's own
 // openMember() calls replaceState(null,...) and would wipe any state object we stored.
 function surfaceFromURL(){
  var sp;try{sp=new URLSearchParams(location.search);}catch(x){return null;}
  var v=sp.get('v');
  if(v==='industry')return {w:'industry'};
  // ?v=staffer&s=<Name>.  A build with no staffer panel falls back to the network rather
  // than switching to a surface that is not in this document.
  if(v==='staffer')return document.getElementById('app-staffer')?{w:'staffer',s:sp.get('s')}:{w:'network'};
  if(v==='desk')return {w:'member'};
  var m=sp.get('m');if(m)return {w:'member',m:m};
  return {w:'network'};
 }
 window.addEventListener('popstate',function(){
  var s=surfaceFromURL();if(!s)return;
  if(s.w==='member'){if(s.m)window.gotoMember(s.m,true);else window.gotoMemberDesk('ranked',true);}
  else if(s.w==='industry')window.gotoIndustry(true);
  else if(s.w==='staffer')window.gotoStaffer(s.s||null,true);
  else window.gotoNetwork(null,true);
 });
 var s0=surfaceFromURL()||{w:'network'};
 if(s0.w==='member'&&s0.m){window.gotoMember(s0.m,true);}
 else if(s0.w==='industry'){window.gotoIndustry(true);}
 else if(s0.w==='staffer'){window.gotoStaffer(s0.s||null,true);}
 else if(s0.w==='member'){window.gotoMemberDesk('ranked',true);}
 else if(innerWidth<=700){window.gotoMemberDesk('ranked',true);}
 else{showApp('network');}
})();
"""

def assemble(pieces, include_staffer):
    """Concatenate the extracted surface pieces into ONE self-contained document.

    PURE: it reads no file, writes no file and touches no module-level state -- every input
    arrives in `pieces` -- so the four-surface deliverable and the three-surface email variant
    are two calls over the SAME extraction rather than two builds.  That is the whole point:
    there is no second parse, no second staleness contract, and no way for the two documents
    to disagree about a surface.

    `include_staffer` False omits exactly three things from the merge itself: the staffer
    stylesheet layer, the #app-staffer panel, and the staffer IIFE.  Everything else -- the
    head, the `@layer desk,industry,staffer` declaration (an unused layer name is inert CSS
    and keeping it makes the two heads byte-identical), the shell CSS, the desk and industry
    panels and their scripts, and the whole controller including gotoStaffer -- is identical
    in both.  The controller reaches the panel only through runtime lookups
    (`getElementById('app-staffer')` in showApp, gotoStaffer, the href intercept and
    surfaceFromURL), so the same text is correct with or without it.

    The ONE exception is the document's single staffer route, `<a id=stflink ...>`, which is
    built independently of `include_staffer` -- a link the email variant cannot honour, since
    Moneyball_Email.html ships with no #app-staffer panel and the zip carries no
    staffer-network.html for it to open.  When `include_staffer` is False that one anchor is
    stripped before concatenation, so a recipient never gets a dead navigation; the True path
    never touches the piece it lives in.  (2026-09-07: that anchor used to sit in the network
    panel's own header, emitted by make_dashboard_html_00.tpl into `snabody`.  Navigation-shell
    option A moved the four panels' cross-surface links into the one shell bar, so it is now the
    shell's Staffer network tab -- same id, same job, same treatment here.)

    Because this is pure concatenation, `assemble(pieces, True)` is exactly the pre-refactor
    expression; fec/tests/surfaces/test_combined_email_variant.py pins that against an
    independent transcription of it.
    """
    import re as _re
    mdcss         = pieces['mdcss']
    snacss_scoped = pieces['snacss_scoped']
    indcss_scoped = pieces['indcss_scoped']
    stfcss_scoped = pieces['stfcss_scoped']
    _ind_reset    = pieces['ind_reset']
    _stf_reset    = pieces['stf_reset']
    shell_css     = pieces['shell_css']
    snabody       = pieces['snabody']
    mdbody        = pieces['mdbody']
    indbody       = pieces['indbody']
    stfbody       = pieces['stfbody']
    snajs_wrapped = pieces['snajs_wrapped']
    mdjs_wrapped  = pieces['mdjs_wrapped']
    mdprof        = pieces['mdprof']
    indjs_wrapped = pieces['indjs_wrapped']
    stfjs_wrapped = pieces['stfjs_wrapped']
    controller    = pieces['controller']
    melshell      = pieces['melshell']
    melshell_js   = pieces['melshell_js']
    # The merge expressions below are the pre-refactor ones VERBATIM.  This alias is what lets
    # them stay that way -- and what keeps the source-level contract suite
    # (fec/tests/surfaces/test_combined_staffer_section.py) pinning the text a browser depends on.
    STF_PRESENT = include_staffer
    # EMAIL-LINK FIX (2026-09-05, retargeted 2026-09-07): the document's one staffer-network.html
    # anchor is baked in unconditionally, so it is present no matter which document is being
    # built.  Pin that with an exact-count assertion before touching anything, then -- for the
    # email variant only -- remove exactly that one anchor and re-assert it is gone.  The True
    # path never enters the `if`, so `melshell` (and therefore the returned document) is
    # untouched there.  The anchor lives in the shell's Staffer network tab since option A moved
    # the cross-surface links out of the four panel headers; the tab wraps its glyph in a <span>,
    # so the pattern is non-greedy-to-</a> rather than the old `[^<]*` body match.
    _stflink_re = _re.compile(r'<a id=stflink\b.*?</a>', _re.S)
    assert len(_stflink_re.findall(melshell)) == 1, (
        'expected exactly one stflink anchor in the navigation shell, found %d'
        % len(_stflink_re.findall(melshell)))
    if not include_staffer:
        melshell = _stflink_re.sub('', melshell, count=1)
        assert not _stflink_re.search(melshell), 'stflink anchor survived removal from the email variant'
    return (
     "<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
     "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
     "<meta name=referrer content=no-referrer>"
     "<title>Melange — Influence Dashboard</title>"
     """<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 208'><path d='M108 20L64 64L28 104L64 144L20 188M64 64L100 104L64 144' fill='none' stroke='%23EECD5C' stroke-width='24' stroke-linejoin='miter' stroke-miterlimit='6'/></svg>">"""
     "<style>\n@layer desk,industry,staffer;\n"
     "@layer desk{\n" + mdcss + "\n}\n"
     + snacss_scoped + "\n"
     "@layer industry{\n" + _ind_reset + "\n" + indcss_scoped + "\n}\n"
     + ("@layer staffer{\n" + _stf_reset + "\n" + stfcss_scoped + "\n}\n" if STF_PRESENT else "")
     + shell_css + "</style></head>"
     "<body class=netmode>" + melshell +
     "<div id=app-network>" + snabody + "</div>"
     "<div id=app-member hidden>" + mdbody + "</div>"
     "<div id=app-industry hidden>" + indbody + "</div>"
     + ("<div id=app-staffer hidden>" + stfbody + "</div>" if STF_PRESENT else "") +
     "<script>\n" + snajs_wrapped + "\n</script>"
     "<script>\n" + mdjs_wrapped + "\n</script>" + mdprof +
     "<script>\n" + indjs_wrapped + "\n</script>"
     + ("<script>\n" + stfjs_wrapped + "\n</script>" if STF_PRESENT else "") +
     "<script>" + controller + "</script>" + melshell_js +
     "</body></html>"
    )


def email_profiles(mdprof):
    """The desk profiles <script> for the emailed variant: same payload, bill_lobbying nulled.

    PURE, like assemble(): str in, str out, no module state (fec/tests/surfaces/
    test_combined_email_variant.py lifts it into an empty namespace).  make_member_desk.py writes
    `window.__melangeProfileMerge({"members_heavy":[...],"bill_lobbying":{...}});` with the
    sidecar as the LAST key, and the desk's merge hook assigns D.bill_lobbying from it and treats
    a missing one as "no lobbying block" (billLobbyingBlocks returns '' when bill.lobbying is
    falsy).  So replacing that one value with null drops ~800 KB compressed and nothing else.
    It refuses, rather than guessing, if the payload is not that shape: exactly one key, and the
    value it names must be the final thing before the closing `});` -- checked by decoding it.
    """
    if not mdprof:                      # DESK_SPLIT=0 build: no profiles script, nothing to drop
        return mdprof
    import json as _json
    key, tail = ',"bill_lobbying":', '});\n</script>'
    assert mdprof.count(key) == 1, (
        'expected exactly one bill_lobbying key in the desk profiles payload, found %d' % mdprof.count(key))
    assert mdprof.endswith(tail), 'desk profiles payload no longer ends with the merge call'
    start = mdprof.index(key)
    _, end = _json.JSONDecoder().raw_decode(mdprof, start + len(key))
    assert end == len(mdprof) - len(tail), 'bill_lobbying is not the last value in the desk profiles payload'
    out = mdprof[:start]
    out += key + 'null' + tail
    return out


PIECES = {
 'mdcss': mdcss, 'snacss_scoped': snacss_scoped, 'indcss_scoped': indcss_scoped,
 'stfcss_scoped': stfcss_scoped, 'ind_reset': _ind_reset, 'stf_reset': _stf_reset,
 'shell_css': shell_css, 'snabody': snabody, 'mdbody': mdbody, 'indbody': indbody,
 'stfbody': stfbody, 'snajs_wrapped': snajs_wrapped, 'mdjs_wrapped': mdjs_wrapped,
 'mdprof': mdprof, 'indjs_wrapped': indjs_wrapped, 'stfjs_wrapped': stfjs_wrapped,
 'controller': controller, 'melshell': MELSHELL_HTML, 'melshell_js': MELSHELL_JS,
}
HTML = assemble(PIECES, STF_PRESENT)
HTML = mark_page(HTML, _honors_generation)

# Retired 2026-09-04 and now a documented NO-OP: this used to append an external
# <script src="http://127.0.0.1:8841/staffer-live/launch.js"> to the page, which contradicts
# this file's own no-external-loads contract and took ERR_CONNECTION_REFUSED whenever that
# service was down.  The staffer network is an in-page panel now (#app-staffer above).  The
# call is kept so fec/surfaces/install_staffer_live.py stays idempotent against this builder.
from surfaces.staffer_live_integration import inject_navigation
HTML = inject_navigation(HTML)
def _output_path(env_name, default_name):
    """Allow a parallel build when a desktop sync client locks the canonical artifact."""
    value = os.environ.get(env_name, '').strip()
    if not value:
        return os.path.join(B, default_name)
    return value if os.path.isabs(value) else os.path.join(B, value)


out = _output_path('MELANGE_MONEYBALL_OUTPUT', 'Moneyball.html')
from moneyball_core import write_text_atomic
write_text_atomic(HTML,out)
print('wrote %s (%.0f KB)' % (out, os.path.getsize(out)/1024))
# Release the four-surface document before assembling the three-surface one.  These pages carry
# at least one astral code point (an emoji in the member text), so CPython stores them UCS-4:
# measured 2026-09-05, the 79.7 M-character Moneyball.html is a 304 MB str, and PIECES holds the
# same characters again.  Holding both documents at once would add ~290 MB to a peak that is
# already ~600 MB; they are never needed together, so drop the first one here.
del HTML

# The EMAIL variant.  Same pieces, staffer omitted and the desk's bill_lobbying sidecar nulled --
# see the module header for why the emailed all-in-one is the three-surface page and why it
# carries no bill-lobbying sidecar.  Only the profiles piece is swapped, and only here, after the
# four-surface document is written, so Moneyball.html keeps the whole payload.  `mdprof` is
# dropped too: it is the other reference to the full string, and freeing it keeps the peak where
# the single `del HTML` above left it.
_full_profiles = len(PIECES['mdprof'])
PIECES['mdprof'] = email_profiles(PIECES['mdprof'])
del mdprof
print('email variant: bill_lobbying sidecar dropped from the desk profiles (%d -> %d chars)'
      % (_full_profiles, len(PIECES['mdprof'])))
EMAIL_HTML = inject_navigation(assemble(PIECES, False))
EMAIL_HTML = mark_page(EMAIL_HTML, _honors_generation)
email_out = _output_path('MELANGE_MONEYBALL_EMAIL_OUTPUT', 'Moneyball_Email.html')
write_text_atomic(EMAIL_HTML, email_out)
print('wrote %s (%.0f KB)' % (email_out, os.path.getsize(email_out)/1024))
print('sizes: Moneyball.html %d B, Moneyball_Email.html %d B (staffer surface %s)'
      % (os.path.getsize(out), os.path.getsize(email_out),
         'merged' if STF_PRESENT else 'absent'))
