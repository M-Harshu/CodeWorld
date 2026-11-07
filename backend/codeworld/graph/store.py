"""In-memory query layer over the persisted nodes/edges tables."""
from __future__ import annotations
import json
from collections import defaultdict, deque
from functools import cached_property

import networkx as nx

SYMBOL_KINDS = ("function", "method", "class", "test")


class RepoGraph:
    def __init__(self, nodes: list[dict], edges: list[tuple]):
        self.nodes: dict[str, dict] = {n["id"]: n for n in nodes}
        self.out: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        self.inn: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        self.children: dict[str, list[str]] = defaultdict(list)
        for s, d, t, conf, res, line in edges:
            self.out[s][t].append((d, conf, res, line))
            self.inn[d][t].append((s, conf, res, line))
            if t == "CONTAINS":
                self.children[s].append(d)
        self.by_path: dict[str, list[str]] = defaultdict(list)
        for n in nodes:
            if n["kind"] in SYMBOL_KINDS:
                self.by_path[n["path"]].append(n["id"])
        self._find_idx: dict[str, list[str]] | None = None

    # -- basic ----------------------------------------------------------------------------------
    def node(self, nid: str) -> dict | None:
        return self.nodes.get(nid)

    def kind(self, nid: str) -> str:
        return self.nodes[nid]["kind"] if nid in self.nodes else ""

    def props(self, nid: str) -> dict:
        return self.nodes[nid]["props"] if nid in self.nodes else {}

    def symbols(self, kinds=SYMBOL_KINDS):
        return [n for n in self.nodes.values() if n["kind"] in kinds]

    def files(self):
        return [n for n in self.nodes.values() if n["kind"] == "file"]

    def file_of(self, nid: str) -> str | None:
        n = self.nodes.get(nid)
        return n["path"] if n and n["kind"] in SYMBOL_KINDS + ("file",) else None

    def ancestors(self, nid: str) -> list[str]:
        out, cur = [], self.nodes.get(nid)
        while cur and cur.get("parent"):
            out.append(cur["parent"])
            cur = self.nodes.get(cur["parent"])
        return out

    def enclosing_symbol(self, path: str, line: int) -> str | None:
        best, span = None, 10 ** 9
        for sid in self.by_path.get(path, []):
            n = self.nodes[sid]
            if n["start"] <= line <= n["end"] and n["end"] - n["start"] < span and n["kind"] != "class":
                best, span = sid, n["end"] - n["start"]
        if best is None:
            for sid in self.by_path.get(path, []):
                n = self.nodes[sid]
                if n["start"] <= line <= n["end"] and n["end"] - n["start"] < span:
                    best, span = sid, n["end"] - n["start"]
        return best

    # -- lookup ---------------------------------------------------------------------------------
    def find(self, query: str, kinds=SYMBOL_KINDS, limit=10) -> list[str]:
        q = query.strip()
        if q in self.nodes:
            return [q]
        if self._find_idx is None:
            idx: dict[str, list[str]] = defaultdict(list)
            for n in self.nodes.values():
                if n["kind"] in SYMBOL_KINDS + ("file", "api"):
                    idx[n["name"].lower()].append(n["id"])
                    idx[n["qualname"].lower()].append(n["id"])
                    if n["kind"] == "file":
                        idx[n["path"].lower()].append(n["id"])
            self._find_idx = idx
        ql = q.lower().replace("()", "")
        hits = list(dict.fromkeys(self._find_idx.get(ql, [])))
        if not hits:
            hits = [i for k, v in self._find_idx.items() if k.endswith("." + ql) or k.endswith("/" + ql) for i in v]
        if not hits:
            hits = [i for k, v in self._find_idx.items() if ql in k for i in v]
        hits = list(dict.fromkeys(hits))
        order = {"function": 0, "method": 0, "class": 1, "test": 2, "api": 3, "file": 4}
        hits = [h for h in hits if self.nodes[h]["kind"] in kinds + ("file", "api")]
        hits.sort(key=lambda h: (order.get(self.nodes[h]["kind"], 9), len(self.nodes[h]["qualname"])))
        return hits[:limit]

    def resolve(self, query: str) -> str | None:
        h = self.find(query, limit=1)
        return h[0] if h else None

    # -- traversal ------------------------------------------------------------------------------
    def _targets_for(self, nid: str) -> list[str]:
        """A class stands for itself plus its methods when asking about callers/callees."""
        n = self.nodes.get(nid)
        if n and n["kind"] == "class":
            return [nid] + [c for c in self.children.get(nid, []) if self.kind(c) in ("method", "function")]
        if n and n["kind"] == "file":
            return [nid] + [s for s in self.by_path.get(n["path"], []) if self.nodes[s]["parent"] == nid]
        return [nid]

    def bfs(self, start: str, direction: str, depth: int = 3, min_conf: float = 0.0, types=("CALLS",), limit: int = 2000) -> dict[str, int]:
        seen: dict[str, int] = {}
        starts = set(self._targets_for(start))
        dq = deque((s, 0) for s in starts)
        visited = set(starts)
        adj = self.out if direction == "out" else self.inn
        while dq:
            cur, d = dq.popleft()
            if d >= depth:
                continue
            for t in types:
                for nxt, conf, res, line in adj[cur].get(t, []):
                    if conf < min_conf or nxt in visited:
                        continue
                    visited.add(nxt)
                    if nxt not in starts:
                        seen[nxt] = d + 1
                    dq.append((nxt, d + 1))
                    if len(seen) >= limit:
                        return seen
        return seen

    def callers(self, nid, depth=1, min_conf=0.0): return self.bfs(nid, "in", depth, min_conf)
    def callees(self, nid, depth=1, min_conf=0.0): return self.bfs(nid, "out", depth, min_conf)

    def path_between(self, a: str, b: str, max_depth: int = 8, min_conf: float = 0.0) -> list[str] | None:
        prev, dq, seen = {a: None}, deque([(a, 0)]), {a}
        while dq:
            cur, d = dq.popleft()
            if cur == b:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                return path[::-1]
            if d >= max_depth:
                continue
            for nxt, conf, _, _ in self.out[cur].get("CALLS", []):
                if conf >= min_conf and nxt not in seen:
                    seen.add(nxt)
                    prev[nxt] = cur
                    dq.append((nxt, d + 1))
        return None

    # -- aggregated graphs (cached) ------------------------------------------------------------
    @cached_property
    def file_graph(self) -> nx.DiGraph:
        g = nx.DiGraph()
        for f in self.files():
            g.add_node(f["path"])
        for s in self.nodes:
            for d, conf, _, _ in self.out[s].get("IMPORTS", []):
                if s.startswith("file:") and d.startswith("file:"):
                    g.add_edge(s[5:], d[5:], kind="import", w=1)
        for s in self.nodes:
            for d, conf, _, _ in self.out[s].get("CALLS", []):
                a, b = self.file_of(s), self.file_of(d)
                if a and b and a != b and conf >= 0.5:
                    if g.has_edge(a, b):
                        g[a][b]["w"] += 1
                    else:
                        g.add_edge(a, b, kind="call", w=1)
        return g

    @cached_property
    def pagerank(self) -> dict[str, float]:
        g = self.file_graph
        if g.number_of_nodes() == 0:
            return {}
        try:
            return nx.pagerank(g.reverse(copy=False), weight="w")       # reversed: widely-depended-on files rank high
        except Exception:
            return {n: 1 / g.number_of_nodes() for n in g}

    @cached_property
    def communities(self) -> list[set[str]]:
        g = self.file_graph.to_undirected()
        if g.number_of_edges() == 0:
            return [{n} for n in g.nodes]
        try:
            return [set(c) for c in nx.community.louvain_communities(g, weight="w", seed=7)]
        except Exception:
            return [set(c) for c in nx.connected_components(g)]


def load_graph(con) -> RepoGraph:
    nodes = []
    for r in con.execute("SELECT * FROM nodes"):
        nodes.append(dict(id=r["id"], kind=r["kind"], name=r["name"], qualname=r["qualname"], path=r["path"], start=r["start"],
                          end=r["end"], parent=r["parent"], props=json.loads(r["props"] or "{}")))
    edges = [(r["src"], r["dst"], r["type"], r["conf"], r["res"], r["line"]) for r in con.execute("SELECT * FROM edges")]
    return RepoGraph(nodes, edges)
