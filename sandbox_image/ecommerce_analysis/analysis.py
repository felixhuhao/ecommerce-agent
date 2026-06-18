"""Pre-baked commerce analysis helpers that run inside the sandbox.

These deterministic building blocks let the agent compose reliable analysis
with small glue code instead of authoring fragile pandas from scratch. They
never fetch data or touch the network: the agent fetches via MCP, writes a file
into /workspace, and these helpers parse it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REALIZED_STATUSES = ("paid", "shipped", "completed")
REQUIRED_COLUMNS = ("created_at", "status", "category", "amount")


def _numeric_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_json_payload(path: Path) -> Any:
    data = json.loads(path.read_text())
    if (
        isinstance(data, list)
        and len(data) == 1
        and isinstance(data[0], dict)
        and isinstance(data[0].get("text"), str)
    ):
        return json.loads(data[0]["text"])
    return data


def _product_lookup(products_path: str | None) -> dict[int, dict[str, str]]:
    if not products_path:
        return {}

    data = _read_json_payload(Path(products_path))
    if isinstance(data, dict):
        products = [data]
    elif isinstance(data, list):
        products = data
    else:
        raise ValueError("products file must contain a product object or list")

    products_by_id: dict[int, dict[str, str]] = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        product_id = product.get("productId", product.get("product_id"))
        if product_id is None:
            continue
        numeric_product_id = _numeric_id(product_id)
        if numeric_product_id is None:
            continue
        products_by_id[numeric_product_id] = {
            "category": str(product.get("category") or "unknown"),
            "sku": str(product.get("sku") or ""),
            "product_name": str(product.get("name") or product.get("productName") or ""),
        }
    return products_by_id


def _flatten_raw_orders(records: list[dict[str, Any]], products_path: str | None) -> pd.DataFrame:
    products_by_id = _product_lookup(products_path)
    rows: list[dict[str, Any]] = []
    for order in records:
        created_at = order.get("createdAt", order.get("created_at"))
        status = order.get("status")
        for item in order.get("items", []):
            product_id = item.get("productId", item.get("product_id"))
            numeric_product_id = _numeric_id(product_id)
            amount = item.get("subtotal", item.get("amount"))
            if amount is None:
                quantity = item.get("quantity")
                unit_price = item.get("unitPrice", item.get("unit_price"))
                if quantity is not None and unit_price is not None:
                    amount = float(quantity) * float(unit_price)
            product = products_by_id.get(numeric_product_id, {})
            rows.append(
                {
                    "created_at": created_at,
                    "status": status,
                    "category": product.get("category", "unknown"),
                    "product_id": numeric_product_id,
                    "sku": product.get("sku", ""),
                    "product_name": product.get("product_name", ""),
                    "amount": amount,
                }
            )
    return pd.DataFrame.from_records(rows)


def load_orders_df(path: str, products_path: str | None = None) -> pd.DataFrame:
    """Parse an order line-item file the agent wrote into /workspace.

    Accepts either:
    - flat JSON/CSV records with created_at, status, category, amount
    - raw Spring order_query JSON plus optional raw product_query JSON for category enrichment
    """
    order_path = Path(path)
    if not order_path.exists():
        raise FileNotFoundError(f"order file not found: {path}")

    if order_path.suffix.lower() == ".csv":
        df = pd.read_csv(order_path)
    else:
        data = _read_json_payload(order_path)
        if isinstance(data, dict):
            records = [data]
        elif isinstance(data, list):
            records = data
        else:
            raise ValueError("orders file must contain an order object or list")

        if records and isinstance(records[0], dict) and "items" in records[0]:
            df = _flatten_raw_orders(records, products_path)
        else:
            df = pd.DataFrame.from_records(records)

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}; got {list(df.columns)}")

    df = df.copy()
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    return df.dropna(subset=["created_at", "amount"]).reset_index(drop=True)


def monthly_sales_by_category(orders_df: pd.DataFrame) -> pd.DataFrame:
    """Return monthly realized sales per category.

    Returns columns: month (month-start Timestamp), category, sales (float).
    Realized sales = rows whose status is in REALIZED_STATUSES.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in orders_df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    df = orders_df.copy()
    df = df[df["status"].isin(REALIZED_STATUSES)]
    if df.empty:
        return pd.DataFrame(columns=["month", "category", "sales"])

    df["month"] = df["created_at"].dt.to_period("M").dt.to_timestamp()
    grouped = (
        df.groupby(["month", "category"], as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "sales"})
    )
    grouped["sales"] = grouped["sales"].astype(float)
    return grouped.sort_values(["category", "month"]).reset_index(drop=True)


