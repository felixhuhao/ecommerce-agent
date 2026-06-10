from __future__ import annotations

import asyncio
from typing import Any

from ecommerce_agent.config import Settings


async def probe_mongo(thread_store: Any) -> dict[str, str]:
    try:
        ok = await asyncio.wait_for(thread_store.ping(), timeout=1.0)
    except Exception as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok" if ok else "unavailable"}


def probe_sandbox(settings: Settings) -> dict[str, str]:
    client = None
    try:
        import docker

        client = docker.from_env()
        client.ping()
    except Exception as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return {"status": "ok"}


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
