"""Retrieval tests: tokenisation, Reciprocal Rank Fusion and hybrid search."""

from __future__ import annotations

import pytest

from app.indexer import IndexProgress, index_repository
from app.retrieval import (
    Retriever,
    fts_query,
    reciprocal_rank_fusion,
    split_identifier,
    tokenize_for_search,
)


def test_split_identifier_handles_camel_and_snake_case():
    assert split_identifier("getUserName") == ["get", "user", "name"]
    assert split_identifier("parse_HTTPResponse") == ["parse", "http", "response"]
    assert split_identifier("ID") == ["id"]


def test_tokenize_keeps_whole_identifier_and_parts():
    assert tokenize_for_search("verifyToken(x)") == "verifytoken verify token x"


def test_fts_query_drops_stopwords():
    assert fts_query("How does verifyToken work?") == '"verifytoken" OR "verify" OR "token"'
    assert fts_query("how does it work") is None


def test_rrf_rewards_items_ranked_well_in_both_lists():
    fused = reciprocal_rank_fusion([[1, 2, 3], [3, 1, 4]], k=60)
    ids = [i for i, _ in fused]
    assert ids[0] == 1  # rank 1 + rank 2
    assert ids[1] == 3  # rank 3 + rank 1
    assert set(ids) == {1, 2, 3, 4}
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)


def test_rrf_single_list_preserves_order():
    assert [i for i, _ in reciprocal_rank_fusion([[5, 7, 9]])] == [5, 7, 9]


@pytest.fixture
def indexed(db, sample_repo, fake_embedder):
    repo = db.add_repo(str(sample_repo), "repo")
    index_repository(db, repo["id"], sample_repo, fake_embedder, IndexProgress())
    return Retriever(db, fake_embedder), repo["id"]


@pytest.mark.parametrize("mode", ["vector", "bm25", "hybrid"])
def test_search_finds_the_right_file(indexed, mode):
    retriever, repo_id = indexed
    result = retriever.search(repo_id, "verify token signature", top_k=3, mode=mode)
    assert result.chunks[0].file_path == "auth.py"


def test_search_returns_fused_and_cosine_scores(indexed):
    retriever, repo_id = indexed
    result = retriever.search(repo_id, "cartTotal", top_k=4)
    assert result.best_cosine > 0
    first = result.chunks[0]
    assert first.file_path == "cart.js"
    assert 0 < first.score < 1 and -1 <= first.cosine <= 1
    assert first.to_dict(repo_id)["chunk_id"] == f"{repo_id}-{first.id}"