def monthly_sales_by_product(
    orders_df: pd.DataFrame,
    *,
    product_id: int | None = None,
    sku: str | None = None,
    label: str | None = None,
) -> pd.DataFrame:
    """Return monthly realized sales for one product, compatible with simple_forecast.

    ``orders_df`` should come from load_orders_df(raw_orders, raw_products), which
    preserves product_id/sku fields from raw Spring payloads.
    """
    if product_id is None and not sku:
        raise ValueError("product_id or sku is required")

    missing = [column for column in REQUIRED_COLUMNS if column not in orders_df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    df = orders_df.copy()
    if product_id is not None:
        if "product_id" not in df.columns:
            raise ValueError("orders_df has no product_id column")
        df = df[df["product_id"] == int(product_id)]
    else:
        if "sku" not in df.columns:
            raise ValueError("orders_df has no sku column")
        df = df[df["sku"].astype(str) == str(sku)]

    df = df[df["status"].isin(REALIZED_STATUSES)]
    if df.empty:
        return pd.DataFrame(columns=["month", "category", "sales"])

    df["month"] = df["created_at"].dt.to_period("M").dt.to_timestamp()
    product_label = label or sku or f"product {product_id}"
    grouped = (
        df.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "sales"})
    )
    grouped["category"] = str(product_label)
    grouped["sales"] = grouped["sales"].astype(float)
    return grouped[["month", "category", "sales"]].sort_values("month").reset_index(drop=True)


def simple_forecast(monthly_df: pd.DataFrame, periods: int = 1) -> pd.DataFrame:
    """Per-category linear-trend forecast for the next `periods` months.

    Input: output of monthly_sales_by_category (month, category, sales).
    Returns category, month, sales, is_forecast. This is illustrative only:
    a simple linear fit on monthly points.
    """
    required = {"month", "category", "sales"}
    if not required.issubset(monthly_df.columns):
        raise ValueError(f"monthly_df missing columns: {required - set(monthly_df.columns)}")
    if periods < 1:
        raise ValueError("periods must be >= 1")
    if monthly_df.empty:
        return pd.DataFrame(columns=["category", "month", "sales", "is_forecast"])

    frames: list[pd.DataFrame] = []
    for category, group in monthly_df.sort_values("month").groupby("category"):
        group = group.reset_index(drop=True)
        history = group[["category", "month", "sales"]].copy()
        history["is_forecast"] = False
        frames.append(history)

        sales = group["sales"].to_numpy(dtype=float)
        last_month = group["month"].iloc[-1]
        future_months = [
            (last_month.to_period("M") + offset).to_timestamp()
            for offset in range(1, periods + 1)
        ]
        if len(group) >= 2:
            x_axis = np.arange(len(group), dtype=float)
            slope, intercept = np.polyfit(x_axis, sales, 1)
            predictions = [
                slope * (len(group) - 1 + offset) + intercept
                for offset in range(1, periods + 1)
            ]
        else:
            predictions = [float(sales[-1])] * periods

        frames.append(
            pd.DataFrame(
                {
                    "category": category,
                    "month": future_months,
                    "sales": [max(0.0, float(value)) for value in predictions],
                    "is_forecast": True,
                }
            )
        )

    return (
        pd.concat(frames, ignore_index=True)
        .sort_values(["category", "month"])
        .reset_index(drop=True)
    )


def validate_forecast_result(forecast_df: pd.DataFrame) -> None:
    """Raise ValueError if a forecast frame is unusable for charting."""
    required = {"category", "month", "sales", "is_forecast"}
    if not required.issubset(forecast_df.columns):
        raise ValueError(f"forecast missing columns: {required - set(forecast_df.columns)}")
    if forecast_df.empty:
        raise ValueError("forecast is empty")
    if not forecast_df["is_forecast"].any():
        raise ValueError("forecast has no forecast rows")
    sales = pd.to_numeric(forecast_df["sales"], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(sales).all():
        raise ValueError("forecast contains non-finite sales values")
