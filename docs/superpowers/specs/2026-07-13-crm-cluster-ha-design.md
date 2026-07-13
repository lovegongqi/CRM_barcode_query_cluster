# CRM Cluster High Availability Design

## Goal

Provide `https://crm.mlmll.cn` as the stable public entry point for the CRM barcode tool. Hong Kong is the primary web origin, Singapore is the automatic standby web origin, and the NAS and US nodes remain query, transfer, close-order, PostgreSQL replica, and backup workers.

The system keeps one shared view of accounts, barcode results, product-prefix rules, distributor history, runtime configuration, jobs, and logs across all nodes without Cloudflare D1 daily row limits.

## Confirmed Decisions

- Use the existing Cloudflare Basic Load Balancing subscription and its two included origins.
- Use Hong Kong as the primary public web origin and Singapore as the standby public web origin.
- Run one independent Cloudflare Tunnel for each public origin.
- Keep the NAS and US nodes off the public load balancer.
- Keep five query slots and two transfer slots on every node unless changed from the settings page.
- Use the application's existing login only. Do not add Cloudflare Access as a second login.
- Use PostgreSQL 16 under Patroni for shared structured state and PostgreSQL-backed job leases.
- Use a three-member etcd quorum on Hong Kong, Singapore, and the US node.
- Use the servers' public DNS names for database replication, consensus, proxy health checks, and cluster administration.
- Do not restrict PostgreSQL by source IP; require TLS client certificates and SCRAM-SHA-256 passwords for every remote connection.
- Use Cloudflare R2 for result HTML, exports, migration snapshots, and encrypted database backups.
- Do not use Cloudflare D1 or Queues for production data or task scheduling.
- Do not store passwords, Cloudflare tokens, tunnel tokens, certificate private keys, database passwords, or encryption secrets in Git.

## Node Roles

| Node | Web role | Database role | CRM worker role |
| --- | --- | --- | --- |
| Hong Kong | Primary load balancer origin | Initial PostgreSQL leader and etcd member | Query, transfer, close-order |
| Singapore | Automatic standby origin | Preferred synchronous failover replica and etcd member | Query, transfer, close-order |
| Synology NAS | No public web origin | Asynchronous replica and backup target | Query, transfer, close-order |
| United States | No public web origin | Asynchronous replica and etcd member | Query, transfer, close-order |

Only Hong Kong and Singapore count toward the Cloudflare Load Balancing origin allowance. All four applications connect to a local database router and claim jobs through PostgreSQL.

## Public Traffic

Create two independent remotely managed tunnels:

- `crm-hk-origin` routes to the Hong Kong application's local `http://crm-barcode-query:5001` service.
- `crm-sg-origin` routes to the Singapore application's local `http://crm-barcode-query:5001` service.

Create one Cloudflare monitor:

- Method: `GET`
- Path: `/readyz`
- Expected status: `2xx`
- Timeout: `5` seconds
- Interval: `60` seconds
- Consecutive failures before unhealthy: `2`

Create one primary pool containing the Hong Kong tunnel origin and one fallback pool containing the Singapore tunnel origin. Bind `crm.mlmll.cn` to the load balancer, with Hong Kong first and Singapore as fallback.

Direct node URLs remain available only during rollout and rollback testing. After failover tests pass, public application ports on Hong Kong and Singapore are closed. Tunnel connections are outbound and do not require public ports 80 or 443.

## Public Database Transport

All servers already have public connectivity, so the cluster does not add a WireGuard overlay. PostgreSQL, Patroni, and etcd use public DNS names and end-to-end TLS.

- PostgreSQL `5432/TCP` is publicly reachable and accepts only `hostssl` connections.
- PostgreSQL clients must present a private-CA client certificate and authenticate with a SCRAM-SHA-256 password.
- Clients use `sslmode=verify-full` and verify the server DNS name.
- etcd peer and client traffic on `2379/2380` requires mutually authenticated TLS certificates.
- Patroni REST traffic on `8008` requires TLS and a client certificate for HAProxy health checks and administration.
- HAProxy listens only on each node's localhost/application Docker network and routes to the current Patroni leader.
- Certificate private keys are mode `0600`, mounted read-only, and rotated without committing them to Git.

