# -*- coding: utf-8 -*-
"""Weekly GovInfo BILLSTATUS bulk refresh — dial-gated, all-or-nothing.

WHY THIS EXISTS.  The BILLSTATUS bulk archives under
``%LOCALAPPDATA%\\Melange\\lda_rebuild\\raw\\billstatus{N}\\`` are the cosponsorship
forecaster's largest input, and they have been frozen at their 2026-08-01 vintage
because nothing in the weekly refresh ever re-downloads them.  This producer is
that missing leg.

THE THREE LAWS THIS PRODUCER OBEYS.

1. DIAL-DEFAULT-INERT.  ``BILLSTATUS_BULK_REFRESH`` unset or ``0`` writes a
   ``SKIPPED_DIAL_OFF`` receipt and touches nothing else, so wiring it into the
   weekly refresh reproduces today's production behaviour byte for byte.  Paul
   flips the dial; the code does not decide for him.

2. ALL-OR-NOTHING.  Every archive is downloaded to a temp file *inside the target
   directory*, proven to be a readable zip carrying at least one ``.xml`` member,
   and only then — after ALL of them pass — published with ``os.replace``.  A
   single bad archive leaves every existing zip exactly as it was.  The forecaster
   hashes these files into its provenance and asserts each archive's vintage from
   the archive's own records; a half-refreshed directory would be a mixed-vintage
   input set, which is worse than a stale one.

3. RECEIPT ALWAYS.  A run writes a receipt whether it ran, skipped or failed.  A
   step that can finish silently is a step nobody can audit.

The sidecar ``BILLSTATUS-{congress}.receipt.json`` is written beside the zips so
the forecaster's ``provenance.billstatus[*].archives[*].sha256`` can be
cross-checked against what this producer actually published, from the data side,
without reading the repo.

NETWORK.  This producer DOES reach the network and therefore belongs to
weekly_refresh.py, never to run_pipeline.py — the chain's network-free law holds.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys  # noqa: E401
_bootstrap_sys.path.insert(
    0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from paths import ROOT as MB_ROOT
from lane_file_io import atomic_write_bytes

# Glass Box: every process emits to the shared stream or it is not acceptable.
# Emission is best-effort by design -- a lost event is a lost dashboard line,
# never a lost refresh.
try:
    from surfaces.glassbox_emit import emit as _gb, new_run_id as _gb_run
except Exception:  # pragma: no cover - the stream is optional, the refresh is not
    def _gb(*_a, **_k): return None
    def _gb_run(prefix='moneyball'): return ''

SCHEMA_VERSION = 1
DIAL = 'BILLSTATUS_BULK_REFRESH'
OFFICIAL_BASE = 'https://www.govinfo.gov/bulkdata/BILLSTATUS'
DEFAULT_CONGRESS = 119
# The 119 archive set the forecaster loads (fec/lobbying/cosponsor_forecast.py
# load_billstatus -> builder.ZIP_TYPES).  116 legitimately ships only hr/s.
DEFAULT_TYPES = ('hr', 's', 'hjres', 'sjres', 'hconres', 'sconres', 'hres', 'sres')
# The largest 119 archive today is BILLSTATUS-119-hr.zip at 30 MB.  This ceiling
# exists to bound a hostile or broken response, not to predict growth.
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_STREAM_CHUNK = 1024 * 1024


class RefreshError(RuntimeError):
    """Raised when refresh evidence fails closed."""


def _require(condition, message):
    if not condition:
        raise RefreshError(message)


def default_raw_root():
    local = os.environ.get('LOCALAPPDATA') or os.path.expanduser(r'~\AppData\Local')
    return os.path.join(local, 'Melange', 'lda_rebuild', 'raw')


def default_receipt_path():
    return os.path.join(MB_ROOT, 'data', 'engine', 'billstatus_bulk_refresh_receipt.json')


def archive_url(congress, type_token):
    return '%s/%d/%s/BILLSTATUS-%d-%s.zip' % (
        OFFICIAL_BASE, int(congress), type_token, int(congress), type_token)


def archive_name(congress, type_token):
    return 'BILLSTATUS-%d-%s.zip' % (int(congress), type_token)


def sha256_file(path):
    """Uppercase hex, matching cosponsor_forecast.py:400-408 ``_sha256_file``.

    Keeping the case identical means a receipt sha and a forecaster provenance
    sha can be compared as strings without a normalization step that could
    silently paper over a real difference.
    """
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(_STREAM_CHUNK), b''):
            digest.update(block)
    return digest.hexdigest().upper()


# --------------------------------------------------------------------------------------
# Official-source transport.  The URL pinning, redirect refusal and status assertion below
# are COPIED from fec/legislation/run_billstatus_bulk_enrichment.py:80-142
# (``official_url`` / ``_NoRedirect`` / ``network_fetcher``) rather than imported, for two
# reasons that are worth stating because "reuse it" was the obvious first instinct:
#   * that fetcher buffers the WHOLE body in memory and caps any URL containing
#     '/BILLSTATUS-' at stage_billstatus_bulk_enrichment.MAX_XML_BYTES (64 MB).  It is
#     built for single XML records; these are multi-megabyte archives that only grow, so
#     a 64 MB ceiling is a scheduled future failure and the buffering is pointless.
#   * it advertises ``Accept: application/xml`` — wrong for a zip.
# The security properties are the point of the copy and are preserved exactly: HTTPS only,
# hostname pinned to www.govinfo.gov, no credentials in the URL, no redirects, HTTP 200
# only, and a hard byte ceiling.
# --------------------------------------------------------------------------------------
def official_url(url):
    parsed = urlparse(url)
    _require(
        parsed.scheme == 'https'
        and parsed.hostname == 'www.govinfo.gov'
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443),
        'non-official URL refused: %s' % url,
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RefreshError('redirect refused: %s -> %s' % (req.full_url, newurl))


def network_fetcher(timeout_seconds):
    """Return ``fetch(url, dest)`` streaming an official archive to ``dest``."""
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )

    def fetch(url, dest):
        official_url(url)
        request = urllib.request.Request(
            url,
            headers={
                'Accept': 'application/zip,application/octet-stream',
                'User-Agent': ('MelangeSystems-BILLSTATUS-BulkRefresh/1.0 '
                               '(official GovInfo bulk archive refresh)'),
            },
        )
        written = 0
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                final_url = response.geturl()
                official_url(final_url)
                _require(final_url == url, 'redirect/final URL drift: %s' % url)
                status = int(getattr(response, 'status', 0))
                _require(status == 200, 'unexpected HTTP status %s: %s' % (status, url))
                with open(dest, 'wb') as handle:
                    while True:
                        chunk = response.read(_STREAM_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        _require(written <= MAX_ARCHIVE_BYTES,
                                 'oversize official archive (> %d bytes): %s'
                                 % (MAX_ARCHIVE_BYTES, url))
                        handle.write(chunk)
        except urllib.error.URLError as error:
            raise RefreshError('official fetch failed: %s: %s' % (url, error)) from error
        _require(written > 0, 'empty official archive: %s' % url)
        return written

    return fetch


def verify_archive(path):
    """Prove the bytes on disk are a readable zip carrying BILLSTATUS XML.

    ``testzip()`` reads and CRC-checks every member, so a truncated or corrupted
    download cannot reach production under a valid-looking name.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            _require(archive.testzip() is None, 'zip CRC failure: %s' % path)
            names = archive.namelist()
    except RefreshError:
        raise
    except Exception as error:
        raise RefreshError('unreadable archive %s: %s' % (path, error)) from error
    xml_members = sum(1 for name in names if name.lower().endswith('.xml'))
    _require(xml_members > 0, 'archive carries no .xml member: %s' % path)
    return xml_members


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


