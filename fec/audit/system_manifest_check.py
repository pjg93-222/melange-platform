# -*- coding: utf-8 -*-
"""SYSTEM MANIFEST vs DISK — the agreement check behind audit_full.py's [SYS] section.

The manifest at data/system/system_manifest.json is the EXPECTATION: what a prior run of
fec/audit/build_system_manifest.py measured.  Live os.stat / os.scandir / hashlib is the
OBSERVATION.  This module compares the two and never conflates them.

ANTI-VACUITY CONTRACT.  This repo has shipped two checks that could not fail — one asserted that a
filename started with its own glob prefix.  Every rule below exists because of that:

  * The manifest is READ FROM DISK.  This module never imports, invokes, or reproduces
    build_system_manifest, and it asserts that build_system_manifest is not even loaded in the
    process (`no_regeneration`).  A manifest compared against itself proves nothing.
  * The manifest must PREDATE this process, and its generator_commit must resolve in git.  A
    manifest that a caller just made up in memory fails both.
  * An EMPTY store list is a contract FAILURE, not a vacuous pass.  A check with nothing to check
    reports that as a defect and stops, rather than emitting "0 disagreements".
  * A root that is absent SKIPS its stores with a stated reason — but if EVERY root is absent, the
    manifest is describing a different machine and that is a FAILURE.
  * Every failure message names WHICH SIDE produced each value: `manifest(...)` vs `disk(...)`.
  * SECRET stores are never opened.  A manifest asking this checker to hash one is itself a failure.

CHECKS
  A0  contract   schema, non-empty stores, manifest age, generator_commit resolves, no regeneration
  A1  existence  declared-present stores exist; declared-absent stores are still absent
  A2  scale      size_class equality + file_count within a per-class tolerance (never exact bytes).
                 A GIT store (repo.git, the bare mirrors) is the one exception: its file_count is
                 supposed to move on every commit, push and gc, so it asserts a populated object
                 store instead of a tolerance band — see the GIT STORES block below.
  A2b sentinels  for stores too large to re-walk in a gate: the recorded sentinel files, exactly.
                 A sentinel that is git bookkeeping is refused rather than honoured.
  A3  ordering   for stores declaring derives_from, no input is newer than its derivative
  A4  hashes     the hash_always set only, plus re-assertion of the envelope that admitted it AND
                 of the scope rule that kept it in range (hash_always covers only what git does not
                 protect — a row claiming both hash_always and git_tracked=true is a contradiction)
  A5  sqlite     a .db store carries a sidecar block; -shm without -wal is inconsistent on disk
  A6  topology   drive/disk id and offbox_replica match observation; single_disk recomputes equal

Read-only.  Writes nothing, anywhere.
"""
import os, sys, json, time, hashlib, fnmatch, subprocess

MANIFEST_REL = os.path.join('data', 'system', 'system_manifest.json')
SCHEMA = 'melange-system-manifest-v1'

# A store bigger than this is not re-walked inside a gate; its sentinels are checked instead.
WALK_LIMIT = int(os.environ.get('SYSAUDIT_WALK_LIMIT', '20000'))
# file_count drift a store's class is allowed before A2 calls it a disagreement.
COUNT_TOLERANCE_PCT = {'SOURCE': 0.05, 'SNAPSHOT': 0.05, 'SECRET': 0.0,
                       'DERIVED': 0.25, 'OPS': 0.25}
COUNT_TOLERANCE_FLOOR = 5


# ---------------------------------------------------------------------------------------------
# observation helpers — all live, none of them consults the manifest for a value
# ---------------------------------------------------------------------------------------------
def _size_class(n):
    if n <= 0:              return 'empty'
    if n < (1 << 20):       return 'tiny'
    if n < (10 << 20):      return 'small'
    if n < (100 << 20):     return 'medium'
    if n < (1 << 30):       return 'large'
    if n < (10 << 30):      return 'huge'
    return 'massive'


def _excluded(rel, excludes):
    if not excludes:
        return False
    base = rel.rsplit('/', 1)[-1]
    for pat in excludes:
        if '/' in pat:
            if fnmatch.fnmatch(rel, pat):
                return True
            trimmed = pat.rstrip('*').rstrip('/')
            if trimmed and (rel == trimmed or rel.startswith(trimmed + '/')):
                return True
        else:
            if fnmatch.fnmatch(base, pat):
                return True
            if rel == pat or rel.startswith(pat + '/'):
                return True
    return False


def _observe(path, excludes, limit):
    """Live measurement of one store.  Returns None when the walk would exceed `limit`, so the
    caller can fall back to sentinels rather than burn a gate's wall clock."""
    if not os.path.exists(path):
        return {'present': False, 'kind': None, 'file_count': 0, 'bytes': 0,
                'newest_mtime_epoch': None, 'complete': True}
    if not os.path.isdir(path):
        st = os.stat(path)
        return {'present': True, 'kind': 'file', 'file_count': 1, 'bytes': st.st_size,
                'newest_mtime_epoch': st.st_mtime, 'complete': True}
    files = 0
    total = 0
    newest = None
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                rel = os.path.relpath(e.path, path).replace('\\', '/')
            except ValueError:
                continue
            if _excluded(rel, excludes):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            files += 1
            total += st.st_size
            if newest is None or st.st_mtime > newest:
                newest = st.st_mtime
            if files > limit:
                return None
    return {'present': True, 'kind': 'dir', 'file_count': files, 'bytes': total,
            'newest_mtime_epoch': newest, 'complete': True}


