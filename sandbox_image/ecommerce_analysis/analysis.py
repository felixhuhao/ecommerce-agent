"""Pre-baked commerce analysis helpers that run inside the sandbox.

These deterministic building blocks let the agent compose reliable analysis
with small glue code instead of authoring fragile pandas from scratch. They
never fetch data or touch the network: the agent fetches via MCP, writes a file
into /workspace, and these helpers parse it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REALIZED_STATUSES = ("paid", "shipped", "completed")
REQUIRED_COLUMNS = ("created_at", "status", "category", "amount")


def load_orders_df(path: str) -> pd.DataFrame:
    """Parse an order line-item file the agent wrote into /workspace.

    Expects JSON (list of records) or CSV with at least:
    created_at (ISO datetime), status (str), category (str), amount (numeric).
    """
    order_path = Path(path)
    if not order_path.exists():
        raise FileNotFoundError(f"order file not found: {path}")

    if order_path.suffix.lower() == ".csv":
        df = pd.read_csv(order_path)
    else:
        df = pd.DataFrame.from_records(json.loads(order_path.read_text()))

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
