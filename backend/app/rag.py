"""The RAG pipeline: retrieve -> check relevance -> generate -> cite.

``RagPipeline.answer`` is an async generator of ``(event, data)`` pairs that
the chat endpoint forwards to the browser as Server-Sent Events:

1. ``retrieval``  the chunks we found and their scores
2. ``token``      one piece of the streamed answer (repeated)
3. ``done``       the full answer plus the parsed citations

or, when nothing relevant was found, ``retrieval`` followed by ``no_answer``.

The most important rule lives here: if the best cosine similarity is below
the relevance threshold, we **do not call the LLM at all**. A small model
handed irrelevant snippets will happily invent an answer; refusing up front
is both more honest and faster.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from app.config import RuntimeSettings
from app.foundry import LLMClient
from app.prompts import NO_ANSWER_MESSAGE, build_answer_messages, build_rewrite_messages
from app.retrieval import Retriever

_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)


def parse_citations(answer: str, n_sources: int) -> list[int]:
    """Return cited snippet numbers in order of first appearance.

    Understands ``[1]``, ``[1][2]`` and ``[1, 2]``; ignores numbers that do
    not match a provided snippet (the model sometimes invents ``[7]``).
    """
    seen: dict[int, None] = {}
    for match in _CITATION.finditer(answer):
        for part in match.group(1).split(","):
            n = int(part)
            if 1 <= n <= n_sources:
                seen.setdefault(n, None)
    return list(seen)


def strip_reasoning(text: str) -> str:
    """Reasoning models (e.g. Qwen3) wrap their thoughts in <think> tags; drop them."""
    return _THINK.sub("", text).strip()


class RagPipeline:
    def __init__(self, retriever: Retriever, llm: LLMClient):
        self.retriever = retriever
        self.llm = llm

    async def rewrite_question(self, question: str, history: list[dict], model: str) -> str:
        """Turn a follow-up ("and where is it called?") into a standalone question."""
        if not history:
            return question
        rewritten = strip_reasoning(
            await self.llm.complete(build_rewrite_messages(question, history), model)
        )
        rewritten = rewritten.strip().strip('"').splitlines()[0] if rewritten.strip() else ""
        return rewritten or question

    async def answer(
        self,
        repo_id: str,
        question: str,
        settings: RuntimeSettings,
        model: str,
        history: list[dict] | None = None,
    ) -> AsyncIterator[tuple[str, dict]]:
        query = await self.rewrite_question(question, history or [], model)

        # Embedding + search are CPU work: keep them off the event loop.
        mode = "hybrid" if settings.hybrid_search else "vector"
        result = await asyncio.to_thread(
            self.retriever.search, repo_id, query, settings.top_k, mode
        )
        chunks = [c.to_dict(repo_id) for c in result.chunks]
        yield "retrieval", {"query": query, "chunks": chunks, "best_cosine": result.best_cosine}

        if not result.chunks or result.best_cosine < settings.relevance_threshold:
            yield (
                "no_answer",
                {
                    "message": NO_ANSWER_MESSAGE,
                    "best_cosine": result.best_cosine,
                    "threshold": settings.relevance_threshold,
                    "suggestions": chunks[:3],
                },
            )
            return

        parts: list[str] = []
        async for token in self.llm.stream_chat(build_answer_messages(query, result.chunks), model):
            parts.append(token)
            yield "token", {"text": token}

        answer = strip_reasoning("".join(parts))
        cited = parse_citations(answer, len(chunks))
        yield (
            "done",
            {
                "answer": answer,
                "citations": [{"n": n, "chunk_id": chunks[n - 1]["chunk_id"]} for n in cited],
            },
        )
