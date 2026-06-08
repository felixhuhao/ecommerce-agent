# E-Commerce AI Assistant — Design Spec

> 电商运营智能助手：基于 DeepAgents + SpringBoot 的多 Agent 系统
> Status: Draft | Date: 2026-05-25

## 1. Project Overview

**What:** An intelligent e-commerce operations assistant that uses multi-agent architecture to handle sales analysis, inventory management, order processing, and data visualization through natural language conversation.

**Why:** Portfolio project demonstrating Agent intelligence, end-to-end architecture, and Java backend engineering. Built by analyzing two existing projects (ERP_OPENCLAW, Deep Search Pro) and combining their strengths.

**Who:** Interview showcase. Target audience: technical interviewers evaluating AI/Agent and backend engineering skills.

**Timeline:** 4 weeks.

## 2. Architecture

### 2.1 System Diagram

```
Vue 3 Frontend (adapted from ERP_OPENCLAW)
  ├── SSE streaming chat
  ├── WebSocket real-time progress
  └── HITL approval cards
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
(readonly tools)       (write tools + HITL)
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
  │   OpenSandbox               │
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

### 2.2 Security Model: Sandbox as Agent Backend

OpenSandbox is configured as the DeepAgents backend (same as ERP_OPENCLAW). All Agent file operations (`write_file`, `read_file`) are routed to Sandbox automatically through the framework layer. The security purpose is **code execution isolation** — Agent-generated Python code runs inside Sandbox, not on the host machine.

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

Use Python `ContextVar` for asyncio coroutine-level isolation. Each user session gets a unique UUID and dedicated directory. MongoDB handles checkpoint persistence for HITL interrupt/resume across restarts.

`ContextVar` only isolates within a single coroutine context — it does **not** automatically protect shared singletons. The session UUID must be propagated explicitly to every boundary that leaves the request coroutine:

- **WebSocket tasks** and **background jobs** — set the ContextVar at task entry; spawned tasks don't inherit it for free.
- **MCP clients** — propagate session/user identity as trusted request metadata (service-authenticated headers/JWT/session binding), not as Agent-controlled tool parameters. `request_approval` and write tools bind to this trusted identity — see §5.2.
- **Checkpoint `thread_id`** — derive from the session UUID so HITL resume targets the correct conversation.
- **Sandbox directories** — namespace under `{session_id}` (uploads, reports, skills) and never accept client-supplied paths.
- **Cleanup** — define when a session's sandbox dir / checkpoints are reaped (e.g. TTL or on session close) to avoid unbounded growth and stale-state leakage.

## 3. Database Design

Database: `ecommerce_db` (MySQL)

### 3.1 Tables

Two distinct order documents, as in real ops systems: **customer sales orders** (`orders`,
**read-mostly** — queried for analytics, with fulfillment-status updates allowed only via the
approved `order_update` tool) and **supplier purchase orders** (`purchase_order`, the write path
for restocking). They have different lifecycles and must not be conflated.

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
  are created live through the order-manager write flow

## 4. Agent Architecture

### 4.1 Main Agent

Role: "E-commerce Operations AI Assistant" — coordinator, not executor.

The main agent analyzes user intent and routes to appropriate sub-agents. It never calls business MCP tools directly.

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
- "Compare sales by category"
- "Which products need restocking?"
- "Supplier performance radar chart"
- "Generate a monthly sales report"
- "Analyze this uploaded CSV file"

### 4.3 Sub-Agent: order-manager

Role: Order management specialist. Handles **procurement** (supplier purchase orders) and
**customer order fulfillment**. Write access with mandatory HITL. All tools are SpringBoot
business tools (see §8.3).

Reads:
- `purchase_order_query` — query supplier purchase orders (read)
- `order_query` — query customer sales orders (read)
- `inventory_query` — check stock before ordering (read)
- `supplier_query` — look up a supplier for a PO (read)

Writes (each requires HITL):
- `purchase_order_create` — create a supplier purchase order (restock)
- `purchase_order_receive` — mark a PO received → increment inventory
- `order_update` — update a customer order's fulfillment status (e.g. shipped, cancelled)
- `request_approval` — trigger HITL approval workflow

Typical tasks:
- "Restock 500 phone cases from supplier A" → `purchase_order_create`
- "Mark PO #88 as received" → `purchase_order_receive` (inventory +500)
- "Change order #12345 status to shipped" → `order_update`

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

The MVP write tools (§8.3) cover Create + Modify only. Delete and Batch are documented here so
the approval framework anticipates them, but no MVP tool performs them — add `order_cancel`
(batch) and any delete tool with the double-confirm flow when needed.

### 5.2 Implementation Flow (Server-Enforced Approval)

Write operations are **server-enforced**, not prompt-based. SpringBoot rejects any write request without a valid `approval_id`.

```
Agent wants to create a PO / receive a PO / update a customer order
→ Agent calls request_approval(tool_name, operation_params)   ← structured params only, no prose/identity
→ SpringBoot resolves trusted user_id/session_id from request metadata
→ SpringBoot reads live DB rows and builds canonical operation_payload =
  operation_params + server-derived preconditions/snapshot
  (e.g. current order/PO status, inventory quantity, supplier/product/unit cost)
