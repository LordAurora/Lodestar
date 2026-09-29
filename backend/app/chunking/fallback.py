"""Sliding-window chunker for files tree-sitter cannot parse.

We cut the file into windows of ~60 lines that overlap by 10 lines. The
overlap means a snippet that straddles a window boundary still appears whole
in at least one chunk.
"""

from __future__ import annotations

from app.chunking import Chunk

WINDOW_LINES = 60
OVERLAP_LINES = 10


def chunk_by_lines(
    path: str,
    source: str,
    language: str,
    *,
    first_line: int = 1,
    symbol_name: str | None = None,
    symbol_kind: str = "block",
    window: int = WINDOW_LINES,
    overlap: int = OVERLAP_LINES,
) -> list[Chunk]:
    """Split ``source`` into overlapping line windows.

    ``first_line`` lets callers (like the tree-sitter chunker splitting an
    oversized function) keep line numbers relative to the whole file.
    """
    lines = source.splitlines()
    if not any(line.strip() for line in lines):
        return []

    chunks: list[Chunk] = []
    step = max(1, window - overlap)
    start = 0
    part = 1
    while start < len(lines):
        end = min(start + window, len(lines))
        body = "\n".join(lines[start:end])
        if body.strip():
            name = symbol_name
            if symbol_name and (start > 0 or end < len(lines)):
                name = f"{symbol_name} (part {part})"
            chunks.append(
                Chunk(
                    file_path=path,
                    language=language,
                    symbol_name=name,
                    symbol_kind=symbol_kind,
                    start_line=first_line + start,
                    end_line=first_line + end - 1,
                    content=body,
                )
            )
            part += 1
        if end == len(lines):
            break
        start += step
    return chunks
