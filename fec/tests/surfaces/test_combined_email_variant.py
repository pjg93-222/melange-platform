# -*- coding: utf-8 -*-
"""Contract suite for assemble() in fec/surfaces/make_combined.py -- the split that produces
BOTH the four-surface Moneyball.html and the three-surface Moneyball_Email.html.

WHY IT IS SHAPED THIS WAY.  make_combined.py does all of its work at module scope: importing it
reads four built surfaces, asserts the id-collision table and writes a ~76 MB artifact.  So this
suite does NOT import it.  It parses the file, lifts the `assemble` function out of the AST and
execs THAT ALONE in an empty namespace.  Two things follow, and both are load-bearing:

  * assemble() is proved PURE by construction -- the namespace it runs in holds nothing but
    builtins, so any reference to a module global (mdcss, _stf_reset, shell_css, STF_PRESENT as
    a module name ...) raises NameError and this suite goes red.  A builder that quietly reached
    back out to module state would produce two documents that could disagree.
  * assemble(pieces, True) can be compared against an INDEPENDENT TRANSCRIPTION of the
    pre-refactor `HTML = (...)` expression, held below.  That is the whole byte-for-byte claim:
    the four-surface deliverable is what it always was, and the refactor moved the concatenation
    without changing it.  If someone edits the head, the layer order or a panel wrapper in the
    builder and not here, this fails -- which is the point.

The three-surface variant is then pinned NEGATIVELY: it must equal the four-surface document
with exactly three staffer chunks removed (the staffer stylesheet layer, the #app-staffer panel,
the staffer IIFE) PLUS one more thing that is not a staffer piece at all -- the single stflink
anchor, which the document carries unconditionally and which would otherwise be a dead link in a
document that ships no #app-staffer panel and no staffer-network.html -- and nothing else touched
-- same head, same shell CSS, same desk and industry panels, same controller.

2026-09-07, navigation-shell option A.  Two pieces joined the concatenation and the transcription
below moved with them: `melshell`, the one navigation bar that now sits between <body> and the
first panel and carries the cross-surface links the four panel headers used to duplicate, and
`melshell_js`, the shell script emitted after the controller so every window hook it reads is
already published.  The stflink anchor moved WITH those links -- it is the shell's Staffer
network tab now, not the network header's own anchor -- so the email variant's one-anchor strip
is asserted against `melshell` instead of `snabody`.  The tab wraps its glyph in a <span>, which
is why the fixture below does too: assemble() had to widen its pattern from `[^<]*` to a
non-greedy match, and this fixture is what exercises that.

2026-10-07, second trim.  The telecom sector pushed the all-in-one zip back over its budget, and
the emailed copy now also ships the desk profiles payload with its bill_lobbying sidecar set to
null.  Section [g] pins that: the helper is pure and lifted the same way, it refuses any payload
shape it cannot trim exactly, and the four-surface Moneyball.html never sees it.

Plain-assert style, no dependencies:  python fec/tests/surfaces/test_combined_email_variant.py
"""
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
COMBINED = os.path.join(FEC, 'surfaces', 'make_combined.py')

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def lift(src, name):
    """Compile a single module-level function out of `src` in an EMPTY namespace."""
    tree = ast.parse(src, COMBINED)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            namespace = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), COMBINED, 'exec'), namespace)
            return namespace[name], node
    raise AssertionError('make_combined.py defines no module-level %s()' % name)


