"""RAG pipeline tests: the relevance threshold, streaming, citations and rewriting."""

from __future__ import annotations

import pytest

from app.config import RuntimeSettings
from app.indexer import IndexProgress, index_repository
from app.prompts import NO_ANSWER_MESSAGE
from app.rag import RagPipeline, parse_citations, strip_reasoning
from app.retrieval import Retriever


@pytest.fixture
def pipeline(db, sample_repo, fake_embedder, fake_llm):
    repo = db.add_repo(str(sample_repo), "repo")
    index_repository(db, repo["id"], sample_repo, fake_embedder, IndexProgress())
    return RagPipeline(Retriever(db, fake_embedder), fake_llm), repo["id"]


async def collect(gen) -> list[tuple[str, dict]]:
    return [item async for item in gen]


async def test_below_threshold_never_calls_the_llm(pipeline, fake_llm):
    rag, repo_id = pipeline
    settings = RuntimeSettings(relevance_threshold=0.99)
    events = await collect(rag.answer(repo_id, "verify token", settings, "model"))

    assert [e for e, _ in events] == ["retrieval", "no_answer"]
    no_answer = events[-1][1]
    assert no_answer["message"] == NO_ANSWER_MESSAGE
    assert no_answer["suggestions"]  # closest matches are still offered
    assert fake_llm.stream_calls == [] and fake_llm.complete_calls == []


async def test_above_threshold_streams_and_parses_citations(pipeline, fake_llm):
    rag, repo_id = pipeline
    settings = RuntimeSettings(relevance_threshold=0.0, top_k=3)
    events = await collect(rag.answer(repo_id, "verify token signature", settings, "model"))

    kinds = [e for e, _ in events]
    assert kinds[0] == "retrieval" and kinds[-1] == "done"
    assert kinds.count("token") > 1
    retrieval, done = events[0][1], events[-1][1]
    assert len(retrieval["chunks"]) == 3
    # [9] does not exist among 3 snippets and must be dropped.
    assert [c["n"] for c in done["citations"]] == [1, 2]
    assert done["citations"][0]["chunk_id"] == retrieval["chunks"][0]["chunk_id"]

    # The prompt labels snippets as "[n] path:start-end".
    prompt = fake_llm.stream_calls[0][-1]["content"]
    first = retrieval["chunks"][0]
    assert f"[1] {first['file_path']}:{first['start_line']}-{first['end_line']}" in prompt


async def test_follow_up_questions_are_rewritten_before_retrieval(pipeline, fake_llm):
    rag, repo_id = pipeline
    history = [
        {"role": "user", "content": "How are tokens signed?"},
        {"role": "assistant", "content": "With HMAC [1]."},
    ]
    settings = RuntimeSettings(relevance_threshold=0.0)
    events = await collect(rag.answer(repo_id, "and verified?", settings, "model", history))
    assert events[0][1]["query"] == "How is the session token verified?"
    assert "How are tokens signed?" in fake_llm.complete_calls[0][0]["content"]


def test_parse_citations_formats():
    assert parse_citations("A [1]. B [2][3]. C [1, 4].", n_sources=4) == [1, 2, 3, 4]
    assert parse_citations("See [0] and [7] and arr[i]", n_sources=3) == []
    assert parse_citations("x [2] y [2] z [1]", n_sources=2) == [2, 1]


def test_strip_reasoning_removes_think_blocks():
    assert strip_reasoning("<think>hmm</think>Answer [1]") == "Answer [1]"
    assert strip_reasoning("<think>unfinished") == ""
