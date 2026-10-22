"""Backend of the admin dashboard's Apps section. Stdlib only.

Docker is reached from this process (never from a container, D-239) through one function, Docker.run. It builds its argv
only from the TEMPLATES allowlist below: shell=False, a timeout per command, a minimal environment, captured output. The
only strings that reach an argv are a service name checked against the configured set, a container id read from
`docker ps`, an image reference read from `compose config`, a timestamp this module formats, and (Terrier only) an invite
email that matches EMAIL plus a role name that maps to fixed flags. Terrier is one service (D-241). The one other program it
starts is Docker Desktop itself (Docker.start_desktop): its exe from two fixed install folders, no shell, no arguments.

Reading the services is two cheap calls, because `compose ps` is slow on a busy machine: `docker ps -a -q --no-trunc` with
the project's compose label (the only filter the allowlist has) lists the container ids, and one `docker inspect` of them
gives each container's service (its compose label), state, health, image and published ports. The parsed `compose config`
is kept until the compose file or an env file it reads changes. The last good list is kept too: a read that is already
running, or one that times out or fails, answers with that list marked stale instead of making the page wait.

Update ready: `compose config --format json` gives the image the compose file pins for each service (digest-pinned, from
ops/.env). A container is ready to update exactly when the image reference it was created from (Config.Image) differs from
that pin (compared by digest when both carry one), which is also exactly when `compose up -d` would recreate it. No
registry call is needed to know that; the release's version label is read from the pinned image only when it is already on
this computer. Start is `compose start`: it never pulls or recreates, so only Update (pull, then up -d) can move a service
to a new release, running or stopped.
"""
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

OPS = Path(__file__).resolve().parents[2]
REDACTED = '[redacted]'
VERSION_LABEL = 'org.opencontainers.image.version'
REVISION_LABEL = 'org.opencontainers.image.revision'
ACTIONS = ('start', 'restart', 'update', 'backup')
ROLES = {'administrator': ('--administrator',),
         'organizer': ('--field-staff', '--field-manager'),
         'canvasser': ('--field-staff',)}
DF_TTL = 300
VERSION_RETRY = 600   # seconds before a failed /version probe is tried again for the same container
REFRESH_BUDGET = 10   # seconds one read of the service list may take in all: the page gives up on a request at 12 s
CALL_CAP = {'ps': 6, 'inspect': 4, 'config': 10, 'image_inspect': 3}   # ps then inspect run one after the other (10 s); config beside them
MIN_CALL = 0.5        # a docker call is not started with less time than this left
INVITE_TIMEOUT = 30  # the invite command; with REFRESH_BUDGET and a 5 s tree kill it ends inside the 50 s the page waits for /apps/invite
PROJECT_LABEL, SERVICE_LABEL, ONEOFF_LABEL = 'com.docker.compose.project', 'com.docker.compose.service', 'com.docker.compose.oneoff'
LOCAL_HOSTS = ('', '0.0.0.0', '::', '127.0.0.1', '::1')   # a published port counts when this computer's loopback reaches it
PINNED_NOTICE = 'The pinned images could not be read from the compose file, so updates cannot be detected.'


class ReadJob:
    """One read of the service list, running in its own thread: result or error is set before done."""

    def __init__(self):
        self.done, self.result, self.error = threading.Event(), None, None


class AppsError(Exception):
    status, code = 500, 'apps_error'

    def __init__(self, message=''):
        super().__init__(message)
        self.message = message


class BadRequest(AppsError):
    status, code = 400, 'invalid_request'


class Busy(AppsError):
    status, code = 409, 'busy'


class NotSetUp(AppsError):
    status, code = 409, 'not_set_up'


class DockerUnavailable(AppsError):
    status, code = 503, 'docker_unavailable'
    down = False   # True when the Docker daemon itself is not running (Docker Desktop is closed): the page then offers to start it


class ActionFailed(AppsError):
    status, code = 502, 'action_failed'


class NotAllowed(ValueError):
    """An argv outside the allowlist was requested: a bug, never a user path."""


@dataclass(frozen=True)
class Service:
    name: str            # compose service name
    display: str
    profile: str         # compose profile that enables it
    relay: str           # compose service that publishes its port (network_mode: service:<relay>)
    terrier: bool = False


# The one place to change when the compose profile changes (ops/compose.local.yaml on claude/local-profiles).
DEFAULT_PROJECT = 'melange-local'
DEFAULT_SERVICES = (Service('melange-desk', 'Melange desk', 'desk', 'desk-relay'),
                    Service('mentat-console', 'Mentat console', 'mentat', 'mentat-relay'),
                    Service('terrier', 'Terrier', 'terrier', 'terrier-relay', terrier=True))


@dataclass(frozen=True)
class AppsConfig:
    project: str
    compose_file: str
    services: tuple
    actor: str = 'admin'

    def __post_init__(self):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', self.project) or not os.path.isabs(self.compose_file):
            raise ValueError('compose project name and an absolute compose file path required')


def default_config(environ=os.environ):
    return AppsConfig(environ.get('MELANGE_ADMIN_COMPOSE_PROJECT', DEFAULT_PROJECT),
                      environ.get('MELANGE_ADMIN_COMPOSE_FILE', str(OPS / 'compose.local.yaml')), DEFAULT_SERVICES)


# ---- the allowlist -------------------------------------------------------------------------------------------------

HEX_ID = re.compile(r'[0-9a-f]{12,64}')
IMAGE_REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/:-]{0,255}(@sha256:[0-9a-f]{64})?')
EMAIL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}')
ORIGIN = re.compile(r'http://127\.0\.0\.1:[0-9]{2,5}')
STAMP = re.compile(r'[0-9]{8}T[0-9]{6}Z')


def _match(pattern, value):
    if not isinstance(value, str) or not pattern.fullmatch(value) or len(value) > 256:
        raise NotAllowed('value outside the allowlist')
    return value


def _service(config, value):
    if not isinstance(value, str) or value not in {s.name for s in config.services}:
        raise NotAllowed('unknown service')
    return value


def _terrier(config, value):
    if not isinstance(value, str) or value not in {s.name for s in config.services if s.terrier}:
        raise NotAllowed('not a Terrier service')
    return value


def _email(config, value):
    if isinstance(value, str) and len(value) > 150:
        raise NotAllowed('value outside the allowlist')
    return _match(EMAIL, value)