# A synthetic piece per key: short, distinct, and shaped like the real thing where the shape
# matters (mdprof is a whole <script> element, the controller opens with its own newline).
PIECES = {
    'mdcss':         '/*mdcss*/.eyebrow{a:1}',
    'snacss_scoped': '/*snacss*/#app-network b{c:2}',
    'indcss_scoped': '/*indcss*/#app-industry d{e:3}',
    'stfcss_scoped': '/*stfcss*/#app-staffer f{g:4}',
    'ind_reset':     '#app-industry{all:revert}',
    'stf_reset':     '#app-staffer{all:revert}',
    'shell_css':     'html,body{margin:0}[hidden]{display:none!important}',
    'snabody':       '<main id=main>NETWORK</main>',
    'mdbody':        '<section id=mdback>DESK</section>',
    'indbody':       '<main id=indmain>INDUSTRY</main>',
    'stfbody':       '<main id=stf-main>STAFFER</main>',
    'snajs_wrapped': '(function(){/*netjs*/})();',
    'mdjs_wrapped':  '(function(){/*deskjs*/})();',
    'mdprof':        '<script>\n/*profiles sidecar*/\n</script>',
    'indjs_wrapped': '(function(){/*indjs*/})();',
    'stfjs_wrapped': '(function(){/*stfjs*/})();',
    'controller':    '\n(function(){/*controller*/})();\n',
    # The navigation shell carries the document's one staffer-network.html anchor -- exactly as
    # the real shell emits it, unconditionally and independent of include_staffer, which is the
    # entire reason assemble() has to strip it for the email variant rather than simply never
    # including it.  The <span> inside that anchor is load-bearing: it is what forces the
    # non-greedy `<a id=stflink\b.*?</a>` pattern instead of a `[^<]*` body match.
    'melshell':      '<div id=melchrome><nav id=meltabs aria-label="Melange surfaces">'
                     '<a class="meltab netlink" data-surface=network href="Moneyball Dashboard.html">'
                     '<span class=meltabicon aria-hidden=true>◧</span>Main dashboard</a>'
                     '<a id=stflink class=meltab data-surface=staffer href="staffer-network.html">'
                     '<span class=meltabicon aria-hidden=true>▧</span>Staffer network</a>'
                     '</nav></div>',
    'melshell_js':   '<script>\n/*melshell*/\n</script>',
}

# ---------------------------------------------------------------------------------------------
# The pre-refactor concatenation, transcribed by hand from make_combined.py as it stood before
# assemble() existed.  Only the piece names are substituted; every literal is verbatim.
# ---------------------------------------------------------------------------------------------
EXPECTED_FOUR = (
 "<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
 "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
 "<meta name=referrer content=no-referrer>"
 "<title>Melange — Influence Dashboard</title>"
 """<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 208'><path d='M108 20L64 64L28 104L64 144L20 188M64 64L100 104L64 144' fill='none' stroke='%23EECD5C' stroke-width='24' stroke-linejoin='miter' stroke-miterlimit='6'/></svg>">"""
 "<style>\n@layer desk,industry,staffer;\n"
 "@layer desk{\n" + PIECES['mdcss'] + "\n}\n"
 + PIECES['snacss_scoped'] + "\n"
 "@layer industry{\n" + PIECES['ind_reset'] + "\n" + PIECES['indcss_scoped'] + "\n}\n"
 "@layer staffer{\n" + PIECES['stf_reset'] + "\n" + PIECES['stfcss_scoped'] + "\n}\n"
 + PIECES['shell_css'] + "</style></head>"
 "<body class=netmode>" + PIECES['melshell'] +
 "<div id=app-network>" + PIECES['snabody'] + "</div>"
 "<div id=app-member hidden>" + PIECES['mdbody'] + "</div>"
 "<div id=app-industry hidden>" + PIECES['indbody'] + "</div>"
 + ("<div id=app-staffer hidden>" + PIECES['stfbody'] + "</div>") +
 "<script>\n" + PIECES['snajs_wrapped'] + "\n</script>"
 "<script>\n" + PIECES['mdjs_wrapped'] + "\n</script>" + PIECES['mdprof'] +
 "<script>\n" + PIECES['indjs_wrapped'] + "\n</script>"
 + ("<script>\n" + PIECES['stfjs_wrapped'] + "\n</script>") +
 "<script>" + PIECES['controller'] + "</script>" + PIECES['melshell_js'] +
 "</body></html>"
)

