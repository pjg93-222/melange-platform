"""Import the adopted historical classification packet, preserving every row.

No classifier is called and no stance is relabeled. A1 permits bill-class
inheritance only for already CU_DIRECT/SHARED model classifications. The
result must reproduce every approved all-crosspost aggregate before writing.
"""
from pathlib import Path
from collections import defaultdict
import argparse
import csv
import hashlib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.primacy_press import CLASS_W, LEDGER, RECEIPT


def import_packet(packet, output_root):
    def read(relative):
        with (packet / relative).open(encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))
    item_path='press/statement_items.csv'
    class_path='press/statement_classes_joined.csv'
    aggregate_path='live_augment/press_by_member_amended_check.csv'
    items=read(item_path)
    joined={r['id']:r for r in read(class_path)}
    rows=[]
    for index,item in enumerate(items,1):
        label=joined[item['id']]
        klass=(label['effective_class'] if label['model_class'] in ('CU_DIRECT','SHARED')
               else label['model_class'])
        rows.append({k:item[k] for k in ('id','member','bioguide','date','channel','url',
                                       'direction','intensity_scaled')} | {
            'model_class':label['model_class'], 'effective_class':klass,
            'text_sha256':hashlib.sha256(item['text'].encode()).hexdigest(), 'occurrence':index})
    groups=defaultdict(list)
    for row in rows: groups[row['member']].append(row)
    expected={r['member']:r for r in read(aggregate_path) if r['xp']=='all'}
    if set(groups)!=set(expected): raise ValueError('approved aggregate membership mismatch')
    for member, records in groups.items():
        num=round(sum(.1*float(r['intensity_scaled'] or 1)*CLASS_W[r['effective_class']]
                      *int(r['direction']) for r in records),9)
        den=round(sum(.1*float(r['intensity_scaled'] or 1) for r in records),9)
        ref=expected[member]
        if (num,den,len(records))!=(float(ref['factor_num_delta']),float(ref['factor_den_delta']),int(ref['n_items'])):
            raise ValueError('approved aggregate mismatch: '+member)
    raw=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows).encode('utf-8')
    receipt={'schema':'primacy-press-corpus-v1','variant':'CLASS_DOCK_PRESS_GRADE_ALL','vintage':'2026-09-20',
        'items':len(rows),'members':len(groups),'sha256':hashlib.sha256(raw).hexdigest(),
        'class_weights':CLASS_W,'crossposts':'all','third_party':'included','decay':'none',
        'denominator':'all admitted support/oppose statement intensity mass',
        'limitations':['Historical classified corpus; new and neutral items require a separately classified generation.',
                      'Association articles are included; this is not exclusively member-authored speech.'],
        'sources':[{'path':rel,'sha256':hashlib.sha256((packet/rel).read_bytes()).hexdigest()}
                   for rel in (item_path,class_path,aggregate_path)]}
    target=output_root/LEDGER
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(raw)
    (output_root/RECEIPT).write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8',newline='\n')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True,
                        help='explicit staging root; review generation before promotion')
    args=parser.parse_args()
    print(json.dumps(import_packet(args.packet,args.output_root),indent=2))
