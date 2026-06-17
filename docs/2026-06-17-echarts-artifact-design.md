# ECharts Artifact Design

## 1. Goal

Replace the default chart-generation path with first-party ECharts artifacts.

The current ModelScope chart MCP path proves MCP extensibility, but the rendered output is too
low-quality for demo-critical business charts. New chart turns should produce structured chart
specs that the operator console renders with ECharts.

This design keeps MCP extensibility as a product story, but moves chart rendering out of MCP.

## 2. Non-Goals

- No broad prompt rewrite beyond chart-specific instructions.
- No arbitrary JavaScript or raw ECharts option objects from the model.
- No pixel-perfect dashboard builder.
- No removal of old image artifact rendering. Existing traces/messages must still render.
- No new external MCP connector in this slice.

## 3. Current Problem

Today the sales analyst receives ModelScope chart tools such as `generate_line_chart` and
`generate_column_chart`. The tool returns a rendered image data URI, which the backend captures
as an image artifact and the frontend displays inline.

Problems:

- Chart quality is inconsistent and sometimes semantically wrong.
- The model has to choose among many overlapping chart tools.
- The frontend cannot improve axes, labels, tooltips, legends, or responsiveness because the
  output is already a raster/SVG image.
- The chart MCP is a weak ecommerce extension story compared with real external systems like
  analytics, support, shipping, payment, marketplace, or NL2SQL data services.

## 4. Decision

Add a first-party chart-spec tool and render its artifacts with ECharts in the frontend.

Default path:

```text
business data tool / sandbox analysis
  -> model calls create_chart_spec(...)
  -> backend validates and stores an artifact with kind="echarts"
  -> conversation renders the artifact with ECharts
```

The chart MCP becomes optional/demo-only, not the default chart path.

## 5. Artifact Contract

New artifacts use `kind: "echarts"` and store a normalized chart spec, not a raw ECharts option.

Target shape:

```json
{
  "id": "chart-abc123",
  "kind": "echarts",
  "title": "Sales by Category",
  "chart_type": "bar",
  "x_axis": {
    "label": "Category",
    "type": "category"
  },
  "y_axis": {
    "label": "Sales",
    "unit": "USD"
  },
  "series": [
    {
      "name": "Sales",
      "data": [
        {"x": "Electronics", "y": 75997.0},
        {"x": "Clothing", "y": 3731.85}
      ]
    }
  ],
  "notes": ["Unknown category excluded from ranking"],
  "tool_name": "create_chart_spec"
}
```

Allowed `chart_type` values for this slice:

- `line`
- `area`
- `bar`
- `column`
- `pie`
- `scatter`

These cover the current demo needs:

- Forecasts and time series: `line` or `area`
- Category comparisons: `bar` or `column`
- Share of total: `pie`
- Customer/product ranking: `bar`

`dual_axis` is intentionally deferred. The v1 schema has one `y_axis`; dual-axis charts need a
secondary axis plus per-series axis assignment, which is not worth adding until there is a concrete
demo prompt for it.

Validation rules:

- Require a non-empty title.
- Require at least one series.
- Require finite numeric `y` values.
- Require non-empty category/time labels for category/time axes.
- For `pie`, ignore axes in rendering and do not require axis labels.
- Cap series count, points per series, and total points to prevent huge payloads in thread
  messages.
- Reject raw `option`, callback strings, HTML, or script-like fields.

## 6. Backend Flow

### 6.1 First-Party Tool

Add a local tool, tentatively named `create_chart_spec`.

The tool should:

- Accept the normalized chart schema.
- Validate it with Pydantic.
- Return the validated artifact as a top-level structured dict:

  ```json
  {"kind": "echarts", "id": "chart-abc123", "...": "..."}
  ```

- Be tagged as `viz.chart`.
- Replace ModelScope chart tools in the default specialist runtime.

One `create_chart_spec` call returns exactly one chart artifact. Multiple charts require multiple
tool calls, but prompts should discourage duplicate charts for one logical answer.

The tool is not data-bearing. Grounding authority still comes from the data tools or sandbox
execution used before chart creation.

### 6.2 Trace and Artifact Capture

Extend trace capture to recognize structured chart artifacts in tool-end output.

This is the main backend seam. Today `capture.py` recursively searches tool output for a
`data:image/` string and returns one image artifact. ECharts capture needs a separate recognizer:

- Add `_echarts_artifact_from_output(value, fallback_id=...)`.
- Run the ECharts recognizer before the existing image-data-URI walk.
- Recognize only structured dicts with `kind == "echarts"` and a valid chart artifact shape.
- Preserve the one-artifact-per-tool-end contract: `TraceEvent.artifact` remains a single dict.
- Do not search arbitrary nested dict values before checking whether the dict itself is an ECharts
  artifact; otherwise a valid structured artifact can be skipped by the generic image walk.

`VIZ_TOOLS` is derived from the `viz.chart` tag, so tagging `create_chart_spec` is what makes
capture attempt artifact extraction for it; the tag is required for capture, not only for tool
selection.

Current image artifacts continue to work:

```json
{"kind": "image", "src": "data:image/svg+xml;base64,..."}
```

New ECharts artifacts are attached to the same message result path:

```json
{"artifacts": [{"kind": "echarts", "...": "..."}]}
```

The validated spec must survive tool output -> capture -> `TraceEvent.artifact` -> thread message
as structured JSON, not as a stringified JSON blob.

