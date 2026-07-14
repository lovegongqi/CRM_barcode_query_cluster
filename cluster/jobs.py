import uuid
from datetime import datetime

from psycopg.types.json import Jsonb

from cluster.db import Database


TERMINAL_STATUSES = {"succeeded", "cancelled", "skipped", "needs_review"}
UNSAFE_KINDS = {"transfer", "service_close"}


def _plain(row):
    if row is None:
        return None
    result = dict(row)
    for key, value in list(result.items()):
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


class JobRepository:
    def __init__(self, database: Database):
        self.db = database

    def create_job(
        self,
        job_type: str,
        items: list[dict],
        payload: dict,
        created_by: str,
        idempotency_key: str | None = None,
    ) -> dict:
        with self.db.transaction() as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = %s",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    return _plain(existing)

            job_id = uuid.uuid4()
            connection.execute(
                """
                INSERT INTO jobs(
                    id, type, status, created_by, payload_json,
                    result_json, idempotency_key, total, created_at, updated_at
                ) VALUES (%s, %s, 'pending', %s, %s, '{}'::jsonb, %s, %s, now(), now())
                """,
                (
                    job_id,
                    job_type,
                    created_by,
                    Jsonb(payload or {}),
                    idempotency_key,
                    len(items),
                ),
            )
            for index, item in enumerate(items):
                item_key = str(item.get("item_key") or item.get("barcode") or index)
                item_payload = dict(item)
                item_payload.setdefault("max_attempts", 6 if job_type in {"query", "library_lookup"} else 2)
                connection.execute(
                    """
                    INSERT INTO job_items(
                        id, job_id, kind, item_key, payload_json,
                        result_json, status, attempts, idempotency_key,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, 'pending', 0, %s, now(), now())
                    """,
                    (
                        uuid.uuid4(),
                        job_id,
                        str(item.get("kind") or job_type),
                        item_key,
                        Jsonb(item_payload),
                        f"{job_id}:{index}:{item_key}",
                    ),
                )
            row = connection.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
            return _plain(row)

    def claim_item(self, kinds: list[str], owner: str, lease_seconds: int):
        if not kinds:
            return None
        self.recover_expired_items()
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT item.*
                FROM job_items AS item
                JOIN jobs AS job ON job.id = item.job_id
                WHERE item.kind = ANY(%s)
                  AND job.stop_requested = FALSE
                  AND (
                    item.status = 'pending'
                    OR (
                        item.status = 'failed'
                        AND item.attempts < COALESCE((item.payload_json->>'max_attempts')::integer, 6)
                    )
                  )
                ORDER BY item.created_at, item.id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (kinds,),
            ).fetchone()
            if not row:
                return None
            claimed = connection.execute(
                """
                UPDATE job_items SET
                    status = 'leased',
                    attempts = attempts + 1,
                    lease_owner = %s,
                    lease_expires_at = now() + (%s * interval '1 second'),
                    updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (owner, lease_seconds, row["id"]),
            ).fetchone()
            connection.execute(
                """
                UPDATE jobs SET
                    status = 'running',
                    started_at = COALESCE(started_at, now()),
                    updated_at = now()
                WHERE id = %s
                """,
                (row["job_id"],),
            )
            return _plain(claimed)

    def request_stop(self, job_id: str) -> bool:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                UPDATE jobs SET stop_requested = TRUE, updated_at = now()
                WHERE id = %s
                RETURNING id
                """,
                (job_id,),
            ).fetchone()
            if not row:
                return False
            connection.execute(
                """
                UPDATE job_items SET
                    status = 'cancelled',
                    error = '用户已停止任务',
                    updated_at = now()
                WHERE job_id = %s AND status IN ('pending', 'failed')
                """,
                (job_id,),
            )
            self._refresh_job(connection, row["id"])
            return True

    def start_item(self, item_id: str, owner: str) -> bool:
        return bool(
            self.db.execute(
                """
                UPDATE job_items SET status = 'running', updated_at = now()
                WHERE id = %s AND lease_owner = %s AND status = 'leased'
                """,
                (item_id, owner),
            )
        )

    def renew_lease(self, item_id: str, owner: str, lease_seconds: int) -> bool:
        return bool(
            self.db.execute(
                """
                UPDATE job_items SET
                    lease_expires_at = now() + (%s * interval '1 second'),
                    updated_at = now()
                WHERE id = %s
                  AND lease_owner = %s
                  AND status IN ('leased', 'running', 'submitted_to_crm')
                """,
                (lease_seconds, item_id, owner),
            )
        )

    def mark_submitted(self, item_id: str, owner: str, external_ref: str = "") -> bool:
        return bool(
            self.db.execute(
                """
                UPDATE job_items SET
                    status = 'submitted_to_crm',
                    external_ref = %s,
                    updated_at = now()
                WHERE id = %s
                  AND lease_owner = %s
                  AND status IN ('leased', 'running')
                """,
                (external_ref, item_id, owner),
            )
        )

    def complete_item(self, item_id: str, owner: str, result: dict | None = None) -> bool:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                UPDATE job_items SET
                    status = 'succeeded',
                    result_json = %s,
                    lease_owner = '',
                    lease_expires_at = NULL,
                    error = '',
                    updated_at = now()
                WHERE id = %s
                  AND lease_owner = %s
                  AND status IN ('leased', 'running', 'submitted_to_crm')
                RETURNING job_id
                """,
                (Jsonb(result or {}), item_id, owner),
            ).fetchone()
            if not row:
                return False
            self._refresh_job(connection, row["job_id"])
            return True

    def fail_item(self, item_id: str, owner: str, error: str) -> bool:
        with self.db.transaction() as connection:
            current = connection.execute(
                "SELECT job_id, kind, status FROM job_items WHERE id = %s AND lease_owner = %s FOR UPDATE",
                (item_id, owner),
            ).fetchone()
            if not current:
                return False
            next_status = (
                "needs_review"
                if current["status"] == "submitted_to_crm" and current["kind"] in UNSAFE_KINDS
                else "failed"
            )
            connection.execute(
                """
                UPDATE job_items SET
                    status = %s,
                    lease_owner = '',
                    lease_expires_at = NULL,
                    error = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (next_status, error, item_id),
            )
            self._refresh_job(connection, current["job_id"])
            return True

    def recover_expired_items(self) -> int:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                UPDATE job_items SET
                    status = CASE
                        WHEN status = 'submitted_to_crm' AND kind IN ('transfer', 'service_close')
                            THEN 'needs_review'
                        ELSE 'pending'
                    END,
                    lease_owner = '',
                    lease_expires_at = NULL,
                    error = CASE
                        WHEN status = 'submitted_to_crm' AND kind IN ('transfer', 'service_close')
                            THEN 'CRM 已提交后租约丢失，需要人工复核'
                        ELSE error
                    END,
                    updated_at = now()
                WHERE lease_expires_at < now()
                  AND status IN ('leased', 'running', 'submitted_to_crm')
                RETURNING job_id
                """
            ).fetchall()
            for job_id in {row["job_id"] for row in rows}:
                self._refresh_job(connection, job_id)
            return len(rows)

    def get_item(self, item_id: str):
        return _plain(
            self.db.fetch_one("SELECT * FROM job_items WHERE id = %s", (item_id,))
        )

    def append_log(
        self,
        job_id: str,
        page: str,
        node_id: str,
        slot_id: str,
        level: str,
        message: str,
    ) -> int:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                INSERT INTO job_logs(job_id, page, node_id, slot_id, level, message)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (job_id, page, node_id, slot_id, level, message),
            ).fetchone()
            return int(row["id"])

    def status(self, job_id: str, since_log_id: int = 0):
        job = self.db.fetch_one("SELECT * FROM jobs WHERE id = %s", (job_id,))
        if not job:
            return None
        items = self.db.fetch_all(
            "SELECT * FROM job_items WHERE job_id = %s ORDER BY created_at, id",
            (job_id,),
        )
        logs = self.db.fetch_all(
            """
            SELECT * FROM job_logs
            WHERE job_id = %s AND id > %s
            ORDER BY id
            """,
            (job_id, since_log_id),
        )
        result = _plain(job)
        result["items"] = [_plain(row) for row in items]
        result["logs"] = [_plain(row) for row in logs]
        result["needs_review"] = sum(row["status"] == "needs_review" for row in items)
        return result

    def latest_job(self, job_type: str, created_by: str = ""):
        params = [job_type]
        created_by_sql = ""
        if created_by:
            created_by_sql = " AND created_by = %s"
            params.append(created_by)
        row = self.db.fetch_one(
            f"""
            SELECT * FROM jobs
            WHERE type = %s{created_by_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            tuple(params),
        )
        return _plain(row)

    def list_needs_review(self, limit: int = 100) -> list[dict]:
        rows = self.db.fetch_all(
            """
            SELECT
                item.*,
                job.type AS job_type,
                job.created_by,
                job.created_at AS job_created_at
            FROM job_items AS item
            JOIN jobs AS job ON job.id = item.job_id
            WHERE item.status = 'needs_review'
            ORDER BY item.updated_at DESC, item.id DESC
            LIMIT %s
            """,
            (max(1, min(int(limit), 500)),),
        )
        return [_plain(row) for row in rows]

    @staticmethod
    def _refresh_job(connection, job_id) -> None:
        rows = connection.execute(
            "SELECT status, attempts, payload_json FROM job_items WHERE job_id = %s",
            (job_id,),
        ).fetchall()
        succeeded = sum(row["status"] == "succeeded" for row in rows)
        needs_review = sum(row["status"] == "needs_review" for row in rows)
        terminal_failed = sum(
            row["status"] == "failed"
            and row["attempts"] >= int((row["payload_json"] or {}).get("max_attempts", 6))
            for row in rows
        )
        completed = sum(row["status"] in TERMINAL_STATUSES for row in rows) + terminal_failed
        failed = needs_review + terminal_failed + sum(row["status"] == "cancelled" for row in rows)
        total = len(rows)
        if completed < total:
            status = "running"
            finished_at = None
        elif failed == 0:
            status = "succeeded"
            finished_at = "now()"
        elif succeeded:
            status = "partial"
            finished_at = "now()"
        else:
            status = "failed"
            finished_at = "now()"
        connection.execute(
            f"""
            UPDATE jobs SET
                status = %s,
                completed = %s,
                succeeded = %s,
                failed = %s,
                finished_at = {finished_at if finished_at else 'NULL'},
                updated_at = now()
            WHERE id = %s
            """,
            (status, completed, succeeded, failed, job_id),
        )
