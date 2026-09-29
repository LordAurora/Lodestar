"""Foundry Local: the on-device model runtime.

We use the official ``foundry-local-sdk`` (v2) to:

1. initialise Foundry Local inside this process (``FoundryLocalManager``),
2. browse the model catalog, download models and load them into memory,
3. start Foundry Local's built-in OpenAI-compatible web service on
   ``127.0.0.1`` and read back the URL it bound to.

After that, chat completions and embeddings go through the regular ``openai``
Python client pointed at that local URL. Nothing leaves the machine: the web
service only listens on the loopback interface.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

# The web service ignores the API key, but the openai client requires one.
DUMMY_API_KEY = "local-no-key"

# Catalog "task" values of models that can answer chat questions. Other tasks
# in the catalog are "embeddings" and "automatic-speech-recognition".
CHAT_TASKS = {"chat-completion", "vision-language-chat"}


class FoundryUnavailable(RuntimeError):
    """Foundry Local could not be started. The message tells the user what to do."""


@dataclass
class ModelJob:
    """Progress of a background download + load of one model."""

    alias: str
    state: str = "downloading"  # downloading | loading | ready | error
    progress: float = 0.0
    error: str | None = None


class LLMClient(Protocol):
    """What the RAG pipeline needs from a language model. Faked in tests."""

    def stream_chat(self, messages: list[dict], model: str) -> AsyncIterator[str]: ...

    async def complete(self, messages: list[dict], model: str) -> str: ...


class FoundryService:
    """Owns the Foundry Local manager and hands out OpenAI clients."""

    def __init__(self, app_name: str = "lodestar"):
        self._app_name = app_name
        self._lock = threading.RLock()
        self._manager = None
        self._base_url: str | None = None
        self._error: str | None = None
        self._jobs: dict[str, ModelJob] = {}
        self._sync_client = None
        self._async_client = None

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Initialise the SDK and start the local web service. Safe to call twice."""
        with self._lock:
            if self._manager is not None:
                return
            try:
                from foundry_local_sdk import Configuration, FoundryLocalManager
            except ImportError as exc:
                self._error = (
                    "The foundry-local-sdk package is not installed. "
                    "Run `make setup` (or `pip install foundry-local-sdk`)."
                )
                raise FoundryUnavailable(self._error) from exc
            try:
                if FoundryLocalManager.instance is None:
                    config = Configuration(
                        app_name=self._app_name,
                        # Loopback only, random free port.
                        web=Configuration.WebService(urls="http://127.0.0.1:0"),
                        disable_nonessential_telemetry=True,
                    )
                    FoundryLocalManager.initialize(config)
                manager = FoundryLocalManager.instance
                if not manager.urls:
                    manager.start_web_service()
                self._base_url = manager.urls[0].rstrip("/") + "/v1"
                self._manager = manager
                self._error = None
                log.info("Foundry Local web service at %s", self._base_url)
            except Exception as exc:
                self._error = (
                    f"Foundry Local could not be started ({exc}). Make sure Foundry Local is "
                    "installed (`winget install Microsoft.FoundryLocal` on Windows, "
                    "`brew install microsoft/foundrylocal/foundrylocal` on macOS) and try again."
                )
                raise FoundryUnavailable(self._error) from exc

    def stop(self) -> None:
        with self._lock:
            if self._manager is not None:
                try:
                    self._manager.close()
                finally:
                    self._manager = None

    @property
    def manager(self):
        if self._manager is None:
            self.start()
        return self._manager

    @property
    def base_url(self) -> str | None:
        return self._base_url

    def status(self) -> dict:
        return {
            "running": self._manager is not None,
            "endpoint": self._base_url,
            "error": self._error,
        }

    # ---- models -------------------------------------------------------

    def list_models(self) -> list[dict]:
        """All chat-capable models in the catalog, with cache/load status."""
        models = []
        for model in self.manager.catalog.list_models():
            info = model.info
            if (info.task or "").lower() not in CHAT_TASKS:
                continue
            job = self._jobs.get(model.alias)
            models.append(
                {
                    "alias": model.alias,
                    "id": model.id,
                    "display_name": info.display_name or model.alias,
                    "size_mb": info.file_size_mb,
                    "cached": model.is_cached,
                    "loaded": model.is_loaded,
                    "job": job.__dict__ if job and job.state != "ready" else None,
                }
            )
        models.sort(key=lambda m: (not m["loaded"], not m["cached"], m["alias"]))
        return models

    def is_ready(self, alias: str) -> bool:
        model = self.manager.catalog.get_model(alias)
        return bool(model and model.is_cached and model.is_loaded)

    def ensure_model(self, alias: str, allow_download: bool = True, progress=None) -> str:
        """Make sure a model is downloaded and loaded. Returns its model id (blocking)."""
        model = self.manager.catalog.get_model(alias)
        if model is None:
            raise FoundryUnavailable(f"Model '{alias}' is not in the Foundry Local catalog.")
        if not model.is_cached:
            if not allow_download:
                raise FoundryUnavailable(
                    f"Model '{alias}' is not downloaded yet. Download it from the Settings page "
                    f"or run `foundry model download {alias}`."
                )
            model.download(progress)
        if not model.is_loaded:
            model.load()
        return model.id

    def start_model_job(self, alias: str) -> ModelJob:
        """Download + load a model in a background thread, tracking progress."""
        with self._lock:
            job = self._jobs.get(alias)
            if job and job.state in ("downloading", "loading"):
                return job
            job = self._jobs[alias] = ModelJob(alias)

        def progress(pct: float) -> None:
            job.progress = float(pct)

        def run() -> None:
            try:
                model = self.manager.catalog.get_model(alias)
                if model is None:
                    raise FoundryUnavailable(f"Unknown model '{alias}'.")
                if not model.is_cached:
                    model.download(progress)
                job.state, job.progress = "loading", 100.0
                model.load()
                job.state = "ready"
            except Exception as exc:
                job.state, job.error = "error", str(exc)

        threading.Thread(target=run, name=f"model-{alias}", daemon=True).start()
        return job

    # ---- clients ------------------------------------------------------

    def sync_client(self):
        from openai import OpenAI

        if self._sync_client is None:
            self._sync_client = OpenAI(base_url=self._require_url(), api_key=DUMMY_API_KEY)
        return self._sync_client

    def async_client(self):
        from openai import AsyncOpenAI

        if self._async_client is None:
            self._async_client = AsyncOpenAI(base_url=self._require_url(), api_key=DUMMY_API_KEY)
        return self._async_client

    def _require_url(self) -> str:
        if self._base_url is None:
            self.start()
        return self._base_url


class FoundryLLM:
    """``LLMClient`` implementation that talks to Foundry Local's chat endpoint."""

    def __init__(self, foundry: FoundryService):
        self._foundry = foundry

    def _model_id(self, alias: str) -> str:
        # Chat requests must name the concrete model id (e.g. "...-generic-cpu:4").
        return self._foundry.ensure_model(alias, allow_download=False)

    async def stream_chat(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        import asyncio

        model_id = await asyncio.to_thread(self._model_id, model)
        stream = await self._foundry.async_client().chat.completions.create(
            model=model_id, messages=messages, stream=True, temperature=0.1
        )
        async for event in stream:
            if event.choices and event.choices[0].delta.content:
                yield event.choices[0].delta.content

    async def complete(self, messages: list[dict], model: str) -> str:
        import asyncio

        model_id = await asyncio.to_thread(self._model_id, model)
        response = await self._foundry.async_client().chat.completions.create(
            model=model_id, messages=messages, temperature=0.0, max_tokens=200
        )
        return response.choices[0].message.content or ""
