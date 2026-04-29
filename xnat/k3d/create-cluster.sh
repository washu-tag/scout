#!/usr/bin/env bash
set -euo pipefail

# Creates a local k3d cluster sized for XNAT-Keycloak testing, generates a
# self-signed TLS cert covering *.localtest.me, and writes a kubeconfig under
# this directory.
#
# Idempotent: re-running deletes and recreates the cluster but reuses the
# existing cert/key if they're still valid.

CLUSTER_NAME=${CLUSTER_NAME:-scout-xnat-dev}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="$HERE/certs"
KUBECONFIG_OUT="$HERE/kubeconfig"
HOSTNAME_BASE="localtest.me"

mkdir -p "$CERTS_DIR"

# ---- TLS cert ----------------------------------------------------------------
CERT="$CERTS_DIR/$HOSTNAME_BASE.crt"
KEY="$CERTS_DIR/$HOSTNAME_BASE.key"
CA_CERT="$CERTS_DIR/rootCA.pem"

if command -v mkcert >/dev/null 2>&1; then
  echo "==> Using mkcert (host trust store; pods still need a separate CA mount)"
  mkcert -install >/dev/null
  cp "$(mkcert -CAROOT)/rootCA.pem" "$CA_CERT"
  if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
    mkcert -cert-file "$CERT" -key-file "$KEY" \
      "$HOSTNAME_BASE" "*.$HOSTNAME_BASE"
  fi
else
  echo "==> mkcert not found; falling back to openssl self-signed cert"
  echo "    (host browsers will warn; pods still need the CA mounted in)"
  if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
      -keyout "$KEY" -out "$CERT" \
      -subj "/CN=$HOSTNAME_BASE" \
      -addext "subjectAltName=DNS:$HOSTNAME_BASE,DNS:*.$HOSTNAME_BASE" \
      >/dev/null 2>&1
    cp "$CERT" "$CA_CERT"
  fi
fi
echo "    cert: $CERT"
echo "    key:  $KEY"
echo "    CA:   $CA_CERT"

# ---- Cluster -----------------------------------------------------------------
if k3d cluster list -o json | grep -q "\"name\": *\"$CLUSTER_NAME\""; then
  echo "==> Deleting existing cluster $CLUSTER_NAME"
  k3d cluster delete "$CLUSTER_NAME"
fi

echo "==> Creating cluster $CLUSTER_NAME"
k3d cluster create "$CLUSTER_NAME" \
  --servers 1 \
  --agents 0 \
  --port "80:80@loadbalancer" \
  --port "443:443@loadbalancer" \
  --k3s-arg "--disable=metrics-server@server:0" \
  --wait

echo "==> Writing kubeconfig to $KUBECONFIG_OUT"
k3d kubeconfig get "$CLUSTER_NAME" > "$KUBECONFIG_OUT"
chmod 600 "$KUBECONFIG_OUT"

cat <<EOF

Cluster ready.

Next steps:
  export KUBECONFIG=$KUBECONFIG_OUT
  kubectl get nodes
  cd "$HERE/../../ansible"
  ansible-playbook -i inventory.k3d.yaml playbooks/dev-tls.yaml
  ansible-playbook -i inventory.k3d.yaml playbooks/postgres.yaml
  ansible-playbook -i inventory.k3d.yaml playbooks/auth-keycloak-only.yaml

See xnat/k3d/runbook.md for the full flow including XNAT install.
EOF