def run(argv=None, fetch=None, environ=None):
    """Return a process exit code.  ``fetch`` is injectable so tests never hit the network."""
    parser = argparse.ArgumentParser(
        description='Refresh the GovInfo BILLSTATUS bulk archives (all-or-nothing).')
    parser.add_argument('--congress', type=int, default=DEFAULT_CONGRESS)
    parser.add_argument('--types', nargs='+', default=list(DEFAULT_TYPES))
    parser.add_argument('--raw-root', default=None)
    parser.add_argument('--receipt', default=None)
    parser.add_argument('--timeout', type=float, default=900.0)
    args = parser.parse_args(argv)

    environ = os.environ if environ is None else environ
    receipt_path = args.receipt or default_receipt_path()
    raw_root = args.raw_root or default_raw_root()
    congress = int(args.congress)
    started = time.time()
    run_id = _gb_run('billstatus-bulk-refresh')

    if not dial_is_on(environ):
        _write_receipt(receipt_path, {
            'schema_version': SCHEMA_VERSION,
            'status': 'SKIPPED_DIAL_OFF',
            'congress': congress,
            'started_utc': _utc(started),
            'finished_utc': _utc(),
            'elapsed_seconds': round(time.time() - started, 3),
            'archives': [],
            'any_changed': False,
            'error': None,
        })
        print('BILLSTATUS bulk refresh SKIPPED — %s is not 1 (receipt: %s)' % (DIAL, receipt_path))
        return 0

    _gb('billstatus.bulk_refresh', 'running',
        'refreshing %d BILLSTATUS-%d archives' % (len(args.types), congress),
        run_id=run_id, source='billstatus-bulk-refresh', stage='download')

    directory = os.path.join(raw_root, 'billstatus%d' % congress)
    staged = []          # entries that passed verification, in publication order
    # Every temp path is recorded the MOMENT it is named, not once it verifies. The
    # cleanup below iterates this list rather than `staged`, because the temp file that
    # matters most on a failure is the one belonging to the archive that just failed --
    # and that one never reaches `staged`.
    temps = []
    error = None
    try:
        os.makedirs(directory, exist_ok=True)
        fetcher = fetch if fetch is not None else network_fetcher(args.timeout)
        for index, type_token in enumerate(args.types):
            url = archive_url(congress, type_token)
            final_path = os.path.join(directory, archive_name(congress, type_token))
            tmp_path = os.path.join(
                directory, '.%s.tmp-%d-%d' % (archive_name(congress, type_token), os.getpid(), index))
            temps.append(tmp_path)
            fetcher(url, tmp_path)
            _require(os.path.exists(tmp_path), 'fetcher wrote nothing for %s' % url)
            verify_archive(tmp_path)
            # The PREVIOUS sha must be read before any publication, or 'changed'
            # would compare the new bytes against themselves.
            previous = sha256_file(final_path) if os.path.exists(final_path) else None
            staged.append({
                'type': type_token,
                'url': url,
                'path': os.path.abspath(final_path),
                'tmp': tmp_path,
                'bytes': os.path.getsize(tmp_path),
                'sha256': sha256_file(tmp_path),
                'previous_sha256': previous,
            })
    except Exception as exc:                     # noqa: BLE001 - every failure is a FAILED receipt
        error = '%s: %s' % (type(exc).__name__, exc)

    if error is None:
        # Publication phase.  Nothing has touched a production name until here.
        # Law 2 (ALL-OR-NOTHING) has to survive a FAILED replace halfway through
        # this loop as well: on Windows a single WinError 32 on archive 5 of 8
        # used to leave archives 1-4 at the new vintage and 4-8 at the old one --
        # a mixed-vintage input set, which the module docstring calls worse than a
        # stale one.  Each live archive is therefore moved aside first and restored
        # if any replace fails.
        promoted = []
        saved = []
        try:
            for entry in staged:
                if os.path.exists(entry['path']):
                    aside = entry['path'] + '.previous'
                    os.replace(entry['path'], aside)
                    saved.append((entry['path'], aside))
                os.replace(entry['tmp'], entry['path'])
                promoted.append(entry['type'])
        except Exception as exc:                 # noqa: BLE001
            error = ('publication failed after promoting %s: %s: %s'
                     % (promoted or 'nothing', type(exc).__name__, exc))
            for live, aside in saved:
                try:
                    if os.path.exists(live):
                        os.unlink(live)
                except OSError:
                    pass
                try:
                    os.replace(aside, live)
                except OSError:
                    pass
            promoted = []
        else:
            for _live, aside in saved:
                try:
                    os.unlink(aside)
                except OSError:
                    pass

    if error is not None:
        for tmp_path in temps:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        payload = {
            'schema_version': SCHEMA_VERSION,
            'status': 'FAILED',
            'congress': congress,
            'started_utc': _utc(started),
            'finished_utc': _utc(),
            'elapsed_seconds': round(time.time() - started, 3),
            'archives': [],
            'any_changed': False,
            'error': error,
        }
        _write_receipt(receipt_path, payload)
        _gb('billstatus.bulk_refresh', 'failed', error[:200], run_id=run_id,
            source='billstatus-bulk-refresh',
            duration_ms=int((time.time() - started) * 1000))
        print('BILLSTATUS bulk refresh FAILED — %s' % error, file=sys.stderr)
        print('every existing archive was left untouched (receipt: %s)' % receipt_path,
              file=sys.stderr)
        return 1

    archives = [{
        'type': entry['type'],
        'url': entry['url'],
        'path': entry['path'],
        'bytes': entry['bytes'],
        'sha256': entry['sha256'],
        'previous_sha256': entry['previous_sha256'],
        'changed': entry['sha256'] != entry['previous_sha256'],
    } for entry in staged]
    any_changed = any(entry['changed'] for entry in archives)
    finished = time.time()
    payload = {
        'schema_version': SCHEMA_VERSION,
        'status': 'RAN',
        'congress': congress,
        'started_utc': _utc(started),
        'finished_utc': _utc(finished),
        'elapsed_seconds': round(finished - started, 3),
        'archives': archives,
        'any_changed': any_changed,
        'error': None,
    }
    _write_receipt(receipt_path, payload)
    # Data-side sidecar: lets the forecaster's provenance be cross-checked against what
    # was actually published, without reading the repo.
    _write_receipt(os.path.join(directory, 'BILLSTATUS-%d.receipt.json' % congress), {
        'schema_version': SCHEMA_VERSION,
        'status': 'RAN',
        'congress': congress,
        'finished_utc': payload['finished_utc'],
        'archives': archives,
        'any_changed': any_changed,
    })
    changed_types = [entry['type'] for entry in archives if entry['changed']]
    print('BILLSTATUS bulk refresh RAN — %d archives, %d changed (%s)'
          % (len(archives), len(changed_types), ', '.join(changed_types) or 'none'))
    _gb('billstatus.bulk_refresh', 'completed',
        '%d archives refreshed, %d changed' % (len(archives), len(changed_types)),
        run_id=run_id, source='billstatus-bulk-refresh',
        duration_ms=int((finished - started) * 1000))
    return 0


def main(argv=None):
    return run(argv)


if __name__ == '__main__':
    sys.exit(main())
