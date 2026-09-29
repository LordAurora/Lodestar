"""Indexer tests: file filtering and incremental re-indexing."""

from __future__ import annotations

from app.indexer import IndexProgress, index_repository, walk_repository
from tests.conftest import write


def run_index(db, repo, root, embedder) -> IndexProgress:
    progress = index_repository(db, repo["id"], root, embedder, IndexProgress())
    assert progress.status == "done", progress.error
    return progress


def chunk_files(db, repo_id) -> set[str]:
    with db.repo(repo_id) as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT file_path FROM chunks")}


def test_walk_respects_gitignore_and_skips_noise(sample_repo, tmp_path):
    big = sample_repo / "big.py"
    big.write_text("x = 1\n" * 200_000)
    files = set(walk_repository(sample_repo, max_bytes=1_000_000))
    assert files == {"auth.py", "cart.js", "notes.txt"}


def test_first_index_then_incremental_updates(db, sample_repo, fake_embedder):
    repo = db.add_repo(str(sample_repo), "repo")

    first = run_index(db, repo, sample_repo, fake_embedder)
    assert first.files_changed == 3
    assert first.chunks_embedded == first.chunks_created > 0
    assert chunk_files(db, repo["id"]) == {"auth.py", "cart.js", "notes.txt"}
    assert db.get_repo(repo["id"])["chunk_count"] == first.chunks_created

    # Nothing changed: nothing is re-embedded.
    calls = fake_embedder.calls
    second = run_index(db, repo, sample_repo, fake_embedder)
    assert second.files_changed == 0 and second.chunks_embedded == 0
    assert fake_embedder.calls == calls

    # Modify one file, add one, delete one.
    write(sample_repo, "cart.js", "export function emptyCart() { return []; }\n")
    write(sample_repo, "orders.py", "def place_order(cart):\n    return len(cart)\n")
    (sample_repo / "notes.txt").unlink()
    third = run_index(db, repo, sample_repo, fake_embedder)
    assert third.files_changed == 2
    assert third.files_deleted == 1
    assert chunk_files(db, repo["id"]) == {"auth.py", "cart.js", "orders.py"}

    with db.repo(repo["id"]) as conn:
        cart = conn.execute("SELECT content FROM chunks WHERE file_path = 'cart.js'").fetchall()
        fts_rows = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        chunk_rows = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert len(cart) == 1 and "emptyCart" in cart[0][0]
    assert fts_rows == chunk_rows  # no orphaned keyword entries


def test_changing_embedding_model_triggers_full_reindex(db, sample_repo, fake_embedder):
    repo = db.add_repo(str(sample_repo), "repo")
    run_index(db, repo, sample_repo, fake_embedder)

    class OtherEmbedder(type(fake_embedder)):
        name = "fake:other"

    progress = run_index(db, db.get_repo(repo["id"]), sample_repo, OtherEmbedder())
    assert progress.files_changed == 3
