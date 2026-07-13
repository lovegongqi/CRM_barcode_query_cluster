# CRM Cluster High Availability Design

## Goal

Provide `https://crm.mlmll.cn` as the stable public entry point for the CRM barcode tool. Hong Kong is the primary web origin, Singapore is the automatic standby web origin, and all four nodes share Cloudflare-managed data and work queues.

The system must keep one shared view of accounts, barcode results, product-prefix rules, distributor history, runtime configuration, jobs, and logs across all nodes without operating a self-hosted database cluster.

## Confirmed Decisions

- Use the existing Cloudflare Basic Load Balancing subscription and its two included origins.
- Use Hong Kong as the primary public web origin.
- Use Singapore as the standby public web origin.
- Run one independent Cloudflare Tunnel for each public origin.
- Keep the NAS and US nodes off the public load balancer.
- Keep five query slots and two transfer slots on every node unless changed from the settings page.
- Use the application's existing login only. Do not add Cloudflare Access as a second login.
- Use Cloudflare D1 for shared structured state.
- Use Cloudflare Queues to distribute query, transfer, close-order, product-lookup, and node-control work.
- Use Cloudflare R2 for result files, exports, migration snapshots, and backups.
- Do not deploy PostgreSQL, Patroni, etcd, or HAProxy.
- WireGuard UDP `51820` may be used for administration and NAS backup transfer, but it is not a production runtime dependency.
- Do not store passwords, Cloudflare tokens, tunnel tokens, WireGuard private keys, or encryption secrets in Git.

## Node Roles

| Node | Web role | Shared-data role | CRM worker role |
| --- | --- | --- | --- |
| Hong Kong | Primary load balancer origin | D1/R2/Queues client | Query, transfer, close-order |
| Singapore | Automatic standby origin | D1/R2/Queues client | Query, transfer, close-order |
| Synology NAS | No public web origin | Backup export target | Query, transfer, close-order |
| United States | No public web origin | D1/R2/Queues client | Query, transfer, close-order |

Only Hong Kong and Singapore count toward the Cloudflare Load Balancing origin allowance. NAS and US workers claim backend work directly from Cloudflare Queues.

## Public Traffic

Create two independent, remotely managed tunnels:

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

## Cloudflare Data Layer

Create one D1 database named `crm-barcode-cluster`. D1 stores:

- Tool accounts and page permissions
- Barcode metadata currently stored in `barcode_data.json`
- Product-prefix rules currently stored in `product_library.json`
- Distributor history and deletion records
- Runtime configuration and per-node channel counts
- Cluster node and CRM slot heartbeats
- Remembered CRM credentials encrypted before storage
- Jobs, job items, idempotency keys, progress, results, and page-scoped logs
- R2 object keys, content hashes, and lifecycle state

Every frequently filtered field has an index so D1 row-read usage stays predictable. Writes use one-statement compare-and-set updates or D1 batches; no workflow depends on a long-lived SQL transaction.

Applications access D1 through a narrowly scoped Cloudflare API token. The existing global Cloudflare key is used only during provisioning and is not deployed to application nodes.

## Cloudflare Queues

Create shared work queues:

- `crm-query`: barcode query and product-prefix lookup items
- `crm-transfer`: complete transfer operations
- `crm-service-close`: one item per unique service order

Create one control queue per node:

- `crm-control-hk`
- `crm-control-sg`
- `crm-control-nas`
- `crm-control-us`

Control queues carry bulk-login, logout, channel restart, and configuration-refresh commands targeted to one node. Shared queues use HTTP pull consumers, allowing every logged-in channel to take one item, finish it, and request the next item. Faster channels naturally process more items.

Queues provide at-least-once delivery, so every message contains a D1 job-item ID and idempotency key. Workers check D1 before starting an item and before acknowledging it.

- Query and product-lookup work can retry automatically.
- Service-order closing deduplicates by service order number.
- Transfer work deduplicates by generated operation key and records the CRM transfer number as soon as it appears.
- A redelivered transfer or close-order item at or after `submitted_to_crm` enters `needs_review` instead of executing again.

## R2 Object Storage

Create one Standard storage bucket named `crm-barcode-query` in APAC. R2 is authoritative for:

- CRM result HTML files under `results/`
- Generated Excel exports under `exports/`
- Temporary result artifacts that must survive a node change under `temporary/`
- Legacy migration snapshots under `backups/migrations/`
- D1 SQL exports and manifests under `backups/d1/`

Each object record in D1 includes its key, SHA-256 digest, byte size, content type, and creation time. Local disks may cache R2 files, but cache files are disposable. Deleting a result removes its D1 record and R2 object in one idempotent workflow.

Applications use a bucket-scoped R2 read/write token. The token cannot access other buckets.

## Shared Job State And Logs

D1 stores the durable job state shown by the web pages. A job item moves through explicit stages:

```text
pending -> leased -> running -> submitted_to_crm -> succeeded
                                      |-> needs_review
                 |-> failed
```

