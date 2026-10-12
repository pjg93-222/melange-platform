"""Insights over verified published facts, with bounded background preparation.

This adapter never repairs/reclassifies source observations or fetches APIs.
Dataset IDs resolve through the same report/source proof as Chart Studio.
"""
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timezone
import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

import analysis_history as history
import analysis_scan
import analysis_metrics as metrics
import analysis_integrations as integrations
import contribution_data as data
import contribution_query as query_engine
import contribution_query_store as store
import contribution_workbook_guard as guard
import proclock

PANELS = [
    ('favorites', 'Industry favorites', 'Recipient amounts, positive-gross shares, company breadth and persistence.'),
    ('trends', 'Comparable trends', 'Compare cycles at the same election-stage cutoff.'),
    ('changes', 'What changed', 'Reconcile continuing, newly observed and no-longer-observed company activity.'),
    ('concentration', 'Concentration & breadth', 'Positive-gross concentration, largest sources and recipient shares.'),
    ('relationships', 'Relationship persistence', 'Compare observed company–recipient relationships across cycles.'),
    ('overlap', 'Company portfolio overlap', 'Compare company distributions across recipient committees.'),
    ('timing', 'Timing patterns', 'Monthly and quarterly giving, election phase and historical context.'),
    ('profiles', 'Recipient profiles', 'Exact dated FEC committee and candidate links.'),
    ('spending', 'Outgoing spending', 'Separate saved disbursements and candidate financial summaries.'),
    ('legislation', 'Legislative context', 'Dated legislative records linked through explicit identities.'),
    ('forecast', 'Forecasts', 'Historical baselines and rolling backtests only when coverage supports them.')]
PANEL_IDS = [p[0] for p in PANELS]
ROOT = data.PROJECT / 'state' / 'insights'
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mentat-insights')
_lock = threading.RLock()
_jobs = OrderedDict()
_code = {str(p): data._stamp(p) for p in (Path(__file__), Path(metrics.__file__), Path(history.__file__), Path(integrations.__file__), Path(analysis_scan.__file__))}
_code_hash = history.digest({p: data._hash(p) for p in _code})
# The prewarm hint file. server.py turns hints on for a console that is not read-only.
HINTS_ENABLED = False
HINTS_PATH = data.PROJECT / 'state' / 'prewarm' / 'hints.json'
HINTS_LIMIT = 25
# A build marker defers other processes for as long as its process runs, whatever its age; a dead or
# recycled pid, or an abandoned partial marker, defers no one (proclock's rule; review M3/H2).
ELSEWHERE_RETRY_MS = 500
ELSEWHERE_MESSAGE = 'Another Mentat process is preparing this analysis; it will be shown as soon as it is saved.'
_ELSEWHERE = object()  # a job's answer when another live process holds the key's build
_log = logging.getLogger('mentat.insights')
_hint_lock = threading.Lock()
_hint_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mentat-insights-hints')


