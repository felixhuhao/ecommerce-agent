import os

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.auth.dependencies import current_actor
from ecommerce_agent.auth.models import Actor, Role
from ecommerce_agent.config import Settings
from tests.integration.helpers import skip_unless_spring_mcp_is_running

OPERATOR = Actor(
    user_id="live-op", username="live-op", role=Role.OPERATOR, spring_user_id=1
)


def _wait_for_agent_answer(client: TestClient, session_id: str) -> dict:
    import time

    deadline = time.monotonic() + 120
    last_thread: dict | None = None
    while time.monotonic() < deadline:
        last_thread = client.get(f"/api/sessions/{session_id}/thread").json()
        if any(message["type"] == "agent_answer" for message in last_thread["messages"]):
            return last_thread
        time.sleep(0.25)
    assert last_thread is not None
    return last_thread


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
    app.dependency_overrides[current_actor] = lambda: OPERATOR
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": "Check 手机 inventory. Keep the answer short."},
        )
        thread = _wait_for_agent_answer(client, session_id)

    assert response.status_code == 202
    assert any(message["type"] == "agent_answer" for message in thread["messages"])
    assert app.state.last_trace is not None
    assert app.state.last_trace.tool_names() or app.state.last_trace.answer