# ---------------------------------------------------------------------------------------------
# GIT STORES.  Re-derived here rather than imported: this module never loads the producer (see the
# anti-vacuity contract above), so it re-implements the predicate the way it already re-implements
# _size_class and _excluded.  Keep the two in step; the controls in
# fec/tests/audit/test_system_manifest_check.py drive BOTH sides.
#
# WHY A GIT STORE IS CHECKED DIFFERENTLY (2026-08-27, and do not "tighten" this back):
# repo.git, mirror.melange and mirror.glassbox exist in order to change.  A commit writes loose
# objects and rewrites the index; a push writes more objects and moves a ref; `git gc` deletes
# thousands of files at once.  A file_count tolerance band over that is a FALSE POSITIVE BY
# CONSTRUCTION, and it fired on this gate's own landing commit: mirror.melange went 86 -> 100 files,
# +14 objects the push added, and the audit called it a disagreement.  The expectation was wrong,
# not the check — so the expectation is corrected to what a git store can honestly promise: it still
# exists (A1), it still carries a populated object store, it is still of the same coarse size class,
# and its content-addressed sentinels are still byte-identical (A2b).  That is what "not lost, not
# corrupted" means for a mirror.  Nothing else is relaxed: the A1/A2/A4/A5/A6 rules for real data
# stores are untouched, and the object-store check below FAILS on an emptied or clobbered mirror.
# ---------------------------------------------------------------------------------------------
GIT_DIR_MARKERS = (('HEAD', 'file'), ('objects', 'dir'), ('refs', 'dir'))
GIT_BOOKKEEPING_FILES = frozenset((
    'index', 'HEAD', 'ORIG_HEAD', 'FETCH_HEAD', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD',
    'REBASE_HEAD', 'AUTO_MERGE', 'BISECT_HEAD', 'BISECT_START', 'BISECT_LOG', 'BISECT_NAMES',
    'BISECT_EXPECTED_REV', 'BISECT_ANCESTORS_OK', 'packed-refs', 'COMMIT_EDITMSG', 'MERGE_MSG',
    'MERGE_MODE', 'SQUASH_MSG', 'TAG_EDITMSG', 'shallow', 'gc.pid', 'gc.log',
))
GIT_BOOKKEEPING_DIRS = ('refs', 'logs', 'rebase-merge', 'rebase-apply', 'sequencer',
                        'worktrees', 'modules')
GIT_DERIVED_INDEX_PREFIXES = ('objects/info/',)
GIT_DERIVED_INDEX_PATHS = ('info/refs', 'objects/pack/multi-pack-index')


def _is_git_dir(path):
    """Observed from the filesystem SHAPE, exactly as the producer measures it — a work tree's
    `.git` and a bare mirror both carry HEAD + objects/ + refs/.  Never taken from the manifest
    alone: a row that could declare this could declare it wrongly and switch off a check."""
    try:
        if not path or not os.path.isdir(path):
            return False
        for name, kind in GIT_DIR_MARKERS:
            probe = os.path.isfile if kind == 'file' else os.path.isdir
            if not probe(os.path.join(path, name)):
                return False
        return True
    except OSError:
        return False


def _git_bookkeeping(rel, store_is_git_dir):
    """True when `rel` is git's own bookkeeping — the material ordinary commits/pushes rewrite.

    Narrow on purpose: outside a git directory `index` and `HEAD` are ordinary filenames and stay
    fully checkable.  Content-addressed object files are NOT bookkeeping and stay checkable too."""
    parts = [p for p in str(rel).replace('\\', '/').split('/') if p not in ('', '.')]
    if '.git' in parts:
        inner = '/'.join(parts[len(parts) - parts[::-1].index('.git'):])
    elif store_is_git_dir:
        inner = '/'.join(parts)
    else:
        return False
    if inner == '':
        return True
    if inner in GIT_DERIVED_INDEX_PATHS or inner.startswith(GIT_DERIVED_INDEX_PREFIXES):
        return True
    seg = inner.split('/')
    if seg[0] in GIT_BOOKKEEPING_DIRS:
        return True
    return len(seg) == 1 and (seg[0] in GIT_BOOKKEEPING_FILES or seg[0].endswith('.lock'))


