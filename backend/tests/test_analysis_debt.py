"""Tech debt board: comment-only detection, git blame, grouping, topics and the API."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.analysis.debt import (
    DebtAnalyzer,
    TagMatcher,
    clean_label,
    cluster_vectors,
    counters,
    export_csv,
    export_markdown,
    group_items,
    keyword_label,
    list_items,
    parse_blame,
    parse_tags,
)
from app.analysis.pipeline import FileContext, run_analysis
from app.chunking import detect_language
from app.db import migrate
from app.main import create_app
from app.state import AppState
from tests.conftest import FakeLLM, write
from tests.test_api import OfflineFoundry, index
from tests.test_indexer import run_index

TAGS = parse_tags("TODO,FIXME,HACK,XXX,BUG,NOTE")


def found(path: str, source: str, tags=TAGS) -> list[tuple[str, str, int, str | None]]:
    """Run the analyzer on one file in a scratch database; return (tag, text, line, assignee)."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    body = textwrap.dedent(source).lstrip("\n")
    ctx = FileContext(path, detect_language(path), body.encode(), "h")
    DebtAnalyzer(tags).analyze(ctx, conn, "r")
    rows = conn.execute("SELECT tag, text, line, assignee FROM debt_items ORDER BY line").fetchall()
    return [tuple(r) for r in rows]


def test_python_only_real_comments_count():
    assert found(
        "a.py",
        '''
        # TODO: real one
        x = "TODO: inside a string"
        def f():
            """TODO: a docstring is a string, not a comment"""
            y = 1  # FIXME(bob): trailing comment
            # note that lowercase prose is not a tag
            # NOTE: uppercase is
            # but TODO mid-sentence is not at the start
        ''',
    ) == [
        ("TODO", "real one", 1, None),
        ("FIXME", "trailing comment", 5, "bob"),
        ("NOTE", "uppercase is", 7, None),
    ]


def test_block_comments_and_other_grammars():
    assert found(
        "a.ts",
        """
        // TODO: line
        const s = "FIXME: string";
        /* HACK: block
         * BUG: second line of the same block
         */
        const t = `TODO ${1}`;
        """,
    ) == [
        ("TODO", "line", 1, None),
        ("HACK", "block", 3, None),
        ("BUG", "second line of the same block", 4, None),
    ]
    assert found("a.go", '// XXX: careful\nvar s = "TODO: no"\n/* BUG: x */\n') == [
        ("XXX", "careful", 1, None),
        ("BUG", "x", 3, None),
    ]
    assert found(
        "A.java", '// TODO: a\n/** NOTE: javadoc */\nclass A { String s = "FIXME: no"; }\n'
    ) == [
        ("TODO", "a", 1, None),
        ("NOTE", "javadoc", 2, None),
    ]
    assert found("A.cs", '// TODO: a\nclass A { string s = "HACK: no"; }\n') == [
        ("TODO", "a", 1, None)
    ]


def test_regex_fallback_for_files_without_a_grammar():
    assert found("job.rb", 'puts "x" # TODO: ruby comment\nputs "TODO: not at a marker"\n') == [
        ("TODO", "ruby comment", 1, None)
    ]
    assert found("q.sql", "-- FIXME: slow query\nSELECT 1;\n") == [("FIXME", "slow query", 1, None)]
    assert found("run.sh", "#!/bin/sh\n# HACK: quoting\n") == [("HACK", "quoting", 2, None)]
    assert found(
        "README.md",
        "- TODO: write docs\n- [ ] FIXME: broken link\nTodo list below\nA TODO in prose\n",
    ) == [
        ("TODO", "write docs", 1, None),
        ("FIXME", "broken link", 2, None),
    ]


def test_configurable_tags():
    tags = parse_tags("warn, todo ,")
    assert tags == ("WARN", "TODO")
    assert found("a.py", "# WARN: custom\n# FIXME: not configured\n# TODO: yes\n", tags) == [
        ("WARN", "custom", 1, None),
        ("TODO", "yes", 3, None),
    ]
    assert parse_tags("") == parse_tags(",,")  # falls back to the defaults
    assert DebtAnalyzer("A,B").version != DebtAnalyzer("A,C").version  # new tags re-analyze


