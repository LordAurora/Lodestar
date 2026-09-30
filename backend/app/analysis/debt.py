"""Tech debt board: collect ``TODO`` / ``FIXME`` / ``HACK`` ... comments.

* **Only comments count.** For languages with a tree-sitter grammar we read the
  *comment nodes*, so ``"TODO: not a task"`` inside a string literal is ignored. Other
  text files use a regex that looks for a comment marker followed by a tag.
* A tag must start the comment line (``# TODO: x``, ``/* FIXME */``, ``* HACK``), and it
  is case sensitive, so prose like "note that ..." is not a task.
* If the folder is a Git repository and ``git`` is installed, ``git blame`` adds author
  and date (a local command, nothing touches the network). Without git everything else
  still works.
* Topics: item texts are embedded and grouped by similarity, then each group gets a short
  label from the chat model (falling back to its most frequent keywords).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import shutil
import subprocess
import time
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.analysis.extract import SUPPORTED_LANGUAGES
from app.analysis.pipeline import FileContext

DEFAULT_TAGS = ("TODO", "FIXME", "HACK", "XXX", "BUG", "NOTE")
MAX_TEXT = 300
PROSE_LANGUAGES = {"markdown", "text"}
_LEADER = re.compile(r"^[\s/*#\-;!<>]+")
_TRAILER = re.compile(r"(\*/|-->)\s*$")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "it", "this", "that",
    "we", "be", "with", "as", "at", "by", "from", "not", "should", "need", "needs", "make",
    "use", "when", "if", "are", "was", "will", "can", "add", "fix", "later", "here", "there",
    "todo", "fixme", "hack", "xxx", "bug", "note",
}  # fmt: skip


def parse_tags(raw: str | tuple[str, ...]) -> tuple[str, ...]:
    tags = raw.split(",") if isinstance(raw, str) else raw
    cleaned = tuple(dict.fromkeys(t.strip().upper() for t in tags if t.strip()))
    return cleaned or DEFAULT_TAGS


@dataclass
class DebtHit:
    tag: str
    text: str
    line: int
    assignee: str | None


class TagMatcher:
    def __init__(self, tags: tuple[str, ...]):
        alt = "|".join(re.escape(t) for t in sorted(tags, key=len, reverse=True))
        self.line = re.compile(rf"^({alt})\b(?:\(([^)\n]*)\))?\s*[:\-–—]?\s*(.*)$")
        # for languages without a grammar: a comment marker, then the tag
        self.marked = re.compile(
            rf"(?:^|\s)(?://+|#+|--+|;+|/\*+|<!--)\s*({alt})\b(?:\(([^)\n]*)\))?\s*[:\-–—]?\s*(.*)$"
        )
        self.block_line = re.compile(rf"^\s*\*\s*({alt})\b(?:\(([^)\n]*)\))?\s*[:\-–—]?\s*(.*)$")
        self.prose = re.compile(
            rf"^\s*(?:[-*+]\s+)?(?:\[[ xX]\]\s*)?({alt})\b(?:\(([^)\n]*)\))?\s*[:\-–—]\s*(.*)$"
        )

    def in_comment(self, comment: str, first_line: int) -> list[DebtHit]:
        """Tags at the start of any line of a comment's text."""
        hits = []
        for offset, raw in enumerate(comment.splitlines()):
            match = self.line.match(_LEADER.sub("", raw))
            if match:
                hits.append(self._hit(match, first_line + offset))
        return hits

    def in_text(self, source: str, language: str) -> list[DebtHit]:
        hits = []
        for number, raw in enumerate(source.splitlines(), start=1):
            if "\x00" in raw or len(raw) > 2000:
                continue
            patterns = (
                (self.prose,) if language in PROSE_LANGUAGES else (self.marked, self.block_line)
            )
            for pattern in patterns:
                match = pattern.search(raw)
                if match:
                    hits.append(self._hit(match, number))
                    break
        return hits

    @staticmethod
    def _hit(match, line: int) -> DebtHit:
        text = _TRAILER.sub("", match.group(3)).strip()[:MAX_TEXT]
        return DebtHit(match.group(1), text, line, (match.group(2) or "").strip() or None)


def _comment_nodes(root):
    stack = [root]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            yield node
            continue  # comments have no comment children worth visiting
        stack.extend(node.named_children)


