"""Verify analytical receipts against the actual published workbook totals.

The older workbook-build manifests are not authority for a later published
file. Read the current workbook, with a cache keyed by its filesystem stamp.
"""
from functools import lru_cache
import sqlite3
import zipfile
from xml.etree import ElementTree as ET

import contribution_data as contributions
import contribution_source_guard as source_guard
import native_scan

MAIN='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'


@lru_cache(maxsize=24)
def cycle_totals(stamp):
    # Contribution stamps are (path,size,mtime,ctime); the graph scanner uses
    # (path,mtime,ctime,size). Preserve every original field when adapting.
    native_stamp = (stamp[0], stamp[2], stamp[3], stamp[1])
    native = native_scan.tables(native_stamp, [dict(name=name, header=5,
        columns=['cycle', 'scope', 'total_giving'], end_row=52)
        for name in ('Spending Overview', 'Crypto Spending Overview')])
    if native is not None:
        if len(native['tables']) != 1:
            raise ValueError('The published report must have one Spending Overview worksheet.')
        table = next(iter(native['tables'].values()))
        if table['headers'] != ['cycle', 'scope', 'total_giving']:
            raise ValueError('The published Spending Overview layout does not expose its receipt totals.')
        result = {}
        for cycle, scope, amount in table['rows']:
            if scope != 'Total Giving':
                continue
            if cycle in result:
                raise ValueError('A published cycle contains more than one Total Giving total.')
            result[cycle] = contributions._cents(amount)
        if not result:
            raise ValueError('The published workbook has no cached receipt totals.')
        return result
    with zipfile.ZipFile(stamp[0]) as archive:
        book=ET.fromstring(archive.read('xl/workbook.xml'))
        relationships=ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        links={r.get('Id'):r.get('Target') for r in relationships}
        sheets=[s for s in book.find('{'+MAIN+'}sheets')
                if s.get('name') in ('Spending Overview','Crypto Spending Overview')]
        if len(sheets)!=1:raise ValueError('The published report must have one Spending Overview worksheet.')
        member=links[sheets[0].get('{'+REL+'}id')]
        member=member.lstrip('/') if member.startswith('/') else 'xl/'+member
        table=ET.fromstring(archive.read(member))
        cells={};needed=set()
        for row in table.findall('{'+MAIN+'}sheetData/{'+MAIN+'}row'):
            number=int(row.get('r'))
            if not 5<=number<=52:continue
            for cell in row:
                ref=cell.get('r');column=''.join(x for x in ref if x.isalpha())
                if column not in ('A','B','C'):continue
                value=cell.find('{'+MAIN+'}v')
                if cell.get('t')=='s' and value is not None:
                    cells[ref]=('shared',int(value.text));needed.add(int(value.text))
                elif cell.get('t')=='inlineStr':
                    cells[ref]=('value',''.join(t.text or '' for t in cell.iter('{'+MAIN+'}t')))
                elif value is not None:cells[ref]=('value',value.text)
        shared={}
        if needed:
            with archive.open('xl/sharedStrings.xml') as stream:
                index=0
                for _,element in ET.iterparse(stream,events=('end',)):
                    if element.tag=='{'+MAIN+'}si':
                        if index in needed:shared[index]=''.join(t.text or '' for t in element.iter('{'+MAIN+'}t'))
                        element.clear();index+=1
                        if index>max(needed):break
        def value(ref):
            kind,item=cells.get(ref,('value',''))
            return shared.get(item,'') if kind=='shared' else item
        if [value(k+'5') for k in ('A','B','C')]!=['Cycle','Scope','Total Giving']:
            raise ValueError('The published Spending Overview layout does not expose its receipt totals.')
        result={}
        for number in range(6,53):
            if value('B'+str(number))!='Total Giving':continue
            cycle=value('A'+str(number))
            if cycle in result:raise ValueError('A published cycle contains more than one Total Giving total.')
            result[cycle]=contributions._cents(value('C'+str(number)))
        if not result:raise ValueError('The published workbook has no cached receipt totals.')
        return result


def changed_since_scan(source):
    """Whether a file `_sources` hashed for this source has changed since. A request's guard and reader
    share one scan, so their hashes agree by construction; this is what still notices a report replaced
    in between. The hash is keyed by the file's stamp: a file that did not move costs one stat."""
    pairs=[(source['workbook'],source['workbook_sha256'])]
    if source.get('hydration_manifest'):pairs.append((source['database'],source['database_sha256']))
    try:return any(contributions._hash(path)!=expected for path,expected in pairs)
    except OSError:return True


def verify_sources(report_directory, dataset=None, strict=True, sources=None):
    # `sources` lets one request reuse a scan it already paid for.
    result=[]
    for source in (contributions._sources(report_directory) if sources is None else sources):
        if not source.get('available') or (dataset is not None and source['id']!=dataset):continue
        try:
            actual=cycle_totals(contributions._stamp(source['workbook']))
            expected={k:contributions._cents(v) for k,v in source['expected_cycle_totals'].items()}
            if actual!=expected:
                raise ValueError('Published workbook totals differ from the pinned contribution source. Reconcile this report before charting its contributions.')
            proof=source_guard.verify(source)
            result.append({'dataset':source['id'],'workbook_sha256':source['workbook_sha256'],
                           'database_sha256':source.get('database_sha256'),
                           'published_cycle_cents':actual,'matched':True,
                           'source_financial_universe_verified':proof['full_financial_universe_preserved'],
                           'source_proof_token':proof.get('proof_token')})
        except (ValueError, OSError, KeyError, TypeError, ArithmeticError, sqlite3.Error, zipfile.BadZipFile, ET.ParseError) as error:
            message=source['industry']+': '+str(error)
            if strict:raise ValueError(message) from error
            result.append({'dataset':source['id'],'matched':False,'error':message})
    return result
