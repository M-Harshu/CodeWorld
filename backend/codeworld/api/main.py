"""FastAPI application."""
from __future__ import annotations
import json
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, field_validator

from .. import __version__
from ..agent.answer import ask as ask_question
from ..agent.tools import ToolError, ToolRegistry
from ..ai.embeddings import get_embedder
from ..analysis import city, health, onboarding, security
from ..analysis import diffreview, impact as impact_mod
from ..config import get_settings
from ..db import registry_db
from ..graph.view import build_view
from ..indexer import index_repo
from ..ingest import IngestError, clone_repo, register_repo, repo_ref, safe_extract_zip, validate_git_url
from ..service import forget, get_context

app = FastAPI(title="CodeWorld API", version=__version__)
_settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=_settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def token_auth(request, call_next):
    """Optional shared-secret auth. Set CODEWORLD_API_TOKEN to require `Authorization: Bearer <token>` (or X-API-Key) on /api/*."""
    import hmac
    import os
    from fastapi.responses import JSONResponse
    token = os.environ.get("CODEWORLD_API_TOKEN", "")
    p = request.url.path
    if token and p.startswith("/api/") and p != "/api/health" and request.method != "OPTIONS":
        given = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), token.encode()):
            return JSONResponse({"detail": "unauthorized: missing or wrong API token"}, status_code=401)
    return await call_next(request)


# ----------------------------------------------------------------------------- helpers
def _ctx(repo_id: str):
    ctx = get_context(repo_id)
    if ctx is None:
        raise HTTPException(404, "Repository not found or not indexed yet")
    return ctx


def _set_job(job_id: str, **kw):
    s = get_settings()
    with registry_db(s.data_dir) as c:
        for k, v in kw.items():
            c.execute(f"UPDATE jobs SET {k}=? WHERE id=?", (v, job_id))


def _new_job(repo_id: str) -> str:
    jid = uuid.uuid4().hex[:10]
    s = get_settings()
    with registry_db(s.data_dir) as c:
        c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (jid, repo_id, "queued", 0, "running", "", time.time()))
    return jid


def _run_job(job_id: str, ref, work):
    s = get_settings()

    def progress(stage, pct):
        _set_job(job_id, stage=stage, pct=round(pct, 1))
    try:
        work(progress)
        index_repo(s, ref, progress)
        forget(ref.id)
        _set_job(job_id, status="done", stage="done", pct=100)
    except Exception as e:                      # surfaced to the UI
        with registry_db(s.data_dir) as c:
            c.execute("UPDATE repos SET status='error' WHERE id=?", (ref.id,))
        _set_job(job_id, status="error", error=f"{type(e).__name__}: {e}")


def _start(ref, work) -> dict:
    jid = _new_job(ref.id)
    threading.Thread(target=_run_job, args=(jid, ref, work), daemon=True).start()
    return {"repo_id": ref.id, "job_id": jid}


# ----------------------------------------------------------------------------- meta
@app.get("/api/health")
def api_health():
    s = get_settings()
    llm_ok = s.llm_provider != "none"
    import os
    from ..ai.llm import get_llm, get_available_models
    llm = get_llm(s)
    return {"status": "ok", "version": __version__, "auth": bool(os.environ.get("CODEWORLD_API_TOKEN")),
            "llm": {"provider": s.llm_provider if llm.available else "none", "model": s.llm_model if llm.available else None, "chain": llm.name, "usage": llm.usage()},
            "embeddings": {"backend": s.embed_backend, "model": get_embedder(s).name}, "resolver_level": s.resolver_level,
            "models": get_available_models(s)}


@app.get("/api/repos")
def list_repos():
    s = get_settings()
    with registry_db(s.data_dir) as c:
        rows = c.execute("SELECT * FROM repos ORDER BY created DESC").fetchall()
    return [{"id": r["id"], "name": r["name"], "source": r["source"] if r["kind"] != "local" else r["name"], "kind": r["kind"],
             "status": r["status"], "indexed": r["indexed"], "stats": json.loads(r["stats"] or "{}")} for r in rows]


class CloneBody(BaseModel):
    url: str
    branch: str | None = None


