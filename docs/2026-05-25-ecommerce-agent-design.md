# E-Commerce AI Assistant — Design Spec

> 电商运营智能助手：基于 DeepAgents + SpringBoot 的多 Agent 系统
> Status: Draft | Date: 2026-05-25

## 1. Project Overview

**What:** A product-grade e-commerce operations assistant that helps operators analyze business
data, produce auditable artifacts, and safely execute approved operational actions through a
multi-agent architecture.

**Why:** Build an extensible agentic operations platform, not only a demo. The system should prove
that AI agents can work inside real commerce workflows while preserving the properties mature
business software needs: permission boundaries, reviewable actions, auditability, data provenance,
and reliable integration with existing backend systems.

**Who:** Commerce operators, analysts, and operations managers who need faster answers and safer
workflow execution. The engineering audience is still important, but the product should be
understandable as a real operator console.

**Cadence:** Build in weekly vertical slices, with a product roadmap in
[2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md). The first product milestone is a
trusted read-only analysis workspace; the second is approved action execution.

### 1.1 Product Principles

1. **Trust before autonomy.** Agents may reason and propose, but the backend remains authoritative
   for permissions, writes, approvals, and canonical business state.
2. **Sub-agents need real boundaries.** Create a sub-agent only when it has a distinct permission
   set, tool set, context budget, or workflow phase. Do not split agents just to appear
   "multi-agent."
3. **Artifacts over chat-only answers.** Valuable outputs should become inspectable artifacts:
   chart specs, reports, approval requests, diffs, tool traces, and audit records.
4. **Operator control stays visible.** The UI must show what the agent did, what data it used,
   which tools it called, and which actions still require human confirmation.
5. **Business systems stay authoritative.** SpringBoot owns business rules, database writes,
   approvals, and operation hashes. Python owns orchestration, reasoning, sandboxed analysis, and
   presentation artifacts.
6. **Extensibility is deliberate.** New MCP servers, tools, agents, prompts, models, and sandbox
   backends must attach through stable registries/seams rather than ad hoc wiring.

## 2. Architecture

### 2.1 System Diagram

```
Vue 3 Operator Console
  ├── SSE streaming chat
  ├── tool traces + artifact panel
  └── HITL approval workspace
        │
        │ HTTP / SSE / WebSocket
        ▼
FastAPI Service Layer
  ├── REST API endpoints
  ├── SSE streaming response
  ├── WebSocket Monitor
  └── ContextVar session isolation + MongoDB checkpoint
        │
        ▼
Main Agent (primary reasoning model)
  ├── Routes tasks to sub-agents
  ├── Manages HITL approval workflow
  └── Aggregates results
        │
  ┌────┴────────────────────┐
  │                         │
  ▼                         ▼
sales-analyst          order-manager
(readonly tools)       (reads + request_approval;
                        backend executes approved writes)
  │                         │
  │    MCP (SSE)            │    MCP (SSE)
  ▼                         ▼
  ┌─────────────────────────────┐
  │   SpringBoot MCP Server     │
  │   (Spring AI @Tool)         │
  │   Tool → Service → Mapper   │
  └────────────┬────────────────┘
               │
               ▼
         MySQL (ecommerce_db)

  ┌─────────────────────────────┐
  │   Sandbox Execution         │
  │   Data files → Code exec →  │
  │   Report generation         │
  └─────────────────────────────┘

  ┌─────────────────────────────┐
  │   Memory System             │
  │   Short-term: MongoDB       │
  │   Preferences: Markdown     │
  │   Skills: TTL + grading     │
  │   Guidance: AGENTS.md       │
  └─────────────────────────────┘
```

M1 intentionally runs the read-only `sales-analyst` as the runtime agent directly, rather than
wrapping it in a coordinator that has no real routing choice yet. The coordinator/sub-agent shape
above is the target product architecture and is activated in M2 when `order-manager` gives the
system a second specialist with a distinct authority boundary.

### 2.2 Security Model: Sandbox as Agent Backend

The sandbox is configured as the DeepAgents backend. In Week 2 this is a self-hosted
`DockerSandbox`; a managed sandbox such as OpenSandbox can replace it later behind the same backend
seam. All Agent file operations (`write_file`, `read_file`) are routed to the sandbox automatically
through the framework layer. The security purpose is **code execution isolation** — Agent-generated
Python code runs inside the sandbox, not on the host machine.

**Database stays outside Sandbox because:**
1. Sandbox is ephemeral — rebuilt/cleared between sessions
2. MySQL is trusted persistent data that must stay outside the Agent's execution environment
3. Sandbox code can NEVER directly connect to MySQL — all DB access goes through MCP tools → SpringBoot

**Data flow: MCP query results → Agent context (same as ERP_OPENCLAW)**

MCP tools return data directly to the Agent's context. The Agent sees query results and can respond immediately or use `run_code` in Sandbox for deeper analysis. No intermediate file-saving wrapper.

