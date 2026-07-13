from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10):
        self.pool = ConnectionPool(
            conninfo=url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )

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