def _ids(config, value):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 16:
        raise NotAllowed('container ids required')
    return [_match(HEX_ID, v) for v in value]


def _role(config, value):
    if value not in ROLES:
        raise NotAllowed('unknown role')
    return value


CHECKS = {'service': _service, 'terrier': _terrier, 'ids': _ids, 'role': _role,
          'container': lambda c, v: _match(HEX_ID, v), 'ref': lambda c, v: _match(IMAGE_REF, v),
          'email': _email,
          'origin': lambda c, v: _match(ORIGIN, v), 'stamp': lambda c, v: _match(STAMP, v)}


def _compose(c):
    return ['compose', '-p', c.project, '-f', c.compose_file]


Template = namedtuple('Template', 'params build')
TEMPLATES = {
    # Plain `docker ps`, not `compose ps` (which asks the daemon about every container and took 77 s on a busy machine). The one
    # filter is the project's own label; its value is the validated project name, so no caller can supply another filter.
    'ps': Template((), lambda c: ['ps', '-a', '-q', '--no-trunc', '--filter', f'label={PROJECT_LABEL}={c.project}']),
    'config': Template((), lambda c: _compose(c) + ['config', '--format', 'json']),
    'inspect': Template(('ids',), lambda c, ids: ['inspect', *ids]),
    'image_inspect': Template(('ref',), lambda c, ref: ['image', 'inspect', ref]),
    'logs': Template(('container',), lambda c, container: ['logs', '--tail', '500', '--timestamps', container]),
    'start': Template(('service',), lambda c, service: _compose(c) + ['start', service]),   # never up/pull: it cannot update
    'restart': Template(('service',), lambda c, service: _compose(c) + ['restart', service]),
    'up': Template(('service',), lambda c, service: _compose(c) + ['up', '-d', service]),
    'pull': Template(('service',), lambda c, service: _compose(c) + ['pull', service]),
    'df': Template((), lambda c: ['system', 'df', '-v', '--format', 'json']),
    'invite': Template(('terrier', 'email', 'role', 'origin'), lambda c, terrier, email, role, origin: (
        _compose(c) + ['exec', '-T', terrier, 'python', 'manage.py', 'invite', *ROLES[role], '--origin', origin, '--', email])),
    # ponytail: backups accumulate in /state/backups with no pruning; retention or deletion is Paul's call.
    'backup': Template(('terrier', 'stamp'), lambda c, terrier, stamp: (
        _compose(c) + ['exec', '-T', terrier, 'python', 'manage.py', 'backup', f'/state/backups/accounts-{stamp}.sqlite3'])),
}


def build_argv(config, kind, **params):
    """The docker argv (after the executable) for one allowlisted template; anything else raises NotAllowed."""
    template = TEMPLATES.get(kind)
    if template is None or set(params) != set(template.params):
        raise NotAllowed('command is not on the allowlist')
    return template.build(config, **{name: CHECKS[name](config, value) for name, value in params.items()})


Result = namedtuple('Result', 'code out err')
ENV_KEEP = ('PATH', 'PATHEXT', 'SystemRoot', 'windir', 'ComSpec', 'USERPROFILE', 'HOME', 'HOMEDRIVE', 'HOMEPATH',
            'APPDATA', 'LOCALAPPDATA', 'ProgramData', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432', 'TEMP', 'TMP',
            'USERNAME', 'USERDOMAIN', 'COMPUTERNAME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG',
            'DOCKER_CERT_PATH', 'DOCKER_TLS_VERIFY')


def _text(value):
    return (value or b'').decode('utf-8', 'replace') if isinstance(value, (bytes, type(None))) else value


DOCKER_DESKTOP = (('LOCALAPPDATA', r'Programs\DockerDesktop\resources\bin\docker.exe'),
                  ('ProgramFiles', r'Docker\Docker\resources\bin\docker.exe'))


def find_docker(which=shutil.which, environ=os.environ):
    """Absolute path of the docker CLI, or None. On Windows only an absolute path ending in .exe counts, and the current
    directory is never searched, so a docker.exe dropped beside wherever the dashboard was started is not run."""
    windows = os.name == 'nt'
    if windows:
        os.environ['NoDefaultCurrentDirectoryInExePath'] = '1'   # shutil.which (3.12+) then leaves the current directory out
    found = which('docker')
    if found and os.path.isabs(found) and (not windows or found.lower().endswith('.exe')):
        return found
    if windows:
        for variable, rest in DOCKER_DESKTOP:
            path = os.path.join(environ.get(variable) or '', rest)
            if environ.get(variable) and os.path.isabs(path) and os.path.isfile(path):
                return path
    return None


DESKTOP_APP = (('LOCALAPPDATA', r'Programs\DockerDesktop\Docker Desktop.exe'),   # this user's install first,
               ('ProgramFiles', r'Docker\Docker\Docker Desktop.exe'))            # then the machine-wide one
DOCKER_SERVICE = 'docker-desktop'   # the name the activity log records for starting Docker Desktop
DOCKER_START_WAIT = 180   # seconds one Docker Desktop launch is given to answer before another is accepted (the page waits as long)


def find_desktop_app(environ=os.environ):
    """Absolute path of Docker Desktop's own exe in one of the two fixed install folders, or None. Nothing a request carries
    reaches this."""
    for variable, rest in DESKTOP_APP:
        path = os.path.join(environ.get(variable) or '', rest)
        if environ.get(variable) and os.path.isabs(path) and os.path.isfile(path):
            return path
    return None


