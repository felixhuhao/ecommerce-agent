# Week 1: Foundation — Design Spec

> The Python/Agent project's first vertical slice: FastAPI + a single DeepAgents agent +
> `MultiServerMCPClient` → SpringBoot MCP server, streamed over SSE.
> Status: Draft | Date: 2026-06-08
> Parent spec: [2026-05-25-ecommerce-agent-design.md](2026-05-25-ecommerce-agent-design.md)
> Server contract: [ecommerce-mcp-server spec](../../ecommerce-mcp-server/docs/2026-06-05-ecommerce-mcp-server-spec.md)

## 1. Scope

Build the minimal end-to-end path that proves the architecture: a user message reaches a
DeepAgents agent, the agent calls a business tool on the already-built SpringBoot MCP server,
real data flows back from MySQL, and the response streams to the client over SSE.

**In scope (this project, Week 1):**
- FastAPI service with an SSE streaming chat endpoint
- A single DeepAgents agent (no sub-agents yet)
- `MultiServerMCPClient` connected to externally managed MCP servers, with the SpringBoot business
  server enabled in Week 1 and room for ModelScope/Python MCP servers later
- DeepSeek (`deepseek-chat`) as the LLM via the OpenAI-compatible API
- Reusable boundary tests + an opt-in live vertical-slice smoke as the acceptance bar

**Out of scope (later weeks):** sub-agents and routing (Week 2), Sandbox / `run_code` and
visualization (Week 2), prompt YAML migration (Week 2), HITL / approvals (Week 3), MongoDB
checkpoint + `interrupt`/resume (Week 3), `ContextVar` session isolation + memory (Week 3),
the Vue frontend (Week 4).

**Already done (server repo):** the SpringBoot MCP server is fully implemented — all read/write
tools, HITL enforcement, `get_statistics`, Testcontainers tests. Week 1 *consumes* it; it does
not modify the server's business logic or packaging. The server and its database are assumed to
be running before this project starts.

## 2. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent framework | DeepAgents (on LangGraph) | Matches parent spec; `create_deep_agent` gives a clean Week 2 sub-agent insertion point. |
| LLM | DeepSeek `deepseek-chat`, OpenAI-compatible | Parent spec default; key available. Provider-agnostic config so it swaps. |
| Structure | "Seams-only" (approach C) | Build the boundaries certain to grow (config, model factory, MCP client, agent, API); no speculative modules for Week 3 session/middleware/checkpoint. |
| Demo surface | Boundary tests + live smoke (no frontend) | Default tests stay durable; the real-agent path is exercised on demand. Frontend is Week 4. |
| Dependency boundary | External MCP servers | This repo orchestrates agents; it does not own MySQL or package/start the SpringBoot server. Additional MCP sources are configured the same way. |
| Test strategy | Boundary-first + opt-in live smoke | Avoid fake-agent scaffolding that will be deleted later; prove durable seams by default and exercise the full real path on demand. |
| Python tooling | Python 3.12 + `uv` | Fast, lockfile-based, modern default. |

## 3. How LangGraph is used

DeepAgents is a thin layer on top of LangGraph; `create_deep_agent(...)` returns a compiled
LangGraph graph (the ReAct model↔tool loop). We configure DeepAgents and let it build the graph
rather than hand-authoring nodes. In Week 1 the **only** LangGraph API touched directly is the
event stream (`astream_events` / `astream`), which is the source for SSE frames. LangGraph's
checkpointer and `interrupt()` (for HITL) are wired in Week 3; versions are pinned now so that
path stays open.

## 4. Architecture

### 4.1 Project structure

```
ecommerce-agent/
├── src/ecommerce_agent/
│   ├── config.py          # pydantic-settings: env → typed Settings
│   ├── models.py          # LLM factory; get_primary_model() wired; fallback/summary = Week 3 seams
│   ├── mcp_client.py      # MultiServerMCPClient factory (SpringBoot now; ModelScope/Python later)
│   ├── agent.py           # build_agent(): deep agent + discovered MCP tools + inline system prompt
│   ├── cli.py             # package CLI entry point for running the FastAPI service
│   └── api/
│       ├── app.py         # FastAPI app, lifespan (build MCP client), /health
│       └── chat.py        # POST /api/chat/stream → SSE
├── tests/
│   ├── test_app.py        # health + SSE frame contract
│   ├── test_cli.py        # package entry point
│   ├── test_config.py     # settings defaults
│   ├── test_mcp_client.py # connection registry + read-only filtering
│   └── integration/
│       ├── test_spring_mcp_integration.py # default: real MCP → real MySQL boundary
│       └── test_chat_stream_live.py   # opt-in: real DeepSeek vertical slice
├── .env.example
├── pyproject.toml
└── README.md
```