class _CapacityReached(Exception):
    """Abort the incomplete journal while retaining honest unavailable panels."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def building_dir():
    """Cross-process build markers, <mentat>/state/insights/building (ROOT/'building').

    Taken beside the response store they guard, the sibling 'insights' folder of its
    root, so a caller that points the store at a scratch root keeps its markers there."""
    return Path(store.ROOT).parent / 'insights' / 'building'


def _probe(identity):
    """store.cached()'s own read of one Insights response - memory, then the
    hash-verified disk envelope - without computing and without waiting on a lock."""
    store._loaded()
    key = store._key([store.LOADED_SHA, 'insights', identity])
    with store._mutex:
        payload = store._memory.get(key)
        if payload is not None:
            store._memory.move_to_end(key)
            return json.loads(payload)
    try:
        envelope = json.loads((store.ROOT / 'responses' / 'insights' / (key + '.json')).read_bytes())
        if not isinstance(envelope, dict):
            return None
        payload = envelope['payload'].encode('utf-8')
        if envelope['key'] != key or hashlib.sha256(payload).hexdigest() != envelope['sha256']:
            return None
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return None
    store._remember(key, payload)
    return json.loads(payload)


def _inspect(path):
    """None when there is no build marker at path; otherwise (live, bytes read or None).

    proclock's one staleness rule decides (review M3/H2): a marker is stale only when its
    process is gone or its pid was recycled, never by its age, so a long build keeps
    deferring other processes for as long as its builder runs. A marker naming this process
    is live only while this process holds it (query() asks only when this process runs no
    job of that key). An unreadable body is being written this instant or was abandoned
    mid-write: live for proclock.UNREADABLE_STALE_SECONDS after its last write, then stale."""
    found = proclock.inspect(path)
    return None if found is None else (found.live, found.raw)


def _unlink(path, expected):
    """Remove the marker at path only while it still holds `expected` (None: only while it is
    still an abandoned unreadable body)."""
    proclock.release(path, expected)


def _building_elsewhere(key):
    """True while another live process holds `key`'s build marker; a stale marker is
    removed so that this process may build."""
    path = building_dir() / (key + '.lock')
    found = _inspect(path)
    if found is None:
        return False
    live, raw = found
    if not live:
        _unlink(path, raw)
    return live


def _claim(key):
    """Atomically create `key`'s build marker (O_CREAT|O_EXCL) holding {pid, pid_started,
    started_utc, key}. Returns its bytes, or None while another live process holds it."""
    return proclock.claim(building_dir() / (key + '.lock'), key)


def _run(key, identity, build, stable, timing):
    """One Insights build in the pool, under the key's cross-process build marker."""
    timing['started'] = time.monotonic()
    body = _claim(key)
    if body is None:
        return _ELSEWHERE
    try:
        return store.cached('insights', identity, build, stable)
    finally:
        _unlink(building_dir() / (key + '.lock'), body)


def _retry_after(future):
    """Poll a build quickly at first and less often as it runs on; a queued job
    counts from submission until the pool starts it."""
    timing = getattr(future, 'mentat_timing', None) or {}
    start = timing.get('started') or timing.get('submitted')
    elapsed = time.monotonic() - start if start is not None else 0
    return 300 if elapsed < 2 else 750 if elapsed < 10 else 1500


def _hint(body, key, dataset):
    """Queue an accepted request for the warmer's hint file. Never raises and never
    waits: the file is written by a background thread after the response is decided."""
    if not HINTS_ENABLED:
        return
    try:
        entry = {'body': json.loads(json.dumps({k: v for k, v in body.items() if k != 'retry'})),
                 'key': key, 'dataset': dataset, 'seen_utc': _utc_now()}
        _hint_writer.submit(_write_hint, entry)
    except Exception:  # noqa: BLE001 - a hint must never fail the request
        _log.warning('Could not queue an Insights prewarm hint.', exc_info=True)


def _write_hint(entry):
    """hints.json: {version, updated_utc, requests newest first, one per key, at most
    HINTS_LIMIT}, replaced atomically. A failure is logged and dropped."""
    try:
        with _hint_lock:
            path = Path(HINTS_PATH)
            try:
                current = json.loads(path.read_text(encoding='utf-8'))
                requests = current.get('requests') if isinstance(current, dict) and current.get('version') == 1 else []
            except FileNotFoundError:
                requests = []
            except (OSError, ValueError) as error:
                _log.warning('Replacing an unreadable Insights hint file: %s', error)
                requests = []
            kept = [item for item in requests or [] if isinstance(item, dict)
                    and isinstance(item.get('key'), str) and item['key'] != entry['key']]
            document = {'version': 1, 'updated_utc': _utc_now(), 'requests': ([entry] + kept)[:HINTS_LIMIT]}
            path.parent.mkdir(parents=True, exist_ok=True)
            pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                pending.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding='utf-8')
                for attempt in range(5):
                    try:
                        os.replace(pending, path)
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(.05 * (attempt + 1))
            finally:
                if pending.exists():
                    pending.unlink()
    except Exception:  # noqa: BLE001 - logged and dropped by design
        _log.warning('Could not write the Insights prewarm hint file.', exc_info=True)


def _deliver(result, request, stamp, stable):
    """A stored or finished analysis, narrowed to the requested panels."""
    if not stable():
        raise ValueError('The source changed before the analysis could be displayed. Refresh sources.')
    if (result.get('options') or {}).get('cycles') is not None:
        _remember_cycles(stamp, request['basis'], result['options']['cycles'])
    result['panels'] = {key: value for key, value in result['panels'].items() if key in request['panels']}
    result['scope']['panels'] = request['panels']
    return result


