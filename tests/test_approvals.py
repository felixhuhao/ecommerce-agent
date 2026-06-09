import pytest

from ecommerce_agent.approvals import approval_card, execute_with_retry


def test_approval_card_parses_operation_detail_and_keeps_identity_fields() -> None:
    card = approval_card(
        {
            "approvalId": "a1",
            "toolName": "purchase_order_create",
            "operationType": "create",
            "status": "pending",
            "operationDetail": '{"title":"Create PO","financialImpact":{"totalCost":42}}',
        }
    )

    assert card["title"] == "Create PO"
    assert card["financialImpact"] == {"totalCost": 42}
    assert card["approvalId"] == "a1"
    assert card["toolName"] == "purchase_order_create"
    assert card["status"] == "pending"


@pytest.mark.asyncio
async def test_execute_with_retry_retries_java_marked_transient_failure() -> None:
    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, approval_id: str) -> dict:
            self.calls += 1
            if self.calls == 1:
                return {
                    "approvalId": approval_id,
                    "status": "approved",
                    "retryable": True,
                }
            return {
                "approvalId": approval_id,
                "status": "consumed",
                "executionResult": {"ok": True},
            }

    client = FlakyClient()

    result = await execute_with_retry(client, "a1", delay_seconds=0)

    assert client.calls == 2
    assert result["status"] == "consumed"
