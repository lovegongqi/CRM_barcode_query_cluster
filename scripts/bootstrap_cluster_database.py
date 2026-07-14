#!/usr/bin/env python3
import json
import os

import psycopg
from psycopg import sql


def _ensure_login_role(connection, role: str, password: str) -> bool:
    exists = connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    if exists:
        connection.execute(
            sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role),
                sql.Literal(password),
            )
        )
        return False
    connection.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(role),
            sql.Literal(password),
        )
    )
    return True


def bootstrap_database(
    admin_dsn: str,
    app_password: str,
    rewind_password: str,
    *,
    app_role: str = "crm_app",
    rewind_role: str = "crm_rewind",
    database_name: str = "crm_barcode",
) -> dict[str, int | bool]:
    roles_created = 0
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        roles_created += _ensure_login_role(connection, app_role, app_password)
        roles_created += _ensure_login_role(connection, rewind_role, rewind_password)

        for predefined_role in ("pg_read_all_settings", "pg_read_all_stats"):
            connection.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(predefined_role),
                    sql.Identifier(rewind_role),
                )
            )
        connection.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION "
                "pg_catalog.pg_ls_dir(text, boolean, boolean), "
                "pg_catalog.pg_stat_file(text, boolean), "
                "pg_catalog.pg_read_binary_file(text), "
                "pg_catalog.pg_read_binary_file(text, bigint, bigint, boolean) TO {}"
            ).format(sql.Identifier(rewind_role))
        )

        database_exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (database_name,),
        ).fetchone()
        if not database_exists:
            connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(app_role),
                )
            )
        else:
            connection.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(app_role),
                )
            )

    return {
        "database_created": not bool(database_exists),
        "roles_created": roles_created,
    }


def main() -> None:
    required = (
        "POSTGRES_ADMIN_DSN",
        "POSTGRES_APP_PASSWORD",
        "POSTGRES_REWIND_PASSWORD",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("missing environment: " + ", ".join(missing))
    result = bootstrap_database(
        os.environ["POSTGRES_ADMIN_DSN"],
        os.environ["POSTGRES_APP_PASSWORD"],
        os.environ["POSTGRES_REWIND_PASSWORD"],
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
