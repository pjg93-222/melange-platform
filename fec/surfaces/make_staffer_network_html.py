# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""Standalone "Staffer network" surface -> <root>/staffer-network.html.

Mirrors fec/surfaces/make_dashboard_html.py exactly: read one JSON artifact, drop it
into a single-file template at the __DATA__ token, write atomically.  The section data
is produced by fec/surfaces/build_staffer_network_section.py (receipt-gated, read-only)
and lands at data/engine/staffer_network_section.json.

Template contract (make_combined.extract() depends on all four points):
  * exactly ONE <style>...</style>,
  * then the <body> markup, containing no other "<script" anywhere,
  * then exactly ONE <script>...</script> carrying `const SDATA=__DATA__;` and the app,
  * every element id prefixed `stf-`, which is collision-proof against the three
    surfaces already merged into Moneyball.html (verified against the live pages:
    227 ids, none beginning "stf-").

TEMPLATES.  --template selects which one is rendered; the default is _01 as of
2026-09-07 (it was _00 before: a bare run would rebuild the retired network-only
page and silently revert the shipped surface, see run_pipeline.py's chain-step
comment for the incident this fixes). --template make_staffer_network_html_00.tpl
still renders the old network surface explicitly.

  _00  the network surface: Coverage / Member station (diagram) / Roster.
  _01  the simplified GROUPS surface (owner ruling D-022, 2026-09-06): one search
       box, offices, organisations, one closed "Sources and method" fold.  It
       carries THREE further tokens, because the Groups data is three artifacts
       rather than one (owner rulings 2026-09-07: the combined document measured
       8.29 MB against the real generation and could not be brought under the 5 MB
       producer pin by any cap, and the client ties then needed a normalized file
       of their own to fit all 41,203 of them).  A template that carries any of
       these tokens and finds no artifact for it still renders: the token is
       filled with an explicitly EMPTY payload that says so, because a missing
       optional artifact is the chain's normal state, not a failure.

       __OFFICES__ -> `const GDATA` : {meta, offices}
       __ORGS__    -> `const ODATA` : {meta, organisations}
       __CLIENTS__ -> `const CDATA` : {meta, c, cn, e, en, f, fn, fe, t, tp}

       Three islands, not one merged object, because each artifact carries its OWN
       meta -- its own generation binding, its own input sha256 list, its own
       counts, its own byte total and (for organisations) its own cap and
       singleton mode.  Merging them would force one of those metas to be thrown
       away or nested under an invented container, and the page's "Sources and
       method" fold has to be able to state all of them honestly.

--sdata says how much of the network section the page needs.  _00 renders members
and needs all of it; _01 renders none of them and needs only the provenance the
owner's language law is printed from, so shipping the member map into it would put
four megabytes of unread data on the page.  Its default follows --template rather
than being one fixed string -- full for _00, provenance for _01 (SDATA_DEFAULT_BY_
TEMPLATE) -- so a bare run stays self-consistent for whichever template it renders;
an explicit --sdata always wins over that default, in either direction, and main()
always prints the resolved value (the `sdata=` line) so a reader can tell which
narrowing actually ran.

Filename says Moneyball, per repo convention; every visible string says Melange.

Language law (owner rulings 2026-09-03/04) is carried through from the producer: the
section is exploratory and REVIEW_REQUIRED, shared staff is a count of people attached
to two offices and never a tie between two members, and the page states that in its
own legend.  Rankings and data only -- no advisory copy.

Dials (an explicit switch always wins over the environment):
  STAFFER_SECTION_DATA_PATH        network section JSON (default <root>/data/engine/staffer_network_section.json)
  STAFFER_OFFICES_DATA_PATH        offices JSON        (default <root>/data/engine/staffer_offices_section.json)
  STAFFER_ORGANISATIONS_DATA_PATH  organisations JSON  (default <root>/data/engine/staffer_organisations_section.json)
  STAFFER_CLIENTS_DATA_PATH        clients JSON        (default <root>/data/engine/staffer_clients_section.json)
  STAFFER_HTML_OUT                 output HTML         (default <root>/staffer-network.html)

  RETIRED 2026-09-07: STAFFER_GROUPS_DATA_PATH and --groups.  They named the single
  combined staffer_groups_section.json, which no producer writes any more.  They are
  not silently ignored -- see main(): a run that still sets either one is told what
  to use instead rather than quietly rendering an empty directory.

Run:  python fec/surfaces/make_staffer_network_html.py
      python fec/surfaces/make_staffer_network_html.py --template make_staffer_network_html_01.tpl \
             --sdata provenance --out preview/staffer-network.html