def _loaded():
    query_engine._check_loaded()
    if any(data._stamp(p) != stamp for p, stamp in _code.items()):
        raise ValueError('Insights code changed. Restart Mentat to load the current analysis rules.')


def validate(request, records=False):
    if not isinstance(request, dict):
        raise ValueError('Insights requests must be objects.')
    allowed = {'dataset', 'basis', 'as_of', 'company', 'funding_source', 'baseline_cycle',
               'current_cycle', 'comparison_dataset', 'panels', 'top_n', 'retry'}
    if records:
        allowed |= {'filters', 'offset', 'limit', 'index_token'}
    if set(request) - allowed:
        raise ValueError('Unknown Insights request fields: ' + ', '.join(sorted(set(request) - allowed)))
    out = dict(request)
    for key in ('dataset', 'company', 'funding_source', 'comparison_dataset'):
        value = out.get(key, '')
        if not isinstance(value, str) or len(value) > 300:
            raise ValueError('Choose a valid ' + key.replace('_', ' ') + '.')
    if not out.get('dataset'):
        raise ValueError('Choose a published industry dataset.')
    out['basis'] = out.get('basis', 'receipt')
    if not isinstance(out['basis'], str) or out['basis'] not in data.BASES or out['basis'] == 'raw':
        raise ValueError('Choose a giving or PAC activity basis; raw observations are not giving.')
    if out.get('as_of') is not None and not isinstance(out.get('as_of'), str):
        raise ValueError('Choose a valid as-of calendar date.')
    try:
        out['as_of'] = date.fromisoformat(out.get('as_of') or date.today().isoformat()).isoformat()
    except (ValueError, TypeError):
        raise ValueError('Choose a valid as-of calendar date.') from None
    for key in ('baseline_cycle', 'current_cycle'):
        value = out.get(key)
        if value:
            year = metrics._cycle(value)
            if year is None:
                raise ValueError('Choose a valid two-year election cycle.')
            out[key] = metrics._label(year)
    if out.get('baseline_cycle') and out.get('current_cycle') and out['baseline_cycle'] >= out['current_cycle']:
        raise ValueError('The baseline cycle must be earlier than the current cycle.')
    panels = out.setdefault('panels', ['favorites', 'trends', 'changes'])
    if not isinstance(panels, list) or any(not isinstance(p, str) or p not in PANEL_IDS for p in panels):
        raise ValueError('Choose valid analysis panels.')
    out['panels'] = list(dict.fromkeys(panels))
    count = out.setdefault('top_n', 25)
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError('Choose 1 to 100 result rows per panel.')
    if out.get('comparison_dataset') == out['dataset']:
        raise ValueError('Choose a different tracked industry for comparison.')
    if records:
        filters = out.get('filters', [])
        if not isinstance(filters, list) or len(filters) > 32:
            raise ValueError('Choose valid source filters.')
        safe_fields = set(data.DIMENSIONS) | {'amount'}
        if any(not isinstance(f, dict) or not isinstance(f.get('field'), str) or f['field'] not in safe_fields for f in filters):
            raise ValueError('Source inspection accepts only observation dimensions and amount filters.')
        for item in filters:
            values = item.get('value') if item.get('op') == 'in' and isinstance(item.get('value'), list) else [item.get('value')]
            if any(isinstance(value, int) and not -2**63 <= value <= 2**63 - 1 or
                   isinstance(value, float) and not math.isfinite(value) for value in values):
                raise ValueError('Source filters require finite values within the supported integer range.')
        data._validate({**out, 'dimensions': [], 'measures': ['amount']})
        if out.get('limit', 50) > 5000:
            raise ValueError('Inspect at most 5,000 source records per page.')
        if out.get('offset', 0) > 2**63 - 1:
            raise ValueError('Choose a valid source record offset.')
        if 'index_token' in out and (not isinstance(out['index_token'], str) or len(out['index_token']) > 128):
            raise ValueError('Choose a valid source snapshot token.')
        data._row_filter(filters, data.BASES[out['basis']][1])
    return out


