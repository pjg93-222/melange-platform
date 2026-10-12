#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit statically constructed repository artifact paths.

This is a read-only tripwire.  It parses source but never imports a scanned
module, because importing a producer can execute work in this repository.
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
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

CATEGORY_RUNTIME_SENTINEL_LOCK = 'runtime sentinel/lock'
CATEGORY_CANDIDATE_ARTIFACT = 'candidate artifact'
CATEGORY_TEMP_PARTIAL_WRITE = 'temp/partial write'
CATEGORY_ON_DEMAND_DIRECTORY = 'on-demand directory'
CATEGORY_OPTIONAL_READ = 'optional read (degrades to default)'

# Auditable suppression taxonomy. Every categorized row remains visible in
# INFORMATIONAL; rows which match no rule remain hard findings.
PATH_CATEGORY_REASONS = {
    CATEGORY_RUNTIME_SENTINEL_LOCK: 'Created at run time by a supervisor or the pipeline; absence while idle is correct.',
    CATEGORY_CANDIDATE_ARTIFACT: 'A gated layer output; absence means that layer has not been run, which is a known state, not a defect.',
    CATEGORY_TEMP_PARTIAL_WRITE: 'Transient by construction.',
    CATEGORY_ON_DEMAND_DIRECTORY: 'The same source expression is passed to os.makedirs(..., exist_ok=True) before on-demand use.',
    CATEGORY_OPTIONAL_READ: 'The call site is written to tolerate absence, so a missing file is a known state rather than a dangling reference.',
}
PATH_SUFFIX_CATEGORY_PATTERNS = (
    (CATEGORY_RUNTIME_SENTINEL_LOCK, r'\.(lock|done|pid)$'),
    (CATEGORY_CANDIDATE_ARTIFACT, r'\.candidate$'),
    (CATEGORY_TEMP_PARTIAL_WRITE, r'\.(tmp|part|bak)$'),
)
MAKEDIRS_CALLS = {'os.makedirs', '_o.makedirs', '_oT.makedirs'}

# This is exactly the "fails by omission" pattern: a missing optional artifact
# can hide behind its silent default. Keep every such read visible, with its
# count and reason, in INFORMATIONAL; this is a downgrade, never suppression.


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return (base + '.' if base else '') + node.attr
    return ''


def _static_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return ''.join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_text(node.left)
        right = _static_text(node.right)
        return None if left is None or right is None else left + right
    return None


def _root_names(tree):
    names = {'MB_ROOT', 'B'}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'paths':
            for item in node.names:
                if item.name == 'ROOT':
                    names.add(item.asname or item.name)
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            rooted = isinstance(value, ast.Name) and value.id in names
            rooted = rooted or (
                isinstance(value, ast.Call)
                and _call_name(value.func) in ('Path', 'pathlib.Path')
                and value.args
                and isinstance(value.args[0], ast.Name)
                and value.args[0].id in names
            )
            if rooted:
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in names:
                        names.add(target.id)
                        changed = True
    return names


