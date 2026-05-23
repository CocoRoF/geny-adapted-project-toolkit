"""Audit domain — D8.

`AuditSink` is the only thing services should call when they need to
record a state-changing action. `PostgresAuditSink` is the M1
implementation: it accepts events on an in-process asyncio queue and
flushes them in batches to `audit_events`. Plaintext patterns are
masked before insert by `masking.scrub`.

Redis Streams shipping is a follow-up (plan §1.5) — the in-process
queue is a drop-in seam for that.
"""

from gapt_server.domains.audit.masking import scrub
from gapt_server.domains.audit.sink import (
    AuditAction,
    AuditEvent,
    AuditSink,
    InMemoryAuditSink,
    NullAuditSink,
    PostgresAuditSink,
)

__all__ = [
    "AuditAction",
    "AuditEvent",
    "AuditSink",
    "InMemoryAuditSink",
    "NullAuditSink",
    "PostgresAuditSink",
    "scrub",
]
