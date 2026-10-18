#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report statically discoverable artifacts which are written but never read.

The scan is intentionally pragmatic: exact resolved paths are preferred, then
basenames are matched because producers and consumers often spell the same
artifact through different path constructions.  This module only reads source.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
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

# Keep this empty by default.  Add only a reviewed basename or repo-relative
# path, with an adjacent justification, after a human confirms it is benign.
WRITE_ONLY_ALLOWLIST = set()
WRITE_ONLY_ALLOWLIST_REASON = 'A reviewed basename or repo-relative path has an adjacent human justification in WRITE_ONLY_ALLOWLIST.'

CATEGORY_TERMINAL_HUMAN_READ = 'terminal artifact (human-read)'
CATEGORY_DEPLOY_OUTPUT = 'deploy output'
CATEGORY_RUNTIME_SENTINEL_LOCK_LOG = 'runtime sentinel/lock/log'
CATEGORY_TEMP_PARTIAL_WRITE = 'temp/partial write'
CATEGORY_CANDIDATE_ARTIFACT = 'candidate artifact'
CATEGORY_MODEL_ARTIFACT = 'model artifact'

# Auditable systematic categories. These are separate from the one-off
# WRITE_ONLY_ALLOWLIST above, and every categorized artifact remains visible.
OUTPUT_CATEGORY_REASONS = {
    CATEGORY_TERMINAL_HUMAN_READ: 'Validation, review, study, result, coverage, plan, Markdown, and workbook outputs are terminal artifacts intended for a human.',
    CATEGORY_DEPLOY_OUTPUT: 'Files under a deploy directory and index.html are deployed surfaces consumed by people or a web server, not read back by repository code.',
    CATEGORY_RUNTIME_SENTINEL_LOCK_LOG: 'Created at run time for coordination or logging; absence of a static code reader is expected.',
    CATEGORY_TEMP_PARTIAL_WRITE: 'Transient by construction.',
    CATEGORY_CANDIDATE_ARTIFACT: 'A gated layer output; absence of a reader can mean that layer has not been promoted.',
    CATEGORY_MODEL_ARTIFACT: 'ACKNOWLEDGED BLIND SPOT: model files are often loaded by a runtime variable, so static reader detection is unreliable and this is not a clean pass.',
}
TERMINAL_ARTIFACT_PATTERNS = (
    '*_validation.json',
    '*_validate*.json',
    '*_audit*.json',
    '*_stats.json',
    '*_review.md',
    '*_result*.json',
    '*_study.json',
    '*_coverage*.json',
    '*_verify*.csv',
    '*_plan.json',
    '*.md',
    '*.xlsx',
)
DEPLOY_DIRECTORY_NAME = 'deploy'
DEPLOY_BASENAME = 'index.html'
RUNTIME_SENTINEL_LOCK_LOG_PATTERN = r'\.(lock|done|log|pid)$'
TEMP_PARTIAL_WRITE_PATTERN = r'\.(tmp|part|bak)$'
CANDIDATE_ARTIFACT_SUFFIX = '.candidate'
MODEL_ARTIFACT_PATTERN = r'\.(pkl|joblib|npz|dic)$'


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return (base + '.' if base else '') + node.attr
    return ''


def _combine(left, right, operation, limit=40):
    out = set()
    for a in left:
        for b in right:
            try:
                out.add(operation(a, b))
            except (ValueError, TypeError, IndexError):
                continue  # invalid constant path operations are unresolved, not executable
            if len(out) >= limit:
                return out
    return out