Trace spans should still show the chart artifact id:

```text
create_chart_spec -> artifact chart-abc123
```

### 6.3 Specialist Runtime

Default specialist tool sets should include the local chart-spec tool when the specialist has the
`viz.chart` tag.

For this slice:

- Sales analyst: gets `create_chart_spec`.
- Customer insights: deferred.
- Inventory and purchasing: no chart tool by default unless a concrete UI use case appears.

ModelScope chart MCP tools should not be included in the default runtime. Keep the MCP client and
health wiring available if we still want to run old/manual demos, but do not ask the model to use
those tools in normal chart prompts.

## 7. Frontend Flow

Add an ECharts artifact renderer in the conversation thread.

Rendering rules:

- If `artifact.kind === "echarts"`, render `EChartsArtifact`.
- If `artifact.kind === "image"` or `src` starts with `data:image/`, keep the existing image path.
- If an artifact matches neither path, render a compact unsupported-artifact state rather than
  dropping it silently.
- Build the actual ECharts option in frontend code from the normalized schema.
- Provide sensible defaults for axes, legend, tooltip, currency formatting, and responsive sizing.
- Import from `echarts/core` and register only the chart/component modules used by v1. Do not import
  the full ECharts bundle.

The frontend should not pass model-generated raw options directly to ECharts.

Expected UI improvements:

- Labeled axes.
- Correct chart orientation for category comparisons.
- Tooltips.
- Legend when multiple series exist.
- Responsive sizing in the chat thread.
- Download option can be deferred unless easy.

## 8. Prompt Changes

Keep this surgical.

Replace the current long ModelScope chart-tool instruction block with:

- Use `create_chart_spec` when the operator asks to chart, plot, show, visualize, or compare
  visually.
- Use `bar` or `column` for category totals/rankings.
- Use `line` or `area` for time series and forecasts.
- Use `pie` only for simple part-to-whole views with a small number of categories.
- Call the chart tool after the data is available.
- Call `create_chart_spec` at most once per logical chart.
- Do not claim a chart was created unless the chart tool succeeded.

Avoid keyword hacks or over-specific SKU/product prompt logic in this slice.

## 9. Grounding and Sources

Chart artifacts inherit the answer's grounding context but do not create authority by themselves.

Rules:

- A chart based on `get_statistics`, Spring read tools, or sandbox evidence can appear under an
  authoritative or derived answer according to the existing grounding rules.
- A chart created from unsupported model-only claims must not upgrade confidence.
- `create_chart_spec` is excluded from data-bearing source lists, like other visualization tools.

## 10. MCP Positioning

Do not make chart MCP the centerpiece of the MCP story.

Better ecommerce MCP extension candidates:

- NL2SQL/data warehouse analytics MCP.
- Shopify/marketplace connector.
- Stripe/payment connector.
- Shipping/fulfillment connector.
- Support-ticket connector.
- Slack/notification connector.

Recommended product story:

```text
MCP extends the agent into external ecommerce systems.
ECharts makes the agent's own analytical output presentation-grade.
```

## 11. Migration

Backward compatibility:

- Existing image artifacts keep rendering.
- Existing trace spans with `generate_*_chart` remain inspectable.
- Health checks may continue to report ModelScope availability if configured.

Default behavior change:

- New chart prompts should use `create_chart_spec`.
- ModelScope chart MCP should be removed from default specialist prompts and default runtime
  selection.
- Move ModelScope chart MCP Compose wiring to an optional profile so it is not on the demo-critical
  startup path.

## 12. Tests

Backend:

- `create_chart_spec` accepts a valid bar chart and returns `kind="echarts"`.
- Invalid specs are rejected: empty series, too many series/points, non-finite numbers, unsupported
  chart type.
- Trace capture attaches ECharts artifacts from tool-end output.
- Trace capture prefers a top-level ECharts artifact over the image-recursion path.
- `_chart_artifacts` includes both `kind="image"` and `kind="echarts"`.
- Grounding excludes `create_chart_spec` from data-bearing sources.
- Specialist tool selection gives sales analyst `create_chart_spec` and does not include
  ModelScope chart MCP tools by default.

Frontend:

- Conversation renders ECharts artifacts inline.
- Existing image artifacts still render.
- Category chart test verifies axis labels and rendered container.
- Unknown artifact kind and malformed ECharts artifact both fall back to compact states, not blank
  cards.

Smoke:

- Update chart smoke from "chart MCP emitted image artifact" to "ECharts artifact emitted".
- Keep chart MCP contract smoke optional only if the MCP server remains part of local startup.

## 13. Implementation Checklist

1. Add Pydantic chart schema and `create_chart_spec` tool.
2. Register the tool under `viz.chart`.
3. Remove ModelScope chart tools from default specialist runtime selection.
4. Update chart prompt instructions.
5. Extend trace artifact extraction for top-level `kind="echarts"` before image recursion.
6. Extend frontend artifact types and conversation rendering.
7. Add ECharts dependency and renderer component using modular `echarts/core` imports.
8. Update tests and smoke expectations.
9. Run unit tests, frontend tests, ruff, and one live chart prompt.

## 14. Scope Decisions

1. Customer-insights charting is deferred. Ship sales-analyst first.
2. PNG/SVG download is deferred. Interactive inline rendering is enough for v1.
3. ModelScope chart MCP moves to an optional profile.
