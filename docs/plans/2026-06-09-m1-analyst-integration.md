# M1 Analyst Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the M1 **sales-analyst** runtime agent: YAML prompts, the ModelScope `generate_visualization` seam, a single deep agent built on the `DockerSandbox` backend (Plan 1), and a one-shot live smoke of the forecast hero flow.

**Architecture:** A single `create_deep_agent` instance (no coordinator) gets the 10 read-only SpringBoot tools + `generate_visualization`; `execute`/file tools come from the shared `DockerSandbox` backend. The agent is built lazily on first chat (the Week 1 pattern), now with the backend + viz tools. Prompts live in `prompts/prompts.yml`. The coordinator/sub-agent shape exists only as a dormant factory seam for M2.

**Tech Stack:** Python 3.12, `deepagents` 0.6.8 (`create_deep_agent(model, tools, *, system_prompt, subagents, middleware, skills, backend)`), `langchain-mcp-adapters` (`MultiServerMCPClient`), `pyyaml`, FastAPI/SSE, `pytest`.

**Prerequisites:** Plan 1 merged (`DockerSandbox`, `sandbox/config.py`, sandbox image, `ecommerce_analysis`). Spec: [docs/2026-06-09-week2-subagents-sandbox-design.md](../2026-06-09-week2-subagents-sandbox-design.md) §3, §5, §6, §7.

**Scope note:** Plan 2 of 3. No trace module / eval harness here (Plan 3). The live smoke here is the single-run rehearsed hero; the N-run structural reliability harness is Plan 3.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` (modify) | Add `pyyaml` dep. |
| `src/ecommerce_agent/prompts/__init__.py` (create) | Package marker. |
| `src/ecommerce_agent/prompts/prompts.yml` (create) | `sales_analyst` prompt (+ dormant `coordinator` seam). |
| `src/ecommerce_agent/prompts/analysis_helpers.md` (create) | Compact `ecommerce_analysis` API reference the prompt points to. |
| `src/ecommerce_agent/prompts/loader.py` (create) | Typed YAML loader (`get_prompt`). |
| `src/ecommerce_agent/mcp_client.py` (modify) | Add `VIZ_TOOLS` allowlist + `load_modelscope_viz_tools`. |
| `src/ecommerce_agent/agent.py` (modify) | Extend `build_agent` to thread `subagents`/`middleware`/`skills`/`backend`. |
| `src/ecommerce_agent/agents.py` (create) | `build_sales_analyst` factory + dormant `sales_analyst_subagent` seam. |
| `src/ecommerce_agent/api/app.py` (modify) | Lifespan builds + stores + closes the `DockerSandbox` backend. |
| `src/ecommerce_agent/api/chat.py` (modify) | Lazy-build the analyst via `build_sales_analyst` with backend + viz tools. |
| `tests/test_prompts.py` (create) | Loader tests. |
| `tests/test_agents.py` (create) | Analyst factory + build_agent threading tests (fakes). |
| `tests/test_mcp_client.py` (modify) | Viz allowlist tests. |
| `tests/test_app.py` (modify) | Backend lifecycle + updated lazy-build test. |
| `tests/integration/test_hero_live_smoke.py` (create) | `RUN_LIVE_LLM` single-run hero smoke. |

---

## Task 1: pyyaml dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dep**

In `[project].dependencies` add:
```toml
    "pyyaml>=6.0.2",
```

- [ ] **Step 2: Sync**

Run: `uv sync`
Expected: installs `pyyaml`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add pyyaml for prompt loading"
```

---

## Task 2: Prompt YAML + loader

