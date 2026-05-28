"""StackManager.logs() — `docker compose logs` wrapper.

Patches `_compose_cli` so we don't actually spawn docker. Just
verifies the args + return shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from gapt_server.domains.deploy.stack_manager import StackManager, StackOpResult


class _FakeStackManager(StackManager):
    """Captures the args passed to `_compose_cli` and returns a
    canned (rc, output) tuple."""

    def __init__(self, rc: int = 0, output: str = "log line 1\nlog line 2\n") -> None:
        super().__init__(client=None)  # type: ignore[arg-type]
        self._rc = rc
        self._output = output
        self.calls: list[list[str]] = []

    async def _compose_cli(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(args)
        return self._rc, self._output


@pytest.mark.asyncio
async def test_logs_passes_project_and_tail() -> None:
    sm = _FakeStackManager()
    result = await sm.logs("01KSKW4TW72E41J84HC0ZEXCTR", tail=50)
    assert isinstance(result, StackOpResult)
    assert result.ok is True
    assert result.action == "logs"
    assert result.project == "gapt-prod-01kskw4tw72e41j84hc0zexctr"
    # Verify argv shape — `-p <proj> logs --no-color --tail 50`
    args = sm.calls[0]
    assert args[:3] == ["-p", "gapt-prod-01kskw4tw72e41j84hc0zexctr", "logs"]
    assert "--no-color" in args
    assert "--tail" in args
    assert "50" in args


@pytest.mark.asyncio
async def test_logs_since_filter_threaded_through() -> None:
    sm = _FakeStackManager()
    await sm.logs("p1", tail=10, since="30s")
    args = sm.calls[0]
    assert "--since" in args
    assert "30s" in args


@pytest.mark.asyncio
async def test_logs_propagates_nonzero_rc() -> None:
    sm = _FakeStackManager(rc=1, output="error: project not found")
    result = await sm.logs("missing-project")
    assert result.ok is False
    assert "project not found" in result.output


@pytest.mark.asyncio
async def test_logs_default_tail_is_200() -> None:
    sm = _FakeStackManager()
    await sm.logs("p1")
    args = sm.calls[0]
    tail_idx = args.index("--tail")
    assert args[tail_idx + 1] == "200"
