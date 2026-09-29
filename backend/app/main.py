"""FastAPI application entry point.

Run with ``uvicorn --factory app.main:create_app`` (or ``make dev``). On startup we:

1. install the network guard (outbound connections to anything but
   localhost are blocked),
2. start Foundry Local and load the embedding model in the background, so
   the UI is responsive immediately and shows status while models warm up.

If the frontend has been built (``frontend/dist``), it is served from the
same origin, so a single process is all a user needs to run.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BACKEND_DIR, get_config
from app.privacy import guard
from app.routes import chat, repos, system
from app.state import AppState

log = logging.getLogger("lodestar")
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"


def _warm_up(state: AppState) -> None:
    """Start Foundry Local and load the embedder. Failures are reported via /api/health."""
    try:
        state.foundry.start()
    except Exception as exc:
        log.warning("Foundry Local is not available: %s", exc)
    try:
        _ = state.embedder
    except Exception as exc:
        log.warning("Embedding model is not available: %s", exc)


def create_app(state: AppState | None = None, warm_up: bool = True) -> FastAPI:
    config = state.config if state else get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if config.block_external_network:
            guard.install(block=True)
        if warm_up:
            asyncio.get_running_loop().run_in_executor(None, _warm_up, app.state.lodestar)
        yield
        app.state.lodestar.foundry.stop()

    app = FastAPI(title="Lodestar", version="0.1.0", lifespan=lifespan)
    app.state.lodestar = state or AppState(config)
    app.include_router(system.router)
    app.include_router(repos.router)
    app.include_router(chat.router)

    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            file = FRONTEND_DIST / path
            if path and file.is_file() and FRONTEND_DIST in file.resolve().parents:
                return FileResponse(file)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


def run() -> None:
    import uvicorn

    config = get_config()
    uvicorn.run("app.main:create_app", factory=True, host=config.host, port=config.port)


if __name__ == "__main__":
    run()