No `session/`, `middleware/`, or `checkpoint/` modules yet — those are Week 3 additions, not
rewrites of Week 1 code.

### 4.2 Module responsibilities

- **config.py** — a `Settings` model (pydantic-settings) loaded from `.env`. Single source of
  typed configuration; no module reads `os.environ` directly.
- **models.py** — `get_primary_model() -> BaseChatModel` building a `ChatOpenAI` pointed at the
  DeepSeek base URL. `get_summary_model()` / `get_fallback_model()` exist as stubs marked
  `# Week 3` so tiering has an obvious home. Constructing the model behind a function keeps the
  app testable without coupling tests to LangChain's internal tool-call choreography.
- **mcp_client.py** — `build_mcp_client() -> MultiServerMCPClient` configured from a typed MCP
  server registry. Week 1 enables the SpringBoot business server (transport, URL, trusted headers);
  ModelScope visualization and Python sandbox MCP servers are disabled config entries until Week 2.
  `get_tools()` returns discovered tools, with SpringBoot tools **filtered to the read-only
  allowlist** (§4.5) before they reach the agent.
- **agent.py** — `build_agent(model, tools)` returns the compiled DeepAgents graph with an inline
  system prompt (the "E-commerce Operations AI Assistant" role, trimmed to Week 1 read-only
  duties). It receives only the allowlisted read tools — the prompt describes intent, the
  allowlist enforces it. Prompt moves to YAML in Week 2.
- **cli.py** — package entry point for `uv run ecommerce-agent serve`, delegating to uvicorn and
  the FastAPI app factory.
- **api/app.py** — creates the FastAPI app; a lifespan handler builds the MCP client once and
  stores it in app state. Exposes `/health` and `/health/mcp`. The agent is not built at startup,
  so health checks stay useful even when `LLM_API_KEY` is intentionally absent.
- **api/chat.py** — the SSE endpoint; lazily discovers SpringBoot read tools, builds the singleton
  agent on first chat behind a lock, and maps LangGraph events to SSE frames.

### 4.3 Request flow

```
client → POST /api/chat/stream {message}
  → handler reads the singleton agent from app state, or builds it on first chat
  → agent.astream_events(message)                 [LangGraph event stream]
       model decides → calls e.g. inventory_query (MCP tool)
       → MultiServerMCPClient → POST {SPRING_MCP_URL}  (+X-Service-Token/X-User-Id/X-Session-Id)
       → SpringBoot → MySQL → rows return into agent context
       model composes the answer from the data
  → handler maps events → SSE frames (token deltas + tool-call markers)
  → client renders the stream
```

- MCP client is built **once at startup**. Spring read-tool discovery and agent construction happen
  **once on first chat**, then the singleton agent is reused per request.
- Trusted identity headers are set on the **MCP client connection, never as tool parameters**
  (parent §8.2, server spec §4.2). Week 1 uses fixed `X-User-Id`/`X-Session-Id` from config.
- **No `session_id` in the Week 1 request body.** A startup-built MCP client carries fixed headers, so
  an accepted-but-ignored `session_id` would misrepresent the contract. Per-session identity is a
  Week 3 concern (`ContextVar`-propagated headers, per server spec §4.2); it returns then as an
  additive, backwards-compatible field. Until the MCP layer can inject headers per request, the
  request stays `{message}` only.

### 4.4 SSE frame format

`text/event-stream` via `sse-starlette`. Minimal, frontend-friendly event shape:

- `event: token` — `data: {"text": "..."}` incremental answer tokens
- `event: tool` — `data: {"name": "inventory_query", "phase": "start|end"}` tool-call markers
- `event: done` — `data: {}` stream end
- `event: error` — `data: {"message": "..."}` on failure

This shape is provisional but enough for the stream assertions and a future Vue client.

### 4.5 Read-only tool allowlist

The server exposes 14 `@McpTool` methods, four of which are writes (`request_approval`,
`purchase_order_create`, `purchase_order_receive`, `order_update`). Week 1 is read-only, and the
write tools require a valid `approval_id` that doesn't exist yet — but a prompt instruction is not
enforcement (the live model could still attempt a write). So `mcp_client.get_tools()` filters
discovered tools to an explicit allowlist before they reach the agent:

```
product_query, product_search, order_query, inventory_query, inventory_low_stock,
user_query, supplier_query, supplier_top, purchase_order_query, get_statistics
```

The list lives in one constant; Week 2/3 widen it (and re-introduce the writes alongside HITL).
Defense-in-depth: the prompt states read-only intent, the allowlist guarantees it.

## 5. Configuration

`.env` (typed by `config.py`). `.env.example` ships with placeholders + commented future MCP/model
seams. MCP servers are external dependencies: this project configures how to call them, but does
not start them.

