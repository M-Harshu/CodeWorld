"""CodeWorld command line interface."""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

from .config import get_settings


def _repo(settings, arg: str, index: bool = True, level: int | None = None):
    from .db import registry_db
    from .indexer import index_repo
    from .ingest import register_repo, repo_ref
    from .service import get_context
    p = Path(arg).expanduser()
    if p.is_dir():
        p = p.resolve()
        with registry_db(settings.data_dir) as c:
            row = c.execute("SELECT id FROM repos WHERE source=? AND kind='local'", (str(p),)).fetchone()
        ref = repo_ref(settings, row["id"]) if row else register_repo(settings, p.name, str(p), "local")
        if index:
            stats = index_repo(settings, ref, lambda st, pc: sys.stderr.write(f"\r[{pc:5.1f}%] {st:<22}") or None, level=level)
            sys.stderr.write(f"\r[100%] indexed {stats['files']} files, {stats['loc']:,} LOC, {stats['edges'].get('CALLS', 0)} call edges in {stats['seconds']}s\n")
        return get_context(ref.id, settings)
    ctx = get_context(arg, settings)
    if ctx is None:
        sys.exit(f"repository '{arg}' not found (give a directory path or a repo id from `codeworld repos`)")
    return ctx


def main(argv=None):
    ap = argparse.ArgumentParser(prog="codeworld", description="Graph-RAG codebase understanding")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("repos", help="list indexed repositories")
    s = sub.add_parser("serve", help="start the API server"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000)
    s = sub.add_parser("index", help="index a local directory"); s.add_argument("path"); s.add_argument("--level", type=int, choices=[0, 1, 2, 3])
    s = sub.add_parser("ask", help="ask a question"); s.add_argument("repo"); s.add_argument("question"); s.add_argument("--mode", default="auto"); s.add_argument("--no-agent", action="store_true")
    s = sub.add_parser("impact", help="change-impact analysis"); s.add_argument("repo"); s.add_argument("target")
    s = sub.add_parser("review", help="AI-PR guardrail on a git range or a diff file")
    s.add_argument("repo"); s.add_argument("--base", default="origin/main"); s.add_argument("--head", default="HEAD"); s.add_argument("--diff-file")
    s.add_argument("--fail-on", choices=["high", "medium", "never"], default="high"); s.add_argument("--json", action="store_true"); s.add_argument("--sarif", action="store_true"); s.add_argument("--output")
    s = sub.add_parser("onboard", help="write the onboarding guide"); s.add_argument("repo"); s.add_argument("--output", default="ONBOARDING.md"); s.add_argument("--narrate", action="store_true")
    s = sub.add_parser("health", help="health report"); s.add_argument("repo")
    s = sub.add_parser("security", help="attack-surface report"); s.add_argument("repo")
    s = sub.add_parser("mcp", help="run the MCP server (stdio)"); s.add_argument("--repo", required=True)
    s = sub.add_parser("doctor", help="check environment, LLM and embedding provider"); s.add_argument("--list-models", action="store_true")
    s = sub.add_parser("trace", help="run a repo's tests under a tracer and merge observed calls into the graph (EXECUTES the repo's code)")
    s.add_argument("repo"); s.add_argument("--run", default="pytest -q"); s.add_argument("--yes", action="store_true", help="confirm you trust the code")
    s = sub.add_parser("watch", help="watch repo and incrementally re-index on file changes")
    s.add_argument("repo"); s.add_argument("--interval", type=float, default=2.0)
    s = sub.add_parser("fix", help="propose a patch for a task (needs an LLM); validated by the guardrail, never auto-applied")
    s.add_argument("repo"); s.add_argument("task"); s.add_argument("--out", help="write the diff here")
    s = sub.add_parser("github", help="GitHub helpers: review-pr, issue, comment")
    s.add_argument("action", choices=["review-pr", "issue", "comment"]); s.add_argument("repo_slug", help="owner/name"); s.add_argument("number", type=int)
    s.add_argument("--post", action="store_true", help="post the result as a comment (needs GITHUB_TOKEN)"); s.add_argument("--file", help="markdown file for `comment`")
    s = sub.add_parser("eval", help="research evaluation harness")
    s.add_argument("what", choices=["qa", "impact", "callgraph", "resolver-ablation", "autoqa", "guardrail"]); s.add_argument("repo")
    s.add_argument("--dataset"); s.add_argument("--modes", default="bm25,vector,hybrid,graph,agentic"); s.add_argument("--k", type=int, default=5)
    s.add_argument("--truth"); s.add_argument("--commits", type=int, default=300); s.add_argument("--out")
    a = ap.parse_args(argv)
    # Windows: stdout may default to a legacy codec (cp1252/charmap) that cannot encode emoji.
    # Reconfigure stdout/stderr to UTF-8 so markdown output (✅ ⚠️ ❌ etc.) prints correctly.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    st = get_settings()

    if a.cmd == "doctor":
        from . import doctor
        sys.exit(doctor.run(a))
    elif a.cmd == "trace":
        from .evaluation.dynamic_trace import trace
        if not a.yes:
            sys.exit("`trace` EXECUTES the repository's code (its tests). Re-run with --yes only for code you trust, ideally inside a container/VM.")
        ctx = _repo(st, a.repo)
        edges = trace(ctx.ref.src, a.run)
        from .indexer import index_repo
        with ctx.con() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES ('trace_edges', ?)", (json.dumps(edges),))
        index_repo(st, ctx.ref, force=True)
        print(f"merged {len(edges)} runtime-observed call edges; re-indexed")
    elif a.cmd == "fix":
        from .agent.patch import propose
        r = propose(_repo(st, a.repo), a.task)
        print(r["report"])
        if a.out and r.get("diff"):
            Path(a.out).write_text(r["diff"]); print(f"diff written to {a.out}")
    elif a.cmd == "github":
        from . import github as gh
        sys.exit(gh.main(st, a))
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("codeworld.api.main:app", host=a.host, port=a.port, reload=False)
    elif a.cmd == "repos":
        from .db import registry_db
        with registry_db(st.data_dir) as c:
            for r in c.execute("SELECT id,name,status,kind FROM repos ORDER BY created DESC"):
                print(f"{r['id']:<30} {r['status']:<8} {r['kind']:<6} {r['name']}")
    elif a.cmd == "index":
        _repo(st, a.path, level=a.level)
    elif a.cmd == "ask":
        from .agent.answer import ask
        r = ask(_repo(st, a.repo), a.question, a.mode, not a.no_agent)
        print(r["answer"]); print(f"\n[{r['provider']} | intent={r['intent']} | {r['latency_ms']} ms | citation precision={r['citation_precision']}]")
    elif a.cmd == "impact":
        from .analysis.impact import impact
        r = impact(_repo(st, a.repo), a.target)
        print(json.dumps({k: r[k] for k in r if k != "graph"}, indent=2, default=str))
        if r.get("test_command"):
            print(f"\n[Test Selection] Recommended test command:\n  {r['test_command']}\n")
    elif a.cmd == "review":
        from .analysis import diffreview
        ctx = _repo(st, a.repo)
        diff = Path(a.diff_file).read_text() if a.diff_file else diffreview.git_diff(ctx.ref.src, a.base, a.head)
        rep = diffreview.review_diff(ctx, diff) if not a.diff_file else diffreview.review_patch(ctx, diff)
        if a.sarif:
            out = json.dumps(diffreview.to_sarif(rep), indent=2)
        elif a.json:
            out = json.dumps({k: v for k, v in rep.items() if k != "graph"}, indent=2)
        else:
            out = diffreview.to_markdown(rep, emoji=True)
        # Write to stdout safely; errors="replace" handles consoles that don't support emoji
        try:
            print(out)
        except UnicodeEncodeError:
            print(out.encode("ascii", errors="replace").decode("ascii"))
        if a.output:
            out_content = out if (a.sarif or a.json) else rep["markdown"]
            Path(a.output).write_text(out_content, encoding="utf-8")
        if a.fail_on != "never" and (rep["counts"]["high"] or (a.fail_on == "medium" and rep["counts"]["medium"])):
            sys.exit(1)
    elif a.cmd == "onboard":
        from .analysis import onboarding
        Path(a.output).write_text(onboarding.markdown(_repo(st, a.repo), a.narrate)); print(f"wrote {a.output}")
    elif a.cmd == "health":
        from .analysis import health
        h = health.health_report(_repo(st, a.repo))
        print(f"health score {h['score']}/100 | cycles {len(h['cycles'])} | layering violations {len(h['layering'])} | dead code {len(h['dead_code'])} | "
              f"duplicates {len(h['duplicates'])} | graph test coverage {h['untested']['coverage']:.0%}")
        for d in h["dead_code"][:10]: print(f"  dead?  {d['name']} ({d['path']}:{d['line']}) [{d['confidence']}]")
        for d in h["duplicates"][:5]: print(f"  dup    {d['a']['name']} ~ {d['b']['name']} ({d['similarity']})")
        for v in h["layering"][:5]: print(f"  layer  {v['from']} -> {v['to']}: {v['rule']}")
    elif a.cmd == "security":
        from .analysis import security
        r = security.security_report(_repo(st, a.repo)); print(json.dumps(r["summary"])); print(r["disclaimer"])
        for f in r["findings"][:20]: print(f"  [{f['severity']}] {f['message']} - {f['symbol']} ({f['path']}:{f['line']})")
    elif a.cmd == "mcp":
        from .mcp_server import serve
        serve(a.repo)
    elif a.cmd == "watch":
        ctx = _repo(st, a.repo)
        src_path = Path(ctx.ref.src)
        print(f"[*] Watching {src_path} for changes... Press Ctrl+C to stop.")
        mtimes: dict[str, float] = {}
        for p in src_path.rglob("*"):
            if p.is_file() and not any(part in p.parts for part in (".git", ".venv", "node_modules", "__pycache__")):
                try:
                    mtimes[str(p)] = p.stat().st_mtime
                except Exception:
                    pass
        try:
            while True:
                time.sleep(a.interval)
                changed = False
                for p in src_path.rglob("*"):
                    if p.is_file() and not any(part in p.parts for part in (".git", ".venv", "node_modules", "__pycache__")):
                        try:
                            mt = p.stat().st_mtime
                            if str(p) not in mtimes or mtimes[str(p)] != mt:
                                mtimes[str(p)] = mt
                                changed = True
                        except Exception:
                            pass
                if changed:
                    print(f"[{time.strftime('%H:%M:%S')}] Detected file modification, re-indexing...")
                    from .indexer import index_repo
                    index_repo(st, ctx.ref)
                    print(f"[{time.strftime('%H:%M:%S')}] Incremental index up to date.")
        except KeyboardInterrupt:
            print("\n[*] Stopped watcher.")
    elif a.cmd == "eval":
        from .evaluation import runner
        runner.run(st, a)


if __name__ == "__main__":
    main()
