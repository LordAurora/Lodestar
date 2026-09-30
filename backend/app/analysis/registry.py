"""The list of analyzers that run for every repository.

New Insights features add their analyzer here. Order matters only for readability:
each analyzer is independent and keeps its own tables.
"""

from __future__ import annotations

from app.analysis.pipeline import Analyzer
from app.analysis.symbols import SymbolsAnalyzer


def default_analyzers() -> list[Analyzer]:
    return [SymbolsAnalyzer()]
