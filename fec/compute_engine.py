# -*- coding: utf-8 -*-
"""Per-phase compute engine for the scheduled refreshes (R-6 Option A, Paul 2026-10-12).

A refresh step runs either on this host exactly as it always has ('windows': sys.executable + the producer script) or in
its compute container ('container': `docker compose run` on one of ops/compute/*.compose.yaml). Which one is decided PER
PHASE, from ops/compute/engines.json (`{"collect":"windows","chain":"windows","office":"windows"}`) with an environment
override per key (MELANGE_ENGINE_COLLECT, MELANGE_ENGINE_CHAIN, MELANGE_ENGINE_OFFICE).  A missing file, or a key not in it,
means 'windows': nothing changes until `python ops/compute/cutover.py set <phase> container` switches a phase on.

    phase    engine key  steps that move                                            compose service
    collect  collect     network collectors in STEP_SERVICE                          collectors.compose.yaml  collect, llm
    chain    chain       fec/run_pipeline.py                                         melange.compose.yaml     chain
    publish  office      the LibreOffice view copy of the workbook (office_view)     office-calc.compose.yaml office-calc

Every producer NOT named in STEP_SERVICE stays on the host whatever the engine says.  The reasons, so the list can be
revisited deliberately: R collectors (refresh_cspan_wj, refresh_press_stream) have no r-compute service yet; the steps that take
the pipeline writer lease themselves (update_bill_repository, daily_media_refresh) are orchestrators and stay where the lease is
judged by pid; fold_promoted_press_runs rewrites Draft Systems, which no service mounts read-write; member_reference_layer
writes member_references.json at the tree root, which the llm service cannot allow; publish_dashboard, the derived layers and
the diagnostics have no parity evidence.  The visible-Chrome congress.gov cookie lane is not a step here at all (decided
2026-10-12: it stays a host tool).

Container mode never guesses: the image is the env override (IMAGE_COMPUTE_*, a digest reference) else the verified current
manifest (`python ops/release_lib/current.py image <name>`), else EngineError and nothing starts.  Inside a container every
phase resolves to 'windows' (the -e flags built here), so a container never tries to start a sibling container.
"""
import collections
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import paths

ENGINES = ('windows', 'container')
KEYS = ('collect', 'chain', 'office')
PHASE_KEY = {'collect': 'collect', 'chain': 'chain', 'publish': 'office'}
PHASES = tuple(PHASE_KEY)
CONFIG = ('ops', 'compute', 'engines.json')
ENV_PREFIX = 'MELANGE_ENGINE_'
IMAGE_RE = re.compile(r'^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$')   # same shape release_lib.manifest accepts; always .fullmatch
# Environment the orchestrators set for their children and the compose files do not declare: forwarded when set, so a
# container child sees what the host child saw (BILLSTATUS_BULK_REFRESH and COSPONSOR_FORECAST_REFRESH change what the chain does).
FORWARD = ('BILLSTATUS_BULK_REFRESH', 'COSPONSOR_FORECAST_REFRESH', 'MELANGE_TODAY', 'PYTHONUTF8', 'PYTHONUNBUFFERED')
SECRET_VARS = {'collect': ('GOVINFO_KEY_FILE_HOST', 'FEC_DATA_GOV_FILE_HOST'), 'llm': ('CLAUDE_CREDENTIALS_FILE_HOST',)}
ALL_SECRET_VARS = ('GOVINFO_KEY_FILE_HOST', 'FEC_DATA_GOV_FILE_HOST', 'CLAUDE_CREDENTIALS_FILE_HOST')

SERVICES = {
    'collect': dict(phase='collect', compose='collectors.compose.yaml', profile='collect',
                    image='compute-collector', image_var='IMAGE_COMPUTE_COLLECTOR'),
    'llm': dict(phase='collect', compose='collectors.compose.yaml', profile='llm',
                image='compute-collector', image_var='IMAGE_COMPUTE_COLLECTOR'),
    'chain': dict(phase='chain', compose='melange.compose.yaml', profile='chain',
                  image='compute-melange', image_var='IMAGE_COMPUTE_MELANGE'),
    'office-calc': dict(phase='publish', compose='office-calc.compose.yaml', profile='office',
                        image='office-calc', image_var='IMAGE_OFFICE_CALC'),
}
STEP_SERVICE = {
    'fetch_congressional_record.py': 'collect',
    'refresh_legislation_discovery.py': 'collect',
    'refresh_fec_money.py': 'collect',
    'refresh_vote_sources.py': 'collect',
    'refresh_billstatus_bulk.py': 'collect',
    'fetch_interviews.py': 'collect',
    'wikipedia_biographies.py': 'collect',
    'refresh_sentinel_admissions.py': 'llm',
    'run_pipeline.py': 'chain',
}
WORKBOOK = 'Moneyball Main Version - Blended.xlsx'
VIEW_DIR = os.path.join('outputs', 'workbook_view')

# argv: the command to run.  env: None = the caller keeps its own environment (the host path, byte for byte as before).
# container: the container's name when one was started, for reap().  phase: the phase it ran under (None on the host).
Launch = collections.namedtuple('Launch', 'argv env container phase')


