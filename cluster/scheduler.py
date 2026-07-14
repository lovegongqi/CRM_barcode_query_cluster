import threading
import time


class ClusterScheduler:
    def __init__(
        self,
        repository,
        node_id: str,
        slot_provider,
        handlers: dict,
        poll_interval: float = 1.0,
        lease_seconds: int = 120,
    ):
        self.repository = repository
        self.node_id = node_id
        self.slot_provider = slot_provider
        self.handlers = handlers
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._slot_threads = {}
        self._manager = None

    def start(self) -> None:
        if self._manager and self._manager.is_alive():
            return
        self._stop.clear()
        self.repository.recover_expired_items()
        self.reconcile_slots()
        self._manager = threading.Thread(
            target=self._manager_loop,
            name=f"cluster-scheduler:{self.node_id}",
            daemon=True,
        )
        self._manager.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            entries = list(self._slot_threads.values())
            for _thread, slot_stop in entries:
                slot_stop.set()
        if self._manager:
            self._manager.join(timeout=5)
        for thread, _slot_stop in entries:
            thread.join(timeout=5)

    def reconcile_slots(self) -> None:
        desired = {
            slot["slot_id"]: slot
            for slot in self.slot_provider()
            if slot.get("logged_in") and slot.get("kind") in {"query", "transfer"}
        }
        with self._lock:
            for slot_id, (thread, slot_stop) in list(self._slot_threads.items()):
                if slot_id not in desired:
                    slot_stop.set()
                if not thread.is_alive():
                    self._slot_threads.pop(slot_id, None)
            for slot_id, slot in desired.items():
                if slot_id in self._slot_threads:
                    continue
                slot_stop = threading.Event()
                thread = threading.Thread(
                    target=self._dispatch_loop,
                    args=(slot_id, slot_stop),
                    name=f"cluster-slot:{self.node_id}:{slot_id}",
                    daemon=True,
                )
                self._slot_threads[slot_id] = (thread, slot_stop)
                thread.start()

    def _manager_loop(self) -> None:
        while not self._stop.wait(max(0.1, self.poll_interval)):
            self.reconcile_slots()

    def _dispatch_loop(self, slot_id: str, slot_stop: threading.Event) -> None:
        owner = f"{self.node_id}:{slot_id}"
        while not self._stop.is_set() and not slot_stop.is_set():
            slot = self._slot(slot_id)
            if not slot or not slot.get("logged_in"):
                slot_stop.wait(self.poll_interval)
                continue
            kinds = (
                ["query", "library_lookup", "transfer_summary", "service_close"]
                if slot["kind"] == "query"
                else ["transfer"]
            )
            item = self.repository.claim_item(kinds, owner, self.lease_seconds)
            if not item:
                slot_stop.wait(self.poll_interval)
                continue
            self.repository.start_item(item["id"], owner)
            handler = self.handlers.get(item["kind"])
            if not handler:
                self.repository.fail_item(item["id"], owner, f"未配置 {item['kind']} 处理器")
                continue

            renew_done = threading.Event()
            renewer = threading.Thread(
                target=self._renew_loop,
                args=(item["id"], owner, renew_done),
                daemon=True,
            )
            renewer.start()
            try:
                result = handler(slot.get("worker"), item, self.repository)
                self.repository.complete_item(item["id"], owner, result or {})
            except Exception as error:
                self.repository.fail_item(item["id"], owner, str(error))
            finally:
                renew_done.set()
                renewer.join(timeout=2)

    def _renew_loop(self, item_id: str, owner: str, done: threading.Event) -> None:
        interval = min(30.0, max(1.0, self.lease_seconds / 3))
        while not self._stop.is_set() and not done.wait(interval):
            if not self.repository.renew_lease(item_id, owner, self.lease_seconds):
                return

    def _slot(self, slot_id: str):
        return next(
            (slot for slot in self.slot_provider() if slot.get("slot_id") == slot_id),
            None,
        )
