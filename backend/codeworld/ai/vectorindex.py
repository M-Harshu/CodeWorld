from __future__ import annotations
import numpy as np


class VectorIndex:
    def __init__(self, ids: list[str], mat: np.ndarray):
        self.ids, self.mat = ids, mat

    @staticmethod
    def load(con, model: str) -> "VectorIndex | None":
        rows = con.execute("SELECT c.id, e.vec, e.dim FROM chunks c JOIN embeddings e ON e.text_hash=c.text_hash AND e.model=?", (model,)).fetchall()
        if not rows:
            return None
        ids = [r["id"] for r in rows]
        mat = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
        return VectorIndex(ids, mat)

    def search(self, q: np.ndarray, k: int = 20) -> list[tuple[str, float]]:
        if q.shape[0] != self.mat.shape[1]:
            return []
        sims = self.mat @ q
        k = min(k, len(self.ids))
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [(self.ids[i], float(sims[i])) for i in top if sims[i] > 0]
