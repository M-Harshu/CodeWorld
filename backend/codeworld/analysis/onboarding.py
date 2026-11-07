"""Onboarding intelligence: architecture overview, flows (Mermaid), glossary, guided tour, good first changes."""
from __future__ import annotations
import re
from collections import Counter, defaultdict, deque

from ..textutil import split_identifier
from . import health

GENERIC = set("""get set init new main test tests self cls args kwargs data value values item items name names list dict str int result results
make create build update delete add remove run handle process check load save find call self_ base util utils helper helpers
for by to with from in is has and or not config cfg impl obj val key keys fn func function method class type types info error errors exception exc msg message""".split())


def _mid(s: str) -> str:
    return "n" + re.sub(r"\W", "_", s)


def _label(s: str) -> str:
    return s.replace('"', "'")[:48]


def entry_points(g) -> list[dict]:
    out = []
    for f in g.files():
        if f["props"].get("has_main"):
            out.append({"kind": "script", "name": f["path"], "path": f["path"], "line": 1})
        if f["name"] in ("__main__.py", "manage.py", "wsgi.py", "asgi.py", "server.js", "index.js", "main.js", "app.js", "main.ts", "index.ts"):
            out.append({"kind": "entry file", "name": f["path"], "path": f["path"], "line": 1})
    for a in g.nodes.values():
        if a["kind"] == "api":
            h = a["props"].get("handler")
            out.append({"kind": "endpoint", "name": a["name"], "path": a["path"], "line": a["start"], "handler": g.nodes[h]["qualname"] if h else "", "id": h})
    return out


def call_tree(g, root: str, depth: int = 4, min_conf: float = 0.6, max_nodes: int = 28) -> tuple[list[str], list[tuple[str, str]]]:
    seen, edges, dq = {root}, [], deque([(root, 0)])
    order = [root]
    while dq and len(seen) < max_nodes:
        cur, d = dq.popleft()
        if d >= depth:
            continue
        for dst, conf, res, _ in g.out[cur].get("CALLS", []):
            if conf < min_conf or g.kind(dst) not in ("function", "method"):
                continue          # constructors link to class nodes; show methods/functions only
            edges.append((cur, dst))
            if dst not in seen and len(seen) < max_nodes:
                seen.add(dst); order.append(dst); dq.append((dst, d + 1))
    return order, edges


def flow_mermaid(g, root: str, **kw) -> str:
    nodes, edges = call_tree(g, root, **kw)
    lines = ["flowchart TD"]
    for n in nodes:
        lines.append(f'  {_mid(n)}["{_label(g.nodes[n]["qualname"])}"]')
    for a, b in dict.fromkeys(edges):
        lines.append(f"  {_mid(a)} --> {_mid(b)}")
    return "\n".join(lines)


def architecture_mermaid(g, max_edges: int = 28) -> str:
    def group(p: str) -> str:
        parts = p.split("/")[:-1]
        return "/".join(parts[:2]) if parts else "(root)"
    agg: Counter = Counter()
    for f in g.files():
        if f["props"].get("is_test"):
            continue
        for dst, *_ in g.out[f["id"]].get("IMPORTS", []):
            if dst.startswith("file:") and not g.props(dst).get("is_test"):
                a, b = group(f["path"]), group(dst[5:])
                if a != b:
                    agg[(a, b)] += 1
    top = agg.most_common(max_edges)
    nodes = sorted({x for e, _ in top for x in e})
    lines = ["flowchart LR"] + [f'  {_mid(n)}["{_label(n)}"]' for n in nodes]
    lines += [f"  {_mid(a)} -->|{w}| {_mid(b)}" for (a, b), w in top]
    return "\n".join(lines) if top else 'flowchart LR\n  A["Single module"]'


def glossary(g, top: int = 18) -> list[dict]:
    cnt: Counter = Counter()
    ex: dict[str, list[str]] = defaultdict(list)
    for n in g.nodes.values():
        if n["kind"] not in ("function", "method", "class") or n["props"].get("is_test"):
            continue
        words = set(split_identifier(n["name"])) | set(w for w in split_identifier(n["props"].get("doc", "")[:120]) if len(w) > 4)
        for w in words:
            if len(w) > 2 and w not in GENERIC and not w.isdigit():
                cnt[w] += 2 if w in split_identifier(n["name"]) else 1
                if len(ex[w]) < 3 and n["qualname"] not in ex[w]:
                    ex[w].append(n["qualname"])
    return [{"term": w, "weight": c, "examples": ex[w]} for w, c in cnt.most_common(top) if c >= 2]


def good_first_changes(ctx, limit: int = 8) -> list[dict]:
    g = ctx.graph
    covered = health.tested_set(g)
    out = []
    for f in g.files():
        for t in f["props"].get("todos", [])[:3]:
            sym = g.enclosing_symbol(f["path"], t["line"])
            out.append({"kind": "resolve TODO", "title": f'{t["tag"]}: {t["text"] or "(no description)"}', "path": f["path"], "line": t["line"],
                        "symbol": g.nodes[sym]["qualname"] if sym else None, "priority": 1})
    for n in g.nodes.values():
        if n["kind"] not in ("function", "method") or n["props"].get("is_test") or n["props"].get("route"):
            continue
        cx, loc = n["props"].get("complexity", 1), n["props"].get("loc", 0)
        callers = len(g.inn[n["id"]].get("CALLS", []))
        if 3 <= loc <= 25 and cx <= 4 and n["id"] not in covered and callers <= 4 and not n["name"].startswith("__"):
            out.append({"kind": "write a test", "title": f'Add a unit test for {n["qualname"]}', "path": n["path"], "line": n["start"], "symbol": n["qualname"], "priority": 2})
        elif not n["props"].get("doc") and 3 <= loc <= 30 and not n["name"].startswith("_") and callers <= 6:
            out.append({"kind": "add docstring", "title": f'Document {n["qualname"]}', "path": n["path"], "line": n["start"], "symbol": n["qualname"], "priority": 3})
    out.sort(key=lambda x: (x["priority"], x["path"]))
    return out[:limit]