| Operation | Execution | Reason |
|-----------|-----------|--------|
| Query inventory/orders/customers | MCP → data returns to Agent context | Agent sees data directly, can answer or analyze further |
| Create/modify orders | MCP → HITL approval → SpringBoot executes | Must be confirmed before crossing trust boundary |
| Web search | MCP API call | No local execution |
| Chart generation | MCP visualization | Pass JSON config |
| User preference read/write | Virtual path | Fixed path, fixed format |
| Agent guidance file | Virtual path | Read-only, loaded at startup |
| Data analysis (pandas/numpy) | Sandbox (run_code) | Agent decides when deep analysis is needed, writes code, Sandbox executes |
| Report document generation | Sandbox | Markdown assembly inside isolated environment |
| User file parsing | Sandbox | Untrusted files never touch host |

Decision rule: Agent only passes parameters → virtual path; Agent writes code to run logic → Sandbox; MCP results → return to Agent context directly.

### 2.3 Session Isolation

Use Python `ContextVar` for asyncio coroutine-level isolation. Each user session gets a unique UUID and dedicated directory. MongoDB handles checkpoint persistence for **conversation continuity** across restarts (not write safety — approved actions are durable MySQL `approval_record`s, §5.2).

`ContextVar` only isolates within a single coroutine context — it does **not** automatically protect shared singletons. The session UUID must be propagated explicitly to every boundary that leaves the request coroutine:

- **WebSocket tasks** and **background jobs** — set the ContextVar at task entry; spawned tasks don't inherit it for free.
- **MCP clients** — propagate session/user identity as trusted request metadata (service-authenticated headers/JWT/session binding), not as Agent-controlled tool parameters. `request_approval` and the backend execution path bind to this trusted identity — see §5.2.
- **Checkpoint `thread_id`** — derive from the session UUID so conversation continuity targets the correct thread.
- **Sandbox directories** — namespace under `{session_id}` (uploads, reports, skills) and never accept client-supplied paths.
- **Cleanup** — define when a session's sandbox dir / checkpoints are reaped (e.g. TTL or on session close) to avoid unbounded growth and stale-state leakage.

## 3. Database Design

Database: `ecommerce_db` (MySQL)

### 3.1 Tables

Two distinct order documents, as in real ops systems: **customer sales orders** (`orders`,
**read-mostly** — queried for analytics, with fulfillment-status updates allowed only via the
approved `order_update` operation) and **supplier purchase orders** (`purchase_order`, the approved
action path for restocking). They have different lifecycles and must not be conflated.

| Table | Purpose | Key Fields |
|-------|---------|------------|
| product | Product catalog | product_id, name, category, price, cost, status |
| orders | Customer sales orders | order_id, user_id, total_amount, status, created_at, paid_at |
| order_item | Sales order line items | item_id, order_id, product_id, quantity, unit_price |
| user | Customer accounts | user_id, username, phone, email, level, registered_at |
| inventory | Stock management | product_id, quantity, safety_stock, warehouse |
| supplier | Supplier info | supplier_id, name, contact, rating, lead_time |
| review | Product reviews | review_id, user_id, product_id, rating, content |
| purchase_order | Supplier purchase orders (procurement) | po_id, supplier_id, status, total_cost, created_at, received_at |
| purchase_order_item | PO line items | po_item_id, po_id, product_id, quantity, unit_cost |

`purchase_order.status`: `placed` → `received` (goods arrive, inventory incremented) /
`cancelled`. A PO row is inserted only by an already-approved `purchase_order_create`, so it
starts at `placed` — the pending/approved gate lives in `approval_record` (§5), not on the PO.
Cost is tracked at `unit_cost` (what we pay the supplier), separate from `product.price` (what
customers pay).

### 3.2 Relationships

- order_item.order_id → orders.order_id
- order_item.product_id → product.product_id
- orders.user_id → user.user_id
- inventory.product_id → product.product_id
- review.product_id → product.product_id
- review.user_id → user.user_id
- purchase_order.supplier_id → supplier.supplier_id
- purchase_order_item.po_id → purchase_order.po_id
- purchase_order_item.product_id → product.product_id

### 3.3 Sample Data

- 50+ products across 5+ categories (electronics, clothing, home, food, sports)
- 200+ customer orders spanning 6 months
- 30+ users with different levels
- 10+ suppliers with varying ratings
- A few historical purchase orders (received) so analytics has procurement history; new POs
  are created live through the order-manager approved action flow

## 4. Agent Architecture

Product rule: use sub-agents for **permission, tool, context, or workflow boundaries**. A
sub-agent earns its existence when it narrows what the model can see or do, or when it owns a
distinct phase of work. M1 runs the read-only analyst directly to avoid paying coordinator latency
before there is a routing decision. The first multi-agent boundary is M2: read-only analysis
(`sales-analyst`) versus action proposal (`order-manager`, reads + `request_approval`, never write
tools) after the approval workflow + deterministic backend executor exist (§5.2).

### 4.1 Main Agent

