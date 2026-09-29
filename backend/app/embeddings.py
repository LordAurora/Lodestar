"""Embeddings: turning text into vectors.

Everything else in Lodestar talks to the small ``Embedder`` interface below,
so the model behind it can be swapped via configuration:

* ``FoundryEmbedder`` runs an embedding model (``qwen3-embedding-0.6b``) inside
  Foundry Local, the same runtime that serves the chat model. This is the
  default because Foundry Local's catalog ships embedding models.
* ``FastEmbedEmbedder`` runs an ONNX model through the ``fastembed`` library.
  It prefers the code-aware ``jinaai/jina-embeddings-v2-base-code`` and falls
  back to the small general-purpose ``BAAI/bge-small-en-v1.5``.

All vectors are returned L2-normalised as float32, so cosine similarity is a
plain dot product.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.foundry import FoundryService

FASTEMBED_MODELS = ["jinaai/jina-embeddings-v2-base-code", "BAAI/bge-small-en-v1.5"]
FOUNDRY_EMBEDDING_MODEL = "qwen3-embedding-0.6b"

# Qwen3-Embedding is instruction-tuned: queries get a task description,
# documents are embedded as-is.
QWEN3_QUERY_TEMPLATE = (
    "Instruct: Given a question about a code base, retrieve the code snippets that answer it\n"
    "Query: {query}"
)


def normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


class Embedder(ABC):
    """Anything that can turn text into normalised vectors."""

    #: Human-readable model name, stored with each index so we can detect a model change.
    name: str

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed code chunks. Returns an array of shape (len(texts), dim)."""

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a user question. Returns a 1-D vector."""
        return self.embed_documents([text])[0]


class FastEmbedEmbedder(Embedder):
    """Local ONNX embeddings via ``fastembed``."""

    def __init__(self, cache_dir: Path, model_name: str | None = None, offline: bool = True):
        if offline:
            # Never reach Hugging Face at runtime; models are fetched by `make setup`.
            os.environ["HF_HUB_OFFLINE"] = "1"
        from fastembed import TextEmbedding

        candidates = [model_name] if model_name else FASTEMBED_MODELS
        errors = []
        for candidate in candidates:
            try:
                self._model = TextEmbedding(candidate, cache_dir=str(cache_dir))
                self.name = f"fastembed:{candidate}"
                return
            except Exception as exc:  # model not downloaded, unsupported, ...
                errors.append(f"{candidate}: {exc}")
        raise RuntimeError(
            "No fastembed model could be loaded. Run `make setup` to download one.\n"
            + "\n".join(errors)
        )

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return normalize(np.array(list(self._model.embed(texts))))


class FoundryEmbedder(Embedder):
    """Embeddings from a Foundry Local embedding model (OpenAI-compatible endpoint)."""

    def __init__(
        self, foundry: FoundryService, alias: str = FOUNDRY_EMBEDDING_MODEL, allow_download=False
    ):
        self._foundry = foundry
        # Loads the model into memory (and downloads it first if allowed).
        self._model_id = foundry.ensure_model(alias, allow_download=allow_download)
        self._alias = alias
        self.name = f"foundry:{alias}"

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        client = self._foundry.sync_client()
        response = client.embeddings.create(model=self._model_id, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return normalize(np.array([item.embedding for item in ordered]))

    def embed_query(self, text: str) -> np.ndarray:
        if self._alias.startswith("qwen3-embedding"):
            text = QWEN3_QUERY_TEMPLATE.format(query=text)
        return self.embed_documents([text])[0]


def create_embedder(setting: str, foundry: FoundryService, cache_dir: Path, offline: bool):
    """Build the embedder named by ``LODESTAR_EMBEDDING_MODEL``.

    * ``auto`` (default): Foundry Local's embedding model if it is already
      downloaded, else fastembed.
    * ``foundry`` or ``foundry:<alias>``: Foundry Local only (downloads the model if needed).
    * ``fastembed`` or ``fastembed:<hf-model-name>``: fastembed only.
    """
    kind, _, model = setting.partition(":")
    if kind in ("auto", "foundry"):
        try:
            return FoundryEmbedder(
                foundry, model or FOUNDRY_EMBEDDING_MODEL, allow_download=kind == "foundry"
            )
        except Exception:
            if kind == "foundry":
                raise
    return FastEmbedEmbedder(cache_dir, model or None, offline=offline)