@app.post("/api/repos")
def add_repo(body: CloneBody):
    s = get_settings()
    try:
        url = validate_git_url(body.url, s)
    except IngestError as e:
        raise HTTPException(400, str(e))
    name = url.rsplit("/", 1)[-1].removesuffix(".git")
    ref = register_repo(s, name, url, "git")
    return _start(ref, lambda progress: (progress("cloning", 1), clone_repo(url, ref.src, s, body.branch)))


@app.post("/api/repos/upload")
async def upload_repo(file: UploadFile = File(...)):
    s = get_settings()
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Please upload a .zip archive")
    name = Path(file.filename).stem
    ref = register_repo(s, name, f"upload:{file.filename}", "zip")
    tmp = Path(tempfile.mkdtemp()) / "up.zip"
    with open(tmp, "wb") as out:
        shutil.copyfileobj(file.file, out)

    def work(progress):
        progress("extracting", 1)
        try:
            safe_extract_zip(tmp, ref.src, s)
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)
    return _start(ref, work)


class LocalBody(BaseModel):
    path: str


@app.post("/api/repos/local")
def add_local(body: LocalBody):
    """Index a directory that already exists on the machine running the API (disabled unless CODEWORLD_ALLOW_LOCAL=1)."""
    import os
    if os.environ.get("CODEWORLD_ALLOW_LOCAL") != "1":
        raise HTTPException(403, "Local path indexing is disabled. Set CODEWORLD_ALLOW_LOCAL=1 to enable it.")
    p = Path(body.path).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(400, "Not a directory")
    ref = register_repo(get_settings(), p.name, str(p), "local")
    return _start(ref, lambda progress: None)


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    s = get_settings()
    with registry_db(s.data_dir) as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r:
        raise HTTPException(404, "job not found")
    return dict(r)


@app.post("/api/repos/{repo_id}/reindex")
def reindex(repo_id: str, force: bool = False):
    s = get_settings()
    ref = repo_ref(s, repo_id)
    if not ref:
        raise HTTPException(404, "not found")
    jid = _new_job(ref.id)

    def run():
        try:
            index_repo(s, ref, lambda st, p: _set_job(jid, stage=st, pct=round(p, 1)), force=force)
            forget(ref.id)
            _set_job(jid, status="done", stage="done", pct=100)
        except Exception as e:
            _set_job(jid, status="error", error=str(e))
    threading.Thread(target=run, daemon=True).start()
    return {"repo_id": ref.id, "job_id": jid}


@app.delete("/api/repos/{repo_id}")
def delete_repo(repo_id: str):
    s = get_settings()
    ref = repo_ref(s, repo_id)
    if not ref:
        raise HTTPException(404, "not found")
    forget(repo_id)
    with registry_db(s.data_dir) as c:
        c.execute("DELETE FROM repos WHERE id=?", (repo_id,))
    if ref.root.exists():
        shutil.rmtree(ref.root, ignore_errors=True)
    return {"deleted": repo_id}


# ----------------------------------------------------------------------------- repository views
@app.get("/api/repos/{repo_id}/stats")
def stats(repo_id: str):
    return _ctx(repo_id).stats()


@app.get("/api/repos/{repo_id}/tree")
def tree(repo_id: str):
    ctx = _ctx(repo_id)
    with ctx.con() as c:
        rows = c.execute("SELECT path, language, loc, is_test FROM files ORDER BY path").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/repos/{repo_id}/file")
def file_view(repo_id: str, path: str):
    ctx = _ctx(repo_id)
    try:
        lines = ctx.read_lines(path)
    except Exception:
        raise HTTPException(404, "file not found")
    g = ctx.graph
    syms = [{"id": i, "name": g.nodes[i]["qualname"], "kind": g.nodes[i]["kind"], "start": g.nodes[i]["start"], "end": g.nodes[i]["end"]}
            for i in g.by_path.get(path, [])]
    fnode = g.node(f"file:{path}")
    return {"path": path, "language": fnode["props"].get("language") if fnode else "text", "lines": lines[:5000], "symbols": syms,
            "todos": fnode["props"].get("todos", []) if fnode else []}


