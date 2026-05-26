"""In-memory `SandboxBackend` stand-in for tests.

The production `SysboxBackend` requires a real docker daemon. Tests
that just need the protocol surface (create / start / stop / exec_in
/ destroy / inspect) and per-sandbox state tracking can use this
narrow fake instead.

Surface — matches what the retired `MockSandboxBackend` exposed and
nothing more:

* `create(spec)` returns a `SandboxRef` and records initial state.
* `start` / `stop` / `destroy` flip the per-sandbox status.
* `inspect` returns a `SandboxInfo` snapshot.
* `exec_in(ref, argv)` appends `argv` to the per-sandbox exec log and
  returns either a queued canned response or a default zero-exit
  `ExecResult`.
* `set_exec_response(argv0, result)` stages a canned response keyed by
  the first argv token, mirroring the old mock.
* `exec_log(ref)` returns the recorded argv list for a sandbox.
* `exec_log` (no-arg attribute access) returns the flat list of all
  recorded argv lists — kept so legacy truthiness checks like
  ``assert backend.exec_log`` still work.
"""

from __future__ import annotations

import time
from typing import Any

from gapt_server.db.ulid import new_ulid
from gapt_server.domains.sandbox.backend import (
    ExecResult,
    SandboxCreateSpec,
    SandboxInfo,
    SandboxRef,
)


class _ExecLogView(list[list[str]]):
    """List subclass that's also callable.

    Old code used both forms:

        backend.exec_log              # attribute → truthy iff anything ran
        backend.exec_log(ref)         # method → argv list for one sandbox

    Keeping a callable list lets both keep working without a separate
    accessor.
    """

    def __init__(self, owner: "FakeSandboxBackend") -> None:
        super().__init__()
        self._owner = owner

    def __call__(self, ref: SandboxRef) -> list[list[str]]:
        return list(self._owner._per_sandbox_log.get(ref.id, ()))


class FakeSandboxBackend:
    """`SandboxBackend`-shaped in-memory stub.

    Not thread-safe; tests are single-event-loop and that's fine.
    """

    name = "fake"

    def __init__(self) -> None:
        # sandbox_id -> "creating" | "running" | "stopped" | "destroyed"
        self._states: dict[str, str] = {}
        # sandbox_id -> image (for inspect)
        self._images: dict[str, str] = {}
        # sandbox_id -> created_at epoch seconds
        self._created_at: dict[str, float] = {}
        # sandbox_id -> list of argv lists exec_in saw
        self._per_sandbox_log: dict[str, list[list[str]]] = {}
        # argv[0] -> canned ExecResult (matches retired MockSandboxBackend)
        self._canned: dict[str, ExecResult] = {}
        # Flat exec log, primarily for the legacy ``assert sandbox.exec_log``
        # truthiness check; also callable for the (ref) -> list[argv] form.
        self.exec_log = _ExecLogView(self)

    # ───────────────────────────────────────────── test seams ──

    def set_exec_response(self, argv0: str, result: ExecResult) -> None:
        """Stage a canned response for any `exec_in` whose first argv
        token equals ``argv0``. Overwrites prior stub for that key."""
        self._canned[argv0] = result

    # ─────────────────────────────────────── SandboxBackend API ──

    async def create(self, spec: SandboxCreateSpec) -> SandboxRef:
        sandbox_id = new_ulid()
        self._states[sandbox_id] = "creating"
        self._images[sandbox_id] = spec.image
        self._created_at[sandbox_id] = time.time()
        self._per_sandbox_log[sandbox_id] = []
        return SandboxRef(
            id=sandbox_id,
            container_id=f"fake-{sandbox_id}",
            backend=self.name,
        )

    async def start(self, ref: SandboxRef) -> None:
        self._states[ref.id] = "running"

    async def stop(self, ref: SandboxRef) -> None:
        self._states[ref.id] = "stopped"

    async def destroy(self, ref: SandboxRef) -> None:
        self._states[ref.id] = "destroyed"

    async def inspect(self, ref: SandboxRef) -> SandboxInfo:
        return SandboxInfo(
            ref=ref,
            status=self._states.get(ref.id, "unknown"),
            image=self._images.get(ref.id, ""),
            created_at=self._created_at.get(ref.id, 0.0),
        )

    async def exec_in(self, ref: SandboxRef, argv: list[str]) -> ExecResult:
        argv_copy = list(argv)
        self._per_sandbox_log.setdefault(ref.id, []).append(argv_copy)
        self.exec_log.append(argv_copy)
        if argv_copy:
            canned = self._canned.get(argv_copy[0])
            if canned is not None:
                return canned
        return ExecResult(exit_code=0, stdout=b"", stderr=b"", duration_ms=0)

    # ───────────────────────────────────────── housekeeping ──

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return (
            f"FakeSandboxBackend(states={self._states!r}, "
            f"canned_keys={sorted(self._canned)!r})"
        )

    # Some legacy tests poke attributes via `backend.<thing> = ...`; the
    # default dataclass-style __setattr__ is fine, but we expose a
    # no-arg helper for completeness so ``backend.exec_log`` always
    # behaves like the union list+callable above even if user code
    # rebinds it (it shouldn't).
    def _all_execs(self) -> list[list[str]]:  # pragma: no cover
        out: list[list[str]] = []
        for log in self._per_sandbox_log.values():
            out.extend(log)
        return out


# Re-export so test files only need one import.
__all__ = ["FakeSandboxBackend", "ExecResult", "SandboxCreateSpec", "SandboxRef"]

# Re-export the dataclass names the old mock surface used.
# Tests do `from tests._helpers.fake_sandbox import FakeSandboxBackend` —
# they keep importing the dataclasses (`ExecResult`, `SandboxCreateSpec`)
# directly from `gapt_server.domains.sandbox`, so the re-exports are a
# safety net, not the documented path.
_ = Any  # silence unused import in static-checking environments
