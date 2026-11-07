"""Tests for the F1–F10 fix pack.

Covers:
  1. patchapply.apply_patch_text — pure-Python diff applier (replaces git apply)
  2. importcheck.check_diff_imports — src/ layout false positive fix (F4)
  3. llm.get_available_models — env-driven model IDs (F5)
  4. api.main.AskBody — 2 MB image field validator (F7)
  5. api.main.AskBody — image field accepts None / small base64 (F7)
"""
from __future__ import annotations
import base64
import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. patchapply
# ---------------------------------------------------------------------------

def test_review_patch_without_git_apply(tmp_path):
    """apply_patch_text applies a simple diff to a scratch copy without git."""
    from codeworld.patchapply import apply_patch_text

    # Create a minimal source file
    src = tmp_path / "mymod.py"
    src.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

    diff = (
        "diff --git a/mymod.py b/mymod.py\n"
        "--- a/mymod.py\n"
        "+++ b/mymod.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def hello():\n"
        "-    return 'hi'\n"
        "+    # Updated greeting\n"
        "+    return 'hello'\n"
    )

    ok, note = apply_patch_text(tmp_path, diff)
    assert ok, note
    assert "applied to a scratch copy" in note


def test_apply_patch_crlf_tolerant(tmp_path):
    """apply_patch_text tolerates CRLF line endings in the diff text."""
    from codeworld.patchapply import apply_patch_text

    src = tmp_path / "hello.py"
    src.write_text("x = 1\n", encoding="utf-8")

    # Simulate Windows CRLF in the diff
    diff = (
        "diff --git a/hello.py b/hello.py\r\n"
        "--- a/hello.py\r\n"
        "+++ b/hello.py\r\n"
        "@@ -1 +1 @@\r\n"
        "-x = 1\r\n"
        "+x = 2\r\n"
    )

    ok, note = apply_patch_text(tmp_path, diff)
    assert ok, note


def test_apply_patch_path_traversal_rejected(tmp_path):
    """apply_patch_text rejects diffs with path-traversal attempts."""
    from codeworld.patchapply import apply_patch_text

    diff = (
        "diff --git a/../evil.py b/../evil.py\n"
        "--- a/../evil.py\n"
        "+++ b/../evil.py\n"
        "@@ -0,0 +1 @@\n"
        "+import os; os.system('rm -rf /')\n"
    )

    ok, note = apply_patch_text(tmp_path, diff)
    assert not ok
    assert "unsafe path" in note


# ---------------------------------------------------------------------------
# 2. importcheck src/ layout (F4)
# ---------------------------------------------------------------------------

def test_check_diff_imports_src_layout(monkeypatch):
    """check_diff_imports does NOT flag packages that live under src/."""
    from codeworld.analysis import importcheck

    # Build a minimal fake context
    class FakeGraph:
        nodes = {}
        def node(self, nid): return None
        def props(self, nid): return {}
        def kind(self, nid): return "file"

    class FakeCtx:
        graph = FakeGraph()

    ctx = FakeCtx()

    # code_files: a new file that imports 'mypackage' which lives at src/mypackage/
    code_files = {
        "src/mypackage/utils.py": {
            "added": {1},
            "added_text": ["import mypackage"],
            "added_map": {1: "import mypackage"},
            "deleted": False,
            "new_file": True,
        }
    }

    # Patch all_paths to include the src/ layout path
    monkeypatch.setattr(importcheck, "_get_all_paths", lambda ctx: ["src/mypackage/__init__.py"], raising=False)

    findings = importcheck.check_diff_imports(ctx, code_files)
    # mypackage should NOT be flagged because src/mypackage/ exists
    titles = [f["title"] for f in findings]
    assert not any("mypackage" in t for t in titles), \
        f"src/ layout package was incorrectly flagged as hallucinated: {titles}"


# ---------------------------------------------------------------------------
# 3. get_available_models env-driven (F5)
# ---------------------------------------------------------------------------

def test_get_available_models_env_driven(monkeypatch):
    """get_available_models uses CODEWORLD_MODEL_* env vars, not hardcoded IDs."""
    monkeypatch.setenv("CODEWORLD_MODEL_GEMINI", "gemini-custom-test")
    monkeypatch.setenv("CODEWORLD_MODEL_OPENROUTER", "mistralai/mixtral-8x7b:free")
    monkeypatch.setenv("CODEWORLD_MODEL_OLLAMA", "llama3:8b")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    # Import after monkeypatching env so os.environ.get picks up the new values
    from codeworld.ai import llm as llm_mod
    import importlib
    importlib.reload(llm_mod)

    class FakeSettings:
        llm_api_key = ""
        llm_provider = "gemini"
        llm_model = "gemini-custom-test"

    models = llm_mod.get_available_models(FakeSettings())
    by_id = {m["id"]: m for m in models}

    assert by_id["gemini"]["model"] == "gemini-custom-test"
    assert by_id["openrouter"]["model"] == "mistralai/mixtral-8x7b:free"
    assert by_id["ollama"]["model"] == "llama3:8b"


# ---------------------------------------------------------------------------
# 4. AskBody image field validator — oversized image rejected (F7)
# ---------------------------------------------------------------------------

def test_ask_body_image_too_large():
    """AskBody rejects image strings exceeding the 2.8 MB limit."""
    from pydantic import ValidationError
    from codeworld.api.main import AskBody

    oversized = "A" * 3_000_000   # 3 MB ASCII string
    with pytest.raises(ValidationError, match="2 MB"):
        AskBody(question="test", image=oversized)


# ---------------------------------------------------------------------------
# 5. AskBody image field validator — None and small images accepted (F7)
# ---------------------------------------------------------------------------

def test_ask_body_image_ok():
    """AskBody accepts None and small base64-encoded images."""
    from codeworld.api.main import AskBody

    # No image
    body_none = AskBody(question="hello")
    assert body_none.image is None

    # Small valid base64 (< 2 MB)
    small_b64 = "data:image/png;base64," + base64.b64encode(b"x" * 1000).decode()
    body_img = AskBody(question="describe this", image=small_b64)
    assert body_img.image == small_b64

