import json
import sqlite3
import unittest

from fec.staffer_network.operational_spine import SpineWriter, identity_bindings, role_state, source_disposition, profile_current_roles


class OperationalSpineTests(unittest.TestCase):
    def test_existing_identity_and_keep_separate_survive(self):
        groups = [{'primary_id': 'person:one', 'alias_ids': ['politico:a'], 'status': 'USER_CONFIRMED_SAME_PERSON'}]
        aliases, decisions = identity_bindings(groups, [('politico:a', 'politico:b')])
        self.assertEqual(aliases, {'politico:a': 'person:one'})
        self.assertEqual(len(decisions), 1)
        with self.assertRaises(ValueError):
            identity_bindings(groups, [('person:one', 'politico:a')])

    def test_conflicting_roots_and_cycles_fail_closed(self):
        def group(root, alias):
            return {'primary_id': root, 'alias_ids': [alias], 'status': 'REFERENCE_RESOLVED_SAME_PERSON'}
        for groups in ([group('a', 'x'), group('b', 'x')], [group('a', 'b'), group('b', 'a')]):
            with self.assertRaises(ValueError):
                identity_bindings(groups)

    def test_proposed_matches_are_not_aliases(self):
        aliases, _ = identity_bindings([{'primary_id': 'p', 'alias_ids': ['q'], 'status': 'REVIEW_REQUIRED'}])
        self.assertFalse(aliases)

    def test_quorum_overlap_is_review_and_quorum_only_is_undated(self):
        self.assertEqual(source_disposition('quorum', ['SUBSTANTIVE'], candidate_count=1)[1], 'review')
        self.assertEqual(source_disposition('quorum', ['SUBSTANTIVE'])[1], 'staffer_undated')

    def test_lda_presence_does_not_establish_staff_role(self):
        self.assertEqual(source_disposition('lda', [])[1], 'ecosystem_lobbyist')

    def test_missing_politico_is_not_a_current_role(self):
        self.assertEqual(source_disposition('politico', ['SUBSTANTIVE'], available=False)[1], 'review')
        self.assertEqual(source_disposition('politico', ['INTERN'], has_history=True)[1], 'excluded')

    def test_intern_and_supervisor_and_mixed_roles(self):
        self.assertEqual(role_state('Legislative Intern'), 'INTERN')
        self.assertEqual(role_state('Internship Coordinator'), 'SUBSTANTIVE')
        self.assertEqual(role_state('Intern / Deputy Director'), 'MIXED_ROLE_REVIEW')

    def test_split_headers_reassembled_and_page_chrome_not_roles(self):
        profile = {'current_header_positions': ['Field Representative for', 'Sen. Sample Member',
            'Colleagues', 'See colleagues', '/url: /congress/member/1',
            'This button will redirect you to a filtered page for Sen. Sample Member']}
        roles, unused = profile_current_roles(profile)
        self.assertEqual(roles, [{'title': 'Field Representative', 'employer': 'Sen. Sample Member', 'kind': 'current_header_reassembled'}])
        self.assertEqual(unused, [2, 3, 4, 5])

    def test_explicit_present_history_fills_absent_header_without_inventing_dates(self):
        roles, _ = profile_current_roles({'observed_at': '2026-09-05', 'employment_history': [
            {'title': 'Chief of Staff', 'employer': 'Rep. Sample Member', 'date_text': '(Jan 2025 - Present)'}]})
        self.assertEqual(roles[0]['kind'], 'current_history')

    def test_technical_staff_not_silently_excluded_by_title_vocabulary(self):
        roles, unused = profile_current_roles({'current_header_positions': ['Software Engineer for House Chief Administrative Officer']})
        self.assertEqual(len(roles), 1)
        self.assertFalse(unused)

    def make_writer(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        writer = SpineWriter(db, {})
        db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', ('politico', 'source', 'hash', 1, '{}'))
        person = writer.record('politico:one', 'politico', 'Sample Person', 'POLITICO_CURRENT_AS_REPORTED', 'staffer_current_reported', {})
        return db, writer, person

    def test_internship_never_supplies_duration_but_later_staff_role_can(self):
        db, w, person = self.make_writer()
        w.role('politico:one', person, 'Intern', 'Rep. Sample Member', 'history', '2026-09-05', '(Jan 2020 - Jan 2021)')
        w.role('politico:one', person, 'Legislative Assistant', 'Rep. Sample Member', 'history', '2026-09-05', '(Feb 2021 - Present)')
        self.assertEqual(db.execute('SELECT role_state,temporal_candidate FROM roles ORDER BY role_state').fetchall(), [('INTERN', 0), ('SUBSTANTIVE', 1)])

    def test_duplicate_support_never_duplicates_roles(self):
        db, w, person = self.make_writer()
        args = ('politico:one', person, 'Chief of Staff', 'Rep. Sample Member', 'history', '2026-09-05', '(Jan 2020 - Present)')
        self.assertEqual(w.role(*args), w.role(*args))
        self.assertEqual(db.execute('SELECT count(*) FROM roles').fetchone()[0], 1)

    def test_no_dates_and_current_headers_do_not_invent_tenure(self):
        db, w, person = self.make_writer()
        w.role('politico:one', person, 'Chief of Staff', 'Rep. Sample Member', 'current_header', '2026-09-05')
        self.assertEqual(db.execute('SELECT start,end_exclusive,temporal_candidate FROM roles').fetchone(), (None, None, 0))

    def test_present_stops_at_observation_and_coarse_start_is_conservative(self):
        db, w, person = self.make_writer()
        w.role('politico:one', person, 'Chief of Staff', 'Rep. Sample Member', 'history', '2026-09-05', '(Jan 2020 - Present)', '2026-09-08')
        self.assertEqual(db.execute('SELECT start,end_exclusive,canonical_eligible FROM roles').fetchone(), ('2020-01-31', '2026-09-06', 0))


if __name__ == '__main__':
    unittest.main()
