# E-Commerce Agent

Python/Agent side of the E-Commerce AI Assistant. The completed Spring Boot MCP
server lives in `../ecommerce-mcp-server`; this repo owns the FastAPI service,
agent orchestration, streaming API, and later sandbox/HITL/frontend layers.

## External Dependencies

Week 1 assumes the Spring Boot MCP server is already running. This repo does not
start, seed, or reset MySQL; that lifecycle belongs to `../ecommerce-mcp-server`
and your local environment.

Run the server from the sibling repo when needed:

```bash
cd ../ecommerce-mcp-server
APP_SERVICE_TOKEN=dev-service-token ./mvnw spring-boot:run
```

Alternatively, set `APP_SERVICE_TOKEN` in that repo's ignored `.env.properties`.
It must match this repo's `SPRING_MCP_SERVICE_TOKEN`, or `/mcp` will return
`401` even though `/actuator/health` is green.

Check the server:

```bash
curl http://localhost:8080/actuator/health
```

The agent project calls the MCP endpoint configured by `SPRING_MCP_URL`, default:
`http://localhost:8080/mcp`.

MCP requests must include trusted headers:

```text
X-Service-Token: dev-service-token
X-User-Id: 1
X-Session-Id: local-session
```

Copy `.env.example` to `.env` and adjust values for your local MCP servers.
Week 1 requires only the SpringBoot business MCP server. The optional
ModelScope/AntV chart MCP server can be enabled for chart-tool smoke tests:

```bash
docker compose -f compose.chart-mcp.yml up chart-mcp
```

Then set:

```env
MODELSCOPE_MCP_URL=http://127.0.0.1:1122/mcp
```

The agent allowlists a small chart surface from that server:
`generate_line_chart`, `generate_bar_chart`, and `generate_column_chart`.
The Compose file also starts a lightweight local renderer stub and wires AntV's
`VIS_REQUEST_SERVER` to it, so backend chart-tool smoke tests do not depend on
the public AntV render endpoint. UI-grade chart rendering belongs to the later
operator console/artifact milestone. If `1122` is already in use, run:

```bash
CHART_MCP_PORT=1123 docker compose -f compose.chart-mcp.yml up chart-mcp
```

and set `MODELSCOPE_MCP_URL=http://127.0.0.1:1123/mcp`.

## Agent-Owned Infrastructure

M2 adds a server-owned conversation thread backed by MongoDB. This repo owns the
agent-side stack — Mongo, the chart MCP server, and the chart renderer — as
Compose projects. The Java MCP server and MySQL remain a separate backend stack
in `../ecommerce-mcp-server` with their own lifecycle; this repo does not start,
seed, or reset them.

Start the full agent-owned stack with one command:

```bash
docker compose -f docker-compose.yml -f compose.chart-mcp.yml up -d
```

This brings up Mongo (`docker-compose.yml`, named volume `mongo-data`) plus the
chart MCP server and renderer (`compose.chart-mcp.yml`). To start only what a
given task needs, target individual services instead.

Start Mongo when exercising sessions or the approval workflow:

```bash
docker compose up -d mongo
```

The default `.env.example` values point the app at that service:

```env
MONGO_URL=mongodb://localhost:27017
MONGO_DB=ecommerce_agent
APPROVAL_API_BASE_URL=http://localhost:8080
```

If `27017` is already in use, run:

```bash
MONGO_PORT=27018 docker compose up -d mongo
```

and set `MONGO_URL=mongodb://localhost:27018`.

## Sandbox Container Cleanup

The agent runs analytical code in per-session sandbox containers named
`ecommerce-sandbox-*` (labeled `com.ecommerce-agent.sandbox=true`). They are
removed when their session is closed or evicted, but containers left over from
debugging or crashes can linger. Remove all stale agent sandbox containers:

```bash
uv run python -m ecommerce_agent.sandbox.cleanup
```

This force-removes only containers carrying the `com.ecommerce-agent.sandbox=true`
label or whose name starts with `ecommerce-sandbox-` (so legacy unlabeled
containers are caught too). It never touches Mongo, the chart services, or any
other container.

## Local App

```bash
uv sync
uv run ecommerce-agent serve --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/mcp
```

`/health/mcp` probes configured external MCP servers and reports `degraded`
when a dependency is unavailable. It does not start or repair external services.
Because it performs live tool discovery, treat it as an operator check rather than
a high-frequency poll target.

## Tests

```bash
uv run pytest
uv run ruff check .
```

The Spring MCP integration test is part of the default suite, but it skips with a
clear message when `SPRING_MCP_URL` is not reachable or `/mcp` rejects the configured
service token. Start the Spring Boot server from `../ecommerce-mcp-server` with a
matching `APP_SERVICE_TOKEN` to exercise the real MCP boundary.

Run the opt-in live LLM smoke when both the Spring MCP server and `LLM_API_KEY`
are available:

```bash
RUN_LIVE_LLM=1 uv run pytest -m live
```

When `MODELSCOPE_MCP_URL` is set, the live reliability harness also requires the
hero run to call one allowlisted chart tool.

Run the opt-in M2 approval loop when Spring MCP/MySQL and MongoDB are both
available:

```bash
docker compose up -d mongo
RUN_M2_APPROVAL_INTEGRATION=1 uv run pytest tests/integration/test_m2_approval_loop.py -v
```

This creates real pending approvals through the Spring MCP `request_approval`
tool, then drives FastAPI approve/reject/execute orchestration against Java REST
and verifies Mongo thread reload/stream replay. The Java companion migration must
have widened `approval_record.status` to `VARCHAR(20)` so the `invalidated` and
`failed` terminal states fit.
