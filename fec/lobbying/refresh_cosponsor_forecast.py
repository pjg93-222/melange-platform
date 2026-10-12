# -*- coding: utf-8 -*-
"""Hash-gated refresh of the cosponsorship forecast — dial-gated, chain-safe.

WHY THIS EXISTS.  ``fec/lobbying/cosponsor_forecast.py`` is a ridge-logistic
cosponsorship forecaster whose outputs (``cosponsor_forecast_report.json`` and the
165 MB ``cosponsor_scores.csv``) are read by ``bill_lobbying_sidecar.py``.  It has
never been part of the chain, so its scores have been frozen at their 2026-08-01
BILLSTATUS vintage while the sidecar kept re-reading them as if they were current.
This step closes that loop WITHOUT paying 16 minutes on every chain run.

THE GATE.  The forecaster already records, in
``provenance.every_input_sha256``, one ``{path, sha256, roles}`` record per input
file it read (cosponsor_forecast.py:3191-3199, fed by ``_record_input`` at
:411-421 and ``_sha256_file`` at :400-408, which returns UPPERCASE hex).  That
list IS the freshness contract: if every listed path still hashes to the recorded
value, the forecaster provably cannot produce different output, so re-running it
would burn 16 minutes to rewrite a byte-identical CSV.  The gate re-hashes that
list and runs only when something actually moved.

The list alone is not sufficient, though, and the extra rule matters: a NEW
BILLSTATUS archive appearing on disk is invisible to a hash check over the OLD
report's paths, because a file that was never read has no recorded hash to differ
from.  ``compare_inputs`` therefore also globs the billstatus directories the
report names and treats any unlisted ``BILLSTATUS-*.zip`` as a change.  (Congress
116 legitimately ships only hr/s, which is why the rule compares against the
report's own archive list rather than against an expected count.)

ONE COMPARISON, TWO CALLERS.  ``compare_inputs`` is the only place this logic
exists.  The chain gate below and ``audit_full.py``'s ``cosponsor_forecast_freshness``
check both call it, so the audit cannot disagree with the thing it audits — a
divergence that would otherwise be invisible until a stale artifact shipped.

DIAL-DEFAULT-INERT.  ``COSPONSOR_FORECAST_REFRESH`` unset or ``0`` writes a
``SKIPPED_DIAL_OFF`` receipt and returns, so adding this to the chain reproduces
today's production behaviour exactly.  Paul flips the dial.

NETWORK-FREE.  The forecaster reads local files only, so this step is lawful
inside run_pipeline.py's network-free chain.  It is invoked as a SUBPROCESS
(never imported): importing a fec producer executes it.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys  # noqa: E401
_bootstrap_sys.path.insert(
    0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.abspath(__file__))))

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from paths import ROOT as MB_ROOT
from lane_file_io import atomic_write_bytes

try:
    from surfaces.glassbox_emit import emit as _gb, new_run_id as _gb_run
except Exception:  # pragma: no cover - the stream is optional, the refresh is not
    def _gb(*_a, **_k): return None
    def _gb_run(prefix='moneyball'): return ''

SCHEMA_VERSION = 1
DIAL = 'COSPONSOR_FORECAST_REFRESH'
REPORT_NAME = 'cosponsor_forecast_report.json'
SCORES_NAME = 'cosponsor_scores.csv'
_STREAM_CHUNK = 1024 * 1024


def default_out_dir():
    """Production out-dir — what bill_lobbying_sidecar.py reads as {data_root}/cosponsor_sc/."""
    local = os.environ.get('LOCALAPPDATA') or os.path.expanduser(r'~\AppData\Local')
    return os.path.join(local, 'Melange', 'lda_rebuild', 'derived', 'cosponsor_sc')


def default_forecaster_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cosponsor_forecast.py')


def default_receipt_path():
    return os.path.join(MB_ROOT, 'data', 'engine', 'cosponsor_forecast_refresh_receipt.json')


def default_log_path():
    return os.path.join(MB_ROOT, 'data', 'engine', 'cosponsor_forecast_refresh.log')


def report_path_for(out_dir):
    return os.path.join(out_dir, REPORT_NAME)


def load_report(out_dir):
    """Return the parsed report, or None when it is absent/unreadable."""
    path = report_path_for(out_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def sha256_file(path):
    """Uppercase hex, matching cosponsor_forecast.py:400-408.

    Comparisons against it are still made case-insensitively: the report is the
    forecaster's word, and a future change to its hash helper's case must not be
    able to fake a freshness failure.
    """
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(_STREAM_CHUNK), b''):
            digest.update(block)
    return digest.hexdigest().upper()


def normalize_inputs(report):
    """Normalize ``provenance.every_input_sha256`` to an ordered {path: sha256} dict.

    The shipped shape is a LIST of ``{"path", "sha256", "roles"}`` records sorted
    by path (cosponsor_forecast.py:3191-3199).  A plain ``{path: sha256}`` mapping
    is also accepted so a future shape change degrades into a working gate rather
    than a silent empty one.
    """
    provenance = (report or {}).get('provenance') or {}
    raw = provenance.get('every_input_sha256')
    inputs = {}
    if isinstance(raw, dict):
        for path, sha in raw.items():
            if isinstance(path, str) and isinstance(sha, str):
                inputs[path] = sha
    elif isinstance(raw, list):
        for record in raw:
            if isinstance(record, dict):
                path, sha = record.get('path'), record.get('sha256')
                if isinstance(path, str) and isinstance(sha, str):
                    inputs[path] = sha
    return inputs


def billstatus_dirs(report):
    """Directories holding the BILLSTATUS archives the report actually read."""
    provenance = (report or {}).get('provenance') or {}
    dirs = []
    seen = set()
    for entry in provenance.get('billstatus') or []:
        if not isinstance(entry, dict):
            continue
        for archive in entry.get('archives') or []:
            if not isinstance(archive, dict):
                continue
            path = archive.get('path')
            if not isinstance(path, str):
                continue
            directory = os.path.dirname(os.path.abspath(path))
            key = os.path.normcase(directory)
            if key not in seen:
                seen.add(key)
                dirs.append(directory)
    return dirs


def compare_inputs(report):
    """THE shared freshness comparison. Used by the chain gate AND audit_full.py.

    Returns a dict with:
      inputs    [{path, report_sha256, disk_sha256, changed}] — one per recorded input
      changed   paths whose bytes moved
      missing   paths the report recorded that are gone from disk
      new_zips  BILLSTATUS-*.zip present on disk but absent from the report
      agrees    True only when all three lists are empty
    A report of ``None`` yields ``agrees=False`` with an explicit reason, so an
    absent report can never read as "nothing to do".
    """
    if report is None:
        return {'inputs': [], 'changed': [], 'missing': [], 'new_zips': [],
                'billstatus_dirs': [], 'agrees': False, 'report_present': False,
                'reasons': ['report missing']}

    recorded = normalize_inputs(report)
    inputs, changed, missing = [], [], []
    for path, report_sha in recorded.items():
        if os.path.exists(path):
            try:
                disk_sha = sha256_file(path)
            except OSError as exc:
                disk_sha = 'UNREADABLE: %s' % exc
        else:
            disk_sha = None
        row = {
            'path': path,
            'report_sha256': report_sha,
            'disk_sha256': disk_sha,
            'changed': disk_sha is None or disk_sha.lower() != report_sha.lower(),
        }
        inputs.append(row)
        if disk_sha is None:
            missing.append(path)
        elif row['changed']:
            changed.append(path)

    listed = {os.path.normcase(os.path.abspath(path)) for path in recorded}
    dirs = billstatus_dirs(report)
    new_zips = []
    for directory in dirs:
        for found in sorted(glob.glob(os.path.join(directory, 'BILLSTATUS-*.zip'))):
            if os.path.normcase(os.path.abspath(found)) not in listed:
                new_zips.append(os.path.abspath(found))

    reasons = []
    if missing:
        reasons.append('%d recorded input(s) missing from disk' % len(missing))
    if changed:
        reasons.append('%d input hash(es) differ' % len(changed))
    if new_zips:
        reasons.append('%d BILLSTATUS archive(s) on disk are absent from the report' % len(new_zips))
    return {
        'inputs': inputs,
        'changed': changed,
        'missing': missing,
        'new_zips': new_zips,
        'billstatus_dirs': dirs,
        'agrees': not (changed or missing or new_zips),
        'report_present': True,
        'reasons': reasons,
    }


def freshness_detail(comparison, limit=5):
    """First ``limit`` disagreements, for an audit failure line."""
    if not comparison.get('report_present'):
        return ('cosponsor_forecast_report.json is absent — the forecast scores the '
                'bill-lobbying sidecar reads have no provenance to verify')
    items = ([('missing', path) for path in comparison['missing']]
             + [('changed', path) for path in comparison['changed']]
             + [('unlisted-archive', path) for path in comparison['new_zips']])
    if not items:
        return ''
    head = '; '.join('%s %s' % (kind, path) for kind, path in items[:limit])
    more = len(items) - limit
    return head + (' (+%d more)' % more if more > 0 else '')


def _write_receipt(receipt_path, payload):
    atomic_write_bytes(
        Path(receipt_path),
        (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8'),
    )


def _utc(seconds=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ',
                         time.gmtime(time.time() if seconds is None else seconds))


def dial_is_on(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(DIAL, '0')).strip() == '1'


def _default_runner(cmd, log_path):
    """Run the forecaster, tee-ing its output to ``log_path``. Returns the exit code."""
    completed = subprocess.run(cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write('$ %s\n' % ' '.join(cmd))
            handle.write('--- stdout ---\n%s\n' % (completed.stdout or ''))
            handle.write('--- stderr ---\n%s\n' % (completed.stderr or ''))
    except OSError:
        pass
    return completed.returncode


def _promote(staging, out_dir):
    """Publish staging outputs onto production names, undoably.

    Both production files are first moved aside to ``*.previous``; only when BOTH
    replaces have succeeded are those removed.  Any failure restores production to
    exactly the bytes it had.
    """
    prod_report = os.path.join(out_dir, REPORT_NAME)
    prod_scores = os.path.join(out_dir, SCORES_NAME)
    stage_report = os.path.join(staging, REPORT_NAME)
    stage_scores = os.path.join(staging, SCORES_NAME)
    prev_report, prev_scores = prod_report + '.previous', prod_scores + '.previous'
    saved = []
    try:
        for live, previous in ((prod_report, prev_report), (prod_scores, prev_scores)):
            if os.path.exists(live):
                os.replace(live, previous)
                saved.append((live, previous))
        os.replace(stage_report, prod_report)
        os.replace(stage_scores, prod_scores)
    except Exception:
        # Undo: drop anything we managed to publish, then move the saved bytes back.
        for live, previous in saved:
            try:
                if os.path.exists(live):
                    os.unlink(live)
            except OSError:
                pass
            try:
                os.replace(previous, live)
            except OSError:
                pass
        raise
    for _live, previous in saved:
        try:
            os.unlink(previous)
        except OSError:
            pass
    return prod_report, prod_scores


def _rmtree(path):
    try:
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                try:
                    os.unlink(os.path.join(root, name))
                except OSError:
                    pass
            for name in dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except OSError:
                    pass
        os.rmdir(path)
    except OSError:
        pass


def run(argv=None, runner=None, environ=None):
    """Return a process exit code.  ``runner`` is injectable so tests never run the forecaster."""
    parser = argparse.ArgumentParser(
        description='Re-run the cosponsorship forecaster only when its inputs moved.')
    parser.add_argument('--out-dir', default=None)
    parser.add_argument('--forecaster', default=None)
    parser.add_argument('--receipt', default=None)
    parser.add_argument('--log', default=None)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)

    environ = os.environ if environ is None else environ
    out_dir = args.out_dir or default_out_dir()
    forecaster = args.forecaster or default_forecaster_path()
    receipt_path = args.receipt or default_receipt_path()
    log_path = args.log or default_log_path()
    started = time.time()
    run_id = _gb_run('cosponsor-forecast-refresh')

    base = {
        'schema_version': SCHEMA_VERSION,
        'report_path': report_path_for(out_dir),
        'started_utc': _utc(started),
        'inputs': [],
        'new_zips': [],
        'decided_because': [],
        'elapsed_seconds': 0.0,
        'forecaster_returncode': None,
        'log_path': None,
        'outputs': {},
        'error': None,
    }

    def finish(status, **fields):
        payload = dict(base)
        payload.update(fields)
        payload['status'] = status
        payload['finished_utc'] = _utc()
        payload['elapsed_seconds'] = round(time.time() - started, 3)
        _write_receipt(receipt_path, payload)
        return payload

    if not dial_is_on(environ):
        finish('SKIPPED_DIAL_OFF', decided_because=['dial %s is not 1' % DIAL])
        print('cosponsor forecast refresh SKIPPED — %s is not 1 (receipt: %s)'
              % (DIAL, receipt_path))
        return 0

    _gb('cosponsor.forecast_refresh', 'running', 'evaluating forecast input hashes',
        run_id=run_id, source='cosponsor-forecast-refresh', stage='gate')

    report = load_report(out_dir)
    comparison = compare_inputs(report)
    reasons = list(comparison['reasons'])
    if args.force:
        reasons.append('--force')
    should_run = bool(reasons)

    _gb('cosponsor.forecast_refresh', 'running',
        ('inputs moved: %s' % '; '.join(reasons)) if should_run else 'inputs unchanged',
        run_id=run_id, source='cosponsor-forecast-refresh', stage='decision')

    if not should_run:
        finish('SKIPPED_UNCHANGED',
               decided_because=['every recorded input hash still matches disk'],
               inputs=comparison['inputs'], new_zips=comparison['new_zips'])
        print('cosponsor forecast refresh SKIPPED_UNCHANGED — %d inputs verified'
              % len(comparison['inputs']))
        _gb('cosponsor.forecast_refresh', 'completed',
            'unchanged; forecaster not run (%d inputs verified)' % len(comparison['inputs']),
            run_id=run_id, source='cosponsor-forecast-refresh',
            duration_ms=int((time.time() - started) * 1000))
        return 0

    staging = os.path.join(out_dir, '.staging-%s-%d'
                           % (time.strftime('%Y%m%dT%H%M%S', time.gmtime(started)), os.getpid()))
    error = None
    returncode = None
    try:
        os.makedirs(staging, exist_ok=True)
        cmd = [sys.executable, forecaster, '--same-chamber-only', '--out-dir', staging]
        invoke = runner if runner is not None else _default_runner
        returncode = invoke(cmd, log_path)
        if returncode != 0:
            error = 'forecaster exited %s' % returncode
        else:
            for name in (REPORT_NAME, SCORES_NAME):
                if not os.path.exists(os.path.join(staging, name)):
                    error = 'forecaster exited 0 but did not write %s' % name
                    break
        if error is None:
            prod_report, prod_scores = _promote(staging, out_dir)
    except Exception as exc:                     # noqa: BLE001 - every failure is a FAILED receipt
        error = '%s: %s' % (type(exc).__name__, exc)

    if error is not None:
        _rmtree(staging)
        finish('FAILED', decided_because=reasons, inputs=comparison['inputs'],
               new_zips=comparison['new_zips'], forecaster_returncode=returncode,
               log_path=log_path, error=error)
        print('cosponsor forecast refresh FAILED — %s' % error, file=sys.stderr)
        print('production report/scores left untouched (receipt: %s)' % receipt_path,
              file=sys.stderr)
        _gb('cosponsor.forecast_refresh', 'failed', error[:200], run_id=run_id,
            source='cosponsor-forecast-refresh',
            duration_ms=int((time.time() - started) * 1000))
        return 1

    _rmtree(staging)
    outputs = {}
    for key, path in (('report', prod_report), ('scores', prod_scores)):
        outputs[key] = {'bytes': os.path.getsize(path), 'sha256': sha256_file(path)}
    finish('RAN', decided_because=reasons, inputs=comparison['inputs'],
           new_zips=comparison['new_zips'], forecaster_returncode=returncode,
           log_path=log_path, outputs=outputs)
    print('cosponsor forecast refresh RAN — %s' % '; '.join(reasons))
    _gb('cosponsor.forecast_refresh', 'completed',
        'forecast rebuilt and promoted (%s)' % '; '.join(reasons), run_id=run_id,
        source='cosponsor-forecast-refresh',
        duration_ms=int((time.time() - started) * 1000))
    return 0


def main(argv=None):
    return run(argv)


if __name__ == '__main__':
    sys.exit(main())
