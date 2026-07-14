import uuid

import pytest
from psycopg import OperationalError

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
