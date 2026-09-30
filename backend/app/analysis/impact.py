"""Impact analysis: what may break if a function, method or class changes?

Everything here is computed from the call graph, not guessed by a language model:

* **callers**, grouped by how many calls away they are (depth 1 = calls it directly);
* **affected tests**: callers that are test code, so you know what to run;
* **module-level uses**: calls made outside any function (scripts, top-level setup);
* a **blast radius** score and a Low / Medium / High level.

The score adds up the *non-test* callers, each weighted by how far away it is and how
sure we are about the edge::

    score = sum(depth_weight[depth] * confidence_weight[confidence])   over non-test callers
    depth_weight      = 1, 1/2, 1/4, 1/8, 1/16   for depth 1..5
    confidence_weight = high 1.0, medium 0.6, low 0.3
    level             = Low (< 3), Medium (3 to < 10), High (>= 10)

Nearby, certain callers count for most. Distant or doubtful ones count for less, and
tests do not add to the score because they are a safety net rather than a risk.
"""

from __future__ import annotations

import re
from collections import defaultdict

from app.analysis.graph import CodeGraph, Hit

MAX_DEPTH = 5
DEPTH_WEIGHT = {1: 1.0, 2: 0.5, 3: 0.25, 4: 0.125, 5: 0.0625}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}
LEVELS = [(10.0, "High"), (3.0, "Medium"), (0.0, "Low")]
FORMULA = (
    "Each non-test caller adds a weight: 1, 1/2, 1/4, 1/8, 1/16 for depth 1 to 5, "
    "times 1.0 (high), 0.6 (medium) or 0.3 (low) for how sure the link is. "
    "Low is under 3, Medium 3 to 10, High 10 or more."
)


def blast_radius(hits: list[Hit]) -> tuple[float, str]:
    score = sum(
        DEPTH_WEIGHT.get(h.depth, 0.0) * CONFIDENCE_WEIGHT[h.confidence]
        for h in hits
        if not h.node.is_test
    )
    level = next(name for floor, name in LEVELS if score >= floor)
    return round(score, 2), level


def _subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


def search_symbols(conn, q: str = "", kind: str = "", limit: int = 30) -> list[dict]:
    """Symbols whose name matches ``q``: exact, then prefix, then substring, then fuzzy."""
    needle = q.strip().lower()
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.file_path, s.start_line, s.is_test,"
        " (SELECT COUNT(*) FROM symbol_references r WHERE r.to_symbol_id = s.id"
        "  AND r.kind IN ('call', 'inherit')) AS callers FROM symbols s"
    ).fetchall()
    scored = []
    for r in rows:
        if kind and r["kind"] != kind:
            continue
        name, qualified = r["name"].lower(), r["qualified_name"].lower()
        if not needle:
            score = 1
        elif name == needle or qualified == needle:
            score = 100
        elif name.startswith(needle):
            score = 80
        elif qualified.startswith(needle):
            score = 70
        elif needle in name:
            score = 60
        elif needle in qualified:
            score = 50
        elif len(needle) >= 3 and _subsequence(needle, qualified):
            score = 30  # fuzzy: the letters appear in order, e.g. "vtok" -> verify_token
        else:
            continue
        scored.append((score, r))
    scored.sort(key=lambda s: (-s[0], -s[1]["callers"], len(s[1]["name"]), s[1]["file_path"]))
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "qualified_name": r["qualified_name"],
            "kind": r["kind"],
            "file_path": r["file_path"],
            "line": r["start_line"],
            "is_test": bool(r["is_test"]),
            "callers": r["callers"],
        }
        for _, r in scored[:limit]
    ]


def _describe(hit: Hit) -> dict:
    node = hit.node
    return {
        "id": node.id,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "kind": node.kind,
        "file_path": node.file_path,
        "line": node.start_line,
        "call_file": hit.call_file,
        "call_line": hit.line,
        "confidence": hit.confidence,
        "is_test": node.is_test,
        "depth": hit.depth,
    }


