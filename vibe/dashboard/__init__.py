"""React Trace Dashboard — FastAPI backend + static frontend.

Served via `vibe dashboard` CLI command.
"""

from vibe.dashboard.server import DashboardState, app, run_server

__all__ = ["DashboardState", "app", "run_server"]
