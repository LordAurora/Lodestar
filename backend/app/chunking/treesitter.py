"""Structure-aware chunking with tree-sitter.

tree-sitter turns source code into a syntax tree. We walk the top level of
that tree and cut chunks at *definition* boundaries:

* a function becomes one chunk;
* a class that fits in the token budget becomes one chunk;
* a class that is too large is split into one chunk per method, and each
  method chunk carries the class signature as extra context;
* everything between definitions (imports, constants, top-level statements)
  is gathered into "module" chunks;
* runs of very small neighbouring chunks are merged, so we do not end up with
  hundreds of three-line chunks that carry no meaning on their own.

Each language only needs a small table of node type names (see ``RULES``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.chunking import MAX_TOKENS, MIN_TOKENS, Chunk, estimate_tokens
from app.chunking.fallback import chunk_by_lines


@dataclass(frozen=True)
class LanguageRules:
    functions: frozenset[str]  # node types that are functions / methods
    classes: frozenset[str]  # node types that are classes / interfaces / structs
    wrappers: frozenset[str] = frozenset()  # nodes that wrap a definition (decorators, export)
    containers: frozenset[str] = frozenset()  # nodes whose children we descend into
    variable_decls: frozenset[str] = frozenset()  # JS `const f = () => ...`


RULES: dict[str, LanguageRules] = {
    "python": LanguageRules(
        functions=frozenset({"function_definition"}),
        classes=frozenset({"class_definition"}),
        wrappers=frozenset({"decorated_definition"}),
    ),
    "javascript": LanguageRules(
        functions=frozenset(
            {"function_declaration", "generator_function_declaration", "method_definition"}
        ),
        classes=frozenset({"class_declaration"}),
        wrappers=frozenset({"export_statement"}),
        variable_decls=frozenset({"lexical_declaration", "variable_declaration"}),
    ),
    "typescript": LanguageRules(
        functions=frozenset(
            {
                "function_declaration",
                "generator_function_declaration",
                "method_definition",
                "function_signature",
            }
        ),
        classes=frozenset(
            {
                "class_declaration",
                "abstract_class_declaration",
                "interface_declaration",
                "enum_declaration",
                "type_alias_declaration",
            }
        ),
        wrappers=frozenset({"export_statement"}),
        containers=frozenset({"internal_module", "module"}),
        variable_decls=frozenset({"lexical_declaration", "variable_declaration"}),
    ),
    "go": LanguageRules(
        functions=frozenset({"function_declaration", "method_declaration"}),
        classes=frozenset({"type_declaration"}),
    ),
    "java": LanguageRules(
        functions=frozenset({"method_declaration", "constructor_declaration"}),
        classes=frozenset(
            {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "annotation_type_declaration",
            }
        ),
    ),
    "csharp": LanguageRules(
        functions=frozenset(
            {
                "method_declaration",
                "constructor_declaration",
                "local_function_statement",
                "operator_declaration",
            }
        ),
        classes=frozenset(
            {
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "enum_declaration",
                "record_declaration",
            }
        ),
        containers=frozenset({"namespace_declaration", "file_scoped_namespace_declaration"}),
    ),
}
RULES["tsx"] = RULES["typescript"]

# Node types that hold the members of a class, per grammar.
CLASS_BODY_TYPES = frozenset(
    {"block", "class_body", "declaration_list", "interface_body", "enum_body", "object_type"}
)
JS_FUNCTION_VALUES = frozenset(
    {"arrow_function", "function_expression", "function", "generator_function"}
)


@dataclass
class _Piece:
    """An intermediate chunk before small neighbours are merged."""

    kind: str
    name: str | None
    start_row: int  # 0-based
    end_row: int  # 0-based, inclusive
    context: str | None = None
    names: list[str] = field(default_factory=list)


class _FileChunker:
    def __init__(self, path: str, source: str, language: str, rules: LanguageRules):
        self.path = path
        self.language = language
        self.rules = rules
        self.lines = source.splitlines()
        self.pieces: list[_Piece] = []

    # ---- helpers ------------------------------------------------------

    def text(self, start_row: int, end_row: int) -> str:
        return "\n".join(self.lines[start_row : end_row + 1])

    def tokens(self, start_row: int, end_row: int) -> int:
        return estimate_tokens(self.text(start_row, end_row))

    @staticmethod
    def name_of(node) -> str | None:
        name = node.child_by_field_name("name")
        if name is None and node.type == "type_declaration":  # Go: `type X struct {}`
            spec = next((c for c in node.named_children if c.type == "type_spec"), None)
            name = spec.child_by_field_name("name") if spec else None
        return name.text.decode("utf-8", "replace") if name is not None else None

    def classify(self, node) -> tuple[str, str | None, object] | None:
        """Return (kind, name, definition_node) if ``node`` is a definition."""
        rules = self.rules
        if node.type in rules.wrappers:
            inner = node.child_by_field_name("definition") or node.child_by_field_name(
                "declaration"
            )
            return self.classify(inner) if inner is not None else None
        if node.type in rules.functions:
            return "function", self.name_of(node), node
        if node.type in rules.classes:
            return "class", self.name_of(node), node
        if node.type in rules.variable_decls:
            # `const handler = async (req) => { ... }` is a function in all but name.
            for declarator in node.named_children:
                value = declarator.child_by_field_name("value")
                if value is not None and value.type in JS_FUNCTION_VALUES:
                    return "function", self.name_of(declarator), node
        return None

    # ---- walking ------------------------------------------------------

    def walk(self, nodes, scope: str | None = None, context: str | None = None) -> None:
        """Turn a list of sibling nodes into pieces, collecting gaps between definitions."""
        gap_start: int | None = None
        gap_end: int | None = None

        def flush_gap() -> None:
            nonlocal gap_start, gap_end
            if gap_start is not None and self.text(gap_start, gap_end).strip():
                kind = "block" if scope else "module"
                self.add(kind, scope, gap_start, gap_end, context)
            gap_start = gap_end = None

        for node in nodes:
            if node.type in self.rules.containers:
                flush_gap()
                body = node.child_by_field_name("body")
                self.walk(body.named_children if body is not None else node.named_children)
                continue

            found = self.classify(node)
            if found is None:
                row_start, row_end = node.start_point.row, node.end_point.row
                gap_start = row_start if gap_start is None else gap_start
                gap_end = row_end if gap_end is None else max(gap_end, row_end)
                continue

            flush_gap()
            kind, name, definition = found
            qualified = f"{scope}.{name}" if scope and name else (name or scope)
            start, end = node.start_point.row, node.end_point.row
            if kind == "function":
                self.add("method" if scope else "function", qualified, start, end, context)
            else:
                self.add_class(definition, qualified, start, end)
        flush_gap()

    def add_class(self, node, name: str | None, start: int, end: int) -> None:
        if self.tokens(start, end) <= MAX_TOKENS:
            self.add("class", name, start, end)
            return

        # Too big: keep the signature as context and chunk the members.
        body = node.child_by_field_name("body")
        if body is None or body.type not in CLASS_BODY_TYPES:
            self.add("class", name, start, end)  # split by lines in add()
            return
        signature = self.lines[start].strip()
        first_member_row = body.named_children[0].start_point.row if body.named_children else end
        header_end = max(start, first_member_row - 1)
        # The class header chunk: signature plus anything before the first member.
        self.add("class", name, start, header_end)
        members = [c for c in body.named_children if c.start_point.row > header_end]
        self.walk(members, scope=name, context=f"{signature} ...")

    def add(self, kind: str, name: str | None, start: int, end: int, context=None) -> None:
        self.pieces.append(_Piece(kind, name, start, end, context, [name] if name else []))

    # ---- output -------------------------------------------------------

    def merge_small(self) -> list[_Piece]:
        """Merge runs of tiny neighbouring pieces (imports, constants, one-liners)."""
        merged: list[_Piece] = []
        for piece in self.pieces:
            prev = merged[-1] if merged else None
            if (
                prev is not None
                and prev.context == piece.context
                and self.tokens(prev.start_row, prev.end_row) < MIN_TOKENS
                and self.tokens(piece.start_row, piece.end_row) < MIN_TOKENS
                and self.tokens(prev.start_row, piece.end_row) <= MAX_TOKENS
            ):
                prev.end_row = piece.end_row
                prev.names.extend(piece.names)
                if prev.kind != piece.kind:
                    prev.kind = "block" if prev.context else "module"
                continue
            merged.append(piece)
        return merged

    def build(self, root) -> list[Chunk]:
        self.walk(root.named_children)
        chunks: list[Chunk] = []
        for piece in self.merge_small():
            names = list(dict.fromkeys(piece.names))
            name = ", ".join(names[:3]) + (", ..." if len(names) > 3 else "") if names else None
            body = self.text(piece.start_row, piece.end_row)
            if estimate_tokens(body) > MAX_TOKENS * 1.5:
                # Very long function or module section: split into windows,
                # keeping the first line (the signature) as context for the parts.
                parts = chunk_by_lines(
                    self.path,
                    body,
                    self.language,
                    first_line=piece.start_row + 1,
                    symbol_name=name,
                    symbol_kind=piece.kind,
                    window=50,
                    overlap=10,
                )
                signature = self.lines[piece.start_row].strip()
                for i, part in enumerate(parts):
                    part.context = (
                        piece.context if i == 0 else (piece.context or f"{signature} ...")
                    )
                chunks.extend(parts)
            else:
                chunks.append(
                    Chunk(
                        file_path=self.path,
                        language=self.language,
                        symbol_name=name,
                        symbol_kind=piece.kind,
                        start_line=piece.start_row + 1,
                        end_line=piece.end_row + 1,
                        content=body,
                        context=piece.context,
                    )
                )
        return chunks


def chunk_with_treesitter(path: str, source: str, language: str) -> list[Chunk] | None:
    """Chunk ``source`` with tree-sitter. Returns ``None`` if parsing is not possible."""
    rules = RULES.get(language)
    if rules is None:
        return None
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(language)
    except Exception:  # grammar not available offline, etc.
        return None

    tree = parser.parse(source.encode("utf-8"))
    return _FileChunker(path, source, language, rules).build(tree.root_node)
