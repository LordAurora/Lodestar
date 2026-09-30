"""Express, Fastify, Koa (koa-router) and NestJS.

Recognised::

    const app = express();  const router = express.Router();
    router.get("/users/:id", auth, handler);   app.use("/api", router);
    router.route("/x").get(a).post(b);         app.use("/users", require("./users"));

    const fastify = Fastify();  fastify.get("/ping", opts, handler);
    fastify.register(plugin, { prefix: "/api" });  fastify.route({ method, url, handler });

    const router = new Router({ prefix: "/api" });  router.get("/x", handler);
    app.use(router.routes());

    @Controller("cats")  class CatsController { @Get(":id") findOne() {} }

A call such as ``cache.get("/x")`` is not a route: the receiver must be a router or app
created in the file, or (for routers that arrive as a parameter, like a Fastify plugin's
``fastify``) have a conventional name in a file that imports a web framework.
"""

from __future__ import annotations

import re

from app.analysis.endpoints.base import (
    EndpointFacts,
    Include,
    Route,
    RouterDef,
    Source,
    method_of,
    walk,
)

LANGUAGES = frozenset({"javascript", "typescript", "tsx"})
HTTP_CALLS = {"get", "post", "put", "delete", "patch", "options", "head", "all"}
CONVENTIONAL = {"app", "router", "server", "fastify", "api", "routes", "route", "instance", "r"}
FRAMEWORK_MODULES = {
    "express": "express", "fastify": "fastify", "koa": "koa", "koa-router": "koa",
    "@koa/router": "koa", "@nestjs/common": "nest", "@nestjs/core": "nest",
}  # fmt: skip
NEST_VERBS = {"Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All"}
FUNCTION_NODES = {"arrow_function", "function_expression", "function", "function_declaration"}
HANDLER_NODES = FUNCTION_NODES | {"identifier", "member_expression"}


def _member(src: Source, call):
    """(receiver node, property name) when the call is `receiver.prop(...)`."""
    func = call.child_by_field_name("function")
    if func is None or func.type != "member_expression":
        return None, None
    prop = func.child_by_field_name("property")
    return func.child_by_field_name("object"), (src.text(prop) if prop is not None else None)


def _args(call):
    args = call.child_by_field_name("arguments")
    return list(args.named_children) if args is not None else []


def _object_props(src: Source, node) -> dict:
    props = {}
    if node is not None and node.type == "object":
        for pair in node.named_children:
            if pair.type == "pair":
                key = pair.child_by_field_name("key")
                if key is not None:
                    props[src.text(key).strip("'\"")] = pair.child_by_field_name("value")
            elif pair.type == "shorthand_property_identifier":
                props[src.text(pair)] = pair
    return props


def _handler_name(src: Source, args) -> str | None:
    """The handler is the last argument: a name, `obj.method`, or an inline function."""
    if not args:
        return None
    last = args[-1]
    if last.type in FUNCTION_NODES:
        return "(inline)"
    if last.type in {"identifier", "member_expression"}:
        return src.text(last)
    return "(inline)"


def _frameworks(src: Source, nodes) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        spec = None
        if node.type == "import_statement":
            source = node.child_by_field_name("source")
            spec = src.string(source) if source is not None else None
        elif node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func is not None and src.text(func) == "require" and _args(node):
                spec = src.string(_args(node)[0])
        if spec in FRAMEWORK_MODULES:
            found.add(FRAMEWORK_MODULES[spec])
    return found


def detect(root, src: Source) -> EndpointFacts:
    facts = EndpointFacts()
    nodes = list(walk(root))
    frameworks = _frameworks(src, nodes)
    web = frameworks - {"nest"}
    default_framework = next(iter(sorted(web)), "express")

    for node in nodes:
        if node.type == "variable_declarator":
            _router_declaration(src, facts, node, frameworks)
    owners = {r.var: r for r in facts.routers}

    def known(receiver, args=None) -> bool:
        if receiver is None or receiver.type != "identifier":
            return False
        name = src.text(receiver)
        if name in owners:
            return True
        if name not in CONVENTIONAL:
            return False
        if web:
            return True
        # No framework import in this file (a plugin receiving `fastify` as a parameter): a
        # route has a handler as its last argument, while `axios.get(url, { params })` does not.
        return bool(args) and len(args) >= 2 and args[-1].type in HANDLER_NODES

    handled: set[int] = set()
    for node in nodes:
        if node.type == "call_expression" and node.id not in handled:
            _call(src, facts, node, known, owners, default_framework, handled)
        elif node.type == "class_declaration" and "nest" in frameworks:
            _nest_controller(src, facts, node)
    return facts