"""
import os as _oT
def _tpl(n): return open(_oT.path.join(_oT.path.dirname(_oT.path.abspath(__file__)),'templates',n),encoding='utf-8').read()
import argparse,json,os
from moneyball_core import json_for_script,write_text_atomic

DEFAULT_TEMPLATE='make_staffer_network_html_01.tpl'

# --sdata's default follows --template rather than being one fixed string, so a
# bare run stays self-consistent for whichever template it renders.  RULED
# 2026-09-07: DEFAULT_TEMPLATE flipped _00 -> _01 (above) but --sdata still
# defaulted to the fixed string 'full', so a bare run embedded the whole ~4.99 MB
# member map into a template that renders none of it (_01 needs only provenance)
# and blew MAX_BYTES. An explicit --sdata always overrides this map, in either
# direction -- see parse_args(). A template not listed here (a dev/preview
# template) keeps the historic 'full' default.
SDATA_DEFAULT_BY_TEMPLATE={
    'make_staffer_network_html_00.tpl':'full',
    'make_staffer_network_html_01.tpl':'provenance',
}

# THE PRODUCER PINS ARE THE BINDING LIMITS, NOT THIS ONE.  Each Groups artifact
# refuses itself above 5,000,000 bytes (fec/surfaces/build_staffer_groups_section.py
# MAX_BYTES) and the network section is called with --max-bytes 6000000 by the
# chain (fec/run_pipeline.py:205, which matches build_staffer_network_section.py's
# own default), so the data that can legitimately reach this page is bounded upstream,
# artifact by artifact, where the producer can name the offending groups.  This
# number is only a build-defect tripwire: a page that exceeds what those pins can
# legitimately produce is a bug in the assembly -- a duplicated island, an SDATA
# that was not narrowed -- and not a big dataset.  The COMBINED page
# (Moneyball.html, built by make_combined.py) has no cap of its own and never did.
#
# RAISED 2026-09-07 from 12,000,000, because the arithmetic behind the old number
# stopped being true when the Groups data became THREE artifacts.  Measured on the
# real generation that day, _01 with --sdata provenance:
#
#   offices        4,132,633
#   organisations  3,939,678
#   clients        3,892,167   (all 41,203 client organisations, all 132,738 ties)
#   sum           11,964,478
#   page          12,109,963   + the narrowed network provenance and ~28 KB of chrome
#
# So a legitimate build was refused at 12,000,000 by 109,963 bytes.  The honest
# ceiling for this template is three artifacts each sitting on its own 5,000,000
# pin -- 15,000,000 -- plus the narrowed network section and the chrome, so 16 MB
# leaves the tripwire tripping only on an assembly that produced MORE than the
# producer pins can account for.  That is what a tripwire is for; it is not a
# product dial, and the per-artifact pins are still the binding limits.
MAX_BYTES=16_000_000

# What a template that asks for __OFFICES__ / __ORGS__ gets when that artifact is not
# on disk.  Each is a valid payload with nothing in it, and it says why, so the page
# renders an honest empty directory instead of failing or silently showing zero groups.
EMPTY_OFFICES={'meta':{'absent':True,'part':'offices','counts':{'offices':0,'people_listed':0},
                       'note':'No offices section artifact was present when this page was built.'},
               'offices':[]}
EMPTY_ORGANISATIONS={'meta':{'absent':True,'part':'organisations',
                             'counts':{'organisations':0,'people_listed':0,
                                       'singleton_registrants':0},
                             'note':'No organisations section artifact was present when this '
                                    'page was built.'},
                     'organisations':[]}
# The clients island is the NORMALIZED one (2026-09-07): parallel tables and
# integer ties, never rows carrying names.  Its empty stand-in is the same shape
# with every table empty, so the page's index lookups find arrays either way and
# a missing artifact renders an honest empty client directory rather than
# throwing on `CDATA.c.length`.
EMPTY_CLIENTS={'meta':{'absent':True,'part':'clients','period_labels':[],
                       'counts':{'client_organisations':0,'contract_filers':0,
                                 'filer_client_ties':0},
                       'note':'No clients section artifact was present when this page was '
                              'built.'},
               'c':[],'cn':[],'e':[],'en':[],'f':[],'fn':[],'fe':[],'t':[],'tp':[]}

# token -> (switch name, env dial, default basename, required keys, empty payload, js const)
GROUP_TOKENS=(
    ('__OFFICES__','offices','STAFFER_OFFICES_DATA_PATH','staffer_offices_section.json',
     ('meta','offices'),EMPTY_OFFICES,'GDATA'),
    ('__ORGS__','organisations','STAFFER_ORGANISATIONS_DATA_PATH',
     'staffer_organisations_section.json',('meta','organisations'),EMPTY_ORGANISATIONS,'ODATA'),
    # Every table make_staffer_network_html_01.tpl dereferences, not a subset: the
    # template reads each one as `CDATA.<k>||[]`, so a key this list does not require
    # is a SILENTLY EMPTY column on the page -- no employer names (en/fe), no filing
    # periods (tp) -- rather than a failure.  That is exactly the skew the
    # organisations/clients schema-id bump of 2026-09-07 exists to make visible.
    ('__CLIENTS__','clients','STAFFER_CLIENTS_DATA_PATH','staffer_clients_section.json',
     ('meta','c','cn','en','f','fn','fe','t','tp'),EMPTY_CLIENTS,'CDATA'),
)


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description='Render a staffer surface from its section JSON.')
    parser.add_argument('--template',default=DEFAULT_TEMPLATE,
                        help='template file name under fec/surfaces/templates (default %s)'%DEFAULT_TEMPLATE)
    parser.add_argument('--data',default=None,help='network section JSON (overrides STAFFER_SECTION_DATA_PATH)')
    parser.add_argument('--offices',default=None,help='offices section JSON (overrides STAFFER_OFFICES_DATA_PATH)')
    parser.add_argument('--organisations',default=None,
                        help='organisations section JSON (overrides STAFFER_ORGANISATIONS_DATA_PATH)')
    parser.add_argument('--clients',default=None,
                        help='clients section JSON (overrides STAFFER_CLIENTS_DATA_PATH)')
    parser.add_argument('--out',default=None,help='output HTML (overrides STAFFER_HTML_OUT)')
    parser.add_argument('--sdata',choices=('full','provenance'),default=None,
                        help='embed the whole network section (full) or only meta+global (provenance); '
                             'default follows --template when omitted (%s) -- an explicit switch always '
                             'wins over that default, in either direction'
                             %', '.join('%s=%s'%(k,v) for k,v in sorted(SDATA_DEFAULT_BY_TEMPLATE.items())))
    parser.add_argument('--max-bytes',type=int,default=MAX_BYTES,
                        help='refuse to write a page larger than this (build-defect tripwire; the '
                             'binding size limits are the per-artifact producer pins)')
    args=parser.parse_args(argv)
    if args.sdata is None:
        args.sdata=SDATA_DEFAULT_BY_TEMPLATE.get(args.template,'full')
    return args


def narrow(data,scope):
    """`provenance` keeps exactly what the owner's language law is printed from.

    members stays present and empty rather than absent: a consumer that reads
    SDATA.members must find an object, and an empty one is the true answer for a
    page that renders no members."""
    if scope!='provenance':
        return data
    return {'meta':data.get('meta'),'global':data.get('global'),'members':{}}


def load_group_payload(path,required,empty,label):
    """One Groups artifact, or an explicitly empty stand-in when it is not there."""
    if not os.path.exists(path):
        print('NOTE: %s section absent (%s); rendering an empty directory'%(label,path))
        return empty
    with open(path,encoding='utf-8') as fh: payload=json.load(fh)
    for key in required:
        if key not in payload:
            raise SystemExit('ERROR: %s has no %r section; refusing to render'%(path,key))
    return payload


def main(argv=None):
    args=parse_args(argv)
    # A retired dial is told, not ignored: silently rendering an empty directory
    # because the operator set the 2026-09-06 variable is the failure mode this
    # message exists to prevent.
    if os.environ.get('STAFFER_GROUPS_DATA_PATH'):
        raise SystemExit('ERROR: STAFFER_GROUPS_DATA_PATH is retired (2026-09-07). The Groups '
                         'data is three artifacts now: set STAFFER_OFFICES_DATA_PATH, '
                         'STAFFER_ORGANISATIONS_DATA_PATH and STAFFER_CLIENTS_DATA_PATH, or pass '
                         '--offices/--organisations/--clients.')
    data_path=args.data or os.environ.get('STAFFER_SECTION_DATA_PATH') or os.path.join(
        MB_ROOT,'data','engine','staffer_network_section.json')
    if not os.path.exists(data_path):
        # Clean skip, matching the chain's OPTIONAL_INPUTS contract: the generation this
        # surface renders may simply be absent in a fresh or data-isolated tree.  Absence is
        # not failure; a present-but-unreadable artifact still fails loudly below.
        print('SKIPPED: staffer network section absent (%s)'%data_path)
        return 0
    with open(data_path,encoding='utf-8') as fh: data=json.load(fh)
    for _k in ('meta','global','members'):
        if _k not in data:
            raise SystemExit('ERROR: %s has no %r section; refusing to render'%(data_path,_k))
    html=_tpl(args.template)
    if html.count('__DATA__')!=1:
        raise SystemExit('ERROR: the staffer template must carry exactly one __DATA__ token')
    out=args.out or os.environ.get('STAFFER_HTML_OUT') or os.path.join(MB_ROOT,'staffer-network.html')

    groups={}
    for token,switch,dial,basename,required,empty,_const in GROUP_TOKENS:
        if token not in html:
            continue
        if html.count(token)!=1:
            raise SystemExit('ERROR: a template that carries %s must carry exactly one'%token)
        path=getattr(args,switch) or os.environ.get(dial) or os.path.join(
            MB_ROOT,'data','engine',basename)
        groups[token]=load_group_payload(path,required,empty,switch)

    # ONE GENERATION PER PAGE.  Every Groups artifact carries its own meta.generation
    # (build_staffer_groups_section.base_meta) and its producer is not a chain step, so
    # a freshly built network section routinely meets group artifacts left over from an
    # earlier generation.  The page prints ONE provenance in its "Sources and method"
    # fold; assembling two would make that fold a lie, so a disagreement refuses here
    # rather than shipping the mixture.  An explicitly EMPTY stand-in carries
    # meta.absent and is exempt: a missing optional artifact is the chain's normal
    # state, which is the whole point of load_group_payload above.
    _section_generation=(data['meta'] or {}).get('generation')
    for token,payload in sorted(groups.items()):
        _gm=payload.get('meta') or {}
        if _gm.get('absent'):
            continue
        if _gm.get('generation')!=_section_generation:
            raise SystemExit('ERROR: the %s artifact was built from generation %r but the network '
                             'section is %r; rebuild the groups artifacts '
                             '(fec/surfaces/build_staffer_groups_section.py) or the section, then '
                             'render. Nothing written.'
                             %(token,_gm.get('generation'),_section_generation))
    html=html.replace('__DATA__',json_for_script(narrow(data,args.sdata),
                                                 separators=(',',':'),sort_keys=True))
    for token,payload in groups.items():
        html=html.replace(token,json_for_script(payload,separators=(',',':'),sort_keys=True))
    size=len(html.encode('utf-8'))
    if size>args.max_bytes:
        raise SystemExit('ERROR: %s would be %d bytes, over the %d cap; nothing written'
                         %(os.path.basename(out),size,args.max_bytes))
    write_text_atomic(html,out)
    print('wrote',out,'(%.0f KB, %d bytes)'%(os.path.getsize(out)/1024,os.path.getsize(out)))
    print('template=%s sdata=%s'%(args.template,args.sdata))
    print('generation=%s verification=%s members=%d'%(
        (data['meta'] or {}).get('generation'),
        ((data['meta'] or {}).get('verification') or {}).get('status'),
        len(data['members'])))
    if '__OFFICES__' in groups:
        gm=groups['__OFFICES__'].get('meta') or {}
        gc=gm.get('counts') or {}
        print('offices: offices=%s people_listed=%s container_source=%s bytes=%s'%(
            gc.get('offices'),gc.get('people_listed'),gm.get('container_source'),gm.get('bytes')))
    if '__ORGS__' in groups:
        om=groups['__ORGS__'].get('meta') or {}
        oc=om.get('counts') or {}
        print('organisations: organisations=%s in_full=%s singletons=%s (%s) people_listed=%s bytes=%s'%(
            oc.get('organisations'),oc.get('organisations_listed_in_full'),
            oc.get('singleton_registrants'),om.get('singleton_mode'),
            oc.get('people_listed'),om.get('bytes')))
    if '__CLIENTS__' in groups:
        cm=groups['__CLIENTS__'].get('meta') or {}
        cc=cm.get('counts') or {}
        print('clients: clients=%s contract_filers=%s ties=%s dropped=%s bytes=%s'%(
            cc.get('client_organisations'),cc.get('contract_filers'),
            cc.get('filer_client_ties'),cc.get('client_organisations_dropped'),cm.get('bytes')))
    return 0


if __name__=='__main__':
    _s.exit(main())
