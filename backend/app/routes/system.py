"""Health, models and settings: everything the Settings page and status badges need."""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from app.config import RuntimeSettings
from app.foundry import FoundryUnavailable
from app.privacy import guard
from app.state import AppState, get_state

router = APIRouter(prefix="/api")


def _is_loopback_url(url: str | None) -> bool:
    return bool(url) and urlparse(url).hostname in ("127.0.0.1", "localhost", "::1")


@router.get("/health")
async def health(state: AppState = Depends(get_state)) -> dict:
    foundry = state.foundry.status()
    model = state.chat_model()
    remote = state.config.remote_llm
    # A model server on this machine (Ollama, LM Studio, llama.cpp) keeps everything local.
    leaves_machine = remote and not _is_loopback_url(state.config.llm_base_url)
    model_ready = remote  # a remote endpoint is not polled on every health check
    if foundry["running"] and not remote:
        try:
            model_ready = await asyncio.to_thread(state.foundry.is_ready, model)
        except Exception:
            model_ready = False
    privacy = guard.status()
    endpoint_local = foundry["endpoint"] is None or _is_loopback_url(foundry["endpoint"])
    privacy["local_only"] = (
        endpoint_local and privacy["external_attempts"] == 0 and not leaves_machine
    )
    return {
        "status": "ok",
        "foundry": foundry,
        "chat_model": {
            "alias": model,
            "ready": model_ready,
            "device": ("Remote" if leaves_machine else "Local server")
            if remote
            else state.foundry.active_device(model),
        },
        "llm": {
            "provider": state.config.llm_provider,
            "remote": remote,
            "host": state.config.llm_host,
            "base_url": state.config.llm_base_url if remote else None,
        },
        "embedder": state.embedder_status(),
        "privacy": privacy,
    }


@router.get("/models")
async def list_models(state: AppState = Depends(get_state)) -> dict:
    if state.config.remote_llm:
        return {"selected": state.chat_model(), "models": [], "remote": True}
    try:
        models = await asyncio.to_thread(state.foundry.list_models)
    except FoundryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"selected": state.chat_model(), "models": models}


@router.post("/models/{alias}/load")
async def load_model(alias: str, state: AppState = Depends(get_state)) -> dict:
    """Download (if needed) and load a chat model in the background."""
    if state.config.remote_llm:
        raise HTTPException(409, "The chat model runs on a remote endpoint. Nothing to load.")
    try:
        job = await asyncio.to_thread(state.foundry.start_model_job, alias)
    except FoundryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return job.__dict__


@router.get("/llm/check")
async def check_llm(state: AppState = Depends(get_state)) -> dict:
    """Try the remote chat endpoint (the Settings page's "Test connection" button)."""
    check = getattr(state.llm, "check", None)
    if not state.config.remote_llm or check is None:
        return {"ok": True, "remote": False, "error": None}
    error = await check()
    return {"ok": error is None, "remote": True, "error": error}


@router.get("/settings")
async def get_settings(state: AppState = Depends(get_state)) -> dict:
    return _settings_payload(state)


@router.put("/settings")
async def put_settings(changes: dict, state: AppState = Depends(get_state)) -> dict:
    allowed = set(RuntimeSettings.model_fields)
    unknown = set(changes) - allowed
    if unknown:
        raise HTTPException(422, f"Unknown settings: {', '.join(sorted(unknown))}")
    try:
        state.settings.update(changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _settings_payload(state)


def _settings_payload(state: AppState) -> dict:
    settings = state.settings.get().model_dump()
    settings["chat_model"] = state.chat_model()
    settings["embedding_model"] = state.embedder_status()["name"] or state.config.embedding_model
    return settings
