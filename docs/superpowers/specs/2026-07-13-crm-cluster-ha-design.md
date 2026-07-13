# CRM Cluster High Availability Design

## Goal

Provide `https://crm.mlmll.cn` as the stable public entry point for the CRM barcode tool. Hong Kong is the primary web origin, Singapore is the automatic standby web origin, and the NAS and US nodes remain query, transfer, close-order, database replica, and backup workers.

The system must keep one shared view of accounts, barcode results, product-prefix rules, distributor history, runtime configuration, jobs, and logs across all nodes.

## Confirmed Decisions

- Use the existing Cloudflare Basic Load Balancing subscription and its two included origins.
- Use Hong Kong as the primary public web origin.
- Use Singapore as the standby public web origin.
- Run one independent Cloudflare Tunnel for each public origin.
- Keep the NAS and US nodes off the public load balancer.
- Keep five query slots and two transfer slots on every node unless changed from the settings page.
- Use the application's existing login only. Do not add Cloudflare Access as a second login.
- Use WireGuard for private node-to-node traffic. The NAS forwards UDP port `51820`.
- Use PostgreSQL for shared structured state and R2 for result files, exports, and backups.
- Do not store passwords, tunnel tokens, WireGuard private keys, database passwords, or R2 secrets in Git.

## Node Roles

| Node | Web role | Database role | CRM worker role |
| --- | --- | --- | --- |
| Hong Kong | Primary load balancer origin | Initial PostgreSQL leader | Query, transfer, close-order |
| Singapore | Automatic standby origin | Preferred failover replica | Query, transfer, close-order |
| Synology NAS | No public web origin | Replica and backup target | Query, transfer, close-order |
| United States | No public web origin | Replica and consensus member | Query, transfer, close-order |

Only Hong Kong and Singapore count toward the Cloudflare Load Balancing origin allowance. The NAS and US workers continue to claim backend jobs over the private network.

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

The direct node URLs remain available only during rollout and rollback testing. After the load balancer passes failover tests, public application ports on Hong Kong and Singapore should be closed. Tunnel connections are outbound and do not require public ports 80 or 443.

## Private Network

Use a WireGuard full mesh so database consensus and worker traffic do not depend on the NAS being online:

| Node | WireGuard address |
| --- | --- |
| NAS | `10.77.0.1` |
| Hong Kong | `10.77.0.2` |
| Singapore | `10.77.0.3` |
| United States | `10.77.0.4` |

WireGuard uses UDP `51820`. Database, Patroni, consensus, and cluster administration ports bind only to WireGuard addresses or localhost. Cloud firewalls allow WireGuard peers only. A periodic endpoint refresh handles the NAS dynamic public IP and domain resolution.

## PostgreSQL Availability

Run PostgreSQL under Patroni on all four nodes. Run the three-member consensus service on Hong Kong, Singapore, and the US node so the database can elect a leader when the NAS is unavailable.

- Hong Kong starts as the database leader.
- Singapore is the preferred automatic failover target.
- NAS and US remain replicas and recovery copies.
- Singapore is synchronous when healthy; NAS and US replicate asynchronously.
- If Singapore is unavailable, the leader continues accepting writes and reports degraded redundancy instead of blocking the application.
- A recovered old leader rejoins as a replica. It is not promoted automatically until fully caught up.

Every application connects to a local database proxy. The proxy discovers the current Patroni leader, so application configuration does not change during database failover.

Expected failover recovery time is up to two minutes, governed by database leader election and the existing 60-second Cloudflare health monitor. An operation already executing inside the CRM browser may still need review after a node failure.

## Shared Application Data

Move structured state from local JSON files into PostgreSQL:

- Tool accounts and page permissions
- Barcode metadata currently stored in `barcode_data.json`
- Product-prefix rules currently stored in `product_library.json`
- Distributor history and deletion records
- Runtime configuration and per-node channel counts
- Cluster nodes, worker heartbeats, and slot state
- Query, transfer, close-order, and product-lookup jobs
- Job items, progress, results, and historical logs
- Encrypted remembered CRM credentials

