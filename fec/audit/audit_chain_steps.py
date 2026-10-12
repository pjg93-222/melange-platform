#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit both directions of the canonical pipeline chain, without executing it."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys

FEC_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(FEC_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(FEC_BOOTSTRAP))

from paths import ROOT as ROOT_TEXT


sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(ROOT_TEXT).resolve()
FEC = ROOT / 'fec'
PIPELINE = FEC / 'run_pipeline.py'


def _literal_chain(tree):
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == 'CHAIN' for target in targets):
            continue
        try:
            value = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError) as exc:
            raise ValueError('CHAIN is not a literal list of (script, args) entries') from exc
        if not isinstance(value, (list, tuple)):
            raise ValueError('CHAIN is not a list or tuple')
        steps = []
        for index, entry in enumerate(value, 1):
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise ValueError('CHAIN entry %d is not (script, args)' % index)
            script, script_args = entry
            if not isinstance(script, str) or not isinstance(script_args, (list, tuple)):
                raise ValueError('CHAIN entry %d has non-literal script or args' % index)
            steps.append({'index': index, 'script': script, 'args': list(script_args)})
        return steps
    raise ValueError('run_pipeline.py has no CHAIN assignment')


def _parse(path):
    source = path.read_text(encoding='utf-8-sig')
    return ast.parse(source, filename=str(path))


def _has_main_guard(tree):
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1:
            continue
        if not isinstance(test.left, ast.Name) or test.left.id != '__name__':
            continue
        if not isinstance(test.ops[0], ast.Eq) or len(test.comparators) != 1:
            continue
        value = test.comparators[0]
        if isinstance(value, ast.Constant) and value.value == '__main__':
            return True
    return False


def _producer_like(path):
    try:
        tree = _parse(path)
    except (OSError, SyntaxError, UnicodeError):
        return False
    has_main = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'main'
        for node in tree.body
    )
    # A main function plus an explicit standalone guard is the most useful static
    # distinction between a producer and an import-only library in this tree.
    return has_main and _has_main_guard(tree)


def _producer_index():
    index = {}
    for path in sorted(FEC.rglob('*.py'), key=lambda item: str(item).lower()):
        relative = path.relative_to(FEC)
        directories = relative.parts[:-1]
        if any(
            part in {'tests', 'attic', '__pycache__'}
            or part.startswith('_quarantine')
            for part in directories
        ):
            continue
        index.setdefault(path.name, []).append(path)
    return index


def audit():
    findings = []
    steps = []
    if not PIPELINE.is_file():
        findings.append({
            'kind': 'pipeline_missing',
            'script': str(PIPELINE.relative_to(ROOT)),
            'detail': 'canonical pipeline file is missing',
        })
    else:
        try:
            steps = _literal_chain(_parse(PIPELINE))
        except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
            findings.append({
                'kind': 'pipeline_unparseable',
                'script': str(PIPELINE.relative_to(ROOT)),
                'detail': str(exc),
            })

    producer_index = _producer_index()
    checks = []
    for step in steps:
        matches = producer_index.get(Path(step['script']).name, [])
        relative_matches = [str(path.relative_to(ROOT)) for path in matches]
        path = matches[0] if len(matches) == 1 else None
        check = dict(
            step,
            path=relative_matches[0] if len(relative_matches) == 1 else step['script'],
            matches=relative_matches,
            exists=path is not None,
            parseable=False,
        )
        if not matches:
            findings.append(dict(check, kind='chain_step_missing', detail='script file is missing'))
        elif len(matches) > 1:
            findings.append(dict(
                check,
                kind='chain_step_duplicate_basename',
                detail='script basename resolves to multiple producer files',
            ))
        else:
            try:
                # ast.parse compiles the complete grammar without importing the
                # module and without py_compile's __pycache__ write.
                _parse(path)
                check['parseable'] = True
            except (OSError, SyntaxError, UnicodeError) as exc:
                findings.append(dict(check, kind='chain_step_unparseable', detail=str(exc)))
        checks.append(check)

    chain_names = {Path(step['script']).name.casefold() for step in steps}
    informational = []
    informational_paths = list(FEC.glob('*.py'))
    informational_paths.extend((FEC / 'audit').glob('*.py'))
    for path in sorted(informational_paths, key=lambda p: (p.name.lower(), str(p).lower())):
        lower = path.name.casefold()
        if lower.startswith(('audit_', 'test_')) or lower in {'run_pipeline.py'}:
            continue
        if _producer_like(path) and lower not in chain_names:
            informational.append({
                'kind': 'producer_not_wired_into_chain',
                'script': path.name,
                'path': str(path.relative_to(ROOT)),
            })
    return {
        'audit': 'chain_steps',
        'clean': not findings,
        'summary': {
            'chain_entries': len(steps),
            'chain_steps_checked': len(checks),
            'hard_findings': len(findings),
            'informational_unwired_producers': len(informational),
        },
        'findings': findings,
        'steps': checks,
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
        print('========== PIPELINE CHAIN AUDIT ==========')
        for step in result['steps']:
            ok = step['exists'] and step['parseable']
            print('%s  %2d. %-36s %s' % (
                '  OK  ' if ok else 'FAIL  ', step['index'], step['script'],
                'exists and parses' if ok else 'missing or unparseable',
            ))
        if not result['steps'] and result['findings']:
            for finding in result['findings']:
                print('FAIL  %(script)s :: %(detail)s' % finding)
        print('\n[INFORMATIONAL — PRODUCER-LIKE MODULES NOT IN CHAIN]')
        if result['informational']:
            for row in result['informational']:
                print(' INFO  producer not wired into the chain: %(script)s' % row)
        else:
            print(' INFO  none')
        print('\nPIPELINE CHAIN AUDIT — %d hard finding(s), %d informational' % (
            len(result['findings']), len(result['informational'])))
        if not result['findings']:
            print('  ALL CHAIN STEPS EXIST AND PARSE.')
    return 0 if result['clean'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
