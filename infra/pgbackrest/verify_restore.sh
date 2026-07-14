#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_DATABASE_URL:?set SOURCE_DATABASE_URL}"
: "${RESTORE_DATABASE_URL:?set RESTORE_DATABASE_URL to an isolated restored database}"

TABLES=(
  app_accounts runtime_config barcode_records product_rules distributors
  crm_credentials cluster_nodes crm_slots jobs job_items job_logs object_records migration_runs
)

for table in "${TABLES[@]}"; do
  source_count="$(psql "${SOURCE_DATABASE_URL}" -Atqc "select count(*) from ${table}")"
  restore_count="$(psql "${RESTORE_DATABASE_URL}" -Atqc "select count(*) from ${table}")"
  if [[ "${source_count}" != "${restore_count}" ]]; then
    echo "count mismatch ${table}: source=${source_count} restore=${restore_count}" >&2
    exit 1
  fi
  echo "verified ${table}: ${source_count}"
done