def _root_suffix(node, roots):
    if isinstance(node, ast.Name) and node.id in roots:
        return ''
    if isinstance(node, ast.Call) and _call_name(node.func) in ('Path', 'pathlib.Path'):
        if len(node.args) == 1:
            return _root_suffix(node.args[0], roots)
    if isinstance(node, ast.Call) and _call_name(node.func) in (
        'os.path.join', '_o.path.join', '_oT.path.join',
    ):
        if not node.args:
            return None
        first = _root_suffix(node.args[0], roots)
        if first is None:
            return None
        parts = []
        for arg in node.args[1:]:
            text = _static_text(arg)
            if text is None:
                return None
            parts.append(text)
        return os.path.join(first, *parts) if parts else first
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _root_suffix(node.left, roots)
        right = _static_text(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _root_suffix(node.left, roots)
        right = _static_text(node.right)
        if left is not None and right is not None:
            return os.path.join(left, right)
    return None


def _write_path_nodes(call):
    """Return call expressions which are recognisable output-path arguments."""
    name = _call_name(call.func)
    short = name.rsplit('.', 1)[-1]
    if short == 'open' and call.args:
        mode = None
        if len(call.args) > 1:
            mode = _static_text(call.args[1])
        for keyword in call.keywords:
            if keyword.arg == 'mode':
                mode = _static_text(keyword.value)
        mode = mode or 'r'
        if any(flag in mode for flag in ('w', 'a', 'x')):
            return [call.args[0]]
    if short in {
        'to_csv', 'to_json', 'to_excel', 'to_parquet', 'savefig',
        'write_text', 'write_bytes', 'touch',
    }:
        if short in {'write_text', 'write_bytes', 'touch'} and isinstance(call.func, ast.Attribute):
            return [call.func.value]
        if call.args:
            return [call.args[0]]
        for keyword in call.keywords:
            if keyword.arg in {'path', 'path_or_buf', 'fname', 'filename'}:
                return [keyword.value]
    if short in {'write_text_atomic', 'dump_atomic'} and len(call.args) >= 2:
        return [call.args[1]]
    if short in {'save', 'savez', 'savez_compressed'} and call.args:
        return [call.args[0]]
    if name in {'os.replace', 'os.rename', '_o.replace'} and len(call.args) >= 2:
        return [call.args[1]]
    return []


def _node_contains(container, wanted):
    if container is None:
        return False
    return any(node is wanted for node in ast.walk(container))


def _source_files():
    paths = list(FEC.glob('*.py'))
    audit = FEC / 'audit'
    tests = FEC / 'tests'
    templates = FEC / 'templates'
    if audit.is_dir():
        paths.extend(audit.glob('*.py'))
    if tests.is_dir():
        paths.extend(tests.glob('*.py'))
    if templates.is_dir():
        paths.extend(templates.glob('*.tpl'))
    return sorted(set(paths), key=lambda path: str(path).lower())


def _parents(tree):
    result = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def _is_default_fallback(node):
    return (
        isinstance(node, (ast.Dict, ast.List))
        or isinstance(node, ast.Constant) and node.value is None
    )


def _is_optional_read(node, parents):
    """Recognize an optional helper or an immediate ``or`` default."""
    ancestor = node
    while ancestor in parents:
        ancestor = parents[ancestor]
        if isinstance(ancestor, ast.Call):
            short = _call_name(ancestor.func).rsplit('.', 1)[-1].casefold()
            if '_optional' in short:
                return True
        if isinstance(ancestor, ast.BoolOp) and isinstance(ancestor.op, ast.Or):
            for index, value in enumerate(ancestor.values[:-1]):
                if _node_contains(value, node) and _is_default_fallback(ancestor.values[index + 1]):
                    return True
    return False


def _python_literals(tree, source_path, source, parents):
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if not value or any(token in value for token in ('/', '\\', '\n', '\r', '\x00')):
            continue
        rows.append({
            'value': value,
            'source': str(source_path.relative_to(ROOT)),
            'line': getattr(node, 'lineno', 0),
            'expression': ast.get_source_segment(source, node) or repr(value),
            'node': node,
            'parents': parents,
            'optional_read': _is_optional_read(node, parents),
        })
    return rows


_TEMPLATE_QUOTED_LITERAL = re.compile(
    r"(?P<quote>['\"])(?P<value>[^'\"\r\n]+)(?P=quote)"
)


def _template_literals(source_path, source):
    rows = []
    for match in _TEMPLATE_QUOTED_LITERAL.finditer(source):
        value = match.group('value')
        if not value or any(token in value for token in ('/', '\\', '\x00')):
            continue
        rows.append({
            'value': value,
            'source': str(source_path.relative_to(ROOT)),
            'line': source.count('\n', 0, match.start()) + 1,
            'expression': match.group(0),
            'node': None,
            'parents': {},
            'optional_read': False,
        })
    return rows


def _assigned_names(tree, construction):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _node_contains(node.value, construction):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and _node_contains(node.value, construction):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _is_write_target(tree, construction):
    bound = _assigned_names(tree, construction)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for path_node in _write_path_nodes(node):
            if _node_contains(path_node, construction):
                return True
            if isinstance(path_node, ast.Name) and path_node.id in bound:
                return True
    return False


def _is_ondemand_directory(tree, construction):
    """Confirm that this exact construction (or its binding) is mkdir'd safely."""
    bound = _assigned_names(tree, construction)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) not in MAKEDIRS_CALLS:
            continue
        if not node.args:
            continue
        exist_ok = next((keyword.value for keyword in node.keywords if keyword.arg == 'exist_ok'), None)
        if not isinstance(exist_ok, ast.Constant) or exist_ok.value is not True:
            continue
        directory = node.args[0]
        if directory is construction:
            return True
        if isinstance(directory, ast.Name) and directory.id in bound:
            return True
    return False


def _category(row):
    if row.get('kind') == 'dangling_artifact_read' and row.get('optional_read'):
        return CATEGORY_OPTIONAL_READ
    artifact = row['artifact'].casefold()
    for category, pattern in PATH_SUFFIX_CATEGORY_PATTERNS:
        if re.search(pattern, artifact):
            return category
    if row.get('ondemand_directory'):
        return CATEGORY_ON_DEMAND_DIRECTORY
    return None


