"""Pluggable embedders. `hash` needs nothing (default, CPU-only, offline); the others improve semantic recall."""
from __future__ import annotations
import hashlib
import logging
import math
from collections import Counter

import httpx
import numpy as np

from ..textutil import tokens

log = logging.getLogger("codeworld.embed")
CODERANK_PREFIX = "Represent this query for searching relevant code: "


class Embedder:
    name = "base"
    dim = 0

    def embed_docs(self, texts: list[str]) -> np.ndarray: raise NotImplementedError
    def embed_query(self, text: str) -> np.ndarray: return self.embed_docs([text])[0]


class HashEmbedder(Embedder):
    """Signed feature hashing of identifier-aware unigrams + bigrams. Zero downloads; lexical-semantic hybrid baseline."""

    def __init__(self, dim: int = 768):
        self.dim, self.name = dim, f"hash-{dim}"
        self._cache: dict[str, tuple[int, int]] = {}

    def _slot(self, tok: str) -> tuple[int, int]:
        v = self._cache.get(tok)
        if v is None:
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
            v = (h % self.dim, 1 if (h >> 40) & 1 else -1)
            self._cache[tok] = v
        return v

    def _vec(self, text: str) -> np.ndarray:
        toks = tokens(text)
        feats = Counter(toks)
        feats.update(f"{a}_{b}" for a, b in zip(toks, toks[1:]))
        v = np.zeros(self.dim, dtype=np.float32)
        for t, c in feats.items():
            i, s = self._slot(t)
            v[i] += s * (1.0 + math.log(c))
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_docs(self, texts):
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)


class STEmbedder(Embedder):
    """sentence-transformers on CPU, e.g. nomic-ai/CodeRankEmbed (137M) or sentence-transformers/all-MiniLM-L6-v2."""

    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model, trust_remote_code=True, device="cpu")
        self.model.max_seq_length = min(getattr(self.model, "max_seq_length", 512) or 512, 512)
        self.prefix = CODERANK_PREFIX if "coderank" in model.lower() else ""
        dim_fn = getattr(self.model, "get_embedding_dimension", getattr(self.model, "get_sentence_embedding_dimension", None))
        self.dim = int(dim_fn()) if dim_fn else 768
        self.name = f"st:{model}"

    def embed_docs(self, texts):
        return np.asarray(self.model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False), dtype=np.float32)

    def embed_query(self, text):
        return self.embed_docs([self.prefix + text])[0]


class HTTPEmbedder(Embedder):
    def __init__(self, kind: str, model: str, base_url: str, api_key: str = ""):
        self.kind, self.model, self.base = kind, model, (base_url or "http://localhost:11434").rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.name = f"{kind}:{model}"
        self.dim = len(self.embed_docs(["probe"])[0])

    def embed_docs(self, texts):
        out = []
        for i in range(0, len(texts), 32):
            batch = texts[i:i + 32]
            if self.kind == "ollama":
                r = httpx.post(f"{self.base}/api/embed", json={"model": self.model, "input": batch}, timeout=120)
                r.raise_for_status()
                vecs = r.json()["embeddings"]
            else:
                r = httpx.post(f"{self.base}/embeddings", json={"model": self.model, "input": batch}, headers=self.headers, timeout=120)
                r.raise_for_status()
                vecs = [d["embedding"] for d in r.json()["data"]]
            out.extend(vecs)
        a = np.asarray(out, dtype=np.float32)
        n = np.linalg.norm(a, axis=1, keepdims=True)
        return a / np.where(n == 0, 1, n)


_cache: dict[tuple, Embedder] = {}


def get_embedder(settings) -> Embedder:
    key = (settings.embed_backend, settings.embed_model, settings.embed_base_url)
    if key in _cache:
        return _cache[key]
    emb: Embedder
    try:
        b = settings.embed_backend
        if b == "st":
            emb = STEmbedder(settings.embed_model)
        elif b in ("ollama", "openai"):
            emb = HTTPEmbedder(b, settings.embed_model, settings.embed_base_url or settings.llm_base_url, settings.llm_api_key)
        else:
            emb = HashEmbedder()
    except Exception as e:      # missing optional dependency / server down -> degrade gracefully
        log.warning("embedding backend %s unavailable (%s); falling back to hash embedder", settings.embed_backend, e)
        emb = HashEmbedder()
    _cache[key] = emb
    return emb
