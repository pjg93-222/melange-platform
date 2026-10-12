"""Recover filing-level evidence without downloading whole FEC transaction files.

Headers establish a filing's form and original-report reference, not whether an
individual transaction is current, unique, or even present in that filing.
The format positions below follow FEC's published version 8.5 specification.
Its change log leaves these report fields unchanged from 8.4 and changes only
the version value in HDR. For 8.3 only the literal cover form and committee are
read; dates and amendment-family fields remain unresolved. This limited record
structure is documented in FEC's 8.3 manual (validator errors 1, 2, 8 and 12).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

from report_source_guard import atomic

SPEC_URL = 'https://docquery.fec.gov/formatspecs/FEC_EFO_Format_Specifications.xlsx'
PREFIX_BYTES = 8192


def parse_header(prefix, file_num):
    if not re.fullmatch(r'[1-9][0-9]*', str(file_num)):
        raise ValueError('Invalid FEC filing number')
    # Do not use str.splitlines(): it treats the FEC field separator as a newline.
    lines = prefix.split(b'\n')
    if len(lines) < 3:
        raise ValueError('Incomplete filing header prefix')
    try:
        text = b'\n'.join(lines[:2]).decode('utf-8-sig')
    except UnicodeDecodeError:
        text = b'\n'.join(lines[:2]).decode('cp1252')
    delimiter = '\x1c' if '\x1c' in text else ','
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(rows) != 2 or len(rows[0]) < 5 or [v.strip() for v in rows[0][:2]] != ['HDR', 'FEC']:
        raise ValueError('Not a valid FEC filing header')
    hdr, report = rows
    # Original filings may omit trailing empty amendment fields; field padding
    # is distinct from inventing an original-report identifier.
    hdr += [''] * max(0, 8-len(hdr))
    hdr[2] = hdr[2].strip()
    if hdr[2] not in ('8.3', '8.4', '8.5'):
        raise ValueError(f'Unsupported filing format {hdr[2]}; recover evidence using a supported adapter')
    match = re.fullmatch(r'(F3X|F3P|F3|F4)([NAT])', report[0])
    if not match or len(report) < 2 or not re.fullmatch(r'C[0-9]{8}', report[1]):
        raise ValueError('Unsupported form or invalid filing committee')
    form, amendment = match.groups()
    if hdr[2] == '8.3':
        return dict(file_num=int(file_num), filing_form=form, committee_id=report[1],
                    amendment_indicator=amendment, original_file_num=None,
                    amendment_number=None, report_type=None, coverage_start=None,
                    coverage_end=None, format_version=hdr[2], relationship_evidence=False,
                    specification='https://efilingapps.fec.gov/fecfiledoc/fecfiledoc.pdf')
    code_index, start_index, end_index = {'F3':(11,15,16), 'F3P':(11,15,16),
                                        'F3X':(9,13,14), 'F4':(10,11,12)}[form]
    if len(report) <= end_index:
        raise ValueError('Incomplete filing summary record')
    dates = []
    for value in (report[start_index], report[end_index]):
        dates.append(datetime.strptime(value, '%Y%m%d').date().isoformat())
    if dates[0] > dates[1]:
        raise ValueError('Reversed filing coverage dates')
    original = hdr[5].strip()
    if original and not re.fullmatch(r'(?:FEC-\s*)?[1-9][0-9]*', original):
        raise ValueError('Invalid original filing reference')
    return dict(file_num=int(file_num), filing_form=form, committee_id=report[1],
                amendment_indicator=amendment, original_file_num=int(re.sub(r'^FEC-\s*','',original)) if original else None,
                amendment_number=hdr[6], report_type=report[code_index],
                coverage_start=dates[0], coverage_end=dates[1], format_version=hdr[2])


def fetch_header(file_num, directory, transport=None):
    if not re.fullmatch(r'[1-9][0-9]*', str(file_num)):
        raise ValueError('Invalid FEC filing number')
    directory=Path(directory); directory.mkdir(parents=True, exist_ok=True)
    path=directory/f'{file_num}.json'
    url=f'https://docquery.fec.gov/dcdev/posted/{file_num}.fec'
    if path.exists():
        cached=json.loads(path.read_text(encoding='utf-8'))
        prefix=bytes.fromhex(cached['prefix_hex'])
        if cached['url'] != url or hashlib.sha256(prefix).hexdigest() != cached['prefix_sha256']:
            raise ValueError('Filing header cache integrity check failed')
        if parse_header(prefix,file_num) != cached['header']:
            raise ValueError('Cached filing interpretation differs; revalidate the evidence')
        return cached
    review_path=directory/f'{file_num}.review.json'
    if review_path.exists():
        saved=json.loads(review_path.read_text(encoding='utf-8'))
        prefix=bytes.fromhex(saved['prefix_hex'])
        if saved['url'] != url or hashlib.sha256(prefix).hexdigest() != saved['prefix_sha256']:
            raise ValueError('Filing review cache integrity check failed')
    elif transport:
        prefix=transport(url)
    else:
        request=Request(url,headers={'Range':f'bytes=0-{PREFIX_BYTES-1}', 'User-Agent':'Mentat FEC evidence recovery'})
        with urlopen(request,timeout=30) as response:
            if response.status == 206 and not response.headers.get('Content-Range','').startswith('bytes 0-'):
                raise ValueError('FEC returned the wrong byte range')
            # Bounded read even if the server ignores Range. Never download a
            # multi-gigabyte filing merely to establish its form.
            prefix=response.read(PREFIX_BYTES)
    result=dict(url=url, retrieved_at=datetime.now(timezone.utc).isoformat(),
                prefix_hex=prefix.hex(), prefix_sha256=hashlib.sha256(prefix).hexdigest(),
                evidence_scope='filing_header_only', specification=SPEC_URL)
    try:
        result['header']=parse_header(prefix,file_num)
    except ValueError as error:
        # Preserve unsupported evidence for a later adapter.
        atomic(directory/f'{file_num}.review.json',{**result,'error':str(error)})
        raise
    atomic(path,result)
    return result


def apply_header(connection, evidence):
    """Fill a missing form only, in an already initialized private recovery DB.

    Match both the existing filing number and committee. Preserve all financial
    facts and row-level metadata. A header never certifies a transaction link.
    The caller owns the transaction and ensures the original source is immutable.
    """
    prefix=bytes.fromhex(evidence['prefix_hex'])
    header=evidence['header']
    if hashlib.sha256(prefix).hexdigest() != evidence['prefix_sha256'] or parse_header(prefix,header['file_num']) != header:
        raise ValueError('Filing header evidence was modified')
    expected_url=f'https://docquery.fec.gov/dcdev/posted/{header["file_num"]}.fec'
    if evidence['url'] != expected_url:
        raise ValueError('Filing header URL disagrees with identity')
    changed=0
    for table in ('contributions','corporate_giving'):
        columns={r[1] for r in connection.execute(f'PRAGMA table_info({table})')}
        if not {'filing_form','file_num','committee_id','company_match_status'}.issubset(columns):
            raise ValueError('Initialize the private copy with hydrate_receipt_metadata first')
        rows=connection.execute(f"SELECT sub_id,committee_id,filing_form FROM {table} WHERE file_num=? AND company_match_status='accepted'",
                                (header['file_num'],)).fetchall()
        for identifier, committee, prior in rows:
            if committee != header['committee_id']:
                raise ValueError(f'Filing committee conflicts with stored source {identifier}')
            if prior and prior != header['filing_form']:
                raise ValueError(f'Filing form conflicts with stored source {identifier}')
            if prior:
                continue
            connection.execute(f'UPDATE {table} SET filing_form=? WHERE sub_id=?', (header['filing_form'],identifier))
            connection.execute('INSERT INTO receipt_metadata_history(source_id,source_table,evidence_source,changes_json) VALUES(?,?,?,?)',
                               (str(identifier),table,expected_url+'#header-sha256='+evidence['prefix_sha256'],
                                json.dumps({'filing_form':{'before':prior,'after':header['filing_form']}})))
            changed+=1
    return changed
