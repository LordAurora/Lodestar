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
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

# Foundry Local runs a model on the GPU through an "execution provider" (EP).
# WebGPU works on any DirectX 12 / Vulkan / Metal GPU (NVIDIA, AMD, Intel) and is
# a small download, so it is our default. The EP is fetched once by
# `scripts/setup_models.py`; at runtime we only *register* it if it is already there.
GPU_EP = "WebGpuExecutionProvider"
GPU_EP_DIR = Path.home() / ".foundry" / "ep" / "webgpu-ep"

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

    def __init__(self, app_name: str = "lodestar", device: str = "auto"):
        self._app_name = app_name
        self._device = device  # "auto" (GPU if available), "gpu" or "cpu"
        self._gpu_ready = False
        self._active: dict[str, tuple[str, str]] = {}  # alias -> (model id, "GPU" or "CPU")
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
                self._register_gpu(manager)
                self._error = None
                log.info("Foundry Local web service at %s", self._base_url)
            except Exception as exc:
                self._error = (
                    f"Foundry Local could not be started ({exc}). Make sure Foundry Local is "
                    "installed (`winget install Microsoft.FoundryLocal` on Windows, "
                    "`brew install microsoft/foundrylocal/foundrylocal` on macOS) and try again."
                )
                raise FoundryUnavailable(self._error) from exc

    def _register_gpu(self, manager) -> None:
        """Make the GPU execution provider available, without ever surprising the user
        with a download: `auto` only registers an EP that is already on disk."""
        if self._device == "cpu":
            return
        if self._device == "auto" and not GPU_EP_DIR.exists():
            log.info("GPU execution provider not installed; using the CPU (run `make setup`).")
            return
        try:
            manager.download_and_register_eps([GPU_EP])
            eps = manager.discover_eps()
            self._gpu_ready = any(e.is_registered for e in eps if e.name == GPU_EP)
        except Exception as exc:
            log.warning("Could not enable the GPU: %s", exc)

    @staticmethod
    def _device_of(variant) -> str:
        runtime = variant.info.runtime
        return str(runtime.device_type).upper() if runtime else "CPU"

    def _candidates(self, alias: str) -> list:
        """The model's variants in the order we would like to use them (GPU first)."""
        model = self.manager.catalog.get_model(alias)
        if model is None:
            raise FoundryUnavailable(f"Model '{alias}' is not in the Foundry Local catalog.")
        variants = list(model.variants) or [model]
        gpu = [v for v in variants if self._device_of(v) == "GPU"]
        cpu = [v for v in variants if self._device_of(v) != "GPU"]
        if self._device == "cpu" or not self._gpu_ready:
            return cpu or variants
        return gpu + cpu

    def active_device(self, alias: str) -> str | None:
        """GPU or CPU for a model that was loaded through ``ensure_model``."""
        active = self._active.get(alias)
        return active[1] if active else None

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
            "gpu": self._gpu_ready,
        }

    # ---- models -------------------------------------------------------

    def list_models(self) -> list[dict]:
        """All chat-capable models in the catalog, with cache/load status."""
        models = []
        for model in self.manager.catalog.list_models():
            info = model.info
            if (info.task or "").lower() not in CHAT_TASKS:
                continue
            variants = self._candidates(model.alias)
            preferred = variants[0]
            job = self._jobs.get(model.alias)
            loaded = next((v for v in variants if v.is_loaded), None)
            models.append(
                {
                    "alias": model.alias,
                    "id": preferred.id,
                    "display_name": info.display_name or model.alias,
                    "size_mb": preferred.info.file_size_mb,
                    "cached": any(v.is_cached for v in variants),
                    "loaded": loaded is not None,
                    "device": self._device_of(loaded or preferred),
                    "job": job.__dict__ if job and job.state != "ready" else None,
                }
            )
        models.sort(key=lambda m: (not m["loaded"], not m["cached"], m["alias"]))
        return models

    def is_ready(self, alias: str) -> bool:
        return any(v.is_cached and v.is_loaded for v in self._candidates(alias))

    def ensure_model(self, alias: str, allow_download: bool = True, progress=None) -> str:
        """Make sure a model is downloaded and loaded. Returns its model id (blocking).

        Tries the GPU variant first. If it cannot be loaded (for example not enough
        video memory) we fall back to the CPU variant instead of failing.
        """
        candidates = self._candidates(alias)
        if not allow_download:
            candidates = [v for v in candidates if v.is_cached]
            if not candidates:
                raise FoundryUnavailable(
                    f"Model '{alias}' is not downloaded yet. Download it from the Settings page "
                    "or run `python scripts/setup_models.py`."
                )
        last_error: Exception | None = None
        for variant in candidates:
            try:
                if not variant.is_cached:
                    variant.download(progress)
                if not variant.is_loaded:
                    variant.load()
            except Exception as exc:
                log.warning("Could not load %s: %s", variant.id, exc)
                last_error = exc
                continue
            self._active[alias] = (variant.id, self._device_of(variant))
            return variant.id
        raise FoundryUnavailable(f"Could not load '{alias}': {last_error}") from last_error

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
                self.ensure_model(alias, progress=progress)
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
        # ensure_model also loads the model if it was unloaded in the meantime.
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
