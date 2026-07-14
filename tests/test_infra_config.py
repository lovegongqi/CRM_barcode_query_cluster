from pathlib import Path

import pytest

from infra.render_node_config import render_node


@pytest.fixture()
def rendered(tmp_path):
    return {
        node_id: render_node(node_id, tmp_path / node_id)
        for node_id in ("hk", "sg", "us", "nas")
    }


def test_postgres_rejects_non_tls(rendered):
    hba = Path(rendered["hk"]["pg_hba"]).read_text(encoding="utf-8")

    assert "hostssl crm_barcode crm_app 0.0.0.0/0 scram-sha-256 clientcert=verify-ca" in hba
    assert "hostssl replication crm_replica 0.0.0.0/0 scram-sha-256 clientcert=verify-ca" in hba
    assert "host all all 0.0.0.0/0 reject" in hba


def test_every_public_control_service_requires_client_certificates(rendered):
    etcd = Path(rendered["hk"]["etcd_env"]).read_text(encoding="utf-8")
    patroni = Path(rendered["hk"]["patroni"]).read_text(encoding="utf-8")
    haproxy = Path(rendered["hk"]["haproxy"]).read_text(encoding="utf-8")

    assert "ETCD_CLIENT_CERT_AUTH=true" in etcd
    assert "ETCD_PEER_CLIENT_CERT_AUTH=true" in etcd
    assert "verify_client: required" in patroni
    assert "verify required" in haproxy


def test_patroni_roles_and_synchronous_policy(rendered):
    hk = Path(rendered["hk"]["patroni"]).read_text(encoding="utf-8")
    sg = Path(rendered["sg"]["patroni"]).read_text(encoding="utf-8")
    us = Path(rendered["us"]["patroni"]).read_text(encoding="utf-8")
    nas = Path(rendered["nas"]["patroni"]).read_text(encoding="utf-8")

    assert "synchronous_mode: true" in hk
    assert "synchronous_mode_strict: false" in hk
    assert "nofailover: false" in hk
    assert "failover_priority: 100" in sg
    assert "failover_priority: 50" in us
    assert "nofailover: true" in nas
    assert "failover_priority: 0" in nas


def test_etcd_quorum_exists_only_on_three_cloud_nodes(rendered):
    for node_id in ("hk", "sg", "us"):
        assert Path(rendered[node_id]["etcd_env"]).exists()
    assert rendered["nas"]["etcd_env"] is None


def test_haproxy_uses_tls_patroni_health_checks(rendered):
    haproxy = Path(rendered["sg"]["haproxy"]).read_text(encoding="utf-8")

    assert "bind 0.0.0.0:5433" in haproxy
    assert "option httpchk GET /primary" in haproxy
    assert "check-ssl" in haproxy
    assert "ca-file /run/secrets/ca.crt" in haproxy
    assert "crt /run/secrets/haproxy-client.pem" in haproxy