Use one shared encryption key on all nodes for remembered CRM credentials. Browser profile directories remain local to each node because CRM login sessions are independent per browser channel.

Use R2 for:

- CRM result HTML files
- Generated Excel exports
- Temporary result artifacts that must survive a node change
- Database backups and migration snapshots

Local disks may cache R2 files, but R2 is the authoritative object store. Deleting a result removes the database record and its R2 object; cache files are disposable.

## Shared Job Queue

Replace process-local batch jobs with PostgreSQL-backed jobs. Workers claim one item at a time using row locking and a renewable lease, which preserves the existing behavior where faster channels receive more work.

- Query and product-lookup jobs can be retried automatically after a lease expires.
- A worker heartbeat records node, slot, CRM login state, current item, and last error.
- The web pages read progress and logs from PostgreSQL, so switching origins or refreshing a page does not lose status.
- Transfer and close-order jobs are not blindly replayed after an uncertain failure. They move to `needs_review` with the last known CRM page, order number, service order number, barcode list, and error text to avoid duplicate business actions.
- All detailed logs remain page-scoped and newest-first.

## Authentication And Sessions

All web origins use the same Flask signing secret so a tool login cookie remains valid after web failover. Tool accounts and permissions are read from PostgreSQL.

Desktop builds continue using their existing local-login behavior and are not routed through the server cluster. CRM browser sessions stay local to each server and slot. The settings page aggregates the status of every node and channel.

## Migration And Cutover

1. Back up all existing NAS data volumes and record file counts and hashes.
2. Establish and verify the WireGuard mesh.
3. Deploy Patroni, consensus members, database proxies, and backups without changing the running application.
4. Add PostgreSQL and R2 storage adapters with tests.
5. Import JSON metadata and result files once, then compare counts and hashes.
6. Enable dual-read verification while NAS remains the public application.
7. Enable the shared job queue and verify all four nodes claim work.
8. Deploy the Hong Kong and Singapore tunnels.
9. Create the Cloudflare health monitor, pools, and `crm.mlmll.cn` load balancer.
10. Test application, node, network, and database failure scenarios before closing direct public application ports.

Each migration step must be reversible. Existing JSON and result files remain untouched until the shared database and R2 verification has passed and a separate backup exists.

## Failure Behavior

- Hong Kong application failure: Cloudflare marks the primary origin unhealthy and routes new requests to Singapore.
- Hong Kong host failure: Singapore serves the web application and becomes PostgreSQL leader after election.
- NAS failure: public web access and database leadership continue; NAS workers disappear until recovery.
- US failure: database consensus remains available with Hong Kong and Singapore; US workers disappear until recovery.
- Singapore failure while Hong Kong is healthy: Hong Kong remains public and writable, but redundancy is reported as degraded.
- In-flight transfer or close-order uncertainty: mark the item for manual review instead of repeating it automatically.

## Verification Criteria

- `https://crm.mlmll.cn` presents a valid Cloudflare HTTPS certificate and the application login page.
- Requests normally reach Hong Kong.
- Stopping the Hong Kong application causes `/readyz` to fail and traffic moves to Singapore within two monitor cycles.
- Stopping the Hong Kong database leader promotes Singapore and the application remains writable.
- Accounts, barcode records, product-prefix rules, distributor history, filters, jobs, and logs are identical through both web origins.
- All four nodes and their configured CRM channels appear in the settings page.
- A batch query can be claimed across all logged-in query channels on all four nodes.
- Refreshing or switching web origins does not lose job progress or logs.
- Transfer and close-order fault tests never create an automatic duplicate.
- Backups can restore PostgreSQL metadata and R2 result objects into an empty test environment.

## Cost Boundary

The design uses the existing two-origin Basic Load Balancing subscription, so it does not add paid load balancer origins. R2 remains within its free allowance while storage and operations remain below Cloudflare's included limits. Any future increase beyond two public origins or the R2 free allowance requires explicit approval before changing the subscription.

## Non-Goals

- NAS and US are not automatic public web origins in this design.
- A CRM browser action interrupted after an external CRM submission is not guaranteed to resume automatically.
- Desktop application data is not merged into the server cluster unless a separate migration is requested.