def catalog(directory, sources=None):
    # One scan of the report folder for this request: the catalogue and the guard read the same list.
    sources = data._sources(directory) if sources is None else sources
    result = data.catalog(directory, sources)
    checks = {item['dataset']: item for item in guard.verify_sources(directory, strict=False, sources=sources)}
    for dataset in result['datasets']:
        check = checks.get(dataset['id'])
        if check and (not check['matched'] or any(check.get(k) is not None and dataset.get('provenance', {}).get(k) != check[k]
                                                 for k in ('workbook_sha256', 'database_sha256'))):
            dataset.update(available=False, message=check.get('error') or 'The published source changed. Refresh sources.')
    result['panels'] = [dict(id=p, title=t, description=d, default=p in PANEL_IDS[:3]) for p, t, d in PANELS]
    result['bases'] = [b for b in result.get('bases', []) if b.get('id') != 'raw']
    result['default_panels'] = PANEL_IDS[:3]
    return result


def _scope_filters(request):
    return [{'field': key, 'op': 'eq', 'value': request[key]}
            for key in ('company', 'funding_source') if request.get(key)]


def _rows(path, request):
    column = data.BASES[request['basis']][1]
    where, params = data._row_filter(_scope_filters(request), column)
    fields = ('source_id,industry,company,committee_id,recipient,date,cycle,funding_source,party,'
              'contributor_key,raw_cents,receipt_cents,original_cents,supported_cents,pac_cents')
    with closing(data._ro(path)) as db:
        # The selected names are distinct, so zipping plain tuples with them gives the
        # dicts dict(sqlite3.Row) gives, keys in the same order, without a Row per row.
        db.row_factory = None
        cursor = db.execute('SELECT ' + fields + ',' + column + ' AS amount_cents FROM facts WHERE ' + where + ' AND ' + column + ' != 0', params)
        names = tuple(item[0] for item in cursor.description)
        for row in cursor:
            yield dict(zip(names, row))


# Scope options per (projection signature, basis). A projection is replaced, never
# rewritten, and its signature (stamp and SQLite header, as the query store keys its own
# verification) names its content, so the same signature always yields the same options.
_OPTIONS_LIMIT = 32
_option_cache = OrderedDict()
_option_lock = threading.Lock()


def _options(path, basis):
    key = (store._projection_signature(path), basis)
    with _option_lock:
        if key in _option_cache:
            _option_cache.move_to_end(key)
            return copy.deepcopy(_option_cache[key])
    with closing(data._ro(path)) as db:
        column = data.BASES[basis][1]
        options = {out: [r[0] for r in db.execute('SELECT DISTINCT "' + field + '" FROM facts WHERE ' + ('1' if field == 'company' else column + ' != 0') + ' AND "' + field + '" != \'\' ORDER BY "' + field + '"')]
                   for field, out in [('company', 'companies'), ('cycle', 'cycles'), ('funding_source', 'funding_sources')]}
    with _option_lock:
        _option_cache[key] = copy.deepcopy(options)
        while len(_option_cache) > _OPTIONS_LIMIT:
            _option_cache.popitem(last=False)
    return options


def _window(value, cycle_value, cutoff):
    """The row's cycle when its date is an eligible event inside that cycle, else None."""
    try:
        observed = date.fromisoformat(value)
        eligible = observed <= cutoff
    except (ValueError, TypeError):
        eligible = False
    cycle = metrics._cycle(cycle_value)
    return cycle if eligible and cycle and observed.year in (cycle - 1, cycle) else None


def _context_rows(rows, context, request):
    pairs, companies = set(), {request['company']} if request.get('company') else set()
    cutoff = date.fromisoformat(request['as_of'])
    # Both tests depend only on the values tested, so each distinct value is tested once.
    # Types are part of the keys: equal values of different types (1 and 1.0) never share.
    windows, committees = {}, {}
    for row in rows:
        value, cycle_value = row['date'], row['cycle']
        key = (type(value), value, type(cycle_value), cycle_value)
        if key in windows:
            cycle = windows[key]
        else:
            cycle = windows[key] = _window(value, cycle_value, cutoff)
        if cycle is not None:
            companies.add(row['company'])
            committee = row['committee_id']
            known = (type(committee), committee)
            if known not in committees:
                committees[known] = re.fullmatch(r'C\d{8}', committee or '') is not None
            if committees[known]:
                pairs.add((committee, cycle))
        yield row
    context.update(recipient_cycles=[{'recipient_id': recipient, 'cycle': cycle} for recipient, cycle in sorted(pairs)],
                   recipient_ids=sorted({p[0] for p in pairs}), cycles=sorted(set(context.get('cycles', [])) | {p[1] for p in pairs}), companies=sorted(companies))


