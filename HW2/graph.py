import statistics
from collections import deque
import re

HREF_RE = re.compile(r"""<a\s[^>]*?href\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)


def parse_links(html):
    return [m.split("/")[-1] for m in HREF_RE.findall(html)]


class Graph:
    def __init__(self, names, out_adj):
        self.names = names
        self.n = len(names)
        self.out_adj = out_adj                       
        self.in_adj = [[] for _ in range(self.n)]
        for u, targets in enumerate(out_adj):
            for v in targets:
                self.in_adj[v].append(u)

    @classmethod
    def from_pages(cls, pages):
        """pages: dict {page_name: [target_name, ...]}"""
        names = sorted(pages)
        index = {name: i for i, name in enumerate(names)}
        out_adj = []
        for name in names:
            targets = {index[t] for t in pages[name] if t in index}   
            out_adj.append(sorted(targets))
        return cls(names, out_adj)

    @classmethod
    def from_edges(cls, n, edges):
        out = [set() for _ in range(n)]
        for u, v in edges:
            out[u].add(v)
        return cls([str(i) for i in range(n)], [sorted(s) for s in out])

    def out_degrees(self):
        return [len(a) for a in self.out_adj]

    def in_degrees(self):
        return [len(a) for a in self.in_adj]


def summarize(values):
    return {
        "avg": sum(values) / len(values),
        "median": statistics.median(values),
        "max": max(values),
        "min": min(values),
        "quintiles": statistics.quantiles(values, n=5, method="inclusive"),
    }


def pagerank(g, damping=0.85, tol=0.005, max_iter=1000):
    n = g.n
    pr = [1.0 / n] * n
    out_deg = g.out_degrees()
    base = (1.0 - damping) / n
    for it in range(1, max_iter + 1):
        share = [pr[u] / out_deg[u] if out_deg[u] else 0.0 for u in range(n)]
        new = [base + damping * sum(share[t] for t in g.in_adj[v]) for v in range(n)]
        change = sum(abs(a - b) for a, b in zip(new, pr))
        old_total = sum(pr)
        pr = new
        if change <= tol * old_total:
            return pr, it
    return pr, max_iter


def top_k(scores, k=5):
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:k]


def closeness_bfs(g, source):
    dist = [-1] * g.n
    dist[source] = 0
    q = deque([source])
    total = reached = 0
    while q:
        u = q.popleft()
        for v in g.out_adj[u]:
            if dist[v] < 0:
                dist[v] = dist[u] + 1
                total += dist[v]
                reached += 1
                q.append(v)
    if reached == 0 or g.n <= 1:
        return 0.0
    return (reached / (g.n - 1)) * (reached / total)


def best_closeness(g):
    scores = [closeness_bfs(g, s) for s in range(g.n)]
    best = top_k(scores, 1)[0]
    return best, scores[best], scores