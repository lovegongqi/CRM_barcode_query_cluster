from contextlib import contextmanager

from psycopg import OperationalError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10):
        self.pool = ConnectionPool(
            conninfo=url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            check=self._check_writable_connection,
            open=True,
        )

    @staticmethod
    def _check_writable_connection(connection) -> None:
        was_autocommit = connection.autocommit
        if not was_autocommit:
            connection.autocommit = True
        try:
            row = connection.execute(
                "SELECT pg_is_in_recovery() AS in_recovery"
            ).fetchone()
            if row["in_recovery"]:
                raise OperationalError("connection is attached to a read-only replica")
        finally:
            if not was_autocommit:
                connection.autocommit = False

    @contextmanager
    def transaction(self):
        with self.pool.connection() as connection:
            with connection.transaction():
                yield connection

    def execute(self, sql: str, params=None) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(sql, params)
            return cursor.rowcount

    def fetch_one(self, sql: str, params=None):
        with self.pool.connection() as connection:
            row = connection.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        with self.pool.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def close(self) -> None:
        self.pool.close()
