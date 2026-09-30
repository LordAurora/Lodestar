"""The list of analyzers that run for every repository.

New Insights features add their analyzer here. Each keeps its own tables. Order matters
in one way: analyzers run in this order for a file, so `symbols` comes first and later
analyzers can look up the enclosing symbol of a line.
"""

from __future__ import annotations

from app.analysis.debt import DEFAULT_TAGS, DebtAnalyzer
from app.analysis.endpoints import EndpointsAnalyzer
from app.analysis.env import EnvAnalyzer
from app.analysis.pipeline import Analyzer
from app.analysis.symbols import SymbolsAnalyzer


def default_analyzers(debt_tags: str | tuple[str, ...] = DEFAULT_TAGS) -> list[Analyzer]:
    return [SymbolsAnalyzer(), EnvAnalyzer(), DebtAnalyzer(debt_tags), EndpointsAnalyzer()]