def _git_object_store_populated(path, scan_cap=4096):
    """FALSIFIABLE observation, and the reason the git relaxation is not an off switch: a mirror
    that has been emptied, truncated to its scaffolding, or clobbered has no objects left, and this
    returns False.  Ordinary pushes and `git gc` both leave it True.  objects/info holds regenerated
    caches, not objects, so it does not count toward "populated"."""
    base = os.path.join(path, 'objects')
    if not os.path.isdir(base):
        return False
    seen = 0
    stack = [base]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if os.path.normcase(e.path) != os.path.normcase(
                                    os.path.join(base, 'info')):
                                stack.append(e.path)
                            continue
                    except OSError:
                        continue
                    return True

        except OSError:
            continue
        seen += 1
        if seen > scan_cap:
            return False
    return False


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _live_volume_disk_map():
    """One cheap PowerShell probe for the live volume -> physical-disk mapping.  Returns None when
    it cannot be established; the caller then SKIPS the disk-id sub-check with that reason."""
    if os.name != 'nt':
        return None
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             "@(Get-Partition | Where-Object {$_.DriveLetter} | "
             "Select-Object DriveLetter,DiskNumber) | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
        raw = (r.stdout or '').strip()
        if not raw:
            return None
        d = json.loads(raw)
        rows = d if isinstance(d, list) else [d]
        return {str(x['DriveLetter']) + ':': str(x['DiskNumber'])
                for x in rows if x.get('DriveLetter')}
    except Exception:
        return None


def _live_onedrive_root():
    od = os.environ.get('OneDrive') or os.environ.get('OneDriveConsumer')
    if od:
        return os.path.abspath(od)
    guess = os.path.join(os.path.expanduser('~'), 'OneDrive')
    return os.path.abspath(guess) if os.path.isdir(guess) else None


def _under(path, root):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) \
            == os.path.abspath(root)
    except (ValueError, TypeError):
        return False


def _ts(epoch):
    if not epoch:
        return 'none'
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


