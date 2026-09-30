"""Resolve names: imports to files, and call sites to symbols (with a confidence).

Dynamic languages make this a best-effort job, so the rules are ordered from the
most to the least trustworthy, and each result says how sure we are:

1. the name is defined in the **same file** (or the same class): ``high``
2. it is defined in a file the caller **imports**: ``high``
3. it is the **only** definition with that name in the whole repo: ``medium``
4. several definitions share the name: one ``low`` edge to each (up to a cap)
5. otherwise the target stays unresolved (``to_symbol_id`` is NULL)

A call on an object of unknown type (``repo.save()``) is one step less certain
than a plain call (``save()``), because the object could be anything.
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_CANDIDATES = 5  # ambiguous names with more definitions than this are left unresolved
SELF_RECEIVERS = {"self", "this", "cls", "super", "base", "Self"}
JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
CLASS_LIKE_CALLABLE = ("function", "method", "class")

# Method names that exist on built-in types (dict.get, list.append, str.join, Promise.then,
# ...). A call like `x.get(k)` on an object of unknown type is almost always one of those,
# so linking it to a user-defined `get` would flood the graph with false edges.
COMMON_METHODS = frozenset({
    "get", "set", "add", "append", "extend", "insert", "remove", "pop", "clear", "update",
    "keys", "values", "items", "copy", "join", "split", "strip", "lstrip", "rstrip", "replace",
    "startswith", "endswith", "lower", "upper", "encode", "decode", "format", "read", "write",
    "close", "flush", "seek", "count", "index", "find", "sort", "reverse", "then", "catch",
    "map", "filter", "reduce", "forEach", "push", "shift", "slice", "splice", "includes",
    "toString", "equals", "hashCode", "length", "size", "contains", "put", "next", "json",
    "text", "trim", "toLowerCase", "toUpperCase", "ToString", "Equals", "GetHashCode", "Add",
    "Remove", "Contains", "Count", "Clear", "First", "Where", "Select", "ToList",
})  # fmt: skip


@dataclass(frozen=True)
class Sym:
    """A symbol as the resolver needs it (a slim view of a `symbols` row)."""

    id: int
    file: str
    name: str
    qualified: str
    kind: str
    owner: str | None
    language: str


@dataclass(frozen=True)
class Target:
    """A file an import points to, and the local name it is bound to (if known)."""

    file: str
    bind: str | None = None


# ---- imports -> files -------------------------------------------------------


def _dotted_suffixes(path: str, drop_ext: str, *, package_file: str | None = None) -> list[str]:
    """`src/app/util.py` -> ['util', 'app.util', 'src.app.util']."""
    stem = path[: -len(drop_ext)] if path.endswith(drop_ext) else path
    parts = stem.split("/")
    if package_file and parts[-1] == package_file:
        parts = parts[:-1]
    return [".".join(parts[i:]) for i in range(len(parts))] if parts else []


class RepoIndex:
    """Lookup tables over the files of one repository."""

    def __init__(self, languages: dict[str, str], namespaces: dict[str, str] | None = None):
        self.languages = languages  # path -> language
        self.files = set(languages)
        self.namespaces = namespaces or {}
        self.py_modules: dict[str, list[str]] = defaultdict(list)
        self.java_classes: dict[str, list[str]] = defaultdict(list)
        self.java_packages: dict[str, list[str]] = defaultdict(list)
        self.go_dirs: dict[str, list[str]] = defaultdict(list)
        self.ns_files: dict[str, list[str]] = defaultdict(list)
        for path, language in languages.items():
            if language == "python":
                for key in _dotted_suffixes(path, ".py", package_file="__init__"):
                    self.py_modules[key].append(path)
            elif language == "java":
                for key in _dotted_suffixes(path, ".java"):
                    self.java_classes[key].append(path)
                directory = posixpath.dirname(path)
                for key in _dotted_suffixes(directory + "/x", "/x"):
                    self.java_packages[key].append(path)
            elif language == "go":
                self.go_dirs[posixpath.dirname(path)].append(path)
        for path, namespace in self.namespaces.items():
            if languages.get(path) == "csharp":
                self.ns_files[namespace].append(path)

    def resolve(
        self, from_path: str, language: str, module: str, names: list[str], alias: str | None
    ) -> list[Target]:
        if language == "python":
            return self._python(from_path, module, names, alias)
        if language in {"javascript", "typescript", "tsx"}:
            return self._javascript(from_path, module, alias)
        if language == "go":
            return self._go(module, alias)
        if language == "java":
            return self._java(module, names)
        if language == "csharp":
            return self._csharp(module)
        return []

    # Python ---------------------------------------------------------------

    def _py_path(self, stem: str) -> list[str]:
        options = [f"{stem}.py", f"{stem}/__init__.py"] if stem else ["__init__.py"]
        return [p for p in options if p in self.files]

    def _python(self, from_path, module, names, alias) -> list[Target]:
        last = alias or module.lstrip(".").split(".")[-1]
        targets: list[Target] = []
        if module.startswith("."):
            dots = len(module) - len(module.lstrip("."))
            rest = module.lstrip(".").replace(".", "/")
            base = posixpath.dirname(from_path)
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            stem = posixpath.join(base, rest) if rest else base
            stem = "" if stem == "." else stem
            targets += [Target(f, last if rest else None) for f in self._py_path(stem)]
            for name in names:
                sub = posixpath.join(stem, name) if stem else name
                targets += [Target(f, name) for f in self._py_path(sub)]
        else:
            found = self.py_modules.get(module, [])
            targets += [Target(f, last) for f in (found if len(found) <= 3 else [])]
            for name in names:
                sub = self.py_modules.get(f"{module}.{name}", [])
                targets += [Target(f, name) for f in (sub if len(sub) <= 3 else [])]
        return targets

    # JavaScript / TypeScript ------------------------------------------------

    def _javascript(self, from_path, module, alias) -> list[Target]:
        if module.startswith("."):
            bases = [posixpath.normpath(posixpath.join(posixpath.dirname(from_path), module))]
        elif module.startswith(("@/", "~/")):
            # The usual `@/*` alias points at the project's `src/*` (Vite) or root (Next.js).
            # We do not read tsconfig, so try every folder above the importing file.
            rest, bases = module[2:], []
            folder = posixpath.dirname(from_path)
            while True:
                bases += [posixpath.join(folder, "src", rest), posixpath.join(folder, rest)]
                if not folder:
                    break
                folder = posixpath.dirname(folder)
        else:
            return []  # a package from node_modules
        for base in bases:
            found = self._js_file(base, alias)
            if found:
                return found
        return []

    def _js_file(self, base: str, alias: str | None) -> list[Target]:
        if base.startswith(".."):
            return []
        stems = [base]
        for ext in (".js", ".jsx", ".mjs", ".cjs"):  # TypeScript imports may say `./x.js`
            if base.endswith(ext):
                stems.append(base[: -len(ext)])
        for stem in stems:
            options = [
                stem,
                *(stem + e for e in JS_EXTENSIONS),
                *(f"{stem}/index{e}" for e in JS_EXTENSIONS),
            ]
            for option in options:
                if option in self.files:
                    return [Target(option, alias)]
        return []

    # Go -------------------------------------------------------------------

    def _go(self, module, alias) -> list[Target]:
        parts = module.split("/")
        bind = alias or parts[-1]
        for k in range(min(len(parts), 4), 0, -1):
            if k == 1 and len(parts) > 1:
                break  # a bare last segment is too weak a match for a long import path
            suffix = "/".join(parts[-k:])
            dirs = [d for d in self.go_dirs if d == suffix or d.endswith("/" + suffix)]
            if dirs:
                files = [f for d in dirs for f in self.go_dirs[d] if not f.endswith("_test.go")]
                return [Target(f, bind) for f in files]
        return []

    # Java -----------------------------------------------------------------

    def _java(self, module, names) -> list[Target]:
        if names == ["*"]:
            return [Target(f) for f in self.java_packages.get(module, [])]
        segments = module.split(".")
        for drop in range(0, 3):  # `import static a.b.C.method` names a member of class C
            key = ".".join(segments[: len(segments) - drop])
            found = self.java_classes.get(key, [])
            if found and len(found) <= 3:
                return [Target(f, segments[len(segments) - drop - 1]) for f in found]
        return []

    # C# -------------------------------------------------------------------

    def _csharp(self, module) -> list[Target]:
        segments = module.split(".")
        for drop in range(0, 2):  # `using static Acme.Util` names a class inside a namespace
            key = ".".join(segments[: len(segments) - drop])
            if key in self.ns_files:
                return [Target(f) for f in self.ns_files[key]]
        return []


# ---- call sites -> symbols ------------------------------------------------------


class CallResolver:
    def __init__(
        self,
        symbols: list[Sym],
        import_targets: dict[str, set[str]],
        bindings: dict[str, dict[str, set[str]]],
    ):
        self.by_id = {s.id: s for s in symbols}
        self.by_name: dict[str, list[Sym]] = defaultdict(list)
        self.by_file_name: dict[tuple[str, str], list[Sym]] = defaultdict(list)
        self.classes_by_file: dict[str, list[Sym]] = defaultdict(list)
        for s in symbols:
            self.by_name[s.name].append(s)
            self.by_file_name[(s.file, s.name)].append(s)
            if s.kind == "class":
                self.classes_by_file[s.file].append(s)
        self.import_targets = import_targets  # file -> files it imports
        self.bindings = bindings  # file -> local name -> files (module aliases)

    def owner_of(self, sym: Sym | None) -> str | None:
        """The class a symbol belongs to, also for functions nested inside methods."""
        if sym is None:
            return None
        if sym.owner:
            return sym.owner
        prefix = sym.qualified + "."
        best = None
        for cls in self.classes_by_file.get(sym.file, []):
            if prefix.startswith(cls.qualified + ".") and (
                best is None or len(cls.qualified) > len(best)
            ):
                best = cls.qualified
        return best

    @staticmethod
    def _pick(cands: list[Sym], unique: str, many: str) -> list[tuple[Sym | None, str]] | None:
        if len(cands) == 1:
            return [(cands[0], unique)]
        if 1 < len(cands) <= MAX_CANDIDATES:
            return [(c, many) for c in cands]
        return None

    def resolve(
        self, name: str, receiver: str | None, kind: str, from_sym: Sym | None, file: str
    ) -> list[tuple[Sym | None, str]]:
        """Return ``[(symbol or None, confidence), ...]`` for one call site."""
        kinds = ("class",) if kind == "inherit" else CLASS_LIKE_CALLABLE
        everywhere = [s for s in self.by_name.get(name, ()) if s.kind in kinds]
        same_file = [s for s in self.by_file_name.get((file, name), ()) if s.kind in kinds]
        imported_files = self.import_targets.get(file, set())
        imported = [s for s in everywhere if s.file in imported_files]
        language = from_sym.language if from_sym else (everywhere[0].language if everywhere else "")
        owner = self.owner_of(from_sym)
        unresolved: list[tuple[Sym | None, str]] = [(None, "low")]

        if receiver in SELF_RECEIVERS:
            own = [s for s in everywhere if owner and s.owner == owner]
            if receiver in {"super", "base"}:  # a parent class's method
                parents = [s for s in everywhere if s.owner != owner]
                return self._pick(parents, "medium", "low") or unresolved
            return (
                self._pick([s for s in own if s.file == file] or own, "high", "medium")
                or self._pick([s for s in same_file if s.kind == "method"], "medium", "low")
                or self._pick(everywhere, "medium", "low")
                or unresolved
            )

        if receiver:
            bound = self.bindings.get(file, {}).get(receiver.split(".")[0], set())
            in_module = [s for s in everywhere if s.file in bound]  # `auth.login()`
            if in_module:
                return self._pick(in_module, "high", "medium") or unresolved
            recv_class = receiver.split(".")[-1]
            owned = [s for s in everywhere if s.owner and s.owner.split(".")[-1] == recv_class]
            if owned:  # `UserRepository.find()`
                return self._pick(owned, "medium", "low") or unresolved
            methods = [s for s in everywhere if s.kind != "class"]  # `obj.save()`: type unknown
            if name in COMMON_METHODS:
                methods = []  # probably a built-in's method, not ours
            return (
                self._pick([s for s in same_file if s.kind != "class"], "medium", "low")
                or self._pick([s for s in imported if s.kind != "class"], "medium", "low")
                or self._pick(methods, "low", "low")
                or unresolved
            )

        # A plain call. In Python and JavaScript it cannot be a method; in Java, C# and Go
        # it can be (an implicit `this`), so methods stay candidates there.
        implicit_methods = language in {"java", "csharp", "go"}

        def keep(cands: list[Sym]) -> list[Sym]:
            return [s for s in cands if s.kind != "method" or implicit_methods]

        same_owner = [s for s in same_file if owner and s.owner == owner]
        return (
            self._pick(same_owner, "high", "medium")
            or self._pick(keep(same_file), "high", "medium")
            or self._pick(keep(imported), "high", "medium")
            or self._pick(keep(everywhere), "medium", "low")
            or unresolved
        )


def dir_of(path: str) -> str:
    """Folder of a file ('' for the repo root)."""
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent
