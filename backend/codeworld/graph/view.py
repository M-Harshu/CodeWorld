"""Semantic-zoom graph views: collapsed containers, expandable on demand; edges aggregated to the nearest visible ancestor."""
from __future__ import annotations
from collections import defaultdict

CONTAINERS = ("repo", "module", "file", "class")
SHOWN = ("module", "file", "class", "function", "method", "test", "api", "external")


def _children(g, nid, show_tests=True):
    kids = []
    for c in g.children.get(nid, []):
        k = g.kind(c)
        if k in ("module", "file", "class", "function", "method", "test") and (show_tests or not _is_test(g, c)):
            kids.append(c)
    return kids


def _is_test(g, nid):
    return bool(g.props(nid).get("is_test")) or g.kind(nid) == "test"


def build_view(g, expanded: list[str], edge_types=("CALLS", "IMPORTS", "INHERITS"), highlight: list[str] | None = None,
               min_conf: float = 0.5, show_tests: bool = False, show_external: bool = False, max_nodes: int = 160, focus: str | None = None) -> dict:
    expanded_set = set(expanded)
    visible: list[str] = []
    stack = list(_children(g, "repo:root", show_tests))
    while stack:
        nid = stack.pop(0)
        k = g.kind(nid)
        if nid in expanded_set and k in CONTAINERS:
            kids = _children(g, nid, show_tests)
            if kids:
                stack = kids + stack
                continue
        visible.append(nid)
    if focus and focus in g.nodes and focus not in visible:       # make sure a focused node is reachable on screen
        anc = [a for a in g.ancestors(focus) if a in visible]
        if not anc:
            visible.append(focus)
    vis_set = set(visible)
    truncated = len(visible) > max_nodes
    anc_cache: dict[str, str | None] = {}

    def rep(nid: str) -> str | None:
        if nid in anc_cache:
            return anc_cache[nid]
        cur, out = nid, None
        while cur:
            if cur in vis_set:
                out = cur
                break
            cur = (g.nodes.get(cur) or {}).get("parent")
        anc_cache[nid] = out
        return out

    agg: dict[tuple, dict] = {}
    for src, by_type in g.out.items():
        if src not in g.nodes:
            continue
        for t in edge_types:
            for dst, conf, res, line in by_type.get(t, []):
                if conf < min_conf or dst not in g.nodes:
                    continue
                if g.kind(dst) in ("external", "effect", "api") and not show_external:
                    continue
                a, b = rep(src), rep(dst)
                if t == "IMPORTS" and not (src.startswith("file:") and dst.startswith("file:")):
                    pass
                if a and b and a != b:
                    key = (a, b, t)
                    e = agg.setdefault(key, {"source": a, "target": b, "type": t, "weight": 0})
                    e["weight"] += 1
    nodes = []
    hl = set(highlight or [])
    for nid in visible[:max_nodes]:
        n = g.nodes[nid]
        kids = len(_children(g, nid, show_tests)) if n["kind"] in CONTAINERS else 0
        nodes.append({"id": nid, "label": n["name"] if n["kind"] != "module" else n["name"] + "/", "kind": n["kind"], "path": n["path"],
                      "line": n["start"], "children": kids, "expanded": nid in expanded_set,
                      "loc": n["props"].get("loc", 0), "complexity": n["props"].get("complexity", 0),
                      "top": _top_module(g, nid), "highlight": nid in hl or any(h for h in hl if h in _descendant_hint(g, nid, hl))})
    keep = {n["id"] for n in nodes}
    edges = [e for e in agg.values() if e["source"] in keep and e["target"] in keep]
    edges.sort(key=lambda e: -e["weight"])
    return {"nodes": nodes, "edges": edges[:600], "truncated": truncated, "total_visible": len(visible)}


def _top_module(g, nid: str) -> str:
    n = g.nodes[nid]
    p = n["path"] or ""
    return p.split("/")[0] if "/" in p else (p if n["kind"] == "module" else "(root)")


def _descendant_hint(g, nid: str, hl: set) -> set:
    """Collapsed containers glow if something inside them is highlighted."""
    n = g.nodes[nid]
    if n["kind"] not in CONTAINERS:
        return set()
    return {h for h in hl if nid in g.ancestors(h)}
