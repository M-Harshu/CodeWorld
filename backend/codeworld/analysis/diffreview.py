"""AI-PR guardrail: graph-aware review of a diff. Catches what code review of AI-generated changes tends to miss."""
from __future__ import annotations
from pathlib import Path
import re
import subprocess

from ..parser import ParseResult
from ..parser.common import JS_EFFECTS, PY_EFFECTS, detect_effects
from . import gitmining, health, importcheck
from .duplicates import similar_to
from .impact import impact
from .security import SEV, SINKS, attack_surface

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1, "info": 0}


def suggest_reviewers(src_path: Path | None, files: list[str], max_reviewers: int = 4) -> list[dict]:
    """Suggest reviewers based on git commit author history for modified files."""
    if not src_path or not files:
        return []
    import collections
    counts: collections.Counter = collections.Counter()
    try:
        cmd = ["git", "log", "-n", "80", "--format=%an", "--"] + files[:15]
        res = subprocess.run(cmd, cwd=src_path, capture_output=True, text=True, check=False, timeout=5)
        for line in res.stdout.splitlines():
            a = line.strip()
            if a:
                counts[a] += 1
    except Exception:
        return []
    return [{"name": name, "commits": c} for name, c in counts.most_common(max_reviewers)]


def compute_architecture_diff(base_graph, head_graph, changed_files: set[str]) -> dict:
    """Identify new dependencies, call edges, and symbols introduced by the PR."""
    base_edges = set()
    if base_graph:
        for src, out_map in getattr(base_graph, "out", {}).items():
            for dst, conf, t, rule in out_map.get("CALLS", []) + out_map.get("IMPORTS", []):
                base_edges.add((src, dst, t))

    new_calls = []
    new_imports = []
    new_symbols = []

    if head_graph:
        for n_id, n_data in head_graph.nodes.items():
            if n_data.get("path") in changed_files:
                if not base_graph or n_id not in base_graph.nodes:
                    new_symbols.append({"id": n_id, "qualname": n_data.get("qualname"), "kind": n_data.get("kind"), "path": n_data.get("path")})
        for src, out_map in getattr(head_graph, "out", {}).items():
            s_node = head_graph.nodes.get(src, {})
            if s_node.get("path") in changed_files:
                for dst, conf, t, rule in out_map.get("CALLS", []) + out_map.get("IMPORTS", []):
                    edge_key = (src, dst, t)
                    if edge_key not in base_edges:
                        d_node = head_graph.nodes.get(dst, {})
                        item = {
                            "source": s_node.get("qualname", src),
                            "target": d_node.get("qualname", dst),
                            "type": t,
                        }
                        if t == "CALLS":
                            new_calls.append(item)
                        else:
                            new_imports.append(item)

    return {
        "new_symbols": new_symbols[:15],
        "new_calls": new_calls[:15],
        "new_imports": new_imports[:15],
        "summary": {
            "new_symbols": len(new_symbols),
            "new_calls": len(new_calls),
            "new_imports": len(new_imports),
        },
    }


def summarize_pr_risk(report: dict) -> str:
    """Generate a succinct PR summary and risk explanation grounded in graph facts."""
    counts = report.get("counts", {})
    files = report.get("changed_files", [])
    b = report.get("blast_radius") or {}
    risk = b.get("risk", {})
    level = risk.get("level", "low")
    score = risk.get("score", 0)

    summary = (
        f"This pull request modifies {len(files)} file{'s' if len(files) != 1 else ''}. "
        f"Guardrail risk assessment is **{level.upper()}** (score {score}/100) with "
        f"{counts.get('high', 0)} high and {counts.get('medium', 0)} medium severity finding{'s' if counts.get('medium', 0) != 1 else ''}. "
    )
    if b.get("tests"):
        summary += f"Impact analysis identified {len(b['tests'])} reaching regression test{'s' if len(b['tests']) != 1 else ''}."
    return summary


