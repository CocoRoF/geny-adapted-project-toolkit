"""In-process metrics + Prometheus exposition.

Rather than pulling in `prometheus_client` (which would bring a global
default registry and process-wide collectors we don't need), we keep a
tiny in-process registry and render the standard text exposition
format ourselves. The format spec is short enough that re-implementing
it sidesteps a dependency with its own metaclass machinery.

The OTel SDK is already a server dependency — operators who want OTLP
push can enable `opentelemetry-instrumentation-fastapi` via env. This
module just covers the *pull* side (/metrics) which is what self-host
operators run first.
"""

from gapt_server.observability.metrics import (
    Counter,
    Gauge,
    MetricsRegistry,
    get_registry,
    reset_registry,
)
from gapt_server.observability.render import render_prometheus

__all__ = [
    "Counter",
    "Gauge",
    "MetricsRegistry",
    "get_registry",
    "render_prometheus",
    "reset_registry",
]