Opening the ports publicly removes tunnel and dynamic-endpoint dependencies, but does not relax authentication. Password-only or plaintext database connections are rejected.

## PostgreSQL Availability

Run PostgreSQL 16 under Patroni on all four nodes. Run etcd on Hong Kong, Singapore, and the US node, so loss of the NAS does not affect leader election.

- Hong Kong starts as the PostgreSQL leader.
- Singapore is the preferred synchronous standby and automatic failover target.
- NAS and US replicate asynchronously and remain recovery copies.
- Patroni uses synchronous mode without strict blocking: when Singapore is healthy, acknowledged writes reach it; if Singapore is unavailable, the leader continues accepting writes and reports degraded redundancy.
- Patroni watchdog and fencing prevent two writable leaders.
- A recovered old leader is rewound and rejoins as a replica before it can be promoted.
- Maximum PostgreSQL replication lag is visible in the settings page.

Each application connects to a local HAProxy service. HAProxy checks Patroni's public TLS primary REST endpoint and routes writes to the current leader over PostgreSQL TLS. Application `DATABASE_URL` is identical on every node and does not change during failover.

Expected database failover is under two minutes. Cloudflare web failover remains governed by the 60-second health monitor. A CRM browser operation already submitted to the external CRM may still require manual review after a worker failure.

## Shared Application Data

Move structured state from local JSON files into PostgreSQL:

- Tool accounts and page permissions
- Barcode metadata currently stored in `barcode_data.json`
- Product-prefix rules currently stored in `product_library.json`
- Distributor history and deletion records
- Runtime configuration and per-node channel counts
- Cluster nodes, worker heartbeats, CRM slot state, and replication health
- Query, transfer, close-order, product-lookup, and bulk-login jobs
- Job items, idempotency keys, progress, results, and historical page-scoped logs
- Encrypted remembered CRM credentials
- R2 object keys, SHA-256 hashes, and lifecycle state

Use one shared application encryption key on all nodes for remembered CRM credentials. Browser profile directories remain local because CRM login sessions are independent per server and channel.

## R2 Object Storage

Use one R2 Standard bucket named `crm-barcode-query` for:

- CRM result HTML under `results/`
- Generated Excel exports under `exports/`
- Temporary cross-node artifacts under `temporary/`
- Legacy migration snapshots under `backups/migrations/`
- pgBackRest full, differential, and WAL backups under `backups/postgresql/`

PostgreSQL stores each object key, SHA-256 digest, byte size, content type, and creation time. Local disks may cache objects, but caches are disposable. Deleting a result removes its PostgreSQL record and R2 object through an idempotent workflow.

Applications use a bucket-scoped R2 credential that cannot access other buckets.

## Shared Job Queue

Replace process-local batch jobs with PostgreSQL-backed jobs. Workers claim one item at a time using `FOR UPDATE SKIP LOCKED` and a renewable lease, so each free logged-in channel immediately takes the next item and faster channels naturally process more work.

- Query and product-lookup items can retry automatically after a lease expires.
- Service-order closing deduplicates by service order number.
- Transfer items deduplicate by operation key and record the CRM transfer number immediately when it appears.
- Transfer and close-order items at or after `submitted_to_crm` never replay automatically; an uncertain failure moves them to `needs_review`.
- A worker heartbeat records node, slot, CRM login state, current item, and last error.
- Pages read job state and logs from PostgreSQL, so refresh, page switching, or Web-origin failover does not lose progress.
- Detailed logs remain page-scoped, newest-first, and are not automatically cleared.

## Authentication And Sessions

All web origins use the same Flask signing secret so a tool login cookie remains valid after web failover. Tool accounts and permissions are read from PostgreSQL. Account passwords are stored as password hashes.

Desktop builds continue using their local storage and local-login behavior. They do not join the server cluster or depend on PostgreSQL/R2.

