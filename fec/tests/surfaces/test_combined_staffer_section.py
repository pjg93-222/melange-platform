# -*- coding: utf-8 -*-
"""Source-level contract suite for the staffer panel inside fec/surfaces/make_combined.py.

make_combined.py does ALL of its work at module scope -- importing it reads three or four
built surfaces, asserts the id-collision table, and writes a ~76 MB Moneyball.html.  So this
suite deliberately does NOT import it.  It reads the file and asserts, by pattern, the merge
contract the browser depends on and that nothing else in this repo can check:

  * the fourth panel exists and is hidden by default, with its stylesheet layered and reset
    the same way the industry panel is (that reset is what stops the global desk stylesheet
    from repainting a surface it never knew about);
  * the controller can reach it -- gotoStaffer, showApp, the href intercept, ?v=staffer in
    surfaceFromURL, the popstate arm and the initial-load arm; a missing arm is a Back button
    that silently strands the reader on the wrong surface, which is exactly the class of bug
    the industry merge shipped;
  * the id-collision table names the three new pairs, so a staffer id that ever drops its
    `stf-` prefix trips an assertion instead of shadowing an id in another panel;
  * the staleness guard exists, and its bypass is still the shared COMBINED_STALE_OK dial;
  * the retired 8841 hook cannot come back: no `<script src=` and no launch.js reference is
    constructed anywhere in the builder.

It also reads fec/surfaces/staffer_live_integration.py and asserts inject_navigation is the
documented no-op, and fec/surfaces/templates/make_dashboard_html_00.tpl for the header link.

Plain-assert style, no dependencies:  python fec/tests/surfaces/test_combined_staffer_section.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
COMBINED = os.path.join(FEC, 'surfaces', 'make_combined.py')
HOOK = os.path.join(FEC, 'surfaces', 'staffer_live_integration.py')
DASH_TPL = os.path.join(FEC, 'surfaces', 'templates', 'make_dashboard_html_00.tpl')
STAFF_TPL = os.path.join(FEC, 'surfaces', 'templates', 'make_staffer_network_html_00.tpl')

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def main():
    src = read(COMBINED)

    print('[a] panel + cascade')
    ok('the staffer panel is emitted, hidden', '"<div id=app-staffer hidden>" + stfbody' in src)
    ok('the panel is optional (absent surface -> no panel)', 'if STF_PRESENT else ""' in src)
    ok('the layer order names staffer last', '@layer desk,industry,staffer;' in src)
    ok('a staffer layer block wraps the scoped sheet', '"@layer staffer{\\n" + _stf_reset' in src)
    ok('the stylesheet is scoped under #app-staffer',
       "scope_css(stfcss, '#app-staffer')" in src)
    ok('the staffer reset mirrors the industry reset',
       '#app-staffer{all:revert;line-height:normal;-webkit-font-smoothing:auto}' in src
       and '#app-staffer *:not(:where(svg,svg *)){all:revert}' in src)
    ok('the shell gives the panel a height and a scroll mode',
       '#app-staffer{min-height:100vh}' in src and 'body.stfmode{overflow:auto}' in src)
    ok('the staffer script is wrapped in its own IIFE',
       'stfjs_wrapped = ("(function(){\\n" + stfjs +' in src)
    ok('the wrapper publishes both cross-app hooks',
       'window.__stafferOpen=window.__stafferOpen||' in src
       and 'window.__stafferShow=window.__stafferShow||' in src)

    print('[b] controller')
    ok('showApp handles the staffer surface',
       re.search(r"function showApp\(w\)\{.*?w!=='staffer'.*?\n \}", src, re.S) is not None)
    ok('showApp toggles the body mode class', "classList.toggle('stfmode',w==='staffer')" in src)
    ok('gotoStaffer is defined', 'window.gotoStaffer=function(name,nopush)' in src)
    ok('gotoStaffer pushes ?v=staffer with the member', "'?v=staffer'+(name?'&s='+encodeURIComponent(name)" in src)
    ok('gotoStaffer calls the panel hook', 'if(window.__stafferOpen)window.__stafferOpen(name||null)' in src)
    ok('staffer-network.html hrefs are intercepted', "h.indexOf('staffer-network.html')===0" in src)
    ok('the intercept parses the s= parameter', "searchParams.get('s')" in src)
    ok('the intercept degrades when the panel is absent',
       "h.indexOf('staffer-network.html')===0&&document.getElementById('app-staffer')" in src)
    ok('surfaceFromURL resolves v=staffer', "if(v==='staffer')return document.getElementById('app-staffer')" in src)
    ok('popstate has a staffer arm', "else if(s.w==='staffer')window.gotoStaffer(s.s||null,true)" in src)
    ok('the initial load has a staffer arm', "else if(s0.w==='staffer'){window.gotoStaffer(s0.s||null,true);}" in src)
    ok('member desk links still switch in-page', "h.indexOf('cu-member-desk.html')===0" in src)

    print('[c] id-collision table')
    ok('the table names all three new pairs',
       "('sna','stf'): set()" in src and "('desk','stf'): set()" in src and "('ind','stf'): set()" in src)
    ok('the guard is fed the staffer id set', "'stf': _stf_ids" in src)
    ok('the staffer id set is computed from body and js', '_stf_ids = ids(stfbody+stfjs)' in src)

    print('[d] staleness guard')
    ok('the section artifact is a named input',
       "os.path.join(B, 'data', 'engine', 'staffer_network_section.json')" in src)
    ok('a section with no page built from it refuses',
       'exists but staffer-network.html does not' in src)
    ok('a page older than its inputs refuses', 'staffer-network.html is STALE' in src)
    ok('the builder and the template count as inputs',
       "('builder', os.path.join(_stf_dir, 'make_staffer_network_html.py'))" in src
       and 'make_staffer_network_html_00.tpl' in src)
    ok('the guard honours the shared bypass dial',
       'if not _STALE_OK and STF_PRESENT:' in src and 'COMBINED_STALE_OK' in src)
    ok('an absent surface is a clean skip, not an error',
       'the staffer panel is not merged' in src)

    print('[e] the 8841 external script cannot return')
    # Scoped to CODE: make_combined.py's own comment block quotes the retired hook verbatim so a
    # reader knows what was removed and why, and that record must not be what fails this check.
    code_only = '\n'.join(line for line in src.splitlines() if not line.lstrip().startswith('#'))
    ok('make_combined constructs no script src', '<script src' not in code_only)
    ok('make_combined references no launch.js', 'launch.js' not in code_only)
    ok('the removal is nonetheless recorded in the source',
       'launch.js' in src and 'NO-OP' in src.upper())
    hook = read(HOOK)
    ok('inject_navigation returns its argument unchanged',
       re.search(r'def inject_navigation\(html\):.*?\n    return html\n', hook, re.S) is not None)
    ok('inject_navigation no longer splices the hook in',
       'html[:index] + HOOK' not in hook)
    ok('the retirement is documented in the module', 'no-op' in hook.lower() and '8841' in hook)
    ok('make_combined.py still imports it (installer idempotence; relocated 57f993316)',
       'from surfaces.staffer_live_integration import inject_navigation' in src)

    print('[f] navigation from the dashboard header')
    tpl = read(DASH_TPL)
    ok('the shared shell links to the staffer surface (relocated onto meltabs, 57f993316)',
       '<a id=stflink class=meltab data-surface=staffer href="staffer-network.html">' in src)
    ok('the existing header links are untouched',
       '<a id=desklink class=desklink href="cu-member-desk.html">' in tpl
       and '<a id=lobnetlink class=desklink href="lobby-network.html">' in tpl)
    ok('the new link reuses the shell tab class', src.count('class=meltab ') >= 3)
    ok('the dashboard id does not collide with the stf- namespace',
       'id=stf-' not in tpl)

    print('[g] the staffer template links back')
    staff = read(STAFF_TPL)
    for target in ('Moneyball Dashboard.html', 'cu-member-desk.html', 'lobby-network.html'):
        ok('the staffer header links to %s' % target, 'href="%s"' % target in staff)
    ok('member names address the desk by the shared law',
       'cu-member-desk.html?m=' in staff and 'class=mlink' in staff)
    ok('the deep link reads ?s= with ?m= as a fallback',
       "sp.get('s')||sp.get('m')" in staff)

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
