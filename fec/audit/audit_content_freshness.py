#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit deployed HTML freshness by content, never by timestamp alone."""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import html
import json
from pathlib import Path
import re
import sys

FEC_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(FEC_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(FEC_BOOTSTRAP))

from paths import ROOT as ROOT_TEXT


sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(ROOT_TEXT).resolve()
FEC = ROOT / 'fec'
SURFACES_DIR = FEC / 'surfaces'
SURFACE_TEMPLATES = SURFACES_DIR / 'templates'
INDUSTRY_FAMILY_PAGES = tuple(
    ROOT / name for name in (
        'lobby-champions.html', 'lobby-forecast.html',
        'lobby-bills.html', 'lobby-screen.html',
    )
)

HONESTY_NAME = re.compile(r'^[A-Z][A-Z0-9_]*(?:NOTE|LABEL)$')
# Long enough to identify the deployed copy without pinning incidental suffixes.
HONESTY_PREFIX_LENGTH = 96
EXEMPT = {
    ('make_lobby_network_html.py', 'POSITION_NOTE'): (
        'Dead duplicate; only make_lobby_interactions_html.py emits this note.'
    ),
    ('make_lobby_network_html.py', 'INTERACTION_NOTE'): (
        'Dead duplicate; only make_lobby_interactions_html.py emits this note.'
    ),
}

# Verified against the builders below: each builder names or defaults to the
# paired output. make_combined.py reads the network, dashboard, and member-desk
# surfaces before Moneyball.html; the interactions surface remains separate.
SURFACES = (
    {
        'surface': ROOT / 'lobby-network.html',
        'builder': SURFACES_DIR / 'make_lobby_network_html.py',
        'templates': (),
        'identity_markers': (
            (SURFACES_DIR / 'make_lobby_network_html.py', '<section id=industries'),
            (SURFACES_DIR / 'make_lobby_network_html.py', 'id=page-data type=application/json'),
        ),
        # One builder emits a five-page family. Method notes that belong to the
        # Champions or Forecast view must be checked on those deployed siblings,
        # not falsely required on the landing page itself.
        'honesty_surfaces': INDUSTRY_FAMILY_PAGES,
    },
    # CHAIN resolves this producer by basename; keep the explicit path here so
    # the deployed surface is checked against the producer that actually moved.
    {
        'surface': ROOT / 'lobby-interactions.html',
        'builder': SURFACES_DIR / 'make_lobby_interactions_html.py',
        'templates': (),
        'identity_markers': (
            (SURFACES_DIR / 'make_lobby_interactions_html.py', '<div class=honesty>'),
            (SURFACES_DIR / 'make_lobby_interactions_html.py', 'id=page-data type=application/json'),
        ),
    },
    {
        'surface': ROOT / 'Moneyball Dashboard.html',
        'builder': SURFACES_DIR / 'make_dashboard_html.py',
        'templates': (SURFACE_TEMPLATES / 'make_dashboard_html_00.tpl',),
        'identity_markers': (
            (SURFACE_TEMPLATES / 'make_dashboard_html_00.tpl', '<select id=issue'),
            (SURFACE_TEMPLATES / 'make_dashboard_html_00.tpl', 'id=lobnetlink'),
        ),
    },
    {
        'surface': ROOT / 'cu-member-desk.html',
        'builder': SURFACES_DIR / 'make_member_desk.py',
        'templates': (SURFACE_TEMPLATES / 'make_member_desk_00.tpl',),
        'identity_markers': (
            (SURFACE_TEMPLATES / 'make_member_desk_00.tpl', 'id=integritybar'),
            (SURFACE_TEMPLATES / 'make_member_desk_00.tpl', '<span class=seg id=viewseg>'),
        ),
    },
    {
        'surface': ROOT / 'Moneyball.html',
        'builder': SURFACES_DIR / 'make_combined.py',
        'templates': (),
        'honesty_builders': (
            SURFACES_DIR / 'make_combined.py',
            SURFACES_DIR / 'make_lobby_network_html.py',
        ),
        # The combined shell embeds the industry landing page and links to the
        # four sibling pages. Treat that linked set as the deployed product family
        # when auditing the industry's module-level honesty constants.
        'honesty_surfaces': INDUSTRY_FAMILY_PAGES,
        'identity_markers': (
            (SURFACES_DIR / 'make_combined.py', '<div id=app-network>'),
            (SURFACES_DIR / 'make_combined.py', '<div id=app-member hidden>'),
            (SURFACES_DIR / 'make_combined.py', '<div id=app-industry hidden>'),
            (SURFACE_TEMPLATES / 'make_dashboard_html_00.tpl', '<select id=issue'),
            (SURFACE_TEMPLATES / 'make_member_desk_00.tpl', 'id=integritybar'),
            (SURFACES_DIR / 'make_lobby_network_html.py', '<section id=industries'),
        ),
    },
)


