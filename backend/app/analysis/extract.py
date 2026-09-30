"""Extract symbols, call sites and imports from one file with tree-sitter.

There is one small ``Extractor`` subclass per language. A subclass only says
*which node types matter* (``handlers`` maps a node type to a method) and how to
read them; the shared base class walks the tree and keeps track of the
enclosing function or class, so a call is attributed to the right symbol.

The walk is iterative (an explicit stack), because very long expressions produce
trees deeper than Python's recursion limit.
"""

from __future__ import annotations

import re

from app.analysis import CallInfo, FileFacts, ImportInfo, SymbolInfo
from app.analysis.conventions import is_test_path, is_test_symbol

MAX_SIGNATURE = 200


class Extractor:
    language = ""
    #: node type -> name of the handler method. Each handler returns the (enclosing
    #: symbol, enclosing class) that apply to the node's children.
    handlers: dict[str, str] = {}
    #: node types that wrap a definition (decorators, `export`, `const x = ...`).
    wrappers: frozenset[str] = frozenset()

    def __init__(self, path: str, source: bytes, root):
        self.path = path
        self.src = source
        self.root = root
        self.facts = FileFacts()
        self.test_file = is_test_path(path)

    # ---- the walk -------------------------------------------------------

    def extract(self) -> FileFacts:
        stack = [(self.root, None, None)]  # (node, enclosing symbol index, class index)
        while stack:
            node, enc, cls = stack.pop()
            handler = self.handlers.get(node.type)
            if handler:
                enc, cls = getattr(self, handler)(node, enc, cls)
            for child in reversed(node.named_children):
                stack.append((child, enc, cls))
        return self.facts

    # ---- helpers --------------------------------------------------------

    def text(self, node) -> str:
        return self.src[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def name_of(self, node, field: str = "name") -> str | None:
        child = node.child_by_field_name(field)
        return self.text(child) if child is not None else None

    def outer(self, node):
        """Climb out of wrappers so line numbers and doc comments use the whole definition."""
        while node.parent is not None and node.parent.type in self.wrappers:
            node = node.parent
        return node

    def signature(self, node, body=None) -> str:
        if body is None:
            body = node.child_by_field_name("body")
        end = body.start_byte if body is not None else node.end_byte
        text = " ".join(self.src[node.start_byte : end].decode("utf-8", "replace").split())
        text = text.rstrip(" :{")
        if text.endswith("=>"):
            text = text[:-2].rstrip()
        return text[:MAX_SIGNATURE]

    def add_symbol(
        self, node, name: str, kind: str, enc, cls, *, body=None, owner=None, qualified=None,
        annotations: set[str] | None = None,
    ) -> int:  # fmt: skip
        syms = self.facts.symbols
        if kind == "function" and enc is not None and syms[enc].kind == "class":
            kind = "method"
        if qualified is None:
            qualified = f"{syms[enc].qualified_name}.{name}" if enc is not None else name
        if owner is None and cls is not None:
            owner = syms[cls].qualified_name
        outer = self.outer(node)
        syms.append(
            SymbolInfo(
                name=name,
                qualified_name=qualified,
                kind=kind,
                start_line=outer.start_point.row + 1,
                end_line=outer.end_point.row + 1,
                signature=self.signature(node, body),
                has_doc=self.has_doc(node, outer),
                is_test=self.test_file
                or is_test_symbol(self.language, name, kind, annotations or set()),
                language=self.language,
                owner=owner,
            )
        )
        return len(syms) - 1

    def add_call(self, name: str | None, node, enc, receiver: str | None = None, kind="call"):
        if name:
            self.facts.calls.append(
                CallInfo(name, node.start_point.row + 1, kind, (receiver or None), enc)
            )

    def add_import(self, module: str | None, node, names=None, alias=None):
        if module:
            self.facts.imports.append(
                ImportInfo(module, node.start_point.row + 1, list(names or []), alias)
            )

    def has_doc(self, node, outer) -> bool:
        return False

    def previous_comment(self, outer, prefixes: tuple[str, ...]) -> bool:
        """True when the sibling right before ``outer`` is a doc comment."""
        prev = outer.prev_named_sibling
        return bool(
            prev is not None
            and "comment" in prev.type
            and self.text(prev).lstrip().startswith(prefixes)
            and prev.end_point.row >= outer.start_point.row - 1
        )

    @staticmethod
    def last_segment(dotted: str) -> str:
        return re.split(r"[.:>\-]+", dotted.strip())[-1]

    def receiver_and_name(self, callee) -> tuple[str | None, str | None]:
        """Split a callee expression into (receiver text, called name). Overridden per grammar."""
        return None, self.text(callee) if callee is not None else None


# ---- Python ---------------------------------------------------------------


class PythonExtractor(Extractor):
    language = "python"
    wrappers = frozenset({"decorated_definition"})
    handlers = {
        "function_definition": "on_function",
        "class_definition": "on_class",
        "call": "on_call",
        "import_statement": "on_import",
        "import_from_statement": "on_import_from",
    }

    def has_doc(self, node, outer) -> bool:
        body = node.child_by_field_name("body")
        first = body.named_children[0] if body is not None and body.named_children else None
        if first is not None and first.type == "expression_statement" and first.named_children:
            first = first.named_children[0]
        return first is not None and first.type == "string"

    def on_function(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        return self.add_symbol(node, name, "function", enc, cls), cls

    def on_class(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        idx = self.add_symbol(node, name, "class", enc, cls)
        bases = node.child_by_field_name("superclasses")
        for base in bases.named_children if bases is not None else []:
            if base.type in {"identifier", "attribute"}:
                receiver, called = self.receiver_and_name(base)
                self.add_call(called, base, idx, receiver, "inherit")
        return idx, idx

    def receiver_and_name(self, callee):
        if callee is None:
            return None, None
        if callee.type == "attribute":
            obj = callee.child_by_field_name("object")
            attr = callee.child_by_field_name("attribute")
            receiver = self.text(obj) if obj is not None else None
            if obj is not None and obj.type == "call":  # super().method()
                inner = obj.child_by_field_name("function")
                receiver = "super" if inner is not None and self.text(inner) == "super" else None
            return receiver, self.text(attr) if attr is not None else None
        if callee.type == "identifier":
            return None, self.text(callee)
        return None, None  # calls on call results, subscripts, lambdas, ...

    def on_call(self, node, enc, cls):
        receiver, name = self.receiver_and_name(node.child_by_field_name("function"))
        self.add_call(name, node, enc, receiver)
        return enc, cls

    def on_import(self, node, enc, cls):
        for child in node.children_by_field_name("name"):
            if child.type == "aliased_import":
                self.add_import(self.name_of(child), node, alias=self.name_of(child, "alias"))
            else:
                self.add_import(self.text(child), node)
        return enc, cls

    def on_import_from(self, node, enc, cls):
        module_node = node.child_by_field_name("module_name")
        module = self.text(module_node) if module_node is not None else None
        names = []
        for child in node.children_by_field_name("name"):
            names.append(
                self.name_of(child) if child.type == "aliased_import" else self.text(child)
            )
        if any(c.type == "wildcard_import" for c in node.named_children):
            names.append("*")
        self.add_import(module, node, [n for n in names if n])
        return enc, cls


# ---- JavaScript / TypeScript ----------------------------------------------

_JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


class JavaScriptExtractor(Extractor):
    language = "javascript"
    wrappers = frozenset({"export_statement", "lexical_declaration", "variable_declaration"})
    handlers = {
        "function_declaration": "on_function",
        "generator_function_declaration": "on_function",
        "method_definition": "on_method",
        "class_declaration": "on_class",
        "abstract_class_declaration": "on_class",
        "interface_declaration": "on_class",
        "enum_declaration": "on_class",
        "variable_declarator": "on_declarator",
        "public_field_definition": "on_field",
        "field_definition": "on_field",
        "call_expression": "on_call",
        "new_expression": "on_new",
        "import_statement": "on_import",
        "export_statement": "on_export",
    }

    def has_doc(self, node, outer) -> bool:
        return self.previous_comment(outer, ("/**",))

    def on_function(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        return self.add_symbol(node, name, "function", enc, cls), cls

    def on_method(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        return self.add_symbol(node, name, "method", enc, cls), cls

    def on_class(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        idx = self.add_symbol(node, name, "class", enc, cls)
        for heritage in node.named_children:
            if heritage.type not in {"class_heritage", "extends_type_clause"}:
                continue
            for clause in heritage.named_children:
                targets = (
                    clause.named_children
                    if clause.type in {"extends_clause", "implements_clause"}
                    else [clause]
                )
                for target in targets:
                    if target.type in {"identifier", "type_identifier", "member_expression"}:
                        receiver, called = self.receiver_and_name(target)
                        self.add_call(called, target, idx, receiver, "inherit")
        return idx, idx

    def on_declarator(self, node, enc, cls):
        value = node.child_by_field_name("value")
        name = self.name_of(node)
        if value is None or value.type not in _JS_FUNCTION_VALUES or not name:
            return enc, cls
        idx = self.add_symbol(
            node, name, "function", enc, cls, body=value.child_by_field_name("body")
        )
        return idx, cls

    def on_field(self, node, enc, cls):
        value = node.child_by_field_name("value")
        name = self.name_of(node) or self.name_of(node, "property")  # JS says `property`
        if value is None or value.type not in _JS_FUNCTION_VALUES or not name:
            return enc, cls
        idx = self.add_symbol(
            node, name, "method", enc, cls, body=value.child_by_field_name("body")
        )
        return idx, cls

    def receiver_and_name(self, callee):
        if callee is None:
            return None, None
        if callee.type == "member_expression":
            obj = callee.child_by_field_name("object")
            prop = callee.child_by_field_name("property")
            receiver = self.text(obj) if obj is not None else None
            return receiver, self.text(prop) if prop is not None else None
        if callee.type in {"identifier", "type_identifier"}:
            return None, self.text(callee)
        return None, None

    def on_call(self, node, enc, cls):
        callee = node.child_by_field_name("function")
        if callee is not None and callee.type == "identifier" and self.text(callee) == "require":
            args = node.child_by_field_name("arguments")
            arg = args.named_children[0] if args is not None and args.named_children else None
            if arg is not None and arg.type == "string":
                self.add_import(self.string_value(arg), node)
                return enc, cls
        receiver, name = self.receiver_and_name(callee)
        self.add_call(name, node, enc, receiver)
        return enc, cls

    def on_new(self, node, enc, cls):
        receiver, name = self.receiver_and_name(node.child_by_field_name("constructor"))
        self.add_call(name, node, enc, receiver)
        return enc, cls

    def string_value(self, node) -> str:
        return self.text(node).strip("'\"`")

    def on_import(self, node, enc, cls):
        source = node.child_by_field_name("source")
        names: list[str] = []
        alias = (
            None  # the local name of a default or namespace import: `auth` in `import * as auth`
        )
        for clause in node.named_children:
            if clause.type != "import_clause":
                continue
            for part in clause.named_children:
                if part.type == "identifier":
                    names.append("default")
                    alias = self.text(part)
                elif part.type == "namespace_import":
                    names.append("*")
                    ident = next((c for c in part.named_children if c.type == "identifier"), None)
                    alias = self.text(ident) if ident is not None else None
                elif part.type == "named_imports":
                    names += [n for s in part.named_children if (n := self.name_of(s))]
        if source is not None:
            self.add_import(self.string_value(source), node, names, alias)
        return enc, cls

    def on_export(self, node, enc, cls):  # `export * from './x'`
        source = node.child_by_field_name("source")
        if source is not None:
            self.add_import(self.string_value(source), node, ["*"])
        return enc, cls


class TypeScriptExtractor(JavaScriptExtractor):
    language = "typescript"


# ---- Go -------------------------------------------------------------------

_GO_RECEIVER = re.compile(r"\(\s*(?:\w+\s+)?\*?\s*(\w+)")


class GoExtractor(Extractor):
    language = "go"
    handlers = {
        "package_clause": "on_package",
        "function_declaration": "on_function",
        "method_declaration": "on_method",
        "type_spec": "on_type",
        "call_expression": "on_call",
        "import_spec": "on_import",
    }

    def has_doc(self, node, outer) -> bool:
        return self.previous_comment(outer, ("//", "/*"))

    def on_package(self, node, enc, cls):
        self.facts.namespace = self.text(node).replace("package", "", 1).strip()
        return enc, cls

    def on_function(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        return self.add_symbol(node, name, "function", enc, cls), cls

    def on_method(self, node, enc, cls):
        name = self.name_of(node)
        receiver = node.child_by_field_name("receiver")
        match = _GO_RECEIVER.match(self.text(receiver)) if receiver is not None else None
        owner = match.group(1) if match else None
        if not name:
            return enc, cls
        idx = self.add_symbol(
            node, name, "method", enc, cls, owner=owner,
            qualified=f"{owner}.{name}" if owner else name,
        )  # fmt: skip
        return idx, cls

    def on_type(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        return self.add_symbol(node, name, "class", enc, cls), cls

    def receiver_and_name(self, callee):
        if callee is None:
            return None, None
        if callee.type == "index_expression":  # generic instantiation: f[T](x)
            callee = callee.child_by_field_name("operand") or callee
        if callee.type == "selector_expression":
            operand = callee.child_by_field_name("operand")
            field = callee.child_by_field_name("field")
            receiver = self.text(operand) if operand is not None else None
            return receiver, self.text(field) if field is not None else None
        if callee.type == "identifier":
            return None, self.text(callee)
        return None, None

    def on_call(self, node, enc, cls):
        receiver, name = self.receiver_and_name(node.child_by_field_name("function"))
        self.add_call(name, node, enc, receiver)
        return enc, cls

    def on_import(self, node, enc, cls):
        path = node.child_by_field_name("path")
        if path is not None:
            self.add_import(self.text(path).strip('"`'), node, alias=self.name_of(node))
        return enc, cls


# ---- Java -----------------------------------------------------------------


class JavaExtractor(Extractor):
    language = "java"
    handlers = {
        "package_declaration": "on_package",
        "class_declaration": "on_class",
        "interface_declaration": "on_class",
        "enum_declaration": "on_class",
        "record_declaration": "on_class",
        "annotation_type_declaration": "on_class",
        "method_declaration": "on_method",
        "constructor_declaration": "on_method",
        "method_invocation": "on_call",
        "object_creation_expression": "on_new",
        "import_declaration": "on_import",
    }

    def has_doc(self, node, outer) -> bool:
        return self.previous_comment(outer, ("/**",))

    def annotations(self, node) -> set[str]:
        found: set[str] = set()
        for child in node.named_children:
            if child.type == "modifiers":
                for mod in child.named_children:
                    if mod.type in {"marker_annotation", "annotation"}:
                        name = self.name_of(mod)
                        if name:
                            found.add(self.last_segment(name))
        return found

    def on_package(self, node, enc, cls):
        match = re.search(r"package\s+([\w.]+)", self.text(node))
        self.facts.namespace = match.group(1) if match else None
        return enc, cls

    def on_class(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        idx = self.add_symbol(node, name, "class", enc, cls, annotations=self.annotations(node))
        for field in ("superclass", "interfaces"):
            clause = node.child_by_field_name(field)
            for ident in self.type_names(clause):
                self.add_call(ident, node, idx, None, "inherit")
        for child in node.named_children:
            if child.type == "extends_interfaces":
                for ident in self.type_names(child):
                    self.add_call(ident, node, idx, None, "inherit")
        return idx, idx

    def type_names(self, node) -> list[str]:
        if node is None:
            return []
        if node.type in {"type_identifier", "scoped_type_identifier"}:
            return [self.last_segment(self.text(node))]
        names: list[str] = []
        for child in node.named_children:
            names += self.type_names(child)
        return names

    def on_method(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        idx = self.add_symbol(node, name, "method", enc, cls, annotations=self.annotations(node))
        return idx, cls

    def on_call(self, node, enc, cls):
        obj = node.child_by_field_name("object")
        self.add_call(self.name_of(node), node, enc, self.text(obj) if obj is not None else None)
        return enc, cls

    def on_new(self, node, enc, cls):
        names = self.type_names(node.child_by_field_name("type"))
        self.add_call(names[0] if names else None, node, enc)
        return enc, cls

    def on_import(self, node, enc, cls):
        match = re.search(r"import\s+(?:static\s+)?([\w.]+)(\.\*)?\s*;", self.text(node))
        if match:
            self.add_import(match.group(1), node, ["*"] if match.group(2) else [])
        return enc, cls


# ---- C# -------------------------------------------------------------------


class CSharpExtractor(Extractor):
    language = "csharp"
    handlers = {
        "namespace_declaration": "on_namespace",
        "file_scoped_namespace_declaration": "on_namespace",
        "class_declaration": "on_class",
        "interface_declaration": "on_class",
        "struct_declaration": "on_class",
        "record_declaration": "on_class",
        "enum_declaration": "on_class",
        "method_declaration": "on_method",
        "constructor_declaration": "on_method",
        "local_function_statement": "on_method",
        "invocation_expression": "on_call",
        "object_creation_expression": "on_new",
        "using_directive": "on_using",
    }

    def has_doc(self, node, outer) -> bool:
        return self.previous_comment(outer, ("///", "/**"))

    def annotations(self, node) -> set[str]:
        found: set[str] = set()
        for child in node.named_children:
            if child.type == "attribute_list":
                for attr in child.named_children:
                    name = self.name_of(attr)
                    if name:
                        found.add(self.last_segment(name))
        return found

    def on_namespace(self, node, enc, cls):
        if self.facts.namespace is None:
            self.facts.namespace = self.name_of(node)
        return enc, cls

    def on_class(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        idx = self.add_symbol(node, name, "class", enc, cls, annotations=self.annotations(node))
        for child in node.named_children:
            if child.type == "base_list":
                for base in child.named_children:
                    called = self.simple_name(base)
                    self.add_call(called, node, idx, None, "inherit")
        return idx, idx

    def simple_name(self, node) -> str | None:
        if node.type in {"identifier", "qualified_name"}:
            return self.last_segment(self.text(node))
        if node.type == "generic_name":
            ident = next((c for c in node.named_children if c.type == "identifier"), None)
            return self.text(ident) if ident is not None else None
        if node.type == "primary_constructor_base_type":  # record Foo(...) : Bar(...)
            inner = node.named_children[0] if node.named_children else None
            return self.simple_name(inner) if inner is not None else None
        return None

    def on_method(self, node, enc, cls):
        name = self.name_of(node)
        if not name:
            return enc, cls
        idx = self.add_symbol(node, name, "method", enc, cls, annotations=self.annotations(node))
        return idx, cls

    def receiver_and_name(self, callee):
        if callee is None:
            return None, None
        if callee.type == "member_access_expression":
            expr = callee.child_by_field_name("expression")
            name = callee.child_by_field_name("name")
            return (
                self.text(expr) if expr is not None else None,
                self.simple_name(name) if name is not None else None,
            )
        return None, self.simple_name(callee)

    def on_call(self, node, enc, cls):
        receiver, name = self.receiver_and_name(node.child_by_field_name("function"))
        self.add_call(name, node, enc, receiver)
        return enc, cls

    def on_new(self, node, enc, cls):
        type_node = node.child_by_field_name("type")
        self.add_call(self.simple_name(type_node) if type_node is not None else None, node, enc)
        return enc, cls

    def on_using(self, node, enc, cls):
        match = re.search(r"using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;", self.text(node))
        if match:
            self.add_import(match.group(1), node)
        return enc, cls


EXTRACTORS: dict[str, type[Extractor]] = {
    "python": PythonExtractor,
    "javascript": JavaScriptExtractor,
    "typescript": TypeScriptExtractor,
    "tsx": TypeScriptExtractor,
    "go": GoExtractor,
    "java": JavaExtractor,
    "csharp": CSharpExtractor,
}
SUPPORTED_LANGUAGES = frozenset(EXTRACTORS)


def extract_facts(path: str, language: str, source: bytes, root) -> FileFacts:
    """Run the extractor for ``language`` over an already parsed tree."""
    return EXTRACTORS[language](path, source, root).extract()
