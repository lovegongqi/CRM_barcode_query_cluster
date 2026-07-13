import uuid

import pytest

from cluster.db import Database
from cluster.migrations import MigrationRunner


@pytest.fixture()
def database(test_database_url):
    database = Database(test_database_url, min_size=1, max_size=2)
    MigrationRunner(database).apply()
    yield database
    database.close()


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

