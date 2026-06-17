import pytest
from pydantic import ValidationError

from ecommerce_agent.tools.charting import (
    ChartSpecInput,
    build_create_chart_spec_tool,
)


def _bar_spec() -> dict:
    return {
        "title": "Sales by Category",
        "chart_type": "bar",
        "x_axis": {"label": "Category", "type": "category"},
        "y_axis": {"label": "Sales", "type": "value", "unit": "USD"},
        "series": [
            {
                "name": "Sales",
                "data": [{"x": "Electronics", "y": 75997.0}],
            }
        ],
    }


async def test_create_chart_spec_returns_echarts_artifact() -> None:
    tool = build_create_chart_spec_tool()

    result = await tool.ainvoke(_bar_spec())

    assert result["kind"] == "echarts"
    assert result["id"].startswith("chart-")
    assert result["title"] == "Sales by Category"
    assert result["chart_type"] == "bar"


def test_chart_spec_rejects_non_finite_values() -> None:
    spec = _bar_spec()
    spec["series"][0]["data"][0]["y"] = float("inf")

    with pytest.raises(ValidationError):
        ChartSpecInput.model_validate(spec)


def test_chart_spec_rejects_missing_axes_for_non_pie() -> None:
    spec = _bar_spec()
    spec.pop("x_axis")

    with pytest.raises(ValidationError):
        ChartSpecInput.model_validate(spec)


def test_pie_chart_does_not_require_axes() -> None:
    spec = {
        "title": "Category Share",
        "chart_type": "pie",
        "series": [
            {
                "name": "Share",
                "data": [{"x": "Electronics", "y": 80.0}],
            }
        ],
    }

    assert ChartSpecInput.model_validate(spec).chart_type == "pie"
