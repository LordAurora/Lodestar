"""API tests: the full flow through HTTP, with fake models."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.state import AppState


class OfflineFoundry:
    """Stands in for Foundry Local; reports itself as not running."""

    base_url = None

    def status(self):
        return {"running": False, "endpoint": None, "error": "not started in tests"}

    def stop(self):
        pass


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if ": " in line and line[0] != ":"
        )
        if "event" in fields:
            events.append((fields["event"], json.loads(fields.get("data", "{}"))))
    return events


@pytest.fixture
def client(config, fake_embedder, fake_llm):
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=fake_llm)
    with TestClient(create_app(state, warm_up=False)) as c:
        yield c


def index(client, repo_id):
    assert client.post(f"/api/repos/{repo_id}/index").status_code == 202
    for _ in range(100):
        events = parse_sse(client.get(f"/api/repos/{repo_id}/index/stream").text)
        if events and events[-1][0] in ("done", "error"):
            return events[-1]
        time.sleep(0.05)
    raise AssertionError("indexing did not finish")


def test_invalid_repo_path_gives_actionable_message(client, tmp_path):
    res = client.post("/api/repos", json={"path": str(tmp_path / "nope")})
    assert res.status_code == 422
    assert "does not exist" in res.json()["detail"]


def test_full_flow(client, sample_repo, fake_llm):
    repo = client.post("/api/repos", json={"path": str(sample_repo)}).json()
    assert client.get("/api/repos").json()[0]["id"] == repo["id"]

    # Asking before indexing is refused with a hint.
    early = client.post(f"/api/repos/{repo['id']}/chat", json={"question": "hi"})
    assert early.status_code == 409

    event, data = index(client, repo["id"])
    assert event == "done" and data["repo"]["chunk_count"] > 0

    client.put("/api/settings", json={"relevance_threshold": 0.0, "top_k": 3})
    res = client.post(f"/api/repos/{repo['id']}/chat", json={"question": "verify token"})
    events = parse_sse(res.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "retrieval" and kinds[-1] == "done" and "token" in kinds
    conv_id = events[0][1]["conversation_id"]

    # The conversation and both messages are persisted.
    convs = client.get(f"/api/repos/{repo['id']}/conversations").json()
    assert [c["id"] for c in convs] == [conv_id]
    messages = client.get(f"/api/repos/{repo['id']}/conversations/{conv_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["meta"]["citations"][0]["n"] == 1

    # A source can be previewed with surrounding lines.
    chunk_id = messages[1]["meta"]["sources"][0]["chunk_id"]
    preview = client.get(f"/api/chunks/{chunk_id}").json()
    assert preview["lines"] and preview["stale"] is False

    # Rename and delete.
    patch = client.patch(
        f"/api/repos/{repo['id']}/conversations/{conv_id}", json={"title": "Tokens"}
    )
    assert patch.json()["title"] == "Tokens"
    assert client.delete(f"/api/repos/{repo['id']}/conversations/{conv_id}").status_code == 204
    assert client.get(f"/api/repos/{repo['id']}/conversations").json() == []


def test_threshold_via_api_skips_llm(client, sample_repo, fake_llm):
    repo = client.post("/api/repos", json={"path": str(sample_repo)}).json()
    index(client, repo["id"])
    client.put("/api/settings", json={"relevance_threshold": 1.0})
    res = client.post(f"/api/repos/{repo['id']}/chat", json={"question": "quantum physics"})
    assert [e for e, _ in parse_sse(res.text)] == ["retrieval", "no_answer"]
    assert fake_llm.stream_calls == []


def test_settings_validation(client):
    assert client.put("/api/settings", json={"top_k": 0}).status_code == 422
    assert client.put("/api/settings", json={"colour": "red"}).status_code == 422
    assert (
        client.put("/api/settings", json={"hybrid_search": False}).json()["hybrid_search"] is False
    )


def test_health_reports_foundry_problems_without_failing(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["foundry"]["running"] is False
    assert body["privacy"]["local_only"] is True
