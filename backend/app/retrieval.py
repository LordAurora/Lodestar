"""Retrieval: find the chunks most relevant to a question.

We combine two very different search methods:

* **Vector search** compares the meaning of the question with the meaning of
  each chunk (cosine similarity of embeddings). Good at "how does login work?"
* **Keyword search** (BM25 via SQLite FTS5) matches exact words. Good at
  "where is `parseConfig` defined?"

Their scores are not comparable, so we merge the two *rankings* with
Reciprocal Rank Fusion (RRF): each chunk earns ``1 / (k + rank)`` from every
list it appears in. Chunks that rank well in both lists float to the top.
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass

import numpy as np

from app.db import Database
from app.embeddings import Embedder

CANDIDATES = 20  # N: how many results each search method contributes
RRF_K = 60  # the standard RRF constant; dampens the weight of top ranks

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "how",
    "i", "in", "is", "it", "me", "of", "on", "or", "show", "that", "the", "this", "to", "what",
    "when", "where", "which", "who", "why", "with", "work", "works", "project", "code", "used",
}  # fmt: skip


def split_identifier(word: str) -> list[str]:
    """``getUserName`` / ``get_user_name`` -> ``["get", "user", "name"]``."""
    parts: list[str] = []
    for piece in word.split("_"):
        parts.extend(p.lower() for p in _CAMEL.findall(piece))
    return parts


def tokenize_for_search(text: str) -> str:
    """Rewrite text so FTS5 can match both whole identifiers and their parts.

    ``parseHTTPResponse`` becomes ``parsehttpresponse parse http response``.
    Used for chunks at index time and for questions at query time.
    """
    out: list[str] = []
    for word in _IDENTIFIER.findall(text):
        lower = word.lower()
        parts = split_identifier(word)
        out.append(lower)
        if len(parts) > 1:
            out.extend(parts)
    return " ".join(out)


def fts_query(question: str) -> str | None:
    terms = [t for t in dict.fromkeys(tokenize_for_search(question).split()) if t not in STOPWORDS]
    if not terms:
        return None
    return " OR ".join(f'"{t}"' for t in terms)


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Merge several ranked lists of ids. Returns (id, score) sorted best first."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


@dataclass
class RetrievedChunk:
    id: int
    file_path: str
    language: str
    symbol_name: str | None
    symbol_kind: str
    start_line: int
    end_line: int
    content: str
    score: float  # fused (or single-method) score used for ranking
    cosine: float  # raw cosine similarity with the question

    def to_dict(self, repo_id: str) -> dict:
        data = asdict(self)
        data["chunk_id"] = f"{repo_id}-{self.id}"
        return data


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    best_cosine: float


class _VectorCache:
    """Keeps each repo's embedding matrix in memory between questions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def get(self, db: Database, repo_id: str) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            if repo_id not in self._data:
                with db.repo(repo_id) as conn:
                    rows = conn.execute(
                        "SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL"
                    ).fetchall()
                ids = np.array([r[0] for r in rows], dtype=np.int64)
                matrix = (
                    np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                    if rows
                    else np.zeros((0, 1), dtype=np.float32)
                )
                self._data[repo_id] = (ids, matrix)
            return self._data[repo_id]

    def invalidate(self, repo_id: str) -> None:
        with self._lock:
            self._data.pop(repo_id, None)


class Retriever:
    def __init__(self, db: Database, embedder: Embedder):
        self.db = db
        self.embedder = embedder
        self.cache = _VectorCache()

    def vector_search(self, repo_id: str, query_vec: np.ndarray, n: int) -> list[tuple[int, float]]:
        ids, matrix = self.cache.get(self.db, repo_id)
        if len(ids) == 0:
            return []
        sims = matrix @ query_vec  # vectors are normalised, so this is cosine similarity
        top = np.argsort(-sims)[:n]
        return [(int(ids[i]), float(sims[i])) for i in top]

    def keyword_search(self, repo_id: str, question: str, n: int) -> list[int]:
        query = fts_query(question)
        if query is None:
            return []
        with self.db.repo(repo_id) as conn:
            rows = conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?"
                " ORDER BY bm25(chunks_fts) LIMIT ?",
                (query, n),
            ).fetchall()
        return [r[0] for r in rows]

    def search(
        self, repo_id: str, question: str, top_k: int = 6, mode: str = "hybrid"
    ) -> RetrievalResult:
        """Run retrieval. ``mode`` is ``hybrid``, ``vector`` or ``bm25``."""
        query_vec = self.embedder.embed_query(question)
        vector_hits = self.vector_search(repo_id, query_vec, CANDIDATES)
        best_cosine = vector_hits[0][1] if vector_hits else 0.0

        if mode == "vector":
            ranked = vector_hits
        elif mode == "bm25":
            ranked = reciprocal_rank_fusion([self.keyword_search(repo_id, question, CANDIDATES)])
        else:
            keyword_ids = self.keyword_search(repo_id, question, CANDIDATES)
            ranked = reciprocal_rank_fusion([[i for i, _ in vector_hits], keyword_ids])

        top = ranked[:top_k]
        chunks = self._load(repo_id, top, query_vec)
        return RetrievalResult(chunks=chunks, best_cosine=best_cosine)

    def _load(self, repo_id, ranked, query_vec) -> list[RetrievedChunk]:
        if not ranked:
            return []
        ids = [i for i, _ in ranked]
        with self.db.repo(repo_id) as conn:
            marks = ",".join("?" * len(ids))
            rows = {
                r["id"]: r for r in conn.execute(f"SELECT * FROM chunks WHERE id IN ({marks})", ids)
            }
        out = []
        for chunk_id, score in ranked:
            row = rows.get(chunk_id)
            if row is None:
                continue
            vec = np.frombuffer(row["embedding"], dtype=np.float32)
            out.append(
                RetrievedChunk(
                    id=chunk_id,
                    file_path=row["file_path"],
                    language=row["language"],
                    symbol_name=row["symbol_name"],
                    symbol_kind=row["symbol_kind"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content=row["content"],
                    score=round(float(score), 5),
                    cosine=round(float(vec @ query_vec), 4),
                )
            )
        return out
