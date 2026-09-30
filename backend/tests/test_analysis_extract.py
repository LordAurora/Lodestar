"""Symbol, call and import extraction, per language (no database involved)."""

from __future__ import annotations

import textwrap

import pytest

from app.analysis.conventions import is_test_path
from app.analysis.extract import extract_facts
from app.analysis.pipeline import FileContext
from app.chunking import detect_language


def facts(path: str, source: str):
    ctx = FileContext(path, detect_language(path), textwrap.dedent(source).encode(), "h")
    assert ctx.root() is not None, f"no grammar for {path}"
    return extract_facts(path, ctx.language, ctx.source, ctx.root())


def by_qualified(f):
    return {s.qualified_name: s for s in f.symbols}


def calls(f):
    return {(c.receiver, c.name, c.kind) for c in f.calls}


def test_python_symbols_calls_and_imports():
    f = facts(
        "svc.py",
        '''
        import os.path as p
        from ..pkg.mod import a, b as c
        from . import util

        @decorator
        class Store(Base):
            """A store."""
            def get(self, key: int) -> str:
                """Read a key."""
                self.load(key)
                p.join(key)
                return helper(key)

            def load(self, key):
                return super().load(key)

        def helper(x):
            def inner():
                return x
            return inner()
        ''',
    )
    symbols = by_qualified(f)
    assert set(symbols) == {"Store", "Store.get", "Store.load", "helper", "helper.inner"}
    assert symbols["Store"].kind == "class" and symbols["Store"].has_doc
    assert symbols["Store.get"].kind == "method" and symbols["Store.get"].has_doc
    assert symbols["Store.get"].owner == "Store"
    assert symbols["Store.get"].signature == "def get(self, key: int) -> str"
    assert not symbols["Store.load"].has_doc and not symbols["helper"].has_doc
    assert symbols["helper.inner"].kind == "function"  # nested in a function, not a method
    assert {
        ("self", "load", "call"),
        ("p", "join", "call"),
        (None, "helper", "call"),
        ("super", "load", "call"),
        (None, "Base", "inherit"),
    } <= calls(f)
    modules = {(i.module, tuple(i.names), i.alias) for i in f.imports}
    assert ("os.path", (), "p") in modules
    assert ("..pkg.mod", ("a", "b as c"), None) in modules  # `b as c` keeps the local name
    assert (".", ("util",), None) in modules


def test_python_calls_are_attributed_to_the_enclosing_symbol():
    f = facts(
        "a.py",
        """
        top()
        def one():
            two()
        class K:
            attr = make()
            def m(self):
                three()
        """,
    )
    owner = {
        c.name: (f.symbols[c.from_index].qualified_name if c.from_index is not None else None)
        for c in f.calls
    }
    assert owner == {"top": None, "two": "one", "make": "K", "three": "K.m"}


@pytest.mark.parametrize("path", ["web/app.ts", "web/app.js"])
def test_javascript_and_typescript(path):
    f = facts(
        path,
        """
        import { a, b as c } from "./mod";
        import D from "../d";
        import * as lib from "@/lib/x";
        const fs = require("fs");
        /** Documented. */
        export class Foo extends Bar {
          handler = () => { run(); };
          method(x) { this.other(x); new Thing(); helper.go(); }
        }
        export const arrow = async (req) => { return svc.call(req); };
        function plain() {}
        """,
    )
    symbols = by_qualified(f)
    assert set(symbols) == {"Foo", "Foo.handler", "Foo.method", "arrow", "plain"}
    assert symbols["Foo"].has_doc and not symbols["plain"].has_doc
    assert symbols["Foo.handler"].kind == "method" and symbols["arrow"].kind == "function"
    assert {
        (None, "run", "call"),
        ("this", "other", "call"),
        (None, "Thing", "call"),
        ("helper", "go", "call"),
        ("svc", "call", "call"),
        (None, "Bar", "inherit"),
    } <= calls(f)
    modules = {(i.module, tuple(i.names), i.alias) for i in f.imports}
    assert ("./mod", ("a", "b as c"), None) in modules
    assert ("../d", ("default",), "D") in modules
    assert ("@/lib/x", ("*",), "lib") in modules
    assert ("fs", ("default",), "fs") in modules  # `const fs = require("fs")` binds `fs`
    assert not any(c.name == "require" for c in f.calls)


