import json
import subprocess
import sys
import time
import zipfile

import pytest

from codeworld.analysis import diffreview
from codeworld.ingest import IngestError, safe_extract_zip, validate_git_url
from codeworld.config import get_settings
from conftest import make_ctx


def test_review_catches_ai_style_problems(data_dir, git_repo):
    src, run = git_repo
    base_ctx = make_ctx(src)
    # craft an "AI-written" change, capture its diff, then restore the tree so the index is the base snapshot
    p = src / "app/services/order_service.py"
    original = p.read_text()
    p.write_text("import requests_oauth_magic\nfrom app.utils.security import encrypt_everything\nfrom app.api.auth import login\n" + original + '''

def total_price_c(items):
    total = 0
    for item in items:
        total += item["price"] * item["qty"]
    return total
''')
    diff = subprocess.run(["git", "diff", "--unified=0"], cwd=src, capture_output=True, text=True).stdout
    p.write_text(original)
    rep = diffreview.review_patch(base_ctx, diff)
    cats = {(f["category"], f["severity"]) for f in rep["findings"]}
    assert ("hallucinated-import", "high") in cats                  # encrypt_everything does not exist
    assert any(f["category"] == "hallucinated-import" and "requests_oauth_magic" in f["title"] for f in rep["findings"])
    assert any(f["category"] == "duplicate-logic" for f in rep["findings"])
    assert rep["verdict"] == "fail" and "CodeWorld PR Guardrail" in rep["markdown"]


def test_cochange_and_incremental_reindex(data_dir, git_repo):
    src, _ = git_repo
    ctx = make_ctx(src)
    from codeworld.analysis import gitmining
    with ctx.con() as c:
        co = gitmining.cochange_for(c, "app/services/auth_service.py")
    assert co and co[0]["path"] == "app/api/auth.py" and co[0]["confidence"] > 0.5
    from codeworld.indexer import index_repo
    (src / "app/utils/security.py").write_text((src / "app/utils/security.py").read_text() + "\n\ndef newfn():\n    return 1\n")
    stats = index_repo(get_settings(), ctx.ref)
    assert stats["changed_files"] == 1
    ctx._version = None
    assert ctx.graph.find("newfn")


def test_url_validation():
    s = get_settings()
    assert validate_git_url("https://github.com/pallets/flask", s) == "https://github.com/pallets/flask"
    for bad in ("http://github.com/a/b", "https://evil.example.com/a/b", "https://user:pw@github.com/a/b", "file:///etc/passwd", "https://github.com/a/b; rm -rf /"):
        with pytest.raises(IngestError):
            validate_git_url(bad, s)


def test_zip_slip_blocked(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../../escape.py", "print(1)")
    with pytest.raises(IngestError):
        safe_extract_zip(z, tmp_path / "out", get_settings())


def test_api_end_to_end(data_dir):
    from fastapi.testclient import TestClient
    from codeworld.api.main import app
    from conftest import FIXTURE
    c = TestClient(app)
    r = c.post("/api/repos/local", json={"path": str(FIXTURE)}).json()
    for _ in range(100):
        j = c.get(f"/api/jobs/{r['job_id']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    rid = r["repo_id"]
    g = c.get(f"/api/repos/{rid}/graph").json()
    assert {n["label"] for n in g["nodes"]} >= {"app/", "tests/", "web/"}
    g2 = c.get(f"/api/repos/{rid}/graph", params={"expanded": "mod:app|mod:app/services"}).json()
    assert any(n["label"] == "auth_service.py" for n in g2["nodes"])
    assert c.get(f"/api/repos/{rid}/file", params={"path": "../../etc/passwd"}).status_code == 404
    assert c.post(f"/api/repos/{rid}/ask", json={"question": "who calls make_token?"}).json()["citations"]
    assert c.get(f"/api/repos/{rid}/city").json()["buildings"]
    assert c.get(f"/api/repos/{rid}/health").json()["score"] >= 0
    assert c.post(f"/api/repos/{rid}/tools/impact", json={"args": {"target": "hash_password"}}).status_code == 200


def test_mcp_stdio(data_dir):
    from conftest import FIXTURE
    p = subprocess.Popen([sys.executable, "-m", "codeworld.mcp_server", "--repo", str(FIXTURE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    def rpc(m):
        p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
        return json.loads(p.stdout.readline())
    try:
        assert rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]["serverInfo"]["name"] == "codeworld"
        assert "impact" in [t["name"] for t in rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        out = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "find_callers", "arguments": {"symbol": "hash_password"}}})
        assert "AuthService.register" in out["result"]["content"][0]["text"]
    finally:
        p.terminate()


def test_eval_harness(ctx):
    import json as j
    from pathlib import Path
    from codeworld.evaluation import qa_eval
    ds = j.loads((Path(qa_eval.__file__).parent / "datasets" / "shop_app_qa.json").read_text())
    res = qa_eval.evaluate(ctx, ds, ["bm25", "graph", "agentic"], 5)
    assert res["per_mode"]["graph"]["sym_recall"] >= res["per_mode"]["bm25"]["sym_recall"] - 0.05
    assert res["per_mode"]["agentic"]["citation_precision"] == 1.0
