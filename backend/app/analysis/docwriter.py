"""Docstring suggestions: ask the model for the *content*, render the comment ourselves,
and insert it into the source file with safeguards.

This is the only part of Lodestar that can change files in the user's repository, so it is
deliberately narrow:

* the model never writes syntax. It answers in a tiny fixed format (SUMMARY / PARAM / RETURNS)
  which is validated and then rendered per language by code in this module;
* the only edit is inserting comment or docstring text at a position computed with tree-sitter;
* before anything is written the edited text is re-parsed and compared byte for byte with the
  original outside the inserted text; any doubt means no write;
* the original file is copied to ``.lodestar-backup/<timestamp>/`` first;
* line endings and indentation of the surrounding code are reused.

Everything here is pure (bytes in, bytes out) except :func:`backup_and_write`.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.analysis.pipeline import _parser

BACKUP_DIR = ".lodestar-backup"
STYLES = {"google", "numpy", "rest"}
SUPPORTED = {"python", "javascript", "typescript", "tsx", "java", "go", "csharp"}

MAX_SUMMARY = 200
MAX_PART = 200
MAX_PARAMS = 12
MAX_SOURCE_LINES = 60
MAX_SOURCE_CHARS = 3500


class DocError(Exception):
    """A suggestion cannot be produced or applied. The message is safe to show to the user."""


# ---------------------------------------------------------------- signature ----------------


def signature_names(signature: str) -> list[str]:
    """Every identifier inside the parameter list of ``signature`` (a superset of the
    parameter names: types are included, which is fine for "is this name in the signature")."""
    start = signature.find("(")
    if start < 0:
        return []
    depth = 0
    end = len(signature)
    for i in range(start, len(signature)):
        if signature[i] == "(":
            depth += 1
        elif signature[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    return re.findall(r"[A-Za-z_$][\w$]*", signature[start + 1 : end])


def parameter_names(signature: str, language: str) -> list[str]:
    """Best-effort ordered parameter names, used to seed the prompt (not for validation)."""
    start = signature.find("(")
    if start < 0:
        return []
    depth, parts, current = 0, [], []
    for i in range(start, len(signature)):
        ch = signature[i]
        if ch in "([{<":
            depth += 1
            if depth == 1:
                continue
        elif ch in ")]}>":
            depth -= 1
            if depth == 0 and ch == ")":
                break
        if ch == "," and depth == 1:
            parts.append("".join(current))
            current = []
        elif depth >= 1:
            current.append(ch)
    parts.append("".join(current))
    names: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if language == "python":
            name = re.match(r"\*{0,2}([A-Za-z_]\w*)", part)
        elif language in {"javascript", "typescript", "tsx"}:
            name = re.match(
                r"(?:(?:public|private|protected|readonly)\s+)*\.{0,3}([A-Za-z_$][\w$]*)", part
            )
        elif language == "go":
            name = re.match(r"([A-Za-z_]\w*)", part)
        else:  # java, csharp: `Type name` or `Type name = default`
            head = part.split("=")[0].strip()
            name = re.search(r"([A-Za-z_]\w*)$", head)
        if name and name.group(1) not in {"self", "cls", "this"}:
            names.append(name.group(1))
    return names[:MAX_PARAMS]


# ------------------------------------------------------------------ prompt ----------------

PROMPT = """Write documentation for this {language} {kind}.

Answer in exactly this format and nothing else:
SUMMARY: one short sentence saying what it does
PARAM name: what the parameter is for (one line per parameter that appears in the signature)
RETURNS: what it returns (leave this line out if it returns nothing)

Example answer:
SUMMARY: Return the total price of an order including tax.
PARAM order: The order whose lines are summed.
PARAM rate: Tax rate as a fraction, for example 0.2.
RETURNS: The total as a float.

