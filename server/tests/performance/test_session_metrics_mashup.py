"""Phase E.2 — `_sample_dto_with_metrics` correctly joins agent
session metrics onto container samples.

Pure unit tests against the helper — no DB / docker / SSE required.
The full SSE round-trip (gpus + session_metrics on the wire) is
exercised by `tests/performance/test_perf_routes.py` which is
postgres-gated like the other route tests.
"""

from __future__ import annotations

from gapt_server.domains.performance import (
    ContainerCategory,
    ContainerLimits,
    ContainerSample,
    ContainerSummary,
)
from gapt_server.routers.performance import (
    SessionMetricsAccumulator,
    _sample_dto_with_metrics,
)


def _make_sample(
    container_id: str,
    workspace_id: str | None,
    category: ContainerCategory = ContainerCategory.WORKSPACE,
) -> ContainerSample:
    return ContainerSample(
        summary=ContainerSummary(
            id=container_id,
            name=f"name-{container_id}",
            image="img",
            category=category,
            workspace_id=workspace_id,
            project_id=None,
        ),
        limits=ContainerLimits(
            cpu_quota_us=None,
            cpu_period_us=None,
            nano_cpus=None,
            cpus_effective=None,
            mem_bytes=None,
            memswap_bytes=None,
            pids_limit=None,
            runtime="runc",
            network_mode="bridge",
        ),
        stats=None,
    )


def test_metrics_attached_when_workspace_has_sessions() -> None:
    wid = "01KSWS00000000000000000001"
    accs = {
        wid: SessionMetricsAccumulator(
            cost_usd_total=0.42,
            input_tokens_total=1500,
            output_tokens_total=800,
            session_count=2,
        )
    }
    sample = _make_sample("c1", workspace_id=wid)
    dto = _sample_dto_with_metrics(sample, accs)
    assert dto.session_metrics is not None
    assert dto.session_metrics.cost_usd_total == 0.42
    assert dto.session_metrics.input_tokens_total == 1500
    assert dto.session_metrics.output_tokens_total == 800
    assert dto.session_metrics.session_count == 2


def test_metrics_omitted_when_workspace_unknown_to_accumulator() -> None:
    """A workspace container with no agent sessions yet → no
    session_metrics card (UI distinguishes "no sessions" from "infra
    container")."""
    sample = _make_sample("c2", workspace_id="01KSWS99999999999999999999")
    dto = _sample_dto_with_metrics(sample, {})
    assert dto.session_metrics is None


def test_metrics_omitted_for_infra_containers() -> None:
    """Containers without `workspace_id` (caddy, postgres, …) must
    never carry session_metrics — they don't host agent sessions."""
    sample = _make_sample(
        "c3", workspace_id=None, category=ContainerCategory.INFRA
    )
    accs = {
        "01KSWS00000000000000000001": SessionMetricsAccumulator(
            cost_usd_total=0.1,
            input_tokens_total=10,
            output_tokens_total=10,
            session_count=1,
        )
    }
    dto = _sample_dto_with_metrics(sample, accs)
    assert dto.session_metrics is None


def test_metrics_omitted_when_accumulator_dict_is_empty() -> None:
    sample = _make_sample("c4", workspace_id="01KSWS00000000000000000002")
    dto = _sample_dto_with_metrics(sample, {})
    assert dto.session_metrics is None


def test_session_metrics_dto_field_shape_is_serialisable() -> None:
    """Round-trip the DTO through `model_dump` to confirm pydantic
    accepts the shape (catches accidental field-rename regressions
    when SessionMetricsAccumulator / SessionMetricsDto drift)."""
    accs = {
        "01KSWS00000000000000000003": SessionMetricsAccumulator(
            cost_usd_total=1.5,
            input_tokens_total=2000,
            output_tokens_total=1500,
            session_count=3,
        )
    }
    sample = _make_sample("c5", workspace_id="01KSWS00000000000000000003")
    dto = _sample_dto_with_metrics(sample, accs)
    dumped = dto.model_dump()
    assert dumped["session_metrics"] == {
        "cost_usd_total": 1.5,
        "input_tokens_total": 2000,
        "output_tokens_total": 1500,
        "session_count": 3,
    }
