"""Independent read-only checks of cached Excel values and report audit CSVs."""
import argparse
from collections import defaultdict
import csv
from datetime import date,datetime
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
import openpyxl


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def records(book,name,header=5):
    iterator=book[name].iter_rows(min_row=header,values_only=True)
    headers=next(iterator)
    for row in iterator:
        if row and row[0] is not None:yield dict(zip(headers,row))


def near(left,right,description):
    if abs(float(left or 0)-float(right or 0))>0.025:
        raise ValueError(f'{description}: {left} != {right}')


def verify(path):
    before=sha(path);counts=defaultdict(int);totals={};warnings=[]
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
        required={'Data Quality','Transfer Paths','Allocation Estimates','Allocation Coverage','Source Freshness','Classification Review'}
        if not required.issubset(book.sheetnames):raise ValueError('Missing downstream audit sheets')
        activity=defaultdict(float);direction=defaultdict(lambda:defaultdict(float))
        for row in records(book,'Downstream Activity'):
            key=(row['Cycle'],row['Source Committee ID'],row['Candidate ID'])
            value=float(row['Net Included Amount'] or 0)
            activity[key]+=value;direction[key][row['Support / Oppose']]+=value
            counts['downstream_rows']+=1
            for field in ('First Activity Date','Last Activity Date'):
                value=row[field]
                if not value:continue
                parsed=value.date() if isinstance(value,datetime) else date.fromisoformat(str(value)[:10])
                if parsed.year<1990:raise ValueError('Malformed downstream date')
                counts['verified_dates']+=1
        bridged=defaultdict(float);bridged_directions=defaultdict(lambda:defaultdict(float));available=defaultdict(set)
        for row in records(book,'Beneficiary Bridge'):
            source=(row['Cycle'],row['Source Committee ID'],row['Candidate ID'])
            target=(row['Cycle'],row['View'],row['Company'],row['Candidate ID'])
            near(row['Downstream Activity'],activity[source],'Bridge versus reported activity')
            bridged[target]+=float(row['Downstream Activity'] or 0)
            for label,value in direction[source].items():bridged_directions[target][label]+=value
            if row['Office'] in ('House','Senate','H','S'):
                available[target[:3]].add(row['Candidate ID'])
        rankings=defaultdict(list)
        for row in records(book,'Beneficiary Summary'):
            key=(row['Cycle'],row['View'],row['Company'],row['Candidate ID'])
            near(row['Downstream Activity'],bridged[key],'Summary versus bridge')
            near(row['Support Activity'],bridged_directions[key]['Support'],'Support direction')
            near(row['Oppose Activity'],bridged_directions[key]['Oppose'],'Opposition direction')
            nonopposed=float(row['Downstream Activity'] or 0)-float(row['Oppose Activity'] or 0)
            for party,field in [('D','Democratic Activity'),('R','Republican Activity')]:
                near(row[field],nonopposed if row['Party']==party else 0,'Party direction')
            near(sum(float(row[field] or 0) for field in ('Democratic Activity','Republican Activity','Other / Unclassified Activity')),nonopposed,'Party partition')
            rankings[key[:3]].append((int(row['Rank']),float(row['Downstream Activity'] or 0)))
            counts['beneficiary_rows']+=1
        for key,ranks in rankings.items():
            values=[value for rank,value in ranks]
            # Excel deliberately uses competition ranks: tied dollars share a
            # rank. Check row coverage and descending order without inventing
            # a distinct-rank requirement for ties.
            if len(ranks)!=min(100,len(available[key])) or values!=sorted(values,reverse=True):
                raise ValueError(f'Congress ranking is incomplete: {key}')
            if any(rank!=1+sum(other>value for other in values) for rank,value in ranks):
                raise ValueError(f'Incorrect competition rank: {key}')
        companies=list(records(book,'Company List'))
        for name in ('2021-2022','2023-2024','2025-2026'):
            if name not in book.sheetnames:continue
            raw=list(records(book,name,4))
            included=[row for row in raw if row.get('Fact Type','Master')=='Master']
            amount=sum(float(row.get('Analysis Amount') or 0) for row in included)
            near(amount,sum(float(row['Total Giving'] or 0) for row in companies if row['Cycle']==name),'Source facts versus company totals')
            totals[name]=round(amount,2)
            counts['cycle_rows']+=len(raw)
        for row in records(book,'Recipient List'):
            # ActBlue and WinRed are their own Conduit category (Paul, 2026-10-09);
            # a book built before that ruling files them under Carey and fails here.
            if row['Committee ID'] in ('C00401224','C00694323') and row['Recipient Category']!='Conduit':
                raise ValueError('Registered earmark conduit is misclassified')
        for row in records(book,'Allocation Coverage'):
            near(row['Nonnegative Model Funding'],float(row['Estimated Candidate Activity'] or 0)+float(row['Unresolved'] or 0),'Allocation conservation')
        for row in records(book,'Data Quality'):
            if row['Passed'] is False:warnings.append(row)
        counts['worksheets']=len(book.sheetnames)
    finally:book.close()
    # Scan all sheets, including cells not included in the summaries above.
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith('xl/worksheets/sheet') or not name.endswith('.xml'):continue
            with archive.open(name) as stream:
                for _,element in ET.iterparse(stream,events=('end',)):
                    if element.tag.endswith('}c') and element.get('t')=='e':
                        raise ValueError(f'Cached Excel error in {name}:{element.get("r")}')
                    element.clear()
    if sha(path)!=before:raise ValueError('Workbook changed during verification')
    return {'file':str(path.resolve()),'sha256':before,'status':'passed','counts':dict(counts),
        'cycle_totals':totals,'warnings':warnings,'congress_rank_counts':{'|'.join(key):len(value) for key,value in rankings.items() if key[1]=='Industry'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbooks',nargs='+',type=Path)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args();results=[]
    for path in args.workbooks:
        result=verify(path);results.append(result)
        print(f'{path.name}: passed; {result["counts"]["beneficiary_rows"]} beneficiary rows; {len(result["warnings"])} warnings',flush=True)
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(results,indent=2,default=str),encoding='utf-8')


if __name__=='__main__':main()
