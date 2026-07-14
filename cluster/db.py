from contextlib import contextmanager
import threading

from psycopg import OperationalError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10):
        self._url = url
        self._min_size = min_size
        self._max_size = max_size
        self._pool_lock = threading.Lock()
        self.pool = self._create_pool()

    def _create_pool(self):
        return ConnectionPool(
            conninfo=self._url,
            min_size=self._min_size,
            max_size=self._max_size,
            kwargs={"row_factory": dict_row},
            check=self._check_writable_connection,
            open=True,
        )

    def _replace_pool(self, failed_pool):
        replaced = False
        with self._pool_lock:
            if self.pool is failed_pool:
                self.pool = self._create_pool()
                replaced = True
            pool = self.pool
        if replaced:
            failed_pool.close(timeout=0)
        return pool

    def _acquire_connection(self):
        pool = self.pool
        try:
            return pool, pool.getconn(timeout=5)
        except (PoolClosed, PoolTimeout):
            pool = self._replace_pool(pool)
            return pool, pool.getconn(timeout=10)

    @contextmanager
    def _connection(self):
        pool, connection = self._acquire_connection()
        try:
            with connection:
                yield connection
        finally:
            pool.putconn(connection)

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
        with self._connection() as connection:
            with connection.transaction():
                yield connection

    def execute(self, sql: str, params=None) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(sql, params)
            return cursor.rowcount

    def fetch_one(self, sql: str, params=None):
        with self._connection() as connection:
            row = connection.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def close(self) -> None:
        self.pool.close()
