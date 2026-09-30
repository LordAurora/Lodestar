"""Docstring suggester API.

Generation runs as a background job and only ever stores *proposals*. Nothing touches the
repository until a suggestion is accepted, and accepting follows the safety rules in
:mod:`app.analysis.docwriter` (hash check, byte-identical outside the insertion, backup first,
re-index afterwards).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.analysis.docs import (
    accept,
    caller_names,
    get_suggestion,
    list_missing,
    list_suggestions,
    preview,
    rebase_pending,
    save_suggestion,
    source_of,
    symbol_rows,
)
from app.analysis.docwriter import (
    STYLES,
    DocError,
    apply_edits,
    build_prompt,
    parse_answer,
    render_lines,
)
from app.analysis.graph import CodeGraph
from app.indexer import RUNNING_STATUSES, IndexProgress, index_repository
from app.routes.insights import repo_or_404
from app.state import AppState, get_state

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/repos/{repo_id}")

MAX_BATCH = 100
LLM_TIMEOUT = 180  # seconds per function; the model is small but may be on the CPU


class SuggestRequest(BaseModel):
    symbol_ids: list[int] = Field(min_length=1, max_length=MAX_BATCH)


class AcceptRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


def _style(state: AppState) -> str:
    style = state.config.docstring_style.lower()
    return style if style in STYLES else "google"


@router.get("/docs/missing")
async def missing_docs(
    repo_id: str,
    lang: str = "",
    min_lines: int = 3,
    q: str = "",
    include_tests: bool = False,
    state: AppState = Depends(get_state),
) -> dict:
    """Functions, methods and classes without a doc comment, most-called first."""
    repo_or_404(state, repo_id)

    def read() -> dict:
        with state.db.repo(repo_id) as conn:
            return list_missing(conn, lang, min_lines, q, include_tests)

    return await asyncio.to_thread(read)


@router.post("/docs/suggest", status_code=202)
async def suggest(repo_id: str, body: SuggestRequest, state: AppState = Depends(get_state)) -> dict:
    """Generate suggestions for the chosen symbols in the background (nothing is written)."""
    repo = repo_or_404(state, repo_id)
    job = state.doc_jobs.get(repo_id)
    if job and job["status"] == "running":
        raise HTTPException(409, "Suggestions are already being generated.")

    def read():
        with state.db.repo(repo_id) as conn:
            return symbol_rows(conn, body.symbol_ids)

    symbols = await asyncio.to_thread(read)
    if not symbols:
        raise HTTPException(422, "None of those functions are undocumented any more.")
    _start_job(state, repo_id, Path(repo["path"]), symbols)
    return state.doc_jobs[repo_id]


@router.get("/docs/job")
async def doc_job(repo_id: str, state: AppState = Depends(get_state)) -> dict:
    """Progress of the current (or last) generation job."""
    repo_or_404(state, repo_id)
    return state.doc_jobs.get(repo_id) or {"status": "idle", "total": 0, "done": 0, "failed": 0}


@router.get("/docs/suggestions")
async def suggestions(repo_id: str, status: str = "", state: AppState = Depends(get_state)) -> dict:
    repo_or_404(state, repo_id)

    def read():
        with state.db.repo(repo_id) as conn:
            return list_suggestions(conn, status)

    items = await asyncio.to_thread(read)
    return {"items": items, "style": _style(state)}


@router.get("/docs/suggestions/{suggestion_id}/diff")
async def suggestion_diff(
    repo_id: str, suggestion_id: int, state: AppState = Depends(get_state)
) -> dict:
    """The unified diff the suggestion would apply. This is the dry run: nothing is written."""
    repo = repo_or_404(state, repo_id)

    def read() -> dict:
        with state.db.repo(repo_id) as conn:
            row = get_suggestion(conn, suggestion_id)
            if row is None:
                raise HTTPException(404, "Unknown suggestion.")
            if row["status"] == "failed":
                return {"diff": "", "stale": False, "error": row["error"]}
            return preview(Path(repo["path"]), row)

    return await asyncio.to_thread(read)


@router.post("/docs/suggestions/{suggestion_id}/reject")
async def reject(repo_id: str, suggestion_id: int, state: AppState = Depends(get_state)) -> dict:
    repo_or_404(state, repo_id)

    def write() -> None:
        with state.db.repo(repo_id) as conn:
            row = get_suggestion(conn, suggestion_id)
            if row is None:
                raise HTTPException(404, "Unknown suggestion.")
            if row["status"] == "accepted":
                raise HTTPException(409, "It was already applied.")
            conn.execute(
                "UPDATE doc_suggestions SET status = 'rejected' WHERE id = ?", (suggestion_id,)
            )

    await asyncio.to_thread(write)
    return {"id": suggestion_id, "status": "rejected"}


@router.post("/docs/suggestions/{suggestion_id}/accept")
async def accept_one(
    repo_id: str, suggestion_id: int, state: AppState = Depends(get_state)
) -> dict:
    result = await _accept(state, repo_id, [suggestion_id])
    only = result["results"][0]
    if only["status"] != "accepted":
        raise HTTPException(409, only["error"])
    return result


@router.post("/docs/accept")
async def accept_many(
    repo_id: str, body: AcceptRequest, state: AppState = Depends(get_state)
) -> dict:
    """Accept several suggestions at once (the UI's "Accept all valid")."""
    return await _accept(state, repo_id, body.ids)


@router.post("/docs/suggestions/{suggestion_id}/regenerate", status_code=202)
async def regenerate(
    repo_id: str, suggestion_id: int, state: AppState = Depends(get_state)
) -> dict:
    repo = repo_or_404(state, repo_id)
    job = state.doc_jobs.get(repo_id)
    if job and job["status"] == "running":
        raise HTTPException(409, "Suggestions are already being generated.")

    def read():
        with state.db.repo(repo_id) as conn:
            row = get_suggestion(conn, suggestion_id)
            if row is None:
                raise HTTPException(404, "Unknown suggestion.")
            return conn.execute(
                "SELECT * FROM symbols WHERE file_path = ? AND qualified_name = ? AND kind = ?"
                " AND has_doc = 0 ORDER BY ABS(start_line - ?) LIMIT 1",
                (row["file_path"], row["qualified_name"], row["kind"], row["start_line"]),
            ).fetchall()

    symbols = await asyncio.to_thread(read)
    if not symbols:
        raise HTTPException(409, "That function changed. Re-analyze the repository first.")
    _start_job(state, repo_id, Path(repo["path"]), symbols)
    return state.doc_jobs[repo_id]


# ---------------------------------------------------------------- internals ---------------


async def _accept(state: AppState, repo_id: str, ids: list[int]) -> dict:
    repo = repo_or_404(state, repo_id)
    root = Path(repo["path"])

    def write():
        with state.db.repo(repo_id) as conn:
            return accept(conn, root, ids)

    results, written = await asyncio.to_thread(write)
    reindexed = False
    if written:
        reindexed = await asyncio.to_thread(_reindex, state, repo_id, root)
        if reindexed:

            def rebase():
                with state.db.repo(repo_id) as conn:
                    rebase_pending(conn, root, written)

            await asyncio.to_thread(rebase)
    return {"results": results, "files": len(written), "reindexed": reindexed}


def _reindex(state: AppState, repo_id: str, root: Path) -> bool:
    """Incremental re-index: only the rewritten files have a new hash, so only they are redone."""
    job = state.index_jobs.get(repo_id)
    if job and job.status in RUNNING_STATUSES:
        return False
    try:
        embedder = state.embedder
    except Exception:
        return False
    progress = IndexProgress()
    state.index_jobs[repo_id] = progress
    index_repository(
        state.db, repo_id, root, embedder, progress,
        batch_size=state.config.embedding_batch_size,
        max_bytes=state.config.max_file_bytes,
        analyzers=state.analyzers,
    )  # fmt: skip
    state.invalidate_vectors(repo_id)
    return progress.status == "done"


def _start_job(state: AppState, repo_id: str, root: Path, symbols: list) -> None:
    state.doc_jobs[repo_id] = {
        "status": "running",
        "total": len(symbols),
        "done": 0,
        "failed": 0,
        "current": None,
        "started_at": time.time(),
        "error": None,
    }
    asyncio.get_running_loop().create_task(_generate(state, repo_id, root, symbols))


async def _generate(state: AppState, repo_id: str, root: Path, symbols: list) -> None:
    job = state.doc_jobs[repo_id]
    style = _style(state)
    model = state.chat_model()
    try:
        graph = await asyncio.to_thread(_load_graph, state, repo_id)
        for symbol in symbols:
            job["current"] = symbol["qualified_name"]
            lines, digest, error = await _suggest_one(state, model, root, graph, symbol, style)
            await asyncio.to_thread(_store, state, repo_id, symbol, lines, style, digest, error)
            job["done"] += 1
            job["failed"] += 1 if error else 0
        job["status"] = "done"
    except Exception as exc:  # the job as a whole, not one function
        log.warning("Docstring generation failed: %s", exc)
        job["status"], job["error"] = "error", str(exc)
    job["current"] = None


def _load_graph(state: AppState, repo_id: str) -> CodeGraph:
    with state.db.repo(repo_id) as conn:
        return CodeGraph(conn)


def _store(state, repo_id, symbol, lines, style, digest, error) -> None:
    with state.db.repo(repo_id) as conn:
        save_suggestion(conn, symbol, lines, style, digest, error)


async def _suggest_one(state, model, root, graph, symbol, style):
    """(comment lines, file hash, error). Two attempts; a bad answer is a failure, not a guess."""
    language, line = symbol["language"], symbol["start_line"]
    try:
        source, digest = await asyncio.to_thread(
            source_of, root, symbol["file_path"], line, symbol["end_line"]
        )
        # Fail early, before spending model time, when this place cannot take a docstring.
        data = await asyncio.to_thread((root / symbol["file_path"]).read_bytes)
        await asyncio.to_thread(apply_edits, data, language, [(line, ["x"])])
    except (DocError, OSError) as exc:
        return None, "", str(exc)

    prompt = build_prompt(
        language, symbol["kind"], symbol["signature"] or symbol["name"], source,
        caller_names(graph, symbol["id"]),
    )  # fmt: skip
    last = "The model gave no usable answer."
    for _ in range(2):
        try:
            raw = await asyncio.wait_for(
                state.llm.complete([{"role": "user", "content": prompt}], model),
                timeout=LLM_TIMEOUT,
            )
            doc = parse_answer(raw, symbol["signature"] or "")
            lines = render_lines(doc, language, symbol["name"], style)
            await asyncio.to_thread(apply_edits, data, language, [(line, lines)])
            return lines, digest, None
        except DocError as exc:
            last = str(exc)
        except Exception as exc:  # the model is unreachable or timed out
            last = f"The model did not answer: {exc}" if str(exc) else "The model timed out."
    return None, digest, last