class DebtAnalyzer:
    name = "debt"
    languages = None  # every text file

    def __init__(self, tags: tuple[str, ...] | str = DEFAULT_TAGS):
        self.tags = parse_tags(tags)
        self.matcher = TagMatcher(self.tags)
        # Changing the configured tags must re-analyze every file.
        self.version = 1 + zlib.crc32(",".join(self.tags).encode())

    def analyze(self, ctx: FileContext, conn, repo_id: str) -> dict | None:
        root = ctx.root() if ctx.language in SUPPORTED_LANGUAGES | {"tsx"} else None
        if root is not None:
            hits = []
            for node in _comment_nodes(root):
                text = ctx.source[node.start_byte : node.end_byte].decode("utf-8", "replace")
                hits += self.matcher.in_comment(text, node.start_point.row + 1)
        else:
            hits = self.matcher.in_text(ctx.text, ctx.language)
        if not hits:
            return None

        symbols = conn.execute(
            "SELECT id, start_line, end_line FROM symbols WHERE file_path = ?", (ctx.path,)
        ).fetchall()

        def enclosing(line: int) -> int | None:
            inside = [s for s in symbols if s["start_line"] <= line <= s["end_line"]]
            return (
                min(inside, key=lambda s: s["end_line"] - s["start_line"])["id"] if inside else None
            )

        conn.executemany(
            "INSERT INTO debt_items (repo_id, tag, text, file_path, line, symbol_id, assignee,"
            " file_hash) VALUES (?,?,?,?,?,?,?,?)",
            [(repo_id, h.tag, h.text, ctx.path, h.line, enclosing(h.line), h.assignee, ctx.hash)
             for h in hits],
        )  # fmt: skip
        return None

    def delete(self, conn, path: str) -> None:
        conn.execute("DELETE FROM debt_items WHERE file_path = ?", (path,))

    def finalize(self, conn, repo_id: str, root: Path) -> None:
        enrich_with_blame(conn, root)


# ---- git blame ---------------------------------------------------------------------------


def git_available(root: Path) -> bool:
    if shutil.which("git") is None:
        return False
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=15, check=False,
        )  # fmt: skip
        return out.returncode == 0 and out.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def parse_blame(output: str) -> dict[int, tuple[str | None, float | None]]:
    """Parse ``git blame --line-porcelain``: final line number -> (author, unix time)."""
    result: dict[int, tuple[str | None, float | None]] = {}
    header = re.compile(r"^[0-9a-f]{40} \d+ (\d+)")
    line_no, author, when = None, None, None
    for raw in output.splitlines():
        match = header.match(raw)
        if match:
            line_no, author, when = int(match.group(1)), None, None
        elif raw.startswith("author "):
            author = raw[7:].strip()
        elif raw.startswith("author-time "):
            when = float(raw[12:].strip())
        elif raw.startswith("\t") and line_no is not None:
            uncommitted = author in (None, "Not Committed Yet")
            result[line_no] = (None if uncommitted else author, None if uncommitted else when)
    return result


def enrich_with_blame(conn, root: Path, batch: int = 100) -> int:
    """Fill author and date for items not blamed yet. Fails silently; returns items enriched."""
    files = [
        r[0] for r in conn.execute("SELECT DISTINCT file_path FROM debt_items WHERE blamed = 0")
    ]
    if not files or not git_available(root):
        return 0  # not a Git repo, or no git: leave the items for a later run
    enriched = 0
    for path in files:
        lines = [r[0] for r in conn.execute(
            "SELECT DISTINCT line FROM debt_items WHERE file_path = ? AND blamed = 0", (path,)
        )]  # fmt: skip
        found: dict[int, tuple[str | None, float | None]] = {}
        for start in range(0, len(lines), batch):
            ranges = [arg for n in lines[start : start + batch] for arg in ("-L", f"{n},{n}")]
            command = [
                "git",
                "-C",
                str(root),
                "blame",
                "--line-porcelain",
                "-w",
                *ranges,
                "--",
                path,
            ]
            try:
                out = subprocess.run(
                    command, capture_output=True, text=True, timeout=30, check=False,
                    encoding="utf-8", errors="replace",
                )  # fmt: skip
            except (OSError, subprocess.SubprocessError):
                continue
            if out.returncode == 0:
                found.update(parse_blame(out.stdout))
        for line in lines:
            author, when = found.get(line, (None, None))
            conn.execute(
                "UPDATE debt_items SET author = ?, commit_date = ?, blamed = 1"
                " WHERE file_path = ? AND line = ?",
                (author, when, path, line),
            )
            enriched += author is not None
    return enriched


