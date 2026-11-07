"""Repository ingestion: safe clone, safe ZIP extraction, local paths, file scanning. Never executes repository code."""
from __future__ import annotations
import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .db import registry_db

IGNORE_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
               "site-packages", "target", ".idea", ".vscode", "vendor", ".next", "coverage", ".gradle", "bower_components", ".eggs",
               ".ruff_cache", "htmlcov", ".cache"}
CODE_EXT = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
            ".ts": "typescript", ".tsx": "typescript"}
DOC_EXT = {".md", ".rst"}


class IngestError(ValueError):
    pass


@dataclass
class RepoRef:
    id: str
    name: str
    root: Path          # directory holding src/ and index.db
    src: Path           # source tree being indexed
    db: Path


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "repo"


def repo_ref(settings: Settings, repo_id: str) -> RepoRef | None:
    with registry_db(settings.data_dir) as con:
        r = con.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
    if not r:
        return None
    root = settings.data_dir / "repos" / r["id"]
    src = Path(r["source"]) if r["kind"] == "local" else root / "src"
    return RepoRef(r["id"], r["name"], root, src, root / "index.db")


def register_repo(settings: Settings, name: str, source: str, kind: str) -> RepoRef:
    rid = f"{slug(name)}-{uuid.uuid4().hex[:6]}"
    with registry_db(settings.data_dir) as con:
        con.execute("INSERT INTO repos(id,name,source,kind,created,indexed,status,stats) VALUES (?,?,?,?,?,?,?,?)",
                    (rid, name, source, kind, time.time(), 0, "pending", "{}"))
    root = settings.data_dir / "repos" / rid
    root.mkdir(parents=True, exist_ok=True)
    src = Path(source) if kind == "local" else root / "src"
    return RepoRef(rid, name, root, src, root / "index.db")


def validate_git_url(url: str, settings: Settings) -> str:
    u = urlparse(url.strip())
    if u.scheme != "https" or not u.hostname:
        raise IngestError("Only https:// repository URLs are accepted.")
    if u.username or u.password:
        raise IngestError("Credentials in URLs are not allowed.")
    if u.hostname.lower() not in settings.allowed_git_hosts:
        raise IngestError(f"Host '{u.hostname}' is not in the allow-list ({', '.join(settings.allowed_git_hosts)}).")
    if not re.fullmatch(r"/[\w.\-]+/[\w.\-]+(\.git)?/?", u.path):
        raise IngestError("URL must look like https://host/owner/repo")
    return f"https://{u.hostname.lower()}{u.path.rstrip('/')}"


def clone_repo(url: str, dest: Path, settings: Settings, branch: str | None = None) -> None:
    url = validate_git_url(url, settings)
    if branch and not re.fullmatch(r"[\w./\-]{1,100}", branch):
        raise IngestError("Invalid branch name.")
    if dest.exists():
        shutil.rmtree(dest)
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never", "-c", "core.symlinks=false",
           "clone", "--depth", str(settings.clone_depth), "--single-branch", "--no-tags", "--quiet"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(dest)]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600, env=env)
    except subprocess.CalledProcessError as e:
        raise IngestError("git clone failed: " + e.stderr.decode("utf8", "replace")[-300:]) from e
    except subprocess.TimeoutExpired as e:
        raise IngestError("git clone timed out") from e
    check_size(dest, settings)


def check_size(root: Path, settings: Settings) -> None:
    total = 0
    for p in root.rglob("*"):
        if ".git" in p.parts:
            continue
        if p.is_file() and not p.is_symlink():
            total += p.stat().st_size
            if total > settings.max_repo_mb * 1024 * 1024:
                shutil.rmtree(root, ignore_errors=True)
                raise IngestError(f"Repository exceeds the {settings.max_repo_mb} MB limit.")


def safe_extract_zip(zip_path: Path, dest: Path, settings: Settings) -> None:
    """Zip-slip, symlink and zip-bomb safe extraction."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    limit = settings.max_repo_mb * 1024 * 1024
    total, count = 0, 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if name.startswith("/") or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
                raise IngestError(f"Unsafe path in archive: {name}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue                                       # symlink
            if any(part in IGNORE_DIRS for part in name.split("/")[:-1]):
                continue
            total += info.file_size
            count += 1
            if total > limit or count > settings.max_repo_files * 3:
                shutil.rmtree(dest, ignore_errors=True)
                raise IngestError("Archive is too large (size or file-count limit).")
            target = (dest / name).resolve()
            if dest.resolve() not in target.parents:
                raise IngestError(f"Unsafe path in archive: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    entries = [p for p in dest.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():              # strip single wrapper folder
        inner = entries[0]
        for p in inner.iterdir():
            shutil.move(str(p), str(dest / p.name))
        inner.rmdir()


@dataclass
class FileInfo:
    path: str
    language: str
    size: int
    sha: str
    abs: Path


def scan_files(src: Path, settings: Settings) -> tuple[list[FileInfo], list[FileInfo]]:
    code, docs = [], []
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".") or d in (".github",)]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.is_symlink():
                continue
            ext = p.suffix.lower()
            if ext not in CODE_EXT and ext not in DOC_EXT:
                continue
            if fn.endswith((".min.js", ".d.ts", ".bundle.js")):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > settings.max_file_bytes or size == 0:
                continue
            rel = p.relative_to(src).as_posix()
            data = p.read_bytes()
            if b"\x00" in data[:2000]:
                continue
            fi = FileInfo(rel, CODE_EXT.get(ext, "markdown"), size, hashlib.sha1(data).hexdigest(), p)
            (code if ext in CODE_EXT else docs).append(fi)
            if len(code) >= settings.max_repo_files:
                return code, docs
    return code, docs
