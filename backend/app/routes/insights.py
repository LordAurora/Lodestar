"""Insights API: (re)run the analysis and read its results.

The analysis itself runs inside indexing (see ``indexer.py``), so this module only
adds the "Re-analyze" action for repositories indexed before Insights existed, and
read-only endpoints that query the tables the analyzers fill.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.indexer import RUNNING_STATUSES, IndexProgress, analyze_repository
from app.state import AppState, get_state

router = APIRouter(prefix="/api/repos/{repo_id}")

# Overview counters. Each Insights feature adds its own line here.
OVERVIEW_COUNTS = {
    "symbols": "SELECT COUNT(*) FROM symbols",
    "tests": "SELECT COUNT(*) FROM symbols WHERE is_test = 1",
    "undocumented": (
        "SELECT COUNT(*) FROM symbols WHERE has_doc = 0 AND is_test = 0 AND kind != 'class'"
        " AND name NOT LIKE '\\_%' ESCAPE '\\'"
    ),
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
        None, analyze_repository, state.db, repo_id, Path(repo["path"]), progress, True
    )
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
