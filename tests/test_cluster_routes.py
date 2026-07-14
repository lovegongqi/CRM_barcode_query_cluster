from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cluster.catalog import CatalogRepository
from cluster.crypto import CredentialCipher
from cluster.db import Database
from cluster.jobs import JobRepository
from cluster.migrations import MigrationRunner


@pytest.fixture()
def cluster_app(monkeypatch, test_database_url):
    import app as app_module

    database = Database(test_database_url, min_size=1, max_size=4)
    MigrationRunner(database).apply()
    database.execute("TRUNCATE jobs, cluster_nodes, runtime_config CASCADE")
    catalog = CatalogRepository(
        database,
        CredentialCipher(CredentialCipher.generate_key()),
    )
    jobs = JobRepository(database)
    services = SimpleNamespace(
        database=database,
        catalog=catalog,
        jobs=jobs,
        objects=Mock(),
    )
    monkeypatch.setattr(app_module, "IS_DESKTOP_APP", True)
    monkeypatch.setattr(
        app_module,
        "SERVER_CLUSTER_CONFIG",
        SimpleNamespace(enabled=True, node_id="hk-1", node_name="香港", node_role="web"),
    )
    monkeypatch.setattr(app_module, "CLUSTER_SERVICES", services)
    monkeypatch.setattr(app_module, "CLUSTER_SCHEDULER", Mock())
    heartbeat_thread = Mock()
    heartbeat_thread.is_alive.return_value = True
    monkeypatch.setattr(app_module, "CLUSTER_HEARTBEAT_THREAD", heartbeat_thread)
    app_module.app.config.update(TESTING=True)
    yield app_module, services
    database.close()


def test_job_status_survives_new_web_client(cluster_app):
    app_module, _services = cluster_app
    started = app_module.app.test_client().post(
        "/api/crm/batch/start",
        json={"barcodes": ["5312503010858"], "retry_limit": 5},
    ).get_json()

    status = app_module.app.test_client().get(
        f"/api/crm/batch/status?job_id={started['job_id']}"
    ).get_json()

    assert started["success"] is True
    assert status["job_id"] == started["job_id"]
    assert status["total"] == 1
    assert status["running"] is True


def test_node_config_updates_only_target(cluster_app):
    app_module, services = cluster_app
    for node_id, node_name in (("hk-1", "香港"), ("sg-1", "新加坡")):
        services.catalog.heartbeat_node(
            {
                "node_id": node_id,
                "node_name": node_name,
                "node_role": "web",
                "query_workers": 5,
                "transfer_workers": 2,
            }
        )
        services.catalog.set_runtime_config(
            f"node:{node_id}",
            {"query_workers": 5, "transfer_workers": 2},
        )

    response = app_module.app.test_client().post(
        "/api/cluster/nodes/sg-1/runtime-config",
        json={"query_workers": 6, "transfer_workers": 3},
    )

    assert response.status_code == 200
    assert services.catalog.get_runtime_config("node:sg-1")["query_workers"] == 6
    assert services.catalog.get_runtime_config("node:hk-1")["query_workers"] == 5


def test_readyz_checks_shared_database(cluster_app):
    app_module, _services = cluster_app

    response = app_module.app.test_client().get("/readyz")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["checks"]["database"]["ok"] is True


def test_local_node_and_slots_publish_heartbeats(cluster_app):
    app_module, services = cluster_app

    app_module._cluster_heartbeat_once()

    nodes = services.catalog.list_nodes()
    slots = services.catalog.list_slots("hk-1")
    assert nodes[0]["node_id"] == "hk-1"
    assert len(slots) == len(app_module.crm_pool.query_slots) + len(app_module.crm_pool.transfer_slots)


