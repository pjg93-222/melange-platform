"""Read-only, evidence-backed enrichment for Mentat Insights.

``enrich(request, context)`` accepts trusted server context: recipient_ids,
recipient_cycles (recipient_id/cycle pairs), companies, cycles, source_scope,
and optional inputs_dir/melange_root Paths. HTTP request paths are never read.
Money values in returned currency columns are integer cents.

Sources: Mentat's cycle-specific FEC cn/cm/ccl files; approved, dated company
affiliations; Melange's fec/legislators.csv, bill_repository_live.json, and
data/money/fec_refresh_current.json (SHA-verified immutable OpenFEC pages).
MENTAT_MELANGE_ROOT overrides the default Melange Systems directory. No source
database or production artifact is modified, and no network calls are made.

Additional saved OpenFEC snapshots can be installed below
MENTAT_INPUTS_DIR/analysis_integrations/<snapshot>/manifest.json. The schema is
{"status":"PASS", "checked_at":"ISO timestamp", "scope":"coverage description",
 "sources":[{"url":"https://api.open.fec.gov/v1/schedules/schedule_b/",
              "path":"page-0001.json", "sha256":"64 lowercase hex digits"}]}.
Pages retain the OpenFEC {"results":[...]} shape. Supported endpoints are
schedule_b, candidates/totals, and committee/<FEC ID>/totals. Files must be
inside the trusted input/Melange roots, hashes must match, and declared scope
is displayed. A manifest is an evidence adapter, not a claim of full coverage.
Never install receipt-reported PAC activity as an outgoing Schedule B ledger.

Cycle links are contextual identities, never allocations of receipt dollars.
Joint-fundraiser links stay explicit. Missing evidence remains unavailable.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

MENTAT = Path(__file__).resolve().parents[1]
DEFAULT_MELANGE = Path.home() / 'OneDrive' / 'Desktop' / 'Melange Systems'
ID = re.compile(r'(?:C\d{8}|[HSP]\d[A-Z0-9]{2}\d{5})\Z')
CANDIDATE = re.compile(r'[HSP]\d[A-Z0-9]{2}\d{5}\Z')
BIOGUIDE = re.compile(r'[A-Z]\d{6}\Z')
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PAGES = 512
MAX_ROWS = 500
LINK_LABELS = {'P': 'Principal campaign committee', 'A': 'Authorized candidate committee',
               'D': 'Leadership PAC link', 'J': 'Joint fundraiser link',
               'U': 'Unauthorized committee link'}


def _roots(context):
    return (Path(context.get('inputs_dir') or os.environ.get('MENTAT_INPUTS_DIR') or MENTAT / 'inputs').resolve(),
            Path(context.get('melange_root') or os.environ.get('MENTAT_MELANGE_ROOT') or DEFAULT_MELANGE).resolve())


def _signature(path):
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(path), None, None)


def _safe_child(path, roots):
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError('Saved evidence path leaves its approved source root.')
    return resolved


@lru_cache(maxsize=96)
def _read(signature):
    path, _, size = signature
    if size is None:
        raise ValueError('Saved source is missing.')
    if size > MAX_FILE_BYTES:
        raise ValueError('Saved source exceeds the bounded integration file limit.')
    raw = Path(path).read_bytes()
    if _signature(Path(path)) != signature:
        raise ValueError('Saved source changed while it was read; retry this view.')
    return raw, hashlib.sha256(raw).hexdigest()


def _json(path):
    raw, digest = _read(_signature(path))
    return json.loads(raw.decode('utf-8-sig')), digest


def _text(path):
    return _read(_signature(path))[0].decode('utf-8-sig', errors='replace')


def _url(value):
    """Only public source URLs; never echo API query strings or credentials."""
    try:
        parsed = urlsplit(str(value or ''))
        allowed = {'www.fec.gov', 'fec.gov', 'api.open.fec.gov', 'docquery.fec.gov', 'www.congress.gov',
                   'congress.gov', 'www.govinfo.gov', 'govinfo.gov'}
        if parsed.scheme != 'https' or parsed.hostname not in allowed or parsed.username or parsed.password:
            return ''
        # OpenFEC's actual filing-image links identify the document with a
        # bare numeric query. Preserve only that narrow public image selector;
        # API keys and every other query string remain excluded.
        image_query = ''
        if parsed.hostname == 'docquery.fec.gov' and parsed.path == '/cgi-bin/fecimg/':
            if not re.fullmatch(r'[0-9]{1,24}', parsed.query):
                return ''
            image_query = parsed.query
        return urlunsplit(('https', parsed.hostname, parsed.path, image_query, ''))
    except ValueError:
        return ''


def _date(value):
    try:
        return date.fromisoformat(str(value or '')[:10]).isoformat()
    except ValueError:
        return ''


def _cycle(value):
    match = re.search(r'(20\d{2})$', str(value or '').strip())
    if not match:
        return None
    year = int(match[1])
    return year if 2000 <= year <= 2100 and year % 2 == 0 else None


def _money(value):
    if value is None or value == '':
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return None
        return int((number * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def _panel(title, columns, rows=(), notices=(), summary='', status=None):
    rows = list(rows)
    total = len(rows)
    notices = list(dict.fromkeys(notices))
    if total > MAX_ROWS:
        notices.append(f'Showing {MAX_ROWS:,} of {total:,} matched evidence rows. Narrow the companies or cycles for the remaining rows.')
    return {'status': status or ('ready' if rows else 'unavailable'), 'title': title,
            'summary': summary, 'columns': [{'key': key, 'label': label, **({'type': kind} if kind else {})}
                                          for key, label, kind in columns],
            'rows': rows[:MAX_ROWS], 'total_rows': total, 'notices': notices}


def _pairs(context):
    pairs = set()
    for row in context.get('recipient_cycles', ()):
        identifier = str(row.get('recipient_id') or row.get('committee_id') or '').upper()
        year = _cycle(row.get('cycle'))
        if ID.fullmatch(identifier) and year:
            pairs.add((identifier, year))
    if not pairs:
        for identifier in context.get('recipient_ids', ()):
            identifier = str(identifier).upper()
            for year in map(_cycle, context.get('cycles', ())):
                if ID.fullmatch(identifier) and year:
                    pairs.add((identifier, year))
    return sorted(pairs, key=lambda row: (-row[1], row[0]))


def _manifest_paths(inputs, melange):
    return [melange / 'data/money/fec_refresh_current.json',
            *sorted((inputs / 'analysis_integrations').glob('*/manifest.json'))]


def sources_fingerprint(context=None):
    """Bounded dependency signature for the root's result-cache invalidation."""
    inputs, melange = _roots(context or {})
    paths = [inputs / 'verified_company_affiliations.json', melange / 'fec/legislators.csv',
             melange / 'bill_repository_live.json']
    paths += list((inputs / 'fec_beneficiary').glob('cn*/cn.txt'))
    paths += list((inputs / 'fec_beneficiary').glob('ccl*/ccl*.txt'))
    paths += list((inputs / 'fec_committee_master').glob('cm*/cm.txt'))
    for manifest in _manifest_paths(inputs, melange):
        paths.append(manifest)
        try:
            payload, _ = _json(_safe_child(manifest, (inputs, melange)))
            for source in payload.get('sources', [])[:MAX_PAGES]:
                path = Path(source.get('path', ''))
                paths.append(_safe_child(path if path.is_absolute() else manifest.parent / path, (inputs, melange)))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return hashlib.sha256(json.dumps(sorted(_signature(path) for path in set(paths))).encode()).hexdigest()