# ---------------------------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------------------------
def check_manifest(manifest_path, emit, notice=None, git_root=None, now=None, walk_limit=None):
    """Compare the manifest on disk against live disk.

    emit(name, cond, detail)  -> the caller's PASS/FAIL sink (audit_full's ck)
    notice(text)              -> informational, never a verdict; may be None
    Returns a tally dict.  A caller that gets checks==0 back should treat that as a failure."""
    started = now if now is not None else time.time()
    limit = WALK_LIMIT if walk_limit is None else walk_limit
    notice = notice or (lambda _t: None)
    tally = {'checks': 0, 'failed': 0, 'skipped': 0, 'notices': 0}
    skips = []

    def ck(name, cond, detail=''):
        tally['checks'] += 1
        if not cond:
            tally['failed'] += 1
        emit(name, bool(cond), detail)

    def skip(name, reason):
        tally['skipped'] += 1
        skips.append((name, reason))

    def note(text):
        tally['notices'] += 1
        notice(text)

    # --- A0 CONTRACT -------------------------------------------------------------------------
    # The single most important assertion in this file: the thing we are about to trust was NOT
    # produced by this process.  A check that regenerates its own expectation cannot fail.
    ck('A0 system manifest was not regenerated in this process',
       'build_system_manifest' not in sys.modules,
       'build_system_manifest is imported in this interpreter; the expectation would be '
       'self-produced and the comparison vacuous')

    if not os.path.isfile(manifest_path):
        ck('A0 system manifest exists on disk', False,
           'disk: no file at %s — run fec/audit/build_system_manifest.py first' % manifest_path)
        return tally
    ck('A0 system manifest exists on disk', True)

    try:
        with open(manifest_path, encoding='utf-8') as fh:
            man = json.load(fh)
    except Exception as exc:
        ck('A0 system manifest parses as JSON', False,
           'disk: %s: %s' % (type(exc).__name__, str(exc)[:160]))
        return tally
    ck('A0 system manifest parses as JSON', True)

    ck('A0 system manifest declares schema %s' % SCHEMA, man.get('schema') == SCHEMA,
       'manifest: schema=%r / expected %r' % (man.get('schema'), SCHEMA))

    stores = man.get('stores')
    roots = man.get('roots')
    ck('A0 system manifest declares at least one store',
       isinstance(stores, list) and len(stores) > 0,
       'manifest: stores=%s — a manifest with no stores would make every check below vacuously '
       'true, so it is a contract failure' % ('[] (empty)' if stores == [] else repr(stores)[:60]))
    ck('A0 system manifest declares at least one root',
       isinstance(roots, list) and len(roots) > 0,
       'manifest: roots=%s' % (repr(roots)[:60]))
    if not (isinstance(stores, list) and stores and isinstance(roots, list) and roots):
        return tally                    # degenerate: emit nothing further rather than fake passes

    gen_epoch = man.get('generated_epoch')
    gen_utc = man.get('generated_utc') or _ts(gen_epoch)
    ck('A0 system manifest predates this audit run',
       isinstance(gen_epoch, (int, float)) and gen_epoch < started,
       'manifest: generated_epoch=%r (%s) / this run started %s — the manifest must come from a '
       'PRIOR producer run' % (gen_epoch, gen_utc, _ts(started)))
    try:
        mstat = os.stat(manifest_path).st_mtime
        ck('A0 system manifest file mtime predates this audit run', mstat < started,
           'disk: manifest mtime %s / this run started %s' % (_ts(mstat), _ts(started)))
    except OSError as exc:
        ck('A0 system manifest file mtime predates this audit run', False, 'disk: %r' % exc)

    commit = man.get('generator_commit')
    groot = git_root or man.get('root') or os.path.dirname(manifest_path)
    if not commit:
        ck('A0 system manifest records a generator_commit', False,
           'manifest: generator_commit is %r (%s)' % (commit, man.get('generator_commit_source')))
    else:
        try:
            r = subprocess.run(['git', '-C', groot, 'rev-parse', '--verify', '--quiet',
                                str(commit) + '^{commit}'],
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                ck('A0 system manifest generator_commit resolves in git', True)
            elif r.returncode == 128:
                skip('A0 generator_commit resolves',
                     'git could not read a repository at %s (%s)' % (groot, (r.stderr or '').strip()[:80]))
            else:
                ck('A0 system manifest generator_commit resolves in git', False,
                   'manifest: generator_commit=%s / git in %s: no such commit' % (commit, groot))
        except (OSError, subprocess.SubprocessError) as exc:
            skip('A0 generator_commit resolves', 'git unavailable: %s' % type(exc).__name__)

    # --- roots -------------------------------------------------------------------------------
    root_present = {}
    for r in roots:
        rid, rpath = r.get('id'), r.get('path')
        root_present[rid] = bool(rpath) and os.path.isdir(rpath)
    ck('A0 at least one declared root is present on this host', any(root_present.values()),
       'manifest declared roots %s; disk: none of them is a directory on this host — this manifest '
       'describes a different machine' % sorted(root_present))
    if not any(root_present.values()):
        return tally

    # --- A6 host topology, observed once -------------------------------------------------------
    host = man.get('host') or {}
    live_od = _live_onedrive_root()
    man_od = host.get('onedrive_root')
    if man_od is None and live_od is None:
        skip('A6 OneDrive root', 'neither the manifest nor this host establishes a OneDrive root')
    else:
        ck('A6 OneDrive root matches observation',
           bool(man_od) and bool(live_od)
           and os.path.normcase(man_od) == os.path.normcase(live_od),
           'manifest: onedrive_root=%r (source %s) / disk+env: %r — every single_disk value in '
           'this manifest was derived from the manifest side'
           % (man_od, host.get('onedrive_root_source'), live_od))
    live_map = _live_volume_disk_map()
    if live_map is None:
        skip('A6 physical disk id', 'the live volume->disk probe did not return a mapping on this host')
    man_map = host.get('volume_disk_map') or {}

    # --- per-store -----------------------------------------------------------------------------
    by_id = {s.get('id'): s for s in stores}
    envelope = (man.get('policy') or {}).get('hash_always_envelope') or {}
    checked_any = False
    topo = {'volume': [], 'disk_id': [], 'offbox_replica': [], 'single_disk': [],
            'durable_backup': []}

    for s in stores:
        sid = s.get('id')
        path = s.get('path')
        cls = s.get('class')
        rid = s.get('root_id')
        m = s.get('measured') or {}
        if not root_present.get(rid):
            skip('store %s' % sid, 'its root %s is absent on this host (fresh clone / other box)' % rid)
            continue
        if not path:
            ck('A1 store %s records a path' % sid, False, 'manifest: path is %r' % path)
            continue
        checked_any = True
        live_exists = os.path.exists(path)
        # FAIL-CLOSED conjunction: a store takes the git-store path only when the manifest recorded
        # it as a git directory AND this process observes the same shape on disk right now. A
        # hand-edited manifest cannot switch off the file_count check for an ordinary data store
        # (disk would disagree), and it cannot be tightened into a false positive by deleting the
        # flag either — that just puts the store back on the strict path.
        git_dir = bool(s.get('git_dir_store')) and _is_git_dir(path)

        # ---- A1 existence parity --------------------------------------------------------------
        if m.get('present'):
            ck('A1 %s still exists' % sid, live_exists,
               'manifest(%s): declared PRESENT at %s / disk: os.path.exists says ABSENT' % (gen_utc, path))
        else:
            ck('A1 %s still absent' % sid, not live_exists,
               'manifest(%s): declared ABSENT / disk: %s now exists' % (gen_utc, path))
        if not (m.get('present') and live_exists):
            continue

        # ---- A6 topology ----------------------------------------------------------------------
        # A6 is accumulated, not emitted per store. A per-store topology disagreement is not a
        # thing that happens on its own: the causes are systematic (OneDrive moved, the volume was
        # re-lettered, someone typed a copy story into the registry), so 5 rolled-up verdicts that
        # NAME their offenders diagnose it exactly as well as 5-per-store would, without burying
        # every other section of this audit under 400 lines that can only move together.
        live_drive = (os.path.splitdrive(os.path.abspath(path))[0] or '').upper() or None
        if live_drive != s.get('volume'):
            topo['volume'].append('%s: manifest=%r / disk=%r' % (sid, s.get('volume'), live_drive))
        if live_map is not None and s.get('disk_id') != live_map.get(live_drive):
            topo['disk_id'].append('%s: manifest=%r for %s / live Get-Partition=%r'
                                   % (sid, s.get('disk_id'), live_drive, live_map.get(live_drive)))
        # single_disk and offbox_replica are DERIVED, never authored. Re-derive them here from the
        # manifest's own path and the LIVE OneDrive root: if the recorded value disagrees with the
        # recomputation, either the rule changed or somebody typed a copy story into the registry.
        if live_od is not None:
            re_replica = 'onedrive_sync' if _under(path, live_od) else None
            if s.get('offbox_replica') != re_replica:
                topo['offbox_replica'].append(
                    '%s: manifest=%r (basis: %s) / recomputed from live OneDrive root %s = %r'
                    % (sid, s.get('offbox_replica'), s.get('topology_basis'), live_od, re_replica))
            if s.get('single_disk') != (re_replica is None):
                topo['single_disk'].append(
                    '%s: manifest=%r / recomputed from live OneDrive root %s = %r'
                    % (sid, s.get('single_disk'), live_od, re_replica is None))
        if s.get('durable_backup') is not False:
            topo['durable_backup'].append('%s: manifest=%r' % (sid, s.get('durable_backup')))

        # ---- A5 sqlite sidecars ---------------------------------------------------------------
        sq = s.get('sqlite')
        looks_sqlite = m.get('kind') == 'file' and str(path).lower().endswith(
            ('.db', '.sqlite', '.sqlite3'))
        if looks_sqlite:
            ck('A5 %s carries a sqlite sidecar block' % sid, isinstance(sq, dict),
               'manifest: sqlite=%r for a database file — the producer failed to classify it, so '
               'any restore plan built from this manifest would copy the .db without its -wal'
               % (sq,))
        if isinstance(sq, dict):
            wal_live = os.path.exists(path + '-wal')
            shm_live = os.path.exists(path + '-shm')
            ck('A5 %s has no -shm stranded without its -wal' % sid, not (shm_live and not wal_live),
               'disk: %s-shm exists but %s-wal does not — an inconsistent sqlite sidecar state'
               % (os.path.basename(path), os.path.basename(path)))
            if wal_live != bool(sq.get('wal_present')):
                note('A5 %s: manifest(%s) recorded wal_present=%s / disk now says %s — normal while '
                     'a connection is open, a finding if it persists'
                     % (sid, gen_utc, bool(sq.get('wal_present')), wal_live))

        # ---- A2 / A2b scale -------------------------------------------------------------------
        if not m.get('walk_complete', True):
            skip('A2 %s' % sid,
                 'manifest recorded BUDGET-EXCEEDED (%s files, walk incomplete): a partial '
                 'expectation cannot be compared to a complete observation'
                 % m.get('file_count'))
            _check_sentinels(s, sid, gen_utc, ck, skip, git_dir, note)
        elif (m.get('file_count') or 0) > limit:
            skip('A2 %s' % sid,
                 'manifest recorded %d files, above this gate\'s re-walk limit of %d; sentinel '
                 'parity is checked instead' % (m.get('file_count'), limit))
            _check_sentinels(s, sid, gen_utc, ck, skip, git_dir, note)
        else:
            obs = _observe(path, s.get('excludes') or [], limit)
            if obs is None:
                skip('A2 %s' % sid,
                     'live walk passed the %d-file limit even though the manifest recorded %d — '
                     'the store grew past what a gate may walk' % (limit, m.get('file_count')))
                _check_sentinels(s, sid, gen_utc, ck, skip, git_dir, note)
            else:
                exp_class = m.get('size_class')
                obs_class = _size_class(obs['bytes'])
                ck('A2 %s size_class agrees' % sid, exp_class == obs_class,
                   'manifest(%s): size_class=%s (%d bytes) / disk: size_class=%s (%d bytes)'
                   % (gen_utc, exp_class, m.get('bytes') or 0, obs_class, obs['bytes']))
                exp_n = m.get('file_count') or 0
                if git_dir:
                    # See the GIT STORES block at the top of this module. The count is SUPPOSED to
                    # move here — a commit adds loose objects, a push adds more, a gc removes
                    # thousands — so the honest expectation is that the object store is still there.
                    ck('A2 %s still holds a populated git object store' % sid,
                       _git_object_store_populated(path),
                       'manifest(%s): %s is a git directory with %d file(s), size_class=%s / disk: '
                       'objects/ holds no object files — the repository has been emptied or '
                       'clobbered. (file_count itself is NOT asserted for a git store: it moves on '
                       'every commit, push and gc, so a tolerance band there is a false positive by '
                       'construction. Observed now: %d file(s).)'
                       % (gen_utc, path, exp_n, m.get('size_class'), obs['file_count']))
                else:
                    tol = max(COUNT_TOLERANCE_FLOOR,
                              int(round(COUNT_TOLERANCE_PCT.get(cls, 0.10) * exp_n)))
                    ck('A2 %s file_count within tolerance' % sid,
                       abs(obs['file_count'] - exp_n) <= tol,
                       'manifest(%s): file_count=%d / disk: file_count=%d (drift %+d, tolerance '
                       '+/-%d for class %s)' % (gen_utc, exp_n, obs['file_count'],
                                                obs['file_count'] - exp_n, tol, cls))
                _check_sentinels(s, sid, gen_utc, ck, skip, git_dir, note)

        # ---- A4 hash agreement (hash_always only) ---------------------------------------------
        if s.get('integrity_policy') == 'hash_always':
            _check_hashes(s, sid, gen_utc, cls, m, envelope, ck, skip)

    ck('A0 the manifest-vs-disk section examined at least one store', checked_any,
       'every declared store was skipped; the section proved nothing')

    # --- A6 rolled-up topology verdicts --------------------------------------------------------
    ck('A6 every store volume matches observation', not topo['volume'],
       '%d disagreement(s), manifest vs live splitdrive :: %s'
       % (len(topo['volume']), topo['volume'][:6]))
    if live_map is not None:
        ck('A6 every store disk id matches observation', not topo['disk_id'],
           '%d disagreement(s), manifest vs live Get-Partition :: %s'
           % (len(topo['disk_id']), topo['disk_id'][:6]))
    if live_od is not None:
        ck('A6 every offbox_replica recomputes from the live OneDrive root',
           not topo['offbox_replica'],
           '%d disagreement(s) :: %s' % (len(topo['offbox_replica']), topo['offbox_replica'][:6]))
        ck('A6 every single_disk recomputes from the live OneDrive root', not topo['single_disk'],
           '%d disagreement(s) — single_disk is DERIVED and must never be authored :: %s'
           % (len(topo['single_disk']), topo['single_disk'][:6]))
    ck('A6 no store claims a durable backup', not topo['durable_backup'],
       '%d store(s) claim durable_backup=True, but no durable backup target exists on this host '
       ':: %s' % (len(topo['durable_backup']), topo['durable_backup'][:6]))

    # ---- A3 mtime ordering --------------------------------------------------------------------
    for s in stores:
        deps = s.get('derives_from') or []
        if not deps:
            continue
        sid = s.get('id')
        if not root_present.get(s.get('root_id')) or not os.path.exists(s.get('path') or ''):
            continue
        d_m = _newest(s.get('path'))
        for dep in deps:
            src = by_id.get(dep)
            if src is None:
                ck('A3 %s names a real input %s' % (sid, dep), False,
                   'manifest: derives_from lists %r but no store carries that id' % dep)
                continue
            if not root_present.get(src.get('root_id')) or not os.path.exists(src.get('path') or ''):
                skip('A3 %s <- %s' % (sid, dep), 'the input store is not present on this host')
                continue
            s_m = _newest(src.get('path'))
            if d_m is None or s_m is None:
                skip('A3 %s <- %s' % (sid, dep), 'no mtime observable on one side')
                continue
            tol = float((man.get('policy') or {}).get('mtime_tolerance_s') or 2.0)
            ck('A3 %s is not older than its input %s' % (sid, dep), s_m <= d_m + tol,
               'manifest: declares %s derives_from %s / disk: input mtime %s is NEWER than '
               'derivative mtime %s (by %.1fs, tolerance %.1fs) — the derivative is stale'
               % (sid, dep, _ts(s_m), _ts(d_m), s_m - d_m, tol))

    # ---- absent-expected (NOTICE, or FAIL for alarm-severity negative facts) -------------------
    for entry in man.get('absent_expected') or []:
        eid = entry.get('id')
        base = entry.get('base')
        if not entry.get('root_id') or not root_present.get(entry['root_id']):
            skip('absent_expected %s' % eid, 'its root %s is absent on this host' % entry.get('root_id'))
            continue
        if not base or not os.path.isdir(base):
            skip('absent_expected %s' % eid, 'base directory %s does not exist on this host' % base)
            continue
        pats = entry.get('patterns') or []
        depth = entry.get('depth')
        hits = []
        for dirpath, dirnames, filenames in os.walk(base):
            rel = os.path.relpath(dirpath, base)
            level = 0 if rel == '.' else rel.count(os.sep) + 1
            if depth is not None and level >= depth:
                dirnames[:] = []
            for name in filenames:
                if any(fnmatch.fnmatch(name.lower(), p.lower()) for p in pats):
                    hits.append(os.path.join(dirpath, name).replace('\\', '/'))
            if len(hits) > 50:
                break
        recorded = entry.get('matches') or []
        if entry.get('severity') == 'alarm':
            ck('A1 absent_expected %s holds' % eid, not hits,
               'manifest(%s): recorded %d match(es) for %s under %s / disk: %d now match %s :: %s'
               % (gen_utc, len(recorded), pats, base, len(hits), pats, hits[:5]))
        elif hits:
            note('A1 absent_expected %s: manifest(%s) recorded %d match(es) / disk now finds %d '
                 '%s -- %s' % (eid, gen_utc, len(recorded), len(hits), hits[:5], entry.get('why')))

    tally['skips'] = skips
    return tally


def _newest(path):
    try:
        if os.path.isdir(path):
            best = None
            for dirpath, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        t = os.stat(os.path.join(dirpath, name)).st_mtime
                    except OSError:
                        continue
                    if best is None or t > best:
                        best = t
            return best
        return os.stat(path).st_mtime
    except OSError:
        return None


def _packed_object_verified(base, rel):
    """Git gc may relocate a loose object; verify its canonical object bytes."""
    import re
    match = re.fullmatch(r'objects/([0-9a-f]{2})/([0-9a-f]{38})', str(rel))
    if not match:
        return False
    oid = ''.join(match.groups())
    try:
        command = ['git', '--git-dir=' + str(base), 'cat-file']
        kind = subprocess.run(command + ['-t', oid], capture_output=True, timeout=15, check=True).stdout.strip()
        if kind not in (b'blob', b'tree', b'commit', b'tag'):
            return False
        payload = subprocess.run(command + [kind.decode(), oid], capture_output=True, timeout=15, check=True).stdout
        return hashlib.sha1(kind + b' ' + str(len(payload)).encode() + b'\0' + payload).hexdigest() == oid
    except (OSError, subprocess.SubprocessError):
        return False


def _git_reachable_commits(git_dir):
    """Every commit reachable from the repository's refs, or False when git cannot answer."""
    try:
        r = subprocess.run(['git', '--git-dir=' + str(git_dir), 'rev-list', '--all'],
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return False
    return set(r.stdout.split()) if r.returncode == 0 else False


def _check_sentinels(store, sid, gen_utc, ck, skip, git_dir=False, note=None):
    """A2b: for stores too large to re-walk, the recorded sentinels ARE the observation surface.

    One verdict per store, naming every disagreeing sentinel in the detail — four verdicts per
    store across eighty stores would be 320 lines that a reader stops reading.

    `note` is check_manifest's own NOTICE sink (informational, never a verdict).  It is optional so
    the signature stays callable without it; when it is absent the DE-011 no-hashable notice below
    is simply not raised, which is the same fail-quiet the module's own `notice=None` default has."""
    sent = store.get('sentinels')
    if not sent:
        if store.get('integrity_policy') == 'sentinel_files':
            skip('A2b %s sentinels' % sid, 'manifest recorded no sentinel files for this store')
        return
    base = store.get('path')
    is_file = (store.get('measured') or {}).get('kind') == 'file'
    bad = []
    reachable = None
    for row in sent:
        rel = row.get('rel')
        # A git COMMIT pin (2026-10-11): the commit HEAD, the trunk or a tag resolved to when the
        # manifest was built must still be reachable from the current refs.  New commits, pushes
        # and gc/repack all preserve that; an emptied, truncated or history-rewritten repo does not.
        if row.get('git_commit'):
            if reachable is None:
                reachable = _git_reachable_commits(base)
            if reachable is False:
                bad.append('%s -> %s: git could not list reachable commits in %s'
                           % (row.get('git_ref'), row['git_commit'][:12], base))
            elif row['git_commit'] not in reachable:
                bad.append('%s -> %s: manifest pinned this commit / disk: no longer reachable from '
                           'any ref (history rewritten, repo truncated, or the ref deleted)'
                           % (row.get('git_ref'), row['git_commit'][:12]))
            continue
        # The producer refuses to SELECT git bookkeeping as a sentinel; this refuses to HONOUR it,
        # the same way A4 re-asserts the hash envelope the producer already enforced. A manifest
        # carrying `index` or `refs/heads/main` as a sentinel is describing an expectation the next
        # commit falsifies, so it is reported as the manifest defect it is rather than quietly
        # passed (which would hide a producer regression) or quietly failed on a byte mismatch
        # (which would blame the commit). This is a TIGHTENING, and it is deliberately not the
        # blanket skip that would let a real deletion through.
        if _git_bookkeeping(rel, git_dir):
            bad.append('%s: recorded as a sentinel, but it is GIT BOOKKEEPING — rewritten by every '
                       'ordinary commit/push/fetch, so it can never be a stable expectation. '
                       'Regenerate the manifest with the current producer.' % rel)
            continue
        full = base if is_file else os.path.join(base, str(rel).replace('/', os.sep))
        if not os.path.isfile(full):
            if git_dir and _packed_object_verified(base, rel):
                if note is not None:
                    note('A2b %s: %s packed; canonical Git object hash verified' % (sid, rel))
                continue
            bad.append('%s: manifest recorded it at %s bytes / disk: not a file at %s'
                       % (rel, row.get('bytes'), full))
            continue
        if row.get('content_stable') is False:
            continue        # existence only: the producer declared this store's bytes unstable
        st = os.stat(full)
        if st.st_size != row.get('bytes'):
            bad.append('%s size: manifest=%s bytes / disk=%d bytes'
                       % (rel, row.get('bytes'), st.st_size))
            continue
        if row.get('sha256'):
            got = _sha256(full)
            if got != row['sha256']:
                bad.append('%s sha256: manifest=%s / disk=%s'
                           % (rel, row['sha256'][:16] + '...', got[:16] + '...'))
    # A store whose A2 scale check was SKIPPED (above WALK_LIMIT) and whose sentinels carry no
    # sha256 at all has neither scale nor byte evidence left — only A1 existence. That is not a
    # thing the producer emits any more (build_system_manifest's by_hashable route prevents it),
    # so seeing it here means either a manifest predating that route or one that was hand-edited.
    # Reported as a NOTICE rather than a verdict: the hole is real but naming it as a FAIL on an
    # older manifest would turn every pre-upgrade gate red for a producer-side fix.
    if sent and not any(r.get('sha256') or r.get('git_commit') for r in sent) and note is not None:
        note('A2b %s: %d sentinel(s) recorded, NONE carrying a sha256 (all above the %d-byte '
             'hash cap). If this store is also above the walk limit its content evidence is '
             'existence-and-size only. Regenerate with a producer that emits the '
             'largest-hashable fallback.' % (sid, len(sent), 8 * 1024 * 1024))
    ck('A2b %s sentinels unchanged' % sid, not bad,
       'manifest(%s): %d sentinel(s) recorded / disk: %d disagree :: %s'
       % (gen_utc, len(sent), len(bad), bad[:4]))


def _check_hashes(store, sid, gen_utc, cls, m, envelope, ck, skip):
    """A4: the hash_always set only — plus a re-assertion that this row was ever entitled to it.

    The envelope re-check is the guard against a hand-edited manifest: the producer refuses to EMIT
    hash_always outside the envelope, and this refuses to HONOUR it."""
    if cls == 'SECRET':
        ck('A4 %s is not a SECRET claiming hash_always' % sid, False,
           'manifest: SECRET store declares integrity_policy=hash_always. Refusing to read it. '
           'Secrets are presence_only, always.')
        return
    max_b = envelope.get('max_bytes')
    max_f = envelope.get('max_files')
    min_i = envelope.get('min_irreplaceability')
    bad = []
    if max_b is not None and (m.get('bytes') or 0) > max_b:
        bad.append('bytes %d > envelope max_bytes %d' % (m.get('bytes') or 0, max_b))
    if max_f is not None and (m.get('file_count') or 0) > max_f:
        bad.append('file_count %d > envelope max_files %d' % (m.get('file_count') or 0, max_f))
    if min_i is not None and (store.get('irreplaceability') or 0) < min_i:
        bad.append('irreplaceability %s < envelope min %s' % (store.get('irreplaceability'), min_i))
    if not m.get('walk_complete', True):
        bad.append('walk was incomplete, so the hash set cannot be anchored')
    ck('A4 %s hash_always is inside the declared envelope' % sid, not bad,
       'manifest: store claims hash_always but violates the envelope it also declares :: %s'
       % '; '.join(bad))
    if bad:
        return

    # Scope re-assertion (Paul's 2026-08-27 ruling): hash_always covers only what git does not
    # protect.  The producer REFUSES to emit hash_always for a store it measured as git-tracked;
    # this refuses to HONOUR one.  Manifest-internal on purpose — same shape as the envelope
    # re-check above — so it catches a row hand-edited back to hash_always while still carrying the
    # producer's own measurement of its tracked-ness, and costs the gate no subprocess.
    if store.get('git_tracked') is True:
        ck('A4 %s hash_always is outside git protection' % sid, False,
           'manifest: store records git_tracked=true AND integrity_policy=hash_always. Those '
           'contradict: hash_always covers only what git does not protect, and the producer '
           'refuses this combination. basis :: %s' % (store.get('git_tracking_basis'),))
        return

    hashes = store.get('hashes')
    if not isinstance(hashes, dict) or not hashes:
        ck('A4 %s records a hash set' % sid, False,
           'manifest: integrity_policy=hash_always but hashes=%r' % (hashes,))
        return
    base = store.get('path')
    is_file = m.get('kind') == 'file'
    live_rels = set()
    if is_file:
        live_rels = {os.path.basename(base)}
    else:
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                rel = os.path.relpath(os.path.join(dirpath, name), base).replace('\\', '/')
                if not _excluded(rel, store.get('excludes') or []):
                    live_rels.add(rel)
    mismatched, missing = [], []
    for rel, want in sorted(hashes.items()):
        full = base if is_file else os.path.join(base, rel.replace('/', os.sep))
        if not os.path.isfile(full):
            missing.append(rel)
            continue
        try:
            got = _sha256(full)
        except OSError as exc:
            mismatched.append('%s (unreadable: %s)' % (rel, type(exc).__name__))
            continue
        if got != want:
            mismatched.append('%s manifest=%s disk=%s' % (rel, want[:16] + '...', got[:16] + '...'))
    ck('A4 %s every hashed file still present' % sid, not missing,
       'manifest(%s): hash set names %d file(s) / disk: %d missing :: %s'
       % (gen_utc, len(hashes), len(missing), missing[:6]))
    ck('A4 %s every hashed file byte-identical' % sid, not mismatched,
       'manifest(%s) vs disk :: %s' % (gen_utc, mismatched[:6]))
    extra = sorted(live_rels - set(hashes))
    ck('A4 %s gained no unhashed file' % sid, not extra,
       'manifest(%s): hash set covers %d file(s) / disk: %d file(s) present, %d of them absent '
       'from the manifest :: %s' % (gen_utc, len(hashes), len(live_rels), len(extra), extra[:6]))


def default_manifest_path(root):
    return os.path.join(root, MANIFEST_REL)
