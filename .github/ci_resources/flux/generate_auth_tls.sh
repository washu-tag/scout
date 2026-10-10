#!/usr/bin/env bash
# Create a short-lived ingress CA + wildcard certificate for the isolated CI job.
# The caller installs cert.pem/key.pem, then removes key.pem. Only ca.crt enters
# the signed site artifact; neither private key belongs in an artifact directory.
set -euo pipefail
: "${1:?usage: generate_auth_tls.sh OUTPUT_DIRECTORY}"
tls_dir="$1"
umask 077
mkdir -p "$tls_dir"
cleanup() {
  local result=$?
  rm -f "$tls_dir/ca.key" "$tls_dir/server.csr" "$tls_dir/ca.srl"
  if (( result != 0 )); then rm -f "$tls_dir/key.pem"; fi
}
trap cleanup EXIT
cat > "$tls_dir/openssl.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = Scout CI ingress CA
[ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
[server]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:scout.test,DNS:*.scout.test
authorityKeyIdentifier = keyid,issuer
EOF
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 2 \
  -config "$tls_dir/openssl.cnf" -extensions ca \
  -keyout "$tls_dir/ca.key" -out "$tls_dir/ca.crt"
openssl req -new -newkey rsa:2048 -nodes -sha256 -subj '/CN=scout.test' \
  -keyout "$tls_dir/key.pem" -out "$tls_dir/server.csr"
openssl x509 -req -sha256 -days 2 -in "$tls_dir/server.csr" \
  -CA "$tls_dir/ca.crt" -CAkey "$tls_dir/ca.key" -CAcreateserial \
  -extfile "$tls_dir/openssl.cnf" -extensions server -out "$tls_dir/cert.pem"
openssl verify -CAfile "$tls_dir/ca.crt" -purpose sslserver \
  -verify_hostname scout.test "$tls_dir/cert.pem"
openssl verify -CAfile "$tls_dir/ca.crt" -purpose sslserver \
  -verify_hostname keycloak.scout.test "$tls_dir/cert.pem"
