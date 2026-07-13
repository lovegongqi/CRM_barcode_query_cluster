from pathlib import Path

from cluster.db import Database


class MigrationRunner:
    def __init__(self, database: Database, schema_dir: Path | None = None):
        self.database = database
        self.schema_dir = schema_dir or Path(__file__).with_name("schema")

    def apply(self) -> list[str]:
        applied_now = []
        with self.database.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (20260713,))
            applied = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }

            for migration_path in sorted(self.schema_dir.glob("*.sql")):
                version = migration_path.stem
                if version in applied:
                    continue
                connection.execute(migration_path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (%s)",
                    (version,),
                )
                applied_now.append(version)

        return applied_now
