import gzip
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import pytest
from fec.surfaces.staffer_source_catalog import build_catalog, SourceCatalog, sha
from fec.surfaces.staffer_network_explorer import build_index

def write_rows(path, values):
    with (gzip.open if path.suffix == '.gz' else open)(path, 'wt', encoding='utf-8') as stream:
        for value in values:
            stream.write(json.dumps(value)+'\n')

@pytest.fixture
def catalog(tmp_path):
    corpus=tmp_path/'corpus';corpus.mkdir()
    entities=corpus/'littlesis_entities.ndjson.gz'
    write_rows(entities,[{'source_numeric_id':1,'source_entity_id':'littlesis:entity:1','name':'Alice Example','primary_ext':'Person','blurb':'Director'},
                         {'source_numeric_id':2,'source_entity_id':'littlesis:entity:2','name':'Example Org','primary_ext':'Org'}])
    quorum=corpus/'quorum_legacy_source_native_seed.ndjson'
    write_rows(quorum,[{'source_entity_id':'1','display_name':'Alice Example','title':'Staff','member_office_source_key':'Example office'}])
    raw=tmp_path/'relationships.json.gz'
    relations=[{'attributes':{'id':i+1,'entity1_id':1,'entity2_id':2 if i<3 else 999,'category_id':category,
                  'start_date':'2018-05-o5' if i==0 else '2019-00-00','end_date':None,'amount':123 if category==5 else None},
                  'self':'https://littlesis.org/relationships/'+str(i+1)} for i,category in enumerate([1,2,5,7])]
    with gzip.open(raw,'wt') as stream:json.dump(relations,stream)
    spec=lambda p,role,n:{'role':role,'bytes':p.stat().st_size,'sha256':sha(p),'records':n}
    receipt={'status':'PASS','failures':[],'record_files':[spec(entities,'littlesis-entities',2),spec(quorum,'quorum-legacy-source-native-seed',1)],
             'input_files':[spec(raw,'relationships',4)],'entity_observed':2,'relationship_observed':4}
    (corpus/'source_native_corpus_receipt.json').write_text(json.dumps(receipt))
    output=tmp_path/'catalog.sqlite'
    with patch('fec.surfaces.staffer_source_catalog.shutil.disk_usage',return_value=SimpleNamespace(free=10*1024**3)):
        build_catalog(output,corpus,raw)
    return SourceCatalog(output)

def test_every_category_paginated_once_with_invalid_dates_preserved(catalog):
    first=catalog.record('littlesis:entity:1',0,2)
    second=catalog.record('littlesis:entity:1',2,2)
    assert first['total']==4 and first['has_more'] and not second['has_more']
    all_rows=first['relationships']+second['relationships']
    assert len({r['source_numeric_id'] for r in all_rows})==4
    assert all_rows[0]['raw_start_date']=='2018-05-o5'
    assert all_rows[0]['precision_bucket']=='invalid_source_date'
    assert all_rows[2]['source_amount']==123
    assert all_rows[3]['endpoint_missing'] and not all_rows[3]['operator_included']
    assert catalog.receipt['counts']['relationships_with_invalid_source_dates']==1

def test_index_keeps_isolated_people_and_leaves_organizations_to_the_groups_view(catalog):
    """Ruled 2026-09-06 (D-022 item 2). This pin was INVERTED for the catalog's
    organisation and unlinked-person rows: they used to enter the people index
    as browseable context and are now source records only. The catalog itself is
    unchanged and still serves every one of them from the evidence pages."""
    nodes={'person:hill:example':{'id':'person:hill:example','kind':'person','label':'Alice Example','lda_source_person_id':'lda:lobbyist:9'},
           'unresolved':{'id':'unresolved','kind':'payroll_record','label':'Unknown source name'}}
    value=build_index(nodes,{},'fixture',catalog)['payload']
    assert value['total']==2 and value['people_total']==2
    assert value['kind_counts']=={'person':2}
    assert {r[0] for r in value['people']}=={'person:hill:example','quorum:1'}
    assert value['person_scope']['excluded_records']['payroll_record']==1
    assert value['person_scope']['excluded_records']['source_catalog_organization']==1
    assert value['person_scope']['excluded_records']['source_catalog_person']==1
    assert next(r for r in value['people'] if r[0]=='person:hill:example')[6]=='Payroll|Lobbying'
    assert catalog.record('absent')['status']=='unknown_source_record'
    assert catalog.record('littlesis:entity:2')['node']['kind']=='organization'

def test_catalog_rejects_modified_database(catalog):
    with open(catalog.path,'ab') as stream:stream.write(b'tampered')
    with pytest.raises(ValueError,match='seal mismatch'):SourceCatalog(catalog.path)
