"""Environment variable finder: which variables does the code read, and how?

For every read of an environment variable (``os.environ["X"]``, ``os.getenv``,
``process.env.X``, ``os.Getenv``, ``System.getenv``, ...) and every Pydantic
``BaseSettings`` field we record the name, where it is used, its literal default
and whether the program *requires* it (no default, so it fails or gets an empty
value when the variable is missing).

**Privacy rules** (they are tested):

* real ``.env`` files are never parsed for values. Only the *names* declared in
  ``.env``, ``.env.example`` and friends are read, and the value part of each line
  is discarded immediately;
* names that look secret (``TOKEN``, ``PASSWORD``, ``KEY``, ...) are flagged, and their
  default values are neither stored nor ever written to a generated file.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.analysis.pipeline import FileContext

SECRET_TOKENS = {
    "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PWD", "CREDENTIAL", "CREDENTIALS",
    "PRIVATE", "APIKEY", "DSN", "SALT", "PASSPHRASE",
}  # fmt: skip
# Words that say a name describes *how* a secret is used (its lifetime, size, ...) rather than
# being one: `TOKEN_TTL=3600` is a setting, not a token. Over-masking is safe and under-masking
# is not, so this list is short and only removes the flag when no stronger evidence exists.
BENIGN_TOKENS = {
    "TTL", "EXPIRES", "EXPIRY", "EXPIRATION", "TIMEOUT", "DURATION", "LIFETIME", "SECONDS",
    "MINUTES", "HOURS", "DAYS", "LENGTH", "SIZE", "COUNT", "ENABLED", "ENABLE", "ALGORITHM",
    "HEADER", "PREFIX", "MAX", "MIN", "LIMIT", "ROUNDS", "ITERATIONS",
}  # fmt: skip
CREDENTIAL_URL = re.compile(r"://[^/\s:@]+:[^/\s@]+@")  # scheme://user:password@host
DOTENV_NAMES = (".env", ".env.example", ".env.sample", ".env.template", ".env.dist", ".env.local")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_DEFAULT = 200

_STRING_TYPES = {
    "string", "string_literal", "interpreted_string_literal", "raw_string_literal",
    "verbatim_string_literal", "template_string", "string_fragment",
}  # fmt: skip
_NUMBER_TYPES = {
    "integer", "float", "number", "integer_literal", "real_literal", "int_literal",
    "decimal_integer_literal", "decimal_floating_point_literal", "float_literal",
}  # fmt: skip
_BOOL_TYPES = {"true", "false", "boolean", "boolean_literal"}
_NULL_TYPES = {"none", "null", "nil", "null_literal", "undefined"}
_QUOTED = re.compile(r"""^[rbfuRBFU@$]*("{3}|'{3}|"|'|`)(.*)\1$""", re.DOTALL)


def is_secret_name(name: str) -> bool:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)  # camelCase -> camel_Case
    tokens = {t.upper() for t in re.split(r"[^A-Za-z0-9]+", spaced) if t}
    looks_secret = bool(tokens & SECRET_TOKENS) or "APIKEY" in name.upper().replace("_", "")
    return looks_secret and not tokens & BENIGN_TOKENS


def is_secret(name: str, default: str | None) -> bool:
    return is_secret_name(name) or bool(default and CREDENTIAL_URL.search(default))