def _literal_string(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left, constants)
        right = _literal_string(node.right, constants)
        return None if left is None or right is None else left + right
    return None


def _module_constants(path):
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source, filename=str(path))
    constants = {}
    for _pass in range(8):
        changed = False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _literal_string(node.value, constants)
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and constants.get(target.id) != value:
                    constants[target.id] = value
                    changed = True
        if not changed:
            break
    return source, tree, constants


def _honesty_strings(builder):
    """Discover literal module constants without importing a producer."""
    _source, tree, constants = _module_constants(builder)
    markers = []
    unresolved = []
    names = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and HONESTY_NAME.fullmatch(target.id):
                names.add(target.id)
    for name in names:
        value = constants.get(name)
        if isinstance(value, str) and value:
            prefix = value[:HONESTY_PREFIX_LENGTH]
            markers.append({
                'name': name,
                'prefix': prefix,
                'deployed_prefixes': tuple(dict.fromkeys((prefix, html.escape(prefix)))),
            })
        else:
            unresolved.append(name)
    return sorted(markers, key=lambda item: item['name']), sorted(unresolved)


def _strip_js_line_comments(source):
    """Remove // comments while retaining strings, including URL literals."""
    out = []
    quote = None
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        if quote is not None:
            out.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'\'', '"', '`'}:
            quote = char
            out.append(char)
            index += 1
            continue
        if (
            char == '/'
            and index + 1 < len(source)
            and source[index + 1] == '/'
            and (index == 0 or source[index - 1] != ':')
        ):
            newline = source.find('\n', index + 2)
            index = len(source) if newline < 0 else newline
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def _inline_javascript_without_line_comments(deployed):
    scripts = re.findall(
        r'<script\b[^>]*>(.*?)</script\s*>', deployed,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return '\n'.join(_strip_js_line_comments(script) for script in scripts)


def _iso_mtime(path):
    if not path.exists():
        return None
    return dt.datetime.fromtimestamp(
        path.stat().st_mtime, tz=dt.timezone.utc
    ).isoformat()


def _add_check(
    checks, findings, surface, name, expected, observed, detail,
    finding_kind='content_freshness_mismatch', finding_check=None,
):
    passed = observed is expected
    row = {
        'surface': surface.name,
        'check': name,
        'expected': expected,
        'observed': observed,
        'passed': passed,
        'detail': detail,
    }
    checks.append(row)
    if not passed:
        finding = dict(row, kind=finding_kind)
        if finding_check is not None:
            finding['check'] = finding_check
        findings.append(finding)


def audit():
    findings = []
    checks = []
    contexts = []
    informational = [
        {
            'kind': 'honesty_string_exemption',
            'builder': str((SURFACES_DIR / builder_name).relative_to(ROOT)),
            'constant': constant,
            'reason': reason,
        }
        for (builder_name, constant), reason in sorted(EXEMPT.items())
    ]
    honesty_cache = {}

    for spec in SURFACES:
        surface = spec['surface']
        builder = spec['builder']
        context = {
            'surface': surface.name,
            'builder': str(builder.relative_to(ROOT)),
            'builder_mtime_utc': _iso_mtime(builder),
            'surface_mtime_utc': _iso_mtime(surface),
            'template_mtimes_utc': {
                str(path.relative_to(ROOT)): _iso_mtime(path)
                for path in spec['templates']
            },
            'honesty_surface_mtimes_utc': {
                str(path.relative_to(ROOT)): _iso_mtime(path)
                for path in spec.get('honesty_surfaces', ())
            },
            'mtime_is_context_only': True,
        }
        contexts.append(context)

        if not builder.is_file():
            findings.append({
                'kind': 'builder_missing', 'surface': surface.name,
                'check': 'builder exists', 'detail': str(builder),
            })
            builder_source = ''
        else:
            try:
                builder_source = builder.read_text(encoding='utf-8-sig')
                ast.parse(builder_source, filename=str(builder))
            except (OSError, SyntaxError, UnicodeError) as exc:
                findings.append({
                    'kind': 'builder_unreadable_or_unparseable',
                    'surface': surface.name, 'check': 'builder parses',
                    'detail': str(exc),
                })
                builder_source = ''
        _add_check(
            checks, findings, surface, 'builder/surface pairing', True,
            surface.name in builder_source,
            '%s names %s' % (builder.relative_to(ROOT), surface.name),
        )
        for template in spec['templates']:
            _add_check(
                checks, findings, surface, 'template exists: %s' % template.name,
                True, template.is_file(), str(template),
            )

        if not surface.is_file():
            findings.append({
                'kind': 'surface_missing', 'surface': surface.name,
                'check': 'deployed surface exists',
                'detail': 'missing surface is a finding, never a skip',
            })
            continue
        try:
            deployed = surface.read_text(encoding='utf-8', errors='replace')
        except OSError as exc:
            findings.append({
                'kind': 'surface_unreadable', 'surface': surface.name,
                'check': 'deployed surface readable', 'detail': str(exc),
            })
            continue

        honesty_deployed = deployed
        for related_surface in spec.get('honesty_surfaces', ()):
            if not related_surface.is_file():
                findings.append({
                    'kind': 'surface_missing', 'surface': surface.name,
                    'check': 'honesty family surface exists',
                    'detail': str(related_surface),
                })
                continue
            try:
                honesty_deployed += related_surface.read_text(
                    encoding='utf-8', errors='replace'
                )
            except OSError as exc:
                findings.append({
                    'kind': 'surface_unreadable', 'surface': surface.name,
                    'check': 'honesty family surface readable',
                    'detail': '%s: %s' % (related_surface, exc),
                })

        for origin, marker in spec['identity_markers']:
            try:
                origin_source = origin.read_text(encoding='utf-8-sig')
                marker_in_origin = marker in origin_source
            except (OSError, UnicodeError):
                marker_in_origin = False
            _add_check(
                checks, findings, surface,
                'marker is still emitted by %s' % origin.name,
                True, marker_in_origin, marker,
            )
            _add_check(
                checks, findings, surface,
                'deployed marker from %s' % origin.name,
                True, marker in deployed, marker,
            )

        for honesty_builder in spec.get('honesty_builders', (builder,)):
            cache_key = str(honesty_builder)
            if cache_key not in honesty_cache:
                try:
                    honesty_cache[cache_key] = (*_honesty_strings(honesty_builder), None)
                except (OSError, SyntaxError, UnicodeError) as exc:
                    honesty_cache[cache_key] = ([], [], str(exc))
            honesty_markers, unresolved, honesty_error = honesty_cache[cache_key]
            if honesty_error is not None:
                findings.append({
                    'kind': 'builder_unreadable_or_unparseable',
                    'surface': surface.name,
                    'check': 'builder honesty strings',
                    'detail': '%s: %s' % (honesty_builder, honesty_error),
                })
                continue
            for name in unresolved:
                findings.append({
                    'kind': 'builder_honesty_constant_unresolved',
                    'surface': surface.name,
                    'check': name,
                    'detail': (
                        'module-level NOTE/LABEL constant in %s could not be '
                        'resolved as a string literal' % honesty_builder.name
                    ),
                })
            for marker in honesty_markers:
                exemption_key = (honesty_builder.name, marker['name'])
                if exemption_key in EXEMPT:
                    continue
                _add_check(
                    checks, findings, surface,
                    'builder honesty string: %s::%s' % (
                        honesty_builder.name, marker['name'],
                    ),
                    True, any(
                        prefix in honesty_deployed
                        for prefix in marker['deployed_prefixes']
                    ),
                    marker['prefix'],
                    finding_kind='honesty_string_defined_but_not_deployed',
                    finding_check=(
                        'honesty string defined but not deployed: %s::%s'
                        % (honesty_builder.name, marker['name'])
                    ),
                )

        if surface.name == 'lobby-network.html':
            # These structural checks replace obsolete finance-card markers
            # from a retired unit test; verified against the live artifact on
            # 2026-08-04 after the sector -> industry -> organization redesign.
            _add_check(
                checks, findings, surface, 'industry-card count at least four',
                True, deployed.count('class=industry-card') >= 4,
                'class=industry-card count: %d' % deployed.count('class=industry-card'),
            )
            for sector in ('bank', 'credit_union', 'crypto', 'fintech', 'telecom'):
                marker = 'data-sector="%s"' % sector
                _add_check(
                    checks, findings, surface, 'deployed sector: %s' % sector,
                    True, marker in deployed, marker,
                )
            _add_check(
                checks, findings, surface, 'industry card name span', True,
                '<span class=cardname>' in deployed, '<span class=cardname>',
            )
            for marker in (
                'ev-documented', 'ev-part-inferred', 'ev-mostly-inferred',
            ):
                _add_check(
                    checks, findings, surface,
                    'evidence-encoding class: %s' % marker,
                    True, marker in deployed, marker,
                )

        if surface.name == 'Moneyball Dashboard.html':
            # A prose match twice produced a false "defect still live" report.
            # Comment-stripping is mandatory before code-marker checks so the
            # rationale may name retired expressions without counting as code.
            stripped_code = _inline_javascript_without_line_comments(deployed)
            _add_check(
                checks, findings, surface, 'corrected cost acceptance test', True,
                'rcost(u,i)/add<avg' in stripped_code, 'rcost(u,i)/add<avg',
            )
            _add_check(
                checks, findings, surface, 'corrected cost sort key', True,
                'rcost(a[0],i)/a[1]-rcost(b[0],i)/b[1]' in stripped_code,
                'rcost(a[0],i)/a[1]-rcost(b[0],i)/b[1]',
            )
            _add_check(
                checks, findings, surface, 'superseded cost guard absent', False,
                'Math.max(0.1,add)' in stripped_code, 'Math.max(0.1,add)',
            )

    return {
        'audit': 'content_freshness',
        'clean': not findings,
        # mtime is NOT the check and cannot be trusted: the guarded failure was
        # a stale surface carrying a newer mtime than its changed builder.
        'mtime_policy': (
            'Context only. mtime is NOT the check and cannot be trusted; a stale '
            'surface can have a newer mtime than its builder.'
        ),
        'summary': {
            'surfaces': len(SURFACES),
            'content_checks': len(checks),
            'passed_checks': sum(row['passed'] for row in checks),
            'findings': len(findings),
        },
        'surface_context': contexts,
        'checks': checks,
        'findings': findings,
        'informational': informational,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args()
    result = audit()
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print('========== CONTENT FRESHNESS AUDIT ==========')
        print('mtime is context only — it is NOT the check and cannot be trusted.')
        for context in result['surface_context']:
            print('\n[%s]' % context['surface'])
            print('  builder mtime: %s' % (context['builder_mtime_utc'] or 'MISSING'))
            print('  surface mtime: %s' % (context['surface_mtime_utc'] or 'MISSING'))
            for row in (item for item in result['checks'] if item['surface'] == context['surface']):
                print('%s %s%s' % (
                    '  OK  ' if row['passed'] else 'FAIL  ', row['check'],
                    '' if row['passed'] else ' :: ' + row['detail'],
                ))
            for row in (
                item for item in result['findings']
                if item.get('surface') == context['surface']
                and item.get('kind') != 'content_freshness_mismatch'
            ):
                print('FAIL  %(check)s :: %(detail)s' % row)
        if result['informational']:
            print('\n[INFORMATIONAL]')
            for row in result['informational']:
                print('  EXEMPT  %(builder)s::%(constant)s :: %(reason)s' % row)
        print('\nCONTENT FRESHNESS AUDIT — %d finding(s)' % len(result['findings']))
        if not result['findings']:
            print('  ALL DEPLOYED SURFACES CONTAIN THEIR CURRENT BUILDER MARKERS.')
    return 0 if result['clean'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
