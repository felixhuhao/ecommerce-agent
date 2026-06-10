from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


def mount_spa(app: FastAPI, dist_dir: str) -> None:
    """Mount the built SPA only when the frontend has been built."""
    dist = Path(dist_dir)
    index = dist / "index.html"
    if not dist.is_dir() or not index.is_file():
        logger.warning("frontend dist %s not found; skipping SPA mount", dist_dir)
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str, request: Request) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/") or full_path.startswith("health"):
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(index)
