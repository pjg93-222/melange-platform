from pathlib import Path
import sys
from decimal import Decimal
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from collect.refresh_fec_money import aggregate,project


def transaction(identifier,amount,**changes):
    return {'sub_id':identifier,'two_year_transaction_period':2026,'committee_id':'C00000001',
            'candidate_id':'H0AA00001','disbursement_type':'24K','disbursement_amount':amount,
            'disbursement_date':'2026-08-30','memoed_subtotal':False,**changes}


def test_refunds_and_duplicate_pages_do_not_inflate_totals():
    row=transaction('1',1000)
    totals,rejected,latest=aggregate([row,row,transaction('2',-200),transaction('3',999,memoed_subtotal=True),
          transaction('4',500,candidate_id=None)],{'H0AA00001':'A000001'},{'C00000001':'cu'},2026)
    assert totals['A000001']['cu']==Decimal(800)
    assert rejected['duplicate_sub_id']==1 and latest=='2026-08-30'


VOID_ARGS=({'H0AA00001':'A000001'},{'C00000001':'cu','C00000002':'cu'},2026)


def test_void_of_earlier_cycle_check_counts_zero(monkeypatch):
    # D-163 (Paul confirmed 2026-10-11): a void of a 2024-cycle check must not eat 2025-26 money.
    import collect.refresh_fec_money as R
    rows=[transaction('1',5000,disbursement_date='2025-02-13'),
          transaction('2',-5000,disbursement_date='2025-04-25',memo_text='VOID CHECK ORIGINALLY DATED 9/19/24'),
          transaction('3',-1000,disbursement_date='2025-03-18',committee_id='C00000002'),
          transaction('4',3000,disbursement_date='2025-06-01',committee_id='C00000002')]
    monkeypatch.setattr(R,'VOID_NET','1')
    totals,rejected,_=R.aggregate(rows,*VOID_ARGS)
    assert totals['A000001']['cu']==Decimal(8000)
    assert rejected['void_of_check_outside_cycle']==1
    monkeypatch.setattr(R,'VOID_NET','0')
    assert R.aggregate(rows,*VOID_ARGS)[0]['A000001']['cu']==Decimal(2000)   # old rule, pinned


def test_in_cycle_void_nets_its_own_check(monkeypatch):
    import collect.refresh_fec_money as R
    rows=[transaction('1',2500,disbursement_date='2025-01-16'),transaction('2',-2500,disbursement_date='2025-05-08'),
          transaction('3',2500,disbursement_date='2025-08-13')]
    for flag in '01':
        monkeypatch.setattr(R,'VOID_NET',flag)
        assert R.aggregate(rows,*VOID_ARGS)[0]['A000001']['cu']==Decimal(2500)


def test_partial_refund_nets_only_its_own_pair(monkeypatch):
    import collect.refresh_fec_money as R
    rows=[transaction('1',1000,disbursement_date='2025-02-01'),transaction('2',-400,disbursement_date='2025-03-01'),
          transaction('3',-500,disbursement_date='2025-03-02',committee_id='C00000002'),
          transaction('4',700,disbursement_date='2025-04-01',committee_id='C00000002')]
    monkeypatch.setattr(R,'VOID_NET','1')
    totals,rejected,_=R.aggregate(rows,*VOID_ARGS)
    assert totals['A000001']['cu']==Decimal(1300) and rejected['refund_exceeds_pair_money']==1
    monkeypatch.setattr(R,'VOID_NET','0')
    assert R.aggregate(rows,*VOID_ARGS)[0]['A000001']['cu']==Decimal(800)


def test_no_void_money_is_unchanged_by_the_dial(monkeypatch):
    import collect.refresh_fec_money as R
    rows=[transaction(str(i),100*i+1,disbursement_date='2025-0%d-01'%(1+i%9),committee_id='C0000000%d'%(1+i%2)) for i in range(30)]
    out={}
    for flag in '01':
        monkeypatch.setattr(R,'VOID_NET',flag); out[flag]=R.aggregate(rows,*VOID_ARGS)
    assert out['0']==out['1']


def test_conflicting_duplicate_or_wrong_cycle_fails():
    args=({'H0AA00001':'A000001'},{'C00000001':'cu'},2026)
    with pytest.raises(ValueError): aggregate([transaction('1',10),transaction('1',20)],*args)
    with pytest.raises(ValueError): aggregate([transaction('1',10,two_year_transaction_period=2024)],*args)


def test_projection_replaces_money_and_preserves_unrelated_fields():
    roster=[{'member':'A','bioguide':'A000001','cu':999,'banks':5,'finserv':5,'receipts':100000,'unrelated':'keep'}]
    data=project(roster,{'A000001':{'cu':Decimal(100),'banks':Decimal(300)}},{'A000001':Decimal(200000)},{'H0AA00001':'A000001'})
    assert data[0]['cu']==100 and data[0]['banks']==300 and data[0]['total']==400
    assert data[0]['unrelated']=='keep' and data[0]['saturation']==.2
    assert data==project(data,{'A000001':{'cu':Decimal(100),'banks':Decimal(300)}},{'A000001':Decimal(200000)},{'H0AA00001':'A000001'})


def test_capacity_share_audit_accepts_zero_funding_and_rejects_wrong_normalization():
    from audit.legacy_workbook_audit import capacity_share_errors
    rows=[{'member':'A','cu':100,'banks':100,'finserv':0,'cu_adj':50,'banks_adj':50,'finserv_adj':0},
          {'member':'B','cu':0,'banks':0,'finserv':0,'cu_adj':None,'banks_adj':None,'finserv_adj':None}]
    assert capacity_share_errors(rows)==[]
    rows[0].update(cu_adj=60,banks_adj=40)
    assert capacity_share_errors(rows)==['A cu','A banks']
