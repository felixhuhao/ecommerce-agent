import os

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.config import Settings
from tests.integration.helpers import skip_unless_spring_mcp_is_running


@pytest.mark.integration
@pytest.mark.live
async def test_live_chat_stream_can_call_spring_mcp_tools() -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live LLM smoke test")

    settings = Settings(
        mcp_request_timeout_seconds=10,
        mcp_sse_read_timeout_seconds=60,
    )
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY is required for the live LLM smoke test")

    await skip_unless_spring_mcp_is_running(settings)

    app = create_app(settings=settings)
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={"message": "Check 手机 inventory. Keep the answer short."},
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: done" in body
    assert "event: error" not in body
    assert ("event: tool" in body) or ("event: token" in body)
