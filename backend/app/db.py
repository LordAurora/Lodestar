"""SQLite storage.

Lodestar keeps everything in plain SQLite files, so there is no database
server to install:

* ``app.db`` is a small registry of the repositories the user has added.
* Each indexed repository gets its own ``repos/<id>.db`` file holding its
  files, code chunks, embeddings (as float32 BLOBs), a full-text index (FTS5)
  and the chat conversations about that repository.

Deleting a repository is therefore as simple as deleting one file.

Each repo database is versioned with SQLite's ``PRAGMA user_version`` and upgraded
by ``migrate`` when it is opened, so indexes made by an older Lodestar keep working.
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


# Insights (static analysis) tables. The `symbols`, `call_sites` and `import_statements`
# tables hold what was *extracted* from each file and follow that file's lifecycle
# (re-created when its hash changes, removed when it is deleted). `symbol_references`
# and `imports` are *derived* from them by resolving names across files, and are
# rebuilt after every analysis run. (The table is not called `references` because that
# is an SQL keyword.)
ANALYSIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY,
    repo_id        TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    language       TEXT NOT NULL,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind           TEXT NOT NULL,          -- function | method | class
    owner          TEXT,                   -- qualified name of the enclosing class
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    is_test        INTEGER NOT NULL DEFAULT 0,
    has_doc        INTEGER NOT NULL DEFAULT 0,
    signature      TEXT,
    file_hash      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);

CREATE TABLE IF NOT EXISTS call_sites (
    id             INTEGER PRIMARY KEY,
    repo_id        TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    from_symbol_id INTEGER,                -- NULL for module-level code
    to_name        TEXT NOT NULL,
    receiver       TEXT,                   -- `obj` in obj.method()
    kind           TEXT NOT NULL,          -- call | inherit
    line           INTEGER NOT NULL,
    file_hash      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_call_sites_file ON call_sites(file_path);

CREATE TABLE IF NOT EXISTS import_statements (
    id         INTEGER PRIMARY KEY,
    repo_id    TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    module     TEXT NOT NULL,
    names_json TEXT,
    alias      TEXT,
    line       INTEGER NOT NULL,
    file_hash  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_statements_file ON import_statements(file_path);

CREATE TABLE IF NOT EXISTS symbol_references (
    id             INTEGER PRIMARY KEY,
    repo_id        TEXT NOT NULL,
    from_symbol_id INTEGER,
    to_name        TEXT NOT NULL,
    to_symbol_id   INTEGER,                -- NULL when the name could not be resolved
    kind           TEXT NOT NULL,          -- call | import | inherit
    line           INTEGER NOT NULL,
    file_path      TEXT NOT NULL,
    confidence     TEXT NOT NULL,          -- high | medium | low
    file_hash      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refs_to ON symbol_references(to_symbol_id);
CREATE INDEX IF NOT EXISTS idx_refs_from ON symbol_references(from_symbol_id);

CREATE TABLE IF NOT EXISTS imports (
    id                 INTEGER PRIMARY KEY,
    repo_id            TEXT NOT NULL,
    file_path          TEXT NOT NULL,
    module             TEXT NOT NULL,
    resolved_file_path TEXT,               -- NULL for third-party or unknown modules
    file_hash          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_path);

-- Which analyzer has processed which version of which file (drives incremental runs).
CREATE TABLE IF NOT EXISTS analysis_files (
    path        TEXT NOT NULL,
    analyzer    TEXT NOT NULL,
    hash        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    analyzed_at REAL NOT NULL,
    meta        TEXT,                      -- small JSON blob the analyzer may keep
    PRIMARY KEY (path, analyzer)
);

CREATE TABLE IF NOT EXISTS analysis_state (
    analyzer         TEXT PRIMARY KEY,
    last_analyzed_at REAL NOT NULL
);
"""

# Environment variables read by the code. Defaults of secret-looking names are never stored.
ENV_SCHEMA = """
CREATE TABLE IF NOT EXISTS env_vars (
    id            INTEGER PRIMARY KEY,
    repo_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    line          INTEGER NOT NULL,
    language      TEXT NOT NULL,
    default_value TEXT,                    -- literal default; NULL for secrets or computed ones
    has_default   INTEGER NOT NULL DEFAULT 0,
    required      INTEGER NOT NULL DEFAULT 1,
    is_secret     INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'code',   -- code | pydantic
    symbol_id     INTEGER,
    file_hash     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_env_name ON env_vars(name);
CREATE INDEX IF NOT EXISTS idx_env_file ON env_vars(file_path);
"""

