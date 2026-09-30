"""Reading the endpoint catalog: filtering, facets and exports."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter

METHOD_ORDER = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "WS", "ANY"]
FRAMEWORK_NAMES = {
    "fastapi": "FastAPI", "flask": "Flask", "express": "Express", "fastify": "Fastify",
    "koa": "Koa", "nest": "NestJS", "spring": "Spring", "nethttp": "net/http", "gin": "Gin",
    "chi": "chi", "echo": "Echo", "aspnet": "ASP.NET",
}  # fmt: skip


def list_endpoints(conn, method: str = "", q: str = "", framework: str = "") -> dict:
    rows = conn.execute(
        "SELECT e.*, s.qualified_name AS symbol FROM endpoints e"
        " LEFT JOIN symbols s ON s.id = e.handler_symbol_id ORDER BY e.path, e.method, e.file_path"
    ).fetchall()
    everything = [dict(r) for r in rows]
    items = []
    for r in everything:
        haystack = f"{r['path']} {r['handler_name'] or ''} {r['file_path']}".lower()
        if method and r["method"] != method.upper():
            continue
        if framework and r["framework"] != framework:
            continue
        if q and q.lower() not in haystack:
            continue
        items.append(
            {
                "id": r["id"],
                "method": r["method"],
                "path": r["path"],
                "handler": r["handler_name"],
                "handler_symbol_id": r["handler_symbol_id"],
                "symbol": r["symbol"],
                "file_path": r["file_path"],
                "line": r["line"],
                "framework": r["framework"],
                "framework_label": FRAMEWORK_NAMES.get(r["framework"], r["framework"]),
                "partial": bool(r["is_partial"]),
            }
        )
    methods = Counter(r["method"] for r in everything)
    frameworks = Counter(r["framework"] for r in everything)
    return {
        "endpoints": items,
        "total": len(everything),
        "shown": len(items),
        "methods": [{"method": m, "count": methods[m]} for m in METHOD_ORDER if m in methods],
        "frameworks": [
            {"framework": f, "label": FRAMEWORK_NAMES.get(f, f), "count": c}
            for f, c in sorted(frameworks.items())
        ],
    }


def export_endpoints(items: list[dict], fmt: str) -> tuple[str, str, str]:
    """(body, media type, file extension) for json, csv or markdown."""
    if fmt == "json":
        keep = ("method", "path", "handler", "file_path", "line", "framework", "partial")
        body = json.dumps([{k: i[k] for k in keep} for i in items], indent=2)
        return body + "\n", "application/json", "json"
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["method", "path", "handler", "file", "line", "framework", "partial"])
        for i in items:
            writer.writerow([i["method"], i["path"], i["handler"] or "", i["file_path"], i["line"],
                             i["framework_label"], "yes" if i["partial"] else ""])  # fmt: skip
        return out.getvalue(), "text/csv", "csv"
    lines = [
        "# API endpoints",
        "",
        "| Method | Path | Handler | Location | Framework |",
        "|---|---|---|---|---|",
    ]
    for i in items:
        path = f"`{i['path']}`" + (" (partial)" if i["partial"] else "")
        handler = f"`{i['handler']}`" if i["handler"] else ""
        place = f"`{i['file_path']}:{i['line']}`"
        lines.append(f"| {i['method']} | {path} | {handler} | {place} | {i['framework_label']} |")
    return "\n".join(lines) + "\n", "text/markdown", "md"
