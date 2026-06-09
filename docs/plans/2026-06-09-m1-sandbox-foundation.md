# M1 Sandbox Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted, hardened `DockerSandbox` DeepAgents backend plus a tested, pre-baked `ecommerce_analysis` helper kit baked into a sandbox image — the isolated code-execution foundation the M1 sales-analyst will run on.

**Architecture:** `DockerSandbox(BaseSandbox)` implements the four abstract members DeepAgents requires (`id`, `execute`, `upload_files`, `download_files`); DeepAgents derives `read/write/edit/ls/glob/grep` from those. The backend runs a **persistent, network-isolated, resource-capped container per session** (`docker run sleep infinity`, then `docker exec` per call), with files persisting in a writable `/workspace` tmpfs across calls. The `ecommerce_analysis` package is deterministic pure-Python (pandas/numpy), unit-tested on the host and baked into the image so agent glue code composes reliable building blocks instead of fragile from-scratch pandas.

**Tech Stack:** Python 3.12, `docker` SDK (container lifecycle), `pandas`/`numpy` (helpers), `pytest` (host unit + real-Docker boundary tests that skip cleanly when Docker is absent). Spec: [docs/2026-06-09-week2-subagents-sandbox-design.md](../2026-06-09-week2-subagents-sandbox-design.md) §4.

**Scope note:** This is Plan 1 of 3 for M1. It deliberately stops at "a tested sandbox backend + helper kit." Agent wiring, prompts, viz (Plan 2) and trace/eval (Plan 3) are separate plans. Do not add agent/MCP/trace code here.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` (modify) | Add `docker` runtime dep; add `pandas`/`numpy` dev deps; add `pythonpath=["sandbox_image"]` so host tests import `ecommerce_analysis`. |
| `src/ecommerce_agent/config.py` (modify) | Add sandbox settings (image, mem/cpu/pids caps, execute timeout, idle TTL). |
| `sandbox_image/ecommerce_analysis/__init__.py` (create) | Package exports of the four helpers. |
| `sandbox_image/ecommerce_analysis/analysis.py` (create) | The four helper functions (pure pandas/numpy). |
| `sandbox_image/Dockerfile.sandbox` (create) | Build `ecommerce-agent-sandbox:dev`: python + pandas + numpy + `ecommerce_analysis`, non-root user, writable `/workspace`. |
| `src/ecommerce_agent/sandbox/__init__.py` (create) | Export `DockerSandbox`. |
| `src/ecommerce_agent/sandbox/config.py` (create) | `SandboxLimits` + `container_run_kwargs()` hardening builder. |
| `src/ecommerce_agent/sandbox/backend.py` (create) | `DockerSandbox(BaseSandbox)` + lifecycle + tar helpers. |
| `tests/test_analysis_helpers.py` (create) | Deterministic helper unit tests (fixtures). |
| `tests/test_sandbox_config.py` (create) | Hardening-kwargs unit tests (no Docker). |
| `tests/integration/test_docker_sandbox.py` (create) | Real-Docker boundary tests; skip if Docker absent. |
| `tests/integration/test_sandbox_image.py` (create) | Image smoke: `import ecommerce_analysis` inside the built image; skip if image/Docker absent. |
| `tests/integration/helpers.py` (modify) | Add `skip_unless_docker_available()`. |

**Helper input contract (the agent assembles this in Plan 2; here it is the tested schema):** `order_query` returns orders + line items but **no category** (category lives on `product`). So the agent's glue code joins product category and writes a flat line-item file. `load_orders_df` parses records with columns: `created_at` (ISO datetime), `status` (str), `category` (str), `amount` (numeric). Helper tests use fixtures matching this schema.

---

## Task 1: Dependencies + test path

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the `docker` runtime dep and `pandas`/`numpy` dev deps**

In `[project].dependencies` add:
```toml
    "docker>=7.1.0",
```
In `[dependency-groups].dev` add:
```toml
    "numpy>=2.1.0",
    "pandas>=2.2.0",
```

- [ ] **Step 2: Make `sandbox_image/` importable in host tests**

In `[tool.pytest.ini_options]` add a `pythonpath` key (keep existing keys):
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["sandbox_image"]
markers = [
    "integration: tests that require external services such as the SpringBoot MCP server",
    "live: tests that call a real LLM provider",
    "docker: tests that require a running Docker daemon",
]
```

- [ ] **Step 3: Sync and verify**