def test_service_close_creates_one_shared_item_per_order(cluster_app, monkeypatch):
    app_module, services = cluster_app
    monkeypatch.setattr(
        app_module,
        "selected_latest_service_orders",
        lambda _barcodes: {
            "orders": [
                {
                    "service_no": "FWD1",
                    "barcodes": ["5312503010858", "5322503310162"],
                    "customer_names": ["客户A"],
                }
            ],
            "missing": [],
            "no_service": [],
        },
    )

    payload = app_module.app.test_client().post(
        "/api/service-close/start",
        json={"barcodes": ["5312503010858", "5322503310162"]},
    ).get_json()

    status = services.jobs.status(payload["job_id"])
    assert payload["success"] is True
    assert status["total"] == 1
    assert status["items"][0]["kind"] == "service_close"
    assert status["items"][0]["payload_json"]["service_order"]["service_no"] == "FWD1"


def test_transfer_submission_becomes_shared_job(cluster_app, monkeypatch):
    app_module, services = cluster_app
    summary = {
        "groups": [{"product_name": "EWD600S", "product_code": "906020907", "quantity": 1}],
        "barcodes": ["5312503010858"],
        "missing": [],
        "incomplete": [],
        "warnings": [],
    }
    monkeypatch.setattr(app_module, "_missing_product_library_representatives", lambda _barcodes: {})
    monkeypatch.setattr(app_module, "build_transfer_summary", lambda *_args: dict(summary))
    monkeypatch.setattr(app_module, "_exclude_unmatched_transfer_barcodes", lambda _summary: None)

    payload = app_module.app.test_client().post(
        "/api/crm/transfer",
        json={
            "barcodes": ["5312503010858"],
            "distributor": "南昌怡口净水",
            "transfer_type": "移出",
            "remark": "test",
        },
    ).get_json()

    status = services.jobs.status(payload["job_id"])
    assert payload["started"] is True
    assert status["items"][0]["kind"] == "transfer"
    assert status["items"][0]["payload_json"]["distributor"] == "南昌怡口净水"


def test_product_library_lookup_uses_durable_job(cluster_app):
    app_module, services = cluster_app

    started = app_module.app.test_client().post(
        "/api/product-library/query/start",
        json={"barcode": "5312503010858"},
    ).get_json()
    status = app_module.app.test_client().get(
        f"/api/product-library/query/status?job_id={started['job_id']}"
    ).get_json()

    assert started["success"] is True
    assert status["job_id"] == started["job_id"]
    assert status["barcode"] == "5312503010858"
    assert services.jobs.status(started["job_id"])["items"][0]["kind"] == "library_lookup"


def test_cluster_nodes_include_shared_slot_status(cluster_app):
    app_module, services = cluster_app
    services.catalog.heartbeat_node(
        {
            "node_id": "sg-1",
            "node_name": "新加坡",
            "node_role": "web",
            "public_url": "https://sg.example.test",
            "query_workers": 1,
            "transfer_workers": 1,
            "database_role": "replica",
        }
    )
    services.catalog.replace_slots(
        "sg-1",
        [
            {"slot_id": "query-1", "kind": "query", "logged_in": True},
            {"slot_id": "transfer-1", "kind": "transfer", "logged_in": False},
        ],
    )

    payload = app_module.app.test_client().get("/api/cluster/nodes").get_json()
    node = next(row for row in payload["nodes"] if row["id"] == "sg-1")

    assert node["online"] is True
    assert node["database_role"] == "replica"
    assert node["status"]["crm"]["query_logged_in"] == 1
    assert node["status"]["crm"]["transfer_total"] == 1


def test_crm_slots_can_return_all_cluster_nodes(cluster_app):
    app_module, services = cluster_app
    for node_id, node_name in (("hk-1", "香港"), ("sg-1", "新加坡")):
        services.catalog.heartbeat_node(
            {
                "node_id": node_id,
                "node_name": node_name,
                "node_role": "worker",
                "public_url": f"https://{node_id}.example.test",
                "query_workers": 1,
                "transfer_workers": 1,
            }
        )
        services.catalog.replace_slots(
            node_id,
            [
                {"slot_id": "query-1", "kind": "query", "logged_in": node_id == "sg-1"},
                {"slot_id": "transfer-1", "kind": "transfer", "logged_in": False},
            ],
        )

    payload = app_module.app.test_client().get(
        "/api/crm/slots?scope=cluster"
    ).get_json()

    assert {row["id"] for row in payload["query"]} == {
        "hk-1:query-1",
        "sg-1:query-1",
    }
    sg_slot = next(row for row in payload["query"] if row["node_id"] == "sg-1")
    assert sg_slot["label"] == "新加坡 · 查询1"
    assert sg_slot["logged_in"] is True


