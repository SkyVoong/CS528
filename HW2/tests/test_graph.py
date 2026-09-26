import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from graph import Graph, parse_links, summarize, pagerank, top_k, closeness_bfs, best_closeness


class TestParsing(unittest.TestCase):
    def test_parse_links(self):
        html = '<a HREF="3.html"> This is a link </a>\n<p>\n<a HREF="10.html"> link </a>'
        self.assertEqual(parse_links(html), ["3.html", "10.html"])

    def test_graph_from_pages(self):
        pages = {"0.html": ["1.html", "1.html", "2.html", "99.html"],  # dup + dangling link
                 "1.html": ["2.html"], "2.html": []}
        g = Graph.from_pages(pages)
        self.assertEqual(g.out_adj, [[1, 2], [2], []])
        self.assertEqual(g.out_degrees(), [2, 1, 0])
        self.assertEqual(g.in_degrees(), [0, 1, 2])


class TestStats(unittest.TestCase):
    def test_summary(self):
        s = summarize([1, 2, 3, 4, 5, 6])
        self.assertAlmostEqual(s["avg"], 3.5)
        self.assertEqual(s["median"], 3.5)


class TestPageRank(unittest.TestCase):
    def test_symmetric_cycle(self):
        g = Graph.from_edges(4, [(0, 1), (1, 2), (2, 3), (3, 0)])
        pr, _ = pagerank(g, tol=1e-12)
        for x in pr:
            self.assertAlmostEqual(x, 0.25, places=10)

    def test_hand_computed_triangle(self):
        # A->B, A->C, B->C, C->A
        g = Graph.from_edges(3, [(0, 1), (0, 2), (1, 2), (2, 0)])
        pr, _ = pagerank(g, tol=1e-12)
        a = 0.128625 / 0.3316875
        self.assertAlmostEqual(pr[0], a, places=9)
        self.assertEqual(top_k(pr, 3), [2, 0, 1])


class TestCloseness(unittest.TestCase):
    def test_directed_path(self):
        g = Graph.from_edges(4, [(0, 1), (1, 2), (2, 3)])
        c0 = closeness_bfs(g, 0)
        self.assertAlmostEqual(c0, (3 / 3) * (3 / 6))
        self.assertEqual(best_closeness(g)[0], 0)

    def test_star(self):
        edges = [(0, i) for i in range(1, 6)] + [(i, 0) for i in range(1, 6)]
        g = Graph.from_edges(6, edges)
        self.assertAlmostEqual(closeness_bfs(g, 0), 1.0)
        self.assertEqual(best_closeness(g)[0], 0)


if __name__ == "__main__":
    unittest.main()