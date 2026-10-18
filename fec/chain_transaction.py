# -*- coding: utf-8 -*-
"""Transactional chain and labelled-degrade primitives (self-healing pipeline, phase 2; Paul 2026-10-15).

Owner rulings this module implements:
  * the system never halts as a whole, and LIVE is never left mixed-vintage;
  * rank-moving steps stay fail-closed -- a chain failure now ROLLS THE WHOLE LIVE WRITE SET BACK;
  * a source past its freshness window is served from last-good and LABELLED, not raised.

CHAIN TRANSACTION (run_pipeline.main holds the pipeline lease, then opens one of these):
  begin()     enumerate the chain's LIVE write set, copy (never hardlink: a hardlink would let the chain mutate its own
              backup) every file in it into <staging>/<stamp>-<run>/files/ (txn_base(): C:/melange-data/chain_txn by
              default, outside OneDrive), and write journal.json
              {path: sha256, size, mtime_ns, existed} with state OPEN.  The journal is the commit point of the snapshot:
              a staging directory with no journal is a snapshot that never finished and is simply deleted.
  commit()    state COMMITTED; the before-image is kept for the previous vintage and old transactions are pruned (KEEP).
  rollback()  restore EVERY journaled path byte-exact (bytes AND mtime, because make_combined's stale-input guards compare
              mtimes) and delete files the run newly created.  ROLLED_BACK is declared ONLY on proof: every journaled path
              is hashed (sha256) against the journal -- size and mtime are never evidence of identical bytes -- no new file
              remains, and no tracked-store file moved.  Anything else is state ROLLBACK_FAILED: the before-image is KEPT,
              RollbackFailed is raised, and no further chain run starts until an operator resolves it (repair by hand from
              <dir>/files, then delete <dir>).
  recover()   at the next start, under the lease: an OPEN journal is a run that died (kill, power loss, taskkill) -> restore
              it first; a ROLLBACK_FAILED one is retried once and, if it still cannot be proven, refuses the run.
  free space  refuses to start if the snapshot would leave less than MIN_FREE_GB free on the volume (reports the numbers).

WRITE SET.  There is no declared step->outputs table, so the set is a declared scope chosen from a measured census of the last
full chain (2026-10-11): root-level artifacts by extension + the data/ trees the chain writes into.  Three classes:
  copy     before-image taken, restored on rollback.
  track    append-only generation stores (rank_honors, congress_advanced_current, ...): too big to copy and immutable by
           construction, so (size, mtime, sha256) is journalled and nothing is copied.  A NEW file is deleted on rollback; a
           MODIFIED or MISSING one cannot be restored, so it makes the rollback ROLLBACK_FAILED.  Rollback re-hashes every tracked
           path: a different size is taken as proof of change (cheap pre-check), but equal size and mtime are never proof of
           equal bytes, so the sha256 decides.  Cost, measured read-only on the real write set 2026-10-15: 56,155 tracked
           files / 5.68 GB hashed in 1,960 s sequentially but 356 s on HASH_WORKERS=8 threads (the cost is per-file open
           latency on this host, which overlaps); the 1,300 copied files (1.32 GB) take 58 s sequentially.
  never    orchestrator/diagnostic state that is not LIVE vintage and is written by other processes during the chain
           (congress_pipeline_*, daily_refresh_*, weekly_refresh*, logs, dead_letter/, source_freshness.json, this directory).
Anything the chain writes outside this scope (the machine-local derived stores that live beside, not inside, the repo root) is
NOT protected.  This module never reads or writes those stores: it touches only paths under the root it is handed (and the
optional MELANGE_CHAIN_TXN_DIR staging override), which keeps it clear of the collector compute policy
(ops/tests/test_compute_collector_policy.py), since the collectors' import closures now reach it through the freshness loaders.

LABELLED DEGRADE: age_status() / note_freshness() give the four time-bomb sites one vocabulary (FRESH / STALE_LABELLED).
Stdlib only, plus the default data root from fec/paths.py.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone

from paths import DEFAULT_MELANGE_DATA

SCHEMA = 'chain-transaction-v1'
TXN_RELATIVE = 'data/engine/chain_txn'
TXN_DIR_ENV = 'MELANGE_CHAIN_TXN_DIR'            # absolute override of the staging base (recover() reads the same variable)
# Default staging base: <melange-data>/chain_txn, resolved like fec/paths.py MELANGE_DATA (outside OneDrive, so a ~1.3 GB before-image
# is never synced and never trips the A2 repo size audits). Falls back to the in-tree TXN_RELATIVE only where that root is absent or
# not writable (e.g. the compute container mounts /melange-data read-only); set MELANGE_CHAIN_TXN_DIR there instead.
DATA_ROOT_ENV = 'MELANGE_DATA_ROOT'
DEFAULT_DATA_ROOT = DEFAULT_MELANGE_DATA
MIN_FREE_GB_ENV = 'MELANGE_CHAIN_TXN_MIN_FREE_GB'  # test/ops override of the 30 GB floor; 0 disables the gate
MIN_FREE_GB = 30.0
KEEP = 2
BLOCK = 1 << 20
HASH_WORKERS = 8   # sha256 of the write set is bound by per-file open latency (scanner/OneDrive) on this host, which overlaps
OPEN, COMMITTED, ROLLED_BACK, ROLLBACK_FAILED = 'OPEN', 'COMMITTED', 'ROLLED_BACK', 'ROLLBACK_FAILED'


class RollbackFailed(RuntimeError):
    """LIVE could not be PROVEN restored to the before-image.  The before-image is kept and no further run will start."""

# A terminal failure of these steps does NOT roll back.  audit_full.py is the chain's last entry and a VERIFIER: when it is the
# step that fails every producer has already succeeded and LIVE is one complete vintage; weekly_refresh/refresh_congress_pipeline
# deliberately accept known-gated audit dissent (audit_failure_gate.tolerate_known_gated, chain-tail guard) on that basis, and
# reverting a whole run for the audit's own verdict would undo exactly that (it froze the UI for five weeks in 2026-07/08).
ROLLBACK_EXEMPT = ('audit_full.py',)

# ---- write-set scope ---------------------------------------------------------------------------------------------------------
ROOT_FILE_EXTENSIONS = ('.json', '.html', '.js', '.xlsx', '.csv', '.ndjson', '.jsonl', '.lastgood', '.candidate')
ROOT_FILE_SKIP = ('*.PRE-*', '*.pre_*', '*.BACKUP-*', '~$*')   # dated workbook backups the chain never rewrites; Excel lock files
COPY_TREES = ('data/engine', 'data/members', 'data/lobbying', 'data/network', 'data/district', 'data/reference', 'data/holistic',
              'data/money', 'data/legislation', 'data/stance', 'data/speech', 'data/press', 'data/staffer_network',
              'data/system', 'outputs/email_package')
EXTRA_FILES = ('backups/Moneyball_Redesign.previous-generation.html',)   # sync_live_dashboard_alias keeps the previous generation here
TRACK_ONLY = ('data/stance/rank_honors', 'data/legislation/vote_sources', 'data/legislation/congress_advanced_current',
              'data/legislation/sentinel_evidence', 'data/money/fec_generations', 'data/money/fec_page_cache',
              'data/speech/source_media_*')
NEVER = ('data/engine/chain_txn', 'data/engine/dead_letter', 'data/engine/source_freshness.json',
         'data/engine/congress_pipeline_*', 'data/engine/daily_refresh_*', 'data/engine/weekly_refresh*',
         'data/engine/gate_step_*', 'data/engine/collector_gate1_cooldown.json', 'data/system/audit_scratch',
         '*.log', '*.lock', '*.tmp', '*.pyc', '__pycache__')


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _stamp():
    """Sortable to the microsecond, so 'newest transaction' is well defined even for runs started in the same second."""
    now = datetime.now(timezone.utc)
    return now.strftime('%Y%m%dT%H%M%S') + '%06dZ' % now.microsecond


def _match(rel, pat):
    """chain_parity's matcher: a pattern without '/' matches any path segment; with '/' it matches the path or anything under it."""
    if '/' not in pat:
        return any(fnmatch.fnmatchcase(seg, pat) for seg in rel.split('/'))
    pat = pat.rstrip('/')
    return fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(rel, pat + '/*')