def parse_unified_diff(text: str) -> dict[str, dict]:
    files: dict[str, dict] = {}
    cur, new_ln, old_ln = None, 0, 0
    for line in text.splitlines():
        if line.startswith("diff --git"):
            m = re.match(r"diff --git a/(.*) b/(.*)", line)
            cur = m.group(2) if m else None
            if cur:
                files[cur] = {"added": set(), "removed": set(), "removed_text": [], "added_text": [], "new_file": False, "deleted": False}
        elif line.startswith("+++ ") and cur is None:
            cur = line[4:].strip().removeprefix("b/")
            files[cur] = {"added": set(), "removed": set(), "removed_text": [], "added_text": [], "new_file": False, "deleted": False}
        elif cur and line.startswith("new file mode"):
            files[cur]["new_file"] = True
        elif cur and line.startswith("deleted file mode"):
            files[cur]["deleted"] = True
        elif line.startswith("@@") and cur:
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", line)
            if m:
                old_ln, new_ln = int(m.group(1)), int(m.group(2))
        elif cur and line.startswith("+") and not line.startswith("+++"):
            files[cur]["added"].add(new_ln); files[cur]["added_text"].append(line[1:]); files[cur].setdefault("added_map", {})[new_ln] = line[1:]; new_ln += 1
        elif cur and line.startswith("-") and not line.startswith("---"):
            files[cur]["removed"].add(old_ln); files[cur]["removed_text"].append(line[1:]); old_ln += 1
        elif cur and not line.startswith("\\"):
            new_ln += 1; old_ln += 1
    return files


def git_diff(src, base: str, head: str = "HEAD") -> str:
    if not re.fullmatch(r"[\w./\-~^@]{1,120}", base) or not re.fullmatch(r"[\w./\-~^@]{1,120}", head):
        raise ValueError("invalid ref")
    r = subprocess.run(["git", "-C", str(src), "diff", "--unified=0", "--no-color", f"{base}...{head}"], capture_output=True, timeout=120)
    if r.returncode != 0:
        r = subprocess.run(["git", "-C", str(src), "diff", "--unified=0", "--no-color", base, head], capture_output=True, timeout=120)
    return r.stdout.decode("utf8", "replace")


def _finding(sev, cat, title, detail, path=None, line=None, symbol=None):
    return {"severity": sev, "category": cat, "title": title, "detail": detail, "path": path, "line": line, "symbol": symbol}


def apply_rules(ctx, findings: list[dict], code_files: dict[str, dict]) -> list[dict]:
    """Filter findings according to repository rules (.codeworld.yml).

    Supports:
      - `ignore`: list of rule categories to ignore (e.g. ['untested-change', 'duplicate-logic'])
      - `changed_lines_only`: if true, report medium/low severity findings only on newly added lines
    """
    rules = health.load_rules(ctx)
    ignore = set(rules.get("ignore", []))
    changed_only = bool(rules.get("changed_lines_only", False))

    filtered = []
    for f in findings:
        cat = f.get("category", "")
        if cat in ignore:
            continue
        if changed_only and f.get("severity") in ("medium", "low"):
            path = f.get("path")
            line = f.get("line")
            if path in code_files and line is not None:
                if line not in code_files[path].get("added", set()):
                    continue
        filtered.append(f)
    return filtered


def review_diff(ctx, diff_text: str) -> dict:
    g = ctx.graph
    files = parse_unified_diff(diff_text)
    code_files = {p: f for p, f in files.items() if g.node(f"file:{p}") and not f["deleted"]}
    findings: list[dict] = []
    changed: list[str] = []
    for p, f in code_files.items():
        syms = {s for ln in f["added"] for s in [g.enclosing_symbol(p, ln)] if s}
        changed += sorted(syms)
    changed = list(dict.fromkeys(changed))
    prod_changed = [s for s in changed if not g.props(s).get("is_test") and g.kind(s) in ("function", "method")]
    # 1 ---- blast radius ------------------------------------------------------------------------------------
    blast = impact(ctx, changed, depth=5, with_cochange=True, max_graph_nodes=60) if changed else None
    if blast and "error" not in blast:
        c = blast["counts"]
        if blast["risk"]["level"] in ("high", "critical"):
            findings.append(_finding("high" if blast["risk"]["level"] == "critical" else "medium", "blast-radius",
                                     f"Large blast radius ({blast['risk']['level']}, score {blast['risk']['score']})",
                                     f"{c['symbols']} dependent symbols, {c['files']} other files, {c['apis']} endpoints. " + "; ".join(blast["risk"]["reasons"])))
        for cc in blast["cochange"]:
            if not cc["in_static"] and cc["confidence"] >= 0.4 and cc["path"] not in code_files:
                findings.append(_finding("low", "co-change", f"`{cc['path']}` usually changes with `{cc['for']}`",
                                         f"Changed together in {cc['support']} past commits ({int(cc['confidence']*100)}%) but is not part of this diff.", cc["path"]))
    # 2 ---- untested changed code ----------------------------------------------------------------------------
    covered = health.tested_set(g)
    for s in prod_changed:
        if s not in covered and g.props(s).get("loc", 0) >= 3:
            n = g.node(s)
            findings.append(_finding("medium" if g.props(s).get("complexity", 1) >= 5 else "low", "untested-change", f"Changed code has no test reaching it: {n['qualname']}",
                                     "No test calls this function directly or transitively. Add or extend a test.", n["path"], n["start"], n["qualname"]))
    # 3 ---- hallucinated imports / APIs ----------------------------------------------------------------------
    added_lines = {p: f["added"] for p, f in code_files.items()}
    seen_import_titles: set[str] = set()
    for h in importcheck.check_imports(ctx, added_lines):
        key = (h["path"], h["title"])
        if key not in seen_import_titles:
            seen_import_titles.add(key)
            findings.append(_finding(h["severity"], h["category"], h["title"], h["detail"], h["path"], h["line"]))
    # Also check directly from diff text (catches cases where the index was not updated on Windows)
    for h in importcheck.check_diff_imports(ctx, code_files):
        key = (h["path"], h["title"])
        if key not in seen_import_titles:
            seen_import_titles.add(key)
            findings.append(_finding(h["severity"], h["category"], h["title"], h["detail"], h["path"], h["line"]))
    # 4 ---- architecture drift: layering + cycles --------------------------------------------------------------
    for v in health.layering_violations(ctx, set(code_files)):
        if v["line"] in code_files[v["from"]]["added"]:
            findings.append(_finding("high", "layering", f"New forbidden dependency {v['from']} -> {v['to']}", v["rule"], v["from"], v["line"]))
    for cyc in health.import_cycles(g):
        hit = [p for p in cyc if p in code_files]
        if hit:
            findings.append(_finding("medium", "import-cycle", "Import cycle involves changed files", " -> ".join(cyc + [cyc[0]]), hit[0]))
    # 5 ---- duplicated logic (missed reuse) ---------------------------------------------------------------------
    with ctx.con() as con:
        rows = [dict(r) for r in con.execute("SELECT node_id, path, name, start, end, text FROM chunks WHERE kind IN ('function','method') AND node_id IS NOT NULL")]
    for s in prod_changed:
        n = g.node(s)
        mine = next((r for r in rows if r["node_id"] == s), None)
        if not mine or n["props"].get("loc", 0) < 5:
            continue
        for d in similar_to(mine["text"], [r for r in rows if not g.props(r["node_id"]).get("is_test")], {s}):
            findings.append(_finding("medium", "duplicate-logic", f"`{n['qualname']}` closely duplicates `{d['name']}` ({int(d['similarity']*100)}% similar)",
                                     f"Consider reusing `{d['name']}` ({d['path']}:{d['start']}) instead of adding a copy.", n["path"], n["start"], n["qualname"]))
    # 6 ---- risky sinks reachable from endpoints ------------------------------------------------------------------
    surf = {r["handler"]: r for r in attack_surface(ctx)}
    for s in changed:
        n = g.node(s)
        eff = [e for e in (n["props"].get("effects") or []) if e in SINKS and SEV[SINKS[e][0]] >= 2]
        added_here = [t for ln, t in (code_files.get(n["path"], {}).get("added_map", {}) or {}).items() if n["start"] <= ln <= n["end"]]
        introduced = set()
        for t in added_here:                        # sinks only count as NEW when an added line itself performs the call
            for callee in re.findall(r"[\w.]+(?=\()", t):
                introduced.update(detect_effects(callee, PY_EFFECTS + JS_EFFECTS))
        for e in eff:
            reach = [a["api"] for a in surf.values() if any(r["symbol"] == n["qualname"] for r in a["risks"])]
            new = e in introduced
            sev = ("high" if SEV[SINKS[e][0]] >= 3 else "medium") if new else "low"
            title = f"{'New' if new else 'Existing'} `{e}` sink in changed function: {n['qualname']}"
            findings.append(_finding(sev, "security-sink", title,
                                     SINKS[e][1] + ("" if new else " (already present before this change; touched code only)") + (f". Reachable from: {', '.join(reach[:4])}" if reach else ""),
                                     n["path"], n["start"], n["qualname"]))
    # 7 ---- removed symbols that are still referenced ---------------------------------------------------------------
    still_defined = {n["name"] for n in g.nodes.values() if n["kind"] in ("function", "method", "class")}
    removed_defs: dict[str, str] = {}
    for p, f in files.items():
        added_blob = "\n".join(f["added_text"])
        for t in f["removed_text"]:
            m = re.match(r"\s*(?:async\s+)?(?:def|class|function)\s+(\w+)", t)
            if m and m.group(1) not in still_defined and not re.search(rf"\b{m.group(1)}\b", added_blob):
                removed_defs[m.group(1)] = p
    if removed_defs:
        with ctx.con() as con:
            for r in con.execute("SELECT path, json FROM parsed"):
                pr = ParseResult.from_json(r["json"])
                for c in pr.calls:
                    last = c.text.split(".")[-1]
                    if last in removed_defs:
                        findings.append(_finding("high", "dangling-reference", f"`{last}` was removed but is still called",
                                                 f"Removed in {removed_defs[last]}; still referenced at {pr.path}:{c.line}.", pr.path, c.line))
    # 8 ---- complexity growth -------------------------------------------------------------------------------------------
    for s in prod_changed:
        cx = g.props(s).get("complexity", 1)
        if cx >= 15:
            n = g.node(s)
            findings.append(_finding("low", "complexity", f"High cyclomatic complexity ({cx}): {n['qualname']}", "Consider splitting this function.", n["path"], n["start"], n["qualname"]))
    findings = apply_rules(ctx, findings, code_files)
    findings.sort(key=lambda f: -SEVERITY_ORDER[f["severity"]])
    seen, uniq = set(), []
    for f in findings:
        k = (f["category"], f["title"], f["path"], f["line"])
        if k not in seen:
            seen.add(k); uniq.append(f)
    counts = {s: sum(1 for f in uniq if f["severity"] == s) for s in ("high", "medium", "low")}
    verdict = "fail" if counts["high"] else "warn" if counts["medium"] else "pass"
    src_dir = getattr(getattr(ctx, "ref", None), "src", None)
    reviewers = suggest_reviewers(Path(src_dir) if src_dir else None, sorted(files))
    report = {"verdict": verdict, "counts": counts, "findings": uniq,
              "changed_files": sorted(files), "changed_symbols": [g.nodes[s]["qualname"] for s in changed],
              "blast_radius": {k: blast[k] for k in ("risk", "counts", "apis", "tests", "likely_tests", "files")} if blast and "error" not in blast else None,
              "graph": blast["graph"] if blast and "error" not in blast else {"nodes": [], "edges": []},
              "suggested_reviewers": reviewers}
    report["executive_summary"] = summarize_pr_risk(report)
    report["markdown"] = to_markdown(report, emoji=False)
    return report


