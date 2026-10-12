"""Refresh official bulk snapshots with atomic files and verifiable receipts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import zipfile

FAMILIES={'cm':('fec_committee_master','cm.txt'),'cn':('fec_beneficiary','cn.txt'),
          'ccl':('fec_beneficiary',None),'pas2':('fec_beneficiary','itpas2.txt'),
          'oth':('fec_beneficiary','itoth.txt')}


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def refresh(root,cycle,family):
    folder,expected=FAMILIES[family]
    token=f'{family}{str(cycle)[-2:]}'
    target=root/folder/token;target.mkdir(parents=True,exist_ok=True)
    receipt_path=target/'source-receipt.json'
    old=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    headers={'User-Agent':'Mentat research data integrity/1.0'}
    if old.get('etag') and old.get('file') and (target/old['file']).exists() and digest(target/old['file'])==old.get('sha256'):
        headers['If-None-Match']=old['etag']
    url=f'https://www.fec.gov/files/bulk-downloads/{cycle}/{token}.zip'
    archive=target/f'download-{os.getpid()}.zip.pending'
    for attempt in range(4):
        try:
            with urlopen(Request(url,headers=headers),timeout=60) as response,archive.open('wb') as stream:
                shutil.copyfileobj(response,stream,1024*1024)
                remote={'etag':response.headers.get('ETag'),'last_modified':response.headers.get('Last-Modified')}
            break
        except HTTPError as error:
            if error.code==304:
                old['checked_at']=datetime.now(timezone.utc).isoformat()
                write_receipt(receipt_path,old)
                return old
            if error.code not in (429,500,502,503,504):raise
            if attempt==3:raise
            time.sleep(min(8,2**attempt))
        except (OSError,TimeoutError):
            if attempt==3:raise
            time.sleep(min(8,2**attempt))
    with zipfile.ZipFile(archive) as zipped:
        names=[n for n in zipped.namelist() if not n.endswith('/')]
        if expected is None:expected=f'ccl.txt'
        candidates=[n for n in names if Path(n).name.lower()==expected]
        if not candidates and family=='ccl':
            candidates=[n for n in names if Path(n).name.lower()==f'ccl{str(cycle)[-2:]}.txt']
        if len(candidates)!=1:raise ValueError(f'Unexpected archive member for {token}')
        name=Path(candidates[0]).name
        temporary=target/f'{name}.pending'
        with zipped.open(candidates[0]) as source,temporary.open('wb') as destination:
            shutil.copyfileobj(source,destination,1024*1024)
        if temporary.stat().st_size==0:raise ValueError(f'Empty FEC file: {token}')
        os.replace(temporary,target/name)
    archive.unlink()
    receipt={'cycle':cycle,'family':family,'url':url,'file':name,'sha256':digest(target/name),
             'bytes':(target/name).stat().st_size,'checked_at':datetime.now(timezone.utc).isoformat(),
             'downloaded_at':datetime.now(timezone.utc).isoformat(),**remote}
    write_receipt(receipt_path,receipt)
    print(f'{token}: verified {receipt["bytes"]:,} bytes',flush=True)
    return receipt


def write_receipt(path,value):
    temp=path.with_suffix('.pending')
    temp.write_text(json.dumps(value,indent=2),encoding='utf-8');os.replace(temp,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--cycles',type=int,nargs='+',default=[2022,2024,2026])
    parser.add_argument('--families',nargs='+',choices=list(FAMILIES),default=list(FAMILIES))
    parser.add_argument('--activate',action='store_true')
    parser.add_argument('--filing-header-directory',type=Path,
                        help='Optional preserved FEC header evidence; read locally, without fetching.')
    args=parser.parse_args()
    if any(c not in (2022,2024,2026) for c in args.cycles):parser.error('Unsupported cycle')
    args.root.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending=[pool.submit(refresh,args.root,c,f) for c in args.cycles for f in args.families]
        receipts=[future.result() for future in pending]
    write_receipt(args.root/'source-manifest.json',{'status':'succeeded','sources':receipts})
    if 'oth' in args.families:
        from fec_transfer_graph import transfer_index,publish_index
        # Twin of the loader in fec_transfer_graph.main(): preserved evidence
        # only, never a *.review.json. Under index VERSION 3 a transaction key
        # seen in more than one filing stays unresolved until a verified header
        # settles it, so without this the refreshed base index resolves none.
        headers=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(args.filing_header_directory.glob('*.json'))
                 if not p.name.endswith('.review.json')] if args.filing_header_directory else []
        for cycle in args.cycles:
            folder=args.root/'fec_beneficiary'/f'oth{str(cycle)[-2:]}'
            index=transfer_index(folder/'itoth.txt',cycle,headers)
            publish_index(folder,index)
    if args.activate:
        if set(args.families)!=set(FAMILIES) or set(args.cycles)!={2022,2024,2026}:
            raise ValueError('Activation requires all source families and supported cycles.')
        inputs=Path(__file__).resolve().parents[1]/'inputs'
        for cycle in args.cycles:
            # Curated leadership links are preserved and retain their original
            # timestamps; downloading bulk data does not refresh these records.
            source=inputs/'fec_beneficiary'/f'leadership{cycle}.csv'
            shutil.copy2(source,args.root/'fec_beneficiary'/source.name)
        write_receipt(inputs/'active_fec_sources.json',{'path':str(args.root.resolve()),
            'activated_at':datetime.now(timezone.utc).isoformat()})
    print('All requested source snapshots verified.',flush=True)


if __name__=='__main__':main()
