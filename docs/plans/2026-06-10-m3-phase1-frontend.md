# M3 Phase 1 — Frontend (Operator Console SPA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React + Vite + TS operator console (served by FastAPI) for the M2 session API: session list, conversation with live SSE streaming, the approval workspace, and a health panel.

**Architecture:** Logic-first. The token-worthy logic — TS types mirrored from the API, a pure per-session **reducer** (seq-keyed dedupe, provisional→durable answer swap, turn finalization on `done` **or** a terminal durable message, ephemeral tool/error), an **approval-folding** selector, and the fetch/SSE clients — is built with Vitest TDD. **Component layout and styling are delegated to the `frontend-design` skill at execution** (Task 8); this plan specifies their data/callback contracts, not their markup.

**Tech Stack:** React 18, TypeScript, Vite, Vitest + @testing-library/react (jsdom), native `EventSource`, `@tanstack/react-query` for fetches.

**Spec:** [docs/2026-06-10-m3-operator-console-design.md](../2026-06-10-m3-operator-console-design.md) §2, §4, §5, §6. Backend plan (done): [2026-06-10-m3-phase1-backend.md](2026-06-10-m3-phase1-backend.md).

**API contract (confirmed against the implemented backend):**
- `POST /api/sessions` → `{ session_id }`
- `GET /api/sessions` → `{ sessions: [{ session_id, title, created_at, last_message_preview, message_count }] }`
- `GET /api/sessions/{id}` → `{ session_id, title, created_at, message_count }`
- `GET /api/sessions/{id}/thread` → `{ session_id, messages: ThreadMessage[] }`
- `POST /api/sessions/{id}/messages` → `202 { turn_id, user_message_id }`, or `409 { detail: { error: "turn_in_progress" } }`
- `POST /api/sessions/{id}/approvals/{aid}/approve` → `{ approval, execution, message }`
- `POST /api/sessions/{id}/approvals/{aid}/reject` (body `{ reason }`) → `{ approval, message }`
- `GET /health` → `{ status, app, environment, configured_mcp_servers, agent_ready, components: { mongo:{status,error?}, sandbox:{status,error?}, model:{status,model?,checked?} } }`
- `GET /health/mcp` → `{ status, servers: { spring:{status,...}, modelscope?:{status,...} } }`
- **SSE** `GET /api/sessions/{id}/stream` event frames (each `data:` is JSON):
  - `thread.append` → `{ event:"thread.append", message: ThreadMessage }`
  - `token` → `{ event:"token", text }`
  - `tool` → `{ event:"tool", name, phase }`
  - `done` → `{ event:"done", turn_id }`
  - `error` → `{ event:"error", message }`
- `ThreadMessage` = `{ message_id, session_id, seq, type, content, created_at, turn_id, trace_id, actor_id, execution_id, approval_id, card, tool_name, status, result, reason }`, `type ∈ { user, agent_answer, agent_proposal, approval_status, execution_result }`.

**Conventions:** all logic in `frontend/src`; run `npm test` (Vitest) and `npm run build` from `frontend/`. Keep files small and single-responsibility.

---

### Task 1: Scaffold the Vite app + Vitest + dev proxy

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/vitest.setup.ts`

- [ ] **Step 1: Scaffold**

Create a Vite React-TS app under `frontend/` with deps: `react`, `react-dom`, `@tanstack/react-query`; dev deps: `vite`, `typescript`, `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`, `@vitejs/plugin-react`.

`frontend/vite.config.ts` — build to `dist`, proxy API + health to FastAPI in dev, configure Vitest (jsdom):

```ts
/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  test: { environment: "jsdom", setupFiles: ["./vitest.setup.ts"], globals: true },
});
```

`frontend/vitest.setup.ts`:

```ts
import "@testing-library/jest-dom";
```

- [ ] **Step 2: Verify the toolchain runs**

Run: `cd frontend && npm install && npm test -- --run`
Expected: Vitest runs and reports "no test files" (or 0 tests) without config errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/
git commit -m "feat(m3-fe): scaffold Vite + React + TS + Vitest"
```

---

### Task 2: API types + a stream-event parser

**Files:**
- Create: `frontend/src/types.ts`
- Create: `frontend/src/api/streamEvents.ts`
- Test: `frontend/src/api/streamEvents.test.ts`

