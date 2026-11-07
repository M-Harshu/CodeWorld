import os
import subprocess

import pytest

from conftest import FIXTURE


def _repo(tmp_path):
    import shutil
    bare_root = tmp_path / "remote" / "acme"
    work = tmp_path / "work"
    shutil.copytree(FIXTURE, work)
    env = {**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@x"}
    run = lambda *a, cwd=work: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout
    run("init", "-q", "-b", "main"); run("add", "."); run("commit", "-qm", "base")
    base = run("rev-parse", "HEAD").strip()
    p = work / "app/services/order_service.py"
    p.write_text("import requests_oauth_magic\n" + p.read_text())
    run("commit", "-qam", "pr change")
    diff = run("diff", base, "HEAD")
    bare_root.parent.mkdir(parents=True)
    run("clone", "-q", "--bare", str(work), str(bare_root / "shop"), cwd=tmp_path)
    run("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare_root / "shop")
    return base, diff


def test_review_pr_flow_with_mocked_api(data_dir, tmp_path, monkeypatch, capsys):
    base, diff = _repo(tmp_path)
    monkeypatch.setenv("CODEWORLD_GITHUB_BASE", f"file://{tmp_path / 'remote'}")
    from codeworld import github as gh
    from codeworld.config import get_settings

    def fake_get(path, accept="application/vnd.github+json"):
        class R:
            text = diff
            def json(self):
                return {"title": "Add oauth", "base": {"sha": base}, "head": {"sha": "f" * 40}}
        return R()
    monkeypatch.setattr(gh, "_get", fake_get)
    posted = {}
    monkeypatch.setattr(gh, "post_comment", lambda slug, n, body: posted.update(slug=slug, n=n, body=body) or "http://x/1")
    rc = gh.review_pr(get_settings(), "acme/shop", 7, post=True)
    out = capsys.readouterr().out
    assert "Unresolvable package 'requests_oauth_magic'" in out and posted["n"] == 7 and "PR Guardrail" in posted["body"]
    assert rc == 0                      # only a medium finding -> not a failing exit code


def test_issue_suspects_flow(data_dir, tmp_path, monkeypatch, capsys):
    base, _ = _repo(tmp_path)
    monkeypatch.setenv("CODEWORLD_GITHUB_BASE", f"file://{tmp_path / 'remote'}")
    from codeworld import github as gh
    from codeworld.config import get_settings

    def fake_get(path, accept="application/vnd.github+json"):
        class R:
            def json(self):
                if path.endswith("/issues/3"):
                    return {"title": "Login fails when password hash mismatch", "body": "verify_password raises for unicode passwords"}
                if "/commits/" in path:
                    return {"sha": base}
                return {"default_branch": "main"}
        return R()
    monkeypatch.setattr(gh, "_get", fake_get)
    gh.issue_suspects(get_settings(), "acme/shop", 3, post=False)
    out = capsys.readouterr().out
    assert "verify_password" in out and "where this issue probably lives" in out


def test_ci_cli_flow_matches_the_workflow_template(data_dir, tmp_path):
    """Replays the commands in .github/workflows/codeworld-review.yml against a local 'origin'."""
    import shutil
    import sys
    work = tmp_path / "w"
    shutil.copytree(FIXTURE, work)
    env = {**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@x"}
    run = lambda *a: subprocess.run(["git", *a], cwd=work, check=True, capture_output=True, text=True, env=env)
    run("init", "-q", "-b", "main"); run("add", "."); run("commit", "-qm", "base")
    run("clone", "-q", "--bare", str(work), str(tmp_path / "origin.git")); run("remote", "add", "origin", str(tmp_path / "origin.git")); run("fetch", "-q", "origin")
    run("checkout", "-q", "-b", "feature")
    p = work / "app/services/order_service.py"
    p.write_text("from app.utils.security import encrypt_everything\n" + p.read_text())
    run("commit", "-qam", "ai change")
    e = {**env, "PYTHONUTF8": "1", "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1])}
    r = subprocess.run([sys.executable, "-m", "codeworld", "review", str(work), "--base", "origin/main", "--head", "HEAD", "--fail-on", "never", "--output", str(tmp_path / "review.md")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    assert r.returncode == 0 and "encrypt_everything" in (tmp_path / "review.md").read_text()
    r2 = subprocess.run([sys.executable, "-m", "codeworld", "review", str(work), "--base", "origin/main", "--head", "HEAD", "--fail-on", "high"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    assert r2.returncode == 1                                      # the gate step of the workflow
