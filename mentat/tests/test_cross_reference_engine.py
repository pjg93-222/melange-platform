import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from cross_reference_engine import analyze


def row(sid, amount=100, **extra):
    return dict(sub_id=sid, company='Acme', industry='Fixture', cycle=2024,
                current_receipt_cents=int(amount * 100), contribution_receipt_amount=amount,
                contributor_name='PERSON, ONE', contributor_zip='12345',
                contribution_receipt_date='2024-01-01', committee_id='C00000001',
                transaction_id=sid, file_num=10, filing_form='F3X', line_number='11AI',
                entity_type='IND', company_match_status='accepted', **extra)


def changed(r, **values):
    return {**r, **values}


def evidence(rows):
    return {r['sub_id']: {**r, 'file_number':r['file_num']} for r in rows}


def headers():
    return {'10':dict(file_num=10,committee_id='C00000001',filing_form='F3X',original_file_num=None),
            '11':dict(file_num=11,committee_id='C00000001',filing_form='F3X',original_file_num=10,amendment_number='1')}


class CrossReferenceTests(unittest.TestCase):
    def test_plain_gifts_are_classified_without_certifying_unique(self):
        result=analyze([row('a')])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
        self.assertEqual(result['totals']['supported_unique_gross_cents'],0)
        self.assertFalse(result['checks']['overall_unique_dollars_established'])
        self.assertEqual(result['decisions'][0]['bucket'],'employee_original')

    def test_amount_magnitude_is_never_exclusion_evidence(self):
        # An extreme but otherwise ordinary receipt is a natural observation.
        # Its size may make a review display, but cannot change the accounting.
        result=analyze([row('large',50_000_000)])
        decision=result['decisions'][0]
        self.assertEqual(decision['amount_cents'],5_000_000_000)
        self.assertEqual(decision['counted_cents'],5_000_000_000)
        self.assertNotEqual(decision['status'],'excluded_with_evidence')
        self.assertFalse(any('outlier' in reason for reason in decision['reasons']))
        self.assertEqual(result['totals']['provisional_original_giving_cents'],5_000_000_000)

    def test_missing_filing_coverage_does_not_create_receipt_ambiguity(self):
        a=row('a')
        for supplied_headers,expected_reason in (({},'filing_relationship_evidence_missing'),
                (headers(),'latest_filing_completeness_unproven')):
            with self.subTest(reason=expected_reason):
                result=analyze([a],evidence([a]),supplied_headers)
                self.assertFalse(result['review_queue'])
                self.assertEqual(result['coverage_queue'][0]['reason'],expected_reason)
                decision=result['decisions'][0]
                self.assertEqual(decision['status'],'source_verified')
                self.assertEqual(decision['receipt_verification_status'],'source_verified')
                self.assertEqual(decision['coverage_status'],'follow_up_required')
                self.assertEqual(result['totals']['unresolved_source_records'],0)
                self.assertEqual(result['totals']['coverage_source_records'],1)
                self.assertEqual(result['totals']['auto_resolved_records'],1)
                self.assertEqual(result['totals']['source_verified_records'],1)
                self.assertEqual(result['totals']['supported_unique_gross_cents'],0)
                self.assertFalse(result['checks']['collection_completeness_established'])
                self.assertFalse(result['checks']['overall_unique_dollars_established'])

    def test_stored_classification_is_distinct_from_exact_source_verification(self):
        result=analyze([row('a')])
        self.assertEqual(result['decisions'][0]['receipt_verification_status'],'classified_from_stored_evidence')
        self.assertFalse(result['decisions'][0]['exact_fec_response_verified'])
        self.assertEqual(result['totals']['source_verified_records'],0)
        self.assertEqual(result['totals']['auto_resolved_records'],1)

    def test_observed_versions_with_missing_headers_remain_row_review(self):
        rows=[changed(row('a'),transaction_id='TX'),changed(row('b',125),transaction_id='TX',file_num=11)]
        result=analyze(rows,evidence(rows))
        self.assertEqual(result['review_queue'][0]['reason'],'observed_filing_versions_unresolved')
        self.assertEqual(result['totals']['unresolved_source_records'],2)
        self.assertEqual(result['totals']['auto_resolved_records'],0)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],22500)
        self.assertEqual(result,analyze(list(reversed(rows)),evidence(rows)))

    def test_reused_transaction_id_for_distinct_receipts_is_not_row_ambiguity(self):
        a=changed(row('a'),transaction_id='TX')
        for change in ({'contribution_receipt_date':'2024-02-01'}, {'contributor_name':'PERSON, TWO'}):
            b=changed(row('b'),transaction_id='TX',file_num=11,**change)
            result=analyze([a,b],evidence([a,b]))
            self.assertFalse(result['review_queue'])
            self.assertEqual(result['totals']['provisional_original_giving_cents'],20000)

    def test_certificate_cannot_promote_receipt_with_missing_filing_coverage(self):
        a=row('a')
        manifest={'unique_origin_certificates':[dict(source_id='a',evidence_ids=['audit1'],
                    filing_complete=True,origin_verified=True,routes_complete=True)]}
        result=analyze([a],evidence([a]),headers(),filing_manifest=manifest)
        self.assertFalse(result['review_queue'])
        self.assertTrue(result['coverage_queue'])
        self.assertEqual(result['totals']['supported_unique_gross_cents'],0)

    def test_equal_gifts_are_not_deduplicated(self):
        result=analyze([row('a'),row('b')])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],20000)
        self.assertFalse(result['links'])

    def test_memo_requires_explicit_same_file_reference(self):
        a=changed(row('a'),transaction_id='SA11AI.TX')
        b=changed(row('b'),memo_code='X')
        self.assertEqual(analyze([a,b])['totals']['provisional_original_giving_cents'],20000)
        b['memo_text']='[SA11AI:TX]'
        result=analyze([a,b])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
        self.assertEqual(result['links'][0]['kind'],'memo')
        b['file_num']=11
        self.assertEqual(analyze([a,b])['totals']['provisional_original_giving_cents'],20000)

    def test_memo_parent_wrong_sign_date_donor_or_small_amount(self):
        a=row('a')
        b=changed(row('b'),memo_code='X',back_reference_transaction_id='a')
        for field,value in [('contribution_receipt_amount',50),('contribution_receipt_amount',-100),
                            ('contributor_name','OTHER PERSON'),('contribution_receipt_date','2024-02-01')]:
            result=analyze([changed(a,**{field:value}),b])
            self.assertFalse(result['links'])

    def test_ambiguous_memo_parents_retained(self):
        rows=[changed(row('a'),transaction_id='TX'),changed(row('b'),transaction_id='TX'),
              changed(row('c'),memo_code='X',back_reference_transaction_id='TX')]
        self.assertEqual(analyze(rows)['totals']['provisional_original_giving_cents'],30000)

    def test_observed_amendment_corrects_amount_but_not_unique(self):
        rows=[changed(row('a'),transaction_id='TX'),changed(row('b',125),transaction_id='TX',file_num=11)]
        result=analyze(rows,evidence(rows),headers())
        self.assertEqual(result['totals']['provisional_original_giving_cents'],12500)
        self.assertEqual(result['totals']['supported_unique_gross_cents'],0)
        self.assertEqual(result['links'][0]['kind'],'amendment')

    def test_same_transaction_unrelated_filings_not_duplicate(self):
        rows=[changed(row('a'),transaction_id='TX'),changed(row('b'),transaction_id='TX',file_num=11)]
        h=headers();h['11']['original_file_num']=None
        self.assertEqual(analyze(rows,evidence(rows),h)['totals']['provisional_original_giving_cents'],20000)

    def test_amendment_reclassified_as_nongiving_supersedes_old_contribution(self):
        rows=[changed(row('a'),transaction_id='TX'),changed(row('b'),transaction_id='TX',file_num=11,
              line_number='17',memo_text='INTEREST')]
        result=analyze(rows,evidence(rows),headers())
        self.assertEqual(result['totals']['provisional_original_giving_cents'],0)
        self.assertTrue(any(link['kind']=='amendment' for link in result['links']))

    def test_deleted_record_requires_complete_latest_filing(self):
        a=row('a')
        manifest={'families':[dict(committee_id='C00000001',root_file_num=10,latest_file_num=11,
                    complete=True,source_url='https://fec.gov/fixture',source_ids=[])]}
        self.assertEqual(analyze([a],evidence([a]),headers())['totals']['provisional_original_giving_cents'],10000)
        self.assertEqual(analyze([a],evidence([a]),headers(),filing_manifest=manifest)['totals']['provisional_original_giving_cents'],0)

    def test_latest_physical_ids_alone_do_not_prove_transaction_deleted(self):
        a=row('a')
        family=dict(committee_id='C00000001',root_file_num=10,latest_file_num=11,
                    complete=True,source_url='https://fec.gov/fixture',source_ids=['some-new-id'])
        manifest={'families':[family]}
        result=analyze([a],evidence([a]),headers(),filing_manifest=manifest)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
        family['transaction_ids']=['other_transaction']
        self.assertEqual(analyze([a],evidence([a]),headers(),filing_manifest=manifest)['totals']['provisional_original_giving_cents'],0)

    def test_route_requires_reciprocal_evidence_and_zip(self):
        a=row('a');b=changed(row('b'),committee_id='C00000002')
        ev=evidence([a,b]);ev['a'].update(receipt_type_full='EARMARKED FOR TEST C00000002',committee={'name':'TEST CONDUIT'})
        self.assertEqual(analyze([a,b],ev)['totals']['provisional_original_giving_cents'],20000)
        ev['b']['memo_text']='EARMARKED THROUGH TEST CONDUIT'
        self.assertEqual(analyze([a,b],ev)['totals']['provisional_original_giving_cents'],10000)
        ev['b']['contributor_zip']='99999'
        self.assertEqual(analyze([a,b],ev)['totals']['provisional_original_giving_cents'],20000)

    def test_overlapping_routes_are_not_allocated(self):
        rows=[row('a'),changed(row('b'),committee_id='C00000002'),changed(row('c'),committee_id='C00000003')]
        ev=evidence(rows)
        ev['a'].update(receipt_type_full='EARMARKED FOR TEST C00000002',committee={'name':'FIRST'})
        ev['b'].update(receipt_type_full='EARMARKED FOR TEST C00000003',memo_text='EARMARKED THROUGH FIRST',committee={'name':'SECOND'})
        ev['c']['memo_text']='EARMARKED THROUGH SECOND'
        result=analyze(rows,ev)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],30000)
        self.assertFalse(result['links'])

    def test_memo_counterpart_survives_later_route_link(self):
        a=row('a');b=changed(row('b'),committee_id='C00000002')
        c=changed(row('c'),committee_id='C00000002',memo_code='X',back_reference_transaction_id='b')
        ev=evidence([a,b,c]);ev['a'].update(receipt_type_full='EARMARKED FOR TEST C00000002',committee={'name':'TEST CONDUIT'})
        ev['b']['memo_text']='EARMARKED THROUGH TEST CONDUIT'
        result=analyze([a,b,c],ev)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
        self.assertEqual({link['retained_source_id'] for link in result['links']},{'a'})
        self.assertTrue(result['checks']['relationship_graph_acyclic'])

    def test_company_pac_outgoing_is_separate_and_affiliation_dated(self):
        a=changed(row('a'),committee_id='C00000009')
        b=changed(row('b'),entity_type='PAC',contributor_id='C00000009')
        affiliations={'records':[dict(company='Acme',entity_id='C00000009',relationship='connected_pac',
                        valid_from='2023-01-01',source_url='https://fec.gov/fixture',evidence_id='aff1')]}
        result=analyze([a,b],affiliations=affiliations)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
        self.assertEqual(result['totals']['pac_activity_observed_on_recipient_receipts_cents'],10000)
        self.assertEqual(result['decisions'][0]['funding_direction'],'employee_to_company_pac')
        self.assertEqual(result['decisions'][1]['attribution_status'],'dated_affiliation_supported')
        self.assertTrue(result['checks']['original_and_pac_buckets_disjoint'])

    def test_exact_dated_pac_affiliation_overrides_provisional_corporate_name_review(self):
        pac=changed(row('pac'),entity_type='PAC',contributor_id='',
                    contributor_name='ACME EMPLOYEE PAC',company_match_status='review',
                    company_match_reason='corporate_name_not_verified')
        affiliation={'records':[dict(company='Acme',entity_id='C00000009',
            entity_name='ACME EMPLOYEE PAC',relationship='connected_pac',
            valid_from='2021-01-01',source_url='https://fec.gov/fixture',evidence_id='pac-aff')]}
        result=analyze([pac],affiliations=affiliation)
        decision=result['decisions'][0]
        self.assertEqual(decision['bucket'],'pac_activity')
        self.assertEqual(decision['counted_cents'],0)
        self.assertEqual(decision['pac_activity_cents'],10000)
        self.assertEqual(decision['attribution_status'],'dated_affiliation_supported')
        self.assertFalse(any(item['reason']=='company_attribution_unverified'
                             for item in result['review_queue']))
        near=changed(pac,sub_id='near',transaction_id='near',
                     contributor_name='ACME EMPLOYEE PAC SUPPORTERS')
        # A near name is not a dated affiliation. It names the company, so it
        # stays counted and queued under the company-named flag (ruling D1).
        self.assertTrue(any(item['reason']=='company_named_unverified'
                            for item in analyze([near],affiliations=affiliation)['review_queue']))

    def test_affiliation_aliases_are_exact_and_do_not_enable_prefix_matching(self):
        pac=changed(row('alias'),entity_type='PAC',contributor_id='',
                    contributor_name='ACME LEGACY PAC',company_match_status='review',
                    company_match_reason='corporate_name_not_verified')
        affiliation={'records':[dict(company='Acme',entity_id='C00000009',
            entity_name='ACME EMPLOYEE PAC',aliases=['ACME LEGACY PAC'],
            relationship='connected_pac',valid_from='2021-01-01',
            source_url='https://fec.gov/fixture',evidence_id='pac-aff')]}
        result=analyze([pac],affiliations=affiliation)
        self.assertEqual(result['decisions'][0]['bucket'],'pac_activity')
        near=changed(pac,sub_id='near-alias',transaction_id='near-alias',
                     contributor_name='ACME LEGACY PAC SUPPORTERS')
        self.assertTrue(any(item['reason']=='company_named_unverified'
                            for item in analyze([near],affiliations=affiliation)['review_queue']))

    def test_exact_dated_treasury_affiliation_overrides_provisional_name_review(self):
        org=changed(row('org'),entity_type='ORG',contributor_name='ACME INC',
                    company_match_status='review',company_match_reason='corporate_name_not_verified')
        affiliation={'records':[dict(company='Acme',entity_name='ACME INC',
            relationship='treasury_entity',valid_from='2021-01-01',
            source_url='https://fec.gov/fixture',evidence_id='treasury-aff')]}
        result=analyze([org],affiliations=affiliation)
        self.assertEqual(result['decisions'][0]['bucket'],'corporate_original')
        self.assertFalse(any(item['reason']=='company_attribution_unverified'
                             for item in result['review_queue']))

    def test_organization_name_is_not_treasury_proof(self):
        a=changed(row('a'),entity_type='ORG',contributor_name='ACME INC')
        self.assertEqual(analyze([a])['decisions'][0]['bucket'],'organization_unverified')
        aff={'records':[dict(company='Acme',entity_name='ACME INC',relationship='treasury_entity',
                  valid_from='2025-01-01',source_url='https://fec.gov/fixture',evidence_id='a')]}
        self.assertEqual(analyze([a],affiliations=aff)['decisions'][0]['bucket'],'organization_unverified')
        aff['records'][0]['valid_from']='2023-01-01'
        self.assertEqual(analyze([a],affiliations=aff)['decisions'][0]['bucket'],'corporate_original')

    def test_negative_does_not_automatically_mean_refund(self):
        rows=[row('a'),row('b',-25)]
        result=analyze(rows)
        self.assertEqual(result['totals']['observed_linked_refund_cents'],0)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],7500)
        rows[1].update(adjustment_type='refund',refund_of_sub_id='a',adjustment_evidence_id='FEC-ref')
        result=analyze(rows)
        self.assertEqual(result['totals']['observed_linked_refund_cents'],-2500)
        self.assertEqual(result['links'][0]['kind'],'refund')

    def test_refund_overallocation_remains_unresolved(self):
        rows=[row('a'),row('b',-75),row('c',-75)]
        for r in rows[1:]:r.update(adjustment_type='refund',refund_of_sub_id='a',adjustment_evidence_id='FEC-ref')
        result=analyze(rows)
        self.assertEqual(result['totals']['observed_linked_refund_cents'],0)
        self.assertTrue(any(r['reason']=='linked_refunds_exceed_original_gift' for r in result['review_queue']))

    def test_special_account_is_giving_but_interest_is_not(self):
        a=changed(row('a'),line_number='17',memo_text='NON-CONTRIBUTION ACCOUNT')
        b=changed(row('b'),line_number='17',memo_text='INTEREST')
        c=changed(row('c'),line_number='17')
        result=analyze([a,b,c])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],20000)
        self.assertEqual(result['decisions'][1]['bucket'],'non_giving')
        self.assertEqual(result['decisions'][2]['counted_cents'],10000)

    def test_supported_subset_requires_all_evidence_contracts(self):
        a=row('a')
        manifest={'families':[dict(committee_id='C00000001',root_file_num=10,latest_file_num=10,
                    complete=True,source_url='https://fec.gov/fixture',source_ids=['a'])],
                  'unique_origin_certificates':[dict(source_id='a',evidence_ids=['audit1'],
                    filing_complete=True,origin_verified=True,routes_complete=True)]}
        result=analyze([a],evidence([a]),headers(),filing_manifest=manifest)
        self.assertEqual(result['totals']['supported_unique_gross_cents'],10000)
        self.assertFalse(result['checks']['overall_unique_dollars_established'])
        manifest['unique_origin_certificates'][0]['routes_complete']=False
        self.assertEqual(analyze([a],evidence([a]),headers(),filing_manifest=manifest)['totals']['supported_unique_gross_cents'],0)

    def test_input_order_idempotence_and_integer_cent_reconciliation(self):
        rows=[row('a',1.25),row('b',-0.25),changed(row('c',1.25),memo_code='X',back_reference_transaction_id='a')]
        one=analyze(rows);two=analyze(list(reversed(rows)))
        self.assertEqual(one,two)
        self.assertEqual(json.dumps(one,sort_keys=True),json.dumps(analyze(copy.deepcopy(rows)),sort_keys=True))
        self.assertTrue(one['checks']['integer_cent_reconciliation'])
        self.assertEqual(one['totals']['baseline_receipt_cents']+sum(c['delta_cents'] for c in one['changes']),one['totals']['provisional_original_giving_cents'])

    def test_duplicate_inputs_and_identity_conflicts(self):
        a=row('a')
        result=analyze([a,a])
        self.assertEqual(result['totals']['source_records'],1)
        self.assertEqual(result['checks']['identical_input_duplicates_coalesced'],1)
        with self.assertRaisesRegex(ValueError,'Conflicting duplicate'):
            analyze([a,changed(a,company='Other')])
        ev=evidence([a]);ev['a']['contribution_receipt_amount']=200
        with self.assertRaisesRegex(ValueError,'amount conflict'):analyze([a],ev)
        with self.assertRaisesRegex(ValueError,'whole cents'):analyze([changed(a,contribution_receipt_amount='1.001')])

    def test_conflicting_archive_claims_block_exclusions_and_certification(self):
        a=changed(row('a'),line_number='17',memo_text='INTEREST',source_conflicts=['amount differs'])
        result=analyze([a])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
        self.assertEqual(result['decisions'][0]['bucket'],'conflicting_source_evidence')
        b=changed(row('b'),attribution_claims=[{'company':'Other','status':'accepted'}])
        result=analyze([b])
        self.assertEqual(result['decisions'][0]['attribution_status'],'unverified')
        self.assertTrue(any(r['reason']=='source_or_attribution_conflict' for r in result['review_queue']))

    def test_stale_complete_manifest_cannot_remove_later_filing(self):
        a=changed(row('a'),file_num=11)
        manifest={'families':[dict(committee_id='C00000001',root_file_num=10,latest_file_num=10,
                    complete=True,source_url='https://fec.gov/fixture',source_ids=[])]}
        result=analyze([a],evidence([a]),headers(),filing_manifest=manifest)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
        self.assertTrue(any(r['reason']=='latest_filing_manifest_not_corroborated' for r in result['review_queue']))

    def test_conflicting_certificates_fail_instead_of_input_order_winner(self):
        c=dict(source_id='a',evidence_ids=['x'],filing_complete=True,origin_verified=True,routes_complete=True)
        with self.assertRaisesRegex(ValueError,'Conflicting unique-origin'):
            analyze([row('a')],filing_manifest={'unique_origin_certificates':[c,{**c,'routes_complete':False}]})

    def test_explicit_source_exclusion_not_restored_by_accepted_match(self):
        a=changed(row('a'),excluded_reason='Manually adjudicated outside company scope',current_receipt_cents=0)
        result=analyze([a],evidence([a]))
        self.assertEqual(result['totals']['provisional_original_giving_cents'],0)
        self.assertEqual(result['decisions'][0]['bucket'],'excluded_source_policy')
        self.assertIn('preexisting_source_exclusion:Manually adjudicated outside company scope',result['decisions'][0]['reasons'])
        self.assertFalse(result['changes'])

    def test_invalid_calendar_date_cannot_support_affiliation_or_unique_subset(self):
        a=changed(row('a'),entity_type='ORG',contributor_name='ACME INC',contribution_receipt_date='2024-02-30')
        affiliation={'records':[dict(company='Acme',entity_name='ACME INC',relationship='treasury_entity',
                  valid_from='2023-01-01',source_url='https://fec.gov/fixture',evidence_id='a')]}
        result=analyze([a],evidence([a]),headers(),affiliations=affiliation)
        self.assertEqual(result['decisions'][0]['bucket'],'organization_unverified')
        self.assertTrue(any(r['reason']=='receipt_date_missing' for r in result['review_queue']))
        self.assertEqual(result['totals']['supported_unique_gross_cents'],0)

    def test_invalid_affiliation_end_date_not_treated_as_unlimited(self):
        a=changed(row('a'),entity_type='ORG',contributor_name='ACME INC')
        affiliation={'records':[dict(company='Acme',entity_name='ACME INC',relationship='treasury_entity',
                  valid_from='2023-01-01',valid_to='2024-99-01',source_url='https://fec.gov/fixture',evidence_id='a')]}
        self.assertEqual(analyze([a],affiliations=affiliation)['decisions'][0]['bucket'],'organization_unverified')

    def test_merged_document_and_field_provenance_reaches_decisions_and_links(self):
        a=row('a');b=changed(row('b'),memo_code='X',back_reference_transaction_id='a')
        ev=evidence([a,b])
        ev['a'].update(_evidence_id='new-doc',_evidence_ids=['new-doc','old-doc'],
                       _field_evidence_ids={'line_number':['old-doc'],'contributor_name':'new-doc'})
        ev['b'].update(_evidence_id='memo-doc',_field_evidence_ids={'back_reference_transaction_id':['reference-doc']})
        result=analyze([a,b],ev)
        self.assertTrue({'new-doc','old-doc'} <= set(result['decisions'][0]['evidence_ids']))
        self.assertEqual(result['decisions'][0]['field_evidence_ids']['line_number'],['old-doc'])
        link=result['links'][0]
        self.assertTrue({'new-doc','old-doc','memo-doc','reference-doc'} <= set(link['evidence_ids']))
        self.assertEqual(link['field_evidence_ids']['b']['back_reference_transaction_id'],['reference-doc'])

    def test_zero_local_filing_number_is_missing_metadata_not_identity_conflict(self):
        for missing in (0,'0'):
            a=changed(row('a'),file_num=missing)
            ev=evidence([a]);ev['a']['file_number']=10
            result=analyze([a],ev,headers())
            self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)

    def test_unverified_company_attribution_keeps_the_money_and_stays_queued(self):
        # Missing or ambiguous employer information is not evidence against the
        # receipt, so the dollars stay counted, flagged and in the review queue.
        for status in ('review','PENDING','',None,'unrecognized_state'):
            with self.subTest(status=status):
                a=changed(row('a'),company_match_status=status,
                          company_match_reason='prefix_with_unverified_trailing_tokens:NA')
                result=analyze([a])
                decision=result['decisions'][0]
                self.assertEqual(decision['counted_cents'],10000)
                self.assertEqual(decision['bucket'],'employee_original')
                self.assertEqual(decision['attribution_status'],'unverified')
                self.assertIn('company_attribution_unverified',decision['reasons'])
                self.assertEqual(decision['status'],'needs_review')
                self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
                self.assertEqual(result['totals']['unresolved_positive_cents'],10000)
                queued=[item for item in result['review_queue']
                        if item['reason']=='company_attribution_unverified']
                self.assertEqual(len(queued),1)
                self.assertEqual(queued[0]['source_ids'],['a'])
                self.assertEqual(queued[0]['company_match_status'],status)
                self.assertEqual(queued[0]['company_match_reason'],'prefix_with_unverified_trailing_tokens:NA')
                self.assertEqual(queued[0]['positive_cents'],10000)

    def test_accepted_match_without_a_company_name_is_counted_and_flagged(self):
        a=changed(row('a'),company='',company_match_status='accepted')
        result=analyze([a])
        decision=result['decisions'][0]
        self.assertEqual(decision['counted_cents'],10000)
        self.assertEqual(decision['attribution_status'],'unverified')
        self.assertIn('company_attribution_unverified',decision['reasons'])
        self.assertEqual(result['review_queue'][0]['reason'],'company_attribution_unverified')

    def test_unverified_attribution_still_reaches_the_memo_and_refund_passes(self):
        parent=changed(row('a'),company_match_status='review',transaction_id='SA11AI.TX')
        memo=changed(row('b'),company_match_status='review',memo_code='X',memo_text='[SA11AI:TX]')
        refund=changed(row('c',-100),company_match_status='review',adjustment_type='refund',
                       refund_of_sub_id='a',adjustment_evidence_id='FEC-ref')
        result=analyze([parent,memo,refund])
        decisions={d['source_id']:d for d in result['decisions']}
        self.assertEqual(decisions['b']['counted_cents'],0)
        self.assertIn('explicit_same_filing_memo_reference',decisions['b']['reasons'])
        self.assertEqual([link['kind'] for link in result['links'] if link['source_id']=='b'],['memo'])
        self.assertEqual(decisions['c']['bucket'],'linked_refund')
        self.assertIn('explicit_observed_refund',decisions['c']['reasons'])
        self.assertEqual(result['totals']['observed_linked_refund_cents'],-10000)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],0)

    def test_unverified_attribution_does_not_restore_an_explicit_source_exclusion(self):
        a=changed(row('a'),company_match_status='review',excluded_reason='Adjudicated source policy')
        result=analyze([a])
        decision=result['decisions'][0]
        self.assertEqual(decision['counted_cents'],0)
        self.assertEqual(decision['bucket'],'excluded_source_policy')
        self.assertIn('preexisting_source_exclusion:Adjudicated source policy',decision['reasons'])
        self.assertFalse(result['review_queue'])

    def test_the_superseded_unresolved_attribution_verdict_is_gone(self):
        rows=[changed(row('a'),company_match_status='review'),
              changed(row('b'),company_match_status='rejected'),
              changed(row('c'),company='',company_match_status='accepted'),
              changed(row('d'),company_match_status='review',
                      source_conflicts=[{'field':'line_number','values':['11AI','17']}])]
        text=json.dumps(analyze(rows,evidence(rows)))
        for gone in ('company_attribution_unresolved','attribution_unresolved','held_for_attribution_review'):
            self.assertNotIn(gone,text)

    def test_explicit_rejected_company_match_is_final_without_review_queue(self):
        for status in ('rejected','excluded'):
            with self.subTest(status=status):
                a=changed(row('a'),company_match_status=status,
                          company_match_reason='persistent_override:reject')
                result=analyze([a])
                decision=result['decisions'][0]
                self.assertEqual(decision['counted_cents'],0)
                self.assertEqual(decision['bucket'],'excluded_company_match')
                self.assertEqual(decision['status'],'excluded_with_evidence')
                self.assertEqual(result['totals']['provisional_original_giving_cents'],0)
                self.assertFalse(result['review_queue'])

    def test_heuristic_rejections_and_generic_professions_are_not_contradictions(self):
        for employer in ('', 'CONSULTANT', 'REFUSED', 'DECLINED', 'NOTEMPLOYED', 'SELF EMPLOYED', 'MYSTERY NAME'):
            for status in ('review','rejected','excluded'):
                with self.subTest(employer=employer,status=status):
                    a=changed(row('a'),company_match_status=status,contributor_employer=employer,
                              company_match_reason='employer_prefix_not_verified')
                    result=analyze([a])
                    self.assertEqual(result['decisions'][0]['counted_cents'],10000)
                    self.assertTrue(result['review_queue'])
        a=changed(row('a'),company_match_status='rejected',contributor_employer='OTHER BANK',
                  company_match_reason='employer_prefix_not_verified')
        self.assertIn('attribution_contradicted_by_text',analyze([a])['decisions'][0]['reasons'])

    def test_hockey_occupation_does_not_exclude_a_gift(self):
        a=changed(row('a'),excluded_reason='occupation:HOCKEY',contributor_occupation='HOCKEY COACH')
        self.assertEqual(analyze([a])['decisions'][0]['counted_cents'],10000)

    def test_negative_unverified_attribution_exposure_retains_sign(self):
        a=changed(row('a',-25),company_match_status='review')
        result=analyze([a])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],-2500)
        self.assertEqual(result['totals']['unresolved_negative_cents'],-2500)

    def test_nongiving_receipt_settles_irrelevant_company_attribution_review(self):
        for fields in ({'line_number':'17','memo_text':'INTEREST'}, {'line_number':'13'},
                       {'contributor_name':'PURCHASER UNKNOWN','memo_text':'PURCHASER UNKNOWN STOCK SOLD'}):
            with self.subTest(fields=fields):
                a=changed(row('a'),company_match_status='review',current_receipt_cents=0,
                          excluded_reason='Ambiguous company name',**fields)
                result=analyze([a],evidence([a]))
                decision=result['decisions'][0]
                self.assertEqual(decision['bucket'],'non_giving')
                self.assertEqual(decision['receipt_verification_status'],'source_verified')
                self.assertEqual(decision['attribution_status'],'not_required_for_non_giving')
                self.assertIn('non_giving_resolved_without_company_attribution',decision['reasons'])
                self.assertFalse(result['review_queue'])
                self.assertFalse(result['coverage_queue'])
                self.assertEqual(result['totals']['unresolved_source_records'],0)
                self.assertEqual(result['totals']['provisional_original_giving_cents'],0)
                self.assertFalse(result['changes'])

    def test_unclear_other_receipt_does_not_bypass_company_review(self):
        a=changed(row('a'),company_match_status='review',line_number='17',current_receipt_cents=0)
        result=analyze([a],evidence([a]))
        decision=result['decisions'][0]
        self.assertEqual(decision['receipt_verification_status'],'needs_review')
        self.assertEqual(decision['attribution_status'],'unverified')
        self.assertIn('company_attribution_unverified',decision['reasons'])
        self.assertIn('company_attribution_unverified',[item['reason'] for item in result['review_queue']])
        # An unresolved receipt meaning is its own open question, not a reason
        # to erase the amount before either question is answered.
        self.assertIn('receipt_role_unresolved',[item['reason'] for item in result['review_queue']])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)

    def test_source_conflict_flags_attribution_without_erasing_the_amount(self):
        a=changed(row('a'),company_match_status='review',line_number='17',memo_text='INTEREST',
                  current_receipt_cents=0,source_conflicts=[{'field':'line_number','values':['11AI','17']}])
        result=analyze([a],evidence([a]))
        decision=result['decisions'][0]
        self.assertEqual(decision['receipt_verification_status'],'needs_review')
        self.assertEqual(decision['bucket'],'conflicting_source_evidence')
        self.assertEqual(decision['attribution_status'],'unverified')
        self.assertIn('company_attribution_unverified',decision['reasons'])
        self.assertEqual(result['totals']['unresolved_positive_cents'],10000)
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)
        self.assertEqual(result['review_queue'][0]['reason'],'source_or_attribution_conflict')

    def test_nongiving_does_not_rewrite_explicit_source_exclusion(self):
        for match in ('accepted','rejected','excluded'):
            a=changed(row('a'),company_match_status=match,line_number='17',memo_text='INTEREST',
                      excluded_reason='Adjudicated source policy',current_receipt_cents=0)
            result=analyze([a],evidence([a]))
            self.assertEqual(result['decisions'][0]['bucket'],'excluded_source_policy')
            self.assertIn('preexisting_source_exclusion:Adjudicated source policy',result['decisions'][0]['reasons'])
            self.assertFalse(result['review_queue'])

    def test_automatic_attribution_trace_cannot_be_overwritten_by_remote_fields(self):
        trace={'rule':'exact_roster_alias','from_status':'review','to_status':'accepted',
               'matched_variant':'ACME'}
        a=changed(row('a'),attribution_resolution=trace)
        ev=evidence([a]);ev['a']['attribution_resolution']={'rule':'untrusted_remote'}
        result=analyze([a],ev)
        self.assertEqual(result['decisions'][0]['attribution_resolution'],trace)
        self.assertIn('automatic_exact_alias_recheck',result['decisions'][0]['reasons'])
        self.assertEqual(result['decisions'][0]['receipt_verification_status'],'source_verified')

    def test_text_naming_another_organization_contradicts_the_attribution(self):
        # Ruling D1 (2026-09-17): a row whose own reported text names an
        # organization that is not the listed company or any of its supplied
        # names is a contradiction, not an ambiguity. It leaves the totals with
        # its evidence; a proven duplicate is the other kind of contradiction.
        names={'Citibank':['Citi','Citibank','Citigroup']}
        pac=changed(row('pac',5000),company='Citibank',entity_type='PAC',contributor_name='END CITIZENS UNITED',
                    company_match_status='review',company_match_reason='corporate_name_not_verified')
        result=analyze([pac],company_names=names)
        decision=result['decisions'][0]
        self.assertEqual(decision['counted_cents'],0)
        self.assertEqual(decision['pac_activity_cents'],0)
        self.assertEqual(decision['status'],'excluded_with_evidence')
        self.assertEqual(decision['bucket'],'excluded_company_match')
        self.assertIn('attribution_contradicted_by_text',decision['reasons'])
        self.assertNotIn('company_attribution_unverified',decision['reasons'])
        self.assertFalse(result['review_queue'])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],0)
        self.assertEqual(result['changes'][0]['delta_cents'],-500000)
        # The company's own label is a name even when no roster is supplied.
        self.assertEqual(analyze([pac])['decisions'][0]['status'],'excluded_with_evidence')

    def test_text_naming_the_company_stays_counted_while_a_stranger_is_excluded(self):
        names={'Citibank':['Citi','Citibank','Citigroup']}
        rows=[changed(row('named'),company='Citibank',contributor_employer='CITIBANK N A',contributor_occupation='BANKER',
                      company_match_status='review',company_match_reason='prefix_with_unverified_trailing_tokens:N A'),
              changed(row('stranger'),company='Citibank',contributor_employer='CITIZENS BUSINESS BANK',contributor_occupation='BANKER',
                      company_match_status='review',company_match_reason='squashed_prefix_unverified_remainder:ZENSBUSINESSBANK'),
              changed(row('blank'),company='Citibank',contributor_employer='',contributor_occupation='',company_match_status='review'),
              changed(row('none'),company='Citibank',contributor_employer='NOT EMPLOYED',contributor_occupation='RETIRED',
                      company_match_status='review'),
              changed(row('short'),company='Citibank',contributor_employer='CITI TRENDS',contributor_occupation='CLERK',
                      company_match_status='review')]
        result=analyze(rows,company_names=names)
        decisions={d['source_id']:d for d in result['decisions']}
        queued={}
        for item in result['review_queue']:queued.setdefault(item['reason'],[]).extend(item['source_ids'])
        for sid in ('named','short'):
            self.assertEqual(decisions[sid]['counted_cents'],10000)
            self.assertEqual(decisions[sid]['attribution_status'],'unverified')
            self.assertIn('company_named_unverified',decisions[sid]['reasons'])
            self.assertEqual(decisions[sid]['status'],'needs_review')
        for sid in ('blank','none'):
            self.assertEqual(decisions[sid]['counted_cents'],10000)
            self.assertEqual(decisions[sid]['attribution_status'],'unverified')
            self.assertIn('company_attribution_unverified',decisions[sid]['reasons'])
        self.assertEqual(decisions['stranger']['counted_cents'],0)
        self.assertEqual(decisions['stranger']['status'],'excluded_with_evidence')
        self.assertIn('attribution_contradicted_by_text',decisions['stranger']['reasons'])
        self.assertEqual(sorted(queued['company_named_unverified']),['named','short'])
        self.assertEqual(sorted(queued['company_attribution_unverified']),['blank','none'])
        self.assertNotIn('stranger',[sid for ids in queued.values() for sid in ids])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],40000)
        self.assertEqual(result['totals']['unresolved_positive_cents'],40000)

    def test_recipient_company_pac_supports_an_unverified_attribution(self):
        # Ruling (2026-09-17): another employer is fine when the money goes to
        # something affiliated with the listed company. The register or the FEC
        # connected-organization field proves the recipient side; the employer
        # field does not have to.
        names={'Citibank':['Citi','Citibank','Citigroup']}
        affiliation={'records':[dict(company='Citibank',entity_id='C00008474',entity_name='CITIGROUP INC. PAC',
            relationship='connected_pac',valid_from='2021-01-01',source_url='https://fec.gov/fixture',evidence_id='citi-pac')]}
        registered=changed(row('registered'),company='Citibank',committee_id='C00008474',contributor_employer='ACME WIDGETS INC',
                           contributor_occupation='ENGINEER',company_match_status='review')
        connected=changed(row('connected'),company='Citibank',committee_id='C00039305',contributor_employer='ACME WIDGETS INC',
                          contributor_occupation='ENGINEER',company_match_status='review')
        stranger=changed(row('stranger'),company='Citibank',committee_id='C00000001',contributor_employer='ACME WIDGETS INC',
                         contributor_occupation='ENGINEER',company_match_status='review')
        result=analyze([registered,connected,stranger],affiliations=affiliation,company_names=names,
                       committee_connected_orgs={'C00039305':['CITIGROUP INC.']})
        decisions={d['source_id']:d for d in result['decisions']}
        self.assertEqual(decisions['registered']['counted_cents'],10000)
        self.assertEqual(decisions['registered']['attribution_status'],'recipient_supported')
        self.assertIn('recipient_is_dated_company_pac',decisions['registered']['reasons'])
        self.assertEqual(decisions['registered']['funding_direction'],'employee_to_company_pac')
        self.assertEqual(decisions['connected']['counted_cents'],10000)
        self.assertEqual(decisions['connected']['attribution_status'],'recipient_supported')
        self.assertIn('recipient_connected_organization_resolves_to_company',decisions['connected']['reasons'])
        for sid in ('registered','connected'):
            self.assertIn('company_attribution_unverified',decisions[sid]['reasons'])
            self.assertEqual(decisions[sid]['status'],'needs_review')
        self.assertEqual(decisions['stranger']['counted_cents'],0)
        self.assertIn('attribution_contradicted_by_text',decisions['stranger']['reasons'])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],20000)

    def test_role_marker_with_the_company_named_is_role_supported(self):
        # Ruling D3 (2026-09-17): advisor, adviser, advisory, consultant,
        # consulting, contractor, trustee, partner, counsel and board are the
        # adjacent-position markers; DIRECTOR and RETIRED are not. The route
        # changes the verification status only; the text test decides dollars.
        names={'Citibank':['Citi','Citibank','Citigroup'],'Wells Fargo Bank NA':['Wells Fargo']}
        consultant=changed(row('consultant'),company='Citibank',contributor_employer='SELF-EMPLOYED',
                           contributor_occupation='CONSULTANT TO CITIBANK',company_match_status='review')
        retired=changed(row('retired'),company='Wells Fargo Bank NA',contributor_employer='WELLS FARGO BANK RETIRED',
                        contributor_occupation='RETIRED',company_match_status='review')
        director=changed(row('director'),company='Citibank',contributor_employer='ACME CAPITAL',
                         contributor_occupation='MANAGING DIRECTOR',company_match_status='review')
        advisor=changed(row('advisor'),company='Citibank',contributor_employer='ACME CAPITAL',
                        contributor_occupation='ADVISOR TO CITI',company_match_status='review')
        result=analyze([consultant,retired,director,advisor],company_names=names)
        decisions={d['source_id']:d for d in result['decisions']}
        for sid in ('consultant','advisor'):
            self.assertEqual(decisions[sid]['counted_cents'],10000)
            self.assertEqual(decisions[sid]['attribution_status'],'role_supported')
            self.assertIn('role_marker_with_company_named',decisions[sid]['reasons'])
            self.assertIn('company_named_unverified',decisions[sid]['reasons'])
        self.assertEqual(decisions['retired']['counted_cents'],10000)
        self.assertEqual(decisions['retired']['attribution_status'],'unverified')
        self.assertIn('company_named_unverified',decisions['retired']['reasons'])
        self.assertNotIn('role_marker_with_company_named',decisions['retired']['reasons'])
        self.assertEqual(decisions['director']['counted_cents'],0)
        self.assertIn('attribution_contradicted_by_text',decisions['director']['reasons'])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],30000)

    def test_proven_duplicate_memo_leg_stays_excluded_under_the_text_test(self):
        names={'Citibank':['Citi','Citibank','Citigroup']}
        parent=changed(row('a'),company='Citibank',contributor_employer='CITIBANK',company_match_status='review',
                       transaction_id='SA11AI.TX')
        memo=changed(row('b'),company='Citibank',contributor_employer='CITIBANK',company_match_status='review',
                     memo_code='X',memo_text='[SA11AI:TX]')
        result=analyze([parent,memo],company_names=names)
        decisions={d['source_id']:d for d in result['decisions']}
        self.assertEqual(decisions['a']['counted_cents'],10000)
        self.assertIn('company_named_unverified',decisions['a']['reasons'])
        self.assertEqual(decisions['b']['counted_cents'],0)
        self.assertEqual(decisions['b']['status'],'excluded_with_evidence')
        self.assertIn('explicit_same_filing_memo_reference',decisions['b']['reasons'])
        self.assertEqual([link['kind'] for link in result['links']],['memo'])
        self.assertEqual(result['totals']['provisional_original_giving_cents'],10000)


if __name__ == '__main__':
    unittest.main()
