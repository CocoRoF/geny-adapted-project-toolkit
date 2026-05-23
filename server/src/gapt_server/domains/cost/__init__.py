"""Cost domain — D-cost.

Aggregations over `agent_sessions` (cost_usd / input_tokens /
output_tokens) for the cost dashboard. Snapshots only; the actual
accumulator lives in `agent/hooks/cost_hook.py` and writes to the
session row via the session manager. This service *reads* those rows
back and groups them by project or by day.
"""

from gapt_server.domains.cost.service import (
    DailyCostRow,
    ProjectCostRow,
    aggregate_daily_for_project,
    aggregate_summary,
)

__all__ = [
    "DailyCostRow",
    "ProjectCostRow",
    "aggregate_daily_for_project",
    "aggregate_summary",
]
