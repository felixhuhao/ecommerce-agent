# M4 Slice 1 — Eval-Validated Routing Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the brittle keyword router with a registry-driven intent classifier in the product path, and build a routing eval that baselines keyword-vs-classifier accuracy on a labeled adversarial dataset.

**Architecture:** A new `routing/` package puts routing behind an async `Router` interface backed by a descriptor-only `SpecialistRegistry`. `ClassifierRouter` makes one structured (`function_calling`) DeepSeek call with thinking disabled and a safe fallback to the registry default. The runtime emits a `route_decision` trace event for operator visibility. A new `evals/routing.py` scores any `Router` over `evals/datasets/routing.yaml` and reports a keyword-vs-classifier delta; baseline persistence reuses the existing JSONL writer via shared `evals/metadata.py` helpers.

**Tech Stack:** Python 3, `langchain-openai` (`ChatOpenAI` → DeepSeek V4), pydantic, pytest + pytest-asyncio (`asyncio_mode = "auto"`), PyYAML.

**Spec:** [docs/2026-06-11-m4-routing-eval-design.md](../2026-06-11-m4-routing-eval-design.md)

**Conventions for every commit in this plan:**
- Run `uv run pytest <paths> -q` for the cited tests; the default suite is `uv run pytest -q`.
- Each task's `git commit -m "<subject>"` shows the **subject line only** — it is shorthand. Always
  append the trailer as a second `-m` so the message ends with it, e.g.:
  `git commit -m "<subject>" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`
- Commits are **local only** — do not push. Stage only the files each task names; do not `git add -A` (the repo has unrelated WIP and a `.env` that must stay untouched).

**Prerequisite (tracked separately, not a task here):** the project model default migration
`deepseek-chat → deepseek-v4-flash` through the reliability harness. The local `.env` is already on
`deepseek-v4-flash`; the `config.py` code default still reads `deepseek-chat`. This slice is
model-agnostic and unblocked either way.

**Ordering note:** the spec's §12 build order is followed, except `prompts.yml` (spec step 10) is
moved to Task 3 because `ClassifierRouter` (Task 4) calls `get_prompt("router_classifier")` at
runtime, and the live spike (Task 5) is placed after the router so it can exercise the real
`ClassifierOutput`. The spike still **gates** the runtime rewrite (Task 10).

---

### Task 1: Classifier model builder

**Files:**
- Modify: `src/ecommerce_agent/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
from ecommerce_agent.config import Settings
from ecommerce_agent.models import (
    CLASSIFIER_MAX_TOKENS,
    CLASSIFIER_STRUCTURED_OUTPUT_METHOD,
    CLASSIFIER_TEMPERATURE,
    CLASSIFIER_TIMEOUT_SECONDS,
    classifier_model_params,
    get_classifier_model,
)


def test_get_classifier_model_is_tuned_for_classification():
    settings = Settings(_env_file=None, llm_api_key="k", llm_model="deepseek-v4-flash")
    model = get_classifier_model(settings)
    assert model.model_name == "deepseek-v4-flash"
    assert model.temperature == CLASSIFIER_TEMPERATURE == 0.0
    assert model.max_tokens == CLASSIFIER_MAX_TOKENS
    assert model.streaming is False
    # thinking disabled rides in extra_body (NOT model_kwargs)
    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_classifier_model_params_records_actual_params():
    settings = Settings(_env_file=None, llm_api_key="k", llm_model="deepseek-v4-flash")
    params = classifier_model_params(settings)
    assert params == {
        "name": "deepseek-v4-flash",
        "base_url": settings.llm_base_url,
        "temperature": 0.0,
        "max_tokens": CLASSIFIER_MAX_TOKENS,
        "streaming": False,
        "timeout_seconds": CLASSIFIER_TIMEOUT_SECONDS,
        # record the knobs that made the spike pass — thinking was the real failure mode
        "thinking": "disabled",
        "structured_output_method": CLASSIFIER_STRUCTURED_OUTPUT_METHOD,
    }
    assert CLASSIFIER_STRUCTURED_OUTPUT_METHOD == "function_calling"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_classifier_model'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ecommerce_agent/models.py`:

```python
CLASSIFIER_TEMPERATURE = 0.0
CLASSIFIER_MAX_TOKENS = 256
CLASSIFIER_TIMEOUT_SECONDS = 20
CLASSIFIER_STRUCTURED_OUTPUT_METHOD = "function_calling"


def get_classifier_model(settings: Settings | None = None) -> ChatOpenAI:
    """Model tuned for routing classification: deterministic, capped, non-thinking.

    Non-thinking is REQUIRED: DeepSeek V4 defaults thinking on, and thinking mode
    rejects the forced tool_choice that with_structured_output(function_calling) uses.
    The flag must ride in extra_body, not model_kwargs.
    """
    settings = settings or get_settings()
    if not settings.llm_api_key:
        raise ValueError("LLM_API_KEY is required to build the classifier model")
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=CLASSIFIER_TEMPERATURE,
        max_tokens=CLASSIFIER_MAX_TOKENS,
        timeout=CLASSIFIER_TIMEOUT_SECONDS,
        streaming=False,
        extra_body={"thinking": {"type": "disabled"}},
    )


def classifier_model_params(settings: Settings | None = None) -> dict:
    """JSON-safe record of the classifier's actual params for eval baselines."""
    settings = settings or get_settings()
    return {
        "name": settings.llm_model,
        "base_url": settings.llm_base_url,
        "temperature": CLASSIFIER_TEMPERATURE,
        "max_tokens": CLASSIFIER_MAX_TOKENS,
        "streaming": False,
        "timeout_seconds": CLASSIFIER_TIMEOUT_SECONDS,
        "thinking": "disabled",
        "structured_output_method": CLASSIFIER_STRUCTURED_OUTPUT_METHOD,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/models.py tests/test_models.py
git commit -m "feat(routing): add classifier model builder (non-thinking, capped)"
```

---

### Task 2: Specialist registry

**Files:**
- Create: `src/ecommerce_agent/routing/__init__.py`
- Create: `src/ecommerce_agent/routing/registry.py`
- Test: `tests/test_routing_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_routing_registry.py`:

