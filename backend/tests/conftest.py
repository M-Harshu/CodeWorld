import os
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "shop_app"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("CODEWORLD_DATA_DIR", str(d))
    monkeypatch.setenv("CODEWORLD_ALLOW_LOCAL", "1")
    monkeypatch.setenv("CODEWORLD_EMBED_BACKEND", "hash")
    monkeypatch.setenv("CODEWORLD_LLM_PROVIDER", "none")
    monkeypatch.delenv("CODEWORLD_LLM_FALLBACKS", raising=False)
    from codeworld import service
    service._ctx_cache.clear()
    return d


def make_ctx(src: Path):
    from codeworld.config import get_settings
    from codeworld.indexer import index_repo
    from codeworld.ingest import register_repo
    from codeworld.service import get_context
    s = get_settings()
    ref = register_repo(s, src.name, str(src.resolve()), "local")
    index_repo(s, ref)
    return get_context(ref.id, s)


@pytest.fixture()
def ctx(data_dir):
    return make_ctx(FIXTURE)


@pytest.fixture()
def git_repo(tmp_path):
    """A writable git copy of the fixture with two commits (needed for history mining + diff review)."""
    dst = tmp_path / "shop_git"
    shutil.copytree(FIXTURE, dst)
    env = {**os.environ, "GIT_AUTHOR_NAME": "Ann", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "Ann", "GIT_COMMITTER_EMAIL": "a@x"}
    run = lambda *a: subprocess.run(["git", *a], cwd=dst, check=True, capture_output=True, env=env)
    run("init", "-q"); run("add", "."); run("commit", "-qm", "initial")
    for i in range(6):                       # synthetic history: auth files change together
        for f in ("app/services/auth_service.py", "app/api/auth.py"):
            p = dst / f
            p.write_text(p.read_text() + f"\n# change {i}\n")
        run("add", "."); run("commit", "-qm", f"tweak auth {i}")
    return dst, run
