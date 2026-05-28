"""In-process metrics registry.

A tiny `Counter` / `Gauge` registry that lives inside the server's
process memory. Consumed *directly* by the performance tab (the
backend reads counter samples and merges them into the SSE payload
the dashboard renders).

No external scrape surface — the Prometheus exposition endpoint
and the Prometheus / Grafana containers were removed; the registry
is purely an internal data structure now. If you ever need to
re-introduce external scraping, the `samples()` method on each
metric returns label-tuple → value pairs ready to format.
"""

from gapt_server.observability.metrics import (
    Counter,
    Gauge,
    MetricsRegistry,
    get_registry,
    reset_registry,
)

__all__ = [
    "Counter",
    "Gauge",
    "MetricsRegistry",
    "get_registry",
    "reset_registry",
]
