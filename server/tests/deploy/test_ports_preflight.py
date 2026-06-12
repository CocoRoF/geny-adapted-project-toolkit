"""Host-port preflight — pure planner units + LocalComposeTarget
integration through the stub runner (no real docker)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gapt_server.domains.deploy import (
    DeployContext,
    DeployRequest,
    DeployStatusKind,
    LocalComposeTarget,
)
from gapt_server.domains.deploy.ports import (
    docker_project_ports,
    parse_compose_ports,
    parse_docker_published_ports,
    parse_proc_net_listen_ports,
    plan_port_overrides,
)
from gapt_server.domains.deploy.protocol import DeployTargetError

# ─────────────────────────────────────────────── parsers ──


def test_parse_compose_ports_long_and_short_forms() -> None:
    config = {
        "services": {
            "frontend": {
                "ports": [
                    {"mode": "ingress", "target": 3000, "published": "3000", "protocol": "tcp"}
                ]
            },
            "api": {"ports": ["127.0.0.1:8080:80/tcp", "9000"]},
            "worker": {"image": "x"},  # no ports — excluded
        }
    }
    out = parse_compose_ports(config)
    assert set(out) == {"frontend", "api"}
    assert out["frontend"][0]["published"] == "3000"


def test_parse_docker_published_ports_excludes_own_project() -> None:
    ps = "\n".join(
        [
            '{"Names":"other-app-1","Labels":"com.docker.compose.project=other","Ports":"0.0.0.0:3000->3000/tcp, :::3000->3000/tcp"}',
            '{"Names":"mine-web-1","Labels":"com.docker.compose.project=gapt-prod-x","Ports":"0.0.0.0:4000->4000/tcp"}',
            '{"Names":"plain","Labels":"","Ports":"127.0.0.1:5432->5432/tcp"}',
        ]
    )
    occupied = parse_docker_published_ports(ps, exclude_project="gapt-prod-x")
    assert occupied == {3000: "other-app-1", 5432: "plain"}
    assert docker_project_ports(ps, project="gapt-prod-x") == {4000}


def test_parse_proc_net_listen_ports_filters_state() -> None:
    tcp = (
        "  sl  local_address rem_address   st\n"
        "   0: 00000000:0BB8 00000000:0000 0A\n"  # 3000 LISTEN
        "   1: 0100007F:1F90 00000000:0000 01\n"  # 8080 ESTABLISHED — skip
    )
    assert parse_proc_net_listen_ports(tcp) == {3000}


# ─────────────────────────────────────────────── planner ──


def test_plan_auto_remaps_conflict_and_keeps_other_entries() -> None:
    services = {
        "frontend": [
            {"target": 3000, "published": "3000", "protocol": "tcp"},
            {"target": 9229, "published": "9229", "protocol": "tcp"},
        ]
    }
    plan = plan_port_overrides(
        services_ports=services,
        occupied={3000: "gapt-prod-old-frontend-1"},
        policy="auto",
    )
    assert plan.remaps == {("frontend", 3000): 3001}
    assert plan.override_yaml is not None
    # !override replaces wholesale → untouched 9229 must be re-stated.
    assert "!override" in plan.override_yaml
    assert '"published": "3001"' in plan.override_yaml
    assert '"published": "9229"' in plan.override_yaml
    assert any("gapt-prod-old-frontend-1" in line for line in plan.log_lines)


def test_plan_auto_skips_free_ports_entirely() -> None:
    services = {"frontend": [{"target": 3000, "published": "3000"}]}
    plan = plan_port_overrides(
        services_ports=services, occupied={4000: "other"}, policy="auto"
    )
    assert plan.override_yaml is None
    assert plan.remaps == {}


def test_plan_auto_resolves_intra_stack_duplicates() -> None:
    # Two services both ask for 3000 — second one must move even
    # though the host itself is free.
    services = {
        "a": [{"target": 3000, "published": "3000"}],
        "b": [{"target": 3000, "published": "3000"}],
    }
    plan = plan_port_overrides(services_ports=services, occupied={}, policy="auto")
    assert plan.remaps == {("b", 3000): 3001}


def test_plan_strict_raises_with_holder() -> None:
    services = {"frontend": [{"target": 3000, "published": "3000"}]}
    with pytest.raises(DeployTargetError) as exc_info:
        plan_port_overrides(
            services_ports=services,
            occupied={3000: "someone-else"},
            policy="strict",
        )
    assert exc_info.value.code == "deploy.port_conflict"
    assert "someone-else" in str(exc_info.value)


def test_plan_unpublish_strips_all_host_ports() -> None:
    services = {"frontend": [{"target": 3000, "published": "3000", "protocol": "tcp"}]}
    plan = plan_port_overrides(services_ports=services, occupied={}, policy="unpublish")
    assert plan.override_yaml is not None
    assert "published" not in plan.override_yaml
    assert '"target": 3000' in plan.override_yaml


def test_plan_rejects_unknown_policy() -> None:
    with pytest.raises(DeployTargetError) as exc_info:
        plan_port_overrides(services_ports={}, occupied={}, policy="yolo")
    assert exc_info.value.code == "deploy.invalid_ports_policy"


# ──────────────────────────────── LocalComposeTarget wiring ──


@pytest.mark.asyncio
async def test_deploy_auto_remaps_conflicting_host_port(tmp_path: Path) -> None:
    """End-to-end through the stub runner: compose model publishes
    3000, another container holds 3000 → the `up` argv gains the
    generated override file remapping to 3001 and the run log
    explains the move."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {frontend: {image: x}}\n")
    calls: list[list[str]] = []

    async def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        calls.append(argv)
        if "compose" in argv and "ps" in argv:
            return (0, "", "")
        if argv[:2] == ["/usr/bin/docker", "ps"]:
            return (
                0,
                '{"Names":"gapt-prod-old-frontend-1","Labels":"com.docker.compose.project=gapt-prod-old","Ports":"0.0.0.0:3000->3000/tcp"}',
                "",
            )
        if "config" in argv:
            return (
                0,
                '{"services": {"frontend": {"ports": [{"target": 3000, "published": "3000", "protocol": "tcp"}]}}}',
                "",
            )
        if "pull" in argv or "up" in argv:
            return (0, "ok\n", "")
        return (1, "", f"unexpected: {argv}")

    target = LocalComposeTarget(docker_binary="/usr/bin/docker", runner=runner)
    request = DeployRequest(
        project_id="01KSXXXXXXXXXXXXXXXXXXXXXX",
        environment="prod",
        version="latest",
        compose_path=str(compose),
    )
    result = await target.deploy(DeployContext(run_id="run-1", request=request))

    assert result.status is DeployStatusKind.SUCCESS
    override = tmp_path / ".gapt" / "ports.override.yml"
    assert override.exists()
    assert '"published": "3001"' in override.read_text()
    up_argv = next(argv for argv in calls if "up" in argv)
    assert str(override) in up_argv
    assert "host port 3000 is already in use" in result.log
    assert "gapt-prod-old-frontend-1" in result.log


