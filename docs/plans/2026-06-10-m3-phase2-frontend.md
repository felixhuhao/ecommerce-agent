# M3 Phase 2 — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the trace timeline and artifact panel to the operator console — a tabbed right rail (Approvals · Artifacts · Trace · Health), an "Inspect" control that opens an answer's persisted trace, a session-scoped artifact gallery with downloads, and the client/types/helpers behind them — over the M3 Phase 2 backend endpoints.

**Architecture:** `App` owns two new React Query reads (`getArtifacts`, `getTrace`) plus `activeTab`/`inspectedTurnId`/`focusMessageId` shell state, mirroring how it already owns the sessions/health/mcp queries and feeds presentational panels. `RightRail` hosts the four tabs and renders the active panel node. `TracePanel` and `ArtifactPanel` are **pure** components (props only, like `HealthPanel`). The 404 read race is absorbed by a pure `shouldRetryTrace` retry predicate on the trace query. Downloads are client-side from the data-URI `src`.

**Tech Stack:** React 18, TypeScript, Vite, `@tanstack/react-query` v5, native `EventSource`, lucide-react, Vitest + @testing-library/react (jsdom).

**Spec:** [docs/2026-06-10-m3-phase2-trace-artifacts-design.md](../2026-06-10-m3-phase2-trace-artifacts-design.md) §5 (surfaces), §6 (download mechanism). **Locked backend contract** (already implemented):
- Timeline: `GET /api/sessions/{id}/turns/{turn_id}/trace` → `project_timeline` output.
- Export: `GET /api/sessions/{id}/turns/{turn_id}/trace/export` (downloadable JSON).
- Artifacts: `GET /api/sessions/{id}/artifacts` → `{session_id, artifacts:[…]}`.

**Conventions (from the frontend):** components live in `frontend/src/components/`, pure components take data via props (see [HealthPanel.tsx](../../frontend/src/components/HealthPanel.tsx) / [HealthPanel.test.tsx](../../frontend/src/components/HealthPanel.test.tsx)); the API client throws on non-ok and callers compare `error.message === "404"` (see [App.tsx](../../frontend/src/App.tsx) `isNotFound`); client tests stub `fetch` (see [client.test.ts](../../frontend/src/api/client.test.ts)); App tests wrap in `QueryClientProvider` with `retry:false` (see [App.test.tsx](../../frontend/src/App.test.tsx)). Run a file: `cd frontend && npx vitest run src/<path>`; full: `cd frontend && npx vitest run`; build: `cd frontend && npm run build`.

**Styling note (logic-first, like Phase 1):** components use semantic classNames reusing existing ones (`rail-panel`, `pane-header compact`, `empty-note`, `notice notice-error`, `status-dot`, `chart-artifact`) plus a few new ones (`rail-tabs`, `artifact-grid`, `trace-span`, …). Functionality does not depend on CSS (the rail renders only the active panel). A `styles.css` pass for the new classes is a **follow-up frontend-design step**, not part of this plan.

---

