"""Architecture diagrams as Mermaid text, generated deterministically from the analysis.

No language model is involved: the diagrams are drawn from the same tables as everything
else, so they are reproducible and always match the code that was analyzed.

* **modules**: which folders (or files) import which. Folders are collapsed until the
  diagram fits a node limit, and import cycles are highlighted.
* **flow**: the calls that start from one function, as a flowchart or a sequence diagram,
  down to a depth and capped at a number of nodes.

Every label and id is sanitized before it reaches Mermaid: a file name containing a quote,
``<``, ``;`` or ``%%`` must not be able to break, or inject into, the diagram.
"""

from __future__ import annotations

import re
from collections import defaultdict

from app.analysis.graph import CodeGraph, find_cycles, module_graph

MAX_MODULE_NODES = 40
MAX_FLOW_NODES = 30
LABEL_LIMIT = 44
CYCLE_COLOR = "#dc2626"  # a red that reads on both the light and the dark theme
_ENTITIES = {'"': "#quot;", "<": "#lt;", ">": "#gt;", "&": "#amp;", "#": "#35;", "|": "#124;"}


def label(text: str, limit: int = LABEL_LIMIT) -> str:
    """Make text safe inside a quoted Mermaid label."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = "…" + text[-(limit - 1) :]  # the end of a path is the informative part
    text = text.replace("%%", "%")
    return "".join(_ENTITIES.get(ch, ch) for ch in text) or "?"


def message(text: str) -> str:
    """Sequence diagram message text: `;` would end the line, so it is replaced."""
    return label(text.replace(";", ","), 60)


def _key(path: str, depth: int | None) -> str:
    """Collapse a file to its first ``depth`` folders (None keeps the file)."""
    if depth is None:
        return path
    folders = path.split("/")[:-1]
    return "/".join(folders[:depth]) or "(root)"


def _collapse(edges, keep, depth: int | None, external_depth: int = 1):
    """Map file edges onto nodes at the given granularity; returns (edges, node kinds).

    Files outside the scope are shown as folders ``external_depth`` levels deep, so a scope
    of ``app/core`` shows its neighbour as ``app/db`` rather than a vague ``app``.
    """
    weights: dict[tuple[str, str], int] = defaultdict(int)
    kinds: dict[str, str] = {}

    def node(path: str) -> str:
        inside = keep(path)
        name = _key(path, depth if inside else external_depth)
        kinds[name] = ("file" if depth is None else "dir") if inside else "external"
        return name

    for e in edges:
        src, dst = node(e["from"]), node(e["to"])
        if src != dst:
            weights[(src, dst)] += e.get("weight", 1)
    return [{"from": a, "to": b, "weight": w} for (a, b), w in sorted(weights.items())], kinds


def modules_diagram(conn, scope: str = "", limit: int = MAX_MODULE_NODES) -> dict:
    """Dependencies between files or folders, collapsed to fit ``limit`` nodes."""
    scope = scope.strip().strip("/")

    def inside(path: str) -> bool:
        return not scope or path == scope or path.startswith(scope + "/")

    files = module_graph(conn, "file")
    edges = [e for e in files["edges"] if inside(e["from"])]
    chosen, kinds, level, truncated = [], {}, "file", False
    external_depth = max(1, len(scope.split("/"))) if scope else 1
    for depth in (None, 3, 2, 1):
        chosen, kinds = _collapse(edges, inside, depth, external_depth)
        level = "file" if depth is None else "dir"
        if len(kinds) <= limit:
            break
    else:
        # Even whole top-level folders exceed the limit: keep the best connected ones.
        degree: dict[str, int] = defaultdict(int)
        for e in chosen:
            degree[e["from"]] += e["weight"]
            degree[e["to"]] += e["weight"]
        keep_nodes = set(sorted(degree, key=lambda n: (-degree[n], n))[:limit])
        chosen = [e for e in chosen if e["from"] in keep_nodes and e["to"] in keep_nodes]
        kinds = {n: k for n, k in kinds.items() if n in keep_nodes}
        truncated = True

    cycles = find_cycles(chosen)
    in_cycle = {n for c in cycles for n in c}
    ids = {name: f"n{i}" for i, name in enumerate(sorted(kinds))}
    lines = ["flowchart LR"]
    for name, node_id in ids.items():
        lines.append(f'  {node_id}["{label(name)}"]')
    cycle_links = []
    for i, e in enumerate(chosen):
        weight = f"|{e['weight']}|" if e["weight"] > 1 else ""
        lines.append(f"  {ids[e['from']]} -->{weight} {ids[e['to']]}")
        if e["from"] in in_cycle and e["to"] in in_cycle:
            cycle_links.append(str(i))
    externals = [ids[n] for n, k in kinds.items() if k == "external"]
    if externals:
        lines += [
            "  classDef external stroke-dasharray:4 3",
            f"  class {','.join(externals)} external",
        ]
    if in_cycle:
        lines += [
            f"  classDef cycle stroke:{CYCLE_COLOR},stroke-width:2px",
            f"  class {','.join(ids[n] for n in sorted(in_cycle) if n in ids)} cycle",
        ]
        if cycle_links:
            lines.append(
                f"  linkStyle {','.join(cycle_links)} stroke:{CYCLE_COLOR},stroke-width:2px"
            )
    return {
        "type": "modules",
        "mermaid": "\n".join(lines) + "\n",
        "nodes": len(kinds),
        "edges": len(chosen),
        "truncated": truncated,
        "level": level,
        "scope": scope,
        "cycles": cycles,
        "node_index": [
            {"id": ids[n], "label": n, "path": n if k == "file" else None, "kind": k}
            for n, k in sorted(kinds.items())
        ],
    }


# ---- call flow ----------------------------------------------------------------------


def _flow_shape(node_id: str, text: str, node, entry: bool) -> str:
    if entry:
        return f'  {node_id}(["{text}"])'  # the starting point: a stadium
    if node.is_test:
        return f'  {node_id}{{{{"{text}"}}}}'  # tests: a hexagon
    if node.kind == "class":
        return f'  {node_id}[["{text}"]]'
    return f'  {node_id}["{text}"]'


def flow_diagram(
    conn, symbol_id: int, depth: int = 3, limit: int = MAX_FLOW_NODES, style: str = "flowchart"
) -> dict | None:
    """The calls that start from one symbol, down to ``depth`` and at most ``limit`` nodes."""
    graph = CodeGraph(conn)
    entry = graph.nodes.get(symbol_id)
    if entry is None:
        return None
    depth = max(1, min(depth, 5))
    traversal = graph.callees(symbol_id, depth, max_nodes=max(1, limit - 1))
    ids = [symbol_id] + [h.node.id for h in traversal.hits]
    edges = graph.edges_between(ids)
    if style == "sequence":
        return _sequence(graph, entry, depth, limit, traversal.truncated)

    node_ids = {sid: f"n{i}" for i, sid in enumerate(ids)}
    lines = ["flowchart TD"]
    for sid in ids:
        node = graph.nodes[sid]
        lines.append(_flow_shape(node_ids[sid], label(node.qualified_name), node, sid == symbol_id))
    for src, dst, _line, confidence in edges:
        arrow = "-.->" if confidence == "low" else "-->"
        lines.append(f"  {node_ids[src]} {arrow} {node_ids[dst]}")
    return {
        "type": "flow",
        "style": "flowchart",
        "mermaid": "\n".join(lines) + "\n",
        "nodes": len(ids),
        "edges": len(edges),
        "truncated": traversal.truncated,
        "depth": depth,
        "entry": {"id": entry.id, "name": entry.qualified_name},
        "node_index": [
            {
                "id": node_ids[sid],
                "label": graph.nodes[sid].qualified_name,
                "path": graph.nodes[sid].file_path,
                "line": graph.nodes[sid].start_line,
                "kind": "symbol",
            }
            for sid in ids
        ],
    }


def _participant(node) -> str:
    """Sequence diagrams group calls by the class that owns the code, else by the file."""
    if node.owner:
        return node.owner.split(".")[0]
    return node.file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _sequence(graph: CodeGraph, entry, depth: int, limit: int, truncated: bool) -> dict:
    """A sequence diagram in the order the calls appear in the code (depth first)."""
    outgoing: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for src, dst, _line, confidence in graph.edges_between(list(graph.nodes)):
        outgoing[src].append((dst, confidence))

    messages: list[tuple[int, int, str]] = []
    seen_edges: set[tuple[int, int]] = set()
    stack = [(entry.id, 0, iter(outgoing.get(entry.id, ())))]
    on_path = {entry.id}
    while stack:
        current, level, children = stack[-1]
        step = next(children, None)
        if step is None:
            stack.pop()
            on_path.discard(current)
            continue
        callee, confidence = step
        if level >= depth or callee in on_path or (current, callee) in seen_edges:
            continue
        if len(messages) >= limit:
            truncated = True
            break
        seen_edges.add((current, callee))
        messages.append((current, callee, confidence))
        on_path.add(callee)
        stack.append((callee, level + 1, iter(outgoing.get(callee, ()))))

    order: list[str] = []
    for src, dst, _ in messages:
        for sid in (src, dst):
            name = _participant(graph.nodes[sid])
            if name not in order:
                order.append(name)
    if not order:
        order = [_participant(entry)]
    ids = {name: f"p{i}" for i, name in enumerate(order)}
    lines = ["sequenceDiagram"]
    lines += [f"  participant {ids[name]} as {label(name, 30)}" for name in order]
    for src, dst, confidence in messages:
        arrow = "-->>" if confidence == "low" else "->>"
        a, b = graph.nodes[src], graph.nodes[dst]
        lines.append(
            f"  {ids[_participant(a)]}{arrow}{ids[_participant(b)]}: {message(b.name + '()')}"
        )
    return {
        "type": "flow",
        "style": "sequence",
        "mermaid": "\n".join(lines) + "\n",
        "nodes": len(order),
        "edges": len(messages),
        "truncated": truncated,
        "depth": depth,
        "entry": {"id": entry.id, "name": entry.qualified_name},
        "node_index": [],
    }
