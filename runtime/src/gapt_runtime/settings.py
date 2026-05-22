from dataclasses import dataclass
from os import environ
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DaemonSettings:
    socket_path: Path
    jwt_secret: str
    project_id: str | None
    workspace_id: str | None
    session_id: str | None
    workspace_root: Path = Path("/workspace")

    @classmethod
    def from_env(cls) -> "DaemonSettings":
        return cls(
            socket_path=Path(environ.get("GAPT_AGENT_SOCKET", "/run/agent.sock")),
            jwt_secret=environ.get("GAPT_DAEMON_TOKEN", ""),
            project_id=environ.get("GAPT_PROJECT_ID"),
            workspace_id=environ.get("GAPT_WORKSPACE_ID"),
            session_id=environ.get("GAPT_SESSION_ID"),
            workspace_root=Path(environ.get("GAPT_WORKSPACE_ROOT", "/workspace")),
        )
