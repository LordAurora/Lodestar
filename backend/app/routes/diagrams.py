"""Architecture diagrams: module dependencies and call flows, as Mermaid text."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.analysis.diagrams import MAX_FLOW_NODES, MAX_MODULE_NODES, flow_diagram, modules_diagram
from app.routes.insights import repo_or_404
from app.state import AppState, get_state

router = APIRouter(prefix="/api/repos/{repo_id}")


@router.get("/diagram")
async def diagram(
    repo_id: str,
    type: str = "modules",
    scope: str = "",
    symbol_id: int | None = None,
    endpoint_id: int | None = None,
    depth: int = 3,
    limit: int = 0,
    style: str = "flowchart",
    state: AppState = Depends(get_state),
) -> dict:
    """A Mermaid diagram.

    * ``type=modules``: folder or file dependencies; ``scope`` limits it to a folder.
    * ``type=flow``: the calls from ``symbol_id`` (or from an endpoint's handler with
      ``endpoint_id``); ``style`` is ``flowchart`` or ``sequence``.
    """
    repo_or_404(state, repo_id)
    if type not in {"modules", "flow"}:
        raise HTTPException(422, "type must be modules or flow.")
    if style not in {"flowchart", "sequence"}:
        raise HTTPException(422, "style must be flowchart or sequence.")

    def build() -> dict:
        with state.db.repo(repo_id) as conn:
            if type == "modules":
                return modules_diagram(conn, scope, max(5, min(limit or MAX_MODULE_NODES, 100)))
            start = symbol_id
            if endpoint_id is not None:
                row = conn.execute(
                    "SELECT handler_symbol_id FROM endpoints WHERE id = ?", (endpoint_id,)
                ).fetchone()
                if row is None:
                    raise HTTPException(
                        404, "Endpoint not found. The repository may have been re-analyzed."
                    )
                if row["handler_symbol_id"] is None:
                    raise HTTPException(
                        422,
                        "This endpoint's handler is an inline function: no flow to draw.",
                    )
                start = row["handler_symbol_id"]
            if start is None:
                raise HTTPException(422, "Give a symbol_id or an endpoint_id for a flow diagram.")
            cap = max(5, min(limit or MAX_FLOW_NODES, 60))
            result = flow_diagram(conn, start, depth, cap, style)
            if result is None:
                raise HTTPException(
                    404, "Symbol not found. The repository may have been re-analyzed."
                )
            return result

    return await asyncio.to_thread(build)


@router.get("/diagram/scopes")
async def scopes(repo_id: str, state: AppState = Depends(get_state)) -> dict:
    """Folders that can scope a module diagram (up to three levels deep)."""
    repo_or_404(state, repo_id)

    def read() -> list[str]:
        with state.db.repo(repo_id) as conn:
            paths = [r["path"] for r in conn.execute("SELECT path FROM files")]
        folders: set[str] = set()
        for path in paths:
            parts = path.split("/")[:-1]
            for depth in range(1, min(len(parts), 3) + 1):
                folders.add("/".join(parts[:depth]))
        return sorted(folders)

    return {"folders": await asyncio.to_thread(read)}