def _values(node, env, source_path):
    if node is None:
        return set()
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        if isinstance(node.value, bytes):
            return set()
        return {node.value}
    if isinstance(node, ast.Name):
        if node.id == '__file__':
            return {str(source_path)}
        return set(env.get(node.id, ()))
    if isinstance(node, ast.JoinedStr):
        current = {''}
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                values = {part.value}
            elif isinstance(part, ast.FormattedValue):
                values = _values(part.value, env, source_path)
            else:
                return set()
            if not values:
                return set()
            current = _combine(current, values, lambda a, b: a + b)
        return current
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        out = set()
        for value in node.values:
            out.update(_values(value, env, source_path))
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _combine(
            _values(node.left, env, source_path),
            _values(node.right, env, source_path),
            lambda a, b: a + b,
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _combine(
            _values(node.left, env, source_path),
            _values(node.right, env, source_path),
            lambda a, b: os.path.join(a, b),
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = _values(node.left, env, source_path)
        right = _values(node.right, env, source_path)
        out = set()
        for template in left:
            for value in right:
                try:
                    out.add(template % value)
                except (TypeError, ValueError):
                    pass
        return out
    if isinstance(node, ast.Attribute):
        base = _values(node.value, env, source_path)
        if node.attr == 'parent':
            return {str(Path(value).parent) for value in base}
        return set()
    if isinstance(node, ast.Subscript):
        # Supports the common Path(__file__).resolve().parents[1] root idiom.
        if isinstance(node.value, ast.Attribute) and node.value.attr == 'parents':
            bases = _values(node.value.value, env, source_path)
            try:
                index = ast.literal_eval(node.slice)
            except (ValueError, TypeError, SyntaxError):
                return set()
            if isinstance(index, int) and index >= 0:
                result = set()
                for value in bases:
                    try:
                        result.add(str(Path(value).parents[index]))
                    except IndexError:
                        continue
                return result
        return set()
    if not isinstance(node, ast.Call):
        return set()
    name = _call_name(node.func)
    short = name.rsplit('.', 1)[-1]
    if name in {'Path', 'pathlib.Path'} and node.args:
        return _values(node.args[0], env, source_path)
    if short == 'resolve' and isinstance(node.func, ast.Attribute):
        return {str(Path(value).resolve()) for value in _values(node.func.value, env, source_path)}
    if short == 'with_name' and isinstance(node.func, ast.Attribute) and node.args:
        return _combine(
            _values(node.func.value, env, source_path),
            _values(node.args[0], env, source_path),
            lambda a, b: str(Path(a).with_name(b)),
        )
    if short == 'with_suffix' and isinstance(node.func, ast.Attribute) and node.args:
        return _combine(
            _values(node.func.value, env, source_path),
            _values(node.args[0], env, source_path),
            lambda a, b: str(Path(a).with_suffix(b)),
        )
    if name in {'os.path.join', '_o.path.join', '_oT.path.join'} and node.args:
        current = _values(node.args[0], env, source_path)
        for arg in node.args[1:]:
            part = _values(arg, env, source_path)
            if not current or not part:
                return set()
            current = _combine(current, part, lambda a, b: os.path.join(a, b))
        return current
    if name in {'os.path.abspath', '_o.path.abspath', '_oT.path.abspath'} and node.args:
        return {os.path.abspath(value) for value in _values(node.args[0], env, source_path)}
    if name in {'os.path.dirname', '_o.path.dirname', '_oT.path.dirname'} and node.args:
        return {os.path.dirname(value) for value in _values(node.args[0], env, source_path)}
    if name in {'os.path.normpath', '_o.path.normpath'} and node.args:
        return {os.path.normpath(value) for value in _values(node.args[0], env, source_path)}
    return set()


def _environment(tree, source_path):
    env = {'MB_ROOT': {str(ROOT)}, 'B': {str(ROOT)}, 'ROOT_TEXT': {str(ROOT)}}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'paths':
            for item in node.names:
                if item.name == 'ROOT':
                    env.setdefault(item.asname or item.name, set()).add(str(ROOT))
    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    for _pass in range(12):
        changed = False
        for node in assignments:
            value = _values(node.value, env, source_path)
            if not value:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                before = len(env.setdefault(target.id, set()))
                env[target.id].update(value)
                changed = changed or len(env[target.id]) != before
        if not changed:
            break
    return env


def _mode(call):
    mode = None
    if len(call.args) > 1:
        try:
            mode = ast.literal_eval(call.args[1])
        except (ValueError, TypeError, SyntaxError):
            pass
    for keyword in call.keywords:
        if keyword.arg == 'mode':
            try:
                mode = ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError):
                pass
    return mode if isinstance(mode, str) else 'r'


def _write_nodes(call):
    name = _call_name(call.func)
    short = name.rsplit('.', 1)[-1]
    if short == 'open' and call.args and any(flag in _mode(call) for flag in ('w', 'a', 'x')):
        return [call.args[0]]
    if short in {'write_text', 'write_bytes', 'touch'} and isinstance(call.func, ast.Attribute):
        return [call.func.value]
    if short in {'to_csv', 'to_json', 'to_excel', 'to_parquet', 'savefig'}:
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


def _read_nodes(call):
    name = _call_name(call.func)
    short = name.rsplit('.', 1)[-1]
    if short == 'open' and call.args:
        mode = _mode(call)
        if 'r' in mode and not any(flag in mode for flag in ('w', 'a', 'x')):
            return [call.args[0]]
    if short in {'read_text', 'read_bytes'} and isinstance(call.func, ast.Attribute):
        return [call.func.value]
    if short in {
        'read_csv', 'read_json', 'read_excel', 'read_parquet', 'read_pickle',
        'load_workbook', 'load_json', '_load_json', 'read', 'L',
    } and call.args:
        return [call.args[0]]
    if name in {'numpy.load', 'np.load'} and call.args:
        return [call.args[0]]
    return []


def _artifact(value):
    if not isinstance(value, str) or not value.strip():
        return None
    if any(token in value for token in ('\n', '\r', '\x00')):
        return None
    value = os.path.expandvars(value)
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return Path(os.path.normpath(str(path)))


def _source_files(on_error=None):
    # Producers moved into domain folders; keep the read-only scan recursive.
    excluded = {'attic', '__pycache__', '.cache', '.pytest_cache', '.mypy_cache',
                '.ruff_cache', '.git', '.hg', '.svn', 'node_modules', '.venv', 'venv'}
    paths = []
    is_junction = getattr(os.path, 'isjunction', lambda path: False)
    for directory, subdirectories, filenames in os.walk(FEC, followlinks=False, onerror=on_error):
        subdirectories[:] = sorted(name for name in subdirectories
                                   if name not in excluded and not name.startswith('_quarantine')
                                   and not os.path.islink(os.path.join(directory, name))
                                   and not is_junction(os.path.join(directory, name)))
        for name in filenames:
            path = Path(directory) / name
            if path.suffix in {'.py', '.pyw', '.tpl'} and not path.is_symlink():
                paths.append(path)
    return sorted(set(paths), key=lambda path: str(path).lower())


def _literal_rows(tree, path):
    """Return bare quoted string literals with their nearest call context."""
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if not value or any(token in value for token in ('/', '\\', '\n', '\r', '\x00')):
            continue
        call = 'string literal'
        ancestor = node
        while ancestor in parents:
            ancestor = parents[ancestor]
            if isinstance(ancestor, ast.Call):
                call = _call_name(ancestor.func) or call
                break
        rows.append({
            'value': value,
            'source': str(path.relative_to(ROOT)),
            'line': getattr(node, 'lineno', 0),
            'call': call,
        })
    return rows


_TEMPLATE_QUOTED_LITERAL = re.compile(
    r"(?P<quote>['\"])(?P<value>[^'\"\r\n]+)(?P=quote)"
)


def _template_literal_rows(path, source):
    rows = []
    for match in _TEMPLATE_QUOTED_LITERAL.finditer(source):
        value = match.group('value')
        if not value or any(token in value for token in ('/', '\\', '\x00')):
            continue
        rows.append({
            'value': value,
            'source': str(path.relative_to(ROOT)),
            'line': source.count('\n', 0, match.start()) + 1,
            'call': 'template string literal',
        })
    return rows


def _scan_python(path):
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source, filename=str(path))
    env = _environment(tree, path)
    writes, reads = [], []
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        for kind, nodes, destination in (
            ('write', _write_nodes(call), writes),
            ('read', _read_nodes(call), reads),
        ):
            for node in nodes:
                for value in sorted(_values(node, env, path)):
                    artifact = _artifact(value)
                    if artifact is None:
                        continue
                    destination.append({
                        'kind': kind,
                        'artifact': str(artifact),
                        'basename': artifact.name,
                        'source': str(path.relative_to(ROOT)),
                        'line': getattr(call, 'lineno', 0),
                        'call': _call_name(call.func),
                    })
    return writes, reads, _literal_rows(tree, path)


