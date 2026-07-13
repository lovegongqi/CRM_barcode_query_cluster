import uuid
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb
from werkzeug.security import check_password_hash, generate_password_hash

from cluster.crypto import CredentialCipher
from cluster.db import Database


def _timestamp(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _public_account(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "permissions": row["permissions_json"],
        "is_admin": row["is_admin"],
        "updated_at": _timestamp(row["updated_at"]),
    }


class CatalogRepository:
    def __init__(self, database: Database, cipher: CredentialCipher):
        self.db = database
        self.cipher = cipher

    def replace_accounts(self, accounts: list[dict]) -> None:
        with self.db.transaction() as connection:
            existing = {
                row["username"]: row["password_hash"]
                for row in connection.execute(
                    "SELECT username, password_hash FROM app_accounts"
                ).fetchall()
            }
            connection.execute("DELETE FROM app_accounts")
            for account in accounts:
                username = str(account.get("username") or "").strip()
                password = str(account.get("password") or "")
                password_hash = (
                    generate_password_hash(password)
                    if password
                    else existing.get(username)
                )
                if not username or not password_hash:
                    raise ValueError("账号和密码不能为空")
                connection.execute(
                    """
                    INSERT INTO app_accounts(
                        id, username, display_name, password_hash,
                        permissions_json, is_admin, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, now())
                    """,
                    (
                        str(account.get("id") or uuid.uuid4()),
                        username,
                        str(account.get("display_name") or ""),
                        password_hash,
                        Jsonb(account.get("permissions") or []),
                        bool(account.get("is_admin") or username == "admin"),
                    ),
                )

    def list_accounts(self) -> list[dict]:
        rows = self.db.fetch_all(
            """
            SELECT id, username, display_name, permissions_json, is_admin, updated_at
            FROM app_accounts
            ORDER BY is_admin DESC, username
            """
        )
        return [_public_account(row) for row in rows]

    def authenticate_account(self, username: str, password: str):
        row = self.db.fetch_one(
            "SELECT * FROM app_accounts WHERE username = %s",
            (username,),
        )
        if not row or not check_password_hash(row["password_hash"], password):
            return None
        return _public_account(row)

    def set_runtime_config(self, scope: str, value: dict) -> None:
        self.db.execute(
            """
            INSERT INTO runtime_config(scope, value_json, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (scope) DO UPDATE SET
                value_json = EXCLUDED.value_json,
                updated_at = now()
            """,
            (scope, Jsonb(value)),
        )

    def get_runtime_config(self, scope: str):
        row = self.db.fetch_one(
            "SELECT value_json FROM runtime_config WHERE scope = %s",
            (scope,),
        )
        return row["value_json"] if row else None

    def upsert_barcode(self, record: dict) -> None:
        barcode = str(record.get("barcode") or "").strip()
        if not barcode:
            raise ValueError("条码不能为空")
        self.db.execute(
            """
            INSERT INTO barcode_records(
                barcode, object_key, object_sha256, fields_json,
                product_name, product_code, current_dealer, service_dealer,
                service_closed, latest_service_order, remark, archived,
                archive_time, current_dealer_override, transfer_updated_at,
                query_node_id, query_slot_id, query_updated_at, metadata_json,
                updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, now()
            )
            ON CONFLICT (barcode) DO UPDATE SET
                object_key = EXCLUDED.object_key,
                object_sha256 = EXCLUDED.object_sha256,
                fields_json = EXCLUDED.fields_json,
                product_name = EXCLUDED.product_name,
                product_code = EXCLUDED.product_code,
                current_dealer = EXCLUDED.current_dealer,
                service_dealer = EXCLUDED.service_dealer,
                service_closed = EXCLUDED.service_closed,
                latest_service_order = EXCLUDED.latest_service_order,
                remark = EXCLUDED.remark,
                archived = EXCLUDED.archived,
                archive_time = EXCLUDED.archive_time,
                current_dealer_override = EXCLUDED.current_dealer_override,
                transfer_updated_at = EXCLUDED.transfer_updated_at,
                query_node_id = EXCLUDED.query_node_id,
                query_slot_id = EXCLUDED.query_slot_id,
                query_updated_at = EXCLUDED.query_updated_at,
                metadata_json = EXCLUDED.metadata_json,
                updated_at = now()
            """,
            (
                barcode,
                str(record.get("object_key") or ""),
                str(record.get("object_sha256") or ""),
                Jsonb(record.get("fields") or record.get("fields_json") or {}),
                str(record.get("product_name") or ""),
                str(record.get("product_code") or ""),
                str(record.get("current_dealer") or ""),
                str(record.get("service_dealer") or ""),
                record.get("service_closed"),
                str(record.get("latest_service_order") or ""),
                str(record.get("remark") or ""),
                bool(record.get("archived", False)),
                record.get("archive_time"),
                str(record.get("current_dealer_override") or ""),
                record.get("transfer_updated_at"),
                str(record.get("query_node_id") or ""),
                str(record.get("query_slot_id") or ""),
                record.get("query_updated_at"),
                Jsonb(record.get("metadata") or record.get("metadata_json") or {}),
            ),
        )

    def get_barcode(self, barcode: str):
        row = self.db.fetch_one(
            "SELECT * FROM barcode_records WHERE barcode = %s",
            (barcode,),
        )
        return self._barcode_row(row) if row else None

    def list_barcodes(self) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT * FROM barcode_records ORDER BY updated_at DESC"
        )
        return [self._barcode_row(row) for row in rows]

    def delete_barcode(self, barcode: str) -> bool:
        return bool(
            self.db.execute(
                "DELETE FROM barcode_records WHERE barcode = %s",
                (barcode,),
            )
        )

    @staticmethod
    def _barcode_row(row: dict) -> dict:
        result = dict(row)
        result["fields"] = result.pop("fields_json")
        result["metadata"] = result.pop("metadata_json")
        for key, value in list(result.items()):
            result[key] = _timestamp(value)
        return result

    def upsert_product_rule(
        self,
        prefix: str,
        product_code: str,
        product_name: str,
        source_barcode: str = "",
    ) -> None:
        self.db.execute(
            """
            INSERT INTO product_rules(
                prefix, product_code, product_name, source_barcode, updated_at
            ) VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (prefix) DO UPDATE SET
                product_code = EXCLUDED.product_code,
                product_name = EXCLUDED.product_name,
                source_barcode = EXCLUDED.source_barcode,
                updated_at = now()
            """,
            (prefix, product_code, product_name, source_barcode),
        )

    def get_product_rule(self, prefix: str):
        row = self.db.fetch_one(
            "SELECT * FROM product_rules WHERE prefix = %s",
            (prefix,),
        )
        if row:
            row["updated_at"] = _timestamp(row["updated_at"])
        return row

    def list_product_rules(self) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT * FROM product_rules ORDER BY length(prefix) DESC, prefix"
        )
        for row in rows:
            row["updated_at"] = _timestamp(row["updated_at"])
        return rows

    def delete_product_rule(self, prefix: str) -> bool:
        return bool(
            self.db.execute(
                "DELETE FROM product_rules WHERE prefix = %s",
                (prefix,),
            )
        )

    def upsert_distributors(self, names: list[str]) -> None:
        clean_names = list(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
        with self.db.transaction() as connection:
            for name in clean_names:
                connection.execute(
                    """
                    INSERT INTO distributors(name, deleted, last_used_at, updated_at)
                    VALUES (%s, FALSE, now(), now())
                    ON CONFLICT (name) DO UPDATE SET
                        deleted = FALSE,
                        last_used_at = now(),
                        updated_at = now()
                    """,
                    (name,),
                )

    def list_distributors(self, include_deleted: bool = False) -> list[dict]:
        where = "" if include_deleted else "WHERE deleted = FALSE"
        rows = self.db.fetch_all(
            f"""
            SELECT name, deleted, last_used_at, updated_at
            FROM distributors
            {where}
            ORDER BY last_used_at DESC NULLS LAST, name
            """
        )
        for row in rows:
            row["last_used_at"] = _timestamp(row["last_used_at"])
            row["updated_at"] = _timestamp(row["updated_at"])
        return rows

    def delete_distributor(self, name: str) -> bool:
        return bool(
            self.db.execute(
                """
                UPDATE distributors
                SET deleted = TRUE, updated_at = now()
                WHERE name = %s
                """,
                (name,),
            )
        )

    def save_credentials(
        self,
        owner_key: str,
        remember: bool,
        username: str = "",
        password: str = "",
    ) -> None:
        if not remember:
            self.db.execute(
                "DELETE FROM crm_credentials WHERE owner_key = %s",
                (owner_key,),
            )
            return
        self.db.execute(
            """
            INSERT INTO crm_credentials(
                owner_key, username, password_ciphertext, remember, updated_at
            ) VALUES (%s, %s, %s, TRUE, now())
            ON CONFLICT (owner_key) DO UPDATE SET
                username = EXCLUDED.username,
                password_ciphertext = EXCLUDED.password_ciphertext,
                remember = TRUE,
                updated_at = now()
            """,
            (owner_key, username, self.cipher.encrypt(password)),
        )

    def get_credentials(self, owner_key: str) -> dict:
        row = self.db.fetch_one(
            """
            SELECT username, password_ciphertext, remember
            FROM crm_credentials
            WHERE owner_key = %s
            """,
            (owner_key,),
        )
        if not row or not row["remember"]:
            return {"remember": False, "username": "", "password": ""}
        return {
            "remember": True,
            "username": row["username"],
            "password": self.cipher.decrypt(row["password_ciphertext"]),
        }

    def heartbeat_node(self, node: dict, ttl_seconds: int = 180) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)
        self.db.execute(
            """
            INSERT INTO cluster_nodes(
                node_id, node_name, node_role, public_url,
                query_workers, transfer_workers, database_role,
                replication_lag_bytes, last_seen_at, expires_at, status_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (node_id) DO UPDATE SET
                node_name = EXCLUDED.node_name,
                node_role = EXCLUDED.node_role,
                public_url = EXCLUDED.public_url,
                query_workers = EXCLUDED.query_workers,
                transfer_workers = EXCLUDED.transfer_workers,
                database_role = EXCLUDED.database_role,
                replication_lag_bytes = EXCLUDED.replication_lag_bytes,
                last_seen_at = EXCLUDED.last_seen_at,
                expires_at = EXCLUDED.expires_at,
                status_json = EXCLUDED.status_json
            """,
            (
                node["node_id"],
                node["node_name"],
                node["node_role"],
                node.get("public_url", ""),
                int(node.get("query_workers", 5)),
                int(node.get("transfer_workers", 2)),
                node.get("database_role", ""),
                node.get("replication_lag_bytes"),
                now,
                expires_at,
                Jsonb(node.get("status") or {}),
            ),
        )

    def list_nodes(self) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT * FROM cluster_nodes ORDER BY last_seen_at DESC, node_id"
        )
        for row in rows:
            row["last_seen_at"] = _timestamp(row["last_seen_at"])
            row["expires_at"] = _timestamp(row["expires_at"])
        return rows

    def replace_slots(
        self,
        node_id: str,
        slots: list[dict],
        ttl_seconds: int = 180,
    ) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM crm_slots WHERE node_id = %s", (node_id,))
            for slot in slots:
                connection.execute(
                    """
                    INSERT INTO crm_slots(
                        node_id, slot_id, kind, logged_in, busy,
                        current_item_id, last_error, last_seen_at, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        node_id,
                        slot["slot_id"],
                        slot["kind"],
                        bool(slot.get("logged_in", False)),
                        bool(slot.get("busy", False)),
                        str(slot.get("current_item_id") or ""),
                        str(slot.get("last_error") or ""),
                        now,
                        expires_at,
                    ),
                )

    def list_slots(self, node_id: str | None = None) -> list[dict]:
        if node_id:
            rows = self.db.fetch_all(
                "SELECT * FROM crm_slots WHERE node_id = %s ORDER BY slot_id",
                (node_id,),
            )
        else:
            rows = self.db.fetch_all(
                "SELECT * FROM crm_slots ORDER BY node_id, slot_id"
            )
        for row in rows:
            row["last_seen_at"] = _timestamp(row["last_seen_at"])
            row["expires_at"] = _timestamp(row["expires_at"])
        return rows
