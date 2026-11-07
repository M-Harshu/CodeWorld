"""SQLite storage. One database per indexed repository plus a tiny registry database."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path

REPO_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, language TEXT, size INTEGER, sha TEXT, loc INTEGER, is_test INTEGER);
CREATE TABLE IF NOT EXISTS parsed(path TEXT PRIMARY KEY, sha TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualname TEXT, path TEXT,
    start INTEGER, end INTEGER, parent TEXT, props TEXT);
CREATE INDEX IF NOT EXISTS nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS nodes_path ON nodes(path);
CREATE TABLE IF NOT EXISTS edges(src TEXT, dst TEXT, type TEXT, conf REAL, res TEXT, line INTEGER);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src, type);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, type);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, node_id TEXT, path TEXT, kind TEXT, name TEXT,
    start INTEGER, end INTEGER, text TEXT, text_hash TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(name, path, body, chunk_id UNINDEXED, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS embeddings(text_hash TEXT, model TEXT, dim INTEGER, vec BLOB, PRIMARY KEY(text_hash, model));
CREATE TABLE IF NOT EXISTS git_file(path TEXT PRIMARY KEY, commits INTEGER, authors INTEGER, top_author_share REAL, added INTEGER, deleted INTEGER, last_ts INTEGER);
CREATE TABLE IF NOT EXISTS git_cochange(a TEXT, b TEXT, n INTEGER, PRIMARY KEY(a, b));
CREATE TABLE IF NOT EXISTS git_commits(sha TEXT PRIMARY KEY, ts INTEGER, author TEXT, subject TEXT, files TEXT);
"""

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos(id TEXT PRIMARY KEY, name TEXT, source TEXT, kind TEXT, created REAL, indexed REAL, status TEXT, stats TEXT);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, repo_id TEXT, stage TEXT, pct REAL, status TEXT, error TEXT, created REAL);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


@contextmanager
def repo_db(path: Path | str):
    con = connect(path)
    con.executescript(REPO_SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextmanager
def registry_db(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    con = connect(data_dir / "registry.db")
    con.executescript(REGISTRY_SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()
