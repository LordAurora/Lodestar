"""Repositories: register, list, delete, index (with live progress), preview chunks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.chunking import detect_language
from app.indexer import IndexProgress, index_repository
from app.state import AppState, get_state

router = APIRouter(prefix="/api")


class RepoIn(BaseModel):
    path: str


def _repo_or_404(state: AppState, repo_id: str) -> dict:
    repo = state.db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(404, "Repository not found.")
    return repo


def _with_job(state: AppState, repo: dict) -> dict:
    job = state.index_jobs.get(repo["id"])
    repo["indexing"] = job is not None and job.status in ("pending", "scanning", "embedding")
    repo["last_error"] = job.error if job else None
    return repo


@router.post("/repos", status_code=201)
async def add_repo(body: RepoIn, state: AppState = Depends(get_state)) -> dict:
    raw = body.path.strip().strip('"')
    if not raw:
        raise HTTPException(422, "Enter the path of a folder on this computer.")
    path = Path(raw).expanduser()
    if not path.exists():
        raise HTTPException(
            422, f"The folder '{raw}' does not exist. Check the path and try again."
        )
    if not path.is_dir():
        raise HTTPException(422, f"'{raw}' is a file. Enter the path of the repository folder.")
    path = path.resolve()
    return _with_job(state, state.db.add_repo(str(path), path.name or str(path)))


@router.get("/repos")
async def list_repos(state: AppState = Depends(get_state)) -> list[dict]:
    return [_with_job(state, r) for r in state.db.list_repos()]


@router.delete("/repos/{repo_id}", status_code=204)
async def delete_repo(repo_id: str, state: AppState = Depends(get_state)) -> None:
    _repo_or_404(state, repo_id)
    job = state.index_jobs.get(repo_id)
    if job and job.status in ("scanning", "embedding"):
        raise HTTPException(409, "This repository is being indexed. Wait for it to finish.")
    if state._retriever:
        state.retriever.cache.invalidate(repo_id)
    state.index_jobs.pop(repo_id, None)
    state.db.delete_repo(repo_id)


@router.post("/repos/{repo_id}/index", status_code=202)
async def start_index(repo_id: str, state: AppState = Depends(get_state)) -> dict:
    repo = _repo_or_404(state, repo_id)
    if not Path(repo["path"]).is_dir():
        raise HTTPException(422, f"The folder {repo['path']} no longer exists.")
    job = state.index_jobs.get(repo_id)
    if job and job.status in ("pending", "scanning", "embedding"):
        return {"job_id": repo_id, "status": job.status}

    progress = IndexProgress()
    state.index_jobs[repo_id] = progress

    def run() -> None:
        try:
            embedder = state.embedder  # may load the model on first use
        except Exception as exc:
            progress.status = "error"
            progress.error = f"The embedding model could not be loaded: {exc}"
            return
        index_repository(
            state.db,
            repo_id,
            Path(repo["path"]),
            embedder,
            progress,
            batch_size=state.config.embedding_batch_size,
            max_bytes=state.config.max_file_bytes,
        )
        state.retriever.cache.invalidate(repo_id)

    # Indexing is CPU- and IO-heavy: run it in a worker thread.
    asyncio.get_running_loop().run_in_executor(None, run)
    return {"job_id": repo_id, "status": progress.status}


@router.get("/repos/{repo_id}/index/stream")
async def index_stream(repo_id: str, state: AppState = Depends(get_state)):
    """Server-Sent Events: a ``progress`` snapshot every 300 ms, then ``done`` or ``error``."""
    _repo_or_404(state, repo_id)

    async def events():
        while True:
            job = state.index_jobs.get(repo_id)
            if job is None:
                yield {"event": "idle", "data": "{}"}
                return
            snapshot = job.snapshot()
            if job.status in ("done", "error"):
                if job.status == "done":
                    snapshot["repo"] = state.db.get_repo(repo_id)
                yield {"event": job.status, "data": json.dumps(snapshot)}
                return
            yield {"event": "progress", "data": json.dumps(snapshot)}
            await asyncio.sleep(0.3)

    return EventSourceResponse(events())


@router.get("/chunks/{chunk_id}")
async def get_chunk(chunk_id: str, context: int = 30, state: AppState = Depends(get_state)) -> dict:
    """A chunk plus ``context`` lines around it, for the source preview drawer."""
    repo_id, _, raw_id = chunk_id.rpartition("-")
    if not repo_id or not raw_id.isdigit():
        raise HTTPException(404, "Chunk not found.")
    repo = _repo_or_404(state, repo_id)
    with state.db.repo(repo_id) as conn:
        row = conn.execute("SELECT * FROM chunks WHERE id = ?", (int(raw_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "Chunk not found. The repository may have been re-indexed.")
    chunk = {k: row[k] for k in row.keys() if k != "embedding"}  # noqa: SIM118 (sqlite3.Row)
    chunk["chunk_id"] = chunk_id

    # Prefer the file on disk (for surrounding lines); fall back to the stored chunk.
    lines = row["content"].splitlines()
    first = row["start_line"]
    file_path = Path(repo["path"]) / row["file_path"]
    stale = False
    try:
        file_lines = file_path.read_text("utf-8", errors="replace").splitlines()
        start = max(1, row["start_line"] - context)
        end = min(len(file_lines), row["end_line"] + context)
        stale = file_lines[row["start_line"] - 1 : row["end_line"]] != lines
        if not stale:
            lines, first = file_lines[start - 1 : end], start
    except OSError:
        stale = True

    return {
        "chunk": chunk,
        "language": row["language"] or detect_language(row["file_path"]),
        "first_line": first,
        "lines": lines,
        "stale": stale,
        "absolute_path": str(file_path),
    }
