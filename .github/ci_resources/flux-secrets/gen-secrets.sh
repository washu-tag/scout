#!/usr/bin/env bash
# CI helper: render the ingest-slice fixed-name Secret manifests from inventory.yaml
# (the same creds the Ansible lane uses), so the values live in exactly one place and
# are not duplicated here in plaintext. Output is piped to `sops -e` and the encrypted
# result committed as secrets.enc.yaml; see README.md.
#
#   ./gen-secrets.sh > secrets.yaml && sops -e secrets.yaml > secrets.enc.yaml && rm secrets.yaml
#
# In production a site hand-authors its SOPS files; this generator is a CI convenience
# only. Cassandra/Elasticsearch datastore secrets are intentionally absent: cass-operator
# and ECK mint those, so seeding them by hand collides with the operator.
set -euo pipefail
INV="${INV:-$(git rev-parse --show-toplevel)/.github/ci_resources/inventory.yaml}"
v() { yq ".k3s_cluster.vars.$1" "$INV"; }

# kubernetes.io/basic-auth Secret (CNPG superuser + managed-role companions).
# username MUST equal the managed.roles[].name.
basic_auth() { # name namespace username password
  cat <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: $1
  namespace: $2
type: kubernetes.io/basic-auth
stringData:
  username: "$3"
  password: "$4"
EOF
}

# Opaque Secret from key=value pairs (args after namespace).
opaque() { # name namespace k1 v1 [k2 v2 ...]
  local name=$1 ns=$2; shift 2
  cat <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: $name
  namespace: $ns
type: Opaque
stringData:
EOF
  while [ "$#" -gt 0 ]; do printf '  %s: "%s"\n' "$1" "$2"; shift 2; done
}

# --- scout-core: CNPG superuser + all 5 managed-role companions ---
basic_auth superuser-secret        scout-core postgres      "$(v postgres_superuser_password)"
basic_auth cnpg-role-extractor     scout-core "$(v postgres_user)"        "$(v postgres_password)"
basic_auth cnpg-role-hive          scout-core hive          "$(v hive_postgres_password)"
basic_auth cnpg-role-hive-readonly scout-core hive_readonly "$(v hive_readonly_postgres_password)"
basic_auth cnpg-role-keycloak      scout-core keycloak      "$(v keycloak_postgres_password)"
basic_auth cnpg-role-superset      scout-core "$(v superset_postgres_user)" "$(v superset_postgres_password)"

# --- scout-extractor: app-side DB + S3 creds (DB_PASSWORD matches cnpg-role-extractor) ---
opaque postgres-secret scout-extractor \
  DB_HOST postgresql-cluster-rw.scout-core DB_PORT 5432 DB_NAME ingest \
  DB_USER "$(v postgres_user)" DB_PASSWORD "$(v postgres_password)"
opaque s3-secret scout-extractor \
  AWS_ACCESS_KEY_ID "$(v s3_lake_writer)" AWS_SECRET_ACCESS_KEY "$(v s3_lake_writer_secret)"
opaque trino-rw-s3 scout-extractor \
  S3_ACCESS_KEY "$(v s3_lake_writer)" S3_SECRET_KEY "$(v s3_lake_writer_secret)"

# --- scout-data: hive metastore (write + readonly), MinIO root config, 5 tenant users ---
opaque hive-metastore-secret scout-data \
  HIVE_METASTORE_PASSWORD "$(v hive_postgres_password)" S3_SECRET_KEY "$(v s3_lake_writer_secret)"
opaque hive-metastore-readonly-secret scout-data \
  HIVE_METASTORE_PASSWORD "$(v hive_readonly_postgres_password)" S3_SECRET_KEY "$(v s3_lake_reader_secret)"
# MinIO root config is a single config.env file of shell exports.
cat <<EOF
---
apiVersion: v1
kind: Secret
metadata:
  name: minio-scout-env-configuration
  namespace: scout-data
type: Opaque
stringData:
  config.env: |
    export MINIO_ROOT_USER=$(v s3_username)
    export MINIO_ROOT_PASSWORD=$(v s3_password)
    export MINIO_REGION_NAME=us-east-1
    export MINIO_REGION=us-east-1
EOF
# All 5 tenant-user creds must exist: the tenant bootstrap Job hard-mounts them.
opaque lake-reader-creds       scout-data CONSOLE_ACCESS_KEY "$(v s3_lake_reader)"  CONSOLE_SECRET_KEY "$(v s3_lake_reader_secret)"
opaque lake-writer-creds       scout-data CONSOLE_ACCESS_KEY "$(v s3_lake_writer)"  CONSOLE_SECRET_KEY "$(v s3_lake_writer_secret)"
opaque loki-writer-creds       scout-data CONSOLE_ACCESS_KEY "$(v s3_loki_writer)"  CONSOLE_SECRET_KEY "$(v s3_loki_writer_secret)"
opaque opa-bundle-writer-creds scout-data CONSOLE_ACCESS_KEY opa-bundle-writer      CONSOLE_SECRET_KEY "$(v s3_opa_bundle_writer_secret)"
opaque opa-bundle-reader-creds scout-data CONSOLE_ACCESS_KEY opa-bundle-reader      CONSOLE_SECRET_KEY "$(v s3_opa_bundle_reader_secret)"