def _dedupe(rows):
    unique = {}
    for row in rows:
        key = (
            os.path.normcase(row['artifact']), row['source'], row['line'],
            row['call'], row['kind'],
        )
        unique[key] = row
    return list(unique.values())


def _dedupe_literals(rows):
    unique = {}
    for row in rows:
        key = (row['value'], row['source'], row['line'], row['call'])
        unique[key] = row
    return list(unique.values())


def _allowed(artifact):
    relative = os.path.relpath(artifact, ROOT).replace('\\', '/')
    choices = {relative.casefold(), Path(artifact).name.casefold()}
    return bool(choices & {str(item).replace('\\', '/').casefold() for item in WRITE_ONLY_ALLOWLIST})


def _category(artifact):
    path = Path(artifact)
    basename = path.name.casefold()
    directory_parts = {part.casefold() for part in path.parts[:-1]}
    if DEPLOY_DIRECTORY_NAME in directory_parts or basename == DEPLOY_BASENAME:
        return CATEGORY_DEPLOY_OUTPUT
    if re.search(RUNTIME_SENTINEL_LOCK_LOG_PATTERN, basename):
        return CATEGORY_RUNTIME_SENTINEL_LOCK_LOG
    if re.search(TEMP_PARTIAL_WRITE_PATTERN, basename):
        return CATEGORY_TEMP_PARTIAL_WRITE
    if basename.endswith(CANDIDATE_ARTIFACT_SUFFIX):
        return CATEGORY_CANDIDATE_ARTIFACT
    if re.search(MODEL_ARTIFACT_PATTERN, basename):
        return CATEGORY_MODEL_ARTIFACT
    if any(fnmatch.fnmatchcase(basename, pattern) for pattern in TERMINAL_ARTIFACT_PATTERNS):
        return CATEGORY_TERMINAL_HUMAN_READ
    return None