def _reference(path, request, source):
    comparison = {**request, 'company': ''}
    channels = defaultdict(lambda: {'total_positive_cents': 0, 'recipient_positive_cents': defaultdict(int)})
    for row in _rows(path, comparison):
        try:
            if date.fromisoformat(row['date']) > date.fromisoformat(request['as_of']):
                continue
        except (ValueError, TypeError):
            pass  # Favorites includes undated observations and discloses that scope.
        amount = max(0, row['amount_cents'])
        group = channels[row['funding_source'] or metrics.UNKNOWN]
        group['total_positive_cents'] += amount
        group['recipient_positive_cents'][row['committee_id'] or metrics.UNKNOWN] += amount
    return {'by_channel': dict(channels), 'label': source.get('industry', source['id']),
            'coverage_note': 'Explicit tracked-industry comparator, all covered companies at the selected basis and cutoff; not a market-wide baseline.'}


def _periods(cycles, as_of, current=None):
    """Cycles at or before the as-of cycle, and the automatic current/baseline pair."""
    phase = date.fromisoformat(as_of).year
    phase += phase % 2
    available = [c for c in cycles if metrics._cycle(c) and metrics._cycle(c) <= phase]
    current = current or (available[-1] if available else '')
    earlier = [c for c in available if c < current]
    return available, current, (earlier[-1] if earlier else '')


# Cycle options seen for a source snapshot and basis. They let an explicit period
# that equals the automatic one share the automatic request's key and job.
_cycle_options = {}


def _remember_cycles(stamp, basis, cycles):
    if len(_cycle_options) >= 64:
        _cycle_options.clear()
    _cycle_options[(stamp, basis)] = list(cycles)


def _canonical(request, stamp):
    cycles = _cycle_options.get((stamp, request['basis']))
    if cycles is None:
        return request
    out = dict(request)
    _, automatic_current, _ = _periods(cycles, request['as_of'])
    if out.get('current_cycle') == automatic_current:
        del out['current_cycle']
    _, _, automatic_baseline = _periods(cycles, request['as_of'], out.get('current_cycle'))
    if out.get('baseline_cycle') and out['baseline_cycle'] == automatic_baseline:
        del out['baseline_cycle']
    return out


def _prepare(path, source, request, comparator=None):
    import analysis_integrations as integrations
    stamp = data._stamp(path)
    path = analysis_scan.view(path)
    options = _options(path, request['basis'])
    _remember_cycles(stamp, request['basis'], options['cycles'])
    available, current, baseline = _periods(options['cycles'], request['as_of'], request.get('current_cycle'))
    for field in ('current_cycle', 'baseline_cycle'):
        if request.get(field) and request[field] not in available:
            raise ValueError('Choose a cycle present in this dataset at or before the as-of cycle.')
    baseline = request.get('baseline_cycle') or baseline
    if baseline and current and baseline >= current:
        raise ValueError('The baseline cycle must be earlier than the current cycle.')
    scoped = {**request, 'baseline_cycle': baseline, 'current_cycle': current}
    with closing(data._ro(path)) as db:
        metadata = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
    provenance = {**metadata.get('provenance', {}), 'index_token': data._token(source), 'analysis_code_sha256': _code_hash}
    context = {'source_scope': {'dataset': source['id'], 'industry': source.get('industry', source['id']),
                                'basis': request['basis'], 'as_of': request['as_of']}, 'provenance': provenance,
               'cycles': sorted({metrics._cycle(c) for c in available})}
    if comparator:
        context['industry_reference'] = _reference(comparator[0], scoped, comparator[1])
        provenance['comparison'] = data._provenance(comparator[1])
    # Complete collection history is deliberately not inferred from observed rows.
    # No trusted completeness manifest is presently part of the contribution index.
    try:
        with history.inspection(source['id'], provenance['index_token'], provenance) as journal:
            facts = _context_rows(journal.observe(_rows(path, scoped)), context, scoped)
            result = metrics.analyze(facts, {**scoped, 'panels': [p for p in request['panels'] if p in metrics.PANEL_ORDER]}, context)
            if result.get('metadata', {}).get('complete_input_scan') is False:
                raise _CapacityReached()
            extra = integrations.enrich(scoped, context) if set(request['panels']) & {'profiles', 'spending', 'legislation'} else {}
            for panel in extra.values():
                total = max(panel.get('total_rows', 0), len(panel.get('rows', [])))
                panel['total_rows'] = total
                if len(panel.get('rows', [])) > request['top_n']:
                    panel['rows'] = panel['rows'][:request['top_n']]
                    panel['truncated'] = True
                    panel.setdefault('notices', []).append(f"Showing {request['top_n']} of {total} source rows; source coverage and totals remain separate from this display limit.")
            result['panels'].update(extra)
            result['history'] = journal.summary()
            yield result, options, scoped, provenance
    except _CapacityReached:
        notice = 'This scope exceeds the exact analysis capacity. Select a company or funding source; no partial totals or inspection snapshot were published.'
        for key, title, description in PANELS:
            result['panels'][key] = {'title': title, 'status': 'unavailable', 'summary': notice,
                                     'columns': [], 'rows': [], 'notices': [notice]}
        result['history'] = None
        yield result, options, scoped, provenance


