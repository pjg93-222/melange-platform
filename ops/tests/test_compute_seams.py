# -*- coding: utf-8 -*-
"""Windows-to-container portability seams of the Melange chain (compute lane MEL, D-173 / D-177, 2026-10-12).

The chain (fec/run_pipeline.py) must run in a Linux container with the Windows run byte-for-byte unchanged.
This suite pins the seams that make that true, with a negative control for each rule:

  * fec/paths.py: LOCAL_MELANGE (MELANGE_LOCAL_ROOT), MELANGE_DATA (MELANGE_DATA_ROOT), today() (MELANGE_TODAY),
    to_host() for Windows absolute paths stored inside data -- defaults are today's Windows values;
  * a static lint over the chain's import closure: no Windows path literals, no bare os.environ['LOCALAPPDATA'],
    no %VAR% expandvars, every text-mode write names newline= (so Linux writes the CRLF Windows writes), and every
    seam name a module uses is actually imported;
  * the artifact-tool lane's files (AT_LANE) are exempt until that lane rewrites them; the exemption list is
    itself ratcheted (an entry with nothing left to exempt must be removed).

Nothing here runs the chain or reads live data.
"""
import ast
import contextlib
import datetime
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FEC = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "fec"))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)
import paths  # noqa: E402

BS = chr(92)

# Files the artifact-tool lane (D-178) rewrites; their Windows literals and implicit-EOL writes stay until it lands.
AT_LANE = {
    'fec/stance/honors_workbook.py': 'D-178',
    'fec/stance/workbook_cell_sync.py': 'D-178 (the merge adapter the replacement feeds)',
    'fec/legislation/sync_sentinel_bill_ledger.py': 'D-178',
    # integration-2: the openpyxl authors module itself (its proof/report/receipt sidecars are text writes without newline= and
    # it reads the plan's stored output path); it arrives with the artifact-tool lane, after this lint was written.
    'fec/xlsx_openpyxl_authors.py': 'D-178: the openpyxl authors',
}
# Runtime coordination files, not artifacts: the lease heartbeat is never compared across platforms.
WRITE_EXEMPT = {'fec/pipeline_lock.py': 'lease heartbeat file'}
# Scripts a chain module launches or loads by path (subprocess / spec_from_file_location targets); the import closure cannot
# see them.  {seed: the module that names it}; test_extra_seeds_are_still_named_by_their_launcher keeps this list honest.
SEED_LAUNCHERS = {
    'engine/cosponsor_route.py': 'audit/audit_full.py', 'engine/scenario.py': 'audit/audit_full.py',
    'engine/money_response.py': 'audit/audit_full.py', 'engine/budget_frontier.py': 'audit/audit_full.py',
    'engine/optimize.py': 'audit/audit_full.py', 'engine/flip_model.py': 'audit/audit_full.py',
    'audit/system_manifest_check.py': 'audit/audit_full.py', 'engine/compute_pillars.py': 'audit/audit_full.py',
    'audit/audit_frontier.py': 'audit/audit_full.py', 'audit/legacy_workbook_audit.py': 'audit/audit_full.py',
    'legislation/build_registry_anchor_backfill.py': 'engine/build_primacy_surfaces.py',
    'legislation/build_bank_anchor_backfill.py': 'engine/build_primacy_surfaces.py',
    'engine/per_area_grade.py': 'engine/build_primacy_surfaces.py',
    'surfaces/build_clean_workspace.py': 'surfaces/sync_live_dashboard_alias.py',
    'lobbying/cosponsor_forecast.py': 'lobbying/refresh_cosponsor_forecast.py',
    'speech/build_member_longform_review_queue.py': 'speech/build_member_longform_turn_features.py',
    'speech/verify_member_longform_speaker_continuity.py': 'speech/build_member_longform_turn_features.py',
}
EXTRA_SEEDS = tuple(SEED_LAUNCHERS)
# Tools the chain only HASHES into a receipt (build_member_longform_turn_features binds their bytes; it imports one pure function from
# the first): their Windows defaults are CLI conveniences no chain step evaluates, and editing them would change hashes a stored
# receipt pins for nothing.
CLI_ONLY = {
    'fec/speech/build_member_longform_review_queue.py': 'CLI defaults; bytes bound into the turn-features receipt',
    'fec/speech/verify_member_longform_speaker_continuity.py': 'CLI defaults; bytes bound into the turn-features receipt',
}
SEAM_NAMES = {'to_host', 'to_stored', 'LOCAL_MELANGE', 'MELANGE_DATA', 'today'}
# Keys of rows and receipts the Windows chain wrote ABSOLUTE Windows paths under (census of data/ and C:\melange-data, 2026-10-12).
# A path-consuming call that reads one of them has to go through paths.to_host() (identity on Windows) or paths.stored_name().
STORED_PATH_KEYS = frozenset({'out_wav', 'src_wav', 'source_wav', 'wav', 'wav_path', 'transcript', 'turn_audio_wav', 'press_delta_root',
                              'source_run', 'comparison_matrix', 'identity_comparison', 'path', 'output', 'source_path', 'artifact'})
