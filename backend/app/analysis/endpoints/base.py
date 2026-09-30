"""Shared types and helpers for the endpoint detectors.

Every framework describes its routes with the same three ideas, so one small model
covers them all:

* a **route** has an HTTP method and a path *relative to its owner*;
* an **owner** is whatever collects routes and may carry a path prefix: a FastAPI
  ``APIRouter``, an Express ``Router``, a Go ``Group``, or a controller class in
  Spring, NestJS and ASP.NET;
* an owner is **mounted** somewhere: it can have a parent in the same file (a Go
  group made from another group) or be included from another file
  (``app.include_router(users.router, prefix="/api")``).

Detectors only report what they see in one file. ``resolve.py`` then follows the
mounts across files to compute each endpoint's full path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}
_QUOTED = re.compile(r"""^[rbfuRBFU@$]*("{3}|'{3}|"|'|`)(.*)\1$""", re.DOTALL)
_STRING_NODES = {
    "string", "string_literal", "interpreted_string_literal", "raw_string_literal",
    "verbatim_string_literal", "template_string",
}  # fmt: skip


@dataclass
class Route:
    method: str  # GET, POST, ..., ANY (any method) or WS (websocket)
    path: str  # relative to the owner
    line: int
    handler: str | None  # a function name, `Class.method`, or "(inline)"
    framework: str
    owner: str | None = None  # the router variable or controller class that holds it
    dynamic: bool = False  # the path is computed, so the real one is unknown


@dataclass
class RouterDef:
    var: str  # variable name, or the class name for controllers
    framework: str
    prefix: str = ""
    root: bool = False  # the application itself: nothing above it to mount it
    parent_var: str | None = None  # a router in the same file this one hangs from
    dynamic: bool = False  # the prefix is computed
    line: int = 0


@dataclass
class Include:
    """``parent.include(target, prefix)``: mounts a router (possibly from another file)."""

    parent_var: str
    target: str  # source text of what is mounted: `router`, `users.router`, `require:./users`
    prefix: str
    framework: str
    line: int
    dynamic: bool = False


@dataclass
class EndpointFacts:
    routes: list[Route] = field(default_factory=list)
    routers: list[RouterDef] = field(default_factory=list)
    includes: list[Include] = field(default_factory=list)


class Source:
    """Text access for tree-sitter nodes of one file."""

    def __init__(self, data: bytes):
        self.data = data

    def text(self, node) -> str:
        return self.data[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def field_text(self, node, name: str) -> str | None:
        child = node.child_by_field_name(name)
        return self.text(child) if child is not None else None

    def string(self, node) -> str | None:
        """The value of a string literal, or None when the node is not a plain literal."""
        if node is None or node.type not in _STRING_NODES:
            return None
        if any(c.type in {"interpolation", "template_substitution"} for c in node.named_children):
            return None
        raw = self.text(node)
        match = _QUOTED.match(raw)
        return match.group(2) if match else raw


def walk(root):
    """Every node of a tree, in source order, without recursion."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


def join_paths(*parts: str) -> str:
    """Join URL path parts: ``("/api/", "users", "/{id}")`` -> ``/api/users/{id}``."""
    pieces = [p.strip("/") for p in parts if p and p.strip("/")]
    joined = "/" + "/".join(pieces)
    last = next((p for p in reversed(parts) if p), "")
    if last.endswith("/") and joined != "/":
        joined += "/"  # `/users` + `/` stays `/users/`
    return joined


def method_of(name: str) -> str | None:
    """`get` / `Get` / `GET` -> `GET`, for the names frameworks use for HTTP verbs."""
    upper = name.upper()
    if upper in HTTP_METHODS:
        return upper
    return "ANY" if upper == "ALL" or upper == "ANY" else None


def last_segment(dotted: str) -> str:
    return re.split(r"[.:]+", dotted.strip())[-1]
