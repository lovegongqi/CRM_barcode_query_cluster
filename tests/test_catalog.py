import uuid

import pytest

from cluster.catalog import CatalogRepository
from cluster.crypto import CredentialCipher
from cluster.db import Database
from cluster.migrations import MigrationRunner


@pytest.fixture()
def catalog(test_database_url):
    database = Database(test_database_url, min_size=1, max_size=2)
    MigrationRunner(database).apply()
    repository = CatalogRepository(
        database,
        CredentialCipher(CredentialCipher.generate_key()),
    )
    yield repository
    database.close()


def test_account_password_is_hashed_and_not_returned(catalog):
    username = "admin-" + uuid.uuid4().hex
    catalog.replace_accounts(
        [
            {
                "id": username,
                "username": username,
                "display_name": "管理员",
                "password": "secret",
                "permissions": ["crm"],
                "is_admin": True,
            }
        ]
    )

    stored = catalog.db.fetch_one(
        "SELECT password_hash FROM app_accounts WHERE username = %s",
        (username,),
    )
    public = catalog.list_accounts()[0]

    assert stored["password_hash"] != "secret"
    assert catalog.authenticate_account(username, "secret")["username"] == username
    assert "password_hash" not in public
    assert "password" not in public


def test_runtime_product_distributor_and_barcode_round_trip(catalog):
    suffix = uuid.uuid4().hex
    barcode = "531" + suffix[:10]
    prefix = barcode[:-10]
    distributor = "测试分销商-" + suffix

    catalog.set_runtime_config("node:hk-1", {"query_workers": 5})
    catalog.upsert_product_rule(prefix, "906020907", "EWD700S", barcode)
    catalog.upsert_distributors([distributor])
    catalog.upsert_barcode(
        {
            "barcode": barcode,
            "fields": {"设备档案": [{"所属经销商": distributor}]},
            "product_name": "EWD700S",
            "product_code": "906020907",
            "current_dealer": distributor,
        }
    )

    assert catalog.get_runtime_config("node:hk-1") == {"query_workers": 5}
    assert catalog.get_product_rule(prefix)["product_code"] == "906020907"
    assert catalog.list_distributors()[0]["name"] == distributor
    assert catalog.get_barcode(barcode)["fields"]["设备档案"][0]["所属经销商"] == distributor


def test_crm_credentials_are_encrypted_at_rest(catalog):
    owner_key = "owner-" + uuid.uuid4().hex

    catalog.save_credentials(owner_key, True, "gongqi", "plain-password")

    stored = catalog.db.fetch_one(
        "SELECT password_ciphertext FROM crm_credentials WHERE owner_key = %s",
        (owner_key,),
    )
    public = catalog.get_credentials(owner_key)

    assert stored["password_ciphertext"] != "plain-password"
    assert public == {
        "remember": True,
        "username": "gongqi",
        "password": "plain-password",
    }


def test_node_and_slot_heartbeats_are_shared(catalog):
    node_id = "node-" + uuid.uuid4().hex
    catalog.heartbeat_node(
        {
            "node_id": node_id,
            "node_name": "香港",
            "node_role": "web",
            "public_url": "https://hk.example.com",
            "query_workers": 5,
            "transfer_workers": 2,
            "database_role": "leader",
        },
        ttl_seconds=180,
    )
    catalog.replace_slots(
        node_id,
        [
            {
                "slot_id": "query-1",
                "kind": "query",
                "logged_in": True,
                "busy": False,
            }
        ],
        ttl_seconds=180,
    )

    assert catalog.list_nodes()[0]["node_id"] == node_id
    assert catalog.list_slots(node_id)[0]["logged_in"] is True


def test_product_rule_replacement_and_distributor_deletion(catalog):
    suffix = uuid.uuid4().hex
    first_prefix = "a" + suffix[:5]
    second_prefix = "b" + suffix[:5]
    distributor = "停用分销商-" + suffix
    catalog.upsert_product_rule(first_prefix, "1", "旧型号")
    catalog.replace_product_rules(
        [
            {
                "prefix": second_prefix,
                "product_code": "2",
                "product_name": "新型号",
                "source_barcode": "",
            }
        ]
    )
    catalog.upsert_distributors([distributor])
    catalog.set_deleted_distributors([distributor])

    assert catalog.get_product_rule(first_prefix) is None
    assert catalog.get_product_rule(second_prefix)["product_name"] == "新型号"
    assert distributor not in {row["name"] for row in catalog.list_distributors()}
    deleted_row = next(
        row
        for row in catalog.list_distributors(include_deleted=True)
        if row["name"] == distributor
    )
    assert deleted_row["deleted"] is True


def test_metadata_update_preserves_barcode_fields(catalog):
    barcode = "metadata-" + uuid.uuid4().hex
    catalog.upsert_barcode(
        {
            "barcode": barcode,
            "fields": {"sr1": [{"name": "machine"}]},
            "product_name": "machine",
        }
    )

    catalog.update_barcode_metadata(
        barcode,
        {"remark": "checked", "archived": True, "archiveTime": "2026-07-13T10:00:00+00:00"},
    )

    row = catalog.get_barcode(barcode)
    assert row["fields"] == {"sr1": [{"name": "machine"}]}
    assert row["remark"] == "checked"
    assert row["archived"] is True