STORED_READ_EXEMPT = {
    'fec/audit/system_manifest_check.py': 'store and root paths are translated once, where the manifest is loaded',
    'fec/speech/build_member_longform_review_queue.py': 'CLI_ONLY tool: the chain binds its bytes, no step runs its main()',
    'fec/speech/verify_member_longform_speaker_continuity.py': 'CLI_ONLY tool: the chain binds its bytes, no step runs its main()',
    'fec/legislation/run_billstatus_bulk_enrichment.py': 'collector lane (3b) reads plans the same lane wrote',
    'fec/legislation/stage_billstatus_bulk_enrichment.py': 'collector lane (3b) reads plans the same lane wrote',
}
_PATH_CALLS = {'Path', 'PurePath', 'open', 'exists', 'isfile', 'isdir', 'getmtime', 'getsize', 'abspath', 'realpath', 'normpath',
               'relpath', 'stat', 'listdir', 'scandir', 'walk', 'samefile', 'copy2', 'copyfile', 'unlink', 'rename', 'replace'}


def _read(path):
    with open(path, 'rb') as handle:
        raw = handle.read()
    return raw[3:].decode('utf-8') if raw.startswith(b'\xef\xbb\xbf') else raw.decode('utf-8', 'replace')


def chain_modules():
    """{repo-relative posix path: absolute path} of every module the chain's steps import, plus EXTRA_SEEDS."""
    tree = ast.parse(_read(os.path.join(FEC, 'run_pipeline.py')))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CHAIN' for t in node.targets) \
                and isinstance(node.value, ast.List):
            for item in node.value.elts:
                if isinstance(item, ast.Tuple) and item.elts and isinstance(item.elts[0], ast.Constant):
                    names.add(item.elts[0].value)
    steps = {}
    for directory, dirs, files in os.walk(FEC):
        dirs[:] = sorted(d for d in dirs if d not in ('tests', 'attic', '__pycache__') and not d.startswith('_quarantine'))
        for name in files:
            if name in names:
                steps[name] = os.path.join(directory, name)
    stdlib = set(sys.stdlib_module_names)

    def resolve(module, base):
        parts = module.split('.')
        for start in (base, FEC):
            candidate = os.path.join(start, *parts)
            if os.path.isfile(candidate + '.py'):
                return candidate + '.py'
            if os.path.isfile(os.path.join(candidate, '__init__.py')):
                return os.path.join(candidate, '__init__.py')
        return None

    seen, todo = {}, list(steps.values()) + [os.path.join(FEC, *seed.split('/')) for seed in EXTRA_SEEDS]
    while todo:
        path = todo.pop()
        if path in seen or not os.path.isfile(path):
            continue
        seen[path] = os.path.relpath(path, ROOT).replace(os.sep, '/')
        try:
            parsed = ast.parse(_read(path))
        except SyntaxError:
            continue
        for node in ast.walk(parsed):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module] + [node.module + '.' + a.name for a in node.names]
            for module in mods:
                if module.split('.')[0] in stdlib:
                    continue
                hit = resolve(module, os.path.dirname(path)) or resolve(module.split('.')[0], os.path.dirname(path))
                if hit:
                    todo.append(hit)
    return {rel: path for path, rel in seen.items()}


_WIN_ENV = re.compile(r"%(?:LOCALAPPDATA|APPDATA|USERPROFILE|TEMP|TMP|HOMEPATH|HOMEDRIVE|PROGRAMFILES|PROGRAMDATA|SYSTEMROOT|WINDIR)%", re.I)
_PATHY = re.compile(r"[\w .,()&'%*?" + re.escape(BS) + r":-]+")
_ESCAPE_ONLY = re.compile(r"(\\[ntrdswDSW])+")
# What marks a string as a REGEX rather than a path: it ends in a word boundary (\b or \B), holds a group/class/alternation/anchor, or a
# quantified class escape.  (A bare "\b" or "\B" PREFIX is NOT one of them: \budget_frontier.json, \backups\ and \Ballotpedia_*.xlsx
# are Windows paths; an unanchored \B alternative once hid the last, M2 of the 2026-10-12 review.)
_REGEXY = re.compile(r"\\[bB]$|[\[\](){}|^$+]|\\[sdwSDW][+*?{]|^\\b[a-z]+$")
_JSON_ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}")


def _is_windows_path_constant(value):
    """A string constant that names a Windows path: a drive path, a ~\\AppData stand-in, %VAR%\\dir, or a backslash-separated
    relative path.  Lone-backslash separators (.replace('\\\\', '/')), JSON \\uXXXX escapes and regexes are not paths."""
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith('~' + BS) or _WIN_ENV.search(value):
        return True
    if BS not in value or _ESCAPE_ONLY.fullmatch(value) or _JSON_ESCAPE.search(value) or _REGEXY.search(value):
        return False
    return bool(_PATHY.fullmatch(value) and re.search(r"\\[A-Za-z_]", value))


