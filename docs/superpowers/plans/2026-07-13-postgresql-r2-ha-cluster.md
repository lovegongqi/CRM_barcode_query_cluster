# PostgreSQL And R2 CRM Cluster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在香港、新加坡、美国和群晖四个现有节点上部署 PostgreSQL 高可用共享数据、R2 文件存储和动态 CRM 任务调度，并通过 Cloudflare 为 `https://crm.mlmll.cn` 提供香港主入口、新加坡自动备用入口。

**Architecture:** PostgreSQL 16/Patroni 运行在四个节点，香港初始主库、新加坡同步备用、美国和群晖异步副本；香港、新加坡、美国组成 etcd 三节点仲裁。数据库、Patroni 和 etcd 使用公网 DNS 和双向 TLS，不使用 WireGuard、不限制来源 IP；每个应用只连接本机 HAProxy，由 HAProxy 路由到当前主库。PostgreSQL 保存结构化数据与任务租约，R2 保存 HTML、Excel 和 pgBackRest 备份。

**Tech Stack:** Python 3.11, Flask, Playwright, pytest, psycopg 3, PostgreSQL 16, Patroni, etcd 3.5, HAProxy 2.8, pgBackRest, boto3, cryptography, Docker Compose, Cloudflare Tunnel and Load Balancing.

## Global Constraints

- 所有四个节点默认 5 个查询通道和 2 个移库通道，设置页可分别修改。
- PostgreSQL 公网端口不做来源 IP 白名单，但仅接受 TLS；应用和复制连接都必须同时通过客户端证书与 SCRAM-SHA-256 密码。
- etcd `2379/2380` 和 Patroni `8008` 必须使用私有 CA 双向 TLS。
- 应用只连接本机 HAProxy；所有节点使用同一个 `DATABASE_URL`。
- 查询任务可重试；移库或结单进入 `submitted_to_crm` 后发生不确定错误，只能转为 `needs_review`。
- 桌面应用继续使用本地 JSON、HTML 和 session，不连接服务器 PostgreSQL/R2。
- 迁移期间不得删除现有 JSON/HTML；计数、字段和 SHA-256 全部匹配后才能切换。
- 密钥、证书私钥、数据库密码和 Cloudflare Token 不得写入 Git。
- 先部署基础设施，再迁移数据，最后才切换公网入口。

## File Map

- Create `cluster/config.py`: 服务器集群环境变量与桌面模式隔离。
- Create `cluster/db.py`: psycopg 连接池、事务与健康检查。
- Create `cluster/schema/0001_initial.sql`: PostgreSQL 表、约束和索引。
- Create `cluster/migrations.py`: 数据库迁移执行器。
- Create `cluster/catalog.py`: 账号、条码、匹配、分销商、配置、节点和凭据仓储。
- Create `cluster/jobs.py`: PostgreSQL 任务项、租约、日志和幂等状态。
- Create `cluster/scheduler.py`: 空闲 CRM 通道动态领取任务。
- Create `cluster/objects.py`: R2 结果、导出和临时对象。
- Create `cluster/crypto.py`: CRM 密码加密。
- Create `cluster/legacy_import.py`: JSON/HTML 到 PostgreSQL/R2 的幂等迁移。
- Create `infra/pki/generate.sh`: 私有 CA 与服务证书生成脚本。
- Create `infra/etcd/compose.yml`, `infra/patroni/compose.yml`, `infra/haproxy/haproxy.cfg`.
- Create `infra/templates/*.yml`: 每节点 Patroni/etcd 配置模板。
- Create `infra/pgbackrest/`: R2 与 NAS 备份配置和验证脚本。
- Create `deploy/compose.cluster.yml`, `deploy/env.cluster.example`.
- Create `tests/`: Python 仓储、迁移、任务租约、API和故障测试。
- Modify `app.py`: 在现有存储和任务入口接入集群适配层，保留本地回退。
- Modify `templates/accounts.html`, `templates/crm.html`, `templates/index.html`, `templates/transfer.html`, `templates/product_library.html`.
- Modify `requirements.txt`, `Dockerfile`, `.gitignore`, `deploy/README.md`, `README.md`.