Role: "E-commerce Operations AI Assistant" — coordinator, not executor.

The main agent analyzes user intent, routes to appropriate sub-agents, aggregates returned
artifacts, and keeps the interaction understandable to the operator. It should not call business
MCP tools directly once sub-agents are enabled; domain tools live on specialists with scoped
permissions.

Tools available to main agent: `web_search`, `assign_skill`, `download_file`, `read_file`, `write_file`

System prompt defines:
- Route sales analysis → `sales-analyst`
- Route order operations → `order-manager`
- Simple greetings/feature questions → respond directly
- Read user preferences at startup from `/memories/{user_id}/preferences.md`
- Compress context after sub-agent returns (compact_conversation)

### 4.2 Sub-Agent: sales-analyst

Role: Sales data analysis specialist. Read-only access.

Tools come from three MCP servers — only the first group is SpringBoot:

*SpringBoot business tools (see §8.3):*
- `product_query` — query product catalog (pagination + category filter)
- `product_search` — fuzzy search products by name
- `order_query` — query customer sales orders + items
- `inventory_query` — query inventory levels
- `inventory_low_stock` — list items below safety stock
- `user_query` — query customer information
- `supplier_query` — query supplier data
- `supplier_top` — list top suppliers by rating and lead time
- `get_statistics` — aggregated business statistics

*ModelScope MCP:*
- `generate_visualization` — merged chart tool (26→1)

*Python MCP (Sandbox):*
- `run_code` — execute Python code in Sandbox (pandas, numpy)
- `read_uploaded_file` — parse uploaded files (CSV, Excel) in Sandbox
- `write_report` — generate Markdown report and save to Sandbox for download

Typical tasks:
- "Show sales trends for last quarter"
- "Which categories are trending up or down over the last 6 months, forecast next month's sales,
  and chart the result"
- "Compare sales by category" (simple aggregation; prefer authoritative `get_statistics`, not
  sandbox)
- "Which products need restocking?"
- "Supplier performance radar chart"
- "Generate a monthly sales report"
- "Analyze this uploaded CSV file"

### 4.3 Sub-Agent: order-manager

Role: Order management specialist. Handles **procurement** (supplier purchase orders) and
**customer order fulfillment**. It can **propose** writes, but it holds **no write tools** — it
analyzes, then calls `request_approval`. Execution happens in a deterministic backend executor
after human approval (§5), never from the LLM.

Tools (reads + propose only — all SpringBoot business tools, see §8.3):
- `purchase_order_query` — query supplier purchase orders (read)
- `order_query` — query customer sales orders (read)
- `inventory_query` — check stock before ordering (read)
- `supplier_query` — look up a supplier for a PO (read)
- `request_approval` — propose a write; SpringBoot builds the canonical payload + server-rendered
  card and returns an `approval_id` (§5)

> **The LLM never receives `purchase_order_create`, `purchase_order_receive`, or `order_update`
> as tools.** Those are write operations executed deterministically by the backend from the stored
> approval payload (§5.2), keyed by `approval_id`. The model proposes; the backend executes.

Typical tasks (each ends in a *proposal*, then human approval, then backend execution):
- "Restock 500 phone cases from supplier A" → proposes `purchase_order_create`
- "Mark PO #88 as received" → proposes `purchase_order_receive` (inventory +500 on execute)
- "Change order #12345 status to shipped" → proposes `order_update`

> Bulk cancellation ("cancel all pending orders older than 30 days") is a High-risk batch
> operation — post-MVP (see §5.1). Not in the initial tool set.

### 4.4 Middleware Stack

Executed in order for each agent turn:

1. **ContextInjectionMiddleware** — inject user_id, username into agent context
2. **SkillsSyncMiddleware** — sync skill files to sandbox
3. **UserSkillsRestoreMiddleware** — restore user's persisted skills
4. **MemoryUpdateMiddleware** — update user preference memory
5. **SummarizationMiddleware** — compress context after sub-agent returns
6. **ModelCallLimitMiddleware** — max 50 model calls per turn
7. **ToolCallLimitMiddleware** — max 200 tool calls per turn

### 4.5 Prompt Management

All agent prompts stored in `prompt/prompts.yml` (not hardcoded in Python). Loaded at agent initialization. Changes to prompts require no code modification.

## 5. HITL (Human-in-the-Loop)

### 5.1 Risk-Based Approval Levels

| Operation Type | Risk | HITL Requirement | Example | MVP |
|---------------|------|-----------------|---------|-----|
| Query | Low | None | "Check phone category inventory" | ✅ |
| Create | Medium | Single confirm, show full PO details | "Restock 500 phone cases from supplier A" | ✅ |
| Modify | Medium | Single confirm, show diff | "Change order #12345 to shipped" | ✅ |
| Delete | High | Show impact scope + double confirm | "Delete all expired orders" | ⬜ post-MVP |
| Batch | High | Show impact scope + double confirm | "Cancel all pending orders >30 days" | ⬜ post-MVP |

