"""Duplicate and near-duplicate code: groups of functions worth refactoring together.

Two complementary signals, because each misses what the other finds:

* **Exact structural clones.** Each function's syntax tree is flattened into tokens with
  every name replaced by ``ID``, every literal by ``STR``/``NUM`` and comments dropped, then
  hashed. Copy-pasted code with renamed variables has the same hash.
* **Semantic near-duplicates.** The chunk embeddings the search index already stores are
  compared in blocks (a matrix product per block keeps memory bounded), and pairs above a
  similarity floor are kept.

Pairs become **groups** by *complete linkage*: two groups merge only if every function in one
is similar enough to every function in the other. That stops chains of "A resembles B,
B resembles C" from gluing unrelated code together. Groups are ranked by a refactor value,
``size x duplicated lines``, so the biggest opportunities come first.
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import textwrap
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.analysis.conventions import is_test_path, is_vendored_path
from app.analysis.pipeline import FileContext

PAIR_FLOOR = 0.80  # pairs below this are never stored, so thresholds cannot go lower
MAX_PAIRS = 100_000
MAX_SYMBOLS = 20_000  # the pairwise comparison is quadratic: cap it on huge repositories
MAX_GROUP_SIZE = 40
MIN_TOKENS = 12  # a getter or a one-line wrapper is not worth reporting
DEFAULT_MIN_LINES = 5

_IDENTIFIERS = {
    "identifier", "property_identifier", "type_identifier", "field_identifier",
    "shorthand_property_identifier", "shorthand_property_identifier_pattern",
    "package_identifier", "namespace_identifier", "statement_identifier",
}  # fmt: skip
_NUMBERS = {
    "integer", "float", "number", "integer_literal", "int_literal", "float_literal",
    "real_literal", "decimal_integer_literal", "decimal_floating_point_literal",
}  # fmt: skip
_CONSTANTS = {"true", "false", "none", "null", "nil", "True", "False", "None"}


def normalized_tokens(root) -> list[tuple[int, str]]:
    """``(row, token)`` for every leaf of a tree, with names and literals erased."""
    tokens: list[tuple[int, str]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        kind = node.type
        if "comment" in kind:
            continue
        # A quoted string is several leaves (or a node with children): treat it as one token.
        is_string = "string" in kind or "template" in kind
        if node.child_count == 0 or is_string:
            if kind in _IDENTIFIERS:
                token = "ID"
            elif kind in _NUMBERS:
                token = "NUM"
            elif kind in _CONSTANTS:
                token = "LIT"
            elif is_string or "escape" in kind:
                token = "STR"
            else:
                token = kind
            if not (token == "STR" and tokens and tokens[-1][1] == "STR"):
                tokens.append((node.start_point.row, token))
            continue
        stack.extend(reversed(node.children))
    return tokens


class DuplicatesAnalyzer:
    name = "duplicates"
    version = 1
    languages = frozenset({"python", "javascript", "typescript", "tsx", "go", "java", "csharp"})

    def __init__(self, block_size: int = 512):
        self.block_size = block_size

    def analyze(self, ctx: FileContext, conn, repo_id: str) -> dict | None:
        root = ctx.root()
        if root is None:
            return None
        symbols = conn.execute(
            "SELECT id, start_line, end_line FROM symbols"
            " WHERE file_path = ? AND kind IN ('function', 'method')",
            (ctx.path,),
        ).fetchall()
        if not symbols:
            return None
        tokens = normalized_tokens(root)
        rows = [t[0] for t in tokens]
        found = []
        for s in symbols:
            lo = bisect.bisect_left(rows, s["start_line"] - 1)
            hi = bisect.bisect_right(rows, s["end_line"] - 1)
            body = [t for _, t in tokens[lo:hi]]
            if len(body) >= MIN_TOKENS:
                digest = hashlib.sha1(" ".join(body).encode()).hexdigest()[:16]
                found.append((repo_id, s["id"], ctx.path, s["start_line"], s["end_line"], digest,
                              len(body), ctx.hash))  # fmt: skip
        conn.executemany(
            "INSERT INTO code_fingerprints (repo_id, symbol_id, file_path, start_line, end_line,"
            " norm_hash, token_count, file_hash) VALUES (?,?,?,?,?,?,?,?)",
            found,
        )
        return None

    def delete(self, conn, path: str) -> None:
        conn.execute("DELETE FROM code_fingerprints WHERE file_path = ?", (path,))

    def finalize(self, conn, repo_id: str, root: Path) -> None:
        conn.execute("DELETE FROM duplicate_groups")  # cached results are now stale
        conn.execute("DELETE FROM duplicate_members")
        conn.execute("DELETE FROM duplicate_pairs")
        conn.executemany(
            "INSERT OR REPLACE INTO duplicate_pairs (a, b, sim) VALUES (?, ?, ?)",
            find_pairs(conn, self.block_size),
        )


# ---- semantic pairs -----------------------------------------------------------------------


def _overlap(a: dict, b: dict) -> bool:
    return a["file_path"] == b["file_path"] and not (
        a["end_line"] < b["start_line"] or b["end_line"] < a["start_line"]
    )


def find_pairs(
    conn, block_size: int = 512, floor: float = PAIR_FLOOR
) -> list[tuple[int, int, float]]:
    """All pairs of functions whose embeddings have cosine similarity >= ``floor``.

    The embeddings are unit vectors, so similarity is a dot product. The full similarity matrix
    would need n x n numbers, so it is computed one block of rows at a time.
    """
    rows = conn.execute(
        "SELECT s.id, s.file_path, s.start_line, s.end_line, c.embedding FROM symbols s"
        " JOIN chunks c ON c.file_path = s.file_path AND c.start_line = s.start_line"
        "  AND c.end_line = s.end_line"
        " WHERE s.kind IN ('function', 'method') AND c.embedding IS NOT NULL"
        " ORDER BY (s.end_line - s.start_line) DESC LIMIT ?",
        (MAX_SYMBOLS,),
    ).fetchall()
    if len(rows) < 2:
        return []
    ids = [r["id"] for r in rows]
    meta = [
        dict(file_path=r["file_path"], start_line=r["start_line"], end_line=r["end_line"])
        for r in rows
    ]
    matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    n = len(ids)
    found: list[tuple[int, int, float]] = []
    for start in range(0, n, max(1, block_size)):
        block = matrix[start : start + block_size] @ matrix.T  # (block, n) similarities
        for offset, row in enumerate(block):
            i = start + offset
            row[: i + 1] = -1.0  # keep each pair once (j > i) and skip self-matches
            for j in np.flatnonzero(row >= floor):
                if not _overlap(meta[i], meta[int(j)]):  # a function and the code nested in it
                    a, b = sorted((ids[i], ids[int(j)]))
                    found.append((a, b, round(float(row[j]), 4)))
    if len(found) > MAX_PAIRS:
        found = sorted(found, key=lambda p: -p[2])[:MAX_PAIRS]
    return found


# ---- groups ---------------------------------------------------------------------------------


def _load_symbols(conn) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT s.id, s.qualified_name, s.name, s.file_path, s.start_line, s.end_line, s.is_test,"
        " f.norm_hash FROM symbols s JOIN code_fingerprints f ON f.symbol_id = s.id"
    ).fetchall()
    return {r["id"]: dict(r) for r in rows}


def _complete_linkage(pairs: list[tuple[int, int, float]], threshold: float) -> list[list[int]]:
    """Group symbols so that every two members of a group are >= ``threshold`` similar."""
    sim = {(a, b): s for a, b, s in pairs if s >= threshold}
    parent: dict[int, int] = {}
    members: dict[int, set[int]] = {}

    def find(x: int) -> int:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def score(x: int, y: int) -> float | None:
        return sim.get((x, y) if x < y else (y, x))

    for a, b in sorted(sim, key=lambda pair: -sim[pair]):
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        group_a, group_b = members.setdefault(ra, {ra}), members.setdefault(rb, {rb})
        if len(group_a) + len(group_b) > MAX_GROUP_SIZE:
            continue
        # Merge only when *every* cross pair is similar enough (the diameter guard).
        if all(score(x, y) is not None for x in group_a for y in group_b):
            parent[rb] = ra
            group_a |= group_b
            del members[rb]
    return [sorted(g) for g in members.values() if len(g) >= 2]


def compute_groups(
    conn,
    min_similarity: float = 0.9,
    kind: str = "all",
    include_tests: bool = False,
    min_lines: int = DEFAULT_MIN_LINES,
) -> list[dict]:
    """Ranked duplicate groups for the given thresholds (best refactor value first)."""
    symbols = _load_symbols(conn)

    def eligible(s: dict) -> bool:
        lines = s["end_line"] - s["start_line"] + 1
        return (
            lines >= min_lines
            and (include_tests or not (s["is_test"] or is_test_path(s["file_path"])))
            and not is_vendored_path(s["file_path"])
        )

    keep = {i for i, s in symbols.items() if eligible(s)}

    # Exact structural clones: functions with the same normalized-code hash.
    by_hash: dict[str, list[int]] = defaultdict(list)
    for i in keep:
        by_hash[symbols[i]["norm_hash"]].append(i)
    exact_sets = []
    for members in by_hash.values():
        distinct = sorted(
            m
            for m in members
            if all(not _overlap(symbols[m], symbols[o]) for o in members if o != m)
        )
        if 2 <= len(distinct) <= MAX_GROUP_SIZE:
            exact_sets.append(distinct)

    # Semantic near-duplicates: complete-linkage groups of similar embeddings. A cluster that is
    # only part of an exact-clone class adds nothing, so it is dropped.
    pairs = [
        (a, b, sim)
        for a, b, sim in conn.execute(
            "SELECT a, b, sim FROM duplicate_pairs WHERE sim >= ?", (min_similarity,)
        )
        if a in keep and b in keep
    ]
    similarity = {(a, b): sim for a, b, sim in pairs}
    similar_sets = [
        members
        for members in _complete_linkage(pairs, min_similarity)
        if not any(set(members) <= set(e) for e in exact_sets)
    ]

    groups: list[tuple[str, list[int], float]] = []
    if kind in {"all", "exact"}:
        groups += [("exact", members, 1.0) for members in exact_sets]
    if kind in {"all", "similar"}:
        for members in similar_sets:
            known = [similarity[(x, y)] for x in members for y in members if (x, y) in similarity]
            groups.append(("similar", members, sum(known) / max(1, len(known))))

    result = []
    for group_type, members, avg in groups:
        lines = [symbols[m]["end_line"] - symbols[m]["start_line"] + 1 for m in members]
        duplicated = sum(lines) - max(lines)  # everything except the one copy you would keep
        result.append(
            {
                "type": group_type,
                "size": len(members),
                "avg_similarity": round(avg, 3),
                "duplicated_lines": duplicated,
                "value": round(len(members) * duplicated, 1),
                "members": [
                    {
                        "symbol_id": m,
                        "qualified_name": symbols[m]["qualified_name"],
                        "file_path": symbols[m]["file_path"],
                        "start_line": symbols[m]["start_line"],
                        "end_line": symbols[m]["end_line"],
                        "lines": symbols[m]["end_line"] - symbols[m]["start_line"] + 1,
                        "is_test": bool(
                            symbols[m]["is_test"] or is_test_path(symbols[m]["file_path"])
                        ),
                    }
                    for m in members
                ],
            }
        )
    result.sort(key=lambda g: (-g["value"], -g["avg_similarity"], g["members"][0]["file_path"]))
    return result


def params_key(min_similarity: float, kind: str, include_tests: bool, min_lines: int) -> str:
    return f"{min_similarity:.2f}|{kind}|{int(include_tests)}|{min_lines}"


def cached_groups(
    conn, repo_id: str, min_similarity: float, kind: str, include_tests: bool, min_lines: int
) -> list[dict]:
    """Groups for these settings, computed once and cached until the code changes."""
    key = params_key(min_similarity, kind, include_tests, min_lines)
    stored = conn.execute(
        "SELECT * FROM duplicate_groups WHERE params_key = ? ORDER BY id", (key,)
    ).fetchall()
    if stored:
        symbols = _load_symbols(conn)
        out = []
        for g in stored:
            member_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT symbol_id FROM duplicate_members WHERE group_id = ? ORDER BY symbol_id",
                    (g["id"],),
                )
            ]
            out.append(
                {
                    "id": g["id"],
                    "type": g["type"],
                    "size": g["size"],
                    "avg_similarity": g["avg_similarity"],
                    "duplicated_lines": g["duplicated_lines"],
                    "value": g["value"],
                    "members": [_member(symbols[m]) for m in member_ids if m in symbols],
                }
            )
        return out
    groups = compute_groups(conn, min_similarity, kind, include_tests, min_lines)
    for g in groups:
        cur = conn.execute(
            "INSERT INTO duplicate_groups (repo_id, params_key, type, size, avg_similarity,"
            " duplicated_lines, value) VALUES (?,?,?,?,?,?,?)",
            (
                repo_id,
                key,
                g["type"],
                g["size"],
                g["avg_similarity"],
                g["duplicated_lines"],
                g["value"],
            ),
        )
        g["id"] = cur.lastrowid
        conn.executemany(
            "INSERT INTO duplicate_members (group_id, symbol_id) VALUES (?, ?)",
            [(g["id"], m["symbol_id"]) for m in g["members"]],
        )
    return groups


def _member(s: dict) -> dict:
    return {
        "symbol_id": s["id"],
        "qualified_name": s["qualified_name"],
        "file_path": s["file_path"],
        "start_line": s["start_line"],
        "end_line": s["end_line"],
        "lines": s["end_line"] - s["start_line"] + 1,
        "is_test": bool(s["is_test"] or is_test_path(s["file_path"])),
    }


# ---- comparing two members ---------------------------------------------------------------


def source_of(conn, repo_root: Path, symbol_id: int) -> tuple[dict, list[str]] | None:
    """A symbol and its lines. Read from disk when the file still matches the index."""
    s = conn.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,)).fetchone()
    if s is None:
        return None
    lines: list[str] | None = None
    try:
        data = (repo_root / s["file_path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() == s["file_hash"]:
            all_lines = data.decode("utf-8", "replace").splitlines()
            lines = all_lines[s["start_line"] - 1 : s["end_line"]]
    except OSError:
        pass
    if lines is None:  # fall back to the indexed chunk
        chunk = conn.execute(
            "SELECT content FROM chunks WHERE file_path = ? AND start_line = ? AND end_line = ?",
            (s["file_path"], s["start_line"], s["end_line"]),
        ).fetchone()
        lines = chunk["content"].splitlines() if chunk else []
    return dict(s), lines


def diff_lines(a: list[str], b: list[str]) -> dict:
    """A side-by-side diff (rows of equal / replace / delete / insert) and a similarity ratio."""
    left, right = (
        textwrap.dedent("\n".join(a)).splitlines(),
        textwrap.dedent("\n".join(b)).splitlines(),
    )
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    rows = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        for k in range(max(i2 - i1, j2 - j1)):
            rows.append(
                {
                    "op": op,
                    "left": left[i1 + k] if i1 + k < i2 else None,
                    "right": right[j1 + k] if j1 + k < j2 else None,
                }
            )
    return {"rows": rows, "ratio": round(matcher.ratio(), 3)}
