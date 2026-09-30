"""FastAPI and Flask: decorator routes, routers/blueprints, and their mounting.

Recognised::

    app = FastAPI();  router = APIRouter(prefix="/users")
    @router.get("/{id}")                       # also post/put/delete/patch/options/head/websocket
    @app.api_route("/x", methods=["GET", "POST"])
    app.include_router(users.router, prefix="/api")
    router.add_api_route("/y", handler, methods=["GET"])

    app = Flask(__name__);  bp = Blueprint("b", __name__, url_prefix="/b")
    @bp.route("/z", methods=["GET", "POST"])   # and @bp.get(...) from Flask 2
    app.register_blueprint(bp, url_prefix="/api")
    app.add_url_rule("/w", view_func=w)
"""

from __future__ import annotations

from app.analysis.endpoints.base import (
    EndpointFacts,
    Include,
    Route,
    RouterDef,
    Source,
    last_segment,
    method_of,
    walk,
)

LANGUAGES = frozenset({"python"})
APP_CLASSES = {"FastAPI": "fastapi", "Flask": "flask", "Quart": "flask"}
ROUTER_CLASSES = {"APIRouter": "fastapi", "Blueprint": "flask"}
VERBS = {"get", "post", "put", "delete", "patch", "options", "head"}
INCLUDES = {"include_router": "prefix", "register_blueprint": "url_prefix"}


def _call_parts(src: Source, call):
    """(receiver text, called name, arguments node) for `a.b(...)` and `b(...)`."""
    func = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    if func is None:
        return None, None, args
    if func.type == "attribute":
        obj = func.child_by_field_name("object")
        attr = func.child_by_field_name("attribute")
        return (
            (src.text(obj) if obj is not None else None),
            (src.text(attr) if attr else None),
            args,
        )
    return None, src.text(func), args


def _arguments(args):
    positional, keywords = [], {}
    for arg in args.named_children if args is not None else []:
        if arg.type == "keyword_argument":
            keywords[arg.child_by_field_name("name").text.decode()] = arg.child_by_field_name(
                "value"
            )
        else:
            positional.append(arg)
    return positional, keywords


def _module_names(src: Source, nodes) -> set[str]:
    modules: set[str] = set()
    for node in nodes:
        if node.type == "import_from_statement":
            modules.add(src.field_text(node, "module_name") or "")
        elif node.type == "import_statement":
            modules.update(
                src.text(n).split(" as ")[0] for n in node.children_by_field_name("name")
            )
    return {m.split(".")[0] for m in modules if m}


def _methods(src: Source, keywords, default: list[str]) -> list[str]:
    node = keywords.get("methods")
    if node is None or node.type not in {"list", "tuple", "set"}:
        return default
    found = [(src.string(item) or "").upper() for item in node.named_children]
    return [m for m in found if m] or default


def detect(root, src: Source) -> EndpointFacts:
    facts = EndpointFacts()
    nodes = list(walk(root))
    modules = _module_names(src, nodes)
    hint = "flask" if "flask" in modules or "quart" in modules else (
        "fastapi" if {"fastapi", "starlette"} & modules else None
    )  # fmt: skip

    # Pass 1: routers, apps and the places routers are mounted.
    for node in nodes:
        if node.type == "assignment":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is None or left.type != "identifier" or right is None or right.type != "call":
                continue
            _, ctor, args = _call_parts(src, right)
            ctor = last_segment(ctor or "")
            var = src.text(left)
            if ctor in APP_CLASSES:
                facts.routers.append(
                    RouterDef(var, APP_CLASSES[ctor], root=True, line=node.start_point.row + 1)
                )
            elif ctor in ROUTER_CLASSES:
                _, keywords = _arguments(args)
                key = "prefix" if ctor == "APIRouter" else "url_prefix"
                prefix, dynamic = _prefix(src, keywords.get(key))
                facts.routers.append(
                    RouterDef(
                        var,
                        ROUTER_CLASSES[ctor],
                        prefix,
                        dynamic=dynamic,
                        line=node.start_point.row + 1,
                    )
                )
        elif node.type == "call":
            receiver, name, args = _call_parts(src, node)
            if name in INCLUDES and receiver:
                positional, keywords = _arguments(args)
                if positional:
                    prefix, dynamic = _prefix(src, keywords.get(INCLUDES[name]))
                    framework = "fastapi" if name == "include_router" else "flask"
                    facts.includes.append(
                        Include(receiver, src.text(positional[0]), prefix, framework,
                                node.start_point.row + 1, dynamic)
                    )  # fmt: skip
    owners = {r.var: r for r in facts.routers}

    # Pass 2: the routes.
    for node in nodes:
        if node.type == "decorated_definition":
            definition = node.child_by_field_name("definition")
            handler = src.field_text(definition, "name") if definition is not None else None
            for decorator in (c for c in node.named_children if c.type == "decorator"):
                call = decorator.named_children[0] if decorator.named_children else None
                if call is not None and call.type == "call":
                    _decorator_routes(src, facts, owners, hint, call, handler, decorator)
        elif node.type == "call":
            receiver, name, args = _call_parts(src, node)
            if name in {"add_api_route", "add_url_rule"} and receiver:
                positional, keywords = _arguments(args)
                if not positional:
                    continue
                handler_node = keywords.get("view_func") or keywords.get("endpoint") or (
                    positional[1] if len(positional) > 1 else None
                )  # fmt: skip
                path = src.string(positional[0])
                framework = "flask" if name == "add_url_rule" else "fastapi"
                for method in _methods(src, keywords, ["GET"]):
                    facts.routes.append(
                        Route(method, path or "", node.start_point.row + 1,
                              src.text(handler_node) if handler_node is not None else None,
                              framework, receiver, dynamic=path is None)
                    )  # fmt: skip
    return facts


def _prefix(src: Source, node) -> tuple[str, bool]:
    """(prefix, is_dynamic). A missing argument is an empty prefix; a computed one is dynamic."""
    if node is None:
        return "", False
    value = src.string(node)
    return (value, False) if value is not None else ("", True)


def _decorator_routes(src, facts, owners, hint, call, handler, decorator) -> None:
    receiver, name, args = _call_parts(src, call)
    if receiver is None or name is None:
        return
    is_verb = name in VERBS
    if not (is_verb or name in {"route", "api_route", "websocket"}):
        return
    owner = owners.get(receiver)
    if owner is None and hint is None:
        return  # `@something.get(...)` in a file with no web framework: not a route
    framework = owner.framework if owner else (hint or "python")
    positional, keywords = _arguments(args)
    path_node = positional[0] if positional else keywords.get("path") or keywords.get("rule")
    path = src.string(path_node) if path_node is not None else ""
    dynamic = path is None
    if name == "websocket":
        methods = ["WS"]
    elif is_verb:
        methods = [method_of(name) or "GET"]
    else:
        methods = _methods(src, keywords, ["GET"])
    for method in methods:
        facts.routes.append(
            Route(method, path or "", decorator.start_point.row + 1, handler, framework, receiver,
                  dynamic=dynamic)
        )  # fmt: skip
