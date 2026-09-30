"""Docstring suggester: answer validation, rendering, insertion safety and the API.

The suggester is the only feature that writes into a repository, so most tests here are about
what must NOT happen: no edit outside the inserted text, none when the file changed, none that
breaks the syntax.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.analysis.docwriter import (
    BACKUP_DIR,
    Doc,
    DocError,
    apply_edits,
    find_insertion,
    parameter_names,
    parse_answer,
    render_comment,
    render_lines,
    signature_names,
    verify_edit,
)
from app.indexer import SKIP_DIRS, walk_repository
from app.main import create_app
from app.state import AppState
from tests.conftest import write
from tests.test_api import OfflineFoundry, index

ANSWER = """SUMMARY: Add two numbers together.
PARAM a: The first number.
PARAM b: The second number.
RETURNS: The sum."""


class DocLLM:
    """Answers every request with a fixed reply (or one per call)."""

    def __init__(self, *replies: str):
        self.replies = list(replies) or [ANSWER]
        self.calls = 0

    async def complete(self, messages, model):
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]

    async def stream_chat(self, messages, model):
        yield ""


# ---- answer validation -----------------------------------------------------------------------


def test_parse_answer_accepts_the_format():
    doc = parse_answer(ANSWER, "def add(a, b):")
    assert doc.summary == "Add two numbers together."
    assert doc.params == [("a", "The first number."), ("b", "The second number.")]
    assert doc.returns == "The sum."


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "Adds numbers.",  # no SUMMARY line
        "```python\nSUMMARY: x\n```",  # code fence
        "SUMMARY: ok\nPARAM zzz: not a parameter",
        "SUMMARY: " + "long " * 60,
        'SUMMARY: contains """ quotes',
        "SUMMARY: ends the comment */ early",
        "SUMMARY: a backslash \\ here",
    ],
)
def test_parse_answer_rejects_bad_output(raw):
    with pytest.raises(DocError):
        parse_answer(raw, "def add(a, b):")


def test_signature_helpers():
    sig = "def f(self, items: list[int], *args, limit=10, **kw) -> int"
    assert {"items", "args", "limit", "kw"} <= set(signature_names(sig))
    assert parameter_names(sig, "python") == ["items", "args", "limit", "kw"]
    assert parameter_names("int add(int a, Map<String, Integer> m)", "java") == ["a", "m"]
    assert parameter_names("function f(a, b = 2)", "javascript") == ["a", "b"]


# ---- rendering ---------------------------------------------------------------------------------

DOC = Doc("Add two numbers", [("a", "First.")], "The sum")


def test_render_styles():
    google = render_lines(DOC, "python", "add", "google")
    assert google == [
        "Add two numbers.",
        "",
        "Args:",
        "    a: First.",
        "",
        "Returns:",
        "    The sum",
    ]
    assert "Parameters" in render_lines(DOC, "python", "add", "numpy")
    assert ":param a: First." in render_lines(DOC, "python", "add", "rest")
    assert render_lines(DOC, "java", "add") == [
        "Add two numbers.", "", "@param a First.", "@return The sum",
    ]  # fmt: skip
    assert "@returns The sum" in render_lines(DOC, "typescript", "add")
    assert render_lines(DOC, "go", "Add") == ["Add add two numbers."]
    cs = render_lines(Doc("Use <b>", [], None), "csharp", "F")
    assert cs == ["<summary>", "Use &lt;b&gt;.", "</summary>"]


def test_render_comment_shapes():
    assert render_comment(["One."], "python", "    ", "\n") == '    """One."""\n'
    assert render_comment(["One.", "", "Two."], "python", "  ", "\n") == (
        '  """One.\n\n  Two.\n  """\n'
    )
    assert render_comment(["One."], "java", "\t", "\r\n") == "\t/**\r\n\t * One.\r\n\t */\r\n"
    assert render_comment(["Add adds."], "go", "", "\n") == "// Add adds.\n"


# ---- insertion ---------------------------------------------------------------------------------

