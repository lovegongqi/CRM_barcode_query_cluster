#!/usr/bin/env bash
set -euo pipefail

TYPE="${1:-diff}"
if [[ "${TYPE}" != "diff" && "${TYPE}" != "full" ]]; then
  echo "backup type must be diff or full" >&2
  exit 2
fi

if ! curl --silent --fail \
  --cacert /run/cluster-secrets/ca.crt \
  --cert /run/cluster-secrets/patroni-client.crt \
  --key /run/cluster-secrets/patroni-client.key \
  https://127.0.0.1:8008/primary >/dev/null; then
  echo "local node is not primary; backup skipped"
  exit 0
fi

pgbackrest --stanza=crm-barcode stanza-create >/dev/null 2>&1 || true
pgbackrest --stanza=crm-barcode check
pgbackrest --stanza=crm-barcode --type="${TYPE}" backup
