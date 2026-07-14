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

    assert "hostssl postgres postgres 0.0.0.0/0 scram-sha-256 clientcert=verify-ca" in hba
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


def test_patroni_ctl_uses_client_certificate_for_peer_requests(rendered):
    patroni = Path(rendered["hk"]["patroni"]).read_text(encoding="utf-8")

    assert "ctl:" in patroni
    assert "cacert: /run/cluster-secrets/ca.crt" in patroni
    assert "certfile: /run/cluster-secrets/patroni-client.crt" in patroni
    assert "keyfile: /run/cluster-secrets/patroni-client.key" in patroni


def test_patroni_roles_and_synchronous_policy(rendered):
    hk = Path(rendered["hk"]["patroni"]).read_text(encoding="utf-8")
    sg = Path(rendered["sg"]["patroni"]).read_text(encoding="utf-8")
    us = Path(rendered["us"]["patroni"]).read_text(encoding="utf-8")
    nas = Path(rendered["nas"]["patroni"]).read_text(encoding="utf-8")

    assert "synchronous_mode: true" in hk
    assert "synchronous_mode_strict: false" in hk
    assert "nofailover:" not in hk
    assert "failover_priority: 90" in hk
    assert "failover_priority: 100" in sg
    assert "failover_priority: 50" in us
    assert "nofailover:" not in nas
    assert "failover_priority: 0" in nas


def test_patroni_etcd_hosts_use_protocol_field(rendered):
    patroni = Path(rendered["hk"]["patroni"]).read_text(encoding="utf-8")

    assert "protocol: https" in patroni
    assert "hosts: hk.mlmll.cn:2379,sg.mlmll.cn:2379,us.mlmll.cn:2379" in patroni
    assert "hosts: https://" not in patroni


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
    assert "server hk hk.mlmll.cn:15432" in haproxy


def test_haproxy_routes_each_nodes_local_database_over_docker_network(rendered):
    hosts = {
        "hk": "hk.mlmll.cn",
        "sg": "sg.mlmll.cn",
        "us": "us.mlmll.cn",
        "nas": "mlmll.cn",
    }

    for node_id, rendered_paths in rendered.items():
        haproxy = Path(rendered_paths["haproxy"]).read_text(encoding="utf-8")
        local_line = next(
            line for line in haproxy.splitlines()
            if line.strip().startswith(f"server {node_id} ")
        )
        assert f"server {node_id} patroni:5432" in local_line
        assert "resolvers public_dns" not in local_line
        for remote_id, remote_host in hosts.items():
            if remote_id != node_id:
                remote_line = next(
                    line for line in haproxy.splitlines()
                    if line.strip().startswith(f"server {remote_id} ")
                )
                assert f"server {remote_id} {remote_host}:15432" in remote_line
                assert "resolvers public_dns" in remote_line


def test_patroni_advertises_non_conflicting_public_postgres_port(rendered):
    patroni = Path(rendered["hk"]["patroni"]).read_text(encoding="utf-8")

    assert "listen: 0.0.0.0:5432" in patroni
    assert "connect_address: hk.mlmll.cn:15432" in patroni
