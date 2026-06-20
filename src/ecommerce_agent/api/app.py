import asyncio
import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.api import health as health_module
from ecommerce_agent.api.audit import router as audit_router
from ecommerce_agent.api.auth import router as auth_router
from ecommerce_agent.api.monitoring import monitor_router, run_monitor_from_app
from ecommerce_agent.api.monitoring import router as monitoring_router
from ecommerce_agent.api.sessions import router as sessions_router
from ecommerce_agent.api.spa import mount_spa
from ecommerce_agent.audit.mongo import MongoAuditStore
from ecommerce_agent.auth.login_sessions import MongoLoginSessionStore
from ecommerce_agent.auth.users_store import MongoUserStore
from ecommerce_agent.config import Settings, get_settings, nl2sql_configured
from ecommerce_agent.mcp_client import (
    APPROVAL_SPRING_TOOLS,
    CUSTOMER_INSIGHTS_SPRING_TOOLS,
    INVENTORY_SPRING_TOOLS,
    NL2SQL_SERVER_NAME,
    NL2SQL_TOOLS,
    ORDER_MANAGER_SPRING_TOOLS,
    PURCHASING_SPRING_TOOLS,
    PYTHON_SERVER_NAME,
    READ_ONLY_SPRING_TOOLS,
    SPRING_SERVER_NAME,
    WRITE_SPRING_TOOLS,
    build_mcp_client,
    filter_customer_insights_tools,
    filter_inventory_tools,
    filter_nl2sql_tools,
    filter_order_manager_tools,
    filter_purchasing_tools,
    filter_spring_read_tools,
    tool_names,
)
from ecommerce_agent.monitoring.bus import AlertBus
from ecommerce_agent.monitoring.mongo import MongoAlertStore
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.factory import build_session_runtime
from ecommerce_agent.sessions.registry import SessionRegistry
from ecommerce_agent.sessions.store import MongoSessionStore
from ecommerce_agent.threads.mongo import MongoThreadStore
from ecommerce_agent.trace.mongo import MongoTraceStore

ApprovalClientCache = dict[tuple[str, str], Any]
logger = logging.getLogger(__name__)


def make_runtime_builder(settings: Settings):
    async def build_runtime(session_id: str, actor):
        return await build_session_runtime(session_id, settings, actor)

    return build_runtime


def _mongo_db(app: FastAPI, settings: Settings) -> Any:
    client = getattr(app.state, "mongo_client", None)
    if client is None:
        client = AsyncIOMotorClient(settings.mongo_url)
        app.state.mongo_client = client
    return client[settings.mongo_db]


def _configure_default_mongo_stores(app: FastAPI, settings: Settings) -> None:
    db: Any | None = None

    def get_db() -> Any:
        nonlocal db
        if db is None:
            db = _mongo_db(app, settings)
        return db

    if getattr(app.state, "thread_store", None) is None:
        mongo_db = get_db()
        app.state.thread_store = MongoThreadStore(
            messages=mongo_db["thread_messages"],
            counters=mongo_db["thread_counters"],
            client=app.state.mongo_client,
            retention_days=settings.audit_retention_days,
        )
    if getattr(app.state, "session_store", None) is None:
        mongo_db = get_db()
        app.state.session_store = MongoSessionStore(
            sessions=mongo_db["sessions"],
            client=app.state.mongo_client,
        )
    if getattr(app.state, "trace_store", None) is None:
        mongo_db = get_db()
        app.state.trace_store = MongoTraceStore(
            traces=mongo_db["traces"],
            client=app.state.mongo_client,
            retention_days=settings.audit_retention_days,
        )
    if getattr(app.state, "user_store", None) is None:
        mongo_db = get_db()
        app.state.user_store = MongoUserStore(
            users=mongo_db["users"],
            client=app.state.mongo_client,
        )
    if getattr(app.state, "login_session_store", None) is None:
        mongo_db = get_db()
        app.state.login_session_store = MongoLoginSessionStore(
            sessions=mongo_db["auth_sessions"],
            client=app.state.mongo_client,
        )
    if getattr(app.state, "audit_store", None) is None:
        mongo_db = get_db()
        app.state.audit_store = MongoAuditStore(
            messages=mongo_db["thread_messages"],
            client=app.state.mongo_client,
        )
    if getattr(app.state, "alert_store", None) is None:
        mongo_db = get_db()
        app.state.alert_store = MongoAlertStore(
            alerts=mongo_db["alerts"],
            client=app.state.mongo_client,
            retention_days=settings.alert_retention_days,
        )


