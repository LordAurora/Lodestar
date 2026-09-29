"""SQLite storage.

Lodestar keeps everything in plain SQLite files, so there is no database
server to install:

* ``app.db`` is a small registry of the repositories the user has added.
* Each indexed repository gets its own ``repos/<id>.db`` file holding its
  files, code chunks, embeddings (as float32 BLOBs), a full-text index (FTS5)
  and the chat conversations about that repository.

Deleting a repository is therefore as simple as deleting one file.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id              TEXT PRIMARY KEY,
    path            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    created_at      REAL NOT NULL,
    last_indexed_at REAL,
    file_count      INTEGER NOT NULL DEFAULT 0,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT
);
"""

REPO_SCHEMA = """
-- One row per indexed file. The hash lets re-indexing skip unchanged files.
CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    hash       TEXT NOT NULL,
    language   TEXT NOT NULL,
    indexed_at REAL NOT NULL
);

-- One row per code chunk (a function, a class, a method or a window of lines).
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    repo_id     TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    language    TEXT NOT NULL,
    symbol_name TEXT,
    symbol_kind TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    content     TEXT NOT NULL,
    file_hash   TEXT NOT NULL,
    embedding   BLOB             -- float32 vector, L2-normalised
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path);

-- Keyword index. `rowid` is the chunk id; `text` holds identifier-split
-- tokens (see retrieval.tokenize_for_search) so BM25 can match `getUser`
-- when the user types "get user".
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize = 'unicode61');

CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,          -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    meta_json       TEXT,                   -- sources, citations, no_answer flag
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults.

    ``check_same_thread=False`` because FastAPI and our worker threads may
    touch the same file; we always open short-lived connections, so this is safe.
    """
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


class Database:
    """Entry point to all storage. Holds paths, hands out connections."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.repos_dir = data_dir / "repos"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.app_db_path = data_dir / "app.db"
        self._initialised: set[str] = set()  # repo ids whose schema already exists
        with self.app() as conn:
            conn.executescript(APP_SCHEMA)

    @contextmanager
    def app(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.app_db_path)
        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    def repo_db_path(self, repo_id: str) -> Path:
        return self.repos_dir / f"{repo_id}.db"

    @contextmanager
    def repo(self, repo_id: str) -> Iterator[sqlite3.Connection]:
        conn = connect(self.repo_db_path(repo_id))
        try:
            if repo_id not in self._initialised:
                conn.executescript(REPO_SCHEMA)
                self._initialised.add(repo_id)
            with conn:
                yield conn
        finally:
            conn.close()

    # ---- repositories -------------------------------------------------

    def add_repo(self, path: str, name: str) -> dict:
        with self.app() as conn:
            existing = conn.execute("SELECT * FROM repos WHERE path = ?", (path,)).fetchone()
            if existing:
                return dict(existing)
            repo_id = new_id()
            conn.execute(
                "INSERT INTO repos (id, path, name, created_at) VALUES (?, ?, ?, ?)",
                (repo_id, path, name, now()),
            )
            return dict(conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone())

    def list_repos(self) -> list[dict]:
        with self.app() as conn:
            rows = conn.execute("SELECT * FROM repos ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_repo(self, repo_id: str) -> dict | None:
        with self.app() as conn:
            row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
            return dict(row) if row else None

    def update_repo_stats(self, repo_id: str, embedding_model: str) -> None:
        with self.repo(repo_id) as rconn:
            files = rconn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunks = rconn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        with self.app() as conn:
            conn.execute(
                "UPDATE repos SET file_count = ?, chunk_count = ?, last_indexed_at = ?,"
                " embedding_model = ? WHERE id = ?",
                (files, chunks, now(), embedding_model, repo_id),
            )

    def delete_repo(self, repo_id: str) -> bool:
        with self.app() as conn:
            deleted = conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,)).rowcount
        self._initialised.discard(repo_id)
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.repo_db_path(repo_id)) + suffix).unlink(missing_ok=True)
        return deleted > 0

    # ---- conversations ------------------------------------------------

    def list_conversations(self, repo_id: str) -> list[dict]:
        with self.repo(repo_id) as conn:
            rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
            return [dict(r) for r in rows]

    def create_conversation(self, repo_id: str, title: str) -> dict:
        conv = {"id": new_id(), "title": title, "created_at": now(), "updated_at": now()}
        with self.repo(repo_id) as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at)"
                " VALUES (:id, :title, :created_at, :updated_at)",
                conv,
            )
        return conv

    def conversation_exists(self, repo_id: str, conv_id: str) -> bool:
        with self.repo(repo_id) as conn:
            row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conv_id,)).fetchone()
            return row is not None

    def rename_conversation(self, repo_id: str, conv_id: str, title: str) -> bool:
        with self.repo(repo_id) as conn:
            n = conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id)
            ).rowcount
            return n > 0

    def delete_conversation(self, repo_id: str, conv_id: str) -> bool:
        with self.repo(repo_id) as conn:
            return conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,)).rowcount > 0

    def list_messages(self, repo_id: str, conv_id: str) -> list[dict]:
        with self.repo(repo_id) as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conv_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def add_message(
        self, repo_id: str, conv_id: str, role: str, content: str, meta_json: str | None = None
    ) -> int:
        with self.repo(repo_id) as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, meta_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (conv_id, role, content, meta_json, now()),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), conv_id))
            return int(cur.lastrowid)

    def delete_last_exchange(self, repo_id: str, conv_id: str) -> None:
        """Remove the last user question and anything after it (used by "Regenerate")."""
        with self.repo(repo_id) as conn:
            row = conn.execute(
                "SELECT MAX(id) FROM messages WHERE conversation_id = ? AND role = 'user'",
                (conv_id,),
            ).fetchone()
            if row and row[0] is not None:
                conn.execute(
                    "DELETE FROM messages WHERE conversation_id = ? AND id >= ?", (conv_id, row[0])
                )