**Files:**
- Create: `src/ecommerce_agent/prompts/__init__.py`
- Create: `src/ecommerce_agent/prompts/prompts.yml`
- Create: `src/ecommerce_agent/prompts/analysis_helpers.md`
- Create: `src/ecommerce_agent/prompts/loader.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_prompts.py`:
```python
import pytest

from ecommerce_agent.prompts.loader import get_prompt


def test_get_sales_analyst_prompt_is_nonempty_and_read_only():
    prompt = get_prompt("sales_analyst")
    assert isinstance(prompt, str) and len(prompt) > 100
    assert "read-only" in prompt.lower()
    assert "ecommerce_analysis" in prompt
    assert "generate_visualization" in prompt


def test_get_prompt_unknown_key_raises():
    with pytest.raises(KeyError, match="not found"):
        get_prompt("does_not_exist")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the package, prompts, and loader**

Create `src/ecommerce_agent/prompts/__init__.py` (empty).

Create `src/ecommerce_agent/prompts/loader.py`:
```python
from __future__ import annotations

import functools
from pathlib import Path

import yaml

_PROMPTS_PATH = Path(__file__).parent / "prompts.yml"


@functools.lru_cache(maxsize=8)
def load_prompts(path: str | None = None) -> dict[str, str]:
    target = Path(path) if path else _PROMPTS_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"prompts file {target} must be a mapping of name -> prompt")
    return data


def get_prompt(name: str, path: str | None = None) -> str:
    prompts = load_prompts(path)
    if name not in prompts:
        raise KeyError(f"prompt {name!r} not found in {path or _PROMPTS_PATH}")
    return prompts[name]
```

Create `src/ecommerce_agent/prompts/analysis_helpers.md`:
```markdown
# ecommerce_analysis helper API (pre-baked in the sandbox)

Use these for commerce time-series analysis instead of writing pandas from scratch.
They read files you already wrote into /workspace; they never fetch data.

- `load_orders_df(path) -> DataFrame`
  Parse a JSON/CSV file of line-item records. Required columns:
  `created_at` (ISO datetime), `status`, `category`, `amount`.
- `monthly_sales_by_category(orders_df) -> DataFrame`
  Realized sales only (status in paid/shipped/completed). Columns: `month`, `category`, `sales`.
- `simple_forecast(monthly_df, periods=1) -> DataFrame`
  Per-category linear-trend forecast. Columns: `category`, `month`, `sales`, `is_forecast`.
- `validate_forecast_result(forecast_df) -> None`
  Raises ValueError if the forecast is empty / non-finite / has no forecast rows.
```

Create `src/ecommerce_agent/prompts/prompts.yml`:
```yaml
sales_analyst: |
  You are the Sales Analyst for an e-commerce operations team. You answer questions
  about products, orders, inventory, suppliers, and sales trends using read-only
  business tools, and you produce a chart when a visual helps.

  Choosing tools:
  - For simple aggregates the backend already computes (totals, counts, sales by
    category, top sellers), call get_statistics and use those authoritative numbers.
    Do NOT recompute backend aggregates in sandbox code.
  - For analysis the backend does not own (trends over time, forecasts, cohort slices,
    correlations), use the sandbox: query the rows, write them into /workspace, then run
    Python using the pre-baked ecommerce_analysis helpers.
  - To chart a result, call generate_visualization with a declarative chart spec.

  Data boundary (critical):
  - You fetch data ONLY through business tools (order_query, product_query, ...).
  - The sandbox has NO network. The ecommerce_analysis helpers read files in /workspace;
    they never fetch. Pattern: fetch via tools -> write_file the rows -> run helpers.

  Commerce time-series / forecast workflow (use the helpers; do not hand-write pandas):
  1. order_query for the date range (paginate if needed).
  2. product_query to map product_id -> category.
  3. write_file a JSON array into /workspace/orders.json with records:
     {created_at, status, category, amount}.
  4. execute Python:
       from ecommerce_analysis import (load_orders_df, monthly_sales_by_category,
                                        simple_forecast, validate_forecast_result)
       df = load_orders_df("/workspace/orders.json")
       monthly = monthly_sales_by_category(df)
       forecast = simple_forecast(monthly, periods=1)
       validate_forecast_result(forecast)
       print(forecast.to_json(orient="records"))
  5. generate_visualization with a line/bar spec of monthly sales plus the forecast point.
  6. Summarize the trend and forecast briefly. Be honest: a few monthly points is
     illustrative, not a rigorous forecast.

  You are read-only. You never create, modify, approve, or execute writes.

