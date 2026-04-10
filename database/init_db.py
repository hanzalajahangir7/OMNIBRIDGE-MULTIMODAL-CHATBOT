#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from database.db import get_database_status, run_schema


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    load_dotenv(ENV_FILE)
    status = get_database_status()

    if not status.enabled:
        print(status.reason or "Database integration is disabled.")
        return 1

    if not status.driver_available:
        print(status.reason or "psycopg2 is not installed.")
        return 1

    try:
        run_schema()
    except Exception as exc:  # pragma: no cover - depends on local postgres setup
        print(f"Failed to initialize schema: {exc}")
        return 1

    print(f"Database schema initialized from {status.schema_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
