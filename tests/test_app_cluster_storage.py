from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cluster.objects import ObjectRecord
from cluster.services import ClusterServices


@pytest.fixture()
def cluster_client(monkeypatch):
    import app as app_module

    catalog = Mock()
    objects = Mock()
    services = SimpleNamespace(
        catalog=catalog,
        objects=objects,
        publish_barcode_result=Mock(),
    )
    monkeypatch.setattr(app_module, "IS_DESKTOP_APP", True)
    monkeypatch.setattr(
        app_module,
        "SERVER_CLUSTER_CONFIG",
        SimpleNamespace(enabled=True, node_id="hk-1"),
    )
    monkeypatch.setattr(app_module, "CLUSTER_SERVICES", services)
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client(), catalog, objects


def test_results_are_read_from_postgres_without_local_html(cluster_client):
    client, catalog, _objects = cluster_client
    catalog.list_barcodes.return_value = [
        {
            "barcode": "5312503010858",
            "fields": {"设备档案": [{"产品名称": "EWD600S"}]},
            "updated_at": "2026-07-13T10:00:00+00:00",
            "archived": False,
            "current_dealer_override": "",
            "transfer_updated_at": None,
            "query_slot_id": "hk-1:query-1",
            "metadata": {},
            "remark": "",
        }
    ]

    payload = client.get("/api/barcodes").get_json()

    assert payload["barcodes"][0]["barcode"] == "5312503010858"
    assert payload["barcodes"][0]["fields"]["设备档案"][0]["产品名称"] == "EWD600S"


def test_detail_streams_r2_object(cluster_client):
    client, catalog, objects = cluster_client
    catalog.get_barcode.return_value = {
        "barcode": "5312503010858",
        "object_key": "results/5312503010858.html",
        "metadata": {},
        "current_dealer_override": "",
    }
    objects.get_bytes.return_value = b"<html>detail</html>"

    response = client.get("/barcode/5312503010858.html")

    assert response.status_code == 200
    assert response.data == b"<html>detail</html>"
    objects.get_bytes.assert_called_once_with("results/5312503010858.html")


def test_product_library_is_read_from_postgres(cluster_client):
    client, catalog, _objects = cluster_client
    catalog.list_product_rules.return_value = [
        {
            "prefix": "531",
            "product_code": "906020907",
            "product_name": "EWD600S",
            "source_barcode": "5312503010858",
            "updated_at": "2026-07-13T10:00:00+00:00",
        }
    ]

    payload = client.get("/api/product-library").get_json()

    assert payload["products"][0]["prefix"] == "531"


def test_runtime_config_is_read_from_postgres(cluster_client):
    client, catalog, _objects = cluster_client
    catalog.get_runtime_config.return_value = {
        "query_workers": 5,
        "transfer_workers": 2,
        "own_dealer_name": "测试省代",
        "frozen_warehouse_name": "测试冻结仓",
        "frozen_warehouse_save_only": True,
    }

    payload = client.get("/api/runtime-config").get_json()

    assert payload["config"]["query_workers"] == 5
    assert payload["config"]["own_dealer_name"] == "测试省代"


def test_app_login_uses_catalog_authentication(cluster_client):
    client, catalog, _objects = cluster_client
    catalog.authenticate_account.return_value = {
        "id": "admin",
        "username": "admin",
        "display_name": "管理员",
        "permissions": ["crm"],
        "is_admin": True,
        "updated_at": "",
    }

    payload = client.post(
        "/api/app-auth/login",
        json={"username": "admin", "password": "secret"},
    ).get_json()

    assert payload["success"] is True
    catalog.authenticate_account.assert_called_once_with("admin", "secret")


def test_delete_removes_r2_object_and_catalog_row(cluster_client):
    client, catalog, objects = cluster_client
    catalog.get_barcode.return_value = {
        "barcode": "5312503010858",
        "object_key": "results/5312503010858.html",
    }
    catalog.delete_barcode.return_value = True

    payload = client.delete("/api/barcodes/5312503010858").get_json()

    assert payload["success"] is True
    objects.delete.assert_called_once_with("results/5312503010858.html")
    catalog.delete_barcode.assert_called_once_with("5312503010858")


def test_result_is_uploaded_before_catalog_publication(tmp_path):
    source = tmp_path / "5312503010858.html"
    source.write_text("<html>detail</html>", encoding="utf-8")
    calls = []
    catalog = Mock()
    objects = Mock()
    objects.put_file.side_effect = lambda *args: (
        calls.append("upload")
        or ObjectRecord(
            object_key="results/5312503010858.html",
            category="results",
            sha256="abc",
            size_bytes=19,
            content_type="text/html",
        )
    )
    catalog.upsert_barcode.side_effect = lambda record: calls.append("catalog")
    services = ClusterServices(
        config=SimpleNamespace(),
        database=Mock(),
        catalog=catalog,
        objects=objects,
    )

    services.publish_barcode_result(
        "5312503010858",
        source,
        {"设备档案": []},
        {"product_name": "EWD600S", "product_code": "906020907"},
    )

    assert calls == ["upload", "catalog"]
    published = catalog.upsert_barcode.call_args.args[0]
    assert published["barcode"] == "5312503010858"
    assert published["object_key"] == "results/5312503010858.html"
    assert published["object_sha256"] == "abc"


def test_requery_publication_preserves_manual_metadata(cluster_client, monkeypatch, tmp_path):
    import app as app_module

    _client, catalog, _objects = cluster_client
    barcode = "5312503010858"
    (tmp_path / f"{barcode}.html").write_text("<html>detail</html>", encoding="utf-8")
    monkeypatch.setattr(app_module, "BARCODE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "extract_fields_from_html", lambda _path: {"sr1": []})
    monkeypatch.setattr(
        app_module,
        "_barcode_product_info",
        lambda _item: {"product_name": "EWD600S", "product_code": "906020907"},
    )
    catalog.get_barcode.return_value = {
        "metadata": {"custom": "keep"},
        "remark": "人工备注",
        "archived": True,
        "archive_time": "2026-07-01T10:00:00+00:00",
        "current_dealer_override": "修正经销商",
        "transfer_updated_at": "2026-07-02T10:00:00+00:00",
        "service_closed": True,
        "latest_service_order": "FWD1",
    }

    success, error = app_module.publish_cluster_query_result(barcode, "query-1")

    assert success is True, error
    published = app_module.CLUSTER_SERVICES.publish_barcode_result.call_args.args[3]
    assert published["remark"] == "人工备注"
    assert published["archive_time"] == "2026-07-01T10:00:00+00:00"
    assert published["current_dealer_override"] == "修正经销商"
    assert published["transfer_updated_at"] == "2026-07-02T10:00:00+00:00"
    assert published["service_closed"] is True
    assert published["latest_service_order"] == "FWD1"
    assert published["metadata"]["custom"] == "keep"