# ---- reading -----------------------------------------------------------------------------

AGE_BUCKETS = [
    ("Last 30 days", 0, 30), ("1 to 6 months", 30, 180),
    ("6 to 12 months", 180, 365), ("Over a year", 365, 10**9),
]  # fmt: skip
DAY = 86400.0


def list_items(
    conn, tag: str = "", q: str = "", folder: str = "", older_than_days: int = 0
) -> list[dict]:
    now = time.time()
    rows = conn.execute(
        "SELECT d.*, s.qualified_name AS symbol FROM debt_items d"
        " LEFT JOIN symbols s ON s.id = d.symbol_id ORDER BY d.file_path, d.line"
    ).fetchall()
    items = []
    for r in rows:
        age = (now - r["commit_date"]) / DAY if r["commit_date"] else None
        if tag and r["tag"] != tag.upper():
            continue
        if q and q.lower() not in f"{r['text']} {r['file_path']} {r['symbol'] or ''}".lower():
            continue
        if folder and not (
            r["file_path"] == folder or r["file_path"].startswith(folder.rstrip("/") + "/")
        ):
            continue
        if older_than_days and (age is None or age < older_than_days):
            continue
        items.append({
            "id": r["id"], "tag": r["tag"], "text": r["text"], "file_path": r["file_path"],
            "line": r["line"], "symbol": r["symbol"], "author": r["author"],
            "assignee": r["assignee"], "commit_date": r["commit_date"],
            "age_days": None if age is None else round(age),
        })  # fmt: skip
    return items


def folder_of(path: str, depth: int = 2) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) or "(root)"


def counters(items: list[dict], tags: tuple[str, ...]) -> dict:
    by_tag = Counter(i["tag"] for i in items)
    dated = [i for i in items if i["age_days"] is not None]
    oldest = max(dated, key=lambda i: i["age_days"], default=None)
    return {
        "total": len(items),
        "by_tag": {t: by_tag.get(t, 0) for t in tags},
        "oldest": oldest,
        "has_dates": bool(dated),
    }


def group_items(
    items: list[dict],
    group_by: str,
    tags: tuple[str, ...],
    topics: dict[int, tuple[int, str]] | None = None,
) -> list[dict]:
    """Group items into ``[{"key", "label", "items"}, ...]`` in a sensible order."""
    groups: dict[str, list[dict]] = defaultdict(list)
    labels: dict[str, str] = {}
    order: list[str] = []
    if group_by == "tag":
        order = [t for t in tags]
        for i in items:
            groups[i["tag"]].append(i)
            labels[i["tag"]] = i["tag"]
    elif group_by == "folder":
        for i in items:
            key = folder_of(i["file_path"])
            groups[key].append(i)
            labels[key] = key
        order = sorted(groups, key=lambda k: (-len(groups[k]), k))
    elif group_by == "age":
        order = [b[0] for b in AGE_BUCKETS] + ["Unknown"]
        for i in items:
            age = i["age_days"]
            key = (
                "Unknown" if age is None else next(n for n, lo, hi in AGE_BUCKETS if lo <= age < hi)
            )
            groups[key].append(i)
            labels[key] = key
    elif group_by == "topic":
        topics = topics or {}
        for i in items:
            cluster, label = topics.get(i["id"], (-1, "Uncategorized"))
            key = f"topic-{cluster}"
            groups[key].append(i)
            labels[key] = label
        order = sorted(groups, key=lambda k: (k == "topic--1", -len(groups[k]), labels[k]))
    else:
        raise ValueError(f"Unknown grouping '{group_by}'.")
    result = [{"key": k, "label": labels.get(k, k), "items": groups.get(k, [])} for k in order]
    return [g for g in result if g["items"] or group_by == "tag"]


