from pathlib import Path

import pytest

from gapt_runtime.settings import DaemonSettings


def test_defaults_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "GAPT_AGENT_SOCKET",
        "GAPT_DAEMON_TOKEN",
        "GAPT_PROJECT_ID",
        "GAPT_WORKSPACE_ID",
        "GAPT_SESSION_ID",
        "GAPT_WORKSPACE_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)

    s = DaemonSettings.from_env()
    assert s.socket_path == Path("/run/agent.sock")
    assert s.jwt_secret == ""
    assert s.project_id is None
    assert s.workspace_id is None
    assert s.session_id is None
    assert s.workspace_root == Path("/workspace")


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAPT_AGENT_SOCKET", "/var/run/x.sock")
    monkeypatch.setenv("GAPT_DAEMON_TOKEN", "tok-123")
    monkeypatch.setenv("GAPT_PROJECT_ID", "p1")
    monkeypatch.setenv("GAPT_WORKSPACE_ID", "w1")
    monkeypatch.setenv("GAPT_SESSION_ID", "s1")
    monkeypatch.setenv("GAPT_WORKSPACE_ROOT", "/custom/root")

    s = DaemonSettings.from_env()
    assert s.socket_path == Path("/var/run/x.sock")
    assert s.jwt_secret == "tok-123"
    assert s.project_id == "p1"
    assert s.workspace_id == "w1"
    assert s.session_id == "s1"
    assert s.workspace_root == Path("/custom/root")
