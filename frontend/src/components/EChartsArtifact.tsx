import { lazy, Suspense } from "react";

const EChartsArtifactRenderer = lazy(() => import("./EChartsArtifactRenderer"));

type ChartType = "line" | "area" | "bar" | "column" | "pie" | "scatter";

interface ChartAxis {
  label: string;
  type?: "category" | "time" | "value";
  unit?: string | null;
}

interface ChartPoint {
  x: string | number;
  y: number;
}

interface ChartSeries {
  name: string;
  data: ChartPoint[];
}

export interface EChartsArtifactSpec {
  id: string;
  kind: "echarts";
  title: string;
  chart_type: ChartType;
  x_axis?: ChartAxis | null;
  y_axis?: ChartAxis | null;
  series: ChartSeries[];
  notes?: string[];
  tool_name?: string | null;
}

export function isEChartsArtifact(value: unknown): value is EChartsArtifactSpec {
  if (!value || typeof value !== "object") return false;
  const artifact = value as Record<string, unknown>;
  return artifact.kind === "echarts";
}

function isValidEChartsArtifact(value: unknown): value is EChartsArtifactSpec {
  if (!isEChartsArtifact(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.chart_type === "string" &&
    Array.isArray(value.series) &&
    value.series.length > 0
  );
}

function formatUnit(value: unknown, unit?: string | null) {
  if (typeof value !== "number") return String(value ?? "");
  if (unit === "USD") {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    }).format(value);
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}

export function buildOption(artifact: EChartsArtifactSpec) {
  const type = artifact.chart_type;
  const yUnit = artifact.y_axis?.unit;
  const horizontal = type === "bar";
  const timeAxis = artifact.x_axis?.type === "time";
  const axisLabels = Array.from(
    new Set(artifact.series.flatMap((series) => series.data.map((point) => point.x))),
  );

  if (type === "pie") {
    const first = artifact.series[0];
    return {
      title: { text: artifact.title, left: 8, top: 4, textStyle: { fontSize: 14 } },
      tooltip: { trigger: "item" },
      legend: { type: "scroll", bottom: 0 },
      series: [
        {
          name: first.name,
          type: "pie",
          radius: ["35%", "68%"],
          center: ["50%", "48%"],
          data: first.data.map((point) => ({ name: String(point.x), value: point.y })),
        },
      ],
    };
  }

  const series = artifact.series.map((item) => ({
    name: item.name,
    type: type === "scatter" ? "scatter" : type === "column" || type === "bar" ? "bar" : "line",
    areaStyle: type === "area" ? {} : undefined,
    data:
      type === "scatter" || timeAxis
        ? item.data.map((point) => [point.x, point.y])
        : axisLabels.map((label) => item.data.find((point) => point.x === label)?.y ?? null),
  }));

  const categoryAxis = {
    type: timeAxis ? "time" : "category",
    name: artifact.x_axis?.label,
    nameLocation: "middle",
    nameGap: 28,
    data: timeAxis ? undefined : axisLabels.map(String),
  };
  const valueAxis = {
    type: "value",
    name: artifact.y_axis?.label,
    axisLabel: { formatter: (value: unknown) => formatUnit(value, yUnit) },
  };

  const xValueAxis = {
    type: "value",
    name: artifact.x_axis?.label,
    nameLocation: "middle",
    nameGap: 28,
  };

  if (type === "scatter") {
    return {
      title: { text: artifact.title, left: 8, top: 4, textStyle: { fontSize: 14 } },
      tooltip: { trigger: "item" },
      legend: artifact.series.length > 1 ? { type: "scroll", bottom: 0 } : undefined,
      grid: { left: 64, right: 28, top: 56, bottom: 64, containLabel: true },
      xAxis: xValueAxis,
      yAxis: valueAxis,
      series,
    };
  }

  return {
    title: { text: artifact.title, left: 8, top: 4, textStyle: { fontSize: 14 } },
    tooltip: { trigger: "axis" },
    legend: artifact.series.length > 1 ? { type: "scroll", bottom: 0 } : undefined,
    grid: { left: horizontal ? 108 : 64, right: 28, top: 56, bottom: 64, containLabel: true },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? categoryAxis : valueAxis,
    series,
  };
}

export function EChartsArtifact({ artifact }: { artifact: EChartsArtifactSpec }) {
  return (
    <Suspense
      fallback={
        <figure className="chart-artifact chart-artifact-echarts">
          <div className="echarts-canvas chart-artifact-loading">Loading chart...</div>
        </figure>
      }
    >
      <EChartsArtifactRenderer artifact={artifact} />
    </Suspense>
  );
}

export function UnsupportedChartArtifact() {
  return (
    <figure className="chart-artifact chart-artifact-error">
      <div className="artifact-error">Chart artifact unavailable</div>
    </figure>
  );
}

export { isValidEChartsArtifact };
