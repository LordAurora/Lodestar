"""Insights API: (re)run the analysis and read its results.

The analysis itself runs inside indexing (see ``indexer.py``), so this module only
adds the "Re-analyze" action for repositories indexed before Insights existed, and
read-only endpoints that query the tables the analyzers fill.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.analysis.env import generate_example, is_dotenv_path, list_env, read_dotenv_keys
from app.chunking import detect_language
from app.indexer import RUNNING_STATUSES, IndexProgress, analyze_repository
from app.state import AppState, get_state

router = APIRouter(prefix="/api/repos/{repo_id}")

# Files the code preview refuses to show, even though they are inside the repository.
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}

# Overview counters. Each Insights feature adds its own line here.
OVERVIEW_COUNTS = {
    "symbols": "SELECT COUNT(*) FROM symbols",
    "tests": "SELECT COUNT(*) FROM symbols WHERE is_test = 1",
    "undocumented": (
        "SELECT COUNT(*) FROM symbols WHERE has_doc = 0 AND is_test = 0 AND kind != 'class'"
        " AND name NOT LIKE '\\_%' ESCAPE '\\'"
    ),
    "env_vars": "SELECT COUNT(DISTINCT name) FROM env_vars",
    "debt_items": "SELECT COUNT(*) FROM debt_items",
    "call_edges": "SELECT COUNT(*) FROM symbol_references WHERE to_symbol_id IS NOT NULL",
    "imports": "SELECT COUNT(*) FROM imports WHERE resolved_file_path IS NOT NULL",
}


def repo_or_404(state: AppState, repo_id: str) -> dict:
    repo = state.db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(404, "Repository not found.")
    return repo


@router.post("/analyze", status_code=202)
async def analyze(repo_id: str, state: AppState = Depends(get_state)) -> dict:
    """Re-run the Insights analysis for every file (progress uses the indexing stream)."""
    repo = repo_or_404(state, repo_id)
    if not Path(repo["path"]).is_dir():
        raise HTTPException(422, f"The folder {repo['path']} no longer exists.")
    job = state.index_jobs.get(repo_id)
    if job and job.status in RUNNING_STATUSES:
        return {"job_id": repo_id, "status": job.status}
    progress = IndexProgress()
    state.index_jobs[repo_id] = progress
    asyncio.get_running_loop().run_in_executor(
        None, analyze_repository, state.db, repo_id, Path(repo["path"]), progress, True,
        state.analyzers,
    )  # fmt: skip
    return {"job_id": repo_id, "status": progress.status}


@router.get("/insights/status")
async def insights_status(repo_id: str, state: AppState = Depends(get_state)) -> dict:
    """When the repo was last analyzed and how much was found (for the overview card)."""
    repo_or_404(state, repo_id)

    def read() -> dict:
        with state.db.repo(repo_id) as conn:
            analyzed = {
                r["analyzer"]: r["last_analyzed_at"]
                for r in conn.execute("SELECT analyzer, last_analyzed_at FROM analysis_state")
            }
            counts = {key: conn.execute(sql).fetchone()[0] for key, sql in OVERVIEW_COUNTS.items()}
        return {
            "analyzed": bool(analyzed),
            "last_analyzed_at": max(analyzed.values(), default=None),
            "analyzers": analyzed,
            "counts": counts,
        }

    return await asyncio.to_thread(read)


@router.get("/env")
async def env_variables(repo_id: str, q: str = "", state: AppState = Depends(get_state)) -> dict:
    """Every environment variable the code reads, with all usage locations."""
    repo = repo_or_404(state, repo_id)

    def read() -> dict:
        declared = read_dotenv_keys(Path(repo["path"]))  # names only, never values
        with state.db.repo(repo_id) as conn:
            variables = list_env(conn, declared, q)
        return {"variables": variables, "total": len(variables)}

    return await asyncio.to_thread(read)


@router.get("/env/example")
async def env_example(repo_id: str, state: AppState = Depends(get_state)) -> dict:
    """A generated `.env.example` (secrets are left empty)."""
    repo_or_404(state, repo_id)

    def read() -> dict:
        with state.db.repo(repo_id) as conn:
            variables = list_env(conn)
        return {"text": generate_example(variables), "count": len(variables)}

    return await asyncio.to_thread(read)


@router.get("/file")
async def file_preview(
    repo_id: str,
    path: str,
    start: int = 1,
    end: int | None = None,
    context: int = 30,
    state: AppState = Depends(get_state),
) -> dict:
    """A file's lines around ``start``-``end``, in the shape the code drawer already reads.

    Insights rows point at files and lines rather than at indexed chunks. The path must
    stay inside the repository, and env files and key material are never served.
    """
    repo = repo_or_404(state, repo_id)
    root = Path(repo["path"]).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "File not found in this repository.")
    if is_dotenv_path(path) or target.suffix.lower() in SECRET_SUFFIXES:
        raise HTTPException(403, "This kind of file is never shown, because it may hold secrets.")

    def read() -> dict:
        lines = target.read_text("utf-8", errors="replace").splitlines()
        first_marked = max(1, min(start, max(len(lines), 1)))
        last_marked = min(max(end or first_marked, first_marked), max(len(lines), 1))
        first = max(1, first_marked - context)
        last = min(len(lines), last_marked + context)
        language = detect_language(path)
        return {
            "chunk": {
                "chunk_id": None,
                "file_path": path,
                "language": language,
                "symbol_name": None,
                "symbol_kind": "file",
                "start_line": first_marked,
                "end_line": last_marked,
            },
            "language": language,
            "first_line": first,
            "lines": lines[first - 1 : last],
            "stale": False,
            "absolute_path": str(target),
        }

    return await asyncio.to_thread(read)
