"""Near-duplicate function detection via normalised token shingles (identifier/number/string abstracted)."""
from __future__ import annotations
import hashlib
import re
from collections import defaultdict

_TOK = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'|[A-Za-z_]\w*|\d+(?:\.\d+)?|==|!=|<=|>=|\+=|-=|->|[^\s\w]")
_KW = frozenset("""def class return if elif else for while in not and or is None True False try except finally with as import from raise
yield lambda pass break continue assert del global nonlocal async await function const let var new this typeof switch case default throw catch""".split())


def normalise(code: str) -> list[str]:
    code = re.sub(r"#.*|//.*", "", code)
    out = []
    for t in _TOK.findall(code):
        if t[0] in "\"'":
            out.append("STR")
        elif t[0].isdigit():
            out.append("NUM")
        elif t[0].isalpha() or t[0] == "_":
            out.append(t if t in _KW else "ID")
        else:
            out.append(t)
    return out


def shingles(toks: list[str], k: int = 5) -> set[int]:
    if len(toks) < k:
        return {hash(tuple(toks))} if toks else set()
    return {int(hashlib.md5(" ".join(toks[i:i + k]).encode()).hexdigest()[:12], 16) for i in range(len(toks) - k + 1)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def find_duplicates(rows: list[dict], threshold: float = 0.75, min_tokens: int = 25, limit: int = 60) -> list[dict]:
    """rows: [{node_id, path, name, start, end, text}] for function/method chunks."""
    sig: dict[str, set[int]] = {}
    toks_len: dict[str, int] = {}
    for r in rows:
        t = normalise(r["text"])
        if len(t) >= min_tokens:
            sig[r["node_id"]] = shingles(t)
            toks_len[r["node_id"]] = len(t)
    index: dict[int, list[str]] = defaultdict(list)
    for nid, s in sig.items():
        for h in s:
            if h % 3 == 0:
                index[h].append(nid)
    cand: dict[tuple, int] = defaultdict(int)
    for ids in index.values():
        if len(ids) > 12:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                cand[(ids[i], ids[j])] += 1
    by_id = {r["node_id"]: r for r in rows}
    out = []
    for (a, b), shared in cand.items():
        if shared < 2:
            continue
        sim = jaccard(sig[a], sig[b])
        if sim >= threshold:
            ra, rb = by_id[a], by_id[b]
            out.append({"a": {"id": a, "name": ra["name"], "path": ra["path"], "start": ra["start"], "end": ra["end"]},
                        "b": {"id": b, "name": rb["name"], "path": rb["path"], "start": rb["start"], "end": rb["end"]},
                        "similarity": round(sim, 3), "kind": "exact (after normalisation)" if sim > 0.97 else "near-duplicate"})
    out.sort(key=lambda x: -x["similarity"])
    return out[:limit]


def similar_to(code: str, rows: list[dict], exclude: set[str], threshold: float = 0.7, top: int = 3) -> list[dict]:
    t = normalise(code)
    if len(t) < 25:
        return []
    s = shingles(t)
    res = []
    for r in rows:
        if r["node_id"] in exclude:
            continue
        rt = normalise(r["text"])
        if len(rt) < 25:
            continue
        sim = jaccard(s, shingles(rt))
        if sim >= threshold:
            res.append({"id": r["node_id"], "name": r["name"], "path": r["path"], "start": r["start"], "similarity": round(sim, 3)})
    res.sort(key=lambda x: -x["similarity"])
    return res[:top]