The MVP approved operations (§8.3) cover Create + Modify only. Delete and Batch are documented here
so the approval framework anticipates them, but no MVP operation performs them — add
`order_cancel` (batch) and any delete operation with the double-confirm flow when needed.

### 5.2 Implementation Flow (Propose → Approve → Backend Execute)

Write operations are **server-enforced**, not prompt-based — and the enforcement is a **durable
approval record**, not a suspended agent run. The flow separates *authority* (a human must approve
risky writes) from *mechanism* (how the system waits between proposal and execution). The LLM can
**propose**; it cannot approve, cannot execute, and never re-issues write params. Each agent turn
completes normally — there is **no LangGraph `interrupt()`/resume and no checkpoint of a paused
graph** on the write path.

```
Turn 1 — PROPOSE (agent):
→ Agent (order-manager) calls request_approval(tool_name, operation_params)   ← structured params only, no prose/identity
→ SpringBoot resolves trusted user_id/session_id from request metadata
→ SpringBoot reads live DB rows and builds canonical operation_payload =
  operation_params + server-derived preconditions/snapshot
  (e.g. current order/PO status, inventory quantity, supplier/product/unit cost)
→ SpringBoot computes operation_hash AND renders the human-facing card (summary/diff/impact)
  from the canonical payload + live DB state — the Agent never authors what the human sees
→ creates PENDING approval_record (bound to user_id + session_id + tool_name)
→ returns {approval_id} to the agent → the agent's turn ENDS NORMALLY
  ("Proposed PO #123 — pending your approval.")

HUMAN — APPROVE (REST, not MCP, not the agent):
→ Frontend/FastAPI fetches the server-rendered card via GET /approvals/{id} and displays it
→ Human approves via POST /approvals/{id}/approve   (authenticated; NEVER an MCP tool)
  → Server marks approval_id as approved   (approve only flips status; it does NOT execute)
→ or rejects via POST /approvals/{id}/reject  (reason persisted)

BACKEND EXECUTE — deterministic, keyed by approval_id (no LLM, no write params from the agent):
→ After approval, Frontend/FastAPI calls POST /approvals/{id}/execute against SpringBoot
→ SpringBoot LOADS the canonical operation_payload from approval_record (it is the source of truth)
→ re-derives trusted identity + current DB preconditions, then validates the Java spec §4 contract
  (exists + approved, hash integrity, tool/actor/session binding, not expired, one-time use,
   live preconditions unchanged)
  → Valid → execute the operation from the stored payload in a DB transaction, mark consumed
  → Invalid (e.g. preconditions drifted) → mark invalidated; a fresh approval is required
  → Execution error after validation → mark failed with execution_result for audit/retry policy
```

The pending action lives as a **first-class `approval_record` in MySQL** with its own lifecycle
(`pending → approved → consumed` / `rejected` / `expired` / `invalidated` / `failed`). Because the
"wait" is a durable row, not an in-memory suspended coroutine, it survives restarts trivially —
**no MongoDB checkpoint is required for write safety**.

**Why a deterministic backend executor (the LLM never executes):** the agent's tool set contains
`request_approval` and reads only — not write operations. Execution is a backend operation keyed by
`approval_id` that reads the stored canonical payload. The model literally cannot perform a write,
and cannot tamper with one after approval, because it never supplies the execution params.

**Why operation_hash (role under this model):** the hash binds the stored authorization to exactly
what the human saw, and provides an integrity check of the `approval_record`. Since the agent never
re-submits params on execution, the old "agent changes params between two calls" attack vanishes;
the hash now guards record integrity, and the live-precondition recheck at execution time guards
against stale writes (DB state drifting between approval and execution → reject, require a fresh
approval).

**Why the approve/reject endpoints are not MCP tools:** approval is a *human* action. Exposing it as a tool would let the Agent approve its own requests. It lives on an authenticated REST endpoint the frontend/FastAPI calls on the human's behalf — never in the Agent's tool list. The full Java contract is in the Java spec §4.

**SpringBoot approval table:**

```sql
CREATE TABLE approval_record (
  approval_id      VARCHAR(36) PRIMARY KEY,
  operation_hash   VARCHAR(64) NOT NULL,        -- canonical hash of operation_payload
  tool_name        VARCHAR(40) NOT NULL,        -- operation/capability this approval authorizes
  operation_type   VARCHAR(20) NOT NULL,
  operation_payload JSON NOT NULL,              -- canonical params + server preconditions/snapshot (hashed)
  operation_detail JSON NOT NULL,               -- server-rendered card (from canonical payload + DB; not Agent prose)
  user_id          BIGINT NOT NULL,             -- actor binding
  session_id       VARCHAR(64) NOT NULL,        -- session binding
  status           VARCHAR(12) NOT NULL DEFAULT 'pending',  -- pending|approved|consumed|rejected|expired|invalidated|failed
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at       DATETIME NOT NULL,
  consumed_at      DATETIME NULL,               -- one-time use
  executed_at      DATETIME NULL,
  execution_result JSON NULL,                   -- result/error summary for audit
  KEY idx_status (status)
);
```