```python
import pytest

from ecommerce_agent.routing.registry import (
    Specialist,
    SpecialistRegistry,
    build_specialist_registry,
)


def test_default_specialist_is_the_flagged_one():
    reg = build_specialist_registry()
    assert reg.default.name == "sales-analyst"
    assert set(reg.names()) == {"sales-analyst", "order-manager"}
    assert reg.is_registered("order-manager") is True
    assert reg.is_registered("unsure") is False


def test_describe_lists_names_and_descriptions():
    reg = build_specialist_registry()
    text = reg.describe()
    assert "sales-analyst:" in text
    assert "order-manager:" in text


def test_registry_requires_exactly_one_default():
    with pytest.raises(ValueError):
        SpecialistRegistry([Specialist("a", "x", default=False)])
    with pytest.raises(ValueError):
        SpecialistRegistry(
            [Specialist("a", "x", default=True), Specialist("b", "y", default=True)]
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routing_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.routing'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ecommerce_agent/routing/__init__.py` (empty file).

Create `src/ecommerce_agent/routing/registry.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Specialist:
    name: str
    description: str
    default: bool = False


class SpecialistRegistry:
    """Descriptor-only registry (no agent instances) shared by runtime and eval."""

    def __init__(self, specialists: list[Specialist]) -> None:
        defaults = [s for s in specialists if s.default]
        if len(defaults) != 1:
            raise ValueError("registry requires exactly one default specialist")
        self.specialists = specialists

    def names(self) -> list[str]:
        return [s.name for s in self.specialists]

    @property
    def default(self) -> Specialist:
        return next(s for s in self.specialists if s.default)

    def is_registered(self, name: str) -> bool:
        return any(s.name == name for s in self.specialists)

    def describe(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self.specialists)


def build_specialist_registry() -> SpecialistRegistry:
    return SpecialistRegistry(
        [
            Specialist(
                "sales-analyst",
                "read-only sales analytics: querying business data, trends, "
                "forecasts, and charts.",
                default=True,
            ),
            Specialist(
                "order-manager",
                "approval-only business writes: purchase orders, replenishment, "
                "receiving, and order-status changes.",
            ),
        ]
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routing_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/routing/__init__.py src/ecommerce_agent/routing/registry.py tests/test_routing_registry.py
git commit -m "feat(routing): add descriptor-only specialist registry"
```

---

### Task 3: Router classifier prompt

**Files:**
- Modify: `src/ecommerce_agent/prompts/prompts.yml`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_prompts.py`:

```python
from ecommerce_agent.prompts.loader import get_prompt


def test_router_classifier_prompt_has_specialists_slot():
    prompt = get_prompt("router_classifier")
    assert "{specialists}" in prompt
    assert "unsure" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompts.py::test_router_classifier_prompt_has_specialists_slot -q`
Expected: FAIL — `KeyError: "prompt 'router_classifier' not found"`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ecommerce_agent/prompts/prompts.yml`:

```yaml
router_classifier: |
  You route an e-commerce operator's message to exactly one specialist.

  Specialists:
  {specialists}

  Choose the single specialist whose responsibilities best fit the message.
  Use "order-manager" when the message asks to take or propose a business write
  (create/receive a purchase order, restock, replenish, change order status),
  even if it is phrased indirectly. Use "sales-analyst" for read-only questions,
  analysis, trends, forecasts, and charts — including questions that merely
  mention purchasing or restocking but only ask to analyze or report.

  Respond with the specialist name and a brief reason. If the message is
  genuinely ambiguous, respond with "unsure".
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/prompts/prompts.yml tests/test_prompts.py
git commit -m "feat(routing): add router_classifier prompt"
```

---

### Task 4: Router interface + ClassifierRouter

**Files:**
- Create: `src/ecommerce_agent/routing/router.py`
- Test: `tests/test_routing_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_routing_router.py`:

```python
import asyncio

import pytest

from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import (
    ClassifierOutput,
    ClassifierRouter,
    RouteDecision,
)


class FakeStructured:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls: list = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self._exc is not None:
            raise self._exc
        return self._result


class FakeModel:
    def __init__(self, structured):
        self._structured = structured
        self.method = None

    def with_structured_output(self, schema, *, method=None):
        self.method = method
        return self._structured


def _router(result=None, exc=None):
    model = FakeModel(FakeStructured(result=result, exc=exc))
    return ClassifierRouter(model, build_specialist_registry()), model


@pytest.mark.asyncio
async def test_valid_specialist_is_returned_from_classifier():
    router, model = _router(ClassifierOutput(specialist="order-manager", reason="po"))
    decision = await router.route("create a purchase order for 200 units")
    assert decision == RouteDecision(
        specialist="order-manager", source="classifier", reason="po"
    )
    assert model.method == "function_calling"
    # system instruction + raw human message (message is not concatenated into instructions)
    sent = model._structured.calls[0]
    assert sent[1].content == "create a purchase order for 200 units"
    assert "{specialists}" not in sent[0].content


@pytest.mark.asyncio
async def test_unsure_falls_back_to_default():
    router, _ = _router(ClassifierOutput(specialist="unsure", reason="ambiguous"))
    decision = await router.route("hello")
    assert decision.specialist == "sales-analyst"
    assert decision.source == "fallback"


@pytest.mark.asyncio
async def test_unregistered_name_falls_back_to_default():
    router, _ = _router(ClassifierOutput(specialist="wizard", reason="?"))
    decision = await router.route("hello")
    assert decision.specialist == "sales-analyst"
    assert decision.source == "fallback"


@pytest.mark.asyncio
async def test_exception_falls_back_and_never_raises():
    router, _ = _router(exc=RuntimeError("boom"))
    decision = await router.route("hello")
    assert decision.specialist == "sales-analyst"
    assert decision.source == "fallback"


@pytest.mark.asyncio
async def test_timeout_falls_back():
    router, _ = _router(exc=asyncio.TimeoutError())
    decision = await router.route("hello")
    assert decision.specialist == "sales-analyst"
    assert decision.source == "fallback"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routing_router.py -q`
Expected: FAIL — `ImportError: cannot import name 'ClassifierRouter'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ecommerce_agent/routing/router.py`:

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ecommerce_agent.models import (
    CLASSIFIER_STRUCTURED_OUTPUT_METHOD,
    CLASSIFIER_TIMEOUT_SECONDS,
)
from ecommerce_agent.prompts.loader import get_prompt
from ecommerce_agent.routing.registry import SpecialistRegistry

logger = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    specialist: str  # always a registered specialist name
    source: str  # "classifier" | "fallback" | "keyword"
    reason: str


