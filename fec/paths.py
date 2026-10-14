# -*- coding: utf-8 -*-
"""Single source of the project root (architecture review 2026-07-02).
Override with the MONEYBALL_ROOT environment variable — every script resolves
its paths from here, so relocating the project (or escaping OneDrive) is a
one-variable change instead of a 95-file edit."""
import os
import sys
_HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.abspath(os.environ.get('MONEYBALL_ROOT',os.path.join(_HERE,os.pardir)))
# 2026-08-04 Phase 2: Root data artifacts are moving into data\<domain>\.
# Call sites currently use relocated literals; accessor consolidation is the
# dedicated next pass. Keep at root: deployed surfaces, the workbook family,
# databases, and ranks_python.json (rank authority stays at root).
DATA = os.path.join(ROOT,'data')
DRAFT=os.path.abspath(os.environ.get('MONEYBALL_DRAFT',os.path.join(ROOT,os.pardir,'Draft Systems')))   # R press-pipeline root
FEC=os.path.join(ROOT,'fec')
WORKBOOK=os.path.join(ROOT,'Moneyball Main Version - Blended.xlsx')
BACKUPS=os.path.join(ROOT,'backups')

# Staffer-network restricted storage is deliberately outside the repository and
# OneDrive tree.  The repository receives only schema-validated, public-safe
# generation artifacts under DATA_STAFFER_NETWORK.  A caller may override the
# machine-local root for tests or a managed encrypted volume.
_LOCAL_APPDATA = os.path.abspath(
    os.environ.get('LOCALAPPDATA', os.path.join(os.path.expanduser('~'), 'AppData', 'Local'))
)
# Compute-container seams (D-173/D-177, 2026-10-12).  Every default below is today's Windows value, so
# nothing moves on the laptop; the container (ops/compute/melange.compose.yaml) sets the env vars and
# every chain step resolves the same way.
#   LOCAL_MELANGE  %LOCALAPPDATA%\Melange (lda_rebuild, staffer_network, demographics)  MELANGE_LOCAL_ROOT
#   MELANGE_DATA   C:\melange-data (audio and clip stores; read only by the chain)       MELANGE_DATA_ROOT
class LocalRootUnset(RuntimeError):
    """Neither MELANGE_LOCAL_ROOT nor LOCALAPPDATA names the machine-local Melange store."""


class _UnsetLocalRoot(os.PathLike):
    """LOCAL_MELANGE when nothing names it.  champions_v2, contest_screen, org_mentions, org_stance_priors, entity_identity,
    sector_spend and both make_lobby_*_html raised when LOCALAPPDATA was unset, at the call that needed it and not at import; so
    this value exists (importing paths, or a module that imports the name, never fails) and any USE of it -- os.fspath (hence
    os.path.join, Path, open), str, format, + and / -- raises LocalRootUnset.  No path is ever built from a guessed home directory."""
    _MESSAGE = ('LOCAL_MELANGE is unset: neither MELANGE_LOCAL_ROOT nor LOCALAPPDATA is set, so the machine-local Melange store '
                '(lda_rebuild, staffer_network, demographics) cannot be located; set MELANGE_LOCAL_ROOT')

    def _raise(self, *args, **kwargs):
        raise LocalRootUnset(self._MESSAGE)

    __fspath__ = __str__ = __format__ = __add__ = __radd__ = __truediv__ = __rtruediv__ = _raise

    def __getattr__(self, name):   # only reached for attributes the class lacks (.startswith, .replace, ...)
        if name.startswith('__'):
            raise AttributeError(name)
        raise LocalRootUnset(self._MESSAGE)

    def __repr__(self):
        return '<LOCAL_MELANGE unset>'


_LOCAL_MELANGE_PATH = os.path.abspath(os.environ.get('MELANGE_LOCAL_ROOT') or os.path.join(_LOCAL_APPDATA, 'Melange'))
LOCAL_MELANGE = (_LOCAL_MELANGE_PATH if os.environ.get('MELANGE_LOCAL_ROOT') or os.environ.get('LOCALAPPDATA')
                 else _UnsetLocalRoot())
MELANGE_DATA = os.path.abspath(os.environ['MELANGE_DATA_ROOT']) if os.environ.get('MELANGE_DATA_ROOT') else 'C:\\melange-data'
# STAFFER_NETWORK_LOCAL keeps the fallback it had before the seams (the home directory's AppData\Local when LOCALAPPDATA is unset);
# only LOCAL_MELANGE fails closed.
STAFFER_NETWORK_LOCAL = os.path.abspath(
    os.environ.get(
        'STAFFER_NETWORK_LOCAL',
        os.path.join(_LOCAL_MELANGE_PATH, 'staffer_network'),
    )
)
STAFFER_NETWORK_RAW = os.path.join(STAFFER_NETWORK_LOCAL, 'raw')
STAFFER_NETWORK_NORMALIZED = os.path.join(STAFFER_NETWORK_LOCAL, 'normalized')
STAFFER_NETWORK_CORPUS = os.path.join(STAFFER_NETWORK_NORMALIZED, 'source-native-corpus')
STAFFER_NETWORK_LEDGER = os.path.join(STAFFER_NETWORK_LOCAL, 'ledger', 'staffer_ledger.db')
STAFFER_NETWORK_ADJUDICATION = os.path.join(STAFFER_NETWORK_LOCAL, 'adjudication')
DATA_STAFFER_NETWORK = os.path.join(DATA, 'staffer_network')

