from __future__ import annotations

import math
import uuid
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, field_validator, model_validator

CREATE_CHART_SPEC_TOOL_NAME = "create_chart_spec"
MAX_SERIES = 8
MAX_POINTS_PER_SERIES = 300
MAX_TOTAL_POINTS = 800

ChartType = Literal["line", "area", "bar", "column", "pie", "scatter"]
AxisType = Literal["category", "time", "value"]


class ChartAxis(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    type: AxisType = "category"
    unit: str | None = Field(default=None, max_length=32)


class ChartPoint(BaseModel):
    x: str | int | float
    y: float

    @field_validator("x")
    @classmethod
    def x_must_be_usable(cls, value: str | int | float) -> str | int | float:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("x must not be empty")
            return value.strip()
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("x must be finite")
        return value

    @field_validator("y")
    @classmethod
    def y_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("y must be finite")
        return value


class ChartSeries(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    data: list[ChartPoint] = Field(min_length=1, max_length=MAX_POINTS_PER_SERIES)


class ChartSpecInput(BaseModel):
    """Validated normalized chart spec emitted as an ECharts artifact."""

    title: str = Field(min_length=1, max_length=120)
    chart_type: ChartType
    x_axis: ChartAxis | None = None
    y_axis: ChartAxis | None = None
    series: list[ChartSeries] = Field(min_length=1, max_length=MAX_SERIES)
    notes: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: list[str]) -> list[str]:
        return [note.strip()[:160] for note in value if note.strip()]

    @model_validator(mode="after")
    def validate_shape(self) -> ChartSpecInput:
        total_points = sum(len(item.data) for item in self.series)
        if total_points > MAX_TOTAL_POINTS:
            raise ValueError(f"chart has too many points; max is {MAX_TOTAL_POINTS}")
        if self.chart_type != "pie":
            if self.x_axis is None or self.y_axis is None:
                raise ValueError("x_axis and y_axis are required for non-pie charts")
        return self


class EChartsArtifact(ChartSpecInput):
    id: str
    kind: Literal["echarts"] = "echarts"


def validate_echarts_artifact(value: Any, *, fallback_id: str | None = None) -> dict | None:
    if not isinstance(value, dict) or value.get("kind") != "echarts":
        return None
    payload = {**value}
    if not isinstance(payload.get("id"), str) or not payload["id"]:
        payload["id"] = fallback_id or f"chart-{uuid.uuid4().hex[:12]}"
    artifact = EChartsArtifact.model_validate(payload)
    return _artifact_dict(artifact)


def _artifact_dict(artifact: EChartsArtifact) -> dict[str, Any]:
    # Keep pie artifacts axis-free when axes are omitted, and avoid empty note
    # arrays in persisted thread payloads.
    payload = artifact.model_dump(mode="json", exclude_none=True)
    if payload.get("notes") == []:
        payload.pop("notes", None)
    return payload


def build_create_chart_spec_tool() -> BaseTool:
    async def create_chart_spec(
        title: str,
        chart_type: ChartType,
        series: list[dict[str, Any]],
        x_axis: dict[str, Any] | None = None,
        y_axis: dict[str, Any] | None = None,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create one validated ECharts artifact from normalized chart data."""
        spec = ChartSpecInput.model_validate(
            {
                "title": title,
                "chart_type": chart_type,
                "x_axis": x_axis,
                "y_axis": y_axis,
                "series": series,
                "notes": notes or [],
            }
        )
        artifact = EChartsArtifact(
            **spec.model_dump(),
            id=f"chart-{uuid.uuid4().hex[:12]}",
        )
        return _artifact_dict(artifact)

    return StructuredTool.from_function(
        coroutine=create_chart_spec,
        name=CREATE_CHART_SPEC_TOOL_NAME,
        description=(
            "Create one validated ECharts chart artifact from normalized data. Use for "
            "operator-visible charts after business data or sandbox analysis is available."
        ),
        args_schema=ChartSpecInput,
    )
