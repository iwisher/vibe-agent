"""React Trace Dashboard — FastAPI backend + static frontend.

Served via `vibe dashboard` CLI command.
"""

from vibe.dashboard.api import create_app
from vibe.dashboard.data import DashboardDataSource

__all__ = ["DashboardDataSource", "create_app"]
