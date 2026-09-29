"""Indexing: walk a repository, chunk its files, embed the chunks, store them.

The indexer is *incremental*. Each file's SHA-256 hash is stored, so a
re-index only re-chunks and re-embeds files that are new or changed, and
removes the chunks of files that were deleted. Re-indexing a large repo after
editing one file takes seconds.

``index_repository`` is plain blocking code. The API runs it in a worker
thread and reads the ``IndexProgress`` object it updates to stream progress
to the browser.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pathspec

from app.chunking import Chunk, chunk_file, detect_language
from app.db import Database
from app.embeddings import Embedder
from app.retrieval import tokenize_for_search

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out", "target", "bin", "obj",
    "venv", ".venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".next", ".nuxt", ".svelte-kit", ".idea", ".vscode", "coverage", ".gradle",
}  # fmt: skip
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock", "uv.lock",
    "Pipfile.lock", "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum", "packages.lock.json",
}  # fmt: skip
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".pdf", ".zip", ".gz",
    ".tar", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".jar", ".pyc",
    ".o", ".a", ".lib", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".wav",
    ".mov", ".avi", ".db", ".sqlite", ".onnx", ".pt", ".safetensors", ".npy", ".parquet",
}  # fmt: skip


@dataclass
class IndexProgress:
    """Live counters, read by the SSE endpoint while indexing runs."""

    status: str = "pending"  # pending | scanning | embedding | done | error
    files_total: int = 0
    files_scanned: int = 0
    files_changed: int = 0
    files_deleted: int = 0
    chunks_created: int = 0
    chunks_embedded: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    recent_files: deque = field(default_factory=lambda: deque(maxlen=30))

    def snapshot(self) -> dict:
        end = self.finished_at or time.time()
        data = {k: v for k, v in self.__dict__.items() if k != "recent_files"}
        data["recent_files"] = list(self.recent_files)
        data["elapsed"] = round(end - self.started_at, 1)
        return data


# ---- walking the repository ---------------------------------------------


def _is_binary(path: Path) -> bool:
    with path.open("rb") as fh:
        return b"\0" in fh.read(8192)


def walk_repository(root: Path, max_bytes: int = 1_000_000) -> Iterator[str]:
    """Yield indexable files as POSIX paths relative to ``root``.

    Respects ``.gitignore`` files at every level, and skips vendored or
    generated folders, binaries, lockfiles, minified bundles and large files.
    """
    # Each entry: (directory relative to root, compiled .gitignore of that dir)
    ignore_specs: list[tuple[str, pathspec.PathSpec]] = []

    def ignored(rel: str, is_dir: bool) -> bool:
        for base, spec in ignore_specs:
            if base and not rel.startswith(base + "/"):
                continue
            sub = rel[len(base) + 1 :] if base else rel
            if spec.match_file(sub + "/" if is_dir else sub):
                return True
        return False

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir

        if ".gitignore" in filenames:
            lines = (Path(dirpath) / ".gitignore").read_text("utf-8", errors="ignore").splitlines()
            ignore_specs.append((rel_dir, pathspec.GitIgnoreSpec.from_lines(lines)))

        def rel(name: str, rel_dir: str = rel_dir) -> str:
            return f"{rel_dir}/{name}" if rel_dir else name

        # Prune in place so os.walk does not descend into skipped folders.
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".") and not ignored(rel(d), True)
        )
        for name in sorted(filenames):
            full = Path(dirpath) / name
            suffix = full.suffix.lower()
            if (
                name in LOCKFILES
                or name.startswith(".")  # .gitignore, .env, editor files
                or suffix in BINARY_EXTENSIONS
                or name.endswith((".min.js", ".min.css", ".map"))
                or ignored(rel(name), False)
            ):
                continue
            try:
                if full.stat().st_size > max_bytes or _is_binary(full):
                    continue
            except OSError:
                continue
            yield rel(name)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- the indexing job ---------------------------------------------------


def index_repository(
    db: Database,
    repo_id: str,
    root: Path,
    embedder: Embedder,
    progress: IndexProgress,
    batch_size: int = 32,
    max_bytes: int = 1_000_000,
) -> IndexProgress:
    try:
        _run(db, repo_id, root, embedder, progress, batch_size, max_bytes)
        progress.status = "done"
    except Exception as exc:
        progress.status, progress.error = "error", str(exc)
    progress.finished_at = time.time()
    return progress


def _run(db, repo_id, root, embedder, progress, batch_size, max_bytes) -> None:
    progress.status = "scanning"
    repo = db.get_repo(repo_id)
    if repo is None:
        raise ValueError(f"Unknown repository {repo_id}")

    paths = list(walk_repository(root, max_bytes))
    progress.files_total = len(paths)

    with db.repo(repo_id) as conn:
        # A different embedding model produces incomparable vectors: start over.
        if repo["embedding_model"] and repo["embedding_model"] != embedder.name:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM chunks_fts")
            conn.execute("DELETE FROM files")
        known = {r["path"]: r["hash"] for r in conn.execute("SELECT path, hash FROM files")}

        # 1. Forget files that no longer exist.
        current = set(paths)
        for gone in [p for p in known if p not in current]:
            _delete_file(conn, gone)
            progress.files_deleted += 1

    # 2. Hash every file; chunk the new and changed ones.
    pending: list[tuple[Chunk, str]] = []  # (chunk, file_hash)
    changed_files: list[tuple[str, str, str]] = []  # (path, hash, language)
    for rel in paths:
        data = (root / rel).read_bytes()
        digest = sha256(data)
        progress.files_scanned += 1
        if known.get(rel) == digest:
            continue
        source = data.decode("utf-8", errors="replace")
        chunks = chunk_file(rel, source)
        pending.extend((c, digest) for c in chunks)
        changed_files.append((rel, digest, detect_language(rel)))
        progress.files_changed += 1
        progress.chunks_created += len(chunks)
        progress.recent_files.append(rel)

    # 3. Embed in batches and store. Old chunks of a changed file are removed
    #    right before its new ones are written.
    progress.status = "embedding"
    with db.repo(repo_id) as conn:
        for rel, _, _ in changed_files:
            _delete_file(conn, rel)

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = embedder.embed_documents([c.embedding_text() for c, _ in batch])
        with db.repo(repo_id) as conn:
            for (chunk, digest), vector in zip(batch, vectors, strict=True):
                _insert_chunk(conn, repo_id, chunk, digest, vector)
        progress.chunks_embedded += len(batch)

    # 4. Only now mark files as indexed, so an interrupted run is retried next time.
    with db.repo(repo_id) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO files (path, hash, language, indexed_at) VALUES (?, ?, ?, ?)",
            [(p, h, lang, time.time()) for p, h, lang in changed_files],
        )
    db.update_repo_stats(repo_id, embedder.name)


def _delete_file(conn, path: str) -> None:
    ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file_path = ?", (path,))]
    conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in ids])
    conn.execute("DELETE FROM chunks WHERE file_path = ?", (path,))
    conn.execute("DELETE FROM files WHERE path = ?", (path,))


def _insert_chunk(conn, repo_id: str, chunk: Chunk, digest: str, vector: np.ndarray) -> None:
    cur = conn.execute(
        "INSERT INTO chunks (repo_id, file_path, language, symbol_name, symbol_kind, start_line,"
        " end_line, content, file_hash, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            repo_id,
            chunk.file_path,
            chunk.language,
            chunk.symbol_name,
            chunk.symbol_kind,
            chunk.start_line,
            chunk.end_line,
            chunk.content,
            digest,
            np.asarray(vector, dtype=np.float32).tobytes(),
        ),
    )
    # The keyword index sees the path and symbol too, split into words.
    searchable = f"{chunk.file_path} {chunk.symbol_name or ''} {chunk.content}"
    conn.execute(
        "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
        (cur.lastrowid, tokenize_for_search(searchable)),
    )
