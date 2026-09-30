"""Go: net/http, gin, chi and echo.

Recognised::

    http.HandleFunc("/health", h);  mux.HandleFunc("GET /items/{id}", h)     // net/http
    r := gin.Default();  api := r.Group("/api");  api.GET("/users", list)     // gin
    e := echo.New();  g := e.Group("/v1");  g.POST("/e", create)              // echo
    r := chi.NewRouter();  r.Get("/x", h);  r.Route("/v1", func(r chi.Router) { r.Get("/y", h) })

chi's ``r.Mount("/admin", adminRouter())`` is not followed: routes registered inside
``adminRouter`` are reported without the mount prefix.
"""

from __future__ import annotations

from app.analysis.endpoints.base import (
    HTTP_METHODS,
    EndpointFacts,
    Route,
    RouterDef,
    Source,
    join_paths,
    method_of,
    walk,
)

LANGUAGES = frozenset({"go"})
IMPORTS = {
    "github.com/gin-gonic/gin": "gin",
    "github.com/go-chi/chi": "chi",
    "github.com/labstack/echo": "echo",
    "net/http": "nethttp",
}
ROOTS = {
    "gin.Default": "gin", "gin.New": "gin", "echo.New": "echo", "chi.NewRouter": "chi",
    "chi.NewMux": "chi", "http.NewServeMux": "nethttp",
}  # fmt: skip
UPPER_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "Any"}
TITLE_VERBS = {"Get", "Post", "Put", "Delete", "Patch", "Options", "Head"}


def _frameworks(src: Source, root) -> set[str]:
    found = set()
    for node in walk(root):
        if node.type == "import_spec":
            path = src.string(node.child_by_field_name("path")) or ""
            for prefix, name in IMPORTS.items():
                if path == prefix or path.startswith(prefix + "/"):
                    found.add(name)
    return found


def _selector(src: Source, call):
    """(operand text, field name) of `a.b(...)`."""
    func = call.child_by_field_name("function")
    if func is None or func.type != "selector_expression":
        return None, None
    operand, field = func.child_by_field_name("operand"), func.child_by_field_name("field")
    return (src.text(operand) if operand is not None else None), (
        src.text(field) if field else None
    )


def _args(call):
    args = call.child_by_field_name("arguments")
    return list(args.named_children) if args is not None else []


def _handler(src: Source, node) -> str | None:
    if node is None:
        return None
    if node.type == "func_literal":
        return "(inline)"
    if node.type in {"identifier", "selector_expression"}:
        return src.text(node)
    return "(inline)"


def _split_pattern(pattern: str) -> tuple[str, str]:
    """Go 1.22 patterns: `GET /items/{id}` -> (`GET`, `/items/{id}`)."""
    head, _, rest = pattern.partition(" ")
    if rest and head.upper() in HTTP_METHODS:
        return head.upper(), rest.strip()
    return "ANY", pattern


def detect(root, src: Source) -> EndpointFacts:
    facts = EndpointFacts()
    frameworks = _frameworks(src, root)
    if not frameworks:
        return facts

    # Routers and groups (in source order, so a group can name an earlier one as its parent).
    for node in walk(root):
        if node.type not in {"short_var_declaration", "assignment_statement", "var_spec"}:
            continue
        left = node.child_by_field_name("left") or node.child_by_field_name("name")
        right = node.child_by_field_name("right") or node.child_by_field_name("value")
        left_var = (
            left.named_children[0] if left is not None and left.type == "expression_list" else left
        )
        call = (
            right.named_children[0]
            if right is not None and right.type == "expression_list"
            else right
        )
        if left_var is None or call is None or call.type != "call_expression":
            continue
        var, line = src.text(left_var), node.start_point.row + 1
        operand, field = _selector(src, call)
        full = f"{operand}.{field}" if operand and field else ""
        if full in ROOTS:
            facts.routers.append(RouterDef(var, ROOTS[full], root=True, line=line))
        elif field == "Group" and operand:
            args = _args(call)
            path = src.string(args[0]) if args else ""
            parent = next((r for r in facts.routers if r.var == operand), None)
            framework = parent.framework if parent else "gin"
            facts.routers.append(
                RouterDef(
                    var, framework, path or "", parent_var=operand, dynamic=path is None, line=line
                )
            )
    owners = {r.var: r for r in facts.routers}

    # Routes. `Route("/v1", func(r chi.Router) {...})` adds to the prefix inside its closure,
    # so the walk carries the accumulated prefix with each node.
    stack = [(root, "")]
    while stack:
        node, prefix = stack.pop()
        if node.type == "call_expression" and _call(
            src, facts, node, prefix, owners, frameworks, stack
        ):
            continue  # a chi closure: its body was scheduled with the longer prefix
        for child in reversed(node.named_children):
            stack.append((child, prefix))
    return facts


def _call(src, facts, node, prefix, owners, frameworks, stack) -> bool:
    """Handle one call. Returns True when its children were already scheduled (chi closures)."""
    operand, field = _selector(src, node)
    if field is None or operand is None:
        return False
    args = _args(node)
    line = node.start_point.row + 1
    owner_def = owners.get(operand)
    framework = owner_def.framework if owner_def else next(iter(sorted(frameworks)))
    if operand == "http":
        framework = "nethttp"
    owner = (
        operand if owner_def and not prefix else None
    )  # inside a chi closure the path is absolute

    def add(method: str, path: str | None, handler_node, *, dynamic=False) -> None:
        if path is None:
            path, dynamic = "", True
        joined = join_paths(prefix, path) if prefix else path
        facts.routes.append(
            Route(
                method, joined, line, _handler(src, handler_node), framework, owner, dynamic=dynamic
            )
        )

    if (
        field == "Route"
        and "chi" in frameworks
        and len(args) == 2
        and args[1].type == "func_literal"
    ):
        path = src.string(args[0]) or ""
        body = args[1].child_by_field_name("body")
        if body is not None:
            stack.append((body, join_paths(prefix, path)))
        return True

    if field in UPPER_VERBS and args and ({"gin", "echo"} & frameworks):
        path = src.string(args[0])
        if path is not None and (path.startswith("/") or path in {"", "*"}):
            echo_style = framework == "echo" or ("echo" in frameworks and "gin" not in frameworks)
            handler = (
                args[1] if echo_style and len(args) > 1 else args[-1] if len(args) > 1 else None
            )
            add(method_of(field) or "ANY", path, handler)
        return False

    if field in TITLE_VERBS and len(args) >= 2 and "chi" in frameworks:
        path = src.string(args[0])
        if path is not None:
            add(method_of(field) or "ANY", path, args[-1])
        return False

    if field in {"Method", "MethodFunc"} and len(args) >= 3 and "chi" in frameworks:
        add((src.string(args[0]) or "ANY").upper(), src.string(args[1]), args[-1])
        return False

    if field in {"Handle", "HandleFunc"} and len(args) >= 2 and ({"nethttp", "chi"} & frameworks):
        pattern = src.string(args[0])
        if pattern is not None:
            method, path = _split_pattern(pattern)
            if path.startswith("/") or path == "":
                add(method, path, args[1])
    return False
