# CI Smoke Test

The `smoke-test` CI job deploys the full Scout stack on a single-node K3s cluster (GitHub Actions `ubuntu-latest` runner, 7 GB RAM) and validates that all services start, endpoints are reachable, and authentication is enforced.

## Jobs overview

| Job | Purpose |
|-----|---------|
| `deploy-and-test` | Deploys the data pipeline (postgres, lake, trino, orchestrator, extractor), runs ingest integration tests, then verifies Trino can query the ingested data. |
| `smoke-test` | Deploys everything _except_ orchestrator and extractor (which are already covered by `deploy-and-test`). Deploys: k3s, traefik, postgres, valkey, auth (Keycloak + OAuth2 Proxy), lake, analytics (Trino + Superset), jupyter, monitor (Prometheus + Loki + Grafana), chat (Open WebUI + Ollama), playbooks (Voila), and launchpad. Runs health checks and auth-curl-tests. |

Both jobs share the same CI inventory (`.github/ci_resources/inventory.yaml`) and run in parallel after `lint` and `build-and-upload`.

## Networking

The smoke-test job creates a realistic networking environment so services can communicate by hostname, TLS works end-to-end, and auth-curl-tests can validate endpoints from outside the cluster.

### Three DNS layers

```
┌─────────────────────────────────────────────────────────┐
│  CI Host (/etc/hosts)                                   │
│  127.0.0.1  scout.test                                  │
│  127.0.0.1  superset.scout.test                         │
│  127.0.0.1  jupyter.scout.test                          │
│  127.0.0.1  ...                                         │
│                                                         │
│  curl  ──► 127.0.0.1:443  ──► Traefik (hostPort)       │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  In-cluster DNS (CoreDNS extra server block)            │
│  NODE_IP  scout.test                                    │
│  NODE_IP  superset.scout.test                           │
│  NODE_IP  keycloak.scout.test                           │
│  NODE_IP  ...                                           │
│                                                         │
│  Pods resolving external hostnames  ──► NODE_IP:443     │
│  ──► Traefik ──► ingress routing ──► backend service    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Kubernetes service DNS (automatic)                     │
│  oauth2-proxy.kube-system                               │
│  keycloak.scout-core                                    │
│  ...                                                    │
│                                                         │
│  Internal service-to-service via ClusterIP              │
└─────────────────────────────────────────────────────────┘
```

**Host-level `/etc/hosts`**: Maps all Scout subdomains to `127.0.0.1`. This is how `curl` from the CI runner reaches Traefik, which listens on the host's ports 80 and 443 via K3s `hostPort`. These entries are created _before_ deployment because some Ansible tasks may resolve service URLs during deploy.

**CoreDNS extra server blocks**: Configured via the `coredns_extra_server_blocks` inventory variable. Maps Scout subdomains to `NODE_IP` (the host's primary network interface IP, obtained at CI time via `hostname -I`). This is needed because pods use CoreDNS for DNS resolution, not the host's `/etc/hosts`. Even if the query reached the host's resolver chain, `/etc/hosts` maps to `127.0.0.1` which inside a pod is the pod's own loopback — not the host. CoreDNS entries return the actual node IP so pods can route to Traefik. This matters for any pod-to-pod communication that goes through external hostnames — for example, OAuth2 Proxy connecting to Keycloak's OIDC endpoints at `https://keycloak.scout.test/realms/scout/...`.

**Kubernetes service DNS**: Standard ClusterIP service discovery (e.g., `oauth2-proxy.kube-system`). Used for the forwardAuth override (see below).

### TLS certificates

A self-signed wildcard certificate is generated at CI time:

```
CN=scout.test
SANs: DNS:scout.test, DNS:*.scout.test, DNS:*.jupyter.scout.test
```

The `*.jupyter.scout.test` SAN is needed because JupyterHub uses subdomain-based user isolation (e.g., `testuser.jupyter.scout.test`).

The cert is required because the Traefik role asserts that `tls_cert_path` and `tls_key_path` are defined — it creates a Kubernetes TLS secret and configures it as Traefik's default certificate via a `TLSStore` CRD. Without a cert, the Traefik playbook fails.

The cert is also added to the host's CA trust store (`/usr/local/share/ca-certificates/` + `update-ca-certificates`) so that `curl` on the CI runner trusts it during auth-curl-tests. An alternative would be to use `curl -k`, but adding it to the trust store tests the full TLS chain more realistically.

### ForwardAuth override

The OAuth2 Proxy `forwardAuth` Traefik middleware normally points to `https://auth.scout.test/oauth2/auth`. In CI, this fails because the Traefik container's CA trust store doesn't include the self-signed cert — Traefik can't verify TLS when connecting to itself.

The CI inventory overrides this to the internal Kubernetes service URL:

```yaml
oauth2_proxy_auth_url: 'http://oauth2-proxy.kube-system/oauth2/auth'
```

This bypasses TLS entirely for the auth check (cluster-internal HTTP) while the external-facing TLS termination at Traefik still works normally.

### NODE_IP placeholder

The CI inventory uses `NODE_IP` as a placeholder in the CoreDNS config. The "Prepare inventory" CI step replaces it:

```bash
NODE_IP=$(hostname -I | awk '{print $1}')
sed -e "s/NODE_IP/$NODE_IP/g" ...
```

Other placeholders replaced at the same time: `HOSTNAME` (system hostname), `K3S_TOKEN` (random hex), `WORK_DIR` (checkout path).

## Auth curl tests

`tests/auth/auth-curl-tests.sh` verifies that OAuth2 Proxy blocks unauthenticated access to all protected Scout endpoints.

**How it works**: For each endpoint, the script sends an unauthenticated `curl` request (no cookies, no tokens) over both HTTPS and HTTP. The Traefik middleware chain processes each request:

1. **`oauth2-proxy-error`** (Errors middleware): Watches for 401 responses; when triggered, queries OAuth2 Proxy's sign-in page and replaces the response body (but preserves the 401 status code).
2. **`oauth2-proxy-auth`** (ForwardAuth middleware): Forwards an auth check to OAuth2 Proxy. With no session cookie, OAuth2 Proxy returns 401.
3. **`security-headers`**: Adds HSTS, CSP, etc.

The test expects **401** for protected endpoints (the Errors middleware replaces the body but the original 401 status code is preserved) and **200** for unprotected endpoints (Keycloak OIDC discovery, OAuth2 Proxy sign-in page).

HTTP requests are expected to get a 301/308 redirect to HTTPS; the test follows the redirect and checks the final HTTPS status.

## Resource constraints

The GitHub Actions runner has ~7 GB RAM. The CI inventory reduces resource requests for memory-heavy services:

- Trino: 1 worker (default: 2), 512M heap (default: 2G)
- Cassandra/Elasticsearch: reduced heap
- PostgreSQL: no resource requests/limits
- Ollama: minimal resources (no GPU, no models)
- Loki: memcached caches disabled
- Helm timeout: 10 minutes (default: 5)

## Inventory placeholders

| Placeholder | Replaced with | Used for |
|-------------|--------------|----------|
| `HOSTNAME` | `$(hostname)` | Ansible inventory host |
| `K3S_TOKEN` | Random hex | K3s cluster token |
| `WORK_DIR` | `$(pwd)` | Repo checkout path |
| `NODE_IP` | `$(hostname -I \| awk '{print $1}')` | CoreDNS in-cluster DNS |
