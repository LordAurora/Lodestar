"""Mermaid diagram generation: module dependencies, call flows, escaping and limits."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.analysis.diagrams import flow_diagram, label, message, modules_diagram
from app.main import create_app
from app.state import AppState
from tests.conftest import FakeLLM, write
from tests.test_api import OfflineFoundry, index
from tests.test_indexer import run_index


def build(db, tmp_path, fake_embedder, files):
    root = tmp_path / f"d{len(list(tmp_path.iterdir()))}"
    for rel, content in files.items():
        write(root, rel, content)
    repo = db.add_repo(str(root), root.name)
    run_index(db, repo, root, fake_embedder)
    return repo["id"]


def edges_of(text: str) -> set[tuple[str, str]]:
    """(from label, to label) pairs of a flowchart, resolving node ids through their labels."""
    names = dict(re.findall(r'^\s+(n\d+)\["(.*?)"\]', text, re.M))
    pattern = r"^\s+(n\d+) -->(?:\|\d+\|)? (n\d+)"
    return {(names[a], names[b]) for a, b in re.findall(pattern, text, re.M)}


MODULE_REPO = {
    "app/api/routes.py": "from app.core import service\nfrom app.core.service import run\n",
    "app/core/service.py": "from app.db import store\n",
    "app/db/store.py": "x = 1\n",
    "app/db/__init__.py": "",
    "app/core/__init__.py": "",
    "app/__init__.py": "",
    "tests/test_x.py": "from app.api import routes\n",
}


def test_module_dependencies_at_file_level(db, tmp_path, fake_embedder):
    repo_id = build(db, tmp_path, fake_embedder, MODULE_REPO)
    with db.repo(repo_id) as conn:
        d = modules_diagram(conn)
    assert d["mermaid"].startswith("flowchart LR") and d["level"] == "file"
    assert edges_of(d["mermaid"]) >= {
        ("app/api/routes.py", "app/core/service.py"),
        ("app/core/service.py", "app/db/store.py"),
        ("tests/test_x.py", "app/api/routes.py"),
    }
    assert d["truncated"] is False and d["cycles"] == []
    kinds = {n["label"]: n["kind"] for n in d["node_index"]}
    assert kinds["app/db/store.py"] == "file"
    assert {n["path"] for n in d["node_index"]} >= {"app/db/store.py"}  # clickable


def test_folders_collapse_until_the_diagram_fits(db, tmp_path, fake_embedder):
    repo_id = build(db, tmp_path, fake_embedder, MODULE_REPO)
    with db.repo(repo_id) as conn:
        d = modules_diagram(conn, limit=5)
    assert d["level"] == "dir" and d["nodes"] <= 5
    assert all(n["path"] is None for n in d["node_index"])  # folders are not files
    assert any(a.startswith("app/") for a, _ in edges_of(d["mermaid"]))


def test_a_scope_shows_outside_dependencies_as_external_nodes(db, tmp_path, fake_embedder):
    repo_id = build(db, tmp_path, fake_embedder, MODULE_REPO)
    with db.repo(repo_id) as conn:
        d = modules_diagram(conn, scope="app/core")
    assert edges_of(d["mermaid"]) == {("app/core/service.py", "app/db")}  # `app/db` is outside
    assert "classDef external" in d["mermaid"]
    assert {n["kind"] for n in d["node_index"]} == {"file", "external"}
    assert not any("tests/" in n["label"] for n in d["node_index"])


def test_import_cycles_are_highlighted(db, tmp_path, fake_embedder):
    repo_id = build(
        db,
        tmp_path,
        fake_embedder,
        {"a.py": "import b\n", "b.py": "import c\n", "c.py": "import b\n", "d.py": "import a\n"},
    )
    with db.repo(repo_id) as conn:
        d = modules_diagram(conn)
    assert d["cycles"] == [["b.py", "c.py"]]
    assert "classDef cycle" in d["mermaid"] and "linkStyle" in d["mermaid"]
    cyc_line = next(ln for ln in d["mermaid"].splitlines() if ln.strip().startswith("class n"))
    assert len(cyc_line.split(",")) == 2  # only b.py and c.py carry the cycle style


def test_too_many_nodes_are_truncated_to_the_best_connected(db, tmp_path, fake_embedder):
    files = {f"pkg{i}/m.py": "" for i in range(12)}
    files["hub/m.py"] = "\n".join(f"import pkg{i}.m" for i in range(12))
    repo_id = build(db, tmp_path, fake_embedder, files)
    with db.repo(repo_id) as conn:
        d = modules_diagram(conn, limit=5)
    assert d["nodes"] <= 5 and d["truncated"] is True
    assert any(n["label"] == "hub" for n in d["node_index"])  # the hub is kept


def test_labels_are_sanitized():
    assert label('a "quoted" <b> & c;d %% e') == "a #quot;quoted#quot; #lt;b#gt; #amp; c;d % e"
    assert label("x|y#z") == "x#124;y#35;z"
    assert label("   ") == "?" and label("") == "?"
    assert label("a/" * 40).startswith("…") and len(label("a/" * 40)) <= 44
    assert ";" not in message("foo(); bar()")


def test_hostile_file_names_cannot_break_the_diagram(db, tmp_path, fake_embedder):
    repo_id = build(
        db,
        tmp_path,
        fake_embedder,
        {"we'ird;na&me#1 (x) [y] {z} %%.py": "import other\n", "other.py": "x = 1\n"},
    )
    with db.repo(repo_id) as conn:
        d = modules_diagram(conn)
    text = d["mermaid"]
    assert "%%" not in text  # a comment marker would swallow the rest of the line
    assert "#amp;" in text and "#35;" in text  # `&` and `#` are entity-encoded
    assert "we'ird;na" in text  # `;` is harmless inside a quoted label
    assert all(line.count('"') % 2 == 0 for line in text.splitlines())  # every label is closed


FLOW_REPO = {
    "svc/handlers.py": """
    def handle(req):
        user = load_user(req)
        return render(user)

    def load_user(req):
        return query(req)

    def render(user):
        return format_name(user)
    """,
    "svc/db.py": """
    def query(req):
        return handle_error(req)

    def handle_error(req):
        return handle(req)
    """,
    "svc/fmt.py": """
    def format_name(user):
        return user
    """,
}


def test_flow_flowchart_shows_calls_down_to_the_depth(db, tmp_path, fake_embedder):
    repo_id = build(db, tmp_path, fake_embedder, FLOW_REPO)
    with db.repo(repo_id) as conn:
        entry = conn.execute("SELECT id FROM symbols WHERE name = 'handle'").fetchone()["id"]
        d = flow_diagram(conn, entry, depth=2)
    text = d["mermaid"]
    names = dict(re.findall(r'^\s+(n\d+)(?:\(\[|\[)"(.*?)"', text, re.M))
    assert names and text.startswith("flowchart TD")
    arrows = re.findall(r"^\s+(n\d+) (?:-->|-\.->) (n\d+)", text, re.M)
    pairs = {(names[a], names[b]) for a, b in arrows}
    assert {
        ("handle", "load_user"),
        ("handle", "render"),
        ("load_user", "query"),
        ("render", "format_name"),
    } <= pairs
    assert "handle_error" not in names.values()  # depth 3: one level too far
    assert re.search(r'n0\(\["handle"\]\)', text)  # the entry point is a stadium
    assert d["depth"] == 2 and d["entry"]["name"] == "handle"
    assert all(n["path"] and n["line"] for n in d["node_index"])


def test_flow_survives_recursion(db, tmp_path, fake_embedder):
    repo_id = build(db, tmp_path, fake_embedder, FLOW_REPO)
    with db.repo(repo_id) as conn:
        entry = conn.execute("SELECT id FROM symbols WHERE name = 'handle'").fetchone()["id"]
        d = flow_diagram(conn, entry, depth=5)  # handle -> ... -> handle_error -> handle: a cycle
    assert "handle_error" in d["mermaid"] and d["truncated"] is False
    assert d["nodes"] == 6


def test_flow_caps_the_number_of_nodes(db, tmp_path, fake_embedder):
    body = "def hub():\n" + "\n".join(f"    f{i}()" for i in range(20)) + "\n"
    body += "\n".join(f"def f{i}():\n    pass\n" for i in range(20))
    repo_id = build(db, tmp_path, fake_embedder, {"m.py": body})
    with db.repo(repo_id) as conn:
        entry = conn.execute("SELECT id FROM symbols WHERE name = 'hub'").fetchone()["id"]
        d = flow_diagram(conn, entry, limit=8)
    assert d["nodes"] == 8 and d["truncated"] is True


def test_flow_sequence_diagram(db, tmp_path, fake_embedder):
    repo_id = build(db, tmp_path, fake_embedder, FLOW_REPO)
    with db.repo(repo_id) as conn:
        entry = conn.execute("SELECT id FROM symbols WHERE name = 'handle'").fetchone()["id"]
        d = flow_diagram(conn, entry, depth=2, style="sequence")
    lines = d["mermaid"].splitlines()
    assert lines[0] == "sequenceDiagram"
    assert "  participant p0 as handlers" in lines
    body = [line.strip() for line in lines if "->>" in line]
    assert body[0] == "p0->>p0: load_user()"  # in the order the calls appear
    assert any(line.endswith(": query()") for line in body)
    assert any("format_name()" in line for line in body)
    assert d["style"] == "sequence" and d["node_index"] == []


ROUTES = (
    "from fastapi import FastAPI\n"
    "from svc.handlers import handle\n"
    "app = FastAPI()\n"
    "@app.get('/x')\n"
    "def x():\n"
    "    return handle(1)\n"
    "app.add_api_route('/inline', lambda: 1, methods=['GET'])\n"
)


@pytest.fixture
def client(config, tmp_path, fake_embedder):
    root = tmp_path / "dapi"
    for rel, content in {**FLOW_REPO, "svc/routes.py": ROUTES}.items():
        write(root, rel, content)
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=FakeLLM())
    with TestClient(create_app(state, warm_up=False)) as test_client:
        repo = test_client.post("/api/repos", json={"path": str(root)}).json()
        index(test_client, repo["id"])
        test_client.base = f"/api/repos/{repo['id']}"
        yield test_client


def test_api(client):
    base = client.base
    modules = client.get(f"{base}/diagram", params={"type": "modules"}).json()
    assert modules["type"] == "modules" and modules["mermaid"].startswith("flowchart LR")
    assert "svc" in client.get(f"{base}/diagram/scopes").json()["folders"]

    symbols = client.get(f"{base}/symbols", params={"q": "handle"}).json()["symbols"]
    entry = next(s["id"] for s in symbols if s["name"] == "handle")
    flow = client.get(f"{base}/diagram", params={"type": "flow", "symbol_id": entry}).json()
    assert flow["style"] == "flowchart" and flow["nodes"] >= 4
    seq = client.get(
        f"{base}/diagram", params={"type": "flow", "symbol_id": entry, "style": "sequence"}
    )
    assert seq.json()["mermaid"].startswith("sequenceDiagram")

    # Starting from an endpoint uses its handler.
    endpoints = client.get(f"{base}/endpoints").json()["endpoints"]
    real = next(e for e in endpoints if e["path"] == "/x")
    flow_of = client.get(f"{base}/diagram", params={"type": "flow", "endpoint_id": real["id"]})
    assert flow_of.status_code == 200 and flow_of.json()["entry"]["name"] == "x"
    inline = next(e for e in endpoints if e["path"] == "/inline")
    inline_flow = client.get(
        f"{base}/diagram", params={"type": "flow", "endpoint_id": inline["id"]}
    )
    assert inline_flow.status_code == 422
    assert (
        client.get(f"{base}/diagram", params={"type": "flow", "endpoint_id": 9999}).status_code
        == 404
    )

    assert client.get(f"{base}/diagram", params={"type": "flow"}).status_code == 422
    assert (
        client.get(f"{base}/diagram", params={"type": "flow", "symbol_id": 9999}).status_code == 404
    )
    assert client.get(f"{base}/diagram", params={"type": "pie"}).status_code == 422
    assert client.get(f"{base}/diagram", params={"style": "gantt"}).status_code == 422
    assert client.get("/api/repos/nope/diagram").status_code == 404