def audit():
    writes, reads, literal_rows, parse_findings = [], [], [], []
    def discovery_error(exc):
        parse_findings.append({
            'kind': 'source_unreadable_or_unparseable',
            'source': os.path.relpath(exc.filename or FEC, ROOT),
            'detail': 'source discovery failed: ' + str(exc),
        })
    for path in _source_files(on_error=discovery_error):
        if path.suffix == '.tpl':
            try:
                source = path.read_text(encoding='utf-8-sig')
                literal_rows.extend(_template_literal_rows(path, source))
            except (OSError, UnicodeError) as exc:
                parse_findings.append({
                    'kind': 'source_unreadable_or_unparseable',
                    'source': str(path.relative_to(ROOT)),
                    'detail': str(exc),
                })
            continue
        try:
            found_writes, found_reads, found_literals = _scan_python(path)
            writes.extend(found_writes)
            reads.extend(found_reads)
            literal_rows.extend(found_literals)
        except (OSError, SyntaxError, UnicodeError) as exc:
            parse_findings.append({
                'kind': 'source_unreadable_or_unparseable',
                'source': str(path.relative_to(ROOT)),
                'detail': str(exc),
            })
    writes, reads = _dedupe(writes), _dedupe(reads)
    literal_rows = _dedupe_literals(literal_rows)
    grouped = {}
    for row in writes:
        key = os.path.normcase(os.path.normpath(row['artifact']))
        grouped.setdefault(key, {'artifact': row['artifact'], 'writers': []})['writers'].append(row)

    artifacts = []
    findings = list(parse_findings)
    suppressed = []
    informational_by_category = {category: [] for category in OUTPUT_CATEGORY_REASONS}
    for key, item in sorted(grouped.items(), key=lambda pair: pair[1]['artifact'].lower()):
        basename = Path(item['artifact']).name.casefold()
        writer_sources = {writer['source'] for writer in item['writers']}
        exact = [
            row for row in reads
            if os.path.normcase(os.path.normpath(row['artifact'])) == key
            and row['source'] not in writer_sources
        ]
        # Preserve exact-path evidence as strongest. A basename literal is
        # weaker, but it is sufficient because loader helpers accept names.
        matches = [dict(row, match_basis='exact resolved path') for row in exact]
        exact_sites = {(row['source'], row['line']) for row in exact}
        for literal in literal_rows:
            if (
                literal['source'] in writer_sources
                or literal['value'].casefold() != basename
                or (literal['source'], literal['line']) in exact_sites
            ):
                continue
            matches.append({
                'kind': 'read',
                'artifact': item['artifact'],
                'basename': Path(item['artifact']).name,
                'source': literal['source'],
                'line': literal['line'],
                'call': literal['call'],
                'match_basis': 'basename literal',
            })
        match_basis = (
            'exact resolved path' if exact
            else 'basename literal' if matches
            else 'none'
        )
        status = 'consumed' if matches else 'unconsumed'
        category = _category(item['artifact']) if not matches else None
        allowlisted = bool(not matches and category is None and _allowed(item['artifact']))
        row = dict(
            item,
            readers=matches,
            match_basis=match_basis,
            status=status,
            category=category,
            allowlisted=allowlisted,
        )
        artifacts.append(row)
        if not matches:
            finding = {
                'kind': 'written_artifact_has_no_reader',
                'artifact': item['artifact'],
                'basename': Path(item['artifact']).name,
                'writers': item['writers'],
                'match_basis': 'none',
                'message': 'written by %s, read by nothing' % ', '.join(
                    sorted({'%s:%s' % (writer['source'], writer['line']) for writer in item['writers']})
                ),
            }
            if category is not None:
                finding['category'] = category
                informational_by_category[category].append(finding)
            elif allowlisted:
                finding['allowlisted'] = True
                suppressed.append(finding)
            else:
                findings.append(finding)
    categorized = [
        row
        for category in OUTPUT_CATEGORY_REASONS
        for row in informational_by_category[category]
    ]
    informational = categorized + suppressed
    return {
        'audit': 'output_consumption',
        'clean': not findings,
        'summary': {
            'distinct_written_artifacts': len(artifacts),
            'consumed_artifacts': sum(row['status'] == 'consumed' for row in artifacts),
            'unconsumed_artifacts': sum(row['status'] == 'unconsumed' for row in artifacts),
            'exact_resolved_path_artifacts': sum(row['match_basis'] == 'exact resolved path' for row in artifacts),
            'basename_literal_artifacts': sum(row['match_basis'] == 'basename literal' for row in artifacts),
            'no_reader_evidence_artifacts': sum(row['match_basis'] == 'none' for row in artifacts),
            'categorized_unconsumed_artifacts': len(categorized),
            'allowlisted_unconsumed_artifacts': len(suppressed),
            'source_parse_findings': len(parse_findings),
            'hard_findings': len(findings),
            'informational': len(informational),
        },
        'allowlist': sorted(WRITE_ONLY_ALLOWLIST),
        'allowlist_reason': WRITE_ONLY_ALLOWLIST_REASON,
        'findings': findings,
        'informational': informational,
        'informational_by_category': informational_by_category,
        'category_reasons': OUTPUT_CATEGORY_REASONS,
        'allowlisted': suppressed,
        'artifacts': artifacts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args()
    result = audit()
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        summary = result['summary']
        print('========== OUTPUT CONSUMPTION AUDIT ==========')
        print('Distinct written artifacts: %d' % summary['distinct_written_artifacts'])
        print('Consumed artifacts: %d' % summary['consumed_artifacts'])
        print('\n[READER MATCH BASIS]')
        print('exact resolved path — %d' % summary['exact_resolved_path_artifacts'])
        print('basename literal — %d' % summary['basename_literal_artifacts'])
        print('none — %d' % summary['no_reader_evidence_artifacts'])
        for artifact in result['artifacts']:
            if artifact['match_basis'] != 'basename literal':
                continue
            relative = os.path.relpath(artifact['artifact'], ROOT)
            readers = ', '.join(sorted({
                '%s:%s' % (reader['source'], reader['line'])
                for reader in artifact['readers']
                if reader['match_basis'] == 'basename literal'
            }))
            print(' READ  %s  [basename literal]  %s' % (relative, readers))
        print('\n[HARD FINDINGS]')
        hard_orphans = [
            finding for finding in result['findings']
            if finding.get('kind') == 'written_artifact_has_no_reader'
        ]
        if not result['findings']:
            print('  OK   none')
        for finding in hard_orphans:
            relative = os.path.relpath(finding['artifact'], ROOT)
            writers = ', '.join(sorted({writer['source'] for writer in finding['writers']}))
            print('FAIL  %s — written by %s, read by nothing [match basis: none]' % (relative, writers))
        for finding in result['findings']:
            if finding.get('kind') == 'source_unreadable_or_unparseable':
                print('FAIL  %(source)s :: %(detail)s' % finding)
        print('\n[INFORMATIONAL]')
        for category, reason in OUTPUT_CATEGORY_REASONS.items():
            rows = result['informational_by_category'][category]
            print('\n%s — %d' % (category, len(rows)))
            print('  Reason: %s' % reason)
            for finding in rows:
                relative = os.path.relpath(finding['artifact'], ROOT)
                writers = ', '.join(sorted({writer['source'] for writer in finding['writers']}))
                print(' INFO  %s — written by %s, read by nothing' % (relative, writers))
        print('\none-off human allowlist — %d' % len(result['allowlisted']))
        print('  Reason: %s' % result['allowlist_reason'])
        for finding in result['allowlisted']:
            relative = os.path.relpath(finding['artifact'], ROOT)
            writers = ', '.join(sorted({writer['source'] for writer in finding['writers']}))
            print(' INFO  %s — written by %s, read by nothing' % (relative, writers))
        print('\nCAVEAT: Static analysis cannot see paths built at runtime from variables, so "read by nothing" is evidence of a probable orphan, not proof of one.')
        print('\nOUTPUT CONSUMPTION AUDIT — %d hard finding(s), %d informational' % (
            len(result['findings']), len(result['informational']),
        ))
        if not result['findings']:
            print('  NO HARD FINDINGS.')
    return 0 if result['clean'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