def kill_tree(child):
    """End a child and everything it started: compose and docker spawn helpers that keep the output pipes open."""
    try:
        if os.name == 'nt':
            subprocess.run([os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'taskkill.exe'), '/T', '/F', '/PID',
                            str(child.pid)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(child.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        child.kill()
    except Exception:
        pass


def run_process(argv, timeout=None, **kwargs):
    """subprocess.run, except that a timeout kills the whole process tree before the pipes are drained."""
    if os.name != 'nt':
        kwargs.setdefault('start_new_session', True)   # so killpg reaches the grandchildren
    child = subprocess.Popen(argv, **kwargs)
    try:
        out, err = child.communicate(timeout=timeout)
    except BaseException:
        kill_tree(child)
        try:
            child.communicate(timeout=5)
        except Exception:
            pass
        raise
    return subprocess.CompletedProcess(argv, child.returncode, out, err)


class Docker:
    """The only place docker runs. `run`, `launch` and `which` are injectable so tests never start a process."""

    def __init__(self, config, which=shutil.which, run=None, environ=None, launch=None):
        self.config, self._run, self.which, self.launch = config, run or run_process, which, launch or subprocess.Popen
        self.exe = find_docker(which)
        source = {k.lower(): (k, v) for k, v in (os.environ if environ is None else environ).items()}
        self.env = {source[n.lower()][0]: source[n.lower()][1] for n in ENV_KEEP if n.lower() in source}
        self.env.update(COMPOSE_PROFILES=','.join(sorted({s.profile for s in config.services})),
                        COMPOSE_ANSI='never', DOCKER_CLI_HINTS='false')

    def run(self, kind, timeout, merge=False, **params):
        argv = build_argv(self.config, kind, **params)
        self.exe = self.exe or find_docker(self.which)   # Docker Desktop may be installed or started after the dashboard
        if not self.exe:
            raise DockerUnavailable('Docker is not installed on this computer.')
        try:
            done = self._run([self.exe, *argv], shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT if merge else subprocess.PIPE, timeout=timeout, env=self.env,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise DockerUnavailable('Docker took too long to answer.') from None
        except (OSError, subprocess.SubprocessError):
            raise DockerUnavailable('Docker did not answer.') from None
        return Result(done.returncode, _text(done.stdout), _text(done.stderr))

    def start_desktop(self):
        """Start Docker Desktop: the exe find_desktop_app returns, with no shell and no arguments, detached from this process so it
        keeps running when the dashboard ends. It inherits the dashboard's environment, as when it is opened from the Start menu."""
        exe = find_desktop_app()
        if not exe:
            raise NotSetUp('Docker Desktop was not found on this computer.')
        self.launch([exe], shell=False, cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))


# ---- redaction -----------------------------------------------------------------------------------------------------

# Mirrored from ops/security_gate.py (SSN_RE, EMAIL_RE, EMAIL_OK, PHONE_RE); test_apps_admin compares them to its source.
SSN_RE = re.compile(r"(?<![\w-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w-])")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
EMAIL_OK = re.compile(r"(^|\.)(example(\.(com|org|net|test))?|test|invalid|localhost|anthropic\.com|"
                      r"users\.noreply\.github\.com)$", re.I)
PHONE_RE = re.compile(r"(?<![\w.-])(?:\+?1[-. ]?)?(?:\(\d{3}\)\s?|\d{3}[-. ])\d{3}[-. ]\d{4}(?![\w-])")
SCHEME = re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}')
AUTH = re.compile(r'''(?i)(\bauthorization["']?\s*[:=]\s*["']?)(?:((?:token|negotiate|ntlm)\s+)[^\s"',]+|(digest\s+)[^\r\n]*)''')
# Cookie: and Set-Cookie: header lines are redacted pair by pair; PAIR only sees `cookie` when it is quoted or uses `=`.
COOKIE = re.compile(r'''(?i)((?:set-)?cookie[ \t]*:[ \t]*)(?!["'])([^\r\n]+)''')
COOKIE_PLAIN = frozenset(('path', 'domain', 'expires', 'max-age', 'samesite', 'secure', 'httponly', 'partitioned', 'priority'))
# A key name counts as a whole token, after a _ . - separator, or after a camelCase step: `monkey` and `turkey` do not
# match; `api_key`, `x-api-key`, `apiKey` and `apikey` do. The prefix is bounded and can only start at a token start, so
# a long line of separators cannot make the match quadratic.
_QUAL = r'(?:api|access|auth|private|secret|client|refresh|csrf|xsrf|signing|bearer|id|app|master)?'
_KEYS = (r'(?:password|passwd|pwd|secret|token|key|aud|csrf|invite|sessionid|session|credentials?|signature|'
         r'''cookie(?=["']?\s*(?:=|:\s*["'])))''')
_EDGE = r'(?:(?<![\w.-])|(?<=[_.-])|(?-i:(?<=[a-z0-9])(?=[A-Z])))'
PAIR = re.compile(r'''(?i)((?<![\w.-])[\w.-]{0,48}?''' + _EDGE + _QUAL + _KEYS + r'''["']?\s*[:=]\s*)'''
                  r'''(\[redacted\]|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\S+)''')
# scheme://[user]:password@host. The password runs to the last @ of the token, so a / or @ inside it cannot end it early.
URL_AUTH = re.compile(r'(?<![A-Za-z0-9+.-])([A-Za-z][A-Za-z0-9+.-]{0,31}://[^/\s:@]{0,128}:)\S{1,1024}@(?=[^\s@])')
JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*')
CANARY = re.compile(r'MLG-CANARY-[0-9a-f]{32}')
PEM_BEGIN = re.compile(r'-----BEGIN [A-Z0-9 ]{0,40}KEY[A-Z ]{0,20}-----')
PEM_END = re.compile(r'-----END [A-Z0-9 ]{0,40}KEY[A-Z ]{0,20}-----')
PEM = re.compile(PEM_BEGIN.pattern + r'.*?(?:' + PEM_END.pattern + r'|\Z)', re.S)


def _canaries(path=OPS / 'security' / 'canaries.txt'):
    try:
        tokens = [t.strip() for t in path.read_text(encoding='utf-8').splitlines() if t.strip() and not t.startswith('#')]
    except OSError:
        tokens = []
    return re.compile('|'.join(re.escape(t) for t in tokens)) if tokens else None


_CANARY_LIST = _canaries()


def _cookies(match):
    parts = []
    for part in match.group(2).split(';'):
        name, eq, _ = part.partition('=')
        if not part.strip() or name.strip().lower() in COOKIE_PLAIN:
            parts.append(part)
        else:
            parts.append((name + '=' if eq else part[:len(part) - len(part.lstrip())]) + REDACTED)
    return match.group(1) + ';'.join(parts)


def _pair(match):
    value = match.group(2)
    quote = value[0] if len(value) > 1 and value[0] in '"\'' and value[-1] == value[0] else ''
    return match.group(1) + quote + REDACTED + quote


def redact(text):
    """Replace secrets and personal identifiers with [redacted]. Idempotent; runs on the server before logs leave it."""
    text = PEM.sub(REDACTED, text)
    text = SCHEME.sub(lambda m: m.group(1) + ' ' + REDACTED, text)
    text = AUTH.sub(lambda m: m.group(1) + (m.group(2) or m.group(3)) + REDACTED, text)
    text = COOKIE.sub(_cookies, text)
    text = PAIR.sub(_pair, text)
    text = URL_AUTH.sub(lambda m: m.group(1) + REDACTED + '@', text)
    text = JWT.sub(REDACTED, text)
    text = CANARY.sub(REDACTED, text)
    if _CANARY_LIST:
        text = _CANARY_LIST.sub(REDACTED, text)
    text = SSN_RE.sub(REDACTED, text)
    text = PHONE_RE.sub(REDACTED, text)
    return EMAIL_RE.sub(lambda m: m.group(0) if EMAIL_OK.search(m.group(2)) else REDACTED, text)


# ---- parsing -------------------------------------------------------------------------------------------------------

def documents(text):
    """JSON documents of docker output: one document, an array, or one object per line (compose v2.21+ prints NDJSON)."""
    text = text.strip()
    if not text:
        return []
    try:
        value = json.loads(text)
        return value if isinstance(value, list) else [value]
    except ValueError:
        out = []
        for line in text.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out


def parse_size(value):
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None   # docker reports -1 for a size it has not computed
    match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*([kKMGTP]?)(i?)B?\s*', value) if isinstance(value, str) else None
    if not match:
        return None
    base, unit = (1024 if match.group(3) else 1000), match.group(2).upper()
    return round(float(match.group(1)) * base ** ('KMGTP'.index(unit) + 1 if unit else 0))


def volume_sizes(text):
    """{volume name: bytes} from `system df -v --format json` (an object with Volumes, or one object per volume)."""
    sizes = {}
    for doc in documents(text):
        for row in (doc.get('Volumes') if isinstance(doc, dict) and 'Volumes' in doc else [doc]) or []:
            if isinstance(row, dict) and isinstance(row.get('Name'), str):
                size = parse_size(row.get('Size', (row.get('UsageData') or {}).get('Size')))
                if size is not None:
                    sizes[row['Name']] = size
    return sizes


def parse_time(text):
    match = re.match(r'(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)', text or '')
    if not match or match.group(1) == '0001':
        return None
    return int(datetime(*map(int, match.groups()), tzinfo=timezone.utc).timestamp())


def label_version(labels):
    """'v<version>' from the image's version label, else 'build <7 characters of the revision>', else None."""
    labels = labels or {}
    version = str(labels.get(VERSION_LABEL) or '').strip()
    if version:
        return version if version.startswith('v') else 'v' + version
    revision = str(labels.get(REVISION_LABEL) or '').strip().lower()
    return 'build ' + revision[:7] if re.fullmatch(r'[0-9a-f]{7,64}', revision) else None


def same_image(pinned, ref):
    """Two digest-pinned references are the same image when their digests match, however the repository is spelled."""
    if '@sha256:' in pinned and '@sha256:' in ref:
        return pinned.partition('@')[2] == ref.partition('@')[2]
    return pinned == ref


LOG_LINE = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z ?(.*)', re.S)
LEVEL_HEAD = re.compile(r'(?:\[?(INFO|DEBUG|WARN(?:ING)?|ERROR|CRITICAL|FATAL)\]?[:\s]+)', re.I)


def parse_logs(text):
    rows = []
    for raw in text.splitlines()[-500:]:
        match = LOG_LINE.fullmatch(raw[:4000])
        rows.append((parse_time(match.group(1) + 'Z'), match.group(2)) if match else (None, raw[:4000]))
    # A key block spans lines (redact only sees one), and the 500-line window can start inside one.
    inside = False
    for _, body in rows:
        begin, end = PEM_BEGIN.search(body), PEM_END.search(body)
        if begin or end:
            inside = bool(end) and (not begin or end.start() < begin.start())
            break
    lines = []
    for stamp, body in rows:
        begin, end = PEM_BEGIN.search(body), PEM_END.search(body)
        if inside:
            body, inside = REDACTED, not end
        else:
            inside = bool(begin) and not PEM_END.search(body, begin.end())
            body = redact(body)
        found = LEVEL_HEAD.match(body)
        head = found.group(1).upper() if found else ''
        level = 'ERROR' if re.search(r'\b(ERROR|CRITICAL|FATAL)\b|Traceback', body) else \
                'WARN' if re.search(r'\b(WARN|WARNING)\b', body) else (head or 'INFO')
        level = {'WARNING': 'WARN', 'CRITICAL': 'ERROR', 'FATAL': 'ERROR'}.get(level, level)
        lines.append({'ts': stamp, 'level': level, 'text': body[found.end():] if found else body})
    return lines


def fetch_loopback_version(port):
    """The app's /version over loopback only; None on any problem."""
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f'http://127.0.0.1:{int(port)}/version', timeout=2) as response:
            value = json.loads(response.read(4096).decode('utf-8'))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def tail(result):
    """A short, redacted reason from a failed command."""
    lines = [l.strip() for l in (result.err or result.out).splitlines() if l.strip()]
    lines = [l.removeprefix('CommandError: ') for l, _ in itertools.groupby(lines)]   # a repeated line says nothing new
    text = redact(' '.join(lines[-2:]))
    if len(text) > 300:
        text = text[:299].rsplit(' ', 1)[0].rstrip(' .,;:') + '…'
    return text or f'docker exited with status {result.code}'


