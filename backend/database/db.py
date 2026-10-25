"""Database access layer. PostgreSQL (psycopg 3) is the production database; SQLite exists ONLY for automated tests / quick local development.
SQL is written once with %s placeholders; the SQLite adapter converts them. JSON values go through `db.js()`."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from ..utils.logging_config import get_logger

log = get_logger("database")
SCHEMA = Path(__file__).with_name("schema.sql")


class DatabaseError(RuntimeError):
    """The database could not be reached or a statement failed (details are logged, never shown with credentials)."""


def to_sqlite_ddl(ddl: str) -> str:
    d = ddl.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    d = re.sub(r"\bJSONB\b", "TEXT", d)
    d = re.sub(r"\bTIMESTAMPTZ\b", "TEXT", d)
    d = d.replace("DOUBLE PRECISION", "REAL")
    return re.sub(r"\bDATE\b", "TEXT", d)


class Database:
    def __init__(self, url: str = "", sqlite_path: str = ""):
        self.url = url
        self.engine = "postgresql" if url.startswith(("postgresql://", "postgres://")) else "sqlite"
        self._lock = threading.RLock()
        self._mem: Optional[sqlite3.Connection] = None
        self.sqlite_path = ""
        if self.engine == "sqlite":
            self.sqlite_path = sqlite_path or (url.removeprefix("sqlite:///") if url.startswith("sqlite:///") else "data/travelai_dev.db")
            if self.sqlite_path == ":memory:":
                self._mem = sqlite3.connect(":memory:", check_same_thread=False)
            else:
                Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            try:
                import psycopg  # noqa: F401
            except ImportError as exc:
                raise DatabaseError("psycopg is not installed. Run: pip install -r requirements.txt") from exc

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        if self.engine == "postgresql":
            import psycopg
            from psycopg.rows import dict_row

            try:
                conn = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=6)
            except Exception as exc:
                log.error("PostgreSQL connection failed: %s", type(exc).__name__)
                raise DatabaseError("Could not connect to PostgreSQL. Check DATABASE_URL and that the server is running.") from None
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            with self._lock:
                conn = self._mem or sqlite3.connect(self.sqlite_path, timeout=15)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    if self._mem is None:
                        conn.close()

    def _sql(self, sql: str) -> str:
        return sql.replace("%s", "?") if self.engine == "sqlite" else sql

    def js(self, value: Any) -> Any:
        """JSON parameter: Jsonb on PostgreSQL, text on SQLite."""
        if self.engine == "postgresql":
            from psycopg.types.json import Jsonb

            return Jsonb(value)
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def load_json(value: Any) -> Any:
        return json.loads(value) if isinstance(value, (str, bytes)) else value

    def _run(self, sql: str, params: tuple, mode: str) -> Any:
        try:
            with self._conn() as c:
                cur = c.execute(self._sql(sql), params)
                if mode == "one":
                    row = cur.fetchone()
                    return dict(row) if row is not None else None
                if mode == "all":
                    return [dict(r) for r in cur.fetchall()]
                return None
        except DatabaseError:
            raise
        except Exception as exc:
            log.error("SQL failed: %s", type(exc).__name__, exc_info=True)
            raise DatabaseError("A database operation failed.") from None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._run(sql, params, "exec")

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        return self._run(sql, params, "one")

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        return self._run(sql, params, "all")

    def init_schema(self) -> None:
        ddl = SCHEMA.read_text(encoding="utf-8")
        try:
            with self._conn() as c:
                if self.engine == "postgresql":
                    c.execute(ddl)
                else:
                    c.executescript(to_sqlite_ddl(ddl))
        except DatabaseError:
            raise
        except Exception as exc:
            log.error("schema init failed: %s", type(exc).__name__, exc_info=True)
            raise DatabaseError("Could not create the database tables.") from None
        log.info("database schema ready (%s)", self.engine)

    def ping(self) -> bool:
        try:
            return self.fetchone("SELECT 1 AS ok") is not None
        except DatabaseError:
            return False