class Router(Protocol):
    async def route(self, message: str) -> RouteDecision: ...


class ClassifierOutput(BaseModel):
    specialist: str = Field(description="a registered specialist name, or 'unsure'")
    reason: str = Field(description="brief reason")


class ClassifierRouter:
    """Model-based router: one structured, non-blocking call with a safe fallback."""

    def __init__(self, model: Any, registry: SpecialistRegistry) -> None:
        self._model = model
        self._registry = registry

    async def route(self, message: str) -> RouteDecision:
        instruction = get_prompt("router_classifier").replace(
            "{specialists}", self._registry.describe()
        )
        structured = self._model.with_structured_output(
            ClassifierOutput, method=CLASSIFIER_STRUCTURED_OUTPUT_METHOD
        )
        try:
            out = await asyncio.wait_for(
                structured.ainvoke(
                    [SystemMessage(content=instruction), HumanMessage(content=message)]
                ),
                CLASSIFIER_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - any failure must fall back, never raise
            logger.warning("classifier routing failed; using default", exc_info=True)
            return self._fallback("classifier call failed")
        if self._registry.is_registered(out.specialist):
            return RouteDecision(
                specialist=out.specialist, source="classifier", reason=out.reason
            )
        return self._fallback(f"classifier returned {out.specialist!r}")

    def _fallback(self, reason: str) -> RouteDecision:
        return RouteDecision(
            specialist=self._registry.default.name, source="fallback", reason=reason
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routing_router.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/routing/router.py tests/test_routing_router.py
git commit -m "feat(routing): add async Router interface and ClassifierRouter"
```

---

### Task 5: Live structured-output spike (RUN_LIVE_LLM gate)

This codifies the 2026-06-11 manual spike as a regression probe. **It gates the runtime rewrite in
Task 10** — keyword routing must not be removed until this passes live.

**Files:**
- Create: `tests/integration/test_routing_classifier_live.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_routing_classifier_live.py`:

```python
import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.models import get_classifier_model
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import ClassifierRouter


@pytest.mark.integration
@pytest.mark.live
async def test_classifier_routes_clear_prompts_live():
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live classifier spike")
    settings = Settings()
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    # The router is the real proof: it internally runs the structured-output call, so
    # source == "classifier" (not "fallback") confirms function_calling + non-thinking
    # actually worked end to end. Two unambiguous prompts, one per specialist.
    router = ClassifierRouter(get_classifier_model(settings), build_specialist_registry())

    po = await router.route("create a purchase order for 200 units of SKU-9")
    assert po.source == "classifier"
    assert po.specialist == "order-manager"

    sales = await router.route("what were total sales by category last month?")
    assert sales.source == "classifier"
    assert sales.specialist == "sales-analyst"
```

- [ ] **Step 2: Run it (gated — confirm it skips without the flag, passes with it)**

Run (skips): `uv run pytest tests/integration/test_routing_classifier_live.py -q`
Expected: SKIPPED.

Run (live, if credentials available): `RUN_LIVE_LLM=1 uv run pytest tests/integration/test_routing_classifier_live.py -q`
Expected: PASS. **If it fails on the request shape, stop and switch to `ChatDeepSeek` or a lenient
parse before Task 10 (see spec R-A).**

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_routing_classifier_live.py
git commit -m "test(routing): live structured-output spike as RUN_LIVE_LLM regression probe"
```

---

### Task 6: KeywordRouter (eval baseline)

**Files:**
- Create: `src/ecommerce_agent/routing/keyword.py`
- Test: `tests/test_routing_keyword.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_routing_keyword.py`:

```python
import pytest

from ecommerce_agent.routing.keyword import KeywordRouter
from ecommerce_agent.routing.registry import build_specialist_registry


def _router():
    return KeywordRouter(build_specialist_registry())


@pytest.mark.asyncio
async def test_keyword_hit_routes_to_order_manager():
    decision = await _router().route("Create a purchase order to restock product 1")
    assert decision.specialist == "order-manager"
    assert decision.source == "keyword"


@pytest.mark.asyncio
async def test_no_keyword_routes_to_default():
    decision = await _router().route("Forecast next month sales by category")
    assert decision.specialist == "sales-analyst"
    assert decision.source == "keyword"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routing_keyword.py -q`
Expected: FAIL — `ModuleNotFoundError: ... routing.keyword`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ecommerce_agent/routing/keyword.py`:

```python
from __future__ import annotations

from ecommerce_agent.routing.registry import SpecialistRegistry
from ecommerce_agent.routing.router import RouteDecision

# Ported from the former sessions/factory.py keyword shortcut. Eval baseline only.
ORDER_MANAGER_KEYWORDS = (
    "approval",
    "approve",
    "create purchase order",
    "purchase order",
    "receive purchase",
    "receive po",
    "replenish",
    "restock",
    "update order",
    "order status",
)


class KeywordRouter:
    """Substring keyword router — the eval's 'before' baseline. Not used at runtime."""

    def __init__(self, registry: SpecialistRegistry) -> None:
        self._registry = registry

    async def route(self, message: str) -> RouteDecision:
        lowered = message.lower()
        if any(keyword in lowered for keyword in ORDER_MANAGER_KEYWORDS):
            return RouteDecision(
                specialist="order-manager", source="keyword", reason="keyword match"
            )
        return RouteDecision(
            specialist=self._registry.default.name, source="keyword", reason="no keyword"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routing_keyword.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/routing/keyword.py tests/test_routing_keyword.py
git commit -m "feat(routing): add KeywordRouter as eval baseline comparator"
```

---

### Task 7: Shared eval metadata helpers

Refactor the metadata helpers out of `live_reliability.py` into `evals/metadata.py` (DRY), generalize
`prompt_hash` to take a prompt name, and add a `model` override so the routing baseline can record the
classifier's real params.

**Files:**
- Create: `src/ecommerce_agent/evals/metadata.py`
- Modify: `src/ecommerce_agent/evals/live_reliability.py`
- Test: `tests/test_evals_metadata.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_evals_metadata.py`:

```python
from ecommerce_agent.config import Settings
from ecommerce_agent.evals.metadata import prompt_hash, run_metadata


def test_prompt_hash_is_named_and_stable():
    h1 = prompt_hash("sales_analyst")
    h2 = prompt_hash("sales_analyst")
    h3 = prompt_hash("router_classifier")
    assert h1 == h2 and len(h1) == 16
    assert h1 != h3


def test_run_metadata_uses_model_override_when_given():
    settings = Settings(_env_file=None, llm_api_key="k", llm_model="primary")
    model = {"name": "deepseek-v4-flash", "temperature": 0.0}
    md = run_metadata(settings, prompt_name="router_classifier", model=model)
    assert md["model"] == model
    assert set(md) == {"git_commit", "prompt_hash", "dependency_versions", "model"}


def test_run_metadata_defaults_to_primary_model():
    settings = Settings(_env_file=None, llm_api_key="k", llm_model="primary")
    md = run_metadata(settings, prompt_name="sales_analyst")
    assert md["model"]["name"] == "primary"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_evals_metadata.py -q`
Expected: FAIL — `ModuleNotFoundError: ... evals.metadata`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ecommerce_agent/evals/metadata.py` (move the bodies from `live_reliability.py`):

```python
from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess

from ecommerce_agent.config import Settings
from ecommerce_agent.prompts.loader import get_prompt


def git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in ("deepagents", "langgraph", "langchain-mcp-adapters", "langchain-openai"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def prompt_hash(name: str) -> str:
    return hashlib.sha256(get_prompt(name).encode("utf-8")).hexdigest()[:16]


def run_metadata(settings: Settings, *, prompt_name: str, model: dict | None = None) -> dict:
    return {
        "git_commit": git_commit(),
        "prompt_hash": prompt_hash(prompt_name),
        "dependency_versions": dependency_versions(),
        "model": model
        or {
            "name": settings.llm_model,
            "base_url": settings.llm_base_url,
            "temperature": settings.llm_temperature,
        },
    }
```

In `src/ecommerce_agent/evals/live_reliability.py`: delete the local `_prompt_hash`,
`_dependency_versions`, `_git_commit`, and `_run_metadata` functions and their now-unused imports
(`hashlib`, `importlib.metadata`, `subprocess`), import the shared helpers, and replace the
`**_run_metadata(settings)` call in `run_reliability` with the shared call:

```python
from ecommerce_agent.evals.metadata import run_metadata

# ... inside run_reliability's returned dict, replace **_run_metadata(settings) with:
        **run_metadata(settings, prompt_name="sales_analyst"),
```

- [ ] **Step 4: Run tests to verify they pass (including live_reliability's unit tests)**

Run: `uv run pytest tests/test_evals_metadata.py tests/integration/test_live_reliability.py -q`
Expected: PASS (the live batch test skips without `RUN_LIVE_LLM`; the non-live unit tests in that
file still pass and prove the refactor kept the baseline shape).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/evals/metadata.py src/ecommerce_agent/evals/live_reliability.py tests/test_evals_metadata.py
git commit -m "refactor(evals): share run metadata helpers; named prompt_hash + model override"
```

---

### Task 8: Routing dataset + loader

**Files:**
- Create: `src/ecommerce_agent/evals/datasets/routing.yaml`
- Create: `src/ecommerce_agent/evals/datasets/__init__.py`
- Modify: `src/ecommerce_agent/evals/routing.py` (created here; extended in Task 9)
- Test: `tests/test_routing_dataset.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_routing_dataset.py`:

```python
import pytest

from ecommerce_agent.evals.routing import RoutingCase, load_routing_cases


def test_dataset_loads_and_is_well_formed():
    cases = load_routing_cases()
    assert len(cases) >= 10
    assert all(isinstance(c, RoutingCase) for c in cases)
    assert all(c.expected in {"sales-analyst", "order-manager"} for c in cases)
    # at least a few deliberately adversarial cases
    assert sum("adversarial" in c.tags for c in cases) >= 4


def test_loader_rejects_unknown_specialist(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: x\n  prompt: hi\n  expected: wizard\n  tags: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_routing_cases(str(bad))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routing_dataset.py -q`
Expected: FAIL — `ModuleNotFoundError: ... evals.routing`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ecommerce_agent/evals/datasets/__init__.py` (empty file).

Create `src/ecommerce_agent/evals/datasets/routing.yaml`:

```yaml
- id: trend-restock-analysis
  prompt: analyze why we keep needing to restock electronics
  expected: sales-analyst
  tags: [adversarial, keyword-false-positive]
- id: po-volume-report
  prompt: show me a report on purchase order volume last quarter
  expected: sales-analyst
  tags: [adversarial, keyword-false-positive]
- id: buy-more-units
  prompt: we should buy 500 more units of SKU-12 from the cheapest supplier
  expected: order-manager
  tags: [adversarial, keyword-false-negative]
- id: low-stock-reorder
  prompt: stock is low on blue widgets, can you set up a reorder?
  expected: order-manager
  tags: [adversarial, keyword-false-negative]
- id: sales-by-category
  prompt: what were total sales by category last month?
  expected: sales-analyst
  tags: [straightforward]
- id: forecast-next-month
  prompt: forecast next month's sales and chart it
  expected: sales-analyst
  tags: [straightforward]
- id: top-sellers
  prompt: which products are my top sellers this year?
  expected: sales-analyst
  tags: [straightforward]
- id: inventory-snapshot
  prompt: how much inventory do we have on hand right now?
  expected: sales-analyst
  tags: [straightforward]
- id: create-po
  prompt: create a purchase order for 200 units of SKU-9
  expected: order-manager
  tags: [straightforward]
- id: receive-po
  prompt: receive purchase order 4471
  expected: order-manager
  tags: [straightforward]
- id: update-order-status
  prompt: mark order 8812 as shipped
  expected: order-manager
  tags: [straightforward]
- id: replenish-supplier
  prompt: replenish our stock of SKU-3 from supplier 12
  expected: order-manager
  tags: [straightforward]
```

Create `src/ecommerce_agent/evals/routing.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ecommerce_agent.routing.registry import build_specialist_registry

_DATASET_PATH = Path(__file__).parent / "datasets" / "routing.yaml"


@dataclass
class RoutingCase:
    id: str
    prompt: str
    expected: str
    tags: list[str] = field(default_factory=list)


def load_routing_cases(path: str | None = None) -> list[RoutingCase]:
    target = Path(path) if path else _DATASET_PATH
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or []
    registry = build_specialist_registry()
    cases: list[RoutingCase] = []
    for entry in raw:
        case = RoutingCase(
            id=entry["id"],
            prompt=entry["prompt"],
            expected=entry["expected"],
            tags=list(entry.get("tags", [])),
        )
        if not registry.is_registered(case.expected):
            raise ValueError(f"case {case.id!r} has unknown specialist {case.expected!r}")
        cases.append(case)
    return cases
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routing_dataset.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/evals/datasets tests/test_routing_dataset.py src/ecommerce_agent/evals/routing.py
git commit -m "feat(evals): add labeled routing dataset + validating loader"
```

---

### Task 9: Scorer, runner, report, compare

**Files:**
- Modify: `src/ecommerce_agent/evals/routing.py`
- Test: `tests/test_routing_eval.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_routing_eval.py`:

```python
import pytest

from ecommerce_agent.evals.routing import (
    EvalReport,
    RoutingCase,
    compare,
    run_routing_eval,
    score_case,
)
from ecommerce_agent.routing.router import RouteDecision


def _case(cid, expected, tags=()):
    return RoutingCase(id=cid, prompt=cid, expected=expected, tags=list(tags))


def test_score_case_pass_and_fail():
    case = _case("a", "order-manager", ["adversarial"])
    ok = score_case(RouteDecision("order-manager", "classifier", "r"), case)
    bad = score_case(RouteDecision("sales-analyst", "classifier", "r"), case)
    assert ok.passed is True and ok.predicted == "order-manager"
    assert bad.passed is False


class StubRouter:
    def __init__(self, mapping, errors=()):
        self._mapping = mapping
        self._errors = set(errors)

    async def route(self, message: str) -> RouteDecision:
        if message in self._errors:
            raise RuntimeError("boom")
        return RouteDecision(self._mapping[message], "classifier", "r")


@pytest.mark.asyncio
async def test_run_routing_eval_aggregates_accuracy_and_confusion():
    cases = [
        _case("p1", "sales-analyst", ["straightforward"]),
        _case("p2", "order-manager", ["adversarial"]),
        _case("p3", "order-manager", ["adversarial"]),
    ]
    router = StubRouter({"p1": "sales-analyst", "p2": "order-manager", "p3": "sales-analyst"})
    report = await run_routing_eval(router, cases, router_name="stub")
    assert isinstance(report, EvalReport)
    assert report.n == 3
    assert report.passed == 2
    assert report.errors == 0
    assert report.accuracy == pytest.approx(2 / 3)
    assert report.per_tag_accuracy["adversarial"] == pytest.approx(0.5)
    # nested-dict confusion (JSON-safe), scored cases only
    assert report.confusion["order-manager"]["sales-analyst"] == 1
    assert report.confusion["order-manager"]["order-manager"] == 1


@pytest.mark.asyncio
async def test_errored_case_excluded_from_confusion_but_counts_as_failure():
    cases = [_case("p1", "sales-analyst"), _case("boom", "order-manager")]
    router = StubRouter({"p1": "sales-analyst"}, errors=["boom"])
    report = await run_routing_eval(router, cases, router_name="stub")
    assert report.errors == 1
    assert report.passed == 1
    assert report.accuracy == pytest.approx(0.5)
    assert "<error>" not in report.confusion.get("order-manager", {})


@pytest.mark.asyncio
async def test_compare_reports_overall_and_adversarial_delta():
    cases = [_case("p1", "order-manager", ["adversarial"])]
    keyword = await run_routing_eval(
        StubRouter({"p1": "sales-analyst"}), cases, router_name="keyword"
    )
    classifier = await run_routing_eval(
        StubRouter({"p1": "order-manager"}), cases, router_name="classifier"
    )
    delta = compare(keyword, classifier)
    assert delta["overall_delta"] == pytest.approx(1.0)
    assert delta["adversarial_delta"] == pytest.approx(1.0)
    assert delta["flips"] == ["p1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routing_eval.py -q`
Expected: FAIL — `ImportError: cannot import name 'score_case'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ecommerce_agent/evals/routing.py`:

```python
from ecommerce_agent.routing.router import RouteDecision, Router

ERROR_PREDICTION = "<error>"


@dataclass
class CaseResult:
    case_id: str
    expected: str
    predicted: str
    passed: bool
    source: str
    tags: list[str]


@dataclass
class EvalReport:
    router_name: str
    n: int
    passed: int
    errors: int
    accuracy: float
    per_tag_accuracy: dict[str, float]
    confusion: dict[str, dict[str, int]]
    cases: list[CaseResult]


def score_case(decision: RouteDecision, case: RoutingCase) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        expected=case.expected,
        predicted=decision.specialist,
        passed=decision.specialist == case.expected,
        source=decision.source,
        tags=case.tags,
    )


async def run_routing_eval(
    router: Router, cases: list[RoutingCase], *, router_name: str
) -> EvalReport:
    results: list[CaseResult] = []
    for case in cases:
        try:
            decision = await router.route(case.prompt)
            results.append(score_case(decision, case))
        except Exception:  # noqa: BLE001 - one bad case must not abort the batch
            results.append(
                CaseResult(case.id, case.expected, ERROR_PREDICTION, False, "error", case.tags)
            )

    passed = sum(1 for r in results if r.passed)
    errors = sum(1 for r in results if r.predicted == ERROR_PREDICTION)

    tags = {tag for r in results for tag in r.tags}
    per_tag_accuracy: dict[str, float] = {}
    for tag in tags:
        tagged = [r for r in results if tag in r.tags]
        per_tag_accuracy[tag] = sum(r.passed for r in tagged) / len(tagged)

    confusion: dict[str, dict[str, int]] = {}
    for r in results:
        if r.predicted == ERROR_PREDICTION:
            continue
        confusion.setdefault(r.expected, {})
        confusion[r.expected][r.predicted] = confusion[r.expected].get(r.predicted, 0) + 1

    return EvalReport(
        router_name=router_name,
        n=len(cases),
        passed=passed,
        errors=errors,
        accuracy=passed / len(cases) if cases else 0.0,
        per_tag_accuracy=per_tag_accuracy,
        confusion=confusion,
        cases=results,
    )


def compare(baseline: EvalReport, candidate: EvalReport) -> dict:
    base = {r.case_id: r.passed for r in baseline.cases}
    flips = [r.case_id for r in candidate.cases if base.get(r.case_id) != r.passed]
    return {
        "overall_delta": candidate.accuracy - baseline.accuracy,
        "adversarial_delta": candidate.per_tag_accuracy.get("adversarial", 0.0)
        - baseline.per_tag_accuracy.get("adversarial", 0.0),
        "flips": flips,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routing_eval.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/evals/routing.py tests/test_routing_eval.py
git commit -m "feat(evals): routing scorer, async runner, report, and compare"
```

- [ ] **Step 6: Lock the deterministic offline keyword baseline (default-suite, no model)**

This is the spec's promised default-CI regression guard (§5.6): `KeywordRouter` is deterministic (no
model), so it runs in the normal suite over the real `routing.yaml`. It establishes the "before"
number the classifier must beat. The stable invariant is that keyword scores **0.0 on the adversarial
subset** (every adversarial case is a keyword failure by construction) — asserting that (plus
`errors == 0` and overall below the classifier's 0.80 floor) is robust to later dataset additions.
The components all exist by now, so this is a characterization test that passes immediately.

Add to `tests/test_routing_eval.py`:

```python
from ecommerce_agent.evals.routing import load_routing_cases
from ecommerce_agent.routing.keyword import KeywordRouter
from ecommerce_agent.routing.registry import build_specialist_registry


@pytest.mark.asyncio
async def test_keyword_baseline_over_dataset_is_deterministic():
    cases = load_routing_cases()
    report = await run_routing_eval(
        KeywordRouter(build_specialist_registry()), cases, router_name="keyword"
    )
    assert report.errors == 0
    # keyword fails every adversarial case by construction — this is the baseline to beat
    assert report.per_tag_accuracy["adversarial"] == 0.0
    # overall sits below the classifier's advisory floor, so the comparison is meaningful
    assert report.accuracy < 0.80
```

- [ ] **Step 7: Run it**

Run: `uv run pytest tests/test_routing_eval.py -q`
Expected: PASS (5 tests) — deterministic, no network.

- [ ] **Step 8: Commit**

```bash
git add tests/test_routing_eval.py
git commit -m "test(evals): lock deterministic offline keyword baseline over dataset"
```

---

### Task 10: Wire ClassifierRouter into the runtime; remove keyword routing

**Gated by Task 5** — only proceed once the live spike passes.

**Files:**
- Modify: `src/ecommerce_agent/sessions/factory.py`
- Test: `tests/test_session_factory.py`

- [ ] **Step 1: Replace the keyword factory tests with router-based tests**

Replace the two `RoutedSessionAgent` tests in `tests/test_session_factory.py`
(`test_routed_session_agent_sends_analysis_directly_to_analyst` and
`..._sends_restock_actions_directly_to_order_manager`) with:

```python
from ecommerce_agent.routing.router import RouteDecision


class StubRouter:
    def __init__(self, specialist: str) -> None:
        self._specialist = specialist
        self.seen: list[str] = []

    async def route(self, message: str) -> RouteDecision:
        self.seen.append(message)
        return RouteDecision(self._specialist, "classifier", "r")


def _agents():
    return {"sales-analyst": FakeAgent("analyst"), "order-manager": FakeAgent("order-manager")}


@pytest.mark.asyncio
async def test_routed_session_agent_delegates_to_router_choice():
    agents = _agents()
    routed = RoutedSessionAgent(
        router=StubRouter("order-manager"), agents=agents, default_specialist="sales-analyst"
    )
    events = [
        e
        async for e in routed.astream_events(
            {"messages": [{"role": "user", "content": "create a purchase order"}]},
            config={},
            version="v2",
        )
    ]
    # first event is the route-decision; then the chosen agent's events
    assert events[0] == {
        "event": "on_route_decision",
        "data": {"specialist": "order-manager", "source": "classifier", "reason": "r"},
    }
    assert {"event": "selected", "name": "order-manager"} in events
    assert agents["order-manager"].calls == ["create a purchase order"]
    assert agents["sales-analyst"].calls == []


@pytest.mark.asyncio
async def test_routed_session_agent_falls_back_to_default_on_unknown_key():
    agents = _agents()
    routed = RoutedSessionAgent(
        router=StubRouter("ghost"), agents=agents, default_specialist="sales-analyst"
    )
    events = [
        e
        async for e in routed.astream_events(
            {"messages": [{"role": "user", "content": "hi"}]}, config={}, version="v2"
        )
    ]
    assert {"event": "selected", "name": "analyst"} in events
    assert agents["sales-analyst"].calls == ["hi"]
```

Also update `test_build_session_runtime_wires_session_scoped_pieces`: add
`monkeypatch.setattr(factory_module, "get_classifier_model", lambda settings: object())` next to the
existing `get_primary_model` patch, and keep the `assert isinstance(runtime.agent, RoutedSessionAgent)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session_factory.py -q`
Expected: FAIL — `RoutedSessionAgent` does not accept `router=`/`agents=`, and
`get_classifier_model` is not in `factory_module`.

- [ ] **Step 3: Rewrite the routing seam in `factory.py`**

In `src/ecommerce_agent/sessions/factory.py`: remove `_ORDER_MANAGER_KEYWORDS`,
`_needs_order_manager`, and rewrite `RoutedSessionAgent`. Add the imports:

```python
from ecommerce_agent.models import get_classifier_model, get_primary_model
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import ClassifierRouter, Router
```

Replace the class and helpers:

```python
class RoutedSessionAgent:
    """Route each turn via a Router, then delegate to the chosen specialist agent."""

    def __init__(self, *, router: Router, agents: dict[str, Any], default_specialist: str) -> None:
        self.router = router
        self.agents = agents
        self.default_specialist = default_specialist

    async def astream_events(
        self,
        inputs: dict,
        *,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        text = _latest_user_text(inputs)
        decision = await self.router.route(text)
        # Surface the route decision in the trace before delegating (see trace/capture.py).
        yield {
            "event": "on_route_decision",
            "data": {
                "specialist": decision.specialist,
                "source": decision.source,
                "reason": decision.reason,
            },
        }
        selected = self.agents.get(decision.specialist) or self.agents[self.default_specialist]
        async for event in selected.astream_events(inputs, config=config, version=version):
            yield event
```

In `build_session_runtime`, replace the `RoutedSessionAgent(...)` construction (keep the existing
`analyst_agent` / `order_manager_agent` builds) with:

```python
    registry = build_specialist_registry()
    routed_agent = RoutedSessionAgent(
        router=ClassifierRouter(get_classifier_model(settings), registry),
        agents={"sales-analyst": analyst_agent, "order-manager": order_manager_agent},
        default_specialist=registry.default.name,
    )
```

Leave `_latest_user_text` as-is. Keep the `_needs_order_manager` removal complete (grep to confirm).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_factory.py -q`
Expected: PASS.

- [ ] **Step 5: Confirm no keyword logic remains, then commit**

Run: `grep -n "_needs_order_manager\|_ORDER_MANAGER_KEYWORDS" src/ecommerce_agent/sessions/factory.py`
Expected: no matches.

```bash
git add src/ecommerce_agent/sessions/factory.py tests/test_session_factory.py
git commit -m "feat(routing): route turns via ClassifierRouter; remove keyword routing"
```

---

### Task 11: Route-decision trace observability

**Files:**
- Modify: `src/ecommerce_agent/trace/capture.py`
- Modify: `src/ecommerce_agent/trace/projection.py`
- Test: `tests/test_trace_capture.py`, `tests/test_trace_projection.py`, `tests/test_session_turn.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trace_capture.py`:

```python
import pytest

from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord


async def _aiter(events):
    for e in events:
        yield e


@pytest.mark.asyncio
async def test_capture_maps_route_decision_event():
    record = TraceRecord()
    raw = [
        {
            "event": "on_route_decision",
            "data": {"specialist": "order-manager", "source": "classifier", "reason": "po"},
        }
    ]
    async for _ in capture(_aiter(raw), record):
        pass
    routes = [e for e in record.events if e.event_type == "route_decision"]
    assert len(routes) == 1
    assert routes[0].name == "order-manager"
    assert routes[0].phase == "end"
    assert "classifier" in routes[0].result_summary and "po" in routes[0].result_summary
```

Add to `tests/test_trace_projection.py`:

```python
def test_project_timeline_includes_route_decision():
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(
        TraceEvent(
            event_type="route_decision",
            name="order-manager",
            phase="end",
            status="ok",
            result_summary="classifier: po",
            ts=0.5,
        )
    )
    timeline = project_timeline(record)
    assert timeline["span_count"] == 1
    span = timeline["spans"][0]
    assert span["kind"] == "route_decision"
    assert span["name"] == "order-manager"
    assert span["result_summary"] == "classifier: po"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trace_capture.py::test_capture_maps_route_decision_event tests/test_trace_projection.py::test_project_timeline_includes_route_decision -q`
Expected: FAIL — capture ignores the unknown event (no `route_decision` event recorded); projection
skips it (`span_count == 0`).

- [ ] **Step 3: Implement capture mapping and projection inclusion**

In `src/ecommerce_agent/trace/capture.py`, add a branch in `_to_trace_event` (before the final
`return None`):

```python
    if event_type == "on_route_decision":
        info = data if isinstance(data, dict) else {}
        specialist = info.get("specialist")
        source = info.get("source")
        reason = info.get("reason", "")
        return TraceEvent(
            event_type="route_decision",
            name=specialist,
            phase="end",
            status="ok",
            trace_id=record.trace_id,
            run_id=run_id,
            result_summary=f"{source}: {reason}",
        )
```

In `src/ecommerce_agent/trace/projection.py`, add `route_decision` to the span set:

```python
_SPAN_EVENT_TYPES = {"model_call", "tool_call", "route_decision"}
```

(No `_merge` change: the event uses `phase="end"`, so `result_summary` is copied as-is.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_trace_capture.py tests/test_trace_projection.py -q`
Expected: PASS.

- [ ] **Step 5: Add the emission test (wiring, not just mapping)**

Add to `tests/test_session_turn.py` a test that drives `run_turn` with a `RoutedSessionAgent` whose
router is a stub, and asserts the resulting `TraceRecord` contains a `route_decision` event. Use the
existing helpers/fixtures in that file for `store`/`bus`; the agent is:

```python
import pytest

from ecommerce_agent.routing.router import RouteDecision
from ecommerce_agent.sessions.factory import RoutedSessionAgent
from ecommerce_agent.sessions.turn import run_turn


class _StubRouter:
    async def route(self, message: str) -> RouteDecision:
        return RouteDecision("sales-analyst", "classifier", "analytics")


class _LeafAgent:
    async def astream_events(self, inputs, *, config, version):
        yield {"event": "on_chat_model_stream", "run_id": "r", "data": {"chunk": _Chunk("hi")}}


class _Chunk:
    def __init__(self, content):
        self.content = content


@pytest.mark.asyncio
async def test_run_turn_records_route_decision_event(thread_store, session_bus):
    agent = RoutedSessionAgent(
        router=_StubRouter(),
        agents={"sales-analyst": _LeafAgent(), "order-manager": _LeafAgent()},
        default_specialist="sales-analyst",
    )
    record = await run_turn(
        agent=agent,
        message="what were sales last month?",
        session_id="s1",
        turn_id="t1",
        store=thread_store,
        bus=session_bus,
        recursion_limit=5,
    )
    kinds = [(e.event_type, e.name) for e in record.events]
    assert ("route_decision", "sales-analyst") in kinds
```

Adapt the fixture names (`thread_store`, `session_bus`) to whatever `tests/test_session_turn.py`
already uses; if it constructs them inline, do the same here.

Run: `uv run pytest tests/test_session_turn.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/trace/capture.py src/ecommerce_agent/trace/projection.py tests/test_trace_capture.py tests/test_trace_projection.py tests/test_session_turn.py
git commit -m "feat(trace): surface route_decision in capture and timeline"
```

---

### Task 12: Live routing-eval comparison (RUN_LIVE_LLM)

**Files:**
- Create: `tests/integration/test_routing_eval_live.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_routing_eval_live.py`:

```python
import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.evals.metadata import run_metadata
from ecommerce_agent.evals.routing import compare, load_routing_cases, run_routing_eval
from ecommerce_agent.models import classifier_model_params, get_classifier_model
from ecommerce_agent.routing.keyword import KeywordRouter
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import ClassifierRouter
from ecommerce_agent.trace.jsonl import append_eval_baseline


@pytest.mark.integration
@pytest.mark.live
async def test_classifier_beats_keyword_on_adversarial(tmp_path):
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live routing eval")
    settings = Settings()
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    cases = load_routing_cases()
    registry = build_specialist_registry()

    keyword_report = await run_routing_eval(
        KeywordRouter(registry), cases, router_name="keyword"
    )
    classifier_report = await run_routing_eval(
        ClassifierRouter(get_classifier_model(settings), registry),
        cases,
        router_name="classifier",
    )

    # Persist the classifier baseline with the classifier's actual params.
    entry = {
        **run_metadata(
            settings,
            prompt_name="router_classifier",
            model=classifier_model_params(settings),
        ),
        "router_name": classifier_report.router_name,
        "n": classifier_report.n,
        "accuracy": classifier_report.accuracy,
        "per_tag_accuracy": classifier_report.per_tag_accuracy,
        "confusion": classifier_report.confusion,
    }
    append_eval_baseline(entry, str(tmp_path / "routing-baseline.jsonl"))

    delta = compare(keyword_report, classifier_report)
    # Primary gate: strictly beats keyword where keyword is weak.
    assert (
        classifier_report.per_tag_accuracy["adversarial"]
        > keyword_report.per_tag_accuracy["adversarial"]
    )
    # Absolute floor (advisory).
    assert classifier_report.accuracy >= 0.80
    assert delta["overall_delta"] >= 0
```

- [ ] **Step 2: Run it (gated)**

Run (skips): `uv run pytest tests/integration/test_routing_eval_live.py -q`
Expected: SKIPPED.

Run (live): `RUN_LIVE_LLM=1 uv run pytest tests/integration/test_routing_eval_live.py -q`
Expected: PASS — classifier strictly beats keyword on the adversarial subset, accuracy ≥ 0.80.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_routing_eval_live.py
git commit -m "test(evals): live routing eval — classifier vs keyword on adversarial set"
```

---

### Task 13 (optional, cuttable): `eval routing` CLI

**Files:**
- Modify: `src/ecommerce_agent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
from ecommerce_agent.cli import build_parser


def test_parser_has_eval_routing_subcommand():
    parser = build_parser()
    args = parser.parse_args(["eval", "routing"])
    assert args.command == "eval"
    assert args.eval_target == "routing"
    assert callable(args.func)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_parser_has_eval_routing_subcommand -q`
Expected: FAIL — `eval` is not a known subcommand.

- [ ] **Step 3: Add the subcommand**

In `src/ecommerce_agent/cli.py`, inside `build_parser` (after the `serve_parser` block) add:

```python
    eval_parser = subparsers.add_parser("eval", help="Run an eval")
    eval_parser.add_argument("eval_target", choices=["routing"])
    eval_parser.set_defaults(func=run_eval_command)
```

And add the command function:

```python
def run_eval_command(args: argparse.Namespace) -> None:
    import asyncio

    from ecommerce_agent.config import get_settings
    from ecommerce_agent.evals.routing import compare, load_routing_cases, run_routing_eval
    from ecommerce_agent.models import get_classifier_model
    from ecommerce_agent.routing.keyword import KeywordRouter
    from ecommerce_agent.routing.registry import build_specialist_registry
    from ecommerce_agent.routing.router import ClassifierRouter

    settings = get_settings()
    cases = load_routing_cases()
    registry = build_specialist_registry()

    async def _run():
        keyword = await run_routing_eval(KeywordRouter(registry), cases, router_name="keyword")
        classifier = await run_routing_eval(
            ClassifierRouter(get_classifier_model(settings), registry),
            cases,
            router_name="classifier",
        )
        return keyword, classifier

    keyword, classifier = asyncio.run(_run())
    delta = compare(keyword, classifier)
    print(f"keyword    accuracy={keyword.accuracy:.2f} adversarial={keyword.per_tag_accuracy.get('adversarial')}")
    print(f"classifier accuracy={classifier.accuracy:.2f} adversarial={classifier.per_tag_accuracy.get('adversarial')}")
    print(f"delta overall={delta['overall_delta']:+.2f} adversarial={delta['adversarial_delta']:+.2f} flips={delta['flips']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'eval routing' comparison subcommand"
```

---

### Task 14: Full-suite verification

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest -q`
Expected: PASS (live/integration tests skip without `RUN_LIVE_LLM` / Docker / Spring). No regressions
in trace, session, or factory tests.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean (fix any findings, then re-run).

- [ ] **Step 3 (recommended): run the live gate end-to-end**

Run: `RUN_LIVE_LLM=1 uv run pytest tests/integration/test_routing_classifier_live.py tests/integration/test_routing_eval_live.py -q`
Expected: PASS — confirms the classifier path and the keyword-vs-classifier delta against the live
deployment.

---

## Self-Review

**Spec coverage** (against [the spec](../2026-06-11-m4-routing-eval-design.md)):
- §3 async Router seam → Task 4. Registry-driven extensibility → Task 2. Canonical names → Tasks 2/8/10.
- §4.1 registry → Task 2. §4.2 RouteDecision/Router → Task 4. §4.3 ClassifierRouter + non-thinking model → Tasks 1, 3, 4; live validation → Task 5. §4.4 KeywordRouter → Task 6. §4.5 factory rewiring → Task 10. §4.6 route observability → Task 11.
- §5.1 dataset → Task 8. §5.2 scorer / §5.3 runner / §5.4 report+compare → Task 9. §5.5 metadata + baseline → Tasks 7, 12 (baseline rows now also record `thinking` + `structured_output_method`). §5.6 run surfaces: **offline keyword baseline over the real dataset in the default suite → Task 9 Step 6** (deterministic, asserts adversarial == 0.0); RUN_LIVE_LLM classifier → Task 12; CLI → Task 13.
- §7 error handling → Tasks 4 (fallback never raises), 9 (per-case error bucket), 10 (missing-key fallback). §8 testing → every task. §10 acceptance 1–7 → Tasks 10, 4, 8/9, 9, 12, 7, 11.
- §11 R-A: live spike gate → Task 5 (gates Task 10).

**Placeholder scan:** every code step contains full code; no TBD/“similar to”. Task 11 step 5 explicitly tells the engineer to adapt to the existing `test_session_turn.py` fixtures (the one place that depends on existing local setup) and shows the agent/assertion code.

**Type consistency:** `RouteDecision(specialist, source, reason)`, `Router.route` async, `ClassifierOutput(specialist, reason)`, `RoutedSessionAgent(*, router, agents, default_specialist)`, `EvalReport(... confusion: dict[str, dict[str,int]] ...)`, `run_metadata(settings, *, prompt_name, model=None)`, canonical names `sales-analyst`/`order-manager`, and the `on_route_decision`→`route_decision` mapping are used consistently across Tasks 1–13.
