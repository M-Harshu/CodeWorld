"""Hybrid retrieval: BM25 (SQLite FTS5) + dense vectors, fused with RRF, then graph expansion over CALLS/INHERITS."""
from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict

from ..textutil import tokens

MODES = ("bm25", "vector", "hybrid", "graph", "auto")
RELATIONAL = {"callers", "callees", "impact", "flow", "tests"}
PROTECT = 3
KIND_PRIOR = {"function": 1.0, "method": 1.0, "class": 0.95, "doc": 0.9, "file": 0.8, "test": 0.75}


@dataclass
class Hit:
    chunk_id: str
    node_id: str | None
    path: str
    kind: str
    name: str
    start: int
    end: int
    score: float
    text: str = ""
    sources: list[str] = field(default_factory=list)
    via: str = ""

    def to_dict(self, snippet: int = 400) -> dict:
        d = asdict(self)
        d["snippet"] = self.text[:snippet]
        d.pop("text")
        return d


class Retriever:
    def __init__(self, ctx):
        self.ctx = ctx

    # -- primitives -----------------------------------------------------------------------------
    def _fts_query(self, q: str) -> str | None:
        toks = list(dict.fromkeys(tokens(q)))[:24]
        toks = [re.sub(r"[^a-z0-9_ ]", "", t) for t in toks]
        toks = [t.replace("_", " ").strip() for t in toks if t.strip()]
        return " OR ".join(f'"{t}"' for t in toks) if toks else None

    def bm25(self, q: str, k: int = 20) -> list[tuple[str, float]]:
        fq = self._fts_query(q)
        if not fq:
            return []
        with self.ctx.con() as con:
            try:
                rows = con.execute("SELECT chunk_id, bm25(chunks_fts, 6.0, 2.0, 1.0) AS s FROM chunks_fts WHERE chunks_fts MATCH ? "
                                   "ORDER BY s LIMIT ?", (fq, k)).fetchall()
            except Exception:
                return []
        return [(r["chunk_id"], -float(r["s"])) for r in rows]

    def vector(self, q: str, k: int = 20) -> list[tuple[str, float]]:
        vi = self.ctx.vindex()
        if vi is None:
            return []
        return vi.search(self.ctx.embedder.embed_query(q), k)

    @staticmethod
    def rrf(*rankings: list[tuple[str, float]], k: int = 60) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in rankings:
            for rank, (cid, _) in enumerate(r):
                out[cid] = out.get(cid, 0.0) + 1.0 / (k + rank + 1)
        return out

    def _load(self, cids: list[str]) -> dict[str, dict]:
        if not cids:
            return {}
        with self.ctx.con() as con:
            out = {}
            for i in range(0, len(cids), 500):
                part = cids[i:i + 500]
                q = ",".join("?" * len(part))
                for r in con.execute(f"SELECT * FROM chunks WHERE id IN ({q})", part):
                    out[r["id"]] = dict(r)
            return out

    def _hits(self, scores: dict[str, float], sources: dict[str, list[str]], k: int, q: str = "", via: dict | None = None) -> list[Hit]:
        want_tests = bool(re.search(r"\btests?\b", q.lower()))
        rows = self._load(list(scores))
        hits = []
        for cid, s in scores.items():
            r = rows.get(cid)
            if not r:
                continue
            prior = 1.0 if (r["kind"] != "test" or want_tests) else KIND_PRIOR["test"]
            hits.append(Hit(cid, r["node_id"], r["path"], r["kind"], r["name"], r["start"], r["end"], s * KIND_PRIOR.get(r["kind"], 1.0) * prior,
                            r["text"], sources.get(cid, []), (via or {}).get(cid, "")))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    # -- public modes ---------------------------------------------------------------------------
    def search(self, q: str, mode: str = "graph", k: int = 8, intent: str = "explain") -> list[Hit]:
        pool = max(30, k * 4)
        if mode == "auto":          # graph expansion helps relational questions and slightly hurts plain lookups, so route by intent
            mode = "graph" if intent in RELATIONAL else "hybrid"
        if mode == "bm25":
            r = self.bm25(q, pool)
            return self._hits({c: s for c, s in r}, {c: ["bm25"] for c, _ in r}, k, q)
        if mode == "vector":
            r = self.vector(q, pool)
            return self._hits({c: s for c, s in r}, {c: ["vector"] for c, _ in r}, k, q)
        b, v = self.bm25(q, pool), self.vector(q, pool)
        fused = self.rrf(b, v)
        src: dict[str, list[str]] = {}
        for c, _ in b:
            src.setdefault(c, []).append("bm25")
        for c, _ in v:
            src.setdefault(c, []).append("vector")
        if mode == "hybrid":
            return self._hits(fused, src, k, q)
        return self._graph_expand(q, fused, src, k, intent)

    def _graph_expand(self, q, fused, src, k, intent) -> list[Hit]:
        g = self.ctx.graph
        seeds = sorted(fused.items(), key=lambda x: -x[1])[:6]
        if not seeds:
            return []
        top = seeds[0][1]
        scores, via = dict(fused), {}
        w = {"calls_out": 0.55, "calls_in": 0.5, "parent": 0.4, "inherits": 0.45}
        if intent == "callers":
            w["calls_in"] = 1.0
        if intent in ("callees", "flow"):
            w["calls_out"] = 0.9
        mentioned = []
        from .intent import mentions
        mentioned = mentions(q, g)
        for nid in mentioned:                                   # explicitly named symbols are strong seeds
            seeds.append((f"c:{nid}", top * 1.2))
            scores[f"c:{nid}"] = max(scores.get(f"c:{nid}", 0), top * 1.2)
            src.setdefault(f"c:{nid}", []).append("mention")
        for cid, s in seeds:
            nid = cid[2:]
            n = g.node(nid)
            if not n or n["kind"] not in ("function", "method", "class", "test"):
                continue
            rel = s / top
            def bump(target, weight, why, depth=1):
                nonlocal scores
                tc = f"c:{target}"
                add = top * rel * weight * (0.5 ** (depth - 1))
                if add > scores.get(tc, 0) * 0.5:
                    scores[tc] = scores.get(tc, 0) + add
                    src.setdefault(tc, [])
                    if "graph" not in src[tc]:
                        src[tc].append("graph")
                    via.setdefault(tc, f"{why} {n['qualname']}")
            for d in g._targets_for(nid)[:1]:
                for dst, conf, _, _ in g.out[d].get("CALLS", [])[:12]:
                    if conf >= 0.5 and g.kind(dst) in ("function", "method", "class"):
                        bump(dst, w["calls_out"], "called by")
                        for d2, c2, _, _ in g.out[dst].get("CALLS", [])[:6]:
                            if c2 >= 0.5 and g.kind(d2) in ("function", "method"):
                                bump(d2, w["calls_out"], "called (indirectly) by", 2)
                for srcn, conf, _, _ in g.inn[d].get("CALLS", [])[:12]:
                    if conf >= 0.5 and g.kind(srcn) in ("function", "method", "test"):
                        bump(srcn, w["calls_in"], "calls")
                for dst, *_ in g.out[d].get("INHERITS", []):
                    bump(dst, w["inherits"], "base of")
            if n["kind"] == "method" and n["parent"]:
                bump(n["parent"], w["parent"], "class of")
        top_seeds = sorted(fused.items(), key=lambda x: -x[1])[:PROTECT]       # graph expansion must not push out the best direct matches
        for cid, _ in top_seeds:
            scores[cid] = fused[cid] + top * 3          # keep the hybrid order among the protected seeds
        return self._hits(scores, src, k, q, via)

    def context(self, hits: list[Hit], budget_chars: int = 9000) -> tuple[str, list[Hit]]:
        g, used, parts, total = self.ctx.graph, [], [], 0
        for h in hits:
            body = h.text if h.kind != "file" else h.text[:500]
            block = f"[{h.path}:{h.start}-{h.end}] {h.kind} {h.name}\n{body}"
            if total + len(block) > budget_chars and used:
                break
            parts.append(block)
            total += len(block)
            used.append(h)
        ids = {h.node_id for h in used if h.node_id}
        rel = []
        for h in used:
            if h.node_id and g.node(h.node_id):
                for dst, conf, res, _ in g.out[h.node_id].get("CALLS", []):
                    if dst in ids and conf >= 0.5:
                        rel.append(f"{g.node(h.node_id)['qualname']} -> calls -> {g.node(dst)['qualname']}")
        if rel:
            parts.append("Relationships between the snippets above:\n" + "\n".join(sorted(set(rel))[:30]))
        return "\n\n".join(parts), used
