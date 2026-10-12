"""Combine verified report audit CSVs without treating allocations as floors."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report_directory',type=Path)
    parser.add_argument('--node',type=Path,required=True)
    args=parser.parse_args()
    rows=[];coverage=[];sources=[];warnings=[]
    for directory in sorted(args.report_directory.glob('*.audit')):
        path=directory/'attribution_summary.csv'
        if not path.exists():continue
        manifest=json.loads((directory/'audit-manifest.json').read_text(encoding='utf-8'))
        workbook=directory.parent/manifest['workbook_name']
        def signature(source):
            with source.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
        if signature(workbook)!=manifest['workbook_sha256']:
            raise ValueError('Workbook does not match its audit bundle')
        for name,expected in manifest['csv_sha256'].items():
            if signature(directory/name)!=expected:raise ValueError('Audit CSV checksum failed')
        with path.open(encoding='utf-8-sig',newline='') as stream:
            entries=list(csv.DictReader(stream))
        industry=next((row['industry'] for row in entries),directory.stem)
        for row in entries:
            row['cycle']=int(row['cycle'])
            row['direct']=float(row['direct'])
            # A cycle whose transfer index was not current publishes its three
            # conditional values BLANK (enrich_downstream.py): blank is not traced
            # and must reach the workbook blank, never as 0.
            estimates=('estimated_non_opposition','estimated_opposition','direct_plus_estimated_non_opposition')
            blank=[key for key in estimates if row[key]=='']
            if blank and len(blank)!=len(estimates):
                raise ValueError('Candidate summary mixes traced and untraced estimates')
            for key in estimates:row[key]=None if blank else float(row[key])
            if not blank and abs(row['direct']+row['estimated_non_opposition']-row['direct_plus_estimated_non_opposition'])>.005:
                raise ValueError('Candidate summary arithmetic does not reconcile')
            rows.append(row)
        for name,target in [('allocation_coverage',coverage),('quality_checks',warnings)]:
            with (directory/f'{name}.csv').open(encoding='utf-8-sig',newline='') as stream:
                for row in csv.DictReader(stream):
                    row={'industry':industry,**row}
                    if name=='allocation_coverage':
                        for key in ('model_funding','estimated_candidate_activity','unresolved'):
                            row[key]=float(row[key])
                        if abs(row['model_funding']-row['estimated_candidate_activity']-row['unresolved'])>max(.005,row['model_funding']*1e-10):
                            raise ValueError('Root coverage arithmetic does not reconcile')
                    target.append(row)
        for source in sorted(directory.glob('*.csv')):
            with source.open('rb') as stream:signature=hashlib.file_digest(stream,'sha256').hexdigest()
            sources.append({'industry':industry,'file':str(source.resolve()),'sha256':signature})
    if not rows:raise ValueError('No rebuilt attribution summaries found')
    keys=[(row['industry'],row['cycle'],row['candidate_id']) for row in rows]
    if len(keys)!=len(set(keys)):raise ValueError('Overlapping report cycles would double count candidates')
    rows.sort(key=lambda row:(row['industry'],row['cycle'],-(row['direct'] if row['direct_plus_estimated_non_opposition'] is None else row['direct_plus_estimated_non_opposition']),row['candidate_id']))
    path=args.report_directory/'attribution-build.json'
    path.write_text(json.dumps({'rows':rows,'coverage':coverage,'sources':sources,'warnings':warnings}),encoding='utf-8')
    subprocess.run([str(args.node),str(Path(__file__).with_name('attribution_workbook_builder.mjs')),str(path),str(args.report_directory/'Attributed Industry Spending.xlsx')],check=True)
    print(f'Candidate summaries: {len(rows)}; root coverage: {len(coverage)}; industries: {len(set(row["industry"] for row in rows))}')


if __name__=='__main__':main()
