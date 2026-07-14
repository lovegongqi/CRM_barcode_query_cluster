import threading
import time

from cluster.scheduler import ClusterScheduler


class FakeRepository:
    def __init__(self):
        self.item = {
            "id": "item-1",
            "job_id": "job-1",
            "kind": "query",
            "item_key": "5312503010858",
        }
        self.claimed = []
        self.completed = threading.Event()
        self.failed = []

    def recover_expired_items(self):
        return 0

    def claim_item(self, kinds, owner, lease_seconds):
        self.claimed.append((tuple(kinds), owner, lease_seconds))
        if self.item is None:
            return None
        item, self.item = self.item, None
        return item

    def start_item(self, item_id, owner):
        return True

    def renew_lease(self, item_id, owner, lease_seconds):
        return True

    def complete_item(self, item_id, owner, result):
        self.completed.set()
        return True

    def fail_item(self, item_id, owner, error):
        self.failed.append(error)
        self.completed.set()
        return True


def test_query_slot_claims_all_safe_query_work():
    repository = FakeRepository()
    worker = object()
    slots = [
        {
            "slot_id": "query-1",
            "kind": "query",
            "logged_in": True,
            "worker": worker,
        }
    ]
    scheduler = ClusterScheduler(
        repository,
        "hk",
        lambda: list(slots),
        {"query": lambda actual_worker, item, _repository: {"barcode": item["item_key"]}},
        poll_interval=0.01,
        lease_seconds=3,
    )

    scheduler.start()
    assert repository.completed.wait(2)
    scheduler.stop()

    kinds, owner, _lease_seconds = repository.claimed[0]
    assert kinds == ("query", "library_lookup", "service_close")
    assert owner == "hk:query-1"
    assert repository.failed == []


def test_new_logged_in_slot_is_added_without_restart():
    repository = FakeRepository()
    repository.item = None
    slots = []
    scheduler = ClusterScheduler(
        repository,
        "sg",
        lambda: list(slots),
        {},
        poll_interval=0.01,
        lease_seconds=3,
    )
    scheduler.start()
    slots.append(
        {
            "slot_id": "transfer-1",
            "kind": "transfer",
            "logged_in": True,
            "worker": object(),
        }
    )
    scheduler.reconcile_slots()

    deadline = time.time() + 2
    while time.time() < deadline and not repository.claimed:
        time.sleep(0.01)
    scheduler.stop()

    assert repository.claimed[0][0] == ("transfer",)
    assert repository.claimed[0][1] == "sg:transfer-1"
