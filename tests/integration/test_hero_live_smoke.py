import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.config import Settings
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
            with client.stream("POST", "/api/chat/stream", json={"message": HERO}) as response:
                body = "".join(response.iter_text())
    except TimeoutError as exc:
        pytest.fail(str(exc))

    assert response.status_code == 200
    assert "event: done" in body
    assert "event: error" not in body
    assert ("execute" in body) or ("generate_visualization" in body)
