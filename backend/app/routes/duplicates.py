"""Duplicate finder API: ranked groups, one group's detail, a diff of two members, an export."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.analysis.duplicates import (
    PAIR_FLOOR,
    _load_symbols,
    _member,
    cached_groups,
    diff_lines,
    source_of,
)
from app.routes.insights import repo_or_404
from app.state import AppState, get_state

router = APIRouter(prefix="/api/repos/{repo_id}")
KINDS = {"all", "exact", "similar"}
LIST_MEMBERS = 6  # members shown per group in the list; the detail view has them all


def _settings(state: AppState, min_similarity, kind, include_tests, min_lines):
    if kind not in KINDS:
        raise HTTPException(422, "type must be all, exact or similar.")
    similarity = state.config.duplicate_min_similarity if min_similarity is None else min_similarity
    lines = state.config.duplicate_min_lines if min_lines is None else min_lines
    return max(PAIR_FLOOR, min(float(similarity), 1.0)), kind, include_tests, max(1, lines)


@router.get("/duplicates")
async def duplicates(
    repo_id: str,
    min_similarity: float | None = None,
    type: str = "all",
    include_tests: bool = False,
    min_lines: int | None = None,
    state: AppState = Depends(get_state),
) -> dict:
    """Groups of near-duplicate functions, best refactor value first."""
    repo_or_404(state, repo_id)
    similarity, kind, tests, lines = _settings(
        state, min_similarity, type, include_tests, min_lines
    )

    def read() -> dict:
        with state.db.repo(repo_id) as conn:
            groups = cached_groups(conn, repo_id, similarity, kind, tests, lines)
            pairs = conn.execute("SELECT COUNT(*) FROM duplicate_pairs").fetchone()[0]
        trimmed = [
            {**g, "members": g["members"][:LIST_MEMBERS], "more": max(0, g["size"] - LIST_MEMBERS)}
            for g in groups
        ]
        return {
            "groups": trimmed,
            "total": len(groups),
            "duplicated_lines": sum(g["duplicated_lines"] for g in groups),
            "min_similarity": similarity,
            "floor": PAIR_FLOOR,
            "compared_pairs": pairs,
        }

    return await asyncio.to_thread(read)


@router.get("/duplicates/export", response_class=PlainTextResponse)
async def export_duplicates(
    repo_id: str,
    min_similarity: float | None = None,
    type: str = "all",
    include_tests: bool = False,
    min_lines: int | None = None,
    state: AppState = Depends(get_state),
) -> PlainTextResponse:
    """The groups as a Markdown checklist you can paste into an issue."""
    repo_or_404(state, repo_id)
    similarity, kind, tests, lines = _settings(
        state, min_similarity, type, include_tests, min_lines
    )

    def read() -> list[dict]:
        with state.db.repo(repo_id) as conn:
            return cached_groups(conn, repo_id, similarity, kind, tests, lines)

    groups = await asyncio.to_thread(read)
    out = [
        "# Duplicate code",
        "",
        f"{len(groups)} groups (similarity at least {similarity:.2f})",
        "",
    ]
    for n, g in enumerate(groups, start=1):
        out.append(
            f"## {n}. {g['type'].capitalize()} match: {g['size']} functions, "
            f"{g['duplicated_lines']} duplicated lines, similarity {g['avg_similarity']:.2f}"
        )
        for m in g["members"]:
            place = f"{m['file_path']}:{m['start_line']}-{m['end_line']}"
            out.append(f"- [ ] `{m['qualified_name']}` in `{place}`")
        out.append("")
    return PlainTextResponse(
        "\n".join(out),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="duplicates.md"'},
    )


def _group(conn, group_id: int) -> dict:
    row = conn.execute("SELECT * FROM duplicate_groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Group not found. The code may have changed: reload the list.")
    symbols = _load_symbols(conn)
    ids = [r[0] for r in conn.execute(
        "SELECT symbol_id FROM duplicate_members WHERE group_id = ? ORDER BY symbol_id", (group_id,)
    )]  # fmt: skip
    return {
        "id": row["id"], "type": row["type"], "size": row["size"],
        "avg_similarity": row["avg_similarity"], "duplicated_lines": row["duplicated_lines"],
        "value": row["value"], "members": [_member(symbols[i]) for i in ids if i in symbols],
    }  # fmt: skip


@router.get("/duplicates/{group_id}")
async def duplicate_group(
    repo_id: str, group_id: int, state: AppState = Depends(get_state)
) -> dict:
    """One group with all of its members."""
    repo_or_404(state, repo_id)

    def read() -> dict:
        with state.db.repo(repo_id) as conn:
            return _group(conn, group_id)

    return await asyncio.to_thread(read)


@router.get("/duplicates/{group_id}/diff")
async def duplicate_diff(
    repo_id: str, group_id: int, a: int, b: int, state: AppState = Depends(get_state)
) -> dict:
    """A side-by-side diff of two members of a group."""
    repo = repo_or_404(state, repo_id)

    def read() -> dict:
        with state.db.repo(repo_id) as conn:
            group = _group(conn, group_id)
            if not {a, b} <= {m["symbol_id"] for m in group["members"]}:
                raise HTTPException(422, "Both symbols must be members of this group.")
            left, right = (source_of(conn, Path(repo["path"]), i) for i in (a, b))
        if left is None or right is None:
            raise HTTPException(404, "Symbol not found.")
        result = diff_lines(left[1], right[1])
        return {
            "a": _member(left[0]),
            "b": _member(right[0]),
            "language": left[0]["language"],
            **result,
        }

    return await asyncio.to_thread(read)
