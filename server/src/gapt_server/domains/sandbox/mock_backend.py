"""In-memory `SandboxBackend` — for tests + dev where docker isn't
available. Models a small state machine so workflow code can be
exercised without booting containers.

States: `creating` → `running` (after start) → `stopped` (after stop)
→ `destroyed` (after destroy). `exec_in` only succeeds in `running`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from gapt_server.db.ulid import new_ulid
from gapt_server.domains.sandbox.backend import (
    ExecResult,
    SandboxBackendError,
    SandboxCreateSpec,
    SandboxInfo,
    SandboxRef,
    validate_mounts,
)


@dataclass
class _MockState:
    spec: SandboxCreateSpec
    status: str
    created_at: float
    exec_log: list[list[str]] = field(default_factory=list)


class MockSandboxBackend:
    name = "mock"

    def __init__(self) -> None:
        self._sandboxes: dict[str, _MockState] = {}
        self._lock = asyncio.Lock()
        # Optional canned response for exec_in — tests inject results
        # keyed by the first argv token.
        self._exec_responses: dict[str, ExecResult] = {}

    def set_exec_response(self, head: str, result: ExecResult) -> None:
        self._exec_responses[head] = result

    async def create(self, spec: SandboxCreateSpec) -> SandboxRef:
        validate_mounts(spec.mounts)
        sandbox_id = new_ulid()
        async with self._lock:
            self._sandboxes[sandbox_id] = _MockState(
                spec=spec, status="creating", created_at=time.time()
            )
        return SandboxRef(id=sandbox_id, container_id=f"mock-{sandbox_id}", backend=self.name)

    async def start(self, ref: SandboxRef) -> None:
        async with self._lock:
            state = self._require(ref)
            if state.status == "destroyed":
                raise SandboxBackendError(f"cannot start destroyed sandbox {ref.id}")
            state.status = "running"

    async def stop(self, ref: SandboxRef) -> None:
        async with self._lock:
            state = self._require(ref)
            if state.status == "destroyed":
                raise SandboxBackendError(f"cannot stop destroyed sandbox {ref.id}")
            state.status = "stopped"

    async def inspect(self, ref: SandboxRef) -> SandboxInfo:
        async with self._lock:
            state = self._require(ref)
            return SandboxInfo(
                ref=ref, status=state.status, image=state.spec.image, created_at=state.created_at
            )

    async def destroy(self, ref: SandboxRef) -> None:
        async with self._lock:
            state = self._require(ref)
            state.status = "destroyed"
            self._sandboxes.pop(ref.id, None)

    async def exec_in(self, ref: SandboxRef, argv: list[str]) -> ExecResult:
        async with self._lock:
            state = self._require(ref)
            if state.status != "running":
                raise SandboxBackendError(f"sandbox {ref.id} is {state.status!r}; cannot exec")
            state.exec_log.append(argv)
        head = argv[0] if argv else ""
        canned = self._exec_responses.get(head)
        if canned is not None:
            return canned
        return ExecResult(exit_code=0, stdout=b"", stderr=b"", duration_ms=0)

    def exec_log(self, ref: SandboxRef) -> list[list[str]]:
        state = self._sandboxes.get(ref.id)
        return list(state.exec_log) if state is not None else []

    def _require(self, ref: SandboxRef) -> _MockState:
        state = self._sandboxes.get(ref.id)
        if state is None:
            raise SandboxBackendError(f"unknown sandbox: {ref.id}")
        return state
