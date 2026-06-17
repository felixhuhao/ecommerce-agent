import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EChartsArtifact, buildOption } from "./EChartsArtifact";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  init: vi.fn(() => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
}));
vi.mock("echarts/charts", () => ({
  BarChart: {},
  LineChart: {},
  PieChart: {},
  ScatterChart: {},
}));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  LegendComponent: {},
  TitleComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({
  SVGRenderer: {},
}));

describe("EChartsArtifact", () => {
  it("uses two value axes for scatter charts", () => {
    const option = buildOption({
      id: "chart-1",
      kind: "echarts",
      title: "Spend vs Orders",
      chart_type: "scatter",
      x_axis: { label: "Orders", type: "value" },
      y_axis: { label: "Spend", type: "value", unit: "USD" },
      series: [{ name: "Customers", data: [{ x: 12, y: 4500 }] }],
    }) as any;

    expect(option.xAxis.type).toBe("value");
    expect(option.yAxis.type).toBe("value");
    expect(option.series[0].data).toEqual([[12, 4500]]);
  });

  it("honors time axes for time-series charts", () => {
    const option = buildOption({
      id: "chart-1",
      kind: "echarts",
      title: "Forecast",
      chart_type: "line",
      x_axis: { label: "Month", type: "time" },
      y_axis: { label: "Sales", type: "value", unit: "USD" },
      series: [{ name: "Sales", data: [{ x: "2026-06", y: 1200 }] }],
    }) as any;

    expect(option.xAxis.type).toBe("time");
    expect(option.series[0].data).toEqual([["2026-06", 1200]]);
  });

  it("renders artifact notes", async () => {
    render(
      <EChartsArtifact
        artifact={{
          id: "chart-1",
          kind: "echarts",
          title: "Sales by Category",
          chart_type: "bar",
          x_axis: { label: "Category", type: "category" },
          y_axis: { label: "Sales", type: "value", unit: "USD" },
          series: [{ name: "Sales", data: [{ x: "Electronics", y: 100 }] }],
          notes: ["Unknown category excluded from ranking"],
        }}
      />,
    );

    expect(await screen.findByText("Unknown category excluded from ranking")).toBeInTheDocument();
  });
});
