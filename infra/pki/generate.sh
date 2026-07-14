#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${ROOT}/generated"
CA_DIR="${OUTPUT}/ca"
ROTATE_CA=0

if [[ "${1:-}" == "--rotate-ca" ]]; then
  ROTATE_CA=1
elif [[ -n "${1:-}" ]]; then
  echo "usage: $0 [--rotate-ca]" >&2
  exit 2
fi

umask 077
mkdir -p "${CA_DIR}"

if [[ -f "${CA_DIR}/ca.key" && "${ROTATE_CA}" -ne 1 ]]; then
  echo "existing CA found; reusing it (use --rotate-ca to replace)"
else
  if [[ "${ROTATE_CA}" -eq 1 ]]; then
    rm -rf "${OUTPUT}"
    mkdir -p "${CA_DIR}"
  elif [[ -e "${CA_DIR}/ca.crt" || -e "${CA_DIR}/ca.key" ]]; then
    echo "incomplete CA exists; refusing to overwrite" >&2
    exit 1
  fi
  openssl ecparam -name prime256v1 -genkey -noout -out "${CA_DIR}/ca.key"
  openssl req -x509 -new -sha256 -days 3650 \
    -key "${CA_DIR}/ca.key" \
    -subj "/O=CRM Barcode Query/OU=Private Cluster/CN=CRM Barcode Private CA" \
    -out "${CA_DIR}/ca.crt"
fi

issue_leaf() {
  local node="$1"
  local host="$2"
  local name="$3"
  local usage="$4"
  local node_dir="${OUTPUT}/${node}"
  local key="${node_dir}/${name}.key"
  local csr="${node_dir}/${name}.csr"
  local crt="${node_dir}/${name}.crt"
  local ext="${node_dir}/${name}.ext"
  mkdir -p "${node_dir}"
  cp "${CA_DIR}/ca.crt" "${node_dir}/ca.crt"
  openssl ecparam -name prime256v1 -genkey -noout -out "${key}"
  openssl req -new -sha256 -key "${key}" \
    -subj "/O=CRM Barcode Query/OU=${node}/CN=${name}-${node}" \
    -out "${csr}"
  local subject_alt_name="DNS:${host},DNS:${node}.mlmll.cn"
  if [[ "${name}" == "postgres-server" ]]; then
    subject_alt_name="${subject_alt_name},DNS:db.mlmll.cn,DNS:localhost,IP:127.0.0.1"
  fi
  if [[ "${name}" == "patroni-server" || "${name}" == "etcd-server" ]]; then
    subject_alt_name="${subject_alt_name},IP:127.0.0.1"
  fi
  {
    echo "basicConstraints=critical,CA:FALSE"
    echo "keyUsage=critical,digitalSignature,keyAgreement"
    echo "extendedKeyUsage=${usage}"
    echo "subjectAltName=${subject_alt_name}"
    echo "subjectKeyIdentifier=hash"
    echo "authorityKeyIdentifier=keyid,issuer"
  } > "${ext}"
  openssl x509 -req -sha256 -days 365 \
    -in "${csr}" -CA "${CA_DIR}/ca.crt" -CAkey "${CA_DIR}/ca.key" -CAcreateserial \
    -extfile "${ext}" -out "${crt}" >/dev/null 2>&1
  rm -f "${csr}" "${ext}"
  chmod 600 "${key}"
  chmod 644 "${crt}" "${node_dir}/ca.crt"
}

for entry in "hk:hk.mlmll.cn" "sg:sg.mlmll.cn" "us:us.mlmll.cn" "nas:mlmll.cn"; do
  node="${entry%%:*}"
  host="${entry#*:}"
  issue_leaf "${node}" "${host}" postgres-server serverAuth
  issue_leaf "${node}" "${host}" etcd-server serverAuth,clientAuth
  issue_leaf "${node}" "${host}" etcd-peer serverAuth,clientAuth
  issue_leaf "${node}" "${host}" etcd-client clientAuth
  issue_leaf "${node}" "${host}" patroni-server serverAuth
  issue_leaf "${node}" "${host}" patroni-client clientAuth
  issue_leaf "${node}" "${host}" haproxy-client clientAuth
  issue_leaf "${node}" "${host}" app-client clientAuth
  issue_leaf "${node}" "${host}" admin-client clientAuth
  issue_leaf "${node}" "${host}" replica-client clientAuth
  cat "${OUTPUT}/${node}/haproxy-client.crt" "${OUTPUT}/${node}/haproxy-client.key" > "${OUTPUT}/${node}/haproxy-client.pem"
  chmod 600 "${OUTPUT}/${node}/haproxy-client.pem"
  python3 "${ROOT}/render_node_config.py" --node "${node}" --output "${OUTPUT}/${node}" >/dev/null
done

echo "cluster certificates generated under ${OUTPUT}"
