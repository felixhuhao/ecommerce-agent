import asyncio

import pytest

from ecommerce_agent.sessions.registry import RuntimeActor, SessionRegistry, SessionRuntime

TEST_ACTOR = RuntimeActor(user_id="alice", spring_user_id=7, can_propose=True)


def make_runtime(
    session_id: str,
    sandbox: object,
    actor: RuntimeActor = TEST_ACTOR,
) -> SessionRuntime:
    return SessionRuntime(
        session_id=session_id,
        agent=object(),
        mcp_client=object(),
        sandbox=sandbox,
        owner_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
    )


@pytest.mark.asyncio
async def test_create_then_get_returns_same_runtime() -> None:
    built: list[str] = []

    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        built.append(session_id)
        return make_runtime(session_id, sandbox=object(), actor=actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    session_id = await registry.create(TEST_ACTOR)
    runtime = await registry.get(session_id)

    assert runtime.session_id == session_id
    assert built == [session_id]
    assert await registry.get(session_id) is runtime


@pytest.mark.asyncio
async def test_get_unknown_session_raises_keyerror() -> None:
    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return make_runtime(session_id, sandbox=object(), actor=actor)

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

    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return make_runtime(session_id, sandbox=FakeSandbox(session_id), actor=actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=0, max_live_sessions=50)
    session_id = await registry.create(TEST_ACTOR)

    reaped = await registry.reap_idle()

    assert reaped == [session_id]
    assert closed == [session_id]
    with pytest.raises(KeyError):
        await registry.get(session_id)


@pytest.mark.asyncio
async def test_reap_idle_skips_session_with_active_turn() -> None:
    closed: list[str] = []

    class FakeSandbox:
        def close(self) -> None:
            closed.append("closed")

    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return make_runtime(session_id, sandbox=FakeSandbox(), actor=actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=0, max_live_sessions=50)
    session_id = await registry.create(TEST_ACTOR)
    assert await registry.try_begin_turn(session_id) is True

    assert await registry.reap_idle() == []
    assert closed == []
    assert (await registry.get(session_id)).session_id == session_id

    await registry.end_turn(session_id)
    assert await registry.reap_idle() == [session_id]
    assert closed == ["closed"]


@pytest.mark.asyncio
async def test_create_evicts_oldest_when_at_cap() -> None:
    closed: list[str] = []

    class FakeSandbox:
        def __init__(self, session_id: str) -> None:
            self._session_id = session_id

        def close(self) -> None:
            closed.append(self._session_id)

    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return make_runtime(session_id, sandbox=FakeSandbox(session_id), actor=actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=1)
    first = await registry.create(TEST_ACTOR)
    second = await registry.create(TEST_ACTOR)

    assert closed == [first]
    with pytest.raises(KeyError):
        await registry.get(first)
    assert (await registry.get(second)).session_id == second


@pytest.mark.asyncio
async def test_create_enforces_cap_after_concurrent_builds() -> None:
    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        await asyncio.sleep(0.01)
        return make_runtime(session_id, sandbox=object(), actor=actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=1)

    first, second = await asyncio.gather(
        registry.create(TEST_ACTOR),
        registry.create(TEST_ACTOR),
    )

    ids_in_registry = []
    for s in (first, second):
        try:
            await registry.get(s)
            ids_in_registry.append(s)
        except KeyError:
            pass
    assert len(ids_in_registry) == 1


@pytest.mark.asyncio
async def test_get_or_create_rehydrates_known_session() -> None:
    built: list[str] = []

    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        built.append(session_id)
        return make_runtime(session_id, sandbox=object(), actor=actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    async def known(session_id: str) -> bool:
        return session_id == "known"

    runtime = await registry.get_or_create_runtime("known", TEST_ACTOR, known)
    assert runtime.session_id == "known"
    assert await registry.get_or_create_runtime("known", TEST_ACTOR, known) is runtime
    assert built == ["known"]

    with pytest.raises(KeyError):
        await registry.get_or_create_runtime("ghost", TEST_ACTOR, known)


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

    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        nonlocal build_count
        build_count += 1
        label = f"{session_id}-{build_count}"
        build_started.set()
        if build_count == 2:
            both_started.set()
        await release_builds.wait()
        return make_runtime(session_id, sandbox=FakeSandbox(label), actor=actor)

    async def known(session_id: str) -> bool:
        return session_id == "known"

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)
    first = asyncio.create_task(registry.get_or_create_runtime("known", TEST_ACTOR, known))
    await build_started.wait()
    second = asyncio.create_task(registry.get_or_create_runtime("known", TEST_ACTOR, known))
    await both_started.wait()
    release_builds.set()

    first_runtime, second_runtime = await asyncio.gather(first, second)

    assert first_runtime is second_runtime
    assert build_count == 2
    assert len(closed) == 1


@pytest.mark.asyncio
async def test_concurrent_rehydration_preserves_winner_at_cap_1() -> None:
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

    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        nonlocal build_count
        build_count += 1
        label = f"{session_id}-{build_count}"
        build_started.set()
        if build_count == 2:
            both_started.set()
        await release_builds.wait()
        return make_runtime(session_id, sandbox=FakeSandbox(label), actor=actor)

    async def known(session_id: str) -> bool:
        return session_id == "known"

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=1)
    first = asyncio.create_task(registry.get_or_create_runtime("known", TEST_ACTOR, known))
    await build_started.wait()
    second = asyncio.create_task(registry.get_or_create_runtime("known", TEST_ACTOR, known))
    await both_started.wait()
    release_builds.set()

    first_runtime, second_runtime = await asyncio.gather(first, second)

    assert first_runtime is second_runtime
    assert len(closed) == 1
    assert (await registry.get("known")) is first_runtime


@pytest.mark.asyncio
async def test_try_begin_turn_enforces_single_turn() -> None:
    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return make_runtime(session_id, sandbox=object(), actor=actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    assert await registry.try_begin_turn("s1") is True
    assert await registry.try_begin_turn("s1") is False
    await registry.end_turn("s1")
    assert await registry.try_begin_turn("s1") is True
