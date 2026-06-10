from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

ORDERS_RAW_PATH = "/workspace/orders_raw.json"
PRODUCTS_RAW_PATH = "/workspace/products_raw.json"
STAGE_SALES_ANALYSIS_TOOL_NAME = "stage_sales_analysis_inputs"


class StageSalesAnalysisInput(BaseModel):
    """Fetch and stage commerce rows for sandbox analysis."""

    order_limit: int = Field(default=100, ge=1, le=500)
    product_limit: int = Field(default=100, ge=1, le=500)


def _tool_by_name(tools: list[BaseTool], name: str) -> BaseTool:
    for tool in tools:
        if tool.name == name:
            return tool
    raise ValueError(f"required tool is missing: {name}")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump())

    content = getattr(value, "content", None)
    if content is not None:
        return _jsonable(content)

    return repr(value)


def _records_from_payload(value: Any) -> list[Any]:
    payload = _jsonable(value)
    if (
        isinstance(payload, list)
        and len(payload) == 1
        and isinstance(payload[0], dict)
        and isinstance(payload[0].get("text"), str)
    ):
        try:
            decoded = json.loads(payload[0]["text"])
        except json.JSONDecodeError:
            decoded = payload
        else:
            payload = decoded

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def _encode_json(value: Any) -> bytes:
    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def _upload_files(backend: Any, files: list[tuple[str, bytes]]) -> None:
    responses = await asyncio.to_thread(backend.upload_files, files)
    failures = [response for response in responses if getattr(response, "error", None)]
    if failures:
        details = ", ".join(f"{response.path}:{response.error}" for response in failures)
        raise RuntimeError(f"failed to stage sandbox files: {details}")


def build_sales_analysis_staging_tool(
    *,
    spring_read_tools: list[BaseTool],
    backend: Any,
) -> BaseTool:
    """Build a deterministic data-staging tool for analyst forecast workflows."""
    order_query = _tool_by_name(spring_read_tools, "order_query")
    product_query = _tool_by_name(spring_read_tools, "product_query")

    async def stage_sales_analysis_inputs(
        order_limit: int = 100,
        product_limit: int = 100,
    ) -> dict[str, Any]:
        """Fetch order/product rows and write raw payloads into /workspace."""
        orders, products = await asyncio.gather(
            order_query.ainvoke({"limit": order_limit}),
            product_query.ainvoke({"limit": product_limit}),
        )
        await _upload_files(
            backend,
            [
                (ORDERS_RAW_PATH, _encode_json(orders)),
                (PRODUCTS_RAW_PATH, _encode_json(products)),
            ],
        )
        return {
            "orders_path": ORDERS_RAW_PATH,
            "products_path": PRODUCTS_RAW_PATH,
            "order_count": len(_records_from_payload(orders)),
            "product_count": len(_records_from_payload(products)),
            "order_limit": order_limit,
            "product_limit": product_limit,
            "note": (
                "Raw payloads were staged directly into the sandbox. Use these paths with "
                "ecommerce_analysis.load_orders_df; do not call write_file for these payloads."
            ),
        }

    return StructuredTool.from_function(
        coroutine=stage_sales_analysis_inputs,
        name=STAGE_SALES_ANALYSIS_TOOL_NAME,
        description=(
            "Fetch order_query and product_query results, write the raw JSON payloads directly "
            "to /workspace/orders_raw.json and /workspace/products_raw.json, and return compact "
            "file-path metadata for sandbox analysis. Use this instead of copying raw rows "
            "through write_file."
        ),
        args_schema=StageSalesAnalysisInput,
    )

