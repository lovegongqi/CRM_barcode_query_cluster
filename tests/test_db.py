import threading
import uuid

import pytest
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

import cluster.db as db_module
from cluster.db import Database
from cluster.migrations import MigrationRunner


@pytest.fixture()
def database(test_database_url):
    database = Database(test_database_url, min_size=1, max_size=2)
    MigrationRunner(database).apply()
    yield database
    database.close()


class _FakeConnection:
    def __init__(self, in_recovery):
        self.autocommit = False
        self.in_recovery = in_recovery

    def execute(self, sql):
        assert "pg_is_in_recovery()" in sql
        return self

    def fetchone(self):
        return {"in_recovery": self.in_recovery}


def test_writable_connection_check_rejects_demoted_primary():
    connection = _FakeConnection(in_recovery=True)

    with pytest.raises(OperationalError, match="read-only replica"):
        Database._check_writable_connection(connection)

    assert connection.autocommit is False

    primary = _FakeConnection(in_recovery=False)
    Database._check_writable_connection(primary)
    assert primary.autocommit is False


def test_database_pool_checks_connections_are_writable(monkeypatch):
    captured = {}

    class FakePool:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(db_module, "ConnectionPool", FakePool)

    Database("postgresql://example")

    assert captured["check"] is Database._check_writable_connection


def test_database_replaces_exhausted_pool_before_running_sql():
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    class FailedPool:
        def __init__(self):
            self.closed = False

        def getconn(self, timeout=None):
            raise PoolTimeout("old primary unavailable")

        def close(self, timeout=5):
            self.closed = True

    class WorkingPool:
        def __init__(self):
            self.connection = FakeConnection()
            self.returned = None
            self.wait_timeout = None

        def wait(self, timeout=None):
            self.wait_timeout = timeout

        def getconn(self, timeout=None):
            return self.connection

        def putconn(self, connection):
            self.returned = connection

    failed_pool = FailedPool()
    working_pool = WorkingPool()
    database = Database.__new__(Database)
    database.pool = failed_pool
    database._pool_lock = threading.Lock()
    database._create_pool = lambda: working_pool

    with database._connection() as connection:
        assert connection is working_pool.connection

    assert database.pool is working_pool
    assert failed_pool.closed is True
    assert working_pool.wait_timeout == 10
    assert working_pool.returned is working_pool.connection


def test_database_does_not_publish_an_unready_replacement_pool():
    class CurrentPool:
        pass

    class UnreadyPool:
        def __init__(self):
            self.closed = False

        def wait(self, timeout=None):
            raise PoolTimeout("primary is still changing")

        def close(self, timeout=5):
            self.closed = True

    current_pool = CurrentPool()
    unready_pool = UnreadyPool()
    database = Database.__new__(Database)
    database.pool = current_pool
    database._pool_lock = threading.Lock()
    database._create_pool = lambda: unready_pool

    with pytest.raises(PoolTimeout, match="primary is still changing"):
        database._replace_pool(current_pool)

    assert database.pool is current_pool
    assert unready_pool.closed is True


def test_transaction_rolls_back_on_error(database):
    scope = "rollback:" + uuid.uuid4().hex

    with pytest.raises(RuntimeError, match="stop"):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO runtime_config(scope, value_json, updated_at) VALUES (%s, %s, now())",
                (scope, "{}"),
            )
            raise RuntimeError("stop")

    assert database.fetch_one(
        "SELECT scope FROM runtime_config WHERE scope = %s",
        (scope,),
    ) is None


def test_fetch_methods_return_plain_dicts(database):
    scope = "fetch:" + uuid.uuid4().hex
    database.execute(
        "INSERT INTO runtime_config(scope, value_json, updated_at) VALUES (%s, %s, now())",
        (scope, '{"query_workers": 5}'),
    )

    row = database.fetch_one(
        "SELECT scope, value_json FROM runtime_config WHERE scope = %s",
        (scope,),
    )
    rows = database.fetch_all(
        "SELECT scope FROM runtime_config WHERE scope = %s",
        (scope,),
    )

    assert row["scope"] == scope
    assert row["value_json"] == {"query_workers": 5}
    assert rows == [{"scope": scope}]


def test_advisory_lock_is_nonblocking(database):
    lock_name = "test-lock:" + uuid.uuid4().hex

    with database.advisory_lock(lock_name) as first_acquired:
        with database.advisory_lock(lock_name) as second_acquired:
            assert first_acquired is True
            assert second_acquired is False

    with database.advisory_lock(lock_name) as acquired_after_release:
        assert acquired_after_release is True