@lru_cache(maxsize=12)
def _masters(signatures):
    candidates, committees, links = {}, {}, {}
    for kind, year, signature in signatures:
        for line in _read(signature)[0].decode('utf-8-sig', errors='replace').splitlines():
            fields = [value.strip() for value in line.split('|')]
            if kind == 'cn' and len(fields) >= 10 and CANDIDATE.fullmatch(fields[0]):
                candidates[year, fields[0]] = {'name': fields[1], 'party': fields[2],
                                               'office': fields[5], 'state': fields[4], 'district': fields[6]}
            elif kind == 'cm' and len(fields) >= 15 and re.fullmatch(r'C\d{8}', fields[0]):
                committees[year, fields[0]] = {'name': fields[1], 'party': fields[10],
                                               'committee_type': fields[9], 'designation': fields[8],
                                               'connected_organization': fields[13]}
            elif kind == 'ccl' and len(fields) >= 6 and CANDIDATE.fullmatch(fields[0]):
                # FEC election year must agree with the cycle folder. Self-links
                # containing a committee ID in CAND_ID do not become candidates.
                if _cycle(fields[2]) == year and re.fullmatch(r'C\d{8}', fields[3]):
                    links.setdefault((year, fields[3]), set()).add((fields[0], fields[5]))
    return candidates, committees, links


