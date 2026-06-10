from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


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
        async with self._lock:
            self._make_room_locked()
            runtime = await self._build_runtime(session_id)
            try:
                self._make_room_locked()
                self._runtimes[session_id] = runtime
            except Exception:
                runtime.close()
                raise
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
        async with self._lock:
            winner = self._runtimes.get(session_id)
            if winner is not None:
                runtime.close()
                winner.touch()
                return winner
            self._make_room_locked()
            self._runtimes[session_id] = runtime
            return runtime

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
            return self._reap_idle_locked()

    async def close_all(self) -> None:
        async with self._lock:
            for runtime in self._runtimes.values():
                runtime.close()
            self._runtimes.clear()
            self._active_turns.clear()

    def _reap_idle_locked(self) -> list[str]:
        reaped: list[str] = []
        for session_id, runtime in list(self._runtimes.items()):
            if session_id in self._active_turns:
                continue
            if runtime.idle_seconds() >= self._idle_ttl_seconds:
                runtime.close()
                del self._runtimes[session_id]
                reaped.append(session_id)
        return reaped

    def _make_room_locked(self) -> list[str]:
        reaped = self._reap_idle_locked()
        while len(self._runtimes) >= self._max_live_sessions:
            oldest = min(self._runtimes.values(), key=lambda runtime: runtime.last_used)
            oldest.close()
            del self._runtimes[oldest.session_id]
            reaped.append(oldest.session_id)
        return reaped