@app.get("/api/repos/{repo_id}/graph")
def graph_view(repo_id: str, expanded: str = "", types: str = "CALLS,IMPORTS,INHERITS", highlight: str = "", min_conf: float = 0.5,
               tests: bool = False, external: bool = False, focus: str | None = None):
    ctx = _ctx(repo_id)
    return build_view(ctx.graph, [e for e in expanded.split("|") if e], tuple(t for t in types.split(",") if t),
                      [h for h in highlight.split("|") if h], min_conf, tests, external, focus=focus)


@app.get("/api/repos/{repo_id}/node")
def node_detail(repo_id: str, id: str):
    ctx = _ctx(repo_id)
    try:
        return ToolRegistry(ctx).call("get_symbol", {"symbol": id}) if ctx.graph.kind(id) not in ("file", "module", "api") else _container_detail(ctx, id)
    except ToolError as e:
        raise HTTPException(404, str(e))


def _container_detail(ctx, nid):
    g = ctx.graph
    n = g.nodes[nid]
    d = {"id": nid, "name": n["qualname"], "kind": n["kind"], "path": n["path"], "line": n["start"], "end": n["end"], "props": n["props"] if n["kind"] != "file" else
         {k: v for k, v in n["props"].items() if k not in ("scope",)}}
    if n["kind"] == "file":
        d["imports"] = [g.nodes[x]["path"] or g.nodes[x]["name"] for x, *_ in g.out[nid].get("IMPORTS", []) if x in g.nodes]
        d["imported_by"] = [x[5:] for x, *_ in g.inn[nid].get("IMPORTS", [])]
        d["symbols"] = [{"id": i, "name": g.nodes[i]["qualname"], "kind": g.nodes[i]["kind"], "line": g.nodes[i]["start"]} for i in g.by_path.get(n["path"], [])][:80]
    if n["kind"] == "api":
        d["handler"] = n["props"].get("handler")
    return d


@app.get("/api/repos/{repo_id}/lens")
def lens(repo_id: str, path: str):
    """Per-symbol facts for editor CodeLens: callers, tests, effects, public-API flag."""
    from ..analysis import health
    ctx = _ctx(repo_id)
    g = ctx.graph
    covered = ctx.cached("tested_set", lambda: health.tested_set(g))
    out = []
    for sid in g.by_path.get(path, []):
        n = g.nodes[sid]
        if n["kind"] not in ("function", "method", "class") or n["props"].get("is_test"):
            continue
        callers = g.callers(sid, 1, 0.5)
        trans = g.callers(sid, 4, 0.5)
        out.append({"id": sid, "name": n["qualname"], "kind": n["kind"], "line": n["start"], "end": n["end"], "callers": len(callers),
                    "transitive": len(trans), "tested": sid in covered, "effects": n["props"].get("effects", []),
                    "public_api": bool(n["props"].get("public_api")), "route": n["props"].get("route"), "complexity": n["props"].get("complexity", 1)})
    return out


class ProposeBody(BaseModel):
    task: str


@app.post("/api/repos/{repo_id}/propose")
def propose_patch(repo_id: str, body: ProposeBody):
    from ..agent.patch import propose
    r = propose(_ctx(repo_id), body.task)
    r.pop("raw", None)
    return r


@app.get("/api/repos/{repo_id}/ancestors")
def ancestors(repo_id: str, id: str):
    g = _ctx(repo_id).graph
    if id not in g.nodes:
        raise HTTPException(404, "unknown node")
    return [a for a in g.ancestors(id) if a != "repo:root"][::-1]


@app.get("/api/repos/{repo_id}/search")
def search(repo_id: str, q: str, mode: str = "auto", k: int = 10):
    ctx = _ctx(repo_id)
    from ..ai.intent import classify
    return {"hits": [h.to_dict(300) for h in ctx.retriever.search(q, mode, k, classify(q))]}


@app.get("/api/repos/{repo_id}/symbols")
def symbols(repo_id: str, q: str = "", limit: int = 20):
    g = _ctx(repo_id).graph
    ids = g.find(q, limit=limit) if q else []
    return [{"id": i, "name": g.nodes[i]["qualname"], "kind": g.nodes[i]["kind"], "path": g.nodes[i]["path"], "line": g.nodes[i]["start"]} for i in ids]