def test_cluster_bulk_login_starts_every_online_node(cluster_app, monkeypatch):
    app_module, services = cluster_app
    monkeypatch.setattr(app_module, "_cluster_admin_token", lambda: "shared-token")
    for node_id, node_name in (("hk-1", "香港"), ("sg-1", "新加坡")):
        services.catalog.heartbeat_node(
            {
                "node_id": node_id,
                "node_name": node_name,
                "node_role": "worker",
                "public_url": f"https://{node_id}.example.test",
                "query_workers": 1,
                "transfer_workers": 1,
            }
        )
    services.catalog.heartbeat_node(
        {
            "node_id": "offline-1",
            "node_name": "离线节点",
            "node_role": "worker",
            "public_url": "https://offline.example.test",
            "query_workers": 1,
            "transfer_workers": 1,
        }
    )
    services.database.execute(
        "UPDATE cluster_nodes SET expires_at = now() - interval '1 minute' WHERE node_id = %s",
        ("offline-1",),
    )

    calls = []

    def fake_node_call(node, action, payload=None):
        calls.append((node["id"], action))
        return {
            "success": True,
            "job_id": f"local-{node['id']}",
            "running": True,
            "done": False,
            "waiting_captcha": True,
            "slots": [
                {
                    "id": "query-1",
                    "kind": "query",
                    "label": "查询1",
                    "status": "waiting_captcha",
                    "message": "等待验证码",
                }
            ],
            "logs": [],
        }

    monkeypatch.setattr(app_module, "_cluster_bulk_login_node_call", fake_node_call)

    payload = app_module.app.test_client().post(
        "/api/crm/bulk-login/start",
        json={"scope": "all", "username": "crm-user", "password": "crm-password"},
    ).get_json()
    second = app_module.app.test_client().post(
        "/api/crm/bulk-login/start",
        json={"scope": "all", "username": "crm-user", "password": "crm-password"},
    ).get_json()

    assert payload["success"] is True
    assert second["job_id"] == payload["job_id"]
    assert {node_id for node_id, action in calls if action == "start"} == {"hk-1", "sg-1"}
    state = services.catalog.get_runtime_config(f"cluster-bulk-login:{payload['job_id']}")
    assert set(state["nodes"]) == {"hk-1", "sg-1", "offline-1"}
    assert state["nodes"]["hk-1"]["job_id"] == "local-hk-1"
    assert "离线节点" in payload["error"]
    assert payload["login_success"] is False


def test_cluster_bulk_login_captcha_is_sent_to_every_node(cluster_app, monkeypatch):
    app_module, services = cluster_app
    monkeypatch.setattr(app_module, "_cluster_admin_token", lambda: "shared-token")
    for node_id, node_name in (("hk-1", "香港"), ("sg-1", "新加坡")):
        services.catalog.heartbeat_node(
            {
                "node_id": node_id,
                "node_name": node_name,
                "node_role": "worker",
                "public_url": f"https://{node_id}.example.test",
                "query_workers": 1,
                "transfer_workers": 1,
            }
        )

    calls = []

    def fake_node_call(node, action, payload=None):
        calls.append((node["id"], action, dict(payload or {})))
        return {
            "success": True,
            "job_id": (payload or {}).get("job_id") or f"local-{node['id']}",
            "running": action != "captcha",
            "done": action == "captcha",
            "waiting_captcha": action != "captcha",
            "slots": [],
            "logs": [],
        }

    monkeypatch.setattr(app_module, "_cluster_bulk_login_node_call", fake_node_call)
    client = app_module.app.test_client()
    started = client.post(
        "/api/crm/bulk-login/start",
        json={"scope": "all", "username": "crm-user", "password": "crm-password"},
    ).get_json()
    payload = client.post(
        "/api/crm/bulk-login/captcha",
        json={"scope": "all", "job_id": started["job_id"], "captcha": "1234"},
    ).get_json()

    captcha_calls = [row for row in calls if row[1] == "captcha"]
    assert {row[0] for row in captcha_calls} == {"hk-1", "sg-1"}
    assert all(row[2]["captcha"] == "1234" for row in captcha_calls)
    assert payload["done"] is True