def test_tag_matcher_handles_odd_shapes():
    m = TagMatcher(TAGS)
    assert [h.tag for h in m.in_comment("# TODO", 1)] == ["TODO"]
    assert m.in_comment("# TODO", 1)[0].text == ""
    assert m.in_comment("// TODO(a b): text", 1)[0].assignee == "a b"
    assert m.in_comment("/* FIXME - dash separator */", 1)[0].text == "dash separator"
    assert m.in_comment("// TODOS are not tags", 1) == []


@pytest.fixture
def debt_repo(tmp_path):
    root = tmp_path / "debtrepo"
    write(
        root,
        "api/views.py",
        "def view():\n    # TODO: validate input\n    # FIXME(dana): auth bypass\n    pass\n",
    )
    write(
        root,
        "api/models.py",
        "# TODO: validate model fields\n# HACK: raw sql\nclass Model:\n    pass\n",
    )
    write(
        root,
        "web/app.ts",
        "// TODO: add loading spinner\nexport function go() {\n  // BUG: crashes on empty\n}\n",
    )
    write(root, "docs/notes.md", "- TODO: document the auth flow\n")
    write(root, "main.py", "# NOTE: entry point\n")
    return root


@pytest.fixture
def debt_indexed(db, debt_repo, fake_embedder):
    repo = db.add_repo(str(debt_repo), "debtrepo")
    run_index(db, repo, debt_repo, fake_embedder)
    return repo


def test_items_are_stored_with_their_symbol(db, debt_indexed):
    with db.repo(debt_indexed["id"]) as conn:
        items = list_items(conn)
    by_text = {i["text"]: i for i in items}
    assert len(items) == 8
    assert by_text["validate input"]["symbol"] == "view"
    assert by_text["crashes on empty"]["symbol"] == "go"
    assert by_text["validate model fields"]["symbol"] is None  # module level
    assert by_text["auth bypass"]["assignee"] == "dana"


def test_filters_and_counters(db, debt_indexed):
    with db.repo(debt_indexed["id"]) as conn:
        assert {i["tag"] for i in list_items(conn, tag="todo")} == {"TODO"}
        assert len(list_items(conn, q="auth")) == 2
        assert {i["file_path"] for i in list_items(conn, folder="api")} == {
            "api/views.py",
            "api/models.py",
        }
        assert list_items(conn, older_than_days=30) == []  # nothing is dated without git
        everything = list_items(conn)
    stats = counters(everything, TAGS)
    assert stats["total"] == 8 and stats["by_tag"]["TODO"] == 4 and stats["by_tag"]["XXX"] == 0
    assert stats["oldest"] is None and stats["has_dates"] is False


def test_groupings(db, debt_indexed):
    with db.repo(debt_indexed["id"]) as conn:
        items = list_items(conn)
    by_tag = group_items(items, "tag", TAGS)
    assert [g["key"] for g in by_tag] == list(TAGS)  # every column exists, even empty ones
    assert len(next(g for g in by_tag if g["key"] == "TODO")["items"]) == 4
    by_folder = {g["label"]: len(g["items"]) for g in group_items(items, "folder", TAGS)}
    assert by_folder == {"api": 4, "web": 2, "docs": 1, "(root)": 1}
    by_age = group_items(items, "age", TAGS)
    assert [g["label"] for g in by_age] == ["Unknown"]  # no git dates
    topics = {items[0]["id"]: (1, "Validation"), items[1]["id"]: (1, "Validation")}
    by_topic = group_items(items, "topic", TAGS, topics)
    assert by_topic[0]["label"] == "Validation" and len(by_topic[0]["items"]) == 2
    assert by_topic[-1]["label"] == "Uncategorized"
    with pytest.raises(ValueError, match="Unknown grouping"):
        group_items(items, "nope", TAGS)


def test_exports(db, debt_indexed):
    with db.repo(debt_indexed["id"]) as conn:
        items = list_items(conn)
    md = export_markdown(items)
    assert "## TODO (4)" in md and "`api/views.py:2`" in md
    csv_text = export_csv(items)
    assert csv_text.splitlines()[0] == "tag,text,file,line,symbol,author,assignee,age_days"
    assert len(csv_text.splitlines()) == 9


# ---- git blame -----------------------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(root, *args, author="Alice", date="2023-01-10T12:00:00"):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
        "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com",
        "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
    }  # fmt: skip
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def test_parse_blame():
    sample = (
        "a" * 40
        + " 1 7 1\nauthor Alice\nauthor-mail <a@x>\nauthor-time 1673352000\nsummary s\n\tline seven\n"
        + "b" * 40
        + " 3 9 1\nauthor Not Committed Yet\nauthor-time 1700000000\n\tline nine\n"
    )
    assert parse_blame(sample) == {7: ("Alice", 1673352000.0), 9: (None, None)}


