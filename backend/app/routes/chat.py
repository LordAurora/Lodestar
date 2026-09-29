"""Conversations and the streaming chat endpoint."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.foundry import FoundryUnavailable
from app.state import AppState, get_state

router = APIRouter(prefix="/api/repos/{repo_id}")

HISTORY_TURNS = 2  # how many previous question/answer pairs feed the query rewrite


class ChatIn(BaseModel):
    question: str
    conversation_id: str | None = None
    regenerate: bool = False


class RenameIn(BaseModel):
    title: str


def _repo_or_404(state: AppState, repo_id: str) -> dict:
    repo = state.db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(404, "Repository not found.")
    return repo


@router.get("/conversations")
async def list_conversations(repo_id: str, state: AppState = Depends(get_state)) -> list[dict]:
    _repo_or_404(state, repo_id)
    return state.db.list_conversations(repo_id)


@router.get("/conversations/{conv_id}/messages")
async def list_messages(repo_id: str, conv_id: str, state: AppState = Depends(get_state)):
    _repo_or_404(state, repo_id)
    messages = state.db.list_messages(repo_id, conv_id)
    for m in messages:
        m["meta"] = json.loads(m.pop("meta_json") or "{}")
    return messages


@router.patch("/conversations/{conv_id}")
async def rename_conversation(
    repo_id: str, conv_id: str, body: RenameIn, state: AppState = Depends(get_state)
) -> dict:
    title = body.title.strip()[:120]
    if not title or not state.db.rename_conversation(repo_id, conv_id, title):
        raise HTTPException(404, "Conversation not found.")
    return {"id": conv_id, "title": title}


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(repo_id: str, conv_id: str, state: AppState = Depends(get_state)):
    if not state.db.delete_conversation(repo_id, conv_id):
        raise HTTPException(404, "Conversation not found.")


@router.post("/chat")
async def chat(repo_id: str, body: ChatIn, state: AppState = Depends(get_state)):
    """Answer a question as Server-Sent Events (see app/rag.py for the event order)."""
    repo = _repo_or_404(state, repo_id)
    question = body.question.strip()
    if not question:
        raise HTTPException(422, "Type a question first.")
    if not repo["chunk_count"]:
        raise HTTPException(409, "This repository has not been indexed yet. Click Re-index first.")

    db = state.db
    if body.conversation_id:
        conv_id = body.conversation_id
        if not db.conversation_exists(repo_id, conv_id):
            raise HTTPException(404, "Conversation not found. It may have been deleted.")
        if body.regenerate:
            db.delete_last_exchange(repo_id, conv_id)
    else:
        conv_id = db.create_conversation(repo_id, question[:80])["id"]

    previous = db.list_messages(repo_id, conv_id)
    history = [{"role": m["role"], "content": m["content"]} for m in previous[-2 * HISTORY_TURNS :]]
    db.add_message(repo_id, conv_id, "user", question)
    settings = state.settings.get()
    model = state.chat_model()

    async def events():
        sources: list[dict] = []
        query = question
        try:
            async for event, data in state.rag.answer(repo_id, question, settings, model, history):
                if event == "retrieval":
                    sources, query = data["chunks"], data["query"]
                    data["conversation_id"] = conv_id
                elif event == "done":
                    meta = {"sources": sources, "citations": data["citations"], "query": query}
                    data["message_id"] = db.add_message(
                        repo_id, conv_id, "assistant", data["answer"], json.dumps(meta)
                    )
                elif event == "no_answer":
                    meta = {"no_answer": True, "suggestions": data["suggestions"], "query": query}
                    db.add_message(repo_id, conv_id, "assistant", data["message"], json.dumps(meta))
                yield {"event": event, "data": json.dumps(data)}
        except asyncio.CancelledError:  # the browser went away (e.g. user pressed Stop)
            raise
        except FoundryUnavailable as exc:
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
        except Exception as exc:
            message = f"Something went wrong while answering: {exc}"
            if "connect" in str(exc).lower():
                message = (
                    "Could not reach Foundry Local. Make sure it is installed and running, "
                    "then try again."
                )
            yield {"event": "error", "data": json.dumps({"message": message})}

    return EventSourceResponse(events())