class AskBody(BaseModel):
    question: str
    mode: str = "auto"
    agent: bool = True
    history: list[dict] = []
    k: int = 8
    provider: str | None = None
    model: str | None = None
    image: str | None = None

    @field_validator("image")
    @classmethod
    def _cap_image(cls, v: str | None) -> str | None:
        """Reject images larger than ~2.8 MB (base64 of 2 MB binary ≈ 2.7 MB).
        This prevents localStorage quota exhaustion and excessively large payloads."""
        if v is not None and len(v) > 2_800_000:
            raise ValueError("image field exceeds 2 MB limit; resize or crop the image first")
        return v


@app.post("/api/repos/{repo_id}/ask")
def ask(repo_id: str, body: AskBody):
    return ask_question(
        _ctx(repo_id), body.question, body.mode, body.agent, body.history, body.k,
        provider=body.provider, model=body.model, image=body.image
    )


@app.get("/api/repos/{repo_id}/impact")
def impact(repo_id: str, target: str, depth: int = 5, min_conf: float = 0.5):
    r = impact_mod.impact(_ctx(repo_id), target, depth=depth, min_conf=min_conf)
    if "error" in r:
        raise HTTPException(404, r["error"])
    return r


class ReviewBody(BaseModel):
    diff: str | None = None
    base: str | None = None
    head: str = "HEAD"
    applied: bool = False     # True when the indexed tree already contains the change (e.g. CI on the PR head)


@app.post("/api/repos/{repo_id}/review")
def review(repo_id: str, body: ReviewBody):
    ctx = _ctx(repo_id)
    try:
        text = body.diff if body.diff else diffreview.git_diff(ctx.ref.src, body.base or "HEAD~1", body.head)
        if not body.diff:
            body.applied = True
    except Exception as e:
        raise HTTPException(400, f"could not obtain diff: {e}")
    if not text.strip():
        raise HTTPException(400, "empty diff")
    return diffreview.review_diff(ctx, text) if body.applied else diffreview.review_patch(ctx, text)


@app.get("/api/repos/{repo_id}/health")
def health_endpoint(repo_id: str):
    return health.health_report(_ctx(repo_id))


@app.get("/api/repos/{repo_id}/security")
def security_endpoint(repo_id: str):
    return security.security_report(_ctx(repo_id))


@app.get("/api/repos/{repo_id}/onboarding")
def onboarding_endpoint(repo_id: str):
    return onboarding.overview(_ctx(repo_id))


@app.get("/api/repos/{repo_id}/onboarding.md", response_class=PlainTextResponse)
def onboarding_md(repo_id: str, narrate: bool = False):
    return onboarding.markdown(_ctx(repo_id), narrate)


@app.get("/api/repos/{repo_id}/city")
def city_endpoint(repo_id: str):
    return _ctx(repo_id).cached("city", lambda: city.layout(_ctx(repo_id)))


@app.get("/api/repos/{repo_id}/cochange")
def cochange(repo_id: str, path: str):
    from ..analysis import gitmining
    with _ctx(repo_id).con() as c:
        return gitmining.cochange_for(c, path)


@app.get("/api/repos/{repo_id}/tools")
def tools_list(repo_id: str):
    return ToolRegistry(_ctx(repo_id)).describe()


class ToolBody(BaseModel):
    args: dict = {}


@app.post("/api/repos/{repo_id}/tools/{name}")
def tool_call(repo_id: str, name: str, body: ToolBody):
    try:
        return ToolRegistry(_ctx(repo_id)).call(name, body.args)
    except ToolError as e:
        raise HTTPException(400, str(e))


# ----------------------------------------------------------------------------- serve the built UI (single-process deployment)
def _mount_ui():
    import os
    from fastapi.staticfiles import StaticFiles
    cands = [os.environ.get("CODEWORLD_UI_DIR", ""), str(Path(__file__).resolve().parents[3] / "frontend" / "dist"), "/app/ui"]
    for c in cands:
        if c and (Path(c) / "index.html").exists():
            app.mount("/", StaticFiles(directory=c, html=True), name="ui")
            return


_mount_ui()