Execution is a backend operation keyed by `approval_id` (`purchase_order_create`,
`purchase_order_receive`, `order_update` are **not** agent tools). The service layer loads the
canonical `operation_payload` from `approval_record` and checks validity before executing: approval
exists, `status=approved`, `operation_hash` integrity holds, the trusted request `user_id`/session
matches the record, the approval has not expired, it has not already been consumed (one-time use),
and the pinned live preconditions still hold. See the Java spec §4 for the full enforcement contract.
Execution must claim the approval under a database transaction/row lock and transition away from
`approved` atomically before or during execution, so two execute requests cannot double-spend the
same approval.

> **Java companion change (tracked, sibling repo).** The implemented Java server currently exposes
> the write tools as `@McpTool`s that take `approval_id` **+ params** and rebuild the hash from the
> *incoming* params (Java spec §4.3). This design moves to **execute-by-`approval_id`**: the backend
> loads the stored canonical payload and executes from it, dropping incoming params. That requires
> a companion update to `ecommerce-mcp-server` (a backend
> `POST /approvals/{approval_id}/execute` path, and removing the write `@McpTool`s from the
> agent-reachable surface). It is a *simplification* of the Java side (no incoming-param mismatch
> path), tracked in the roadmap, not yet applied.

## 6. Memory System

### 6.1 Four-Layer Architecture

| Layer | Storage | Purpose | Implementation |
|-------|---------|---------|---------------|
| Short-term | MongoDB checkpoint | Conversation state / multi-turn continuity (NOT write safety — approvals are durable MySQL records, §5.2) | LangGraph checkpointer |
| Preferences | `/memories/{user_id}/preferences.md` | Display prefs, business constraints | Virtual path read/write (Markdown) |
| Skills | `/persisted-skills/` | Agent experience accumulation | Sandbox storage |
| Guidance | `/AGENTS.md` | Behavior boundaries, compliance | Read-only at startup |

### 6.2 User Preferences (Markdown)

Stored as Markdown at `/memories/{user_id}/preferences.md` (same as ERP_OPENCLAW). Markdown is more natural for LLM read/write — Agent generates plain text, no strict syntax to break.

```markdown
# 用户偏好

## 展示偏好
- 图表类型: 柱状图 (bar)
- 语言: 中文
- 货币: CNY

## 业务偏好
- 权限级别: operator
- 默认供应商: SUP001, SUP003
- 预算上限: 50000
- 审批流程: single_level
```

### 6.3 Skill Auto-Learning with Constraints

Skills are memos the agent writes for its future self — not uncontrolled code execution.

**Constraint 1: Skill Grading**

| Grade | Creation | Usage | Example |
|-------|----------|-------|---------|
| General | Agent auto-creates | Auto-used | Product comparison workflow |
| Sensitive | Agent creates, marked "pending review" | Used after human confirmation | New supplier evaluation flow |
| System | Engineer pre-writes | Agent read-only | Security rules, compliance |

**Constraint 2: Skill Metadata**

Each skill carries: `skill_name`, `description`, `created_at`, `expire_at` (3-month TTL), `confidence`, `usage_count`, `scope`. Expired skills are marked but not auto-deleted — cleanup is manual.

## 7. Model Strategy

Primary model: configurable OpenAI-compatible chat model. Default for implementation is `deepseek-chat`; use a stronger reasoning model only when the provider and model name are confirmed in `.env`.

Summarization model: configurable cheaper/fast chat model. It may use the same provider as the primary model, but should be isolated as `SUMMARY_MODEL_NAME` so it can be swapped without code changes.

Fallback: On API timeout/error, auto-switch to the configured fallback model, e.g. `qwen-max`. Alert ops. Auto-revert when the primary provider recovers.

Cost control via tool merging (26→1 visualization), context compression, and model tiering (stronger model for reasoning, cheaper model for summarization).

## 8. MCP Integration

### 8.1 Python ↔ SpringBoot Boundary

The Agent connects to multiple MCP Servers via `MultiServerMCPClient` (same client pattern as ERP_OPENCLAW). Business tools are served **directly by SpringBoot** using Spring AI's MCP server support — no Python proxy in between:

```
Agent (DeepAgents)                  MCP Servers
┌──────────────────┐    ┌─────────────────────────────────┐
│ Agent orchestration│    │ SpringBoot MCP Server (Spring AI) │
│ LangGraph         │◄──►│ @Tool 业务工具 + HITL 审批          │
│ MultiServerMCP    │    │ streamable-HTTP / SSE transport   │
│   Client          │    │ Controller → Service → Mapper     │
└───────┬──────────┘    └──────────────┬────────────────────┘
        │                              │
        │                     ┌────────▼─────────┐
        │                     │ MySQL ecommerce_db│
        │                     └──────────────────┘
        │
        ├──► ┌──────────────────┐
        │    │ ModelScope MCP    │
        │    │ 可视化工具 (26图表) │
        │    └──────────────────┘
        │
        └──► ┌──────────────────┐
             │ Python MCP        │
             │ sandbox / run_code │
             └──────────────────┘
```

