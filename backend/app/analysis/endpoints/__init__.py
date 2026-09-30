"""API endpoint catalog: find HTTP routes and work out their full paths.

Each web framework gets its own small detector module (``python_web``, ``js_web``,
``spring``, ``go_web``, ``aspnet``). A detector reads one parsed file and reports its
routes, routers and mounts (see ``base.py``). This analyzer stores those raw facts per
file, and ``resolve.py`` combines them across files into the ``endpoints`` table.

Detection is syntactic, so it is heuristic: a route whose path or prefix is computed at
run time, or whose router is mounted in code we cannot follow, is marked ``partial``.
"""

from __future__ import annotations

from app.analysis.conventions import is_test_path
from app.analysis.endpoints import aspnet, go_web, js_web, python_web, spring
from app.analysis.endpoints.base import EndpointFacts, Source
from app.analysis.endpoints.resolve import build_endpoints
from app.analysis.pipeline import FileContext

DETECTORS = {
    "python": [python_web],
    "javascript": [js_web],
    "typescript": [js_web],
    "tsx": [js_web],
    "java": [spring],
    "go": [go_web],
    "csharp": [aspnet],
}
ENDPOINT_LANGUAGES = frozenset(DETECTORS)


def detect_file(language: str, source: bytes, root) -> EndpointFacts:
    """Run every detector for ``language`` on one parsed file."""
    merged = EndpointFacts()
    src = Source(source)
    for detector in DETECTORS.get(language, []):
        facts = detector.detect(root, src)
        merged.routes += facts.routes
        merged.routers += facts.routers
        merged.includes += facts.includes
    return merged


class EndpointsAnalyzer:
    name = "endpoints"
    version = 1
    languages = ENDPOINT_LANGUAGES

    def analyze(self, ctx: FileContext, conn, repo_id: str) -> dict | None:
        if is_test_path(ctx.path):
            return None  # test apps and fixtures are not the project's API
        root = ctx.root()
        if root is None:
            return None
        facts = detect_file(ctx.language, ctx.source, root)
        conn.executemany(
            "INSERT INTO endpoint_routes (repo_id, file_path, method, path, owner, line, handler,"
            " framework, dynamic, file_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(repo_id, ctx.path, r.method, r.path, r.owner, r.line, r.handler, r.framework,
              int(r.dynamic), ctx.hash) for r in facts.routes],
        )  # fmt: skip
        conn.executemany(
            "INSERT INTO endpoint_routers (repo_id, file_path, var, framework, prefix, is_root,"
            " parent_var, dynamic, line, file_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(repo_id, ctx.path, r.var, r.framework, r.prefix, int(r.root), r.parent_var,
              int(r.dynamic), r.line, ctx.hash) for r in facts.routers],
        )  # fmt: skip
        conn.executemany(
            "INSERT INTO endpoint_includes (repo_id, file_path, parent_var, target, prefix,"
            " framework, dynamic, line, file_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            [(repo_id, ctx.path, i.parent_var, i.target, i.prefix, i.framework, int(i.dynamic),
              i.line, ctx.hash) for i in facts.includes],
        )  # fmt: skip
        return None

    def delete(self, conn, path: str) -> None:
        for table in ("endpoint_routes", "endpoint_routers", "endpoint_includes"):
            conn.execute(f"DELETE FROM {table} WHERE file_path = ?", (path,))  # noqa: S608

    def finalize(self, conn, repo_id: str, root) -> None:
        build_endpoints(conn, repo_id)