def windows_path_literals(path):
    tree = ast.parse(_read(path))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if isinstance(parents.get(node), ast.Expr):      # docstrings and bare strings
            continue
        up = parents.get(node)                           # a hard-coded Windows path handed to to_host() is the seam itself
        if isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id == 'to_host':
            continue
        # arguments of re.* calls are regexes, whatever they look like
        anc, in_regex = parents.get(node), False
        while anc is not None and not isinstance(anc, ast.stmt):
            if isinstance(anc, ast.Call) and isinstance(anc.func, ast.Attribute) \
                    and isinstance(anc.func.value, ast.Name) and anc.func.value.id == 're':
                in_regex = True
                break
            anc = parents.get(anc)
        if not in_regex and _is_windows_path_constant(node.value):
            hits.append((node.lineno, node.value))
    return hits


def bare_localappdata_reads(path):
    """os.environ['LOCALAPPDATA'] (a KeyError on Linux) and expandvars('%...%') (POSIX leaves the literal in place)."""
    tree = ast.parse(_read(path))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == 'LOCALAPPDATA':
            hits.append((node.lineno, 'os.environ[LOCALAPPDATA]'))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'expandvars' \
                and node.args and isinstance(node.args[0], ast.Constant) and '%' in str(node.args[0].value):
            hits.append((node.lineno, 'expandvars(%VAR%)'))
    return hits


def stored_path_reads(path):
    """Path-consuming calls whose first argument reads a STORED_PATH_KEYS key (row.get('out_wav'), receipt['path']) with no
    to_host() in the expression: on POSIX such a path is a relative string that names nothing."""
    tree = ast.parse(_read(path))

    def key_of(node):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return node.slice.value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
        return None

    def translated(node):
        return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in ('to_host', 'stored_name', 'PureWindowsPath')
                   for c in ast.walk(node))

    hits = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ''
        if name in _PATH_CALLS and not translated(node.args[0]) \
                and {key_of(c) for c in ast.walk(node.args[0])} & STORED_PATH_KEYS:
            hits.add((node.lineno, name))
    return sorted(hits)


