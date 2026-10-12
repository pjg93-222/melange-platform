import unittest
import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import shortest_path
from fec.staffer_network.evaluate_operational_spine import pagerank, path_from_predecessors


class PopulationEvaluationTests(unittest.TestCase):
    def test_scope_removal_loses_a_bridge_without_changing_weights(self):
        graph = sparse.csr_matrix([[0., 2., 0.], [2., 0., 3.], [0., 3., 0.]])
        costs = graph.copy()
        costs.data = 1. / costs.data
        dist, pred = shortest_path(costs, directed=False, indices=0, return_predecessors=True)
        self.assertAlmostEqual(dist[2], 1/2 + 1/3)
        self.assertEqual(path_from_predecessors(pred, 0, 2), [0, 1, 2])
        filtered = costs[[0, 2]][:, [0, 2]]
        after = shortest_path(filtered, directed=False, indices=0)
        self.assertTrue(np.isinf(after[1]))

    def test_pagerank_handles_isolates_and_conserves_mass(self):
        scores, _ = pagerank(sparse.csr_matrix([[0., 1., 0.], [1., 0., 0.], [0., 0., 0.]]))
        self.assertAlmostEqual(float(scores.sum()), 1., places=10)
        self.assertAlmostEqual(scores[0], scores[1], places=10)
        self.assertLess(scores[2], scores[0])

    def test_all_isolates_are_uniform(self):
        scores, _ = pagerank(sparse.csr_matrix((4, 4)))
        np.testing.assert_allclose(scores, [.25] * 4)


if __name__ == '__main__':
    unittest.main()