### Task 1: Types + API client (ApiError, getTrace, getArtifacts, traceExportUrl, shouldRetryTrace)

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/api/client.test.ts`, change the import line to add the new symbols:

```ts
import {
  ApiError,
  approveApproval,
  createSession,
  getArtifacts,
  getMcpHealth,
  getTrace,
  listSessions,
  postMessage,
  shouldRetryTrace,
  traceExportUrl,
} from "./client";
```

Add these tests inside `describe("api client", …)`:

```ts
  it("getTrace returns the timeline", async () => {
    mockFetch(200, {
      trace_id: "tr", session_id: "s1", turn_id: "t1", started_at: 1, ended_at: 2,
      duration_ms: 1, tokens_in_total: null, tokens_out_total: null, span_count: 0, spans: [],
    });
    const timeline = await getTrace("s1", "t1");
    expect(timeline.turn_id).toBe("t1");
    expect(timeline.spans).toEqual([]);
  });

  it("getTrace throws an ApiError carrying the status on 404", async () => {
    mockFetch(404, { detail: "trace not found" });
    await expect(getTrace("s1", "missing")).rejects.toMatchObject({ status: 404 });
  });

  it("getArtifacts returns the artifacts array", async () => {
    mockFetch(200, {
      session_id: "s1",
      artifacts: [{
        id: "c0", kind: "image", mime_type: "image/png", src: "data:image/png;base64,AA",
        tool_name: "generate_bar_chart", turn_id: "t1", trace_id: "tr",
        created_at: "x", message_id: "m1",
      }],
    });
    const artifacts = await getArtifacts("s1");
    expect(artifacts[0].id).toBe("c0");
  });

  it("traceExportUrl builds the export path", () => {
    expect(traceExportUrl("s1", "t1")).toBe("/api/sessions/s1/turns/t1/trace/export");
  });

  it("shouldRetryTrace retries only 404 within the grace window", () => {
    expect(shouldRetryTrace(0, new ApiError(404))).toBe(true);
    expect(shouldRetryTrace(2, new ApiError(404))).toBe(true);
    expect(shouldRetryTrace(3, new ApiError(404))).toBe(false);
    expect(shouldRetryTrace(0, new ApiError(500))).toBe(false);
    expect(shouldRetryTrace(0, new Error("boom"))).toBe(false);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/api/client.test.ts`
Expected: FAIL — `ApiError`/`getTrace`/etc. are not exported.

- [ ] **Step 3: Add the types**

Append to `frontend/src/types.ts`:

```ts
export interface TraceSpan {
  kind: "model_call" | "tool_call";
  name: string | null;
  status: string;
  ts: number;
  duration_ms: number | null;
  args_summary: string | null;
  result_summary: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  span_id: string;
  artifact_id: string | null;
  approval_id: string | null;
  error_message: string | null;
}

export interface TraceTimeline {
  trace_id: string;
  session_id: string;
  turn_id: string;
  started_at: number;
  ended_at: number | null;
  duration_ms: number | null;
  tokens_in_total: number | null;
  tokens_out_total: number | null;
  span_count: number;
  spans: TraceSpan[];
}

export interface ArtifactSummary {
  id: string;
  kind: string;
  mime_type: string;
  src: string;
  tool_name: string | null;
  turn_id: string | null;
  trace_id: string | null;
  created_at: string;
  message_id: string;
}
```

- [ ] **Step 4: Add the client functions**

In `frontend/src/api/client.ts`, change the import line to add the new types:

```ts
import type {
  ArtifactSummary,
  HealthStatus,
  McpHealth,
  SessionDetail,
  SessionSummary,
  ThreadMessage,
  TraceTimeline,
} from "../types";
```

Replace the `json` helper (top of the file) with an `ApiError`-throwing version (the message stays the status string, so the existing `isNotFound` in `App.tsx` keeps working):

```ts
export class ApiError extends Error {
  constructor(readonly status: number) {
    super(String(status));
    this.name = "ApiError";
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new ApiError(res.status);
  return (await res.json()) as T;
}
```

Append the new functions at the end of `frontend/src/api/client.ts`:

```ts
export async function getTrace(sessionId: string, turnId: string): Promise<TraceTimeline> {
  return json(await fetch(`/api/sessions/${sessionId}/turns/${turnId}/trace`));
}

export async function getArtifacts(sessionId: string): Promise<ArtifactSummary[]> {
  const body = await json<{ artifacts: ArtifactSummary[] }>(
    await fetch(`/api/sessions/${sessionId}/artifacts`),
  );
  return body.artifacts;
}

export function traceExportUrl(sessionId: string, turnId: string): string {
  return `/api/sessions/${sessionId}/turns/${turnId}/trace/export`;
}

// React Query retry predicate: absorb the brief post-`done` 404 window (the trace persists
// sub-second after the answer appears), but never retry other errors.
export function shouldRetryTrace(failureCount: number, error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && failureCount < 3;
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/api/client.test.ts`
Expected: PASS (existing client tests still green).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "feat(m3-fe): add trace/artifact types, client calls, retry predicate"
```

---

### Task 2: `extFromMime` helper

**Files:**
- Create: `frontend/src/lib/mime.ts`
- Test: `frontend/src/lib/mime.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/mime.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { extFromMime } from "./mime";

describe("extFromMime", () => {
  it("maps known image mime types", () => {
    expect(extFromMime("image/svg+xml")).toBe("svg");
    expect(extFromMime("image/png")).toBe("png");
    expect(extFromMime("image/jpeg")).toBe("jpg");
  });

  it("falls back to bin for unknown or missing mime", () => {
    expect(extFromMime("application/x-weird")).toBe("bin");
    expect(extFromMime(null)).toBe("bin");
    expect(extFromMime(undefined)).toBe("bin");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/mime.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the helper**

Create `frontend/src/lib/mime.ts`:

```ts
const EXT_BY_MIME: Record<string, string> = {
  "image/svg+xml": "svg",
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
};

export function extFromMime(mime: string | null | undefined): string {
  if (!mime) return "bin";
  return EXT_BY_MIME[mime] ?? "bin";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/mime.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/mime.ts frontend/src/lib/mime.test.ts
git commit -m "feat(m3-fe): add extFromMime download-extension helper"
```

---

### Task 3: `ArtifactPanel` (pure component)

**Files:**
- Create: `frontend/src/components/ArtifactPanel.tsx`
- Test: `frontend/src/components/ArtifactPanel.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/ArtifactPanel.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ArtifactPanel } from "./ArtifactPanel";
import type { ArtifactSummary } from "../types";

function artifact(overrides: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return {
    id: "c0", kind: "image", mime_type: "image/png", src: "data:image/png;base64,AAAA",
    tool_name: "generate_bar_chart", turn_id: "t1", trace_id: "tr",
    created_at: "2026-06-10T00:00:00Z", message_id: "m1", ...overrides,
  };
}

const base = { isLoading: false, isError: false, onJumpToMessage: vi.fn() };

describe("ArtifactPanel", () => {
  it("renders a card with a download link named by mime", () => {
    render(<ArtifactPanel {...base} artifacts={[artifact()]} />);
    const link = screen.getByRole("link", { name: /Download/i });
    expect(link).toHaveAttribute("download", "c0.png");
    expect(link).toHaveAttribute("href", "data:image/png;base64,AAAA");
  });

  it("fires onJumpToMessage with the owning message id", () => {
    const onJumpToMessage = vi.fn();
    render(<ArtifactPanel {...base} onJumpToMessage={onJumpToMessage} artifacts={[artifact()]} />);
    fireEvent.click(screen.getByRole("button", { name: /Jump to message/i }));
    expect(onJumpToMessage).toHaveBeenCalledWith("m1");
  });

  it("shows the empty state", () => {
    render(<ArtifactPanel {...base} artifacts={[]} />);
    expect(screen.getByText(/No charts generated/i)).toBeInTheDocument();
  });

  it("shows the error state", () => {
    render(<ArtifactPanel {...base} isError artifacts={[]} />);
    expect(screen.getByText(/Could not load artifacts/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/ArtifactPanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the component**

Create `frontend/src/components/ArtifactPanel.tsx`:

```tsx
import { Download } from "lucide-react";
import { extFromMime } from "../lib/mime";
import type { ArtifactSummary } from "../types";

interface ArtifactPanelProps {
  artifacts: ArtifactSummary[];
  isLoading: boolean;
  isError: boolean;
  onJumpToMessage: (messageId: string) => void;
}

export function ArtifactPanel({ artifacts, isLoading, isError, onJumpToMessage }: ArtifactPanelProps) {
  return (
    <section className="rail-panel artifact-panel">
      <div className="pane-header compact">
        <div>
          <p className="eyebrow">Outputs</p>
          <h2>Artifacts</h2>
        </div>
      </div>
      {isError ? (
        <p className="notice notice-error">Could not load artifacts.</p>
      ) : isLoading ? (
        <p className="empty-note">Loading artifacts…</p>
      ) : artifacts.length === 0 ? (
        <p className="empty-note">No charts generated in this session yet</p>
      ) : (
        <div className="artifact-grid">
          {artifacts.map((artifact) => (
            <figure className="artifact-card" key={`${artifact.message_id}:${artifact.id}`}>
              <img src={artifact.src} alt={artifact.tool_name ?? "chart"} />
              <figcaption>
                <span className="artifact-tool">{artifact.tool_name ?? "chart"}</span>
                <span className="artifact-time">{artifact.created_at}</span>
              </figcaption>
              <div className="artifact-actions">
                <a
                  className="artifact-download"
                  href={artifact.src}
                  download={`${artifact.id}.${extFromMime(artifact.mime_type)}`}
                >
                  <Download size={14} aria-hidden="true" /> Download
                </a>
                <button type="button" onClick={() => onJumpToMessage(artifact.message_id)}>
                  Jump to message
                </button>
              </div>
            </figure>
          ))}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/ArtifactPanel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ArtifactPanel.tsx frontend/src/components/ArtifactPanel.test.tsx
git commit -m "feat(m3-fe): add ArtifactPanel (gallery + client download)"
```

---

### Task 4: `TracePanel` (pure component)

**Files:**
- Create: `frontend/src/components/TracePanel.tsx`
- Test: `frontend/src/components/TracePanel.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/TracePanel.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TracePanel } from "./TracePanel";
import type { TraceTimeline } from "../types";

function timeline(overrides: Partial<TraceTimeline> = {}): TraceTimeline {
  return {
    trace_id: "tr", session_id: "s1", turn_id: "t1", started_at: 1, ended_at: 2,
    duration_ms: 1200, tokens_in_total: 10, tokens_out_total: 20, span_count: 1,
    spans: [{
      kind: "tool_call", name: "generate_line_chart", status: "ok", ts: 1, duration_ms: 12,
      args_summary: "series", result_summary: "data:image/...", tokens_in: null, tokens_out: null,
      span_id: "x1", artifact_id: "chart-x1", approval_id: null, error_message: null,
    }],
    ...overrides,
  };
}

const base = {
  inspectedTurnId: "t1" as string | null,
  isLoading: false,
  isError: false,
  exportHref: "/api/sessions/s1/turns/t1/trace/export",
  onViewArtifacts: vi.fn(),
  onViewApproval: vi.fn(),
};

describe("TracePanel", () => {
  it("renders spans, duration and the export link", () => {
    render(<TracePanel {...base} timeline={timeline()} />);
    expect(screen.getByText("generate_line_chart")).toBeInTheDocument();
    expect(screen.getByText("12 ms")).toBeInTheDocument();
    expect(screen.getByText("1 spans")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /JSON/i })).toHaveAttribute(
      "href", "/api/sessions/s1/turns/t1/trace/export",
    );
  });

  it("links an artifact span to the Artifacts tab", () => {
    const onViewArtifacts = vi.fn();
    render(<TracePanel {...base} onViewArtifacts={onViewArtifacts} timeline={timeline()} />);
    fireEvent.click(screen.getByRole("button", { name: /View in Artifacts/i }));
    expect(onViewArtifacts).toHaveBeenCalled();
  });

  it("prompts to select a turn when none is inspected", () => {
    render(<TracePanel {...base} inspectedTurnId={null} timeline={undefined} />);
    expect(screen.getByText(/Select an answer's Inspect/i)).toBeInTheDocument();
  });

  it("shows empty-activity and error states", () => {
    const { rerender } = render(
      <TracePanel {...base} timeline={timeline({ spans: [], span_count: 0 })} />,
    );
    expect(screen.getByText(/No tool or model activity/i)).toBeInTheDocument();
    rerender(<TracePanel {...base} isError timeline={undefined} />);
    expect(screen.getByText(/Could not load trace/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/TracePanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the component**

Create `frontend/src/components/TracePanel.tsx`:

```tsx
import { Cpu, Download, Wrench } from "lucide-react";
import type { TraceSpan, TraceTimeline } from "../types";

interface TracePanelProps {
  timeline: TraceTimeline | undefined;
  inspectedTurnId: string | null;
  isLoading: boolean;
  isError: boolean;
  exportHref: string | null;
  onViewArtifacts: () => void;
  onViewApproval: () => void;
}

function SpanRow({
  span,
  onViewArtifacts,
  onViewApproval,
}: {
  span: TraceSpan;
  onViewArtifacts: () => void;
  onViewApproval: () => void;
}) {
  const Icon = span.kind === "tool_call" ? Wrench : Cpu;
  return (
    <details className="trace-span">
      <summary>
        <Icon size={14} aria-hidden="true" />
        <span className="trace-span-name">{span.name ?? span.kind}</span>
        <span className={`status-dot status-${span.status}`} aria-hidden="true" />
        {span.duration_ms != null ? (
          <span className="trace-span-dur">{Math.round(span.duration_ms)} ms</span>
        ) : null}
      </summary>
      <div className="trace-span-body">
        {span.args_summary ? (
          <p><span className="label">args</span> {span.args_summary}</p>
        ) : null}
        {span.result_summary ? (
          <p><span className="label">result</span> {span.result_summary}</p>
        ) : null}
        {span.tokens_in != null || span.tokens_out != null ? (
          <p className="trace-tokens">tokens {span.tokens_in ?? 0} in / {span.tokens_out ?? 0} out</p>
        ) : null}
        {span.error_message ? <p className="trace-error">{span.error_message}</p> : null}
        {span.artifact_id ? (
          <button type="button" className="trace-link" onClick={onViewArtifacts}>
            View in Artifacts
          </button>
        ) : null}
        {span.approval_id ? (
          <button type="button" className="trace-link" onClick={onViewApproval}>
            View approval
          </button>
        ) : null}
      </div>
    </details>
  );
}

export function TracePanel({
  timeline,
  inspectedTurnId,
  isLoading,
  isError,
  exportHref,
  onViewArtifacts,
  onViewApproval,
}: TracePanelProps) {
  return (
    <section className="rail-panel trace-panel">
      <div className="pane-header compact">
        <div>
          <p className="eyebrow">Provenance</p>
          <h2>Trace</h2>
        </div>
        {timeline && exportHref ? (
          <a className="trace-export" href={exportHref} download>
            <Download size={14} aria-hidden="true" /> JSON
          </a>
        ) : null}
      </div>
      {!inspectedTurnId ? (
        <p className="empty-note">Select an answer's Inspect to view its trace</p>
      ) : isError ? (
        <p className="notice notice-error">Could not load trace.</p>
      ) : isLoading || !timeline ? (
        <p className="empty-note">Loading trace…</p>
      ) : (
        <>
          <div className="trace-summary">
            <span>{timeline.span_count} spans</span>
            {timeline.duration_ms != null ? (
              <span>{Math.round(timeline.duration_ms)} ms</span>
            ) : null}
            {timeline.tokens_in_total != null || timeline.tokens_out_total != null ? (
              <span>{timeline.tokens_in_total ?? 0}/{timeline.tokens_out_total ?? 0} tok</span>
            ) : null}
          </div>
          {timeline.spans.length === 0 ? (
            <p className="empty-note">No tool or model activity recorded.</p>
          ) : (
            <div className="trace-timeline">
              {timeline.spans.map((span) => (
                <SpanRow
                  key={span.span_id}
                  span={span}
                  onViewArtifacts={onViewArtifacts}
                  onViewApproval={onViewApproval}
                />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/TracePanel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TracePanel.tsx frontend/src/components/TracePanel.test.tsx
git commit -m "feat(m3-fe): add TracePanel (span timeline + export + cross-links)"
```

---

### Task 5: `RightRail` (tabbed host)

**Files:**
- Create: `frontend/src/components/RightRail.tsx`
- Test: `frontend/src/components/RightRail.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/RightRail.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";

const nodes = {
  approvals: <div>APPROVALS</div>,
  artifacts: <div>ARTIFACTS</div>,
  trace: <div>TRACE</div>,
  health: <div>HEALTH</div>,
};

describe("RightRail", () => {
  it("renders only the active tab's panel and a pending badge", () => {
    render(<RightRail activeTab="approvals" onTabChange={vi.fn()} approvalCount={2} {...nodes} />);
    expect(screen.getByText("APPROVALS")).toBeInTheDocument();
    expect(screen.queryByText("TRACE")).not.toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("switches tab on click", () => {
    const onTabChange = vi.fn();
    render(<RightRail activeTab="approvals" onTabChange={onTabChange} approvalCount={0} {...nodes} />);
    fireEvent.click(screen.getByRole("tab", { name: "Trace" }));
    expect(onTabChange).toHaveBeenCalledWith("trace");
  });

  it("hides the badge when there are no pending approvals", () => {
    render(<RightRail activeTab="health" onTabChange={vi.fn()} approvalCount={0} {...nodes} />);
    expect(screen.getByText("HEALTH")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/RightRail.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the component**

Create `frontend/src/components/RightRail.tsx`:

```tsx
import type { ReactNode } from "react";

export type RailTab = "approvals" | "artifacts" | "trace" | "health";

interface RightRailProps {
  activeTab: RailTab;
  onTabChange: (tab: RailTab) => void;
  approvalCount: number;
  approvals: ReactNode;
  artifacts: ReactNode;
  trace: ReactNode;
  health: ReactNode;
}

const TABS: { id: RailTab; label: string }[] = [
  { id: "approvals", label: "Approvals" },
  { id: "artifacts", label: "Artifacts" },
  { id: "trace", label: "Trace" },
  { id: "health", label: "Health" },
];

export function RightRail({
  activeTab,
  onTabChange,
  approvalCount,
  approvals,
  artifacts,
  trace,
  health,
}: RightRailProps) {
  const panels: Record<RailTab, ReactNode> = { approvals, artifacts, trace, health };
  return (
    <div className="rail-tabbed">
      <div className="rail-tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`rail-tab ${activeTab === tab.id ? "is-active" : ""}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
            {tab.id === "approvals" && approvalCount > 0 ? (
              <span className="rail-tab-badge">{approvalCount}</span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="rail-tab-panel">{panels[activeTab]}</div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/RightRail.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/RightRail.tsx frontend/src/components/RightRail.test.tsx
git commit -m "feat(m3-fe): add tabbed RightRail host"
```

---

### Task 6: `ConversationView` — Inspect control + scroll-to-message

**Files:**
- Modify: `frontend/src/components/ConversationView.tsx`
- Test: `frontend/src/components/ConversationView.test.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/ConversationView.test.tsx`, change the testing-library import to add `fireEvent`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
```

Add these tests inside `describe("ConversationView", …)`:

```tsx
  it("shows an Inspect control on agent answers that calls onInspect with the turn id", () => {
    const onInspect = vi.fn();
    render(
      <ConversationView {...baseProps()} onInspect={onInspect} messages={[message({ turn_id: "turn-9" })]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Inspect/i }));
    expect(onInspect).toHaveBeenCalledWith("turn-9");
  });

  it("shows no Inspect control on operator messages", () => {
    render(
      <ConversationView
        {...baseProps()}
        onInspect={vi.fn()}
        messages={[message({ type: "user", content: "hi", turn_id: null })]}
      />,
    );
    expect(screen.queryByRole("button", { name: /Inspect/i })).toBeNull();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/ConversationView.test.tsx`
Expected: FAIL — no Inspect button (prop/feature not implemented).

- [ ] **Step 3: Add the prop, the icon import, the data attribute, the button, and the scroll effect**

In `frontend/src/components/ConversationView.tsx`:

Add `Activity` to the lucide import:

```tsx
import { Activity, RefreshCw, Send, Wrench } from "lucide-react";
```

Add two optional props to `ConversationViewProps`:

```tsx
  onInspect?: (turnId: string) => void;
  focusMessageId?: string | null;
```

Destructure them in the component signature (add after `onSend`):

```tsx
  onInspect,
  focusMessageId,
```

Add a scroll-to-message effect after the existing auto-scroll `useEffect`:

```tsx
  useEffect(() => {
    if (!focusMessageId) return;
    document.querySelector(`[data-message-id="${focusMessageId}"]`)?.scrollIntoView({ block: "center" });
  }, [focusMessageId]);
```

Add `data-message-id` to the durable message `<article>` (the one keyed by `${message.seq}-${message.message_id}`):

```tsx
            <article
              className={messageClass(message.type)}
              key={`${message.seq}-${message.message_id}`}
              data-message-id={message.message_id}
            >
```

In that article's `<header>`, after the status-pill line, add the Inspect button:

```tsx
                {onInspect && message.turn_id &&
                (message.type === "agent_answer" || message.type === "agent_proposal") ? (
                  <button
                    type="button"
                    className="inspect-button"
                    onClick={() => onInspect(message.turn_id as string)}
                  >
                    <Activity size={13} aria-hidden="true" /> Inspect
                  </button>
                ) : null}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/ConversationView.test.tsx`
Expected: PASS (existing ConversationView tests still green — they pass no `onInspect`, so no button renders).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ConversationView.tsx frontend/src/components/ConversationView.test.tsx
git commit -m "feat(m3-fe): add Inspect control + scroll-to-message to ConversationView"
```

---

### Task 7: Wire the rail, queries, and inspect/jump state into `App`

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/App.test.tsx`

- [ ] **Step 1: Write/adjust the failing tests**

In `frontend/src/App.test.tsx`, the existing "renders the operator console panes" test asserts `findByText("System")`, but Health now lives behind a tab. Replace its final two assertions:

```tsx
    expect(await screen.findByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("Conversation")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Approvals" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Trace" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Health" }));
    expect(await screen.findByText("System")).toBeInTheDocument();
```

Add a new integration test at the end of `describe("App", …)`:

```tsx
  it("inspecting an answer opens its trace in the rail", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const sessions: SessionSummary[] = [
      { session_id: "s1", title: "S1", created_at: "2026-06-10T00:00:00Z", last_message_preview: null, message_count: 1 },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/sessions") return jsonResponse({ sessions });
      if (url === "/api/sessions/s1/thread") return jsonResponse({ messages: [] });
      if (url === "/api/sessions/s1/artifacts") return jsonResponse({ session_id: "s1", artifacts: [] });
      if (url === "/api/sessions/s1/turns/turn-1/trace") {
        return jsonResponse({
          trace_id: "tr", session_id: "s1", turn_id: "turn-1", started_at: 1, ended_at: 2,
          duration_ms: 5, tokens_in_total: null, tokens_out_total: null, span_count: 1,
          spans: [{
            kind: "tool_call", name: "order_query", status: "ok", ts: 1, duration_ms: 3,
            args_summary: null, result_summary: null, tokens_in: null, tokens_out: null,
            span_id: "x", artifact_id: null, approval_id: null, error_message: null,
          }],
        });
      }
      if (url === "/health") {
        return jsonResponse({
          status: "ok", app: "a", environment: "t", configured_mcp_servers: ["spring"],
          agent_ready: true,
          components: { mongo: { status: "ok" }, sandbox: { status: "ok" }, model: { status: "ok" } },
        });
      }
      if (url === "/health/mcp") {
        return jsonResponse({ status: "ok", servers: { spring: { status: "ok", tool_count: 1 } } });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await waitFor(() =>
      expect(FakeEventSource.sources.some((s) => s.url === "/api/sessions/s1/stream")).toBe(true),
    );
    const stream = FakeEventSource.sources.find((s) => s.url === "/api/sessions/s1/stream");
    act(() =>
      stream?.emit("thread.append", {
        message: threadMessage({ message_id: "m1", type: "agent_answer", content: "done", turn_id: "turn-1" }),
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: /Inspect/i }));
    expect(await screen.findByText("order_query")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/App.test.tsx`
Expected: FAIL — no `Trace` tab / no Inspect button yet; the integration test cannot find `order_query`.

- [ ] **Step 3: Add imports to `App.tsx`**

Extend the client import and add the new components/types:

```tsx
import {
  approveApproval,
  createSession,
  getArtifacts,
  getHealth,
  getMcpHealth,
  getThread,
  getTrace,
  listSessions,
  postMessage,
  rejectApproval,
  shouldRetryTrace,
  traceExportUrl,
} from "./api/client";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { RightRail, type RailTab } from "./components/RightRail";
import { TracePanel } from "./components/TracePanel";
import type { ArtifactSummary, SessionSummary } from "./types";
```

(Remove the now-duplicated `import type { SessionSummary } from "./types";` line.)

- [ ] **Step 4: Add the new state, queries, and effects**

Add an empty-array constant next to `EMPTY_SESSIONS`:

```tsx
const EMPTY_ARTIFACTS: ArtifactSummary[] = [];
```

Inside `App`, after the existing `useState` declarations, add:

```tsx
  const [activeTab, setActiveTab] = useState<RailTab>("approvals");
  const [inspectedTurnId, setInspectedTurnId] = useState<string | null>(null);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(null);
  const wasInFlight = useRef(false);
```

After the existing `mcpQuery` declaration, add the two reads:

```tsx
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", activeId],
    queryFn: () => getArtifacts(activeId as string),
    enabled: !!activeId,
  });
  const traceQuery = useQuery({
    queryKey: ["trace", activeId, inspectedTurnId],
    queryFn: () => getTrace(activeId as string, inspectedTurnId as string),
    enabled: activeTab === "trace" && !!activeId && !!inspectedTurnId,
    retry: shouldRetryTrace,
    retryDelay: 400,
  });
```

After the existing `useEffect` that auto-selects the first session, add an effect that clears the inspected turn when the session changes, and one that refetches artifacts when a turn finishes:

```tsx
  useEffect(() => {
    setInspectedTurnId(null);
  }, [activeId]);

  useEffect(() => {
    const inFlight = state.inFlightTurnId !== null;
    if (wasInFlight.current && !inFlight && activeIdRef.current) {
      queryClient.invalidateQueries({ queryKey: ["artifacts", activeIdRef.current] });
    }
    wasInFlight.current = inFlight;
  }, [state.inFlightTurnId, queryClient]);
```

Add the handlers (near `handleSelectSession`):

```tsx
  const handleInspect = useCallback((turnId: string) => {
    setInspectedTurnId(turnId);
    setActiveTab("trace");
  }, []);

  const handleJumpToMessage = useCallback((messageId: string) => {
    setActiveTab("approvals");
    setFocusMessageId(messageId);
  }, []);
```

- [ ] **Step 5: Wire `onInspect`/`focusMessageId` into `ConversationView` and replace the rail**

In the `ConversationView` element, add the two props:

```tsx
          onSend={handleSend}
          onInspect={handleInspect}
          focusMessageId={focusMessageId}
```

Replace the `rail={ … }` prop of `AppShell` with the tabbed rail:

```tsx
      rail={
        <RightRail
          activeTab={activeTab}
          onTabChange={setActiveTab}
          approvalCount={approvals.filter((approval) => approval.status === "pending").length}
          approvals={
            <ApprovalWorkspace
              approvals={approvals}
              pendingApprovalId={pendingApprovalId}
              actionError={actionError}
              onApprove={handleApprove}
              onReject={handleReject}
            />
          }
          artifacts={
            <ArtifactPanel
              artifacts={artifactsQuery.data ?? EMPTY_ARTIFACTS}
              isLoading={artifactsQuery.isLoading}
              isError={artifactsQuery.isError}
              onJumpToMessage={handleJumpToMessage}
            />
          }
          trace={
            <TracePanel
              timeline={traceQuery.data}
              inspectedTurnId={inspectedTurnId}
              isLoading={traceQuery.isLoading}
              isError={traceQuery.isError}
              exportHref={
                activeId && inspectedTurnId ? traceExportUrl(activeId, inspectedTurnId) : null
              }
              onViewArtifacts={() => setActiveTab("artifacts")}
              onViewApproval={() => setActiveTab("approvals")}
            />
          }
          health={
            <HealthPanel
              health={healthQuery.data}
              mcp={mcpQuery.data}
              healthUnavailable={healthQuery.isError || healthQuery.isRefetchError}
              mcpUnavailable={mcpQuery.isError || mcpQuery.isRefetchError}
            />
          }
        />
      }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/App.test.tsx`
Expected: PASS (all three App tests, including the new inspect-opens-trace integration test).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat(m3-fe): wire tabbed rail, trace/artifact reads, inspect + jump"
```

---

### Task 8: Full suite + production build green

**Files:** none (verification only).

- [ ] **Step 1: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS (all suites, including the pre-existing ones).

- [ ] **Step 2: Type-check + production build**

Run: `cd frontend && npm run build`
Expected: `tsc -b` clean and `vite build` succeeds (emits `frontend/dist`).

- [ ] **Step 3: Commit any type/build fixes (if needed)**

```bash
git add -A
git commit -m "chore(m3-fe): build green for Phase 2 frontend"
```

---

## Self-Review

**1. Spec coverage:**
- §5.1 tabbed right rail (Approvals · Artifacts · Trace · Health), badge, single-dashboard tab state → Tasks 5, 7. ✓
- §5.2 Inspect on agent_answer/agent_proposal → sets `inspectedTurnId` + switches to Trace tab → Tasks 6, 7. ✓
- §5.3 TracePanel: turn header (duration/tokens/span count), span rows (icon/name/duration/status, expandable args/result/tokens), View-in-Artifacts + approval cross-links, Download trace JSON, and no-turn/loading/error/empty states → Task 4 (+ retry data flows from Task 7's query). ✓
- §5.4 ArtifactPanel: thumbnail/tool/time cards, Download (`{id}.{ext}` via `extFromMime`), Jump-to-message, loading/empty/error; key `${message_id}:${id}` → Tasks 2, 3. ✓
- §5.5 types, client (`getTrace`/`getArtifacts`/`traceExportUrl`), no reducer changes; `inspectedTurnId` + tab as the only new shell state → Tasks 1, 7. ✓
- §5.5 / §3.4 status-bearing `ApiError` + 404 grace-retry predicate → Task 1; applied as `retry: shouldRetryTrace, retryDelay: 400` → Task 7. ✓
- §6 client-side download from the data URI (`<a download>`), no byte-stream endpoint → Tasks 3 (artifact), 4 (trace export link). ✓
- Refetch artifacts on turn completion (so new charts appear) → Task 7 effect. ✓

**2. Placeholder scan:** No TBD/TODO/"handle errors"/"similar to" — every step shows full code or exact edits. ✓

**3. Type consistency:** `TraceTimeline`/`TraceSpan`/`ArtifactSummary` (Task 1) match the panel props (Tasks 3, 4) and the backend `project_timeline`/`_session_artifacts` shapes confirmed in the backend code. `RailTab` (Task 5) is imported and used by `App` (Task 7). `getTrace(sessionId, turnId)`, `getArtifacts(sessionId)`, `traceExportUrl(sessionId, turnId)`, `shouldRetryTrace(count, error)` signatures are consistent between Task 1 (definition + tests) and Task 7 (call sites). `ConversationView` gains optional `onInspect?`/`focusMessageId?` (Task 6) used by `App` (Task 7); existing callers/tests that omit them still compile and render no button. `ApiError.message` stays the status string, so `App.tsx`'s existing `isNotFound` (`error.message === "404"`) is unaffected. ✓

**Notes:**
- Pure panels (no React Query inside) keep component tests provider-free, matching `HealthPanel.test.tsx`; the only RQ-aware test is the App integration test, which uses the file's existing `renderApp()` + `FakeEventSource` harness.
- Visual styling for the new classNames is a deliberate follow-up (frontend-design pass), as in Phase 1; functionality is independent of CSS because `RightRail` renders only the active panel.
