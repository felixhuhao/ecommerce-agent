from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ecommerce_agent.tools.staging import (
    ORDERS_RAW_PATH,
    PRODUCTS_RAW_PATH,
    _encode_json,
    _records_from_payload,
    _tool_by_name,
    _upload_files,
)

SALES_FORECAST_TOOL_NAME = "sales_forecast"
MAX_FORECAST_PERIODS = 3


class SalesForecastInput(BaseModel):
    """Return chart-ready monthly sales history and illustrative forecast rows."""

    sku: str | None = Field(
        default=None,
        description="Optional SKU to forecast. Omit for category-level business forecast.",
    )
    product_id: int | None = Field(
        default=None,
        ge=1,
        description="Optional product id to forecast. Takes precedence over sku.",
    )
    label: str | None = Field(
        default=None,
        description="Short display label for a product forecast.",
    )
    periods: int = Field(
        default=1,
        ge=1,
        le=MAX_FORECAST_PERIODS,
        description="Number of future months to forecast.",
    )
    order_limit: int = Field(default=100, ge=1, le=500)
    product_limit: int = Field(default=100, ge=1, le=500)


def _forecast_command(params: dict[str, Any]) -> str:
    params_json = json.dumps(params, ensure_ascii=False)
    return f"""python3 <<'PY'
import json
from ecommerce_analysis import (
    load_orders_df,
    monthly_sales_by_category,
    monthly_sales_by_product,
    simple_forecast,
    validate_forecast_result,
)

params = json.loads({params_json!r})
orders_df = load_orders_df({ORDERS_RAW_PATH!r}, {PRODUCTS_RAW_PATH!r})
if params.get("product_id") is not None or params.get("sku"):
    monthly_df = monthly_sales_by_product(
        orders_df,
        product_id=params.get("product_id"),
        sku=params.get("sku"),
        label=params.get("label") or params.get("sku") or f"product {{params.get('product_id')}}",
    )
else:
    monthly_df = monthly_sales_by_category(orders_df)

forecast_df = simple_forecast(monthly_df, periods=int(params["periods"]))
try:
    validate_forecast_result(forecast_df)
except ValueError as exc:
    print(json.dumps({{"status": "no_data", "reason": str(exc)}}, ensure_ascii=False))
    raise SystemExit(0)

forecast_df = forecast_df.sort_values(["category", "month", "is_forecast"])
rows = [
    {{
        "time": row["month"].strftime("%Y-%m"),
        "value": round(float(row["sales"]), 2),
        "group": f"{{row['category']}} {{'forecast' if row['is_forecast'] else 'actual'}}",
        "is_forecast": bool(row["is_forecast"]),
    }}
    for row in forecast_df.to_dict("records")
]
summary = {{
    "actual_months": sorted(
        forecast_df.loc[~forecast_df["is_forecast"], "month"].dt.strftime("%Y-%m").unique().tolist()
    ),
    "forecast_months": sorted(
        forecast_df.loc[forecast_df["is_forecast"], "month"].dt.strftime("%Y-%m").unique().tolist()
    ),
    "subjects": sorted(forecast_df["category"].astype(str).unique().tolist()),
}}
print(json.dumps({{"status": "ok", "rows": rows, "summary": summary}}, ensure_ascii=False))
PY"""


def _parse_forecast_output(output: str) -> dict[str, Any]:
    for line in reversed((output or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict) and decoded.get("status") in {"ok", "no_data"}:
            return decoded
    return {"status": "error", "reason": "forecast output was not valid JSON"}


def _forecast_result(
    *,
    params: dict[str, Any],
    staged_counts: dict[str, int],
    output: dict[str, Any],
) -> dict[str, Any]:
    rows = output.get("rows") if isinstance(output.get("rows"), list) else []
    summary = output.get("summary") if isinstance(output.get("summary"), dict) else {}
    return {
        "kind": "analytics_result",
        "metric": SALES_FORECAST_TOOL_NAME,
        "source": "sandbox",
        "status": output.get("status", "error"),
        "filters": params,
        "rows": rows,
        "summary": {
            **summary,
            **staged_counts,
            "row_count": len(rows),
            "reason": output.get("reason"),
            "currency": "USD",
        },
        "provenance": {
            "system": "sandbox",
            "backing_tools": ["order_query", "product_query", "execute"],
            "helper": "ecommerce_analysis.simple_forecast",
        },
    }


def build_sales_forecast_tool(
    *,
    spring_read_tools: list[BaseTool],
    backend: Any,
) -> BaseTool:
    order_query = _tool_by_name(spring_read_tools, "order_query")
    product_query = _tool_by_name(spring_read_tools, "product_query")

    async def sales_forecast(
        sku: str | None = None,
        product_id: int | None = None,
        label: str | None = None,
        periods: int = 1,
        order_limit: int = 100,
        product_limit: int = 100,
    ) -> dict[str, Any]:
        """Return monthly history and illustrative forecast rows for sales."""
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
        params = {
            "sku": sku.strip() if isinstance(sku, str) and sku.strip() else None,
            "product_id": product_id,
            "label": label.strip() if isinstance(label, str) and label.strip() else None,
            "periods": periods,
        }
        response = await asyncio.to_thread(
            backend.execute,
            _forecast_command(params),
            timeout=30,
        )
        output = _parse_forecast_output(getattr(response, "output", ""))
        if getattr(response, "exit_code", 0) not in (0, None) and output["status"] != "no_data":
            output = {
                "status": "error",
                "reason": (getattr(response, "output", "") or "forecast execution failed")[-500:],
            }
        return _forecast_result(
            params=params,
            staged_counts={
                "order_count": len(_records_from_payload(orders)),
                "product_count": len(_records_from_payload(products)),
            },
            output=output,
        )

    return StructuredTool.from_function(
        coroutine=sales_forecast,
        name=SALES_FORECAST_TOOL_NAME,
        description=(
            "Return chart-ready monthly sales history and an illustrative forecast from "
            "staged order/product data. Use for sales forecasts and forecast charts; "
            "if status is no_data, explain that there is no forecastable history and do not chart."
        ),
        args_schema=SalesForecastInput,
    )
