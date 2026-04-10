from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any


AUTH_DIR = Path(__file__).resolve().parent
AUTH_DB_PATH = Path(os.getenv("AUTH_DB_PATH", AUTH_DIR / "auth.db")).expanduser()
SCHEMA_FILE = AUTH_DIR / "schema.sql"

_init_lock = threading.Lock()
_initialized = False


class AuthStoreError(RuntimeError):
    pass


def initialize_auth_store() -> None:
    global _initialized

    if _initialized and AUTH_DB_PATH.exists():
        return

    with _init_lock:
        if _initialized and AUTH_DB_PATH.exists():
            return

        AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(AUTH_DB_PATH)
        try:
            connection.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            connection.execute("PRAGMA journal_mode=WAL")
            connection.commit()
        finally:
            connection.close()

        _initialized = True


def get_connection() -> sqlite3.Connection:
    initialize_auth_store()
    connection = sqlite3.connect(AUTH_DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def auth_query(query: str, params: tuple | list | None = None, *, fetch: str = "all") -> Any:
    connection = get_connection()
    try:
        cursor = connection.execute(query, params or ())
        if fetch == "none":
            connection.commit()
            return None
        if fetch == "one":
            row = cursor.fetchone()
            connection.commit()
            return row
        rows = cursor.fetchall()
        connection.commit()
        return rows
    except sqlite3.Error as exc:
        raise AuthStoreError(str(exc)) from exc
    finally:
        connection.close()
