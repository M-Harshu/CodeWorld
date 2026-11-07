"""Tool registry shared by the REST API, the LLM agent, the offline agent and the MCP server."""
from __future__ import annotations
import json
from collections import deque

from ..analysis import diffreview, health, impact as impact_mod, onboarding, security
from ..ai.intent import classify


class ToolError(Exception):
    pass


class Seen:
    """Records every (path, start, end) range surfaced to the model so citations can be verified afterwards."""

    def __init__(self):
        self.ranges: list[tuple[str, int, int]] = []

    def add(self, path, start, end=None):
        if path and start:
            self.ranges.append((path, int(start), int(end or start)))

    def covers(self, path, start, end) -> bool:
        return any(p == path and not (end < s or start > e) for p, s, e in self.ranges)


def _brief(g, nid, extra=None):
    n = g.nodes[nid]
    d = {"id": nid, "name": n["qualname"], "kind": n["kind"], "path": n["path"], "line": n["start"], "end": n["end"]}
    if n["props"].get("signature"):
        d["signature"] = n["props"]["signature"]
    if extra:
        d.update(extra)
    return d


class ToolRegistry:
    def __init__(self, ctx, seen: Seen | None = None):
        self.ctx, self.seen = ctx, seen or Seen()
        self.specs = [
            ("search_code", "Hybrid (BM25 + vector + graph) search for code relevant to a natural-language query.",
             {"query": "string", "k": "integer (default 8)", "mode": "bm25|vector|hybrid|graph|auto (default auto)"}, ["query"]),
            ("find_symbol", "Find functions/classes/files by (partial) name.", {"name": "string"}, ["name"]),
            ("get_symbol", "Full details + source code + callers/callees/tests of one symbol.", {"symbol": "string"}, ["symbol"]),
            ("get_file", "Read lines of a source file.", {"path": "string", "start": "integer", "end": "integer"}, ["path"]),
            ("find_callers", "Who calls this symbol (transitively up to depth).", {"symbol": "string", "depth": "integer", "min_confidence": "number"}, ["symbol"]),
            ("find_callees", "What does this symbol call (transitively up to depth).", {"symbol": "string", "depth": "integer", "min_confidence": "number"}, ["symbol"]),
            ("trace_dependencies", "File-level import dependencies (out) or dependents (in).", {"target": "string", "direction": "out|in|both", "depth": "integer"}, ["target"]),
            ("find_tests", "Tests that exercise a symbol or file, ranked by call distance.", {"target": "string"}, ["target"]),
            ("impact", "Change-impact analysis: dependents, endpoints, tests, risk score, co-changed files.", {"target": "string", "depth": "integer"}, ["target"]),
            ("explain_flow", "Call-tree / Mermaid flow starting at a function or endpoint.", {"entry": "string", "depth": "integer"}, ["entry"]),
            ("repo_overview", "Architecture summary: key modules, entry points, communities.", {}, []),
            ("list_endpoints", "All API endpoints with handlers.", {}, []),
            ("hotspots", "Riskiest files by churn x complexity.", {"top": "integer"}, []),
            ("health_summary", "Cycles, dead code, duplicates, layering, test coverage by graph.", {}, []),
            ("security_summary", "Attack-surface findings: endpoints that reach dangerous sinks.", {}, []),
            ("review_diff", "Run the AI-PR guardrail on a unified diff.", {"diff": "string"}, ["diff"]),
        ]

    # -- MCP / LLM descriptions ---------------------------------------------------------------------
    def describe(self) -> list[dict]:
        out = []
        for name, desc, params, req in self.specs:
            props = {k: {"type": "integer" if "integer" in v else "number" if "number" in v else "string", "description": v} for k, v in params.items()}
            out.append({"name": name, "description": desc, "inputSchema": {"type": "object", "properties": props, "required": req}})
        return out

    def call(self, name: str, args: dict | None = None) -> dict:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            raise ToolError(f"unknown tool '{name}'")
        try:
            res = fn(**(args or {}))
        except TypeError as e:
            raise ToolError(f"bad arguments for {name}: {e}") from e
        self._harvest(res)
        return res

    def _harvest(self, obj, depth: int = 0):
        """Register every code location a tool surfaced so that citations can be verified later."""
        if depth > 6:
            return
        g = self.ctx.graph
        if isinstance(obj, dict):
            nid = obj.get("id")
            if isinstance(nid, str) and nid in g.nodes and g.nodes[nid]["path"] and g.nodes[nid]["kind"] not in ("module", "repo"):
                n = g.nodes[nid]
                self.seen.add(n["path"], n["start"], n["end"])
            p, ln = obj.get("path"), obj.get("line") or obj.get("start")
            if isinstance(p, str) and isinstance(ln, int):
                self.seen.add(p, ln, obj.get("end") if isinstance(obj.get("end"), int) else ln)
            for v in obj.values():
                self._harvest(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj[:200]:
                self._harvest(v, depth + 1)

    # -- helpers ----------------------------------------------------------------------------------------
    def _node(self, ref: str) -> str:
        g = self.ctx.graph
        nid = g.resolve(ref)
        if not nid:
            raise ToolError(f"'{ref}' not found. Try find_symbol or search_code.")
        return nid

    def _reg(self, g, nid):
        n = g.nodes[nid]
        self.seen.add(n["path"], n["start"], n["end"])

    # -- tools --------------------------------------------------------------------------------------------
    def t_search_code(self, query: str, k: int = 8, mode: str = "auto") -> dict:
        hits = self.ctx.retriever.search(query, mode if mode in ("bm25", "vector", "hybrid", "graph", "auto") else "auto", int(k), classify(query))
        for h in hits:
            self.seen.add(h.path, h.start, h.end)
        return {"hits": [h.to_dict(300) for h in hits]}

    def t_find_symbol(self, name: str) -> dict:
        g = self.ctx.graph
        res = [_brief(g, i) for i in g.find(name, limit=8)]
        for r in res:
            self.seen.add(r["path"], r["line"], r["end"])
        return {"matches": res}

    def t_get_symbol(self, symbol: str) -> dict:
        g = self.ctx.graph
        nid = self._node(symbol)
        n = g.nodes[nid]
        self._reg(g, nid)
        d = _brief(g, nid)
        d.update(doc=n["props"].get("doc", ""), complexity=n["props"].get("complexity"), effects=n["props"].get("effects", []),
                 route=n["props"].get("route"), decorators=n["props"].get("decorators", []))
        d["code"] = self.ctx.code_of(nid, 80)
        d["callers"] = [_brief(g, i, {"depth": dpt}) for i, dpt in list(g.callers(nid, 1, 0.5).items())[:10]]
        d["callees"] = [_brief(g, i, {"depth": dpt}) for i, dpt in list(g.callees(nid, 1, 0.5).items()) if g.kind(i) != "class"][:10]
        d["tests"] = [t["name"] for t in impact_mod.tests_for(self.ctx, nid).get("tests", [])[:5]]
        for x in d["callers"] + d["callees"]:
            self.seen.add(x["path"], x["line"], x["end"])
        return d

    def t_get_file(self, path: str, start: int = 1, end: int = 200) -> dict:
        try:
            lines = self.ctx.read_lines(path, int(start), min(int(end), int(start) + 399))
        except Exception as e:
            raise ToolError(f"cannot read {path}: {e}")
        self.seen.add(path, int(start), int(start) + len(lines) - 1)
        return {"path": path, "start": int(start), "text": "\n".join(f"{int(start)+i}: {l}" for i, l in enumerate(lines))}

    def _rel(self, symbol, direction, depth, min_conf):
        g = self.ctx.graph
        nid = self._node(symbol)
        res = g.bfs(nid, direction, int(depth), float(min_conf))
        rows = []
        for i, d in sorted(res.items(), key=lambda x: x[1]):
            if g.kind(i) in ("function", "method", "test", "class", "file"):
                self._reg(g, i)
                rows.append(_brief(g, i, {"depth": d}))
        return {"symbol": g.nodes[nid]["qualname"], "count": len(rows), "results": rows[:60]}

    def t_find_callers(self, symbol: str, depth: int = 1, min_confidence: float = 0.5) -> dict:
        return self._rel(symbol, "in", depth, min_confidence)

    def t_find_callees(self, symbol: str, depth: int = 1, min_confidence: float = 0.5) -> dict:
        r = self._rel(symbol, "out", depth, min_confidence)
        r["results"] = [x for x in r["results"] if x["kind"] != "class"]
        return r

    def t_trace_dependencies(self, target: str, direction: str = "both", depth: int = 2) -> dict:
        g = self.ctx.graph
        nid = self._node(target)
        path = g.nodes[nid]["path"]
        start = f"file:{path}"
        out: dict = {"target": path}
        for dname, adj in (("depends_on", g.out), ("depended_on_by", g.inn)):
            if (dname == "depends_on" and direction == "in") or (dname == "depended_on_by" and direction == "out"):
                continue
            seen, dq, rows = {start}, deque([(start, 0)]), []
            while dq:
                cur, d = dq.popleft()
                if d >= int(depth):
                    continue
                for nxt, *_ in adj[cur].get("IMPORTS", []):
                    if nxt not in seen and g.kind(nxt) in ("file", "external"):
                        seen.add(nxt); rows.append({"path": g.nodes[nxt]["path"] or g.nodes[nxt]["name"], "kind": g.kind(nxt), "depth": d + 1}); dq.append((nxt, d + 1))
            out[dname] = rows[:80]
        return out

    def t_find_tests(self, target: str) -> dict:
        return impact_mod.tests_for(self.ctx, self._node(target))

    def t_impact(self, target: str, depth: int = 5) -> dict:
        r = impact_mod.impact(self.ctx, self._node(target), depth=int(depth))
        for lst in ("direct", "indirect"):
            for x in r.get(lst, []):
                self.seen.add(x["path"], x["line"])
        return r

    def t_explain_flow(self, entry: str, depth: int = 4) -> dict:
        g = self.ctx.graph
        nid = self._node(entry)
        if g.kind(nid) == "api":
            nid = g.props(nid).get("handler") or nid
        nodes, edges = onboarding.call_tree(g, nid, depth=int(depth), min_conf=0.5)
        steps = []
        for n in nodes:
            self._reg(g, n)
            steps.append(_brief(g, n))
        return {"entry": g.nodes[nid]["qualname"], "steps": steps, "edges": [[g.nodes[a]["qualname"], g.nodes[b]["qualname"]] for a, b in dict.fromkeys(edges)],
                "mermaid": onboarding.flow_mermaid(g, nid, depth=int(depth), min_conf=0.5)}

    def t_repo_overview(self) -> dict:
        o = onboarding.overview(self.ctx)
        for m in o["key_modules"]:
            self.seen.add(m["path"], 1, m["loc"])
        return {k: o[k] for k in ("summary", "key_modules", "entry_points", "communities", "glossary", "tour")}

    def t_list_endpoints(self) -> dict:
        g = self.ctx.graph
        rows = []
        for n in g.nodes.values():
            if n["kind"] == "api":
                h = n["props"].get("handler")
                rows.append({"api": n["name"], "handler": g.nodes[h]["qualname"] if h else None, "path": n["path"], "line": n["start"]})
                self.seen.add(n["path"], n["start"], n["end"])
        return {"endpoints": sorted(rows, key=lambda r: r["api"])}

    def t_hotspots(self, top: int = 10) -> dict:
        return {"hotspots": health.hotspots(self.ctx, int(top))}

    def t_health_summary(self) -> dict:
        h = health.health_report(self.ctx)
        return {"score": h["score"], "cycles": h["cycles"][:5], "layering": h["layering"][:8], "dead_code": h["dead_code"][:10],
                "duplicates": h["duplicates"][:6], "test_coverage_by_graph": h["untested"]["coverage"], "untested_top": h["untested"]["top"][:8],
                "complexity": h["complexity"][:6]}

    def t_security_summary(self) -> dict:
        s = security.security_report(self.ctx)
        return {"summary": s["summary"], "findings": s["findings"][:15], "attack_surface": s["attack_surface"][:8], "secrets": s["secrets"][:8],
                "disclaimer": s["disclaimer"]}

    def t_review_diff(self, diff: str) -> dict:
        r = diffreview.review_diff(self.ctx, diff)
        return {k: r[k] for k in ("verdict", "counts", "findings", "changed_symbols", "blast_radius", "markdown")}


def compact(obj, limit: int = 3500) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + "…(truncated)"
