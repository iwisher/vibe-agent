"""React Trace Dashboard — FastAPI backend + static frontend.

Served via `vibe dashboard` CLI command.
"""

from vibe.dashboard.data import DashboardDataSource
from vibe.dashboard.api import create_app

__all__ = ["DashboardDataSource", "create_app"]
