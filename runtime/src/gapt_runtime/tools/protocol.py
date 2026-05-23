"""Tool ABC + result types.

A ``Tool`` is a single callable the MCP bridge exposes to the LLM.
It declares a JSON Schema for its arguments and returns a
``ToolResult`` (or raises ``ToolError`` with a stable code).

``ToolInvocation`` carries the workspace root so tools don't need to
know about settings. Daemon handlers fill it in before dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSchema:
    """JSON Schema fragment the MCP bridge ships to the LLM."""

    type: str = "object"
    properties: dict[str, dict[str, Any]] | None = None
    required: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.properties is not None:
            out["properties"] = self.properties
        if self.required:
            out["required"] = list(self.required)
        return out


@dataclass
class ToolInvocation:
    """One ``tools/call`` request after auth / DTO validation."""

    name: str
    arguments: dict[str, Any]
    workspace_root: str  # absolute path; daemon supplies from settings


@dataclass
class ToolResult:
    """Successful tool execution.

    ``content`` is the human/LLM-readable text; ``metadata`` carries
    structured side info (line counts, match offsets, etc.) that the
    bridge can surface separately.
    """

    content: str
    metadata: dict[str, Any] | None = None


class ToolError(RuntimeError):
    """Stable failure with an ``exec.tool.*`` code suffix."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Tool(Protocol):
    """Protocol every concrete tool implementation satisfies."""

    name: str
    description: str
    schema: ToolSchema

    async def execute(self, invocation: ToolInvocation) -> ToolResult: ...