def text_writes_without_newline(path):
    """Text-mode write opens (open/io.open/os.fdopen/Path.open/NamedTemporaryFile/Path.write_text) with no newline=."""
    tree = ast.parse(_read(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else '')
        owner = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else ''
        if name not in ('open', 'fdopen', 'NamedTemporaryFile', 'write_text'):
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        if name == 'write_text':
            mode = 'w'
        elif name == 'NamedTemporaryFile':
            mode = keywords['mode'].value if isinstance(keywords.get('mode'), ast.Constant) else 'w+b'
        else:
            index = 0 if isinstance(fn, ast.Attribute) and owner not in ('io', 'codecs', 'os') and name == 'open' else 1
            mode = node.args[index].value if len(node.args) > index and isinstance(node.args[index], ast.Constant) else 'r'
            if isinstance(keywords.get('mode'), ast.Constant):
                mode = keywords['mode'].value
        if not isinstance(mode, str) or 'b' in mode or not any(c in mode for c in 'wax+'):
            continue
        if 'newline' not in keywords:
            hits.append((node.lineno, name))
    return hits


def undefined_seam_names(path):
    """Seam names a module loads but never binds (a codemod that missed an import is a NameError on first use)."""
    tree = ast.parse(_read(path))
    bound, used = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((a.asname or a.name).split('.')[0] for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            if not isinstance(node, ast.ClassDef):
                args = node.args
                bound.update(a.arg for a in args.args + args.kwonlyargs + args.posonlyargs)
                bound.update(a.arg for a in (args.vararg, args.kwarg) if a)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.Name) and node.id in SEAM_NAMES:
            used.setdefault(node.id, node.lineno)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    return sorted((n, ln) for n, ln in used.items() if n not in bound)


class PathSeams(unittest.TestCase):
    """fec/paths.py constants resolve at import, so each case reads them from a fresh interpreter."""

    @staticmethod
    def _env(**env):
        keep = {k: v for k, v in os.environ.items()
                if k not in ('MELANGE_LOCAL_ROOT', 'MELANGE_DATA_ROOT', 'STAFFER_NETWORK_LOCAL', 'LOCALAPPDATA', 'MELANGE_TODAY')}
        keep.update(env)
        keep['PYTHONPATH'] = FEC
        return keep

    @classmethod
    def _run(cls, code, **env):
        return subprocess.run([sys.executable, '-c', code], env=cls._env(**env), capture_output=True, text=True, check=True).stdout

    @classmethod
    def _resolve(cls, **env):
        # 'lm' is null when LOCAL_MELANGE is the fail-closed placeholder (nothing names the local store)
        code = ("import paths,json;lm=paths.LOCAL_MELANGE;"
                "print(json.dumps({'lm':lm if isinstance(lm,str) else None,'md':paths.MELANGE_DATA,'sn':paths.STAFFER_NETWORK_LOCAL}))")
        return json.loads(cls._run(code, **env))

    def test_defaults_are_todays_windows_values(self):
        base = os.path.abspath(os.path.join(tempfile.gettempdir(), 'LocalAppData'))
        got = self._resolve(LOCALAPPDATA=base)
        self.assertEqual(got['lm'], os.path.join(base, 'Melange'))
        self.assertEqual(got['md'], 'C:' + BS + 'melange-data')
        self.assertEqual(got['sn'], os.path.join(base, 'Melange', 'staffer_network'))

    def test_env_overrides_win_and_staffer_network_follows(self):
        target = os.path.abspath(os.path.join(tempfile.gettempdir(), 'cont', 'Melange'))
        data = os.path.abspath(os.path.join(tempfile.gettempdir(), 'cont', 'data'))
        got = self._resolve(MELANGE_LOCAL_ROOT=target, MELANGE_DATA_ROOT=data, LOCALAPPDATA=os.path.abspath('elsewhere'))
        self.assertEqual((got['lm'], got['md']), (target, data))
        self.assertEqual(got['sn'], os.path.join(target, 'staffer_network'))   # one relocation moves the whole local store

    def test_no_localappdata_at_all_still_resolves(self):
        # the Linux case audit_full.py:2314 used to KeyError on
        got = self._resolve(MELANGE_LOCAL_ROOT=os.path.abspath('somewhere'))
        self.assertTrue(got['lm'].endswith('somewhere'))

    def test_staffer_network_local_env_still_wins(self):
        explicit = os.path.abspath(os.path.join(tempfile.gettempdir(), 'sn_local'))
        self.assertEqual(self._resolve(STAFFER_NETWORK_LOCAL=explicit)['sn'], explicit)

    # The modules that raised when LOCALAPPDATA was unset (MINOR 3 of the 2026-10-12 review) and the call that did the raising.
    FAIL_CLOSED = (('lobbying.champions_v2', '_derived_dir'), ('lobbying.contest_screen', '_derived_dir'),
                   ('lobbying.org_mentions', '_derived_dir'), ('lobbying.org_stance_priors', '_derived_dir'),
                   ('lobbying.entity_identity', '_sweep_root'), ('lobbying.sector_spend', 'default_sweep_root'),
                   ('surfaces.make_lobby_interactions_html', '_default_data_root'),
                   ('surfaces.make_lobby_network_html', '_default_data_root'))

    def test_unset_local_root_fails_closed_at_first_use_not_at_import(self):
        # neither MELANGE_LOCAL_ROOT nor LOCALAPPDATA: importing paths is fine, every USE of LOCAL_MELANGE raises a clear error
        code = ("import os, pathlib, paths\n"
                "print('imported')\n"
                "print('staffer', os.path.isabs(paths.STAFFER_NETWORK_LOCAL))\n"     # its own, older fallback is unchanged
                "uses = [lambda: os.path.join(paths.LOCAL_MELANGE, 'x'), lambda: str(paths.LOCAL_MELANGE),\n"
                "        lambda: '%s' % paths.LOCAL_MELANGE, lambda: f'{paths.LOCAL_MELANGE}', lambda: paths.LOCAL_MELANGE + 'x',\n"
                "        lambda: 'x' + paths.LOCAL_MELANGE, lambda: paths.LOCAL_MELANGE / 'x', lambda: pathlib.Path(paths.LOCAL_MELANGE),\n"
                "        lambda: open(paths.LOCAL_MELANGE), lambda: paths.LOCAL_MELANGE.startswith('C')]\n"
                "for use in uses:\n"
                "    try:\n"
                "        use()\n"
                "    except paths.LocalRootUnset as exc:\n"
                "        print('raised', 'MELANGE_LOCAL_ROOT' in str(exc) and 'LOCALAPPDATA' in str(exc))\n"
                "    else:\n"
                "        print('NO RAISE')\n"
                "print(repr(paths.LOCAL_MELANGE))\n")
        expected = ['imported', 'staffer True'] + ['raised True'] * 10 + ['<LOCAL_MELANGE unset>']
        self.assertEqual(self._run(code).splitlines(), expected)
        self.assertEqual(self._run(code, LOCALAPPDATA='').splitlines(), expected)          # empty counts as unset
        control = "import paths;print(type(paths.LOCAL_MELANGE).__name__)"                  # negative controls: any name resolves it
        self.assertEqual(self._run(control, MELANGE_LOCAL_ROOT=os.path.abspath('somewhere')).split(), ['str'])
        self.assertEqual(self._run(control, LOCALAPPDATA=os.path.abspath('somewhere')).split(), ['str'])

    def test_the_raise_if_unset_modules_stay_fail_closed(self):
        code = ("import importlib, paths\n"
                "for mod, fn in %r:\n"
                "    func = getattr(importlib.import_module(mod), fn)\n"
                "    try:\n"
                "        print(mod, 'returned', func())\n"
                "    except paths.LocalRootUnset:\n"
                "        print(mod, 'raised')\n" % (self.FAIL_CLOSED,))
        done = subprocess.run([sys.executable, '-c', code], env=self._env(), capture_output=True, text=True)
        if done.returncode and 'ModuleNotFoundError' in done.stderr:
            self.skipTest('a chain module needs a package this interpreter lacks: ' + done.stderr.strip().splitlines()[-1])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), ['%s raised' % mod for mod, _ in self.FAIL_CLOSED])     # importing never raised
        root = os.path.abspath(os.path.join(tempfile.gettempdir(), 'fail_closed_root'))
        control = self._run(code, MELANGE_LOCAL_ROOT=root).splitlines()                     # with a root named, each one answers
        self.assertEqual(len(control), len(self.FAIL_CLOSED))
        for line in control:
            self.assertIn(' returned ', line)
            self.assertIn(root, line)

    def test_pinned_today_warns_once_per_process_on_stderr(self):
        for value, warns in (('2026-10-12', True), ('', False), (None, False)):
            with mock.patch.dict(os.environ, {}), mock.patch.object(paths, '_TODAY_WARNED', False):
                os.environ.pop('MELANGE_TODAY', None)
                if value is not None:
                    os.environ['MELANGE_TODAY'] = value
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    first, second = paths.today(), paths.today()
                self.assertEqual(first, second)
                self.assertEqual(first, datetime.date(2026, 10, 12) if warns else datetime.date.today())
                self.assertEqual(err.getvalue().count('WARNING'), 1 if warns else 0, err.getvalue())
                if warns:
                    self.assertIn('MELANGE_TODAY=2026-10-12', err.getvalue())
        with mock.patch.dict(os.environ, {'MELANGE_TODAY': 'not-a-date'}), mock.patch.object(paths, '_TODAY_WARNED', False):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(ValueError):
                paths.today()
            self.assertEqual(err.getvalue(), '')                                           # an unusable pin returns nothing, warns nothing
        done = subprocess.run([sys.executable, '-c', 'import paths; paths.today(); paths.today()'],
                              env=self._env(MELANGE_TODAY='2026-10-12'), capture_output=True, text=True, check=True)
        self.assertEqual(done.stderr.count('WARNING'), 1, done.stderr)                      # and a real process prints it once

    def test_today_is_the_real_date_unless_pinned(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(paths, '_TODAY_WARNED', True):   # warning has its own test
            os.environ.pop('MELANGE_TODAY', None)
            self.assertEqual(paths.today(), datetime.date.today())
            os.environ['MELANGE_TODAY'] = ''
            self.assertEqual(paths.today(), datetime.date.today())           # empty (a compose pass-through) means unset
            os.environ['MELANGE_TODAY'] = '2026-10-12'
            self.assertEqual(paths.today(), datetime.date(2026, 10, 12))
            os.environ['MELANGE_TODAY'] = 'not-a-date'
            with self.assertRaises(ValueError):
                paths.today()

    def test_build_media_blend_reads_the_pinned_day(self):
        text = _read(os.path.join(FEC, 'stance', 'build_media_blend.py'))
        self.assertRegex(text, r"(?m)^TODAY=today\(\)")
        self.assertNotIn('TODAY=datetime.date.today()', text)


class ToHost(unittest.TestCase):
    ROOTS = dict(ROOT='/melange', DRAFT='/draft', LOCAL_MELANGE='/localappdata/Melange', MELANGE_DATA='/melange-data')

    def _posix(self):
        stack = [mock.patch.object(paths, '_IS_WINDOWS', False)] + [mock.patch.object(paths, k, v) for k, v in self.ROOTS.items()]
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stored_windows_roots_map_onto_the_container_mounts(self):
        self._posix()
        cases = {
            BS.join(['C:', 'melange-data', 'member_clips', 'npr', 'a.wav']): '/melange-data/member_clips/npr/a.wav',
            'c:/MELANGE-DATA': '/melange-data',                                         # case-insensitive, either slash
            'C:\\Users\\pgoff\\OneDrive\\Desktop\\Melange Systems\\data\\x.json': '/melange/data/x.json',
            'C:/Users/pgoff/OneDrive/Desktop/Draft Systems/cu.csv': '/draft/cu.csv',
            'C:\\Users\\pgoff\\AppData\\Local\\Melange\\lda_rebuild\\derived\\y': '/localappdata/Melange/lda_rebuild/derived/y',
        }
        for stored, expected in cases.items():
            self.assertEqual(paths.to_host(stored), expected, stored)

    def test_everything_else_is_returned_unchanged(self):
        self._posix()
        for text in ('/already/native', 'relative/x.wav', 'D:\\other\\x.wav', 'C:\\melange-data-extra\\x', 'C:', ''):
            self.assertEqual(paths.to_host(text), text)
        self.assertEqual(paths.to_host(os.path.join('a', 'b')), os.path.join('a', 'b'))

    def test_identity_on_windows_even_for_stored_roots(self):
        with mock.patch.object(paths, '_IS_WINDOWS', True):
            for stored in ('C:\\melange-data\\a.wav', 'C:/melange-data/a.wav', 'C:\\Users\\pgoff\\x'):
                self.assertEqual(paths.to_host(stored), stored)

    def test_stored_name_reads_either_separator_on_any_host(self):
        # pathlib.Path('C:\\a\\b.json').name is the whole string on POSIX; a stored Windows path still names its file
        self.assertEqual(paths.stored_name('C:\\Users\\pgoff\\x\\press_refresh_20261011'), 'press_refresh_20261011')
        self.assertEqual(paths.stored_name('C:/melange-data/a/b.wav'), 'b.wav')
        self.assertEqual(paths.stored_name('/melange/outputs/run_7'), 'run_7')
        self.assertEqual(paths.stored_name(''), '')
        with mock.patch.object(paths, '_IS_WINDOWS', False):
            self.assertEqual(paths.stored_name('C:\\a\\b\\c.json'), 'c.json')

    def test_path_objects_are_accepted(self):
        self._posix()
        self.assertEqual(paths.to_host(__import__('pathlib').PureWindowsPath('C:/melange-data/a.wav')), '/melange-data/a.wav')

    def test_audit_collection_path_is_within_sees_the_container_mount(self):
        import audit_collection
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'wj_audio'))
            stored = 'C:\\melange-data\\wj_audio\\a.wav'
            with mock.patch.object(paths, '_IS_WINDOWS', False), mock.patch.object(paths, 'MELANGE_DATA', tmp):
                self.assertTrue(audit_collection.path_is_within(stored, __import__('pathlib').Path(tmp, 'wj_audio')))
                self.assertFalse(audit_collection.path_is_within(stored, __import__('pathlib').Path(tmp, 'other')))   # negative control