def test_go_methods_types_and_imports():
    f = facts(
        "server.go",
        """
        package main
        import (
          "fmt"
          str "strings"
        )
        // Run starts the server.
        func (s *Server) Run(x int) error { fmt.Println(x); s.stop(); return helper(x) }
        type Server struct{}
        func helper(x int) error { return nil }
        func TestRun(t *testing.T) {}
        """,
    )
    symbols = by_qualified(f)
    assert set(symbols) == {"Server.Run", "Server", "helper", "TestRun"}
    assert symbols["Server.Run"].kind == "method" and symbols["Server.Run"].owner == "Server"
    assert symbols["Server.Run"].has_doc and not symbols["helper"].has_doc
    assert symbols["TestRun"].is_test and not symbols["helper"].is_test
    assert {("fmt", "Println", "call"), ("s", "stop", "call"), (None, "helper", "call")} <= calls(f)
    assert {(i.module, i.alias) for i in f.imports} == {("fmt", None), ("strings", "str")}
    assert f.namespace == "main"


def test_java_classes_annotations_and_imports():
    f = facts(
        "src/com/acme/Svc.java",
        """
        package com.acme.app;
        import java.util.List;
        import static com.acme.Util.helper;
        import com.acme.model.*;
        /** Documented. */
        public class Svc extends Base implements Runnable {
          @Test
          public void run() { helper(); this.go(); repo.save(new Item()); }
          void undocumented() {}
        }
        """,
    )
    symbols = by_qualified(f)
    assert set(symbols) == {"Svc", "Svc.run", "Svc.undocumented"}
    assert symbols["Svc"].has_doc and not symbols["Svc.undocumented"].has_doc
    assert symbols["Svc.run"].is_test and not symbols["Svc.undocumented"].is_test
    assert {
        (None, "helper", "call"),
        ("this", "go", "call"),
        ("repo", "save", "call"),
        (None, "Item", "call"),
        (None, "Base", "inherit"),
        (None, "Runnable", "inherit"),
    } <= calls(f)
    modules = {(i.module, tuple(i.names)) for i in f.imports}
    assert modules == {
        ("java.util.List", ()),
        ("com.acme.Util.helper", ()),
        ("com.acme.model", ("*",)),
    }
    assert f.namespace == "com.acme.app"


def test_csharp_classes_attributes_and_usings():
    f = facts(
        "Svc.cs",
        """
        using System;
        using Acme.Models;
        namespace Acme.App {
          /// <summary>doc</summary>
          public class Svc : Base, IRun {
            [Fact]
            public void Run() { Helper(); this.Go(); repo.Save(new Item()); }
            public void Plain() {}
          }
        }
        """,
    )
    symbols = by_qualified(f)
    assert set(symbols) == {"Svc", "Svc.Run", "Svc.Plain"}
    assert symbols["Svc"].has_doc and not symbols["Svc.Plain"].has_doc
    assert symbols["Svc.Run"].is_test and not symbols["Svc.Plain"].is_test
    assert {
        (None, "Helper", "call"),
        ("this", "Go", "call"),
        ("repo", "Save", "call"),
        (None, "Item", "call"),
        (None, "Base", "inherit"),
        (None, "IRun", "inherit"),
    } <= calls(f)
    assert {i.module for i in f.imports} == {"System", "Acme.Models"}
    assert f.namespace == "Acme.App"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_a.py", True),
        ("pkg/test_a.py", True),
        ("pkg/a_test.go", True),
        ("web/src/a.test.ts", True),
        ("web/src/a.spec.tsx", True),
        ("web/__tests__/a.js", True),
        ("src/FooTest.java", True),
        ("src/FooTests.cs", True),
        ("pkg/contest.py", False),
        ("src/latest.ts", False),
        ("pkg/testimony.py", False),
    ],
)
def test_test_path_conventions(path, expected):
    assert is_test_path(path) is expected


def test_python_test_symbols_by_name():
    f = facts("logic.py", "def test_it(): pass\ndef work(): pass\nclass TestThing: pass\n")
    marked = {s.name: s.is_test for s in f.symbols}
    assert marked == {"test_it": True, "work": False, "TestThing": True}


def test_deeply_nested_expression_does_not_hit_the_recursion_limit():
    expr = " + ".join(f"f{i}()" for i in range(1500))
    f = facts("deep.py", f"def big():\n    return {expr}\n")
    assert len(f.calls) == 1500
