"""Static analysis ("Insights"): symbols, calls, imports and the graphs built from them.

Why this package exists: the RAG pipeline answers *questions*, but many useful
things about a code base can be computed exactly, without a language model. This
package reuses the tree-sitter grammars the chunker already loads to extract

* **symbols**: functions, methods and classes,
* **call sites** and **imports**, resolved across files with a confidence level,

and stores them in SQLite (see ``app/db.py``). Later modules (impact analysis,
diagrams, endpoint and env-var finders, ...) are plain queries over those tables.

All of this is *heuristic*. Python, JavaScript and friends are dynamic, so a call
like ``repo.save()`` cannot always be tied to one definition. Every resolved edge
therefore carries a confidence (``high``, ``medium`` or ``low``) that the UI shows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def weakest(a: str, b: str) -> str:
    """The lower of two confidence levels (a path is only as reliable as its weakest edge)."""
    return a if CONFIDENCE_ORDER[a] <= CONFIDENCE_ORDER[b] else b


@dataclass
class SymbolInfo:
    """A function, method or class definition found in a file."""

    name: str
    qualified_name: str  # Class.method, outer.inner, ...
    kind: str  # function | method | class
    start_line: int  # 1-based, inclusive
    end_line: int
    signature: str
    has_doc: bool
    is_test: bool
    language: str
    owner: str | None = None  # qualified name of the enclosing class, if any


@dataclass
class CallInfo:
    """A call (or inheritance) site."""

    name: str  # the called name: `helper`, or `save` in `repo.save()`
    line: int
    kind: str = "call"  # call | inherit
    receiver: str | None = None  # `repo` in `repo.save()`; `self` in `self.save()`
    from_index: int | None = None  # index into FileFacts.symbols; None = module level


@dataclass
class ImportInfo:
    """An import, using or require statement."""

    module: str  # `os.path`, `./mod`, `com.acme.Util`, `Acme.Models`
    line: int
    names: list[str] = field(default_factory=list)  # `a` and `b` in `from x import a, b`
    alias: str | None = None


@dataclass
class FileFacts:
    """Everything the extractor learned about one file."""

    symbols: list[SymbolInfo] = field(default_factory=list)
    calls: list[CallInfo] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    namespace: str | None = None  # Java package / C# namespace / Go package
