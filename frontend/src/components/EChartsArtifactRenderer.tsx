import { useEffect, useMemo, useRef } from "react";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { buildOption, type EChartsArtifactSpec } from "./EChartsArtifact";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  SVGRenderer,
]);

export default function EChartsArtifactRenderer({
  artifact,
}: {
  artifact: EChartsArtifactSpec;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const option = useMemo(() => buildOption(artifact), [artifact]);

  useEffect(() => {
    if (!ref.current) return;
    const element = ref.current;
    const chart = echarts.init(element, undefined, { renderer: "svg" });
    chart.setOption(option);
    const resize = () => chart.resize();
    const animationFrame = window.requestAnimationFrame(resize);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            chart.resize();
          });
    observer?.observe(element);
    window.addEventListener("resize", resize);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [option]);

  return (
    <figure className="chart-artifact chart-artifact-echarts">
      <div
        ref={ref}
        className="echarts-canvas"
        role="img"
        aria-label={artifact.title}
        data-chart-type={artifact.chart_type}
      />
      <figcaption>
        <div className="chart-artifact-caption-main">
          <span className="chart-artifact-title">{artifact.title}</span>
          <span className="chart-artifact-meta">
            {artifact.tool_name ? <span>{artifact.tool_name}</span> : null}
          </span>
        </div>
        {artifact.notes && artifact.notes.length > 0 ? (
          <ul className="chart-artifact-notes">
            {artifact.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
      </figcaption>
    </figure>
  );
}
