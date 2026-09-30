"""Call resolution and confidence, the call graph, migrations and incremental analysis."""

from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.analysis import weakest
from app.analysis.graph import CodeGraph, find_cycles, module_graph
from app.analysis.pipeline import run_analysis
from app.analysis.registry import default_analyzers
from app.db import LATEST_VERSION, connect, migrate
from app.main import create_app
from app.state import AppState
from tests.conftest import FakeLLM, write
from tests.test_api import OfflineFoundry, index
from tests.test_indexer import run_index


@pytest.fixture
def py_repo(tmp_path):
    root = tmp_path / "pyrepo"
    write(root, "app/__init__.py", "")
    write(
        root,
        "app/auth.py",
        """
        import json
        from app import util
        from app.util import helper

        def login(user):
            token = issue_token(user)
            helper(token)
            util.shout(token)
            json.dumps(token)
            return token

        def issue_token(user):
            return sign(user)

        def sign(x):
            return x
        """,
    )
    write(
        root,
        "app/util.py",
        """
        def helper(t):
            return t

        def shout(t):
            return t

        class Store:
            def get(self, k):
                return self.load(k)

            def load(self, k):
                return k

            def fetch(self, k):
                return k
        """,
    )
    write(root, "app/other.py", "def dup():\n    pass\n")
    write(root, "app/third.py", "def dup():\n    pass\n\ndef only_here():\n    pass\n")
    write(
        root,
        "app/main.py",
        """
        def run():
            dup()
            only_here()
            store.fetch(1)
            cache.get(1)
            missing_thing()
        """,
    )
    write(
        root,
        "tests/test_auth.py",
        """
        from app.auth import login

        def test_login():
            login("x")
        """,
    )
    return root


@pytest.fixture
def indexed(db, py_repo, fake_embedder):
    repo = db.add_repo(str(py_repo), "pyrepo")
    run_index(db, repo, py_repo, fake_embedder)
    return repo


def refs(db, repo_id, kind="call"):
    """Resolved references as (from, to name, to symbol or None, confidence) tuples."""
    with db.repo(repo_id) as conn:
        rows = conn.execute(
            "SELECT f.qualified_name AS src, r.to_name, t.qualified_name AS dst, t.file_path AS dst_file,"
            " r.confidence FROM symbol_references r"
            " LEFT JOIN symbols f ON f.id = r.from_symbol_id"
            " LEFT JOIN symbols t ON t.id = r.to_symbol_id WHERE r.kind = ?",
            (kind,),
        ).fetchall()
    return {(r["src"], r["to_name"], r["dst"], r["dst_file"], r["confidence"]) for r in rows}