@pytest.mark.asyncio
async def test_deploy_strict_policy_fails_before_up(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {frontend: {image: x}}\n")
    calls: list[list[str]] = []

    async def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        calls.append(argv)
        if "compose" in argv and "ps" in argv:
            return (0, "", "")
        if argv[:2] == ["/usr/bin/docker", "ps"]:
            return (
                0,
                '{"Names":"holder-1","Labels":"","Ports":"0.0.0.0:3000->3000/tcp"}',
                "",
            )
        if "config" in argv:
            return (
                0,
                '{"services": {"frontend": {"ports": [{"target": 3000, "published": "3000"}]}}}',
                "",
            )
        return (0, "ok\n", "")

    target = LocalComposeTarget(docker_binary="/usr/bin/docker", runner=runner)
    request = DeployRequest(
        project_id="01KSXXXXXXXXXXXXXXXXXXXXXX",
        environment="prod",
        version="latest",
        compose_path=str(compose),
        target_options={"ports_policy": "strict"},
    )
    result = await target.deploy(DeployContext(run_id="run-1", request=request))

    assert result.status is DeployStatusKind.FAILED
    assert result.exec_code == "deploy.port_conflict"
    assert "holder-1" in (result.log or "")
    assert not any("up" in argv for argv in calls)


@pytest.mark.asyncio
async def test_deploy_proceeds_when_preflight_unavailable(tmp_path: Path) -> None:
    """Old compose without `config --format json` AND broken YAML
    fallback → preflight degrades to a log note, deploy continues."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {frontend: {image: x}}\n")

    async def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        if "compose" in argv and "ps" in argv:
            return (0, "", "")
        if "config" in argv:
            return (1, "", "unknown flag --format")
        if "pull" in argv or "up" in argv:
            return (0, "ok\n", "")
        if argv[:2] == ["/usr/bin/docker", "ps"]:
            return (0, "", "")
        return (1, "", "unexpected")

    target = LocalComposeTarget(docker_binary="/usr/bin/docker", runner=runner)
    request = DeployRequest(
        project_id="01KSXXXXXXXXXXXXXXXXXXXXXX",
        environment="prod",
        version="latest",
        compose_path=str(compose),
    )
    result = await target.deploy(DeployContext(run_id="run-1", request=request))

    assert result.status is DeployStatusKind.SUCCESS
    assert "port preflight skipped" in (result.log or "")