def compute_impact(conn, symbol_id: int, depth: int = 3) -> dict | None:
    """The full impact report for one symbol, or None when the symbol does not exist."""
    graph = CodeGraph(conn)
    target = graph.nodes.get(symbol_id)
    if target is None:
        return None
    depth = max(1, min(depth, MAX_DEPTH))

    # Changing a class can break callers of any of its methods, not only of its constructor.
    roots = [symbol_id]
    if target.kind == "class":
        roots += [
            n.id for n in graph.nodes.values()
            if n.owner == target.qualified_name and n.file_path == target.file_path
        ]  # fmt: skip
    traversal = graph.callers(roots, depth)
    hits = traversal.hits

    levels: dict[int, list[dict]] = defaultdict(list)
    for hit in hits:
        levels[hit.depth].append(_describe(hit))
    tests = [_describe(h) for h in hits if h.node.is_test]
    score, level = blast_radius(hits)

    names = [graph.nodes[i].name for i in roots]
    marks = ",".join("?" * len(names))
    unresolved = conn.execute(
        f"SELECT COUNT(*) FROM symbol_references WHERE to_symbol_id IS NULL AND kind = 'call'"  # noqa: S608
        f" AND to_name IN ({marks})",
        names,
    ).fetchone()[0]
    module_level = [
        {"file_path": r["file_path"], "line": r["line"], "confidence": r["confidence"]}
        for r in conn.execute(
            f"SELECT file_path, line, confidence FROM symbol_references WHERE kind = 'call'"  # noqa: S608
            f" AND from_symbol_id IS NULL AND to_symbol_id IN ({','.join('?' * len(roots))})"
            " ORDER BY file_path, line",
            roots,
        )
    ]
    low = sum(1 for h in hits if h.confidence == "low")
    production_files = {h.node.file_path for h in hits if not h.node.is_test}
    return {
        "target": {
            "id": target.id,
            "name": target.name,
            "qualified_name": target.qualified_name,
            "kind": target.kind,
            "file_path": target.file_path,
            "line": target.start_line,
            "end_line": target.end_line,
        },
        "depth": depth,
        "levels": [{"depth": d, "items": levels[d]} for d in sorted(levels)],
        "tests": tests,
        "module_level": module_level,
        "total": len(hits),
        "direct": len(levels.get(1, [])),
        "files": len({h.node.file_path for h in hits}),
        "production_files": len(production_files),
        "score": score,
        "level": level,
        "formula": FORMULA,
        "low_confidence": low,
        "possible_unresolved": unresolved,
        "unresolved_warning": bool(low or unresolved),
        "truncated": traversal.truncated,
    }


# ---- the plain-language summary --------------------------------------------------------

SUMMARY_SYSTEM = (
    "You write a short risk note about changing one piece of code. Write exactly 3 sentences "
    "of plain prose. Mention only names and numbers that appear in the facts. "
    "No lists, no headings, no bold text, no general programming advice."
)
# A worked example: small models copy the shape of an answer they are shown far more reliably
# than they follow a description of it.
_EXAMPLE_FACTS = (
    "- The function `parse_date` is in utils/dates.py.\n"
    "- Blast radius: Medium (score 4.5).\n"
    "- 3 direct callers, 6 affected in total across 4 files (looking 3 levels out).\n"
    "- 2 affected tests.\n"
    "- Direct callers include: `load_row`, `import_csv`, `render_report`."
)
_EXAMPLE_NOTE = (
    "`parse_date` is called directly by 3 functions, including `load_row` and `import_csv`, "
    "and 6 symbols across 4 files could be affected in total. The blast radius is Medium, so a "
    "change here deserves a careful review. 2 tests reach this code and should be run after "
    "any edit."
)


def summary_messages(facts: str) -> list[dict]:
    return [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"Facts:\n{_EXAMPLE_FACTS}"},
        {"role": "assistant", "content": _EXAMPLE_NOTE},
        {"role": "user", "content": f"Facts:\n{facts}"},
    ]


def summary_facts(report: dict) -> str:
    target = report["target"]
    lines = [
        f"- The {target['kind']} `{target['qualified_name']}` is in {target['file_path']}.",
        f"- Blast radius: {report['level']} (score {report['score']}).",
        f"- {report['direct']} direct callers, {report['total']} affected in total "
        f"across {report['files']} files (looking {report['depth']} levels out).",
        f"- {len(report['tests'])} affected tests.",
    ]
    names = [i["qualified_name"] for lvl in report["levels"][:1] for i in lvl["items"][:5]]
    if names:
        lines.append("- Direct callers include: " + ", ".join(f"`{n}`" for n in names) + ".")
    if report["module_level"]:
        lines.append(f"- {len(report['module_level'])} uses in module-level code.")
    if report["unresolved_warning"]:
        lines.append(
            f"- {report['low_confidence']} links are low confidence and "
            f"{report['possible_unresolved']} calls by the same name could not be resolved."
        )
    return "\n".join(lines)


def fallback_summary(report: dict) -> str:
    """A deterministic summary, used when the model output is unusable."""
    target = report["target"]
    if report["total"] == 0:
        text = (
            f"Nothing calls `{target['qualified_name']}` within {report['depth']} levels, so "
            f"changing it looks low risk (blast radius {report['level']})."
        )
    else:
        text = (
            f"Changing `{target['qualified_name']}` may affect {report['total']} symbols across "
            f"{report['files']} files, {report['direct']} of them direct callers. "
            f"The blast radius is {report['level']} (score {report['score']}). "
            f"{len(report['tests'])} affected tests would show a break."
        )
    if report["unresolved_warning"]:
        text += " Some links are low confidence or unresolved, so check the callers by hand."
    return text


def usable_summary(text: str) -> str | None:
    """Accept the model's text only if it reads like a short paragraph."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    if not cleaned or "```" in cleaned or len(cleaned) > 900 or not 2 <= len(sentences) <= 8:
        return None
    if re.search(r"^\s*(\d+[.)]|[-*•])\s", cleaned, re.MULTILINE) or "**" in cleaned:
        return None  # a list or headings: the model ignored "plain prose"
    return cleaned
