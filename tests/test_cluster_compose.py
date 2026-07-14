from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_runs_shared_app_database_and_local_sessions():
    text = (ROOT / "deploy" / "compose.cluster.yml").read_text(encoding="utf-8")

    assert "CRM_CLUSTER_MODE: postgresql" in text
    assert "db.mlmll.cn" in text
    assert "sslmode=verify-full" in text
    assert "crm_browser_session:/app/session" in text
    assert "postgres_data:/var/lib/postgresql/data" in text
    assert 'profiles: ["etcd"]' in text
    assert 'profiles: ["nas"]' in text


def test_patroni_replication_uses_mutual_tls_credentials():
    text = (ROOT / "deploy" / "compose.cluster.yml").read_text(encoding="utf-8")

    assert "PATRONI_REPLICATION_USERNAME: crm_replica" in text
    assert "PATRONI_REPLICATION_SSLMODE: verify-full" in text
    assert "PATRONI_REPLICATION_SSLROOTCERT: /run/cluster-secrets/ca.crt" in text
    assert "PATRONI_REPLICATION_SSLCERT: /run/cluster-secrets/replica-client.crt" in text
    assert "PATRONI_REPLICATION_SSLKEY: /run/cluster-secrets/replica-client.key" in text
    assert "PATRONI_REWIND_USERNAME: crm_rewind" in text
    assert "PATRONI_REWIND_SSLMODE: verify-full" in text


def test_pki_generates_dedicated_database_admin_certificate():
    text = (ROOT / "infra" / "pki" / "generate.sh").read_text(encoding="utf-8")

    assert 'issue_leaf "${node}" "${host}" admin-client clientAuth' in text


def test_pgbackrest_keeps_seven_full_backups_in_r2():
    text = (ROOT / "infra" / "pgbackrest" / "pgbackrest.conf.tpl").read_text(encoding="utf-8")

    assert "repo1-type=s3" in text
    assert "repo1-retention-full=7" in text
    assert "repo1-path=/backups/postgresql" in text
    assert "archive-async=y" in text


def test_backup_schedule_has_weekly_full_and_daily_differential():
    text = (ROOT / "infra" / "pgbackrest" / "backup.cron").read_text(encoding="utf-8")

    assert "backup-if-primary diff" in text
    assert "backup-if-primary full" in text
