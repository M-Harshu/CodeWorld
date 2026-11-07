"""Codebase health: cycles, dead code, hotspots, layering, bus factor, test coverage-by-graph, duplicates, complexity."""
from __future__ import annotations
import math
from pathlib import Path
import re
from collections import Counter, deque

import networkx as nx
import yaml

from . import gitmining
from .duplicates import find_duplicates

DEFAULT_LAYERS = [["api", "apis", "routes", "routers", "controllers", "views", "handlers", "web", "cli", "ui"],
                  ["services", "service", "usecases", "use_cases", "core", "domain", "logic", "managers"],
                  ["repositories", "repository", "dao", "db", "database", "models", "model", "store", "storage", "persistence"],
                  ["utils", "util", "lib", "common", "helpers", "shared"]]


def load_rules(ctx) -> dict:
    for name in (".codeworld.yml", ".codeworld.yaml"):
        src = getattr(getattr(ctx, "ref", None), "src", None)
        if src:
            p = Path(src) / name
            if p.exists():
                try:
                    d = yaml.safe_load(p.read_text("utf8")) or {}
                    return {
                        "layers": d.get("layers") or DEFAULT_LAYERS,
                        "forbid": d.get("forbid") or [],
                        "ignore": d.get("ignore") or [],
                        "changed_lines_only": bool(d.get("changed_lines_only", False)),
                        "source": name,
                    }
                except Exception:
                    pass
    return {"layers": DEFAULT_LAYERS, "forbid": [], "ignore": [], "changed_lines_only": False, "source": "default heuristics"}


def layer_of(path: str, layers, use_stem: bool = False) -> int | None:
    parts = [p.lower() for p in path.split("/")[:-1]] + ([path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()] if use_stem else [])
    for part in parts:
        for i, names in enumerate(layers):
            if part in names:
                return i
    return None


def layering_violations(ctx, files: set[str] | None = None) -> list[dict]:
    g, rules, out = ctx.graph, load_rules(ctx), []
    layers, stem = rules["layers"], rules["source"] != "default heuristics"
    for src in g.files():
        if files is not None and src["path"] not in files:
            continue
        ls = layer_of(src["path"], layers, stem)
        for dst, conf, res, line in g.out[src["id"]].get("IMPORTS", []):
            if not dst.startswith("file:"):
                continue
            dpath = dst[5:]
            ld = layer_of(dpath, layers, stem)
            if g.props(src["id"]).get("is_test"):
                continue
            if ls is not None and ld is not None and ld < ls and not (ls == len(layers) - 1 and False):
                out.append({"from": src["path"], "to": dpath, "line": line, "rule": f"layer '{layers[ls][0]}' must not import higher layer '{layers[ld][0]}'"})
            for f in rules["forbid"]:
                if src["path"].startswith(str(f.get("from", "\0"))) and dpath.startswith(str(f.get("to", "\0"))):
                    out.append({"from": src["path"], "to": dpath, "line": line, "rule": f"forbidden dependency {f.get('from')} -> {f.get('to')}"})
    return out


def import_cycles(g) -> list[list[str]]:
    ig = nx.DiGraph()
    for f in g.files():
        for dst, *_ in g.out[f["id"]].get("IMPORTS", []):
            if dst.startswith("file:"):
                ig.add_edge(f["path"], dst[5:])
    cycles = []
    for comp in nx.strongly_connected_components(ig):
        if len(comp) > 1:
            try:
                cyc = [e[0] for e in nx.find_cycle(ig.subgraph(comp))]
            except Exception:
                cyc = sorted(comp)
            cycles.append(cyc)
    return sorted(cycles, key=len)


def tested_set(g, depth: int = 8, min_conf: float = 0.5) -> set[str]:
    seen: set[str] = set()
    dq = deque((n["id"], 0) for n in g.nodes.values() if n["kind"] == "test")
    seen.update(i for i, _ in dq)
    while dq:
        cur, d = dq.popleft()
        if d >= depth:
            continue
        for dst, conf, *_ in g.out[cur].get("CALLS", []):
            if conf >= min_conf and dst not in seen:
                seen.add(dst)
                dq.append((dst, d + 1))
    return seen


def untested(g) -> dict:
    covered = tested_set(g)
    syms = [n for n in g.nodes.values() if n["kind"] in ("function", "method") and not n["props"].get("is_test")]
    miss = [n for n in syms if n["id"] not in covered]
    miss.sort(key=lambda n: -(n["props"].get("complexity", 1) * (1 + len(g.inn[n["id"]].get("CALLS", [])))))
    return {"coverage": round(1 - len(miss) / len(syms), 3) if syms else 1.0, "total": len(syms), "untested": len(miss),
            "top": [{"id": n["id"], "name": n["qualname"], "path": n["path"], "line": n["start"], "complexity": n["props"].get("complexity", 1),
                     "callers": len(g.inn[n["id"]].get("CALLS", []))} for n in miss[:40]]}


