"""Tech debt API: the board, its exports, and the lazily computed topic groups."""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.analysis.debt import (
    LABEL_PROMPT,
    clean_label,
    cluster_vectors,
    counters,
    export_csv,
    export_markdown,
    group_items,
    keyword_label,
    list_items,
    parse_tags,
    signature,
)
from app.routes.insights import repo_or_404
from app.state import AppState, get_state

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/repos/{repo_id}")

GROUPINGS = {"tag", "folder", "topic", "age"}
MAX_LLM_LABELS = 12  # only the biggest topics are labelled by the model; the rest use keywords


def _embedder_name(state: AppState) -> str:
    return state.embedder_status()["name"] or "unloaded"


def _saved_topics(conn, expected: str) -> dict[int, tuple[int, str]] | None:
    """{item id: (cluster, label)} if the cache matches the current items, else None."""
    row = conn.execute("SELECT signature FROM debt_topic_state WHERE id = 1").fetchone()
    if row is None or row["signature"] != expected:
        return None
    labels = {
        r["cluster_id"]: r["label"] for r in conn.execute("SELECT * FROM debt_cluster_labels")
    }
    return {
        r["item_id"]: (r["cluster_id"], labels.get(r["cluster_id"], "Uncategorized"))
        for r in conn.execute("SELECT item_id, cluster_id FROM debt_clusters")
    }


@router.get("/debt")
async def debt_board(
    repo_id: str,
    tag: str = "",
    q: str = "",
    folder: str = "",
    older_than_days: int = 0,
    group_by: str = "tag",
    state: AppState = Depends(get_state),
) -> dict:
    """Items grouped by tag, folder, age or topic, plus summary counters."""
    repo_or_404(state, repo_id)
    if group_by not in GROUPINGS:
        raise HTTPException(422, f"group_by must be one of {', '.join(sorted(GROUPINGS))}.")
    tags = parse_tags(state.config.debt_tags)

    def read():
        with state.db.repo(repo_id) as conn:
            everything = list_items(conn)
            items = list_items(conn, tag, q, folder, older_than_days)
            topics = None
            if group_by == "topic":
                topics = _saved_topics(conn, signature(everything, _embedder_name(state)))
        return everything, items, topics

    everything, items, topics = await asyncio.to_thread(read)
    pending = False
    error = None
    if group_by == "topic":
        job = state.debt_jobs.get(repo_id)
        current = signature(everything, "")  # identifies the comments, whatever the embedder
        if topics is None and everything:
            # A failed job is not retried on every poll: only when the comments changed
            # or the user asks again with "refresh".
            failed_for_these = job and job["status"] == "error" and job["items"] == current
            if job is None or (job["status"] in ("done", "error") and not failed_for_these):
                _start_topic_job(state, repo_id, current)
                job = state.debt_jobs[repo_id]
            pending = job["status"] == "running"
        error = job["error"] if job and job["status"] == "error" else None
    return {
        "group_by": group_by,
        "groups": group_items(items, group_by, tags, topics),
        "counters": counters(everything, tags),
        "shown": len(items),
        "topics_pending": pending,
        "topics_error": error,
    }


@router.post("/debt/topics/refresh", status_code=202)
async def refresh_topics(repo_id: str, state: AppState = Depends(get_state)) -> dict:
    """Recompute the topic groups (they are cached until the comments change)."""
    repo_or_404(state, repo_id)
    if (state.debt_jobs.get(repo_id) or {}).get("status") != "running":
        with state.db.repo(repo_id) as conn:
            conn.execute("DELETE FROM debt_topic_state")
            current = signature(list_items(conn), "")
        _start_topic_job(state, repo_id, current)
    return {"status": state.debt_jobs[repo_id]["status"]}


