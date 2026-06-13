import pytest

from ecommerce_agent.sessions.registry import RuntimeActor, SessionRegistry, SessionRuntime


def _runtime(session_id: str, actor: RuntimeActor) -> SessionRuntime:
    return SessionRuntime(
        session_id=session_id,
        agent=object(),
        mcp_client=None,
        sandbox=None,
        owner_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
    )


async def test_create_binds_owner_and_spring_id():
    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return _runtime(session_id, actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)
    actor = RuntimeActor(user_id="alice", spring_user_id=7, can_propose=True)
    session_id = await registry.create(actor)
    runtime = await registry.get(session_id)
    assert runtime.owner_id == "alice"
    assert runtime.spring_user_id == 7


async def test_cached_runtime_owner_mismatch_raises():
    async def build(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return _runtime(session_id, actor)

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)
    alice = RuntimeActor(user_id="alice", spring_user_id=7, can_propose=True)
    session_id = await registry.create(alice)

    async def known(_session_id: str) -> bool:
        return True

    bob = RuntimeActor(user_id="bob", spring_user_id=8, can_propose=True)
    with pytest.raises(PermissionError):
        await registry.get_or_create_runtime(session_id, bob, known)
