import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import FIXTURE, make_ctx


class Mock:
    """Tiny OpenAI-compatible + Ollama server that replays scripted replies (str or callable(messages)->str)."""
    def __init__(self, script, fail_first=0):
        self.script, self.fail_first, self.requests = list(script), fail_first, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self._send({"data": [{"id": "models/mock-1"}, {"id": "mock-2"}]})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body, self.headers.get("Authorization")))
                if outer.fail_first > 0:
                    outer.fail_first -= 1
                    self.send_response(429); self.send_header("Retry-After", "0"); self.end_headers(); self.wfile.write(b"slow down"); return
                item = outer.script.pop(0) if outer.script else "{}"
                text = item(body["messages"]) if callable(item) else item
                if self.path.endswith("/api/chat"):
                    self._send({"message": {"content": text}, "prompt_eval_count": 5, "eval_count": 7})
                else:
                    self._send({"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 5, "completion_tokens": 7}})
            def _send(self, obj):
                b = json.dumps(obj).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()


@pytest.fixture()
def mock(request):
    ms = []
    def make(script, **kw):
        m = Mock(script, **kw); ms.append(m); return m
    yield make
    for m in ms:
        m.stop()


def _use(monkeypatch, provider, url, model="mock-1", key="k"):
    from codeworld.ai import llm as L
    L._cache.clear()
    monkeypatch.setenv("CODEWORLD_LLM_PROVIDER", provider)
    monkeypatch.setenv("CODEWORLD_LLM_MODEL", model)
    monkeypatch.setenv("CODEWORLD_LLM_BASE_URL", url)
    monkeypatch.setenv("CODEWORLD_LLM_API_KEY", key)


def test_retry_on_429_and_usage(data_dir, monkeypatch, mock):
    m = mock(["pong"], fail_first=2)
    _use(monkeypatch, "openai", m.url + "/v1")
    from codeworld.config import get_settings
    from codeworld.ai.llm import get_llm
    llm = get_llm(get_settings())
    assert llm.chat([{"role": "user", "content": "x"}]) == "pong"
    assert len(m.requests) == 3 and m.requests[-1][2] == "Bearer k"
    assert llm.usage()["tokens_in"] == 5 and llm.list_models() == ["mock-1", "mock-2"]


def test_fallback_chain(data_dir, monkeypatch, mock):
    bad = mock([])
    good = mock(["from fallback"])
    _use(monkeypatch, "ollama", "http://127.0.0.1:9")            # nothing listens here -> fails -> falls through
    monkeypatch.setenv("CODEWORLD_LLM_FALLBACKS", "ollama:mock-1")
    from codeworld.config import get_settings
    from codeworld.ai import llm as L
    s = get_settings()
    s.llm_base_url = good.url
    # first part points at a dead port, second at the mock
    first = L.OllamaLLM("m", "http://127.0.0.1:9"); first.retries = 0
    second = L.OllamaLLM("m", good.url)
    chain = L.ChainLLM([first, second])
    assert chain.chat([{"role": "user", "content": "hi"}]) == "from fallback"
    assert chain.usage()["parts"][0]["failures"] == 1


def test_agent_loop_with_scripted_model(data_dir, monkeypatch, mock):
    def final(msgs):                         # uses the real tool result the loop fed back
        assert "TOOL RESULT" in msgs[-1]["content"] and "AuthService.register" in msgs[-1]["content"]
        return json.dumps({"final": "hash_password is called by AuthService.register [app/services/auth_service.py:13-14] and verify_password. Also invented [nope/x.py:1]."})
    m = mock([json.dumps({"tool": "find_callers", "args": {"symbol": "hash_password"}}), final])
    _use(monkeypatch, "openai", m.url + "/v1")
    ctx = make_ctx(FIXTURE)
    from codeworld.agent.answer import ask
    r = ask(ctx, "who calls hash_password?")
    assert r["provider"].startswith("openai") and [t["tool"] for t in r["trace"]] == ["find_callers"]
    valid = {(c["path"], c["valid"]) for c in r["citations"]}
    assert ("app/services/auth_service.py", True) in valid and ("nope/x.py", False) in valid     # hallucinated citation is flagged
    assert r["citation_precision"] == 0.5


def test_llm_failure_falls_back_to_offline_answer(data_dir, monkeypatch):
    _use(monkeypatch, "ollama", "http://127.0.0.1:9")
    from codeworld.ai import llm as L
    ctx = make_ctx(FIXTURE)
    L.get_llm(ctx.settings).parts if hasattr(L.get_llm(ctx.settings), "parts") else None
    llm = L.get_llm(ctx.settings); llm.retries = 0
    from codeworld.agent.answer import ask
    r = ask(ctx, "who calls make_token?")
    assert "login" in r["answer"] and r["notes"]


def test_patch_proposal_is_reviewed(data_dir, monkeypatch, mock):
    reply = """app/utils/security.py
<<<<<<< SEARCH
def make_token() -> str:
    \"\"\"Create a random session token.\"\"\"
    return secrets.token_hex(16)
=======
def make_token(n: int = 16) -> str:
    \"\"\"Create a random session token.\"\"\"
    return secrets.token_hex(n)
>>>>>>> REPLACE"""
    m = mock([reply])
    _use(monkeypatch, "openai", m.url + "/v1")
    ctx = make_ctx(FIXTURE)
    from codeworld.agent.patch import propose
    r = propose(ctx, "make the token length configurable")
    assert r["ok"] and "token_hex(n)" in r["diff"] and r["review"]["verdict"] in ("pass", "warn", "fail")
    assert "make_token" in " ".join(r["review"]["changed_symbols"])


def test_patch_bad_search_block_retries(data_dir, monkeypatch, mock):
    bad = "app/utils/security.py\n<<<<<<< SEARCH\nnot in file\n=======\nx\n>>>>>>> REPLACE"
    m = mock([bad, bad])
    _use(monkeypatch, "openai", m.url + "/v1")
    from codeworld.agent.patch import propose
    r = propose(make_ctx(FIXTURE), "do something")
    assert not r["ok"] and "matched 0 times" in r["report"] and len(m.requests) == 2


def test_api_token_auth(data_dir, monkeypatch):
    monkeypatch.setenv("CODEWORLD_API_TOKEN", "s3cret")
    from fastapi.testclient import TestClient
    from codeworld.api.main import app
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200 and c.get("/api/health").json()["auth"] is True
    assert c.get("/api/repos").status_code == 401
    assert c.get("/api/repos", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/repos", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/api/repos", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_trace_overlay_resolves_dynamic_dispatch(data_dir, tmp_path):
    import shutil
    from codeworld.config import get_settings
    from codeworld.indexer import index_repo
    src = tmp_path / "shop"
    shutil.copytree(FIXTURE, src)
    out = tmp_path / "t.json"
    subprocess.run([sys.executable, "-m", "codeworld.evaluation.dynamic_trace", "--root", str(src), "--run", "pytest -q tests", "--out", str(out)], check=True, capture_output=True)
    ctx = make_ctx(src)
    g = ctx.graph
    reg, fake = g.resolve("AuthService.register"), g.resolve("FakeRepo.find_by_email")
    assert fake not in g.callees(reg, 1, 0.5)                      # static analysis cannot see the test double
    with ctx.con() as c:
        c.execute("INSERT OR REPLACE INTO meta VALUES ('trace_edges', ?)", (json.dumps(json.loads(out.read_text())),))
    index_repo(get_settings(), ctx.ref, force=True)
    ctx._version = None
    assert ctx.graph.resolve("FakeRepo.find_by_email") in ctx.graph.callees(ctx.graph.resolve("AuthService.register"), 1, 0.5)


def test_override_edges_and_public_api(data_dir, tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("from .base import Base, helper\n__all__ = ['Base', 'helper']\n")
    (tmp_path / "pkg/base.py").write_text("class Base:\n    def run(self):\n        return 1\n\ndef helper():\n    return 2\n\ndef _private_unused():\n    return 3\n")
    (tmp_path / "pkg/impl.py").write_text("from .base import Base\n\nclass Impl(Base):\n    def run(self):\n        return 2\n")
    (tmp_path / "pkg/use.py").write_text("from .base import Base\n\ndef go(b: Base):\n    return b.run()\n")
    (tmp_path / "pkg/server.py").write_text("def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n")
    ctx = make_ctx(tmp_path)
    g = ctx.graph
    assert g.resolve("Impl.run") in g.callees(g.resolve("go"), 1, 0.5)          # CHA: Base.run may dispatch to Impl.run
    from codeworld.analysis import health
    dead = {d["name"]: d for d in health.dead_code(ctx)}
    assert dead["helper"]["confidence"] == "low" and "public API" in dead["helper"]["note"]
    assert "_private_unused" in dead and dead["_private_unused"]["confidence"] == "high"


def test_doctor_runs(data_dir, monkeypatch, mock, capsys):
    m = mock(["pong", json.dumps({"tool": "search_code", "args": {"query": "login"}})])
    _use(monkeypatch, "openai", m.url + "/v1")
    from codeworld import doctor
    import argparse
    rc = doctor.run(argparse.Namespace(list_models=True))
    out = capsys.readouterr().out
    assert "reachable" in out and "follows the JSON tool-call protocol" in out and "mock-1" in out
    assert rc == 0