@needs_git
def test_git_blame_adds_author_and_age(db, tmp_path, fake_embedder):
    root = tmp_path / "gitrepo"
    write(root, "a.py", "# TODO: first\nx = 1\n")
    git(root.parent, "init", "-q", str(root))
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "one", author="Alice", date="2023-01-10T12:00:00")
    with (root / "a.py").open("a", encoding="utf-8", newline="") as fh:
        fh.write("# FIXME: second\n")
    git(root, "commit", "-qam", "two", author="Bob", date="2024-06-01T12:00:00")
    write(root, "b.py", "# HACK: never committed\n")  # untracked: blame has nothing to say

    repo = db.add_repo(str(root), "gitrepo")
    run_index(db, repo, root, fake_embedder)
    with db.repo(repo["id"]) as conn:
        items = {i["text"]: i for i in list_items(conn)}
    assert items["first"]["author"] == "Alice" and items["first"]["age_days"] > 365
    assert items["second"]["author"] == "Bob"
    assert items["second"]["age_days"] < items["first"]["age_days"]
    assert (
        items["never committed"]["author"] is None and items["never committed"]["age_days"] is None
    )
    with db.repo(repo["id"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM debt_items WHERE blamed = 0").fetchone()[0] == 0
        stats = counters(list_items(conn), TAGS)
    assert stats["has_dates"] and stats["oldest"]["text"] == "first"
    with db.repo(repo["id"]) as conn:
        groups = group_items(list_items(conn), "age", TAGS)
    assert {"Over a year", "Unknown"} <= {g["label"] for g in groups}


def test_without_git_everything_else_still_works(db, debt_indexed):
    with db.repo(debt_indexed["id"]) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM debt_items WHERE author IS NOT NULL").fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT COUNT(*) FROM debt_items").fetchone()[0] == 8
        # not a repo: items stay unblamed so a later run (after `git init`) can enrich them
        assert conn.execute("SELECT COUNT(*) FROM debt_items WHERE blamed = 0").fetchone()[0] == 8


# ---- topics --------------------------------------------------------------------------------


def unit(*values):
    v = np.array(values, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_cluster_vectors_groups_similar_items_without_chaining():
    vectors = np.array(
        [
            unit(1, 0.05, 0),
            unit(1, 0.1, 0),
            unit(0.95, 0, 0.1),  # group A
            unit(0, 1, 0.05),
            unit(0.05, 1, 0),  # group B
            unit(0, 0, 1),
        ]  # fmt: skip
    )
    labels = cluster_vectors(vectors, threshold=0.9)
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4]
    assert len({labels[0], labels[3], labels[5]}) == 3
    assert cluster_vectors(np.zeros((0, 3), dtype=np.float32), 0.5) == []
    # A chain of neighbours that are individually similar must not merge into one cluster:
    angles = np.linspace(0, np.pi / 2, 9)
    chain = np.array([unit(np.cos(a), np.sin(a)) for a in angles])
    assert len(set(cluster_vectors(chain, threshold=0.93))) > 1


def test_large_sets_use_the_greedy_fallback():
    rng = np.random.default_rng(1)
    centers = rng.normal(size=(3, 16))
    points = np.vstack([c + rng.normal(scale=0.02, size=(250, 16)) for c in centers])
    points /= np.linalg.norm(points, axis=1, keepdims=True)
    labels = cluster_vectors(points, threshold=0.9)
    assert len(points) > 600 and len(set(labels)) == 3


def test_keyword_and_model_labels():
    texts = ["cache the database rows", "database cache is stale", "cache eviction"]
    assert keyword_label(texts) == "Cache database eviction"  # ties break alphabetically
    assert keyword_label(texts, words=2) == "Cache database"
    assert keyword_label(["", "todo"]) == "Miscellaneous"
    assert clean_label("Database caching") == "Database caching"
    assert clean_label('"auth cleanup".') == "Auth cleanup"
    assert clean_label("Label: retry logic\nbecause...") == "Retry logic"
    assert clean_label("This is far too long to be a label at all for sure") is None
    assert clean_label("{json: 1}") is None and clean_label("  ") is None


class LabelLLM(FakeLLM):
    async def complete(self, messages, model):
        self.complete_calls.append(messages)
        return "Input validation"


def wait_for(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_api_board_topics_and_exports(config, debt_repo, fake_embedder):
    llm = LabelLLM()
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=llm)
    with TestClient(create_app(state, warm_up=False)) as client:
        repo = client.post("/api/repos", json={"path": str(debt_repo)}).json()
        base = f"/api/repos/{repo['id']}"
        index(client, repo["id"])

        board = client.get(f"{base}/debt").json()
        assert board["counters"]["total"] == 8 and board["shown"] == 8
        assert [g["key"] for g in board["groups"]] == [
            "TODO",
            "FIXME",
            "HACK",
            "XXX",
            "BUG",
            "NOTE",
        ]
        filtered = client.get(f"{base}/debt", params={"tag": "TODO", "folder": "api"}).json()
        assert filtered["shown"] == 2 and filtered["counters"]["total"] == 8
        assert (
            client.get(f"{base}/debt", params={"group_by": "folder"}).json()["groups"][0]["label"]
            == "api"
        )
        assert client.get(f"{base}/debt", params={"group_by": "nope"}).status_code == 422

        # Topics are lazy: the first request starts the job, then the result is cached.
        first = client.get(f"{base}/debt", params={"group_by": "topic"}).json()
        assert first["topics_pending"] is True
        topics = wait_for(
            lambda: (
                (r := client.get(f"{base}/debt", params={"group_by": "topic"}).json())
                and not r["topics_pending"]
                and r
            )
        )
        assert topics["topics_error"] is None
        assert sum(len(g["items"]) for g in topics["groups"]) == 8
        assert any(g["label"] == "Input validation" for g in topics["groups"]) or all(
            g["label"] for g in topics["groups"]
        )
        embed_calls = fake_embedder.calls
        client.get(f"{base}/debt", params={"group_by": "topic"})
        assert fake_embedder.calls == embed_calls  # served from the cache

        # Editing a comment invalidates the cache.
        write(debt_repo, "main.py", "# NOTE: entry point moved\n")
        index(client, repo["id"])
        again = client.get(f"{base}/debt", params={"group_by": "topic"}).json()
        assert again["topics_pending"] is True
        wait_for(
            lambda: (
                not client.get(f"{base}/debt", params={"group_by": "topic"}).json()[
                    "topics_pending"
                ]
            )
        )

        md = client.get(f"{base}/debt/export", params={"format": "markdown", "tag": "TODO"})
        assert (
            md.status_code == 200
            and "## TODO (4)" in md.text
            and "attachment" in md.headers["content-disposition"]
        )
        assert client.get(f"{base}/debt/export", params={"format": "csv"}).text.startswith(
            "tag,text,file"
        )
        assert client.get(f"{base}/debt/export", params={"format": "xml"}).status_code == 422
        assert client.get(f"{base}/insights/status").json()["counts"]["debt_items"] == 8
        assert client.get("/api/repos/nope/debt").status_code == 404


def test_topic_failure_is_reported_not_raised(config, debt_repo, fake_embedder, monkeypatch):
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=FakeLLM())
    with TestClient(create_app(state, warm_up=False)) as client:
        repo = client.post("/api/repos", json={"path": str(debt_repo)}).json()
        index(client, repo["id"])
        monkeypatch.setattr(
            fake_embedder,
            "embed_documents",
            lambda texts: (_ for _ in ()).throw(RuntimeError("no model")),
        )
        base = f"/api/repos/{repo['id']}/debt"
        client.get(base, params={"group_by": "topic"})
        result = wait_for(
            lambda: (
                (r := client.get(base, params={"group_by": "topic"}).json())
                and r["topics_error"]
                and r
            )
        )
        assert "no model" in result["topics_error"]
        assert (
            sum(len(g["items"]) for g in result["groups"]) == 8
        )  # the board still shows everything


def test_changed_tags_trigger_reanalysis(db, debt_indexed, debt_repo):
    summary = run_analysis(db, debt_indexed["id"], debt_repo, [DebtAnalyzer("TODO")])
    assert summary.analyzed_files == 5  # a different tag set means a different analyzer version
    with db.repo(debt_indexed["id"]) as conn:
        assert {i["tag"] for i in list_items(conn)} == {"TODO"}
