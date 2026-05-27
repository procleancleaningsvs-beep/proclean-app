"""CLI para job programado (Railway cron): refrescar snapshot Headcount."""
from __future__ import annotations

from modules.headcount.refresh_snapshot_job import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
