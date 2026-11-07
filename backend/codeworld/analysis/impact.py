"""Change-impact analysis: static reverse reachability + git co-change (hybrid) + test selection + risk score."""
from __future__ import annotations
import math

from . import gitmining

SYMS = ("function", "method", "class", "test")


def _seeds(g, target) -> list[str]:
    targets = [target] if isinstance(target, str) else list(target)
    seeds: list[str] = []
    for t in targets:
        nid = g.resolve(t) if t not in g.nodes else t
        if not nid:
            continue
        n = g.node(nid)
        if n["kind"] == "file":
            seeds += [s for s in g.by_path.get(n["path"], []) if g.nodes[s]["kind"] != "class" or True]
            seeds.append(nid)
        else:
            seeds.append(nid)
    return list(dict.fromkeys(seeds))


def impact(ctx, target, depth: int = 5, min_conf: float = 0.5, with_cochange: bool = True, max_graph_nodes: int = 70) -> dict:
    g = ctx.graph
    seeds = _seeds(g, target)
    if not seeds:
        return {"error": f"No symbol or file matches '{target}'."}
    affected: dict[str, int] = {}
    for s in seeds:
        for nid, d in g.bfs(s, "in", depth, min_conf).items():
            if nid not in seeds and (nid not in affected or d < affected[nid]):
                affected[nid] = d
        for nid, d in g.bfs(s, "in", depth, min_conf, types=("INHERITS", "IMPLEMENTS")).items():
            if nid not in seeds and (nid not in affected or d < affected[nid]):
                affected[nid] = d
    seed_files = {g.file_of(s) for s in seeds if g.file_of(s)}
    for f in seed_files:                                           # file importers (module-level coupling)
        for src, conf, *_ in g.inn[f"file:{f}"].get("IMPORTS", []):
            affected.setdefault(src, 1)
    nodes = [(nid, d) for nid, d in affected.items() if g.kind(nid) in SYMS + ("file",)]
    tests = [(nid, d) for nid, d in nodes if g.kind(nid) == "test" or g.props(nid).get("is_test") and g.kind(nid) != "file"]
    prod = [(nid, d) for nid, d in nodes if (nid, d) not in tests and g.kind(nid) != "file"]
    apis = []
    for nid in list(affected) + seeds:
        for dst, *_ in g.out[nid].get("EXPOSES", []):
            apis.append({"api": g.nodes[dst]["name"], "id": dst, "handler": g.nodes[nid]["qualname"], "distance": affected.get(nid, 0)})
    apis = list({a["id"]: a for a in apis}.values())
    files = sorted({g.file_of(n) for n, _ in nodes if g.file_of(n)} - seed_files)
    likely_tests = []
    for f in seed_files:                                           # file-level fallback (name/import based)
        for src, conf, res, _ in g.inn[f"file:{f}"].get("TESTS", []):
            likely_tests.append({"path": g.file_of(src), "confidence": conf, "via": res})
    seen_t = {g.file_of(n) for n, _ in tests}
    likely_tests = [t for t in likely_tests if t["path"] not in seen_t]
    cochange = []
    if with_cochange:
        with ctx.con() as con:
            for f in seed_files:
                for c in gitmining.cochange_for(con, f):
                    c["in_static"] = c["path"] in files or c["path"] in seed_files
                    c["for"] = f
                    cochange.append(c)
    # ---- risk score -------------------------------------------------------------------------------
    reasons, score = [], 0.0
    n_prod = len(prod)
    score += 40 * min(1.0, math.log1p(n_prod) / math.log1p(30))
    if n_prod:
        reasons.append(f"{n_prod} dependent symbol(s) across {len(files)} other file(s)")
    if apis:
        score += 20
        reasons.append(f"reachable from {len(apis)} API endpoint(s)")
    seed_syms = [s for s in seeds if g.kind(s) in ("function", "method")]
    if seed_syms and not tests:
        score += 20
        reasons.append("no test reaches this code")
    cx = max([g.props(s).get("complexity", 1) for s in seed_syms] or [1])
    if cx >= 10:
        score += min(15, cx)
        reasons.append(f"high complexity ({cx})")
    missed = [c for c in cochange if not c["in_static"] and c["confidence"] >= 0.3]
    if missed:
        score += 5
        reasons.append(f"{len(missed)} file(s) usually change with it but are not statically linked")
    score = round(min(100.0, score), 1)
    level = "low" if score < 25 else "medium" if score < 50 else "high" if score < 75 else "critical"
    # ---- subgraph for the UI ------------------------------------------------------------------------
    keep = list(dict.fromkeys(seeds[:6] + [n for n, _ in sorted(nodes, key=lambda x: x[1])]))[:max_graph_nodes]
    keep_set = set(keep)
    role = lambda n: "seed" if n in seeds else "test" if g.kind(n) == "test" else "direct" if affected.get(n, 9) == 1 else "indirect"
    sub_nodes = [{"id": n, "label": g.nodes[n]["qualname"], "kind": g.kind(n), "path": g.nodes[n]["path"], "role": role(n),
                  "depth": affected.get(n, 0)} for n in keep]
    sub_edges = []
    for n in keep:
        for t in ("CALLS", "INHERITS", "IMPORTS"):
            for dst, conf, *_ in g.out[n].get(t, []):
                if dst in keep_set and conf >= min_conf:
                    sub_edges.append({"source": n, "target": dst, "type": t})
    final_tests = sorted([{"id": n, "name": g.nodes[n]["qualname"], "path": g.nodes[n]["path"], "distance": d} for n, d in tests], key=lambda x: (x["distance"], x["name"]))
    return {
        "target": [g.nodes[s]["qualname"] for s in seeds[:5]], "seeds": seeds[:20],
        "risk": {"score": score, "level": level, "reasons": reasons},
        "direct": sorted(_brief(g, [n for n, d in prod if d == 1]), key=lambda x: x["name"]), "indirect": sorted(_brief(g, [n for n, d in prod if d > 1]), key=lambda x: x["name"])[:80],
        "files": files[:80], "apis": apis,
        "tests": final_tests,
        "likely_tests": likely_tests, "cochange": cochange,
        "test_command": generate_test_command(final_tests, likely_tests),
        "counts": {"symbols": n_prod, "direct": sum(1 for _, d in prod if d == 1), "files": len(files), "apis": len(apis), "tests": len(tests)},
        "graph": {"nodes": sub_nodes, "edges": sub_edges},
    }