class RebaseStored(unittest.TestCase):
    """MELANGE_REBASE_STORED=1: a relocated Windows scratch root rebases stored paths like the container; the laptop never does."""
    STORED_ROOT = BS.join(['C:', 'Users', 'pgoff', 'OneDrive', 'Desktop', 'Melange Systems'])

    def _windows(self, flag, **roots):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        if flag:
            os.environ['MELANGE_REBASE_STORED'] = '1'
        else:
            os.environ.pop('MELANGE_REBASE_STORED', None)
        patchers = [mock.patch.object(paths, '_IS_WINDOWS', True)] + [mock.patch.object(paths, k, v) for k, v in roots.items()]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_flag_off_is_the_identity_even_on_a_moved_root(self):
        self._windows(False, ROOT='D:/scratch/root')
        stored = self.STORED_ROOT + BS + 'data' + BS + 'x.json'
        self.assertEqual(paths.to_host(stored), stored)
        self.assertEqual(paths.to_stored('D:/scratch/root/data/x.json'), 'D:/scratch/root/data/x.json')

    def test_flag_on_rebases_a_moved_root_and_leaves_an_unmoved_root_as_written(self):
        self._windows(True, ROOT='D:/scratch/root', DRAFT=BS.join(['C:', 'Users', 'pgoff', 'OneDrive', 'Desktop', 'Draft Systems']))
        self.assertEqual(paths.to_host(self.STORED_ROOT + BS + 'data' + BS + 'x.json'), 'D:/scratch/root' + BS + 'data' + BS + 'x.json')
        draft = 'c:/users/pgoff/onedrive/desktop/draft systems/cu.csv'                  # DRAFT is unmoved: the text comes back untouched
        self.assertEqual(paths.to_host(draft), draft)
        self.assertEqual(paths.to_host('D:\\other\\x'), 'D:\\other\\x')

    def test_to_stored_is_the_inverse_in_the_laptops_spelling(self):
        for host, roots in ((False, dict(ROOT='/melange', DRAFT='/draft', LOCAL_MELANGE='/localappdata/Melange', MELANGE_DATA='/melange-data')),
                            (True, dict(ROOT='D:/scratch/root', DRAFT='D:/scratch/draft', LOCAL_MELANGE='D:/scratch/l/Melange', MELANGE_DATA='D:/scratch/d'))):
            with self.subTest(windows=host):
                if host:
                    self._windows(True, **roots)
                else:
                    stack = [mock.patch.object(paths, '_IS_WINDOWS', False)] + [mock.patch.object(paths, k, v) for k, v in roots.items()]
                    for patcher in stack:
                        patcher.start()
                        self.addCleanup(patcher.stop)
                for stored in (self.STORED_ROOT + BS + 'social_media.db', BS.join(['C:', 'melange-data', 'member_clips', 'a.wav']),
                               BS.join(['C:', 'Users', 'pgoff', 'AppData', 'Local', 'Melange', 'staffer_network', 'x'])):
                    self.assertEqual(paths.to_stored(paths.to_host(stored)), stored)
                self.assertEqual(paths.to_stored(roots['ROOT']), self.STORED_ROOT)              # the root itself
                self.assertEqual(paths.to_stored('/elsewhere/x'), '/elsewhere/x')               # not under a stored root: unchanged
                self.assertEqual(paths.to_stored(roots['ROOT'] + '-other/x'), roots['ROOT'] + '-other/x')   # a name that merely starts alike

    def test_a_root_nested_in_another_maps_to_the_longest_one(self):
        self._windows(True, ROOT='D:/s/root', LOCAL_MELANGE='D:/s/root/local/Melange')
        self.assertEqual(paths.to_stored('D:/s/root/local/Melange/a'), BS.join(['C:', 'Users', 'pgoff', 'AppData', 'Local', 'Melange', 'a']))
        self.assertEqual(paths.to_stored('D:/s/root/data'), self.STORED_ROOT + BS + 'data')

    def test_an_unset_local_root_does_not_make_the_helpers_raise(self):
        self._windows(True, ROOT='D:/scratch/root', LOCAL_MELANGE=paths._UnsetLocalRoot())
        self.assertEqual(paths.to_stored('D:/scratch/root/x'), self.STORED_ROOT + BS + 'x')
        self.assertEqual(paths.to_host(BS.join(['C:', 'melange-data', 'a'])), BS.join(['C:', 'melange-data', 'a']))

    def test_snapshot_path_rebases_a_stored_windows_path(self):
        from stance.build_field_action_evidence import Snapshot
        with tempfile.TemporaryDirectory() as tmp:
            stack = [mock.patch.object(paths, '_IS_WINDOWS', False), mock.patch.object(paths, 'ROOT', tmp)]
            for patcher in stack:
                patcher.start()
                self.addCleanup(patcher.stop)
            self.assertEqual(Snapshot(tmp).path(self.STORED_ROOT + BS + 'data' + BS + 'x.json'), pathlib.Path(tmp, 'data', 'x.json').resolve())
            self.assertEqual(Snapshot(tmp).path('data/x.json'), pathlib.Path(tmp, 'data', 'x.json').resolve())   # relative: unchanged

    def test_the_stance_fingerprint_names_inputs_relative_to_the_root(self):
        """A relocated root with the same bytes has the same fingerprint (the absolute path no longer enters the hash)."""
        OSS = __import__('stance.own_source_stance_model', fromlist=['x'])
        digests = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                for name in ('data/stance/cu_golden_labels.csv', 'data/stance/tweet_gold_queue.csv'):
                    os.makedirs(os.path.dirname(os.path.join(tmp, name)), exist_ok=True)
                    with open(os.path.join(tmp, name), 'wb') as handle:
                        handle.write(b'a,b\n1,2\n')
                with mock.patch.object(OSS, 'ROOT', tmp):
                    digests.append(OSS.input_fingerprint())
                    with open(os.path.join(tmp, 'data/stance/cu_golden_labels.csv'), 'ab') as handle:
                        handle.write(b'3,4\n')
                    digests.append(OSS.input_fingerprint())
        self.assertEqual(digests[0], digests[2])                                                # same bytes, two roots
        self.assertNotEqual(digests[0], digests[1])                                             # a changed input still moves it


