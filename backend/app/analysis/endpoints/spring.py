"""Spring MVC: ``@RestController`` / ``@Controller`` classes and ``@*Mapping`` methods.

The class-level ``@RequestMapping("/api/books")`` prefix and each method's mapping are
combined by the resolver::

    @RestController
    @RequestMapping("/api/books")
    class BookController {
      @GetMapping("/{id}")   Book get() {}
      @PostMapping           void create() {}
      @RequestMapping(value = "/x", method = RequestMethod.POST)   void x() {}
    }
"""

from __future__ import annotations

from app.analysis.endpoints.base import EndpointFacts, Route, RouterDef, Source, last_segment, walk

LANGUAGES = frozenset({"java"})
MAPPINGS = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH", "RequestMapping": "ANY",
}  # fmt: skip
PATH_KEYS = {"value", "path"}


def _annotations(src: Source, node):
    """(simple name, arguments or None, annotation node) for each annotation on a declaration."""
    for child in node.named_children:
        if child.type != "modifiers":
            continue
        for annotation in child.named_children:
            if annotation.type in {"marker_annotation", "annotation"}:
                name = src.field_text(annotation, "name") or ""
                yield last_segment(name), annotation.child_by_field_name("arguments"), annotation


def _strings(src: Source, node) -> tuple[list[str], bool]:
    """String values of a literal or `{"a", "b"}`; the flag says a value was a constant."""
    if node is None:
        return [], False
    value = src.string(node)
    if value is not None:
        return [value], False
    if node.type == "element_value_array_initializer":
        found, dynamic = [], False
        for item in node.named_children:
            values, dyn = _strings(src, item)
            found += values
            dynamic = dynamic or dyn
        return found, dynamic
    return [], True  # e.g. a reference to a String constant


def _paths(src: Source, args) -> tuple[list[str], bool]:
    if args is None:
        return [""], False
    paths, dynamic = [], False
    for arg in args.named_children:
        if arg.type == "element_value_pair":
            key = src.field_text(arg, "key")
            if key in PATH_KEYS:
                values, dyn = _strings(src, arg.child_by_field_name("value"))
                paths += values
                dynamic = dynamic or dyn
        else:
            values, dyn = _strings(src, arg)
            paths += values
            dynamic = dynamic or dyn
    return (paths or [""]), dynamic


def _request_methods(src: Source, args) -> list[str]:
    if args is None:
        return ["ANY"]
    for arg in args.named_children:
        if arg.type == "element_value_pair" and src.field_text(arg, "key") == "method":
            value = arg.child_by_field_name("value")
            nodes = (
                value.named_children if value.type == "element_value_array_initializer" else [value]
            )
            methods = [last_segment(src.text(n)).upper() for n in nodes]
            return methods or ["ANY"]
    return ["ANY"]


def detect(root, src: Source) -> EndpointFacts:
    facts = EndpointFacts()
    for cls in (n for n in walk(root) if n.type == "class_declaration"):
        name = src.field_text(cls, "name") or "Controller"
        prefix, dynamic = "", False
        for annotation, args, _ in _annotations(src, cls):
            if annotation == "RequestMapping":
                paths, dynamic = _paths(src, args)
                prefix = paths[0]
        routes = []
        body = cls.child_by_field_name("body")
        for method in (
            (c for c in body.named_children if c.type == "method_declaration") if body else []
        ):
            method_name = src.field_text(method, "name") or "?"
            for annotation, args, node in _annotations(src, method):
                verb = MAPPINGS.get(annotation)
                if verb is None:
                    continue
                verbs = _request_methods(src, args) if annotation == "RequestMapping" else [verb]
                paths, dyn = _paths(src, args)
                for path in paths:
                    for http in verbs:
                        routes.append(
                            Route(http, path, node.start_point.row + 1, f"{name}.{method_name}",
                                  "spring", name, dynamic=dyn)
                        )  # fmt: skip
        if routes:
            facts.routers.append(
                RouterDef(
                    name, "spring", prefix, root=True, dynamic=dynamic, line=cls.start_point.row + 1
                )
            )
            facts.routes += routes
    return facts
