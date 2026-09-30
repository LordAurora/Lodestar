"""Impact analysis: callers by depth, tests, blast radius, symbol search and the summary."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.analysis.graph import Hit, Node
from app.analysis.impact import (
    blast_radius,
    compute_impact,
    fallback_summary,
    search_symbols,
    summary_facts,
    usable_summary,
)
from app.main import create_app
from app.state import AppState
from tests.conftest import FakeLLM, write
from tests.test_api import OfflineFoundry, index, parse_sse
from tests.test_indexer import run_index

REPO = {
    "lib/core.py": """
    def sign(x):
        return x

    class Signer:
        def run(self, x):
            return sign(x)

    def issue(u):
        return sign(u)
    """,
    "lib/api.py": """
    from lib.core import issue

    def login(u):
        return issue(u)
    """,
    "lib/uses.py": """
    from lib.core import Signer

    def make():
        return Signer()
    """,
    "lib/more.py": """
    def use(s):
        return s.run(1)
    """,
    "tests/test_api.py": """
    from lib.api import login

    def test_login():
        login("x")
    """,
    "main.py": """
    from lib.api import login
    login("boot")
    """,
}


@pytest.fixture
def indexed(db, tmp_path, fake_embedder):
    root = tmp_path / "impact"
    for rel, content in REPO.items():
        write(root, rel, content)
    repo = db.add_repo(str(root), "impact")
    run_index(db, repo, root, fake_embedder)
    return repo


def symbol_id(db, repo_id, qualified):
    with db.repo(repo_id) as conn:
        return conn.execute(
            "SELECT id FROM symbols WHERE qualified_name = ?", (qualified,)
        ).fetchone()["id"]


def report(db, repo_id, qualified, depth=3):
    with db.repo(repo_id) as conn:
        found = search_symbols(conn, qualified)
        return compute_impact(
            conn, next(s["id"] for s in found if s["qualified_name"] == qualified), depth
        )


def names(level):
    return {i["qualified_name"] for i in level["items"]}


def test_callers_are_grouped_by_depth_and_tests_are_listed(db, indexed):
    r = report(db, indexed["id"], "sign")
    by_depth = {lvl["depth"]: names(lvl) for lvl in r["levels"]}
    # `use` calls `s.run(...)` on an object of unknown type, so it reaches sign only through the
    # doubtful edge to Signer.run.
    assert by_depth == {1: {"issue", "Signer.run"}, 2: {"login", "use"}, 3: {"test_login"}}
    assert [t["qualified_name"] for t in r["tests"]] == ["test_login"]
    assert r["tests"][0]["depth"] == 3 and r["tests"][0]["call_file"] == "tests/test_api.py"
    assert r["total"] == 5 and r["direct"] == 2
    assert r["files"] == 4 and r["production_files"] == 3  # core, api, more (plus the test file)
    assert r["target"]["file_path"] == "lib/core.py"


def test_depth_limits_the_search_and_is_clamped(db, indexed):
    assert [lvl["depth"] for lvl in report(db, indexed["id"], "sign", depth=1)["levels"]] == [1]
    assert report(db, indexed["id"], "sign", depth=0)["depth"] == 1
    assert report(db, indexed["id"], "sign", depth=99)["depth"] == 5


def test_score_uses_depth_and_confidence_and_ignores_tests(db, indexed):
    r = report(db, indexed["id"], "sign")
    # issue 1.0 + Signer.run 1.0 + login 0.5 + use (depth 2, low confidence) 0.5 * 0.3;
    # test_login is a test and adds nothing.
    assert r["score"] == 2.65 and r["level"] == "Low"
    assert "1/2" in r["formula"]


def node(is_test=False, name="n"):
    return Node(1, name, name, "function", "a.py", 1, 2, is_test)


@pytest.mark.parametrize(
    ("hits", "score", "level"),
    [
        ([], 0.0, "Low"),
        ([Hit(node(), 1, 1, "a.py", "high")] * 3, 3.0, "Medium"),
        ([Hit(node(), 1, 1, "a.py", "high")] * 10, 10.0, "High"),
        ([Hit(node(), 1, 1, "a.py", "low")] * 10, 3.0, "Medium"),  # doubtful links weigh 0.3
        ([Hit(node(), 3, 1, "a.py", "high")] * 8, 2.0, "Low"),  # distant callers weigh 1/4
        ([Hit(node(is_test=True), 1, 1, "a.py", "high")] * 50, 0.0, "Low"),
    ],
)
def test_blast_radius_formula(hits, score, level):
    assert blast_radius(hits) == (score, level)


def test_class_impact_includes_callers_of_its_methods(db, indexed):
    r = report(db, indexed["id"], "Signer")
    by_name = {i["qualified_name"]: i for lvl in r["levels"] for i in lvl["items"]}
    assert by_name["make"]["confidence"] == "high"  # `Signer()` names the class
    assert by_name["use"]["confidence"] == "low"  # `s.run()`: the receiver's type is unknown
    assert r["low_confidence"] == 1 and r["unresolved_warning"] is True


def test_confident_impact_has_no_warning(db, indexed):
    assert report(db, indexed["id"], "issue")["unresolved_warning"] is False
    assert report(db, indexed["id"], "sign")["unresolved_warning"] is True  # reached via `use`


def test_module_level_uses_are_reported(db, indexed):
    r = report(db, indexed["id"], "login")
    assert [(m["file_path"], m["line"]) for m in r["module_level"]] == [("main.py", 2)]


def test_a_symbol_without_callers(db, indexed):
    r = report(db, indexed["id"], "test_login")
    assert r["total"] == 0 and r["levels"] == [] and r["level"] == "Low"
    assert "Nothing calls" in fallback_summary(r)


def test_search_ranks_exact_prefix_substring_and_fuzzy(db, indexed):
    with db.repo(indexed["id"]) as conn:

        def find(q, **kw):
            return [s["qualified_name"] for s in search_symbols(conn, q, **kw)]

        assert find("sign")[0] == "sign"  # exact beats the `Signer` prefix match
        assert find("sign")[1] == "Signer"
        assert "Signer.run" in find("run") and find("run")[0] == "Signer.run"
        assert find("ogi")[0] == "login"  # substring
        assert "test_login" in find("tlgn")  # fuzzy: letters in order
        assert find("zzzz") == []
        assert set(find("s", kind="class")) == {"Signer"}
        assert len(search_symbols(conn, "", limit=2)) == 2
        assert search_symbols(conn, "")[0]["callers"] >= search_symbols(conn, "")[-1]["callers"]


def test_summary_helpers(db, indexed):
    r = report(db, indexed["id"], "sign")
    facts = summary_facts(r)
    assert "`sign`" in facts and "Blast radius: Low (score 2.65)" in facts and "`issue`" in facts
    assert "affected tests" in facts
    assert usable_summary("First sentence. Second sentence. Third sentence.") is not None
    assert usable_summary("One.") is None  # too short
    assert usable_summary("Sentence one. Sentence two.\n```py\ncode\n```") is None
    assert usable_summary("x. " * 400) is None  # far too long
    assert (
        usable_summary("Risks include:\n\n1. **Logic**: it may break.\n\n2. Speed. More.") is None
    )
    assert usable_summary("Intro one. Intro two.\n- a bullet") is None
    assert usable_summary("<think>hidden</think>Real one. Real two.") == "Real one. Real two."


class SummaryLLM(FakeLLM):
    async def stream_chat(self, messages, model):
        self.stream_calls.append(messages)
        for word in self.answer.split(" "):
            yield word + " "


class BrokenLLM(FakeLLM):
    async def stream_chat(self, messages, model):
        raise RuntimeError("Foundry Local is not running")
        yield ""  # pragma: no cover


@pytest.fixture
def client_for(config, tmp_path, fake_embedder):
    def make(llm):
        root = tmp_path / f"api{id(llm)}"
        for rel, content in REPO.items():
            write(root, rel, content)
        state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=llm)
        client = TestClient(create_app(state, warm_up=False))
        client.__enter__()
        repo = client.post("/api/repos", json={"path": str(root)}).json()
        index(client, repo["id"])
        client.base = f"/api/repos/{repo['id']}"
        return client

    return make


def first_id(client, name):
    found = client.get(f"{client.base}/symbols", params={"q": name}).json()["symbols"]
    return next(s["id"] for s in found if s["qualified_name"] == name)


def test_endpoints(client_for):
    client = client_for(FakeLLM())
    listing = client.get(f"{client.base}/symbols", params={"q": "sig"}).json()["symbols"]
    assert listing[0]["name"] == "sign" and listing[0]["callers"] == 2
    assert client.get(f"{client.base}/symbols", params={"kind": "nope"}).status_code == 422

    data = client.get(
        f"{client.base}/impact/{first_id(client, 'sign')}", params={"depth": 2}
    ).json()
    assert [lvl["depth"] for lvl in data["levels"]] == [1, 2] and data["level"] == "Low"
    assert client.get(f"{client.base}/impact/999999").status_code == 404
    assert client.get("/api/repos/nope/symbols").status_code == 404


def test_summary_streams_and_validates(client_for):
    good = SummaryLLM(
        "Changing sign touches two callers. The blast radius is low. Tests would notice."
    )
    client = client_for(good)
    url = f"{client.base}/impact/{first_id(client, 'sign')}/summary"
    events = parse_sse(client.post(url).text)
    assert [e for e, _ in events][-1] == "done" and any(e == "token" for e, _ in events)
    final = events[-1][1]
    assert final["fallback"] is False and final["text"].startswith("Changing sign touches")
    messages = good.stream_calls[0]
    assert "Mention only names and numbers" in messages[0]["content"]
    assert "`sign`" in messages[-1]["content"]  # only computed facts are sent, no source code
    assert "def " not in messages[-1]["content"]

    junk = client_for(SummaryLLM("ok"))
    final = parse_sse(junk.post(f"{junk.base}/impact/{first_id(junk, 'sign')}/summary").text)[-1][1]
    assert final["fallback"] is True and "may affect 5 symbols" in final["text"]

    broken = client_for(BrokenLLM())
    final = parse_sse(broken.post(f"{broken.base}/impact/{first_id(broken, 'sign')}/summary").text)[
        -1
    ][1]
    assert final["fallback"] is True and "not running" in final["reason"]
    assert broken.post(f"{broken.base}/impact/999999/summary").status_code == 404