→ SpringBoot computes operation_hash AND renders the human-facing card (summary/diff/impact)
  from the canonical payload + live DB state — the Agent never authors what the human sees
→ creates pending approval_record (bound to user_id + session_id + tool_name)
→ Agent execution interrupted with {approval_id} → MongoDB checkpoint persists interrupt state
→ Frontend fetches the server-rendered card via GET /approvals/{id} and displays it
→ Human approves via POST /approvals/{id}/approve  (authenticated, NOT an MCP tool)
  → Server marks approval_id as approved
  → Resume from checkpoint → Agent receives approval_id
→ Agent calls write tool with approval_id + same operation params
→ SpringBoot re-derives trusted identity + current DB preconditions, then validates the Java spec §4 contract (exists+approved, hash, tool/actor/session,
  not expired, one-time use)
  → Valid → execute operation, mark consumed
  → Invalid → reject with error
→ Human rejects via POST /approvals/{id}/reject → Agent receives rejection reason → responds
```

**Why operation_hash:** Prevents the Agent from modifying the operation payload after approval. The hash is computed from the exact, canonically-serialized authorization payload before the approval card is shown: Agent operation params plus server-derived DB preconditions/snapshot. If the Agent sends different parameters, or if relevant DB state changes before execution, SpringBoot rejects it and requires a fresh approval.

**Why the approve/reject endpoints are not MCP tools:** approval is a *human* action. Exposing it as a tool would let the Agent approve its own requests. It lives on an authenticated REST endpoint the frontend/FastAPI calls on the human's behalf — never in the Agent's tool list. The full Java contract is in the Java spec §4.

**SpringBoot approval table:**

```sql
CREATE TABLE approval_record (
  approval_id      VARCHAR(36) PRIMARY KEY,
  operation_hash   VARCHAR(64) NOT NULL,        -- canonical hash of operation_payload
  tool_name        VARCHAR(40) NOT NULL,        -- write tool this approval authorizes
  operation_type   VARCHAR(20) NOT NULL,
  operation_payload JSON NOT NULL,              -- canonical params + server preconditions/snapshot (hashed)
  operation_detail JSON NOT NULL,               -- server-rendered card (from canonical payload + DB; not Agent prose)
  user_id          BIGINT NOT NULL,             -- actor binding
  session_id       VARCHAR(64) NOT NULL,        -- session binding
  status           VARCHAR(10) NOT NULL DEFAULT 'pending',  -- pending|approved|consumed|rejected|expired
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at       DATETIME NOT NULL,
  consumed_at      DATETIME NULL,               -- one-time use
  KEY idx_status (status)
);
```

Write tools (`purchase_order_create`, `purchase_order_receive`, `order_update`) take `approval_id` as a parameter. The service layer checks validity before executing: approval exists, `status=approved`, `operation_hash` matches the rebuilt canonical payload (incoming params + current DB preconditions), the trusted request `user_id`/session matches, the approval has not expired, and it has not already been consumed (one-time use). See the Java spec §4 for the full enforcement contract.

## 6. Memory System

### 6.1 Four-Layer Architecture

| Layer | Storage | Purpose | Implementation |
|-------|---------|---------|---------------|
| Short-term | MongoDB checkpoint | Conversation state, HITL resume | LangGraph checkpointer |
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
1. **SpringBoot MCP Server** — business tools (query/create/update) + `request_approval`, exposed via Spring AI `@Tool` over streamable-HTTP/SSE
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

### 8.3 MCP Tool List

All tools **in this table** are SpringBoot `@Tool` methods exposed directly over MCP (Spring AI);
the "Service Method" column is the business method each delegates to. Visualization
(`generate_visualization`) and sandbox tools (`run_code`, `read_uploaded_file`, `write_report`)
are **not** here — they are served by the ModelScope and Python MCP servers respectively (§8.2).

Tool names follow a consistent `{domain}_{action}` convention. These are the exact names the sub-agents in §4 reference.

| MCP Tool | Type | Service Method | Description |
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
| purchase_order_create | Write | PurchaseOrderService.create | Create a supplier purchase order — restock (requires approval_id) |
| purchase_order_receive | Write | PurchaseOrderService.receive | Mark PO received → increment inventory (requires approval_id) |
| order_update | Write | OrderService.update | Update a customer order's fulfillment status (requires approval_id) |
| request_approval | Write | ApprovalService.create | Build canonical authorization payload (params + server preconditions), create pending approval + server-rendered card, return approval_id |

### 8.4 SpringBoot MCP Tools (Spring AI)

Business tools are registered directly in SpringBoot with Spring AI's `@Tool` annotation. Spring AI generates the MCP tool schema from the method signature and Javadoc/`@ToolParam`, and the `spring-ai-starter-mcp-server-webmvc` starter exposes them over streamable-HTTP/SSE for the DeepAgents client to connect.

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

Write tools (`purchase_order_create`, `purchase_order_receive`, `order_update`) validate `approval_id` + `operation_hash` in the service layer before executing (see §5.2). Approval enforcement therefore lives entirely in Java, alongside the writes it guards.

### 8.5 SpringBoot Project Structure

Standard layered architecture, exposing business capability as **MCP tools** (Spring AI) instead of a hand-written REST API. A thin tool layer wraps the existing services.

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

## 11. Frontend

Adapted from ERP_OPENCLAW Vue 3 frontend. Changes:
- Replace motorcycle parts terminology with e-commerce
- Adjust sidebar for e-commerce session history
- Keep: SSE streaming chat, HITL approval banner, chart rendering, markdown display

Not a focus area. "Good enough to demo" is the bar.

## 12. Technology Stack

| Layer | Technology | Source |
|-------|-----------|--------|
| Agent framework | DeepAgents + LangGraph | Both projects |
| Web framework | FastAPI + uvicorn | Both projects |
| Real-time comms | WebSocket full-chain monitoring | Deep Search Pro |
| Streaming output | SSE | ERP_OPENCLAW |
| Session isolation | ContextVar (coroutine-level) | Deep Search Pro |
| Session persistence | MongoDB checkpoint | ERP_OPENCLAW |
| Code execution | OpenSandbox (full sandbox model) | ERP_OPENCLAW |
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

## 13. Development Roadmap

> **Scope reality check:** this 4-week plan spans a lot (SpringBoot MCP, FastAPI, DeepAgents,
> Sandbox, MongoDB checkpoint, HITL, memory, visualization, upload, Vue). For a portfolio MVP,
> treat **read-only analysis + one chart** (Weeks 1–2) and the **HITL purchase-order write**
> (Week 3) as the marquee features that must land; memory layers, skills, and frontend polish
> are the first things to cut if time runs short.

### Week 1: Foundation

- SpringBoot project setup + MySQL schema (9 tables incl. `purchase_order*` + `approval_record`) *(7 base tables + seed already done; PO/approval tables to add)*
- Seed sample data (50 products, 200 customer orders, 30 users, 10 suppliers, a few historical POs) *(base done)*
- SpringBoot MCP server (Spring AI) exposing 6 read-only `@Tool` methods over streamable-HTTP/SSE
- FastAPI project setup (web/SSE/WebSocket service layer for the frontend)
- Single agent + `MultiServerMCPClient` connecting to the SpringBoot MCP server
- SSE streaming response
- **Demo:** "Check phone category inventory" → Agent calls SpringBoot MCP tool → returns data

### Week 2: Sub-Agents + Sandbox

- Split into sales-analyst and order-manager sub-agents
- Migrate prompts to YAML configuration
- Integrate OpenSandbox for code execution
- Implement `run_code` tool (query results → sandbox file → pandas analysis)
- Implement 26→1 visualization tool merging
- **Demo:** "Compare sales by category" → Agent analyzes → generates bar chart

### Week 3: HITL + Memory

- MongoDB checkpoint integration
- Implement `request_approval` + full enforcement (canonical hash incl. server preconditions, status, actor/session, expiry, one-time use)
- Write tools: `purchase_order_create`, `purchase_order_receive`, `order_update`
- ContextVar session isolation
- User preference read/write (preferences.md)
- Skill creation with TTL and grading
- AGENTS.md guidance loading
- **Demo:** "Restock 500 phone cases from supplier A" → approval card → confirm → PO created → receive → inventory +500

### Week 4: Frontend + Polish

- Adapt ERP_OPENCLAW Vue 3 frontend for e-commerce
- HITL approval card component
- WebSocket progress monitoring
- Enrich sample data for convincing demo
- Tune YAML prompts for better responses
- End-to-end testing
- Record demo video
- **Demo:** Full conversation → analysis → chart → order → approval flow
