"""Shared test fixtures: a fake embedder, a fake LLM and a tiny sample repo.

The fakes make tests fast, deterministic and independent of Foundry Local.
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import numpy as np
import pytest

from app.config import Config
from app.db import Database
from app.embeddings import Embedder, normalize
from app.retrieval import split_identifier


class FakeEmbedder(Embedder):
    """Bag-of-words hashing embedder: texts that share words get similar vectors."""

    name = "fake:hashing"
    dim = 256

    def __init__(self):
        self.calls = 0

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for word in text.replace(".", " ").replace("(", " ").split():
            for token in [word.lower(), *split_identifier(word)]:
                h = int(hashlib.md5(token.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        return normalize(np.array([self._vector(t) for t in texts]))


class FakeLLM:
    """Records every call; streams a canned answer that cites snippet 1."""

    def __init__(self, answer: str = "Tokens are signed with HMAC [1]. See also [2][9]."):
        self.answer = answer
        self.stream_calls: list[list[dict]] = []
        self.complete_calls: list[list[dict]] = []

    async def stream_chat(self, messages, model):
        self.stream_calls.append(messages)
        for word in self.answer.split(" "):
            yield word + " "

    async def complete(self, messages, model):
        self.complete_calls.append(messages)
        return "How is the session token verified?"


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "data")


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path / "data", block_external_network=False)


def write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(
        root,
        "auth.py",
        '''
        import hmac

        SECRET = "s3cret"


        def sign_token(user_id: int) -> str:
            """Sign a session token for a user with HMAC."""
            return hmac.new(SECRET.encode(), str(user_id).encode(), "sha256").hexdigest()


        def verify_token(token: str, user_id: int) -> bool:
            """Verify a session token signature."""
            return hmac.compare_digest(token, sign_token(user_id))
        ''',
    )
    write(
        root,
        "cart.js",
        """
        export function addToCart(cart, item) {
          return [...cart, item];
        }

        export const cartTotal = (cart) => cart.reduce((sum, i) => sum + i.price, 0);
        """,
    )
    write(root, "notes.txt", "Shopping cart totals are computed on the client.\n")
    write(root, ".gitignore", "secret/\n*.log\n")
    write(root, "secret/keys.py", "API_KEY = 'do-not-index'\n")
    write(root, "debug.log", "noise\n")
    write(root, "node_modules/lib/index.js", "module.exports = 1;\n")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0binary")
    return root


@pytest.fixture
def endpoints_of(tmp_path: Path, db: Database, fake_embedder):
    """Index a throw-away repo made of ``{path: source}`` and return its endpoints.

    Each result is ``(METHOD, full path, handler, is_partial)``; compare them as a set.
    """
    from app.analysis.endpoints.catalog import list_endpoints
    from app.indexer import IndexProgress, index_repository

    def run(files: dict[str, str]) -> set[tuple[str, str, str | None, bool]]:
        root = tmp_path / f"ep{len(list(tmp_path.iterdir()))}"
        for rel, content in files.items():
            write(root, rel, content)
        repo = db.add_repo(str(root), root.name)
        progress = index_repository(db, repo["id"], root, fake_embedder, IndexProgress())
        assert progress.status == "done" and not progress.analysis_error, progress.error
        with db.repo(repo["id"]) as conn:
            found = list_endpoints(conn)["endpoints"]
        return {(e["method"], e["path"], e["handler"], e["partial"]) for e in found}

    return run
