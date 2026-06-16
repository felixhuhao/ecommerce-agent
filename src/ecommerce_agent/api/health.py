from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ecommerce_agent.config import Settings

logger = logging.getLogger(__name__)


async def probe_mongo(thread_store: Any) -> dict[str, str]:
    try:
        ok = await asyncio.wait_for(thread_store.ping(), timeout=1.0)
    except Exception:
        logger.warning("Mongo health probe failed", exc_info=True)
        return {"status": "unavailable"}
    return {"status": "ok" if ok else "unavailable"}


def probe_sandbox(settings: Settings) -> dict[str, str]:
    backend = settings.sandbox_backend.strip().lower()
    if backend == "remote":
        if not settings.sandbox_executor_url:
            return {"status": "unconfigured", "backend": "remote"}
        try:
            response = httpx.get(
                f"{settings.sandbox_executor_url.rstrip('/')}/health",
                timeout=1.0,
            )
            response.raise_for_status()
        except Exception:
            logger.warning("Remote sandbox health probe failed", exc_info=True)
            return {"status": "unavailable", "backend": "remote"}
        return {"status": "ok", "backend": "remote"}
    if backend != "docker":
        return {"status": "unconfigured", "backend": backend}

    client = None
    try:
        import docker

        client = docker.from_env()
        client.ping()
    except Exception:
        logger.warning("Sandbox health probe failed", exc_info=True)
        return {"status": "unavailable"}
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return {"status": "ok", "backend": "docker"}


def probe_model(settings: Settings) -> dict[str, str]:
    if settings.llm_api_key and settings.llm_base_url:
        return {"status": "ok", "model": settings.llm_model, "checked": "config-only"}
    return {"status": "unconfigured"}


async def health_components(app_state: Any) -> dict[str, Any]:
    settings: Settings = app_state.settings
    sandbox = await asyncio.to_thread(probe_sandbox, settings)
    return {
        "mongo": await probe_mongo(app_state.thread_store),
        "sandbox": sandbox,
        "model": probe_model(settings),
    }
