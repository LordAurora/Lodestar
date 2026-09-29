"""Chunker tests: one per supported language, plus the fallback and splitting rules."""

from __future__ import annotations

import textwrap

from app.chunking import Chunk, chunk_file
from app.chunking.fallback import chunk_by_lines


def chunks_of(path: str, source: str) -> list[Chunk]:
    return chunk_file(path, textwrap.dedent(source).lstrip())


def names(chunks: list[Chunk]) -> list[str | None]:
    return [c.symbol_name for c in chunks]


def long_body(n: int, indent: str = "    ") -> str:
    return "\n".join(
        f"{indent}value_{i} = compute_something_long({i}) + another_term_{i}" for i in range(n)
    )


def test_python_functions_and_classes():
    src = (
        "import os\n\n\n"
        f'def load(path):\n    """Load a file."""\n{long_body(12)}\n    return path\n\n\n'
        f'class Store:\n    """A store."""\n\n    def get(self, key):\n'
        f"{long_body(12, '        ')}\n        return key\n"
    )
    chunks = chunk_file("store.py", src)
    assert all(c.language == "python" for c in chunks)
    by_name = {c.symbol_name: c for c in chunks}
    assert "load" in by_name and by_name["load"].symbol_kind == "function"
    assert by_name["load"].start_line == 4
    assert "Store" in by_name and by_name["Store"].symbol_kind == "class"


def test_python_large_class_is_split_into_methods_with_signature_context():
    methods = "\n\n".join(
        f"    def method_{m}(self):\n{long_body(8, '        ')}\n        return {m}"
        for m in range(4)
    )
    src = f"class BigService(BaseService):\n    retries = 3\n\n{methods}\n"
    chunks = chunk_file("svc.py", src)
    method_chunks = [c for c in chunks if c.symbol_kind == "method"]
    assert [c.symbol_name for c in method_chunks] == [f"BigService.method_{m}" for m in range(4)]
    assert all(c.context == "class BigService(BaseService): ..." for c in method_chunks)
    assert "class BigService(BaseService):" in method_chunks[0].embedding_text()
    header = next(c for c in chunks if c.symbol_kind == "class")
    assert header.symbol_name == "BigService" and header.start_line == 1


def test_python_decorated_function_includes_decorator():
    src = f"@app.route('/login')\ndef login():\n{long_body(12)}\n    return 1\n"
    [chunk] = chunk_file("views.py", src)
    assert chunk.symbol_name == "login"
    assert chunk.start_line == 1 and chunk.content.startswith("@app.route")


def test_small_neighbours_are_merged_into_one_header_chunk():
    src = "import os\nimport sys\n\nA = 1\nB = 2\n\n\ndef tiny():\n    return A\n"
    chunks = chunk_file("consts.py", src)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1 and chunks[0].end_line == 9


def test_javascript_functions_classes_and_arrow_functions():
    src = f"""
    import x from "y";

    export function handleLogin(req) {{
    {long_body(10, "  ")}
    }}

    export const computeTotal = (items) => {{
    {long_body(10, "  ")}
    }};

    class Cart {{
      add(item) {{
    {long_body(10, "    ")}
      }}
    }}
    """
    found = names(chunks_of("app.js", src))
    assert "handleLogin" in found
    assert "computeTotal" in found
    assert "Cart" in found


def test_typescript_interfaces_and_classes():
    src = f"""
    export interface User {{
      id: number;
      name: string;
    }}

    export class UserService {{
      async find(id: number): Promise<User> {{
    {long_body(12, "    ")}
      }}
    }}
    """
    chunks = chunks_of("user.ts", src)
    assert chunks[0].language == "typescript"
    assert "UserService" in " ".join(n or "" for n in names(chunks))


def test_go_functions_methods_and_types():
    src = f"""
    package auth

    type Session struct {{
    	UserID int
    }}

    func NewSession(id int) Session {{
    {long_body(10, "	")}
    	return Session{{UserID: id}}
    }}

    func (s Session) Valid() bool {{
    {long_body(10, "	")}
    	return s.UserID > 0
    }}
    """
    found = names(chunks_of("session.go", src))
    assert "NewSession" in found
    assert "Valid" in found


def test_java_class_with_methods():
    methods = "\n".join(
        f"    public int m{m}() {{\n{long_body(8, '        ')}\n        return {m};\n    }}"
        for m in range(3)
    )
    src = f"package app;\n\npublic class OrderService {{\n{methods}\n}}\n"
    found = names(chunk_file("OrderService.java", src))
    assert "OrderService.m0" in found and "OrderService.m2" in found


def test_csharp_namespace_class_and_methods():
    methods = "\n".join(
        f"        public bool Check{m}(string u) {{\n{long_body(8, '            ')}\n"
        f"            return true;\n        }}"
        for m in range(3)
    )
    src = f"using System;\n\nnamespace App.Auth {{\n    public class Login {{\n{methods}\n    }}\n}}\n"
    chunks = chunk_file("Login.cs", src)
    assert chunks[0].language == "csharp"
    assert "Login.Check0" in names(chunks)


def test_unsupported_language_uses_sliding_window():
    src = "\n".join(f"line {i}" for i in range(1, 131))
    chunks = chunk_file("notes.txt", src)
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 60), (51, 110), (101, 130)]
    assert all(c.symbol_kind == "block" for c in chunks)


def test_sliding_window_ignores_blank_files():
    assert chunk_by_lines("empty.txt", "\n\n  \n", "text") == []


def test_embedding_text_has_path_and_symbol_header():
    chunk = Chunk("pkg/auth.py", "python", "Auth.login", "method", 1, 2, "def login(): ...")
    assert chunk.embedding_text().startswith("# pkg/auth.py :: Auth.login\n")


def test_missing_grammar_falls_back_to_line_windows(monkeypatch):
    """Offline without a cached grammar, tree-sitter cannot load: chunk by lines instead."""
    import tree_sitter_language_pack

    def unavailable(language):
        raise RuntimeError("grammar not downloaded")

    monkeypatch.setattr(tree_sitter_language_pack, "get_parser", unavailable)
    chunks = chunk_file("app.py", "def f():\n    return 1\n")
    assert [(c.symbol_kind, c.start_line, c.end_line) for c in chunks] == [("block", 1, 2)]
