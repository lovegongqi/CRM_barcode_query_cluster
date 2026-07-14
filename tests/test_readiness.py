import threading
import time
from types import SimpleNamespace


def test_database_readiness_check_is_single_flight(monkeypatch):
    import app as app_module

    started = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingDatabase:
        def fetch_one(self, _sql):
            calls.append(True)
            started.set()
            release.wait(timeout=2)
            return {"in_recovery": False, "checked_at": "now"}

    services = SimpleNamespace(database=BlockingDatabase())
    monkeypatch.setattr(
        app_module,
        "DATABASE_READINESS_STATE",
        {"checked_at": 0.0, "result": None},
    )

    first_result = []
    thread = threading.Thread(
        target=lambda: first_result.append(
            app_module._database_readiness_check(services)
        )
    )
    thread.start()
    assert started.wait(timeout=1)

    before = time.monotonic()
    concurrent = app_module._database_readiness_check(services)
    elapsed = time.monotonic() - before

    assert elapsed < 0.2
    assert concurrent["ok"] is False
    assert "进行中" in concurrent["message"]
    assert len(calls) == 1

    release.set()
    thread.join(timeout=2)
    assert first_result[0]["ok"] is True

    cached = app_module._database_readiness_check(services)
    assert cached["ok"] is True
    assert len(calls) == 1
