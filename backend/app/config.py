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
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, model_validator
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

    # Bring your own chat model. "foundry" runs the model on this machine. "openai" sends the
    # question and the retrieved code snippets to any OpenAI-compatible endpoint (a company
    # gateway, vLLM, Ollama, LM Studio, a hosted API). Embeddings always stay local.
    llm_provider: str = "foundry"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout: float = 120.0

    # Extra hosts the network guard lets through (IPs, CIDR ranges or host names, comma
    # separated). The host of ``llm_base_url`` is allowed automatically.
    allowed_hosts: str = ""

    # Comment tags collected by the tech debt board, and how similar two comments must be
    # (cosine similarity) to be grouped under one topic.
    debt_tags: str = "TODO,FIXME,HACK,XXX,BUG,NOTE"
    debt_topic_threshold: float = 0.6

    # Duplicate finder: how similar two functions must be (cosine of their embeddings) to be
    # reported by default, the smallest function considered (lines), and the block size of the
    # matrix product that keeps memory bounded on large repositories.
    duplicate_min_similarity: float = 0.90
    duplicate_min_lines: int = 5
    duplicate_block_size: int = 512

    # Docstring suggester: the Python docstring style (google, numpy or rest).
    docstring_style: str = "google"

    # Indexing limits.
    max_file_bytes: int = 1_000_000

    @model_validator(mode="after")
    def _check_llm(self) -> Config:
        if self.llm_provider not in {"foundry", "openai"}:
            raise ValueError("LODESTAR_LLM_PROVIDER must be 'foundry' or 'openai'.")
        if self.llm_provider == "openai":
            if urlparse(self.llm_base_url).scheme not in {"http", "https"}:
                raise ValueError(
                    "LODESTAR_LLM_BASE_URL must be an http(s) URL, e.g. https://host/v1"
                )
            if not self.llm_model:
                raise ValueError(
                    "LODESTAR_LLM_MODEL is required when LODESTAR_LLM_PROVIDER=openai."
                )
        return self

    @property
    def remote_llm(self) -> bool:
        return self.llm_provider == "openai"

    @property
    def llm_host(self) -> str | None:
        return urlparse(self.llm_base_url).hostname if self.remote_llm else None

    @property
    def guard_allowed_hosts(self) -> list[str]:
        """Hosts the network guard lets through: the ones listed plus the remote model's."""
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        if self.llm_host:
            hosts.append(self.llm_host)
        return hosts

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
