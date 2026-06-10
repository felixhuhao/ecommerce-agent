import asyncio

import pytest

from ecommerce_agent.sessions.registry import SessionRegistry, SessionRuntime


def make_runtime(session_id: str, sandbox: object) -> SessionRuntime:
    return SessionRuntime(
        session_id=session_id,
        agent=object(),
        mcp_client=object(),
        sandbox=sandbox,
    )


@pytest.mark.asyncio
async def test_create_then_get_returns_same_runtime() -> None:
    built: list[str] = []

    async def build(session_id: str) -> SessionRuntime:
        built.append(session_id)
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    session_id = await registry.create()
    runtime = await registry.get(session_id)

    assert runtime.session_id == session_id
    assert built == [session_id]
    assert await registry.get(session_id) is runtime


@pytest.mark.asyncio
async def test_get_unknown_session_raises_keyerror() -> None:
    async def build(session_id: str) -> SessionRuntime:
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    with pytest.raises(KeyError):
        await registry.get("nope")


@pytest.mark.asyncio
async def test_reap_idle_closes_sandbox_and_drops_session() -> None:
    closed: list[str] = []

    class FakeSandbox:
        def __init__(self, session_id: str) -> None:
            self._session_id = session_id

        def close(self) -> None:
            closed.append(self._session_id)

    async def build(session_id: str) -> SessionRuntime:
        return make_runtime(session_id, sandbox=FakeSandbox(session_id))

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=0, max_live_sessions=50)
    session_id = await registry.create()

    reaped = await registry.reap_idle()

    assert reaped == [session_id]
    assert closed == [session_id]
    with pytest.raises(KeyError):
        await registry.get(session_id)


@pytest.mark.asyncio
async def test_create_evicts_oldest_when_at_cap() -> None:
    closed: list[str] = []

    class FakeSandbox:
        def __init__(self, session_id: str) -> None:
            self._session_id = session_id

        def close(self) -> None:
            closed.append(self._session_id)

    async def build(session_id: str) -> SessionRuntime:
        return make_runtime(session_id, sandbox=FakeSandbox(session_id))

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=1)
    first = await registry.create()
    second = await registry.create()

    assert closed == [first]
    with pytest.raises(KeyError):
        await registry.get(first)
    assert (await registry.get(second)).session_id == second


@pytest.mark.asyncio
async def test_create_serializes_runtime_builds_to_preserve_cap() -> None:
    active_builds = 0
    max_active_builds = 0

    async def build(session_id: str) -> SessionRuntime:
        nonlocal active_builds, max_active_builds
        active_builds += 1
        max_active_builds = max(max_active_builds, active_builds)
        await asyncio.sleep(0.01)
        active_builds -= 1
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=1)

    first, second = await asyncio.gather(registry.create(), registry.create())

    assert max_active_builds == 1
    with pytest.raises(KeyError):
        await registry.get(first)
    assert (await registry.get(second)).session_id == second


@pytest.mark.asyncio
async def test_get_or_create_rehydrates_known_session() -> None:
    built: list[str] = []

    async def build(session_id: str) -> SessionRuntime:
        built.append(session_id)
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    async def known(session_id: str) -> bool:
        return session_id == "known"

    runtime = await registry.get_or_create_runtime("known", known)
    assert runtime.session_id == "known"
    assert await registry.get_or_create_runtime("known", known) is runtime
    assert built == ["known"]

    with pytest.raises(KeyError):
        await registry.get_or_create_runtime("ghost", known)


@pytest.mark.asyncio
async def test_concurrent_rehydration_closes_loser_runtime() -> None:
    closed: list[str] = []

    class FakeSandbox:
        def __init__(self, label: str) -> None:
            self.label = label

        def close(self) -> None:
            closed.append(self.label)

    build_started = asyncio.Event()
    both_started = asyncio.Event()
    release_builds = asyncio.Event()
    build_count = 0

    async def build(session_id: str) -> SessionRuntime:
        nonlocal build_count
        build_count += 1
        label = f"{session_id}-{build_count}"
        build_started.set()
        if build_count == 2:
            both_started.set()
        await release_builds.wait()
        return make_runtime(session_id, sandbox=FakeSandbox(label))

    async def known(session_id: str) -> bool:
        return session_id == "known"

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)
    first = asyncio.create_task(registry.get_or_create_runtime("known", known))
    await build_started.wait()
    second = asyncio.create_task(registry.get_or_create_runtime("known", known))
    await both_started.wait()
    release_builds.set()

    first_runtime, second_runtime = await asyncio.gather(first, second)

    assert first_runtime is second_runtime
    assert build_count == 2
    assert len(closed) == 1


@pytest.mark.asyncio
async def test_try_begin_turn_enforces_single_turn() -> None:
    async def build(session_id: str) -> SessionRuntime:
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    assert await registry.try_begin_turn("s1") is True
    assert await registry.try_begin_turn("s1") is False
    await registry.end_turn("s1")
    assert await registry.try_begin_turn("s1") is True