def export_markdown(items: list[dict]) -> str:
    lines = ["# Tech debt", "", f"{len(items)} items", ""]
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for i in items:
        by_tag[i["tag"]].append(i)
    for tag in sorted(by_tag):
        lines += [f"## {tag} ({len(by_tag[tag])})", ""]
        for i in by_tag[tag]:
            who = f" ({i['author']})" if i["author"] else ""
            lines.append(f"- {i['text'] or '(no text)'} `{i['file_path']}:{i['line']}`{who}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_csv(items: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["tag", "text", "file", "line", "symbol", "author", "assignee", "age_days"])
    for i in items:
        writer.writerow([i["tag"], i["text"], i["file_path"], i["line"], i["symbol"] or "",
                         i["author"] or "", i["assignee"] or "", i["age_days"] or ""])  # fmt: skip
    return out.getvalue()


# ---- topics ------------------------------------------------------------------------------

AGGLOMERATIVE_LIMIT = 600
LABEL_PROMPT = (
    "Below are related TODO comments from a code base. Reply with a short topic label of "
    "2 to 4 words that names what they are about. Reply with the label only.\n\n{items}\n\nLabel:"
)


def signature(items: list[dict], embedder_name: str) -> str:
    text = "\n".join(f"{i['file_path']}:{i['line']}:{i['tag']}:{i['text']}" for i in items)
    return hashlib.sha256(f"{embedder_name}\n{text}".encode()).hexdigest()


def cluster_vectors(vectors: np.ndarray, threshold: float) -> list[int]:
    """Group unit vectors so that members of a group are similar (average linkage).

    Average-linkage agglomerative clustering merges the two most similar groups until
    no pair is at least ``threshold`` similar. It is exact but cubic, so very large sets
    fall back to a greedy pass that compares each vector with group centroids.
    """
    n = len(vectors)
    if n == 0:
        return []
    if n > AGGLOMERATIVE_LIMIT:
        return _greedy(vectors, threshold)
    sims = vectors @ vectors.T
    members: list[list[int]] = [[i] for i in range(n)]
    active = np.ones(n, dtype=bool)
    sim = sims.copy()
    np.fill_diagonal(sim, -1.0)
    while True:
        masked = np.where(active[:, None] & active[None, :], sim, -1.0)
        flat = int(masked.argmax())
        a, b = divmod(flat, n)
        if masked[a, b] < threshold:
            break
        # merge b into a: the new similarity to every other group is the size-weighted average
        na, nb = len(members[a]), len(members[b])
        merged = (sim[a] * na + sim[b] * nb) / (na + nb)
        sim[a, :] = merged
        sim[:, a] = merged
        sim[a, a] = -1.0
        members[a] += members[b]
        members[b] = []
        active[b] = False
    labels = [0] * n
    for cluster, group in enumerate(g for g in members if g):
        for i in group:
            labels[i] = cluster
    return labels


def _greedy(vectors: np.ndarray, threshold: float) -> list[int]:
    centroids: list[np.ndarray] = []
    sizes: list[int] = []
    labels = []
    for vec in vectors:
        if centroids:
            sims = np.array(centroids) @ vec
            best = int(sims.argmax())
            if sims[best] >= threshold:
                centroids[best] = (centroids[best] * sizes[best] + vec) / (sizes[best] + 1)
                centroids[best] /= np.linalg.norm(centroids[best]) or 1.0
                sizes[best] += 1
                labels.append(best)
                continue
        centroids.append(vec.copy())
        sizes.append(1)
        labels.append(len(centroids) - 1)
    return labels


def keyword_label(texts: list[str], words: int = 3) -> str:
    """The most frequent meaningful words: the fallback when the model gives no usable label."""
    counts: Counter[str] = Counter()
    for text in texts:
        seen = {
            w for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text.lower()) if w not in _STOPWORDS
        }
        counts.update(seen)
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:words]
    return " ".join(w for w, _ in top).capitalize() or "Miscellaneous"


def clean_label(raw: str) -> str | None:
    """Accept a model label only if it looks like one (short, one line, few words)."""
    label = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    label = label.strip("\"'`*. :-").removeprefix("Label:").strip()
    if not label or len(label) > 40 or len(label.split()) > 5 or re.search(r"[{}\[\]<>]", label):
        return None
    return label[:1].upper() + label[1:]
