from collections import defaultdict

from fec.staffer_network.adjudicate_quorum_current_roles import exact_current_matches, identity_reasons


def quorum(office,title):
    return {'payload':{'member_office_source_key':office,'title':title}}


def politico(office,title):
    return {'payload':{'current_header_positions':[title+' for '+office]}}


def test_exact_current_match_uses_politico_role_and_normalizes_adviser():
    q=quorum('Brian Fitzpatrick','National Security Advisor')
    p=politico('Rep. Brian FitzpatrickRepublicanPA-01','National Security Adviser')
    assert exact_current_matches(q,p)==[{'employer':'Rep. Brian FitzpatrickRepublicanPA-01','title':'National Security Adviser','kind':'current_header'}]


def test_current_match_requires_same_office_and_substantive_role():
    assert exact_current_matches(quorum('Brian Fitzpatrick','Chief of Staff'),politico('Rep. Other MemberRepublicanPA-01','Chief of Staff'))==[]
    assert exact_current_matches(quorum('Brian Fitzpatrick','Intern'),politico('Rep. Brian FitzpatrickRepublicanPA-01','Intern'))==[]


def test_identity_gate_holds_nicknames_and_nonreciprocal_crosswalks():
    names=defaultdict(set,{'joseph knowles':{'p'}});reverse=defaultdict(set,{'p':{'q'}})
    assert identity_reasons('q','Joseph Knowles','p','Joseph Knowles',names,reverse)==[]
    assert 'QUORUM_POLITICO_NAME_NOT_EXACT' in identity_reasons('q','Joseph Knowles','p','Joe Knowles',names,reverse)
    reverse['p'].add('other')
    assert 'REVERSE_CROSSWALK_NOT_UNIQUE' in identity_reasons('q','Joseph Knowles','p','Joseph Knowles',names,reverse)