```
# LLM (OpenAI-compatible)
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=
LLM_MODEL=deepseek-chat

# SpringBoot MCP server (business tools, assumed running)
SPRING_MCP_URL=http://localhost:8080/mcp
SPRING_MCP_SERVICE_TOKEN=dev-service-token
SPRING_MCP_USER_ID=1
SPRING_MCP_SESSION_ID=local-session

# Future MCP servers (disabled in Week 1)
MODELSCOPE_MCP_URL=
PYTHON_MCP_URL=

# --- Week 3 seams (not wired yet) ---
# SUMMARY_MODEL_NAME=
# FALLBACK_MODEL_NAME=
```

## 6. External MCP Dependencies

Week 1 assumes the SpringBoot MCP server is already running, with its own MySQL/database lifecycle
handled by the `ecommerce-mcp-server` project or the developer's local environment. This agent repo
does not start, seed, or reset MySQL.

Required SpringBoot MCP contract:

- Endpoint: `POST {SPRING_MCP_URL}` (default `http://localhost:8080/mcp`)
- Transport: streamable HTTP
- Required trusted headers on every MCP call:
  `X-Service-Token`, `X-User-Id`, `X-Session-Id`
- Week 1 enabled tools: SpringBoot read tools only, enforced by the allowlist in §4.5

Future MCP sources follow the same registry shape:

- **ModelScope MCP** — visualization/chart tools, enabled in Week 2
- **Python MCP** — sandbox/file/report tools, enabled in Week 2

Startup behavior should be explicit: FastAPI may check configured MCP reachability during lifespan
or expose it in `/health`, but it should not try to launch or repair external services. Integration
tests skip with a clear message when required external MCP endpoints are unavailable.

## 7. Testing — boundary-first + live smoke

There is no "phone" category in the seed data — products are categorized `electronics`, and
`手机` only matches via product *name* (`智能手机` id=1, `手机壳` id=4). Week 1 should not create a
throwaway fake ReAct script just to make a fully deterministic "agent called a tool and narrated
the result" e2e. That would couple tests to LangChain message/tool-call internals and become
fragile once sub-agents, checkpoints, and HITL arrive.

**Default tests — deterministic, free, CI-safe, and reusable.**

- **MCP integration boundary:** with the SpringBoot MCP server running, build `MultiServerMCPClient`,
  discover tools, assert the read-only allowlist excludes write/approval tools, call
  `inventory_query(productId=1)`, and verify real seeded inventory data returns from MySQL. This
  proves the durable SpringBoot MCP boundary without involving LLM behavior.
- **SSE contract boundary:** run the FastAPI app with a simple test runnable/model that emits a
  short response, then assert `/api/chat/stream` returns valid `token`/`done` or `error` frames.
  This proves the HTTP streaming surface without pretending to validate business data flow.

**Live vertical-slice smoke — opt-in.** Run the real agent with DeepSeek, gated behind a marker +
env (`@pytest.mark.live`, `RUN_LIVE_LLM=1`). Given a prompt like "check 手机 inventory," the real
model should perform the realistic `product_search("手机") → inventory_query(productId)` chain on
its own. Assertions stay loose: stream completes, a read tool is called, and inventory-like data is
present. This is the Week 1 demo path, not the default CI gate. Run it before merging dependency
bumps to DeepAgents, LangGraph, LangChain, or MCP adapters, because those packages define the real
`astream_events(version="v2")` event names and chunk shapes consumed by the SSE mapper.

**Reusability:** all test infrastructure should carry forward: MCP readiness checks,
FastAPI app + `httpx` async client, settings overrides, MCP allowlist assertions, and SSE
assertion helpers. Avoid custom fake-model tool choreography unless it directly serves future
production behavior.

## 8. Acceptance (definition of done)

- The SpringBoot MCP server is running externally and reachable at `SPRING_MCP_URL`.
- `uv run uvicorn ...` serves FastAPI; `GET /health` returns green.
- Default tests pass: MCP discovery/read call against real MySQL, read-only tool allowlist, and the
  SSE frame contract.
- Opt-in live smoke passes by hand against DeepSeek (realistic `product_search → inventory_query`).
- `.env.example` and a short README run/test section are committed.

## 9. Risks & notes

- **External dependency drift:** the agent project assumes the SpringBoot MCP server is already
  running. Keep the Java server spec and this repo's env/tool allowlist in sync; integration tests
  should fail or skip loudly when the server contract changes.
- **Streamable-HTTP transport details** (exact `MultiServerMCPClient` transport key/header wiring
  for Spring AI `STREAMABLE` at `/mcp`) are the most likely integration friction; L0/L1-style
  manual checks (tool discovery, one read call) de-risk this before the agent loop is trusted.
- **Read-only enforcement** is an allowlist (§4.5), not a prompt promise — the write/approval
  tools the server exposes are filtered out before the agent (and the live model) can reach them.
