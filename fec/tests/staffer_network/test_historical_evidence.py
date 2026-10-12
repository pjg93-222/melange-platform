import json
import sqlite3
import unittest

from fec.staffer_network.evaluate_historical_evidence import assess_clause, connect_queue, evaluate


class HistoricalEvidenceTests(unittest.TestCase):
    def test_repeated_assertions_are_preserved_and_same_names_do_not_merge(self):
        source = sqlite3.connect(':memory:')
        source.executescript('''
            CREATE TABLE covered_position(id,person,raw_text,filing_uuid,activity);
            CREATE TABLE person_name(person,label);
        ''')
        ledger = sqlite3.connect(':memory:')
        ledger.executescript('CREATE TABLE claim(id,person,payload,source); CREATE TABLE person(id,source);')
        text = 'Chief of Staff, 2010-2015'
        ledger.execute('INSERT INTO claim VALUES(?,?,?,?)',
                       ('existing', 'lda:lobbyist:1', json.dumps({'raw_text': text}), 'lda_covered_position'))
        ledger.execute("INSERT INTO person VALUES('person:lda:lobbyist:1','lda')")
        source.executemany('INSERT INTO person_name VALUES(?,?)',
                           [('lda:lobbyist:1', 'Same Name'), ('lda:lobbyist:2', 'Same Name')])
        source.executemany('INSERT INTO covered_position VALUES(?,?,?,?,?)', [
            ('a', 'lda:lobbyist:1', text, 'f1', 'a1'),
            ('b', 'lda:lobbyist:1', text, 'f2', 'a2'),
            ('c', 'lda:lobbyist:2', text, 'f3', 'a3'),
            ('d', 'lda:lobbyist:2', 'Intern; Legislative Assistant', 'f4', 'a4'),
        ])
        queue = connect_queue(':memory:')
        counts = evaluate(source, ledger, queue, '2020', '2026-09-08')
        self.assertEqual(counts['source_assertions_accounted'], 4)
        self.assertEqual(counts['distinct_person_texts_in_window'], 3)
        self.assertEqual(counts['repeated_assertions_grouped'], 1)
        self.assertEqual(counts['existing_claim_reparses_avoided'], 1)
        self.assertEqual(counts['person_NEW_SOURCE_ID_REVIEW'], 1)
        self.assertEqual(counts['clause_EXCLUDED_INTERN'], 1)
        self.assertEqual(counts['clause_MISSING_DATE_BOUND'], 1)
        self.assertEqual(queue.execute('SELECT count(*) FROM clause').fetchone()[0], 3)
        second = evaluate(source, ledger, queue, '2019', '2026-09-08')
        self.assertEqual(second['previously_processed_exact_texts'], 3)
        self.assertEqual(queue.execute('SELECT count(*) FROM clause').fetchone()[0], 3)
        self.assertEqual(queue.execute('SELECT count(*) FROM support').fetchone()[0], 8)

    def test_dates_preserve_unknowns_and_reject_mixed_ranges(self):
        self.assertEqual(assess_clause('Counsel, since 2014', '2026-09-08')['status'], 'MISSING_DATE_BOUND')
        self.assertEqual(assess_clause('Intern 2010-2015', '2026-09-08')['status'], 'EXCLUDED_INTERN')
        self.assertEqual(assess_clause('Counsel 2028-2030', '2026-09-08')['status'], 'FUTURE_SOURCE_DATE')
        self.assertEqual(assess_clause('Counsel 2010-2015 and Director January 2016 to May 2020',
                                      '2026-09-08')['status'], 'MULTIPLE_INTERVALS_REVIEW')
        result = assess_clause('Counsel January 2010 to May 2015', '2026-09-08')
        self.assertEqual(result['status'], 'BOUNDED_SOURCE_INTERVAL')
        self.assertFalse(result['employment_promoted'])


if __name__ == '__main__':
    unittest.main()
