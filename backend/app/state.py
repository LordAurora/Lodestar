"""Application state: one object that owns every long-lived service.

Routes receive it through FastAPI's dependency injection (``get_state``), and
tests build their own with a fake embedder and a fake LLM. Expensive things
(the embedding model) are created lazily, on first use.
"""

from __future__ import annotations

import threading

from fastapi import Request

from app.analysis.registry import default_analyzers
from app.config import Config, SettingsStore
from app.db import Database
from app.embeddings import Embedder, create_embedder
from app.foundry import FoundryLLM, FoundryService, LLMClient
from app.indexer import IndexProgress
from app.rag import RagPipeline
from app.retrieval import Retriever


class AppState:
    def __init__(
        self,
        config: Config,
        foundry: FoundryService | None = None,
        embedder: Embedder | None = None,
        llm: LLMClient | None = None,
    ):
        self.config = config
        self.db = Database(config.data_dir)
        self.settings = SettingsStore(config.data_dir / "settings.json")
        self.foundry = foundry or FoundryService(device=config.device)
        self.llm: LLMClient = llm or FoundryLLM(self.foundry)
        self.index_jobs: dict[str, IndexProgress] = {}
        self.debt_jobs: dict[str, dict] = {}  # topic clustering jobs, by repo id
        self._embedder = embedder
        self._embedder_error: str | None = None
        self._retriever: Retriever | None = None
        self._lock = threading.Lock()

    @property
    def embedder(self) -> Embedder:
        """The embedding model, loaded on first use (this can take a few seconds)."""
        with self._lock:
            if self._embedder is None:
                try:
                    self._embedder = create_embedder(
                        self.config.embedding_model,
                        self.foundry,
                        self.config.models_dir,
                        offline=self.config.offline,
                    )
                    self._embedder_error = None
                except Exception as exc:
                    self._embedder_error = str(exc)
                    raise
            return self._embedder

    def embedder_status(self) -> dict:
        return {
            "name": self._embedder.name if self._embedder else None,
            "loaded": self._embedder is not None,
            "error": self._embedder_error,
        }

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever(self.db, self.embedder)
        return self._retriever

    @property
    def analyzers(self) -> list:
        """The Insights analyzers, configured from the settings (for example the debt tags)."""
        return default_analyzers(self.config.debt_tags, self.config.duplicate_block_size)

    def invalidate_vectors(self, repo_id: str) -> None:
        """Forget a repo's cached embedding matrix (after re-indexing or deletion)."""
        if self._retriever is not None:
            self._retriever.cache.invalidate(repo_id)

    @property
    def rag(self) -> RagPipeline:
        return RagPipeline(self.retriever, self.llm)

    def chat_model(self) -> str:
        return self.settings.get().chat_model or self.config.default_chat_model


def get_state(request: Request) -> AppState:
    return request.app.state.lodestar