def test_indexing_runs_the_analysis(db, indexed):
    with db.repo(indexed["id"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] >= 10
        analyzers = {r[0] for r in conn.execute("SELECT analyzer FROM analysis_state")}
    assert analyzers == {"symbols", "env"}


def test_resolution_and_confidence_levels(db, indexed):
    r = refs(db, indexed["id"])
    # Same file: high.
    assert ("login", "issue_token", "issue_token", "app/auth.py", "high") in r
    assert ("issue_token", "sign", "sign", "app/auth.py", "high") in r
    # Imported by name, and through a module alias: high.
    assert ("login", "helper", "helper", "app/util.py", "high") in r
    assert ("login", "shout", "shout", "app/util.py", "high") in r
    # `self.load()` inside the same class: high.
    assert ("Store.get", "load", "Store.load", "app/util.py", "high") in r
    # A tests calls the function it imports.
    assert ("test_login", "login", "login", "app/auth.py", "high") in r
    # Unique definition elsewhere, not imported: medium.
    assert ("run", "only_here", "only_here", "app/third.py", "medium") in r
    # Two definitions share a name: one low edge to each.
    assert ("run", "dup", "dup", "app/other.py", "low") in r
    assert ("run", "dup", "dup", "app/third.py", "low") in r
    # A method call on an object of unknown type is only a low confidence guess.
    assert ("run", "fetch", "Store.fetch", "app/util.py", "low") in r
    # `cache.get()` looks like a built-in's method: not linked to our own `Store.get`.
    assert ("run", "get", None, None, "low") in r
    # Nothing is called `dumps` or `missing_thing` in the repo: unresolved.
    assert ("login", "dumps", None, None, "low") in r
    assert ("run", "missing_thing", None, None, "low") in r


def test_named_imports_count_as_uses(db, indexed):
    imports = refs(db, indexed["id"], kind="import")
    assert (None, "helper", "helper", "app/util.py", "high") in imports
    assert (None, "login", "login", "app/auth.py", "high") in imports


def test_inheritance_edges(db, tmp_path, fake_embedder):
    root = tmp_path / "inh"
    write(root, "base.py", "class Base:\n    pass\n")
    write(root, "child.py", "from base import Base\n\nclass Child(Base):\n    pass\n")
    repo = db.add_repo(str(root), "inh")
    run_index(db, repo, root, fake_embedder)
    assert ("Child", "Base", "Base", "base.py", "high") in refs(db, repo["id"], kind="inherit")


def test_imports_resolve_across_languages(db, tmp_path, fake_embedder):
    root = tmp_path / "poly"
    write(root, "web/util.ts", "export const a = 1;\n")
    write(root, "web/lib/x.ts", "export function f() {}\n")
    write(
        root,
        "web/app.ts",
        'import { a } from "./util";\nimport { f } from "@/lib/x";\nimport React from "react";\n',
    )
    write(
        root,
        "src/main/java/com/acme/util/Helper.java",
        "package com.acme.util;\npublic class Helper {}\n",
    )
    write(
        root,
        "src/main/java/com/acme/App.java",
        "package com.acme;\nimport com.acme.util.Helper;\nclass App {}\n",
    )
    write(root, "Models/User.cs", "namespace Acme.Models { public class User {} }\n")
    write(root, "Program.cs", "using Acme.Models;\nclass P {}\n")
    write(root, "internal/auth/auth.go", "package auth\nfunc Check() {}\n")
    write(
        root,
        "cmd/main.go",
        'package main\nimport "example.com/app/internal/auth"\nfunc main() { auth.Check() }\n',
    )
    repo = db.add_repo(str(root), "poly")
    run_index(db, repo, root, fake_embedder)
    with db.repo(repo["id"]) as conn:
        resolved = {
            (r[0], r[1]) for r in conn.execute("SELECT file_path, resolved_file_path FROM imports")
        }
        unresolved = {
            r[0]
            for r in conn.execute("SELECT module FROM imports WHERE resolved_file_path IS NULL")
        }
    assert ("web/app.ts", "web/util.ts") in resolved
    assert (
        "web/app.ts",
        "web/lib/x.ts",
    ) in resolved  # `@/lib/x`: found under the importing file's own folder
    assert (
        "src/main/java/com/acme/App.java",
        "src/main/java/com/acme/util/Helper.java",
    ) in resolved
    assert ("Program.cs", "Models/User.cs") in resolved
    assert ("cmd/main.go", "internal/auth/auth.go") in resolved
    assert "react" in unresolved  # third-party packages stay unresolved
    with db.repo(repo["id"]) as conn:
        graph = module_graph(conn, level="dir")
    assert {"from": "cmd", "to": "internal/auth", "weight": 1} in graph["edges"]


def test_callers_are_grouped_by_depth_with_the_weakest_confidence(db, indexed):
    with db.repo(indexed["id"]) as conn:
        graph = CodeGraph(conn)
        sign = next(n for n in graph.nodes.values() if n.qualified_name == "sign")
        found = graph.callers(sign.id, depth=3)
    by_name = {h.node.qualified_name: h for h in found.hits}
    assert by_name["issue_token"].depth == 1 and by_name["issue_token"].confidence == "high"
    assert by_name["login"].depth == 2
    assert by_name["test_login"].depth == 3 and by_name["test_login"].node.is_test
    assert not found.truncated
    with db.repo(indexed["id"]) as conn:
        shallow = CodeGraph(conn).callers(sign.id, depth=1)
    assert [h.node.qualified_name for h in shallow.hits] == ["issue_token"]


def test_graph_handles_cycles_and_caps_nodes(db, tmp_path, fake_embedder):
    root = tmp_path / "cyc"
    calls = "\n".join(f"def f{i}():\n    f{(i + 1) % 8}()\n" for i in range(8))
    write(root, "loop.py", calls)
    repo = db.add_repo(str(root), "cyc")
    run_index(db, repo, root, fake_embedder)
    with db.repo(repo["id"]) as conn:
        graph = CodeGraph(conn)
        f0 = next(n for n in graph.nodes.values() if n.name == "f0")
        full = graph.callers(f0.id, depth=20)
        capped = graph.callers(f0.id, depth=20, max_nodes=3)
    assert len(full.hits) == 7 and not full.truncated  # every other node once, no infinite loop
    assert len(capped.hits) == 3 and capped.truncated


def test_find_cycles():
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"},
             {"from": "c", "to": "d"}]  # fmt: skip
    assert find_cycles(edges) == [["a", "b", "c"]]
    assert find_cycles([{"from": "x", "to": "y"}]) == []


def test_weakest_confidence():
    assert weakest("high", "low") == "low"
    assert weakest("medium", "high") == "medium"


def test_migration_upgrades_an_old_database_and_is_idempotent(tmp_path):
    from app.db import REPO_SCHEMA

    path = tmp_path / "old.db"
    conn = connect(path)
    conn.executescript(REPO_SCHEMA)  # what Lodestar wrote before Insights: user_version is 0
    conn.execute(
        "INSERT INTO chunks (repo_id, file_path, language, symbol_kind, start_line, end_line,"
        " content, file_hash) VALUES ('r', 'a.py', 'python', 'function', 1, 2, 'x', 'h')"
    )
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0

    assert migrate(conn) == LATEST_VERSION
    assert migrate(conn) == LATEST_VERSION  # running it again changes nothing
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"symbols", "symbol_references", "imports", "analysis_files"} <= tables
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1  # data kept
    conn.close()


