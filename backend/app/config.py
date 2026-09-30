"""Configuration.

Two kinds of configuration live here:

* ``Config`` holds *startup* values read once from environment variables
  (or a ``.env`` file). Things like where data is stored, or which embedding
  model to load.
* ``RuntimeSettings`` holds values the user can change from the Settings page
  while the app is running (chat model, top-K, relevance threshold, hybrid
  search on/off). They are persisted to a small JSON file so they survive a
  restart.
"""

from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Config(BaseSettings):
    """Startup configuration. Every field can be set with a ``LODESTAR_`` env var."""

    model_config = SettingsConfigDict(
        env_prefix="LODESTAR_",
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # Where SQLite databases, settings and cached embedding models are stored.
    data_dir: Path = BACKEND_DIR / "data"

    host: str = "127.0.0.1"
    port: int = 8000

    # Embeddings. "auto" tries the code-aware model first and falls back to the
    # small general-purpose one if it cannot be loaded.
    embedding_model: str = "auto"
    embedding_batch_size: int = 32

    # When true, the embedding library is told never to reach the internet.
    # Models must then be downloaded once with `make setup`.
    offline: bool = True

    # When true, an audit hook blocks any outbound socket connection that is
    # not to a loopback address (see app/privacy.py).
    block_external_network: bool = True

    # Where models run: "auto" uses the GPU when its execution provider is installed,
    # "gpu" insists on it, "cpu" never uses it.
    device: str = "auto"

    # Default Foundry Local chat model alias, used until the user picks one.
    default_chat_model: str = "qwen2.5-coder-1.5b"

    # Indexing limits.
    max_file_bytes: int = 1_000_000

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


@lru_cache
def get_config() -> Config:
    config = Config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.repos_dir.mkdir(parents=True, exist_ok=True)
    config.models_dir.mkdir(parents=True, exist_ok=True)
    return config


class RuntimeSettings(BaseModel):
    """User-tunable settings, edited from the Settings page."""

    chat_model: str | None = None
    top_k: int = Field(default=6, ge=1, le=20)
    relevance_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    hybrid_search: bool = True


class SettingsStore:
    """Loads and saves ``RuntimeSettings`` as JSON, safe to use from threads."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> RuntimeSettings:
        if self._path.exists():
            try:
                return RuntimeSettings.model_validate_json(self._path.read_text("utf-8"))
            except ValueError:
                pass  # A corrupt file should not stop the app; fall back to defaults.
        return RuntimeSettings()

    def get(self) -> RuntimeSettings:
        with self._lock:
            return self._settings.model_copy()

    def update(self, changes: dict) -> RuntimeSettings:
        with self._lock:
            merged = self._settings.model_dump() | changes
            self._settings = RuntimeSettings.model_validate(merged)
            self._path.write_text(json.dumps(self._settings.model_dump(), indent=2), "utf-8")
            return self._settings.model_copy()
