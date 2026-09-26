"""Idempotent seed of the two demo destinations + fictional fixture firms into DATABASE_URL.

Usage (from the repo root, API venv active, after `alembic upgrade head`):
    python scripts/seed_demo.py
Safe to run repeatedly: firms are upserted by stable slug.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from caseline.config import get_settings  # noqa: E402
from caseline.db import Database  # noqa: E402
from caseline.seed import seed  # noqa: E402


def main() -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    with db.sessionmaker() as session:
        firms = seed(session, settings)
    for f in firms:
        label = "DEMO" if f.is_demo else "fixture"
        print(f"{label:8} {f.slug:24} {f.display_name}")


if __name__ == "__main__":
    main()
