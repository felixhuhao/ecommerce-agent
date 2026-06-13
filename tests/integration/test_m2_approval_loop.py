import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.approvals import extract_approval_id
from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import SPRING_SERVER_NAME, build_mcp_client
from ecommerce_agent.sessions.store import MongoSessionStore
from tests.integration.helpers import (
    skip_on_spring_mcp_auth_error,
    skip_unless_mongo_is_running,
    skip_unless_spring_mcp_is_running,
)

pytestmark = [pytest.mark.integration]


def _skip_unless_enabled() -> None:
    if os.getenv("RUN_M2_APPROVAL_INTEGRATION") != "1":
        pytest.skip(
            "Set RUN_M2_APPROVAL_INTEGRATION=1 with Spring MCP, MySQL, and MongoDB "
            "running to exercise the real approval loop"
        )


def _settings() -> Settings:
    return Settings(
        llm_api_key="",
        mcp_request_timeout_seconds=10,
        mcp_sse_read_timeout_seconds=60,
    )


async def _request_approval(
    settings: Settings,
    *,
    session_id: str,
    tool_name: str,
    operation_type: str,
    operation_params: dict,
) -> str:
    client = build_mcp_client(
        settings,
        user_id=settings.spring_mcp_user_id,
        session_id=session_id,
    )
    try:
        tools = await client.get_tools(server_name=SPRING_SERVER_NAME)
        request_approval = next(tool for tool in tools if tool.name == "request_approval")
        result = await request_approval.ainvoke(
            {
                "toolName": tool_name,
                "operationType": operation_type,
                "operationParams": operation_params,
            }
        )
    except StopIteration:
        pytest.fail("Spring MCP server did not expose request_approval")
    except Exception as exc:
        skip_on_spring_mcp_auth_error(exc, settings)
        if "Unknown column 'execution_result'" in str(exc):
            pytest.fail(
                "Spring MCP is running M2 approval code, but the live MySQL "
                "approval_record table is missing execution_result/executed_at. "
                "Apply the Java companion schema migration before running this test."
            )
        raise

    approval_id = extract_approval_id(result)
    assert approval_id, f"request_approval result did not contain approval id: {result!r}"
    return approval_id


async def _call_spring_tool(
    settings: Settings,
    *,
    session_id: str,
    tool_name: str,
    payload: dict,
):
    client = build_mcp_client(
        settings,
        user_id=settings.spring_mcp_user_id,
        session_id=session_id,
    )
    try:
        tools = await client.get_tools(server_name=SPRING_SERVER_NAME)
        tool = next(tool for tool in tools if tool.name == tool_name)
        return await tool.ainvoke(payload)
    except StopIteration:
        pytest.fail(f"Spring MCP server did not expose {tool_name}")
    except Exception as exc:
        skip_on_spring_mcp_auth_error(exc, settings)
        raise