PY = b"""import os


def add(a, b):
    total = a + b
    return total


class Box:
    def size(self):
        return 1
"""


def test_python_insertion_goes_first_in_the_body():
    new, _ = apply_edits(PY, "python", [(4, ["Add.", "", "Args:", "    a: One."])])
    text = new.decode()
    assert 'def add(a, b):\n    """Add.\n\n    Args:\n        a: One.\n    """\n    total' in text
    # nothing else changed
    assert (
        text.replace(text[text.index('    """Add.') : text.index("    total")], "") == PY.decode()
    )


def test_several_edits_in_one_file_keep_positions_valid():
    new, edits = apply_edits(PY, "python", [(4, ["Add."]), (9, ["A box."]), (10, ["Its size."])])
    text = new.decode()
    assert '"""Add."""' in text and '"""A box."""' in text and '"""Its size."""' in text
    assert len(edits) == 3
    verify_edit("python", PY, new, edits)


def test_indentation_and_line_endings_are_preserved():
    crlf = PY.replace(b"\n", b"\r\n")
    new, _ = apply_edits(crlf, "python", [(10, ["Its size."])])
    assert b'        """Its size."""\r\n        return 1' in new
    assert b"\n" not in new.replace(b"\r\n", b"")  # no bare LF was introduced
    tabs = b"def f(x):\n\ty = x\n\treturn y\n"
    assert b'\t"""Doc."""\n\ty = x' in apply_edits(tabs, "python", [(1, ["Doc."])])[0]


def test_decorated_python_function():
    src = b"@cache\ndef f(x):\n    y = x\n    return y\n"
    new, _ = apply_edits(src, "python", [(1, ["Doc."])])  # the symbol starts at the decorator
    assert new == b'@cache\ndef f(x):\n    """Doc."""\n    y = x\n    return y\n'


def test_one_line_and_broken_files_are_refused():
    with pytest.raises(DocError, match="One-line"):
        find_insertion(b"def f(): return 1\n", "python", 1)
    with pytest.raises(DocError, match="syntax"):
        find_insertion(b"def f(:\n    pass\n", "python", 1)
    with pytest.raises(DocError, match="UTF-8"):
        find_insertion(b"def f():\n    x = '\xff'\n    return x\n", "python", 1)
    with pytest.raises(DocError, match="find"):
        find_insertion(PY, "python", 2)  # a blank line is not a definition


def test_comment_languages():
    js = b"export function add(a, b) {\n  return a + b;\n}\n"
    new, _ = apply_edits(js, "javascript", [(1, ["Add.", "", "@param a One."])])
    assert new.startswith(b"/**\n * Add.\n *\n * @param a One.\n */\nexport function add")
    go = b"package m\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n"
    new, _ = apply_edits(go, "go", [(3, ["Add adds."])])
    assert b"// Add adds.\nfunc Add" in new
    java = b"class A {\n    int add(int a) {\n        return a;\n    }\n}\n"
    new, _ = apply_edits(java, "java", [(2, ["Adds."])])
    assert b"    /**\n     * Adds.\n     */\n    int add" in new
    cs = b"class A {\n    public int Add(int a) {\n        return a;\n    }\n}\n"
    new, _ = apply_edits(cs, "csharp", [(2, ["<summary>", "Adds.", "</summary>"])])
    assert b"    /// <summary>\n    /// Adds.\n    /// </summary>\n    public int Add" in new


def test_verify_refuses_changes_to_existing_code():
    good, edits = apply_edits(PY, "python", [(4, ["Add."])])
    tampered = good.replace(b"a + b", b"a - b")
    with pytest.raises(DocError, match="existing code"):
        verify_edit("python", PY, tampered, edits)
    broken = good.replace(b'"""Add."""', b'"""Add.')  # would leave a string open
    with pytest.raises(DocError):
        verify_edit("python", PY, broken, [(edits[0][0], edits[0][1] - 3)])