class EngineError(RuntimeError):
    """A named, fail-closed stop: the container engine was asked for and cannot be started honestly."""


def load(root=None, environ=None):
    """{'collect': ..., 'chain': ..., 'office': ...} from engines.json under `root`, then the MELANGE_ENGINE_* overrides."""
    environ = os.environ if environ is None else environ
    path = os.path.join(os.path.abspath(root or paths.ROOT), *CONFIG)
    try:
        with open(path, encoding='utf-8') as handle:
            doc = json.load(handle)
    except FileNotFoundError:
        doc = {}
    except (OSError, ValueError) as error:
        raise EngineError('%s is unreadable: %s' % (path, error))
    if not isinstance(doc, dict) or set(doc) - set(KEYS):
        raise EngineError('%s must be a JSON object with only the keys %s' % (path, ', '.join(KEYS)))
    engines = dict.fromkeys(KEYS, 'windows')
    engines.update(doc)
    for key in KEYS:
        if environ.get(ENV_PREFIX + key.upper(), '').strip():
            engines[key] = environ[ENV_PREFIX + key.upper()].strip()
    bad = {key: value for key, value in engines.items() if value not in ENGINES}
    if bad:
        raise EngineError('engine must be one of %s: %s' % (' / '.join(ENGINES), json.dumps(bad, sort_keys=True)))
    return engines


_SNAPSHOT = None


def snapshot():
    """The engines for this process, read once: a refresh never changes engine half way through."""
    global _SNAPSHOT
    if _SNAPSHOT is None:
        _SNAPSHOT = load()
    return _SNAPSHOT


def reset():
    global _SNAPSHOT
    _SNAPSHOT = None