# The three staffer chunks the email variant drops ...
CSS_CHUNK = "@layer staffer{\n" + PIECES['stf_reset'] + "\n" + PIECES['stfcss_scoped'] + "\n}\n"
PANEL_CHUNK = "<div id=app-staffer hidden>" + PIECES['stfbody'] + "</div>"
JS_CHUNK = "<script>\n" + PIECES['stfjs_wrapped'] + "\n</script>"
# ... plus the ONE thing that is not a staffer piece at all: the shell's stflink anchor, which
# melshell carries regardless of include_staffer.  Pulled out of the fixture with the SAME
# pattern assemble() matches against, so the two can never quietly drift apart.
_STFLINK_MATCH = re.search(r'<a id=stflink\b.*?</a>', PIECES['melshell'], re.S)
assert _STFLINK_MATCH, 'the melshell fixture must carry exactly one stflink anchor'
STFLINK_CHUNK = _STFLINK_MATCH.group(0)
EXPECTED_THREE = (EXPECTED_FOUR.replace(CSS_CHUNK, '').replace(PANEL_CHUNK, '')
                  .replace(JS_CHUNK, '').replace(STFLINK_CHUNK, ''))


def main():
    src = read(COMBINED)
    assemble, node = lift(src, 'assemble')

    print('[a] assemble() is pure and reproduces the pre-refactor document')
    four = assemble(PIECES, True)
    ok('assemble ran in a namespace holding only builtins (no module globals reached)', True)
    ok('assemble(pieces, True) == the pre-refactor concatenation, byte for byte',
       four == EXPECTED_FOUR,
       'lengths %d vs %d' % (len(four), len(EXPECTED_FOUR)))
    ok('assemble takes exactly (pieces, include_staffer)',
       [a.arg for a in node.args.args] == ['pieces', 'include_staffer'],
       [a.arg for a in node.args.args])
    # A second call must return the same string: assemble may not memoize or mutate `pieces`.
    before = dict(PIECES)
    ok('assemble is repeatable and does not mutate its input',
       assemble(PIECES, True) == four and PIECES == before)

    print('[b] the email variant drops the staffer pieces and NOTHING else')
    three = assemble(PIECES, False)
    ok('assemble(pieces, False) == the four-surface document minus exactly three chunks',
       three == EXPECTED_THREE, 'lengths %d vs %d' % (len(three), len(EXPECTED_THREE)))
    for label, chunk in (('stylesheet layer', CSS_CHUNK), ('panel', PANEL_CHUNK), ('IIFE', JS_CHUNK)):
        ok('the staffer %s is present once in the four-surface document' % label,
           four.count(chunk) == 1, four.count(chunk))
        ok('the staffer %s is absent from the email variant' % label, chunk not in three)
    # The shell's stflink anchor is not a staffer piece -- it lives in melshell, which is shared
    # by all builds -- but it is a dead link with no #app-staffer panel and no
    # staffer-network.html in the zip, so the email variant must drop it too.
    ok('the shell keeps its stflink anchor exactly once in the four-surface document',
       four.count(STFLINK_CHUNK) == 1, four.count(STFLINK_CHUNK))
    ok('the email variant contains no id=stflink anchor', 'id=stflink' not in three)
    ok('the email variant is smaller by exactly the three chunks plus the stflink anchor',
       len(four) - len(three) == len(CSS_CHUNK) + len(PANEL_CHUNK) + len(JS_CHUNK) + len(STFLINK_CHUNK),
       len(four) - len(three))

    print('[c] everything the two documents share really is shared')
    head = four.split('<style>')[0]
    ok('the head is identical', three.startswith(head) and four.startswith(head))
    ok('the layer declaration is identical (an unused layer name is inert)',
       '@layer desk,industry,staffer;' in three)
    ok('the desk layer is identical',
       ('@layer desk{\n' + PIECES['mdcss'] + '\n}\n') in three)
    ok('the industry layer and its reset are identical',
       ('@layer industry{\n' + PIECES['ind_reset'] + '\n' + PIECES['indcss_scoped'] + '\n}\n') in three)
    ok('the shell CSS is identical', (PIECES['shell_css'] + '</style></head>') in three)
    for panel in ('<div id=app-network>', '<div id=app-member hidden>', '<div id=app-industry hidden>'):
        ok('the email variant keeps %s' % panel, panel in three and panel in four)
    for script in ('snajs_wrapped', 'mdjs_wrapped', 'indjs_wrapped'):
        ok('the email variant keeps the %s IIFE' % script, PIECES[script] in three)
    ok('the desk profiles sidecar survives', PIECES['mdprof'] in three)
    _tail = '<script>' + PIECES['controller'] + '</script>' + PIECES['melshell_js'] + '</body></html>'
    ok('the controller is byte-identical in both documents, and the shell script follows it',
       _tail in three and _tail in four)
    ok('both documents close the same way',
       three.endswith('</body></html>') and four.endswith('</body></html>'))

    print('[c2] the navigation shell is emitted once, above the panels, in BOTH documents')
    # Option A's whole claim is that there is ONE bar for four surfaces.  Two properties carry it:
    # the shell appears exactly once, and it appears BEFORE the first panel (a shell emitted after
    # #app-network would scroll away with the map instead of sitting over all four).
    ok('the shell opens the body in the four-surface document',
       four.count('<body class=netmode>' + PIECES['melshell']) == 1)
    ok('the shell precedes every panel',
       four.index('id=melchrome') < four.index('<div id=app-network>')
       and three.index('id=melchrome') < three.index('<div id=app-network>'))
    ok('the shell chrome element is emitted exactly once in both documents',
       four.count('id=melchrome') == 1 and three.count('id=melchrome') == 1)
    ok('the email variant keeps the shell, minus only its staffer tab',
       PIECES['melshell'].replace(STFLINK_CHUNK, '') in three)
    ok('the shell script is the last thing before </body> in both documents',
       four.endswith(PIECES['melshell_js'] + '</body></html>')
       and three.endswith(PIECES['melshell_js'] + '</body></html>'))

    print('[d] the piece contract cannot drift')
    keys_used = sorted({n.slice.value for n in ast.walk(node)
                        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                        and n.value.id == 'pieces' and isinstance(n.slice, ast.Constant)})
    supplied = None
    for stmt in ast.parse(src, COMBINED).body:
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == 'PIECES'
                and isinstance(stmt.value, ast.Dict)):
            supplied = sorted(k.value for k in stmt.value.keys)
    ok('the module builds a PIECES dict for assemble', supplied is not None)
    ok('every key assemble reads is supplied by the module',
       supplied is not None and keys_used == supplied,
       'reads %s / supplies %s' % (keys_used, supplied))
    ok('this suite fixtures exactly the same key set',
       keys_used == sorted(PIECES), 'reads %s' % keys_used)

    print('[e] the builder writes both documents')
    ok('the four-surface document is still written to Moneyball.html',
       "out = _output_path('MELANGE_MONEYBALL_OUTPUT', 'Moneyball.html')" in src
       and 'write_text_atomic(HTML,out)' in src)
    ok('the email variant is assembled with include_staffer False',
       'assemble(PIECES, False)' in src)
    ok('the email variant is written atomically to Moneyball_Email.html',
       "email_out = _output_path('MELANGE_MONEYBALL_EMAIL_OUTPUT', 'Moneyball_Email.html')" in src
       and 'write_text_atomic(EMAIL_HTML, email_out)' in src)
    ok('the four-surface build still passes the real STF_PRESENT flag',
       'assemble(PIECES, STF_PRESENT)' in src)
    ok('both sizes are printed at the end',
       "print('sizes: Moneyball.html %d B, Moneyball_Email.html %d B" in src)
    # NB: make_combined.py's header block is NOT __doc__ -- a one-line `import os as _o, sys ...`
    # precedes it (the centralized-root shim), so ast.get_docstring() returns None.  Read the
    # first bare string expression in the module body instead, which is that header block.
    header = next((n.value.value for n in ast.parse(src, COMBINED).body
                   if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                   and isinstance(n.value.value, str)), '')
    ok('the module header block exists and is substantial', len(header) > 500, len(header))
    ok('the module header block names the email variant and why it exists',
       'Moneyball_Email.html' in header and '14,808,817' in header)

    print('[f] gotoStaffer survives in a document with no staffer panel')
    ok('gotoStaffer looks the panel up before switching to it',
       "window.gotoStaffer=function(name,nopush){var stf=document.getElementById('app-staffer');" in src)
    ok('a missing panel falls back to the network instead of hiding everything',
       "if(!stf){window.gotoNetwork(null,nopush);return;}" in src)
    ok('the fallback forwards nopush, so Back/Forward does not double-push',
       'window.gotoNetwork(null,nopush)' in src)

    print('[g] the email variant ships the desk profiles without the bill_lobbying sidecar (2026-10-07)')
    email_profiles, _ = lift(src, 'email_profiles')
    sidecar = ('{"bills":{"hr1-119":{"totals":{"filings":3}}},'
               '"coverage_note":"finance committees","schema":"bill-lobbying-sidecar-v2"}')
    heavy = '[{"articles":[1]},{"bills":[{"key":"hr1-119"}]}]'
    profiles = ('<script>\nwindow.__melangeProfileMerge({"members_heavy":' + heavy +
                ',"bill_lobbying":' + sidecar + '});\n</script>')
    slim = email_profiles(profiles)
    body = slim[len('<script>\nwindow.__melangeProfileMerge('):-len(');\n</script>')]
    parsed = json.loads(body)
    ok('the sidecar value is replaced by null and the members_heavy rows are untouched',
       parsed == {'members_heavy': json.loads(heavy), 'bill_lobbying': None}, slim)
    ok('no byte of the sidecar survives', 'bill-lobbying-sidecar-v2' not in slim and 'finance committees' not in slim)
    ok('the result is still exactly one <script> element calling the merge hook',
       slim.startswith('<script>\nwindow.__melangeProfileMerge(') and slim.endswith(');\n</script>')
       and slim.count('<script>') == 1 and slim.count('</script>') == 1)
    ok('the trim is smaller by exactly the sidecar text minus "null"',
       len(profiles) - len(slim) == len(sidecar) - len('null'), len(profiles) - len(slim))
    ok('a desk with no profiles script (DESK_SPLIT=0) passes through untouched', email_profiles('') == '')
    for label, bad in (
            ('no bill_lobbying key', profiles.replace(',"bill_lobbying":', ',"other":')),
            ('two bill_lobbying keys', profiles.replace('});\n</script>', ',"bill_lobbying":{}});\n</script>')),
            ('bill_lobbying not the last value',
             profiles.replace('});\n</script>', ',"later":1});\n</script>')),
            ('a payload that no longer ends with the merge call', profiles + '\n')):
        try:
            email_profiles(bad)
            refused = False
        except AssertionError:
            refused = True
        ok('email_profiles refuses %s rather than guessing' % label, refused)
    assemble_names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    ok('assemble() never touches the trim, so Moneyball.html keeps the whole payload',
       'email_profiles' not in assemble_names)
    i_four = src.index('assemble(PIECES, STF_PRESENT)')
    i_trim = src.index("PIECES['mdprof'] = email_profiles(PIECES['mdprof'])")
    i_email = src.index('assemble(PIECES, False)')
    ok('the trim happens after the four-surface build and before the email build',
       i_four < i_trim < i_email, (i_four, i_trim, i_email))
    ok('the four-surface build is never given a trimmed piece',
       src.count('email_profiles(') == 2, src.count('email_profiles('))      # the def and the one call
    producer = read(os.path.join(FEC, 'surfaces', 'make_member_desk.py'))
    ok('make_member_desk.py still writes bill_lobbying as the last key of the profiles payload',
       "{'members_heavy': heavy_rows, 'bill_lobbying': bundle.get('bill_lobbying')}" in producer)
    ok('the module header names the 2026-10-07 trim and the size it fixed',
       '2026-10-07' in header and '14,990,864' in header and 'bill_lobbying' in header)

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
