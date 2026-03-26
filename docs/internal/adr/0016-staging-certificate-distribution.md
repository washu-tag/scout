# ADR 0016: Staging Node Certificate Distribution

**Date**: 2026-03
**Status**: Proposed
**Decision Owner**: TAG Team

## Context

In air-gapped deployments, the staging node runs services (currently Harbor, potentially others in the future) behind Traefik with TLS. The Scout production cluster needs to communicate with these staging services over HTTPS.

The staging node's TLS certificate may be either:
- **A properly CA-signed certificate** (e.g., from an enterprise CA or Let's Encrypt) — if the operator provides one via `tls_cert_path` in inventory. In this case, services already trust the certificate through the standard system CA bundle and no additional configuration is needed.
- **A self-signed certificate** (the default) — generated during staging deployment when no `tls_cert_path` is provided. Services do not trust this certificate by default, so it must be explicitly distributed.

Currently, when the staging node uses a self-signed certificate, K3s's `registries.yaml` bypasses certificate verification entirely via `insecure_skip_verify: true`. This works but has downsides:
- No protection against man-in-the-middle attacks between the production cluster and staging node
- Inconsistent with security hardening efforts (ADR 0012)
- As more staging-hosted services are added, each would need its own skip-verify workaround

Since Ansible already runs from the jump node, which has connectivity to both the staging node and production cluster (ADR 0007), it is well-positioned to fetch the staging CA certificate and distribute it to production nodes.

## Decision

**When the staging node uses a self-signed certificate, distribute it to the production cluster via Ansible and configure services to trust it explicitly instead of skipping TLS verification. When a CA-signed certificate is provided, no distribution is needed — services trust it by default.**

The `staging_uses_self_signed_cert` variable (derived automatically by comparing the configured `tls_cert_path` against the default self-signed path) gates all cert distribution tasks. When the operator provides a CA-signed cert, these tasks are skipped entirely.

### Mechanism (self-signed cert only)

1. **Fetch**: Ansible fetches the staging CA cert from the staging node to the jump node (using `ansible.builtin.fetch` or `ansible.builtin.slurp`)
2. **Distribute**: Ansible copies the cert to production nodes at a well-known path (e.g., `/etc/rancher/k3s/staging-ca.crt`) and into a Kubernetes Secret or ConfigMap for pod-mounted access
3. **Configure**: Each service that contacts the staging node references the cert in its trust configuration

### Per-Service Configuration

**containerd (K3s image pulls)** — file on the host node:

```yaml
# /etc/rancher/k3s/registries.yaml
configs:
  "<harbor-host>:443":
    tls:
      ca_file: "/etc/rancher/k3s/staging-ca.crt"
```

This replaces the current `insecure_skip_verify: true`.

**Future containerized services** — mount from a Kubernetes Secret:

```yaml
# Create a secret from the cert
kubectl create secret generic staging-ca-cert \
  --from-file=staging-ca.crt=/etc/rancher/k3s/staging-ca.crt

# Mount into pods that need it
volumeMounts:
  - name: staging-ca
    mountPath: /etc/ssl/certs/staging-ca.crt
    subPath: staging-ca.crt
    readOnly: true
volumes:
  - name: staging-ca
    secret:
      secretName: staging-ca-cert
```

How a service uses the mounted cert depends on its runtime:
- **JVM services**: Add to a Java truststore via `keytool`
- **Python/Node.js**: Set environment variables (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, or `REQUESTS_CA_BUNDLE`) or append to the system CA bundle
- **Go/system-level**: Append to `/etc/ssl/certs/ca-certificates.crt`

### Scope

Today the only service that contacts staging is containerd (for Harbor image pulls). The Kubernetes Secret and pod-mounting patterns are documented here for when additional staging-hosted services are introduced, but should be implemented only when needed.

Note that if a CA-signed certificate is provided for the staging node, none of the above per-service configuration is necessary — the certificate is already trusted through the standard system CA bundle and container base image trust stores.

## Alternatives Considered

### Require a CA-Signed Certificate on Staging

Rather than supporting self-signed certificates at all, require the operator to provide a CA-signed certificate for the staging node.

**Pros:**
- No custom cert distribution needed — all clients trust it by default
- Simplest client-side configuration
- No additional Ansible tasks or host-level files

**Cons:**
- Requires a publicly resolvable domain name for the staging node, which some deployments may not have (e.g., internal-only DNS)
- Adds an external dependency (the CA and ACME infrastructure) to the deployment pipeline
- Not all operators have access to a CA that can issue certificates for internal hosts

**Verdict:** Not chosen as a requirement, but fully supported as an option. When the operator provides a CA-signed certificate via `tls_cert_path`, Scout uses it directly and skips all self-signed cert distribution. The self-signed path exists as a default for environments where a CA-signed certificate is unavailable.

## Consequences

### Positive

- TLS verification is enforced between the production cluster and staging node, closing a MITM gap
- Consistent security posture across the deployment
- Pattern is extensible to future staging-hosted services without additional skip-verify workarounds
- No new infrastructure required — uses existing Ansible fetch/copy and Kubernetes primitives
- Operators who can provide a CA-signed certificate get the simplest path: set `tls_cert_path` and everything works with zero cert distribution

### Negative

- When using a self-signed certificate, it must be re-distributed if regenerated (e.g., expiry, hostname change)
- Adds a deployment ordering dependency: staging cert must exist before it can be fetched and distributed (self-signed path only)

### Operational

- **CA-signed cert**: Set `tls_cert_path` in inventory to the cert path on the staging node. No distribution or trust configuration is needed — services trust it by default.
- **Self-signed cert rotation**: When the staging cert is regenerated, re-run the playbook that distributes it and restart affected services (K3s for containerd, pods for mounted secrets)
- **Adding a new service that contacts staging (self-signed only)**: Create a Kubernetes Secret from the cert (if not already present), mount it into the pod, and configure the service's TLS trust as appropriate for its runtime

## Related

- **ADR 0002**: K3s Air-Gapped Deployment Strategy
- **ADR 0007**: Jump Node Architecture — jump node connectivity model
- **ADR 0012**: Security Scan Response and Hardening
- `ansible/roles/k3s/tasks/registry.yaml` — cert distribution and registry mirror TLS configuration
- `ansible/playbooks/staging.yaml` — self-signed cert generation