def _excluded(rel, patterns):
    return any(_match(rel, p) for p in patterns)


def _p(path):
    """Windows long-path form for the staging side (before-image paths nest deeper than the source paths)."""
    path = os.path.abspath(path)
    if os.name == 'nt' and not path.startswith('\\\\?\\'):
        return '\\\\?\\' + path
    return path


def _is_junction(path):
    return bool(getattr(os.path, 'isjunction', lambda _p: False)(path))


def iter_scope(root):
    """Yield (rel_posix, absolute_path, mode) for every file in the chain's LIVE write set; mode is 'copy' or 'track'."""
    root = os.path.abspath(root)
    staging = os.path.normcase(txn_base(root))     # an overridden staging dir inside a copy tree must never snapshot itself
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(root, name)
        if (name.lower().endswith(ROOT_FILE_EXTENSIONS) and not _excluded(name, ROOT_FILE_SKIP + NEVER)
                and os.path.isfile(path) and not os.path.islink(path)):
            yield name, path, 'copy'
    for tree in COPY_TREES:
        base = os.path.join(root, *tree.split('/'))
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, '/')
            dirnames[:] = sorted(d for d in dirnames
                                 if not _excluded(rel_dir + '/' + d, NEVER)
                                 and os.path.normcase(os.path.join(dirpath, d)) != staging
                                 and not os.path.islink(os.path.join(dirpath, d)) and not _is_junction(os.path.join(dirpath, d)))
            for name in sorted(filenames):
                rel = rel_dir + '/' + name
                path = os.path.join(dirpath, name)
                if _excluded(rel, NEVER) or os.path.islink(path):
                    continue
                yield rel, path, 'track' if _excluded(rel, TRACK_ONLY) else 'copy'
    for rel in EXTRA_FILES:
        path = os.path.join(root, *rel.split('/'))
        if os.path.isfile(path) and not os.path.islink(path):
            yield rel, path, 'copy'


