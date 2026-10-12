"""Fixture-only frontier status propagation and actual dashboard caption rendering.

The builder executes on import, so compile only its real frontier_for definition.
Run the template's formatter and refresh function in Node with a small DOM fixture;
no production builder, artifact, engine, or browser session is executed.
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


FEC = Path(__file__).resolve().parents[2]
BUNDLE = FEC / 'surfaces' / 'build_dashboard_bundle.py'
TEMPLATE = FEC / 'surfaces' / 'templates' / 'make_dashboard_html_00.tpl'
NODE = shutil.which('node')


def _frontier(knee_found=False, *, include_status=True):
    row = {
        'base': 10.25,
        'knee': 100000 if knee_found else 150000,
        'core_seeds': 1,
        'path': [
            {'b': 0, 'spread': 10.25},
            {'b': 50000, 'spread': 14.5, 'seed': 'Seed A', 'type': 'core'},
            {'b': 100000, 'spread': 17.75, 'seed': 'Seed B', 'type': 'coalition'},
            {'b': 150000, 'spread': 18.0, 'seed': 'Seed C', 'type': 'coalition'},
        ],
    }
    if include_status:
        row['knee_found'] = knee_found
    return row


def _build_frontier(row):
    tree = ast.parse(BUNDLE.read_text(encoding='utf-8'), filename=str(BUNDLE))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'frontier_for']
    assert len(functions) == 1
    namespace = {'BF': {'topics': {'Synthetic issue': row}},
                 'idx': {'Seed A': 2, 'Seed B': 5, 'Seed C': 8}}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(BUNDLE), 'exec'), namespace)
    return namespace['frontier_for']


@pytest.mark.parametrize('knee_found', [False, True])
def test_builder_carries_status_without_changing_numeric_frontier(knee_found):
    row = _frontier(knee_found)
    original = copy.deepcopy(row)
    result = _build_frontier(row)('Synthetic issue')
    assert result == {
        'base': 10.25, 'knee': 100000 if knee_found else 150000,
        'knee_found': knee_found, 'core': 1,
        'path': [[0, 10.25, -1, ''], [50000, 14.5, 2, 'c'],
                 [100000, 17.75, 5, 'c'], [150000, 18.0, 8, 'c']],
    }
    assert row == original


def test_legacy_frontier_does_not_infer_a_knee_from_its_endpoint():
    build = _build_frontier(_frontier(include_status=False))
    assert build('Synthetic issue')['knee_found'] is False
    assert build('Missing issue') is None


def _render_caption(frontier):
    source = TEMPLATE.read_text(encoding='utf-8')
    formatter = re.search(r'^function frontierKneeText\(f\)\{[^\n]+\}$', source, re.M)
    refresh = re.search(r'^function refresh\(\)\{\n.*?^\}', source, re.M | re.S)
    assert formatter, 'dashboard knee caption formatter missing'
    assert refresh, 'dashboard refresh function missing'
    script = r"""
const f = JSON.parse(process.argv[1]);
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {style:{}, innerHTML:'', textContent:''});
  return elements.get(id);
}
const document = {getElementById:element, querySelector:element, querySelectorAll:()=>[]};
const DATA={frontier:[f],stats:[{}],stance:[],engine:{refflip:[1]}};
const HONORS=false, N=0, issue=0, mode='coal', rmode='coal', sel=-1;
const slider={style:{},value:100};
let budget=0,ORD=[],SPREAD=18,flipHi=null;
function computeFund() {}
function updateEngChk() {}
function draw() {}
""" + formatter.group(0) + '\n' + refresh.group(0) + r"""
refresh();
process.stdout.write(JSON.stringify(document.getElementById('mdetail').innerHTML));
"""
    result = subprocess.run([NODE, '-e', script, json.dumps(frontier)],
                            capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason='Node is required to execute the dashboard template fixture')
@pytest.mark.parametrize('knee_found', [False, True])
def test_dashboard_caption_honors_builder_status(knee_found):
    frontier = _build_frontier(_frontier(knee_found))('Synthetic issue')
    caption = _render_caption(frontier)
    if knee_found:
        assert 'best value at $100k' in caption
        assert 'best-value point not identified' not in caption
    else:
        assert 'best-value point not identified within this range' in caption
        assert 'best value at' not in caption
        assert '$150k' not in caption
    assert caption.startswith('~<b>18</b> co-sponsors, up from <b>10</b>')
    assert caption.endswith('drag to set budget')


@pytest.mark.skipif(NODE is None, reason='Node is required to execute the dashboard template fixture')
def test_dashboard_legacy_bundle_without_status_does_not_claim_best_value():
    frontier = {'base': 10.25, 'knee': 150000, 'core': 1,
                'path': [[0, 10.25, -1, ''], [150000, 18.0, 8, 'c']]}
    caption = _render_caption(frontier)
    assert 'best-value point not identified within this range' in caption
    assert 'best value at' not in caption