def _stable(path, source, stamp, token):
    return store.source_stable(path, stamp) and data._token(source) == token


def query(directory, request, sources=None):
    _loaded()
    body = request
    request = validate(request)
    source = data._source(directory, request, sources)
    path, pending = data._ready(source, retry=request.get('retry') is True)
    if pending:
        return pending
    token, stamp = data._token(source), data._stamp(path)
    comparator = None
    if request.get('comparison_dataset'):
        guard.verify_sources(directory, request['comparison_dataset'], sources=sources)
        other = data._source(directory, {'dataset': request['comparison_dataset']}, sources)
        other_path, pending = data._ready(other, retry=request.get('retry') is True)
        if pending:
            return {**pending, 'provenance': data._provenance(source), 'message': 'Preparing the selected comparison industry.'}
        comparator = (other_path, other, data._stamp(other_path), data._token(other))
    import analysis_integrations as integrations
    integration_context = {'source_scope': {'dataset': source['id'], 'industry': source.get('industry', source['id']),
                                            'basis': request['basis'], 'as_of': request['as_of']}}
    needs_integrations = bool(set(request['panels']) & {'profiles', 'spending', 'legislation'})
    fingerprint = integrations.sources_fingerprint(integration_context) if needs_integrations else None
    # One job per resolved scope: every engine panel is computed once (the scan
    # dominates their cost) and the reply is filtered to the requested panels, so
    # switching an engine analysis on or off reuses the job. Saved integrations
    # open only when asked for. An explicit period equal to the automatic one is
    # keyed as automatic, so Swap or re-picking the shown cycle reuses it too.
    canonical = {**_canonical(request, stamp),
                 'panels': [p for p in PANEL_IDS if p in metrics.PANEL_ORDER or p in request['panels']]}
    canonical.pop('retry', None)
    key = history.digest({'source': token, 'stamp': stamp, 'code': _code_hash, 'integrations': fingerprint,
                          'comparator': comparator[2:] if comparator else None, 'request': canonical})

    def stable():
        _loaded()
        return (_stable(path, source, stamp, token) and (not comparator or _stable(*comparator))
                and (not needs_integrations or integrations.sources_fingerprint(integration_context) == fingerprint))

    if not stable():
        raise ValueError('An Insights source has changed or has active writes. Retry after collection is stable.')

    # A result already stored (memory or verified disk) is answered now, without the
    # pool; the pool is only for a true miss. While this process still runs the key's
    # job, its result is not stored before the job ends, so the job answers instead
    # (and it has released its build marker by then).
    identity = {'request_key': key}
    dataset = source.get('industry', source['id'])
    with _lock:
        running = key in _jobs and not _jobs[key].done()
    stored = None if running else _probe(identity)
    if stored is not None:
        response = _deliver(stored, request, stamp, stable)
        _hint(body, key, dataset)
        return response

    def build():
        if not stable():
            raise ValueError('The analysis source changed before preparation.')
        prepared = _prepare(path, source, canonical, comparator)
        try:
            result, options, scoped, provenance = next(prepared)
            if not stable():
                raise ValueError('An Insights source changed during analysis. Refresh against the current snapshot.')
            # Resume the generator to commit the observation journal only after
            # verifying every input still identifies the same snapshot.
            try:
                next(prepared)
            except StopIteration:
                pass
        finally:
            prepared.close()
        return {'status': 'ready', 'dataset': source['id'], 'scope': {**scoped, 'basis_label': data.BASES[canonical['basis']][0]},
                'provenance': provenance, 'options': options, **result,
                'source_history': history.snapshots(source['id']),
                'notices': [history.NOTICE, 'Event-date cutoffs do not recreate what was publicly known on that date.',
                            'Amounts describe observed giving; they do not establish influence or a causal connection to legislative activity.']}

    response = None
    with _lock:
        future = _jobs.get(key)
        if future is not None and future.done():
            failed = future.exception() is not None
            if (failed and request.get('retry')) or (not failed and future.result() is _ELSEWHERE):
                del _jobs[key]
                future = None
        if future is None:
            if _building_elsewhere(key):
                response = {'status': 'preparing', 'message': ELSEWHERE_MESSAGE,
                            'retry_after_ms': ELSEWHERE_RETRY_MS, 'provenance': data._provenance(source)}
            elif sum(not job.done() for job in _jobs.values()) >= 6:
                response = {'status': 'preparing', 'message': 'Other Insights requests are finishing. This request will retry shortly.',
                            'retry_after_ms': 2000, 'provenance': data._provenance(source)}
            else:
                timing = {'submitted': time.monotonic(), 'started': None}
                future = _pool.submit(_run, key, identity, build, stable, timing)
                future.mentat_timing = timing
                _jobs[key] = future
        if future is not None:
            _jobs.move_to_end(key)
        while len(_jobs) > 12:
            old = next((k for k, f in _jobs.items() if f.done() and k != key), None)
            if old is None:
                break
            del _jobs[old]
    if response is None and not future.done():
        response = {'status': 'preparing', 'message': 'Analyzing the complete verified scope and saved source integrations…',
                    'retry_after_ms': _retry_after(future), 'provenance': data._provenance(source)}
    if response is None:
        result = future.result()
        if result is _ELSEWHERE:
            # Another process claimed the key between this request's check and the job.
            response = {'status': 'preparing', 'message': ELSEWHERE_MESSAGE,
                        'retry_after_ms': ELSEWHERE_RETRY_MS, 'provenance': data._provenance(source)}
        else:
            response = _deliver(copy.deepcopy(result), request, stamp, stable)
    _hint(body, key, dataset)
    return response


