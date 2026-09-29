"""Code chunking.

A *chunk* is the unit we embed and retrieve. Good chunks follow the natural
structure of code (functions, methods, classes), which is why we parse files
with tree-sitter when we can, and fall back to overlapping line windows when
we cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

# File extension -> language name understood by tree-sitter-language-pack.
TREE_SITTER_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".cs": "csharp",
}

# Extra languages we recognise for labelling and highlighting, but chunk with
# the line-window fallback.
OTHER_LANGUAGES: dict[str, str] = {
    ".md": "markdown",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "bash",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".vue": "vue",
    ".svelte": "svelte",
}

# Rough token budget per chunk. We estimate tokens as characters / 4, which is
# close enough for English text and code with BPE tokenizers.
MIN_TOKENS = 100
MAX_TOKENS = 400


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def detect_language(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return TREE_SITTER_LANGUAGES.get(suffix) or OTHER_LANGUAGES.get(suffix) or "text"


@dataclass
class Chunk:
    """A piece of a source file, ready to be embedded and stored."""

    file_path: str
    language: str
    symbol_name: str | None
    symbol_kind: str  # function | method | class | module | block
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    content: str
    # Optional extra context that is embedded but not shown as the chunk body,
    # e.g. the signature of the class a method belongs to.
    context: str | None = None

    def embedding_text(self) -> str:
        """The text we actually embed: a header naming file and symbol, then the code.

        Putting the path and symbol in front means a question like "where is
        the login handler?" can match `auth/views.py :: login` even if the
        code body never uses the word "login".
        """
        comment = "//" if self.language not in {"python", "text", "markdown", "yaml"} else "#"
        header = f"{comment} {self.file_path}"
        if self.symbol_name:
            header += f" :: {self.symbol_name}"
        parts = [header]
        if self.context:
            parts.append(self.context)
        parts.append(self.content)
        return "\n".join(parts)


def chunk_file(path: str, source: str) -> list[Chunk]:
    """Split one file into chunks, choosing the best strategy for its language."""
    from app.chunking.fallback import chunk_by_lines
    from app.chunking.treesitter import chunk_with_treesitter

    language = detect_language(path)
    if language in TREE_SITTER_LANGUAGES.values():
        chunks = chunk_with_treesitter(path, source, language)
        if chunks is not None:
            return chunks
    return chunk_by_lines(path, source, language)