def generate_test_command(tests: list[dict], likely_tests: list[dict] | None = None) -> str:
    """Generate a runnable pytest or jest/npm command for targeted regression testing."""
    test_paths: set[str] = set()
    test_symbols: set[str] = set()
    for t in (tests or []):
        p = t.get("path")
        if p:
            test_paths.add(p)
        name = t.get("name")
        if name and "::" in name:
            test_symbols.add(name.split("::")[-1])
        elif name:
            test_symbols.add(name)
    for lt in (likely_tests or []):
        p = lt.get("path")
        if p:
            test_paths.add(p)
    if not test_paths:
        return ""
    sorted_paths = sorted(test_paths)
    is_python = any(p.endswith(".py") for p in sorted_paths)
    is_js = any(p.endswith((".js", ".ts", ".jsx", ".tsx")) for p in sorted_paths)
    if is_python:
        cmd = f"pytest {' '.join(sorted_paths)}"
        if test_symbols and len(test_symbols) <= 8:
            k_expr = " or ".join(sorted(test_symbols))
            cmd += f' -k "{k_expr}"'
        return cmd
    elif is_js:
        return f"npm test -- {' '.join(sorted_paths)}"
    return f"pytest {' '.join(sorted_paths)}"


def _brief(g, ids):
    return [{"id": i, "name": g.nodes[i]["qualname"], "kind": g.nodes[i]["kind"], "path": g.nodes[i]["path"], "line": g.nodes[i]["start"]} for i in ids]


def tests_for(ctx, target, depth: int = 6, min_conf: float = 0.5) -> dict:
    """Minimal-test-selection helper: which tests exercise this symbol/file (ranked by call distance)."""
    r = impact(ctx, target, depth=depth, min_conf=min_conf, with_cochange=True, max_graph_nodes=5)
    if "error" in r:
        return r
    cmd = r.get("test_command") or generate_test_command(r["tests"], r["likely_tests"])
    return {"target": r["target"], "tests": r["tests"], "likely_tests": r["likely_tests"],
            "test_command": cmd,
            "cochange_tests": [c for c in r["cochange"] if "test" in c["path"].lower()]}