---

### Task 1: Add Cluster Configuration And Test Harness

**Files:**
- Create: `cluster/__init__.py`
- Create: `cluster/config.py`
- Create: `requirements-dev.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_cluster_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `ClusterConfig.from_env(env=None) -> ClusterConfig`
- Produces: `ClusterConfig.validate() -> None`

- [ ] **Step 1: Write failing tests**

```python
def test_desktop_never_enables_server_cluster(monkeypatch):
    monkeypatch.setenv("CRM_DESKTOP_APP", "1")
    monkeypatch.setenv("CRM_CLUSTER_MODE", "postgresql")
    assert ClusterConfig.from_env().enabled is False


def test_server_cluster_requires_database_and_r2(monkeypatch):
    monkeypatch.setenv("CRM_DESKTOP_APP", "0")
    monkeypatch.setenv("CRM_CLUSTER_MODE", "postgresql")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        ClusterConfig.from_env().validate()
```

- [ ] **Step 2: Run and confirm missing module failure**

Run: `python -m pytest tests/test_cluster_config.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'cluster'`.

- [ ] **Step 3: Implement the immutable config**

```python
@dataclass(frozen=True)
class ClusterConfig:
    enabled: bool
    node_id: str
    node_name: str
    node_role: str
    database_url: str
    r2_endpoint_url: str
    r2_bucket: str
    r2_access_key_id: str
    r2_secret_access_key: str
    credentials_key: str

    @classmethod
    def from_env(cls, env=None):
        values = os.environ if env is None else env
        enabled = values.get("CRM_CLUSTER_MODE") == "postgresql" and values.get("CRM_DESKTOP_APP") != "1"
        return cls(
            enabled=enabled,
            node_id=values.get("CRM_NODE_ID", "standalone-1").strip(),
            node_name=values.get("CRM_NODE_NAME", "单机节点").strip(),
            node_role=values.get("CRM_NODE_ROLE", "standalone").strip(),
            database_url=values.get("DATABASE_URL", ""),
            r2_endpoint_url=values.get("R2_ENDPOINT_URL", ""),
            r2_bucket=values.get("R2_BUCKET", ""),
            r2_access_key_id=values.get("R2_ACCESS_KEY_ID", ""),
            r2_secret_access_key=values.get("R2_SECRET_ACCESS_KEY", ""),
            credentials_key=values.get("CRM_CREDENTIALS_KEY", ""),
        )

    def validate(self):
        if not self.enabled:
            return
        required = {
            "DATABASE_URL": self.database_url,
            "R2_ENDPOINT_URL": self.r2_endpoint_url,
            "R2_BUCKET": self.r2_bucket,
            "R2_ACCESS_KEY_ID": self.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
            "CRM_CREDENTIALS_KEY": self.credentials_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("缺少集群配置: " + ", ".join(missing))
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_cluster_config.py -q`

Expected: `2 passed`.

```bash
git add cluster requirements-dev.txt tests .gitignore
git commit -m "Add PostgreSQL cluster configuration"
```

### Task 2: Add PostgreSQL Schema, Connection Pool, And Migrations

**Files:**
- Create: `cluster/db.py`
- Create: `cluster/migrations.py`
- Create: `cluster/schema/0001_initial.sql`
- Create: `tests/test_db.py`
- Create: `tests/test_migrations.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `Database.transaction() -> ContextManager[psycopg.Connection]`
- Produces: `Database.fetch_all(sql, params=()) -> list[dict]`
- Produces: `Database.fetch_one(sql, params=()) -> dict | None`
- Produces: `Database.execute(sql, params=()) -> int`
- Produces: `MigrationRunner.apply() -> list[str]`

- [ ] **Step 1: Write transaction rollback and migration tests**

```python
def test_transaction_rolls_back_on_error(database):
    with pytest.raises(RuntimeError):
        with database.transaction() as conn:
            conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", ("bad",))
            raise RuntimeError("stop")
    assert database.fetch_one("SELECT version FROM schema_migrations WHERE version=%s", ("bad",)) is None


def test_migration_is_idempotent(database):
    runner = MigrationRunner(database)
    assert runner.apply() == ["0001_initial"]
    assert runner.apply() == []
```

- [ ] **Step 2: Run tests against an ephemeral PostgreSQL container**

Run: `docker run --rm -d --name crm-test-postgres -e POSTGRES_PASSWORD=test -e POSTGRES_DB=crm_test -p 55432:5432 postgres:16`

Run: `TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/crm_test python -m pytest tests/test_db.py tests/test_migrations.py -q`

Expected: FAIL because database modules are missing.

- [ ] **Step 3: Implement pool and migration runner**

Use `psycopg_pool.ConnectionPool(min_size=1, max_size=8, timeout=10, kwargs={"row_factory": dict_row})`. Transactions commit only on a clean exit and roll back on any exception. Migration files run under PostgreSQL advisory lock `pg_advisory_lock(20260713)`.

- [ ] **Step 4: Create normalized schema**

The migration creates these tables: `app_accounts`, `runtime_config`, `barcode_records`, `product_rules`, `distributors`, `crm_credentials`, `cluster_nodes`, `crm_slots`, `jobs`, `job_items`, `job_logs`, `object_records`, and `migration_runs`. Use `JSONB`, `TIMESTAMPTZ`, foreign keys, and indexes on barcode filters, product prefix, node expiry, job status, claim order, and log cursor.

The claim index must be:

```sql
CREATE INDEX idx_job_items_claim
ON job_items(kind, status, lease_expires_at, created_at)
WHERE status IN ('pending', 'failed', 'leased', 'running');
```

- [ ] **Step 5: Run tests and commit**

Run: `TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/crm_test python -m pytest tests/test_db.py tests/test_migrations.py -q`

Expected: all tests pass.

```bash
git add cluster requirements.txt tests
git commit -m "Add PostgreSQL schema and migrations"
```

### Task 3: Add Catalog Repositories, Encryption, And R2 Storage

**Files:**
- Create: `cluster/catalog.py`
- Create: `cluster/crypto.py`
- Create: `cluster/objects.py`
- Create: `tests/test_catalog.py`
- Create: `tests/test_crypto.py`
- Create: `tests/test_objects.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces repository methods for accounts, runtime config, barcodes, product rules, distributors, credentials, nodes, and slots.
- Produces: `CredentialCipher.encrypt(text) -> str`, `decrypt(token) -> str`.
- Produces: `R2ObjectStore.put_file(...) -> ObjectRecord`, `get_bytes(key) -> bytes`, `delete(key) -> None`.

- [ ] **Step 1: Write repository and security tests**

```python
def test_account_password_is_hashed(catalog):
    catalog.replace_accounts([{"id": "admin", "username": "admin", "password": "secret", "permissions": []}])
    stored = catalog.db.fetch_one("SELECT password_hash FROM app_accounts WHERE username='admin'")
    assert stored["password_hash"] != "secret"


def test_r2_upload_records_sha256(tmp_path, fake_s3):
    path = tmp_path / "5312503010858.html"
    path.write_bytes(b"result")
    record = R2ObjectStore(config(), client=fake_s3).put_file("results", path, path.name, "text/html")
    assert record.sha256 == hashlib.sha256(b"result").hexdigest()
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tests/test_catalog.py tests/test_crypto.py tests/test_objects.py -q`

Expected: FAIL because repositories are missing.

- [ ] **Step 3: Implement parameterized catalog SQL**

Use Werkzeug password hashes, Fernet authenticated encryption for remembered CRM passwords, `INSERT ... ON CONFLICT DO UPDATE`, and transactions for multi-row replacement. Never return password hashes or encrypted CRM passwords from public APIs.

- [ ] **Step 4: Implement R2 idempotency**

Upload first, calculate SHA-256 locally, then insert `object_records`. For download caches use `.part` plus `os.replace()` and reject a hash mismatch. Delete is repeatable when the R2 object is already absent.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_catalog.py tests/test_crypto.py tests/test_objects.py -q`

Expected: all tests pass.

```bash
git add cluster requirements.txt tests
git commit -m "Add shared catalog and R2 storage"
```

### Task 4: Integrate Shared Storage With Existing Flask Routes

**Files:**
- Create: `cluster/services.py`
- Create: `tests/test_app_cluster_storage.py`
- Modify: `app.py`
- Modify: `templates/index.html`

**Interfaces:**
- Produces: `build_cluster_services(config) -> ClusterServices`.
- Existing API response keys remain compatible.
- Local mode preserves all current JSON/HTML behavior.

- [ ] **Step 1: Write cloud-mode route compatibility tests**

```python
def test_results_are_read_from_postgres_without_local_html(client, catalog):
    catalog.list_barcodes.return_value = [{"barcode": "5312503010858", "fields": {}, "updated_at": "2026-07-13T10:00:00Z"}]
    payload = client.get("/api/barcodes").get_json()
    assert payload["barcodes"][0]["barcode"] == "5312503010858"


def test_detail_streams_r2_object(client, objects):
    objects.get_bytes.return_value = b"<html>detail</html>"
    response = client.get("/barcode/5312503010858.html")
    assert response.data == b"<html>detail</html>"
```

- [ ] **Step 2: Run tests and confirm local-only failure**

Run: `python -m pytest tests/test_app_cluster_storage.py -q`

Expected: FAIL because routes still require local files.

- [ ] **Step 3: Adapt existing storage functions**

In cluster mode route `load_runtime_config`, product library, distributor history, barcode metadata, accounts, credentials, `scan_barcodes`, `scan_archived`, archive/delete, detail, and export through `ClusterServices`. Keep function names so transfer/query parsing code does not require broad refactoring.

- [ ] **Step 4: Make result publication ordered**

For a successful CRM query: parse fields, upload HTML to R2, insert/update PostgreSQL metadata and object hash in one database transaction, then expose the result. Product-only lookup updates `product_rules`, removes temporary R2 data, and never appears in result management.

- [ ] **Step 5: Verify both modes and commit**

Run: `python -m pytest tests/test_app_cluster_storage.py -q`

Run: `CRM_CLUSTER_MODE=local python -m compileall app.py cluster`

Expected: tests pass and compile exits 0.

```bash
git add app.py templates/index.html cluster/services.py tests/test_app_cluster_storage.py
git commit -m "Use PostgreSQL and R2 application storage"
```

### Task 5: Add Resumable Legacy Data Migration

**Files:**
- Create: `cluster/legacy_import.py`
- Create: `scripts/migrate_cluster_data.py`
- Create: `tests/test_legacy_import.py`

**Interfaces:**
- Produces: `LegacyImporter.inventory() -> dict`.
- Produces: `LegacyImporter.run(dry_run=False) -> MigrationReport`.
- Produces: `LegacyImporter.verify(run_id) -> MigrationReport`.

- [ ] **Step 1: Write dry-run and resume tests**

```python
def test_dry_run_never_writes(importer, catalog, objects):
    report = importer.run(dry_run=True)
    assert report.source_counts["barcode_html"] == 2
    catalog.upsert_barcode.assert_not_called()
    objects.put_file.assert_not_called()


def test_matching_r2_hash_is_not_uploaded_twice(importer, objects):
    objects.head.return_value.sha256 = importer.source_hash("5312503010858.html")
    report = importer.run()
    assert report.skipped_matching == 1
```

- [ ] **Step 2: Implement inventory, import, and verification**

Read `config/*.json`, `barcode/*.html`, archived HTML, results, and exports. Store per-file SHA-256 and source counts in `migration_runs`. Re-running repairs missing rows/objects but does not duplicate matching content. Write `migration-reports/<run-id>.json`; exit non-zero when counts or hashes differ.

- [ ] **Step 3: Run tests and commit**

Run: `python -m pytest tests/test_legacy_import.py -q`

Expected: all tests pass.

```bash
git add cluster/legacy_import.py scripts/migrate_cluster_data.py tests/test_legacy_import.py
git commit -m "Add verified legacy data migration"
```

### Task 6: Add PostgreSQL Job Leases And Cross-Node Scheduler

**Files:**
- Create: `cluster/jobs.py`
- Create: `cluster/scheduler.py`
- Create: `tests/test_jobs.py`
- Create: `tests/test_scheduler.py`
- Modify: `app.py`

**Interfaces:**
- Produces: `JobRepository.create_job(...)`, `claim_item(...)`, `renew_lease(...)`, `mark_submitted(...)`, `complete_item(...)`, `fail_item(...)`, `append_log(...)`, and `status(...)`.
- Produces: `ClusterScheduler.start()`, `stop()`, and `reconcile_slots()`.

- [ ] **Step 1: Write concurrency and unsafe-retry tests**

```python
def test_skip_locked_gives_item_to_only_one_worker(repository):
    repository.create_job("query", [{"item_key": "A", "barcode": "A"}], {}, "admin", "query:A")
    first = repository.claim_item(["query"], "hk:query-1", 120)
    second = repository.claim_item(["query"], "sg:query-1", 120)
    assert first["item_key"] == "A"
    assert second is None


def test_submitted_transfer_is_never_replayed(repository):
    item = repository.seed_item("transfer", "submitted_to_crm", lease_expired=True)
    repository.recover_expired_items()
    assert repository.get_item(item["id"])["status"] == "needs_review"
```

- [ ] **Step 2: Implement atomic claim SQL**

Use one transaction with `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`, then update owner, expiry, attempts, and status. Query leases expire back to pending/failed; submitted transfer and close items move to `needs_review`.

- [ ] **Step 3: Implement one dispatcher per local CRM slot**

Query slots claim query, product-lookup, and service-close items; transfer slots claim transfer items. Renew every 30 seconds, write heartbeats on state change plus every 120 seconds idle, and stop removed slots only after current work finishes.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_jobs.py tests/test_scheduler.py -q`

Expected: all tests pass.

```bash
git add cluster/jobs.py cluster/scheduler.py tests app.py
git commit -m "Distribute CRM jobs through PostgreSQL"
```

### Task 7: Persist Job APIs, Logs, Node Status, And Settings

**Files:**
- Create: `tests/test_cluster_routes.py`
- Modify: `app.py`
- Modify: `templates/crm.html`
- Modify: `templates/index.html`
- Modify: `templates/transfer.html`
- Modify: `templates/product_library.html`
- Modify: `templates/accounts.html`
- Modify: `static/log_modal.js`

**Interfaces:**
- Existing start/status/stop URLs remain.
- Status reads PostgreSQL by durable `job_id` and log cursor.
- Settings reads PostgreSQL node/slot heartbeats and replication health.

- [ ] **Step 1: Write refresh and failover persistence tests**

```python
def test_job_status_survives_new_web_process(app, client):
    started = client.post("/api/crm/batch/start", json={"barcodes": ["5312503010858"]}).get_json()
    status = app.test_client().get(f"/api/crm/batch/status?job_id={started['job_id']}").get_json()
    assert status["job_id"] == started["job_id"]


def test_node_config_updates_only_target(client, catalog):
    response = client.post("/api/cluster/nodes/hk-1/runtime-config", json={"query_workers": 6, "transfer_workers": 3})
    assert response.status_code == 200
    assert catalog.get_runtime_config("node:sg-1")["query_workers"] != 6
```

- [ ] **Step 2: Convert job endpoints in cluster mode**

Persist batch query, product lookup, transfer summary, transfer submission, service close, and bulk login. Keep existing JSON keys. Logs remain page-scoped and detail modals show newest first.

- [ ] **Step 3: Add database health to `/readyz`**

Read `SELECT pg_is_in_recovery(), now()` through HAProxy. `/readyz` returns 503 when the application cannot reach the writable leader; Cloudflare then moves Web traffic to the other origin.

- [ ] **Step 4: Show replication lag and `needs_review`**

Settings lists each node, CRM slots, database role, replay lag, last heartbeat, current task, and error. Add an administrator-only view for uncertain transfer/close items without an automatic retry button.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_cluster_routes.py -q`

Run: `python -m compileall app.py cluster`

Expected: tests pass and compile exits 0.

```bash
git add app.py templates static/log_modal.js tests/test_cluster_routes.py
git commit -m "Persist cluster jobs and node status"
```

### Task 8: Generate Public TLS PKI And Database HA Configuration

**Files:**
- Create: `infra/pki/generate.sh`
- Create: `infra/pki/openssl.cnf`
- Create: `infra/etcd/compose.yml`
- Create: `infra/patroni/compose.yml`
- Create: `infra/haproxy/haproxy.cfg`
- Create: `infra/templates/patroni.yml.tpl`
- Create: `infra/templates/etcd.env.tpl`
- Create: `infra/render_node_config.py`
- Create: `tests/test_infra_config.py`

**Interfaces:**
- Generates one private CA, per-node server certificates with DNS SANs, per-node application client certificates, replication certificates, etcd peer/client certificates, Patroni API certificates, and HAProxy client certificates.
- Generated keys live under `infra/generated/<node>/` and are gitignored.

- [ ] **Step 1: Write configuration validation tests**

```python
def test_postgres_rejects_non_tls(rendered_hba):
    assert "hostssl crm_barcode crm_app 0.0.0.0/0 scram-sha-256 clientcert=verify-ca" in rendered_hba
    assert "host all all 0.0.0.0/0 reject" in rendered_hba


def test_every_public_service_requires_certificates(rendered):
    assert rendered.etcd["ETCD_CLIENT_CERT_AUTH"] == "true"
    assert rendered.patroni["restapi"]["verify_client"] == "required"
    assert "verify required" in rendered.haproxy
```

- [ ] **Step 2: Generate certificates without printing private material**

Create ECDSA P-256 keys, 10-year CA, 1-year leaf certificates, server SANs for `hk.mlmll.cn`, `sg.mlmll.cn`, `us.mlmll.cn`, and `mlmll.cn`. Set private keys to `0600`. The script refuses to overwrite an existing CA unless passed `--rotate-ca`.

- [ ] **Step 3: Configure PostgreSQL authentication**

Use these terminal HBA rules after local/replication rules:

```text
hostssl crm_barcode crm_app 0.0.0.0/0 scram-sha-256 clientcert=verify-ca
hostssl replication crm_replica 0.0.0.0/0 scram-sha-256 clientcert=verify-ca
host all all 0.0.0.0/0 reject
```

Clients use `sslmode=verify-full`, `sslrootcert`, `sslcert`, and `sslkey`. etcd peer/client and Patroni REST listeners require mTLS.

- [ ] **Step 4: Configure Patroni roles**

Hong Kong starts leader; Singapore has failover priority 100 and synchronous eligibility; US priority 50; NAS priority 0 and `nofailover: true`. Enable `synchronous_mode: true`, `synchronous_mode_strict: false`, `use_pg_rewind: true`, and PostgreSQL checksums.

- [ ] **Step 5: Configure local HAProxy**

HAProxy listens on Docker network port `5433`, checks each Patroni `/primary` endpoint over TLS with its client certificate, and forwards PostgreSQL TLS unchanged to port `5432`. DNS resolvers refresh the dynamic `mlmll.cn` address.

- [ ] **Step 6: Run static tests and commit**

Run: `bash -n infra/pki/generate.sh`

Run: `python -m pytest tests/test_infra_config.py -q`

Expected: all checks pass.

```bash
git add infra tests/test_infra_config.py
git commit -m "Add public TLS PostgreSQL HA infrastructure"
```

### Task 9: Add Common Cluster Compose And pgBackRest

**Files:**
- Create: `deploy/compose.cluster.yml`
- Create: `deploy/env.cluster.example`
- Create: `infra/pgbackrest/pgbackrest.conf.tpl`
- Create: `infra/pgbackrest/verify_restore.sh`
- Modify: `Dockerfile`
- Modify: `deploy/README.md`
- Modify: `deploy/compose.nas.yml`
- Modify: `deploy/compose.worker.yml`

**Interfaces:**
- All nodes run app, PostgreSQL/Patroni, and HAProxy; HK/SG/US also run etcd.
- NAS additionally mounts a local backup directory.
- pgBackRest archives WAL continuously to R2.

- [ ] **Step 1: Validate compose before implementation**

Run: `docker compose --env-file deploy/env.cluster.example -f deploy/compose.cluster.yml config --quiet`

Expected: FAIL because files are missing.

- [ ] **Step 2: Implement services and health checks**

Use pinned images for PostgreSQL/Patroni, etcd 3.5, HAProxy 2.8, and the application. Mount certificates read-only. PostgreSQL data and browser session volumes remain node-local. Health checks validate etcd mTLS, Patroni role, HAProxy writable connection, and `/readyz`.

- [ ] **Step 3: Configure backups**

Archive WAL to `s3://crm-barcode-query/backups/postgresql/`, run nightly differential and weekly full backups, keep 7 full backup sets, and copy manifests/schema dumps to NAS. `verify_restore.sh` restores into an isolated temporary container and compares all application table counts.

- [ ] **Step 4: Validate compose and commit**

Run: `docker compose --env-file deploy/env.cluster.example -f deploy/compose.cluster.yml config --quiet`

Expected: exit code 0.

```bash
git add deploy infra/pgbackrest Dockerfile
git commit -m "Add PostgreSQL cluster deployment and backups"
```

### Task 10: Stage Infrastructure, Migrate Data, And Cut Over

**Files:**
- Create: `docs/operations/postgresql-cutover.md`
- Modify: `README.md`
- Modify: `deploy/README.md`

**Interfaces:**
- Produces migration, replication, backup, failover, and rollback reports.
- Does not delete source data.

- [ ] **Step 1: Run the complete local test suite**

Run: `python -m pytest -q`

Run: `docker build -t crm-barcode-query:postgresql-test .`

Expected: all tests pass and image builds.

- [ ] **Step 2: Back up current NAS data**

Record Docker volume names, file counts, byte totals, and SHA-256 manifest for config, barcode, results, and session. Keep the current application running.

- [ ] **Step 3: Deploy PKI, etcd, Patroni, PostgreSQL, and HAProxy only**

Verify three-member etcd quorum, HK leader, SG synchronous standby, US/NAS asynchronous replicas, and TLS rejection for missing certificate, wrong certificate, wrong password, and non-TLS clients.

- [ ] **Step 4: Test database failover before application migration**

Stop HK Patroni/PostgreSQL, verify SG is promoted, verify every local HAProxy routes to SG, restart HK, and verify it rejoins as a replica after rewind. Record recovery time and replication lag.

- [ ] **Step 5: Run migration dry-run and apply**

Run: `python scripts/migrate_cluster_data.py --dry-run --data-dir /app/data`

Expected: no writes and no unreadable source files.

Run: `python scripts/migrate_cluster_data.py --apply --verify --data-dir /app/data`

Expected: `verified: true` with matching PostgreSQL rows and R2 hashes.

- [ ] **Step 6: Deploy cluster application to all four nodes with direct URLs open**

Verify `/readyz`, settings node status, 5 query/2 transfer defaults, batch queries claimed by multiple nodes, one test-safe transfer, and one already-closed service check.

- [ ] **Step 7: Deploy HK and SG Cloudflare Tunnels and Load Balancer**

Use `/readyz`, 5-second timeout, 60-second interval, and two failures. Test HK Web failure, SG fallback, job/log continuity, and return to HK.

- [ ] **Step 8: Verify backup and rollback**

Run pgBackRest full backup, restore into an empty temporary PostgreSQL container, compare table counts, and confirm R2 plus NAS copies. Switch one app back to file mode to prove rollback without deleting PostgreSQL/R2.

- [ ] **Step 9: Close direct HK/SG application ports after acceptance**

Keep PostgreSQL/Patroni/etcd public TLS ports and SSH. Only close the direct Web application ports after the user confirms traffic, data, failover, tasks, and backups.

- [ ] **Step 10: Commit the runbook**

```bash
git add README.md deploy/README.md docs/operations/postgresql-cutover.md
git commit -m "Document PostgreSQL cluster cutover"
```

## Final Acceptance Commands

```bash
python -m pytest -q
docker compose --env-file deploy/env.cluster.example -f deploy/compose.cluster.yml config --quiet
docker build -t crm-barcode-query:postgresql-final .
```

Expected: tests pass, compose validates, image builds, PostgreSQL leader failover succeeds, migration counts and hashes match, all four nodes process tasks, Web traffic fails from HK to SG, and submitted CRM operations never replay automatically.
