# -*- coding: utf-8 -*-
"""Stale-import tripwire for the Phase 3 package layout (Paul-approved 2026-08-05).

Every domain move has tripped on at least one of four stale-import forms
referencing a moved module by its OLD flat identity. This scanner knows the
moved-module ledger DYNAMICALLY (every package under fec/ maps its contained
basenames), so it grows as domains move, and it fails loudly on any of:

  (a) ``import <name>`` / ``import <name> as X``
  (b) ``from <name> import ...``
  (c) ``from fec import ... <name> ...``
  (d) quoted "<name>" fed to __import__ / import_module /
      spec_from_file_location

A same-package sibling importing its neighbour bare is STILL stale here:
with fec/ on sys.path (the house bootstrap), ``import sibling`` inside
fec/<pkg>/ does NOT resolve to the package sibling -- the packages are
namespace packages, so in-package imports must be package-qualified. That is
exactly the bug class this tripwire exists to catch, so no sibling exemption
is granted.

Read-only. Exit 0 clean / 1 findings. ``--json`` for machine output. Prints
``stale imports: N passed, M failed`` so run_tests.py's verdict parser can
register it.
"""
import argparse
import json
import os
import re
import sys

FEC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {'tests', 'attic', 'templates', '__pycache__', 'liwc', 'lep'}


def build_ledger():
    """basename (no .py) -> package name, for every fec/<pkg>/*.py."""
    ledger = {}
    for entry in sorted(os.listdir(FEC)):
        pkg_dir = os.path.join(FEC, entry)
        if not os.path.isdir(pkg_dir):
            continue
        if entry in SKIP_DIRS or entry.startswith('_quarantine'):
            continue
        for fn in os.listdir(pkg_dir):
            if fn.endswith('.py'):
                ledger[fn[:-3]] = entry
    return ledger


def iter_sources():
    for dirpath, dirnames, filenames in os.walk(FEC):
        dirnames[:] = [d for d in dirnames
                       if d not in ('__pycache__', 'attic')
                       and not d.startswith('_quarantine')]
        for fn in filenames:
            if fn.endswith('.py'):
                yield os.path.join(dirpath, fn)


def scan(ledger):
    import ast
    names = sorted(ledger, key=len, reverse=True)
    alt = '|'.join(re.escape(n) for n in names)
    pat_ab = re.compile(
        r'^\s*(?:import\s+(%s)\b(?!\s*\.)|from\s+(%s)\s+import\b)' % (alt, alt),
        re.M)
    pat_c = re.compile(r'^\s*from\s+fec\s+import\s+([^\n#]+)', re.M)
    # spec_from_file_location's first argument is a MODULE NAME, not a
    # location -- only its PATH argument can go stale, and path staleness is
    # caught by the bare-name-with-.py check below. __import__/import_module
    # strings ARE resolution-bearing.
    pat_d = re.compile(
        r'(?:__import__|import_module)\s*\('
        r'''[^)]{0,120}?['"](%s)['"]''' % alt)
    pat_d2 = re.compile(
        r'''spec_from_file_location\s*\([^)]{0,200}?['"](%s)\.py['"]''' % alt)
    self_path = os.path.abspath(__file__)
    findings = []
    for path in iter_sources():
        if os.path.abspath(path) == self_path:
            continue
        rel = os.path.relpath(path, os.path.dirname(FEC))
        src = open(path, encoding='utf-8', errors='replace').read()
        def line_of(pos):
            return src[:pos].count('\n') + 1

        def pragma_ok(pos):
            # `# stale-ok: <reason>` on the flagged line suppresses it -- for
            # deliberate guarded fallbacks (e.g. a script-execution branch
            # that inserts its own directory before a bare sibling import).
            end = src.find('\n', pos)
            return 'stale-ok:' in src[pos:end if end != -1 else len(src)]

        # 2026-10-15: parse static aliases/multiline imports without executing code.
        # Keep the legacy tripwire for incomplete or older-dialect source files.
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError:
            tree = None
        if tree is None:
            for m in pat_ab.finditer(src):
                if pragma_ok(m.start()):
                    continue
                name = m.group(1) or m.group(2)
                findings.append({'file': rel, 'line': line_of(m.start()),
                                 'form': 'bare-import',
                                 'module': name, 'text': m.group(0).strip()[:100]})
            for m in pat_c.finditer(src):
                if pragma_ok(m.start()):
                    continue
                imported = [t.strip().split(' ')[0] for t in m.group(1).split(',')]
                hit = [t for t in imported if t in ledger]
                if hit:
                    findings.append({'file': rel, 'line': line_of(m.start()),
                                     'form': 'from-fec',
                                     'module': ','.join(hit),
                                     'text': m.group(0).strip()[:100]})
        else:
            starts = [0] + [m.end() for m in re.finditer('\n', src)]
            imports = sorted((node for node in ast.walk(tree)
                              if isinstance(node, (ast.Import, ast.ImportFrom))),
                             key=lambda node: (node.lineno, node.col_offset))
            for node in imports:
                if pragma_ok(starts[node.lineno - 1]):
                    continue
                if isinstance(node, ast.Import):
                    hits = [(alias.name, alias.lineno) for alias in node.names
                            if alias.name in ledger and not pragma_ok(starts[alias.lineno - 1])]
                    form = 'bare-import'
                elif node.level == 0 and node.module in ledger:
                    hits = [(node.module, node.lineno)]
                    form = 'bare-import'
                elif node.level == 0 and node.module == 'fec':
                    hits = [(alias.name, alias.lineno) for alias in node.names
                            if alias.name in ledger and not pragma_ok(starts[alias.lineno - 1])]
                    form = 'from-fec'
                else:
                    continue
                if hits:
                    findings.append({'file': rel, 'line': hits[0][1], 'form': form,
                                     'module': ','.join(name for name, _line in hits),
                                     'text': (ast.get_source_segment(src, node) or '').strip()[:100]})
        for m in pat_d.finditer(src):
            if pragma_ok(m.start()):
                continue
            findings.append({'file': rel, 'line': line_of(m.start()),
                             'form': 'dynamic-string', 'module': m.group(1),
                             'text': m.group(0).strip()[:100]})
        for m in pat_d2.finditer(src):
            if pragma_ok(m.start()):
                continue
            name = m.group(1)
            # Not stale if the call window already routes through the module's
            # package directory (a separate quoted path segment, e.g.
            # FEC / "legislation" / "x.py").
            window = m.group(0)
            pkg = ledger[name]
            if ('"%s"' % pkg) in window or ("'%s'" % pkg) in window \
                    or ('%s/' % pkg) in window or ('%s\\' % pkg) in window:
                continue
            findings.append({'file': rel, 'line': line_of(m.start()),
                             'form': 'flat-py-path', 'module': name,
                             'text': window.strip()[:100]})
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    ledger = build_ledger()
    findings = scan(ledger)
    if args.json:
        print(json.dumps({'ledger_size': len(ledger),
                          'findings': findings}, indent=1))
    else:
        print('moved-module ledger: %d modules across packages' % len(ledger))
        for f in findings:
            print('  STALE %(form)-14s %(file)s:%(line)s  [%(module)s]  %(text)s'
                  % f)
        print('stale imports: %d passed, %d failed'
              % (1 if not findings else 0, len(findings)))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