def test_backup_folder_is_never_indexed(tmp_path):
    assert BACKUP_DIR.startswith(".")  # hidden folders are skipped by the walker
    root = tmp_path / "r"
    write(root, "a.py", "x = 1\n")
    write(root, f"{BACKUP_DIR}/20260101-000000/a.py", "x = 0\n")
    assert list(walk_repository(root, 1_000_000)) == ["a.py"]
    assert BACKUP_DIR in SKIP_DIRS  # skipped by name, not only because it is hidden


# ---- API -------------------------------------------------------------------------------------

CODE = '''
def add(a, b):
    total = a + b
    return total


def sub(a, b):
    """Subtract."""
    return a - b


def mul(a, b):
    result = a * b
    return result


def div(a, b):
    result = a / b
    return result


def caller():
    return add(1, 2) + mul(2, 3) + div(4, 2)
'''


@pytest.fixture
def env(config, tmp_path, fake_embedder):
    root = tmp_path / "docrepo"
    write(root, "calc.py", CODE)
    write(
        root, "tests/test_calc.py", "def test_add():\n    assert add(1, 2) == 3\n    assert True\n"
    )
    llm = DocLLM()
    state = AppState(config, foundry=OfflineFoundry(), embedder=fake_embedder, llm=llm)
    with TestClient(create_app(state, warm_up=False)) as client:
        repo = client.post("/api/repos", json={"path": str(root)}).json()
        index(client, repo["id"])
        client.base = f"/api/repos/{repo['id']}"
        client.root = root
        client.llm = llm
        yield client


def wait_job(client):
    for _ in range(200):
        job = client.get(f"{client.base}/docs/job").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def missing(client, **params):
    return client.get(f"{client.base}/docs/missing", params=params).json()


def test_missing_lists_undocumented_most_called_first(env):
    data = missing(env, min_lines=1)
    names = [i["qualified_name"] for i in data["items"]]
    assert "sub" not in names  # documented
    assert not any(n.startswith("test_") for n in names)  # tests are left out by default
    assert names.index("add") < names.index("caller")  # called functions first
    assert data["languages"] == {"python": data["total"]}
    assert missing(env, q="mul")["total"] == 1
    assert missing(env, lang="go")["total"] == 0


def generate(env, names):
    items = {i["qualified_name"]: i for i in missing(env, min_lines=1)["items"]}
    ids = [items[n]["symbol_id"] for n in names]
    res = env.post(f"{env.base}/docs/suggest", json={"symbol_ids": ids})
    assert res.status_code == 202
    return wait_job(env)


def test_generate_stores_pending_suggestions_and_writes_nothing(env):
    before = (env.root / "calc.py").read_bytes()
    job = generate(env, ["add", "mul"])
    assert job["status"] == "done" and job["done"] == 2 and job["failed"] == 0
    assert (env.root / "calc.py").read_bytes() == before  # dry run by default
    items = env.get(f"{env.base}/docs/suggestions").json()["items"]
    assert [i["status"] for i in items] == ["pending", "pending"]
    assert items[0]["proposed_text"].startswith("Add two numbers together.")
    diff = env.get(f"{env.base}/docs/suggestions/{items[0]['id']}/diff").json()
    assert diff["error"] is None and not diff["stale"]
    assert '+    """Add two numbers together.' in diff["diff"]
    assert "@@" in diff["diff"] and "--- a/calc.py" in diff["diff"]
    assert not (env.root / BACKUP_DIR).exists()


def test_bad_model_output_is_retried_once_then_fails(env):
    env.llm.replies = ["I cannot do that.", "Still no format."]
    job = generate(env, ["add"])
    assert env.llm.calls == 2 and job["failed"] == 1
    item = env.get(f"{env.base}/docs/suggestions").json()["items"][0]
    assert item["status"] == "failed" and "summary" in item["error"]
    # a good second answer rescues the same request
    env.llm.calls, env.llm.replies = 0, ["nope", ANSWER]
    generate(env, ["add"])
    assert env.get(f"{env.base}/docs/suggestions").json()["items"][0]["status"] == "pending"


