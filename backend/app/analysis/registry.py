"""The list of analyzers that run for every repository.

New Insights features add their analyzer here. Each keeps its own tables. Order matters
in one way: analyzers run in this order for a file, so `symbols` comes first and later
analyzers can look up the enclosing symbol of a line.
"""

from __future__ import annotations

from app.analysis.env import EnvAnalyzer
from app.analysis.pipeline import Analyzer
from app.analysis.symbols import SymbolsAnalyzer


def default_analyzers() -> list[Analyzer]:
    return [SymbolsAnalyzer(), EnvAnalyzer()]