def _profiles(context, inputs):
    pairs = _pairs(context)
    signatures, notices, sources = [], [], []
    for year in sorted({row[1] for row in pairs}):
        suffix = str(year)[-2:]
        for kind, folder, filename in [('cn', 'fec_beneficiary', 'cn.txt'),
                                        ('ccl', 'fec_beneficiary', 'ccl.txt'),
                                        ('cm', 'fec_committee_master', 'cm.txt')]:
            path = inputs / folder / f'{kind}{suffix}' / filename
            if kind == 'ccl' and not path.exists():
                path = path.with_name(f'ccl{suffix}.txt')
            try:
                path = _safe_child(path, (inputs,))
                raw, digest = _read(_signature(path))
                signatures.append((kind, year, _signature(path)))
                sources.append({'file': str(path), 'sha256': digest,
                                'url': f'https://www.fec.gov/files/bulk-downloads/{year}/{kind}{suffix}.zip'})
            except (OSError, ValueError):
                notices.append(f'The saved {year} FEC {kind} file is unavailable; that identity evidence remains unverified.')
    candidates, committees, links = _masters(tuple(signatures))
    rows, candidate_pairs = [], set()
    for identifier, year in pairs:
        cand = CANDIDATE.fullmatch(identifier)
        master = candidates.get((year, identifier), {}) if cand else committees.get((year, identifier), {})
        associations = {(identifier, 'candidate')} if cand else links.get((year, identifier), set())
        candidate_pairs.update((candidate, year) for candidate, _ in associations)
        offices = sorted({f'{item.get("office", "")} {item.get("state", "")} {item.get("district", "")}'.strip()
                          for candidate, _ in associations
                          if (item := candidates.get((year, candidate), {}))})
        rows.append({'recipient_id': identifier, 'cycle': year, 'name': master.get('name', identifier),
                     'party': master.get('party', ''), 'committee_type': master.get('committee_type', 'Candidate' if cand else ''),
                     'offices': '; '.join(offices), 'connected_organization': master.get('connected_organization', ''),
                     'candidate_ids': ', '.join(sorted({candidate for candidate, _ in associations})),
                     'candidate_links': '; '.join(f'{candidates.get((year, candidate), {}).get("name", candidate)} [{candidate}] — {LINK_LABELS.get(designation, designation)}'
                                                  for candidate, designation in sorted(associations)),
                     'identity_status': 'Matched exact ID and cycle' if master else 'Not in saved cycle master',
                     'source_url': f'https://www.fec.gov/data/{"candidate" if cand else "committee"}/{identifier}/'})
    notices.append('Candidate relationships are cycle-specific context. Joint-fundraiser and leadership links do not allocate receipts to candidates; no name matching is used.')
    matched_identities = sum(row['identity_status'] == 'Matched exact ID and cycle' for row in rows)
    panel = _panel('Recipient profiles', [('name', 'Recipient', ''), ('recipient_id', 'FEC ID', ''),
                    ('cycle', 'Cycle', ''), ('party', 'Party', ''), ('committee_type', 'Type', ''),
                    ('offices', 'Candidate offices', ''), ('connected_organization', 'Connected organization', ''),
                    ('candidate_links', 'Candidate relationships', ''), ('identity_status', 'Identity coverage', ''),
                    ('source_url', 'Source', 'url')], rows, notices,
                   f'{matched_identities:,} of {len(rows):,} recipient/cycle identities matched; {len(candidate_pairs):,} distinct linked candidate/cycle identities.',
                   ('partial' if matched_identities < len(rows) or notices[:-1] else 'ready') if rows else 'unavailable')
    panel['sources'] = sources
    return panel, candidate_pairs, candidates


