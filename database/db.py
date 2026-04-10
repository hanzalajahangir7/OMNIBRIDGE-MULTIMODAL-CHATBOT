from __future__ import annotations
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import psycopg2
    from psycopg2 import pool, ProgrammingError
    from psycopg2 import Error as PsycopgError
except ImportError:
    psycopg2 = None
    pool = None
    ProgrammingError = Exception
    PsycopgError = Exception

FALSEY_VALUES = {"0", "false", "no", "off"}
SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"

class DatabaseError(RuntimeError):
    pass

class DatabaseUnavailableError(DatabaseError):
    pass

class DatabaseQueryError(DatabaseError):
    pass

@dataclass
class DatabaseStatus:
    enabled: bool
    ready: bool
    driver_available: bool
    reason: str | None
    dbname: str
    host: str
    port: str
    schema_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "driverAvailable": self.driver_available,
            "reason": self.reason,
            "dbname": self.dbname,
            "host": self.host,
            "port": self.port,
            "schemaPath": self.schema_path,
        }

class DatabaseClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pool: pool.ThreadedConnectionPool | None = None

    def _enabled(self) -> bool:
        return os.getenv("DB_ENABLED", "true").strip().lower() not in FALSEY_VALUES

    def _params(self) -> dict[str, Any]:
        return {
            "dbname": os.getenv("DB_NAME", "chatbot"),
            "user": os.getenv("DB_USER", "postgres"),
            "password": os.getenv("DB_PASSWORD", ""),
            "host": os.getenv("DB_HOST", "localhost"),
            "port": os.getenv("DB_PORT", "5432"),
            "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "3")),
        }

    def _max_pool_size(self) -> int:
        return max(1, int(os.getenv("DB_POOL_MAX", "5")))

    def _require_driver(self) -> None:
        if psycopg2 is None or pool is None:
            raise DatabaseUnavailableError(
                "psycopg2 is not installed. Run 'pip3 install psycopg2-binary' first."
            )

    def _get_pool(self) -> pool.ThreadedConnectionPool:
        if not self._enabled():
            raise DatabaseUnavailableError("Database integration is disabled.")
        self._require_driver()
        if self._pool is not None:
            return self._pool
        with self._lock:
            if self._pool is None:
                params = self._params()
                try:
                    self._pool = pool.ThreadedConnectionPool(1, self._max_pool_size(), **params)
                except PsycopgError as exc:
                    raise DatabaseUnavailableError(f"PostgreSQL connection failed: {exc}") from exc
        return self._pool

    def query(self, query: str, params: tuple | list | None = None, *, fetch: str = "auto") -> Any:
        db_pool = self._get_pool()
        connection = db_pool.getconn()
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(query, params or ())
                if fetch == "none": return None
                if fetch == "one": return cursor.fetchone()
                if fetch == "all": return cursor.fetchall()
                try: return cursor.fetchall()
                except ProgrammingError: return None
        except PsycopgError as exc:
            raise DatabaseQueryError(str(exc)) from exc
        finally:
            db_pool.putconn(connection)

    def run_schema(self, schema_path: Path = SCHEMA_FILE) -> None:
        sql = schema_path.read_text(encoding="utf-8")
        self.query(sql, fetch="none")

    def status(self) -> DatabaseStatus:
        params = self._params()
        if not self._enabled():
            return DatabaseStatus(False, False, psycopg2 is not None, "Disabled", params["dbname"], params["host"], str(params["port"]), str(SCHEMA_FILE))
        if psycopg2 is None:
            return DatabaseStatus(True, False, False, "psycopg2 missing", params["dbname"], params["host"], str(params["port"]), str(SCHEMA_FILE))
        try:
            self.query("SELECT 1", fetch="one")
            return DatabaseStatus(True, True, True, None, params["dbname"], params["host"], str(params["port"]), str(SCHEMA_FILE))
        except DatabaseError as exc:
            return DatabaseStatus(True, False, True, str(exc), params["dbname"], params["host"], str(params["port"]), str(SCHEMA_FILE))

database_client = DatabaseClient()
def db_query(query: str, params: tuple | list | None = None, *, fetch: str = "auto") -> Any:
    return database_client.query(query, params, fetch=fetch)
def run_schema(schema_path: Path = SCHEMA_FILE) -> None:
    database_client.run_schema(schema_path)
def get_database_status() -> DatabaseStatus:
    return database_client.status()