def _close_resource(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def _close_mongo_resources(app: FastAPI) -> None:
    shared_client = getattr(app.state, "mongo_client", None)
    for name in (
        "thread_store",
        "session_store",
        "trace_store",
        "user_store",
        "login_session_store",
        "audit_store",
        "alert_store",
    ):
        store = getattr(app.state, name, None)
        if store is None:
            continue
        if shared_client is not None and getattr(store, "_client", None) is shared_client:
            continue
        _close_resource(store)
    _close_resource(shared_client)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.mcp_client = getattr(app.state, "mcp_client", None) or build_mcp_client(settings)
    _configure_default_mongo_stores(app, settings)
    for store in (
        app.state.thread_store,
        app.state.trace_store,
        app.state.user_store,
        app.state.login_session_store,
        app.state.audit_store,
        app.state.alert_store,
    ):
        ensure = getattr(store, "ensure_indexes", None)
        if callable(ensure):
            await ensure()
    app.state.session_bus = getattr(app.state, "session_bus", None) or SessionBus()
    app.state.alert_bus = getattr(app.state, "alert_bus", None) or AlertBus()
    app.state.monitor_run_lock = getattr(app.state, "monitor_run_lock", None) or asyncio.Lock()
    app.state.background_tasks = getattr(app.state, "background_tasks", None) or set()
    app.state.approval_clients = getattr(app.state, "approval_clients", None) or {}
    app.state.session_registry = getattr(app.state, "session_registry", None) or SessionRegistry(
        build_runtime=make_runtime_builder(settings),
        idle_ttl_seconds=settings.session_idle_ttl_seconds,
        max_live_sessions=settings.max_live_sessions,
    )
    app.state.reaper_task = asyncio.create_task(_reap_loop(app))
    app.state.monitor_task = None
    if settings.monitor_enabled:
        app.state.monitor_task = asyncio.create_task(_monitor_loop(app))
    try:
        yield
    finally:
        monitor_task = getattr(app.state, "monitor_task", None)
        if monitor_task is not None:
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task
        reaper_task = getattr(app.state, "reaper_task", None)
        if reaper_task is not None:
            reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await reaper_task
        pending_background_tasks = list(getattr(app.state, "background_tasks", set()))
        for task in pending_background_tasks:
            task.cancel()
        if pending_background_tasks:
            await asyncio.gather(*pending_background_tasks, return_exceptions=True)
            app.state.background_tasks.clear()
        await app.state.session_registry.close_all()
        monitor_runtime = getattr(app.state, "monitor_runtime", None)
        close_monitor = getattr(monitor_runtime, "close", None)
        if callable(close_monitor):
            result = close_monitor()
            if inspect.isawaitable(result):
                await result
        _close_mongo_resources(app)
        await _close_approval_clients(getattr(app.state, "approval_clients", {}))


async def _reap_loop(app: FastAPI) -> None:
    registry = app.state.session_registry
    try:
        while True:
            await asyncio.sleep(60)
            reaped_session_ids = await registry.reap_idle()
            await _evict_approval_clients_for_sessions(
                getattr(app.state, "approval_clients", {}),
                reaped_session_ids,
            )
            for store in (
                getattr(app.state, "thread_store", None),
                getattr(app.state, "trace_store", None),
                getattr(app.state, "alert_store", None),
            ):
                sweep = getattr(store, "sweep_expired", None)
                if callable(sweep):
                    await sweep()
    except asyncio.CancelledError:
        pass


async def _monitor_loop(app: FastAPI) -> None:
    try:
        while True:
            try:
                await run_monitor_from_app(app)
            except Exception:
                logger.warning("scheduled monitor run failed", exc_info=True)
            await asyncio.sleep(app.state.settings.monitor_interval_seconds)
    except asyncio.CancelledError:
        pass


async def _close_approval_client(client: Any) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


async def _close_approval_clients(clients: ApprovalClientCache) -> None:
    for client in clients.values():
        await _close_approval_client(client)
    clients.clear()


async def _evict_approval_clients_for_sessions(
    clients: ApprovalClientCache,
    session_ids: list[str],
) -> None:
    if not session_ids:
        return
    reaped = set(session_ids)
    stale_keys = [key for key in clients if key[0] in reaped]
    for key in stale_keys:
        await _close_approval_client(clients.pop(key))


def configured_mcp_servers(settings: Settings) -> list[str]:
    servers = [SPRING_SERVER_NAME]
    if settings.python_mcp_url:
        servers.append(PYTHON_SERVER_NAME)
    if nl2sql_configured(settings):
        servers.append(NL2SQL_SERVER_NAME)
    return servers


async def probe_mcp_server(mcp_client: Any, server_name: str) -> dict[str, Any]:
    try:
        tools = await mcp_client.get_tools(server_name=server_name)
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }

    names = tool_names(tools)
    result: dict[str, Any] = {
        "status": "ok",
        "tool_count": len(tools),
        "tools": sorted(names),
    }

    if server_name == SPRING_SERVER_NAME:
        read_tools = filter_spring_read_tools(tools)
        order_manager_tools = filter_order_manager_tools(tools)
        purchasing_tools = filter_purchasing_tools(tools)
        inventory_tools = filter_inventory_tools(tools)
        customer_insights_tools = filter_customer_insights_tools(tools)
        result.update(
            {
                "sales_analyst_allowed_tool_count": len(read_tools),
                "sales_analyst_allowed_tools": sorted(tool_names(read_tools)),
                "order_manager_allowed_tool_count": len(order_manager_tools),
                "order_manager_allowed_tools": sorted(tool_names(order_manager_tools)),
                "purchasing_allowed_tool_count": len(purchasing_tools),
                "purchasing_allowed_tools": sorted(tool_names(purchasing_tools)),
                "inventory_allowed_tool_count": len(inventory_tools),
                "inventory_allowed_tools": sorted(tool_names(inventory_tools)),
                "customer_insights_allowed_tool_count": len(customer_insights_tools),
                "customer_insights_allowed_tools": sorted(
                    tool_names(customer_insights_tools)
                ),
                "blocked_write_tools": sorted(names & WRITE_SPRING_TOOLS),
                "approval_tools": sorted(names & APPROVAL_SPRING_TOOLS),
                "missing_expected_read_tools": sorted(READ_ONLY_SPRING_TOOLS - names),
                "missing_expected_order_manager_tools": sorted(
                    ORDER_MANAGER_SPRING_TOOLS - names
                ),
                "missing_expected_purchasing_tools": sorted(PURCHASING_SPRING_TOOLS - names),
                "missing_expected_inventory_tools": sorted(
                    INVENTORY_SPRING_TOOLS - names
                ),
                "missing_expected_customer_insights_tools": sorted(
                    CUSTOMER_INSIGHTS_SPRING_TOOLS - names
                ),
            }
        )
    elif server_name == NL2SQL_SERVER_NAME:
        nl2sql_tools = filter_nl2sql_tools(tools)
        result.update(
            {
                "runtime_enabled": True,
                "data_warehouse_allowed_tool_count": len(nl2sql_tools),
                "data_warehouse_allowed_tools": sorted(tool_names(nl2sql_tools)),
                "expected_tools": sorted(NL2SQL_TOOLS),
                "missing_expected_tools": sorted(NL2SQL_TOOLS - names),
            }
        )

    return result