def test_accept_writes_backup_and_reindexes(env):
    generate(env, ["add"])
    sid = env.get(f"{env.base}/docs/suggestions").json()["items"][0]["id"]
    original = (env.root / "calc.py").read_bytes()
    res = env.post(f"{env.base}/docs/suggestions/{sid}/accept")
    assert res.status_code == 200 and res.json()["files"] == 1 and res.json()["reindexed"]

    now = (env.root / "calc.py").read_text()
    assert 'def add(a, b):\n    """Add two numbers together.' in now
    assert "total = a + b\n    return total" in now
    backups = list((env.root / BACKUP_DIR).glob("*/calc.py"))
    assert len(backups) == 1 and backups[0].read_bytes() == original

    # the file was re-analysed: `add` is documented now, so it no longer shows up
    assert "add" not in [i["qualified_name"] for i in missing(env, min_lines=1)["items"]]
    accepted = env.get(f"{env.base}/docs/suggestions", params={"status": "accepted"}).json()
    assert len(accepted["items"]) == 1
    # and the backup folder did not become part of the index
    files = env.get(f"{env.base}/insights/status").json()
    assert files["counts"]["symbols"] > 0
    assert env.post(f"{env.base}/docs/suggestions/{sid}/accept").status_code == 409


def test_accept_refuses_when_the_file_changed(env):
    generate(env, ["add"])
    sid = env.get(f"{env.base}/docs/suggestions").json()["items"][0]["id"]
    path = env.root / "calc.py"
    path.write_text(path.read_text() + "\n# edited by hand\n")
    edited = path.read_bytes()

    diff = env.get(f"{env.base}/docs/suggestions/{sid}/diff").json()
    assert diff["stale"] and "changed" in diff["error"]
    res = env.post(f"{env.base}/docs/suggestions/{sid}/accept")
    assert res.status_code == 409 and "changed" in res.json()["detail"]
    assert path.read_bytes() == edited and not (env.root / BACKUP_DIR).exists()


def test_accept_all_edits_each_file_once_and_keeps_the_rest_valid(env):
    generate(env, ["add", "mul", "div"])
    items = env.get(f"{env.base}/docs/suggestions").json()["items"]
    first, rest = items[0], items[1:]
    # accept one: the others follow their functions to the new line numbers
    assert env.post(f"{env.base}/docs/suggestions/{first['id']}/accept").status_code == 200
    pending = env.get(f"{env.base}/docs/suggestions", params={"status": "pending"}).json()
    assert len(pending["items"]) == len(rest)
    res = env.post(f"{env.base}/docs/accept", json={"ids": [i["id"] for i in pending["items"]]})
    body = res.json()
    assert body["files"] == 1 and all(r["status"] == "accepted" for r in body["results"])
    text = (env.root / "calc.py").read_text()
    assert text.count('"""') == 2 * 4  # sub's original docstring plus the three new ones
    compile(text, "calc.py", "exec")  # still valid Python
    assert len(list((env.root / BACKUP_DIR).glob("*/calc.py"))) >= 1


def test_reject_and_regenerate(env):
    generate(env, ["add"])
    sid = env.get(f"{env.base}/docs/suggestions").json()["items"][0]["id"]
    assert env.post(f"{env.base}/docs/suggestions/{sid}/reject").json()["status"] == "rejected"
    assert env.post(f"{env.base}/docs/suggestions/{sid}/accept").status_code == 409
    assert env.post(f"{env.base}/docs/suggestions/{sid}/regenerate").status_code == 202
    wait_job(env)
    statuses = [i["status"] for i in env.get(f"{env.base}/docs/suggestions").json()["items"]]
    assert "pending" in statuses
    assert env.post(f"{env.base}/docs/suggestions/999/reject").status_code == 404
    assert env.get(f"{env.base}/docs/suggestions/999/diff").status_code == 404


def test_suggest_validation_and_single_job(env):
    assert env.post(f"{env.base}/docs/suggest", json={"symbol_ids": []}).status_code == 422
    assert env.post(f"{env.base}/docs/suggest", json={"symbol_ids": [10**6]}).status_code == 422
    assert env.get(f"{env.base}/docs/job").json()["status"] == "idle"