def _decode_jsonish(value):
    if isinstance(value, str):
        try:
            return _decode_jsonish(json.loads(value))
        except json.JSONDecodeError:
            return value
    if isinstance(value, dict):
        return {key: _decode_jsonish(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_decode_jsonish(item) for item in value]
    return value


def _find_key(value, *keys: str):
    value = _decode_jsonish(value)
    if isinstance(value, dict):
        for key in keys:
            if value.get(key) is not None:
                return value[key]
        for nested in value.values():
            found = _find_key(nested, *keys)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_key(item, *keys)
            if found is not None:
                return found
    return None


async def _pending_order_id(settings: Settings, *, session_id: str) -> int:
    result = await _call_spring_tool(
        settings,
        session_id=session_id,
        tool_name="order_query",
        payload={"status": "pending", "limit": 1},
    )
    order_id = _find_key(result, "orderId", "order_id")
    if order_id is None:
        pytest.skip("No pending order available to exercise stale approval invalidation")
    return int(order_id)


async def _preflight(settings: Settings) -> None:
    _skip_unless_enabled()
    await skip_unless_spring_mcp_is_running(settings)
    await skip_unless_mongo_is_running(settings)


async def _create_session_record(settings: Settings, session_id: str) -> None:
    store = MongoSessionStore.from_settings(settings)
    try:
        await store.create(session_id, owner_id=str(settings.spring_mcp_user_id))
    finally:
        store.close()


@pytest.mark.asyncio
async def test_real_approval_loop_approve_execute_reload_and_stream() -> None:
    settings = _settings()
    await _preflight(settings)
    session_id = f"itest-{uuid.uuid4().hex}"
    approval_id = await _request_approval(
        settings,
        session_id=session_id,
        tool_name="purchase_order_create",
        operation_type="create",
        operation_params={
            "supplierId": 1,
            "items": [{"productId": 2, "quantity": 1}],
        },
    )
    await _create_session_record(settings, session_id)

    app = create_app(settings=settings)
    with TestClient(app) as api:
        response = api.post(f"/api/sessions/{session_id}/approvals/{approval_id}/approve")
        replay = api.post(f"/api/sessions/{session_id}/approvals/{approval_id}/approve")
        thread = api.get(f"/api/sessions/{session_id}/thread").json()

    assert response.status_code == 200
    assert response.json()["execution"]["status"] == "consumed"
    assert response.json()["execution"]["executionResult"]["status"] == "created"
    assert replay.status_code == 200

    messages = thread["messages"]
    assert [message["type"] for message in messages].count("execution_result") == 1
    assert any(
        message["type"] == "execution_result"
        and message["approval_id"] == approval_id
        and message["result"]["status"] == "created"
        for message in messages
    )


@pytest.mark.asyncio
async def test_real_approval_loop_reject_appends_status() -> None:
    settings = _settings()
    await _preflight(settings)
    session_id = f"itest-{uuid.uuid4().hex}"
    approval_id = await _request_approval(
        settings,
        session_id=session_id,
        tool_name="purchase_order_create",
        operation_type="create",
        operation_params={
            "supplierId": 1,
            "items": [{"productId": 2, "quantity": 1}],
        },
    )
    await _create_session_record(settings, session_id)

    app = create_app(settings=settings)
    with TestClient(app) as api:
        response = api.post(
            f"/api/sessions/{session_id}/approvals/{approval_id}/reject",
            json={"reason": "integration reject path"},
        )
        thread = api.get(f"/api/sessions/{session_id}/thread").json()

    assert response.status_code == 200
    assert response.json()["approval"]["status"] == "rejected"
    assert thread["messages"][-1]["type"] == "approval_status"
    assert thread["messages"][-1]["status"] == "rejected"
    assert thread["messages"][-1]["reason"] == "integration reject path"


@pytest.mark.asyncio
async def test_real_approval_loop_invalidated_precondition_appends_status() -> None:
    settings = _settings()
    await _preflight(settings)
    session_id = f"itest-{uuid.uuid4().hex}"
    order_id = await _pending_order_id(settings, session_id=session_id)
    first_approval_id = await _request_approval(
        settings,
        session_id=session_id,
        tool_name="order_update",
        operation_type="update",
        operation_params={"orderId": order_id, "newStatus": "cancelled"},
    )
    second_approval_id = await _request_approval(
        settings,
        session_id=session_id,
        tool_name="order_update",
        operation_type="update",
        operation_params={"orderId": order_id, "newStatus": "cancelled"},
    )
    await _create_session_record(settings, session_id)

    app = create_app(settings=settings)
    with TestClient(app) as api:
        first_response = api.post(
            f"/api/sessions/{session_id}/approvals/{first_approval_id}/approve"
        )
        response = api.post(
            f"/api/sessions/{session_id}/approvals/{second_approval_id}/approve"
        )
        thread = api.get(f"/api/sessions/{session_id}/thread").json()

    assert first_response.status_code == 200
    assert first_response.json()["execution"]["status"] == "consumed"
    assert response.status_code == 200
    execution = response.json()["execution"]
    assert execution["status"] == "invalidated", execution
    assert thread["messages"][-1]["type"] == "approval_status"
    assert thread["messages"][-1]["status"] == "invalidated"
    assert "fresh approval" in thread["messages"][-1]["reason"]