coordinator: |
  (Dormant M2 seam — not used in M1.) You route operator requests to the right
  specialist sub-agent and aggregate their results. Use sales-analyst for analysis and
  charts; use order-manager for procurement/order proposals. You never call business
  tools directly.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/prompts tests/test_prompts.py
git commit -m "feat(prompts): YAML sales_analyst prompt + helper reference + loader"
```

---

## Task 3: Extend `build_agent` to thread all DeepAgents slots

**Files:**
- Modify: `src/ecommerce_agent/agent.py`
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agents.py`:
```python
from collections.abc import Sequence

import ecommerce_agent.agent as agent_module
from ecommerce_agent.agent import build_agent


class _Tool:
    def __init__(self, name):
        self.name = name


def test_build_agent_threads_backend_and_slots(monkeypatch):
    captured = {}

    def fake_create_deep_agent(*, model, tools, system_prompt, subagents, middleware, skills, backend):
        captured.update(
            model=model, tools=tools, system_prompt=system_prompt,
            subagents=subagents, middleware=middleware, skills=skills, backend=backend,
        )
        return "AGENT"

    monkeypatch.setattr(agent_module, "create_deep_agent", fake_create_deep_agent)

    sentinel_backend = object()
    result = build_agent(
        "MODEL",
        [_Tool("order_query")],
        system_prompt="PROMPT",
        backend=sentinel_backend,
        subagents=[],
        skills=[],
    )

    assert result == "AGENT"
    assert captured["model"] == "MODEL"
    assert [t.name for t in captured["tools"]] == ["order_query"]
    assert captured["system_prompt"] == "PROMPT"
    assert captured["backend"] is sentinel_backend
    assert captured["subagents"] == []
    assert captured["skills"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agents.py::test_build_agent_threads_backend_and_slots -v`
Expected: FAIL — current `build_agent(model, tools)` ignores the new kwargs / hardcodes `system_prompt`.

- [ ] **Step 3: Rewrite `agent.py`**

Replace the contents of `src/ecommerce_agent/agent.py` with:
```python
from collections.abc import Sequence
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool


def build_agent(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    *,
    system_prompt: str,
    subagents: Sequence[Any] = (),
    middleware: Sequence[Any] = (),
    skills: Sequence[str] = (),
    backend: Any | None = None,
) -> Any:
    """Build a DeepAgents graph, threading every extension slot (proven seams).

    M1 passes a single analyst's tools + the DockerSandbox backend, with empty
    subagents/middleware/skills. M2 reuses the same signature to add the coordinator,
    sub-agents, and middleware without touching this function.
    """
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        subagents=list(subagents),
        middleware=list(middleware),
        skills=list(skills),
        backend=backend,
    )
```

> Note: the inline `SYSTEM_PROMPT` constant is removed; callers pass `system_prompt`. Task 5 supplies it from the YAML loader.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agents.py::test_build_agent_threads_backend_and_slots -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/agent.py tests/test_agents.py
git commit -m "feat(agent): build_agent threads subagents/middleware/skills/backend"
```

---

## Task 4: Visualization seam in `mcp_client`

**Files:**
- Modify: `src/ecommerce_agent/mcp_client.py`
- Test: `tests/test_mcp_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_client.py`:
```python
from ecommerce_agent.mcp_client import VIZ_TOOLS, filter_viz_tools