def _saved_pages(inputs, melange):
    pages, notices, sources = [], [], []
    for manifest in _manifest_paths(inputs, melange):
        if not manifest.is_file():
            continue
        try:
            payload, digest = _json(_safe_child(manifest, (inputs, melange)))
            if payload.get('status') not in ('PASS', 'succeeded', 'complete'):
                raise ValueError('Saved OpenFEC manifest is not marked complete.')
            declared = payload.get('sources', [])
            if not isinstance(declared, list) or len(declared) > MAX_PAGES:
                raise ValueError('Saved OpenFEC manifest exceeds the page limit.')
            accepted = []
            for source in declared:
                url = _url(source.get('url'))
                endpoint = urlsplit(url).path
                if not url.startswith('https://api.open.fec.gov/') or not (
                        endpoint == '/v1/schedules/schedule_b/' or endpoint == '/v1/candidates/totals/'
                        or re.fullmatch(r'/v1/committee/C\d{8}/totals/', endpoint)):
                    continue
                path = Path(source.get('path', ''))
                path = _safe_child(path if path.is_absolute() else manifest.parent / path, (inputs, melange))
                page, page_digest = _json(path)
                if not re.fullmatch(r'[a-f0-9]{64}', str(source.get('sha256', ''))) or page_digest != source['sha256']:
                    raise ValueError('Saved OpenFEC page failed its SHA-256 integrity check.')
                if not isinstance(page.get('results'), list):
                    raise ValueError('Saved OpenFEC page has no results array.')
                accepted.append({'rows': page['results'], 'url': url, 'file': str(path), 'sha256': page_digest,
                                 'observed_at': payload.get('checked_at', ''), 'scope': str(payload.get('scope', 'Saved OpenFEC subset')),
                                 'endpoint': endpoint})
            pages.extend(accepted)
            sources.append({'file': str(manifest), 'sha256': digest, 'observed_at': payload.get('checked_at', ''),
                            'scope': str(payload.get('scope', 'Saved OpenFEC subset')), 'pages': len(accepted)})
        except (OSError, ValueError, TypeError, AttributeError) as error:
            notices.append(f'Saved FEC evidence: {error if isinstance(error, ValueError) else "Saved source could not be read."}')
    return pages, notices, sources


def _bounds(request, context):
    scope = context.get('source_scope') or {}
    end = _date(request.get('as_of') or scope.get('as_of'))
    start = _date(request.get('date_from') or scope.get('date_from'))
    return start, end


def _within(value, start, end):
    value = _date(value)
    return bool(value and (not start or value >= start) and (not end or value <= end))


def _affiliations(inputs, companies):
    path = inputs / 'verified_company_affiliations.json'
    if not path.is_file():
        return []
    payload, _ = _json(_safe_child(path, (inputs,)))
    return [row for row in payload.get('records', []) if row.get('company') in companies
            and row.get('relationship') == 'connected_pac' and re.fullmatch(r'C\d{8}', str(row.get('entity_id', '')))
            and _date(row.get('valid_from')) and (not row.get('valid_to') or _date(row['valid_to']))]


def _outgoing_patterns(rows):
    """Aggregate all retained payments, before the ledger's presentation cap.

    Candidate financial summaries are deliberately excluded. A payee ID, when
    supplied by the payment, is authoritative; otherwise grouping uses the
    exact reported name. Each denominator is one spending committee, cycle and
    attribution scope (dated connected PAC or selected recipient committee).
    """
    groups = defaultdict(list)
    for row in rows:
        if row['record_kind'] == 'Observed Schedule B payment':
            groups[row['entity'], row['cycle'], row['scope']].append(row)
    vendors, purposes, summaries = [], [], []
    for (sender, cycle, scope), payments in sorted(groups.items()):
        positive = sum(max(0, row['amount']) for row in payments)
        negative = sum(min(0, row['amount']) for row in payments)
        by_payee, by_purpose = defaultdict(list), defaultdict(list)
        for row in payments:
            by_payee[row.get('counterparty_id') or 'name:' + row['counterparty']].append(row)
            by_purpose[row['purpose'] or 'Not reported'].append(row)
        common = {'sender_id': sender, 'cycle': cycle,
                  'company': '; '.join(sorted({row['company'] for row in payments if row['company']})),
                  'scope': scope, 'channel': 'Observed outgoing Schedule B payments',
                  'denominator': 'Saved eligible outgoing Schedule B payments from this committee, cycle and attribution scope',
                  'source_url': f'https://www.fec.gov/data/committee/{sender}/'}
        def amounts(group):
            gross = sum(max(0, row['amount']) for row in group)
            adjustment = sum(min(0, row['amount']) for row in group)
            return {'amount_cents': gross + adjustment, 'gross_cents': gross,
                    'adjustment_cents': adjustment, 'records': len(group),
                    'positive_share': round(gross / positive, 8) if positive else None}
        for identity, group in by_payee.items():
            vendors.append({**common, 'payee_id': '' if identity.startswith('name:') else identity,
                            'payee': min(row['counterparty'] for row in group),
                            'identity_basis': 'Exact reported name; unresolved ID' if identity.startswith('name:') else 'Reported FEC ID',
                            **amounts(group)})
        for purpose, group in by_purpose.items():
            purposes.append({**common, 'purpose': purpose, **amounts(group)})
        payee_gross = sorted((sum(max(0, row['amount']) for row in group) for group in by_payee.values()), reverse=True)
        summaries.append({**common, 'amount_cents': positive + negative, 'gross_cents': positive,
                          'adjustment_cents': negative, 'records': len(payments),
                          'payee_groups': len(by_payee), 'reported_purposes': len(by_purpose),
                          'top_payee_share': round(payee_gross[0] / positive, 8) if positive else None,
                          'top_five_share': round(sum(payee_gross[:5]) / positive, 8) if positive else None})
    for table in (vendors, purposes, summaries):
        table.sort(key=lambda row: (-row['gross_cents'], row['sender_id'], -row['cycle'], row['scope'], row.get('payee', row.get('purpose', ''))))
    return {'vendor_patterns': vendors, 'purpose_patterns': purposes, 'spending_summaries': summaries}