## Backup And Recovery

Use pgBackRest with R2's S3-compatible API:

- Continuously archive WAL to R2.
- Run a full backup weekly and a differential backup nightly.
- Keep the latest verified backup manifest and database schema dump on the NAS.
- Run `pgbackrest check` after every backup.
- Restore the latest backup into an isolated temporary PostgreSQL container weekly and compare application table counts.
- Keep the current NAS JSON/HTML source snapshot until the PostgreSQL/R2 migration and restore tests pass.

Cloudflare R2 is the off-site backup; the NAS is an additional local recovery copy, not the only database leader.

## Migration And Cutover

1. Back up all current NAS data volumes and record source file counts and hashes.
2. Generate a private certificate authority and per-node PostgreSQL, Patroni, etcd, HAProxy, and application certificates.
3. Deploy etcd, Patroni/PostgreSQL, HAProxy, and pgBackRest without changing the running application.
4. Add PostgreSQL and R2 adapters with local-file fallback and tests.
5. Import JSON metadata and result files once, then compare row counts and hashes.
6. Enable dual-read verification while the existing NAS application remains public.
7. Enable PostgreSQL job leases and verify all four nodes claim work.
8. Deploy Hong Kong and Singapore Tunnels.
9. Create the Cloudflare health monitor, pools, and `crm.mlmll.cn` load balancer.
10. Test application, node, network, PostgreSQL leader, R2, and backup failure scenarios before closing direct application ports.

Every migration step is reversible. Existing JSON and result files remain untouched until PostgreSQL and R2 verification passes and a separate NAS backup exists.

## Failure Behavior

- Hong Kong application failure: Cloudflare routes new requests to Singapore.
- Hong Kong host failure: Patroni promotes Singapore and Cloudflare routes Web traffic to Singapore.
- Singapore failure while Hong Kong is healthy: Hong Kong remains writable with degraded synchronous redundancy.
- NAS failure: public access, PostgreSQL leadership, R2, and cloud workers continue; NAS backup copying pauses.
- US failure: HK and SG still form the etcd majority; US database and workers rejoin after recovery.
- Loss of either HK or SG plus US at the same time: etcd loses quorum and Patroni refuses unsafe automatic leadership changes; the current healthy leader may continue, but promotion requires operator recovery.
- Worker failure during query: its expired item lease becomes retryable.
- Worker failure after transfer or close submission: the item becomes `needs_review` and cannot execute twice automatically.
- R2 outage: new result publication pauses; PostgreSQL does not point to an object until upload succeeds.

## Verification Criteria

- `https://crm.mlmll.cn` presents valid HTTPS and the application login page.
- Requests normally reach Hong Kong.
- Stopping the Hong Kong application moves Web traffic to Singapore within two monitor cycles.
- Stopping the Hong Kong PostgreSQL leader promotes Singapore and all applications reconnect through local HAProxy.
- Accounts, barcodes, product rules, distributor history, filters, jobs, and logs are identical through both Web origins.
- All four nodes, replication lag, and configured CRM channels appear in settings.
- A batch query is claimed across logged-in query channels on all four nodes.
- Refreshing or switching origins does not lose progress or logs.
- Transfer and close-order fault tests never create an automatic duplicate.
- PostgreSQL/R2 migration reports matching source and destination counts and hashes.
- A pgBackRest backup restores into an empty PostgreSQL test container and matches table counts.

## Cost Boundary

The design uses the existing two-origin Basic Load Balancing subscription and R2 included allowance. PostgreSQL, Patroni, etcd, and HAProxy run on existing servers, so there is no D1 or managed-database usage charge. Additional load balancer origins, R2 usage beyond the included allowance, or new paid services require explicit approval.

## Non-Goals

- NAS and US are not automatic public Web origins.
- The design does not promise zero data loss if both the leader and synchronous standby fail together before asynchronous replicas catch up.
- A CRM browser action interrupted after external CRM submission is not guaranteed to resume automatically.
- Desktop application data is not merged into the server cluster.