# TODO / FIXME / ... comments, plus the cached topic clusters built from their text.
DEBT_SCHEMA = """
CREATE TABLE IF NOT EXISTS debt_items (
    id          INTEGER PRIMARY KEY,
    repo_id     TEXT NOT NULL,
    tag         TEXT NOT NULL,
    text        TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    line        INTEGER NOT NULL,
    symbol_id   INTEGER,
    assignee    TEXT,                      -- `alice` in `TODO(alice): ...`
    author      TEXT,                      -- from `git blame`, when available
    commit_date REAL,                      -- unix time from `git blame`
    blamed      INTEGER NOT NULL DEFAULT 0,
    file_hash   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_debt_file ON debt_items(file_path);

CREATE TABLE IF NOT EXISTS debt_clusters (
    item_id    INTEGER PRIMARY KEY,
    cluster_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS debt_cluster_labels (
    cluster_id INTEGER PRIMARY KEY,
    label      TEXT NOT NULL,
    size       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS debt_topic_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    signature   TEXT NOT NULL,
    computed_at REAL NOT NULL
);
"""

# HTTP endpoints. The first three tables hold what each file says (and follow that file's
# lifecycle); `endpoints` is derived from them by following mounts across files.
ENDPOINT_SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoint_routes (
    id        INTEGER PRIMARY KEY,
    repo_id   TEXT NOT NULL,
    file_path TEXT NOT NULL,
    method    TEXT NOT NULL,
    path      TEXT NOT NULL,               -- relative to its owner
    owner     TEXT,                        -- router variable or controller class
    line      INTEGER NOT NULL,
    handler   TEXT,
    framework TEXT NOT NULL,
    dynamic   INTEGER NOT NULL DEFAULT 0,
    file_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_endpoint_routes_file ON endpoint_routes(file_path);

CREATE TABLE IF NOT EXISTS endpoint_routers (
    id         INTEGER PRIMARY KEY,
    repo_id    TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    var        TEXT NOT NULL,
    framework  TEXT NOT NULL,
    prefix     TEXT NOT NULL DEFAULT '',
    is_root    INTEGER NOT NULL DEFAULT 0,
    parent_var TEXT,
    dynamic    INTEGER NOT NULL DEFAULT 0,
    line       INTEGER NOT NULL DEFAULT 0,
    file_hash  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_endpoint_routers_file ON endpoint_routers(file_path);

CREATE TABLE IF NOT EXISTS endpoint_includes (
    id         INTEGER PRIMARY KEY,
    repo_id    TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    parent_var TEXT NOT NULL,
    target     TEXT NOT NULL,
    prefix     TEXT NOT NULL DEFAULT '',
    framework  TEXT NOT NULL,
    dynamic    INTEGER NOT NULL DEFAULT 0,
    line       INTEGER NOT NULL,
    file_hash  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_endpoint_includes_file ON endpoint_includes(file_path);

CREATE TABLE IF NOT EXISTS endpoints (
    id                INTEGER PRIMARY KEY,
    repo_id           TEXT NOT NULL,
    method            TEXT NOT NULL,
    path              TEXT NOT NULL,       -- the full path, when it could be resolved
    handler_symbol_id INTEGER,
    handler_name      TEXT,
    file_path         TEXT NOT NULL,
    line              INTEGER NOT NULL,
    framework         TEXT NOT NULL,
    is_partial        INTEGER NOT NULL DEFAULT 0,
    file_hash         TEXT NOT NULL
);
"""

# (version, SQL). Versions only ever grow, and every script must be safe to run twice.
MIGRATIONS: list[tuple[int, str]] = [
    (1, REPO_SCHEMA),
    (2, ANALYSIS_SCHEMA),
    (3, ENV_SCHEMA),
    (4, DEBT_SCHEMA),
    (5, ENDPOINT_SCHEMA),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def migrate(conn: sqlite3.Connection) -> int:
    """Bring a repo database up to date. Idempotent. Returns the resulting version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in MIGRATIONS:
        if version > current:
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {version}")  # PRAGMA takes no parameters
            current = version
    return current


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
                migrate(conn)
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
