"""Turn per-file routes into endpoints with full paths.

A route's path is only the last part. ``@router.get("/{id}")`` lives in ``users.py``, the
router says ``prefix="/users"``, and ``main.py`` mounts it with
``include_router(..., prefix="/api")``.
This module follows that chain across files, using the same import resolution as the call graph:

    full path = mounts (outermost first) + the router's own prefix + the route's path

A path is ``partial`` when some part could not be resolved: a prefix computed at run time,
a router that nothing visible mounts, or a mount we cannot trace.
"""

from __future__ import annotations

import json
from collections import defaultdict

from app.analysis.endpoints.base import join_paths
from app.analysis.resolve import split_alias
from app.analysis.symbols import load_repo_index

Node = tuple[str, str]  # (file, router variable or controller class)
MAX_CHAIN = 10  # a guard against mount cycles


class _Repo:
    """Routers, mounts and import bindings of one repository, loaded for resolution."""

    def __init__(self, conn):
        self.index = load_repo_index(conn)
        self.routers: dict[Node, dict] = {
            (r["file_path"], r["var"]): dict(r)
            for r in conn.execute("SELECT * FROM endpoint_routers")
        }
        self.by_file: dict[str, list[Node]] = defaultdict(list)
        for node in self.routers:
            self.by_file[node[0]].append(node)
        # file -> local name -> [(files, original name or None)]
        self.bindings: dict[str, dict[str, list[tuple[set[str], str | None]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for stmt in conn.execute(
            "SELECT file_path, module, names_json, alias FROM import_statements"
        ).fetchall():
            self._bind(
                stmt["file_path"],
                stmt["module"],
                json.loads(stmt["names_json"] or "[]"),
                stmt["alias"],
            )
        self.parents: dict[Node, tuple[Node | None, str, bool]] = {}
        # A whole file mounted as a plugin, e.g. `fastify.register(require("./x"), { prefix })`:
        # its routes hang off a function parameter, so the mount applies to the file itself.
        self.file_mounts: dict[str, tuple[Node | None, str, bool]] = {}
        self._connect(conn)

    # ---- imports ---------------------------------------------------------------

    def _files(self, path: str, module: str, names: list[str], alias: str | None) -> list:
        return self.index.resolve(path, self.index.languages.get(path, ""), module, names, alias)

    def _bind(self, path: str, module: str, names: list[str], alias: str | None) -> None:
        module_files = {t.file for t in self._files(path, module, [], alias)}
        language = self.index.languages.get(path, "")
        if alias and (not names or names == ["*"]):
            self.bindings[path][alias].append((module_files, None))  # `import x as a`, `* as a`
        elif language == "python" and not names:
            local = module.lstrip(".").split(".")[-1]
            if local:
                self.bindings[path][local].append((module_files, None))
        if not names:
            return
        with_names = self._files(path, module, names, alias)
        for imported in names:
            original, local = split_alias(imported)
            if original in {"*"}:
                continue
            if original == "default":
                self.bindings[path][alias or local].append((module_files, "default"))
                continue
            submodules = {
                t.file for t in with_names if t.bind == local and t.file not in module_files
            }
            if submodules:
                self.bindings[path][local].append((submodules, None))  # `from pkg import module`
            else:
                self.bindings[path][local].append((module_files, original))  # `from m import name`

    # ---- resolving names to routers ---------------------------------------------

    def _routers_in(self, file: str, name: str | None) -> list[Node]:
        nodes = self.by_file.get(file, [])
        if name and name != "default" and (file, name) in self.routers:
            return [(file, name)]
        mounted = [n for n in nodes if not self.routers[n]["is_root"]]
        return mounted or list(nodes)

    def resolve_expr(self, file: str, expr: str) -> list[Node]:
        """The routers an expression like `router`, `users.router` or `require:./users` names."""
        if expr.startswith("require:"):
            spec = expr[len("require:") :]
            files = {t.file for t in self._files(file, spec, [], None)}
            return [n for f in sorted(files) for n in self._routers_in(f, None)]
        if "." not in expr:
            if (file, expr) in self.routers:
                return [(file, expr)]
            found: list[Node] = []
            for files, original in self.bindings[file].get(expr, []):
                for f in sorted(files):
                    found += self._routers_in(f, original)
            return found
        base, attribute = expr.split(".", 1)[0], expr.rsplit(".", 1)[-1]
        found = []
        for files, _ in self.bindings[file].get(base, []):
            for f in sorted(files):
                found += self._routers_in(f, attribute)
        return found

    def resolve_var(self, file: str, var: str) -> Node | None:
        nodes = self.resolve_expr(file, var)
        return nodes[0] if nodes else None

    def target_files(self, file: str, expr: str) -> set[str]:
        """The files an include target refers to (used when the target holds no router)."""
        if expr.startswith("require:"):
            return {t.file for t in self._files(file, expr[len("require:") :], [], None)}
        base = expr.split(".", 1)[0]
        return {f for files, _ in self.bindings[file].get(base, []) for f in files}

    def _connect(self, conn) -> None:
        for inc in conn.execute("SELECT * FROM endpoint_includes ORDER BY file_path, line"):
            parent = self.resolve_var(inc["file_path"], inc["parent_var"])
            mount = (parent, inc["prefix"], bool(inc["dynamic"]))
            children = self.resolve_expr(inc["file_path"], inc["target"])
            for child in children:
                self.parents.setdefault(child, mount)
            if not children:
                for file in self.target_files(inc["file_path"], inc["target"]):
                    if not self.by_file.get(file):
                        self.file_mounts.setdefault(file, mount)

    # ---- prefixes -------------------------------------------------------------------

    def prefix_chain(self, node: Node) -> tuple[list[str], bool]:
        """(prefix parts from the outermost mount to the router itself, is_partial)."""
        parts: list[str] = []
        partial = False
        seen: set[Node] = set()
        current: Node | None = node
        while current is not None and len(seen) < MAX_CHAIN:
            if current in seen:
                return list(reversed(parts)), True
            seen.add(current)
            router = self.routers.get(current)
            if router is None:
                return list(reversed(parts)), True
            parts.append(router["prefix"])
            partial = partial or bool(router["dynamic"])
            if router["parent_var"]:  # a group made from another router in the same file
                current = self.resolve_var(current[0], router["parent_var"])
                partial = partial or current is None
                continue
            mount = self.parents.get(current)
            if mount is not None:
                parent, prefix, dynamic = mount
                if router["framework"] == "flask" and prefix:
                    parts[-1] = ""  # Flask: the registration prefix replaces the blueprint's own
                parts.append(prefix)
                partial = partial or dynamic or parent is None
                current = parent
                continue
            if not router["is_root"]:
                partial = True  # nothing we can see mounts this router
            break
        return list(reversed(parts)), partial


def build_endpoints(conn, repo_id: str) -> None:
    repo = _Repo(conn)
    symbols = defaultdict(dict)  # file -> qualified name -> id
    by_name: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in conn.execute("SELECT id, file_path, name, qualified_name, kind FROM symbols"):
        if r["kind"] in {"function", "method"}:
            symbols[r["file_path"]][r["qualified_name"]] = r["id"]
            by_name[r["name"]].append((r["id"], r["file_path"]))

    def link(handler: str | None, file: str) -> int | None:
        if not handler or handler == "(inline)":
            return None
        if handler in symbols[file]:
            return symbols[file][handler]
        short = handler.split(".")[-1]
        same_file = [i for i, f in by_name.get(short, []) if f == file]
        if len(same_file) == 1:
            return same_file[0]
        everywhere = by_name.get(short, [])
        return everywhere[0][0] if not same_file and len(everywhere) == 1 else None

    rows = []
    memo: dict[Node, tuple[list[str], bool]] = {}
    for r in conn.execute("SELECT * FROM endpoint_routes ORDER BY file_path, line, id"):
        partial = bool(r["dynamic"])
        if r["owner"]:
            node = repo.resolve_var(r["file_path"], r["owner"])
            mount = repo.file_mounts.get(r["file_path"]) if node is None else None
            if mount is not None:  # a plugin file: apply the prefix it was registered with
                parent, prefix, dynamic = mount
                parts, chain_partial = ([], True) if parent is None else repo.prefix_chain(parent)
                parts = [*parts, prefix]
                chain_partial = chain_partial or dynamic
                if parent is None:
                    chain_partial = True
            elif node is None:
                parts, chain_partial = [], True
            else:
                if node not in memo:
                    memo[node] = repo.prefix_chain(node)
                parts, chain_partial = memo[node]
            partial = partial or chain_partial
            path = join_paths(*parts, r["path"])
        else:
            path = join_paths(
                r["path"]
            )  # an absolute path (net/http, chi closures, `[HttpGet("/x")]`)
        rows.append(
            (repo_id, r["method"], path, link(r["handler"], r["file_path"]), r["handler"],
             r["file_path"], r["line"], r["framework"], int(partial), r["file_hash"])
        )  # fmt: skip
    conn.execute("DELETE FROM endpoints")
    conn.executemany(
        "INSERT INTO endpoints (repo_id, method, path, handler_symbol_id, handler_name, file_path,"
        " line, framework, is_partial, file_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
