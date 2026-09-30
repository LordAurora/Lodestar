"""Graph queries over the resolved references: callers, callees and module dependencies.

Every traversal has two guards, because real code bases contain recursion, mutual
recursion and hub functions called from everywhere:

* a ``visited`` set, so cycles cannot loop forever;
* a hard cap on the number of nodes returned (``truncated`` tells the caller).

The confidence of a *path* is the confidence of its weakest edge: two ``high``
edges and one ``low`` edge make a ``low`` path.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from app.analysis import weakest
from app.analysis.resolve import dir_of

MAX_NODES = 300
EDGE_KINDS = ("call", "inherit")


@dataclass(frozen=True)
class Node:
    id: int
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    is_test: bool
    has_doc: bool = False
    owner: str | None = None  # qualified name of the enclosing class
    language: str = ""


@dataclass(frozen=True)
class Hit:
    """A symbol reached by a traversal."""

    node: Node
    depth: int  # 1 = direct caller / callee
    line: int  # the line of the call, in `call_file`
    call_file: str
    confidence: str  # the weakest confidence on the path from the start symbol


@dataclass
class Traversal:
    hits: list[Hit]
    truncated: bool  # the node cap was reached; there is more


class CodeGraph:
    """The call graph of one repository, loaded into memory for fast traversal."""

    def __init__(self, conn):
        self.nodes: dict[int, Node] = {
            r["id"]: Node(
                r["id"], r["name"], r["qualified_name"], r["kind"], r["file_path"],
                r["start_line"], r["end_line"], bool(r["is_test"]), bool(r["has_doc"]),
                r["owner"], r["language"],
            )
            for r in conn.execute("SELECT * FROM symbols")
        }  # fmt: skip
        # target -> [(caller, line, file, confidence)] and caller -> [(target, ...)]
        self._incoming: dict[int, list[tuple[int, int, str, str]]] = defaultdict(list)
        self._outgoing: dict[int, list[tuple[int, int, str, str]]] = defaultdict(list)
        marks = ",".join("?" * len(EDGE_KINDS))
        for r in conn.execute(
            "SELECT from_symbol_id, to_symbol_id, line, file_path, confidence"
            f" FROM symbol_references WHERE kind IN ({marks})"  # noqa: S608
            " AND from_symbol_id IS NOT NULL AND to_symbol_id IS NOT NULL",
            EDGE_KINDS,
        ):
            src, dst = r["from_symbol_id"], r["to_symbol_id"]
            self._incoming[dst].append((src, r["line"], r["file_path"], r["confidence"]))
            self._outgoing[src].append((dst, r["line"], r["file_path"], r["confidence"]))

    def callers(
        self, symbol_id: int | list[int], depth: int = 3, max_nodes: int = MAX_NODES
    ) -> Traversal:
        """Who calls (or inherits from) this symbol, directly and transitively.

        Pass several ids to start from all of them at once (a class and its methods).
        """
        return self._walk(symbol_id, self._incoming, depth, max_nodes)

    def callees(
        self, symbol_id: int | list[int], depth: int = 3, max_nodes: int = MAX_NODES
    ) -> Traversal:
        """What this symbol calls, directly and transitively."""
        return self._walk(symbol_id, self._outgoing, depth, max_nodes)

    def _walk(self, start: int | list[int], edges, depth: int, max_nodes: int) -> Traversal:
        starts = [start] if isinstance(start, int) else list(start)
        visited = set(starts)
        hits: list[Hit] = []
        queue: deque[tuple[int, int, str]] = deque((s, 0, "high") for s in starts)
        truncated = False
        while queue:
            current, level, path_confidence = queue.popleft()
            if level >= depth:
                continue
            for other, line, file_path, confidence in sorted(
                edges.get(current, ()), key=lambda e: (e[2], e[1])
            ):
                if other in visited or other not in self.nodes:
                    continue
                if len(hits) >= max_nodes:
                    truncated = True
                    continue
                visited.add(other)
                combined = weakest(path_confidence, confidence)
                hits.append(Hit(self.nodes[other], level + 1, line, file_path, combined))
                queue.append((other, level + 1, combined))
        hits.sort(key=lambda h: (h.depth, h.node.file_path, h.line))
        return Traversal(hits, truncated)


def module_graph(conn, level: str = "file") -> dict:
    """Dependencies between files (or folders): ``{"nodes": [...], "edges": [...]}``.

    Built from the resolved imports. With ``level="dir"`` files collapse into their
    folder and the edge weight counts the imports that were merged.
    """
    key = (lambda p: dir_of(p) or ".") if level == "dir" else (lambda p: p)
    weights: dict[tuple[str, str], int] = defaultdict(int)
    nodes: set[str] = set()
    for r in conn.execute(
        "SELECT file_path, resolved_file_path FROM imports WHERE resolved_file_path IS NOT NULL"
    ):
        src, dst = key(r["file_path"]), key(r["resolved_file_path"])
        nodes.update((src, dst))
        if src != dst:
            weights[(src, dst)] += 1
    edges = [{"from": a, "to": b, "weight": w} for (a, b), w in sorted(weights.items())]
    return {"nodes": sorted(nodes), "edges": edges}


def find_cycles(edges: list[dict]) -> list[list[str]]:
    """Strongly connected components with more than one node (Tarjan, iterative)."""
    graph: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        graph[e["from"]].append(e["to"])
        graph.setdefault(e["to"], [])
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []
    counter = 0
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(graph[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(graph[child])))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    cycles.append(sorted(component))
    return sorted(cycles)
