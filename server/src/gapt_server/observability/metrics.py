"""Minimal in-process Prometheus-style metrics.

Two metric kinds are enough for M1:

- `Counter` — monotonic; `inc(value, labels=...)`. Cannot decrease.
- `Gauge` — `set(value, labels=...)`. Can be replaced by an async
  collector (`set_collector(callable)`) that polls on /metrics request.

Both keep one value per label tuple. Label values are stringified at
registration so we never store unbounded payloads.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class _Sample:
    """One name+labels+value triple ready for the exposition writer."""

    labels: tuple[tuple[str, str], ...]
    value: float


MetricKind = Literal["counter", "gauge"]


class _Series:
    """Shared structure for Counter + Gauge — name, help, label keys,
    and the (labels → value) map under a lock."""

    def __init__(self, name: str, help_: str, label_names: tuple[str, ...]) -> None:
        self.name = name
        self.help = help_
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def _key(self, labels: Mapping[str, str] | None) -> tuple[str, ...]:
        if not self.label_names:
            if labels:
                raise ValueError(f"metric {self.name!r} has no labels; got {dict(labels)}")
            return ()
        if labels is None:
            raise ValueError(f"metric {self.name!r} requires labels {self.label_names}")
        try:
            return tuple(str(labels[k]) for k in self.label_names)
        except KeyError as exc:
            missing = exc.args[0]
            raise ValueError(
                f"metric {self.name!r} missing label {missing!r} (need {self.label_names})"
            ) from None

    def samples(self) -> list[_Sample]:
        with self._lock:
            return [
                _Sample(labels=tuple(zip(self.label_names, key, strict=True)), value=val)
                for key, val in self._values.items()
            ]


class Counter(_Series):
    """Monotonic — `inc(amount, labels)`. Negative amounts raise."""

    kind: MetricKind = "counter"

    def inc(self, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        if amount < 0:
            raise ValueError(f"counter {self.name!r} cannot decrease (amount={amount})")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount


CollectorCallable = Callable[[], Awaitable[Mapping[tuple[str, ...], float]]]


class Gauge(_Series):
    """Settable value. Optionally driven by an async collector that
    runs at /metrics render time — the collector returns a fresh
    `{label_tuple: value}` snapshot and the gauge swaps its map."""

    kind: MetricKind = "gauge"

    def __init__(self, name: str, help_: str, label_names: tuple[str, ...] = ()) -> None:
        super().__init__(name, help_, label_names)
        self._collector: CollectorCallable | None = None

    def set(self, value: float, labels: Mapping[str, str] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def set_collector(self, collector: CollectorCallable) -> None:
        """Replace the values lazily on each render. Useful for "live"
        gauges like `gapt_sessions_active` that query the DB on demand
        rather than being pushed."""
        self._collector = collector

    async def refresh(self) -> None:
        if self._collector is None:
            return
        snapshot = await self._collector()
        with self._lock:
            self._values = {tuple(k): float(v) for k, v in snapshot.items()}


@dataclass
class MetricsRegistry:
    counters: dict[str, Counter] = field(default_factory=dict)
    gauges: dict[str, Gauge] = field(default_factory=dict)

    def counter(self, name: str, help_: str, label_names: tuple[str, ...] = ()) -> Counter:
        if name in self.counters:
            return self.counters[name]
        c = Counter(name, help_, label_names)
        self.counters[name] = c
        return c

    def gauge(self, name: str, help_: str, label_names: tuple[str, ...] = ()) -> Gauge:
        if name in self.gauges:
            return self.gauges[name]
        g = Gauge(name, help_, label_names)
        self.gauges[name] = g
        return g

    async def refresh_collectors(self) -> None:
        for g in self.gauges.values():
            await g.refresh()


_REGISTRY: MetricsRegistry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    return _REGISTRY


def reset_registry() -> None:
    """Test-only — forget every counter / gauge."""
    global _REGISTRY  # noqa: PLW0603 — module-level singleton, deliberate.
    _REGISTRY = MetricsRegistry()