def create_app(
    settings: Settings | None = None,
    mcp_client: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="E-Commerce Agent", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.mcp_client = mcp_client
    app.state.last_trace = None
    app.state.trace_records = {}
    app.state.thread_store = None
    app.state.session_store = None
    app.state.trace_store = None
    app.state.mongo_client = None
    app.state.session_bus = None
    app.state.session_registry = None
    app.state.background_tasks = None
    app.state.approval_clients = None
    app.state.user_store = None
    app.state.login_session_store = None
    app.state.audit_store = None
    app.state.alert_store = None
    app.state.alert_bus = None
    app.state.monitor_run_lock = None
    app.state.monitor_runtime = None
    app.state.monitor_runtime_factory = None
    app.state.monitor_task = None

    @app.get("/health")
    async def health_endpoint() -> dict[str, Any]:
        components = await health_module.health_components(app.state)
        return {
            "status": "ok",
            "app": app.state.settings.app_name,
            "environment": app.state.settings.environment,
            "configured_mcp_servers": configured_mcp_servers(app.state.settings),
            "agent_ready": app.state.session_registry is not None,
            "components": components,
        }

    @app.get("/health/mcp")
    async def mcp_health() -> dict[str, Any]:
        servers = configured_mcp_servers(app.state.settings)
        server_results = {
            server_name: await probe_mcp_server(app.state.mcp_client, server_name)
            for server_name in servers
        }
        overall_status = (
            "ok"
            if all(result["status"] == "ok" for result in server_results.values())
            else "degraded"
        )
        return {"status": overall_status, "servers": server_results}

    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(monitoring_router)
    app.include_router(monitor_router)
    app.include_router(sessions_router)
    mount_spa(app, app.state.settings.frontend_dist_dir)
    return app


app = create_app()
