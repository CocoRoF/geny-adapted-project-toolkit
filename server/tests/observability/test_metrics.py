"""Unit tests for the in-process metrics registry.

The Prometheus text-exposition renderer was removed when the
external Prometheus / Grafana stack came out — the registry is now
consumed directly by the performance tab. These tests verify the
registry's own contract (counter monotonicity, gauge replacement,
async collector refresh) by reading `samples()` directly rather
than asserting against a rendered text payload.
"""

from __future__ import annotations

import pytest

from gapt_server.observability.metrics import MetricsRegistry, reset_registry


def _sample_values(samples) -> dict[tuple[tuple[str, str], ...], float]:
    return {s.labels: s.value for s in samples}


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
    by_labels = _sample_values(c.samples())
    assert by_labels[(("project_id", "p1"),)] == 5
    assert by_labels[(("project_id", "p2"),)] == 1


def test_gauge_set_overwrites() -> None:
    reg = MetricsRegistry()
    g = reg.gauge("g_test_gauge", "test")
    g.set(10)
    g.set(7)
    samples = g.samples()
    assert len(samples) == 1
    assert samples[0].value == 7


@pytest.mark.asyncio
async def test_gauge_collector_refreshes_on_request() -> None:
    """`refresh_collectors()` lets a gauge defer value population to
    request time — the performance tab relies on this for "live"
    counters (sessions_active, sandbox_count)."""
    reg = MetricsRegistry()
    g = reg.gauge("g_live_gauge", "live", ("kind",))
    state = {"value": 1.0}

    async def collect() -> dict[tuple[str, ...], float]:
        return {("foo",): state["value"]}

    g.set_collector(collect)
    await reg.refresh_collectors()
    assert _sample_values(g.samples())[(("kind", "foo"),)] == 1

    state["value"] = 4.0
    await reg.refresh_collectors()
    assert _sample_values(g.samples())[(("kind", "foo"),)] == 4


def test_counter_label_value_stored_verbatim() -> None:
    """Label values pass through unescaped — the registry is now an
    internal data structure (no text exposition), so escape concerns
    don't apply. The performance-tab JSON serialiser handles its
    own encoding."""
    reg = MetricsRegistry()
    c = reg.counter("g_quoted", "test", ("label",))
    c.inc(1, {"label": 'a"b'})
    by_labels = _sample_values(c.samples())
    assert by_labels[(("label", 'a"b'),)] == 1


def test_empty_registry_yields_no_samples() -> None:
    reg = MetricsRegistry()
    assert reg.counters == {}
    assert reg.gauges == {}


def test_reset_registry_is_idempotent() -> None:
    reset_registry()
    reset_registry()  # second call shouldn't blow up
