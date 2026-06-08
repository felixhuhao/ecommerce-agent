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
Week 1 enables only the SpringBoot business MCP server; ModelScope and Python
MCP URLs are reserved for later phases.

## Local App

```bash
uv sync
uv run uvicorn ecommerce_agent.api.app:create_app --factory --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/mcp
```

`/health/mcp` probes configured external MCP servers and reports `degraded`
when a dependency is unavailable. It does not start or repair external services.

## Tests

```bash
uv run pytest
uv run ruff check .
```

The Spring MCP integration test is part of the default suite, but it skips with a
clear message when `SPRING_MCP_URL` is not reachable. Start the Spring Boot server
from `../ecommerce-mcp-server` to exercise the real MCP boundary.

Run the opt-in live LLM smoke when both the Spring MCP server and `LLM_API_KEY`
are available:

```bash
RUN_LIVE_LLM=1 uv run pytest -m live
```
