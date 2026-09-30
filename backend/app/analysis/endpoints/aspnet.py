"""ASP.NET Core: controllers with attribute routing, and minimal APIs.

Recognised::

    [ApiController] [Route("api/[controller]")]
    class BooksController : ControllerBase {
      [HttpGet("{id}")] IActionResult Get(int id) {}
      [HttpPost]        IActionResult Create() {}
    }

    var app = builder.Build();  var g = app.MapGroup("/api");
    g.MapGet("/ping", () => "pong");  app.MapPost("/x", Handler);

The ``[controller]`` and ``[action]`` tokens are replaced by the class and method names.
"""

from __future__ import annotations

import re

from app.analysis.endpoints.base import EndpointFacts, Route, RouterDef, Source, last_segment, walk

LANGUAGES = frozenset({"csharp"})
HTTP_ATTRIBUTES = {
    "HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpDelete": "DELETE",
    "HttpPatch": "PATCH", "HttpHead": "HEAD", "HttpOptions": "OPTIONS",
}  # fmt: skip
MAP_CALLS = {
    "MapGet": "GET", "MapPost": "POST", "MapPut": "PUT", "MapDelete": "DELETE", "MapPatch": "PATCH",
}  # fmt: skip
ROOT_CALLS = {"Build", "Create", "CreateBuilder"}


def _attributes(src: Source, node):
    """(attribute name, template, is_dynamic) for every attribute on a declaration."""
    for child in node.named_children:
        if child.type != "attribute_list":
            continue
        for attribute in child.named_children:
            if attribute.type != "attribute":
                continue
            name = last_segment(src.field_text(attribute, "name") or "")
            template, dynamic = "", False
            arguments = next(
                (c for c in attribute.named_children if c.type == "attribute_argument_list"), None
            )
            for argument in arguments.named_children if arguments is not None else []:
                inner = argument.named_children[-1] if argument.named_children else None
                if inner is None:
                    continue
                value = src.string(inner)
                if value is None:
                    dynamic = True
                elif not template:
                    template = value
            yield name, template, dynamic


def _tokens(template: str, controller: str, action: str = "") -> str:
    return template.replace("[controller]", controller).replace("[action]", action)


def _invocation(src: Source, node):
    """(receiver text, member name, argument expressions) of `a.B(...)`."""
    func = node.child_by_field_name("function")
    if func is None or func.type != "member_access_expression":
        return None, None, []
    expr, name = func.child_by_field_name("expression"), func.child_by_field_name("name")
    args = node.child_by_field_name("arguments")
    expressions = [
        a.named_children[-1] for a in (args.named_children if args else []) if a.named_children
    ]
    return (
        (src.text(expr) if expr is not None else None),
        (src.text(name) if name else None),
        expressions,
    )


def _handler(src: Source, node) -> str | None:
    if node is None:
        return None
    if node.type in {"identifier", "member_access_expression"}:
        return src.text(node)
    return "(inline)"


def detect(root, src: Source) -> EndpointFacts:
    facts = EndpointFacts()
    nodes = list(walk(root))

    # Controllers.
    for cls in (n for n in nodes if n.type == "class_declaration"):
        name = src.field_text(cls, "name") or "Controller"
        controller = re.sub(r"Controller$", "", name)
        attrs = list(_attributes(src, cls))
        prefix, dynamic = "", False
        for attr, template, dyn in attrs:
            if attr == "Route":
                prefix, dynamic = _tokens(template, controller), dyn
        routes = []
        body = cls.child_by_field_name("body")
        for method in (
            (c for c in body.named_children if c.type == "method_declaration") if body else []
        ):
            action = src.field_text(method, "name") or "?"
            method_attrs = list(_attributes(src, method))
            verbs = [(HTTP_ATTRIBUTES[a], t, d) for a, t, d in method_attrs if a in HTTP_ATTRIBUTES]
            route_attr = next(((t, d) for a, t, d in method_attrs if a == "Route"), None)
            if not verbs and route_attr is not None:
                verbs = [("ANY", "", False)]
            for http, template, dyn in verbs:
                if route_attr is not None and not template:
                    template, dyn = route_attr
                template = _tokens(template, controller, action)
                absolute = template.startswith(("/", "~/"))  # ignores the controller's prefix
                path = template.lstrip("~") if absolute else template
                routes.append(
                    Route(http, path, method.start_point.row + 1, f"{name}.{action}", "aspnet",
                          None if absolute else name, dynamic=dyn)
                )  # fmt: skip
        if routes:
            facts.routers.append(
                RouterDef(
                    name, "aspnet", prefix, root=True, dynamic=dynamic, line=cls.start_point.row + 1
                )
            )
            facts.routes += routes

    # Minimal APIs: roots and groups first, then the Map* calls.
    for node in nodes:
        if node.type != "variable_declarator":
            continue
        var = src.field_text(node, "name")
        call = next(
            (c for c in reversed(node.named_children) if c.type == "invocation_expression"), None
        )
        if var is None or call is None:
            continue
        receiver, member, args = _invocation(src, call)
        line = node.start_point.row + 1
        if member == "MapGroup" and receiver:
            path = src.string(args[0]) if args else ""
            facts.routers.append(
                RouterDef(
                    var, "aspnet", path or "", parent_var=receiver, dynamic=path is None, line=line
                )
            )
        elif member in ROOT_CALLS:
            facts.routers.append(RouterDef(var, "aspnet", root=True, line=line))
    owners = {r.var: r for r in facts.routers}

    for node in nodes:
        if node.type != "invocation_expression":
            continue
        receiver, member, args = _invocation(src, node)
        if member not in MAP_CALLS or receiver is None or not args:
            continue
        path = src.string(args[0])
        facts.routes.append(
            Route(MAP_CALLS[member], path or "", node.start_point.row + 1,
                  _handler(src, args[1] if len(args) > 1 else None), "aspnet",
                  receiver if receiver in owners else receiver, dynamic=path is None)
        )  # fmt: skip
    return facts