def readme_excerpt(ctx) -> str:
    with ctx.con() as con:
        r = con.execute("SELECT path FROM files WHERE language='markdown' AND lower(path) LIKE 'readme%' ORDER BY length(path) LIMIT 1").fetchone()
    if not r:
        return ""
    try:
        return re.sub(r"\n{3,}", "\n\n", ctx.abs_path(r["path"]).read_text("utf8", errors="replace"))[:900]
    except Exception:
        return ""


def overview(ctx) -> dict:
    return ctx.cached("overview", lambda: _overview(ctx))


def _overview(ctx) -> dict:
    g, stats = ctx.graph, ctx.stats()
    eps = entry_points(g)
    pr = g.pagerank
    key = [f for f in sorted((x for x in g.files() if not x["props"].get("is_test") and x["name"] != "__init__.py"), key=lambda f: -pr.get(f["path"], 0))[:8]]
    key_modules = [{"path": f["path"], "rank": round(pr.get(f["path"], 0), 4), "loc": f["props"].get("loc", 0),
                    "doc": f["props"].get("doc", "")[:140],
                    "depended_on_by": len(g.inn[f["id"]].get("IMPORTS", []))} for f in key]
    flows = []
    for e in [x for x in eps if x["kind"] == "endpoint" and x.get("id")][:8]:
        nodes, edges = call_tree(g, e["id"])
        flows.append({"endpoint": e["name"], "handler": e["handler"], "steps": len(nodes), "mermaid": flow_mermaid(g, e["id"])})
    comm = health.communities(ctx)
    summary_bits = []
    langs = stats.get("languages", {})
    summary_bits.append(f"{stats.get('files', 0)} source files (~{stats.get('loc', 0):,} lines) in " + ", ".join(f"{k} ({v})" for k, v in langs.items()))
    summary_bits.append(f"{stats.get('classes', 0)} classes, {stats.get('functions', 0) + stats.get('methods', 0)} functions/methods, {stats.get('apis', 0)} API endpoints, {stats.get('tests', 0)} tests")
    tour = [{"step": 1, "title": "Read the README", "detail": "Start with the project's own description.", "target": None}]
    if eps:
        e = eps[0]
        tour.append({"step": len(tour) + 1, "title": f"Find the entry point: {e['name']}", "detail": "See how the application starts or what it exposes.", "target": e["path"]})
    for m in key_modules[:3]:
        tour.append({"step": len(tour) + 1, "title": f"Study the core module {m['path']}", "detail": f"Imported by {m['depended_on_by']} other file(s) - changes here ripple widely.", "target": m["path"]})
    if flows:
        tour.append({"step": len(tour) + 1, "title": f"Trace one request: {flows[0]['endpoint']}", "detail": "Follow the call chain from endpoint to persistence.", "target": None})
    tour.append({"step": len(tour) + 1, "title": "Read the tests", "detail": "Tests document intended behaviour and show how pieces are used.", "target": None})
    return {"summary": ". ".join(summary_bits) + ".", "readme": readme_excerpt(ctx), "entry_points": eps[:40], "key_modules": key_modules,
            "communities": comm[:10], "architecture": architecture_mermaid(g), "flows": flows, "glossary": glossary(g),
            "tour": tour, "good_first_changes": good_first_changes(ctx)}


def markdown(ctx, narrate: bool = False) -> str:
    o = overview(ctx)
    name = ctx.ref.name
    lines = [f"# {name}: onboarding guide", "", "## At a glance", o["summary"], ""]
    if narrate and ctx.llm.available:
        try:
            prompt = (f"Write a 4-sentence plain-English summary of what this project does and how it is organised.\nREADME:\n{o['readme'][:900]}\n"
                      f"Key modules: {', '.join(m['path'] for m in o['key_modules'][:6])}\nEndpoints: {', '.join(e['name'] for e in o['entry_points'] if e['kind']=='endpoint')[:300]}")
            lines += [ctx.llm.chat([{"role": "user", "content": prompt}], max_tokens=300), ""]
        except Exception:
            pass
    lines += ["## Architecture", "```mermaid", o["architecture"], "```", "", "## Key modules"]
    lines += [f"- `{m['path']}` ({m['loc']} lines, imported by {m['depended_on_by']}) {m['doc']}" for m in o["key_modules"]]
    lines += ["", "## Entry points"] + [f"- {e['kind']}: `{e['name']}`" + (f" -> `{e['handler']}`" if e.get("handler") else "") for e in o["entry_points"][:25]]
    for f in o["flows"][:5]:
        lines += ["", f"## Flow: {f['endpoint']}", "```mermaid", f["mermaid"], "```"]
    lines += ["", "## Glossary"] + [f"- **{x['term']}**: e.g. {', '.join('`'+e+'`' for e in x['examples'])}" for x in o["glossary"]]
    lines += ["", "## Suggested reading order"] + [f"{t['step']}. **{t['title']}**: {t['detail']}" for t in o["tour"]]
    lines += ["", "## Good first changes"] + [f"- {c['title']} (`{c['path']}:{c['line']}`)" for c in o["good_first_changes"]]
    return "\n".join(lines) + "\n"