def test_cluster_bulk_login_status_merges_node_logs(cluster_app, monkeypatch):
    app_module, services = cluster_app
    monkeypatch.setattr(app_module, "_cluster_admin_token", lambda: "shared-token")
    for node_id, node_name in (("hk-1", "香港"), ("sg-1", "新加坡")):
        services.catalog.heartbeat_node(
            {
                "node_id": node_id,
                "node_name": node_name,
                "node_role": "worker",
                "public_url": f"https://{node_id}.example.test",
                "query_workers": 1,
                "transfer_workers": 1,
            }
        )

    def fake_node_call(node, action, payload=None):
        if action == "start":
            return {
                "success": True,
                "job_id": f"local-{node['id']}",
                "running": True,
                "done": False,
                "waiting_captcha": True,
                "slots": [],
                "logs": [],
            }
        return {
            "success": True,
            "job_id": (payload or {}).get("job_id"),
            "running": True,
            "done": False,
            "waiting_captcha": True,
            "slots": [
                {
                    "id": "query-1",
                    "kind": "query",
                    "label": "查询1",
                    "status": "waiting_captcha",
                    "message": "等待验证码",
                }
            ],
            "logs": [
                {"id": 1, "time": "10:00:00", "level": "info", "message": "已进入验证码步骤"}
            ],
        }

    monkeypatch.setattr(app_module, "_cluster_bulk_login_node_call", fake_node_call)
    client = app_module.app.test_client()
    started = client.post(
        "/api/crm/bulk-login/start",
        json={"scope": "all", "username": "crm-user", "password": "crm-password"},
    ).get_json()

    status = client.get(
        f"/api/crm/bulk-login/status?scope=all&job_id={started['job_id']}"
    ).get_json()

    assert {slot["id"] for slot in status["slots"]} == {"hk-1:query-1", "sg-1:query-1"}
    assert {slot["label"] for slot in status["slots"]} == {"香港 · 查询1", "新加坡 · 查询1"}
    assert [row["id"] for row in status["logs"]] == [1, 2]
    assert status["logs"][0]["message"].startswith("[香港]")
    assert status["logs"][1]["message"].startswith("[新加坡]")


