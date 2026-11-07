"""GitHub helpers that work from a laptop with a personal access token - no server, no GitHub App needed.

  codeworld github review-pr owner/repo 123 [--post]   review a pull request with the graph guardrail
  codeworld github issue    owner/repo 45  [--post]    suggest which files/functions an issue probably concerns
  codeworld github comment  owner/repo 123 --file report.md
Token: export GITHUB_TOKEN=<fine-grained PAT>  (read access suffices; 'Pull requests: write' / 'Issues: write' to post comments)
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

API = "https://api.github.com"


def _h(accept="application/vnd.github+json"):
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "codeworld"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    return h


def _get(path, accept="application/vnd.github+json"):
    r = httpx.get(API + path, headers=_h(accept), timeout=60, follow_redirects=True)
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("GitHub API rate limit hit - set GITHUB_TOKEN to raise it.")
    r.raise_for_status()
    return r


def post_comment(slug: str, number: int, body: str) -> str:
    if not os.environ.get("GITHUB_TOKEN"):
        raise SystemExit("GITHUB_TOKEN is required to post comments")
    r = httpx.post(f"{API}/repos/{slug}/issues/{number}/comments", headers=_h(), json={"body": body[:60000]}, timeout=60)
    r.raise_for_status()
    return r.json()["html_url"]


def _checkout_sha(settings, slug: str, sha: str, name: str):
    """Index the repository at an exact commit (shallow fetch by SHA). Reuses an existing index of the same commit."""
    from .db import registry_db
    from .indexer import index_repo
    from .ingest import register_repo, repo_ref, IngestError, check_size
    url = f"{os.environ.get('CODEWORLD_GITHUB_BASE', 'https://github.com')}/{slug}"      # override only for tests
    source = f"{url}@{sha}"
    with registry_db(settings.data_dir) as c:
        row = c.execute("SELECT id FROM repos WHERE source=?", (source,)).fetchone()
    if row:
        ref = repo_ref(settings, row["id"])
    else:
        ref = register_repo(settings, name, source, "git")
        dest = ref.src
        dest.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        run = lambda *a: subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(dest), *a], check=True, capture_output=True, env=env, timeout=600)
        try:
            run("init", "-q"); run("remote", "add", "origin", url)
            run("fetch", "--depth", "1", "origin", sha); run("checkout", "-q", "FETCH_HEAD")
        except subprocess.CalledProcessError as e:
            raise IngestError("git fetch failed: " + e.stderr.decode("utf8", "replace")[-200:])
        check_size(dest, settings)
    index_repo(settings, ref)
    from .service import get_context
    return get_context(ref.id, settings)


def review_pr(settings, slug: str, number: int, post: bool) -> int:
    from .analysis import diffreview
    pr = _get(f"/repos/{slug}/pulls/{number}").json()
    diff = _get(f"/repos/{slug}/pulls/{number}", "application/vnd.github.v3.diff").text
    base_sha = pr["base"]["sha"]
    print(f"PR #{number}: {pr['title']}  (base {base_sha[:8]} -> head {pr['head']['sha'][:8]}), indexing base snapshot ...", file=sys.stderr)
    ctx = _checkout_sha(settings, slug, base_sha, slug.split("/")[1])
    rep = diffreview.review_patch(ctx, diff)
    print(rep["markdown"])
    if post:
        print("posted:", post_comment(slug, number, rep["markdown"]), file=sys.stderr)
    return 1 if rep["counts"]["high"] else 0


def issue_suspects(settings, slug: str, number: int, post: bool) -> int:
    from .agent.answer import ask
    repo = _get(f"/repos/{slug}").json()
    issue = _get(f"/repos/{slug}/issues/{number}").json()
    branch = repo["default_branch"]
    sha = _get(f"/repos/{slug}/commits/{branch}").json()["sha"]
    ctx = _checkout_sha(settings, slug, sha, slug.split("/")[1])
    text = f"{issue['title']}\n{(issue.get('body') or '')[:1500]}"
    hits = ctx.retriever.search(text, "graph", 8, "locate")
    g = ctx.graph
    lines = [f"### CodeWorld: where this issue probably lives (indexed `{sha[:8]}`)", ""]
    for h in hits[:6]:
        if h.kind in ("function", "method", "class"):
            n = g.node(h.node_id)
            callers = len(g.callers(h.node_id, 1, 0.5)) if n else 0
            tests = [t["name"] for t in __import__("codeworld.analysis.impact", fromlist=["x"]).tests_for(ctx, h.node_id).get("tests", [])[:2]]
            lines.append(f"- `{h.name}` ({h.path}:{h.start}) - {callers} caller(s)" + (f"; tests: {', '.join(tests)}" if tests else "; no tests reach it"))
        elif h.kind == "doc":
            lines.append(f"- docs: `{h.path}` section \"{h.name}\"")
    lines += ["", "_Automated suggestion from static retrieval over the code graph; may be wrong._"]
    out = "\n".join(lines)
    print(out)
    if post:
        print("posted:", post_comment(slug, number, out), file=sys.stderr)
    return 0


def main(settings, a) -> int:
    if a.action == "review-pr":
        return review_pr(settings, a.repo_slug, a.number, a.post)
    if a.action == "issue":
        return issue_suspects(settings, a.repo_slug, a.number, a.post)
    if a.action == "comment":
        if not a.file:
            raise SystemExit("--file required")
        print(post_comment(a.repo_slug, a.number, Path(a.file).read_text()))
        return 0
    return 2
