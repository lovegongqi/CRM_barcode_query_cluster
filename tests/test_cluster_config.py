import pytest

from cluster.config import ClusterConfig


def test_desktop_never_enables_server_cluster(monkeypatch):
    monkeypatch.setenv("CRM_DESKTOP_APP", "1")
    monkeypatch.setenv("CRM_CLUSTER_MODE", "postgresql")

    assert ClusterConfig.from_env().enabled is False


def test_server_cluster_requires_database_and_r2(monkeypatch):
    monkeypatch.setenv("CRM_DESKTOP_APP", "0")
    monkeypatch.setenv("CRM_CLUSTER_MODE", "postgresql")
    for name in (
        "DATABASE_URL",
        "R2_ENDPOINT_URL",
        "R2_BUCKET",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "CRM_CREDENTIALS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        ClusterConfig.from_env().validate()


def test_server_cluster_accepts_complete_configuration():
    config = ClusterConfig.from_env({
        "CRM_CLUSTER_MODE": "postgresql",
        "CRM_DESKTOP_APP": "0",
        "CRM_NODE_ID": "hk-1",
        "CRM_NODE_NAME": "核云香港",
        "CRM_NODE_ROLE": "primary-web",
        "DATABASE_URL": "postgresql://crm@haproxy:5433/crm_barcode",
        "R2_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com",
        "R2_BUCKET": "crm-barcode-query",
        "R2_ACCESS_KEY_ID": "access",
        "R2_SECRET_ACCESS_KEY": "secret",
        "CRM_CREDENTIALS_KEY": "fernet-key",
    })

    config.validate()

    assert config.enabled is True
    assert config.node_id == "hk-1"
    assert config.node_name == "核云香港"
    assert config.node_role == "primary-web"

