"""Container entry point for Mentat's compute jobs (D-173..D-182). Linux-safe: stdlib plus fec_key_store and glassbox_emit,
no ctypes.windll at import, no PowerShell. Windows launchers (run_logged.ps1, the console's PowerShell orchestrators) are untouched.

    python compute_job.py run <job> [--run-id ID] [--dry-run] [-- arguments for the job]
    python compute_job.py verify --receipt FILE --job JOB --run-id ID [--map /container/prefix=C:/host/prefix ...]
    python compute_job.py event --kind K --status S --summary TEXT --run-id ID [--stage S] [--detail D]
    python compute_job.py jobs

`run` writes <receipt dir>/<run id>.json (MENTAT_COMPUTE_RECEIPT_DIR, else %LOCALAPPDATA%/Mentat/compute/receipts): job, exit code,
the effective locations, every output with its sha256, and the job's own completion check. A job is complete only when that check
passes (DE-053: never the exit code alone); `verify` repeats it from the host after the container is gone. The FEC keys, for the jobs
that collect, come from the Docker secret file the Windows wrapper decrypted (compute_secrets.py) and live only in the child's
environment; no receipt, argument or log carries a key value, only fingerprints.

D075 adds three jobs for the downstream spending frontier (collect-downstream, collect-downstream-recover, collect-downstream-import). They hold no FEC key: the
host broker does (tools/api_host_pool.py --scope spending-evidence-v2). Each can run a preflight before the child and a finish step after it whatever its exit code
(the in-job export of the volume's ledger to a host bind, tools/downstream_volume.py).
"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
from typing import Callable

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.append(str(PROJECT / 'app'))
import glassbox_emit  # noqa: E402
import mentat_env  # noqa: E402
from fec_key_store import key_id, normalize  # noqa: E402

SCHEMA = 'mentat-compute-job-receipt-v1'
SECRET = '/run/secrets/fec_api_keys'
KEY_VARIABLES = ('FEC_API_KEY', 'FEC_API_KEYS', 'FEC_API_KEYS_FILE')


class JobError(Exception):
    """A request the job cannot start: nothing ran."""


def _value(extra, name):
    for index, argument in enumerate(extra):
        if argument == name and index + 1 < len(extra):
            return extra[index + 1]
        if argument.startswith(name + '='):
            return argument.split('=', 1)[1]
    return None


def _need(extra, *names):
    missing = [name for name in names if _value(extra, name) is None]
    if missing:
        raise JobError('this job needs ' + ', '.join(missing))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _refit_runs(env):
    """Where run_refit.R puts run folders (rf_config.R: REFIT_OUT, else the runtime, else MENTAT_DATA_ROOT, else LOCALAPPDATA)."""
    if env.get('REFIT_OUT') or env.get('RF_RUNTIME'):
        return Path(env.get('REFIT_OUT') or env['RF_RUNTIME']) / 'runs'
    if env.get('MENTAT_DATA_ROOT'):
        return Path(env['MENTAT_DATA_ROOT']) / 'forecast' / 'refit' / 'runs'
    base = Path(env['LOCALAPPDATA']) / 'Mentat' if env.get('LOCALAPPDATA') else Path.home() / '.local' / 'share' / 'Mentat'
    return base / 'forecast' / 'refit' / 'runs'


@dataclass(frozen=True)
class Job:
    summary: str
    minutes: int                  # the scheduled task's own cap where there is one
    network: bool                 # needs the collect network (the FEC API); every other compose service has none
    keys: bool                    # needs the FEC keys secret
    argv: Callable                # (ctx, extra) -> command line
    outputs: Callable             # (ctx, extra) -> files a clean run leaves
    check: Callable               # (ctx, extra, started) -> problems with those files
    cwd: Callable = lambda ctx: ctx['project']
    broker: bool = False                       # D075: needs the host evidence broker's key-free descriptor (never the keys)
    preflight: Callable = None                 # (ctx, extra) -> raises JobError before the child starts; nothing has run
    finish: Callable = None                    # (ctx, extra, started, exit code) -> problems; runs after the child whatever its exit code (the export)


# --- anchors (J1, MentatNightlyAnchors) ------------------------------------------------------------------------------------------
def _anchors_dir(ctx, extra):
    return Path(_value(extra, '--output-dir') or ctx['project'] / 'state' / 'giving-anchors')


def _anchors_outputs(ctx, extra):
    folder = _anchors_dir(ctx, extra)
    return [] if '--check' in extra else [folder / 'giving-anchors-v1.json', folder / 'giving-anchors-v1.receipt.json']


def _anchors_check(ctx, extra, started):
    if '--check' in extra:
        return []
    snapshot, receipt = _anchors_outputs(ctx, extra)
    if not (snapshot.is_file() and receipt.is_file()):
        return ['the snapshot or its receipt was not written']
    problems = []
    record = _json(receipt)
    if record.get('snapshot_sha256') != _sha256(snapshot):
        problems.append('the snapshot does not match its receipt')
    if str(record.get('built_at_utc', '')) < _utc(started):
        problems.append('the snapshot predates this run')
    return problems


# --- refit (J2, MentatRefitWatcher and the manual parity / refit modes) ---------------------------------------------------------
def _refit_argv(mode, fixed=()):
    def argv(ctx, extra):
        return [ctx['rscript'], str(ctx['project'] / 'forecast' / 'refit' / 'run_refit.R'), '--mode', mode, '--run-id', ctx['run_id'], *fixed, *extra]
    return argv


def _refit_file(name):
    return lambda ctx, extra: [_refit_runs(ctx['env']) / ctx['run_id'] / name]


def _present(ctx, extra, started):
    return [] if all(path.is_file() and path.stat().st_size for path in JOBS[ctx['job']].outputs(ctx, extra)) else ['an expected output is missing or empty']


def _refit_receipt_check(ctx, extra, started):
    problems = _present(ctx, extra, started)
    if not problems:
        _json(JOBS[ctx['job']].outputs(ctx, extra)[0])
    return problems


# --- pocket page (J5) ------------------------------------------------------------------------------------------------------------
def _pocket_argv(ctx, extra):
    _need(extra, '--data', '--out')          # never rewrite the repository's template in place
    return [sys.executable, str(ctx['project'] / 'tools' / 'pocket' / 'build_page.py'), *extra]


def _pocket_check(ctx, extra, started):
    page = Path(_value(extra, '--out'))
    return [] if page.is_file() and '/*__DATA_START__*/' in page.read_text(encoding='utf-8') else ['the page is missing or lost its data markers']


# --- FEC collection (J3 collect lane, J5 PAC evidence) ---------------------------------------------------------------------------
def _fanout_argv(ctx, extra):
    _need(extra, '--source-database', '--industry', '--run-directory')
    library = ctx['env'].get('MENTAT_R_LIBRARY')
    return [sys.executable, str(ctx['project'] / 'tools' / 'api_company_cycle_fanout.py'), '--rscript-path', ctx['rscript'],
            *(['--r-library-path', library] if library else []), *extra]


def _fanout_receipt(ctx, extra):
    return Path(_value(extra, '--output-receipt') or Path(_value(extra, '--run-directory')) / 'receipt.json')


def _fanout_check(ctx, extra, started):
    receipt = _fanout_receipt(ctx, extra)
    return [] if receipt.is_file() and _json(receipt).get('status') == 'succeeded' else ['the fanout receipt is missing or is not "succeeded"']


def _evidence_argv(ctx, extra):
    _need(extra, '--output', '--committees', '--company')
    return [sys.executable, str(ctx['project'] / 'tools' / 'pac_evidence.py'), *extra]


def _evidence_outputs(ctx, extra):
    return [Path(_value(extra, '--output')) / 'evidence.json', Path(_value(extra, '--output')) / 'snapshots.json']


def _evidence_check(ctx, extra, started):
    evidence, snapshots = _evidence_outputs(ctx, extra)
    if not (evidence.is_file() and snapshots.is_file()):
        return ['evidence.json or snapshots.json was not written']
    return [] if _json(evidence).get('source_snapshot_sha256') == _sha256(snapshots) else ['evidence.json does not cite the snapshots.json beside it']


# --- downstream spending frontier in a container (D075 phase 2; the host-native run in run_job.ps1 stays the default) ---------------------------------
# /frontier is the Docker named volume holding the live ledger (never a bind: SQLite locks are blind across the Docker Desktop VM); /hoststate is the host evidence root,
# read-only here (the host broker writes the archive there); /export gets a hash-checked export of every invocation; /inputs holds the seed file the host observer prepared;
# /import holds a snapshot to seed an empty volume. NO FEC key is mounted: MENTAT_REQUEST_POOL_CONFIG names the host broker, which holds them. `--live` is the caller's
# argument alone and is never added here, so the authorization stays visible in -PlanOnly and in the receipt.
FRONTIER, HOSTSTATE, EXPORT, INPUTS, IMPORT = Path('/frontier'), Path('/hoststate'), Path('/export'), Path('/inputs'), Path('/import')
DOWN_VALUES = ('--batch', '--collector-run-id', '--expect-base-sha256', '--seeds', '--max-requests', '--max-failed-attempts', '--workers', '--stop-after-seconds', '--priority-candidates',
               '--bulk-activity-index', '--expect-bulk-index-sha256')    # D076
DOWN_REPEATS = ('--enable-endpoint-contract', '--ccl', '--ccl-vintage')
DOWN_SWITCHES = ('--live', '--include-later-cycle-context', '--optional-receipts')
DOWN_REQUIRED = ('--batch', '--collector-run-id', '--expect-base-sha256', '--seeds', '--max-requests', '--max-failed-attempts', '--workers')
INTEGRITY_STOPS = ('ArchiveBindingFailed', 'FECArchiveFailed', 'EndpointContractRefused', 'CrashRecovered', 'IntegrityError', 'OperationalError', 'DatabaseError', 'BrokerUnavailable')


def _posix(path):
    return Path(path).as_posix()


def _under(path, root):
    parts = Path(path).as_posix().split('/')
    return Path(path).as_posix().startswith(root.as_posix().rstrip('/') + '/') and '..' not in parts


def _down_parse(extra):
    values, repeats, switches, index = {}, {}, set(), 0
    while index < len(extra):
        flag = extra[index]
        if flag in DOWN_SWITCHES:
            switches.add(flag)
            index += 1
        elif flag in DOWN_VALUES or flag in DOWN_REPEATS:
            if index + 1 >= len(extra) or extra[index + 1].startswith('--'):
                raise JobError(flag + ' needs a value')
            if flag in DOWN_VALUES:
                if flag in values:
                    raise JobError(flag + ' was given twice')
                values[flag] = extra[index + 1]
            else:
                repeats.setdefault(flag, []).append(extra[index + 1])
            index += 2
        else:
            raise JobError(flag + ' is not accepted by this job (it owns --state, --archive-root, --transport and --run-id; recovery, close-out, re-bind and --init-new-ledger are separate deliberate commands)')
    missing = [flag for flag in DOWN_REQUIRED if flag not in values] + ([] if '--live' in switches else ['--live'])
    if missing:
        raise JobError('this job needs ' + ', '.join(missing))
    return values, repeats, switches


def _down_args(extra):
    import re
    values, repeats, switches = _down_parse(extra)

    def number(flag, low, high=None, kind=int):
        try:
            value = kind(values[flag])
        except ValueError:
            raise JobError(flag + ' must be a number') from None
        if value < low or (high is not None and value > high):
            raise JobError(flag + ' is out of range')
        return value
    number('--batch', 1)
    number('--max-requests', 1)
    number('--max-failed-attempts', 1)
    number('--workers', 1, 32)
    if '--stop-after-seconds' in values and not number('--stop-after-seconds', 0, kind=float) > 0:
        raise JobError('--stop-after-seconds must be positive')
    if not re.fullmatch(r'[0-9a-f]{32}', values['--collector-run-id']):
        raise JobError('--collector-run-id must be 32 lowercase hex digits')
    if not re.fullmatch(r'[0-9a-f]{64}', values['--expect-base-sha256']):
        raise JobError('--expect-base-sha256 must be 64 lowercase hex digits')
    # D076: a bulk activity index is a data file the host prepared next to the seeds; the pin is mandatory and travels with it (the collector would refuse a lone flag too)
    if ('--bulk-activity-index' in values) != ('--expect-bulk-index-sha256' in values):
        raise JobError('--bulk-activity-index and --expect-bulk-index-sha256 are given together or not at all')
    if '--expect-bulk-index-sha256' in values and not re.fullmatch(r'[0-9a-f]{64}', values['--expect-bulk-index-sha256']):
        raise JobError('--expect-bulk-index-sha256 must be 64 lowercase hex digits')
    for flag in ('--seeds', '--priority-candidates', '--bulk-activity-index'):
        if flag in values and not _under(values[flag], INPUTS):
            raise JobError(flag + ' must be a file under ' + _posix(INPUTS) + ' (the folder the host prepared)')
    if any(not re.fullmatch(r'openfec-[A-Za-z0-9._-]+', item) for item in repeats.get('--enable-endpoint-contract', [])):
        raise JobError('--enable-endpoint-contract takes a pinned contract id')
    for item in repeats.get('--ccl', []):
        cycle, _, path = item.partition('=')
        if cycle not in ('2022', '2024', '2026') or not _under(path, Path('/mentat')):
            raise JobError('--ccl takes CYCLE=/mentat/... (a read-only file in the tree)')
    for item in repeats.get('--ccl-vintage', []):
        if item.partition('=')[0] not in ('2022', '2024', '2026') or not item.partition('=')[2]:
            raise JobError('--ccl-vintage takes CYCLE=TEXT')
    return values, repeats, switches


def _down_stored_map_problem():
    import stored_paths
    probe = FRONTIER / 'frontier.sqlite'
    if stored_paths.to_stored(probe) == str(probe) or stored_paths.to_stored(HOSTSTATE / 'responses-x') == str(HOSTSTATE / 'responses-x'):
        return 'MENTAT_STORED_PATH_MAP does not map the frontier volume and the host state: receipts would record container paths and break the chain (run_job.ps1 sets it)'
    return None


def _downstream_argv(ctx, extra):
    values, repeats, switches = _down_args(extra)
    stop = values.get('--stop-after-seconds') or str(JOBS['collect-downstream'].minutes * 60 - 600)      # a graceful deadline: dispatch stops, in-flight work drains, the receipt says DeadlineReached
    argv = [sys.executable, '-B', str(ctx['project'] / 'tools' / 'collect_committee_activity.py'), '--state', _posix(FRONTIER), '--archive-root', _posix(HOSTSTATE), '--transport', 'broker',
            '--run-id', values['--collector-run-id'], '--seeds', values['--seeds'], '--max-requests', values['--max-requests'], '--max-failed-attempts', values['--max-failed-attempts'],
            '--workers', values['--workers'], '--stop-after-seconds', stop]
    for contract in repeats.get('--enable-endpoint-contract', []):
        argv += ['--enable-endpoint-contract', contract]
    if '--priority-candidates' in values:
        argv += ['--priority-candidates', values['--priority-candidates']]
    if '--bulk-activity-index' in values:
        argv += ['--bulk-activity-index', values['--bulk-activity-index'], '--expect-bulk-index-sha256', values['--expect-bulk-index-sha256']]
    for flag in ('--ccl', '--ccl-vintage'):
        for item in repeats.get(flag, []):
            argv += [flag, item]
    return argv + [flag for flag in ('--include-later-cycle-context', '--optional-receipts') if flag in switches] + ['--live']


def _downstream_preflight(ctx, extra):
    import downstream_volume
    values, _, _ = _down_args(extra)
    descriptor = ctx['env'].get('MENTAT_REQUEST_POOL_CONFIG')
    if not descriptor:
        raise JobError('MENTAT_REQUEST_POOL_CONFIG names no host evidence-pool descriptor (run_job.ps1 starts the broker and mounts it)')
    problem = _down_stored_map_problem()
    if problem:
        raise JobError(problem)
    problems = downstream_volume.preflight(FRONTIER, HOSTSTATE, expect_base_sha256=values['--expect-base-sha256'], descriptor=descriptor, tools=ctx['project'] / 'tools')
    if problems:
        raise JobError('; '.join(problems))


def _down_cid(extra):
    value = _value(extra, '--collector-run-id')
    return value if value and len(value) == 32 else 'unknown'


def _downstream_finish(ctx, extra, started, code):
    import downstream_volume
    cid = _down_cid(extra)
    return downstream_volume.export_run(FRONTIER, EXPORT, cid, image_digest=ctx['env'].get('MENTAT_IMAGE_DIGEST'), volume=ctx['env'].get('MENTAT_DOWNSTREAM_VOLUME'))['problems']


def _downstream_outputs(ctx, extra):
    cid = _down_cid(extra)
    folder = EXPORT / cid
    names = ['frontier.sqlite', 'RUN-' + cid + '.START.json', 'RECEIPT-' + cid + '.json' if not (FRONTIER / ('RUN-' + cid + '.ABANDONED.json')).is_file() else 'RUN-' + cid + '.ABANDONED.json', 'EXPORT.json']
    if folder.is_dir():
        names += sorted(item.name for item in folder.iterdir() if item.name.startswith(('seeds-', 'frontier.endpoint-contracts')))
    return [folder / name for name in dict.fromkeys(names)]


def _down_export(extra):
    """(EXPORT.json document, the folder) or a list of problems."""
    folder = EXPORT / _down_cid(extra)
    try:
        return json.loads((folder / 'EXPORT.json').read_text(encoding='utf-8')), folder
    except (OSError, ValueError):
        return None, folder


def _downstream_check(ctx, extra, started):
    import stored_paths
    values, repeats, _ = _down_args(extra)
    cid = values['--collector-run-id']
    exported, folder = _down_export(extra)
    if exported is None:
        return ['the export (EXPORT.json) was not written']
    problems = ['export: ' + item for item in exported.get('problems', [])]
    receipt = folder / ('RECEIPT-' + cid + '.json')
    if not receipt.is_file():
        return problems + ['no receipt for run ' + cid + ' in the export']
    data = _json(receipt)
    stop = (data.get('stop') or {}).get('error_type') or ''
    last = str(data.get('raw_archive_directory', '')).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    checks = {'the receipt is not older than the start': receipt.stat().st_mtime >= started - 2,
              'the receipt names the stored ledger': data.get('ledger') == stored_paths.to_stored(FRONTIER / 'frontier.sqlite'),
              'the receipt names this run\'s response folder': last == 'responses-' + cid,
              'the ledger hash is the exported one': data.get('ledger_sha256') == (exported.get('ledger_sha256') or {}).get('copy'),
              'cap and workers are the requested': data.get('physical_request_cap') == int(values['--max-requests']) and data.get('workers') == int(values['--workers']),
              'attempts are within the cap': isinstance(data.get('http_attempts'), int) and data['http_attempts'] <= int(values['--max-requests']),
              'every attempt is accounted': data.get('endpoint_attempts_recorded', -1) + data.get('candidate_committee_lookup_http_attempts', -1) == data.get('http_attempts'),
              'the run read its evidence through the broker': (data.get('transport') or {}).get('kind') == 'broker' and (data.get('transport') or {}).get('keys_held_by_this_process') == 0,
              'the receipt names the pinned bulk index (or none was requested)': (data.get('bulk_activity_index') or {}).get('sha256') == values.get('--expect-bulk-index-sha256'),
              'the stop is not an integrity failure': stop not in INTEGRITY_STOPS}
    return problems + ['receipt check failed: ' + name for name, ok in checks.items() if not ok]


def _recover_args(extra):
    import re
    if len(extra) != 2 or extra[0] != '--collector-run-id' or not re.fullmatch(r'[0-9a-f]{32}', extra[1]):
        raise JobError('this job takes exactly --collector-run-id <32 hex digits> (the unresolved run to recover)')
    return extra[1]


def _recover_argv(ctx, extra):
    _recover_args(extra)
    return [sys.executable, '-B', str(ctx['project'] / 'tools' / 'collect_committee_activity.py'), '--state', _posix(FRONTIER), '--archive-root', _posix(HOSTSTATE), '--recover-crashed-runs']


def _recover_preflight(ctx, extra):
    import collect_committee_activity as driver
    cid = _recover_args(extra)
    problem = _down_stored_map_problem()
    if problem:
        raise JobError(problem)
    if not (FRONTIER / 'frontier.sqlite').is_file():
        raise JobError('no ledger in the frontier volume')
    crashed, legacy = driver.unresolved_runs(FRONTIER, HOSTSTATE if HOSTSTATE.is_dir() else None)
    if crashed != [cid] or legacy:
        raise JobError('the unresolved runs are ' + (', '.join(crashed + legacy) or 'none') + ', not exactly ' + cid)


def _recover_check(ctx, extra, started):
    cid = _recover_args(extra)
    exported, folder = _down_export(extra)
    if exported is None:
        return ['the export (EXPORT.json) was not written']
    problems = ['export: ' + item for item in exported.get('problems', [])]
    receipt, abandoned = folder / ('RECEIPT-' + cid + '.json'), folder / ('RUN-' + cid + '.ABANDONED.json')
    if receipt.is_file():
        if not _json(receipt).get('recovered_after_crash'):
            problems.append('the receipt does not say it was written by the recovery')
    elif not abandoned.is_file():
        problems.append('recovery wrote neither a receipt nor an abandon marker for run ' + cid)
    return problems


def _import_sha(extra):
    import re
    if len(extra) != 2 or extra[0] != '--expect-sha256' or not re.fullmatch(r'[0-9a-f]{64}', extra[1]):
        raise JobError('this job takes exactly --expect-sha256 <64 hex digits> (the snapshot named in its MIGRATION record)')
    return extra[1]


def _import_record(ctx):
    return _receipt_dir(ctx['env']) / ('IMPORT-' + ctx['run_id'] + '.json')


def _import_argv(ctx, extra):
    return [sys.executable, '-B', str(ctx['project'] / 'tools' / 'downstream_volume.py'), 'import', '--source', _posix(IMPORT / 'frontier.sqlite'), '--expect-sha256', _import_sha(extra),
            '--dest', _posix(FRONTIER / 'frontier.sqlite'), '--record', str(_import_record(ctx))]


def _import_check(ctx, extra, started):
    record = _import_record(ctx)
    if not record.is_file():
        return ['the import record was not written']
    return [] if _json(record).get('sha256') == _import_sha(extra) else ['the import record does not name the expected snapshot']


def downstream_plan(job, extra):
    """What the Windows wrapper needs to know before it starts anything (no Docker, no key): the run id, the physical cap, the workers and the endpoint families the host broker
    must admit for exactly these arguments (B and E always; F, H4, totals and candidate_committees only when their contract id is passed; A only with --optional-receipts)."""
    import re
    if job == 'collect-downstream':
        values, repeats, switches = _down_args(extra)
        from spending_endpoint_contracts import CONTRACTS, activation_plan
        try:
            ids = activation_plan([], repeats.get('--enable-endpoint-contract', []))['enabled_contract_ids']
        except ValueError as error:
            raise JobError(str(error)) from None
        order = ('A', 'B', 'E', 'F', 'H4', 'totals', 'candidate_committees')
        names = {'B', 'E'} | {kind for kind, value in CONTRACTS.items() if value['contract_id'] in ids} | ({'A'} if '--optional-receipts' in switches else set())
        return {'job': job, 'collector_run_id': values['--collector-run-id'], 'batch': int(values['--batch']), 'max_requests': int(values['--max-requests']),
                'workers': int(values['--workers']), 'families': ','.join(name for name in order if name in names), 'expect_base_sha256': values['--expect-base-sha256']}
    if job == 'collect-downstream-recover':
        return {'job': job, 'collector_run_id': _recover_args(extra), 'max_requests': 0, 'families': ''}
    if job == 'collect-downstream-import':
        return {'job': job, 'expect_sha256': _import_sha(extra), 'max_requests': 0, 'families': ''}
    raise JobError('no downstream plan for job ' + job)


JOBS = {
    'anchors': Job('Giving Forecast anchor snapshot (MentatNightlyAnchors)', 45, False, False,
                   lambda ctx, extra: [sys.executable, str(ctx['project'] / 'forecast' / 'anchors' / 'build_giving_anchors.py'), *extra],
                   _anchors_outputs, _anchors_check, lambda ctx: ctx['project'].parent),
    'refit-detect': Job('Giving Forecast refit watcher, detect mode (MentatRefitWatcher)', 20, False, False, _refit_argv('detect'),
                        _refit_file('detect.csv'), _present, lambda ctx: ctx['project'] / 'forecast' / 'refit'),
    'refit-parity': Job('Giving Forecast refit, parity mode (manual)', 240, False, False, _refit_argv('parity'),
                        _refit_file('receipt.json'), _refit_receipt_check, lambda ctx: ctx['project'] / 'forecast' / 'refit'),
    'refit': Job('Giving Forecast refit, never publishes (manual)', 480, False, False, _refit_argv('refit', ('--publish', 'never')),
                 _refit_file('receipt.json'), _refit_receipt_check, lambda ctx: ctx['project'] / 'forecast' / 'refit'),
    'pocket-build': Job('Pocket page from a data snapshot (console publish)', 10, False, False, _pocket_argv,
                        lambda ctx, extra: [Path(_value(extra, '--out'))], _pocket_check),
    'collect-fanout': Job('FEC API collection across key lanes (industry report collect lane)', 1440, True, True, _fanout_argv,
                          lambda ctx, extra: [_fanout_receipt(ctx, extra)], _fanout_check),
    'pac-evidence': Job('PAC evidence from the FEC API (console)', 120, True, True, _evidence_argv, _evidence_outputs, _evidence_check),
    'collect-downstream': Job('Downstream spending frontier through the host evidence broker, no FEC key in the container (D075)', 720, True, False, _downstream_argv, _downstream_outputs, _downstream_check,
                              broker=True, preflight=_downstream_preflight, finish=_downstream_finish),
    'collect-downstream-recover': Job('Downstream spending frontier: crash recovery inside the volume, no provider, no key (D075)', 60, False, False, _recover_argv, _downstream_outputs, _recover_check,
                                      preflight=_recover_preflight, finish=_downstream_finish),
    'collect-downstream-import': Job('Seed an empty frontier volume from a host snapshot, no provider, no key (D075)', 60, False, False, _import_argv, lambda ctx, extra: [_import_record(ctx)], _import_check),
}
# Not here: the industry report build (PowerShell 5.1 orchestrators, Excel finalizer, artifact-tool). Its Rscript / R-library
# defaults take the same variables now (tests/Test-MentatPathSeams.ps1); the port itself is M2-c / M2-d (design 5).


def _context(job, run_id, env):
    return {'job': job, 'run_id': run_id, 'env': env, 'project': PROJECT, 'rscript': env.get('MENTAT_RSCRIPT', '').strip() or mentat_env.SEAMS['MENTAT_RSCRIPT'][0]}


def _read_keys(env):
    secret = Path(env.get('FEC_API_KEYS_FILE') or SECRET)
    if not secret.is_file():
        raise JobError('the FEC keys secret is not mounted (' + str(secret) + ')')
    keys = normalize([secret.read_text(encoding='utf-8-sig')])
    if not keys:
        raise JobError('the FEC keys secret holds no key')
    return secret, keys


def _receipt_dir(env):
    if env.get('MENTAT_COMPUTE_RECEIPT_DIR'):
        return Path(env['MENTAT_COMPUTE_RECEIPT_DIR'])
    return (Path(env['LOCALAPPDATA']) if env.get('LOCALAPPDATA') else Path.home() / '.local' / 'share') / 'Mentat' / 'compute' / 'receipts'


def _locations(ctx):
    env = ctx['env']
    names = ('LOCALAPPDATA', 'MENTAT_REPORT_DIRECTORY', 'MENTAT_DATA_ROOT', 'MENTAT_MELANGE_ROOT', 'MENTAT_R_LIBRARY', 'RF_RUNTIME', 'REFIT_OUT',
             'RF_REPORT_DIR', 'RF_PUBLISH_DIR', 'RF_GLASSBOX_DIR', 'ANCHORS_GLASSBOX_DIR', 'GLASSBOX_EVENTS_DIR', 'TZ')
    return {'project': str(ctx['project']), 'rscript': ctx['rscript'], **{name: env[name] for name in names if env.get(name)}}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    pending.write_text(json.dumps(value, indent=1, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8', newline='\n')
    os.replace(pending, path)


def _run_child(argv, cwd, env, minutes):
    options = {} if os.name == 'nt' else {'start_new_session': True}
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, **options)
    try:
        return child.wait(timeout=minutes * 60)
    except subprocess.TimeoutExpired:
        # ponytail: off Windows the whole process group dies; on Windows (tests only) just the child. The container is Linux.
        os.killpg(child.pid, signal.SIGKILL) if os.name != 'nt' else child.kill()
        child.wait()
        print('compute_job: killed after ' + str(minutes) + ' minutes', file=sys.stderr)
        return 124


def run(name, run_id, extra, env=None, dry_run=False):
    env = dict(os.environ if env is None else env)
    job = JOBS[name]
    ctx = _context(name, run_id, env)
    argv, outputs = job.argv(ctx, extra), job.outputs(ctx, extra)
    child = {k: v for k, v in env.items() if k not in KEY_VARIABLES and not k.startswith('FEC_API_KEY_')}
    child.setdefault('PYTHONUTF8', '1')                           # as run_logged.ps1 sets it for the scheduled tasks
    child.setdefault('PYTHONIOENCODING', 'utf-8')
    keys = None
    if job.keys:
        secret, keys = _read_keys(env)
        child.update(FEC_API_KEYS='|'.join(keys), FEC_API_KEYS_FILE=str(secret))
    plan = {'job': name, 'run_id': run_id, 'argv': argv, 'cwd': str(job.cwd(ctx)), 'network': job.network, 'minutes': job.minutes,
            'outputs': [str(path) for path in outputs], 'locations': _locations(ctx), 'keys': {'count': len(keys), 'key_ids': [key_id(k) for k in keys]} if keys else None}
    if job.broker:
        plan['broker'] = True
    if dry_run:
        print(json.dumps(plan, indent=1))
        return 0
    if job.preflight:
        job.preflight(ctx, extra)                                 # JobError: refused with exit 2, nothing ran
    started = time.time()
    glassbox_emit.emit('run.start', 'running', 'Compute job ' + name + ': ' + job.summary, run_id=run_id, stage='start', lane='system', source='mentat-compute')
    code = _run_child(argv, job.cwd(ctx), child, job.minutes)
    finished = job.finish(ctx, extra, started, code) if job.finish else []
    outputs = job.outputs(ctx, extra) if job.finish else outputs         # the export is only there now
    problems = finished + ([] if code else job.check(ctx, extra, started))
    files = [{'path': str(path), 'sha256': _sha256(path), 'bytes': path.stat().st_size} for path in outputs if path.is_file()]
    status = 'ok' if code == 0 and not problems else 'failed'
    _write_json(_receipt_dir(env) / (run_id + '.json'), {
        'schema': SCHEMA, 'job': name, 'run_id': run_id, 'status': status, 'exit_code': code, 'started_utc': _utc(started), 'finished_utc': _utc(time.time()),
        'argv': argv, 'network': job.network, 'keys': plan['keys'], 'locations': plan['locations'], 'outputs': files, 'check': {'ok': not problems, 'problems': problems},
        'runtime': {'python': platform.python_version(), 'os': os.name, 'platform': platform.platform()}})
    glassbox_emit.emit('run.finish', 'completed' if status == 'ok' else 'failed', 'Compute job ' + name + ' ' + status, run_id=run_id, stage='finish',
                       detail='; '.join(problems) or 'exit ' + str(code), lane='system', source='mentat-compute', duration_ms=int((time.time() - started) * 1000))
    return 0 if status == 'ok' else code or 1


def _host(path, maps):
    """The host path of a container path: the longest mapped prefix that ends on a path boundary (a mount nested under another wins)."""
    text = str(path)
    hits = [(container, host) for container, host in maps if text == container or text.startswith(container.rstrip('/') + '/')]
    if not hits:
        return Path(path)
    container, host = max(hits, key=lambda pair: len(pair[0]))
    return Path(host + text[len(container):])


def verify(receipt, job, run_id, maps=()):
    """Problems with a finished run, from its receipt alone (the container may be gone); empty means the job completed."""
    if not Path(receipt).is_file():
        return ['no receipt for this run']
    record, problems = _json(receipt), []
    for field, expected in (('schema', SCHEMA), ('job', job), ('run_id', run_id), ('status', 'ok'), ('exit_code', 0)):
        if record.get(field) != expected:
            problems.append(field + ' is ' + repr(record.get(field)) + ', expected ' + repr(expected))
    if not record.get('check', {}).get('ok'):
        problems.extend(record.get('check', {}).get('problems') or ['the job reported no completion check'])
    for output in record.get('outputs', []):
        path = _host(output['path'], maps)
        if not path.is_file() or _sha256(path) != output['sha256']:
            problems.append('output changed or missing since the run: ' + str(path))
    return problems


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if '--' in argv:
        split = argv.index('--')
        argv, extra = argv[:split], argv[split + 1:]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    runner = commands.add_parser('run')
    runner.add_argument('job', choices=sorted(JOBS))
    runner.add_argument('--run-id')
    runner.add_argument('--dry-run', action='store_true')
    checker = commands.add_parser('verify')
    checker.add_argument('--receipt', required=True)
    checker.add_argument('--job', required=True, choices=sorted(JOBS))
    checker.add_argument('--run-id', required=True)
    checker.add_argument('--map', action='append', default=[], help='/container/prefix=C:/host/prefix')
    notifier = commands.add_parser('event')
    for flag in ('--kind', '--status', '--summary', '--run-id'):
        notifier.add_argument(flag, required=True)
    notifier.add_argument('--stage', default='')
    notifier.add_argument('--detail', default='')
    commands.add_parser('jobs')
    planner = commands.add_parser('downstream-plan')
    planner.add_argument('--job', required=True, choices=('collect-downstream', 'collect-downstream-recover', 'collect-downstream-import'))
    args = parser.parse_args(argv)
    if args.command == 'jobs':
        print(json.dumps({name: {'summary': j.summary, 'minutes': j.minutes, 'network': j.network, 'keys': j.keys, 'broker': j.broker} for name, j in sorted(JOBS.items())}, indent=1))
        return 0
    if args.command == 'downstream-plan':
        try:
            print(json.dumps({'ok': True, **downstream_plan(args.job, extra)}))
            return 0
        except JobError as error:
            print(json.dumps({'ok': False, 'error': str(error)}))
            return 2
    if args.command == 'event':
        glassbox_emit.emit(args.kind, args.status, args.summary, run_id=args.run_id, stage=args.stage, detail=args.detail, lane='system', source='mentat-compute')
        return 0
    if args.command == 'verify':
        problems = verify(args.receipt, args.job, args.run_id, [tuple(pair.split('=', 1)) for pair in args.map])
        print(json.dumps({'ok': not problems, 'problems': problems}))
        return 1 if problems else 0
    try:
        return run(args.job, args.run_id or args.job + '-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()), extra, dry_run=args.dry_run)
    except JobError as error:
        print('compute_job: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
