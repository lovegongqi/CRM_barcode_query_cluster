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
