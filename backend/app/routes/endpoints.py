"""API endpoint catalog: the searchable table and its exports."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.analysis.endpoints.catalog import export_endpoints, list_endpoints
from app.routes.insights import repo_or_404
from app.state import AppState, get_state

router = APIRouter(prefix="/api/repos/{repo_id}")


@router.get("/endpoints")
async def endpoints(
    repo_id: str,
    method: str = "",
    q: str = "",
    framework: str = "",
    state: AppState = Depends(get_state),
) -> dict:
    """Detected HTTP routes, filtered by method, framework or a search text."""
    repo_or_404(state, repo_id)

    def read() -> dict:
        with state.db.repo(repo_id) as conn:
            return list_endpoints(conn, method, q, framework)

    return await asyncio.to_thread(read)


@router.get("/endpoints/export", response_class=PlainTextResponse)
async def export(
    repo_id: str,
    format: str = "json",
    method: str = "",
    q: str = "",
    framework: str = "",
    state: AppState = Depends(get_state),
) -> PlainTextResponse:
    repo_or_404(state, repo_id)
    if format not in {"json", "csv", "markdown"}:
        raise HTTPException(422, "format must be json, csv or markdown.")

    def read() -> list[dict]:
        with state.db.repo(repo_id) as conn:
            return list_endpoints(conn, method, q, framework)["endpoints"]

    body, media, extension = export_endpoints(await asyncio.to_thread(read), format)
    return PlainTextResponse(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="endpoints.{extension}"'},
    )
