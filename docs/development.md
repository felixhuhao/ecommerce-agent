# Development Guide

Operational setup for the Python/Agent side of the E-Commerce AI Assistant. The
Spring Boot MCP server lives in `../ecommerce-mcp-server`; this repo owns the
FastAPI service, agent orchestration, streaming API, sandbox executor, approval
orchestration, monitoring, and frontend.

## External Dependencies

Local development assumes the Spring Boot MCP server is already running. This
repo does not start, seed, or reset MySQL; that lifecycle belongs to
`../ecommerce-mcp-server` and your local environment.

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
The default runtime requires the Spring Boot business MCP server. Charts render
through the first-party `create_chart_spec` tool as ECharts artifacts; no
external chart MCP server is required.

## Agent-Owned Infrastructure

This repo owns a server-owned conversation thread backed by MongoDB, plus the
agent-side stack — Mongo and the local sandbox executor — as Compose projects.
The Java MCP server and MySQL remain a separate backend stack in
`../ecommerce-mcp-server` with their own lifecycle; this repo does not start,
seed, or reset them.

Start the full agent-owned stack with one command:

```bash
docker compose -f docker-compose.yml -f compose.sandbox.yml up -d
```

This all-services command brings up Mongo (`docker-compose.yml`, named volume
`mongo-data`) and the local sandbox executor service (`compose.sandbox.yml`).
For normal local development, prefer:

```bash
docker compose -f docker-compose.yml -f compose.sandbox.yml up -d
```

To start only what a given task needs, target individual services instead.

Start Mongo when exercising sessions or the approval workflow:

```bash
docker compose up -d mongo
```

The default `.env.example` values point the app at that service:

```env
MONGO_URL=mongodb://ecommerce_agent:dev-mongo-password@localhost:27017/?authSource=admin
MONGO_DB=ecommerce_agent
APPROVAL_API_BASE_URL=http://localhost:8080
```

If `27017` is already in use, run:

```bash
MONGO_PORT=27018 docker compose up -d mongo
```

and set `MONGO_URL=mongodb://ecommerce_agent:dev-mongo-password@localhost:27018/?authSource=admin`.

## Sandbox Executor

Local development defaults to the long-lived sandbox executor service:

```env
SANDBOX_BACKEND=remote
SANDBOX_EXECUTOR_URL=http://localhost:8006
```

Keep `SANDBOX_BACKEND=docker` as a fallback when you need the older
per-session DockerSandbox path.

## Sandbox Container Cleanup

When `SANDBOX_BACKEND=docker`, analytical code runs in per-session sandbox
containers named `ecommerce-sandbox-*` (labeled
`com.ecommerce-agent.sandbox=true`). They are removed when their session is
closed or evicted, but containers left over from debugging or crashes can
linger. Remove all stale agent sandbox containers:

```bash
uv run python -m ecommerce_agent.sandbox.cleanup
```

This force-removes only containers carrying the `com.ecommerce-agent.sandbox=true`
label or whose name starts with `ecommerce-sandbox-` (so legacy unlabeled
containers are caught too). It never touches Mongo, the chart services, the
sandbox executor, or any other container.

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

## Docker App

Build and run the FastAPI app, Mongo, and sandbox executor through Docker:

```bash
docker compose -f docker-compose.yml -f compose.sandbox.yml up -d --build app sandbox-executor mongo
```

The app is published on `127.0.0.1:${ECOMMERCE_AGENT_PORT:-8010}` and serves the
built React SPA from the same origin. Inside Docker, the app uses container-safe
URLs:

```env
MONGO_URL=mongodb://...@mongo:27017/?authSource=admin
SANDBOX_BACKEND=remote
SANDBOX_EXECUTOR_URL=http://sandbox-executor:8000
SPRING_MCP_URL=http://host.docker.internal:8080/mcp
APPROVAL_API_BASE_URL=http://host.docker.internal:8080
```

Check the running app:

```bash
curl http://127.0.0.1:${ECOMMERCE_AGENT_PORT:-8010}/health
docker exec ecommerce-agent-app python -c 'import urllib.request; print(urllib.request.urlopen("http://sandbox-executor:8000/health", timeout=3).read().decode())'
```

If the optional NL2SQL backend is running from the sibling `nl2sql_pro` compose
project, include the NL2SQL overlay so the app joins that Docker network instead
of using a host-local URL:

```bash
docker compose -f docker-compose.yml -f compose.sandbox.yml -f compose.nl2sql.yml up -d --build app sandbox-executor mongo
curl http://127.0.0.1:${ECOMMERCE_AGENT_PORT:-8010}/health/mcp
```

With the overlay, `nl2sql` should report `ok` in `/health/mcp`. Without the
overlay, NL2SQL is disabled for the Docker app even if `.env` contains a local
`NL2SQL_MCP_URL`.

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

The live reliability harness requires chart prompts to call `create_chart_spec`.

Run the opt-in approval workflow integration when Spring MCP/MySQL and MongoDB
are both available:

```bash
docker compose up -d mongo
RUN_M2_APPROVAL_INTEGRATION=1 uv run pytest tests/integration/test_m2_approval_loop.py -v
```

The flag name is historical; the test covers the current approval workflow.

This creates real pending approvals through the Spring MCP `request_approval`
tool, then drives FastAPI approve/reject/execute orchestration against Java REST
and verifies Mongo thread reload/stream replay. The Java companion migration must
have widened `approval_record.status` to `VARCHAR(20)` so the `invalidated` and
`failed` terminal states fit.