Signature: {signature}
{callers}
Code:
{source}
"""


def build_prompt(language: str, kind: str, signature: str, source: str, callers: list[str]) -> str:
    lines = source.splitlines()
    if len(lines) > MAX_SOURCE_LINES:
        lines = [*lines[:MAX_SOURCE_LINES], "..."]
    code = "\n".join(lines)[:MAX_SOURCE_CHARS]
    called_by = f"Called by: {', '.join(callers[:3])}\n" if callers else ""
    return PROMPT.format(
        language=language, kind=kind, signature=signature, callers=called_by, source=code
    )


@dataclass
class Doc:
    summary: str
    params: list[tuple[str, str]]
    returns: str | None


def parse_answer(raw: str, signature: str) -> Doc:
    """Parse and validate the model's answer. Raises :class:`DocError` when it is unusable."""
    if "```" in raw:
        raise DocError("The answer contained a code block.")
    summary = ""
    params: list[tuple[str, str]] = []
    returns = None
    allowed = set(signature_names(signature))
    for line in raw.splitlines():
        line = line.strip().lstrip("-* ").strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("SUMMARY:"):
            summary = line[8:].strip()
        elif upper.startswith("PARAM "):
            name, _, text = line[6:].partition(":")
            name = name.strip().lstrip("*")
            if name not in allowed:
                raise DocError(f"The answer documents '{name}', which is not a parameter.")
            if text.strip() and len(text.strip()) <= MAX_PART:
                params.append((name, text.strip()))
        elif upper.startswith("RETURNS:") or upper.startswith("RETURN:"):
            returns = line.partition(":")[2].strip() or None
    if not summary:
        raise DocError("The answer had no summary.")
    if len(summary) > MAX_SUMMARY:
        raise DocError("The summary was too long.")
    if returns and len(returns) > MAX_PART:
        returns = None
    doc = Doc(summary, params[:MAX_PARAMS], returns)
    for text in [doc.summary, *(t for _, t in doc.params), doc.returns or ""]:
        if "*/" in text or '"""' in text or "\\" in text:
            raise DocError("The answer contained characters that would break a comment.")
    return doc


# ---------------------------------------------------------------- rendering ---------------


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def render_lines(doc: Doc, language: str, name: str, style: str = "google") -> list[str]:
    """The comment body as plain lines (no delimiters, no indentation)."""
    summary = _sentence(doc.summary)
    if language == "python":
        out = [summary]
        if style == "numpy":
            if doc.params:
                out += ["", "Parameters", "----------"]
                for pname, text in doc.params:
                    out += [pname, f"    {text}"]
            if doc.returns:
                out += ["", "Returns", "-------", f"    {doc.returns}"]
        elif style == "rest":
            out += [""] if doc.params or doc.returns else []
            out += [f":param {p}: {t}" for p, t in doc.params]
            if doc.returns:
                out.append(f":returns: {doc.returns}")
        else:
            if doc.params:
                out += ["", "Args:"] + [f"    {p}: {t}" for p, t in doc.params]
            if doc.returns:
                out += ["", "Returns:", f"    {doc.returns}"]
        return out
    if language == "go":
        acronym = len(summary) > 1 and summary[1].isupper()
        first = summary if acronym else summary[0].lower() + summary[1:]
        return [f"{name} {first}"]
    if language == "csharp":
        esc = html.escape
        out = ["<summary>", esc(summary), "</summary>"]
        out += [f'<param name="{p}">{esc(t)}</param>' for p, t in doc.params]
        if doc.returns:
            out.append(f"<returns>{esc(doc.returns)}</returns>")
        return out
    ret = "@return" if language == "java" else "@returns"
    out = [summary]
    if doc.params or doc.returns:
        out.append("")
    out += [f"@param {p} {t}" for p, t in doc.params]
    if doc.returns:
        out.append(f"{ret} {doc.returns}")
    return out


def render_comment(lines: list[str], language: str, indent: str, eol: str) -> str:
    """Wrap ``lines`` in the language's comment syntax, each line indented and ending in ``eol``."""
    if language == "python":
        if len(lines) == 1:
            return f'{indent}"""{lines[0]}"""{eol}'
        body = [f"{indent}{line}".rstrip() for line in lines]
        body[0] = f'{indent}"""{lines[0]}'
        return eol.join([*body, f'{indent}"""']) + eol
    if language in {"go"}:
        return "".join(f"{indent}// {line}{eol}" for line in lines)
    if language == "csharp":
        return "".join(f"{indent}/// {line}{eol}" for line in lines)
    out = [f"{indent}/**"] + [f"{indent} * {line}".rstrip() for line in lines] + [f"{indent} */"]
    return eol.join(out) + eol


# ------------------------------------------------------------ insertion point -------------


@dataclass
class Insertion:
    offset: int  # byte offset where the text goes (always the start of a line)
    indent: str
    eol: str


def detect_eol(source: bytes) -> str:
    crlf = source.count(b"\r\n")
    lf = source.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _line_starts(source: bytes) -> list[int]:
    starts = [0]
    for m in re.finditer(rb"\n", source):
        starts.append(m.end())
    return starts


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _leading_whitespace(source: bytes, line_start: int) -> bytes:
    m = re.match(rb"[ \t]*", source[line_start:])
    return m.group(0)


def parse(source: bytes, language: str):
    if language not in SUPPORTED:
        raise DocError(f"Docstrings are not supported for {language} files.")
    return _parser(language).parse(source)