`types.ts` mirrors the API contract (above). The parser turns a raw SSE `(eventName, dataJson)` into a typed `StreamEvent` — pure and unit-testable (the hook in Task 4 feeds it).

- [ ] **Step 1: Write the failing test**

`frontend/src/api/streamEvents.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseStreamEvent } from "./streamEvents";

describe("parseStreamEvent", () => {
  it("parses a thread.append into a typed message event", () => {
    const ev = parseStreamEvent(
      "thread.append",
      JSON.stringify({ event: "thread.append", message: { seq: 3, type: "agent_answer", content: "hi", turn_id: "t1" } }),
    );
    expect(ev).toEqual({ kind: "thread.append", message: { seq: 3, type: "agent_answer", content: "hi", turn_id: "t1" } });
  });

  it("parses token/tool/done/error frames", () => {
    expect(parseStreamEvent("token", JSON.stringify({ text: "ab" }))).toEqual({ kind: "token", text: "ab" });
    expect(parseStreamEvent("tool", JSON.stringify({ name: "order_query", phase: "start" }))).toEqual({ kind: "tool", name: "order_query", phase: "start" });
    expect(parseStreamEvent("done", JSON.stringify({ turn_id: "t1" }))).toEqual({ kind: "done", turnId: "t1" });
    expect(parseStreamEvent("error", JSON.stringify({ message: "boom" }))).toEqual({ kind: "error", message: "boom" });
  });

  it("returns null for unknown or malformed frames", () => {
    expect(parseStreamEvent("nope", "{}")).toBeNull();
    expect(parseStreamEvent("token", "not json")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run streamEvents`
Expected: FAIL — `parseStreamEvent` not found.

- [ ] **Step 3: Implement types + parser**

`frontend/src/types.ts`:

```ts
export type MessageType =
  | "user" | "agent_answer" | "agent_proposal" | "approval_status" | "execution_result";

export interface ThreadMessage {
  message_id: string;
  session_id: string;
  seq: number;
  type: MessageType;
  content: string;
  created_at: string;
  turn_id: string | null;
  trace_id: string | null;
  actor_id: string | null;
  execution_id: string | null;
  approval_id: string | null;
  card: Record<string, unknown> | null;
  tool_name: string | null;
  status: string | null;
  result: Record<string, unknown> | null;
  reason: string | null;
}

export interface SessionSummary {
  session_id: string;
  title: string | null;
  created_at: string;
  last_message_preview: string | null;
  message_count: number;
}

export type StreamEvent =
  | { kind: "thread.append"; message: ThreadMessage }
  | { kind: "token"; text: string }
  | { kind: "tool"; name: string; phase: string }
  | { kind: "done"; turnId: string }
  | { kind: "error"; message: string };
```

`frontend/src/api/streamEvents.ts`:

```ts
import type { StreamEvent } from "../types";

export function parseStreamEvent(eventName: string, data: string): StreamEvent | null {
  let payload: any;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  switch (eventName) {
    case "thread.append":
      return payload?.message ? { kind: "thread.append", message: payload.message } : null;
    case "token":
      return typeof payload?.text === "string" ? { kind: "token", text: payload.text } : null;
    case "tool":
      return { kind: "tool", name: payload?.name, phase: payload?.phase };
    case "done":
      return { kind: "done", turnId: payload?.turn_id };
    case "error":
      return { kind: "error", message: payload?.message ?? "error" };
    default:
      return null;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --run streamEvents`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/streamEvents.ts frontend/src/api/streamEvents.test.ts
git commit -m "feat(m3-fe): API types + SSE event parser"
```

---

### Task 3: Fetch API client

**Files:**
- Create: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

A thin typed client over the REST endpoints. `postMessage` reports the `409 turn_in_progress` distinctly (so the caller adds no optimistic message).

- [ ] **Step 1: Write the failing test**

`frontend/src/api/client.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSession, listSessions, postMessage } from "./client";

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  })));
}