# Fields of the Insights projection (analysis_scan.FIELDS): a records filter on these
# alone is counted and paged on the projection instead of the full contribution index.
_PROJECTED = frozenset(analysis_scan.FIELDS) | {'amount', 'amount_cents'}
# Irregular stored date values per projection signature (see _irregular_dates).
_date_census = OrderedDict()
_census_lock = threading.Lock()
# More irregular values than this and the records filter stays on the full index.
IRREGULAR_DATE_LIMIT = 500


def _projection(source_path):
    """The Insights projection analysis_scan.view() returns for source_path, when it already
    exists and verifies against its proof; None otherwise. It never builds one: a records
    page must not wait on a projection build, and the full index answers it instead."""
    source_path = Path(source_path)
    version = data._stamp(source_path)
    identity = {'source_stamp': list(version), 'insights_projection': analysis_scan.CODE_SHA}
    path = store.ROOT / 'insights-views' / (store._key(identity) + '.sqlite')
    try:
        if not path.is_file() or not store.source_stable(source_path, version):
            return None
        return path if store._valid(path, path.with_suffix('.json'), identity) else None
    except (OSError, ValueError):
        return None


def _canonical_date(value):
    """A text value date.fromisoformat() accepts whose isoformat() is the value itself:
    for these, text order is date order, so `date <= cutoff` compares like the dates."""
    if type(value) is not str or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _irregular_dates(path):
    """Every distinct stored date value of the projection at path that is not canonical
    (NULL included). A projection is replaced, never rewritten, so the census is kept per
    signature. Only these values are still judged by insights_event_allowed()."""
    key = store._projection_signature(path)
    with _census_lock:
        if key in _date_census:
            _date_census.move_to_end(key)
            return _date_census[key]
    with closing(data._ro(path)) as db:
        irregular = tuple(value for (value,) in db.execute('SELECT DISTINCT date FROM facts')
                          if not _canonical_date(value))
    with _census_lock:
        _date_census[key] = irregular
        while len(_date_census) > 16:
            _date_census.popitem(last=False)
    return irregular


