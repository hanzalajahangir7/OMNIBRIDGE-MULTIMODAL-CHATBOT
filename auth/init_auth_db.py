#!/usr/bin/env python3
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from auth.db import AUTH_DB_PATH, initialize_auth_store


def main() -> int:
    initialize_auth_store()
    print(f"Auth database initialized at {AUTH_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