_IS_WINDOWS = os.name == 'nt'   # one switch so tests can exercise the POSIX branch on Windows

def p(*parts): return os.path.join(ROOT,*parts)

_TODAY_WARNED = False   # one MELANGE_TODAY warning per process

def today():
    """datetime.date.today(), or the ISO date in MELANGE_TODAY: a parity run on two platforms must see one
    calendar day (build_media_blend decays by age, so two days apart differ for a reason that is not the
    container's).  Empty or unset means the real date.  An override is loud: stderr gets one warning per process,
    because a pinned date left in the environment of a production run would silently age every decay by the pin."""
    import datetime
    global _TODAY_WARNED
    stamp = os.environ.get('MELANGE_TODAY')
    if not stamp:
        return datetime.date.today()
    pinned = datetime.date.fromisoformat(stamp)
    if not _TODAY_WARNED:
        _TODAY_WARNED = True
        sys.stderr.write('*** WARNING: MELANGE_TODAY=%s -- today() returns %s, NOT the calendar date (%s). Parity runs only: '
                         'unset MELANGE_TODAY for production. ***\n' % (stamp, pinned.isoformat(), datetime.date.today().isoformat()))
        sys.stderr.flush()
    return pinned

def stored_name(path):
    """Last component of a path stored inside data, whichever separator it was written with.  On Windows this is
    Path(path).name; PureWindowsPath reads both separators, so a Windows path stored in a manifest names the same
    file on a POSIX host, where Path('C:\a\b.json').name is the whole string."""
    from pathlib import PureWindowsPath
    return PureWindowsPath(os.fspath(path)).name

# Stored Windows roots -> the name in this module that holds this host's spelling of each.
_STORED = (
    ('C:/Users/pgoff/OneDrive/Desktop/Melange Systems', 'ROOT'),
    ('C:/Users/pgoff/OneDrive/Desktop/Draft Systems', 'DRAFT'),
    ('C:/Users/pgoff/AppData/Local/Melange', 'LOCAL_MELANGE'),
    ('C:/melange-data', 'MELANGE_DATA'),
)

def _rebasing():
    """POSIX always rebases; Windows only when MELANGE_REBASE_STORED=1 (a relocated scratch root).  Opt-in there because
    shadow tools and kernel temp roots set MONEYBALL_ROOT legitimately and must keep reading the paths they were given."""
    return not _IS_WINDOWS or os.environ.get('MELANGE_REBASE_STORED') == '1'

def _rebase():
    """(stored prefix, this host's root) for each stored root that has MOVED.  An unmoved root is skipped, so a path under
    it comes back as the text it came in as (no separator rewrite on the laptop)."""
    for prefix, name in _STORED:
        root = globals()[name]
        if not isinstance(root, _UnsetLocalRoot):
            root = os.fspath(root)
            if root.replace('\\', '/').rstrip('/').lower() != prefix.lower():
                yield prefix, root

def _under(flat, prefix):
    low, pre = flat.lower(), prefix.lower()
    return low == pre or low.startswith(pre + '/')

def to_host(path):
    """A Windows absolute path stored INSIDE data (a clip manifest's out_wav, a receipt) -> this host's path.
    Identity on Windows (unless MELANGE_REBASE_STORED=1) and for anything that is not under a known stored Windows root,
    so a native path is never mangled.  Stored roots: the project tree, Draft Systems, C:\\melange-data, %LOCALAPPDATA%\\Melange."""
    text = os.fspath(path)
    if not _rebasing() or len(text) < 3 or text[1] != ':' or text[2] not in '\\/':
        return text
    flat = text.replace('\\', '/')
    for prefix, root in _rebase():
        if _under(flat, prefix):
            return root + (flat[len(prefix):].replace('/', '\\') if _IS_WINDOWS else flat[len(prefix):])
    return text

def to_stored(path):
    """to_host's inverse, for an output that RECORDS a path: a path under this host's moved root -> the Windows spelling the
    laptop writes, so a relocated run and the laptop store the same bytes.  Identity when not rebasing or not under a moved root."""
    text = os.fspath(path)
    if not _rebasing():
        return text
    flat = text.replace('\\', '/')
    for prefix, root in sorted(_rebase(), key=lambda pr: -len(pr[1])):
        root = root.replace('\\', '/').rstrip('/')
        if _under(flat, root):
            return (prefix + flat[len(root):]).replace('/', '\\')
    return text
