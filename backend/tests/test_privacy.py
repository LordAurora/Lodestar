"""Privacy tests: Lodestar must never talk to anything but localhost.

1. The network guard blocks non-loopback connections and allows loopback ones.
2. A complete index + chat flow makes no outbound connection attempts at all.
3. The frontend source contains no external URLs (fonts, icons, scripts).
"""

from __future__ import annotations

import re
import socket
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.privacy import NetworkGuard, guard
from app.state import AppState
from tests.test_api import OfflineFoundry, index

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@pytest.fixture
def strict_guard():
    guard.install(block=True)
    before = len(guard.blocked)
    yield guard
    guard.enabled = False
    del guard.blocked[before:]


def test_is_local_classification():
    assert NetworkGuard.is_local(("127.0.0.1", 80))
    assert NetworkGuard.is_local(("::1", 80, 0, 0))
    assert NetworkGuard.is_local(("localhost", 80))
    assert not NetworkGuard.is_local(("93.184.216.34", 443))
    assert not NetworkGuard.is_local(("example.com", 443))


def test_guard_blocks_external_and_allows_loopback(strict_guard):
    with pytest.raises(ConnectionRefusedError, match="only localhost"):
        socket.create_connection(("93.184.216.34", 443), timeout=1)
    assert strict_guard.blocked[-1].startswith("('93.184.216.34'")

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    threading.Thread(target=lambda: server.accept(), daemon=True).start()
    socket.create_connection(server.getsockname(), timeout=1).close()
    server.close()


def test_full_flow_makes_no_outbound_requests(strict_guard, config, sample_repo, fake_embedder):
    from tests.conftest import FakeLLM

    before = len(strict_guard.blocked)
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=FakeLLM())
    with TestClient(create_app(state, warm_up=False)) as client:
        repo = client.post("/api/repos", json={"path": str(sample_repo)}).json()
        index(client, repo["id"])
        client.put("/api/settings", json={"relevance_threshold": 0.0})
        client.post(f"/api/repos/{repo['id']}/chat", json={"question": "verify token"})
        health = client.get("/api/health").json()
    assert strict_guard.blocked[before:] == []
    assert health["privacy"]["guard_enabled"] is True


EXTERNAL_URL = re.compile(
    r"""(?:src|href|url|import|from|fetch)\s*\(?\s*['"`]?(https?:)?//(?!localhost|127\.0\.0\.1)"""
)


def frontend_files():
    roots = [FRONTEND / "src", FRONTEND / "index.html", FRONTEND / "dist"]
    for root in roots:
        if root.is_file():
            yield root
        elif root.is_dir():
            yield from (
                p for p in root.rglob("*") if p.suffix in {".ts", ".tsx", ".css", ".html", ".js"}
            )


@pytest.mark.skipif(not FRONTEND.exists(), reason="frontend not present")
def test_frontend_loads_nothing_from_external_urls():
    offenders = []
    for path in frontend_files():
        text = path.read_text("utf-8", errors="ignore")
        for match in EXTERNAL_URL.finditer(text):
            offenders.append(
                f"{path.relative_to(FRONTEND)}: {text[match.start() : match.end() + 40]}"
            )
    assert offenders == []
