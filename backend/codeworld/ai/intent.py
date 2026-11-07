"""Lightweight rule-based intent + symbol-mention detection (used to route retrieval and by the offline agent)."""
from __future__ import annotations
import re

PATTERNS = [
    ("impact", r"\b(impact|blast radius|break|breaks|affect|affected|safe to (change|modify|delete|remove|rename)|what happens if i (change|remove|delete|rename)|ripple)\b"),
    ("callers", r"(who|what|which|where)\b.*\b(call|calls|called|use|uses|used|invoke|invokes|reference|references|depend on|depends on)\b|\bcallers? of\b|\busages? of\b"),
    ("callees", r"\bwhat does .* (call|use|invoke|depend on)\b|\bcallees? of\b|\bdependencies of\b"),
    ("tests", r"\btests?\b.*\b(for|cover|covers|covering)\b|\bwhich tests\b|\bis .* tested\b|\bcoverage\b"),
    ("security", r"\b(security|vulnerab\w*|unsafe|injection|attack surface|sink|exploit|insecure)\b"),
    ("flow", r"\bhow (does|do|is|are)\b|\bwalk me through\b|\btrace\b|\bwhat happens when\b|\bflow\b|\blifecycle\b|\bend[- ]to[- ]end\b"),
    ("overview", r"\b(overview|architecture|high[- ]level|structure|what is this (project|repo|codebase)|summari[sz]e|big picture)\b"),
    ("locate", r"\bwhere (is|are|do|does)\b|\b(find|locate)\b|\bwhich file\b"),
]
_IDENT = re.compile(r"`([^`]+)`|\b([A-Za-z_]+_[A-Za-z0-9_]+)\b|\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b|\b([A-Za-z_]\w*)\(\)")


def classify(q: str) -> str:
    ql = q.lower()
    for name, rx in PATTERNS:
        if re.search(rx, ql):
            return name
    return "explain"


def mentions(q: str, graph, limit: int = 3) -> list[str]:
    cands: list[str] = re.findall(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", q)
    for m in _IDENT.finditer(q):
        t = next((g for g in m.groups() if g), None)
        if t:
            cands.append(t.strip("() "))
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", q):
        cands.append(w)
    out: list[str] = []
    for c in cands:
        found = []
        for h in graph.find(c, limit=6):
            n = graph.node(h)
            exact = n and (n["name"].lower() == c.lower() or n["qualname"].lower() == c.lower())
            if exact and n["kind"] in ("function", "method", "class", "file", "api"):
                found.append((0 if (n["name"] == c or n["qualname"] == c) else 1, h))   # prefer case-exact matches
        found.sort()
        if found and found[0][0] == 0:
            found = [f for f in found if f[0] == 0]
        for _, h in found[:2]:
            if h not in out:
                out.append(h)
        if len(out) >= limit:
            break
    return out[:limit]