def to_markdown(r: dict, emoji: bool = True) -> str:
    import sys
    # Use emoji only when explicitly requested or when stdout supports it;
    # .md file output (emoji=False) uses ASCII-safe alternatives so Windows read_text() works.
    _safe = emoji and getattr(sys.stdout, "encoding", "ascii").lower().replace("-", "") in ("utf8", "utf16", "utf32")
    icon = {"pass": "\u2705" if _safe else "[PASS]",
            "warn": "\u26a0\ufe0f" if _safe else "[WARN]",
            "fail": "\u274c" if _safe else "[FAIL]"}[r["verdict"]]
    out = [f"## {icon} CodeWorld PR Guardrail: **{r['verdict'].upper()}**",
           f"{r['counts']['high']} high · {r['counts']['medium']} medium · {r['counts']['low']} low | {len(r['changed_files'])} files, {len(r['changed_symbols'])} symbols changed", ""]
    if r.get("executive_summary"):
        out += [f"> {r['executive_summary']}", ""]
    b = r.get("blast_radius")
    if b:
        out += [f"**Blast radius:** {b['risk']['level']} ({b['risk']['score']}/100): {b['counts']['symbols']} dependent symbols, {b['counts']['files']} files, "
                f"{b['counts']['apis']} endpoints, {b['counts']['tests']} tests reach this change.", ""]
        if b.get("test_command"):
            out += [f"**Run tests command:** `{b['test_command']}`", ""]
        elif b.get("tests"):
            out += ["**Run these tests first:** " + ", ".join(f"`{t['name']}`" for t in b["tests"][:8]), ""]
    arch = r.get("architecture_diff")
    if arch and (arch["summary"]["new_calls"] or arch["summary"]["new_imports"]):
        out += [f"**Architecture diff:** {arch['summary']['new_symbols']} new symbols, {arch['summary']['new_calls']} new calls, {arch['summary']['new_imports']} new imports.", ""]
    revs = r.get("suggested_reviewers")
    if revs:
        out += ["**Suggested reviewers:** " + ", ".join(f"`{x['name']}` ({x['commits']} commits)" for x in revs[:4]), ""]
    for sev in ("high", "medium", "low"):
        items = [f for f in r["findings"] if f["severity"] == sev]
        if items:
            out.append(f"### {sev.title()} severity")
            for f in items[:25]:
                loc = f" (`{f['path']}:{f['line']}`)" if f["path"] and f["line"] else (f" (`{f['path']}`)" if f["path"] else "")
                out.append(f"- **{f['title']}**{loc}: {f['detail']}")
            out.append("")
    if not r["findings"]:
        out.append("No issues found by graph checks.")
    return "\n".join(out)


