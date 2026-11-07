"""Automatically generated benchmarks (no manual labels, so they scale to any repository).

doc2code : query = first sentence of a docstring with the symbol's own name removed (CodeSearchNet-style);   gold = that symbol.
callers  : "Which functions call X?"; gold = callers observed in a dynamic trace of the test suite (ground truth, not the static graph).
"""
from __future__ import annotations
import random
import re

from ..textutil import split_identifier


def _first_sentence(doc: str, max_words: int = 40) -> str:
    s = re.split(r"(?<=[.!?])\s", doc.strip(), maxsplit=1)[0]
    return " ".join(s.split()[:max_words])


def doc2code(ctx, n: int = 200, seed: int = 7, min_words: int = 8) -> list[dict]:
    g, rnd = ctx.graph, random.Random(seed)
    cands = [x for x in g.nodes.values() if x["kind"] in ("function", "method", "class") and not x["props"].get("is_test")
             and len(x["props"].get("doc", "").split()) >= min_words and not x["name"].startswith("__")]
    rnd.shuffle(cands)
    out = []
    for x in cands:
        q = _first_sentence(x["props"]["doc"])
        for tok in {x["name"], *split_identifier(x["name"])}:
            if len(tok) > 2:
                q = re.sub(rf"(?i)\b{re.escape(tok)}\b", " ", q)
        q = " ".join(q.split())
        if len(q.split()) < 5:
            continue
        out.append({"id": f"d{len(out)}", "type": "doc2code", "question": q, "gold_symbols": [x["qualname"]], "gold_files": [x["path"]]})
        if len(out) >= n:
            break
    return out


def callers(ctx, truth: list[list[str]], n: int = 100, seed: int = 7) -> list[dict]:
    g, rnd = ctx.graph, random.Random(seed)
    by_callee: dict[str, set[str]] = {}
    for a, b in truth:
        pa, qa = a.split("::", 1)
        pb, qb = b.split("::", 1)
        na, nb = g.node(f"sym:{a}"), g.node(f"sym:{b}")
        if not na or not nb or na["props"].get("is_test") or nb["props"].get("is_test") or nb["name"].startswith("__"):
            continue
        by_callee.setdefault(b, set()).add(a)
    items = [(b, sorted(cs)) for b, cs in by_callee.items() if 1 <= len(cs) <= 12]
    rnd.shuffle(items)
    out = []
    for b, cs in items[:n]:
        nb = g.node(f"sym:{b}")
        out.append({"id": f"c{len(out)}", "type": "callers", "question": f"Which functions call {nb['name']}?",
                    "gold_symbols": [c.split("::", 1)[1] for c in cs], "gold_files": sorted({c.split("::", 1)[0] for c in cs}), "target": nb["id"]})
    return out
