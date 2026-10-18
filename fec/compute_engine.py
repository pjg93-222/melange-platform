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

Secrets (Paul 2026-10-18): a *_FILE_HOST variable the caller already set is used as given.  One it left unset is staged by
launch() on the container path only (never on the host): the key goes into a one-line file in a per-run folder that only this
user can read, found where the Windows collectors find it; release() / reap() delete the folder when the run ends and the next
staging sweeps a crash leftover.  The CLI login is not copied: its own file is mounted read-only.  No key value is ever put in
an argument, an environment value launch() returns, an error message or a log line.
"""
import collections
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

import paths
from bounded_subprocess import run_bounded

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
SECRET_FILE = {'GOVINFO_KEY_FILE_HOST': 'govinfo_key', 'FEC_DATA_GOV_FILE_HOST': 'fec_data_gov'}   # names in a run's folder (the compose secret names)
KEYS_JSON = ('OneDrive', 'Desktop', 'API Keys', 'keys.json')   # under the home dir: the --key-file default of refresh_fec_money.py:162
SECRET_TTL = 6 * 3600   # a staged folder this old is a crash leftover: a secret is read when its container starts, seconds after staging

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
            done = run_bounded([sys.executable, current, 'image', name], timeout=60,
                               start_new_session=os.name != 'nt')
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


def _secret_root():
    return os.path.join(tempfile.gettempdir(), 'melange-secrets')   # under the user profile, so Docker Desktop can bind-mount it


def _key_line(var, root, environ):
    """The key a Windows collector would use for `var`, looked up as it does (the environment, then its file): -> the one line
    as bytes, or None for any problem (absent, unreadable, blank, more than one line).  Nothing read is ever put in a message."""
    try:
        if var == 'GOVINFO_KEY_FILE_HOST':          # fetch_congressional_record.py:33
            value = environ.get('GOVINFO_KEY')
            if not value:
                with open(os.path.join(root, 'fec', 'govinfo_key.txt'), encoding='utf-8-sig') as handle:
                    value = handle.read()
        else:                                       # refresh_fec_money.py:165-166
            value = environ.get('FEC_API_KEY')
            if not value:
                with open(os.path.join(os.path.expanduser('~'), *KEYS_JSON), encoding='utf-8') as handle:
                    value = json.load(handle)['data_gov']['keys'][0]
        value = value.strip()
        return value.encode('utf-8') if value and len(value.splitlines()) == 1 else None
    except (OSError, ValueError, LookupError, TypeError, AttributeError):
        return None


def _owner_only(path):
    """POSIX: refuse `path` unless it is a real directory (not a link) owned by this process with no group or other access.  In a
    shared /tmp a root another user made first, or can write, lets them swap a run's folder for a link to theirs between the mkdir
    and the key write; O_EXCL guards only the last path component.  Never chmods.  Windows: per-user %TEMP% and icacls do this."""
    if os.name == 'nt':
        return
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise EngineError('%s is not a directory (a link or a file); not running' % path)
    # geteuid, not getuid: the kernel gives new files and folders the effective uid, so that is who must own them.
    if info.st_uid != os.geteuid():
        raise EngineError('%s is owned by uid %d, not this process (uid %d); not running' % (path, info.st_uid, os.geteuid()))
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise EngineError('%s allows group or other access (mode %03o); not running' % (path, stat.S_IMODE(info.st_mode)))


def _private_dir(name):
    """A new folder for one run's secret files that only this user can read; sweeps the crash leftovers of earlier runs first."""
    parent = _secret_root()
    os.makedirs(parent, mode=0o700, exist_ok=True)  # a new root is 0o700 (umask only removes bits); an existing one is checked, never fixed
    if os.path.islink(parent) or os.path.isjunction(parent):    # a planted link would send the keys, and the sweep, elsewhere
        raise EngineError('%s is a symbolic link or junction; not running' % parent)
    _owner_only(parent)                             # before the sweep and the mkdir: no other user may change what is in it
    for old in os.listdir(parent):
        try:
            if time.time() - os.path.getmtime(os.path.join(parent, old)) > SECRET_TTL:
                shutil.rmtree(os.path.join(parent, old), ignore_errors=True)
        except OSError:
            pass
    path = os.path.join(parent, name)
    os.mkdir(path, 0o700)                           # POSIX: owner only.  Windows ignores the mode, so the ACL below does it
    _owner_only(path)
    if os.name == 'nt':
        user = os.environ.get('USERNAME')
        if not user:
            raise EngineError('USERNAME is not set, so %s cannot be restricted to the current user; not running' % path)
        user = '%s\\%s' % (os.environ['USERDOMAIN'], user) if os.environ.get('USERDOMAIN') else user
        # Inheritance off and this user the only entry; the files created inside inherit it.  Python's own mkdir(0o700) ACL (3.12.4+)
        # and the parent's both carry SYSTEM, Administrators and OWNER RIGHTS (*S-1-5-18, *S-1-5-32-544, *S-1-3-4): removed as well.
        try:
            done = run_bounded(['icacls', path, '/inheritance:r', '/remove:g', '*S-1-5-18', '*S-1-5-32-544', '*S-1-3-4',
                                '/grant:r', user + ':(OI)(CI)F'], timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            raise EngineError('could not restrict %s to the current user (%s); not running' % (path, type(error).__name__))
        if done.returncode != 0:
            raise EngineError('could not restrict %s to the current user (icacls exit %s); not running' % (path, done.returncode))
    return path


def _stage_secrets(service, root, environ, name):
    """Container path only: -> {VAR: path} for the secret-file variables of `service` that `environ` does not already set.
    GOVINFO_KEY_FILE_HOST and FEC_DATA_GOV_FILE_HOST name a one-line file written into this run's private folder; the folder is
    named for the container (`name`) so release() / reap() find it.  CLAUDE_CREDENTIALS_FILE_HOST names the CLI's own login file
    (D-179: Compose mounts it read-only; it is not copied).  The caller (launch) deletes the folder if anything after this fails."""
    staged, folder = {}, None
    for var in SECRET_VARS.get(service, ()):
        if (environ.get(var) or '').strip():
            continue                                # the caller's own file (collectors_replay, a test) wins
        if var == 'CLAUDE_CREDENTIALS_FILE_HOST':
            staged[var] = os.path.join(os.path.expanduser('~'), '.claude', '.credentials.json')
            continue
        line = _key_line(var, root, environ)
        if line is None:
            raise EngineError('%s is unset and no usable one-line key was found in %s; not running' % (
                var, 'GOVINFO_KEY or fec/govinfo_key.txt' if var == 'GOVINFO_KEY_FILE_HOST'
                else 'FEC_API_KEY or data_gov.keys[0] of ~/' + '/'.join(KEYS_JSON)))
        folder = folder or _private_dir(name)
        path = staged[var] = os.path.join(folder, SECRET_FILE[var])
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(line + b'\n')
        except OSError as error:
            raise EngineError('could not write %s (%s); not running' % (path, type(error).__name__))
    return staged


def _drop(name):
    """Delete the secret folder staged for container `name`.  Never raises; a `name` that is not a plain folder name directly under
    the secret root is ignored, so a bad name cannot reach anything else."""
    try:
        parent = _secret_root()
        path = os.path.join(parent, name)
        if name and os.path.dirname(os.path.abspath(path)) == os.path.abspath(parent):
            shutil.rmtree(path, ignore_errors=True)
            if os.path.exists(path):
                print('melange-secrets: %s could not be deleted; the next staging sweeps it' % path, file=sys.stderr)
    except Exception:
        pass


def release(spec):
    """Delete the secret files launch() staged for `spec`: a no-op on the host engine and on a second call; never raises.  reap()
    calls it; a caller that does not reap on a normal exit calls it on every path."""
    if spec.container:
        _drop(spec.container)


def _compose_env(service, root, environ):
    """The environment `docker compose` needs to parse the service's file.  Variables already set win (launch stages the secret
    files before this runs, so they are set by now); a secret file that still does not exist is an error, never a guess."""
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
                    raise EngineError('the %s service needs %s to name an existing secret file (the caller sets it, or launch() stages it); not running'
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
    and env None -- exactly the command the callers built before this module existed.

    On the container path a secret-file variable the caller did not set is staged here (_stage_secrets), so the caller MUST call
    release(spec) or reap(spec) when the run ends, on every path; a launch() that raises has already taken its files back."""
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
    name = _container_name(service)
    svc = SERVICES[service]
    # The image first: a run that cannot start never stages a key.  _compose_env finds the resolved reference here and checks it again.
    environ = {**environ, svc['image_var']: image(svc['image'], svc['image_var'], root, environ)}
    try:
        env = _compose_env(service, root, {**environ, **_stage_secrets(service, root, environ, name)})
        argv = ['docker', 'compose', '-f', compose, '--profile', SERVICES[service]['profile'], 'run', '--rm', '-T', '--name', name,
                '-w', posixpath.normpath('/melange/' + _rel(root, cwd or os.path.dirname(script)))]
        for key in KEYS:
            argv += ['-e', '%s%s=windows' % (ENV_PREFIX, key.upper())]
        for key in FORWARD:
            if environ.get(key):
                argv += ['-e', '%s=%s' % (key, environ[key])]
        argv += [service, 'python', '/melange/' + _rel(root, script), *[str(a) for a in arguments]]
    except BaseException:
        _drop(name)                                 # nothing was started: take back any secret file staged for it
        raise
    return Launch(argv, env, name, phase)


def reap(spec):
    """Stop, then remove, a container whose client was killed (a timeout, Ctrl-C).  --rm already removed one that ended; this is best effort.

    The stop gives the chain a SIGTERM and 30 s to unwind (release its lease) before the force-remove.  The container's secret
    files (if launch staged any) are deleted last, once nothing can still have them mounted."""
    if spec.container:
        for argv, limit in ((['docker', 'stop', '-t', '30', spec.container], 90), (['docker', 'rm', '-f', spec.container], 60)):
            try:
                run_bounded(argv, timeout=limit, start_new_session=os.name != 'nt')
            except Exception:
                pass
        release(spec)


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