def dead_code(ctx, limit: int = 60) -> list[dict]:
    g = ctx.graph
    is_app = any(n["kind"] == "api" for n in g.nodes.values()) or any(f["props"].get("has_main") for f in g.files())
    cands = []
    for n in g.nodes.values():
        if n["kind"] not in ("function", "method") or n["props"].get("is_test"):
            continue
        name = n["name"]
        if name.startswith("__") or name in ("main", "setUp", "tearDown", "constructor", "render"):
            continue
        decos = [d for d in n["props"].get("decorators", []) if not re.search(r"staticmethod|classmethod", d)]
        if decos or n["props"].get("route"):
            continue
        if any(c >= 0.3 for _, c, *_ in g.inn[n["id"]].get("CALLS", [])) or g.out[n["id"]].get("EXPOSES"):
            continue
        parent = n["parent"]
        if parent and g.kind(parent) == "class":                     # overrides/implementations are live via polymorphism
            if g.out[parent].get("INHERITS") or g.out[parent].get("IMPLEMENTS"):
                continue
        cands.append(n)
    out = []
    pub_note = "exported as public API (__init__/__all__) - unused inside this repo but probably used by external code"
    with ctx.con() as con:
        for n in cands[:300]:
            hits = con.execute("SELECT COUNT(*) c FROM chunks_fts WHERE chunks_fts MATCH ?", (f'"{n["name"].lower()}"',)).fetchone()["c"]
            public = not n["name"].startswith("_")
            if n["props"].get("public_api"):
                out.append({"id": n["id"], "name": n["qualname"], "path": n["path"], "line": n["start"], "loc": n["props"].get("loc", 0),
                            "confidence": "low", "note": pub_note})
                continue
            conf = "high" if hits <= 2 and (is_app or not public) else "medium"
            note = "no callers, not an endpoint"
            if hits > 2:
                note += "; name also appears elsewhere (dynamic use?)"
            elif public and not is_app:
                note += "; public name in a library - may be used by external code"
            out.append({"id": n["id"], "name": n["qualname"], "path": n["path"], "line": n["start"], "loc": n["props"].get("loc", 0),
                        "confidence": conf, "note": note})
    out.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["confidence"]], -x["loc"]))
    return out[:limit]


def hotspots(ctx, top: int = 20) -> list[dict]:
    g = ctx.graph
    with ctx.con() as con:
        rows = {r["path"]: dict(r) for r in con.execute("SELECT * FROM git_file")}
    pr = g.pagerank
    out = []
    for f in g.files():
        if f["props"].get("is_test"):
            continue
        cx = f["props"].get("complexity", 1)
        git = rows.get(f["path"])
        churn = git["commits"] if git else 0
        score = (churn * (1 + cx / 20)) if git else cx * (1 + 50 * pr.get(f["path"], 0))
        out.append({"path": f["path"], "score": round(score, 2), "commits": churn, "complexity": cx, "loc": f["props"].get("loc", 0),
                    "authors": git["authors"] if git else None, "basis": "churn x complexity" if git else "complexity x centrality (no git history)"})
    out.sort(key=lambda x: -x["score"])
    return out[:top]


def bus_factor(ctx) -> dict:
    with ctx.con() as con:
        commits = con.execute("SELECT author, COUNT(*) c FROM git_commits GROUP BY author ORDER BY c DESC").fetchall()
        risky = con.execute("SELECT path, commits, top_author_share FROM git_file WHERE commits>=5 AND authors=1 ORDER BY commits DESC LIMIT 15").fetchall()
    if not commits:
        return {"available": False}
    total, acc, k = sum(r["c"] for r in commits), 0, 0
    for r in commits:
        acc += r["c"]; k += 1
        if acc >= total * 0.5:
            break
    return {"available": True, "bus_factor": k, "authors": len(commits), "top_authors": [{"author": r["author"], "commits": r["c"]} for r in commits[:5]],
            "single_owner_files": [dict(r) for r in risky]}


def duplicates(ctx) -> list[dict]:
    with ctx.con() as con:
        rows = [dict(r) for r in con.execute("SELECT node_id, path, name, start, end, text FROM chunks WHERE kind IN ('function','method') AND node_id IS NOT NULL")]
    rows = [r for r in rows if not ctx.graph.props(r["node_id"]).get("is_test")]
    return find_duplicates(rows)


def complexity_top(g, top: int = 15) -> list[dict]:
    syms = [n for n in g.nodes.values() if n["kind"] in ("function", "method") and not n["props"].get("is_test")]
    syms.sort(key=lambda n: -n["props"].get("complexity", 1))
    return [{"id": n["id"], "name": n["qualname"], "path": n["path"], "line": n["start"], "complexity": n["props"]["complexity"], "loc": n["props"].get("loc", 0)}
            for n in syms[:top] if n["props"].get("complexity", 1) >= 8]


def communities(ctx) -> list[dict]:
    g = ctx.graph
    out = []
    for i, c in enumerate(sorted(g.communities, key=len, reverse=True)):
        files = sorted(c)
        prefix = _common_prefix(files)
        out.append({"id": i, "label": prefix or f"cluster {i + 1}", "size": len(files), "files": files[:12]})
    return out


def _common_prefix(paths: list[str]) -> str:
    if not paths:
        return ""
    parts = [p.split("/")[:-1] for p in paths]
    pre = []
    for items in zip(*parts):
        if len(set(items)) == 1:
            pre.append(items[0])
        else:
            break
    return "/".join(pre)


def health_report(ctx) -> dict:
    return ctx.cached("health", lambda: _health_report(ctx))


def _health_report(ctx) -> dict:
    from .docdrift import check_doc_drift
    g = ctx.graph
    cyc, lay, dead, dup, ut = import_cycles(g), layering_violations(ctx), dead_code(ctx), duplicates(ctx), untested(g)
    cx = complexity_top(g)
    dd = check_doc_drift(ctx)
    score = 100.0
    score -= min(20, 6 * len(cyc)) + min(15, 3 * len(lay)) + min(10, 0.5 * len([d for d in dead if d["confidence"] == "high"]))
    score -= min(10, 3 * len(dup)) + min(10, 2 * len(cx)) + 25 * (1 - ut["coverage"])
    return {"score": round(max(0.0, score), 1), "cycles": cyc, "layering": lay, "layering_rules": load_rules(ctx)["source"], "dead_code": dead,
            "duplicates": dup, "untested": ut, "complexity": cx, "hotspots": hotspots(ctx), "bus_factor": bus_factor(ctx),
            "communities": communities(ctx), "doc_drift": dd}