**Why SpringBoot as the MCP server (not a Python FastMCP proxy):** Spring AI ships first-class MCP server support (`spring-ai-starter-mcp-server-webmvc`), built on the official MCP Java SDK co-maintained by the Spring team. SpringBoot exposes `@Tool`-annotated methods directly over MCP, so a separate Python proxy that only forwarded calls via httpx would be redundant. Java owns business logic, database, **and** the business-tool MCP surface; Python owns only what is genuinely Python's job (Agent orchestration, sandbox execution, ModelScope charts).

### 8.2 MCP Tool Loading (Framework Native)

MCP tools are loaded via `MultiServerMCPClient` (same as ERP_OPENCLAW). The client connects to multiple MCP Servers:
1. **SpringBoot MCP Server** — agent-reachable business **read** tools + `request_approval`,
   exposed via Spring AI `@Tool` over streamable-HTTP/SSE. Write operations live behind the
   deterministic backend executor keyed by `approval_id`, not in the agent's tool list.
2. **ModelScope MCP Server** — 26 chart tools + 1 spreadsheet tool
3. **Python MCP Server** — sandbox / `run_code` / file-parsing tools (Python's native job)

Tools are auto-discovered with schemas, grouped by prefix, and assigned to sub-agents.

Tool results return data directly to Agent context. Agent sees query results and can respond immediately, or use `run_code` in Sandbox for deeper analysis.

**SpringBoot MCP connection contract (what the `MultiServerMCPClient` config must supply).** The
Java server is implemented and live; its authoritative contract is the [Java spec](../../ecommerce-mcp-server/docs/2026-06-05-ecommerce-mcp-server-spec.md):

- **Transport / endpoint:** streamable-HTTP at `POST /mcp` (`spring.ai.mcp.server.protocol=STREAMABLE`).
- **Required headers on every call** (set by FastAPI on the client connection, **never** by the LLM):
  `X-Service-Token` (must match the server's `APP_SERVICE_TOKEN`, else 401), plus `X-User-Id` and
  `X-Session-Id` — the trusted actor the server binds approvals/writes to. The agent cannot pass
  identity as a tool parameter; it is derived server-side from these headers (§5.2).
- **Approval transition is REST, not MCP:** `GET/POST /approvals/{id}[/approve|/reject]` carry the
  same headers; these are deliberately absent from the agent's tool list so the agent cannot approve
  its own request.

### 8.3 SpringBoot Business Capability List

This table describes SpringBoot business capabilities and their target agent/backend surface. In
the target product model, only **Read** tools and `request_approval` are agent-reachable MCP tools.
The write rows are backend-executed operations keyed by `approval_id`, not tools exposed to the
LLM. Visualization (`generate_visualization`) and sandbox tools (`run_code`, `read_uploaded_file`,
`write_report`) are served by the ModelScope and Python MCP servers respectively (§8.2).

Tool names follow a consistent `{domain}_{action}` convention.

> **Target model (vs as-built).** Sub-agents reference the **Read** tools + `request_approval`
> only. The three **Write** rows below (`purchase_order_create`, `purchase_order_receive`,
> `order_update`) are **not** agent-reachable in the target model — they run in the deterministic
> backend executor keyed by `approval_id` (§5.2). The implemented Java server currently exposes them
> as `@McpTool`s taking `approval_id` + params; aligning it (execute-by-`approval_id`, removed from
> the agent surface) is the tracked Java companion change (§5.2, roadmap §5).

| Capability | Surface | Service Method | Description |
|----------|------|----------------|-------------|
| product_query | Read | ProductService.page | Query product catalog with pagination and category filter |
| product_search | Read | ProductService.search | Search products by name (fuzzy) |
| order_query | Read | OrderService.searchDetails | Search customer sales orders + items with date/product filters |
| inventory_query | Read | InventoryService.query | Query inventory levels for products/warehouses |
| inventory_low_stock | Read | InventoryService.findLowStockItems | List items below safety stock |
| user_query | Read | UserService.query | Query customer accounts (by name/level/id) |
| supplier_query | Read | SupplierService.search | Search suppliers by name (fuzzy) |
| supplier_top | Read | SupplierService.findTopSuppliers | List top suppliers by rating and lead time |
| purchase_order_query | Read | PurchaseOrderService.query | Query supplier purchase orders |
| get_statistics | Read | StatsService.get | Aggregated business statistics (top sellers = realized sales: paid/shipped/completed) |
| purchase_order_create | Backend execute | PurchaseOrderService.create | Create a supplier purchase order — executed from stored approval payload |
| purchase_order_receive | Backend execute | PurchaseOrderService.receive | Mark PO received → increment inventory — executed from stored approval payload |
| order_update | Backend execute | OrderService.update | Update a customer order's fulfillment status — executed from stored approval payload |
| request_approval | Propose | ApprovalService.create | Agent-reachable proposal tool: build canonical authorization payload, create pending approval + server-rendered card, return approval_id |

### 8.4 SpringBoot Read Tools And Backend Executor (Spring AI)

Read/propose tools are registered directly in SpringBoot with Spring AI's `@Tool` annotation.
Spring AI generates the MCP tool schema from the method signature and Javadoc/`@ToolParam`, and the
`spring-ai-starter-mcp-server-webmvc` starter exposes them over streamable-HTTP/SSE for the
DeepAgents client to connect. Backend write operations stay in the service layer and execute only
from stored approval payloads keyed by `approval_id`.

```java
// tool/ProductTools.java
@Component
public class ProductTools {

    private final ProductService productService;

    @Tool(name = "product_query",
          description = "分页查询商品列表，支持按名称模糊查询和分类筛选。")
    public PageResult<ProductDTO> queryProducts(
            @ToolParam(required = false, description = "分类") String category,
            @ToolParam(required = false, description = "名称模糊匹配") String name,
            @ToolParam(description = "页码") int current,
            @ToolParam(description = "每页数量") int size) {
        return productService.page(category, name, current, size);
    }
}
```

Write operations (`purchase_order_create`, `purchase_order_receive`, `order_update`) execute in the
service layer keyed by `approval_id`, loading the stored canonical payload and validating the §5.2
contract before executing. Approval enforcement lives entirely in Java, alongside the writes it
guards. **These are not agent-reachable `@McpTool`s in the target model** — they run in the
deterministic backend executor (§5.2). Aligning the implemented Java server to this is the tracked
companion change (§5.2, roadmap §5).

### 8.5 SpringBoot Project Structure

Standard layered architecture, exposing read/propose business capability as **MCP tools** (Spring AI)
instead of a hand-written REST API. A thin tool layer wraps the existing services; approved writes
execute through the service layer from durable approval records.

- **Tool (MCP layer):** `@Tool`-annotated `@Component` classes. Spring AI derives MCP schemas and serves them over streamable-HTTP/SSE. Thin — delegates straight to services.
- **Controller (REST layer):** the authenticated approve/reject/read approval endpoints only (human-driven, not MCP). See Java spec §4.4.
- **Service (Business logic):** Validation, filtering, aggregation, approval enforcement. Each table has a dedicated service class.
- **Mapper (MyBatis):** SQL queries with dynamic conditions. Uses XML mapper files for complex queries.

Package structure:
```
com.ecommerce.agent/
├── tool/           # @Tool MCP tools (exposed via Spring AI)
├── controller/     # Approval REST endpoints (§5.2) — human/FastAPI only, NOT MCP
├── service/        # Business logic + approval enforcement
├── mapper/         # MyBatis mappers
├── entity/         # MyBatis entities
├── dto/            # Tool request/response DTOs
└── config/         # Spring configuration (MCP server, datasource, auth)
```

> The `controller/` layer is required for the human approval transition (§5.2). All business
> reads/writes stay on the MCP `@Tool` path.

## 9. Visualization

Use MCP visualization tools from ModelScope community (same as ERP_OPENCLAW).

Merge 26 chart tools into 1 `generate_visualization` entry:
- Tool description contains compact reference table (~800 tokens)
- Full parameter schema stored in sandbox `/skills/analyst/chart_params.md`
- Agent reads reference file when uncertain, then calls tool

Supported chart types: line, bar, column, pie, area, scatter, radar, funnel, sankey, waterfall, dual_axes, heatmap, treemap, word_cloud, mind_map, flow_diagram, etc.

## 10. File Upload & Report Generation

### 10.1 File Upload

Users can upload files (CSV, Excel) for Agent analysis. Flow:

1. Frontend uploads file via `POST /api/upload`
2. FastAPI receives the upload stream and immediately writes it to Sandbox `/workspace/uploads/{session_id}/{filename}`
3. Agent uses `read_uploaded_file` tool to parse file in Sandbox
4. Agent analyzes data with `run_code` and responds

Uploaded files are not persisted on host disk. The service process receives bytes over HTTP, validates filename/type/size, writes them to Sandbox, and only exposes paths under `/workspace/uploads/{session_id}`.

### 10.2 Report Generation

Agent can generate Markdown reports on demand. Flow:

1. User says "generate a monthly sales report"
2. Agent gathers data via MCP tools
3. Agent uses `write_report` tool — assembles Markdown in Sandbox via `run_code`
4. Report saved to Sandbox `/workspace/reports/{session_id}/`, registered under a generated `report_id`
5. User downloads via `GET /api/download?report_id={id}` — the server resolves `report_id` →
   path scoped to the caller's own session

**No raw `path` parameter.** Downloads are addressed by `report_id` (or a signed, session-bound
token), never by a client-supplied filesystem path — this prevents path traversal and
cross-session file access. The server validates the report belongs to the requesting session
before streaming it.

No PDF generation — Markdown only. Keeps it cross-platform and simple.

## 11. Operator Console

The frontend is an operator console, not a decorative chat shell. It can reuse ERP_OPENCLAW Vue 3
components where useful, but the product surface is defined by trust and work visibility.

Core surfaces:
- **Conversation + streaming answer** — SSE chat remains the main interaction pattern.
- **Tool trace timeline** — show read tools, sandbox execution, visualization calls, and write
  proposals in a human-readable sequence.
- **Artifacts panel** — chart specs/rendered charts, generated Markdown reports, exported data
  snippets, and approval cards.
- **HITL approval workspace** — server-rendered action cards with canonical payload, diff/impact,
  expiry, actor/session binding, approve/reject controls, and final execution status.
- **Session history** — conversation, artifacts, approvals, and audit references grouped by
  session.
- **Health/operator checks** — dependency status for MCP servers, sandbox, model provider, and
  database connectivity. `/health/mcp` is a live operator probe, not a high-frequency poll target.

Product rule: the console should make agent work **inspectable and reversible where possible**.
Do not hide tool calls, approval preconditions, or generated artifacts behind a pure chat transcript.

## 12. Technology Stack

| Layer | Technology | Source |
|-------|-----------|--------|
| Agent framework | DeepAgents + LangGraph | Both projects |
| Web framework | FastAPI + uvicorn | Both projects |
| Real-time comms | WebSocket full-chain monitoring | Deep Search Pro |
| Streaming output | SSE | ERP_OPENCLAW |
| Session isolation | ContextVar (coroutine-level) | Deep Search Pro |
| Session persistence | MongoDB checkpoint (conversation continuity only — not write safety, §5.2) | ERP_OPENCLAW |
| Approved-action lifecycle | Durable MySQL `approval_record` + deterministic backend executor (§5.2) | Product decision |
| Code execution | DockerSandbox now; managed sandbox/remote executor swappable | Product decision |
| File operations | CompositeBackend routing | ERP_OPENCLAW |
| Tool protocol | MCP (Spring AI MCP server + MultiServerMCPClient) | New (replaces FastMCP proxy) |
| Tool merging | 26→1 compact reference strategy | ERP_OPENCLAW |
| Business backend | SpringBoot MCP server (Spring AI @Tool) + MyBatis + MySQL | New |
| Prompt management | YAML configuration | Deep Search Pro |
| LLM | Configurable OpenAI-compatible models (`deepseek-chat` default + optional qwen fallback) | Simplified from ERP_OPENCLAW |
| Frontend | Vue 3 (adapted) | ERP_OPENCLAW |
| Visualization | ModelScope MCP charts | ERP_OPENCLAW |
| File upload | Sandbox storage | Deep Search Pro |
| Report generation | Markdown via Sandbox run_code | Deep Search Pro (simplified) |

## 13. Product Roadmap

The detailed roadmap lives in
[2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md). The parent spec keeps the product
direction; the roadmap owns sequencing and cut lines.

### Milestone 1: Trusted Read-Only Analysis Workspace

Status: Week 1 foundation complete; Week 2 in design.

- FastAPI/SSE + DeepAgents connected to the SpringBoot MCP server.
- Read-only SpringBoot tool allowlist enforced before the agent can use tools.
- Single read-only `sales-analyst` runtime agent; coordinator/sub-agent routing remains a dormant
  seam until M2.
- Sandboxed Python analysis for computations SpringBoot stats do not already own.
- Declarative chart artifact generation through a visualization seam.
- Operator-visible traces for tools and artifacts.

### Milestone 2: Approved Action Workflow

- `order-manager` sub-agent with **reads + `request_approval` only** — no write tools in the LLM's
  hands.
- Propose → approve → backend-execute, with a **durable MySQL `approval_record`** as the
  pending-action lifecycle (no MongoDB checkpoint/resume needed for write safety, §5.2).
- Server-rendered approval cards, canonical operation hashes, one-time-use approvals, expiry, and
  actor/session binding.
- **Deterministic backend executor keyed by `approval_id`**: SpringBoot loads the stored canonical
  payload and executes; the LLM never issues write params. Requires the Java companion change (§5.2).

### Milestone 3: Operator Console

- Conversation, tool timeline, artifacts panel, approval workspace, session history, and dependency
  health views.
- Markdown report downloads and chart rendering.
- Audit references for approvals and executed actions.

### Milestone 4: Product Hardening

- Multi-user session isolation, role-based permissions, audit search, model/provider fallback,
  evaluation suite, dependency-bump live smoke gates, and deployment packaging.
- Memory and skills are product hardening/stretch features, not prerequisites for the first two
  milestones.

### Future Expansion

- Additional domain agents: `customer-insight`, `procurement-planner`, `catalog-manager`.
- External connectors and additional MCP servers.
- A2A-style peer agents only when integrating independent external agent systems; MCP remains the
  main tool/data protocol for this product.
