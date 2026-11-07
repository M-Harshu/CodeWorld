"""RepoContext: lazily-loaded, version-aware handle on one indexed repository."""
from __future__ import annotations
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from .ai.embeddings import get_embedder
from .ai.llm import get_llm
from .ai.retrieval import Retriever
from .ai.vectorindex import VectorIndex
from .config import Settings, get_settings
from .db import connect, repo_db
from .graph.store import RepoGraph, load_graph
from .ingest import RepoRef, repo_ref

_ctx_cache: dict[str, "RepoContext"] = {}
_lock = threading.Lock()


class RepoContext:
    def __init__(self, settings: Settings, ref: RepoRef):
        self.settings, self.ref = settings, ref
        self._graph: RepoGraph | None = None
        self._vindex: VectorIndex | None = None
        self._version: str | None = None
        self._cache: dict[str, object] = {}
        self.retriever = Retriever(self)

    @contextmanager
    def con(self):
        with repo_db(self.ref.db) as c:
            yield c

    def version(self) -> str:
        with self.con() as c:
            r = c.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        return r["value"] if r else ""

    def _refresh(self):
        v = self.version()
        if v != self._version:
            self._graph, self._vindex, self._cache, self._version = None, None, {}, v

    @property
    def graph(self) -> RepoGraph:
        self._refresh()
        if self._graph is None:
            with self.con() as c:
                self._graph = load_graph(c)
        return self._graph

    @property
    def embedder(self):
        return get_embedder(self.settings)

    @property
    def llm(self):
        return get_llm(self.settings)

    def vindex(self) -> VectorIndex | None:
        self._refresh()
        if self._vindex is None:
            with self.con() as c:
                self._vindex = VectorIndex.load(c, self.embedder.name)
        return self._vindex

    def cached(self, key: str, fn):
        self._refresh()
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def stats(self) -> dict:
        with self.con() as c:
            r = c.execute("SELECT value FROM meta WHERE key='stats'").fetchone()
        return json.loads(r["value"]) if r else {}

    # -- source access (path-traversal safe) --------------------------------------------------------
    def abs_path(self, rel: str) -> Path:
        p = (self.ref.src / rel).resolve()
        if self.ref.src.resolve() not in p.parents and p != self.ref.src.resolve():
            raise ValueError("path escapes repository")
        return p

    def read_lines(self, rel: str, start: int = 1, end: int | None = None) -> list[str]:
        p = self.abs_path(rel)
        lines = p.read_text("utf8", errors="replace").splitlines()
        return lines[max(0, start - 1): end if end else len(lines)]

    def code_of(self, nid: str, max_lines: int = 120) -> str:
        n = self.graph.node(nid)
        if not n or not n["path"] or n["kind"] in ("module", "repo", "external", "effect", "api"):
            return ""
        return "\n".join(self.read_lines(n["path"], n["start"], min(n["end"], n["start"] + max_lines - 1)))


def get_context(repo_id: str, settings: Settings | None = None) -> RepoContext | None:
    settings = settings or get_settings()
    ref = repo_ref(settings, repo_id)
    if ref is None or not ref.db.exists():
        return None
    with _lock:
        ctx = _ctx_cache.get(repo_id)
        if ctx is None or ctx.settings.data_dir != settings.data_dir:
            ctx = RepoContext(settings, ref)
            _ctx_cache[repo_id] = ctx
        return ctx


def forget(repo_id: str) -> None:
    with _lock:
        _ctx_cache.pop(repo_id, None)