Each node writes a heartbeat containing node ID, slot ID, CRM login state, current item, last error, and expiry time. Pages read D1 state, so refreshing, switching pages, or failing from Hong Kong to Singapore does not lose progress.

Detailed logs remain page-scoped and newest-first. Logs are not automatically deleted. D1 usage is monitored; if storage or daily read/write usage reaches 80% of the free allowance, the settings page shows a warning before any paid upgrade is considered.

## Authentication And Secrets

All web origins use the same Flask signing secret so a tool login cookie remains valid after web failover. Tool accounts and permissions are read from D1.

Remembered CRM credentials use authenticated encryption with one shared application key. Browser profile directories remain local to each node because CRM login sessions are independent per browser channel.

Provisioning creates separate scoped credentials for D1, Queues, R2, and Tunnels. Secrets are written only to protected server environment files and are never returned by application APIs.

Desktop builds continue using their current local storage and local-login behavior. They do not share server-cluster data unless a separate desktop synchronization feature is requested.

## Backup And Recovery

Cloudflare is the live shared data platform. Backups provide provider-independent recovery:

- Export D1 as SQL every night.
- Upload the SQL export plus a manifest and table counts to R2.
- Copy the latest verified D1 export and manifest to the NAS through the administration channel.
- Keep R2 object manifests and hashes in the backup.
- Restore each new backup into a temporary D1 database and compare table counts before marking the backup verified.

The NAS therefore holds offline recovery copies, not a live database replica. NAS failure does not affect the public application or job processing on cloud nodes.

## Free-Tier Boundary

The account currently has access to Workers Free resources and no Workers Paid subscription. The design initially stays within:

- D1: 5 million rows read per day, 100,000 rows written per day, and 5 GB total storage
- Queues: 10,000 operations per day with 24-hour message retention
- R2: 10 GB-month Standard storage, 1 million Class A operations, and 10 million Class B operations in the included allowance

If a free daily D1 or Queues limit is exhausted, Cloudflare rejects operations until the daily reset. The system monitors usage and requires explicit approval before enabling Workers Paid or any additional billed service.

## Migration And Cutover

1. Back up all existing NAS data volumes and record source file counts and hashes.
2. Create the scoped Cloudflare API tokens, D1 database, Queues, and R2 bucket.
3. Apply the D1 schema and indexes.
4. Add D1, Queues, and R2 adapters with file-mode fallback and tests.
5. Import JSON metadata and result files once, then compare counts and hashes.
6. Enable dual-read verification while the existing NAS application remains public.
7. Enable Queue consumers and verify all four nodes claim work.
8. Deploy Hong Kong and Singapore Tunnels.
9. Create the Cloudflare health monitor, pools, and `crm.mlmll.cn` load balancer.
10. Test application, node, queue, D1, R2, and backup failure scenarios before closing direct public application ports.

Every migration step is reversible. Existing JSON and result files remain untouched until D1 and R2 verification passes and a separate NAS backup exists.

## Failure Behavior

- Hong Kong application or host failure: Cloudflare routes new requests to Singapore.
- Singapore failure while Hong Kong is healthy: Hong Kong remains public and all backend workers continue.
- NAS failure: public access, D1, R2, Queues, and cloud workers continue; NAS backup copying pauses.
- US failure: US worker heartbeats expire and its leased safe work returns to the queue.
- Worker failure during query: the message is retried after its visibility timeout.
- Worker failure during an uncertain transfer or close-order operation: D1 marks the item `needs_review` and prevents automatic duplicate execution.
- Temporary Cloudflare API failure: clients use bounded retry with jitter; unsafe business operations rely on idempotency state before retrying.
- Free-tier exhaustion: new work pauses with a visible error and no subscription is changed automatically.

## Verification Criteria

- `https://crm.mlmll.cn` presents a valid Cloudflare HTTPS certificate and the application login page.
- Requests normally reach Hong Kong.
- Stopping the Hong Kong application moves traffic to Singapore within two monitor cycles.
- Accounts, barcode records, product-prefix rules, distributor history, filters, jobs, and logs are identical through both web origins.
- All four nodes and their configured CRM channels appear in the settings page.
- A batch query is claimed across logged-in query channels on all four nodes.
- Refreshing or switching web origins does not lose job progress or logs.
- Transfer and close-order fault tests never create an automatic duplicate.
- D1/R2 migration reports matching source and destination counts and hashes.
- A nightly D1 export restores into a temporary database and matches table counts.
- Settings reports D1, Queues, and R2 usage plus an 80% warning threshold.

## Cost Boundary

The design uses the existing two-origin Basic Load Balancing subscription, so it does not add paid load balancer origins. D1, Queues, and R2 start on their free allowances. Any Workers Paid upgrade, additional load balancer origin, or usage beyond an included allowance requires explicit approval before changing billing.

## Non-Goals

- NAS and US are not automatic public web origins.
- NAS and US do not hold live D1 replicas; Cloudflare manages live database availability.
- A CRM browser action interrupted after an external CRM submission is not guaranteed to resume automatically.
- Desktop application data is not merged into the server cluster.
