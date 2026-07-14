import uuid

import pytest

from cluster.db import Database
from cluster.jobs import JobRepository
from cluster.migrations import MigrationRunner


@pytest.fixture()
def repository(test_database_url):
    database = Database(test_database_url, min_size=1, max_size=4)
    MigrationRunner(database).apply()
    database.execute("TRUNCATE jobs CASCADE")
    repository = JobRepository(database)
    yield repository
    database.close()


def test_skip_locked_gives_item_to_only_one_worker(repository):
    repository.create_job(
        "query",
        [{"item_key": "A", "barcode": "A"}],
        {},
        "admin",
        "query:" + uuid.uuid4().hex,
    )

    first = repository.claim_item(["query"], "hk:query-1", 120)
    second = repository.claim_item(["query"], "sg:query-1", 120)

    assert first["item_key"] == "A"
    assert first["lease_owner"] == "hk:query-1"
    assert second is None


def test_submitted_transfer_is_never_replayed(repository):
    job = repository.create_job(
        "transfer",
        [{"item_key": "T1", "barcodes": ["5312503010858"]}],
        {},
        "admin",
        "transfer:" + uuid.uuid4().hex,
    )
    item = repository.claim_item(["transfer"], "hk:transfer-1", 120)
    repository.start_item(item["id"], "hk:transfer-1")
    repository.mark_submitted(item["id"], "hk:transfer-1", "TRSF1")
    repository.db.execute(
        "UPDATE job_items SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        (item["id"],),
    )

    repository.recover_expired_items()

    recovered = repository.get_item(item["id"])
    assert recovered["status"] == "needs_review"
    assert repository.claim_item(["transfer"], "sg:transfer-1", 120) is None
    assert repository.status(job["id"])["needs_review"] == 1


def test_expired_query_returns_to_pending(repository):
    repository.create_job(
        "query",
        [{"item_key": "Q1", "barcode": "Q1"}],
        {},
        "admin",
        "query:" + uuid.uuid4().hex,
    )
    item = repository.claim_item(["query"], "hk:query-1", 120)
    repository.start_item(item["id"], "hk:query-1")
    repository.db.execute(
        "UPDATE job_items SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        (item["id"],),
    )

    repository.recover_expired_items()

    assert repository.get_item(item["id"])["status"] == "pending"
    assert repository.claim_item(["query"], "sg:query-1", 120)["item_key"] == "Q1"


def test_job_creation_is_idempotent_and_logs_use_cursor(repository):
    idempotency_key = "query:" + uuid.uuid4().hex
    first = repository.create_job(
        "query",
        [{"item_key": "A", "barcode": "A"}],
        {},
        "admin",
        idempotency_key,
    )
    second = repository.create_job(
        "query",
        [{"item_key": "B", "barcode": "B"}],
        {},
        "admin",
        idempotency_key,
    )
    first_log = repository.append_log(first["id"], "crm", "hk", "query-1", "info", "开始")
    repository.append_log(first["id"], "crm", "hk", "query-1", "info", "继续")

    status = repository.status(first["id"], since_log_id=first_log)

    assert second["id"] == first["id"]
    assert status["total"] == 1
    assert [row["message"] for row in status["logs"]] == ["继续"]


def test_stopped_job_cancels_pending_items(repository):
    job = repository.create_job(
        "query",
        [
            {"item_key": "A", "barcode": "A"},
            {"item_key": "B", "barcode": "B"},
        ],
        {},
        "admin",
        "stop:" + uuid.uuid4().hex,
    )

    assert repository.request_stop(job["id"]) is True

    status = repository.status(job["id"])
    assert status["stop_requested"] is True
    assert {item["status"] for item in status["items"]} == {"cancelled"}
    assert repository.claim_item(["query"], "hk:query-1", 120) is None


def test_latest_job_and_needs_review_are_queryable(repository):
    library_job = repository.create_job(
        "library_lookup",
        [{"item_key": "5312503010858", "barcode": "5312503010858"}],
        {},
        "admin",
        "library:" + uuid.uuid4().hex,
    )
    transfer_job = repository.create_job(
        "transfer",
        [{"item_key": "transfer", "barcodes": ["5312503010858"]}],
        {},
        "admin",
        "transfer:" + uuid.uuid4().hex,
    )
    item = repository.claim_item(["transfer"], "hk:transfer-1", 120)
    repository.start_item(item["id"], "hk:transfer-1")
    repository.mark_submitted(item["id"], "hk:transfer-1", "TRSF1")
    repository.fail_item(item["id"], "hk:transfer-1", "确认结果未知")

    assert repository.latest_job("library_lookup", "admin")["id"] == library_job["id"]
    review_rows = repository.list_needs_review()
    assert len(review_rows) == 1
    assert review_rows[0]["job_id"] == transfer_job["id"]
    assert review_rows[0]["external_ref"] == "TRSF1"
