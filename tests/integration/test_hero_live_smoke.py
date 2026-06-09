import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import VIZ_TOOLS
from tests.integration.helpers import (
    skip_unless_docker_available,
    skip_unless_spring_mcp_is_running,
)

HERO = (
    "Which categories are trending up or down over the last 6 months, forecast next "
    "month's sales, and chart the result. If product_query does not return a product "
    "ID from an order item, bucket it as unknown and continue. Keep the summary short."
)

_LIVE_SMOKE_TIMEOUT_SECONDS = 180


def _wait_for_agent_answer(client: TestClient, session_id: str) -> dict:
    import time

    deadline = time.monotonic() + _LIVE_SMOKE_TIMEOUT_SECONDS
    last_thread: dict | None = None
    while time.monotonic() < deadline:
        last_thread = client.get(f"/api/sessions/{session_id}/thread").json()
        if any(message["type"] == "agent_answer" for message in last_thread["messages"]):
            return last_thread
        time.sleep(0.25)
    assert last_thread is not None
    return last_thread


@contextmanager
def _fail_after(seconds: int) -> Iterator[None]:
    def raise_timeout(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"live hero smoke exceeded {seconds}s")

    previous_handler = signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


@pytest.mark.integration
@pytest.mark.live
async def test_hero_flow_single_run() -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live hero smoke")
    skip_unless_docker_available()

    settings = Settings(mcp_request_timeout_seconds=15, mcp_sse_read_timeout_seconds=120)
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    import docker

    try:
        docker.from_env().images.get(settings.sandbox_image)
    except Exception:
        pytest.skip(f"sandbox image {settings.sandbox_image} is not built")

    await skip_unless_spring_mcp_is_running(settings)

    app = create_app(settings=settings)
    try:
        with _fail_after(_LIVE_SMOKE_TIMEOUT_SECONDS), TestClient(app) as client:
            session_id = client.post("/api/sessions").json()["session_id"]
            response = client.post(f"/api/sessions/{session_id}/messages", json={"message": HERO})
            thread = _wait_for_agent_answer(client, session_id)
    except TimeoutError as exc:
        pytest.fail(str(exc))

    assert response.status_code == 202
    assert any(message["type"] == "agent_answer" for message in thread["messages"])
    assert app.state.last_trace is not None
    tools = set(app.state.last_trace.tool_names())
    assert "execute" in tools or bool(tools & VIZ_TOOLS)