def _spending(request, context, inputs, melange, candidate_pairs, candidates):
    pages, notices, sources = _saved_pages(inputs, melange)
    start, end = _bounds(request, context)
    try:
        affiliations = _affiliations(inputs, set(context.get('companies', ())))
    except (OSError, ValueError, TypeError, AttributeError):
        affiliations = []
        notices.append('The dated company affiliation file could not be read; company-PAC attribution is unavailable.')
    committee_pairs = {(identifier, year) for identifier, year in _pairs(context) if identifier.startswith('C')}
    selected_cycles = {year for _, year in _pairs(context)} | {year for year in map(_cycle, context.get('cycles', ())) if year}
    explicit_pacs = set(context.get('source_committee_ids', ()))
    observations, conflicts, outside = {}, set(), 0
    for page in pages:
        for row in page['rows']:
            if not isinstance(row, dict):
                continue
            cycle = _cycle(row.get('two_year_transaction_period') or row.get('cycle') or row.get('two_year_period'))
            if cycle not in selected_cycles:
                continue
            if page['endpoint'] == '/v1/schedules/schedule_b/':
                committee = str(row.get('committee_id', ''))
                payment_date = _date(row.get('disbursement_date'))
                matched = [item['company'] for item in affiliations if item['entity_id'] == committee
                           and _within(payment_date, _date(item.get('valid_from')), _date(item.get('valid_to')))]
                if not matched and committee not in explicit_pacs and (committee, cycle) not in committee_pairs:
                    continue
                if not _within(payment_date, start, end):
                    outside += 1
                    continue
                if row.get('memo_code') == 'X' or row.get('memoed_subtotal'):
                    continue
                amount = _money(row.get('disbursement_amount'))
                sub_id = str(row.get('sub_id', ''))
                if amount is None or not sub_id:
                    notices.append('Payment rows without an exact transaction ID or finite amount were excluded.')
                    continue
                identity = ('payment', committee, cycle, sub_id)
                payee_id = str(row.get('recipient_committee_id') or row.get('candidate_id') or '').strip().upper()
                value = {'record_kind': 'Observed Schedule B payment', 'entity': committee,
                         'company': ', '.join(sorted(set(matched))), 'cycle': cycle, 'date': payment_date,
                         'counterparty': str(row.get('recipient_name') or row.get('candidate_name') or row.get('recipient_committee_id') or 'Not reported'),
                         'counterparty_id': payee_id if ID.fullmatch(payee_id) else '',
                         'purpose': str(row.get('disbursement_description') or row.get('disbursement_type_description') or ''),
                         'amount': amount, 'receipts': None, 'cash': None, 'debt': None,
                         'scope': 'Dated connected company PAC' if matched else 'Selected committee outgoing activity',
                         'record_id': sub_id, 'source_url': _url(row.get('pdf_url')) or f'https://www.fec.gov/data/committee/{committee}/',
                         'observed_at': page['observed_at'], 'evidence_file': page['file'], 'evidence_sha256': page['sha256']}
            else:
                is_candidate_total = page['endpoint'] == '/v1/candidates/totals/'
                candidate = str(row.get('candidate_id', '')) if is_candidate_total else ''
                committee = str(row.get('committee_id', ''))
                if is_candidate_total and not CANDIDATE.fullmatch(candidate):
                    notices.append('A saved candidate-finance row lacks a valid candidate ID and was excluded.')
                    continue
                if not is_candidate_total:
                    endpoint_committee = re.fullmatch(r'/v1/committee/(C\d{8})/totals/', page['endpoint'])[1]
                    if committee != endpoint_committee:
                        notices.append('A saved committee-finance row disagrees with its source committee ID and was excluded.')
                        continue
                coverage_start = _date(row.get('coverage_start_date'))
                coverage_end = _date(row.get('coverage_end_date'))
                finance_companies = []
                if candidate:
                    if (candidate, cycle) not in candidate_pairs:
                        continue
                    entity = candidate
                else:
                    # A connected PAC may have no counted receipts in the
                    # selected industry. Its independently saved outgoing
                    # finances still belong in this contextual view. Require
                    # the entire financial period to fit the approved dated
                    # affiliation; never attribute an earlier owner's period.
                    finance_companies = [item['company'] for item in affiliations
                                         if item['entity_id'] == committee and coverage_start and coverage_end
                                         and _within(coverage_start, _date(item.get('valid_from')), _date(item.get('valid_to')))
                                         and _within(coverage_end, _date(item.get('valid_from')), _date(item.get('valid_to')))]
                    if (committee, cycle) not in committee_pairs and committee not in explicit_pacs and not finance_companies:
                        continue
                    entity = committee
                # Totals are an entire reported period, never a partial-period estimate.
                if (not _within(coverage_end, '', end) or (coverage_start and coverage_start > coverage_end)
                        or (start and (not coverage_start or coverage_start < start))):
                    outside += 1
                    continue
                identity = ('finances', entity, cycle, coverage_start, coverage_end)
                value = {'record_kind': 'Reported candidate finances' if candidate else 'Reported committee finances',
                         'entity': str(row.get('name') or candidates.get((cycle, candidate), {}).get('name') or entity),
                         'company': ', '.join(sorted(set(finance_companies))), 'cycle': cycle, 'date': coverage_end, 'counterparty': entity,
                         'purpose': f'Reported coverage {coverage_start or "unknown"} through {coverage_end}',
                         'amount': _money(row.get('disbursements')), 'receipts': _money(row.get('receipts')),
                         'cash': _money(row.get('cash_on_hand_end_period')), 'debt': _money(row.get('debts_owed_by_committee')),
                         'scope': 'All reported campaign activity; not selected-industry dollars', 'record_id': entity,
                         'source_url': f'https://www.fec.gov/data/{"candidate" if candidate else "committee"}/{entity}/',
                         'observed_at': page['observed_at'], 'evidence_file': page['file'], 'evidence_sha256': page['sha256']}
            comparable = {key: value[key] for key in value if key not in ('observed_at', 'evidence_file', 'evidence_sha256')}
            if identity in observations and observations[identity][0] != comparable:
                conflicts.add(identity)
            else:
                observations[identity] = comparable, value
    rows = [value for identity, (_, value) in observations.items() if identity not in conflicts]
    # Multiple saved periods can coexist after a new snapshot is imported. Keep
    # one latest eligible financial period per entity/cycle; period totals do
    # not become additive observations merely because both files are retained.
    latest_periods = {}
    for row in rows:
        if row['record_kind'].startswith('Reported '):
            key = row['record_id'], row['cycle']
            latest_periods[key] = max(latest_periods.get(key, ''), row['date'])
    older_periods = sum(1 for row in rows if row['record_kind'].startswith('Reported ')
                        and row['date'] != latest_periods[row['record_id'], row['cycle']])
    rows = [row for row in rows if not row['record_kind'].startswith('Reported ')
            or row['date'] == latest_periods[row['record_id'], row['cycle']]]
    period_counts = {}
    for row in rows:
        if row['record_kind'].startswith('Reported '):
            key = row['record_id'], row['cycle']
            period_counts[key] = period_counts.get(key, 0) + 1
    ambiguous_periods = {key for key, count in period_counts.items() if count > 1}
    rows = [row for row in rows if not row['record_kind'].startswith('Reported ')
            or (row['record_id'], row['cycle']) not in ambiguous_periods]
    if ambiguous_periods:
        notices.append(f'{len(ambiguous_periods):,} entity/cycle financial summaries have competing period definitions and were excluded.')
    if older_periods:
        notices.append(f'{older_periods:,} older financial periods were superseded by the latest saved period at or before the cutoff; period totals were not added together.')
    rows.sort(key=lambda row: (row['record_kind'], row['date'], abs(row['amount'] or 0), row['entity']), reverse=True)
    if conflicts:
        notices.append(f'{len(conflicts)} conflicting saved financial identities were excluded; reconcile their snapshots before combining them.')
    if outside:
        notices.append(f'{outside:,} saved records fell outside the selected date window or lacked usable coverage dates.')
    notices += ['Outgoing payments and financial totals are separate views; do not add them together or to industry receipts.',
                'Saved direct-PAC Schedule B coverage is limited. It does not establish a complete expenditure ledger or vendor market share. Refunds remain signed.']
    if not pages:
        notices.append('This view needs verified saved OpenFEC disbursement or financial-summary evidence. No eligible saved source is available.')
    elif not rows:
        notices.append('The saved OpenFEC pages contain no eligible financial records for these IDs, cycles and dates. Missing coverage is not zero spending.')
    panel = _panel('Outgoing payments and campaign finances', [('record_kind', 'Evidence type', ''), ('entity', 'Entity', ''),
                    ('company', 'Connected company', ''), ('cycle', 'Cycle', ''), ('date', 'Date / coverage end', 'date'),
                    ('counterparty', 'Payee / FEC ID', ''), ('purpose', 'Purpose / period', ''), ('amount', 'Disbursements', 'money'),
                    ('receipts', 'Receipts', 'money'), ('cash', 'Cash', 'money'), ('debt', 'Debt', 'money'),
                    ('scope', 'Scope', ''), ('source_url', 'Source', 'url')], rows, notices,
                   f'{len(rows):,} matched saved records. Campaign totals cover the entire candidate or committee; company payments require dated affiliations.',
                   'partial' if rows else 'unavailable')
    panel['sources'] = sources
    patterns = _outgoing_patterns(rows)
    limit = request.get('top_n', 25)
    limit = min(MAX_ROWS, max(1, limit)) if type(limit) is int else min(MAX_ROWS, 25)
    for key, table in patterns.items():
        panel[key] = table[:limit]
        panel[key + '_total_rows'] = len(table)
        if len(table) > limit:
            panel['notices'].append(f'{key.replace("_", " ").capitalize()}: showing {limit:,} of {len(table):,} rows; all retained outgoing payments enter the calculations.')
    panel['notices'].append('Payee and reported-purpose patterns use saved outgoing payments only, grouped separately by spending committee, cycle and attribution scope. Candidate financial totals are excluded. Shares use positive payments; negative adjustments remain separate. Payee groups use reported IDs or exact names, not verified unique vendors. A payee may be a campaign or nonprofit rather than a commercial vendor.')
    return panel


