# -*- coding: utf-8 -*-
"""Extract the supplied Senate-expert Big Five appendix into a frozen gold artifact.

The source PDF is not redistributed.  The derived artifact preserves every table value, panel,
rating count, source name, PDF page, and the SHA-256 of the exact input document.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from moneyball_core import dump_atomic
from paths import ROOT

TRAITS=('O','C','E','A','ES')
EXPECTED_COUNTS={'senate_insiders':129,'academic_experts':125}
OUTPUT=os.path.join(ROOT,'data','stance','big5_senate_expert_gold.json')


def sha256_file(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def clean_name(value):
    return re.sub(r'\s+',' ',str(value or '')).strip()


def extract(path):
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError('pdfplumber is required only for appendix extraction') from exc
    records=[]
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages)!=7:
            raise ValueError('expected the seven-page online appendix; found %d pages'%len(pdf.pages))
        first=' '.join((pdf.pages[0].extract_text() or '').split())
        if 'Personality on the Hill' not in first or 'Table A1' not in first or 'Table A2' not in first:
            raise ValueError('source is not the expected Personality on the Hill appendix')
        for page_number,page in enumerate(pdf.pages[1:],2):
            panel='senate_insiders' if page_number<=4 else 'academic_experts'
            tables=page.extract_tables()
            if len(tables)!=1:
                raise ValueError('page %d: expected one table, found %d'%(page_number,len(tables)))
            for row in tables[0]:
                if not row or len(row)!=7 or not clean_name(row[0]) or clean_name(row[0]).startswith('Table A'):
                    continue
                try:
                    values=[None if not clean_name(value) else float(clean_name(value)) for value in row[1:6]]
                    rating_n=int(clean_name(row[6]))
                except (TypeError,ValueError):
                    # Column headings are the only non-data row that reaches this branch.
                    continue
                if rating_n<1 or any(value is not None and not 1.0<=value<=7.0 for value in values):
                    raise ValueError('page %d: invalid scale/rating count for %s'%(page_number,row[0]))
                records.append({
                    'panel':panel,'source_name':clean_name(row[0]),'page':page_number,
                    'ratings_n':rating_n,**dict(zip(TRAITS,values)),
                })
    counts={panel:sum(row['panel']==panel for row in records) for panel in EXPECTED_COUNTS}
    if counts!=EXPECTED_COUNTS:
        raise ValueError('unexpected table counts: %r (expected %r)'%(counts,EXPECTED_COUNTS))
    keys=[(row['panel'],row['source_name']) for row in records]
    if len(keys)!=len(set(keys)):
        raise ValueError('duplicate panel/name rows in appendix extraction')
    return {
        'schema':'big5-senate-expert-gold-v1','status':'FROZEN_EXTERNAL_GOLD',
        'source':{
            'appendix_title':'Online Appendix',
            'article_title':'Personality on the Hill: Expert Evaluations of U.S. Senators Psychological Traits',
            'pdf_sha256':sha256_file(path),'pdf_pages':7,
            'table_a1':'Big Five Personality Ratings of U.S. Senators: Senate Insiders',
            'table_a2':'Big Five Personality Ratings of U.S. Senators: Academics',
            'scale':'1-7 panel mean; Emotional Stability is the inverse-pole counterpart of Neuroticism',
        },
        'traits':list(TRAITS),'panel_counts':counts,'record_n':len(records),'records':records,
        'usage_contract':{
            'criterion_not_training_text':True,
            'preserve_panel_specific_values':True,
            'preserve_rating_counts':True,
            'historic_senator_ratings_not_current_roster_norms':True,
        },
    }


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--appendix',required=True,help='path to online_appendix.pdf')
    parser.add_argument('--out',default=OUTPUT)
    args=parser.parse_args(argv)
    doc=extract(os.path.abspath(args.appendix)); dump_atomic(doc,os.path.abspath(args.out),indent=1)
    print('%s: %d frozen expert ratings (%s)'%(
          os.path.basename(args.out),doc['record_n'],doc['source']['pdf_sha256'][:12]))


if __name__=='__main__':
    main()
