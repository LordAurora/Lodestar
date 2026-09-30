"""Duplicate finder: structural hashes, blocked similarity, complete-linkage groups and the API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.analysis.duplicates import (
    PAIR_FLOOR,
    _complete_linkage,
    cached_groups,
    compute_groups,
    diff_lines,
    find_pairs,
)
from app.main import create_app
from app.state import AppState
from tests.conftest import FakeLLM, write
from tests.test_api import OfflineFoundry, index
from tests.test_indexer import run_index

TOTAL = """
def {name}({items}, {tax}):
    {acc} = 0
    for {it} in {items}:
        {acc} += {it}.price * {it}.qty
    return {acc} * (1 + {tax})
"""

FILES = {
    "pricing/a.py": TOTAL.format(
        name="total_price", items="items", tax="tax", acc="subtotal", it="item"
    ),
    "pricing/b.py": TOTAL.format(
        name="total_price", items="items", tax="tax", acc="subtotal", it="item"
    ),
    # the same structure with every name changed: an exact clone once names are erased
    "pricing/c.py": TOTAL.format(
        name="cart_total", items="lines", tax="rate", acc="running", it="line"
    ),
    # similar, but not the same code: an extra statement
    "pricing/d.py": """
    def total_with_discount(items, tax, discount):
        subtotal = 0
        for item in items:
            subtotal += item.price * item.qty
        subtotal -= discount
        return subtotal * (1 + tax)
    """,
    "tests/test_pricing.py": TOTAL.format(
        name="total_price", items="items", tax="tax", acc="subtotal", it="item"
    ),
    "vendor/lib.py": TOTAL.format(
        name="total_price", items="items", tax="tax", acc="subtotal", it="item"
    ),
    "io/config.py": """
    def read_config(path):
        with open(path) as handle:
            text = handle.read()
        parsed = parse(text)
        validate(parsed)
        return parsed
    """,
    "misc/tiny.py": "def tiny(x):\n    return x\n",
    "misc/nested.py": """
    def outer(items):
        def inner(items):
            total = 0
            for x in items:
                total += x
            return total
        return inner(items)
    """,
}


@pytest.fixture
def indexed(db, tmp_path, fake_embedder):
    root = tmp_path / "dup"
    for rel, content in FILES.items():
        write(root, rel, content)
    repo = db.add_repo(str(root), "dup")
    run_index(db, repo, root, fake_embedder)
    return repo


def names(group):
    return {m["qualified_name"] for m in group["members"]}


def files(group):
    return {m["file_path"] for m in group["members"]}


def test_structural_hash_ignores_names_literals_and_comments(db, indexed):
    with db.repo(indexed["id"]) as conn:
        rows = {
            (r["file_path"]): r["norm_hash"]
            for r in conn.execute("SELECT file_path, norm_hash FROM code_fingerprints")
        }
    assert rows["pricing/a.py"] == rows["pricing/b.py"] == rows["pricing/c.py"]
    assert rows["pricing/a.py"] == rows["tests/test_pricing.py"]
    assert rows["pricing/d.py"] != rows["pricing/a.py"]  # an extra statement
    assert rows["io/config.py"] != rows["pricing/a.py"]
    assert "misc/tiny.py" not in rows  # too small to fingerprint


def test_exact_clones_are_grouped_and_ranked(db, indexed):
    with db.repo(indexed["id"]) as conn:
        groups = compute_groups(conn, min_similarity=PAIR_FLOOR)
    exact = [g for g in groups if g["type"] == "exact"]
    assert len(exact) == 1
    group = exact[0]
    assert files(group) == {"pricing/a.py", "pricing/b.py", "pricing/c.py"}  # not tests, not vendor
    assert group["size"] == 3 and group["avg_similarity"] == 1.0
    lines = [m["lines"] for m in group["members"]]
    assert group["duplicated_lines"] == sum(lines) - max(lines)  # everything but one copy
    assert group["value"] == group["size"] * group["duplicated_lines"]
    assert groups == sorted(groups, key=lambda g: -g["value"])


def test_exclusion_toggles(db, indexed):
    with db.repo(indexed["id"]) as conn:
        with_tests = compute_groups(conn, PAIR_FLOOR, include_tests=True)
        exact = next(g for g in with_tests if g["type"] == "exact")
        assert "tests/test_pricing.py" in files(exact)
        assert "vendor/lib.py" not in files(exact)  # vendored code is always skipped
        assert all(m["lines"] >= 5 for g in compute_groups(conn, PAIR_FLOOR) for m in g["members"])
        strict = compute_groups(conn, PAIR_FLOOR, min_lines=6)
        assert all(m["lines"] >= 6 for g in strict for m in g["members"])
        # A function and the function nested in it are never "duplicates" of each other.
        assert not any(
            {"outer", "outer.inner"} <= names(g) for g in compute_groups(conn, PAIR_FLOOR)
        )


def test_similar_but_not_identical_code_forms_a_similar_group(db, indexed):
    with db.repo(indexed["id"]) as conn:
        similar = compute_groups(conn, min_similarity=0.85, kind="similar")
    assert similar, "expected the discount variant to resemble the others"
    group = next(g for g in similar if "total_with_discount" in names(g))
    assert group["type"] == "similar" and 0.85 <= group["avg_similarity"] < 1.0
    with db.repo(indexed["id"]) as conn:
        assert all(g["type"] == "exact" for g in compute_groups(conn, 0.85, kind="exact"))


def test_a_higher_threshold_keeps_only_closer_matches(db, indexed):
    with db.repo(indexed["id"]) as conn:
        loose = compute_groups(conn, 0.80)
        tight = compute_groups(conn, 0.99, kind="similar")
    assert len(tight) <= len(loose) and all(g["avg_similarity"] >= 0.99 for g in tight)


def test_blocked_similarity_matches_the_full_matrix(db, indexed):
    with db.repo(indexed["id"]) as conn:
        small_blocks = find_pairs(conn, block_size=2)
        one_block = find_pairs(conn, block_size=512)
    assert sorted(small_blocks) == sorted(one_block) and small_blocks
    assert all(a < b and sim >= PAIR_FLOOR for a, b, sim in small_blocks)


def test_complete_linkage_does_not_chain_unrelated_code():
    chain = [(1, 2, 0.95), (2, 3, 0.95)]  # 1~2 and 2~3, but 1 and 3 are not similar
    groups = _complete_linkage(chain, 0.9)
    assert len(groups) == 1 and len(groups[0]) == 2  # never all three
    assert _complete_linkage(chain + [(1, 3, 0.92)], 0.9) == [[1, 2, 3]]
    assert _complete_linkage([(1, 2, 0.8)], 0.9) == []
    big = [(i, j, 0.99) for i in range(60) for j in range(i + 1, 60)]
    assert max(len(g) for g in _complete_linkage(big, 0.9)) <= 40  # a size cap for hub-like code


def test_groups_are_cached_until_the_code_changes(db, indexed, tmp_path, fake_embedder):
    with db.repo(indexed["id"]) as conn:
        first = cached_groups(conn, indexed["id"], 0.9, "all", False, 5)
    with db.repo(indexed["id"]) as conn:
        again = cached_groups(conn, indexed["id"], 0.9, "all", False, 5)
        stored = conn.execute("SELECT COUNT(*) FROM duplicate_groups").fetchone()[0]
    assert [g["id"] for g in first] == [g["id"] for g in again] and stored == len(first)
    root = tmp_path / "dup"
    write(root, "pricing/b.py", "def other():\n    return 1\n")
    run_index(db, indexed, root, fake_embedder)
    with db.repo(indexed["id"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM duplicate_groups").fetchone()[0] == 0  # cleared
        fresh = cached_groups(conn, indexed["id"], 0.9, "all", False, 5)
    exact = next(g for g in fresh if g["type"] == "exact")
    assert files(exact) == {"pricing/a.py", "pricing/c.py"}  # b.py is no longer a copy


def test_diff_of_two_members():
    d = diff_lines(
        ["def f(x):", "    a = 1", "    return a"],
        ["def g(x):", "    a = 1", "    b = 2", "    return a"],
    )
    ops = [r["op"] for r in d["rows"]]
    assert ops == ["replace", "equal", "insert", "equal"] and 0 < d["ratio"] < 1
    assert d["rows"][2] == {"op": "insert", "left": None, "right": "    b = 2"}  # indentation kept
    same = diff_lines(["a", "b"], ["a", "b"])
    assert {r["op"] for r in same["rows"]} == {"equal"} and same["ratio"] == 1.0


@pytest.fixture
def client(config, tmp_path, fake_embedder):
    root = tmp_path / "dupapi"
    for rel, content in FILES.items():
        write(root, rel, content)
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=FakeLLM())
    with TestClient(create_app(state, warm_up=False)) as test_client:
        repo = test_client.post("/api/repos", json={"path": str(root)}).json()
        index(test_client, repo["id"])
        test_client.base = f"/api/repos/{repo['id']}"
        yield test_client


def test_api(client):
    base = client.base
    data = client.get(f"{base}/duplicates", params={"min_similarity": 0.8}).json()
    assert data["total"] >= 1 and data["floor"] == PAIR_FLOOR and data["compared_pairs"] > 0
    top = data["groups"][0]
    assert top["type"] == "exact" and top["size"] == 3 and len(top["members"]) <= 6

    clamped = client.get(f"{base}/duplicates", params={"min_similarity": 0.1}).json()
    assert clamped["min_similarity"] == PAIR_FLOOR  # cannot go below what was compared
    assert client.get(f"{base}/duplicates", params={"type": "weird"}).status_code == 422
    with_tests = client.get(
        f"{base}/duplicates", params={"min_similarity": 0.8, "include_tests": True}
    ).json()
    assert with_tests["groups"][0]["size"] == 4

    group = client.get(f"{base}/duplicates/{top['id']}").json()
    assert group["size"] == 3 and len(group["members"]) == 3
    a, b = group["members"][0]["symbol_id"], group["members"][1]["symbol_id"]
    diff = client.get(f"{base}/duplicates/{top['id']}/diff", params={"a": a, "b": b}).json()
    assert diff["a"]["symbol_id"] == a and diff["language"] == "python" and diff["rows"]
    outsider = client.get(f"{base}/symbols", params={"q": "read_config"}).json()["symbols"][0]["id"]
    bad = client.get(f"{base}/duplicates/{top['id']}/diff", params={"a": a, "b": outsider})
    assert bad.status_code == 422
    assert client.get(f"{base}/duplicates/999999").status_code == 404

    md = client.get(f"{base}/duplicates/export", params={"min_similarity": 0.8})
    assert (
        md.status_code == 200
        and "Exact match: 3 functions" in md.text
        and "- [ ] `total_price`" in md.text
    )
    assert client.get("/api/repos/nope/duplicates").status_code == 404
    assert client.get(f"{base}/insights/status").json()["counts"]["duplicate_pairs"] > 0
