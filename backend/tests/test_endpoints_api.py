"""Endpoint catalog: filtering, exports, handler links and incremental updates."""

from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from app.analysis.endpoints.catalog import list_endpoints
from app.main import create_app
from app.state import AppState
from tests.conftest import FakeLLM, write
from tests.test_api import OfflineFoundry, index
from tests.test_indexer import run_index

FILES = {
    "api/users.py": """
    from fastapi import APIRouter
    router = APIRouter(prefix="/users")

    def helper():
        pass

    @router.get("/{id}")
    def get_user(id: int):
        return helper()

    @router.post("/")
    def create_user():
        pass
    """,
    "api/main.py": """
    from fastapi import FastAPI
    from api import users
    app = FastAPI()
    app.include_router(users.router, prefix="/api")

    @app.get("/health")
    def health():
        pass
    """,
    "web/server.js": """
    const express = require("express");
    const app = express();
    app.get("/js", (req, res) => {});
    """,
    "svc/main.go": """
    package main
    import "net/http"
    func main() { http.HandleFunc("/go", goHandler) }
    func goHandler(w http.ResponseWriter, r *http.Request) {}
    """,
}


@pytest.fixture
def client(config, tmp_path, fake_embedder):
    root = tmp_path / "epapi"
    for rel, content in FILES.items():
        write(root, rel, content)
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=FakeLLM())
    with TestClient(create_app(state, warm_up=False)) as test_client:
        repo = test_client.post("/api/repos", json={"path": str(root)}).json()
        index(test_client, repo["id"])
        test_client.repo_id = repo["id"]
        test_client.root = root
        yield test_client


def url(client, tail: str) -> str:
    return f"/api/repos/{client.repo_id}{tail}"


def test_list_with_facets(client):
    data = client.get(url(client, "/endpoints")).json()
    assert data["total"] == data["shown"] == 5
    assert {(e["method"], e["path"]) for e in data["endpoints"]} == {
        ("GET", "/api/users/{id}"),
        ("POST", "/api/users/"),
        ("GET", "/health"),
        ("GET", "/js"),
        ("ANY", "/go"),
    }
    assert [m["method"] for m in data["methods"]] == ["GET", "POST", "ANY"]
    assert {f["label"] for f in data["frameworks"]} == {"FastAPI", "Express", "net/http"}


def test_filters(client):
    def search(**params):
        return client.get(url(client, "/endpoints"), params=params).json()

    assert {e["path"] for e in search(method="post")["endpoints"]} == {"/api/users/"}
    assert {e["path"] for e in search(framework="express")["endpoints"]} == {"/js"}
    assert {e["path"] for e in search(q="users")["endpoints"]} == {"/api/users/{id}", "/api/users/"}
    assert search(q="zzz")["shown"] == 0 and search(q="zzz")["total"] == 5  # facets stay global


def test_handlers_link_to_symbols_so_flows_can_start_there(client):
    data = client.get(url(client, "/endpoints")).json()["endpoints"]
    by_path = {e["path"]: e for e in data}
    assert by_path["/api/users/{id}"]["symbol"] == "get_user"
    assert by_path["/api/users/{id}"]["handler_symbol_id"] is not None
    assert by_path["/go"]["symbol"] == "goHandler"  # found by name in another declaration
    assert by_path["/js"]["handler_symbol_id"] is None  # an inline function has no symbol
    assert by_path["/health"]["file_path"] == "api/main.py" and by_path["/health"]["line"] == 6


def test_exports(client):
    as_json = client.get(url(client, "/endpoints/export"), params={"format": "json"})
    assert "endpoints.json" in as_json.headers["content-disposition"]
    assert {e["path"] for e in json.loads(as_json.text)} >= {"/health", "/js"}

    rows = list(
        csv.reader(
            io.StringIO(client.get(url(client, "/endpoints/export"), params={"format": "csv"}).text)
        )
    )
    assert rows[0] == ["method", "path", "handler", "file", "line", "framework", "partial"]
    assert ["GET", "/health", "health", "api/main.py", "6", "FastAPI", ""] in rows

    md = client.get(
        url(client, "/endpoints/export"), params={"format": "markdown", "method": "GET"}
    ).text
    assert "| GET | `/health` | `health` | `api/main.py:6` | FastAPI |" in md and "POST" not in md
    assert client.get(url(client, "/endpoints/export"), params={"format": "xml"}).status_code == 422
    assert client.get("/api/repos/nope/endpoints").status_code == 404
    assert client.get(url(client, "/insights/status")).json()["counts"]["endpoints"] == 5


def test_a_changed_file_updates_the_catalog_and_the_paths_that_depend_on_it(client, db=None):
    write(
        client.root,
        "api/main.py",
        """
    from fastapi import FastAPI
    from api import users
    app = FastAPI()
    app.include_router(users.router, prefix="/v2")
    """,
    )
    index(client, client.repo_id)
    paths = {e["path"] for e in client.get(url(client, "/endpoints")).json()["endpoints"]}
    # main.py changed, so /health is gone, and users.py (unchanged) now sits under /v2.
    assert paths == {"/v2/users/{id}", "/v2/users/", "/js", "/go"}


def test_test_files_are_not_part_of_the_api(db, tmp_path, fake_embedder):
    root = tmp_path / "t"
    write(
        root,
        "tests/test_app.py",
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/fixture')\ndef f(): pass\n",
    )
    write(
        root,
        "app.py",
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/real')\ndef g(): pass\n",
    )
    repo = db.add_repo(str(root), "t")
    run_index(db, repo, root, fake_embedder)
    with db.repo(repo["id"]) as conn:
        assert {e["path"] for e in list_endpoints(conn)["endpoints"]} == {"/real"}
