"""Synthetic calls to selected actual source AST; never import live producers."""
import ast
import calendar
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unicodedata
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[3]

def load(relative, names, extra=None, class_name=None):
    path = ROOT / relative
    source = ast.parse(path.read_text(encoding='utf-8-sig'), str(path))
    body = source.body
    if class_name:
        body = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == class_name).body
    picked = [n for n in body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in picked} == set(names)
    env = dict(Path=Path, re=re, os=os, sys=sys, json=json, date=date, timedelta=timedelta,
               calendar=calendar, defaultdict=defaultdict, deepcopy=deepcopy,
               threading=threading, unicodedata=unicodedata)
    env.update(extra or {})
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    tree = ast.fix_missing_locations(ast.Module(body=[future, *picked], type_ignores=[]))
    exec(compile(tree, str(path), 'exec'), env)
    return env

class ReconcileBasics(unittest.TestCase):
    def routes(self, final='search_error'):
        mod = load('fec/surfaces/staffer_distinct_routes.py',
                   ['contact_key', '_contact_groups', 'contact_group', 'evidence_contexts', 'route_contexts', 'distinct_routes'],
                   dict(check_cancelled=lambda: None, _CONTACT_LOCK=threading.Lock()))
        rows = iter([dict(status='found', node_ids=['a','b','z'], hops=[]), dict(status=final)])
        router = SimpleNamespace(nodes={k:dict(label=k) for k in 'abz'}, _clock=lambda: 1,
                                 route_many=lambda *args: next(rows))
        return mod['distinct_routes'](router, 'a', ['z'], dict(route_count=2,
             alternative_strategy='distinct', _excluded_nodes=(), _excluded_edges=(), _excluded_contexts=()), None)

    def test_P01_failed_alternative_is_incomplete(self):
        result = self.routes()
        self.assertEqual(result['routes_returned'], 1)
        self.assertEqual(result['alternatives_stop_reason'], 'search_error')
        self.assertFalse(result['alternatives_complete'])

    def test_P01_exhausted_search_is_complete(self):
        result = self.routes('unreachable')
        self.assertTrue(result['alternatives_complete'])
        self.assertEqual(result['routes_returned'], 1)

    def test_P06_maximum_date_does_not_invent_exclusive_end(self):
        mod=load('fec/surfaces/staffer_connection_facts.py', ['date_bounds','employment_dates'])
        self.assertEqual(mod['employment_dates']('2020 - 9999-12-31', '2026-01-01'), {'listed_current':False})

    def test_P06_ordinary_and_coarse_dates_unchanged(self):
        mod=load('fec/surfaces/staffer_connection_facts.py', ['date_bounds','employment_dates'])
        result=mod['employment_dates']('Jan 2020 - Aug 2021', '2026-01-01')
        self.assertEqual((result['start'],result['end_exclusive']), ('2020-01-31','2021-08-02'))
        self.assertEqual(mod['employment_dates']('bad - Present','2026-01-01'), {'listed_current':True})

    def network(self, positions):
        mod=load('fec/surfaces/staffer_live_service.py', ['full_network'],
                 dict(utcnow=lambda:'fixture', digest=lambda value:hashlib.sha256(repr(value).encode()).hexdigest()),
                 'LiveService')
        state=SimpleNamespace(ready=True,_assertion_update_pending=False,_profile_revision='p1',revision='r1',
           _full_network_revision=None,_full_network_payload=None,router=SimpleNamespace(),metadata={},
           profile_nodes={str(i):dict(id=str(i),kind='person',label=str(i),current_positions=[p]) for i,p in enumerate(positions)},
           _completion_state=lambda:{},_current_metadata=lambda:{})
        return mod['full_network'](state)

    def test_P09_employer_case_is_consistent_with_detection(self):
        result=self.network(['Director FOR Example Office','Analyst for Example Office'])
        self.assertEqual(len(result['edges']),1)
        self.assertEqual(result['edges'][0]['employer_label'],'Example Office')
        self.assertFalse(result['edges'][0]['routing_eligible'])

    def test_P09_lowercase_and_no_separator_controls(self):
        self.assertEqual(len(self.network(['Director for Example Office','Analyst for Example Office'])['edges']),1)
        self.assertEqual(len(self.network(['Example Office','Example Office'])['edges']),1)

    def test_P11_initial_workbook_closes_when_resolver_fails(self):
        workbook=Mock()
        def fail(*args): raise ValueError('synthetic resolver refusal')
        mod=load('fec/surfaces/make_member_desk.py',['load_press_and_bills','_read_press_and_bills'],
                 dict(openpyxl=SimpleNamespace(load_workbook=lambda *a,**k:workbook),WB='fixture',BIO={},Resolver=fail))
        with self.assertRaisesRegex(ValueError,'synthetic resolver refusal'):
            mod['load_press_and_bills'](['Fixture Member'])
        workbook.close.assert_called_once_with()

    def test_P11_normal_return_and_close(self):
        workbook=Mock(); expected=object()
        mod=load('fec/surfaces/make_member_desk.py',['load_press_and_bills'],
                 dict(openpyxl=SimpleNamespace(load_workbook=lambda *a,**k:workbook),WB='fixture',
                      _read_press_and_bills=lambda names,wb: expected))
        self.assertIs(mod['load_press_and_bills'](['Fixture']),expected)
        workbook.close.assert_called_once_with()

    def test_P11_inner_resource_finally_closes(self):
        path=ROOT/'fec/surfaces/make_member_desk.py'
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_read_press_and_bills')
        for resource in ['swb','fcon']:
            block=next(n for n in ast.walk(fn) if isinstance(n,ast.Try) and n.finalbody and
                       ast.unparse(n.finalbody[0])==resource+'.close()')
            item=Mock()
            # Actual complete acquired-resource body runs until the first synthetic refusal.
            if resource=='swb':
                class BrokenWorkbook:
                    close=Mock()
                    def __getitem__(self,key):raise ValueError('synthetic workbook refusal')
                item=BrokenWorkbook();env=dict(swb=item)
            else:
                def fail(*args):raise ValueError('synthetic database refusal')
                env=dict(fcon=item,STANCE_ON=True,_grade_index=fail)
            with self.assertRaisesRegex(ValueError,'synthetic'):
                exec(compile(ast.Module(body=[block],type_ignores=[]),str(path),'exec'),env)
            item.close.assert_called_once_with()

    def test_P12_zero_investments_and_absent_sample_do_not_block_output(self):
        path=ROOT/'fec/surfaces/make_member_desk.py'
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        main=next(n for n in reversed(tree.body) if isinstance(n,ast.If) and ast.unparse(n.test)=="__name__ == '__main__'")
        cut=next(i for i,n in enumerate(main.body) if isinstance(n,ast.Import) and n.names[0].name=='io')
        for members in [[],[dict(name='Fixture Member',invested=0,ev_play_code='n',partners=[],articles=[],bills=[],bio={})],
                        [dict(name='Fixture Member',invested=10,ev_play_code='n',partners=[],articles=[],bills=[],bio={})]]:
            env=dict(build_bundle=lambda:dict(members=members,integrity={'source_display':{}},cats=[]))
            with redirect_stdout(io.StringIO()):
                exec(compile(ast.Module(body=main.body[:cut],type_ignores=[]),str(path),'exec'),env)

if __name__=='__main__':
    unittest.main(verbosity=2)
