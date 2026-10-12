import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from engine.money_observation import industry_money_missing


@pytest.mark.parametrize('row', [
    {'receipts':0,'banks':0,'finserv':0,'money_source_status':'VERIFIED_OPENFEC_CURRENT_CYCLE'},
    {'receipts':0,'banks':1500},
    {'receipts':None,'finserv':300},
    {'receipts':0,'cu':10500},
    {'receipts':450000,'banks':0},
])
def test_observed_money_is_never_replaced_by_estimate(row):
    assert not industry_money_missing(row)


@pytest.mark.parametrize('row', [{},{'receipts':0,'banks':0,'cu':None},
    {'receipts':0,'money_source_status':'UNRESOLVED_CANDIDATE_ID_LAST_VALUE_RETAINED'}])
def test_unobserved_money_keeps_existing_median_fallback(row):
    assert industry_money_missing(row)