Run: `uv sync`
Expected: resolves and installs `docker`, `pandas`, `numpy` with no error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add docker/pandas/numpy deps and sandbox_image test path"
```

---

## Task 2: Sandbox settings in config

**Files:**
- Modify: `src/ecommerce_agent/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:
```python
def test_sandbox_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.sandbox_image == "ecommerce-agent-sandbox:dev"
    assert settings.sandbox_memory == "512m"
    assert settings.sandbox_cpus == 1.0
    assert settings.sandbox_pids == 128
    assert settings.sandbox_execute_timeout_seconds == 30
    assert settings.sandbox_idle_ttl_seconds == 600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_sandbox_settings_have_safe_defaults -v`
Expected: FAIL with `AttributeError`/validation error (fields don't exist).

- [ ] **Step 3: Add the fields**

In `src/ecommerce_agent/config.py`, inside class `Settings` (after the existing `mcp_*` fields, before `get_settings`):
```python
    # Sandbox (DockerSandbox backend)
    sandbox_image: str = "ecommerce-agent-sandbox:dev"
    sandbox_memory: str = "512m"
    sandbox_cpus: float = Field(default=1.0, gt=0)
    sandbox_pids: int = Field(default=128, gt=0)
    sandbox_execute_timeout_seconds: int = Field(default=30, gt=0)
    sandbox_idle_ttl_seconds: int = Field(default=600, gt=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/config.py tests/test_config.py
git commit -m "feat(config): add DockerSandbox settings with safe defaults"
```

---

## Task 3: `load_orders_df` helper

**Files:**
- Create: `sandbox_image/ecommerce_analysis/__init__.py`
- Create: `sandbox_image/ecommerce_analysis/analysis.py`
- Test: `tests/test_analysis_helpers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_analysis_helpers.py`:
```python
import json

import numpy as np
import pandas as pd
import pytest

from ecommerce_analysis import (
    load_orders_df,
    monthly_sales_by_category,
    simple_forecast,
    validate_forecast_result,
)


def _write_orders(tmp_path, records, name="orders.json"):
    path = tmp_path / name
    path.write_text(json.dumps(records))
    return str(path)


def test_load_orders_df_parses_and_types_records(tmp_path):
    records = [
        {"created_at": "2026-01-15T10:00:00", "status": "paid", "category": "electronics", "amount": "100.5"},
        {"created_at": "2026-02-20T10:00:00", "status": "shipped", "category": "electronics", "amount": 50},
    ]
    df = load_orders_df(_write_orders(tmp_path, records))

    assert list(df.columns) >= ["created_at", "status", "category", "amount"]
    assert pd.api.types.is_datetime64_any_dtype(df["created_at"])
    assert df["amount"].sum() == pytest.approx(150.5)


def test_load_orders_df_rejects_missing_columns(tmp_path):
    records = [{"created_at": "2026-01-15T10:00:00", "amount": 100}]
    with pytest.raises(ValueError, match="missing required columns"):
        load_orders_df(_write_orders(tmp_path, records))


def test_load_orders_df_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_orders_df(str(tmp_path / "nope.json"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analysis_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ecommerce_analysis'`.

- [ ] **Step 3: Create the package + `load_orders_df`**

Create `sandbox_image/ecommerce_analysis/__init__.py`:
```python
from ecommerce_analysis.analysis import (
    load_orders_df,
    monthly_sales_by_category,
    simple_forecast,
    validate_forecast_result,
)

__all__ = [
    "load_orders_df",
    "monthly_sales_by_category",
    "simple_forecast",
    "validate_forecast_result",
]
```

Create `sandbox_image/ecommerce_analysis/analysis.py`:
```python
"""Pre-baked commerce analysis helpers (run inside the sandbox).

Deterministic, dependency-light building blocks the agent composes with glue code
instead of authoring fragile pandas from scratch. They never fetch data or touch the
network: the agent fetches via MCP, writes a file into /workspace, and these parse it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REALIZED_STATUSES = ("paid", "shipped", "completed")
REQUIRED_COLUMNS = ("created_at", "status", "category", "amount")


def load_orders_df(path: str) -> pd.DataFrame:
    """Parse an order line-item file the agent wrote into /workspace.

    Expects JSON (list of records) or CSV with at least:
    created_at (ISO datetime), status (str), category (str), amount (numeric).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"order file not found: {path}")

    if p.suffix.lower() == ".csv":
        df = pd.read_csv(p)
    else:
        df = pd.DataFrame.from_records(json.loads(p.read_text()))

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}; got {list(df.columns)}")

    df = df.copy()
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    return df.dropna(subset=["created_at", "amount"]).reset_index(drop=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_analysis_helpers.py -k load_orders -v`
Expected: the three `load_orders_df` tests PASS (others error on missing functions — fixed next tasks).

- [ ] **Step 5: Commit**

```bash
git add sandbox_image/ecommerce_analysis/__init__.py sandbox_image/ecommerce_analysis/analysis.py tests/test_analysis_helpers.py
git commit -m "feat(analysis): add load_orders_df helper with schema validation"
```

---

## Task 4: `monthly_sales_by_category` helper

**Files:**
- Modify: `sandbox_image/ecommerce_analysis/analysis.py`
- Test: `tests/test_analysis_helpers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analysis_helpers.py`:
```python
def _orders_df(records):
    df = pd.DataFrame.from_records(records)
    df["created_at"] = pd.to_datetime(df["created_at"])
    return df


def test_monthly_sales_by_category_sums_realized_only():
    df = _orders_df([
        {"created_at": "2026-01-05", "status": "paid", "category": "electronics", "amount": 100},
        {"created_at": "2026-01-25", "status": "shipped", "category": "electronics", "amount": 40},
        {"created_at": "2026-01-10", "status": "pending", "category": "electronics", "amount": 999},
        {"created_at": "2026-02-10", "status": "completed", "category": "clothing", "amount": 70},
    ])
    out = monthly_sales_by_category(df)

    assert set(out.columns) == {"month", "category", "sales"}
    jan_elec = out[(out["category"] == "electronics") & (out["month"] == pd.Timestamp("2026-01-01"))]
    assert jan_elec["sales"].iloc[0] == pytest.approx(140.0)  # pending excluded
    assert not (out["category"] == "electronics").any() or 999 not in out["sales"].values


def test_monthly_sales_by_category_empty_when_no_realized():
    df = _orders_df([
        {"created_at": "2026-01-05", "status": "pending", "category": "electronics", "amount": 100},
    ])
    out = monthly_sales_by_category(df)
    assert out.empty
    assert set(out.columns) == {"month", "category", "sales"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analysis_helpers.py -k monthly -v`
Expected: FAIL with `ImportError`/`AttributeError` (function missing).

- [ ] **Step 3: Add the function**

Append to `sandbox_image/ecommerce_analysis/analysis.py`:
```python
def monthly_sales_by_category(orders_df: pd.DataFrame) -> pd.DataFrame:
    """Tidy monthly realized sales per category.

    Returns columns: month (month-start Timestamp), category, sales (float).
    Realized sales = rows whose status is in REALIZED_STATUSES.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in orders_df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    df = orders_df.copy()
    df = df[df["status"].isin(REALIZED_STATUSES)]
    if df.empty:
        return pd.DataFrame(columns=["month", "category", "sales"])

    df["month"] = df["created_at"].dt.to_period("M").dt.to_timestamp()
    grouped = (
        df.groupby(["month", "category"], as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "sales"})
    )
    grouped["sales"] = grouped["sales"].astype(float)
    return grouped.sort_values(["category", "month"]).reset_index(drop=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_analysis_helpers.py -k monthly -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sandbox_image/ecommerce_analysis/analysis.py tests/test_analysis_helpers.py
git commit -m "feat(analysis): add monthly_sales_by_category (realized sales only)"
```

---

## Task 5: `simple_forecast` helper

**Files:**
- Modify: `sandbox_image/ecommerce_analysis/analysis.py`
- Test: `tests/test_analysis_helpers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analysis_helpers.py`:
```python
def test_simple_forecast_extends_linear_trend():
    monthly = pd.DataFrame({
        "month": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
        "category": ["electronics"] * 3,
        "sales": [100.0, 200.0, 300.0],
    })
    out = simple_forecast(monthly, periods=1)

    assert set(out.columns) == {"category", "month", "sales", "is_forecast"}
    fc = out[out["is_forecast"]]
    assert len(fc) == 1
    assert fc["month"].iloc[0] == pd.Timestamp("2026-04-01")
    assert fc["sales"].iloc[0] == pytest.approx(400.0, abs=1.0)  # linear continuation
    assert (out["sales"] >= 0).all()


def test_simple_forecast_single_point_carries_last_value():
    monthly = pd.DataFrame({
        "month": pd.to_datetime(["2026-03-01"]),
        "category": ["clothing"],
        "sales": [80.0],
    })
    out = simple_forecast(monthly, periods=2)
    fc = out[out["is_forecast"]]
    assert len(fc) == 2
    assert (fc["sales"] == 80.0).all()


def test_simple_forecast_rejects_bad_periods():
    monthly = pd.DataFrame({"month": pd.to_datetime(["2026-01-01"]), "category": ["x"], "sales": [1.0]})
    with pytest.raises(ValueError, match="periods"):
        simple_forecast(monthly, periods=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analysis_helpers.py -k forecast -v`
Expected: FAIL (function missing).

- [ ] **Step 3: Add the function**

Append to `sandbox_image/ecommerce_analysis/analysis.py`:
```python
def simple_forecast(monthly_df: pd.DataFrame, periods: int = 1) -> pd.DataFrame:
    """Per-category linear-trend forecast for the next `periods` months.

    Input: output of monthly_sales_by_category (month, category, sales).
    Returns: category, month, sales, is_forecast — historical rows (False) plus
    forecast rows (True). Illustrative only: a linear fit on monthly points.
    """
    required = {"month", "category", "sales"}
    if not required.issubset(monthly_df.columns):
        raise ValueError(f"monthly_df missing columns: {required - set(monthly_df.columns)}")
    if periods < 1:
        raise ValueError("periods must be >= 1")
    if monthly_df.empty:
        return pd.DataFrame(columns=["category", "month", "sales", "is_forecast"])

    frames = []
    for category, grp in monthly_df.sort_values("month").groupby("category"):
        grp = grp.reset_index(drop=True)
        hist = grp[["category", "month", "sales"]].copy()
        hist["is_forecast"] = False
        frames.append(hist)

        sales = grp["sales"].to_numpy(dtype=float)
        last_month = grp["month"].iloc[-1]
        future_months = [
            (last_month.to_period("M") + i).to_timestamp() for i in range(1, periods + 1)
        ]
        if len(grp) >= 2:
            x = np.arange(len(grp), dtype=float)
            slope, intercept = np.polyfit(x, sales, 1)
            preds = [slope * (len(grp) - 1 + i) + intercept for i in range(1, periods + 1)]
        else:
            preds = [float(sales[-1])] * periods
        preds = [max(0.0, float(p)) for p in preds]

        frames.append(pd.DataFrame({
            "category": category,
            "month": future_months,
            "sales": preds,
            "is_forecast": True,
        }))

    return pd.concat(frames, ignore_index=True).sort_values(["category", "month"]).reset_index(drop=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_analysis_helpers.py -k forecast -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sandbox_image/ecommerce_analysis/analysis.py tests/test_analysis_helpers.py
git commit -m "feat(analysis): add simple_forecast linear-trend helper"
```

---

## Task 6: `validate_forecast_result` helper

**Files:**
- Modify: `sandbox_image/ecommerce_analysis/analysis.py`
- Test: `tests/test_analysis_helpers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analysis_helpers.py`:
```python
def test_validate_forecast_result_accepts_valid_frame():
    good = pd.DataFrame({
        "category": ["x", "x"],
        "month": pd.to_datetime(["2026-01-01", "2026-02-01"]),
        "sales": [10.0, 12.0],
        "is_forecast": [False, True],
    })
    validate_forecast_result(good)  # must not raise


def test_validate_forecast_result_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_forecast_result(pd.DataFrame(columns=["category", "month", "sales", "is_forecast"]))


def test_validate_forecast_result_rejects_non_finite():
    bad = pd.DataFrame({
        "category": ["x"],
        "month": pd.to_datetime(["2026-02-01"]),
        "sales": [np.inf],
        "is_forecast": [True],
    })
    with pytest.raises(ValueError, match="non-finite"):
        validate_forecast_result(bad)


def test_validate_forecast_result_requires_a_forecast_row():
    no_fc = pd.DataFrame({
        "category": ["x"],
        "month": pd.to_datetime(["2026-01-01"]),
        "sales": [10.0],
        "is_forecast": [False],
    })
    with pytest.raises(ValueError, match="no forecast rows"):
        validate_forecast_result(no_fc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analysis_helpers.py -k validate -v`
Expected: FAIL (function missing).

- [ ] **Step 3: Add the function**

Append to `sandbox_image/ecommerce_analysis/analysis.py`:
```python
def validate_forecast_result(forecast_df: pd.DataFrame) -> None:
    """Raise ValueError if the forecast frame is unusable for charting."""
    required = {"category", "month", "sales", "is_forecast"}
    if not required.issubset(forecast_df.columns):
        raise ValueError(f"forecast missing columns: {required - set(forecast_df.columns)}")
    if forecast_df.empty:
        raise ValueError("forecast is empty")
    if not forecast_df["is_forecast"].any():
        raise ValueError("forecast has no forecast rows")
    sales = pd.to_numeric(forecast_df["sales"], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(sales).all():
        raise ValueError("forecast contains non-finite sales values")
```

- [ ] **Step 4: Run the full helper suite to verify all pass**

Run: `uv run pytest tests/test_analysis_helpers.py -v`
Expected: PASS (all helper tests).

- [ ] **Step 5: Commit**

```bash
git add sandbox_image/ecommerce_analysis/analysis.py tests/test_analysis_helpers.py
git commit -m "feat(analysis): add validate_forecast_result guard"
```

---

## Task 7: Sandbox image (Dockerfile)

**Files:**
- Create: `sandbox_image/Dockerfile.sandbox`
- Modify: `tests/integration/helpers.py`
- Test: `tests/integration/test_sandbox_image.py`

- [ ] **Step 1: Add a Docker-availability skip helper**

Append to `tests/integration/helpers.py`:
```python
def skip_unless_docker_available() -> None:
    try:
        import docker
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"docker SDK not installed: {exc}")

    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        pytest.skip(f"Docker daemon not reachable: {exc}")
```

- [ ] **Step 2: Write the failing image smoke test**

Create `tests/integration/test_sandbox_image.py`:
```python
import subprocess

import pytest

from ecommerce_agent.config import Settings
from tests.integration.helpers import skip_unless_docker_available


@pytest.mark.integration
@pytest.mark.docker
def test_sandbox_image_has_helpers_importable():
    skip_unless_docker_available()
    image = Settings(_env_file=None).sandbox_image

    check = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    if check.returncode != 0:
        pytest.skip(f"image {image} not built; run: docker build -f sandbox_image/Dockerfile.sandbox -t {image} sandbox_image/")

    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", image,
         "python3", "-c", "import ecommerce_analysis, pandas, numpy; print('ok')"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
```

- [ ] **Step 3: Run test to verify it fails (or skips meaningfully)**

Run: `uv run pytest tests/integration/test_sandbox_image.py -v`
Expected: SKIP with "image ... not built" (or FAIL if Docker present and you force a build of a wrong image). The skip message names the build command.

- [ ] **Step 4: Create the Dockerfile**

Create `sandbox_image/Dockerfile.sandbox`:
```dockerfile
# Sandbox image for agent-generated code execution.
# Network-isolated at runtime (--network none), so all deps are baked in here.
FROM python:3.12-slim

# coreutils (incl. `timeout`) is present in debian-slim base.
RUN pip install --no-cache-dir "pandas>=2.2.0,<3" "numpy>=2.1.0,<3"

# Non-root user that owns the writable workspace.
RUN useradd --create-home --uid 10001 sandbox \
 && mkdir -p /workspace \
 && chown sandbox:sandbox /workspace

# Bake the helper kit onto the import path.
COPY ecommerce_analysis /opt/ecommerce_analysis/ecommerce_analysis
ENV PYTHONPATH=/opt/ecommerce_analysis

USER sandbox
WORKDIR /workspace
# Container is kept alive by the backend (docker run sleep infinity); this CMD is
# only used for ad-hoc `docker run ... python3 ...` smoke checks.
CMD ["python3", "-c", "import ecommerce_analysis; print('sandbox image ready')"]
```

- [ ] **Step 5: Build the image and verify the smoke test passes**

Run:
```bash
docker build -f sandbox_image/Dockerfile.sandbox -t ecommerce-agent-sandbox:dev sandbox_image/
uv run pytest tests/integration/test_sandbox_image.py -v
```
Expected: build succeeds; test PASSES (or SKIPS only if Docker is unavailable).

- [ ] **Step 6: Commit**

```bash
git add sandbox_image/Dockerfile.sandbox tests/integration/test_sandbox_image.py tests/integration/helpers.py
git commit -m "feat(sandbox): build sandbox image with baked ecommerce_analysis helpers"
```

---

## Task 8: Hardening kwargs builder

**Files:**
- Create: `src/ecommerce_agent/sandbox/__init__.py`
- Create: `src/ecommerce_agent/sandbox/config.py`
- Test: `tests/test_sandbox_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox_config.py`:
```python
from ecommerce_agent.config import Settings
from ecommerce_agent.sandbox.config import SandboxLimits, container_run_kwargs, limits_from_settings


def test_limits_from_settings_converts_cpus_to_nano():
    limits = limits_from_settings(Settings(_env_file=None))
    assert limits.image == "ecommerce-agent-sandbox:dev"
    assert limits.nano_cpus == 1_000_000_000
    assert limits.pids_limit == 128
    assert limits.execute_timeout_seconds == 30


def test_container_run_kwargs_is_hardened():
    limits = SandboxLimits(
        image="img:dev", mem_limit="256m", nano_cpus=500_000_000, pids_limit=64,
        execute_timeout_seconds=10, idle_ttl_seconds=300,
    )
    kwargs = container_run_kwargs(limits, name="ecommerce-sandbox-abc")

    assert kwargs["image"] == "img:dev"
    assert kwargs["name"] == "ecommerce-sandbox-abc"
    assert kwargs["command"] == ["sleep", "infinity"]
    assert kwargs["detach"] is True
    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
    assert kwargs["user"] == "sandbox"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["mem_limit"] == "256m"
    assert kwargs["nano_cpus"] == 500_000_000
    assert kwargs["pids_limit"] == 64
    assert "/workspace" in kwargs["tmpfs"]
    assert "/tmp" in kwargs["tmpfs"]
    assert kwargs["working_dir"] == "/workspace"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ecommerce_agent.sandbox'`.

- [ ] **Step 3: Create the package + config builder**

Create `src/ecommerce_agent/sandbox/__init__.py`:
```python
from ecommerce_agent.sandbox.backend import DockerSandbox

__all__ = ["DockerSandbox"]
```

> Note: `backend.py` is created in Task 9. If running Task 8 in isolation, temporarily make
> `__init__.py` empty, then restore this export in Task 9. (Subagent-driven execution runs tasks
> in order, so the import resolves by the time Task 8's tests import only `sandbox.config`.)

Create `src/ecommerce_agent/sandbox/config.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from ecommerce_agent.config import Settings


@dataclass(frozen=True)
class SandboxLimits:
    image: str
    mem_limit: str
    nano_cpus: int
    pids_limit: int
    execute_timeout_seconds: int
    idle_ttl_seconds: int
    workspace_size: str = "64m"
    tmp_size: str = "32m"


def limits_from_settings(settings: Settings) -> SandboxLimits:
    return SandboxLimits(
        image=settings.sandbox_image,
        mem_limit=settings.sandbox_memory,
        nano_cpus=int(settings.sandbox_cpus * 1_000_000_000),
        pids_limit=settings.sandbox_pids,
        execute_timeout_seconds=settings.sandbox_execute_timeout_seconds,
        idle_ttl_seconds=settings.sandbox_idle_ttl_seconds,
    )


def container_run_kwargs(limits: SandboxLimits, name: str) -> dict:
    """Docker SDK kwargs for a hardened, network-isolated, persistent sandbox container."""
    return {
        "image": limits.image,
        "name": name,
        "command": ["sleep", "infinity"],
        "detach": True,
        "working_dir": "/workspace",
        "network_mode": "none",
        "read_only": True,
        "tmpfs": {
            "/workspace": f"rw,size={limits.workspace_size},mode=1777",
            "/tmp": f"rw,size={limits.tmp_size},mode=1777",
        },
        "user": "sandbox",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "mem_limit": limits.mem_limit,
        "nano_cpus": limits.nano_cpus,
        "pids_limit": limits.pids_limit,
        "auto_remove": False,
    }
```

To satisfy the Task 8 import note, set `src/ecommerce_agent/sandbox/__init__.py` to empty for now; Task 9 restores the `DockerSandbox` export.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sandbox_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sandbox/__init__.py src/ecommerce_agent/sandbox/config.py tests/test_sandbox_config.py
git commit -m "feat(sandbox): hardening-kwargs builder + limits from settings"
```

---

## Task 9: `DockerSandbox` backend — lifecycle + execute

**Files:**
- Create: `src/ecommerce_agent/sandbox/backend.py`
- Modify: `src/ecommerce_agent/sandbox/__init__.py`
- Test: `tests/integration/test_docker_sandbox.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_docker_sandbox.py`:
```python
import uuid

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sandbox.backend import DockerSandbox
from ecommerce_agent.sandbox.config import limits_from_settings
from tests.integration.helpers import skip_unless_docker_available


@pytest.fixture
def sandbox():
    skip_unless_docker_available()
    import docker
    image = Settings(_env_file=None).sandbox_image
    try:
        docker.from_env().images.get(image)
    except Exception:
        pytest.skip(f"image {image} not built; run the Task 7 docker build")
    sb = DockerSandbox(limits_from_settings(Settings(_env_file=None)), session_id=uuid.uuid4().hex[:8])
    try:
        yield sb
    finally:
        sb.close()


@pytest.mark.integration
@pytest.mark.docker
def test_execute_runs_command_and_returns_output(sandbox):
    result = sandbox.execute("echo hello-sandbox")
    assert result.exit_code == 0
    assert "hello-sandbox" in result.output


@pytest.mark.integration
@pytest.mark.docker
def test_id_is_stable(sandbox):
    assert sandbox.id == sandbox.id
    assert sandbox.id.startswith("ecommerce-sandbox-")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_docker_sandbox.py -v`
Expected: FAIL with `ImportError` (no `DockerSandbox` in `backend`) — or SKIP if Docker/image absent. To exercise the code, build the image (Task 7) and have Docker running.

- [ ] **Step 3: Implement the backend**

Create `src/ecommerce_agent/sandbox/backend.py`:
```python
"""DockerSandbox: a self-hosted, hardened DeepAgents BaseSandbox backend.

Persistent per-session container (docker run sleep infinity), reused via docker exec.
Implements the four abstract BaseSandbox members; DeepAgents derives read/write/edit/
ls/glob/grep from execute()/upload_files()/download_files().
"""
from __future__ import annotations

import io
import posixpath
import tarfile
import time
import uuid

import docker

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from ecommerce_agent.sandbox.config import SandboxLimits, container_run_kwargs

_OUTPUT_LIMIT = 64 * 1024  # cap captured output at 64 KiB
_TIMEOUT_EXIT = 124  # coreutils `timeout` exit code on expiry


def _split_workspace_path(path: str) -> tuple[str, str]:
    directory = posixpath.dirname(path) or "/workspace"
    name = posixpath.basename(path)
    if not name:
        raise ValueError(f"invalid file path: {path!r}")
    return directory, name


def _single_file_tar(name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _extract_single_file(stream) -> bytes:
    raw = b"".join(stream)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        if not members:
            raise FileNotFoundError("archive contained no file")
        extracted = tar.extractfile(members[0])
        return extracted.read() if extracted else b""


class DockerSandbox(BaseSandbox):
    def __init__(self, limits: SandboxLimits, *, session_id: str | None = None, client=None):
        self._limits = limits
        self._session_id = session_id or uuid.uuid4().hex[:12]
        self._client = client or docker.from_env()
        self._container = None
        self._last_used = time.monotonic()

    @property
    def id(self) -> str:
        return f"ecommerce-sandbox-{self._session_id}"

    # --- lifecycle ---
    def _ensure_container(self):
        if self._container is not None:
            self._container.reload()
            if self._container.status == "running":
                return self._container
            self._remove_quietly()
        self._container = self._client.containers.run(
            **container_run_kwargs(self._limits, name=self.id)
        )
        return self._container

    def _remove_quietly(self) -> None:
        if self._container is not None:
            try:
                self._container.remove(force=True)
            finally:
                self._container = None

    def close(self) -> None:
        self._remove_quietly()

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used

    # --- BaseSandbox abstract members ---
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        container = self._ensure_container()
        self._last_used = time.monotonic()
        secs = int(timeout or self._limits.execute_timeout_seconds)
        result = container.exec_run(
            cmd=["timeout", str(secs), "sh", "-c", command], demux=False
        )
        text = (result.output or b"").decode("utf-8", errors="replace")
        truncated = len(text) > _OUTPUT_LIMIT
        if truncated:
            text = text[:_OUTPUT_LIMIT] + "\n[output truncated]"
        if result.exit_code == _TIMEOUT_EXIT:
            text += f"\n[execution exceeded {secs}s timeout]"
        return ExecuteResponse(output=text, exit_code=result.exit_code, truncated=truncated)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        container = self._ensure_container()
        self._last_used = time.monotonic()
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                directory, name = _split_workspace_path(path)
                ok = container.put_archive(directory, _single_file_tar(name, content))
                responses.append(
                    FileUploadResponse(path=path, error=None if ok else "put_archive returned False")
                )
            except Exception as exc:
                responses.append(FileUploadResponse(path=path, error=f"{type(exc).__name__}: {exc}"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        container = self._ensure_container()
        self._last_used = time.monotonic()
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                stream, _ = container.get_archive(path)
                responses.append(
                    FileDownloadResponse(path=path, content=_extract_single_file(stream), error=None)
                )
            except Exception as exc:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error=f"{type(exc).__name__}: {exc}")
                )
        return responses
```

Restore `src/ecommerce_agent/sandbox/__init__.py`:
```python
from ecommerce_agent.sandbox.backend import DockerSandbox

__all__ = ["DockerSandbox"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_docker_sandbox.py -v`
Expected: PASS (with Docker + built image) or clean SKIP otherwise.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sandbox/backend.py src/ecommerce_agent/sandbox/__init__.py tests/integration/test_docker_sandbox.py
git commit -m "feat(sandbox): DockerSandbox backend with persistent-container lifecycle + execute"
```

---

## Task 10: Files + upload/download + hardening boundary tests

**Files:**
- Test: `tests/integration/test_docker_sandbox.py`

- [ ] **Step 1: Write the boundary tests**

Append to `tests/integration/test_docker_sandbox.py`:
```python
@pytest.mark.integration
@pytest.mark.docker
def test_files_persist_across_execute_calls(sandbox):
    write = sandbox.execute("echo persisted > /workspace/state.txt")
    assert write.exit_code == 0
    read = sandbox.execute("cat /workspace/state.txt")
    assert read.exit_code == 0
    assert "persisted" in read.output


@pytest.mark.integration
@pytest.mark.docker
def test_upload_then_download_roundtrip(sandbox):
    [up] = sandbox.upload_files([("/workspace/data.json", b'{"k": 1}')])
    assert up.error is None
    seen = sandbox.execute("cat /workspace/data.json")
    assert '"k": 1' in seen.output
    [down] = sandbox.download_files(["/workspace/data.json"])
    assert down.error is None
    assert down.content == b'{"k": 1}'


@pytest.mark.integration
@pytest.mark.docker
def test_network_is_isolated(sandbox):
    # --network none: any outbound socket must fail.
    code = (
        "import socket,sys\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1',53),timeout=3); print('REACHED')\n"
        "except Exception as e:\n"
        "    print('BLOCKED', type(e).__name__); sys.exit(0)\n"
    )
    [up] = sandbox.upload_files([("/workspace/net.py", code.encode())])
    assert up.error is None
    result = sandbox.execute("python3 /workspace/net.py")
    assert "BLOCKED" in result.output
    assert "REACHED" not in result.output


@pytest.mark.integration
@pytest.mark.docker
def test_execute_timeout_is_enforced(sandbox):
    result = sandbox.execute("sleep 10", timeout=1)
    assert result.exit_code == 124
    assert "timeout" in result.output.lower()


@pytest.mark.integration
@pytest.mark.docker
def test_helper_kit_is_importable_in_sandbox(sandbox):
    result = sandbox.execute("python3 -c 'import ecommerce_analysis; print(\"helpers-ok\")'")
    assert result.exit_code == 0
    assert "helpers-ok" in result.output


@pytest.mark.integration
@pytest.mark.docker
def test_close_removes_the_container(sandbox):
    sandbox.execute("true")  # force container creation
    import docker
    client = docker.from_env()
    assert client.containers.get(sandbox.id) is not None
    sandbox.close()
    with pytest.raises(docker.errors.NotFound):
        client.containers.get(sandbox.id)
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/integration/test_docker_sandbox.py -v`
Expected: all PASS with Docker + built image (network-isolation, timeout, persistence, roundtrip, helper import, teardown), or clean SKIP without Docker.

> If `test_network_is_isolated` ever fails with `REACHED`, the hardening regressed — `network_mode="none"` is not applied. If `test_execute_timeout_is_enforced` returns exit_code 0, the `timeout` wrapper is missing. These are the two highest-value guards in this plan.

- [ ] **Step 3: Run the whole default suite (no Docker required) to confirm nothing regressed**

Run: `uv run pytest -m "not integration and not live" -q && uv run ruff check .`
Expected: all default tests PASS (incl. helper unit tests, sandbox config); ruff clean.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_docker_sandbox.py
git commit -m "test(sandbox): network-isolation, timeout, persistence, upload/download, teardown boundary tests"
```

---

## Self-Review

**Spec coverage (spec §4 + §9 default-suite items for the sandbox):**
- §4.1 BaseSandbox conformance + persistent-container lifecycle → Task 9. ✅
- §4.2 hardening flags (network none, read-only, tmpfs, non-root, cap-drop, no-new-privileges, mem/cpu/pids, per-execute timeout) → Task 8 (builder) + Task 10 (boundary assertions). ✅
- §4.3 prebuilt image with python+pandas+numpy+`ecommerce_analysis` → Task 7. ✅
- §4.5 four-helper kit, deterministic + unit-tested, stable signatures → Tasks 3–6. ✅
- §9 default boundary tests: helper unit tests (Tasks 3–6), real-Docker sandbox tests incl. `--network none`/timeout/persistence/teardown (Task 10), image smoke (Task 7), Docker-absent skip (Task 7 helper). ✅
- Out of scope here (correctly deferred to Plans 2/3): agent wiring, prompts, viz seam, trace module, eval harness, idle-TTL reaper scheduler (the `idle_seconds()` hook exists; the scheduler lands with app lifespan in Plan 2).

**Placeholder scan:** No TBD/TODO; every code step is complete. The only forward-reference is the Task 8 `__init__.py` note, resolved in Task 9.

**Type consistency:** `ExecuteResponse(output, exit_code, truncated)`, `FileUploadResponse(path, error)`, `FileDownloadResponse(path, content, error)` match the installed `deepagents` 0.6.8 signatures. Helper names (`load_orders_df`, `monthly_sales_by_category`, `simple_forecast`, `validate_forecast_result`) and the `SandboxLimits`/`container_run_kwargs`/`limits_from_settings` API are consistent across tasks. `DockerSandbox(limits, *, session_id, client)` is constructed identically in Tasks 9 and 10.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-09-m1-sandbox-foundation.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?** (And: this is Plan 1 of 3 — want Plans 2 and 3 written before any execution starts, or execute Plan 1 first?)
