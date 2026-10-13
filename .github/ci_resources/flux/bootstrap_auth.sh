#!/usr/bin/env bash
# Run from the checkout after k3s, with ansible/inventory.yaml already prepared.
set -euo pipefail
: "${RUNNER_TEMP:?}" "${GITHUB_ENV:?}" "${HOME:?}"
tls_dir="$RUNNER_TEMP/platform-tls"
trap 'rm -f "$tls_dir/ca.key" "$tls_dir/key.pem"' EXIT
bash .github/ci_resources/flux/generate_auth_tls.sh "$tls_dir"

# Trust the issuer on the runner (curl), in Node via PLATFORM_CA_CERT exported
# below, and in Chromium's per-user NSS store. Browser tests keep TLS checking on.
sudo install -m 0644 "$tls_dir/ca.crt" /usr/local/share/ca-certificates/scout-ci.crt
sudo update-ca-certificates
if ! command -v certutil >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends libnss3-tools
fi
mkdir -p "$HOME/.pki/nssdb"
if [[ ! -f "$HOME/.pki/nssdb/cert9.db" ]]; then
  certutil -N -d "sql:$HOME/.pki/nssdb" --empty-password
fi
certutil -A -d "sql:$HOME/.pki/nssdb" -n scout-ci-ingress -t 'C,,' -i "$tls_dir/ca.crt"
printf '127.0.0.1 scout.test keycloak.scout.test auth.scout.test\n' | sudo tee -a /etc/hosts >/dev/null

python3 - "$tls_dir" <<'PY'
import json
from pathlib import Path
import sys
directory = Path(sys.argv[1])
(directory / 'ansible-vars.json').write_text(json.dumps({
    'tls_mode': 'file',
    'tls_cert_path': str(directory / 'cert.pem'),
    'tls_key_path': str(directory / 'key.pem'),
    'helm_install_diff_plugin': False,
}))
PY
(cd ansible && sudo -E /opt/pipx_bin/ansible-playbook -i inventory.yaml \
  ../.github/ci_resources/flux/auth-bootstrap.yaml --extra-vars "@$tls_dir/ansible-vars.json")

# The signed site reconciles before application foundations. Make the namespace
# exist for its public CA ConfigMap; the foundation later manages the namespace.
# kube-system already exists. No CI Namespace resource is added to the site.
kubectl create namespace scout-core --dry-run=client -o yaml | kubectl apply -f -
kubectl wait --for=condition=Ready --timeout=2m clusterissuer/scout-internal-ca
printf 'PLATFORM_CA_CERT=%s\n' "$tls_dir/ca.crt" >> "$GITHUB_ENV"
