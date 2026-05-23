"""Unit tests for the in-process metrics + Prometheus renderer."""

from __future__ import annotations

import pytest

from gapt_server.observability.metrics import MetricsRegistry, reset_registry
from gapt_server.observability.render import render_prometheus


def test_counter_rejects_negative() -> None:
    reg = MetricsRegistry()
    c = reg.counter("g_test_counter", "test", ("k",))
    c.inc(1.5, {"k": "a"})
    with pytest.raises(ValueError, match="cannot decrease"):
        c.inc(-1, {"k": "a"})


def test_counter_accumulates_per_label_tuple() -> None:
    reg = MetricsRegistry()
    c = reg.counter("g_test_counter", "test", ("project_id",))
    c.inc(2, {"project_id": "p1"})
    c.inc(3, {"project_id": "p1"})
    c.inc(1, {"project_id": "p2"})
    body = render_prometheus(reg)
    assert 'g_test_counter{project_id="p1"} 5' in body
    assert 'g_test_counter{project_id="p2"} 1' in body


def test_gauge_set_overwrites() -> None:
    reg = MetricsRegistry()
    g = reg.gauge("g_test_gauge", "test")
    g.set(10)
    g.set(7)
    body = render_prometheus(reg)
    assert "g_test_gauge 7" in body
    assert "# TYPE g_test_gauge gauge" in body


@pytest.mark.asyncio
async def test_gauge_collector_refreshes_on_render() -> None:
    reg = MetricsRegistry()
    g = reg.gauge("g_live_gauge", "live", ("kind",))
    state = {"value": 1.0}

    async def collect() -> dict[tuple[str, ...], float]:
        return {("foo",): state["value"]}

    g.set_collector(collect)
    await reg.refresh_collectors()
    body = render_prometheus(reg)
    assert 'g_live_gauge{kind="foo"} 1' in body

    state["value"] = 4.0
    await reg.refresh_collectors()
    body = render_prometheus(reg)
    assert 'g_live_gauge{kind="foo"} 4' in body


def test_render_escapes_label_quotes() -> None:
    reg = MetricsRegistry()
    c = reg.counter("g_quoted", "escape\"test", ("label",))
    c.inc(1, {"label": 'a"b'})
    body = render_prometheus(reg)
    assert 'g_quoted{label="a\\"b"} 1' in body


def test_render_with_empty_registry_is_empty_string() -> None:
    reg = MetricsRegistry()
    assert render_prometheus(reg) == ""


def test_reset_registry_is_idempotent() -> None:
    reset_registry()
    reset_registry()  # second call shouldn't blow up