def find_insertion(source: bytes, language: str, start_line: int) -> Insertion:
    """Where the doc comment for the definition starting on ``start_line`` (1-based) goes."""
    try:
        source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocError("The file is not valid UTF-8, so it is left alone.") from exc
    tree = parse(source, language)
    if tree.root_node.has_error:
        raise DocError("The file has syntax errors, so it is left alone.")
    starts = _line_starts(source)
    if not 1 <= start_line <= len(starts):
        raise DocError("The definition is no longer where it was.")
    row = start_line - 1
    eol = detect_eol(source)

    if language == "python":
        for node in _walk(tree.root_node):
            if node.type not in {"function_definition", "class_definition"}:
                continue
            outer = (
                node.parent if node.parent and node.parent.type == "decorated_definition" else node
            )
            if outer.start_point.row != row:
                continue
            body = node.child_by_field_name("body")
            if body is None or not body.children:
                break
            first = body.children[0]
            if first.start_point.row == node.start_point.row:
                raise DocError("One-line functions are not supported.")
            line_start = starts[first.start_point.row]
            indent = _leading_whitespace(source, line_start)
            if len(indent) != first.start_point.column:
                raise DocError("Could not determine the indentation.")
            return Insertion(line_start, indent.decode(), eol)
        raise DocError("Could not find the definition.")

    line_start = starts[row]
    indent = _leading_whitespace(source, line_start)
    column = len(indent)
    for node in _walk(tree.root_node):
        if node.start_point.row > row:
            break
        if node.start_point.row == row and node.start_point.column == column:
            kind = node.type
            if any(
                w in kind
                for w in ("function", "method", "class", "declaration", "constructor", "export")
            ):
                return Insertion(line_start, indent.decode(), eol)
    raise DocError("Could not find the definition.")


def insert_text(source: bytes, insertion: Insertion, text: str) -> bytes:
    return source[: insertion.offset] + text.encode() + source[insertion.offset :]


def verify_edit(language: str, old: bytes, new: bytes, edits: list[tuple[int, int]]) -> None:
    """Refuse the edit unless it only added text and the file still parses.

    ``edits`` are ``(offset in old, inserted length)`` pairs. Cutting the inserted ranges out
    of ``new`` must give back exactly ``old``.
    """
    rebuilt: list[bytes] = []
    cursor = 0  # position in `new`
    added = 0
    for offset, length in sorted(edits):
        start = offset + added
        rebuilt.append(new[cursor:start])
        cursor = start + length
        added += length
    rebuilt.append(new[cursor:])
    if b"".join(rebuilt) != old:
        raise DocError("The edit would change existing code, so nothing was written.")
    if parse(new, language).root_node.has_error:
        raise DocError("The edit would introduce a syntax error, so nothing was written.")


def apply_edits(
    source: bytes, language: str, jobs: list[tuple[int, list[str]]]
) -> tuple[bytes, list[tuple[int, int]]]:
    """Insert several doc comments into one file.

    ``jobs`` are ``(start_line, comment lines)``. Positions are computed on the original text
    and applied from the bottom up, so earlier positions stay valid. Returns the new bytes and
    the ``(offset, length)`` of every insertion.
    """
    plan = [(find_insertion(source, language, line), lines) for line, lines in jobs]
    plan.sort(key=lambda p: p[0].offset)
    if len({p[0].offset for p in plan}) != len(plan):
        raise DocError("Two suggestions point at the same place.")
    edits: list[tuple[int, int]] = []
    out = source
    for insertion, lines in reversed(plan):
        text = render_comment(lines, language, insertion.indent, insertion.eol)
        out = insert_text(out, insertion, text)
        edits.append((insertion.offset, len(text.encode())))
    verify_edit(language, source, out, edits)
    return out, edits


def unified_diff(path: str, old: bytes, new: bytes) -> str:
    return "".join(
        difflib.unified_diff(
            old.decode("utf-8", "replace").splitlines(keepends=True),
            new.decode("utf-8", "replace").splitlines(keepends=True),
            f"a/{path}",
            f"b/{path}",
            n=3,
        )
    ).replace("\r\n", "\n")


# ------------------------------------------------------------------- files ----------------


def file_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_in_repo(root: Path, rel: str) -> Path:
    root = root.resolve()
    target = (root / rel).resolve()
    if root not in target.parents or not target.is_file():
        raise DocError("That file is not inside the repository.")
    if BACKUP_DIR in target.relative_to(root).parts:
        raise DocError("Backups are not edited.")
    return target


def backup_and_write(root: Path, rel: str, target: Path, original: bytes, new: bytes) -> Path:
    """Copy the original to ``.lodestar-backup/<timestamp>/<rel>``, then replace the file.

    The new content is written to a temporary file next to the target and moved into place,
    so a crash cannot leave a half-written source file.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = root.resolve() / BACKUP_DIR / stamp / rel
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(original)
    if backup.read_bytes() != original:
        raise DocError("The backup could not be verified, so nothing was written.")
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".lodestar-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new)
        shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return backup
