import json

from fec.staffer_network.adjudicate_covered_date_scope import career_context, run


def test_date_scope_distinguishes_complete_clause_from_orphaned_context():
    assert career_context('Jan. 2018 - Jan. 2019: Legislative Assistant, House Committee','Jan. 2018 - Jan. 2019') == (
        'Legislative Assistant, House Committee','SINGLE_CLAUSE_DATE_SCOPE_CONFIRMED')
    assert career_context(' and Scheduler: June 2013-June 2015.','June 2013-June 2015') == (
        'and Scheduler','CONTEXT_DEPENDENT_FRAGMENT')
    assert career_context(' September 2013 - September 2017','September 2013 - September 2017') == (
        '','NO_CAREER_CONTEXT')


def test_scope_receipt_accounts_for_every_bounded_refinement(tmp_path):
    source=tmp_path/'refinements.json';output=tmp_path/'scopes.json'
    source.write_text(json.dumps({'status':'PASS','source_ledger_sha256':'ledger','counts':{'BOUNDED_SOURCE_INTERVAL':2},'refinements':[
        {'claim_id':'a','source_person_id':'p','source_text':'Counsel, Senate Jan. 2020-Dec. 2020','assessment':{'status':'BOUNDED_SOURCE_INTERVAL','source_date_text':'Jan. 2020-Dec. 2020'}},
        {'claim_id':'b','source_person_id':'p','source_text':'Jan. 2019-Dec. 2019','assessment':{'status':'BOUNDED_SOURCE_INTERVAL','source_date_text':'Jan. 2019-Dec. 2019'}}]}))
    result=run(source,output)
    assert sum(result['counts'].values())==2
    assert result['counts']=={'SINGLE_CLAUSE_DATE_SCOPE_CONFIRMED':1,'NO_CAREER_CONTEXT':1}
    assert all(not row['employment_promoted'] for row in result['adjudications'])
