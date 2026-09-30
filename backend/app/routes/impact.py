"""Impact analysis API: the symbol picker, the impact report and its plain-language summary."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.analysis.impact import (
    compute_impact,
    fallback_summary,
    search_symbols,
    summary_facts,
    summary_messages,
    usable_summary,
)
from app.routes.insights import repo_or_404
from app.state import AppState, get_state

router = APIRouter(prefix="/api/repos/{repo_id}")


@router.get("/symbols")
async def symbols(
    repo_id: str,
    q: str = "",
    kind: str = "",
    limit: int = 30,
    state: AppState = Depends(get_state),
) -> dict:
    """Search functions, methods and classes by name (exact, prefix, substring or fuzzy)."""
    repo_or_404(state, repo_id)
    if kind not in {"", "function", "method", "class"}:
        raise HTTPException(422, "kind must be function, method or class.")

    def read() -> list[dict]:
        with state.db.repo(repo_id) as conn:
            return search_symbols(conn, q, kind, max(1, min(limit, 100)))

    return {"symbols": await asyncio.to_thread(read)}


def _report(state: AppState, repo_id: str, symbol_id: int, depth: int) -> dict:
    with state.db.repo(repo_id) as conn:
        report = compute_impact(conn, symbol_id, depth)
    if report is None:
        raise HTTPException(404, "Symbol not found. The repository may have been re-analyzed.")
    return report


@router.get("/impact/{symbol_id}")
async def impact(
    repo_id: str, symbol_id: int, depth: int = 3, state: AppState = Depends(get_state)
) -> dict:
    """What may break if this symbol changes: callers by depth, affected tests, blast radius."""
    repo_or_404(state, repo_id)
    return await asyncio.to_thread(_report, state, repo_id, symbol_id, depth)


@router.post("/impact/{symbol_id}/summary")
async def impact_summary(
    repo_id: str, symbol_id: int, depth: int = 3, state: AppState = Depends(get_state)
):
    """Stream a short risk summary written by the chat model from the computed facts only.

    Events: ``token`` (repeated) then ``done`` with the final text. If the model is not
    available or its text is unusable, ``done`` carries a deterministic summary instead
    (``fallback: true``), so the reader always gets something.
    """
    repo_or_404(state, repo_id)
    report = await asyncio.to_thread(_report, state, repo_id, symbol_id, depth)
    messages = summary_messages(summary_facts(report))

    async def events():
        parts: list[str] = []
        reason = None
        try:
            async for token in state.llm.stream_chat(messages, state.chat_model()):
                parts.append(token)
                yield {"event": "token", "data": json.dumps({"text": token})}
        except Exception as exc:  # Foundry not running, model not downloaded, ...
            reason = str(exc)
        text = usable_summary("".join(parts)) if reason is None else None
        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "text": text or fallback_summary(report),
                    "fallback": text is None,
                    "reason": reason or (None if text else "The model's answer was not usable."),
                }
            ),
        }

    return EventSourceResponse(events())
