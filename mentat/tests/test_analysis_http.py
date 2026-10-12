"""Local authenticated HTTP -> real Insights engine -> source-record round trip."""
import time
import unittest
from unittest.mock import patch

import test_contribution_http as transport
import test_analysis_data as fixture


class InsightsHTTPTests(unittest.TestCase):
    setUpClass = classmethod(transport.ContributionHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(transport.ContributionHTTPTests.tearDownClass.__func__)
    request = transport.ContributionHTTPTests.request

    def setUp(self):
        fixture.AnalysisAdapterTests.setUp(self)
        proof = patch.object(transport.app.contribution_workbook_guard, 'verify_sources', return_value=[])
        self.proof = proof.start(); self.addCleanup(proof.stop)

    def test_real_query_records_and_toggle_via_read_only_http(self):
        request = {'dataset': 'Example', 'as_of': '2026-09-20', 'panels': ['favorites', 'spending']}
        for _ in range(100):
            status, headers, payload = self.request('/api/insights/query', method='POST', data=request)
            self.assertEqual(status, 200, payload)
            if payload['status'] == 'ready':
                break
            time.sleep(.01)
        self.assertEqual(payload['status'], 'ready')
        self.assertEqual(set(payload['panels']), {'favorites', 'spending'})
        favorite = payload['panels']['favorites']['rows'][0]
        status, _, records = self.request('/api/insights/records', method='POST', data={
            **request, 'filters': favorite['filters'], 'index_token': payload['provenance']['index_token']})
        self.assertEqual(status, 200, records)
        self.assertEqual(sum(r['amount_cents'] for r in records['rows']), favorite['amount_cents'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.proof.assert_called_with(self.report_root, 'Example', sources=[])

    def test_host_origin_token_scope_and_source_proof_fail_closed(self):
        for endpoint in ('/api/insights/query', '/api/insights/records'):
            for headers in ({'X-Mentat-Token': 'wrong'}, {'Origin': 'https://elsewhere.invalid'}, {'Host': 'elsewhere.invalid'}):
                with self.subTest(endpoint=endpoint, headers=headers):
                    status, _, _ = self.request(endpoint, method='POST', data={'dataset': 'Example'}, headers=headers)
                    self.assertEqual(status, 403)
            status, _, _ = self.request(endpoint, method='POST', data={'dataset': 'Example', 'melange_root': 'untrusted'})
            self.assertEqual(status, 400)
        self.proof.side_effect = ValueError('Published source proof changed.')
        status, _, body = self.request('/api/insights/query', method='POST', data={'dataset': 'Example'})
        self.assertEqual(status, 400)
        self.assertIn('proof changed', body['error'])

    def test_catalog_and_assets_are_served_from_registered_paths(self):
        with patch.object(fixture.a, 'catalog', return_value={'datasets': [], 'panels': [dict(id=p[0]) for p in fixture.a.PANELS]}) as catalog:
            status, _, payload = self.request('/api/insights/catalog?directory=untrusted')
            self.assertEqual(status, 200)
            self.assertEqual(len(payload['panels']), 11)
            catalog.assert_called_once_with(self.report_root)
        for endpoint in ('/insights.js', '/insights.css'):
            status, _, content = self.request(endpoint)
            self.assertEqual(status, 200)
            self.assertTrue(content)


if __name__ == '__main__':
    unittest.main()