def _projected_page(projection, index, where, params, column, cutoff, eligible, limit, offset):
    """(count, rows) of the records filter, counted and ordered on the projection, the page's
    rows then read from the full index by source_id; None when the full index must answer."""
    irregular = _irregular_dates(projection)
    if len(irregular) > IRREGULAR_DATE_LIMIT:
        return None
    values = [value for value in irregular if value is not None]
    if irregular:
        # NULL and irregular values keep the UDF; every other stored value is canonical.
        odd = ['date IS NULL'] if len(values) < len(irregular) else []
        if values:
            odd.append('date IN (' + ','.join('?' * len(values)) + ')')
        test = 'CASE WHEN ' + ' OR '.join(odd) + ' THEN insights_event_allowed(date)=1 ELSE date <= ? END'
    else:
        test = 'date <= ?'
    clause = where + ' AND ' + column + ' != 0 AND ' + test
    arguments = params + values + [cutoff.isoformat()]
    with closing(data._ro(projection)) as db:
        db.create_function('insights_event_allowed', 1, eligible, deterministic=True)
        count = db.execute('SELECT COUNT(*) FROM facts WHERE ' + clause, arguments).fetchone()[0]
        ids = [row[0] for row in db.execute('SELECT source_id FROM facts WHERE ' + clause +
                                            ' ORDER BY date DESC,source_id LIMIT ? OFFSET ?', arguments + [limit, offset])]
    if not ids:
        return count, []
    with closing(data._ro(index)) as db:
        found = {row['source_id']: dict(row) for row in db.execute(
            'SELECT *,' + column + ' AS amount_cents FROM facts WHERE source_id IN (' + ','.join('?' * len(ids)) + ')', ids)}
    if len(found) != len(ids):
        return None
    return count, [found[source_id] for source_id in ids]


def records(directory, request, sources=None):
    _loaded()
    request = validate(request, records=True)
    source = data._source(directory, request, sources)
    if request.get('index_token') and data._token(source) != request['index_token']:
        raise ValueError('The source changed since this analysis. Refresh Insights before opening its records.')
    # The engine emits exact event/cycle constraints for each row. The global
    # cutoff also applies; undated facts remain eligible for nontemporal panels.
    column = data.BASES[request['basis']][1]
    filters = _scope_filters(request) + request.get('filters', [])
    path, pending = data._ready(source, retry=request.get('retry') is True)
    if pending:
        return pending
    token, stamp = data._token(source), data._stamp(path)
    cutoff = date.fromisoformat(request['as_of'])
    temporal = any(item.get('field') == 'date' for item in filters)
    def eligible(value):
        try:
            return int(date.fromisoformat(value) <= cutoff)
        except (TypeError, ValueError):
            return 0 if temporal else 1
    where, params = data._row_filter(filters, column)
    limit, offset = request.get('limit', 50), request.get('offset', 0)
    page = None
    if all(item.get('field') in _PROJECTED for item in filters):
        projection = _projection(path)
        if projection is not None:
            try:
                page = _projected_page(projection, path, where, params, column, cutoff, eligible, limit, offset)
            except sqlite3.Error:
                page = None  # the full index answers exactly as before
    if page is not None:
        count, rows = page
    else:
        where += ' AND ' + column + ' != 0 AND insights_event_allowed(date)=1'
        with closing(data._ro(path)) as db:
            db.create_function('insights_event_allowed', 1, eligible, deterministic=True)
            count = db.execute('SELECT COUNT(*) FROM facts WHERE ' + where, params).fetchone()[0]
            rows = [dict(row) for row in db.execute('SELECT *,' + column + ' AS amount_cents FROM facts WHERE ' + where +
                    ' ORDER BY date DESC,source_id LIMIT ? OFFSET ?', params + [limit, offset])]
    if not _stable(path, source, stamp, token):
        raise ValueError('The source changed during record inspection. Refresh Insights.')
    history.annotate(request['dataset'], rows)
    for row in rows:
        row.pop('contributor_key', None)
        for field in ('reasons', 'evidence_ids'):
            row[field] = json.loads(row[field])
    result = {'status': 'ready', 'dataset': source['id'], 'basis': request['basis'], 'rows': rows,
              'total_records': count, 'offset': offset, 'limit': limit, 'has_more': offset + len(rows) < count,
              'provenance': {**data._provenance(source), 'index_token': token}, 'amount_unit': 'cents',
              'notices': [history.NOTICE]}
    return result
