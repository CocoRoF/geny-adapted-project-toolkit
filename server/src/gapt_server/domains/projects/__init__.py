"""Projects domain — D1.

A `Project` ties an external git remote to GAPT — it owns
Environments, Workspaces, and AgentSessions. Cycle 1.6 ships CRUD
behind the auth gate; clone/checkout integrate in M1-E2.
"""

from gapt_server.domains.projects.service import (
    ProjectError,
    ProjectService,
    ProjectView,
)

__all__ = ["ProjectError", "ProjectService", "ProjectView"]