def image(name, var, root=None, environ=None):
    """repo@sha256:... for an image: the env override `var`, else the verified current manifest.  Anything else raises."""
    environ = os.environ if environ is None else environ
    ref = (environ.get(var) or '').strip()
    if not ref:
        current = os.path.join(os.path.abspath(root or paths.ROOT), 'ops', 'release_lib', 'current.py')
        try:
            done = subprocess.run([sys.executable, current, 'image', name], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            raise EngineError('no verified image for %s: not running (%s: %s)' % (name, type(error).__name__, error))
        ref = done.stdout.strip()
        if done.returncode != 0 or not ref:
            raise EngineError('no verified image for %s: not running (%s). To run a build no signed release names yet, set %s '
                              'to its digest.' % (name, done.stderr.strip() or 'current.py printed nothing', var))
    if not IMAGE_RE.fullmatch(ref):
        raise EngineError('%s is not a digest reference (repo@sha256:<64 hex>); tags are refused (design F10): %r' % (name, ref))
    return ref


def _rel(root, path):
    """`path` as a POSIX path under `root`, or EngineError: the container mounts the tree and nothing else."""
    try:
        rel = os.path.relpath(os.path.abspath(path), root)
    except ValueError:
        rel = '..'
    if rel == '..' or rel.startswith('..' + os.sep) or os.path.isabs(rel):
        raise EngineError('%s is outside the tree %s, which is all a container mounts' % (path, root))
    return rel.replace(os.sep, '/')


def _scratch():
    """An existing empty file and an existing directory for compose variables a service does not use but Compose still parses."""
    empty = os.path.join(tempfile.gettempdir(), 'melange-compute-empty')
    if not os.path.exists(empty):
        open(empty, 'w').close()
    return empty, tempfile.gettempdir()


def _glassbox():
    return os.environ.get('GLASSBOX_EVENTS_DIR') or os.path.join(os.path.expanduser('~'), '.claude', 'glassbox', 'events')


def _compose_env(service, root, environ):
    """The environment `docker compose` needs to parse the service's file.  Variables already set win; secrets are never derived."""
    spec, env = SERVICES[service], dict(environ)
    derived = {spec['image_var']: image(spec['image'], spec['image_var'], root, environ)}
    if spec['compose'] == 'melange.compose.yaml':
        derived.update(MELANGE_TREE_DIR=root, MELANGE_DRAFT_DIR=paths.DRAFT, MELANGE_DATA_DIR=paths.MELANGE_DATA,
                       MELANGE_LOCAL_DIR=os.fspath(paths.LOCAL_MELANGE), MELANGE_GLASSBOX_DIR=_glassbox())
    elif spec['compose'] == 'collectors.compose.yaml':
        empty, directory = _scratch()
        derived.update(MELANGE_HOST_ROOT=root, GLASSBOX_HOST_EVENTS=_glassbox(),
                       LDA_RAW_HOST_DIR=os.path.join(os.fspath(paths.LOCAL_MELANGE), 'lda_rebuild', 'raw'),
                       CASSETTE_HOST_DIR=directory, REPLAY_OUT_HOST_DIR=directory)
        for var in ALL_SECRET_VARS:
            if var in SECRET_VARS[service]:
                if not os.path.isfile(env.get(var) or ''):
                    raise EngineError('the %s service needs %s to name an existing secret file (the job wrapper sets it); not running'
                                      % (service, var))
            else:
                derived[var] = empty
    for key, value in derived.items():
        if not env.get(key):
            env[key] = value
    return env


def _container_name(service):
    return 'melange-%s-%s' % (service, uuid.uuid4().hex[:10])


def engine_of(script, phases=PHASES):
    """'container' when launch() would start `script` in its container, else 'windows'."""
    service = STEP_SERVICE.get(os.path.basename(script))
    if service is None or SERVICES[service]['phase'] not in phases:
        return 'windows'
    return snapshot()[PHASE_KEY[SERVICES[service]['phase']]]


def launch(script, arguments, *, phases=PHASES, root=None, cwd=None, environ=None):
    """How to start one producer: -> Launch.  `script` is its path on this host, `arguments` what follows it, `environ` the
    environment the host child would get (None: this process's).  On the host this is [sys.executable, script, *arguments]
    and env None -- exactly the command the callers built before this module existed."""
    host = Launch([sys.executable, script, *arguments], None, None, None)
    if engine_of(script, phases) != 'container':
        return host
    service = STEP_SERVICE[os.path.basename(script)]
    phase = SERVICES[service]['phase']
    root = os.path.abspath(root or paths.ROOT)
    environ = os.environ if environ is None else environ
    for argument in arguments:
        if root in str(argument):
            raise EngineError('argument %r names the host tree; a container sees it as /melange. Not translated, not running.'
                              % str(argument)[:200])
    compose = os.path.join(root, 'ops', 'compute', SERVICES[service]['compose'])
    if not os.path.isfile(compose):
        raise EngineError('%s does not exist: the container engine needs the compose files of this tree' % compose)
    env = _compose_env(service, root, environ)
    name = _container_name(service)
    argv = ['docker', 'compose', '-f', compose, '--profile', SERVICES[service]['profile'], 'run', '--rm', '-T', '--name', name,
            '-w', posixpath.normpath('/melange/' + _rel(root, cwd or os.path.dirname(script)))]
    for key in KEYS:
        argv += ['-e', '%s%s=windows' % (ENV_PREFIX, key.upper())]
    for key in FORWARD:
        if environ.get(key):
            argv += ['-e', '%s=%s' % (key, environ[key])]
    argv += [service, 'python', '/melange/' + _rel(root, script), *[str(a) for a in arguments]]
    return Launch(argv, env, name, phase)


def reap(spec):
    """Remove a container whose client was killed (a timeout, Ctrl-C).  --rm already removed one that ended; this is best effort."""
    if spec.container:
        try:
            subprocess.run(['docker', 'rm', '-f', spec.container], capture_output=True, timeout=60)
        except Exception:
            pass


def office_view(root=None, timeout=3600):
    """publish phase, office engine: a LibreOffice view copy of the canonical workbook.  -> (status, note); status is
    'skipped' on the host engine (today the refresh has no such step), else 'ok' or 'failed'.  Never raises: the view is
    produced after the chain and nothing waits on it."""
    if snapshot()['office'] != 'container':
        return 'skipped', ''
    root = os.path.abspath(root or paths.ROOT)
    out = os.path.join(root, VIEW_DIR)
    folder = os.path.join(out, 'in')
    try:
        import bounded_subprocess
        import hashlib
        service = SERVICES['office-calc']
        compose = os.path.join(root, 'ops', 'compute', service['compose'])
        if not os.path.isfile(compose):
            raise EngineError('%s does not exist' % compose)
        env = dict(os.environ)
        env[service['image_var']] = image(service['image'], service['image_var'], root)
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
        copy = os.path.join(folder, WORKBOOK)
        shutil.copyfile(os.path.join(root, WORKBOOK), copy)
        digests = []
        for path in (os.path.join(root, WORKBOOK), copy):
            h = hashlib.sha256()
            with open(path, 'rb') as handle:
                for block in iter(lambda: handle.read(1 << 20), b''):
                    h.update(block)
            digests.append(h.hexdigest())
        if digests[0] != digests[1]:
            raise EngineError('the workbook changed while it was being copied')
        env.update(OFFICE_IN_DIR=folder, OFFICE_OUT_DIR=out)
        name = _container_name('office-calc')
        view = WORKBOOK[:-len('.xlsx')] + '.view.xlsx'
        argv = ['docker', 'compose', '-f', compose, '--profile', service['profile'], 'run', '--rm', '-T', '--name', name,
                'office-calc', '--in', '/in/' + WORKBOOK, '--expect-sha', digests[0], '--out', '/out/' + view,
                '--receipt', '/out/' + view + '.receipt.json']
        run = bounded_subprocess.run_bounded(argv, timeout=timeout, env=env, cwd=root)
        if run.timed_out:
            reap(Launch(argv, env, name, 'publish'))
            return 'failed', 'timed out after %ds' % timeout
        if run.returncode != 0:
            return 'failed', 'office-calc exited %s: %s' % (run.returncode, (run.stderr or '')[-300:].replace('\n', ' | '))
        return 'ok', 'view copy %s' % os.path.join(VIEW_DIR, view)
    except Exception as error:
        return 'failed', '%s: %s' % (type(error).__name__, str(error)[:300])
    finally:
        shutil.rmtree(folder, ignore_errors=True)
