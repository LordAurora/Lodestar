"""Environment variable finder: detection per language, secrets, and `.env` privacy."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analysis.env import (
    _key_of,
    generate_example,
    is_secret,
    is_secret_name,
    list_env,
    read_dotenv_keys,
    scan_env,
)
from app.analysis.pipeline import FileContext
from app.chunking import detect_language
from app.main import create_app
from app.state import AppState
from tests.conftest import FakeLLM, write
from tests.test_api import OfflineFoundry, index
from tests.test_indexer import run_index


def hits(path: str, source: str) -> dict[str, tuple]:
    ctx = FileContext(path, detect_language(path), textwrap.dedent(source).encode(), "h")
    found = scan_env(ctx.language, ctx.source, ctx.root())
    return {h.name: (h.default, h.has_default, h.source) for h in found}


def test_python_reads():
    found = hits(
        "cfg.py",
        """
        import os
        a = os.environ["A"]
        b = os.getenv("B", "dflt")
        c = os.getenv("C")
        d = os.getenv("D") or "fallback"
        e = os.environ.get("E", os.getcwd())
        f = os.environ.get("F", default="kw")
        g = os.environ.get("G", None)
        os.environ["WRITTEN"] = "x"
        h = os.environ[some_name]
        i = os.getenv(f"PREFIX_{name}")
        """,
    )
    assert found == {
        "A": (None, False, "code"),  # raises KeyError when missing: required
        "B": ("dflt", True, "code"),
        "C": (None, False, "code"),  # returns None when missing: required
        "D": ("fallback", True, "code"),
        "E": (None, True, "code"),  # a computed default exists but is not a literal
        "F": ("kw", True, "code"),
        "G": (None, False, "code"),  # `None` is not a default
    }  # WRITTEN is a write, and dynamic names cannot be listed


def test_pydantic_settings_fields():
    found = hits(
        "config.py",
        """
        from pydantic import Field
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class Config(BaseSettings):
            model_config = SettingsConfigDict(env_prefix="APP_")
            host: str = "127.0.0.1"
            port: int = 8000
            name: str
            token: str = Field(...)
            level: str = Field("info")
            path: str = Field(default_factory=lambda: "x")
            other: str = Field(validation_alias="ALIASED")
            _private: str = "x"
            NOTE: ClassVar[str] = "skip"

        class Legacy(BaseSettings):
            debug: bool = False
            class Config:
                env_prefix = "OLD_"

        class NotSettings:
            x: int = 1
        """,
    )
    assert found == {
        "APP_HOST": ("127.0.0.1", True, "pydantic"),
        "APP_PORT": ("8000", True, "pydantic"),
        "APP_NAME": (None, False, "pydantic"),
        "APP_TOKEN": (None, False, "pydantic"),
        "APP_LEVEL": ("info", True, "pydantic"),
        "APP_PATH": (None, True, "pydantic"),
        "ALIASED": (None, False, "pydantic"),
        "OLD_DEBUG": ("False", True, "pydantic"),
    }


def test_lodestars_own_settings_are_found():
    source = (Path(__file__).parents[1] / "app" / "config.py").read_text("utf-8")
    found = hits("config.py", source)
    assert found["LODESTAR_DATA_DIR"][1] is True  # has a (computed) default
    assert found["LODESTAR_PORT"] == ("8000", True, "pydantic")
    assert found["LODESTAR_EMBEDDING_MODEL"] == ("auto", True, "pydantic")


@pytest.mark.parametrize("path", ["web/env.ts", "web/env.js"])
def test_javascript_and_typescript(path):
    found = hits(
        path,
        """
        const a = process.env.A || "d";
        const b = process.env["B"];
        const c = process.env.C ?? "nullish";
        const { D, E = "x", F: renamed } = process.env;
        const g = import.meta.env.VITE_G;
        process.env.WRITTEN = "1";
        const h = process.env[dynamic];
        """,
    )
    assert found == {
        "A": ("d", True, "code"),
        "B": (None, False, "code"),
        "C": ("nullish", True, "code"),
        "D": (None, False, "code"),
        "E": ("x", True, "code"),
        "F": (None, False, "code"),
        "VITE_G": (None, False, "code"),
    }


def test_go_java_and_csharp():
    assert hits(
        "main.go", 'package main\nfunc f() { a := os.Getenv("A"); b, ok := os.LookupEnv("B") }\n'
    ) == {
        "A": (None, False, "code"),  # "" when unset: required
        "B": (None, True, "code"),  # LookupEnv reports absence, so the code handles it
    }
    java = hits(
        "A.java",
        """
        class A { void f() {
          String a = System.getenv("A");
          String b = System.getenv().getOrDefault("B", "d");
        } }
        """,
    )
    assert java == {"A": (None, False, "code"), "B": ("d", True, "code")}
    csharp = hits(
        "A.cs",
        """
        class A { void F() {
          var a = Environment.GetEnvironmentVariable("A") ?? "d";
          var b = Environment.GetEnvironmentVariable("B");
          var c = Environment.GetEnvironmentVariable("C") ?? throw new Exception();
        } }
        """,
    )
    assert csharp == {
        "A": ("d", True, "code"),
        "B": (None, False, "code"),
        "C": (None, False, "code"),
    }


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("API_TOKEN", True), ("DB_PASSWORD", True), ("SECRET_KEY", True), ("apiKey", True),
        ("STRIPE_APIKEY", True), ("SENTRY_DSN", True), ("PRIVATE_KEY_PATH", True),
        ("PORT", False), ("DEBUG", False), ("MONKEY_COUNT", False), ("KEYBOARD_LAYOUT", False),
        ("DATABASE_URL", False),
        # settings *about* a secret are not secrets
        ("TOKEN_TTL", False), ("JWT_EXPIRY_SECONDS", False), ("PASSWORD_MIN_LENGTH", False),
        ("SECRET_KEY_ALGORITHM", False), ("API_TOKEN", True), ("TOKEN_SECRET", True),
    ],
)  # fmt: skip
def test_secret_names(name, secret):
    assert is_secret_name(name) is secret


def test_credentials_in_a_default_make_it_a_secret():
    assert is_secret("DATABASE_URL", "postgres://admin:hunter2@db/prod")
    assert not is_secret("DATABASE_URL", "postgres://localhost/dev")


@pytest.fixture
def env_repo(tmp_path):
    root = tmp_path / "envrepo"
    write(
        root,
        "backend/app/settings.py",
        """
        import os
        PORT = os.getenv("PORT", "8000")
        HOST = os.getenv("HOST", "0.0.0.0 all")
        TOKEN = os.getenv("API_TOKEN", "sk-live-abc123")
        DB = os.environ["DATABASE_URL"]

        def connect():
            return os.environ.get("PORT")
        """,
    )
    write(
        root,
        "web/src/env.ts",
        'export const api = process.env.API_URL || "http://localhost:3000";\n',
    )
    write(root, "worker.go", 'package main\nfunc f() { _ = os.Getenv("WORKER_COUNT") }\n')
    write(
        root,
        ".env",
        "API_TOKEN=hunter2-real-value\nDATABASE_URL=postgres://admin:pw123@db/prod\nUNUSED=1\n",
    )
    write(root, ".env.example", "# sample\nexport PORT=8000\nAPI_URL=\n")
    write(root, "production.env", "API_TOKEN=leaked-value-999\n")
    write(root, "deploy.pem", "-----BEGIN PRIVATE KEY-----\nSUPERSECRETKEYMATERIAL\n")
    return root


@pytest.fixture
def env_indexed(db, env_repo, fake_embedder):
    repo = db.add_repo(str(env_repo), "envrepo")
    run_index(db, repo, env_repo, fake_embedder)
    return repo


def test_env_files_and_keys_are_never_indexed(db, env_indexed):
    with db.repo(env_indexed["id"]) as conn:
        files = {r[0] for r in conn.execute("SELECT path FROM files")}
        chunk_text = " ".join(r[0] for r in conn.execute("SELECT content FROM chunks"))
        env_rows = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM env_vars"))
    assert files == {"backend/app/settings.py", "web/src/env.ts", "worker.go"}
    for leaked in ("hunter2", "leaked-value-999", "SUPERSECRETKEYMATERIAL", "pw123"):
        assert leaked not in chunk_text and leaked not in env_rows


def test_secret_defaults_are_not_stored(db, env_indexed):
    with db.repo(env_indexed["id"]) as conn:
        by_name = {v["name"]: v for v in list_env(conn)}
    token = by_name["API_TOKEN"]
    assert token["is_secret"] and token["default"] is None
    assert token["usages"][0]["has_default"]  # we know there is a default, but not its value
    assert "sk-live-abc123" not in str(by_name)


def test_grouping_usages_and_requirements(db, env_indexed):
    with db.repo(env_indexed["id"]) as conn:
        by_name = {v["name"]: v for v in list_env(conn)}
    port = by_name["PORT"]
    assert port["usage_count"] == 2 and port["default"] == "8000"
    assert port["required"]  # `os.environ.get("PORT")` in connect() has no default
    assert {u["symbol"] for u in port["usages"]} == {None, "connect"}
    assert by_name["DATABASE_URL"]["required"] and not by_name["HOST"]["required"]
    assert by_name["WORKER_COUNT"]["usages"][0]["language"] == "go"


def test_dotenv_keys_are_read_without_values(env_repo):
    declared = read_dotenv_keys(env_repo)
    assert declared["PORT"] == [".env.example"]
    assert set(declared["API_TOKEN"]) == {".env"}  # production.env is not a known env file name
    assert "UNUSED" in declared
    assert all(isinstance(v, list) for v in declared.values())
    assert _key_of("export KEY=value # comment") == "KEY"
    assert _key_of("# KEY=value") is None and _key_of("not a pair") is None


def test_generated_example(db, env_indexed):
    with db.repo(env_indexed["id"]) as conn:
        text = generate_example(list_env(conn))
    lines = text.splitlines()
    assert (
        "# ---- backend ----" in lines
        and "# ---- web ----" in lines
        and "# ---- (root) ----" in lines
    )
    assert "PORT=8000" in lines
    assert 'HOST="0.0.0.0 all"' in lines  # values with spaces are quoted
    assert "API_TOKEN=" in lines and "DATABASE_URL=" in lines  # secrets stay empty
    assert "sk-live-abc123" not in text and "hunter2" not in text
    assert "API_URL=http://localhost:3000" in lines
    assert any(line.startswith("# required. Used in backend/app/settings.py:") for line in lines)
    assert "# Secret: set a real value; never commit it." in lines
    # The output is itself a valid env file: every variable is a `NAME=` line.
    assert {_key_of(line) for line in lines if _key_of(line)} == {
        "API_TOKEN", "API_URL", "DATABASE_URL", "HOST", "PORT", "WORKER_COUNT",
    }  # fmt: skip


def test_env_endpoints(config, env_repo, fake_embedder):
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=FakeLLM())
    with TestClient(create_app(state, warm_up=False)) as client:
        repo = client.post("/api/repos", json={"path": str(env_repo)}).json()
        index(client, repo["id"])
        data = client.get(f"/api/repos/{repo['id']}/env").json()
        names = {v["name"] for v in data["variables"]}
        assert {"PORT", "HOST", "API_TOKEN", "DATABASE_URL", "API_URL", "WORKER_COUNT"} == names
        port = next(v for v in data["variables"] if v["name"] == "PORT")
        assert port["declared_in"] == [".env.example"]
        assert "hunter2" not in client.get(f"/api/repos/{repo['id']}/env").text
        assert client.get(f"/api/repos/{repo['id']}/env", params={"q": "api"}).json()["total"] == 2
        example = client.get(f"/api/repos/{repo['id']}/env/example").json()
        assert example["count"] == 6 and "PORT=8000" in example["text"]
        counts = client.get(f"/api/repos/{repo['id']}/insights/status").json()["counts"]
        assert counts["env_vars"] == 6
        assert client.get("/api/repos/nope/env").status_code == 404


def test_removing_a_read_updates_the_catalog(db, env_indexed, env_repo, fake_embedder):
    write(env_repo, "worker.go", "package main\nfunc f() {}\n")
    run_index(db, env_indexed, env_repo, fake_embedder)
    with db.repo(env_indexed["id"]) as conn:
        assert "WORKER_COUNT" not in {v["name"] for v in list_env(conn)}


def test_file_preview_stays_inside_the_repo_and_hides_secrets(config, env_repo, fake_embedder):
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=FakeLLM())
    with TestClient(create_app(state, warm_up=False)) as client:
        repo = client.post("/api/repos", json={"path": str(env_repo)}).json()
        url = f"/api/repos/{repo['id']}/file"
        ok = client.get(
            url, params={"path": "worker.go", "start": 2, "end": 2, "context": 0}
        ).json()
        assert ok["lines"] == ['func f() { _ = os.Getenv("WORKER_COUNT") }']
        assert ok["first_line"] == 2 and ok["chunk"]["start_line"] == 2 and ok["language"] == "go"
        wide = client.get(url, params={"path": "backend/app/settings.py", "start": 3}).json()
        assert wide["first_line"] == 1 and wide["chunk"]["end_line"] == 3  # clamped to the file
        assert client.get(url, params={"path": "../outside.txt"}).status_code == 404
        assert client.get(url, params={"path": str(env_repo.parent)}).status_code == 404
        assert client.get(url, params={"path": "nope.py"}).status_code == 404
        assert client.get(url, params={"path": ".env"}).status_code == 403
        assert client.get(url, params={"path": "production.env"}).status_code == 403
        assert client.get(url, params={"path": "deploy.pem"}).status_code == 403
        assert client.get("/api/repos/nope/file", params={"path": "a.py"}).status_code == 404
