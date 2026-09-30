"""The symbols analyzer: definitions, call sites and imports, then cross-file resolution.

``analyze`` stores what each file says about itself. ``finalize`` then combines
the files: it resolves every import to the files it points to, and every call
site to the symbol(s) it probably calls, writing the derived ``imports`` and
``symbol_references`` tables (see ``resolve.py`` for the rules and confidences).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.analysis.extract import SUPPORTED_LANGUAGES, extract_facts
from app.analysis.pipeline import FileContext
from app.analysis.resolve import CallResolver, RepoIndex, Sym


class SymbolsAnalyzer:
    name = "symbols"
    version = 1
    languages = SUPPORTED_LANGUAGES

    def analyze(self, ctx: FileContext, conn, repo_id: str) -> dict | None:
        root = ctx.root()
        if root is None:
            return None
        facts = extract_facts(ctx.path, ctx.language, ctx.source, root)

        ids: list[int] = []
        for s in facts.symbols:
            cur = conn.execute(
                "INSERT INTO symbols (repo_id, file_path, language, name, qualified_name, kind,"
                " owner, start_line, end_line, is_test, has_doc, signature, file_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (repo_id, ctx.path, s.language, s.name, s.qualified_name, s.kind, s.owner,
                 s.start_line, s.end_line, int(s.is_test), int(s.has_doc), s.signature, ctx.hash),
            )  # fmt: skip
            ids.append(cur.lastrowid)
        conn.executemany(
            "INSERT INTO call_sites (repo_id, file_path, from_symbol_id, to_name, receiver, kind,"
            " line, file_hash) VALUES (?,?,?,?,?,?,?,?)",
            [
                (repo_id, ctx.path, ids[c.from_index] if c.from_index is not None else None,
                 c.name, c.receiver, c.kind, c.line, ctx.hash)
                for c in facts.calls
            ],
        )  # fmt: skip
        conn.executemany(
            "INSERT INTO import_statements (repo_id, file_path, module, names_json, alias, line,"
            " file_hash) VALUES (?,?,?,?,?,?,?)",
            [
                (repo_id, ctx.path, i.module, json.dumps(i.names) if i.names else None, i.alias,
                 i.line, ctx.hash)
                for i in facts.imports
            ],
        )  # fmt: skip
        return {"namespace": facts.namespace} if facts.namespace else None

    def delete(self, conn, path: str) -> None:
        for table in ("symbols", "call_sites", "import_statements"):
            conn.execute(f"DELETE FROM {table} WHERE file_path = ?", (path,))  # noqa: S608

    # ---- cross-file resolution -------------------------------------------------

    def finalize(self, conn, repo_id: str, root: Path) -> None:
        languages = {
            r["path"]: r["language"] for r in conn.execute("SELECT path, language FROM files")
        }
        namespaces: dict[str, str] = {}
        for r in conn.execute("SELECT path, meta FROM analysis_files WHERE analyzer = 'symbols'"):
            if r["meta"]:
                ns = json.loads(r["meta"]).get("namespace")
                if ns:
                    namespaces[r["path"]] = ns
        index = RepoIndex(languages, namespaces)

        # 1. Imports -> the files they point to.
        import_targets: dict[str, set[str]] = defaultdict(set)
        bindings: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        import_rows: list[tuple] = []
        named_imports: list[tuple[str, int, str, str, list[str], set[str]]] = []
        for stmt in conn.execute(
            "SELECT file_path, module, names_json, alias, line, file_hash FROM import_statements"
        ).fetchall():
            path = stmt["file_path"]
            names = json.loads(stmt["names_json"]) if stmt["names_json"] else []
            targets = index.resolve(
                path, languages.get(path, ""), stmt["module"], names, stmt["alias"]
            )
            for t in targets:
                import_targets[path].add(t.file)
                if t.bind:
                    bindings[path][t.bind].add(t.file)
            files = sorted({t.file for t in targets}) or [None]
            import_rows += [(repo_id, path, stmt["module"], f, stmt["file_hash"]) for f in files]
            if targets and names:
                named_imports.append(
                    (path, stmt["line"], stmt["file_hash"], stmt["module"], names,
                     {t.file for t in targets})
                )  # fmt: skip
        conn.execute("DELETE FROM imports")
        conn.executemany(
            "INSERT INTO imports (repo_id, file_path, module, resolved_file_path, file_hash)"
            " VALUES (?,?,?,?,?)",
            import_rows,
        )

        # 2. Call sites -> symbols.
        symbols = [
            Sym(r["id"], r["file_path"], r["name"], r["qualified_name"], r["kind"], r["owner"],
                r["language"])
            for r in conn.execute(
                "SELECT id, file_path, name, qualified_name, kind, owner, language FROM symbols"
            )
        ]  # fmt: skip
        resolver = CallResolver(symbols, import_targets, bindings)
        by_id = resolver.by_id
        ref_rows: list[tuple] = []
        for cs in conn.execute(
            "SELECT file_path, from_symbol_id, to_name, receiver, kind, line, file_hash"
            " FROM call_sites"
        ).fetchall():
            from_sym = by_id.get(cs["from_symbol_id"]) if cs["from_symbol_id"] else None
            for target, confidence in resolver.resolve(
                cs["to_name"], cs["receiver"], cs["kind"], from_sym, cs["file_path"]
            ):
                ref_rows.append(
                    (repo_id, cs["from_symbol_id"], cs["to_name"], target.id if target else None,
                     cs["kind"], cs["line"], cs["file_path"], confidence, cs["file_hash"])
                )  # fmt: skip

        # 3. `from x import name` also counts as a (high confidence) use of `name`.
        for path, line, file_hash, _module, names, target_files in named_imports:
            for name in names:
                for sym in resolver.by_name.get(name, ()):
                    if sym.file in target_files:
                        ref_rows.append(
                            (repo_id, None, name, sym.id, "import", line, path, "high", file_hash)
                        )

        conn.execute("DELETE FROM symbol_references")
        conn.executemany(
            "INSERT INTO symbol_references (repo_id, from_symbol_id, to_name, to_symbol_id, kind,"
            " line, file_path, confidence, file_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            ref_rows,
        )
