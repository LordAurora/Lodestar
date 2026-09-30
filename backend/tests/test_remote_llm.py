"""Bring your own chat model: config checks, the guard's allow list and the OpenAI-compatible client."""

from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Config
from app.main import create_app
from app.privacy import NetworkGuard
from app.remote_llm import RemoteLLM
from app.state import AppState
from tests.test_api import OfflineFoundry

# ---- configuration ---------------------------------------------------------------------------


def test_foundry_is_the_default(tmp_path):
    config = Config(data_dir=tmp_path)
    assert not config.remote_llm and config.llm_host is None and config.guard_allowed_hosts == []


@pytest.mark.parametrize(
    "values",
    [
        {"llm_provider": "azure"},
        {"llm_provider": "openai", "llm_model": "big"},  # no URL
        {"llm_provider": "openai", "llm_base_url": "ftp://x/v1", "llm_model": "big"},
        {"llm_provider": "openai", "llm_base_url": "https://gw.example/v1"},  # no model
    ],
)
def test_invalid_remote_config_is_refused(tmp_path, values):
    with pytest.raises(ValidationError):
        Config(data_dir=tmp_path, **values)


def test_remote_config_allows_its_own_host_and_hides_the_key(tmp_path):
    config = Config(
        data_dir=tmp_path,
        llm_provider="openai",
        llm_base_url="https://gw.example.com:8443/v1",
        llm_model="big-model",
        llm_api_key="sk-secret",
        allowed_hosts=" 10.0.0.0/8, other.example ",
    )
    assert config.llm_host == "gw.example.com"
    assert config.guard_allowed_hosts == ["10.0.0.0/8", "other.example", "gw.example.com"]
    assert "sk-secret" not in repr(config) and "sk-secret" not in config.model_dump_json()


# ---- network guard allow list ----------------------------------------------------------------


def hook(guard: NetworkGuard, address) -> None:
    guard._hook("socket.connect", (SimpleNamespace(family=socket.AF_INET), address))


def test_guard_allow_list(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda name, *a, **k: [(2, 1, 6, "", ("203.0.113.7", 0))] if name == "gw.corp" else [],
    )
    guard = NetworkGuard()
    guard.enabled = True
    with pytest.raises(ConnectionRefusedError):  # nothing allowed yet
        hook(guard, ("203.0.113.7", 443))

    guard.allow(["gw.corp", "10.20.0.0/16", "192.0.2.9"])
    hook(guard, ("203.0.113.7", 443))  # what gw.corp resolved to
    hook(guard, ("gw.corp", 443))  # the name itself
    hook(guard, ("10.20.5.5", 443))  # inside the range
    hook(guard, ("192.0.2.9", 443))  # a single address
    assert guard.allowed_remote == 4
    for other in [("203.0.113.8", 443), ("10.21.0.1", 443), ("example.com", 443)]:
        with pytest.raises(ConnectionRefusedError):
            hook(guard, other)
    assert guard.status()["allowed_hosts"] == ["10.20.0.0/16", "192.0.2.9/32", "gw.corp"]
    assert guard.status()["external_attempts"] == 4  # the earlier failure plus these three

    guard.allow([])  # the list can be cleared
    with pytest.raises(ConnectionRefusedError):
        hook(guard, ("203.0.113.7", 443))


# ---- the client ------------------------------------------------------------------------------


def chat_transport(seen: list) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization"), request.content))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": []})
        body = json.loads(request.content)
        if body.get("stream"):
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": part}}]} for part in ("Hel", "lo")
            ]
            text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]}
        )

    return httpx.MockTransport(handler)


async def test_client_streams_and_completes():
    seen: list = []
    llm = RemoteLLM(
        "https://gw.example/v1",
        api_key="sk-test",
        http_client=httpx.AsyncClient(transport=chat_transport(seen)),
    )
    messages = [{"role": "user", "content": "hi"}]
    assert [t async for t in llm.stream_chat(messages, "big-model")] == ["Hel", "lo"]
    assert await llm.complete(messages, "big-model") == "ok"
    assert await llm.check() is None
    path, auth, body = seen[0]
    assert path == "/v1/chat/completions" and auth == "Bearer sk-test"
    assert json.loads(body)["model"] == "big-model"


async def test_check_reports_a_failure():
    def refuse(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    llm = RemoteLLM(
        "https://gw.example/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
    )
    assert "401" in (await llm.check() or "")


# ---- the API ---------------------------------------------------------------------------------


@pytest.fixture
def remote_client(tmp_path, fake_embedder):
    config = Config(
        data_dir=tmp_path / "data",
        block_external_network=False,
        llm_provider="openai",
        llm_base_url="https://gw.example.com/v1",
        llm_model="big-model",
    )
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder)
    assert isinstance(state.llm, RemoteLLM)
    state.llm = RemoteLLM(
        config.llm_base_url, http_client=httpx.AsyncClient(transport=chat_transport([]))
    )
    with TestClient(create_app(state, warm_up=False)) as client:
        yield client


def test_health_says_the_model_is_remote(remote_client):
    health = remote_client.get("/api/health").json()
    assert health["llm"] == {
        "provider": "openai",
        "remote": True,
        "host": "gw.example.com",
        "base_url": "https://gw.example.com/v1",
    }
    assert health["chat_model"] == {"alias": "big-model", "ready": True, "device": "Remote"}
    assert health["privacy"]["local_only"] is False  # honest: chat leaves this machine


def test_models_and_check_for_a_remote_model(remote_client):
    models = remote_client.get("/api/models").json()
    assert models == {"selected": "big-model", "models": [], "remote": True}
    assert remote_client.post("/api/models/anything/load").status_code == 409
    assert remote_client.get("/api/llm/check").json() == {"ok": True, "remote": True, "error": None}
    assert "sk-" not in remote_client.get("/api/settings").text


def test_local_setup_is_unchanged(tmp_path, fake_embedder):
    state = AppState(
        Config(data_dir=tmp_path / "d", block_external_network=False),
        foundry=OfflineFoundry(),
        embedder=fake_embedder,
    )
    with TestClient(create_app(state, warm_up=False)) as client:
        health = client.get("/api/health").json()
        assert health["llm"]["remote"] is False and health["llm"]["host"] is None
        assert client.get("/api/llm/check").json()["remote"] is False


def test_a_model_server_on_this_machine_keeps_the_app_local_only(tmp_path, fake_embedder):
    config = Config(
        data_dir=tmp_path / "data",
        block_external_network=False,
        llm_provider="openai",
        llm_base_url="http://127.0.0.1:11434/v1",  # Ollama's default address
        llm_model="llama3.3:70b",
    )
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder)
    with TestClient(create_app(state, warm_up=False)) as client:
        health = client.get("/api/health").json()
    assert health["privacy"]["local_only"] is True
    assert health["chat_model"]["device"] == "Local server"
