"""Prometheus text exposition format renderer.

Follows the spec at
https://github.com/prometheus/docs/blob/main/content/docs/instrumenting/exposition_formats.md
— roughly: blank line between metric blocks, `# HELP` and `# TYPE`
preamble per metric, label values quoted with backslash-escapes.

Only counters and gauges are emitted in M1. Histograms / summaries
land if/when we instrument latency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gapt_server.observability.metrics import MetricsRegistry, _Sample


def _escape_help(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _escape_label_value(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_value(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(value)


def _format_sample(name: str, sample: _Sample) -> str:
    if not sample.labels:
        return f"{name} {_format_value(sample.value)}"
    parts = ",".join(f'{k}="{_escape_label_value(v)}"' for k, v in sample.labels)
    return f"{name}{{{parts}}} {_format_value(sample.value)}"


def render_prometheus(registry: MetricsRegistry) -> str:
    """Return the full /metrics body for the given registry.

    Counters first, then gauges; within each group the metrics are
    sorted by name so the output is stable across requests."""
    blocks: list[str] = []
    for name in sorted(registry.counters):
        c = registry.counters[name]
        body = [f"# HELP {c.name} {_escape_help(c.help)}", f"# TYPE {c.name} counter"]
        for sample in c.samples():
            body.append(_format_sample(c.name, sample))
        blocks.append("\n".join(body))
    for name in sorted(registry.gauges):
        g = registry.gauges[name]
        body = [f"# HELP {g.name} {_escape_help(g.help)}", f"# TYPE {g.name} gauge"]
        for sample in g.samples():
            body.append(_format_sample(g.name, sample))
        blocks.append("\n".join(body))
    return "\n\n".join(blocks) + ("\n" if blocks else "")