def test_only_changed_files_are_reanalyzed(db, indexed, py_repo, fake_embedder):
    repo_id = indexed["id"]

    def stamps():
        with db.repo(repo_id) as conn:
            return {
                r["path"]: r["analyzed_at"]
                for r in conn.execute("SELECT path, analyzed_at FROM analysis_files")
            }

    before = stamps()
    time.sleep(0.02)
    nothing = run_analysis(db, repo_id, py_repo, default_analyzers())
    assert nothing.analyzed_files == 0 and stamps() == before

    write(py_repo, "app/other.py", "def dup():\n    pass\n\ndef added():\n    pass\n")
    run_index(db, indexed, py_repo, fake_embedder)
    after = stamps()
    assert after["app/other.py"] > before["app/other.py"]
    assert all(after[p] == before[p] for p in before if p != "app/other.py")
    with db.repo(repo_id) as conn:
        names = {
            r[0] for r in conn.execute("SELECT name FROM symbols WHERE file_path = 'app/other.py'")
        }
    assert names == {"dup", "added"}

    (py_repo / "app" / "other.py").unlink()
    run_index(db, indexed, py_repo, fake_embedder)
    with db.repo(repo_id) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE file_path = 'app/other.py'"
            ).fetchone()[0]
            == 0
        )
    # With one `dup` gone the other is now the unique definition.
    assert ("run", "dup", "dup", "app/third.py", "medium") in refs(db, repo_id)


def test_reanalyze_backfills_an_index_made_before_insights(db, indexed, py_repo):
    repo_id = indexed["id"]
    with db.repo(repo_id) as conn:  # pretend this index predates the analysis
        for table in ("symbols", "call_sites", "import_statements", "symbol_references",
                      "imports", "analysis_files", "analysis_state"):  # fmt: skip
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
    summary = run_analysis(db, repo_id, py_repo, default_analyzers())
    assert summary.analyzed_files > 0
    assert ("login", "helper", "helper", "app/util.py", "high") in refs(db, repo_id)

    forced = run_analysis(db, repo_id, py_repo, default_analyzers(), force=True)
    assert forced.analyzed_files == summary.analyzed_files


def test_a_file_that_fails_to_analyze_does_not_stop_the_run(db, indexed, py_repo, monkeypatch):
    from app.analysis import extract

    real = extract.extract_facts

    def flaky(path, language, source, root):
        if path == "app/util.py":
            raise RuntimeError("boom")
        return real(path, language, source, root)

    monkeypatch.setattr("app.analysis.symbols.extract_facts", flaky)
    summary = run_analysis(db, indexed["id"], py_repo, default_analyzers(), force=True)
    assert summary.failed == 1 and summary.analyzed_files > 3
    with db.repo(indexed["id"]) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM symbols WHERE file_path = 'app/util.py'").fetchone()[
                0
            ]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM symbols WHERE file_path = 'app/auth.py'").fetchone()[
                0
            ]
            > 0
        )


def test_unsupported_languages_are_skipped(db, tmp_path, fake_embedder):
    root = tmp_path / "rb"
    write(root, "thing.rb", "def hello\n  puts 1\nend\n")
    write(root, "app.py", "def f():\n    pass\n")
    repo = db.add_repo(str(root), "rb")
    run_index(db, repo, root, fake_embedder)
    with db.repo(repo["id"]) as conn:
        assert {r[0] for r in conn.execute("SELECT DISTINCT file_path FROM symbols")} == {"app.py"}


def test_analyze_and_status_endpoints(config, py_repo, fake_embedder):
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=FakeLLM())
    with TestClient(create_app(state, warm_up=False)) as client:
        repo = client.post("/api/repos", json={"path": str(py_repo)}).json()
        before = client.get(f"/api/repos/{repo['id']}/insights/status").json()
        assert before["analyzed"] is False and before["counts"]["symbols"] == 0
        index(client, repo["id"])
        after = client.get(f"/api/repos/{repo['id']}/insights/status").json()
        assert after["analyzed"] is True and after["counts"]["symbols"] > 5
        assert after["counts"]["undocumented"] > 0 and after["last_analyzed_at"]

        assert client.post(f"/api/repos/{repo['id']}/analyze").status_code == 202
        for _ in range(100):
            job = client.get(f"/api/repos/{repo['id']}/index/stream").text
            if "event: done" in job:
                break
            time.sleep(0.05)
        assert "event: done" in job
        assert client.post("/api/repos/nope/analyze").status_code == 404


def test_sqlite_row_helper_sanity():
    # `refs` relies on sqlite3.Row supporting name access; guard against a driver change.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
