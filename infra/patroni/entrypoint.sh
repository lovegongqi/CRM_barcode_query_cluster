#!/usr/bin/env bash
set -euo pipefail

install -d -m 0700 \
  /etc/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest \
  /run/patroni /run/cluster-secrets /var/lib/postgresql/data /var/run/postgresql
cp -a /run/secrets/. /run/cluster-secrets/
find /run/cluster-secrets -type f \( -name '*.key' -o -name '*.pem' \) -exec chmod 0600 {} +
find /run/cluster-secrets -type f -name '*.crt' -exec chmod 0644 {} +
chown -R postgres:postgres \
  /var/lib/postgresql/data /var/run/postgresql /run/patroni /run/cluster-secrets \
  /var/log/pgbackrest /var/spool/pgbackrest
render-pgbackrest-config
chown postgres:postgres /etc/pgbackrest/pgbackrest.conf
cron

(
  until gosu postgres curl --silent --fail \
    --cacert /run/cluster-secrets/ca.crt \
    --cert /run/cluster-secrets/patroni-client.crt \
    --key /run/cluster-secrets/patroni-client.key \
    https://127.0.0.1:8008/liveness >/dev/null; do
    sleep 5
  done
  gosu postgres pgbackrest --stanza=crm-barcode stanza-create >/dev/null 2>&1 || true
) &

exec gosu postgres "$@"
