# ADR 0015: Staging Node Certificate Distribution

**Date**: 2026-03
**Status**: Proposed
**Decision Owner**: TAG Team

## Context

In air-gapped deployments, the staging node runs services (currently Harbor, potentially others in the future) behind Traefik with a self-signed TLS certificate. The Scout production cluster needs to communicate with these staging services, but currently bypasses certificate verification entirely via `insecure_skip_verify: true` in the K3s `registries.yaml` configuration.

This works but has downsides:
- No protection against man-in-the-middle attacks between the production cluster and staging node
- Inconsistent with security hardening efforts (ADR 0012)
- As more staging-hosted services are added, each would need its own skip-verify workaround

Since Ansible already runs from the jump node, which has connectivity to both the staging node and production cluster (ADR 0007), it is well-positioned to fetch the staging CA certificate and distribute it to production nodes.

## Decision

**Distribute the staging node's self-signed CA certificate to the production cluster via Ansible, and configure services to trust it explicitly instead of skipping TLS verification.**

### Mechanism

1. **Fetch**: Ansible fetches the staging CA cert from the staging node to the jump node (using `ansible.builtin.fetch` or `ansible.builtin.slurp`)
2. **Distribute**: Ansible copies the cert to production nodes at a well-known path (e.g., `/etc/rancher/k3s/agent/staging-ca.crt`) and into a Kubernetes Secret or ConfigMap for pod-mounted access
3. **Configure**: Each service that contacts the staging node references the cert in its trust configuration

### Per-Service Configuration

**containerd (K3s image pulls)** — file on the host node:

```yaml
# /etc/rancher/k3s/registries.yaml
configs:
  "<harbor-host>:443":
    tls:
      ca_file: "/etc/rancher/k3s/agent/staging-ca.crt"
```

This replaces the current `insecure_skip_verify: true`.

**Future containerized services** — mount from a Kubernetes Secret:

```yaml
# Create a secret from the cert
kubectl create secret generic staging-ca-cert \
  --from-file=staging-ca.crt=/etc/rancher/k3s/agent/staging-ca.crt

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

## Alternatives Considered

### Use a Real CA-Signed Certificate on Staging

Obtain a certificate from a trusted CA (e.g., Let's Encrypt) for the staging node. The staging node and jump node both have internet access, so ACME challenges and certificate issuance are feasible.

**Pros:**
- No custom cert distribution needed — all clients trust it by default
- Simplest client-side configuration
- Automated renewal via certbot or similar tooling

**Cons:**
- Requires a publicly resolvable domain name for the staging node, which some deployments may not have (e.g., internal-only DNS)
- Adds an external dependency (the CA and ACME infrastructure) to the deployment pipeline
- More moving parts on the staging node (certbot, renewal cron/timer, Traefik reload)
- If the staging hostname changes between deployments, the cert must be re-issued

**Verdict:** Rejected for now. Distributing the self-signed cert is simpler and works regardless of DNS configuration. However, deployments that already have a publicly resolvable staging hostname could reasonably choose this approach instead.

## Consequences

### Positive

- TLS verification is enforced between the production cluster and staging node, closing a MITM gap
- Consistent security posture across the deployment
- Pattern is extensible to future staging-hosted services without additional skip-verify workarounds
- No new infrastructure required — uses existing Ansible fetch/copy and Kubernetes primitives

### Negative

- Certificate must be re-distributed if the staging cert is regenerated (e.g., expiry, hostname change)
- Adds a deployment ordering dependency: staging cert must exist before it can be fetched and distributed

### Operational

- **Cert rotation**: When the staging cert is regenerated, re-run the playbook that distributes it and restart affected services (K3s for containerd, pods for mounted secrets)
- **Adding a new service that contacts staging**: Create a Kubernetes Secret from the cert (if not already present), mount it into the pod, and configure the service's TLS trust as appropriate for its runtime

## Related

- **ADR 0002**: K3s Air-Gapped Deployment Strategy
- **ADR 0007**: Jump Node Architecture — jump node connectivity model
- **ADR 0012**: Security Scan Response and Hardening
- `ansible/roles/k3s/tasks/registry.yaml` — current `insecure_skip_verify` configuration
- `ansible/playbooks/staging.yaml` — self-signed cert generation
