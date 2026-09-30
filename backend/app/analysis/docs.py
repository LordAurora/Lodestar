"""Docstring suggestions: which symbols lack docs, and the review queue of proposed comments.

The pure text handling lives in :mod:`app.analysis.docwriter`; this module connects it to the
database and to the files of a repository. Nothing here writes to the repo except
:func:`accept`, which follows the safety rules described in ``docwriter``.
"""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from app.analysis.docwriter import (
    SUPPORTED,
    DocError,
    apply_edits,
    backup_and_write,
    file_digest,
    resolve_in_repo,
    unified_diff,
)
from app.analysis.graph import CodeGraph

DOC_KINDS = ("function", "method", "class")


def list_missing(
    conn: sqlite3.Connection,
    lang: str = "",
    min_lines: int = 3,
    q: str = "",
    include_tests: bool = False,
    limit: int = 500,
) -> dict:
    """Undocumented functions, methods and classes, most-called first."""
    where = [
        "s.has_doc = 0",
        f"s.kind IN ({','.join('?' * len(DOC_KINDS))})",
        f"s.language IN ({','.join('?' * len(SUPPORTED))})",
        "s.end_line - s.start_line + 1 >= ?",
    ]
    args: list = [*DOC_KINDS, *sorted(SUPPORTED), max(min_lines, 1)]
    if lang:
        where.append("s.language = ?")
        args.append(lang)
    if q:
        where.append("(s.qualified_name LIKE ? OR s.file_path LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if not include_tests:
        where.append("s.is_test = 0")
    rows = conn.execute(
        "SELECT s.id, s.qualified_name, s.kind, s.language, s.file_path, s.start_line,"  # noqa: S608
        " s.end_line, s.signature,"
        " (SELECT COUNT(*) FROM symbol_references r WHERE r.to_symbol_id = s.id"
        "  AND r.kind = 'call') AS callers,"
        " (SELECT d.status FROM doc_suggestions d WHERE d.file_path = s.file_path"
        "  AND d.start_line = s.start_line AND d.status IN ('pending', 'failed')"
        "  ORDER BY d.id DESC LIMIT 1) AS suggestion"
        f" FROM symbols s WHERE {' AND '.join(where)}"
        " ORDER BY callers DESC, (s.end_line - s.start_line) DESC, s.file_path, s.start_line",
        args,
    ).fetchall()
    languages = defaultdict(int)
    for r in conn.execute(
        "SELECT language, COUNT(*) n FROM symbols WHERE has_doc = 0 AND kind IN (?, ?, ?)"
        " AND is_test = 0 GROUP BY language",
        DOC_KINDS,
    ):
        if r["language"] in SUPPORTED:
            languages[r["language"]] = r["n"]
    items = [
        {
            "symbol_id": r["id"],
            "qualified_name": r["qualified_name"],
            "kind": r["kind"],
            "language": r["language"],
            "file_path": r["file_path"],
            "start_line": r["start_line"],
            "lines": r["end_line"] - r["start_line"] + 1,
            "signature": r["signature"],
            "callers": r["callers"],
            "suggestion": r["suggestion"],
        }
        for r in rows[:limit]
    ]
    return {"items": items, "total": len(rows), "languages": dict(languages)}


def symbol_rows(conn: sqlite3.Connection, symbol_ids: list[int]) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(symbol_ids))
    return conn.execute(
        f"SELECT * FROM symbols WHERE id IN ({marks}) AND has_doc = 0",  # noqa: S608
        symbol_ids,
    ).fetchall()


def caller_names(graph: CodeGraph, symbol_id: int, limit: int = 3) -> list[str]:
    hits = graph.callers(symbol_id, depth=1).hits
    tests_last = sorted(hits, key=lambda h: (h.node.is_test, h.node.qualified_name))
    return [h.node.qualified_name for h in tests_last[:limit]]


def source_of(root: Path, file_path: str, start: int, end: int) -> tuple[str, str]:
    """(the definition's source text, the file's hash)."""
    target = resolve_in_repo(root, file_path)
    data = target.read_bytes()
    lines = data.decode("utf-8", "replace").splitlines()
    return "\n".join(lines[start - 1 : end]), file_digest(data)


def save_suggestion(
    conn: sqlite3.Connection,
    symbol: sqlite3.Row,
    lines: list[str] | None,
    style: str,
    file_hash: str,
    error: str | None = None,
) -> int:
    """Store a suggestion (or a failure). It replaces an earlier open one for the same place."""
    conn.execute(
        "DELETE FROM doc_suggestions WHERE file_path = ? AND start_line = ?"
        " AND status IN ('pending', 'failed')",
        (symbol["file_path"], symbol["start_line"]),
    )
    cur = conn.execute(
        "INSERT INTO doc_suggestions (symbol_id, file_path, qualified_name, kind, language,"
        " start_line, proposed_text, style, status, error, created_at, file_hash_at_suggestion)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            symbol["id"], symbol["file_path"], symbol["qualified_name"], symbol["kind"],
            symbol["language"], symbol["start_line"], "\n".join(lines or []), style,
            "failed" if error else "pending", error, time.time(), file_hash,
        ),
    )  # fmt: skip
    return cur.lastrowid