describe("api client", () => {
  it("createSession returns the session id", async () => {
    mockFetch(201, { session_id: "abc" });
    expect(await createSession()).toEqual({ session_id: "abc" });
  });

  it("listSessions returns the summaries array", async () => {
    mockFetch(200, { sessions: [{ session_id: "s1", title: "t", created_at: "x", last_message_preview: null, message_count: 0 }] });
    const sessions = await listSessions();
    expect(sessions[0].session_id).toBe("s1");
  });

  it("postMessage flags turn_in_progress on 409", async () => {
    mockFetch(409, { detail: { error: "turn_in_progress" } });
    expect(await postMessage("s1", "hi")).toEqual({ turnInProgress: true });
  });

  it("postMessage returns the turn id on 202", async () => {
    mockFetch(202, { turn_id: "t1", user_message_id: "m1" });
    expect(await postMessage("s1", "hi")).toEqual({ turnInProgress: false, turnId: "t1" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run client`
Expected: FAIL — `./client` exports missing.

- [ ] **Step 3: Implement the client**

`frontend/src/api/client.ts`:

```ts
import type { SessionSummary, ThreadMessage } from "../types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as T;
}

export async function createSession(): Promise<{ session_id: string }> {
  return json(await fetch("/api/sessions", { method: "POST" }));
}

export async function listSessions(): Promise<SessionSummary[]> {
  const body = await json<{ sessions: SessionSummary[] }>(await fetch("/api/sessions"));
  return body.sessions;
}

export async function getThread(sessionId: string): Promise<ThreadMessage[]> {
  const body = await json<{ messages: ThreadMessage[] }>(
    await fetch(`/api/sessions/${sessionId}/thread`),
  );
  return body.messages;
}

export type SendResult = { turnInProgress: true } | { turnInProgress: false; turnId: string };

export async function postMessage(sessionId: string, message: string): Promise<SendResult> {
  const res = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (res.status === 409) return { turnInProgress: true };
  const body = await json<{ turn_id: string }>(res);
  return { turnInProgress: false, turnId: body.turn_id };
}

export async function approveApproval(sessionId: string, approvalId: string): Promise<unknown> {
  return json(await fetch(`/api/sessions/${sessionId}/approvals/${approvalId}/approve`, { method: "POST" }));
}

export async function rejectApproval(sessionId: string, approvalId: string, reason?: string): Promise<unknown> {
  return json(await fetch(`/api/sessions/${sessionId}/approvals/${approvalId}/reject`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ reason }),
  }));
}

export async function getHealth(): Promise<any> {
  return json(await fetch("/health"));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --run client`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "feat(m3-fe): typed REST API client with 409 turn handling"
```

---

### Task 4: Per-session reducer (the core logic)

**Files:**
- Create: `frontend/src/state/sessionReducer.ts`
- Test: `frontend/src/state/sessionReducer.test.ts`

The reducer folds stream events + the send action into render state. **Turn finalization happens on `done` OR on a terminal durable message (`agent_answer`/`agent_proposal`) carrying the in-flight `turn_id`** — reconnect-safe.

- [ ] **Step 1: Write the failing test**

`frontend/src/state/sessionReducer.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { initialSessionState, sessionReducer } from "./sessionReducer";
import type { ThreadMessage } from "../types";

const msg = (over: Partial<ThreadMessage>): ThreadMessage => ({
  message_id: "m", session_id: "s1", seq: 1, type: "user", content: "", created_at: "",
  turn_id: null, trace_id: null, actor_id: null, execution_id: null, approval_id: null,
  card: null, tool_name: null, status: null, result: null, reason: null, ...over,
});

describe("sessionReducer", () => {
  it("upserts messages by seq (idempotent dedupe)", () => {
    let s = initialSessionState();
    s = sessionReducer(s, { kind: "thread.append", message: msg({ seq: 1, content: "a" }) });
    s = sessionReducer(s, { kind: "thread.append", message: msg({ seq: 1, content: "a" }) });
    s = sessionReducer(s, { kind: "thread.append", message: msg({ seq: 2, type: "agent_answer", content: "b" }) });
    expect(s.messages.map((m) => m.seq)).toEqual([1, 2]);
  });

  it("accumulates tokens into a provisional bubble for the in-flight turn", () => {
    let s = initialSessionState();
    s = sessionReducer(s, { kind: "turn_started", turnId: "t1" });
    s = sessionReducer(s, { kind: "token", text: "Hel" });
    s = sessionReducer(s, { kind: "token", text: "lo" });
    expect(s.inFlightTurnId).toBe("t1");
    expect(s.tokenBuffer).toBe("Hello");
  });

  it("finalizes the turn when the durable agent_answer arrives (reconnect-safe, no done frame)", () => {
    let s = initialSessionState();
    s = sessionReducer(s, { kind: "turn_started", turnId: "t1" });
    s = sessionReducer(s, { kind: "token", text: "Hi" });
    s = sessionReducer(s, { kind: "thread.append", message: msg({ seq: 5, type: "agent_answer", content: "Hi", turn_id: "t1" }) });
    expect(s.inFlightTurnId).toBeNull();
    expect(s.tokenBuffer).toBe("");
  });

  it("finalizes on a done frame for the in-flight turn", () => {
    let s = initialSessionState();
    s = sessionReducer(s, { kind: "turn_started", turnId: "t1" });
    s = sessionReducer(s, { kind: "done", turnId: "t1" });
    expect(s.inFlightTurnId).toBeNull();
  });

  it("tracks the active tool and clears it on turn end", () => {
    let s = initialSessionState();
    s = sessionReducer(s, { kind: "turn_started", turnId: "t1" });
    s = sessionReducer(s, { kind: "tool", name: "order_query", phase: "start" });
    expect(s.activeTool).toBe("order_query");
    s = sessionReducer(s, { kind: "done", turnId: "t1" });
    expect(s.activeTool).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run sessionReducer`
Expected: FAIL — module/exports missing.

- [ ] **Step 3: Implement the reducer**

`frontend/src/state/sessionReducer.ts`:

```ts
import type { StreamEvent, ThreadMessage } from "../types";

export type SessionAction = StreamEvent | { kind: "turn_started"; turnId: string };

export interface SessionState {
  bySeq: Record<number, ThreadMessage>;
  messages: ThreadMessage[]; // seq-sorted, derived on write
  inFlightTurnId: string | null;
  tokenBuffer: string;
  activeTool: string | null;
  error: string | null;
}

export function initialSessionState(): SessionState {
  return { bySeq: {}, messages: [], inFlightTurnId: null, tokenBuffer: "", activeTool: null, error: null };
}

const TERMINAL_TYPES = new Set(["agent_answer", "agent_proposal"]);

function finalize(state: SessionState): SessionState {
  return { ...state, inFlightTurnId: null, tokenBuffer: "", activeTool: null };
}

export function sessionReducer(state: SessionState, action: SessionAction): SessionState {
  switch (action.kind) {
    case "turn_started":
      return { ...state, inFlightTurnId: action.turnId, tokenBuffer: "", activeTool: null, error: null };
    case "thread.append": {
      const m = action.message;
      const bySeq = { ...state.bySeq, [m.seq]: m };
      const messages = Object.values(bySeq).sort((a, b) => a.seq - b.seq);
      let next: SessionState = { ...state, bySeq, messages };
      if (TERMINAL_TYPES.has(m.type) && m.turn_id && m.turn_id === state.inFlightTurnId) {
        next = finalize(next);
      }
      return next;
    }
    case "token":
      return state.inFlightTurnId ? { ...state, tokenBuffer: state.tokenBuffer + action.text } : state;
    case "tool":
      return { ...state, activeTool: action.phase === "start" ? action.name : null };
    case "done":
      return action.turnId === state.inFlightTurnId ? finalize(state) : state;
    case "error":
      return { ...state, error: action.message };
    default:
      return state;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --run sessionReducer`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/state/sessionReducer.ts frontend/src/state/sessionReducer.test.ts
git commit -m "feat(m3-fe): per-session reducer (dedupe, streaming, reconnect-safe finalize)"
```

---

### Task 5: Approval folding selector

**Files:**
- Create: `frontend/src/state/approvals.ts`
- Test: `frontend/src/state/approvals.test.ts`

Derives one `ApprovalView` per `approval_id` from the seq-ordered messages — latest status wins; execution result is captured.

- [ ] **Step 1: Write the failing test**

`frontend/src/state/approvals.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { foldApprovals } from "./approvals";
import type { ThreadMessage } from "../types";

const m = (o: Partial<ThreadMessage>): ThreadMessage => ({
  message_id: "x", session_id: "s", seq: 0, type: "user", content: "", created_at: "",
  turn_id: null, trace_id: null, actor_id: null, execution_id: null, approval_id: null,
  card: null, tool_name: null, status: null, result: null, reason: null, ...o,
});

describe("foldApprovals", () => {
  it("folds proposal -> consumed with the execution result", () => {
    const views = foldApprovals([
      m({ seq: 1, type: "agent_proposal", approval_id: "a1", tool_name: "purchase_order_create", status: "pending", card: { title: "PO" } }),
      m({ seq: 2, type: "approval_status", approval_id: "a1", status: "approved" }),
      m({ seq: 3, type: "execution_result", approval_id: "a1", status: "consumed", result: { purchaseOrderId: 88 } }),
    ]);
    expect(views).toHaveLength(1);
    expect(views[0]).toMatchObject({ approvalId: "a1", status: "consumed", toolName: "purchase_order_create", result: { purchaseOrderId: 88 }, card: { title: "PO" } });
  });

  it("captures a rejected reason", () => {
    const views = foldApprovals([
      m({ seq: 1, type: "agent_proposal", approval_id: "a1", status: "pending" }),
      m({ seq: 2, type: "approval_status", approval_id: "a1", status: "rejected", reason: "too costly" }),
    ]);
    expect(views[0]).toMatchObject({ status: "rejected", reason: "too costly" });
  });

  it("ignores messages without an approval_id", () => {
    expect(foldApprovals([m({ seq: 1, type: "agent_answer" })])).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run approvals`
Expected: FAIL — `foldApprovals` missing.

- [ ] **Step 3: Implement the selector**

`frontend/src/state/approvals.ts`:

```ts
import type { ThreadMessage } from "../types";

export interface ApprovalView {
  approvalId: string;
  card: Record<string, unknown> | null;
  toolName: string | null;
  status: string;
  result: Record<string, unknown> | null;
  reason: string | null;
}

export function foldApprovals(messages: ThreadMessage[]): ApprovalView[] {
  const byId = new Map<string, ApprovalView>();
  for (const msg of [...messages].sort((a, b) => a.seq - b.seq)) {
    if (!msg.approval_id) continue;
    const prev = byId.get(msg.approval_id) ?? {
      approvalId: msg.approval_id, card: null, toolName: null, status: "pending", result: null, reason: null,
    };
    byId.set(msg.approval_id, {
      ...prev,
      card: msg.card ?? prev.card,
      toolName: msg.tool_name ?? prev.toolName,
      status: msg.status ?? prev.status,
      result: msg.result ?? prev.result,
      reason: msg.reason ?? prev.reason,
    });
  }
  return [...byId.values()];
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --run approvals`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/state/approvals.ts frontend/src/state/approvals.test.ts
git commit -m "feat(m3-fe): approval folding selector"
```

---

### Task 6: `useSessionStream` hook (EventSource → reducer)

**Files:**
- Create: `frontend/src/api/useSessionStream.ts`
- Test: `frontend/src/api/useSessionStream.test.ts`

Wraps `EventSource`, parses each frame with `parseStreamEvent`, and dispatches into the reducer. The hook is thin; the test injects a fake EventSource factory so it runs in jsdom.

- [ ] **Step 1: Write the failing test**

`frontend/src/api/useSessionStream.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useSessionStream } from "./useSessionStream";

class FakeEventSource {
  listeners: Record<string, (e: MessageEvent) => void> = {};
  constructor(public url: string) {}
  addEventListener(name: string, fn: (e: MessageEvent) => void) { this.listeners[name] = fn; }
  emit(name: string, data: unknown) { this.listeners[name]?.({ data: JSON.stringify(data) } as MessageEvent); }
  close() {}
}

describe("useSessionStream", () => {
  it("applies thread.append + token frames into state", () => {
    let es!: FakeEventSource;
    const factory = (url: string) => (es = new FakeEventSource(url)) as unknown as EventSource;
    const { result } = renderHook(() => useSessionStream("s1", factory));

    act(() => result.current.markTurnStarted("t1"));
    act(() => es.emit("token", { text: "Hi" }));
    act(() => es.emit("thread.append", { message: { seq: 1, type: "agent_answer", content: "Hi", turn_id: "t1" } }));

    expect(result.current.state.messages.map((m) => m.seq)).toEqual([1]);
    expect(result.current.state.inFlightTurnId).toBeNull(); // finalized by the durable answer
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run useSessionStream`
Expected: FAIL — hook missing.

- [ ] **Step 3: Implement the hook**

`frontend/src/api/useSessionStream.ts`:

```ts
import { useEffect, useReducer, useRef } from "react";
import { initialSessionState, sessionReducer } from "../state/sessionReducer";
import { parseStreamEvent } from "./streamEvents";

type ESFactory = (url: string) => EventSource;
const defaultFactory: ESFactory = (url) => new EventSource(url);
const EVENT_NAMES = ["thread.append", "token", "tool", "done", "error"] as const;

export function useSessionStream(sessionId: string | null, factory: ESFactory = defaultFactory) {
  const [state, dispatch] = useReducer(sessionReducer, undefined, initialSessionState);
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

  useEffect(() => {
    if (!sessionId) return;
    const es = factory(`/api/sessions/${sessionId}/stream`);
    const handlers = EVENT_NAMES.map((name) => {
      const fn = (e: MessageEvent) => {
        const parsed = parseStreamEvent(name, e.data);
        if (parsed) dispatchRef.current(parsed);
      };
      es.addEventListener(name, fn);
      return [name, fn] as const;
    });
    return () => {
      handlers.forEach(([name, fn]) => es.removeEventListener?.(name, fn));
      es.close();
    };
  }, [sessionId, factory]);

  return { state, markTurnStarted: (turnId: string) => dispatch({ kind: "turn_started", turnId }) };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --run useSessionStream`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/useSessionStream.ts frontend/src/api/useSessionStream.test.ts
git commit -m "feat(m3-fe): useSessionStream hook wiring EventSource to the reducer"
```

---

### Task 7: Send action — no duplicate on 409

**Files:**
- Create: `frontend/src/state/sendMessage.ts`
- Test: `frontend/src/state/sendMessage.test.ts`

A small orchestrator: POST the message; on `202` mark the turn started (the user message streams back as `thread.append` — **no optimistic append**); on `409 turn_in_progress` do nothing but signal "busy".

- [ ] **Step 1: Write the failing test**

`frontend/src/state/sendMessage.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { performSend } from "./sendMessage";

describe("performSend", () => {
  it("marks the turn started on 202 and appends no optimistic message", async () => {
    const api = { postMessage: vi.fn(async () => ({ turnInProgress: false as const, turnId: "t1" })) };
    const markTurnStarted = vi.fn();
    const result = await performSend("s1", "hi", api, markTurnStarted);
    expect(markTurnStarted).toHaveBeenCalledWith("t1");
    expect(result).toEqual({ busy: false });
  });

  it("reports busy on 409 and does not start a turn", async () => {
    const api = { postMessage: vi.fn(async () => ({ turnInProgress: true as const })) };
    const markTurnStarted = vi.fn();
    const result = await performSend("s1", "hi", api, markTurnStarted);
    expect(markTurnStarted).not.toHaveBeenCalled();
    expect(result).toEqual({ busy: true });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run sendMessage`
Expected: FAIL — `performSend` missing.

- [ ] **Step 3: Implement**

`frontend/src/state/sendMessage.ts`:

```ts
import type { SendResult } from "../api/client";

interface SendApi {
  postMessage: (sessionId: string, message: string) => Promise<SendResult>;
}

export async function performSend(
  sessionId: string,
  message: string,
  api: SendApi,
  markTurnStarted: (turnId: string) => void,
): Promise<{ busy: boolean }> {
  const result = await api.postMessage(sessionId, message);
  if (result.turnInProgress) return { busy: true };
  markTurnStarted(result.turnId);
  return { busy: false };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --run sendMessage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/state/sendMessage.ts frontend/src/state/sendMessage.test.ts
git commit -m "feat(m3-fe): send action with 409 turn-in-progress handling"
```

---

### Task 8: Components + layout — delegate to `frontend-design`

**Files:**
- Create: `frontend/src/components/*`, update `frontend/src/App.tsx`

This task builds the visual layer. **Invoke the `frontend-design` skill** to produce the dashboard; it must consume the tested logic from Tasks 2–7 and honor these **contracts** (data in / callbacks out). No business logic in components — they render state and call the provided actions.

- [ ] **Step 1: Invoke frontend-design with these component contracts**

- **AppShell** — three panes: `SessionSidebar` (left), `ConversationView` (center), right rail (`ApprovalWorkspace` + `HealthPanel`).
- **SessionSidebar** — props: `sessions: SessionSummary[]`, `activeId`, `onSelect(id)`, `onNew()`. Uses `listSessions`/`createSession`.
- **ConversationView** — props: `messages: ThreadMessage[]`, `provisionalAnswer: string | null` (from `tokenBuffer` when `inFlightTurnId`), `activeTool: string | null`, `composerDisabled: boolean` (= `inFlightTurnId !== null`), `onSend(text)`. Renders each `type` distinctly; shows the provisional bubble while streaming; the composer is disabled during a turn; a `busy` send (409) surfaces a transient "a turn is already running" note and adds **no** message.
- **ApprovalWorkspace** — props: `approvals: ApprovalView[]`, `onApprove(id)`, `onReject(id, reason)`. Card per approval: header from `{toolName, status}` + `card` fields rendered generically (labeled key/value) + a "raw details" expander showing `card` JSON (resilient to varying `operationDetail`). Buttons disabled while in flight or once `status !== "pending"`; show the execution `result` on `consumed`, the `reason` on `rejected`, the fresh-approval note on `invalidated`, the error on `failed`.
- **HealthPanel** — props: `health` (`GET /health` body) + `mcp` (`GET /health/mcp` body). Status dots for `components.{mongo,sandbox,model}` + `servers.{spring,modelscope}`. Polls (React Query `refetchInterval`); never blocks.
- **App** — wires `useSessionStream(activeId)`, `performSend`, React Query for sessions/health, and `foldApprovals(state.messages)` into `ApprovalWorkspace`.

- [ ] **Step 2: Smoke test the rendered shell**

`frontend/src/App.test.tsx` — render `<App />` with a `QueryClientProvider` and a mocked `fetch`/EventSource; assert the three panes mount and an empty-state renders without error. Run: `cd frontend && npm test -- --run App`.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: `frontend/dist/index.html` + `frontend/dist/assets/*` produced (FastAPI's `mount_spa` serves these).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat(m3-fe): operator console components (frontend-design)"
```

---

### Task 9: Manual end-to-end verification (acceptance)

**Files:** none (a verification checklist).

- [ ] **Step 1: Run the stack**

Start the Java MCP server + MySQL + Mongo (external), then `uv run uvicorn ecommerce_agent.api.app:app --port 8000`, then `cd frontend && npm run dev`. Open the Vite URL.

- [ ] **Step 2: Walk the spec §9 acceptance**

- Create a session; it appears in the sidebar (and survives a backend restart).
- Send a message → the answer streams token-by-token with a live tool indicator; the composer is disabled during the turn; a second send while streaming shows "busy" and adds no duplicate.
- Ask the order-manager for a restock → a proposal card renders with impact; **Approve** runs approve→execute and the result appears live and survives reload; **Reject** with a reason shows rejected.
- Force a stale precondition → `invalidated` renders with the fresh-approval note.
- Health panel shows MCP / sandbox / model / Mongo; the model dot reflects config only (no token spend).
- Refresh the browser → state rebuilds from `thread` + stream; kill/restart the stream → it reconnects and re-syncs.

- [ ] **Step 3: Commit any fixes found during the walk-through, then done.**

---

## Self-Review

**Spec coverage (§2/§4/§5/§6):**
- §2 SPA served by FastAPI + dev proxy → Task 1 (backend `mount_spa` already serves `dist`).
- §4 surfaces + SSE handling: types/parser → Task 2; reducer (seq dedupe, provisional→durable swap, `done`-or-terminal finalize, tool/error) → Task 4; hook → Task 6; components → Task 8.
- §4 composer disabled during a turn; 409 adds no duplicate → Tasks 7, 8.
- §5 approval workspace: folding → Task 5; card generic render + raw-JSON fallback + one-click approve/reject + lifecycle states → Task 8.
- §6 error handling: 409 busy (Task 7), reconnect re-sync via backlog replay (Task 6 + reducer dedupe), health degraded dots (Task 8), loading/empty states (Task 8).
- §9 acceptance → Task 9 walk-through.

**Placeholder scan:** Tasks 1–7 contain complete code + exact commands. Task 8 deliberately delegates *visual* markup to `frontend-design` (per the chosen plan shape) but pins exact component contracts; Task 9 is a manual checklist. No "TODO"/vague-logic placeholders.

**Type consistency:** `StreamEvent`/`ThreadMessage`/`SessionSummary` (Task 2) used identically in Tasks 3–8; `SessionState`/`SessionAction` + `turn_started` (Task 4) used by Tasks 6/8; `ApprovalView`/`foldApprovals` (Task 5) used by Task 8; `SendResult`/`postMessage` (Task 3) used by Task 7; `parseStreamEvent` (Task 2) used by Task 6.

**Logic vs UI boundary:** all branching/state logic is in Tasks 2–7 with Vitest tests; Task 8 components are render-only against those contracts — keeping the token-worthy correctness under test while `frontend-design` owns the look.