def is_dotenv_path(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name.startswith(".env") or name.endswith(".env")


@dataclass
class EnvHit:
    name: str
    line: int
    default: str | None  # literal default (None when there is none, or it is not a literal)
    has_default: bool
    source: str = "code"  # code | pydantic


class EnvScanner:
    """Finds environment reads in a parsed file. One instance per file."""

    def __init__(self, language: str, source: bytes, root):
        self.language = language
        self.src = source
        self.root = root
        self.hits: list[EnvHit] = []

    # ---- helpers ------------------------------------------------------------

    def text(self, node) -> str:
        return self.src[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def literal(self, node) -> tuple[bool, str | None]:
        """(is_literal, value). A null literal is (True, None): an explicit "no default"."""
        if node is None:
            return False, None
        kind = node.type
        if kind in _NULL_TYPES:
            return True, None
        if kind in _NUMBER_TYPES or kind in _BOOL_TYPES:
            return True, self.text(node)
        if kind in {"unary_expression", "unary_operator"} and node.named_children:
            inner = node.named_children[-1]
            if inner.type in _NUMBER_TYPES:
                return True, self.text(node)
        if kind in _STRING_TYPES:
            if any(
                c.type in {"interpolation", "template_substitution"} for c in node.named_children
            ):
                return False, None  # f"{x}" and `${x}` are computed, not literal
            raw = self.text(node)
            match = _QUOTED.match(raw)
            return True, (match.group(2) if match else raw)[:MAX_DEFAULT]
        return False, None

    def string_arg(self, node) -> str | None:
        ok, value = self.literal(node)
        return value if ok and value and NAME_RE.match(value) else None

    def add(self, name: str | None, node, default_node=None, *, has_default=None, source="code"):
        if not name or not NAME_RE.match(name):
            return
        default = None
        if has_default is None:
            has_default = False
            if default_node is not None:
                is_lit, value = self.literal(default_node)
                if is_lit:
                    default, has_default = value, value is not None  # `None` means "no default"
                else:
                    has_default = True  # a computed default: there *is* one, we just cannot show it
        self.hits.append(EnvHit(name, node.start_point.row + 1, default, has_default, source))

    def fallback_of(self, node, operators: set[str]):
        """The right-hand side of `env_read || "default"` / `?? "default"`, if that is the shape."""
        parent = node.parent
        if parent is None or parent.type not in {"binary_expression", "boolean_operator"}:
            return None
        op = parent.child_by_field_name("operator")
        if op is None or self.text(op) not in operators:
            return None
        if parent.child_by_field_name("left") != node:
            return None
        return parent.child_by_field_name("right")

    # ---- the walk ---------------------------------------------------------------

    def scan(self) -> list[EnvHit]:
        handler = {
            "python": self.python,
            "javascript": self.javascript,
            "typescript": self.javascript,
            "tsx": self.javascript,
            "go": self.go,
            "java": self.java,
            "csharp": self.csharp,
        }[self.language]
        stack = [self.root]
        while stack:
            node = stack.pop()
            handler(node)
            stack.extend(reversed(node.named_children))
        return self.hits

    # ---- Python -------------------------------------------------------------

    def python(self, node) -> None:
        kind = node.type
        if kind == "subscript":
            value = node.child_by_field_name("value")
            if value is not None and self.text(value) in {"os.environ", "environ"}:
                parent = node.parent
                is_write = (
                    parent is not None
                    and parent.type in {"assignment", "augmented_assignment"}
                    and parent.child_by_field_name("left") == node
                )
                if not is_write:
                    self.add(self.string_arg(node.child_by_field_name("subscript")), node)
        elif kind == "call":
            func = node.child_by_field_name("function")
            if func is None or self.text(func) not in {
                "os.environ.get", "environ.get", "os.getenv", "getenv",
            }:  # fmt: skip
                return
            args = node.child_by_field_name("arguments")
            positional = [a for a in args.named_children if a.type != "keyword_argument"]
            if not positional:
                return
            default = positional[1] if len(positional) > 1 else None
            for arg in args.named_children:
                if (
                    arg.type == "keyword_argument"
                    and self.text(arg.child_by_field_name("name")) == "default"
                ):
                    default = arg.child_by_field_name("value")
            if default is None:
                default = self.fallback_of(node, {"or"})
            self.add(self.string_arg(positional[0]), node, default)
        elif kind == "class_definition":
            bases = node.child_by_field_name("superclasses")
            if bases is not None and any(
                self.text(b).endswith("BaseSettings") for b in bases.named_children
            ):
                self.pydantic_settings(node)

    def pydantic_settings(self, cls) -> None:
        body = cls.child_by_field_name("body")
        if body is None:
            return
        statements = []
        for stmt in body.named_children:
            if stmt.type == "expression_statement" and stmt.named_children:
                stmt = stmt.named_children[0]
            statements.append(stmt)

        prefix = ""
        for stmt in statements:
            # model_config = SettingsConfigDict(env_prefix="APP_")
            if (
                stmt.type == "assignment"
                and self.text(stmt.child_by_field_name("left")) == "model_config"
            ):
                prefix = (
                    self.keyword_string(stmt.child_by_field_name("right"), "env_prefix") or prefix
                )
            # class Config: env_prefix = "APP_"  (pydantic v1 style)
            if (
                stmt.type == "class_definition"
                and self.text(stmt.child_by_field_name("name")) == "Config"
            ):
                inner = stmt.child_by_field_name("body")
                for item in inner.named_children if inner is not None else []:
                    item = item.named_children[0] if item.type == "expression_statement" else item
                    if (
                        item.type == "assignment"
                        and self.text(item.child_by_field_name("left")) == "env_prefix"
                    ):
                        ok, value = self.literal(item.child_by_field_name("right"))
                        prefix = value if ok and value else prefix

        for stmt in statements:
            if stmt.type != "assignment" or stmt.child_by_field_name("type") is None:
                continue  # only annotated attributes are settings fields
            left = stmt.child_by_field_name("left")
            name = self.text(left)
            annotation = self.text(stmt.child_by_field_name("type"))
            if name.startswith("_") or name == "model_config" or annotation.startswith("ClassVar"):
                continue
            right = stmt.child_by_field_name("right")
            env_name = prefix + name.upper()
            default_node, computed = right, False  # computed: a default exists but is not a literal
            if right is not None and right.type == "call":
                func = right.child_by_field_name("function")
                if func is not None and self.text(func).endswith("Field"):
                    alias = self.keyword_string(right, "validation_alias")
                    alias = alias or self.keyword_string(right, "alias")
                    env_name = alias or env_name
                    default_node, computed = self.field_default(right)
            if default_node is None:
                self.add(env_name, left, None, has_default=computed, source="pydantic")
            else:
                self.add(env_name, left, default_node, source="pydantic")

    def keyword_string(self, call, keyword: str) -> str | None:
        if call is None or call.type != "call":
            return None
        args = call.child_by_field_name("arguments")
        for arg in args.named_children if args is not None else []:
            if (
                arg.type == "keyword_argument"
                and self.text(arg.child_by_field_name("name")) == keyword
            ):
                ok, value = self.literal(arg.child_by_field_name("value"))
                return value if ok else None
        return None

    def field_default(self, call):
        """(default node, has_default) for `Field(...)`, `Field("x")`, `Field(default="x")`."""
        args = call.child_by_field_name("arguments")
        for arg in args.named_children if args is not None else []:
            if arg.type == "keyword_argument":
                key = self.text(arg.child_by_field_name("name"))
                if key == "default":
                    return arg.child_by_field_name("value"), False
                if key == "default_factory":
                    return None, True  # computed default
            elif arg.type == "ellipsis":
                return None, False  # Field(...) means required
            else:
                return arg, False  # the first positional argument is the default
        return None, False

    # ---- JavaScript / TypeScript ------------------------------------------------

    JS_ENV = {"process.env", "import.meta.env"}

    def javascript(self, node) -> None:
        kind = node.type
        if kind == "member_expression":
            obj = node.child_by_field_name("object")
            prop = node.child_by_field_name("property")
            if (
                obj is not None
                and prop is not None
                and prop.type == "property_identifier"
                and self.text(obj) in self.JS_ENV
                and not self.is_assignment_target(node)
            ):
                self.add(self.text(prop), node, self.fallback_of(node, {"||", "??"}))
        elif kind == "subscript_expression":
            obj = node.child_by_field_name("object")
            if (
                obj is not None
                and self.text(obj) in self.JS_ENV
                and not self.is_assignment_target(node)
            ):
                self.add(
                    self.string_arg(node.child_by_field_name("index")),
                    node,
                    self.fallback_of(node, {"||", "??"}),
                )
        elif kind == "variable_declarator":  # const { A, B = "x" } = process.env
            value = node.child_by_field_name("value")
            pattern = node.child_by_field_name("name")
            if (
                value is not None
                and pattern is not None
                and pattern.type == "object_pattern"
                and self.text(value) in self.JS_ENV
            ):
                for item in pattern.named_children:
                    if item.type == "shorthand_property_identifier_pattern":
                        self.add(self.text(item), item)
                    elif item.type == "object_assignment_pattern":
                        self.add(
                            self.text(item.child_by_field_name("left")),
                            item,
                            item.child_by_field_name("right"),
                        )
                    elif item.type == "pair_pattern":
                        self.add(self.text(item.child_by_field_name("key")), item)

    @staticmethod
    def is_assignment_target(node) -> bool:
        parent = node.parent
        return (
            parent is not None
            and parent.type in {"assignment_expression", "augmented_assignment_expression"}
            and parent.child_by_field_name("left") == node
        )

    # ---- Go -------------------------------------------------------------------

    def go(self, node) -> None:
        if node.type != "call_expression":
            return
        func = node.child_by_field_name("function")
        callee = self.text(func) if func is not None else ""
        if callee in {"os.Getenv", "syscall.Getenv"} | {"os.LookupEnv"}:
            args = node.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            # Getenv returns "" when unset (so the value is required); LookupEnv reports it.
            self.add(self.string_arg(first), node, has_default=callee == "os.LookupEnv")

    # ---- Java -----------------------------------------------------------------

    def java(self, node) -> None:
        if node.type != "method_invocation":
            return
        obj = node.child_by_field_name("object")
        name = node.child_by_field_name("name")
        args = node.child_by_field_name("arguments")
        if obj is None or name is None or args is None:
            return
        method, receiver, params = self.text(name), self.text(obj), args.named_children
        if receiver == "System" and method == "getenv" and params:
            self.add(self.string_arg(params[0]), node)
        elif (
            method == "getOrDefault"
            and len(params) == 2
            and receiver.replace(" ", "") == "System.getenv()"
        ):
            self.add(self.string_arg(params[0]), node, params[1])

    # ---- C# -------------------------------------------------------------------

    def csharp(self, node) -> None:
        if node.type != "invocation_expression":
            return
        func = node.child_by_field_name("function")
        if func is None or self.text(func) not in {
            "Environment.GetEnvironmentVariable",
            "System.Environment.GetEnvironmentVariable",
        }:
            return
        args = node.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        if first is not None and first.type == "argument" and first.named_children:
            first = first.named_children[0]
        fallback = self.fallback_of(node, {"??"})
        if fallback is not None and fallback.type == "throw_expression":
            fallback = None  # `?? throw`: the variable is required
        self.add(self.string_arg(first), node, fallback)


def scan_env(language: str, source: bytes, root) -> list[EnvHit]:
    return EnvScanner(language, source, root).scan()


ENV_LANGUAGES = frozenset({"python", "javascript", "typescript", "tsx", "go", "java", "csharp"})


class EnvAnalyzer:
    name = "env"
    version = 2  # 2: settings about a secret (TOKEN_TTL) are no longer flagged as secrets
    languages = ENV_LANGUAGES

    def analyze(self, ctx: FileContext, conn, repo_id: str) -> dict | None:
        if is_dotenv_path(ctx.path):
            return None  # defensive: real env files are never parsed
        root = ctx.root()
        if root is None:
            return None
        hits = scan_env(ctx.language, ctx.source, root)
        if not hits:
            return None
        symbols = conn.execute(
            "SELECT id, start_line, end_line FROM symbols WHERE file_path = ?", (ctx.path,)
        ).fetchall()

        def enclosing(line: int) -> int | None:
            best = None
            for s in symbols:
                if s["start_line"] <= line <= s["end_line"] and (
                    best is None
                    or s["end_line"] - s["start_line"] < best["end_line"] - best["start_line"]
                ):
                    best = s
            return best["id"] if best else None

        rows = []
        for h in hits:
            secret = is_secret(h.name, h.default)
            rows.append(
                (repo_id, h.name, ctx.path, h.line, ctx.language,
                 None if secret else h.default,  # a secret's default is never stored
                 int(h.has_default), int(not h.has_default), int(secret), h.source,
                 enclosing(h.line), ctx.hash)
            )  # fmt: skip
        conn.executemany(
            "INSERT INTO env_vars (repo_id, name, file_path, line, language, default_value,"
            " has_default, required, is_secret, source, symbol_id, file_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        return None

    def delete(self, conn, path: str) -> None:
        conn.execute("DELETE FROM env_vars WHERE file_path = ?", (path,))


# ---- reading names from .env files (never values) -------------------------------------


def read_dotenv_keys(root: Path, max_depth: int = 2) -> dict[str, list[str]]:
    """Map each variable name to the env files that declare it. Values are never kept."""
    declared: dict[str, list[str]] = defaultdict(list)
    skip = {"node_modules", ".git", "venv", ".venv", "dist", "build", "__pycache__"}
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if depth < max_depth and entry.name not in skip:
                    stack.append((entry, depth + 1))
            elif entry.name in DOTENV_NAMES or entry.name.startswith(".env."):
                try:
                    with entry.open("r", encoding="utf-8", errors="ignore") as fh:
                        for line in fh:
                            key = _key_of(line)
                            if key:
                                rel = entry.relative_to(root).as_posix()
                                if rel not in declared[key]:
                                    declared[key].append(rel)
                except OSError:
                    continue
    return dict(declared)


def _key_of(line: str) -> str | None:
    """The variable name on a `KEY=value` line. The value is discarded right here."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    return key if NAME_RE.match(key) else None


# ---- reading results ------------------------------------------------------------------


def list_env(conn, declared: dict[str, list[str]] | None = None, q: str = "") -> list[dict]:
    """Variables grouped by name, each with every place it is used."""
    declared = declared or {}
    groups: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT e.*, s.qualified_name AS symbol FROM env_vars e"
        " LEFT JOIN symbols s ON s.id = e.symbol_id ORDER BY e.name, e.file_path, e.line"
    ).fetchall()
    for r in rows:
        g = groups.setdefault(
            r["name"],
            {
                "name": r["name"],
                "is_secret": False,
                "required": False,
                "defaults": [],
                "usages": [],
            },
        )
        g["is_secret"] = g["is_secret"] or bool(r["is_secret"])
        g["required"] = g["required"] or bool(r["required"])
        if r["default_value"] is not None and r["default_value"] not in g["defaults"]:
            g["defaults"].append(r["default_value"])
        g["usages"].append(
            {
                "file_path": r["file_path"],
                "line": r["line"],
                "language": r["language"],
                "required": bool(r["required"]),
                "has_default": bool(r["has_default"]),
                "default": r["default_value"],
                "source": r["source"],
                "symbol": r["symbol"],
            }
        )
    result = []
    for g in groups.values():
        g["usage_count"] = len(g["usages"])
        g["declared_in"] = declared.get(g["name"], [])
        g["default"] = g["defaults"][0] if len(g["defaults"]) == 1 else None
        g["default_varies"] = len(g["defaults"]) > 1
        if not q or q.lower() in g["name"].lower():
            result.append(g)
    return result


def _quote(value: str) -> str:
    if re.search(r"[\s#'\"$\\]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def module_of(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "(root)"


def generate_example(variables: list[dict]) -> str:
    """A ready-to-edit `.env.example`: grouped by top-level module, with usage comments.

    Secrets are always written with an empty value. Other variables get their literal
    default when they have exactly one.
    """
    by_module: dict[str, list[dict]] = defaultdict(list)
    for v in variables:
        counts: dict[str, int] = defaultdict(int)
        for u in v["usages"]:
            counts[module_of(u["file_path"])] += 1
        home = sorted(counts, key=lambda m: (-counts[m], m))[0]
        by_module[home].append(v)

    lines = ["# Generated by Lodestar from the environment variables read in the code.", ""]
    for module in sorted(by_module):
        lines += [f"# ---- {module} ----", ""]
        for v in sorted(by_module[module], key=lambda x: x["name"]):
            places = [f"{u['file_path']}:{u['line']}" for u in v["usages"][:3]]
            more = len(v["usages"]) - len(places)
            used = ", ".join(places) + (f" (+{more} more)" if more > 0 else "")
            status = "required" if v["required"] else "optional"
            lines.append(f"# {status}. Used in {used}")
            if v["is_secret"]:
                lines.append("# Secret: set a real value; never commit it.")
                lines.append(f"{v['name']}=")
            elif v["default"] is not None:
                lines.append(f"{v['name']}={_quote(v['default'])}")
            else:
                lines.append(f"{v['name']}=")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
