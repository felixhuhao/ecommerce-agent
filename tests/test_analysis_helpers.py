import json

import numpy as np
import pandas as pd
import pytest
from ecommerce_analysis import (
    load_orders_df,
    monthly_sales_by_category,
    simple_forecast,
    validate_forecast_result,
)


def _write_orders(tmp_path, records, name: str = "orders.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(records))
    return str(path)


def _orders_df(records) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records)
    df["created_at"] = pd.to_datetime(df["created_at"])
    return df


def test_load_orders_df_parses_and_types_records(tmp_path) -> None:
    records = [
        {
            "created_at": "2026-01-15T10:00:00",
            "status": "paid",
            "category": "electronics",
            "amount": "100.5",
        },
        {
            "created_at": "2026-02-20T10:00:00",
            "status": "shipped",
            "category": "electronics",
            "amount": 50,
        },
    ]
    df = load_orders_df(_write_orders(tmp_path, records))

    assert list(df.columns) >= ["created_at", "status", "category", "amount"]
    assert pd.api.types.is_datetime64_any_dtype(df["created_at"])
    assert df["amount"].sum() == pytest.approx(150.5)


def test_load_orders_df_rejects_missing_columns(tmp_path) -> None:
    records = [{"created_at": "2026-01-15T10:00:00", "amount": 100}]
    with pytest.raises(ValueError, match="missing required columns"):
        load_orders_df(_write_orders(tmp_path, records))


def test_load_orders_df_flattens_raw_spring_orders_with_product_categories(tmp_path) -> None:
    orders_path = _write_orders(
        tmp_path,
        [
            {
                "orderId": 1,
                "status": "paid",
                "createdAt": "2026-01-15T10:00:00",
                "items": [
                    {"productId": 1, "subtotal": 100.5},
                    {"productId": 99, "quantity": 2, "unitPrice": 7.5},
                ],
            }
        ],
        name="orders_raw.json",
    )
    products_path = _write_orders(
        tmp_path,
        [{"productId": 1, "category": "electronics"}],
        name="products_raw.json",
    )

    df = load_orders_df(orders_path, products_path)

    assert list(df["category"]) == ["electronics", "unknown"]
    assert list(df["amount"]) == pytest.approx([100.5, 15.0])
    assert pd.api.types.is_datetime64_any_dtype(df["created_at"])


def test_load_orders_df_accepts_langchain_content_block_files(tmp_path) -> None:
    orders = [
        {
            "orderId": 1,
            "status": "completed",
            "createdAt": "2026-02-01T10:00:00",
            "items": [{"productId": 2, "subtotal": 25}],
        }
    ]
    products = [{"productId": 2, "category": "clothing"}]
    orders_path = _write_orders(
        tmp_path,
        [{"type": "text", "text": json.dumps(orders)}],
        name="orders_block.json",
    )
    products_path = _write_orders(
        tmp_path,
        [{"type": "text", "text": json.dumps(products)}],
        name="products_block.json",
    )

    df = load_orders_df(orders_path, products_path)

    assert df[["status", "category", "amount"]].to_dict(orient="records") == [
        {"status": "completed", "category": "clothing", "amount": 25}
    ]


def test_load_orders_df_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_orders_df(str(tmp_path / "nope.json"))


def test_monthly_sales_by_category_sums_realized_only() -> None:
    df = _orders_df(
        [
            {
                "created_at": "2026-01-05",
                "status": "paid",
                "category": "electronics",
                "amount": 100,
            },
            {
                "created_at": "2026-01-25",
                "status": "shipped",
                "category": "electronics",
                "amount": 40,
            },
            {
                "created_at": "2026-01-10",
                "status": "pending",
                "category": "electronics",
                "amount": 999,
            },
            {
                "created_at": "2026-02-10",
                "status": "completed",
                "category": "clothing",
                "amount": 70,
            },
        ]
    )
    out = monthly_sales_by_category(df)

    assert set(out.columns) == {"month", "category", "sales"}
    jan_elec = out[
        (out["category"] == "electronics") & (out["month"] == pd.Timestamp("2026-01-01"))
    ]
    assert jan_elec["sales"].iloc[0] == pytest.approx(140.0)
    assert 999 not in out["sales"].values


def test_monthly_sales_by_category_empty_when_no_realized() -> None:
    df = _orders_df(
        [
            {
                "created_at": "2026-01-05",
                "status": "pending",
                "category": "electronics",
                "amount": 100,
            },
        ]
    )
    out = monthly_sales_by_category(df)
    assert out.empty
    assert set(out.columns) == {"month", "category", "sales"}


def test_simple_forecast_extends_linear_trend() -> None:
    monthly = pd.DataFrame(
        {
            "month": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
            "category": ["electronics"] * 3,
            "sales": [100.0, 200.0, 300.0],
        }
    )
    out = simple_forecast(monthly, periods=1)

    assert set(out.columns) == {"category", "month", "sales", "is_forecast"}
    forecast = out[out["is_forecast"]]
    assert len(forecast) == 1
    assert forecast["month"].iloc[0] == pd.Timestamp("2026-04-01")
    assert forecast["sales"].iloc[0] == pytest.approx(400.0, abs=1.0)
    assert (out["sales"] >= 0).all()


def test_simple_forecast_single_point_carries_last_value() -> None:
    monthly = pd.DataFrame(
        {
            "month": pd.to_datetime(["2026-03-01"]),
            "category": ["clothing"],
            "sales": [80.0],
        }
    )
    out = simple_forecast(monthly, periods=2)
    forecast = out[out["is_forecast"]]
    assert len(forecast) == 2
    assert (forecast["sales"] == 80.0).all()


def test_simple_forecast_rejects_bad_periods() -> None:
    monthly = pd.DataFrame(
        {"month": pd.to_datetime(["2026-01-01"]), "category": ["x"], "sales": [1.0]}
    )
    with pytest.raises(ValueError, match="periods"):
        simple_forecast(monthly, periods=0)


def test_validate_forecast_result_accepts_valid_frame() -> None:
    good = pd.DataFrame(
        {
            "category": ["x", "x"],
            "month": pd.to_datetime(["2026-01-01", "2026-02-01"]),
            "sales": [10.0, 12.0],
            "is_forecast": [False, True],
        }
    )
    validate_forecast_result(good)


def test_validate_forecast_result_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        validate_forecast_result(
            pd.DataFrame(columns=["category", "month", "sales", "is_forecast"])
        )


def test_validate_forecast_result_rejects_non_finite() -> None:
    bad = pd.DataFrame(
        {
            "category": ["x"],
            "month": pd.to_datetime(["2026-02-01"]),
            "sales": [np.inf],
            "is_forecast": [True],
        }
    )
    with pytest.raises(ValueError, match="non-finite"):
        validate_forecast_result(bad)


def test_validate_forecast_result_requires_a_forecast_row() -> None:
    no_forecast = pd.DataFrame(
        {
            "category": ["x"],
            "month": pd.to_datetime(["2026-01-01"]),
            "sales": [10.0],
            "is_forecast": [False],
        }
    )
    with pytest.raises(ValueError, match="no forecast rows"):
        validate_forecast_result(no_forecast)
