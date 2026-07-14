from cluster.db import Database
from cluster.migrations import MigrationRunner


def test_migration_is_idempotent(test_database_url):
    database = Database(test_database_url, min_size=1, max_size=2)
    try:
        with database.transaction() as connection:
            connection.execute("DROP SCHEMA public CASCADE")
            connection.execute("CREATE SCHEMA public")

        runner = MigrationRunner(database)

        assert runner.apply() == [
            "0001_initial",
            "0002_job_safety_states",
            "0003_transfer_summary_jobs",
        ]
        assert runner.apply() == []
        assert database.fetch_one(
            "SELECT version FROM schema_migrations WHERE version = %s",
            ("0001_initial",),
        ) == {"version": "0001_initial"}
    finally:
        database.close()