def test_filter_viz_tools_keeps_only_allowlisted_viz_tools():
    tools = [
        SimpleNamespace(name="generate_visualization"),
        SimpleNamespace(name="some_other_modelscope_tool"),
    ]
    filtered = filter_viz_tools(tools)  # type: ignore[arg-type]
    assert {t.name for t in filtered} == {"generate_visualization"}
    assert VIZ_TOOLS == frozenset({"generate_visualization"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_client.py -k viz -v`
Expected: FAIL with `ImportError` (`VIZ_TOOLS`/`filter_viz_tools` missing).

- [ ] **Step 3: Add the viz allowlist + loader**

In `src/ecommerce_agent/mcp_client.py`, after `WRITE_OR_APPROVAL_SPRING_TOOLS` add:
```python
VIZ_TOOLS: frozenset[str] = frozenset({"generate_visualization"})
```

After `filter_spring_read_tools`, add:
```python
def filter_viz_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in VIZ_TOOLS]


async def load_modelscope_viz_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    tools = await client.get_tools(server_name=MODELSCOPE_SERVER_NAME)
    return filter_viz_tools(tools)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/mcp_client.py tests/test_mcp_client.py
git commit -m "feat(mcp): ModelScope viz-tool allowlist + loader seam"
```

---

## Task 5: `agents.py` — sales-analyst factory + dormant seam

**Files:**
- Create: `src/ecommerce_agent/agents.py`
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agents.py`:
```python
import ecommerce_agent.agents as agents_module
from ecommerce_agent.agents import build_sales_analyst, sales_analyst_subagent


def test_build_sales_analyst_combines_tools_and_threads_backend(monkeypatch):
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

    captured = {}

    def fake_build_agent(model, tools, *, system_prompt, backend, middleware=(), **kwargs):
        captured.update(
            model=model, tools=tools, system_prompt=system_prompt,
            backend=backend, middleware=list(middleware),
        )
        return "ANALYST"

    monkeypatch.setattr(agents_module, "build_agent", fake_build_agent)

    backend = object()
    result = build_sales_analyst(
        "MODEL",
        spring_read_tools=[_Tool("order_query"), _Tool("get_statistics")],
        viz_tools=[_Tool("generate_visualization")],
        backend=backend,
    )

    assert result == "ANALYST"
    assert captured["backend"] is backend
    assert [t.name for t in captured["tools"]] == ["order_query", "get_statistics", "generate_visualization"]
    assert "read-only" in captured["system_prompt"].lower()
    # bounded self-debug retry (R3/R5): per-run model + tool call limits prevent runaway loops
    mw_types = {type(m).__name__ for m in captured["middleware"]}
    assert {"ModelCallLimitMiddleware", "ToolCallLimitMiddleware"} <= mw_types


def test_sales_analyst_subagent_seam_shape():
    sub = sales_analyst_subagent(
        spring_read_tools=[_Tool("order_query")],
        viz_tools=[_Tool("generate_visualization")],
    )
    assert sub["name"] == "sales-analyst"
    assert "description" in sub and "system_prompt" in sub
    assert {t.name for t in sub["tools"]} == {"order_query", "generate_visualization"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agents.py -k "sales_analyst" -v`
Expected: FAIL with `ModuleNotFoundError: ecommerce_agent.agents`.

- [ ] **Step 3: Create `agents.py`**

Create `src/ecommerce_agent/agents.py`:
```python
"""M1 runtime agent factory (single sales-analyst) + dormant M2 coordinator seam."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ecommerce_agent.agent import build_agent
from ecommerce_agent.prompts.loader import get_prompt

_ANALYST_DESCRIPTION = (
    "Read-only sales analyst: queries business data, runs sandboxed analysis when "
    "computation is needed, and produces chart specs."
)

# Bounded self-debug retry (R3/R5): cap model + tool calls per run so a looping
# analyst (e.g. repeatedly retrying broken sandbox code) ends gracefully instead of
# burning the budget. Generous vs the ~6-12 calls of the hero flow; configurable later.
_MAX_MODEL_CALLS_PER_RUN = 25
_MAX_TOOL_CALLS_PER_RUN = 40


def _reliability_middleware() -> list[Any]:
    return [
        ModelCallLimitMiddleware(run_limit=_MAX_MODEL_CALLS_PER_RUN, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=_MAX_TOOL_CALLS_PER_RUN, exit_behavior="end"),
    ]


def build_sales_analyst(
    model: BaseChatModel,
    *,
    spring_read_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    """The M1 runtime agent: a single deep agent on the DockerSandbox backend.

    No coordinator and no `subagents` in M1 — with one specialist a coordinator only
    adds serial model calls. The coordinator seam is `sales_analyst_subagent` below.
    Call-limit middleware bounds self-debug retries so loops end gracefully.
    """
    tools = list(spring_read_tools) + list(viz_tools)
    return build_agent(
        model,
        tools,
        system_prompt=get_prompt("sales_analyst"),
        subagents=[],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )


def sales_analyst_subagent(
    *,
    spring_read_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
) -> dict[str, Any]:
    """Dormant M2 seam: the analyst expressed as a DeepAgents SubAgent dict.

    Unused in M1. M2 enables `subagents=[sales_analyst_subagent(...), order_manager(...)]`
    once a coordinator has a real routing decision.
    """
    return {
        "name": "sales-analyst",
        "description": _ANALYST_DESCRIPTION,
        "system_prompt": get_prompt("sales_analyst"),
        "tools": list(spring_read_tools) + list(viz_tools),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/agents.py tests/test_agents.py
git commit -m "feat(agents): sales-analyst factory + dormant coordinator/sub-agent seam"
```

---

## Task 6: Build + own the sandbox backend in app lifespan

**Files:**
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:
```python
def test_lifespan_builds_and_closes_sandbox_backend(monkeypatch):
    import ecommerce_agent.api.app as app_module

    events = {"built": 0, "closed": 0}

    class FakeBackend:
        def close(self):
            events["closed"] += 1

    def fake_build_backend(settings):
        events["built"] += 1
        return FakeBackend()

    monkeypatch.setattr(app_module, "build_sandbox_backend", fake_build_backend)

    app = create_app(settings=make_settings())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert events["built"] == 1
        assert app.state.sandbox_backend is not None
    # lifespan exit closes it
    assert events["closed"] == 1
```

(Reuses `make_settings` and `TestClient` already imported in `tests/test_app.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_lifespan_builds_and_closes_sandbox_backend -v`
Expected: FAIL — no `build_sandbox_backend`, no `sandbox_backend` state, no close on exit.

- [ ] **Step 3: Add the backend builder + lifecycle**

In `src/ecommerce_agent/api/app.py`:

Add imports near the top:
```python
from ecommerce_agent.sandbox import DockerSandbox
from ecommerce_agent.sandbox.config import limits_from_settings
```

Add a builder function (module level, before `lifespan`):
```python
def build_sandbox_backend(settings: Settings) -> DockerSandbox:
    return DockerSandbox(limits_from_settings(settings))
```

Replace the `lifespan` body to also build/close the backend:
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.mcp_client = getattr(app.state, "mcp_client", None) or build_mcp_client(settings)
    app.state.sandbox_backend = getattr(app.state, "sandbox_backend", None) or build_sandbox_backend(settings)
    app.state.agent = getattr(app.state, "agent", None)
    app.state.agent_lock = getattr(app.state, "agent_lock", asyncio.Lock())
    app.state.tool_count = getattr(app.state, "tool_count", 0)
    try:
        yield
    finally:
        backend = getattr(app.state, "sandbox_backend", None)
        if backend is not None:
            backend.close()
```

In `create_app`, initialise the new state field (next to `app.state.mcp_client = mcp_client`):
```python
    app.state.sandbox_backend = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS (new test + existing app tests).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/app.py tests/test_app.py
git commit -m "feat(app): build, own, and close the DockerSandbox backend in lifespan"
```

---

## Task 7: Lazy-build the analyst with backend + viz tools

**Files:**
- Modify: `src/ecommerce_agent/api/chat.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Update the failing lazy-build test**

Replace `test_chat_stream_lazily_builds_agent_once` in `tests/test_app.py` with:
```python
def test_chat_stream_lazily_builds_analyst_with_backend(monkeypatch):
    calls = {"spring": 0, "viz": 0, "model": 0, "analyst": 0}

    async def fake_load_spring_read_tools(mcp_client):
        calls["spring"] += 1
        return [SimpleNamespace(name="order_query")]

    async def fake_load_modelscope_viz_tools(mcp_client):
        calls["viz"] += 1
        return [SimpleNamespace(name="generate_visualization")]

    def fake_get_primary_model(settings):
        calls["model"] += 1
        return object()

    def fake_build_sales_analyst(model, *, spring_read_tools, viz_tools, backend):
        calls["analyst"] += 1
        assert backend is not None
        assert [t.name for t in spring_read_tools] == ["order_query"]
        assert [t.name for t in viz_tools] == ["generate_visualization"]
        return FakeAgent()

    monkeypatch.setattr(chat_module, "load_spring_read_tools", fake_load_spring_read_tools)
    monkeypatch.setattr(chat_module, "load_modelscope_viz_tools", fake_load_modelscope_viz_tools)
    monkeypatch.setattr(chat_module, "get_primary_model", fake_get_primary_model)
    monkeypatch.setattr(chat_module, "build_sales_analyst", fake_build_sales_analyst)

    app = create_app(
        settings=make_settings(llm_api_key="test-key"),
        mcp_client=HealthyFakeMcpClient(),
    )
    app.state.sandbox_backend = object()

    with TestClient(app) as client:
        app.state.sandbox_backend = object()  # lifespan may reset; ensure present
        for _ in range(2):
            with client.stream("POST", "/api/chat/stream", json={"message": "trend?"}) as response:
                body = "".join(response.iter_text())
            assert response.status_code == 200
            assert "Inventory looks healthy." in body

    assert calls == {"spring": 1, "viz": 1, "model": 1, "analyst": 1}
```

(`HealthyFakeMcpClient.get_tools` already accepts `server_name`; for `modelscope` it is monkeypatched away here, so the fake's spring branch is unused for viz.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_chat_stream_lazily_builds_analyst_with_backend -v`
Expected: FAIL — `chat.py` still calls `build_agent`/`load_spring_read_tools` only, no viz/backend.

- [ ] **Step 3: Update `_ensure_agent` in `chat.py`**

In `src/ecommerce_agent/api/chat.py`, update the imports:
```python
from ecommerce_agent.agents import build_sales_analyst
from ecommerce_agent.mcp_client import load_modelscope_viz_tools, load_spring_read_tools
from ecommerce_agent.models import get_primary_model
```
(Remove the now-unused `from ecommerce_agent.agent import build_agent`.)

Replace the body of `_ensure_agent` (inside the lock, the build branch) with:
```python
        settings = request.app.state.settings
        mcp_client = request.app.state.mcp_client
        spring_tools = await load_spring_read_tools(mcp_client)
        viz_tools = await load_modelscope_viz_tools(mcp_client) if settings.modelscope_mcp_url else []
        model = get_primary_model(settings)
        request.app.state.agent = build_sales_analyst(
            model,
            spring_read_tools=spring_tools,
            viz_tools=viz_tools,
            backend=request.app.state.sandbox_backend,
        )
        request.app.state.tool_count = len(spring_tools) + len(viz_tools)
        return request.app.state.agent
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full default suite + lint**

Run: `uv run pytest -m "not integration and not live" -q && uv run ruff check .`
Expected: green; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/api/chat.py tests/test_app.py
git commit -m "feat(chat): lazy-build sales-analyst with sandbox backend + viz tools"
```

---

## Task 8: Live hero smoke (opt-in)

**Files:**
- Create: `tests/integration/test_hero_live_smoke.py`

- [ ] **Step 1: Write the smoke test**

Create `tests/integration/test_hero_live_smoke.py`:
```python
import os

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.config import Settings
from tests.integration.helpers import (
    skip_unless_docker_available,
    skip_unless_spring_mcp_is_running,
)

HERO = (
    "Which categories are trending up or down over the last 6 months, forecast next "
    "month's sales, and chart the result. Keep the summary short."
)


@pytest.mark.integration
@pytest.mark.live
async def test_hero_flow_single_run():
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live hero smoke")
    skip_unless_docker_available()

    settings = Settings(mcp_request_timeout_seconds=15, mcp_sse_read_timeout_seconds=120)
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")
    await skip_unless_spring_mcp_is_running(settings)

    app = create_app(settings=settings)
    with TestClient(app) as client:
        with client.stream("POST", "/api/chat/stream", json={"message": HERO}) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: done" in body
    assert "event: error" not in body
    # tool boundary is observable: sandbox execute and/or viz appear
    assert ("execute" in body) or ("generate_visualization" in body)
```

- [ ] **Step 2: Run it (manually, with the stack up)**

Run: `RUN_LIVE_LLM=1 uv run pytest tests/integration/test_hero_live_smoke.py -v`
Expected: with Docker + sandbox image + Spring MCP + `LLM_API_KEY` set, PASS; otherwise a clear SKIP.

> This is the rehearsed single-run hero. The N-run structural reliability harness (pass rate +
> failure reasons over the trace) is Plan 3. Run this before demos.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_hero_live_smoke.py
git commit -m "test(live): single-run hero flow smoke (opt-in)"
```

---

## Task 9: Low LLM temperature (reliability)

Determinism control (R6/R3): analytical + codegen steps want consistency, not creativity. Set a
low default temperature on the primary model.

**Files:**
- Modify: `src/ecommerce_agent/config.py`
- Modify: `src/ecommerce_agent/models.py`
- Test: `tests/test_config.py`, `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:
```python
def test_llm_temperature_defaults_low() -> None:
    assert Settings(_env_file=None).llm_temperature == 0.1
```

Create `tests/test_models.py`:
```python
from ecommerce_agent.config import Settings
from ecommerce_agent.models import get_primary_model


def test_primary_model_uses_configured_low_temperature():
    settings = Settings(_env_file=None, llm_api_key="test-key", llm_temperature=0.1)
    model = get_primary_model(settings)
    assert model.temperature == 0.1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py::test_llm_temperature_defaults_low tests/test_models.py -v`
Expected: FAIL — `llm_temperature` field and the model wiring don't exist yet.

- [ ] **Step 3: Add the setting and wire it**

In `src/ecommerce_agent/config.py`, in `Settings` next to the other `llm_*` fields:
```python
    llm_temperature: float = Field(default=0.1, ge=0)
```

In `src/ecommerce_agent/models.py`, pass it in `get_primary_model`'s `ChatOpenAI(...)` call:
```python
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        streaming=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/config.py src/ecommerce_agent/models.py tests/test_config.py tests/test_models.py
git commit -m "feat(models): low default LLM temperature for analytical determinism"
```

---

## Self-Review

**Spec coverage:**
- §3.2 single analyst, no coordinator, dormant seam → Tasks 3, 5. ✅
- §5 viz seam (ModelScope `generate_visualization` allowlist) → Task 4 + wired in Task 7. ✅
- §6 YAML prompts + helper reference + data boundary → Task 2. ✅
- §7 hero data-flow exercised by the live smoke → Task 8. ✅
- backend ownership/lifecycle (build at startup, close at shutdown) → Task 6. ✅
- Reliability controls: bounded self-debug retry via call-limit middleware (Task 5) + low LLM
  temperature (Task 9) — realizes the R3/R5/R6 mitigations rather than leaving them aspirational. ✅
- Deferred correctly to Plan 3: trace module, N-run eval harness, SSE-renders-trace.

**Placeholder scan:** None. The prompt and helper reference are full text; all code steps complete.

**Type consistency:** `build_agent(model, tools, *, system_prompt, subagents, middleware, skills, backend)` is defined in Task 3 and called identically in Task 5. `build_sales_analyst(model, *, spring_read_tools, viz_tools, backend)` is defined in Task 5 and called identically in Task 7's test + `chat.py`. `load_modelscope_viz_tools`/`filter_viz_tools`/`VIZ_TOOLS` consistent (Task 4). `build_sandbox_backend(settings)` defined and monkeypatched consistently (Task 6). `DockerSandbox`/`limits_from_settings` match Plan 1.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-09-m1-analyst-integration.md`. Execute after Plan 1. (Plan 3 follows.)
