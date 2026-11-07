"""Indexing pipeline: scan -> parse (incremental) -> graph -> chunks -> FTS -> embeddings -> git mining."""
from __future__ import annotations
import json
import time
from collections import Counter
from pathlib import Path

from .ai.chunking import chunk_code_file, chunk_markdown, embed_text, sha1
from .ai.embeddings import get_embedder
from .analysis import gitmining
from .config import Settings
from .db import repo_db, registry_db
from .graph.builder import GraphBuilder
from .ingest import RepoRef, scan_files
from .parser import ParseResult, parse_file
from .textutil import fts_text


def _read(p: Path) -> str:
    return p.read_text("utf8", errors="replace")


def index_repo(settings: Settings, ref: RepoRef, progress=None, force: bool = False, level: int | None = None) -> dict:
    progress = progress or (lambda stage, pct: None)
    t0 = time.time()
    level = settings.resolver_level if level is None else level
    progress("scanning", 2)
    code, docs = scan_files(ref.src, settings)
    ref.root.mkdir(parents=True, exist_ok=True)
    with repo_db(ref.db) as con:
        old = {r["path"]: r["sha"] for r in con.execute("SELECT path, sha FROM files")}
        allf = {f.path: f for f in code + docs}
        changed = [f for f in code + docs if force or old.get(f.path) != f.sha]
        removed = [p for p in old if p not in allf]
        # ---- parse changed code files -----------------------------------------------------------------
        progress("parsing", 8)
        new_chunks: list[dict] = []
        for i, f in enumerate(changed):
            text = _read(f.abs)
            _drop_chunks(con, f.path)
            if f.language == "markdown":
                if f.path.count("/") <= 2:
                    new_chunks += chunk_markdown(f.path, text)
                con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", (f.path, "markdown", f.size, f.sha, text.count("\n") + 1, 0))
                continue
            pr = parse_file(f.path, text.encode("utf8"))
            con.execute("INSERT OR REPLACE INTO parsed VALUES (?,?,?)", (f.path, f.sha, pr.to_json()))
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", (f.path, f.language, f.size, f.sha, pr.loc, int(pr.is_test)))
            new_chunks += chunk_code_file(pr, text)
            if i % 25 == 0:
                progress("parsing", 8 + 30 * i / max(1, len(changed)))
        for p in removed:
            _drop_chunks(con, p)
            con.execute("DELETE FROM files WHERE path=?", (p,))
            con.execute("DELETE FROM parsed WHERE path=?", (p,))
        # ---- graph ---------------------------------------------------------------------------------------
        progress("building graph", 42)
        parsed = {r["path"]: ParseResult.from_json(r["json"]) for r in con.execute("SELECT path, json FROM parsed")}
        tr = con.execute("SELECT value FROM meta WHERE key='trace_edges'").fetchone()
        gb = GraphBuilder(parsed, level=level, root=ref.src, repo_name=ref.name, trace_edges=json.loads(tr["value"]) if tr else None)
        nodes, edges, rstats = gb.build()
        con.execute("DELETE FROM nodes"); con.execute("DELETE FROM edges")
        con.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?)", [
            (n["id"], n["kind"], n["name"], n["qualname"], n["path"], n["start"], n["end"], n["parent"], json.dumps(n["props"], default=str))
            for n in nodes])
        con.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?)", [(s, d, t, c, r, l) for (s, d, t), (c, r, l) in edges.items()])
        # ---- chunks + FTS ----------------------------------------------------------------------------------
        progress("indexing text", 55)
        for c in new_chunks:
            h = sha1(embed_text(c))
            cur = con.execute("INSERT OR REPLACE INTO chunks(id,node_id,path,kind,name,start,end,text,text_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                              (c["id"], c["node_id"], c["path"], c["kind"], c["name"], c["start"], c["end"], c["text"], h))
            con.execute("DELETE FROM chunks_fts WHERE rowid=?", (cur.lastrowid,))      # heal any stale row left by a replaced chunk
            con.execute("INSERT INTO chunks_fts(rowid,name,path,body,chunk_id) VALUES (?,?,?,?,?)",
                        (cur.lastrowid, fts_text(c["name"]), fts_text(c["path"]), fts_text(c["text"][:8000]), c["id"]))
        # ---- embeddings ------------------------------------------------------------------------------------
        progress("embedding", 62)
        emb = get_embedder(settings)
        rows = con.execute("SELECT c.text_hash, c.kind, c.name, c.path, c.text FROM chunks c LEFT JOIN embeddings e "
                           "ON e.text_hash=c.text_hash AND e.model=? WHERE e.text_hash IS NULL GROUP BY c.text_hash", (emb.name,)).fetchall()
        batch = 64
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            vecs = emb.embed_docs([embed_text(dict(r)) for r in part])
            con.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)",
                            [(r["text_hash"], emb.name, int(v.shape[0]), v.astype("float32").tobytes()) for r, v in zip(part, vecs)])
            progress("embedding", 62 + 25 * min(1.0, (i + batch) / max(1, len(rows))))
        # ---- git history ------------------------------------------------------------------------------------
        has_git = gitmining.git_available(ref.src)
        if has_git:
            progress("mining git history", 90)
            try:
                gitmining.store(con, gitmining.mine(ref.src, settings.git_commit_limit))
            except Exception:
                has_git = False
        # ---- stats ---------------------------------------------------------------------------------------------
        kinds = Counter(n["kind"] for n in nodes)
        etypes = Counter(t for (_, _, t) in edges)
        langs = Counter(r["language"] for r in con.execute("SELECT language FROM files WHERE language!='markdown'"))
        loc = con.execute("SELECT COALESCE(SUM(loc),0) FROM files WHERE language!='markdown'").fetchone()[0]
        stats = {"files": kinds["file"], "loc": loc, "classes": kinds["class"], "functions": kinds["function"], "methods": kinds["method"],
                 "tests": kinds["test"], "apis": kinds["api"], "externals": kinds["external"], "edges": dict(etypes), "languages": dict(langs),
                 "resolver_level": level, "resolution": rstats, "git": has_git, "seconds": round(time.time() - t0, 2),
                 "changed_files": len(changed), "removed_files": len(removed), "embed_model": emb.name,
                 "parse_errors": sum(1 for p in parsed.values() if p.error)}
        version = str(time.time())
        con.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)", (version,))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('stats', ?)", (json.dumps(stats),))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('resolver_level', ?)", (str(level),))
    with registry_db(settings.data_dir) as rc:
        rc.execute("UPDATE repos SET indexed=?, status='ready', stats=? WHERE id=?", (time.time(), json.dumps(stats), ref.id))
    progress("done", 100)
    return stats


def _drop_chunks(con, path: str) -> None:
    rows = con.execute("SELECT rowid FROM chunks WHERE path=?", (path,)).fetchall()
    if rows:
        ids = [r[0] for r in rows]
        con.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
        con.execute("DELETE FROM chunks WHERE path=?", (path,))
