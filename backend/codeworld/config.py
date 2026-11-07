"""Runtime configuration. Everything is driven by environment variables (see .env.example)."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv, find_dotenv
    _repo_env = Path(__file__).resolve().parents[2] / ".env"
    _home_env = Path.home() / ".codeworld" / ".env"
    _found = find_dotenv(usecwd=True)
    if _found:
        load_dotenv(_found, override=False)
    elif _repo_env.exists():
        load_dotenv(_repo_env, override=False)
    elif _home_env.exists():
        load_dotenv(_home_env, override=False)
except ImportError:
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path
    llm_provider: str          # none | ollama | openai
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    embed_backend: str         # hash | st | ollama | openai
    embed_model: str
    embed_base_url: str
    max_file_bytes: int
    max_repo_files: int
    max_repo_mb: int
    clone_depth: int
    allowed_git_hosts: list[str]
    cors_origins: list[str]
    resolver_level: int        # 0 name-only, 1 scope+imports, 2 +types (default), 3 +jedi
    git_commit_limit: int


def get_settings() -> Settings:
    base = Path(_env("CODEWORLD_DATA_DIR", str(Path.home() / ".codeworld"))).expanduser().resolve()
    return Settings(
        data_dir=base,
        llm_provider=_env("CODEWORLD_LLM_PROVIDER", "none").lower(),
        llm_model=_env("CODEWORLD_LLM_MODEL", "qwen2.5-coder:3b"),
        llm_base_url=_env("CODEWORLD_LLM_BASE_URL", "http://localhost:11434"),
        llm_api_key=_env("CODEWORLD_LLM_API_KEY", ""),
        embed_backend=_env("CODEWORLD_EMBED_BACKEND", "hash").lower(),
        embed_model=_env("CODEWORLD_EMBED_MODEL", "nomic-ai/CodeRankEmbed"),
        embed_base_url=_env("CODEWORLD_EMBED_BASE_URL", ""),
        max_file_bytes=int(_env("CODEWORLD_MAX_FILE_BYTES", "400000")),
        max_repo_files=int(_env("CODEWORLD_MAX_REPO_FILES", "6000")),
        max_repo_mb=int(_env("CODEWORLD_MAX_REPO_MB", "200")),
        clone_depth=int(_env("CODEWORLD_CLONE_DEPTH", "1000")),
        allowed_git_hosts=[h.strip() for h in _env("CODEWORLD_GIT_HOSTS", "github.com,gitlab.com,bitbucket.org,codeberg.org").split(",") if h.strip()],
        cors_origins=[o.strip() for o in _env("CODEWORLD_CORS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
        resolver_level=int(_env("CODEWORLD_RESOLVER_LEVEL", "2")),
        git_commit_limit=int(_env("CODEWORLD_GIT_COMMITS", "3000")),
    )
