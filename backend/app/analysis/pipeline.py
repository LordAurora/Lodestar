"""The analysis pipeline: run analyzers over the files that changed, incrementally.

An *analyzer* extracts one kind of fact from a file (symbols, env vars, TODO
comments, ...) and writes rows into its own tables. This module decides **which
files need which analyzer**:

* ``analysis_files`` remembers, per file and analyzer, the file hash and the
  analyzer version that was processed;
* a file is (re)analyzed when its hash changed, the analyzer's ``version`` was
  bumped, or no row exists yet (a repository indexed before Insights existed,
  or a newly added analyzer);
* the rows of files that were deleted are removed;
* each file is parsed with tree-sitter **once** and the tree is shared by all
  analyzers that need it.

After the per-file work, each analyzer that saw changes gets a ``finalize`` call
for whole-repository steps (resolving calls across files, clustering, ...).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.chunking import TREE_SITTER_LANGUAGES
from app.db import Database

log = logging.getLogger(__name__)
BATCH_FILES = 40  # files per database transaction

_local = threading.local()  # tree-sitter parsers are not safe to share between threads


def _parser(language: str):
    parsers = _local.__dict__.setdefault("parsers", {})
    if language not in parsers:
        from tree_sitter_language_pack import get_parser

        parsers[language] = get_parser(language)
    return parsers[language]


class FileContext:
    """One file, read once, with a lazily parsed tree shared by every analyzer."""

    def __init__(self, path: str, language: str, source: bytes, file_hash: str):
        self.path = path
        self.language = language
        self.source = source
        self.hash = file_hash
        self._tree = None
        self._parsed = False

    @property
    def text(self) -> str:
        return self.source.decode("utf-8", errors="replace")

    def root(self):
        """The tree-sitter root node, or None when the language has no grammar here."""
        if not self._parsed:
            self._parsed = True
            if self.language in TREE_SITTER_LANGUAGES.values():
                try:
                    self._tree = _parser(self.language).parse(self.source)
                except Exception as exc:  # grammar missing offline, etc.
                    log.warning("Cannot parse %s (%s): %s", self.path, self.language, exc)
        return self._tree.root_node if self._tree is not None else None


class Analyzer(Protocol):
    name: str
    version: int
    languages: frozenset[str] | None  # None = every file

    def analyze(self, ctx: FileContext, conn, repo_id: str) -> dict | None:
        """Write this file's rows. May return a small JSON-able ``meta`` dict to keep."""

    def delete(self, conn, path: str) -> None:
        """Remove every row this analyzer wrote for ``path``."""

    def finalize(self, conn, repo_id: str, root: Path) -> None:
        """Optional whole-repository step, run after files changed."""


@dataclass
class AnalysisSummary:
    analyzed_files: int = 0
    removed_files: int = 0
    skipped_files: int = 0
    failed: int = 0


def _set(progress, **values) -> None:
    if progress is not None:
        for key, value in values.items():
            setattr(progress, key, value)


def run_analysis(
    db: Database,
    repo_id: str,
    root: Path,
    analyzers: list[Analyzer],
    progress=None,
    force: bool = False,
) -> AnalysisSummary:
    """Analyze new and changed files. ``force`` re-analyzes everything ("Re-analyze")."""
    summary = AnalysisSummary()
    by_name = {a.name: a for a in analyzers}
    touched: set[str] = set()  # analyzers whose data changed, so they need `finalize`

    with db.repo(repo_id) as conn:
        files = {
            r["path"]: (r["hash"], r["language"])
            for r in conn.execute("SELECT path, hash, language FROM files")
        }
        state = {
            (r["path"], r["analyzer"]): (r["hash"], r["version"])
            for r in conn.execute("SELECT path, analyzer, hash, version FROM analysis_files")
        }
        # Files that were deleted since they were analyzed.
        for path, name in [k for k in state if k[0] not in files]:
            if name in by_name:
                by_name[name].delete(conn, path)
                touched.add(name)
            conn.execute("DELETE FROM analysis_files WHERE path = ? AND analyzer = ?", (path, name))
            summary.removed_files += 1
        if force:
            state = {}

    todo: list[tuple[str, list[Analyzer]]] = []
    for path, (file_hash, language) in files.items():
        stale = [
            a
            for a in analyzers
            if (a.languages is None or language in a.languages)
            and state.get((path, a.name)) != (file_hash, a.version)
        ]
        if stale:
            todo.append((path, stale))
    _set(progress, analysis_total=len(todo), analysis_done=0)

    for start in range(0, len(todo), BATCH_FILES):
        with db.repo(repo_id) as conn:
            for path, stale in todo[start : start + BATCH_FILES]:
                file_hash, language = files[path]
                try:
                    data = (root / path).read_bytes()
                except OSError:
                    summary.skipped_files += 1
                    continue
                if hashlib.sha256(data).hexdigest() != file_hash:
                    summary.skipped_files += 1  # changed since indexing: the next index fixes it
                    continue
                ctx = FileContext(path, language, data, file_hash)
                for analyzer in stale:
                    analyzer.delete(conn, path)
                    meta = None
                    try:
                        meta = analyzer.analyze(ctx, conn, repo_id)
                    except Exception as exc:  # one bad file must not stop the run
                        log.warning("%s failed on %s: %s", analyzer.name, path, exc)
                        summary.failed += 1
                        analyzer.delete(conn, path)
                        meta = {"error": str(exc)}
                    conn.execute(
                        "INSERT OR REPLACE INTO analysis_files"
                        " (path, analyzer, hash, version, analyzed_at, meta) VALUES (?,?,?,?,?,?)",
                        (path, analyzer.name, file_hash, analyzer.version, time.time(),
                         json.dumps(meta) if meta else None),
                    )  # fmt: skip
                    touched.add(analyzer.name)
                summary.analyzed_files += 1
        _set(progress, analysis_done=min(start + BATCH_FILES, len(todo)))

    with db.repo(repo_id) as conn:
        for name in touched:
            analyzer = by_name.get(name)
            if analyzer is not None:
                finalize = getattr(analyzer, "finalize", None)
                if finalize:
                    finalize(conn, repo_id, root)
                conn.execute(
                    "INSERT OR REPLACE INTO analysis_state (analyzer, last_analyzed_at)"
                    " VALUES (?, ?)",
                    (name, time.time()),
                )
    return summary
