"""CLI/cron global para refrescar snapshot Headcount (toda la app)."""
from __future__ import annotations

import sys


def main() -> int:
    from app import DB_PATH, create_app
    from modules.headcount.snapshot_service import refresh_headcount_snapshot

    app = create_app()
    with app.app_context():
        result = refresh_headcount_snapshot(str(DB_PATH), source="cron")
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
