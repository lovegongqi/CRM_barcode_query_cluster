import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from scripts.bootstrap_cluster_database import bootstrap_database


def test_bootstrap_database_is_idempotent(test_database_url):
    suffix = uuid.uuid4().hex[:10]
    app_role = f"crm_app_{suffix}"
    rewind_role = f"crm_rewind_{suffix}"
    database_name = f"crm_barcode_{suffix}"
    params = conninfo_to_dict(test_database_url)
    params["dbname"] = "postgres"
    admin_dsn = make_conninfo(**params)

    try:
        first = bootstrap_database(
            admin_dsn,
            app_password="first-app-password",
            rewind_password="first-rewind-password",
            app_role=app_role,
            rewind_role=rewind_role,
            database_name=database_name,
        )
        second = bootstrap_database(
            admin_dsn,
            app_password="second-app-password",
            rewind_password="second-rewind-password",
            app_role=app_role,
            rewind_role=rewind_role,
            database_name=database_name,
        )

        assert first == {"database_created": True, "roles_created": 2}
        assert second == {"database_created": False, "roles_created": 0}
        with psycopg.connect(admin_dsn) as connection:
            assert connection.execute(
                "SELECT rolname FROM pg_roles WHERE rolname IN (%s, %s) ORDER BY rolname",
                (app_role, rewind_role),
            ).fetchall() == [(app_role,), (rewind_role,)]
            assert connection.execute(
                "SELECT datname FROM pg_database WHERE datname = %s",
                (database_name,),
            ).fetchone() == (database_name,)
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(app_role))
            )
            connection.execute(
                sql.SQL("DROP OWNED BY {}").format(sql.Identifier(rewind_role))
            )
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(rewind_role))
            )
