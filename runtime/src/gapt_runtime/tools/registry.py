"""Lookup-by-name registry for daemon-side tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gapt_runtime.tools.edit import GaptEdit
from gapt_runtime.tools.git_tool import GaptGit
from gapt_runtime.tools.glob import GaptGlob
from gapt_runtime.tools.grep import GaptGrep
from gapt_runtime.tools.read import GaptRead

if TYPE_CHECKING:
    from gapt_runtime.tools.protocol import Tool


class ToolRegistry:
    """Name → ``Tool`` map. Hand-built (no plug-in discovery in M1)."""

    def __init__(self, tools: list[Tool]) -> None:
        self._by_name: dict[str, Tool] = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    def list_specs(self) -> list[dict[str, Any]]:
        """Manifest the MCP bridge ships to the CLI's LLM."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.schema.to_dict(),
            }
            for t in self._by_name.values()
        ]


def build_default_registry() -> ToolRegistry:
    return ToolRegistry([GaptRead(), GaptGlob(), GaptGrep(), GaptEdit(), GaptGit()])
