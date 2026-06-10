from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionRuntime:
    session_id: str
    agent: Any
    mcp_client: Any
    sandbox: Any
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    def close(self) -> None:
        close = getattr(self.sandbox, "close", None)
        if callable(close):
            close()


BuildRuntime = Callable[[str], Awaitable[SessionRuntime]]


class SessionRegistry:
    """Owns per-session runtimes; single-instance/in-process only."""

    def __init__(
        self,
        *,
        build_runtime: BuildRuntime,
        idle_ttl_seconds: int,
        max_live_sessions: int,
    ) -> None:
        self._build_runtime = build_runtime
        self._idle_ttl_seconds = idle_ttl_seconds
        self._max_live_sessions = max_live_sessions
        self._runtimes: dict[str, SessionRuntime] = {}
        self._active_turns: set[str] = set()
        self._lock = asyncio.Lock()

    async def create(self) -> str:
        session_id = uuid.uuid4().hex
        evicted: list[SessionRuntime] = []
        try:
            runtime = await self._build_runtime(session_id)
        except Exception:
            await self._close_evicted(evicted)
            raise
        async with self._lock:
            try:
                evicted.extend(self._make_room_locked())
                self._runtimes[session_id] = runtime
            except Exception:
                evicted.append(runtime)
                raise
        await self._close_evicted(evicted)
        return session_id

    async def get(self, session_id: str) -> SessionRuntime:
        async with self._lock:
            runtime = self._runtimes.get(session_id)
            if runtime is None:
                raise KeyError(session_id)
            runtime.touch()
            return runtime

    async def get_or_create_runtime(
        self,
        session_id: str,
        session_known: Callable[[str], Awaitable[bool]],
    ) -> SessionRuntime:
        async with self._lock:
            cached = self._runtimes.get(session_id)
            if cached is not None:
                cached.touch()
                return cached

        if not await session_known(session_id):
            raise KeyError(session_id)

        runtime = await self._build_runtime(session_id)
        evicted: list[SessionRuntime] = []
        loser: SessionRuntime | None = None
        async with self._lock:
            winner = self._runtimes.get(session_id)
            if winner is not None:
                loser = runtime
                winner.touch()
            else:
                evicted.extend(self._make_room_locked())
                self._runtimes[session_id] = runtime
        await self._close_evicted([loser] if loser else evicted)
        return winner if loser is not None else runtime

    async def try_begin_turn(self, session_id: str) -> bool:
        async with self._lock:
            if session_id in self._active_turns:
                return False
            self._active_turns.add(session_id)
            return True

    async def end_turn(self, session_id: str) -> None:
        async with self._lock:
            self._active_turns.discard(session_id)

    async def reap_idle(self) -> list[str]:
        async with self._lock:
            evicted = self._reap_idle_locked()
        await self._close_evicted(evicted)
        return [rt.session_id for rt in evicted]

    async def close_all(self) -> list[str]:
        async with self._lock:
            evicted = list(self._runtimes.values())
            self._runtimes.clear()
            self._active_turns.clear()
        await self._close_evicted(evicted)
        return [rt.session_id for rt in evicted]

    async def _close_evicted(self, runtimes: list[SessionRuntime]) -> None:
        if not runtimes:
            return
        results = await asyncio.gather(
            *(asyncio.to_thread(rt.close) for rt in runtimes),
            return_exceptions=True,
        )
        for rt, result in zip(runtimes, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Failed to close runtime %s: %s", rt.session_id, result)

    def _reap_idle_locked(self) -> list[SessionRuntime]:
        evicted: list[SessionRuntime] = []
        for session_id, runtime in list(self._runtimes.items()):
            if session_id in self._active_turns:
                continue
            if runtime.idle_seconds() >= self._idle_ttl_seconds:
                del self._runtimes[session_id]
                evicted.append(runtime)
        return evicted

    def _make_room_locked(self) -> list[SessionRuntime]:
        evicted = self._reap_idle_locked()
        while len(self._runtimes) >= self._max_live_sessions:
            oldest = min(self._runtimes.values(), key=lambda runtime: runtime.last_used)
            del self._runtimes[oldest.session_id]
            evicted.append(oldest)
        return evicted