class TextWrites(unittest.TestCase):
    def test_indented_json_is_crlf_on_every_os(self):
        import moneyball_core as MC
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'out.json')
            MC.dump_atomic({'a': [1, 2], 'b': {'c': 3}}, target, indent=1)
            with open(target, 'rb') as handle:
                raw = handle.read()
        self.assertIn(b'\r\n', raw)
        self.assertNotRegex(raw, rb'(?<!\r)\n')                                         # no bare LF: Linux bytes == Windows bytes
        self.assertEqual(json.loads(raw.decode('utf-8')), {'a': [1, 2], 'b': {'c': 3}})

    def test_compact_json_has_no_newline_at_all(self):
        # ranks_python.json / dashboard_data.json / bipartisan.json are byte-gated; compact output must stay newline-free
        import moneyball_core as MC
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'out.json')
            MC.dump_atomic({'a': 1}, target, separators=(',', ':'))
            with open(target, 'rb') as handle:
                self.assertEqual(handle.read(), b'{"a":1}')


class ChainLint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = chain_modules()

    def test_closure_is_not_vacuous(self):
        self.assertGreater(len(self.modules), 140)
        for must in ('fec/run_pipeline.py', 'fec/audit/audit_full.py', 'fec/engine/compute_pillars.py', 'fec/paths.py',
                     'fec/stance/build_media_blend.py', 'fec/speech/build_descriptive_speech.py', 'fec/engine/salience_shock.py'):
            self.assertIn(must, self.modules)

    def test_extra_seeds_are_still_named_by_their_launcher(self):
        for seed, launcher in SEED_LAUNCHERS.items():
            self.assertIn(os.path.basename(seed), _read(os.path.join(FEC, *launcher.split('/'))), '%s is not named by %s' % (seed, launcher))
            self.assertIn('fec/' + launcher, self.modules, launcher + ' is not in the closure itself')

    def _violations(self, finder, exempt=()):
        found = {}
        for rel, path in sorted(self.modules.items()):
            if rel in exempt or rel == 'fec/paths.py':
                continue
            hits = finder(path)
            if hits:
                found[rel] = hits
        return found

    def test_no_windows_path_literals_in_the_chain(self):
        self.assertEqual(self._violations(windows_path_literals, set(AT_LANE) | set(CLI_ONLY)), {})

    def test_cli_only_exemptions_still_needed(self):
        for rel in CLI_ONLY:
            self.assertTrue(windows_path_literals(self.modules[rel]), '%s has no Windows literal left: drop it from CLI_ONLY' % rel)

    def test_negative_control_the_detector_sees_windows_literals(self):
        # the exempt files still hold exactly what the lint hunts; if they stop, the exemption (and this proof) must go
        for rel in AT_LANE:
            self.assertTrue(windows_path_literals(self.modules[rel]) or text_writes_without_newline(self.modules[rel]),
                            '%s has nothing left to exempt: remove it from AT_LANE' % rel)

    def test_detector_classifies_the_known_shapes(self):
        for yes in ('C:' + BS + 'melange-data' + BS + 'x', BS + 'data' + BS + 'x.json', 'audit' + BS + 'a.py', '~' + BS + 'AppData',
                    '%LOCALAPPDATA%' + BS + 'Melange', 'C:/melange-data',
                    BS + 'budget_frontier.json', BS + 'b5_audio_attrib_*.json.candidate', BS + 'backups' + BS + 'x.xlsx',
                    BS + 'data' + BS + 'money' + BS + '%s.json',
                    BS + 'Ballotpedia_119th_CU_Influence_*.xlsx', BS + 'archive' + BS + 'Ballotpedia_119th_CU_Influence_*.xlsx'):   # M2
            self.assertTrue(_is_windows_path_constant(yes), yes)
        for no in (BS, BS + 'u2028', BS + 'bWORD' + BS + 'b', BS + 's+', BS + 'd', '/data/x', 'plain', BS + 'n',
                   BS + 'bfoo' + BS + 'b', BS + 'b(a|b)' + BS + 'b', BS + 'd{3}', '^' + BS + 'data',
                   BS + 'Bfoo' + BS + 'B', BS + 'Bfoo' + BS + 'w+' + BS + 'B'):
            self.assertFalse(_is_windows_path_constant(no), no)

    def test_negative_control_ballotpedia_glob_is_caught(self):
        # M2: an unanchored \B alternative once classed B+r'\Ballotpedia_*.xlsx' as a regex and hid it from the lint
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8', newline='\n') as handle:
            handle.write("import glob\nglob.glob(B + r'" + BS + "Ballotpedia_119th_CU_Influence_*.xlsx')\n")
        try:
            self.assertEqual([line for line, _ in windows_path_literals(handle.name)], [2])
        finally:
            os.unlink(handle.name)

    def test_no_bare_localappdata_subscript_or_percent_expandvars(self):
        self.assertEqual(self._violations(bare_localappdata_reads), {})

    def test_every_text_write_declares_newline(self):
        self.assertEqual(self._violations(text_writes_without_newline, set(AT_LANE) | set(WRITE_EXEMPT)), {})

    def test_every_stored_path_read_is_translated(self):
        self.assertEqual(self._violations(stored_path_reads, set(AT_LANE) | set(STORED_READ_EXEMPT)), {})

    def test_stored_read_exemptions_still_needed(self):
        for rel in STORED_READ_EXEMPT:
            self.assertTrue(stored_path_reads(self.modules[rel]), '%s no longer needs its stored-path exemption' % rel)

    def test_negative_control_stored_path_detector(self):
        for source, expected in (
                ('from pathlib import Path\nPath(row.get("out_wav") or "")\n', [(2, 'Path')]),
                ('import os\nos.path.exists(receipt["path"])\n', [(2, 'exists')]),
                ('from pathlib import Path\nPath(to_host(row.get("out_wav") or ""))\n', []),
                ('from pathlib import Path\nPath(stored_name(row["source_run"]))\n', []),
                ('from pathlib import Path\nPath(row.get("title") or "")\n', [])):
            with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8', newline='\n') as handle:
                handle.write(source)
            try:
                self.assertEqual(stored_path_reads(handle.name), expected, source)
            finally:
                os.unlink(handle.name)

    def test_write_exemptions_still_needed(self):
        for rel in WRITE_EXEMPT:
            self.assertTrue(text_writes_without_newline(self.modules[rel]), '%s no longer needs its write exemption' % rel)

    def test_every_seam_name_a_module_uses_is_bound(self):
        self.assertEqual(self._violations(undefined_seam_names), {})

    def test_negative_control_unbound_name_is_caught(self):
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8', newline='\n') as handle:
            handle.write('import os\nx = to_host("C:' + BS * 2 + 'a")\n')
        try:
            self.assertEqual(undefined_seam_names(handle.name), [('to_host', 2)])
            self.assertEqual(text_writes_without_newline(handle.name), [])
        finally:
            os.unlink(handle.name)
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8', newline='\n') as handle:
            handle.write("open('p', 'w')\nopen('q', 'w', newline='')\nopen('r', 'rb')\nopen('s')\n")
        try:
            self.assertEqual(text_writes_without_newline(handle.name), [(1, 'open')])
        finally:
            os.unlink(handle.name)

    def test_stored_path_read_sites_translate(self):
        """The four audio modules read Windows paths stored in clip manifests; every Path built from such a field goes through to_host()."""
        raw_field = re.compile(r"""Path\((?:str\()?\w+(?:\.get\(|\[)["'](?:out_wav|src_wav|source_wav|wav|wav_path)["']""")
        # (module, to_host call sites at the time of writing): a drop means a read site lost its translation
        for rel, floor in (('fec/audit_collection.py', 10), ('fec/speech/build_descriptive_speech.py', 14),
                           ('fec/members/b5_audio_manifest_adapter.py', 2), ('fec/members/b5_audio_descriptors_candidate.py', 1),
                           ('fec/speech/build_member_longform_turn_features.py', 1)):
            text = _read(self.modules[rel])
            self.assertEqual(raw_field.findall(text), [], rel)
            self.assertGreaterEqual(text.count('to_host('), floor, rel)


if __name__ == '__main__':
    unittest.main()