def review_refs(ctx, base: str, head: str = "HEAD") -> dict:
    return review_diff(ctx, git_diff(ctx.ref.src, base, head))


def review_patch(ctx, diff_text: str) -> dict:
    """Review a pasted diff against the *currently indexed snapshot* (treated as the base).

    The snapshot is copied to a scratch directory, the patch is applied with the
    pure-Python patchapply module (no `git apply` — avoids CRLF issues on Windows),
    the scratch copy is re-indexed incrementally, and the guardrail runs on the
    resulting 'head' graph. The original index is never modified.
    """
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path

    from ..config import get_settings
    from ..indexer import index_repo
    from ..ingest import IGNORE_DIRS, RepoRef
    from ..patchapply import apply_patch_text
    from ..service import RepoContext

    s = get_settings()
    tmp = Path(tempfile.mkdtemp(prefix="cw_review_"))
    try:
        src = tmp / "src"
        shutil.copytree(ctx.ref.src, src, ignore=shutil.ignore_patterns(*IGNORE_DIRS), symlinks=False)

        # Apply patch with pure-Python applier (works on Windows with CRLF diffs)
        ok, err = apply_patch_text(src, diff_text)
        if not ok:
            rep = review_diff(ctx, diff_text)
            rep["note"] = (
                f"Patch could not be applied to the indexed snapshot ({err[:160]}). "
                "Reviewed against the current index instead — make sure the index contains the PR head."
            )
            return rep

        db = tmp / "index.db"
        with sqlite3.connect(str(ctx.ref.db)) as a, sqlite3.connect(str(db)) as b:
            a.backup(b)
        ref = RepoRef(f"review-{tmp.name}", ctx.ref.name, tmp, src, db)
        index_repo(s, ref)
        tctx = RepoContext(s, ref)
        rep = review_diff(tctx, diff_text)
        parsed_diff = parse_unified_diff(diff_text)
        rep["architecture_diff"] = compute_architecture_diff(ctx.graph, tctx.graph, set(parsed_diff.keys()))
        rep["note"] = "applied to a scratch copy of the indexed snapshot and re-indexed; your index was not modified."
        rep["markdown"] = rep["markdown"] + "\n\n_Patch " + rep["note"] + "_"
        return rep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def to_sarif(report: dict, tool_name: str = "CodeWorld", tool_version: str = "1.0.0") -> dict:
    """Export review report findings to SARIF 2.1.0 format for GitHub code scanning."""
    level_map = {
        "high": "error",
        "medium": "warning",
        "low": "note",
    }
    rules = {}
    results = []
    for f in report.get("findings", []):
        cat = f.get("category", "guardrail")
        if cat not in rules:
            rules[cat] = {
                "id": cat,
                "name": cat.replace("-", " ").title(),
                "shortDescription": {"text": f.get("title", cat)},
                "defaultConfiguration": {"level": level_map.get(f.get("severity"), "warning")},
            }
        path = f.get("path") or ""
        line = max(1, int(f.get("line") or 1))
        loc = [{
            "physicalLocation": {
                "artifactLocation": {"uri": path},
                "region": {"startLine": line, "startColumn": 1},
            }
        }] if path else []
        results.append({
            "ruleId": cat,
            "level": level_map.get(f.get("severity"), "warning"),
            "message": {"text": f"{f.get('title')}: {f.get('detail')}"},
            "locations": loc,
        })

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": tool_name,
                    "version": tool_version,
                    "rules": list(rules.values()),
                }
            },
            "results": results,
        }],
    }