def plan(root):
    """Read-only census of the write set: {'copy': [(rel, path, size, mtime_ns)], 'track': [...], 'copy_bytes': n}."""
    out = {'copy': [], 'track': [], 'copy_bytes': 0}
    for rel, path, mode in iter_scope(root):
        try:
            st = os.stat(path)
        except OSError:
            continue
        out[mode].append((rel, path, st.st_size, st.st_mtime_ns))
        if mode == 'copy':
            out['copy_bytes'] += st.st_size
    return out


# ---- journal i/o -------------------------------------------------------------------------------------------------------------

def _replace(src, dst):
    try:
        from lane_file_io import replace_atomic   # retried through Windows reader/OneDrive/scanner locks
        replace_atomic(src, dst)
    except ImportError:
        os.replace(src, dst)


def _write_json(path, doc):
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with open(_p(tmp), 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(doc, handle, indent=1, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    _replace(_p(tmp), _p(path))


def _copy_hash(src, dst):
    digest, size = hashlib.sha256(), 0
    os.makedirs(os.path.dirname(_p(dst)), exist_ok=True)
    with open(_p(src), 'rb') as source, open(_p(dst), 'wb') as target:
        for block in iter(lambda: source.read(BLOCK), b''):
            digest.update(block)
            target.write(block)
            size += len(block)
    return digest.hexdigest(), size


def _sha(path):
    digest = hashlib.sha256()
    with open(_p(path), 'rb') as handle:
        for block in iter(lambda: handle.read(BLOCK), b''):
            digest.update(block)
    return digest.hexdigest()


def _sha_or_none(path):
    try:
        return _sha(path)
    except OSError:
        return None


def _hash_many(paths):
    """{path: sha256, or None when unreadable} for every path, hashed on HASH_WORKERS threads (hashlib releases the GIL)."""
    from concurrent.futures import ThreadPoolExecutor
    paths = list(paths)
    if not paths:
        return {}
    with ThreadPoolExecutor(min(HASH_WORKERS, len(paths))) as pool:
        return dict(zip(paths, pool.map(_sha_or_none, paths)))


def _in_tree_base(root):
    return os.path.join(os.path.abspath(root), *TXN_RELATIVE.split('/'))


def txn_base(root):
    override = os.environ.get(TXN_DIR_ENV)
    if override:
        return os.path.abspath(override)
    data_root = os.path.abspath(os.environ.get(DATA_ROOT_ENV) or DEFAULT_DATA_ROOT)
    if os.path.isdir(data_root):
        base = os.path.join(data_root, 'chain_txn')
        try:
            os.makedirs(base, exist_ok=True)
            if os.access(base, os.W_OK):
                return base
        except OSError:
            pass
    return _in_tree_base(root)


def _recovery_bases(root):
    """The staging base plus the in-tree one, so a journal left by a run that staged in-tree (fallback, or before the default
    moved off OneDrive) is still found and finished."""
    bases = [txn_base(root)]
    legacy = _in_tree_base(root)
    if os.path.normcase(legacy) != os.path.normcase(bases[0]) and os.path.isdir(legacy):
        bases.append(legacy)
    return bases


def _read_journal(txn_dir):
    try:
        with open(_p(os.path.join(txn_dir, 'journal.json')), encoding='utf-8') as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get('schema') == SCHEMA else None


def _finalized_dirs(base):
    """Newest-first [(dir, journal)] for transactions that reached COMMITTED or ROLLED_BACK."""
    out = []
    try:
        names = sorted(os.listdir(base), reverse=True)
    except OSError:
        return out
    for name in names:
        path = os.path.join(base, name)
        journal = _read_journal(path) if os.path.isdir(path) else None
        if journal and journal.get('state') in (COMMITTED, ROLLED_BACK):
            out.append((path, journal))
    return out


def prune(base, keep=KEEP, log=print):
    """Delete finalized transactions older than the newest `keep`.  OPEN / incomplete ones are never touched here."""
    for path, _journal in _finalized_dirs(base)[max(keep, 0):]:
        try:
            shutil.rmtree(_p(path))
        except OSError as exc:
            log('  chain-txn: could not prune %s (%s)' % (os.path.basename(path), exc))


def _gate(base, copy_bytes, log):
    raw = os.environ.get(MIN_FREE_GB_ENV)
    floor_gb = MIN_FREE_GB if raw in (None, '') else float(raw)
    if floor_gb <= 0:
        return
    probe = base
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    free = shutil.disk_usage(probe).free
    left = free - copy_bytes
    if left < floor_gb * 1e9:
        drive = os.path.splitdrive(os.path.abspath(probe))[0] or os.path.abspath(probe)
        raise RuntimeError(
            'chain transaction refused to start: the before-image (%.2f GB) would leave %.2f GB free on %s, below the %.0f GB '
            'floor (free now %.2f GB). Free disk or move the staging area (%s) and rerun; nothing was written.'
            % (copy_bytes / 1e9, max(left, 0) / 1e9, drive, floor_gb, free / 1e9, TXN_DIR_ENV))


class ChainTxn:
    def __init__(self, root, txn_dir, journal, emit=None, emit_kw=None, log=print):
        self.root, self.dir, self.journal = os.path.abspath(root), txn_dir, journal
        self.run_id = journal.get('run_id')
        self.recovered = []
        self._emit, self._emit_kw, self._log = emit, emit_kw or {}, log

    @property
    def state(self):
        return self.journal.get('state')

    def _say(self, status, summary):
        self._log('  chain-txn: ' + summary)
        if self._emit:
            try:
                self._emit('pipeline.txn', status, summary, **self._emit_kw)
            except Exception:
                pass

    def _finish(self, state, **fields):
        final = dict(self.journal, state=state, finished_utc=_now(), **fields)
        _write_json(os.path.join(self.dir, 'journal.json'), final)
        self.journal = final

    def commit(self, note=None):
        if self.state != OPEN:
            return
        try:
            self._finish(COMMITTED, **({'note': note} if note else {}))
        except OSError as exc:
            # The chain SUCCEEDED; a journal that cannot be finalized must not make the next start revert it. Dropping the OPEN
            # journal turns this directory into an unfinished snapshot, which recover() discards without restoring anything.
            self._log('  chain-txn: WARNING could not finalize %s (%s); discarding its journal so the completed run stands'
                      % (self.run_id, exc))
            try:
                os.unlink(_p(os.path.join(self.dir, 'journal.json')))
            except OSError:
                self._log('  chain-txn: WARNING the OPEN journal could not be removed either; the next start WILL roll back %s'
                          % self.run_id)
            self.journal = dict(self.journal, state=COMMITTED)
            return
        prune(os.path.dirname(self.dir), KEEP, self._log)
        self._say('completed', 'committed %s (%d files journalled)%s' % (
            self.run_id, len(self.journal['files']), '; ' + note if note else ''))

    def rollback(self, reason):
        """Restore the before-image and PROVE it.  Returns True only when every journaled path hashes to its journalled sha256,
        no new file remains and no tracked file moved (state ROLLED_BACK, before-image dropped).  Otherwise the journal says
        ROLLBACK_FAILED, the before-image is kept, and RollbackFailed is raised: this is the one place a failure must be loud."""
        if self.state not in (OPEN, ROLLBACK_FAILED):
            return
        report = {'restored': [], 'removed': [], 'unrestorable_modified': [], 'unrestorable_missing': [], 'errors': []}
        verified = 0
        self._say('running', 'rolling back %s: %s' % (self.run_id, reason))
        files, tracked = self.journal['files'], self.journal['tracked']
        try:
            live = _hash_many(os.path.join(self.root, *rel.split('/')) for rel in files)
            for rel, meta in sorted(files.items()):
                path = os.path.join(self.root, *rel.split('/'))
                try:
                    if live[path] == meta['sha256']:             # bytes already the before-image's; size/mtime prove nothing
                        if os.stat(path).st_mtime_ns != meta['mtime_ns']:
                            os.utime(path, ns=(meta['mtime_ns'], meta['mtime_ns']))
                        verified += 1
                        continue
                    self._restore_one(rel, meta, path)
                    if _sha(path) != meta['sha256']:               # prove the restore landed
                        raise OSError('restored bytes do not match the journal')
                    report['restored'].append(rel)
                except Exception as exc:
                    report['errors'].append('%s: %s: %s' % (rel, type(exc).__name__, exc))
            known = set(files) | set(tracked)
            for rel, path, _mode in list(iter_scope(self.root)):
                if rel in known:
                    continue
                try:
                    os.unlink(path)
                    report['removed'].append(rel)
                except OSError as exc:
                    report['errors'].append('%s: could not delete new file (%s)' % (rel, exc))
            # tracked paths cannot be restored, so ANY difference is a failed rollback. A different size is proof of change (the cheap
            # pre-check); the same size and mtime are NOT proof of the same bytes, so those are hashed.
            same_size = {}
            for rel, meta in sorted(tracked.items()):
                path = os.path.join(self.root, *rel.split('/'))
                try:
                    size = os.stat(path).st_size
                except OSError:
                    report['unrestorable_missing'].append(rel)
                    continue
                if size != meta['size']:
                    report['unrestorable_modified'].append(rel)
                else:
                    same_size[rel] = path
            tracked_live = _hash_many(same_size.values())
            for rel, path in same_size.items():
                meta = tracked[rel]
                if tracked_live[path] != meta['sha256']:
                    report['unrestorable_modified'].append(rel)
                    continue
                try:
                    if os.stat(path).st_mtime_ns != meta['mtime_ns']:    # same bytes, touched: put the mtime back
                        os.utime(path, ns=(meta['mtime_ns'], meta['mtime_ns']))
                except OSError as exc:
                    report['errors'].append('%s: could not restore the modification time (%s)' % (rel, exc))
        except Exception as exc:                     # the restore loop itself failed: the tree is not proven
            report['errors'].append('rollback aborted: %s: %s' % (type(exc).__name__, exc))
        summary = {k: (v[:200] if isinstance(v, list) else v) for k, v in report.items()}
        summary['counts'] = {k: len(v) for k, v in report.items()}
        summary['counts']['verified_unchanged'] = verified
        proven = not (report['errors'] or report['unrestorable_modified'] or report['unrestorable_missing'])
        try:
            self._finish(ROLLED_BACK if proven else ROLLBACK_FAILED, reason=reason, rollback=summary)
        except Exception as exc:
            proven = False
            report['errors'].append('journal not updated after rollback: %s' % exc)
            self.journal = dict(self.journal, state=ROLLBACK_FAILED)
        if proven:
            try:
                shutil.rmtree(_p(os.path.join(self.dir, 'files')))   # every path verified: LIVE now equals it; keep the journal
            except OSError:
                pass
            prune(os.path.dirname(self.dir), KEEP, self._log)
        self._say('completed' if proven else 'failed',
                  '%s %s: restored %d, %d already identical, removed %d new, %d unrestorable modified, %d unrestorable missing, '
                  '%d errors' % ('ROLLED_BACK' if proven else ROLLBACK_FAILED, self.run_id, len(report['restored']), verified,
                                 len(report['removed']), len(report['unrestorable_modified']),
                                 len(report['unrestorable_missing']), len(report['errors'])))
        if proven:
            return True
        detail = (report['errors'][:5] + ['unrestorable modified: ' + r for r in report['unrestorable_modified'][:5]]
                  + ['unrestorable missing: ' + r for r in report['unrestorable_missing'][:5]])
        for line in detail:
            self._log('    ' + line)
        raise RollbackFailed(
            'chain transaction %s is %s: LIVE is NOT proven restored (%s). The before-image is kept in %s; no further chain run '
            'will start until this is resolved -- repair the listed paths by hand from <that directory>/files, then delete the '
            'directory.' % (self.run_id, ROLLBACK_FAILED, '; '.join(detail) or 'see the journal', self.dir))

    def _restore_one(self, rel, meta, path):
        source = os.path.join(self.dir, 'files', *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = '%s.%d.rollback.tmp' % (path, os.getpid())
        try:
            sha, _size = _copy_hash(source, tmp)             # copy AND hash the before-image bytes
            if sha != meta['sha256']:                        # a damaged before-image must never replace live bytes
                raise OSError('the before-image copy is damaged (sha256 mismatch); live bytes left untouched')
            os.utime(tmp, ns=(meta['mtime_ns'], meta['mtime_ns']))
            _replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def begin(root, *, run_id=None, argv=None, emit=None, emit_kw=None, log=print):
    """Snapshot the write set and return an OPEN ChainTxn.  Raises RuntimeError (nothing written to LIVE) if it cannot."""
    root = os.path.abspath(root)
    base = txn_base(root)
    os.makedirs(base, exist_ok=True)
    prune(base, KEEP - 1, log)                      # make room: the newest finalized one + this one survive
    census = plan(root)
    _gate(base, census['copy_bytes'], log)
    stamp = _stamp()
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', run_id or '')[-24:] or uuid.uuid4().hex[:12]
    txn_dir = os.path.join(base, '%s-%s' % (stamp, safe))
    while os.path.exists(txn_dir):
        txn_dir += 'x'
    os.makedirs(_p(os.path.join(txn_dir, 'files')))
    t0, files, tracked = time.time(), {}, {}
    log('  chain-txn: snapshot of %d files (%.2f GB) + %d tracked (%.2f GB hashed, not copied) -> %s' % (
        len(census['copy']), census['copy_bytes'] / 1e9, len(census['track']),
        sum(size for _r, _p, size, _m in census['track']) / 1e9, os.path.relpath(txn_dir, root)))
    try:
        for rel, path, _size, _mtime in census['copy']:
            for attempt in (1, 2, 3):
                before = os.stat(path)
                sha, size = _copy_hash(path, os.path.join(txn_dir, 'files', *rel.split('/')))
                after = os.stat(path)
                if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and size == after.st_size:
                    break
                if attempt == 3:
                    raise RuntimeError('chain transaction refused to start: %s kept changing during the snapshot' % rel)
            copied = _p(os.path.join(txn_dir, 'files', *rel.split('/')))
            os.utime(copied, ns=(before.st_mtime_ns, before.st_mtime_ns))
            files[rel] = {'sha256': sha, 'size': size, 'mtime_ns': before.st_mtime_ns, 'existed': True}
        # tracked paths are hashed, not copied: rollback cannot restore them, only prove them
        stats = {path: os.stat(path) for _rel, path, _size, _mtime in census['track']}
        shas = _hash_many(stats)
        for rel, path, _size, _mtime in census['track']:
            before, sha = stats[path], shas[path]
            for attempt in (1, 2, 3):
                after = os.stat(path)
                if sha is not None and (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                    break
                if attempt == 3:
                    raise RuntimeError('chain transaction refused to start: %s kept changing (or is unreadable) during the '
                                       'snapshot' % rel)
                before, sha = os.stat(path), _sha_or_none(path)
            tracked[rel] = {'sha256': sha, 'size': before.st_size, 'mtime_ns': before.st_mtime_ns}
    except OSError as exc:
        shutil.rmtree(_p(txn_dir), ignore_errors=True)
        raise RuntimeError('chain transaction refused to start: cannot snapshot the write set (%s: %s)' % (
            type(exc).__name__, exc)) from exc
    except BaseException:
        shutil.rmtree(_p(txn_dir), ignore_errors=True)
        raise
    journal = {'schema': SCHEMA, 'state': OPEN, 'run_id': run_id or safe, 'started_utc': _now(), 'root': root,
               'pid': os.getpid(), 'argv': list(argv or []), 'files': files,
               'tracked': tracked,
               'snapshot_seconds': round(time.time() - t0, 2)}
    _write_json(os.path.join(txn_dir, 'journal.json'), journal)   # the commit point of the snapshot
    txn = ChainTxn(root, txn_dir, journal, emit, emit_kw, log)
    txn._say('running', 'opened %s: %d files, %.2f GB in %.1fs' % (
        txn.run_id, len(files), census['copy_bytes'] / 1e9, journal['snapshot_seconds']))
    return txn


def recover(root, *, emit=None, emit_kw=None, log=print):
    """Under the lease, before a new run: finish what a dead run left.  Returns the recovered run ids; raises RollbackFailed if a
    transaction cannot be proven rolled back (LIVE is then in an unknown state and the run must not start)."""
    recovered, paths = [], []
    for base in _recovery_bases(root):
        try:
            paths.extend(os.path.join(base, name) for name in sorted(os.listdir(base)))
        except OSError:
            continue
    for path in paths:
        name = os.path.basename(path)
        if not os.path.isdir(path):
            continue
        journal = _read_journal(path)
        if journal is None:                          # snapshot never finished: LIVE was never touched
            shutil.rmtree(_p(path), ignore_errors=True)
            log('  chain-txn: removed an unfinished snapshot (%s)' % name)
            continue
        if journal.get('state') in (OPEN, ROLLBACK_FAILED):
            if os.path.normcase(str(journal.get('root'))) != os.path.normcase(os.path.abspath(root)):
                log('  chain-txn: %s belongs to another root (%s); left alone' % (name, journal.get('root')))
                continue                             # a shared MELANGE_CHAIN_TXN_DIR must never restore bytes into the wrong tree
            txn = ChainTxn(root, path, journal, emit, emit_kw, log)
            # RollbackFailed propagates: LIVE is in an unknown state and the run must not start (a ROLLBACK_FAILED journal that
            # still cannot be proven on this retry keeps refusing every start until an operator resolves it)
            txn.rollback('recovered: the previous run (pid %s) never finalized its transaction' % journal.get('pid')
                         if journal.get('state') == OPEN else 'retry: the previous rollback was not proven')
            recovered.append(journal.get('run_id'))
    return recovered


class transaction:
    """`with pipeline_run_lock(), transaction(root, ...) as txn:` -- recover, snapshot, and roll back on any exit that is not
    an explicit txn.commit() (a `return 1`, an exception, KeyboardInterrupt, SystemExit)."""

    def __init__(self, root, *, run_id=None, argv=None, emit=None, emit_kw=None, log=print):
        self._a = (root,)
        self._kw = dict(run_id=run_id, argv=argv, emit=emit, emit_kw=emit_kw, log=log)
        self.txn = None

    def __enter__(self):
        recovered = recover(self._a[0], emit=self._kw['emit'], emit_kw=self._kw['emit_kw'], log=self._kw['log'])
        self.txn = begin(*self._a, **self._kw)
        self.txn.recovered = recovered
        return self.txn

    def __exit__(self, exc_type, exc, tb):
        if self.txn is not None and self.txn.state == OPEN:
            self.txn.rollback('%s: %s' % (exc_type.__name__, str(exc)[:120]) if exc_type else 'the chain did not complete')
        return False


# ---- labelled degrade --------------------------------------------------------------------------------------------------------

FRESH, STALE_LABELLED = 'FRESH', 'STALE_LABELLED'
FRESHNESS_RELATIVE = 'data/engine/source_freshness.json'
CLOCK_SKEW_SECONDS = 300
_NOTED = {}


def age_status(age_seconds, window_days, refusal):
    """FRESH inside the window; STALE_LABELLED past it.  A stamp from the FUTURE (beyond clock skew) is a clock fault, not
    staleness, and keeps raising ValueError(refusal)."""
    if age_seconds < -CLOCK_SKEW_SECONDS:
        raise ValueError(refusal)
    return FRESH if age_seconds <= window_days * 86400 else STALE_LABELLED


def note_freshness(root, source, status, *, checked_at, window_days, producer, log=print):
    """Label a source's freshness.  Prints the STALE_LABELLED line once per process, and merges {source: entry} into
    data/engine/source_freshness.json when that file exists (another phase owns and creates it; absent or unparseable -> no write).
    Read-modify-atomic-write, and only when (status, checked_at) changed, so hot loaders do not write on every call.  Never raises."""
    try:
        key = (os.path.abspath(str(root)), source)
        if _NOTED.get(key) == (status, checked_at):
            return False
        _NOTED[key] = (status, checked_at)
        if status == STALE_LABELLED:
            log('  %s: %s last verified %s, past its %d-day window; proceeding on last-good (labelled, not refused)'
                % (STALE_LABELLED, source, checked_at, window_days))
        path = os.path.join(os.path.abspath(str(root)), *FRESHNESS_RELATIVE.split('/'))
        if not os.path.isfile(path):
            return False
        with open(path, encoding='utf-8') as handle:
            doc = json.load(handle)
        if not isinstance(doc, dict):
            return False
        holder = doc['sources'] if isinstance(doc.get('sources'), dict) else doc
        old = holder.get(source)
        if isinstance(old, dict) and old.get('status') == status and old.get('checked_at') == checked_at:
            return True
        holder[source] = {'status': status, 'checked_at': checked_at, 'window_days': window_days, 'producer': producer,
                          'evaluated_utc': _now()}
        tmp = '%s.%d.tmp' % (path, os.getpid())
        with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(doc, handle, indent=2)
            handle.write('\n')
        _replace(tmp, path)
        return True
    except Exception:
        return False