def shape(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise BadRequest('unexpected fields')


def published_port(obj):
    """The host port a container publishes, from `docker inspect` NetworkSettings.Ports ({'18080/tcp': [{'HostIp', 'HostPort'}]}):
    the first binding on a loopback or wildcard address. None for a container that publishes nothing (the apps share their
    relay's network, so only the relay does)."""
    ports = ((obj or {}).get('NetworkSettings') or {}).get('Ports')
    for key in sorted(ports) if isinstance(ports, dict) else ():
        for bind in ports[key] if isinstance(ports[key], list) else ():
            host = str(bind.get('HostPort') or '') if isinstance(bind, dict) else ''
            if isinstance(bind, dict) and (bind.get('HostIp') or '') in LOCAL_HOSTS and host.isdigit() and 0 < int(host) < 65536:
                return int(host)
    return None


def stamp_of(path):
    """(path, mtime_ns, size) of a file, None when it is not there: what the config cache is keyed on."""
    try:
        found = os.stat(path)
    except OSError:
        return None
    return (str(path), found.st_mtime_ns, found.st_size)


class Deadline:
    """The time one read of Docker may take in all, so that calls made one after another cannot add up past it."""

    def __init__(self, seconds, now=time.monotonic):
        self.now, self.end = now, now() + seconds

    def left(self):
        return self.end - self.now()

    def timeout(self, cap):
        """The timeout for the next docker call: its own cap, or what is left when that is less."""
        left = self.left()
        if left < MIN_CALL:
            raise DockerUnavailable('Docker took too long to answer.')
        return min(cap, left)


# ---- the Apps section ----------------------------------------------------------------------------------------------

class UnavailableApps:
    """Stands in for AppsAdmin when its settings are invalid: every Apps route answers not_set_up and nothing runs."""

    def __init__(self, reason):
        self.reason = reason

    def handle(self, path, value):
        raise NotSetUp(self.reason)

    def shutdown(self):
        pass


class AppsAdmin:
    """audit needs audit_app_action(actor, service, action, outcome), app_actions(limit) and last_app_action(service, action,
    outcome): AccessStore has all three."""

    def __init__(self, config=None, audit=None, docker=None, clock=time.time, spawn=None, fetch_version=None, monotonic=time.monotonic):
        self.config = config or default_config()
        self.docker = docker or Docker(self.config)
        self.audit, self.clock, self.monotonic = audit, clock, monotonic
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True, name='apps-action').start())
        self.fetch_version = fetch_version or fetch_loopback_version
        self.by_name = {s.name: s for s in self.config.services}
        self.lock = threading.Lock()          # busy, last, cache, good, reading, config_cache
        self.busy, self.last, self.cache, self.df_running = {}, {}, {}, False
        self.reading = None                   # the read of the service list that is running, if any (one at a time)
        self.good = None                      # the last list read without error: {'at', 'infos', 'notice'}
        self.failed = None                    # the DockerUnavailable the latest finished read ended with, or None when it succeeded
        self.config_cache = None              # (watched paths, their stamps, {service: pinned image}) of the last good `compose config`
        self.logs_running = set()             # services whose logs are being read right now
        self.docker_started = None            # monotonic time of the Docker Desktop launch that Docker has not answered since

    # routing
    def handle(self, path, value):
        if path == '/apps':
            shape(value, ())
            return self.overview()
        if path == '/apps/invite':
            shape(value, ('email', 'role'))
            return self.invite(value['email'], value['role'])
        if path == '/apps/docker-start':
            shape(value, ())
            return self.start_docker()
        shape(value, ('service', 'action') if path == '/apps/action' else ('service',))
        service = self.service(value['service'])
        if path == '/apps/detail':
            return self.detail(service)
        if path == '/apps/logs':
            return self.logs(service)
        if path == '/apps/action':
            return self.act(service, value['action'])
        raise BadRequest('unknown route')

    def service(self, name):
        if not isinstance(name, str) or name not in self.by_name:
            raise BadRequest('unknown service')
        return self.by_name[name]

    # reading docker
    def read(self, kind, timeout, **params):
        result = self.docker.run(kind, timeout, **params)
        if result.code != 0:
            raise self.failure(result)
        return result.out

    @staticmethod
    def failure(result):
        daemon = re.search(r'daemon|pipe|connect', result.err, re.I)
        error = DockerUnavailable('Docker is not running. Start Docker Desktop, then check again.' if daemon
                                  else 'Docker could not read the local services.')
        error.down = bool(daemon)
        return error

    def inspect(self, ids, timeout=20):
        """`docker inspect` exits 1 when one container vanished since `ps` but still prints the others: use that list."""
        result = self.docker.run('inspect', timeout, ids=ids)
        if result.code == 0:
            return [o for o in documents(result.out) if isinstance(o, dict)]
        try:
            found = json.loads(result.out)
        except ValueError:
            found = None
        if isinstance(found, list) and (found or not re.search(r'daemon|pipe|connect', result.err, re.I)):
            return [o for o in found if isinstance(o, dict)]
        raise self.failure(result)

    def containers(self, deadline=None):
        """{compose service: its `docker inspect` document}: every container of the project, relays included. A container
        started by `compose run` is not a service container; one that carries another project's label is not ours."""
        deadline = deadline or Deadline(REFRESH_BUDGET, self.monotonic)
        listed = self.read('ps', deadline.timeout(CALL_CAP['ps']))
        ids = [line for line in (l.strip() for l in listed.splitlines()) if HEX_ID.fullmatch(line)][:16]   # 16 is the allowlist's cap
        found = {}
        for obj in self.inspect(ids, deadline.timeout(CALL_CAP['inspect'])) if ids else []:
            labels = (obj.get('Config') or {}).get('Labels') or {}
            if labels.get(PROJECT_LABEL) == self.config.project and labels.get(ONEOFF_LABEL) != 'True':
                found.setdefault(labels.get(SERVICE_LABEL), obj)
        return found

    def kept(self):
        with self.lock:
            return self.good

    def snapshot(self):
        """(list, reason): the services as Docker reports them now, reason None; or, when a read is already running or Docker
        times out or fails, the last good list and why it is old. Raises DockerUnavailable only when there is no list to show.
        The read runs in its own thread and the caller waits for it REFRESH_BUDGET at most, so a docker call that is slow to be
        killed cannot hold the page past its 12 s: the read goes on in the background and the next caller finds its result. A
        caller never waits behind a running read when it has a list to answer with."""
        end = time.monotonic() + REFRESH_BUDGET   # the wall clock, whatever clock the read itself is given
        with self.lock:
            job = self.reading
            mine = job is None
            if mine:
                job = self.reading = ReadJob()
        if mine:
            try:
                threading.Thread(target=self.run_read, args=(job,), daemon=True, name='apps-read').start()
            except Exception:   # no thread could start: read here, in this request
                self.run_read(job)
        elif (kept := self.kept()):
            return kept, 'Another check is still running.'
        # (Otherwise nothing to show yet: wait, inside the same budget, for the read that is running to give the first list.)
        job.done.wait(max(0, end - time.monotonic()))
        if job.done.is_set() and job.error is None:
            return job.result, None
        if job.done.is_set() and not isinstance(job.error, DockerUnavailable):
            raise job.error
        reason = job.error.message if job.done.is_set() else 'Docker took too long to answer.'
        kept = self.kept()
        if kept:
            return kept, reason
        raise job.error if job.done.is_set() else DockerUnavailable(reason)   # the read's own error keeps its `down`

    def run_read(self, job):
        try:
            job.result = self.read_services(Deadline(REFRESH_BUDGET, self.monotonic))
        except BaseException as error:
            job.error = error
        finally:
            with self.lock:
                self.reading = None
                self.failed = job.error if isinstance(job.error, DockerUnavailable) else None
                if job.error is None:
                    self.docker_started = None   # Docker answered: a later start is a new one
            job.done.set()

    def read_services(self, deadline):
        """One docker read pass: {'at', 'infos': {service: info or None}, 'notice'}. Raises DockerUnavailable."""
        at = int(self.clock())
        if not Path(self.config.compose_file).is_file():
            return {'at': at, 'infos': {s.name: None for s in self.config.services}, 'notice': 'The local Docker setup was not found on this computer.'}
        config = {}

        def read_config():
            try:
                config['pinned'], config['notice'] = self.pinned_images(deadline)
            except Exception:   # a bug here must not cost the list: the notice below says updates cannot be detected
                pass
        reader = threading.Thread(target=read_config, daemon=True, name='apps-config')
        reader.start()   # beside ps and inspect, so a cold `compose config` (slow on a busy machine) does not add to them
        found = self.containers(deadline)   # if this fails the config thread is left to end by its own timeout
        reader.join(max(0, deadline.left()) + 6)   # it ends by its own timeout; the margin covers killing the process tree
        pinned, notice = config.get('pinned', {}), config.get('notice', PINNED_NOTICE)
        sizes = self.volume_sizes()
        infos = {}
        for svc in self.config.services:
            obj = found.get(svc.name)
            infos[svc.name] = self.info(svc, obj, found.get(svc.relay), pinned.get(svc.name), sizes) if obj else None
        for info in infos.values():
            if info:   # looked up here, inside the budget, so that showing the list later never calls docker or a service
                info['version'] = self.version(info, deadline)
                info['readyVersion'] = self.ready_version(info, deadline) if info['update'] else None
        good = {'at': at, 'infos': infos, 'notice': notice}
        with self.lock:
            self.good = good
        return good

    def pinned_images(self, deadline):
        """({service: image the compose file pins}, notice). The parsed `compose config` is reused until the compose file, the
        env file beside it (ops/.env) or an env_file it lists changes; a failure is never kept."""
        with self.lock:
            entry = self.config_cache
        if entry and tuple(stamp_of(path) for path in entry[0]) == entry[1]:
            return dict(entry[2]), None
        compose = Path(self.config.compose_file)
        base = (str(compose), str(compose.parent / '.env'))
        before = tuple(stamp_of(path) for path in base)
        try:
            parsed = (documents(self.read('config', deadline.timeout(CALL_CAP['config']))) or [{}])[0]
            services = parsed.get('services', {})
            pinned = {name: service.get('image') for name, service in services.items()}
        except (DockerUnavailable, AttributeError, IndexError, TypeError):
            return {}, PINNED_NOTICE
        paths = base + tuple(sorted(self.env_files(services)))
        stamps = tuple(stamp_of(path) for path in paths)
        if stamps[:2] == before:   # the files did not change while compose read them
            with self.lock:
                self.config_cache = (paths, stamps, pinned)
        return dict(pinned), None

    def env_files(self, services):
        """The env_file paths of a parsed `compose config` (a string, a list of strings, or {'path', 'required'} entries)."""
        folder, found = Path(self.config.compose_file).parent, set()
        for service in services.values() if isinstance(services, dict) else ():
            entries = service.get('env_file') if isinstance(service, dict) else None
            for entry in [entries] if isinstance(entries, (str, dict)) else entries if isinstance(entries, list) else []:
                path = entry.get('path') if isinstance(entry, dict) else entry
                if isinstance(path, str) and path:
                    found.add(str(folder / path))   # an absolute path stays as it is
        return found

    def volume_sizes(self):
        """The last `system df -v` result. It is slow, so refresh it in the background and never wait for it."""
        with self.lock:
            stamp, sizes = self.cache.get('df', (0, None))
            refresh = self.clock() - stamp >= DF_TTL and not self.df_running
            self.df_running = self.df_running or refresh

        def update():
            try:
                result = self.docker.run('df', 120)
                found = volume_sizes(result.out) if result.code == 0 else None
            except AppsError:
                found = None
            with self.lock:
                self.cache['df'] = (self.clock(), found)
                self.df_running = False
        if refresh:
            try:
                self.spawn(update)
            except Exception:   # no thread could start: let the next poll try again instead of waiting for it forever
                with self.lock:
                    self.df_running = False
            with self.lock:
                stamp, sizes = self.cache.get('df', (0, None))
        return sizes

    def info(self, svc, obj, relay, pinned, sizes):
        state, config = obj.get('State') or {}, obj.get('Config') or {}
        status, health = state.get('Status'), (state.get('Health') or {}).get('Status')
        port = published_port(relay) or published_port(obj)   # the relay publishes it; a service that publishes its own works too
        note = None
        if status == 'running':
            kind, note = ('unhealthy', 'health check failing') if health == 'unhealthy' else ('running', 'starting up' if health == 'starting' else None)
        elif status == 'restarting':
            kind, note = 'unhealthy', 'restarting'
        else:
            kind = 'stopped'
        ref = config.get('Image')
        update = isinstance(pinned, str) and isinstance(ref, str) and bool(pinned and ref) and not same_image(pinned, ref)
        volumes = [m for m in obj.get('Mounts') or [] if m.get('Type') == 'volume']
        return {'kind': kind, 'note': note, 'health': health, 'id': obj.get('Id'), 'port': port, 'ref': ref, 'pinned': pinned, 'update': update,
                'started': parse_time(state.get('StartedAt')), 'finished': parse_time(state.get('FinishedAt')),
                'exitCode': state.get('ExitCode') if kind == 'stopped' else None,
                'healthCheckedAt': parse_time(((state.get('Health') or {}).get('Log') or [{}])[-1].get('End')),
                'label': label_version(config.get('Labels')),
                'mounts': [{'type': m.get('Type'), 'destination': m.get('Destination'), 'readOnly': not m.get('RW', True),
                            'bytes': (sizes or {}).get(m.get('Name')) if m.get('Type') == 'volume' else None} for m in obj.get('Mounts') or []],
                'disk': sum(sizes[m.get('Name')] for m in volumes) if volumes and sizes is not None and all(m.get('Name') in sizes for m in volumes) else None}

    def version(self, info, deadline):
        if info['label']:
            return info['label']
        key = ('version', info['id'])
        with self.lock:
            expires, text = self.cache.get(key, (0, None))
            if expires > self.clock():
                return text
        if info['kind'] == 'stopped' or not info['port'] or deadline.left() < 2.5:   # the probe waits 2 s at most; not asked again if out of time
            return None
        sha = str((self.fetch_version(info['port']) or {}).get('git_sha') or '')
        text = 'build ' + sha[:7] if sha and sha != 'unknown' else None
        with self.lock:   # a version never changes for one container id; a failed probe waits VERSION_RETRY before the next
            self.cache[key] = (float('inf') if text else self.clock() + VERSION_RETRY, text)
        return text

    def ready_version(self, info, deadline):
        """Version label of the pinned image, when that image is already on this computer."""
        ref = info['pinned']
        try:
            if not info['update'] or not IMAGE_REF.fullmatch(ref):
                return None
            with self.lock:
                if ('ready', ref) in self.cache:
                    return self.cache[('ready', ref)]
            result = self.docker.run('image_inspect', deadline.timeout(CALL_CAP['image_inspect']), ref=ref)
            text = label_version(((documents(result.out) or [{}])[0].get('Config') or {}).get('Labels')) if result.code == 0 else None
        except (AppsError, AttributeError, TypeError, ValueError):
            return None
        if text:
            with self.lock:
                self.cache[('ready', ref)] = text
        return text

    def recent(self, limit):
        try:
            return self.audit.app_actions(limit) if self.audit else []
        except Exception:
            return []

    def last_backup(self, svc):
        """When the newest succeeded backup was written: a direct query, so it survives any number of later actions."""
        try:
            return self.audit.last_app_action(svc.name, 'backup', 'succeeded') if svc.terrier and self.audit else None
        except Exception:
            return None

    def row(self, svc, info):
        with self.lock:
            busy, last = self.busy.get(svc.name), self.last.get(svc.name)
        row = {'service': svc.name, 'name': svc.display, 'state': 'missing', 'terrier': svc.terrier,
               'backup': 'supported' if svc.terrier else 'not_set_up', 'lastBackup': self.last_backup(svc),
               'busy': busy, 'last': last, 'actions': []}
        if info is None:
            return row
        update = info['update']   # a stopped service shows Stopped and Update ready both; only Update (never Start) moves it
        row.update(state='update_ready' if update and info['kind'] != 'stopped' else info['kind'], note=info['note'],
                   since=info['finished'] if info['kind'] == 'stopped' else info['started'],
                   exitCode=info['exitCode'], update=update, version=info['version'], readyVersion=info['readyVersion'],
                   disk=info['disk'], openUrl=f"http://127.0.0.1:{info['port']}" if info['port'] else None)
        up = info['kind'] in ('running', 'unhealthy')
        if not busy:
            row['actions'] = [a for a, ok in (('update', update), ('start', info['kind'] == 'stopped'), ('open', up and bool(info['port'])),
                                              ('restart', up), ('logs', info['kind'] != 'running')) if ok]
        return row

    def overview(self):
        """The Apps list. `stale` is true when it is the last good list (a read is running, or Docker timed out or failed): its
        checkedAt says when that was read and staleReason why a new read did not answer. Busy, last and activity are always current."""
        now = int(self.clock())
        names = {**{name: svc.display for name, svc in self.by_name.items()}, DOCKER_SERVICE: 'Docker Desktop'}
        activity = [{**a, 'name': names[a['service']]} for a in self.recent(8) if a['service'] in names]
        try:
            good, reason = self.snapshot()
        except DockerUnavailable as error:
            # Docker cannot be read and no list was kept, but a running action is still ours: keep reporting busy/last so the page keeps polling.
            rows = [{**self.row(s, None), 'state': 'unknown'} for s in self.config.services]
            return {'checkedAt': now, 'docker': {'ok': False, 'message': error.message, 'canStart': error.down and bool(find_desktop_app())}, 'notice': None, 'stale': False, 'services': rows, 'activity': activity}
        rows = [self.row(s, good['infos'][s.name]) for s in self.config.services]
        failed = self.failed   # read once, as in act()
        failed = failed if reason and failed and failed.down else None   # Docker went away under the list we kept: say so, and offer to start it
        docker = {'ok': True, 'message': failed.message if failed else None, 'canStart': bool(failed and find_desktop_app())}
        view = {'checkedAt': good['at'], 'docker': docker, 'notice': good['notice'], 'stale': bool(reason), 'services': rows, 'activity': activity}
        return {**view, 'staleReason': reason} if reason else view

    def detail(self, svc):
        now = int(self.clock())
        good, reason = self.snapshot()
        info = good['infos'][svc.name]
        result = {'checkedAt': good['at'], 'notice': good['notice'], 'stale': bool(reason), 'service': self.row(svc, info),
                  'accounts': {'supported': svc.terrier, 'listing': False, 'roles': list(ROLES) if svc.terrier else []}}
        if reason:
            result['staleReason'] = reason
        if info:
            result.update(health={'status': {'healthy': 'passing', 'starting': 'starting', 'unhealthy': 'failing'}.get(info['health'], 'none'),
                                  'checkedAt': info['healthCheckedAt']},
                          started=info['started'], uptime=now - info['started'] if info['kind'] != 'stopped' and info['started'] else None,
                          image={'update': info['update'], 'pinnedByDigest': '@sha256:' in str(info['pinned']),
                                 'version': result['service'].get('version'), 'readyVersion': result['service'].get('readyVersion')},
                          storage=info['mounts'])
        return result

    def logs(self, svc):
        with self.lock:   # one read per service at a time: a follow poll that outlives its request must not pile up docker calls
            if svc.name in self.logs_running:
                raise Busy('The logs are still being read.')
            self.logs_running.add(svc.name)
        try:
            kept = self.kept()   # the id the page's list showed: no `docker ps` here, which can outlast the 12 s the page waits
            if kept:
                found = kept['infos'].get(svc.name)
                container = found and found['id']
            else:
                found = self.containers().get(svc.name)
                container = found and found.get('Id')
            if not container or not HEX_ID.fullmatch(str(container)):
                raise NotSetUp('Not set up in Docker')
            result = self.docker.run('logs', 20, merge=True, container=container)
            if result.code != 0:
                raise ActionFailed('Docker could not read the logs.')
            return {'service': svc.name, 'lines': parse_logs(result.out)}
        finally:
            with self.lock:
                self.logs_running.discard(svc.name)

    # actions
    def act(self, svc, action):
        if action not in ACTIONS:
            raise BadRequest('unknown action')
        if action == 'backup' and not svc.terrier:
            raise BadRequest('backups are not set up for this service')
        failed = self.failed   # read once: a read finishing on another thread may replace it between the test and the raise
        if failed and failed.down:   # the latest read found the daemon not running: say so now, rather than accept an action that will fail
            raise failed.with_traceback(None)
        kept = self.kept()   # the list that offered the button: no docker read here, which can outlast the 12 s the page waits
        if kept and kept['infos'].get(svc.name) is None:   # with no list yet the action runs and reports its own error
            raise NotSetUp('Not set up in Docker')
        with self.lock:
            if svc.name in self.busy:
                raise Busy('another action is already running for this service')
            self.busy[svc.name] = {'action': action, 'since': int(self.clock())}
        try:
            self.spawn(lambda: self.run_action(svc, action))
        except Exception:   # the thread never started, so nothing would ever clear busy
            with self.lock:
                self.busy.pop(svc.name, None)
            raise ActionFailed('The action could not start. Try again.') from None
        return {'ok': True, 'accepted': True}

    def start_docker(self):
        """Start Docker Desktop. Nothing in the request chooses what runs; the page then polls until Docker answers, and the
        containers (restart: unless-stopped) come back on their own. One launch at a time: until Docker answers a read, or for
        DOCKER_START_WAIT, another start (a second click, another tab) is refused Busy and not recorded."""
        with self.lock:
            started = self.docker_started
            if started is not None and self.monotonic() - started < DOCKER_START_WAIT:
                raise Busy('Docker Desktop is already starting. This can take a minute or two.')
            self.docker_started = self.monotonic()
        outcome = None
        try:
            self.docker.start_desktop()   # NotSetUp passes through: nothing was started, so nothing is recorded
            outcome = 'succeeded'
        except (OSError, subprocess.SubprocessError):
            outcome = 'failed'
        finally:
            if outcome != 'succeeded':   # nothing is starting, so the next click may try again at once
                with self.lock:
                    self.docker_started = None
        try:
            self.audit.audit_app_action(self.config.actor, DOCKER_SERVICE, 'start', outcome)
        except Exception:   # the launch already happened; the response says so whatever the log could do
            pass
        if outcome != 'succeeded':
            raise ActionFailed('Docker Desktop could not be started.')
        return {'ok': True, 'accepted': True}

    def steps(self, svc, action):
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        return {'start': [('start', 300, {'service': svc.name})],
                'restart': [('restart', 180, {'service': svc.name})],
                'update': [('pull', 900, {'service': svc.name}), ('up', 300, {'service': svc.name})],
                'backup': [('backup', 120, {'terrier': svc.name, 'stamp': stamp})]}[action]

    def run_action(self, svc, action):
        outcome, message = 'failed', None
        try:
            for kind, timeout, params in self.steps(svc, action):
                result = self.docker.run(kind, timeout, **params)
                if result.code != 0:
                    raise ActionFailed(tail(result))
            outcome = 'succeeded'
        except AppsError as error:
            message = error.message
        except Exception:
            message = 'The action stopped unexpectedly.'
        finally:
            self.finish(svc, action, outcome, message)

    def shutdown(self):
        """The dashboard is closing: the activity log gets an `interrupted` entry for each action still running."""
        with self.lock:
            pending, self.busy = self.busy, {}
        for name, job in pending.items():
            try:
                self.audit.audit_app_action(self.config.actor, name, job['action'], 'interrupted')
            except Exception:
                pass

    def finish(self, svc, action, outcome, message):
        """The one activity-log entry for an action, then release the service."""
        with self.lock:
            if svc.name not in self.busy:   # shutdown already recorded this one as interrupted
                return
        try:
            self.audit.audit_app_action(self.config.actor, svc.name, action, outcome)
        except Exception:
            message = (message or '') + ' The activity log could not be written.'
        with self.lock:
            if action != 'invite':  # the invite caller gets its own response
                self.last[svc.name] = {'action': action, 'outcome': outcome, 'at': int(self.clock()), 'message': message}
            self.busy.pop(svc.name, None)

    def invite(self, email, role):
        if not isinstance(email, str) or len(email) > 150 or not EMAIL.fullmatch(email) or role not in ROLES:
            raise BadRequest('a valid email and role are required')
        svc = next((s for s in self.config.services if s.terrier), None)
        if svc is None:
            raise NotSetUp('Not set up in Docker')
        found = self.containers()
        port = published_port(found.get(svc.relay))
        if svc.name not in found or (found[svc.name].get('State') or {}).get('Status') != 'running' or not port:
            raise NotSetUp('Terrier is not running')
        with self.lock:
            if svc.name in self.busy:
                raise Busy('another action is already running for this service')
            self.busy[svc.name] = {'action': 'invite', 'since': int(self.clock())}
        origin, outcome, message, link = f'http://127.0.0.1:{port}', 'failed', 'Terrier did not return an invitation link.', None
        try:
            result = self.docker.run('invite', INVITE_TIMEOUT, terrier=svc.name, email=email, role=role, origin=origin)
            last = [l.strip() for l in result.out.splitlines() if l.strip()][-1:]
            if result.code != 0:
                message = tail(result)
            elif last and last[0].startswith(origin + '/'):
                outcome, link = 'succeeded', last[0]
        except AppsError as error:
            message = error.message
        finally:
            self.finish(svc, 'invite', outcome, None if outcome == 'succeeded' else message)
        if not link:
            raise ActionFailed(message)
        return {'ok': True, 'link': link, 'expiresInHours': 24}