def _router_declaration(src: Source, facts: EndpointFacts, node, frameworks: set[str]) -> None:
    name_node, value = node.child_by_field_name("name"), node.child_by_field_name("value")
    if name_node is None or name_node.type != "identifier" or value is None:
        return
    var, line = src.text(name_node), node.start_point.row + 1
    if value.type == "call_expression":
        func = value.child_by_field_name("function")
        callee = src.text(func) if func is not None else ""
        required = re.fullmatch(r"require\(['\"](express|fastify)['\"]\)", callee)
        if required:  # `const app = require("fastify")()`
            callee = required.group(1)
        if callee == "express":
            facts.routers.append(RouterDef(var, "express", root=True, line=line))
        elif callee in {"express.Router", "Router"}:
            framework = "koa" if "koa" in frameworks and "express" not in frameworks else "express"
            facts.routers.append(RouterDef(var, framework, line=line))
        elif callee in {"fastify", "Fastify"}:
            facts.routers.append(RouterDef(var, "fastify", root=True, line=line))
    elif value.type == "new_expression":
        ctor = value.child_by_field_name("constructor")
        callee = src.text(ctor) if ctor is not None else ""
        if callee == "Koa":
            facts.routers.append(RouterDef(var, "koa", root=True, line=line))
        elif callee in {"Router", "KoaRouter"}:
            args = _args(value)
            prefix_node = _object_props(src, args[0] if args else None).get("prefix")
            prefix = src.string(prefix_node) if prefix_node is not None else ""
            facts.routers.append(
                RouterDef(var, "koa", prefix or "", dynamic=prefix is None, line=line)
            )


def _call(src, facts, node, known, owners, default_framework, handled) -> None:
    receiver, prop = _member(src, node)
    if prop is None or receiver is None:
        return
    line = node.start_point.row + 1
    args = _args(node)

    # router.route("/x").get(a).post(b): climb the chain to the `.route(path)` call.
    if receiver.type == "call_expression" and prop in HTTP_CALLS:
        verbs, current = [(prop, _handler_name(src, args))], receiver
        while current is not None and current.type == "call_expression":
            handled.add(current.id)
            inner_receiver, inner_prop = _member(src, current)
            if inner_prop == "route":
                path_args = _args(current)
                path = src.string(path_args[0]) if path_args else ""
                if known(inner_receiver):
                    owner = src.text(inner_receiver)
                    framework = owners[owner].framework if owner in owners else default_framework
                    for verb, handler in reversed(verbs):
                        facts.routes.append(
                            Route(method_of(verb) or "ANY", path or "", line, handler,
                                  framework, owner, dynamic=path is None)
                        )  # fmt: skip
                break
            if inner_prop in HTTP_CALLS:  # each verb in the chain has its own handler
                verbs.append((inner_prop, _handler_name(src, _args(current))))
            current = inner_receiver
        return

    if not known(receiver, args):
        return
    owner = src.text(receiver)
    framework = owners[owner].framework if owner in owners else default_framework

    if prop in HTTP_CALLS and args:
        path = src.string(args[0])
        if path is not None and (path.startswith("/") or path in {"", "*"}):
            facts.routes.append(
                Route(
                    method_of(prop) or "ANY",
                    path,
                    line,
                    _handler_name(src, args[1:]),
                    framework,
                    owner,
                )
            )
    elif prop == "route" and len(args) == 1 and args[0].type == "object":  # fastify.route({...})
        props = _object_props(src, args[0])
        url = src.string(props["url"]) if "url" in props else src.string(props.get("path"))
        method_node = props.get("method")
        methods = (
            [src.string(m) or "" for m in method_node.named_children]
            if method_node is not None and method_node.type == "array"
            else [src.string(method_node) or "ANY"]
            if method_node is not None
            else ["ANY"]
        )
        handler = props.get("handler")
        named = handler is not None and handler.type == "identifier"
        handler_name = src.text(handler) if named else "(inline)"
        for method in methods:
            facts.routes.append(
                Route(
                    method.upper(),
                    url or "",
                    line,
                    handler_name,
                    framework,
                    owner,
                    dynamic=url is None,
                )
            )
    elif prop == "use" and args:
        _use(src, facts, owner, framework, args, line)
    elif prop == "register" and args:  # fastify.register(plugin, { prefix })
        options = _object_props(src, args[1] if len(args) > 1 else None)
        prefix_node = options.get("prefix")
        prefix = src.string(prefix_node) if prefix_node is not None else ""
        facts.includes.append(
            Include(owner, _target(src, args[0]), prefix or "", "fastify", line, prefix is None)
        )


