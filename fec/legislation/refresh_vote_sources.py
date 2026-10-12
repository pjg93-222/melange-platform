"""Acquire a complete new Voteview snapshot before the network-free vote build."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,csv,json,urllib.request,hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from paths import ROOT
from moneyball_core import dump_atomic


def refresh(root,install=True):
    root=Path(root);target=root/'data/legislation/vote_source_snapshot.json'
    config=json.loads(target.read_text(encoding='utf-8'))
    now=datetime.now(timezone.utc)
    out=root/'data/legislation/vote_sources'/now.strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    def fetch(pair):
        congress,kind=pair;name=f'HS{congress}_{kind}.csv'
        url=f'https://voteview.com/static/data/out/{kind}/{name}'
        with urllib.request.urlopen(url,timeout=120) as response:
            data=response.read(100000001)
            modified=response.headers.get('Last-Modified')
        if len(data)>100000000: raise ValueError('vote source exceeds envelope')
        file=out/name;file.write_bytes(data)
        required={'congress','chamber','icpsr'} if kind=='members' else {'congress','chamber','rollnumber','cast_code','icpsr'} if kind=='votes' else {'congress','chamber','rollnumber','bill_number','date','vote_question'}
        with file.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            if not required.issubset(reader.fieldnames or []): raise ValueError('vote source schema mismatch '+name)
            count=0
            for row in reader:
                if int(row['congress'])!=congress: raise ValueError('vote source Congress mismatch')
                count+=1
        if not count: raise ValueError('empty vote source '+name)
        return {'path':str(file),'url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                'retrieved_at':now.isoformat(),'last_modified':modified,'rows':count}
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            files=list(pool.map(fetch,[(c,k) for c in (117,118,119) for k in ('members','rollcalls','votes')]))
        receipt={'schema':'voteview-source-download-v1','status':'PASS','checked_at':now.isoformat(),
                 'source_documentation':'https://voteview.com/articles/data_help_votes','files':files}
        dump_atomic(receipt,str(out/'receipt.json'))
        if install: dump_atomic({**config,'source_dir':str(out)},str(target))
        return receipt
    except Exception as exc:
        dump_atomic({'status':'FAIL','checked_at':now.isoformat(),'error':str(exc)},str(out/'receipt.json'))
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--no-install',action='store_true');a=p.parse_args()
    d=refresh(ROOT,not a.no_install);print('Verified vote source files:',len(d['files']))