def test_internal_bulk_login_requires_cluster_token(cluster_app, monkeypatch):
    app_module, _services = cluster_app
    monkeypatch.setattr(app_module, "IS_DESKTOP_APP", False)
    monkeypatch.setattr(app_module, "_cluster_admin_token", lambda: "shared-token")
    monkeypatch.setattr(
        app_module,
        "_start_local_bulk_login",
        lambda _data: {"success": True, "job_id": "local-job"},
        raising=False,
    )
    client = app_module.app.test_client()

    denied = client.post("/api/internal/crm/bulk-login/start", json={})
    allowed = client.post(
        "/api/internal/crm/bulk-login/start",
        json={},
        headers={"X-CRM-Cluster-Token": "shared-token"},
    )
    encrypted_body = app_module._encode_cluster_request_payload(
        {},
        "hk-1",
        "/api/internal/crm/bulk-login/start",
        "test-request",
    )
    encrypted = client.post(
        "/api/internal/crm/bulk-login/start",
        data=encrypted_body,
        headers={
            "Content-Type": "application/json",
            "X-CRM-Cluster-Encrypted": "1",
        },
    )
    replayed = client.post(
        "/api/internal/crm/bulk-login/start",
        data=encrypted_body,
        headers={
            "Content-Type": "application/json",
            "X-CRM-Cluster-Encrypted": "1",
        },
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.get_json()["job_id"] == "local-job"
    assert encrypted.status_code == 200
    assert app_module._decode_cluster_response(encrypted.data, "test-request")["job_id"] == "local-job"
    assert replayed.status_code == 403


def test_internal_bulk_login_still_requires_token_in_desktop_mode(cluster_app, monkeypatch):
    app_module, _services = cluster_app
    monkeypatch.setattr(app_module, "IS_DESKTOP_APP", True)
    monkeypatch.setattr(app_module, "_cluster_admin_token", lambda: "shared-token")

    response = app_module.app.test_client().post(
        "/api/internal/crm/bulk-login/start",
        json={},
    )

    assert response.status_code == 403


def test_cluster_internal_payload_is_encrypted(cluster_app, monkeypatch):
    app_module, _services = cluster_app
    monkeypatch.setattr(app_module, "_cluster_admin_token", lambda: "shared-token")
    payload = {"username": "crm-user", "password": "crm-password"}

    encoded = app_module._encode_cluster_payload(payload)
    decoded = app_module._decode_cluster_payload(encoded)

    assert decoded == payload
    assert "crm-password" not in encoded.decode("utf-8")
    assert "shared-token" not in encoded.decode("utf-8")


def test_remote_bulk_login_sends_only_encrypted_credentials(cluster_app, monkeypatch):
    app_module, _services = cluster_app
    monkeypatch.setattr(app_module, "_cluster_admin_token", lambda: "shared-token")
    captured = {}

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.body

    def fake_urlopen(req, timeout):
        captured["request"] = req
        captured["timeout"] = timeout
        request_payload = app_module._decode_cluster_payload(req.data)
        request_id = request_payload["_cluster_request"]["request_id"]
        return Response(
            app_module._encode_cluster_response_payload(
                {"success": True, "job_id": "remote-job"},
                request_id,
            )
        )

    monkeypatch.setattr(app_module.urlrequest, "urlopen", fake_urlopen)
    result = app_module._cluster_bulk_login_node_call(
        {"id": "sg-1", "url": "http://sg.example.test"},
        "start",
        {"username": "crm-user", "password": "crm-password"},
    )

    request_body = captured["request"].data
    assert result["job_id"] == "remote-job"
    assert captured["timeout"] == 5
    assert b"crm-password" not in request_body
    assert captured["request"].headers["X-crm-cluster-encrypted"] == "1"
    assert "X-crm-cluster-token" not in captured["request"].headers
    assert app_module._decode_cluster_payload(request_body)["password"] == "crm-password"


def test_transient_cluster_status_error_does_not_finish_job(cluster_app):
    app_module, _services = cluster_app
    state = {
        "job_id": "cluster-job",
        "scope": "all",
        "log_seq": 0,
        "logs": [],
        "nodes": {
            "sg-1": {
                "id": "sg-1",
                "name": "新加坡",
                "url": "http://sg.example.test",
                "job_id": "local-job",
                "error": "",
                "error_count": 0,
                "last_status": {
                    "running": True,
                    "done": False,
                    "waiting_captcha": True,
                    "slots": [],
                },
            }
        },
    }
    failed = ({"id": "sg-1", "name": "新加坡"}, {"success": False, "error": "timeout"})

    app_module._cluster_bulk_login_merge_results(state, [failed])
    first = app_module._cluster_bulk_login_payload(state)
    app_module._cluster_bulk_login_merge_results(state, [failed])
    app_module._cluster_bulk_login_merge_results(state, [failed])
    third = app_module._cluster_bulk_login_payload(state)

    assert first["running"] is True
    assert first["done"] is False
    assert third["running"] is False
    assert third["done"] is True
    assert third["login_success"] is False


def test_local_bulk_login_request_id_is_idempotent(cluster_app, monkeypatch):
    app_module, _services = cluster_app
    monkeypatch.setattr(app_module, "_bulk_login_slots_for_scope", lambda _scope: ("all", []))
    with app_module.bulk_login_job_lock:
        app_module.bulk_login_jobs.clear()
        app_module.latest_bulk_login_job_by_scope.clear()

    first = app_module._start_local_bulk_login(
        {
            "scope": "all",
            "username": "crm-user",
            "password": "crm-password",
            "requested_job_id": "cluster-job:hk-1",
        }
    )
    second = app_module._start_local_bulk_login(
        {
            "scope": "all",
            "username": "crm-user",
            "password": "crm-password",
            "requested_job_id": "cluster-job:hk-1",
        }
    )

    assert first["job_id"] == "cluster-job:hk-1"
    assert second["job_id"] == first["job_id"]
    assert len(app_module.bulk_login_jobs) == 1


def test_local_bulk_login_rejects_overlapping_scope(cluster_app, monkeypatch):
    app_module, _services = cluster_app
    query_slot = {"id": "query-1", "kind": "query", "label": "查询1"}
    running = app_module._empty_bulk_login_job("query", [query_slot])
    running["running"] = True
    with app_module.bulk_login_job_lock:
        app_module.bulk_login_jobs.clear()
        app_module.latest_bulk_login_job_by_scope.clear()
        app_module.bulk_login_jobs[running["job_id"]] = running
        app_module.latest_bulk_login_job_by_scope["query"] = running["job_id"]
    monkeypatch.setattr(
        app_module,
        "_bulk_login_slots_for_scope",
        lambda _scope: ("all", [query_slot]),
    )

    payload = app_module._start_local_bulk_login(
        {"scope": "all", "username": "crm-user", "password": "crm-password"}
    )

    assert payload["success"] is False
    assert "正在登录" in payload["error"]


def test_needs_review_endpoint_lists_uncertain_submission(cluster_app):
    app_module, services = cluster_app
    job = services.jobs.create_job(
        "transfer",
        [{"item_key": "transfer", "barcodes": ["5312503010858"]}],
        {},
        "admin",
        "transfer-review",
    )
    item = services.jobs.claim_item(["transfer"], "hk-1:transfer-1", 120)
    services.jobs.start_item(item["id"], "hk-1:transfer-1")
    services.jobs.mark_submitted(item["id"], "hk-1:transfer-1", "TRSF1")
    services.jobs.fail_item(item["id"], "hk-1:transfer-1", "提交后连接中断")

    payload = app_module.app.test_client().get("/api/cluster/needs-review").get_json()

    assert payload["success"] is True
    assert payload["items"][0]["job_id"] == job["id"]
    assert payload["items"][0]["external_ref"] == "TRSF1"


def test_service_close_handler_syncs_successful_barcodes(cluster_app, monkeypatch):
    app_module, services = cluster_app
    recorded = []
    monkeypatch.setattr(
        app_module,
        "_record_service_closed_for_barcodes",
        lambda service_no, barcodes: recorded.append((service_no, list(barcodes))),
    )
    worker = Mock(slot_id="query-1")
    worker.close_service_orders.return_value = (
        True,
        {
            "results": [
                {"service_no": "FWD1", "success": True, "status": "closed"}
            ]
        },
    )
    job = services.jobs.create_job(
        "service_close",
        [
            {
                "item_key": "FWD1",
                "kind": "service_close",
                "service_order": {"service_no": "FWD1", "barcodes": ["5312503010858"]},
            }
        ],
        {},
        "admin",
        "close-sync",
    )
    item = services.jobs.claim_item(["service_close"], "hk-1:query-1", 120)

    result = app_module._handle_cluster_service_close_item(worker, item, services.jobs)

    assert result["results"][0]["success"] is True
    assert recorded == [("FWD1", ["5312503010858"])]


def test_transfer_summary_uses_shared_query_job(cluster_app):
    app_module, services = cluster_app

    payload = app_module.app.test_client().post(
        "/api/transfer/summary/start",
        json={
            "barcodes": ["5312503010858"],
            "distributor": "南昌怡口净水",
            "transfer_type": "移出",
        },
    ).get_json()
    status = services.jobs.status(payload["job_id"])

    assert payload["success"] is True
    assert status["items"][0]["kind"] == "transfer_summary"
    assert status["items"][0]["payload_json"]["barcodes"] == ["5312503010858"]