@router.get("/debt/export", response_class=PlainTextResponse)
async def export_debt(
    repo_id: str,
    format: str = "markdown",
    tag: str = "",
    q: str = "",
    folder: str = "",
    older_than_days: int = 0,
    state: AppState = Depends(get_state),
) -> PlainTextResponse:
    repo_or_404(state, repo_id)
    if format not in {"markdown", "csv"}:
        raise HTTPException(422, "format must be markdown or csv.")

    def read():
        with state.db.repo(repo_id) as conn:
            return list_items(conn, tag, q, folder, older_than_days)

    items = await asyncio.to_thread(read)
    if format == "csv":
        body, media, ext = export_csv(items), "text/csv", "csv"
    else:
        body, media, ext = export_markdown(items), "text/markdown", "md"
    return PlainTextResponse(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="tech-debt.{ext}"'},
    )


# ---- topic clustering (background) -----------------------------------------------------------


def _start_topic_job(state: AppState, repo_id: str, items_signature: str) -> None:
    state.debt_jobs[repo_id] = {
        "status": "running",
        "error": None,
        "items": items_signature,  # which comments this job is for
        "started_at": time.time(),
    }
    asyncio.get_running_loop().create_task(_compute_topics(state, repo_id))


async def _compute_topics(state: AppState, repo_id: str) -> None:
    job = state.debt_jobs[repo_id]
    try:

        def read():
            with state.db.repo(repo_id) as conn:
                return list_items(conn)

        items = await asyncio.to_thread(read)
        texts = [f"{i['tag']}: {i['text']}".strip() or i["file_path"] for i in items]
        embedder = await asyncio.to_thread(lambda: state.embedder)  # may load the model
        vectors = await asyncio.to_thread(
            _embed, embedder, texts, state.config.embedding_batch_size
        )
        clusters = await asyncio.to_thread(
            cluster_vectors, vectors, state.config.debt_topic_threshold
        )

        members: dict[int, list[int]] = {}
        for index, cluster in enumerate(clusters):
            members.setdefault(cluster, []).append(index)
        labels: dict[int, str] = {}
        by_size = sorted(members, key=lambda c: -len(members[c]))
        model = state.chat_model()
        for rank, cluster in enumerate(by_size):
            cluster_texts = [items[i]["text"] or items[i]["tag"] for i in members[cluster]]
            label = None
            if len(cluster_texts) >= 2 and rank < MAX_LLM_LABELS:
                label = await _model_label(state, model, cluster_texts)
            labels[cluster] = label or keyword_label(cluster_texts)

        def save():
            with state.db.repo(repo_id) as conn:
                conn.execute("DELETE FROM debt_clusters")
                conn.execute("DELETE FROM debt_cluster_labels")
                conn.executemany(
                    "INSERT INTO debt_clusters (item_id, cluster_id) VALUES (?, ?)",
                    [(items[i]["id"], c) for i, c in enumerate(clusters)],
                )
                conn.executemany(
                    "INSERT INTO debt_cluster_labels (cluster_id, label, size) VALUES (?, ?, ?)",
                    [(c, labels[c], len(members[c])) for c in members],
                )
                conn.execute(
                    "INSERT OR REPLACE INTO debt_topic_state (id, signature, computed_at)"
                    " VALUES (1, ?, ?)",
                    (signature(items, _embedder_name(state)), time.time()),
                )

        await asyncio.to_thread(save)
        job["status"] = "done"
    except Exception as exc:
        log.warning("Topic grouping failed: %s", exc)
        job["status"], job["error"] = "error", f"Could not group by topic: {exc}"


def _embed(embedder, texts: list[str], batch: int) -> np.ndarray:
    parts = [embedder.embed_documents(texts[i : i + batch]) for i in range(0, len(texts), batch)]
    return np.vstack(parts) if parts else np.zeros((0, 1), dtype=np.float32)


async def _model_label(state: AppState, model: str, texts: list[str]) -> str | None:
    """Ask the chat model for a short topic label. Any failure just means "use keywords"."""
    listing = "\n".join(f"- {t[:120]}" for t in texts[:8])
    try:
        raw = await asyncio.wait_for(
            state.llm.complete(
                [{"role": "user", "content": LABEL_PROMPT.format(items=listing)}], model
            ),
            timeout=90,
        )
    except Exception:
        return None
    return clean_label(raw)