def _target(src: Source, node) -> str:
    """What is being mounted: a name, `a.b`, or `require:./path` for an inline require."""
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        if func is not None and src.text(func) == "require" and _args(node):
            return "require:" + (src.string(_args(node)[0]) or "")
        receiver, prop = _member(src, node)
        if prop == "routes" and receiver is not None:  # koa: router.routes()
            return src.text(receiver)
    return src.text(node)


def _use(src: Source, facts: EndpointFacts, owner: str, framework: str, args, line: int) -> None:
    prefix, dynamic, rest = "", False, args
    first = src.string(args[0])
    if first is not None and (first.startswith("/") or first == ""):
        prefix, rest = first, args[1:]
    elif args[0].type in {"identifier", "template_string"} and len(args) > 1:
        dynamic, rest = True, args[1:]  # app.use(BASE_PATH, router)
    for arg in rest[-1:]:
        if arg.type in {"identifier", "member_expression"} or (
            arg.type == "call_expression"
            and (
                src.text(arg.child_by_field_name("function")) == "require"
                or _member(src, arg)[1] == "routes"
            )
        ):
            facts.includes.append(
                Include(owner, _target(src, arg), prefix, framework, line, dynamic)
            )


# ---- NestJS ------------------------------------------------------------------------------------


def _decorator(src: Source, node) -> tuple[str | None, list]:
    """(name, arguments) of a `@Name(...)` decorator node."""
    inner = node.named_children[0] if node.named_children else None
    if inner is None:
        return None, []
    if inner.type == "call_expression":
        func = inner.child_by_field_name("function")
        return (src.text(func) if func is not None else None), _args(inner)
    return src.text(inner), []


def _nest_path(src: Source, args) -> tuple[str, bool]:
    if not args:
        return "", False
    if args[0].type == "object":
        node = _object_props(src, args[0]).get("path")
        value = src.string(node) if node is not None else ""
        return (value, False) if value is not None else ("", True)
    value = src.string(args[0])
    return (value, False) if value is not None else ("", True)


def _nest_controller(src: Source, facts: EndpointFacts, cls) -> None:
    decorators = [c for c in cls.named_children if c.type == "decorator"]
    if cls.parent is not None and cls.parent.type == "export_statement":
        decorators += [c for c in cls.parent.named_children if c.type == "decorator"]
    controller_args = None
    for decorator in decorators:
        decorator_name, decorator_args = _decorator(src, decorator)
        if decorator_name == "Controller":
            controller_args = decorator_args
            break
    if controller_args is None:
        return
    name = src.field_text(cls, "name") or "Controller"
    prefix, dynamic = _nest_path(src, controller_args)
    facts.routers.append(
        RouterDef(name, "nest", prefix, root=True, dynamic=dynamic, line=cls.start_point.row + 1)
    )
    body = cls.child_by_field_name("body")
    pending: list = []
    for child in body.named_children if body is not None else []:
        if child.type == "decorator":
            pending.append(child)
        elif child.type == "method_definition":
            method_name = src.field_text(child, "name") or "?"
            for decorator in pending:
                verb, args = _decorator(src, decorator)
                if verb in NEST_VERBS:
                    path, dyn = _nest_path(src, args)
                    facts.routes.append(
                        Route(method_of(verb) or "ANY", path, decorator.start_point.row + 1,
                              f"{name}.{method_name}", "nest", name, dynamic=dyn)
                    )  # fmt: skip
            pending = []
        else:
            pending = []