def list_suggestions(conn: sqlite3.Connection, status: str = "") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM doc_suggestions" + (" WHERE status = ?" if status else "") + " ORDER BY id",
        (status,) if status else (),
    ).fetchall()
    return [dict(r) for r in rows]


def get_suggestion(conn: sqlite3.Connection, suggestion_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM doc_suggestions WHERE id = ?", (suggestion_id,)).fetchone()


def preview(root: Path, suggestion: sqlite3.Row) -> dict:
    """The unified diff this suggestion would produce right now. Never writes anything."""
    result = {"diff": "", "stale": False, "error": None}
    try:
        target = resolve_in_repo(root, suggestion["file_path"])
        data = target.read_bytes()
        if file_digest(data) != suggestion["file_hash_at_suggestion"]:
            result["stale"] = True
            raise DocError("The file changed after this was suggested. Regenerate it.")
        new, _ = apply_edits(
            data, suggestion["language"], [(suggestion["start_line"], _lines(suggestion))]
        )
        result["diff"] = unified_diff(suggestion["file_path"], data, new)
    except DocError as exc:
        result["error"] = str(exc)
    return result


def _lines(suggestion) -> list[str]:
    return suggestion["proposed_text"].split("\n")


def accept(
    conn: sqlite3.Connection, root: Path, suggestion_ids: list[int]
) -> tuple[list[dict], list[str]]:
    """Write the chosen pending suggestions into the repository.

    All suggestions for one file are applied in a single edit, so the file is backed up and
    rewritten once. A file that changed since the suggestions were made is refused entirely.
    Returns per-suggestion results and the paths of the files that were written.
    """
    by_file: dict[str, list[sqlite3.Row]] = defaultdict(list)
    results: dict[int, dict] = {}
    for sid in suggestion_ids:
        row = get_suggestion(conn, sid)
        if row is None:
            results[sid] = {"id": sid, "status": "error", "error": "Unknown suggestion."}
        elif row["status"] != "pending":
            results[sid] = {"id": sid, "status": "error", "error": f"It is {row['status']}."}
        else:
            by_file[row["file_path"]].append(row)

    written: list[str] = []
    for file_path, rows in by_file.items():
        try:
            target = resolve_in_repo(root, file_path)
            original = target.read_bytes()
            digest = file_digest(original)  # re-hash right before editing
            if any(r["file_hash_at_suggestion"] != digest for r in rows):
                raise DocError("The file changed after this was suggested. Regenerate it.")
            language = rows[0]["language"]
            new, _ = apply_edits(original, language, [(r["start_line"], _lines(r)) for r in rows])
            if file_digest(target.read_bytes()) != digest:  # nobody edited it meanwhile
                raise DocError("The file changed while it was being edited. Try again.")
            backup = backup_and_write(root, file_path, target, original, new)
        except (DocError, OSError) as exc:
            for r in rows:
                results[r["id"]] = {"id": r["id"], "status": "error", "error": str(exc)}
            continue
        written.append(file_path)
        for r in rows:
            conn.execute("UPDATE doc_suggestions SET status = 'accepted' WHERE id = ?", (r["id"],))
            results[r["id"]] = {
                "id": r["id"], "status": "accepted", "error": None, "backup": str(backup),
            }  # fmt: skip
    return [results[s] for s in suggestion_ids if s in results], written


def rebase_pending(conn: sqlite3.Connection, root: Path, files: list[str]) -> None:
    """After files were rewritten and re-analysed, re-point their other open suggestions.

    Inserting comments moves lines and gives symbols new ids. The remaining suggestions are
    still valid comment text, so they follow their symbol by name and take the new file hash.
    A suggestion whose symbol is gone or already documented is dropped.
    """
    for file_path in files:
        try:
            digest = file_digest(resolve_in_repo(root, file_path).read_bytes())
        except (DocError, OSError):
            continue
        for row in conn.execute(
            "SELECT * FROM doc_suggestions WHERE file_path = ? AND status = 'pending'",
            (file_path,),
        ).fetchall():
            symbol = conn.execute(
                "SELECT id, start_line FROM symbols WHERE file_path = ? AND qualified_name = ?"
                " AND kind = ? AND has_doc = 0 ORDER BY ABS(start_line - ?) LIMIT 1",
                (file_path, row["qualified_name"], row["kind"], row["start_line"]),
            ).fetchone()
            if symbol is None:
                conn.execute("DELETE FROM doc_suggestions WHERE id = ?", (row["id"],))
            else:
                conn.execute(
                    "UPDATE doc_suggestions SET symbol_id = ?, start_line = ?,"
                    " file_hash_at_suggestion = ? WHERE id = ?",
                    (symbol["id"], symbol["start_line"], digest, row["id"]),
                )