def _legislation(request, context, melange, candidate_pairs, candidates):
    notices, rows = [], []
    source = melange / 'bill_repository_live.json'
    crosswalk = melange / 'fec/legislators.csv'
    sources = []
    try:
        bills, digest = _json(_safe_child(source, (melange,)))
        mapping, conflicts = {}, set()
        text = _text(_safe_child(crosswalk, (melange,)))
        for row in csv.DictReader(text.splitlines()):
            bio = str(row.get('bioguide_id', ''))
            if not BIOGUIDE.fullmatch(bio):
                continue
            for candidate in re.findall(r'[HSP]\d[A-Z0-9]{2}\d{5}', row.get('fec_ids', '')):
                if candidate in mapping and mapping[candidate] != bio:
                    conflicts.add(candidate)
                mapping[candidate] = bio
        wanted = {}
        for candidate, cycle in candidate_pairs:
            if candidate in mapping and candidate not in conflicts:
                wanted.setdefault((mapping[candidate], (cycle - 1788) // 2), set()).add((candidate, cycle))
        unmapped = {candidate for candidate, _ in candidate_pairs if candidate not in mapping or candidate in conflicts}
        if unmapped:
            notices.append(f'{len(unmapped):,} linked candidate IDs lack an unambiguous saved Bioguide crosswalk; no name-based legislative match was made.')
        start, end = _bounds(request, context)
        collection = bills.get('bills', {})
        if not isinstance(collection, dict):
            raise ValueError('The saved bill repository has an unsupported schema.')
        undated = 0
        for key, bill in collection.items():
            congress = bill.get('congress')
            sponsors = [(bill.get('sponsor') or {}, 'Sponsor')]
            sponsors += [(member, 'Cosponsor') for member in bill.get('cosponsors', []) if isinstance(member, dict)]
            for member, relationship in sponsors:
                identities = wanted.get((member.get('bioguide'), congress), set())
                if not identities:
                    continue
                event_date = _date(member.get('date') or (bill.get('introduced_date') if relationship == 'Sponsor' else ''))
                if not event_date:
                    undated += 1
                    continue
                if not _within(event_date, start, end):
                    continue
                latest = bill.get('latest_action') or {}
                latest_date = _date(latest.get('date'))
                action = str(latest.get('text', '')) if _within(latest_date, '', end) else ''
                withdrawn = _date(member.get('withdrawn_date'))
                withdrawn = withdrawn if _within(withdrawn, '', end) else ''
                rows.append({'date': event_date, 'member': str(member.get('name') or member.get('bioguide')),
                             'bioguide_id': member.get('bioguide'), 'candidate_ids': ', '.join(sorted({candidate for candidate, _ in identities})),
                             'cycle': ', '.join(str(cycle) for cycle in sorted({cycle for _, cycle in identities})),
                             'bill': str(bill.get('display') or key), 'title': str(bill.get('title', '')),
                             'relationship': relationship, 'withdrawn_date': withdrawn, 'congress': congress,
                             'topics': ', '.join(map(str, bill.get('areas', []))), 'latest_action': action,
                             'latest_action_date': latest_date if action else '', 'source_url': _url(bill.get('url')),
                             'source_status': 'Saved source marked stale' if bill.get('stale') else 'Saved legislative evidence',
                             'observed_at': str(bill.get('updated_at') or bills.get('updated_at') or '')})
        rows.sort(key=lambda row: (row['date'], row['bill'], row['member']), reverse=True)
        if undated:
            notices.append(f'{undated:,} matched relationships lack a usable joined date and were excluded from the timeline.')
        stale_count = sum(row['source_status'] == 'Saved source marked stale' for row in rows)
        if stale_count:
            notices.append(f'{stale_count:,} relationships come from bills marked stale by the saved repository; their source status is preserved in the records.')
        sources = [{'file': str(source), 'sha256': digest, 'observed_at': bills.get('updated_at', ''), 'tracked_bills': len(collection)},
                   {'file': str(crosswalk), 'sha256': _read(_signature(crosswalk))[1]}]
    except (OSError, ValueError, TypeError, AttributeError) as error:
        notices.append(f'Legislative context needs the saved Melange bill collection and an exact FEC-to-Bioguide identity crosswalk. {error if isinstance(error, ValueError) else "The configured saved sources could not be read."}')
    notices += ["Coverage is Melange's tracked legislative collection, including its credit-union and financial-policy focus; it is not all congressional activity or a complete industry agenda.",
                'Relationships are matched by explicit IDs and the Congress corresponding to the selected cycle. Dates are sponsorship dates; no contribution dollars are assigned to bills and temporal association does not establish causation.',
                'Saved updates may include evidence learned after an event. These rows are current historical context, not a reconstruction of what was known at the selected date.']
    panel = _panel('Legislative context', [('date', 'Joined / introduced', 'date'), ('member', 'Member', ''),
                    ('bill', 'Bill', ''), ('title', 'Title', ''), ('relationship', 'Role', ''),
                    ('withdrawn_date', 'Withdrawn', 'date'), ('topics', 'Tracked topics', ''),
                    ('latest_action', 'Latest saved action by cutoff', ''), ('source_url', 'Source', 'url')], rows, notices,
                   f'{len(rows):,} dated sponsor or cosponsor relationships match the selected recipient identities.',
                   'partial' if rows else 'unavailable')
    panel['sources'] = sources
    return panel


def enrich(request, context):
    """Return only requested named integration panels, using trusted local roots."""
    requested = request.get('panels')
    requested = set(requested) if isinstance(requested, (list, tuple, set)) else {'profiles', 'spending', 'legislation'}
    selected = requested & {'profiles', 'spending', 'legislation'}
    if not selected:
        return {}
    inputs, melange = _roots(context)
    profile, candidate_pairs, candidates = _profiles(context, inputs)
    result = {}
    if 'profiles' in selected:
        result['profiles'] = profile
    if 'spending' in selected:
        result['spending'] = _spending(request, context, inputs, melange, candidate_pairs, candidates)
    if 'legislation' in selected:
        result['legislation'] = _legislation(request, context, melange, candidate_pairs, candidates)
    return result
