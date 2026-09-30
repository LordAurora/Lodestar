"""Path and name conventions: which files and symbols are tests, which are vendored.

Kept in one place because several features need the same answers (impact analysis
lists the *affected tests*, the duplicate finder skips tests, the docstring
suggester skips them too).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "testing", "e2e"}
VENDORED_DIRS = {
    "vendor", "third_party", "third-party", "node_modules", "site-packages", "generated",
    "gen", "migrations", "__generated__", "dist", "build",
}  # fmt: skip

_TEST_FILE = re.compile(
    r"""(^test_.*\.py$)|(_test\.py$)|(^conftest\.py$)|(_test\.go$)"""
    r"""|(\.(test|spec)\.[cm]?[jt]sx?$)"""
    r"""|((Test|Tests|IT)\.java$)|((Test|Tests)\.cs$)""",
)
_TEST_ANNOTATIONS = {
    # JUnit
    "Test", "ParameterizedTest", "RepeatedTest", "TestFactory",
    # xUnit, NUnit, MSTest
    "Fact", "Theory", "TestMethod", "TestCase", "TestCaseSource", "DataTestMethod",
}  # fmt: skip
_TEST_CLASS_ATTRIBUTES = {"TestFixture", "TestClass"}
_GO_TEST_FUNC = re.compile(r"^(Test|Benchmark|Example|Fuzz)([A-Z_0-9].*)?$")


def is_test_path(path: str) -> bool:
    """True for files that live in a test folder or follow a test naming convention."""
    p = PurePosixPath(path)
    if any(part.lower() in TEST_DIRS for part in p.parts[:-1]):
        return True
    return bool(_TEST_FILE.search(p.name))


def is_vendored_path(path: str) -> bool:
    return any(part.lower() in VENDORED_DIRS for part in PurePosixPath(path).parts[:-1])


def is_test_symbol(language: str, name: str, kind: str, annotations: set[str]) -> bool:
    """Symbol-level test markers (used on top of the file-level convention)."""
    if language == "python":
        return name.startswith("test") or (kind == "class" and name.startswith("Test"))
    if language == "go":
        return bool(_GO_TEST_FUNC.match(name))
    if language in {"java", "csharp"}:
        return bool(annotations & _TEST_ANNOTATIONS) or (
            kind == "class" and bool(annotations & _TEST_CLASS_ATTRIBUTES)
        )
    return False