def _resolve(suffix):
    suffix = suffix.replace('/', os.sep).replace('\\', os.sep)
    return Path(os.path.normpath(os.path.join(str(ROOT), suffix.lstrip('/\\'))))


def audit():
    constructions = []
    parse_findings = []
    literal_candidates = []
    covered_literal_nodes = set()
    for source_path in _source_files():
        if source_path.suffix == '.tpl':
            try:
                source = source_path.read_text(encoding='utf-8-sig')
                literal_candidates.extend(_template_literals(source_path, source))
            except (OSError, UnicodeError) as exc:
                parse_findings.append({
                    'kind': 'source_unreadable_or_unparseable',
                    'source': str(source_path.relative_to(ROOT)),
                    'detail': str(exc),
                })
            continue
        try:
            source = source_path.read_text(encoding='utf-8-sig')
            tree = ast.parse(source, filename=str(source_path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            parse_findings.append({
                'kind': 'source_unreadable_or_unparseable',
                'source': str(source_path.relative_to(ROOT)),
                'detail': str(exc),
            })
            continue
        roots = _root_names(tree)
        parents = _parents(tree)
        literal_candidates.extend(_python_literals(tree, source_path, source, parents))
        candidates = {}
        for node in ast.walk(tree):
            suffix = _root_suffix(node, roots)
            if suffix not in (None, ''):
                candidates[node] = suffix
        for node, suffix in candidates.items():
            # Count the complete construction, not each nested prefix.
            parent = parents.get(node)
            if parent in candidates:
                continue
            target = _resolve(suffix)
            is_glob = '*' in str(target) or '?' in str(target)
            glob_matches = []
            if is_glob:
                glob_matches = [match for match in glob.glob(str(target)) if Path(match).is_file()]
            write_target = _is_write_target(tree, node)
            constructions.append({
                'source': str(source_path.relative_to(ROOT)),
                'line': getattr(node, 'lineno', 0),
                'expression': ast.get_source_segment(source, node) or '<expression>',
                'artifact': str(target),
                'relative_artifact': os.path.relpath(target, ROOT),
                'exists': bool(glob_matches) if is_glob else target.exists(),
                'is_glob': is_glob,
                'glob_match_count': len(glob_matches) if is_glob else None,
                'write_target': write_target,
                'ondemand_directory': _is_ondemand_directory(tree, node),
                'optional_read': not write_target and _is_optional_read(node, parents),
                'match_basis': 'exact resolved path',
            })
            covered_literal_nodes.update(
                child for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            )

    targets_by_basename = {}
    writer_sources = {}
    for row in constructions:
        if row['is_glob']:
            continue
        key = os.path.normcase(os.path.normpath(row['artifact']))
        basename = Path(row['artifact']).name.casefold()
        targets_by_basename.setdefault(basename, {})[key] = row['artifact']
        if row['write_target']:
            writer_sources.setdefault(key, set()).add(row['source'])

    literal_sites = set()
    for literal in literal_candidates:
        if literal['node'] in covered_literal_nodes:
            continue
        # Exact rooted constructions remain the strongest evidence. These
        # bare-name sites are weaker reader evidence used by loader helpers.
        for key, artifact in targets_by_basename.get(literal['value'].casefold(), {}).items():
            if literal['source'] in writer_sources.get(key, set()):
                continue
            site = (key, literal['source'], literal['line'])
            if site in literal_sites:
                continue
            literal_sites.add(site)
            target = Path(artifact)
            constructions.append({
                'source': literal['source'],
                'line': literal['line'],
                'expression': literal['expression'],
                'artifact': artifact,
                'relative_artifact': os.path.relpath(target, ROOT),
                'exists': target.exists(),
                'is_glob': False,
                'glob_match_count': None,
                'write_target': False,
                'ondemand_directory': False,
                'optional_read': literal['optional_read'],
                'match_basis': 'basename literal',
            })

    missing_reads = [
        row for row in constructions
        if not row['is_glob'] and not row['exists'] and not row['write_target']
    ]
    missing_writes = [
        row for row in constructions
        if not row['is_glob'] and not row['exists'] and row['write_target']
    ]
    glob_patterns = [row for row in constructions if row['is_glob']]
    empty_globs = [row for row in glob_patterns if row['glob_match_count'] == 0]
    distinct = {os.path.normcase(os.path.normpath(row['artifact'])) for row in constructions}
    unresolved = [
        dict(row, kind='dangling_artifact_read') for row in missing_reads
    ] + [
        dict(row, kind='write_target_not_yet_present') for row in missing_writes
    ] + [
        dict(row, kind='glob_pattern_matches_nothing') for row in empty_globs
    ]
    findings = list(parse_findings)
    informational_by_category = {category: [] for category in PATH_CATEGORY_REASONS}
    for row in unresolved:
        category = _category(row)
        if category is None:
            findings.append(row)
        else:
            informational_by_category[category].append(dict(row, category=category))
    informational = [
        row
        for category in PATH_CATEGORY_REASONS
        for row in informational_by_category[category]
    ]
    return {
        'audit': 'path_resolution',
        'clean': not findings,
        'summary': {
            'constructions_found': len(constructions),
            'distinct_artifacts': len(distinct),
            'exact_resolved_path_matches': sum(row['match_basis'] == 'exact resolved path' for row in constructions),
            'basename_literal_matches': sum(row['match_basis'] == 'basename literal' for row in constructions),
            'dangling_reads': len(missing_reads),
            'write_targets_not_yet_present': len(missing_writes),
            'glob_patterns': len(glob_patterns),
            'glob_patterns_matching_files': len(glob_patterns) - len(empty_globs),
            'glob_patterns_matching_nothing': len(empty_globs),
            'source_parse_findings': len(parse_findings),
            'hard_findings': len(findings),
            'informational': len(informational),
        },
        'findings': findings,
        'informational': informational,
        'informational_by_category': informational_by_category,
        'category_reasons': PATH_CATEGORY_REASONS,
        'constructions': constructions,
        'glob_patterns': glob_patterns,
        'dangling_reads': missing_reads,
        'write_targets_not_yet_present': missing_writes,
    }


def _print_rows(rows, marker='FAIL'):
    if not rows:
        print('  OK   none')
        return
    for row in rows:
        if row.get('category') == CATEGORY_OPTIONAL_READ:
            detail = CATEGORY_OPTIONAL_READ
        else:
            detail = {
                'dangling_artifact_read': 'dangling artifact read',
                'write_target_not_yet_present': 'write target not yet present',
                'glob_pattern_matches_nothing': 'glob pattern matches zero files',
            }.get(row.get('kind'), row.get('kind', 'unresolved artifact'))
        print('%-5s %s:%s  [%s; match basis: %s]  %s' % (
            marker, row['source'], row['line'], detail,
            row.get('match_basis', 'none'), row['expression'],
        ))
        print('      -> %(relative_artifact)s' % row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args()
    result = audit()
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        summary = result['summary']
        print('========== PATH RESOLUTION AUDIT ==========')
        print('Constructions found: %d' % summary['constructions_found'])
        print('DISTINCT ARTIFACTS: %d' % summary['distinct_artifacts'])
        print('\n[READER MATCH BASIS]')
        print('exact resolved path — %d' % summary['exact_resolved_path_matches'])
        print('basename literal — %d' % summary['basename_literal_matches'])
        print('none — a finding (never counted as reader evidence)')
        for row in result['constructions']:
            if row['match_basis'] == 'basename literal':
                print(' READ  %(source)s:%(line)s  [basename literal]  -> %(relative_artifact)s' % row)
        print('\n[GLOB RESOLUTION]')
        print('Glob patterns found: %d' % summary['glob_patterns'])
        if not result['glob_patterns']:
            print('  OK   none')
        for row in result['glob_patterns']:
            if row['glob_match_count']:
                print('  OK   %(source)s:%(line)s  %(expression)s' % row)
                print('       -> %(relative_artifact)s matched %(glob_match_count)d file(s)' % row)
        if summary['glob_patterns_matching_nothing']:
            print(' ZERO  %d pattern(s) matched 0 files; each is reported once in its HARD or INFORMATIONAL group below.' % summary['glob_patterns_matching_nothing'])
        print('\n[HARD FINDINGS]')
        hard_rows = [
            row for row in result['findings']
            if row.get('kind') != 'source_unreadable_or_unparseable'
        ]
        if hard_rows:
            _print_rows(hard_rows)
        elif not result['findings']:
            print('  OK   none')
        for row in result['findings']:
            if row.get('kind') == 'source_unreadable_or_unparseable':
                print('FAIL  %(source)s :: %(detail)s' % row)
        print('\n[INFORMATIONAL]')
        for category, reason in PATH_CATEGORY_REASONS.items():
            rows = result['informational_by_category'][category]
            print('\n%s — %d' % (category, len(rows)))
            print('  Reason: %s' % reason)
            if rows:
                _print_rows(rows, marker='INFO')
        print('\nCAVEAT: Static analysis cannot see paths built at runtime from variables, so unresolved-path results are evidence, not proof, of a defect.')
        print('\nPATH RESOLUTION AUDIT — %d hard finding(s), %d informational' % (
            len(result['findings']), len(result['informational']),
        ))
        if not result['findings']:
            print('  NO HARD FINDINGS.')
    return 0 if result['clean'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
